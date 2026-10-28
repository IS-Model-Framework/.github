#!/usr/bin/env python3
"""Check AI review rounds and dispositions without running PR code."""

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import cache
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CHECK_NAME = "AI Review / Disposition Check"
STATUSES = ("Accepted", "Rejected", "Deferred", "Invalid")
AUTHOR_FIELDS = "author { login __typename }"
EDIT_FIELDS = "editor { login __typename } lastEditedAt"
COMMENT_FIELDS = f"""
  id body url updatedAt {AUTHOR_FIELDS} {EDIT_FIELDS}
  pullRequestReview {{ state }}
"""
PAGE_FIELDS = "pageInfo { hasNextPage endCursor }"
CODEX = "chatgpt-codex-connector[bot]"
CODEX_SUMMARY = "<!-- codex-pull-request-review-summary -->"
PUBLISHED_REVIEWS = {"COMMENTED", "APPROVED", "CHANGES_REQUESTED"}
INFISCALE_COMPLETE = "未发现本次 PR 引入的新问题。LGTM！"  # noqa: RUF001
JOURNAL_LOCK = Lock()


def actor_login(author):
  author = author or {}
  login = author.get("login", "").lower()
  if author.get("__typename") == "Bot" and not login.endswith("[bot]"):
    login += "[bot]"
  return login


def content_actor(comment):
  if comment.get("lastEditedAt") or comment.get("editor"):
    return comment.get("editor") or {}
  return comment.get("author") or {}


