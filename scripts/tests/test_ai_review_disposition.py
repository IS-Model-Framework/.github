import copy
import unittest
from unittest.mock import patch

from scripts.check_ai_review_disposition import (
  CHECK_NAME,
  GitHub,
  check_pr,
  evaluate,
  parse_disposition,
  passed,
  snapshot,
  summary,
  tracking_target,
)

HEAD = "a" * 40
FIX = "b" * 40
BOT = "reviewer[bot]"


def comment(body, login="contributor", timestamp="2026-10-09T02:00:00Z"):
  return {
    "id": "comment-" + login,
    "body": body,
    "url": "https://github.com/org/repo/pull/128#discussion_r1",
    "createdAt": timestamp,
    "updatedAt": timestamp,
    "author": {"login": login, "__typename": "Bot" if login == BOT else "User"},
    "pullRequestReview": {"state": "COMMENTED"},
  }


def fixture(body=None, resolved=True):
  comments = [comment("Please fix this", BOT, "2026-10-09T01:00:00Z")]
  if body:
    comments.append(comment(body))
  return {
    "head": HEAD,
    "author": "contributor",
    "state": "open",
    "commits": [FIX, HEAD],
    "reviews": [
      {
        "id": "review-1",
        "body": f"AI-Review-Complete: {HEAD}",
        "state": "COMMENTED",
        "submittedAt": "2026-10-09T01:30:00Z",
        "author": {"login": BOT},
        "commit": {"oid": HEAD},
      }
    ],
    "threads": [
      {
        "id": "thread-1",
        "isResolved": resolved,
        "isOutdated": False,
        "comments": comments,
      }
    ],
  }


def result(data, bots=None, authorized=None, tracking_valid=None):
  return evaluate(
    data,
    bots or {BOT},
    authorized or (lambda login, author: login in {author, "maintainer"}),
    tracking_valid or (lambda value: value == "https://github.com/org/repo/issues/42"),
  )


