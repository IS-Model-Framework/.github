import copy
import io
import json
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from scripts.check_ai_review_disposition import (
  CHECK_NAME,
  GitHub,
  check_pr,
  current_status,
  register_status,
  prepare_targets,
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
        "createdAt": "2026-10-09T01:00:00Z",
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


def pr_metadata(**changes):
  return {
    "node_id": "pr-1",
    "head": {"sha": HEAD},
    "user": {"login": "contributor"},
    "state": "open",
    **changes,
  }


def page(nodes, next_cursor=None):
  return {
    "nodes": nodes,
    "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
  }


class PolicyTests(unittest.TestCase):
  def test_all_dispositions_pass_independently_of_resolution(self):
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
        for resolved in (False, True):
          with self.subTest(reviewer=reviewer, body=body, resolved=resolved):
            self.assertTrue(passed(result(fixture(body, resolved, reviewer))))

  def test_resolve_alone_does_not_pass(self):
    evaluation = result(fixture())
    self.assertFalse(passed(evaluation))
    self.assertEqual(evaluation["findings"][0]["status"], "Unaddressed")

  def test_valid_reply_does_not_require_resolution_field(self):
    data = fixture("Disposition: Rejected\nReason: Covered upstream")
    del data["threads"][0]["isResolved"]
    self.assertTrue(passed(result(data)))

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

  def test_newer_pending_review_waits_for_new_completion(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    review = copy.deepcopy(data["reviews"][0])
    review.update(
      id="review-2", state="PENDING", submittedAt=None, createdAt="2026-10-09T03:00:00Z"
    )
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
      (CODEX, CODEX_SUMMARY.replace("\n", "\r\n"), True),
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

  def test_new_infiscale_start_invalidates_old_completion(self):
    data = fixture("Disposition: Invalid\nReason: Test", reviewer="infiscale-bot")
    data["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
    data["comments"] = [comment(INFISCALE_START, "infiscale-bot")]
    self.assertFalse(passed(result(data, bots={"infiscale-bot"})))

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

  def test_human_findings_keep_reply_permissions(self):
    for login in ("stranger", BOT):
      with self.subTest(login=login):
        data = fixture("Disposition: Invalid\nReason: Test", False, "human-reviewer")
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

  def test_discussion_and_root_edits_do_not_require_another_disposition(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture("Disposition: Invalid\nReason: Test", reviewer=reviewer)
        data["threads"][0]["comments"][0]["updatedAt"] = "2026-10-09T03:00:00Z"
        data["threads"][0]["comments"].append(
          comment("Thanks", reviewer, "2026-10-09T03:00:00Z")
        )
        self.assertTrue(passed(result(data)))
        data["threads"][0]["isResolved"] = False
        self.assertTrue(passed(result(data)))

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

  def test_summary_links_to_blocked_threads(self):
    data = fixture("Disposition: Accepted\nReason: Fixed", resolved=False)
    report = summary(128, HEAD, result(data))
    self.assertIn("blocked dispositions: 1", report)
    self.assertIn("Conversation resolution is enforced separately", report)
    self.assertIn(data["threads"][0]["comments"][0]["url"], report)


class RoundTests(unittest.TestCase):
  def infiscale(self):
    data = fixture(
      "Disposition: Invalid\nReason: False positive", reviewer="infiscale-bot"
    )
    data["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
    data["comments"] = [comment("👀 " + INFISCALE_START, "infiscale-bot")]
    return data

  def test_only_a_review_after_the_latest_start_completes_the_round(self):
    data = self.infiscale()
    self.assertFalse(passed(result(data, {"infiscale-bot"})))
    review = copy.deepcopy(data["reviews"][0])
    review.update(id="review-2", submittedAt="2026-10-09T03:00:00Z")
    data["reviews"].append(review)
    self.assertTrue(passed(result(data, {"infiscale-bot"})))
    data["head"] = "c" * 40
    self.assertTrue(passed(result(data, {"infiscale-bot"})))

  def test_zero_findings_message_must_belong_to_the_new_round(self):
    data = self.infiscale()
    data["comments"].append(
      comment(INFISCALE_COMPLETE, "infiscale-bot", "2026-10-09T01:00:00Z")
    )
    self.assertFalse(passed(result(data, {"infiscale-bot"})))
    data["comments"].append(
      comment(INFISCALE_COMPLETE, "infiscale-bot", "2026-10-09T03:00:00Z")
    )
    self.assertTrue(passed(result(data, {"infiscale-bot"})))

  def test_withdrawn_round_can_fall_back_but_a_later_start_cannot(self):
    data = self.infiscale()
    withdrawn = copy.deepcopy(data["reviews"][0])
    withdrawn.update(
      id="review-2", state="DISMISSED", submittedAt="2026-10-09T03:00:00Z"
    )
    data["reviews"].append(withdrawn)
    self.assertTrue(passed(result(data, {"infiscale-bot"})))
    data["reviews"] = [withdrawn]
    self.assertFalse(passed(result(data, {"infiscale-bot"})))
    data["comments"].append(
      comment(INFISCALE_COMPLETE, "infiscale-bot", "2026-10-09T01:00:00Z")
    )
    self.assertTrue(passed(result(data, {"infiscale-bot"})))
    data["comments"].append(
      comment(INFISCALE_START, "infiscale-bot", "2026-10-09T04:00:00Z")
    )
    self.assertFalse(passed(result(data, {"infiscale-bot"})))

  def test_one_remaining_review_is_sufficient(self):
    data = self.infiscale()
    review = copy.deepcopy(data["reviews"][0])
    review.update(id="review-2", submittedAt="2026-10-09T03:00:00Z", state="DISMISSED")
    data["reviews"].append(review)
    data["reviews"].append({**review, "id": "review-3", "state": "COMMENTED"})
    self.assertTrue(passed(result(data, {"infiscale-bot"})))

  def test_human_quotes_and_other_bot_starts_do_not_reset_a_bot(self):
    data = fixture("Disposition: Invalid\nReason: Test")
    data["comments"] = [
      comment(INFISCALE_START),
      comment(INFISCALE_START, "other[bot]"),
    ]
    self.assertTrue(passed(result(data)))

  def test_codex_in_progress_or_failed_overrides_old_reviews(self):
    for state in ("⏳ **In progress**", "⚠️ **Failed**"):
      data = fixture("Disposition: Invalid\nReason: Test")
      data["reviews"][0]["author"] = {"login": CODEX}
      body = CODEX_SUMMARY.replace("✅ **Completed**", state)
      data["comments"] = [comment(body, CODEX)]
      self.assertFalse(passed(result(data, {CODEX})))
      data["comments"][0]["body"] = CODEX_SUMMARY
      self.assertTrue(passed(result(data, {CODEX})))

  def test_codex_security_edit_cannot_refresh_old_code_review_completion(self):
    data = fixture()
    data["threads"] = []
    data["reviews"] = [
      {
        "id": "pending",
        "author": {"login": CODEX},
        "state": "PENDING",
        "createdAt": "2026-10-15T01:00:00Z",
        "submittedAt": None,
      }
    ]
    old = comment(CODEX_SUMMARY, CODEX)
    old["updatedAt"] = "2026-10-20T01:00:00Z"
    data["comments"] = [old]
    self.assertFalse(passed(result(data, {CODEX})))
    body = CODEX_SUMMARY.replace("2026-10-14", "2026-10-16").replace(
      "✅ **Completed**", "⏳ **In progress**"
    )
    data["comments"].append(comment(body, CODEX))
    self.assertFalse(passed(result(data, {CODEX})))

  def test_legacy_codex_completion_without_a_clock_does_not_complete_new_pending(self):
    body = CODEX_SUMMARY.replace(
      ' <relative-time datetime="2026-10-14T09:05:35Z">'
      "2026-10-14T09:05:35Z</relative-time>",
      "",
    )
    data = fixture()
    data.update(threads=[], reviews=[], comments=[comment(body, CODEX)])
    self.assertTrue(passed(result(data, {CODEX})))
    data["reviews"] = [
      {
        "state": "PENDING",
        "author": {"login": CODEX},
        "createdAt": "2026-10-10T01:00:00Z",
      }
    ]
    self.assertFalse(passed(result(data, {CODEX})))


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
      page(list(range(100)), "next"),
      page([100]),
    ]
    for field in ("reviews", "comments"):
      with self.subTest(field=field):
        responses = [{"node": {field: p}} for p in pages]
        with patch.object(api, "graphql", side_effect=responses) as graphql:
          self.assertEqual(len(api.connection("pr-1", field, "id")), 101)
          self.assertEqual(graphql.call_args.args[1]["cursor"], "next")

  def test_long_thread_comments_are_paginated(self):
    api = GitHub("test-token")
    thread = {
      "id": "thread-1",
      "comments": page(list(range(100)), "next"),
    }
    last_page = page([100])
    with patch.object(api, "graphql", return_value={"node": {"comments": last_page}}):
      comments = api.connection(
        thread["id"],
        "comments",
        "id",
        "PullRequestReviewThread",
        page=thread["comments"],
      )
    self.assertEqual(len(comments), 101)
    self.assertEqual(comments[-1], 100)
    self.assertEqual(len(thread["comments"]["nodes"]), 100)

  def test_snapshot_keeps_inline_replies_and_top_level_completion_separate(self):
    api = GitHub("test-token")
    data = fixture("Disposition: Invalid\nReason: Test")
    pr = pr_metadata(user={"login": data["author"]})

    thread = copy.deepcopy(data["threads"][0])
    thread["comments"] = page(thread["comments"])
    completed = comment(INFISCALE_COMPLETE, "infiscale-bot")
    responses = [
      {"node": {field: page(nodes)}}
      for field, nodes in (
        ("reviews", data["reviews"]),
        ("comments", [completed]),
        ("reviewThreads", [thread]),
        ("commits", [{"commit": {"oid": FIX}}, {"commit": {"oid": HEAD}}]),
      )
    ]
    with (
      patch.object(api, "request", return_value=pr),
      patch.object(api, "graphql", side_effect=responses) as graphql,
    ):
      loaded = snapshot(api, "org/repo", 128)
    self.assertEqual(loaded["comments"], [completed])
    self.assertEqual(loaded["threads"], data["threads"])
    self.assertEqual(loaded["commits"], [FIX, HEAD])
    self.assertTrue(passed(result(loaded, bots={BOT, "infiscale-bot"})))
    self.assertEqual(graphql.call_count, 4)
    self.assertTrue(all(c.args[1]["id"] == "pr-1" for c in graphql.call_args_list))

  def test_graphql_errors_reject_partial_data(self):
    api = GitHub("test-token")
    with patch.object(api, "request", return_value={"data": {}, "errors": [{}]}):
      with self.assertRaises(RuntimeError):
        api.graphql("query", {})

  def test_resolution_changes_during_check_do_not_block(self):
    data = fixture("Disposition: Invalid\nReason: False positive")

    for resolutions in ((False, True), (True, False)):
      with self.subTest(resolutions=resolutions):
        states = iter(resolutions)

        def graphql(query, variables, states=states):
          if "reviewThreads(first:" in query:
            thread = {
              "id": "thread-1",
              "comments": page(data["threads"][0]["comments"]),
            }
            resolved = next(states)
            # Return resolution state only if the query requested it.
            if "isResolved" in query:
              thread["isResolved"] = resolved
            return {"node": {"reviewThreads": page([thread])}}
          for field, nodes in (
            ("reviews", data["reviews"]),
            ("comments", []),
            ("commits", [{"commit": {"oid": FIX}}, {"commit": {"oid": HEAD}}]),
          ):
            if f"{field}(first:" in query:
              return {"node": {field: page(nodes)}}
          self.fail("Unexpected GraphQL query")

        api = StatusStore()
        with patch.object(api, "graphql", side_effect=graphql), patch("builtins.print"):
          self.assertTrue(check_pr(api, "org/repo", 128, {BOT}))
        self.assertEqual(list(states), [])
        self.assertEqual(api.gate()[0]["state"], "success")

  def test_snapshot_rejects_head_change_during_read(self):
    api = GitHub("test-token")
    before = pr_metadata()
    after = {**before, "head": {"sha": FIX}}
    with (
      patch.object(api, "request", side_effect=[before, after]),
      patch.object(api, "connection", return_value=[]),
      self.assertRaises(RuntimeError),
    ):
      snapshot(api, "org/repo", 128)

  def test_snapshot_retains_repository_for_commit_link_validation(self):
    pr = pr_metadata()
    api = GitHub("test-token")
    with (
      patch.object(api, "request", return_value=pr),
      patch.object(api, "connection", return_value=[]),
    ):
      self.assertEqual(snapshot(api, "org/repo", 128)["repository"], "org/repo")

  def test_snapshot_rejects_base_change_but_ignores_unrelated_repo_metadata(self):
    before = pr_metadata(
      base={"ref": "main", "sha": FIX, "repo": {"description": "old"}}
    )
    for base in ({"ref": "develop", "sha": FIX}, {"ref": "main", "sha": HEAD}):
      with self.subTest(base=base):
        api = GitHub("test-token")
        with (
          patch.object(api, "request", side_effect=[before, {**before, "base": base}]),
          patch.object(api, "connection", return_value=[]),
          self.assertRaises(RuntimeError),
        ):
          snapshot(api, "org/repo", 128)
    api = GitHub("test-token")
    after = {**before, "base": {**before["base"], "repo": {"description": "new"}}}
    with (
      patch.object(api, "request", side_effect=[before, after]),
      patch.object(api, "connection", return_value=[]),
    ):
      self.assertEqual(
        snapshot(api, "org/repo", 128)["base"], {"ref": "main", "sha": FIX}
      )

  def test_snapshot_reads_top_level_comments_for_completion(self):
    pr = pr_metadata()
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
    self.assertEqual(connection.call_args_list[1].args[1], "comments")
    self.assertNotIn("pullRequestReview", connection.call_args_list[1].args[2])

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
        self.assertEqual(calls[-1].args[2]["state"], "success")

  def test_check_is_written_to_pr_head_not_event_sha(self):
    data = fixture("Disposition: Invalid\nReason: False positive")
    calls, ok = self.run_check([data, copy.deepcopy(data)])
    self.assertTrue(ok)
    self.assertEqual(calls[1].args[1].rsplit("/", 1)[-1], HEAD)
    self.assertEqual(calls[1].args[2]["context"], CHECK_NAME)
    self.assertEqual(calls[-1].args[2]["state"], "success")

  def test_new_finding_during_read_prevents_success(self):
    for reviewer in (BOT, "human-reviewer"):
      with self.subTest(reviewer=reviewer):
        data = fixture("Disposition: Invalid\nReason: False positive")
        changed = copy.deepcopy(data)
        changed["threads"].append(fixture(reviewer=reviewer)["threads"][0])
        calls, ok = self.run_check([data, changed])
        self.assertFalse(ok)
        self.assertEqual(calls[-1].args[2]["state"], "failure")

  def test_completion_comment_changed_during_read_prevents_success(self):
    data = fixture("Disposition: Invalid\nReason: Test")
    data["comments"] = [comment(INFISCALE_COMPLETE, "infiscale-bot")]
    changed = copy.deepcopy(data)
    changed["comments"][0]["body"] = INFISCALE_START
    calls, ok = self.run_check([data, changed])
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["state"], "failure")

  def test_api_failure_finishes_check_as_failure(self):
    calls, ok = self.run_check(RuntimeError("failed"))
    self.assertFalse(ok)
    self.assertEqual(calls[-1].args[2]["state"], "failure")

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
          self.assertEqual(calls[-1].args[2]["state"], "failure")

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
        self.assertEqual(calls[-1].args[2]["state"], "success")

  def test_missing_unreadable_or_failed_issue_lookup_blocks_deferred(self):
    data = fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    for status in (404, 410, 401, 403, 429, 500):
      with self.subTest(status=status):
        error = HTTPError("https://api.github.com/issue", status, "error", {}, None)
        calls, ok = self.run_check([data], error)
        self.assertFalse(ok)
        self.assertEqual(calls[2].args, ("GET", "/repos/org/repo/issues/42"))
        self.assertEqual(calls[-1].args[2]["state"], "failure")

  def test_deleted_issue_reports_invalid_tracking_instead_of_api_failure(self):
    data = fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    error = HTTPError("https://api.github.com/issue", 410, "Gone", {}, None)
    _, ok = self.run_check([data], error)
    self.assertFalse(ok)
    report = self.report
    self.assertIn("[Deferred]", report)
    self.assertIn("deleted issues", report)
    self.assertNotIn("evaluation failed", report)

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
        self.assertEqual(calls[-1].args[2]["state"], "failure")

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
    self.assertEqual(len(issue_reads), 2)

  def test_closed_issue_or_revoked_permission_during_final_verification_blocks(self):
    for first, second, body, login in (
      (
        {"state": "open"},
        {"state": "closed"},
        "Disposition: Deferred\nReason: Later\nTracking: https://github.com/org/repo/issues/42",
        "contributor",
      ),
      (
        {"permission": "write"},
        {"permission": "read"},
        "Disposition: Invalid\nReason: Test",
        "maintainer",
      ),
    ):
      with self.subTest(first=first):
        data = fixture(body)
        data["threads"][0]["comments"][-1]["author"]["login"] = login
        calls, ok = self.run_check([data, copy.deepcopy(data)], [first, second])
        self.assertFalse(ok)
        self.assertEqual(calls[-1].args[2]["state"], "failure")

  def test_cross_repository_links_are_rejected_without_reading_them(self):
    for repo in ("org/other", "other/repo"):
      data = fixture(
        f"Disposition: Deferred\nReason: Later\nTracking: https://github.com/{repo}/issues/42"
      )
      calls, ok = self.run_check([data])
      self.assertFalse(ok)
      self.assertFalse(any("/issues/" in c.args[1] for c in calls))

  def test_issue_transfer_redirect_cannot_bypass_repository_restriction(self):
    data = fixture(
      "Disposition: Deferred\nReason: Later\nTracking: https://github.com/org/repo/issues/42"
    )
    _, ok = self.run_check(
      [data],
      {"state": "open", "repository_url": "https://api.github.com/repos/org/other"},
    )
    self.assertFalse(ok)

  def test_reply_permissions_are_queried_and_cached(self):
    for permission, expected in (
      ("write", True),
      ("admin", True),
      ("read", False),
      ("none", False),
    ):
      with self.subTest(permission=permission):
        data = fixture("Disposition: Invalid\nReason: Test")
        data["threads"][0]["comments"][-1]["author"]["login"] = "maintainer"
        data["threads"].append(copy.deepcopy(data["threads"][0]))
        calls, ok = self.run_check(
          [data, copy.deepcopy(data)], {"permission": permission}
        )
        self.assertEqual(ok, expected)
        permission_reads = [c for c in calls if "/collaborators/" in c.args[1]]
        self.assertEqual(len(permission_reads), 2 if expected else 1)
        self.assertEqual(
          permission_reads[0].args,
          ("GET", "/repos/org/repo/collaborators/maintainer/permission"),
        )

  def test_permission_lookup_errors_block_and_encode_the_login(self):
    for status in (404, 403, 429, 500):
      with self.subTest(status=status):
        data = fixture("Disposition: Invalid\nReason: Test")
        data["threads"][0]["comments"][-1]["author"]["login"] = "user/name"
        error = HTTPError(
          "https://api.github.com/permission", status, "error", {}, None
        )
        calls, ok = self.run_check([data], error)
        self.assertFalse(ok)
        self.assertEqual(
          calls[2].args, ("GET", "/repos/org/repo/collaborators/user%2Fname/permission")
        )
        self.assertEqual(calls[-1].args[2]["state"], "failure")

  def run_check(self, snapshots, lookup=None):
    api = GitHub("test-token")
    pr = {"head": {"sha": HEAD}, "state": "open"}
    lookups = iter(lookup) if isinstance(lookup, list) else None

    def respond(method, path, body=None):
      if (method, path) == ("GET", "/repos/org/repo/pulls/128"):
        return pr
      if method == "POST":
        return {"id": 99, "creator": {"login": "github-actions[bot]"}, **body}
      if method == "PATCH":
        return {}
      value = next(lookups) if lookups else lookup
      if isinstance(value, Exception):
        raise value
      if value is None:
        self.fail(f"Unexpected lookup: {path}")
      if "state" in value:
        return {"repository_url": "https://api.github.com/repos/org/repo", **value}
      return value

    with (
      patch.object(api, "request", side_effect=respond) as request,
      patch.object(api, "pages", return_value=[]),
      patch("scripts.check_ai_review_disposition.current_status", return_value=True),
      patch("scripts.check_ai_review_disposition.snapshot", side_effect=snapshots),
      patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}),
      patch("sys.stdout", new_callable=io.StringIO) as stdout,
    ):
      ok = check_pr(api, "org/repo", 128, {BOT})
      self.report = stdout.getvalue()
      return request.call_args_list, ok


class StatusStore(GitHub):
  """Model GitHub's append-only history and latest-per-context combined endpoint."""

  def __init__(self):
    super().__init__("test-token")
    self.statuses = []

  def add(
    self,
    run,
    attempt=1,
    number=128,
    head=HEAD,
    state="pending",
    context=CHECK_NAME,
    digest="none",
    writes=None,
    actor="github-actions[bot]",
  ):
    writes = writes if writes is not None else len(self.statuses) + 1
    status = {
      "id": len(self.statuses) + 1,
      "context": context,
      "state": state,
      "head_sha": head,
      "creator": {"login": actor},
      "target_url": f"https://github.com/org/repo/actions/runs/{run}/attempts/{attempt}"
      f"#ai-disposition:{number}:{writes}:{digest}",
      "description": "Fixture",
    }
    self.statuses.append(status)
    return copy.deepcopy(status)

  def request(self, method, path, body=None):
    if method == "GET" and "/pulls/" in path:
      return pr_metadata(number=128)
    if method == "POST" and "/statuses/" in path:
      assert set(body) == {"context", "state", "target_url", "description"}
      status = {
        "id": len(self.statuses) + 1,
        "creator": {"login": "github-actions[bot]"},
        "head_sha": path.rsplit("/", 1)[-1],
        **copy.deepcopy(body),
      }
      self.statuses.append(status)
      return copy.deepcopy(status)
    if method == "GET" and "/commits/" in path and "/status?" in path:
      head = path.split("/commits/", 1)[1].split("/", 1)[0]
      latest = {}
      for status in reversed(self.statuses):
        if status["head_sha"] == head:
          latest.setdefault(status["context"].lower(), status)
      query = parse_qs(urlsplit(path).query)
      start = (int(query["page"][0]) - 1) * 100
      return {"statuses": copy.deepcopy(list(latest.values())[start : start + 100])}
    raise AssertionError(f"Unexpected API call: {method} {path}")

  def gate(self, head=HEAD):
    return [
      status
      for status in self.pages(f"/repos/org/repo/commits/{head}/status", "statuses")
      if status["context"].lower() == CHECK_NAME.lower()
    ]


class LatestStatusTests(unittest.TestCase):
  def setUp(self):
    self.environment = patch.dict(
      "os.environ", {"GITHUB_STEP_SUMMARY": "", "AI_REVIEW_CHECKS_FILE": ""}
    )
    self.environment.start()
    self.addCleanup(self.environment.stop)

  def test_twenty_evaluations_keep_one_context_and_enter_real_pending_every_time(self):
    api = StatusStore()
    ready = fixture("Disposition: Invalid\nReason: Test")
    with patch("builtins.print"):
      for run in range(1, 21):
        ok = run % 2 == 0

        def read(api_arg, repository, number, ok=ok):
          self.assertEqual(api.gate()[0]["state"], "pending")
          return ready if ok else fixture()

        with (
          patch.dict(
            "os.environ", {"GITHUB_RUN_ID": str(run), "GITHUB_RUN_ATTEMPT": "1"}
          ),
          patch("scripts.check_ai_review_disposition.snapshot", side_effect=read),
        ):
          self.assertEqual(check_pr(api, "org/repo", 128, {BOT}), ok)
        self.assertEqual(len(api.gate()), 1)
        self.assertEqual(api.gate()[0]["state"], "success" if ok else "failure")
    self.assertEqual(len(api.statuses), 40)
    self.assertEqual([item["state"] for item in api.statuses[::2]], ["pending"] * 20)

  def test_success_can_return_to_pending_without_a_new_visible_context(self):
    api = StatusStore()
    old = api.add(1, state="success")
    newer = register_status(api, "org/repo", 128, HEAD, (2, 1))
    self.assertEqual(newer["state"], "pending")
    self.assertEqual(len(api.gate()), 1)
    self.assertFalse(current_status(api, "org/repo", 128, HEAD, old))
    self.assertTrue(current_status(api, "org/repo", 128, HEAD, newer))

  def test_delayed_old_registration_and_rerun_cannot_take_over(self):
    api = StatusStore()
    old = register_status(api, "org/repo", 128, HEAD, (20, 1))
    self.assertIsNone(register_status(api, "org/repo", 128, HEAD, (19, 99)))
    newer = register_status(api, "org/repo", 128, HEAD, (20, 2))
    self.assertFalse(current_status(api, "org/repo", 128, HEAD, old))
    self.assertTrue(current_status(api, "org/repo", 128, HEAD, newer))
    self.assertIsNone(register_status(api, "org/repo", 128, HEAD, (20, 1)))
    self.assertEqual(len(api.statuses), 2)

  def test_recovery_uses_global_run_id_instead_of_local_run_number(self):
    api = StatusStore()
    api.add(100)
    with patch.dict(
      "os.environ",
      {"GITHUB_RUN_ID": "101", "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1"},
    ):
      newer = register_status(api, "org/repo", 128, HEAD)
    self.assertIn("/runs/101/attempts/1", newer["target_url"])
    self.assertEqual(len(api.gate()), 1)

  def test_unrelated_contexts_and_existing_history_are_untouched(self):
    api = StatusStore()
    for run in range(1, 102):
      api.add(run)
    api.add(999, context="Unrelated CI", actor="another-service")
    before = copy.deepcopy(api.statuses)
    register_status(api, "org/repo", 128, HEAD, (102, 1))
    self.assertEqual(api.statuses[:-1], before)
    self.assertEqual(len(api.gate()), 1)

  def test_new_head_gets_one_independent_context(self):
    api = StatusStore()
    register_status(api, "org/repo", 128, HEAD, (1, 1))
    other_head = "c" * 40
    register_status(api, "org/repo", 128, other_head, (2, 1))
    register_status(api, "org/repo", 128, other_head, (3, 1))
    self.assertEqual(len(api.gate()), 1)
    self.assertEqual(len(api.gate(other_head)), 1)

  def test_unknown_writer_or_shared_pr_head_is_not_silently_adopted(self):
    for options in ({"actor": "stranger"}, {"number": 129}):
      api = StatusStore()
      api.add(1, **options)
      with self.subTest(options=options), self.assertRaises(ValueError):
        register_status(api, "org/repo", 128, HEAD, (2, 1))
      if "number" in options:
        self.assertEqual(api.gate()[0]["state"], "failure")
      else:
        self.assertEqual(len(api.statuses), 1)

  def test_queued_worker_uses_live_head_instead_of_routing_snapshot(self):
    api = StatusStore()
    with (
      patch("scripts.check_ai_review_disposition.snapshot", return_value=fixture()),
      patch("builtins.print"),
    ):
      self.assertFalse(check_pr(api, "org/repo", 128, {BOT}, "c" * 40))
    self.assertEqual(api.gate()[0]["head_sha"], HEAD)


class MainTests(unittest.TestCase):
  def run_main(self, *arguments, passed=True, numbers=None, results=None):
    with (
      patch.dict("os.environ", {"GH_TOKEN": "test-token"}, clear=True),
      patch.object(
        sys, "argv", ["checker", "--repository", "org/repo", "--bots", BOT, *arguments]
      ),
      patch("scripts.check_ai_review_disposition.GitHub") as client,
      patch(
        "scripts.check_ai_review_disposition.register_status", return_value={"id": 99}
      ),
      patch("scripts.check_ai_review_disposition.current_status", return_value=True),
      patch(
        "scripts.check_ai_review_disposition.check_pr", return_value=passed
      ) as check,
      patch("sys.stderr", new_callable=io.StringIO),
    ):
      if numbers is not None:
        client.return_value.pages.return_value = [
          {"number": n, "state": "open", "head": {"sha": HEAD}} for n in numbers
        ]
      if results is not None:
        check.side_effect = results
      return main(), client, check

  def test_sweep_only_routes_all_targets_without_writing_checks(self):
    api = StatusStore()
    prs = [{"number": n, "state": "open", "head": {"sha": HEAD}} for n in [24, 7, 24]]
    with patch.object(api, "pages", return_value=prs):
      self.assertEqual(
        prepare_targets(api, "org/repo", all_open=True),
        [{"number": n, "head": HEAD} for n in [7, 24]],
      )
    self.assertEqual(api.statuses, [])

  def test_empty_sweep_routes_no_workers(self):
    with patch.object(GitHub, "pages", return_value=[]):
      self.assertEqual(prepare_targets(GitHub("token"), "org/repo", all_open=True), [])

  def test_direct_sweep_checks_every_target_even_after_a_failure(self):
    code, _, check = self.run_main(
      "--all-open-prs", numbers=[1, 2], results=[False, True]
    )
    self.assertEqual(code, 1)
    self.assertEqual(check.call_count, 2)

  def test_open_pr_enumeration_reads_all_pages_without_issue_backlinks(self):
    api = GitHub("test-token")
    pages = [[{"number": n} for n in range(1, 101)], [{"number": 101}]]
    with patch.object(api, "request", side_effect=pages) as request:
      self.assertEqual(len(api.pages("/repos/org/repo/pulls?state=open")), 101)
    self.assertEqual(request.call_count, 2)
    self.assertTrue(
      all(
        "/pulls?state=open&per_page=100&page=" in c.args[1]
        for c in request.call_args_list
      )
    )

  def test_checks_only_the_requested_pr(self):
    code, client, check = self.run_main("--pr-number", "313")
    self.assertEqual(code, 0)
    client.assert_called_once_with("test-token")
    check.assert_called_once_with(client.return_value, "org/repo", 313, {BOT})

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

  def test_invalid_bot_configuration_is_published_as_failure(self):
    for bots in (set(), {"reviewer+bot"}):
      api = StatusStore()
      with self.subTest(bots=bots), patch("builtins.print"):
        self.assertFalse(check_pr(api, "org/repo", 128, bots))
      self.assertEqual([item["state"] for item in api.statuses], ["pending", "failure"])

  def test_closed_pr_does_not_create_a_check(self):
    api = GitHub("test-token")
    with patch.object(api, "request", return_value={"state": "closed"}) as request:
      self.assertTrue(check_pr(api, "org/repo", 313, {BOT}))
    request.assert_called_once_with("GET", "/repos/org/repo/pulls/313")


if __name__ == "__main__":
  unittest.main()