class GitHub:
  def __init__(self, token):
    self.token = token

  def request(self, method, path, body=None):
    request = Request(
      "https://api.github.com" + path,
      data=json.dumps(body).encode("utf-8") if body is not None else None,
      method=method,
      headers={
        "Authorization": f"Bearer {self.token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
      },
    )
    read_only = method == "GET" or (
      method == "POST"
      and path == "/graphql"
      and re.match(r"\s*query\b", (body or {}).get("query", ""))
    )
    for attempt in range(3):
      try:
        with urlopen(request, timeout=30) as response:
          return json.load(response)
      except HTTPError as error:
        retry_after = (error.headers or {}).get("Retry-After")
        if (
          not read_only
          or attempt == 2
          or not (
            error.code in {429, 500, 502, 503, 504}
            or (error.code == 403 and retry_after)
          )
        ):
          raise
        delay = 2**attempt
        if retry_after:
          try:
            delay = max(0, float(retry_after))
          except ValueError:
            delay = max(
              0,
              (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds(),
            )
        # Do not retry earlier than GitHub allows, or sleep without a bound.
        if delay > 60:
          raise
        time.sleep(delay)
      except (URLError, TimeoutError):
        if not read_only or attempt == 2:
          raise
        time.sleep(2**attempt)

  def graphql(self, query, variables):
    result = self.request("POST", "/graphql", {"query": query, "variables": variables})
    if result.get("errors"):
      raise RuntimeError("GitHub GraphQL returned errors; refusing partial data")
    return result["data"]

  def connection(self, node_id, field, selection, typename="PullRequest", page=None):
    cursor = None
    nodes = []
    while True:
      if page is None:
        query = f"""
          query($id: ID!, $cursor: String) {{
            node(id: $id) {{ ... on {typename} {{
              {field}(first: 100, after: $cursor) {{
                nodes {{ {selection} }} {PAGE_FIELDS}
              }}
            }} }}
          }}
        """
        data = self.graphql(query, {"id": node_id, "cursor": cursor})
        page = data["node"][field]
      nodes.extend(page["nodes"])
      if not page["pageInfo"]["hasNextPage"]:
        return nodes
      cursor = page["pageInfo"]["endCursor"]
      page = None

  def pages(self, path, field=None):
    """Read a REST list completely, including object-wrapped lists such as checks."""
    nodes = []
    separator = "&" if "?" in path else "?"
    page = 1
    while True:
      response = self.request("GET", f"{path}{separator}per_page=100&page={page}")
      items = response[field] if field else response
      nodes.extend(items)
      if len(items) < 100:
        return nodes
      page += 1


def snapshot(api, repository, number):
  pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
  reviews = api.connection(
    pr["node_id"], "reviews", f"id state createdAt submittedAt {AUTHOR_FIELDS}"
  )
  comments = api.connection(
    pr["node_id"],
    "comments",
    f"id body createdAt updatedAt {AUTHOR_FIELDS} {EDIT_FIELDS}",
  )
  threads = api.connection(
    pr["node_id"],
    "reviewThreads",
    # Repository rules enforce resolution; it must not affect snapshot equality.
    f"""id
      comments(first: 100) {{ nodes {{ {COMMENT_FIELDS} }} {PAGE_FIELDS} }}""",
  )
  for thread in threads:
    thread["comments"] = api.connection(
      thread["id"],
      "comments",
      COMMENT_FIELDS,
      "PullRequestReviewThread",
      page=thread["comments"],
    )
  commits = api.connection(pr["node_id"], "commits", "commit { oid }")
  commit_ids = [node["commit"]["oid"] for node in commits]
  expected = pr.get("commits", len(commit_ids))
  if len(commit_ids) != expected:
    # PullRequest.commits silently stops at 250; paginated compare is BASE..HEAD.
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    commit_ids = [
      commit["sha"]
      for commit in api.pages(f"/repos/{repository}/compare/{base}...{head}", "commits")
    ]
    if len(set(commit_ids)) != expected or head not in commit_ids:
      raise RuntimeError("Incomplete PR commit comparison; refusing partial evidence")
  current = api.request("GET", f"/repos/{repository}/pulls/{number}")

  def revision(item):
    base = item.get("base", {})
    return item["head"]["sha"], base.get("ref"), base.get("sha"), item["state"]

  if revision(current) != revision(pr):
    raise RuntimeError("PR changed while reading review data; rerun required")
  return {
    "repository": repository,
    "head": pr["head"]["sha"],
    "base": {key: pr.get("base", {}).get(key) for key in ("ref", "sha")},
    "author": pr["user"]["login"].lower(),
    "state": current["state"],
    "reviews": reviews,
    "comments": comments,
    "threads": threads,
    "commits": commit_ids,
  }


def parse_disposition(body):
  lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
  if not lines or not lines[0].lower().startswith("disposition:"):
    return None
  fields = {}
  for line in lines:
    key, separator, value = line.partition(":")
    key = key.lower()
    if (
      not separator
      or key not in {"disposition", "reason", "fix", "tracking"}
      or key in fields
      or not value.strip()
    ):
      return {"error": "Use one nonempty value per field, without duplicate fields"}
    fields[key] = value.strip()
  status = fields["disposition"].capitalize()
  if status not in STATUSES:
    return {"error": "Disposition must be Accepted / Rejected / Deferred / Invalid"}
  fields["disposition"] = status
  if not fields.get("reason"):
    fields["error"] = "Reason is required"
  return fields


def timestamp(value):
  return (
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value
    else datetime.min.replace(tzinfo=UTC)
  )


def infiscale_start(body):
  lines = body.strip().splitlines()
  return bool(lines) and lines[0].removeprefix("👀").strip() == "收到，开始 review"  # noqa: RUF001


def codex_progress(comment):
  """Use the Code Review row's own time; Security Review edits are unrelated."""
  body = comment["body"].replace("\r\n", "\n").strip()
  if not body.startswith(CODEX_SUMMARY + "\n"):
    return None
  row = re.search(r"(?m)^\| 📝 \*\*Code Review\*\* \| ([^\n|]+)\|", body)
  if not row:
    return None
  completed = bool(
    re.fullmatch(
      r"✅ \*\*Completed\*\*(?: <relative-time [^\n|]*</relative-time>)?",
      row[1].strip(),
    )
  )
  clock = re.search(r'<relative-time datetime="([^"]+)"', row[1])
  # A legacy Completed row without a clock cannot complete a newer round.
  fallback = comment.get("createdAt") if completed else comment.get("updatedAt")
  return timestamp(clock[1] if clock else fallback), completed


def review_complete(data, bot):
  """Rounds for a bot do not overlap; a withdrawn round may use older evidence."""
  reviews = [r for r in data["reviews"] if actor_login(r.get("author")) == bot]
  comments = [c for c in data["comments"] if actor_login(c.get("author")) == bot]
  starts = [timestamp(r.get("createdAt")) for r in reviews if r["state"] == "PENDING"]
  completed = [
    timestamp(r.get("submittedAt")) for r in reviews if r["state"] in PUBLISHED_REVIEWS
  ]
  progress = []
  for comment in comments:
    if bot in {"infiscale-bot", CODEX} and actor_login(content_actor(comment)) != bot:
      changed = timestamp(comment.get("lastEditedAt") or comment.get("updatedAt"))
      starts.append(changed)
      if bot == CODEX:
        progress.append((changed, False))
      continue
    if bot == "infiscale-bot":
      if infiscale_start(comment["body"]):
        starts.append(timestamp(comment.get("createdAt")))
      elif comment["body"].strip() == INFISCALE_COMPLETE:
        completed.append(timestamp(comment.get("createdAt")))
    elif bot == CODEX:
      status = codex_progress(comment)
      if status:
        progress.append(status)
        if status[1]:
          completed.append(status[0])
  latest_status = max(
    progress, key=lambda status: (status[0], not status[1]), default=None
  )
  if latest_status and not latest_status[1]:
    starts.append(latest_status[0])
  start = max(starts, default=timestamp(None))
  current = [
    r
    for r in reviews
    if r["state"] in PUBLISHED_REVIEWS | {"DISMISSED"}
    and timestamp(r.get("submittedAt")) >= start
  ]
  if current and all(r["state"] == "DISMISSED" for r in current):
    return bool(completed)
  if latest_status and not latest_status[1]:
    return False
  return any(when >= start for when in completed)


def evaluate(data, bots, authorized, tracking_valid):
  """Check PR-wide completion, then apply the same reply policy to AI/human threads."""
  errors = [
    f"{bot}: waiting for this review round to complete (no valid completion evidence)"
    for bot in sorted(bots)
    if not review_complete(data, bot)
  ]
  findings = []

  for thread in data["threads"]:
    comments = [
      comment
      for comment in thread["comments"]
      if (comment.get("pullRequestReview") or {}).get("state") != "PENDING"
    ]
    if not comments:
      continue
    root = comments[0]
    reviewer = actor_login(root.get("author"))
    if reviewer not in bots and (
      (root.get("author") or {}).get("__typename") != "User"
      or reviewer == data["author"]
    ):
      continue
    candidates = []
    for comment in comments[1:]:
      author = comment.get("author") or {}
      login = actor_login(author)
      fields = parse_disposition(comment["body"])
      if (
        fields is not None
        and author.get("__typename") == "User"
        and login not in bots
        and authorized(login, data["author"])
      ):
        editor = content_actor(comment)
        editor_login = actor_login(editor)
        if (
          editor.get("__typename") != "User"
          or editor_login in bots
          or not authorized(editor_login, data["author"])
        ):
          fields["error"] = "Disposition edits require an authorized human editor"
        candidates.append((comment, fields))
    latest = max(reversed(candidates), key=lambda c: c[0]["updatedAt"], default=None)
    status = "Unaddressed"
    reason = "No authorized disposition reply"
    if latest:
      _, fields = latest
      status = fields.get("disposition", "Unaddressed")
      reason = fields.get("error", "")
      if not reason and status == "Accepted":
        fix = fields.get("fix", "")
        link = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/commit/([^/]+)", fix)
        if link and link[1].lower() == data["repository"].lower():
          fix = link[2]
        fix = fix.lower()
        if not re.fullmatch(r"[0-9a-f]{40}", fix) or fix not in data["commits"]:
          reason = "Fix must be a current PR commit SHA or its repository commit URL"
      elif not reason and status == "Deferred":
        if not tracking_valid(fields.get("tracking", "")):
          reason = (
            "Tracking must reference an existing, readable, open issue in this "
            "repository; deleted issues cannot be deferred"
          )
    findings.append({
      "id": thread["id"],
      "url": root["url"],
      "status": status,
      "error": reason,
    })
  return {"errors": errors, "findings": findings}


def passed(result):
  return not result["errors"] and not any(f["error"] for f in result["findings"])


def summary(number, head, result):
  blocked = [f for f in result["findings"] if f["error"]]
  lines = [
    f"## PR #{number} · AI Review Disposition",
    "",
    f"Head: `{head}`",
    "",
    f"Findings: {len(result['findings'])}; blocked dispositions: {len(blocked)}",
    "",
    "Conversation resolution is enforced separately by GitHub repository rules.",
    "",
  ]
  lines.extend(f"- {error}" for error in result["errors"])
  for finding in blocked[:100]:
    lines.append(f"- [{finding['status']}]({finding['url']}): {finding['error']}")
  if len(blocked) > 100:
    lines.append(f"- {len(blocked) - 100} additional blocked findings")
  return "\n".join(lines)


def current_check(api, repository, number, head, check):
  """Verify ownership; the shared per-PR Actions queue serializes all writers."""
  live = api.request("GET", f"/repos/{repository}/check-runs/{check['id']}")
  return (
    live["name"] == CHECK_NAME
    and live["head_sha"] == head
    and live.get("external_id") == check["external_id"]
    and live["app"]["id"] == check["app"]["id"]
  )


def generation():
  return (
    # Run IDs are comparable across caller and recovery workflows; run numbers are not.
    int(os.environ.get("GITHUB_RUN_ID", os.environ.get("GITHUB_RUN_NUMBER", "0"))),
    int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
  )


def register_check(api, repository, number, head, sequence=None):
  """Get or reopen one managed Check. Call only inside the per-PR writer queue."""
  run, attempt = sequence if sequence is not None else generation()
  prefix = f"ai-review-disposition:{number}:"
  runs = api.pages(
    f"/repos/{repository}/commits/{head}/check-runs"
    f"?check_name={quote(CHECK_NAME, safe='')}&filter=all",
    "check_runs",
  )
  runs = [
    check
    for check in runs
    if check["app"].get("slug") == "github-actions"
    and (not check.get("external_id") or check["external_id"].startswith(prefix))
  ]

  def order(check):
    match = re.fullmatch(
      re.escape(prefix) + r"([0-9]+):([0-9]+)", check.get("external_id") or ""
    )
    return (int(match[1]), int(match[2]), check["id"]) if match else (0, 0, check["id"])

  previous = max(runs, key=order) if runs else None
  if previous and run and order(previous)[:2] > (run, attempt):
    return None
  body = {
    "name": CHECK_NAME,
    "status": "in_progress",
    "external_id": f"{prefix}{run}:{attempt}",
    "output": {"title": "Checking", "summary": f"Evaluating PR #{number} at {head}."},
  }
  if previous:
    check = api.request(
      "PATCH", f"/repos/{repository}/check-runs/{previous['id']}", body
    )
  else:
    check = api.request(
      "POST", f"/repos/{repository}/check-runs", {"head_sha": head, **body}
    )
  journal = os.environ.get("AI_REVIEW_CHECKS_FILE")
  if journal:
    with JOURNAL_LOCK, open(journal, "a", encoding="utf-8") as stream:
      stream.write(
        json.dumps({"number": number, "head": head, "sequence": [run, attempt]}) + "\n"
      )
  return check


def finish_pending(api, repository, number, head, sequence):
  """Close this job's unfinished Check before releasing its per-PR writer queue."""
  external_id = f"ai-review-disposition:{number}:{sequence[0]}:{sequence[1]}"
  runs = api.pages(
    f"/repos/{repository}/commits/{head}/check-runs"
    f"?check_name={quote(CHECK_NAME, safe='')}&filter=all",
    "check_runs",
  )
  own = [
    run
    for run in runs
    if run.get("external_id") == external_id
    and run["app"].get("slug") == "github-actions"
  ]
  if not own:
    return True
  check = max(own, key=lambda run: run["id"])
  if check["status"] == "completed":
    return True
  if current_check(api, repository, number, head, check):
    api.request(
      "PATCH",
      f"/repos/{repository}/check-runs/{check['id']}",
      {
        "status": "completed",
        "conclusion": "failure",
        "output": {
          "title": "Evaluation interrupted",
          "summary": "Evaluation interrupted; dispatch a new disposition check.",
        },
      },
    )
  return True


def cleanup_file(api, repository, path):
  if not os.path.exists(path):
    return True
  with open(path, encoding="utf-8") as stream:
    targets = {
      (item["number"], item["head"], tuple(item["sequence"]))
      for line in stream
      if line.strip()
      for item in [json.loads(line)]
    }
  ok = True
  for target in sorted(targets):
    ok = finish_pending(api, repository, *target) and ok
  return ok


def recovery_targets(api, repository, run_id):
  """Route recovery to the same per-PR workers; this function never writes Checks."""
  run = api.request("GET", f"/repos/{repository}/actions/runs/{run_id}")
  if run["path"] != ".github/workflows/ai-review-disposition.yml":
    raise ValueError("Recovery requires the documented caller workflow")
  if run["status"] != "completed" or run["conclusion"] not in {
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
  }:
    return []
  title = run["display_title"]
  if title == "all-open-prs":
    prs = api.pages(f"/repos/{repository}/pulls?state=open")
  elif re.fullmatch(r"[1-9][0-9]*", title):
    prs = [api.request("GET", f"/repos/{repository}/pulls/{title}")]
  else:
    raise ValueError("Recovery requires a PR number or all-open-prs run-name")
  return prs


def prepare_targets(api, repository, number=None, all_open=False, recover=None):
  """Enumerate trusted metadata for an Actions matrix, without publishing Checks."""
  if recover:
    prs = recovery_targets(api, repository, recover)
  elif all_open:
    prs = api.pages(f"/repos/{repository}/pulls?state=open")
  else:
    prs = [api.request("GET", f"/repos/{repository}/pulls/{number}")]
  targets = {
    pr["number"]: {"number": pr["number"], "head": pr["head"]["sha"]}
    for pr in prs
    if pr["state"] == "open"
  }
  if len(targets) > 256:
    raise ValueError("Actions matrix supports at most 256 open PRs per sweep")
  return [targets[number] for number in sorted(targets)]


def check_pr(api, repository, number, bots, head=None):
  try:
    pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
    if pr["state"] != "open":
      return True
    head = pr["head"]["sha"]
  except Exception:
    # A trusted routing snapshot lets registration revoke old success even if
    # this metadata read fails. The evaluation must still obtain live PR data.
    if head is None:
      raise
  check = register_check(api, repository, number, head)
  if check is None:
    return True

  @cache
  def authorized(login, author):
    if login == author:
      return True
    try:
      response = api.request(
        "GET", f"/repos/{repository}/collaborators/{quote(login, safe='')}/permission"
      )
      return response["permission"] in {"admin", "write"}
    except HTTPError as error:
      if error.code != 404:
        raise
      return False

  @cache
  def tracking_valid(value):
    match = re.fullmatch(
      r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)",
      value,
    )
    if not match or match[1].lower() != repository.lower():
      return False
    try:
      item = api.request("GET", f"/repos/{repository}/issues/{match[2]}")
      return (
        item["state"] == "open"
        and "pull_request" not in item
        and item["repository_url"].lower()
        == f"https://api.github.com/repos/{repository}".lower()
      )
    except HTTPError as error:
      if error.code not in {404, 410}:
        raise
      return False

  try:
    if not current_check(api, repository, number, head, check):
      return True
    data = snapshot(api, repository, number)
    if data["head"] != head or data["state"] != "open":
      raise RuntimeError("PR changed before evaluation; rerun required")
    result = evaluate(data, bots, authorized, tracking_valid)
    if passed(result):
      # A second read catches comments/commits changed during pagination.
      if snapshot(api, repository, number) != data:
        result["errors"].append(
          "PR review data changed during evaluation; rerun required"
        )
      else:
        authorized.cache_clear()
        tracking_valid.cache_clear()
        result = evaluate(data, bots, authorized, tracking_valid)
    ok = passed(result)
    report = summary(number, head, result)
  except Exception as error:
    ok = False
    # Avoid printing tokens, response bodies, or attacker-controlled exception text.
    report = (
      f"PR #{number}: evaluation failed ({type(error).__name__}); rerun required."
    )
  try:
    if not current_check(api, repository, number, head, check):
      return True
    api.request(
      "PATCH",
      f"/repos/{repository}/check-runs/{check['id']}",
      {
        "status": "completed",
        "conclusion": "success" if ok else "failure",
        "output": {"title": "Passed" if ok else "Failed", "summary": report[:60000]},
      },
    )
  except Exception as error:
    print(
      f"PR #{number}: could not publish Check #{check['id']} "
      f"({type(error).__name__}); check may remain in_progress; dispatch a new check.",
      file=sys.stderr,
    )
    return False
  # PR comment text is never emitted, including Actions workflow command sequences.
  print(report)
  if os.environ.get("GITHUB_STEP_SUMMARY"):
    with (
      JOURNAL_LOCK,
      open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream,
    ):
      stream.write(report + "\n\n")
  return ok


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
  target = parser.add_mutually_exclusive_group(required=True)
  target.add_argument("--pr-number", type=int, help="PR to check")
  target.add_argument(
    "--all-open-prs", action="store_true", help="Explicitly recheck every open PR"
  )
  target.add_argument(
    "--cleanup-file", help="Finish interrupted Checks from a trusted local journal"
  )
  target.add_argument(
    "--recover-run", type=int, help="Route an interrupted caller run to PR workers"
  )
  parser.add_argument(
    "--prepare-targets",
    action="store_true",
    help="Output the worker matrix; never write Checks",
  )
  parser.add_argument(
    "--head-sha",
    help="Trusted head from the routing job; register before reading PR data",
  )
  parser.add_argument("--bots", default=os.environ.get("AI_REVIEW_BOTS", ""))
  args = parser.parse_args()
  bots = {bot.strip().lower() for bot in args.bots.split(",") if bot.strip()}
  token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
  evaluating = not (args.cleanup_file or args.prepare_targets)
  if (
    (
      evaluating
      and (
        not bots
        or any(not re.fullmatch(r"[a-z0-9-]+(?:\[bot\])?", bot) for bot in bots)
      )
    )
    or not args.repository
    or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository)
    or (args.pr_number is not None and args.pr_number <= 0)
    or (args.recover_run is not None and args.recover_run <= 0)
    or ((args.recover_run or args.all_open_prs) and not args.prepare_targets)
    or (args.prepare_targets and args.cleanup_file)
    or (
      args.head_sha is not None
      and (not args.pr_number or not re.fullmatch(r"[0-9a-f]{40}", args.head_sha))
    )
    or not token
  ):
    parser.error(
      "Valid repository, bot logins, token and a positive PR or explicit sweep required"
    )
  api = GitHub(token)
  if args.cleanup_file:
    return 0 if cleanup_file(api, args.repository, args.cleanup_file) else 1
  if args.prepare_targets:
    targets = prepare_targets(
      api, args.repository, args.pr_number, args.all_open_prs, args.recover_run
    )
    matrix = json.dumps({"include": targets}, separators=(",", ":"))
    print(matrix)
    if os.environ.get("GITHUB_OUTPUT"):
      with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"matrix={matrix}\nhas-targets={'true' if targets else 'false'}\n")
    return 0
  if args.head_sha:
    ok = check_pr(api, args.repository, args.pr_number, bots, args.head_sha)
  else:
    ok = check_pr(api, args.repository, args.pr_number, bots)
  return 0 if ok else 1


if __name__ == "__main__":
  try:
    sys.exit(main())
  except Exception as failure:
    print(f"Disposition check failed: {type(failure).__name__}", file=sys.stderr)
    sys.exit(2)
