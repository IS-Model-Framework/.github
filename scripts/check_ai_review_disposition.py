#!/usr/bin/env python3
"""Check one PR's AI review completion and dispositions without running PR code."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

CHECK_NAME = "AI Review / Disposition Check"
STATUSES = ("Accepted", "Rejected", "Deferred", "Invalid")
AUTHOR_FIELDS = "author { login __typename }"
COMMENT_FIELDS = f"""
  id body url createdAt updatedAt {AUTHOR_FIELDS}
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
    data = None
    if body is not None:
      data = json.dumps(body).encode("utf-8")

    request = Request(
      "https://api.github.com" + path,
      data=data,
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

  def connection(self, repository, number, field, selection):
    owner, name = repository.split("/")
    cursor = None
    nodes = []
    while True:
      query = f"""
        query($owner: String!, $name: String!, $number: Int!, $cursor: String) {{
          repository(owner: $owner, name: $name) {{
            pullRequest(number: $number) {{
              {field}(first: 100, after: $cursor) {{
                nodes {{ {selection} }} {PAGE_FIELDS}
              }}
            }}
          }}
        }}
      """
      data = self.graphql(
        query, {"owner": owner, "name": name, "number": number, "cursor": cursor}
      )
      page = data["repository"]["pullRequest"][field]
      nodes.extend(page["nodes"])
      if not page["pageInfo"]["hasNextPage"]:
        return nodes
      cursor = page["pageInfo"]["endCursor"]

  def comments(self, thread):
    page = thread["comments"]
    comments = list(page["nodes"])
    while page["pageInfo"]["hasNextPage"]:
      query = f"""
        query($id: ID!, $cursor: String) {{
          node(id: $id) {{ ... on PullRequestReviewThread {{
            comments(first: 100, after: $cursor) {{
              nodes {{ {COMMENT_FIELDS} }} {PAGE_FIELDS}
            }}
          }} }}
        }}
      """
      data = self.graphql(
        query, {"id": thread["id"], "cursor": page["pageInfo"]["endCursor"]}
      )
      page = data["node"]["comments"]
      comments.extend(page["nodes"])
    return comments


def snapshot(api, repository, number):
  pr = api.request("GET", f"/repos/{repository}/pulls/{number}")
  reviews = api.connection(
    repository,
    number,
    "reviews",
    f"id body state submittedAt {AUTHOR_FIELDS} commit {{ oid }}",
  )
  threads = api.connection(
    repository,
    number,
    "reviewThreads",
    f"""id isResolved isOutdated
      comments(first: 100) {{ nodes {{ {COMMENT_FIELDS} }} {PAGE_FIELDS} }}""",
  )
  for thread in threads:
    thread["comments"] = api.comments(thread)
  commits = api.connection(repository, number, "commits", "commit { oid }")
  current = api.request("GET", f"/repos/{repository}/pulls/{number}")
  if current["head"]["sha"] != pr["head"]["sha"]:
    raise RuntimeError("PR head changed while reading review data; rerun required")
  return {
    "repository": repository,
    "head": pr["head"]["sha"],
    "author": pr["user"]["login"].lower(),
    "state": current["state"],
    "reviews": reviews,
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


def evaluate(data, bots, authorized, tracking_valid):
  """Pure policy; API-dependent identity and tracking checks are supplied by caller."""
  errors = []
  findings = []
  completed_at = {}
  for bot in sorted(bots):
    reviews = [
      review
      for review in data["reviews"]
      if actor_login(review.get("author")) == bot and review["state"] != "PENDING"
    ]
    latest = max(reversed(reviews), key=lambda r: r["submittedAt"] or "", default=None)
    marker = f"AI-Review-Complete: {data['head']}"
    if (
      not latest
      or latest["state"] == "DISMISSED"
      or (latest.get("commit") or {}).get("oid") != data["head"]
      or marker not in [line.strip() for line in latest["body"].splitlines()]
    ):
      errors.append(f"{bot}: missing completed review for current head")
    else:
      completed_at[bot] = latest["submittedAt"]

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
    for bot in bots:
      if bot in completed_at and any(
        actor_login(c.get("author")) == bot and c["updatedAt"] > completed_at[bot]
        for c in comments
      ):
        error = f"{bot}: findings changed after review completion"
        if error not in errors:
          errors.append(error)
    reviewer_activity = root["updatedAt"]
    if reviewer in bots:
      reviewer_activity = max(
        c["updatedAt"] for c in comments if actor_login(c.get("author")) in bots
      )
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
      comment, fields = latest
      status = fields.get("disposition", "Unaddressed")
      reason = fields.get("error", "")
      if comment["updatedAt"] < reviewer_activity:
        reason = "Review comment changed after disposition; reply again"
      elif not reason and status == "Accepted":
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
  counts = Counter(f["status"] for f in result["findings"])
  remaining = sum(bool(f["error"]) for f in result["findings"])
  lines = [
    f"## PR #{number} · AI Review Disposition",
    "",
    f"Head: `{head}`",
    "",
    f"Findings: {len(result['findings'])}; unresolved requirements: {remaining}",
    "",
    "| Accepted | Rejected | Deferred | Invalid | Unaddressed |",
    "| --- | --- | --- | --- | --- |",
    "| " + " | ".join(str(counts[s]) for s in (*STATUSES, "Unaddressed")) + " |",
    "",
  ]
  lines.extend(f"- {error}" for error in result["errors"])
  blocked = [f for f in result["findings"] if f["error"]]
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
  permissions = {}
  tracking = {}

  def authorized(login, author):
    if login == author:
      return True
    if login not in permissions:
      try:
        response = api.request(
          "GET", f"/repos/{repository}/collaborators/{quote(login, safe='')}/permission"
        )
        permissions[login] = response["permission"] in {"admin", "maintain", "write"}
      except HTTPError as error:
        if error.code != 404:
          raise
        permissions[login] = False
    return permissions[login]

  def tracking_valid(value):
    if value not in tracking:
      match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*)",
        value,
      )
      valid = False
      if match:
        try:
          item = api.request("GET", f"/repos/{match[1]}")
          valid = item["state"] == "open" and "pull_request" not in item
        except HTTPError as error:
          if error.code != 404:
            raise
      tracking[value] = valid
    return tracking[value]

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
