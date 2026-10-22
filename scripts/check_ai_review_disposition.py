#!/usr/bin/env python3
"""Check one PR's AI review completion and dispositions without running PR code."""

import argparse
import json
import os
import re
import sys
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


def snapshot(api, repository, number):
  pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
  reviews = api.connection(pr["node_id"], "reviews", f"id state {AUTHOR_FIELDS}")
  comments = api.connection(pr["node_id"], "comments", f"id body {AUTHOR_FIELDS}")
  threads = api.connection(
    pr["node_id"],
    "reviewThreads",
    f"""id isResolved
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
  if current["head"]["sha"] != pr["head"]["sha"]:
    raise RuntimeError("PR head changed while reading review data; rerun required")
  return {
    "repository": repository,
    "head": pr["head"]["sha"],
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


def completion_comment(comment):
  bot = actor_login(comment.get("author"))
  body = comment["body"].replace("\r\n", "\n").strip()
  if bot == "infiscale-bot":
    return body == "未发现本次 PR 引入的新问题。LGTM！"  # noqa: RUF001
  return (
    bot == "chatgpt-codex-connector[bot]"
    and body.startswith("<!-- codex-pull-request-review-summary -->\n")
    and bool(
      re.search(
        r"(?m)^\| 📝 \*\*Code Review\*\* \| ✅ \*\*Completed\*\*"
        r"(?: <relative-time [^\n|]*</relative-time>)? \|",
        body,
      )
    )
  )


def evaluate(data, bots, authorized, tracking_valid):
  """Check PR-wide completion, then apply the same reply policy to AI/human threads."""
  completed = {
    actor_login(review.get("author"))
    for review in data["reviews"]
    if review["state"] in {"COMMENTED", "APPROVED", "CHANGES_REQUESTED"}
  } | {
    actor_login(comment.get("author"))
    for comment in data["comments"]
    if completion_comment(comment)
  }
  errors = [
    f"{bot}: missing published review or successful completion comment"
    for bot in sorted(bots - completed)
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
          reason = "Tracking must reference an existing, readable, open GitHub issue"
      if not reason and not thread["isResolved"]:
        reason = "Disposition is valid; resolve the conversation"
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
    f"Findings: {len(result['findings'])}; unresolved requirements: {len(blocked)}",
    "",
  ]
  lines.extend(f"- {error}" for error in result["errors"])
  for finding in blocked[:100]:
    lines.append(f"- [{finding['status']}]({finding['url']}): {finding['error']}")
  if len(blocked) > 100:
    lines.append(f"- {len(blocked) - 100} additional blocked findings")
  return "\n".join(lines)


def check_pr(api, repository, number, bots):
  pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
  if pr["state"] != "open":
    return True
  head = pr["head"]["sha"]
  check = api.request(
    "POST",
    f"/repos/{repository}/check-runs",
    {"name": CHECK_NAME, "head_sha": head, "status": "in_progress"},
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
      r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*)",
      value,
    )
    if not match:
      return False
    try:
      item = api.request("GET", f"/repos/{match[1]}")
      return item["state"] == "open" and "pull_request" not in item
    except HTTPError as error:
      if error.code != 404:
        raise
      return False

  try:
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
    ok = passed(result)
    report = summary(number, head, result)
  except Exception as error:
    ok = False
    # Avoid printing tokens, response bodies, or attacker-controlled exception text.
    report = (
      f"PR #{number}: evaluation failed ({type(error).__name__}); rerun required."
    )
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
  parser.add_argument("--pr-number", type=int, required=True, help="PR to check")
  parser.add_argument("--bots", default=os.environ.get("AI_REVIEW_BOTS", ""))
  args = parser.parse_args()
  bots = {bot.strip().lower() for bot in args.bots.split(",") if bot.strip()}
  token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
  if (
    not bots
    or any(not re.fullmatch(r"[a-z0-9-]+(?:\[bot\])?", bot) for bot in bots)
    or not args.repository
    or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository)
    or args.pr_number <= 0
    or not token
  ):
    parser.error(
      "Valid repository, nonempty bot logins, PR number > 0 and token required"
    )
  api = GitHub(token)
  ok = check_pr(api, args.repository, args.pr_number, bots)
  return 0 if ok else 1


if __name__ == "__main__":
  try:
    sys.exit(main())
  except Exception as failure:
    print(f"Disposition check failed: {type(failure).__name__}", file=sys.stderr)
    sys.exit(2)
