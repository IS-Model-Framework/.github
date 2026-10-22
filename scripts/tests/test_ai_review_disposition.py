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
CODEX = "chatgpt-codex-connector[bot]"
CODEX_SUMMARY = (
  "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
  "| Review | Status | Commit | Review trigger |\n"
  "| --- | --- | --- | --- |\n"
  "| 📝 **Code Review** | ✅ **Completed** "
  '<relative-time datetime="2026-10-14T09:05:35Z">'
  "2026-10-14T09:05:35Z</relative-time> | `7a7a473` | PR opened |"
)
INFISCALE_COMPLETE = "未发现本次 PR 引入的新问题。LGTM！"  # noqa: RUF001
INFISCALE_START = "收到，开始 review"  # noqa: RUF001


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


def fixture(body=None, resolved=True, reviewer=BOT):
  comments = [comment("Please fix this", reviewer, "2026-10-09T01:00:00Z")]
  if body:
    comments.append(comment(body))
  return {
    "repository": "org/repo",
    "head": HEAD,
    "author": "contributor",
    "state": "open",
    "commits": [FIX, HEAD],
    "reviews": [
      {
        "id": "review-1",
        "body": "",
        "state": "COMMENTED",
        "submittedAt": "2026-10-09T01:30:00Z",
        "author": {"login": BOT},
        "commit": {"oid": HEAD},
      }
    ],
    "comments": [],
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
    for reviewer in (BOT, "human-reviewer"):
      for body in replies:
        with self.subTest(reviewer=reviewer, body=body):
          self.assertTrue(passed(result(fixture(body, reviewer=reviewer))))

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
    for fix in ("", FIX[:7], "c" * 40):
      with self.subTest(fix=fix):
        self.assertFalse(
          passed(result(fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {fix}")))
        )

  def test_force_push_removing_fix_invalidates_acceptance(self):
    for fix in (FIX, f"https://github.com/org/repo/commit/{FIX}"):
      with self.subTest(fix=fix):
        data = fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {fix}")
        data["commits"] = [HEAD]
        self.assertFalse(passed(result(data)))

  def test_commit_links_and_sha_share_pr_membership_validation(self):
    for reviewer in (BOT, "human-reviewer"):
      for fix in (
        FIX,
        f"https://github.com/org/repo/commit/{FIX}",
        f"https://github.com/ORG/REPO/commit/{FIX.upper()}",
      ):
        with self.subTest(reviewer=reviewer, fix=fix):
          data = fixture(
            f"Disposition: Accepted\nReason: Fixed\nFix: {fix}", reviewer=reviewer
          )
          self.assertTrue(passed(result(data)))
          self.assertEqual(len(result(data)["findings"]), 1)

  def test_commit_links_require_the_current_repository_and_a_full_sha(self):
    for fix in (
      f"http://github.com/org/repo/commit/{FIX}",
      f"https://github.com.evil/org/repo/commit/{FIX}",
      f"https://user@github.com/org/repo/commit/{FIX}",
      f"https://github.com/other/repo/commit/{FIX}",
      f"https://github.com/org/other/commit/{FIX}",
      f"https://github.com/org/repo/COMMIT/{FIX}",
      f"https://github.com/org/repo/pull/128/commits/{FIX}",
      "https://github.com/org/repo/issues/42",
      f"https://github.com/org/repo/commit/{FIX[:7]}",
      f"https://github.com/org/repo/commit/{'g' * 40}",
      f"https://github.com/org/repo/commit/{'c' * 40}",
      f"https://github.com/org/repo/commit/{FIX}?query=1",
      f"https://github.com/org/repo/commit/{FIX}#diff-1",
      f"https://github.com/org/repo/commit/{FIX}/extra",
    ):
      with self.subTest(fix=fix):
        data = fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {fix}")
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
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture(
          "Disposition: Rejected\nReason: Covered upstream", reviewer=reviewer
        )
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

  def test_missing_pending_or_dismissed_review_blocks(self):
    for state in (None, "DISMISSED", "PENDING"):
      with self.subTest(state=state):
        data = fixture("Disposition: Invalid\nReason: False positive")
        if state is None:
          data["reviews"] = []
        else:
          data["reviews"][0]["state"] = state
        self.assertFalse(passed(result(data)))

  def test_published_review_needs_neither_current_head_nor_custom_marker(self):
    for state in ("COMMENTED", "APPROVED", "CHANGES_REQUESTED"):
      with self.subTest(state=state):
        data = fixture("Disposition: Invalid\nReason: False positive")
        data["reviews"][0].update(state=state, body="", commit={"oid": FIX})
        self.assertTrue(passed(result(data)))

  def test_later_pending_review_does_not_erase_a_published_review(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    review = copy.deepcopy(data["reviews"][0])
    review.update(id="review-2", state="PENDING", submittedAt=None)
    data["reviews"].append(review)
    self.assertTrue(passed(result(data)))

  def test_zero_findings_pass_only_after_review_completion(self):
    data = fixture()
    data["threads"] = []
    self.assertTrue(passed(result(data)))
    data["reviews"] = []
    self.assertFalse(passed(result(data)))

  def test_every_configured_bot_must_complete_review(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    self.assertFalse(passed(result(data, bots={BOT, "another[bot]"})))

  def test_both_bots_can_complete_without_findings(self):
    data = fixture()
    data.update(reviews=[], threads=[])
    bots = {CODEX, "infiscale-bot"}
    data["comments"] = [comment(CODEX_SUMMARY, CODEX)]
    self.assertFalse(passed(result(data, bots=bots)))
    data["comments"].append(comment(INFISCALE_COMPLETE, "infiscale-bot"))
    evaluation = result(data, bots=bots)
    self.assertTrue(passed(evaluation))
    self.assertEqual(evaluation["findings"], [])
    data["threads"] = fixture(reviewer="human-reviewer")["threads"]
    self.assertFalse(passed(result(data, bots=bots)))

  def test_completion_comments_require_the_exact_bot_and_success_format(self):
    cases = (
      (CODEX, CODEX_SUMMARY, True),
      (CODEX, CODEX_SUMMARY.replace("✅ **Completed**", "⚠️ **Failed**"), False),
      (CODEX, CODEX_SUMMARY.replace("✅ **Completed**", "⏳ **In progress**"), False),
      (CODEX, CODEX_SUMMARY.replace("**Code Review**", "**Security Review**"), False),
      (
        CODEX,
        CODEX_SUMMARY.replace("<!-- codex-pull-request-review-summary -->", ""),
        False,
      ),
      (CODEX, "Codex usage limits have been reached for code reviews.", False),
      (CODEX, "Completed", False),
      (CODEX, INFISCALE_COMPLETE, False),
      ("infiscale-bot", INFISCALE_COMPLETE, True),
      ("infiscale-bot", "\n" + INFISCALE_COMPLETE + "\n", True),
      ("infiscale-bot", INFISCALE_START, False),
      ("infiscale-bot", "review failed", False),
      ("infiscale-bot", "LGTM!", False),
      ("infiscale-bot", CODEX_SUMMARY, False),
    )
    for bot, body, expected in cases:
      for author in (bot, "contributor", "other-bot"):
        with self.subTest(bot=bot, body=body, author=author):
          data = fixture()
          data.update(reviews=[], threads=[], comments=[comment(body, author)])
          self.assertEqual(passed(result(data, bots={bot})), expected and author == bot)

  def test_quoted_completion_text_does_not_count(self):
    for bot, body in ((CODEX, CODEX_SUMMARY), ("infiscale-bot", INFISCALE_COMPLETE)):
      for quoted in ("> " + body, "```\n" + body + "\n```", "Example:\n" + body):
        with self.subTest(bot=bot, body=quoted):
          data = fixture()
          data.update(reviews=[], threads=[], comments=[comment(quoted, bot)])
          self.assertFalse(passed(result(data, bots={bot})))

  def test_infiscale_start_comment_does_not_replace_a_published_review(self):
    data = fixture("Disposition: Invalid\nReason: Test", reviewer="infiscale-bot")
    data["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
    data["comments"] = [comment(INFISCALE_START, "infiscale-bot")]
    self.assertTrue(passed(result(data, bots={"infiscale-bot"})))

  def test_codex_completion_accepts_graphql_bot_login_without_suffix(self):
    data = fixture()
    completed = comment(CODEX_SUMMARY, CODEX)
    completed["author"] = {"login": "chatgpt-codex-connector", "__typename": "Bot"}
    data.update(reviews=[], threads=[], comments=[completed])
    self.assertTrue(passed(result(data, bots={CODEX})))

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

  def test_new_finding_requires_disposition_without_requiring_a_new_review(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    data["threads"][0]["comments"][0]["updatedAt"] = "2026-10-09T01:45:00Z"
    self.assertTrue(passed(result(data)))
    data["threads"].append(fixture()["threads"][0])
    self.assertFalse(passed(result(data)))

  def test_human_findings_require_complete_dispositions(self):
    for body in (
      None,
      "done",
      "Disposition: Rejected",
      "Disposition: Unknown\nReason: Test",
      "Disposition: Accepted\nReason: Fixed",
      f"Disposition: Accepted\nReason: Fixed\nFix: {'c' * 40}",
      "Disposition: Deferred\nReason: Later",
      "Disposition: Deferred\nReason: Later\nTracking: ISSUE-42",
    ):
      with self.subTest(body=body):
        evaluation = result(fixture(body, reviewer="human-reviewer"))
        self.assertFalse(passed(evaluation))
        self.assertEqual(len(evaluation["findings"]), 1)

  def test_human_findings_keep_resolve_and_reply_permissions(self):
    for resolved, login in ((False, "contributor"), (True, "stranger"), (True, BOT)):
      with self.subTest(resolved=resolved, login=login):
        data = fixture("Disposition: Invalid\nReason: Test", resolved, "human-reviewer")
        data["threads"][0]["comments"][-1]["author"]["login"] = login
        data["threads"][0]["isOutdated"] = True
        evaluation = result(data)
        self.assertFalse(passed(evaluation))
        self.assertEqual(len(evaluation["findings"]), 1)
    data["threads"][0]["comments"][-1]["author"]["login"] = "maintainer"
    self.assertTrue(passed(result(data)))

  def test_pr_author_deleted_author_and_unconfigured_bots_are_excluded(self):
    for author in (
      {"login": "contributor", "__typename": "User"},
      None,
      {"login": "other", "__typename": "Bot"},
    ):
      with self.subTest(author=author):
        data = fixture()
        data["threads"][0]["comments"][0]["author"] = author
        self.assertEqual(result(data)["findings"], [])
        self.assertTrue(passed(result(data)))

  def test_configured_user_bot_takes_priority_over_pr_author_exclusion(self):
    for owner in ("contributor", "infiscale-bot"):
      with self.subTest(owner=owner):
        data = fixture("Disposition: Invalid\nReason: Test", reviewer="infiscale-bot")
        data["author"] = owner
        data["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
        data["threads"][0]["comments"][-1]["author"]["login"] = "maintainer"
        self.assertTrue(passed(result(data, bots={"infiscale-bot"})))
        data["threads"][0]["comments"][-1]["author"]["login"] = "infiscale-bot"
        self.assertFalse(passed(result(data, bots={"infiscale-bot"})))

  def test_editing_the_human_root_requires_a_fresh_disposition(self):
    data = fixture("Disposition: Invalid\nReason: Test", reviewer="human-reviewer")
    comments = data["threads"][0]["comments"]
    comments[0]["updatedAt"] = "2026-10-09T03:00:00Z"
    evaluation = result(data)
    self.assertEqual(evaluation["errors"], [])
    self.assertIn("changed", evaluation["findings"][0]["error"])
    comments[1]["updatedAt"] = "2026-10-09T04:00:00Z"
    self.assertTrue(passed(result(data)))

  def test_human_follow_up_keeps_the_existing_disposition(self):
    for text in ("OK", "Thanks", "Follow-up discussion"):
      with self.subTest(text=text):
        data = fixture("Disposition: Invalid\nReason: Test", reviewer="human-reviewer")
        data["threads"][0]["comments"].append(
          comment(text, "human-reviewer", "2026-10-09T03:00:00Z")
        )
        self.assertTrue(passed(result(data)))
        data["threads"][0]["isResolved"] = False
        self.assertIn("resolve", result(data)["findings"][0]["error"])

  def test_ai_follow_up_still_requires_a_fresh_disposition(self):
    data = fixture("Disposition: Invalid\nReason: Test")
    data["threads"][0]["comments"].append(
      comment("Follow up", BOT, "2026-10-09T03:00:00Z")
    )
    data["reviews"][0]["submittedAt"] = "2026-10-09T03:30:00Z"
    evaluation = result(data)
    self.assertEqual(evaluation["errors"], [])
    self.assertIn("changed", evaluation["findings"][0]["error"])

  def test_human_reviews_do_not_require_completion_markers(self):
    data = fixture("Disposition: Invalid\nReason: Test", reviewer="human-reviewer")
    data["threads"][0]["comments"][0]["updatedAt"] = "2026-10-09T01:45:00Z"
    human_review = copy.deepcopy(data["reviews"][0])
    human_review.update(author={"login": "human-reviewer"}, body="General feedback")
    data["reviews"].append(human_review)
    self.assertTrue(passed(result(data)))
    data["reviews"].pop(0)
    self.assertFalse(passed(result(data)))

  def test_unaddressed_human_finding_blocks_an_addressed_ai_finding(self):
    data = fixture("Disposition: Invalid\nReason: Test")
    other = fixture(reviewer="human-reviewer")["threads"][0]
    other["id"] = "human-thread"
    data["threads"].append(other)
    evaluation = result(data)
    self.assertFalse(passed(evaluation))
    self.assertEqual(len(evaluation["findings"]), 2)
    self.assertEqual(evaluation["findings"][0]["error"], "")
    self.assertEqual(evaluation["findings"][1]["status"], "Unaddressed")

  def test_pending_review_comments_are_not_published_findings(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture(reviewer=reviewer)
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
      self.assertEqual(api.request("GET", "/repos/org/repo/pulls/313"), {"number": 313})
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
    for field in ("reviews", "comments"):
      with self.subTest(field=field):
        responses = [{"repository": {"pullRequest": {field: p}}} for p in pages]
        with patch.object(api, "graphql", side_effect=responses) as graphql:
          self.assertEqual(len(api.connection("org/repo", 128, field, "id")), 101)
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

  def test_snapshot_retains_repository_for_commit_link_validation(self):
    pr = {"head": {"sha": HEAD}, "user": {"login": "contributor"}, "state": "open"}
    api = GitHub("test-token")
    with (
      patch.object(api, "request", return_value=pr),
      patch.object(api, "connection", return_value=[]),
    ):
      self.assertEqual(snapshot(api, "org/repo", 128)["repository"], "org/repo")

  def test_snapshot_reads_top_level_comments_for_completion(self):
    pr = {"head": {"sha": HEAD}, "user": {"login": "contributor"}, "state": "open"}
    api = GitHub("test-token")
    completed = comment(INFISCALE_COMPLETE, "infiscale-bot")
    with (
      patch.object(api, "request", return_value=pr),
      patch.object(
        api, "connection", side_effect=[[], [completed], [], []]
      ) as connection,
    ):
      data = snapshot(api, "org/repo", 128)
    self.assertEqual(data["comments"], [completed])
    self.assertEqual(connection.call_args_list[1].args[2], "comments")
    self.assertNotIn("pullRequestReview", connection.call_args_list[1].args[3])

  def test_commit_link_does_not_add_api_requests(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture(
          f"Disposition: Accepted\nReason: Fixed\nFix: https://github.com/org/repo/commit/{FIX}",
          reviewer=reviewer,
        )
        calls, ok = self.run_check([data, copy.deepcopy(data)])
        self.assertTrue(ok)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1].args[2]["conclusion"], "success")

  def test_check_is_written_to_pr_head_not_event_sha(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    calls, ok = self.run_check([data, copy.deepcopy(data)])
    self.assertTrue(ok)
    self.assertEqual(calls[1].args[2]["head_sha"], HEAD)
    self.assertEqual(calls[1].args[2]["name"], CHECK_NAME)
    self.assertEqual(calls[-1].args[2]["conclusion"], "success")

  def test_new_finding_during_read_prevents_success(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture("Disposition: Invalid\nReason: False positive")
        changed = copy.deepcopy(data)
        changed["threads"].append(fixture(reviewer=reviewer)["threads"][0])
        calls, ok = self.run_check([data, changed])
        self.assertFalse(ok)
        self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_completion_comment_changed_during_read_prevents_success(self):
    data = fixture("Disposition: Invalid\nReason: Test")
    data["comments"] = [comment(INFISCALE_COMPLETE, "infiscale-bot")]
    changed = copy.deepcopy(data)
    changed["comments"][0]["body"] = INFISCALE_START
    calls, ok = self.run_check([data, changed])
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_api_failure_finishes_check_as_failure(self):
    calls, ok = self.run_check(RuntimeError("failed"))
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_closed_tracking_issue_or_pr_cannot_defer(self):
    for reviewer in (BOT, "human-reviewer"):
      data = fixture(
        "Disposition: Deferred\nReason: Later\n"
        "Tracking: https://github.com/org/repo/issues/42",
        reviewer=reviewer,
      )
      for item in ({"state": "closed"}, {"state": "open", "pull_request": {}}):
        with self.subTest(reviewer=reviewer, item=item):
          calls, ok = self.run_check([data], item)
          self.assertFalse(ok)
          self.assertEqual(calls[-1].args[2]["conclusion"], "failure")

  def test_deferred_requires_a_successful_open_issue_lookup(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture(
          "Disposition: Deferred\nReason: Follow up\n"
          "Tracking: https://github.com/org/repo/issues/42",
          reviewer=reviewer,
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
    issue_reads = [
      c for c in calls if c.args[:2] == ("GET", "/repos/org/repo/issues/42")
    ]
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
      patch(
        "scripts.check_ai_review_disposition.check_pr", return_value=passed
      ) as check,
      patch("sys.stderr", new_callable=io.StringIO),
    ):
      return main(), client, check

  def test_checks_only_the_requested_pr(self):
    code, client, check = self.run_main("--pr-number", "313")
    self.assertEqual(code, 0)
    client.assert_called_once_with("test-token")
    check.assert_called_once_with(client.return_value, "org/repo", 313, {BOT})
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
