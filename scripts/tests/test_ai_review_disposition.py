import copy
import io
import json
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.check_ai_review_disposition import (
  CHECK_NAME,
  GitHub,
  check_pr,
  evaluate,
  main,
  parse_disposition,
  passed,
  snapshot,
  summary,
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


class ApiTests(unittest.TestCase):
  def test_http_client_reads_and_writes_github_json(self):
    api = GitHub("test-token")
    calls = []
    responses = [
      {"number": 313},
      {"data": {"viewer": {"login": "reviewer"}}},
      {"id": 99},
      {"conclusion": "success"},
    ]

    def respond(request, timeout):
      self.assertEqual(timeout, 30)
      calls.append(request)
      return io.BytesIO(json.dumps(responses[len(calls) - 1]).encode())

    with patch("scripts.check_ai_review_disposition.urlopen", side_effect=respond):
      self.assertEqual(
        api.request("GET", "/repos/org/repo/pulls/313"), {"number": 313}
      )
      self.assertEqual(
        api.graphql("query { viewer { login } }", {}),
        {"viewer": {"login": "reviewer"}},
      )
      self.assertEqual(
        api.request("POST", "/repos/org/repo/check-runs", {"head_sha": HEAD}),
        {"id": 99},
      )
      self.assertEqual(
        api.request(
          "PATCH", "/repos/org/repo/check-runs/99", {"output": {"summary": "检查通过"}}
        ),
        {"conclusion": "success"},
      )
    self.assertEqual([r.get_method() for r in calls], ["GET", "POST", "POST", "PATCH"])
    self.assertEqual(
      [r.full_url for r in calls],
      [
        "https://api.github.com/repos/org/repo/pulls/313",
        "https://api.github.com/graphql",
        "https://api.github.com/repos/org/repo/check-runs",
        "https://api.github.com/repos/org/repo/check-runs/99",
      ],
    )
    self.assertIsNone(calls[0].data)
    self.assertEqual(
      json.loads(calls[1].data),
      {"query": "query { viewer { login } }", "variables": {}},
    )
    self.assertEqual(json.loads(calls[2].data), {"head_sha": HEAD})
    self.assertEqual(json.loads(calls[3].data)["output"]["summary"], "检查通过")
    self.assertTrue(
      all(r.get_header("Authorization") == "Bearer test-token" for r in calls)
    )

  def test_connections_read_more_than_100_nodes(self):
    api = GitHub("test-token")
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
    api = GitHub("test-token")
    thread = {
      "id": "thread-1",
      "comments": {
        "nodes": list(range(100)),
        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
      },
    }
    page = {"nodes": [100], "pageInfo": {"hasNextPage": False, "endCursor": None}}
    with patch.object(api, "graphql", return_value={"node": {"comments": page}}):
      comments = api.comments(thread)
    self.assertEqual(len(comments), 101)
    self.assertEqual(comments[-1], 100)
    self.assertEqual(len(thread["comments"]["nodes"]), 100)

  def test_graphql_errors_reject_partial_data(self):
    api = GitHub("test-token")
    with patch.object(api, "request", return_value={"data": {}, "errors": [{}]}):
      with self.assertRaises(RuntimeError):
        api.graphql("query", {})

  def test_snapshot_rejects_head_change_during_read(self):
    api = GitHub("test-token")
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

  def test_deferred_requires_a_successful_open_issue_lookup(self):
    data = fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    calls, ok = self.run_check([data, copy.deepcopy(data)], {"state": "open"})
    self.assertTrue(ok)
    self.assertEqual(calls[2].args, ("GET", "/repos/org/repo/issues/42"))
    self.assertEqual(calls[-1].args[2]["conclusion"], "success")

  def test_missing_unreadable_or_failed_issue_lookup_blocks_deferred(self):
    data = fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    for status in (404, 401, 403, 429, 500):
      with self.subTest(status=status):
        error = HTTPError("https://api.github.com/issue", status, "error", {}, None)
        calls, ok = self.run_check([data], error)
        self.assertFalse(ok)
        self.assertEqual(calls[2].args, ("GET", "/repos/org/repo/issues/42"))
        self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_only_canonical_github_issue_urls_are_queried(self):
    for url in (
      "https://jira.test/browse/PROJ-42",
      "https://github.com.evil/org/repo/issues/42",
      "http://github.com/org/repo/issues/42",
      "https://github.com/org/repo/pull/42",
      "https://github.com/org/repo/issues/0",
      "https://github.com/org/repo/issues/42?redirect=1",
      "https://github.com/org/repo/issues/42#issuecomment-1",
      "https://user@github.com/org/repo/issues/42",
      "PROJ-42",
    ):
      with self.subTest(url=url):
        data = fixture(f"Disposition: Deferred\nReason: Follow up\nTracking: {url}")
        calls, ok = self.run_check([data])
        self.assertFalse(ok)
        # Only PR metadata, Check creation, and Check completion; no issue request.
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_multiple_findings_share_one_issue_lookup(self):
    data = fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    other = copy.deepcopy(data["threads"][0])
    other["id"] = "thread-2"
    data["threads"].append(other)
    calls, ok = self.run_check([data, copy.deepcopy(data)], {"state": "open"})
    self.assertTrue(ok)
    issue_reads = [c for c in calls if c.args[:2] == ("GET", "/repos/org/repo/issues/42")]
    self.assertEqual(len(issue_reads), 1)

  def run_check(self, snapshots, issue=None):
    api = GitHub("test-token")
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
      ok = check_pr(api, "org/repo", 128, {BOT})
      return request.call_args_list, ok


class MainTests(unittest.TestCase):
  def run_main(self, *arguments, passed=True):
    with (
      patch.dict("os.environ", {"GH_TOKEN": "test-token"}, clear=True),
      patch.object(
        sys, "argv", ["checker", "--repository", "org/repo", "--bots", BOT, *arguments]
      ),
      patch("scripts.check_ai_review_disposition.GitHub") as client,
      patch("scripts.check_ai_review_disposition.check_pr", return_value=passed) as check,
      patch("sys.stderr", new_callable=io.StringIO),
    ):
      return main(), client, check

  def test_checks_only_the_requested_pr(self):
    code, client, check = self.run_main("--pr-number", "313")
    self.assertEqual(code, 0)
    client.assert_called_once_with("test-token")
    check.assert_called_once_with(
      client.return_value, "org/repo", 313, {BOT}
    )
    client.return_value.open_prs.assert_not_called()

  def test_target_pr_failure_sets_exit_code(self):
    code, _, check = self.run_main("--pr-number", "313", passed=False)
    self.assertEqual(code, 1)
    self.assertEqual(check.call_count, 1)

  def test_missing_or_invalid_pr_never_defaults_to_a_repository_scan(self):
    for arguments in (
      (),
      ("--pr-number", "0"),
      ("--pr-number", "-1"),
      ("--pr-number", "1.5"),
    ):
      with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
        self.run_main(*arguments)
      self.assertEqual(error.exception.code, 2)

  def test_closed_pr_does_not_create_a_check(self):
    api = GitHub("test-token")
    with patch.object(api, "request", return_value={"state": "closed"}) as request:
      self.assertTrue(check_pr(api, "org/repo", 313, {BOT}))
    request.assert_called_once_with("GET", "/repos/org/repo/pulls/313")

  def test_removed_jira_option_is_not_accepted(self):
    with self.assertRaises(SystemExit) as error:
      self.run_main("--pr-number", "313", "--jira-url", "https://jira.test")
    self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
  unittest.main()