class PolicyTests(unittest.TestCase):
  def test_all_dispositions_pass_with_required_evidence(self):
    replies = [
      f"Disposition: Accepted\nReason: Added bounds check\nFix: {FIX}",
      "Disposition: Rejected\nReason: This input is already validated upstream",
      "Disposition: Invalid\nReason: The reported method does not exist",
      (
        "Disposition: Deferred\nReason: Follow-up agreed\n"
        "Tracking: https://github.com/org/repo/issues/42"
      ),
    ]
    for body in replies:
      with self.subTest(body=body):
        self.assertTrue(passed(result(fixture(body))))

  def test_resolve_alone_does_not_pass(self):
    evaluation = result(fixture())
    self.assertFalse(passed(evaluation))
    self.assertEqual(evaluation["findings"][0]["status"], "Unaddressed")

  def test_valid_reply_still_needs_resolve(self):
    data = fixture("Disposition: Rejected\nReason: Covered upstream", resolved=False)
    self.assertIn("resolve", result(data)["findings"][0]["error"])

  def test_outdated_findings_still_block(self):
    data = fixture()
    data["threads"][0]["isOutdated"] = True
    self.assertFalse(passed(result(data)))

  def test_accepted_requires_full_sha_still_in_pr(self):
    for fix in ("", FIX[:7], "c" * 40, "https://github.com/org/repo/commit/" + FIX):
      with self.subTest(fix=fix):
        self.assertFalse(
          passed(result(fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {fix}")))
        )

  def test_force_push_removing_fix_invalidates_acceptance(self):
    data = fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {FIX}")
    data["commits"] = [HEAD]
    self.assertFalse(passed(result(data)))

  def test_every_disposition_requires_reason(self):
    for status in ("Accepted", "Rejected", "Deferred", "Invalid"):
      with self.subTest(status=status):
        self.assertFalse(passed(result(fixture(f"Disposition: {status}"))))

  def test_deferred_requires_valid_tracking(self):
    for tracking in ("", "ISSUE-42", "https://evil.test/browse/PROJ-42"):
      data = fixture("Disposition: Deferred\nReason: Later\n" + f"Tracking: {tracking}")
      self.assertFalse(passed(result(data)))

  def test_unauthorized_replies_cannot_dispose(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["threads"][0]["comments"][-1]["author"]["login"] = "stranger"
    self.assertFalse(passed(result(data)))

  def test_bot_cannot_dispose_even_as_pr_author(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["author"] = "other[bot]"
    data["threads"][0]["comments"][-1]["author"] = {
      "login": "other[bot]",
      "__typename": "Bot",
    }
    self.assertFalse(passed(result(data)))

  def test_maintainer_can_dispose(self):
    data = fixture("Disposition: Rejected\nReason: Covered upstream")
    data["threads"][0]["comments"][-1]["author"]["login"] = "maintainer"
    self.assertTrue(passed(result(data)))

  def test_latest_explicit_reply_supersedes_old_valid_reply(self):
    data = fixture("Disposition: Rejected\nReason: Covered upstream")
    data["threads"][0]["comments"].append(
      comment("Disposition: Accepted", timestamp="2026-10-09T03:00:00Z")
    )
    self.assertFalse(passed(result(data)))

  def test_edit_timestamp_selects_latest_disposition(self):
    data = fixture("Disposition: Accepted")
    data["threads"][0]["comments"][-1]["updatedAt"] = "2026-10-09T04:00:00Z"
    data["threads"][0]["comments"].append(
      comment(
        "Disposition: Invalid\nReason: False positive", timestamp="2026-10-09T03:00:00Z"
      )
    )
    self.assertFalse(passed(result(data)))

  def test_ai_edit_invalidates_older_reply(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["threads"][0]["comments"][0]["updatedAt"] = "2026-10-09T03:00:00Z"
    self.assertIn("changed", result(data)["findings"][0]["error"])

  def test_missing_old_dismissed_or_incomplete_review_blocks(self):
    for mode in ("missing", "old", "dismissed", "incomplete", "pending"):
      with self.subTest(mode=mode):
        data = fixture("Disposition: Invalid\nReason: False positive")
        if mode == "missing":
          data["reviews"] = []
        elif mode == "old":
          data["reviews"][0]["commit"]["oid"] = FIX
        elif mode == "dismissed":
          data["reviews"][0]["state"] = "DISMISSED"
        elif mode == "pending":
          data["reviews"][0]["state"] = "PENDING"
        else:
          data["reviews"][0]["body"] = "No problems"
        self.assertFalse(passed(result(data)))

  def test_later_unfinished_review_supersedes_completion(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    review = copy.deepcopy(data["reviews"][0])
    review.update(id="review-2", body="Working", submittedAt="2026-10-09T03:00:00Z")
    data["reviews"].append(review)
    self.assertFalse(passed(result(data)))

  def test_zero_findings_pass_only_after_review_completion(self):
    data = fixture()
    data["threads"] = []
    self.assertTrue(passed(result(data)))
    data["reviews"] = []
    self.assertFalse(passed(result(data)))

  def test_every_configured_bot_must_complete_review(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    self.assertFalse(passed(result(data, bots={BOT, "another[bot]"})))

  def test_graphql_bot_login_without_suffix_is_canonicalized(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    for author in (
      data["reviews"][0]["author"],
      data["threads"][0]["comments"][0]["author"],
    ):
      author.update(login="reviewer", __typename="Bot")
    self.assertTrue(passed(result(data)))

  def test_ordinary_user_cannot_impersonate_bot_login(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["reviews"][0]["author"] = {"login": "reviewer", "__typename": "User"}
    self.assertFalse(passed(result(data)))

  def test_new_finding_after_completion_requires_new_marker(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["threads"][0]["comments"][0]["updatedAt"] = "2026-10-09T01:45:00Z"
    self.assertFalse(passed(result(data)))
    data["reviews"][0]["submittedAt"] = "2026-10-09T01:50:00Z"
    self.assertTrue(passed(result(data)))

  def test_human_root_thread_is_not_an_ai_finding(self):
    data = fixture()
    data["threads"][0]["comments"][0]["author"] = {
      "login": "maintainer",
      "__typename": "User",
    }
    self.assertTrue(passed(result(data)))

  def test_pending_review_comments_are_not_published_findings(self):
    data = fixture()
    data["threads"][0]["comments"][0]["pullRequestReview"]["state"] = "PENDING"
    self.assertTrue(passed(result(data)))

  def test_quoted_and_fenced_dispositions_are_not_replies(self):
    for text in (
      "> Disposition: Accepted",
      "```\nDisposition: Accepted\n```",
      "Accepted",
    ):
      self.assertIsNone(parse_disposition(text))

  def test_duplicate_and_unknown_fields_are_invalid(self):
    for text in (
      "Disposition: Invalid\nReason: First\nReason: Second",
      "Disposition: Invalid\nReason: False positive\nStatus: Done",
      "Disposition: Whatever\nReason: Test",
    ):
      self.assertIn("error", parse_disposition(text))

  def test_summary_separates_status_counts_and_remaining_requirements(self):
    data = fixture("Disposition: Accepted\nReason: Fixed", resolved=False)
    report = summary(128, HEAD, result(data))
    self.assertIn("unresolved requirements: 1", report)
    self.assertIn("| 1 | 0 | 0 | 0 | 0 |", report)

  def test_tracking_urls_are_bound_to_trusted_hosts(self):
    self.assertEqual(
      tracking_target(
        "https://github.com/org/repo/issues/42", "https://github.com", ""
      ),
      ("github", "org", "repo", "42"),
    )
    self.assertIsNotNone(
      tracking_target(
        "https://jira.test/browse/PROJ-42", "https://github.com", "https://jira.test"
      )
    )
    for url in (
      "http://jira.test/browse/PROJ-42",
      "https://jira.test.evil/browse/PROJ-42",
      "https://github.com/org/repo/pull/42",
      "https://github.com/org/repo/issues/42?redirect=1",
      "https://user@github.com/org/repo/issues/42",
    ):
      self.assertIsNone(tracking_target(url, "https://github.com", "https://jira.test"))


class ApiTests(unittest.TestCase):
  def test_connections_read_more_than_100_nodes(self):
    api = GitHub("test-token", "https://api.github.com")
    pages = [
      {
        "nodes": list(range(100)),
        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
      },
      {"nodes": [100], "pageInfo": {"hasNextPage": False, "endCursor": None}},
    ]
    responses = [{"repository": {"pullRequest": {"reviews": p}}} for p in pages]
    with patch.object(api, "graphql", side_effect=responses) as graphql:
      self.assertEqual(len(api.connection("org/repo", 128, "reviews", "id")), 101)
      self.assertEqual(graphql.call_args.args[1]["cursor"], "next")

  def test_long_thread_comments_are_paginated(self):
    api = GitHub("test-token", "https://api.github.com")
    thread = {
      "id": "thread-1",
      "comments": {
        "nodes": list(range(100)),
        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
      },
    }
    page = {"nodes": [100], "pageInfo": {"hasNextPage": False, "endCursor": None}}
    with patch.object(api, "graphql", return_value={"node": {"comments": page}}):
      self.assertEqual(api.comments(thread)[-1], 100)

  def test_graphql_errors_reject_partial_data(self):
    api = GitHub("test-token", "https://api.github.com")
    with patch.object(api, "request", return_value={"data": {}, "errors": [{}]}):
      with self.assertRaises(RuntimeError):
        api.graphql("query", {})

  def test_snapshot_rejects_head_change_during_read(self):
    api = GitHub("test-token", "https://api.github.com")
    before = {"head": {"sha": HEAD}, "user": {"login": "contributor"}, "state": "open"}
    after = {**before, "head": {"sha": FIX}}
    with (
      patch.object(api, "request", side_effect=[before, after]),
      patch.object(api, "connection", return_value=[]),
      self.assertRaises(RuntimeError),
    ):
      snapshot(api, "org/repo", 128)

  def test_check_is_written_to_pr_head_not_event_sha(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    calls, ok = self.run_check([data, copy.deepcopy(data)])
    self.assertTrue(ok)
    self.assertEqual(calls[1].args[2]["head_sha"], HEAD)
    self.assertEqual(calls[1].args[2]["name"], CHECK_NAME)
    self.assertEqual(calls[-1].args[2]["conclusion"], "success")

  def test_new_finding_during_read_prevents_success(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    changed = copy.deepcopy(data)
    changed["threads"].append(fixture()["threads"][0])
    calls, ok = self.run_check([data, changed])
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_api_failure_finishes_check_as_failure(self):
    calls, ok = self.run_check(RuntimeError("failed"))
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_closed_tracking_issue_or_pr_cannot_defer(self):
    data = fixture(
      "Disposition: Deferred\nReason: Later\nTracking: https://github.com/org/repo/issues/42"
    )
    for item in ({"state": "closed"}, {"state": "open", "pull_request": {}}):
      with self.subTest(item=item):
        calls, ok = self.run_check([data], item)
        self.assertFalse(ok)
        self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def run_check(self, snapshots, issue=None):
    api = GitHub("test-token", "https://api.github.com")
    pr = {"head": {"sha": HEAD}, "state": "open"}
    responses = [pr, {"id": 99}]
    if issue is not None:
      responses.append(issue)
    responses.append({})
    with (
      patch.object(api, "request", side_effect=responses) as request,
      patch("scripts.check_ai_review_disposition.snapshot", side_effect=snapshots),
      patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}),
      patch("builtins.print"),
    ):
      ok = check_pr(api, "org/repo", 128, {BOT}, "https://github.com", "")
      return request.call_args_list, ok


if __name__ == "__main__":
  unittest.main()
