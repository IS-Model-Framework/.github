#!/usr/bin/env python3
"""Check AI review rounds and dispositions without running PR code."""

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from functools import cache
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

CHECK_NAME = "AI Review / Disposition Check"
STATUSES = ("Accepted", "Rejected", "Deferred", "Invalid")
AUTHOR_FIELDS = "author { login __typename }"
COMMENT_FIELDS = f"""
  id body url updatedAt {AUTHOR_FIELDS}
  pullRequestReview {{ state }}
"""
PAGE_FIELDS = "pageInfo { hasNextPage endCursor }"
CODEX = "chatgpt-codex-connector[bot]"
CODEX_SUMMARY = "<!-- codex-pull-request-review-summary -->"
PUBLISHED_REVIEWS = {"COMMENTED", "APPROVED", "CHANGES_REQUESTED"}
INFISCALE_COMPLETE = "未发现本次 PR 引入的新问题。LGTM！"  # noqa: RUF001


def actor_login(author):
  author = author or {}
  login = author.get("login", "").lower()
  if author.get("__typename") == "Bot" and not login.endswith("[bot]"):
    login += "[bot]"
  return login


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
    with urlopen(request, timeout=30) as response:
      return json.load(response)

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
    pr["node_id"], "comments", f"id body createdAt updatedAt {AUTHOR_FIELDS}"
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
    "commits": [node["commit"]["oid"] for node in commits],
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
  """Only one fixed-name gate remains; late writes cannot rename it back."""
  path = f"/repos/{repository}/commits/{head}/check-runs"
  runs = api.pages(
    f"{path}?check_name={quote(CHECK_NAME, safe='')}&filter=all", "check_runs"
  )
  prefix = f"ai-review-disposition:{number}:"
  runs = [
    run
    for run in runs
    if run["app"]["id"] == check["app"]["id"]
    and (not run.get("external_id") or run["external_id"].startswith(prefix))
  ]
  runs = {run["id"]: run for run in [*runs, check]}.values()

  def order(run):
    key = re.fullmatch(
      re.escape(prefix) + r"([0-9]+):([0-9]+)", run.get("external_id") or ""
    )
    return (int(key[1]), int(key[2]), run["id"]) if key and int(key[1]) else None

  managed = [run for run in runs if order(run)]
  newest = max(managed, key=order) if managed else check
  # An explicit local CLI check is ordered by registration, not an Actions sequence.
  newest = max(
    [newest, *(run for run in runs if not order(run))], key=lambda run: run["id"]
  )
  for run in runs:
    if run["id"] != newest["id"]:
      api.request(
        "PATCH",
        f"/repos/{repository}/check-runs/{run['id']}",
        {
          "name": f"{CHECK_NAME} (superseded #{run['id']})",
          "status": "completed",
          "conclusion": "cancelled",
        },
      )
  return newest["id"] == check["id"]


def check_pr(api, repository, number, bots):
  pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
  if pr["state"] != "open":
    return True
  head = pr["head"]["sha"]
  check = api.request(
    "POST",
    f"/repos/{repository}/check-runs",
    {
      "name": CHECK_NAME,
      "head_sha": head,
      "status": "in_progress",
      "external_id": f"ai-review-disposition:{number}:"
      f"{os.environ.get('GITHUB_RUN_NUMBER', '0')}:"
      f"{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}",
    },
  )

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
  # PR comment text is never emitted, including Actions workflow command sequences.
  print(report)
  if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
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
  parser.add_argument("--bots", default=os.environ.get("AI_REVIEW_BOTS", ""))
  args = parser.parse_args()
  bots = {bot.strip().lower() for bot in args.bots.split(",") if bot.strip()}
  token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
  if (
    not bots
    or any(not re.fullmatch(r"[a-z0-9-]+(?:\[bot\])?", bot) for bot in bots)
    or not args.repository
    or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository)
    or (args.pr_number is not None and args.pr_number <= 0)
    or not token
  ):
    parser.error(
      "Valid repository, bot logins, token and a positive PR or explicit sweep required"
    )
  api = GitHub(token)
  if args.all_open_prs:
    numbers = sorted({
      pr["number"] for pr in api.pages(f"/repos/{args.repository}/pulls?state=open")
    })
    ok = True
    for number in numbers:
      try:
        ok = check_pr(api, args.repository, number, bots) and ok
      except Exception as error:
        print(
          f"PR #{number}: check failed ({type(error).__name__}); rerun required.",
          file=sys.stderr,
        )
        ok = False
    return 0 if ok else 1
  ok = check_pr(api, args.repository, args.pr_number, bots)
  return 0 if ok else 1


if __name__ == "__main__":
  try:
    sys.exit(main())
  except Exception as failure:
    print(f"Disposition check failed: {type(failure).__name__}", file=sys.stderr)
    sys.exit(2)
