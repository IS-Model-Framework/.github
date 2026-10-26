import copy
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import check_ai_review_disposition as checker
from scripts.tests import test_ai_review_disposition as fixtures


class RetryTests(unittest.TestCase):
  def request(self, responses, method="GET", path="/resource", body=None):
    api = checker.GitHub("test-token")
    with (
      patch.object(checker, "urlopen", side_effect=responses) as request,
      patch.object(checker.time, "sleep") as sleep,
    ):
      value = api.request(method, path, body)
    return value, request, sleep

  def error(self, status, headers=None):
    return HTTPError("https://api.github.com/resource", status, "error", headers, None)

  def test_read_recovers_from_transient_errors(self):
    for error in (self.error(500), self.error(502), URLError("connection reset")):
      with self.subTest(error=type(error).__name__):
        value, request, sleep = self.request([error, io.BytesIO(b'{"ok": true}')])
        self.assertEqual(value, {"ok": True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

  def test_rate_limit_honors_retry_after(self):
    for status in (403, 429):
      with self.subTest(status=status):
        _, _, sleep = self.request([
          self.error(status, {"Retry-After": "3"}),
          io.BytesIO(b"{}"),
        ])
        sleep.assert_called_once_with(3)

  def test_long_retry_after_does_not_retry_early(self):
    with (
      patch.object(
        checker, "urlopen", side_effect=self.error(429, {"Retry-After": "120"})
      ) as request,
      patch.object(checker.time, "sleep") as sleep,
      self.assertRaises(HTTPError),
    ):
      checker.GitHub("test-token").request("GET", "/resource")
    self.assertEqual(request.call_count, 1)
    sleep.assert_not_called()

  def test_retries_are_bounded_and_authorization_errors_are_not_retried(self):
    for status, expected in ((403, 1), (404, 1), (503, 3)):
      with (
        self.subTest(status=status),
        patch.object(checker, "urlopen", side_effect=self.error(status)) as request,
        patch.object(checker.time, "sleep"),
        self.assertRaises(HTTPError),
      ):
        checker.GitHub("test-token").request("GET", "/resource")
      self.assertEqual(request.call_count, expected)

  def test_check_writes_and_graphql_mutations_are_never_blindly_retried(self):
    for method, path, body in (
      ("POST", "/repos/org/repo/check-runs", {"name": "check"}),
      ("PATCH", "/repos/org/repo/check-runs/1", {"conclusion": "success"}),
      ("POST", "/graphql", {"query": "mutation { example }"}),
    ):
      with (
        self.subTest(method=method, path=path),
        patch.object(checker, "urlopen", side_effect=self.error(503)) as request,
        patch.object(checker.time, "sleep") as sleep,
        self.assertRaises(HTTPError),
      ):
        checker.GitHub("test-token").request(method, path, body)
      self.assertEqual(request.call_count, 1)
      sleep.assert_not_called()

  def test_read_only_graphql_is_retried(self):
    _, request, _ = self.request(
      [self.error(504), io.BytesIO(b'{"data": {}}')],
      "POST",
      "/graphql",
      {"query": "\nquery { viewer { login } }"},
    )
    self.assertEqual(request.call_count, 2)


class EditorTests(unittest.TestCase):
  def test_bot_or_unknown_editor_cannot_borrow_human_authorship(self):
    for editor in (
      {"login": "reviewer", "__typename": "Bot"},
      {"login": "stranger", "__typename": "User"},
      None,
    ):
      with self.subTest(editor=editor):
        data = fixtures.fixture("Disposition: Invalid\nReason: Old decision")
        reply = fixtures.comment("Disposition: Rejected\nReason: Changed decision")
        reply.update(
          editor=editor,
          lastEditedAt="2026-10-10T01:00:00Z",
          updatedAt="2026-10-10T01:00:00Z",
        )
        data["threads"][0]["comments"].append(reply)
        result = fixtures.result(data)
        self.assertFalse(checker.passed(result))
        self.assertIn("editor", result["findings"][0]["error"])

  def test_authorized_human_editor_is_allowed(self):
    data = fixtures.fixture("Disposition: Invalid\nReason: Updated by maintainer")
    data["threads"][0]["comments"][-1].update(
      editor={"login": "maintainer", "__typename": "User"},
      lastEditedAt="2026-10-10T01:00:00Z",
    )
    self.assertTrue(checker.passed(fixtures.result(data)))

  def test_configured_user_bot_cannot_edit_a_human_disposition(self):
    data = fixtures.fixture(
      "Disposition: Invalid\nReason: Test", reviewer="infiscale-bot"
    )
    data["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
    data["threads"][0]["comments"][-1].update(
      editor={"login": "infiscale-bot", "__typename": "User"},
      lastEditedAt="2026-10-10T01:00:00Z",
    )
    self.assertFalse(checker.passed(fixtures.result(data, {"infiscale-bot"})))

  def test_tampered_codex_summary_blocks_old_evidence_until_new_completion(self):
    data = fixtures.fixture()
    data["reviews"][0]["author"] = {"login": fixtures.CODEX}
    summary = fixtures.comment(fixtures.CODEX_SUMMARY, fixtures.CODEX)
    summary.update(
      editor={"login": "contributor", "__typename": "User"},
      lastEditedAt="2026-10-15T00:00:00Z",
    )
    data["comments"] = [summary]
    self.assertFalse(checker.review_complete(data, fixtures.CODEX))
    fresh = fixtures.comment(
      fixtures.CODEX_SUMMARY.replace("2026-10-14", "2026-10-16"), fixtures.CODEX
    )
    data["comments"].append(fresh)
    self.assertTrue(checker.review_complete(data, fixtures.CODEX))

  def test_tampered_infiscale_completion_cannot_complete_a_round(self):
    data = fixtures.fixture()
    completed = fixtures.comment(fixtures.INFISCALE_COMPLETE, "infiscale-bot")
    completed.update(
      editor={"login": "contributor", "__typename": "User"},
      lastEditedAt="2026-10-10T00:00:00Z",
    )
    data.update(reviews=[], comments=[completed])
    self.assertFalse(checker.review_complete(data, "infiscale-bot"))

  def test_review_after_running_or_failed_codex_summary_still_waits(self):
    for status in ("🔄 **Running** since", "⚠️ **Failed**"):
      with self.subTest(status=status):
        data = fixtures.fixture()
        data["reviews"][0].update(
          author={"login": fixtures.CODEX}, submittedAt="2026-10-15T00:00:00Z"
        )
        summary = fixtures.comment(
          fixtures.CODEX_SUMMARY.replace("✅ **Completed**", status), fixtures.CODEX
        )
        data["comments"] = [summary]
        self.assertFalse(checker.review_complete(data, fixtures.CODEX))
        summary["body"] = fixtures.CODEX_SUMMARY
        self.assertTrue(checker.review_complete(data, fixtures.CODEX))


class LargeCommitTests(unittest.TestCase):
  def load(self, count=301, complete=True):
    ids = [f"{n:040x}" for n in range(count - 1)] + [fixtures.HEAD]
    pr = fixtures.pr_metadata(commits=count, base={"ref": "main", "sha": fixtures.FIX})
    api = checker.GitHub("test-token")
    with (
      patch.object(api, "request", return_value=pr),
      patch.object(
        api,
        "connection",
        side_effect=[[], [], [], [{"commit": {"oid": oid}} for oid in ids[:250]]],
      ),
      patch.object(
        api,
        "pages",
        return_value=[{"sha": oid} for oid in (ids if complete else ids[:250])],
      ) as pages,
    ):
      data = checker.snapshot(api, "org/repo", 128)
    return data, ids, pages

  def test_valid_fix_beyond_graphql_limit_is_accepted(self):
    data, ids, pages = self.load()
    self.assertEqual(data["commits"], ids)
    pages.assert_called_once_with(
      f"/repos/org/repo/compare/{fixtures.FIX}...{fixtures.HEAD}", "commits"
    )
    finding = fixtures.fixture(f"Disposition: Accepted\nReason: Fixed\nFix: {ids[275]}")
    finding["commits"] = data["commits"]
    self.assertTrue(checker.passed(fixtures.result(finding)))
    finding["commits"].remove(ids[275])
    self.assertFalse(checker.passed(fixtures.result(finding)))

  def test_truncated_compare_never_silently_accepts_partial_membership(self):
    with self.assertRaisesRegex(RuntimeError, "Incomplete PR commit comparison"):
      self.load(complete=False)

  def test_small_pr_keeps_the_existing_graphql_fast_path(self):
    data, ids, pages = self.load(count=3)
    self.assertEqual(data["commits"], ids)
    pages.assert_not_called()

  def test_compare_explicitly_paginates_beyond_250(self):
    api = checker.GitHub("test-token")
    replies = [
      {"commits": [{"sha": str(n)} for n in range(start, end)]}
      for start, end in ((0, 100), (100, 200), (200, 300), (300, 301))
    ]
    with patch.object(api, "request", side_effect=replies) as request:
      self.assertEqual(
        len(api.pages("/repos/org/repo/compare/base...head", "commits")), 301
      )
    self.assertEqual(request.call_count, 4)
    self.assertIn("per_page=100&page=4", request.call_args.args[1])


class RecoveryStore(fixtures.CheckStore):
  def __init__(self):
    super().__init__()
    self.source = {
      "path": ".github/workflows/ai-review-disposition.yml",
      "status": "completed",
      "conclusion": "failure",
      "display_title": "128",
      "run_number": 2,
      "run_attempt": 1,
    }

  def add(self, *args, **kwargs):
    check = super().add(*args, **kwargs)
    self.checks[check["id"]]["app"]["slug"] = "github-actions"
    return copy.deepcopy(self.checks[check["id"]])

  def request(self, method, path, body=None):
    if method == "GET" and "/actions/runs/" in path:
      return copy.deepcopy(self.source)
    if method == "GET" and "/pulls/" in path:
      return {"number": 128, "state": "open", "head": {"sha": fixtures.HEAD}}
    return super().request(method, path, body)


class RecoveryTests(unittest.TestCase):
  def setUp(self):
    self.environment = patch.dict(
      os.environ, {"AI_REVIEW_CHECKS_FILE": "", "GITHUB_STEP_SUMMARY": ""}
    )
    self.environment.start()
    self.addCleanup(self.environment.stop)

  def test_first_metadata_read_failure_is_fenced_by_default_branch_recovery(self):
    api = RecoveryStore()
    old = api.add(1)
    api.checks[old["id"]].update(status="completed", conclusion="success")
    with (
      patch.object(api, "request", side_effect=RuntimeError("read failed")),
      self.assertRaises(RuntimeError),
    ):
      checker.check_pr(api, "org/repo", 128, {fixtures.BOT})
    self.assertTrue(checker.recover_run(api, "org/repo", 999))
    self.assertEqual(len(api.gate()), 1)
    self.assertEqual(api.gate()[0]["conclusion"], "failure")
    self.assertEqual(api.gate()[0]["external_id"], "ai-review-disposition:128:2:1")

  def test_timeout_fences_late_success_with_a_different_check_id(self):
    api = RecoveryStore()
    old = api.add(2)
    self.assertTrue(checker.finish_pending(api, "org/repo", 128, fixtures.HEAD, (2, 1)))
    api.request(
      "PATCH",
      f"/repos/org/repo/check-runs/{old['id']}",
      {"status": "completed", "conclusion": "success"},
    )
    self.assertNotEqual(api.gate()[0]["id"], old["id"])
    self.assertEqual(api.gate()[0]["conclusion"], "failure")

  def test_old_recovery_cannot_replace_newer_success_or_pending(self):
    for status in ("completed", "in_progress"):
      with self.subTest(status=status):
        api = RecoveryStore()
        newest = api.add(3)
        api.checks[newest["id"]].update(
          status=status, conclusion="success" if status == "completed" else None
        )
        checker.finish_pending(api, "org/repo", 128, fixtures.HEAD, (2, 1))
        self.assertEqual([c["id"] for c in api.gate()], [newest["id"]])
        self.assertEqual(api.gate()[0]["status"], status)

  def test_completed_target_is_preserved_and_recovery_is_idempotent(self):
    api = RecoveryStore()
    own = api.add(2)
    api.checks[own["id"]].update(status="completed", conclusion="success")
    before = copy.deepcopy(api.checks)
    checker.recover_run(api, "org/repo", 999)
    self.assertEqual(api.checks, before)
    api.checks[own["id"]]["status"] = "in_progress"
    checker.recover_run(api, "org/repo", 999)
    count = len(api.checks)
    checker.recover_run(api, "org/repo", 999)
    self.assertEqual(len(api.checks), count)

  def test_recovery_uses_source_attempt_not_its_own_workflow_generation(self):
    api = RecoveryStore()
    api.source["run_attempt"] = 2
    with patch.dict(
      os.environ, {"GITHUB_RUN_NUMBER": "999", "GITHUB_RUN_ATTEMPT": "7"}
    ):
      checker.recover_run(api, "org/repo", 999)
    self.assertEqual(api.gate()[0]["external_id"], "ai-review-disposition:128:2:2")

  def test_recovery_rejects_unrelated_workflows_and_invalid_routing(self):
    for field, value in (
      ("path", ".github/workflows/untrusted.yml"),
      ("display_title", "a PR title"),
    ):
      api = RecoveryStore()
      api.source[field] = value
      with self.subTest(field=field), self.assertRaises(ValueError):
        checker.recover_run(api, "org/repo", 999)
      self.assertEqual(api.checks, {})

  def test_running_rerun_or_success_is_not_marked_failed(self):
    for changes in ({"status": "in_progress"}, {"conclusion": "success"}):
      api = RecoveryStore()
      api.source.update(changes)
      self.assertTrue(checker.recover_run(api, "org/repo", 999))
      self.assertEqual(api.checks, {})

  def test_recovery_is_not_blocked_by_invalid_bot_configuration(self):
    with (
      patch.dict(
        os.environ, {"GH_TOKEN": "test-token", "AI_REVIEW_BOTS": "invalid+bot"}
      ),
      patch.object(
        sys, "argv", ["checker", "--repository", "org/repo", "--recover-run", "999"]
      ),
      patch.object(checker, "recover_run", return_value=True) as recover,
    ):
      self.assertEqual(checker.main(), 0)
    self.assertEqual(recover.call_args.args[1:], ("org/repo", 999))

  def test_sweep_recovery_visits_all_targets_and_preserves_completed_results(self):
    api = RecoveryStore()
    api.source["display_title"] = "all-open-prs"
    prs = [
      {"number": n, "head": {"sha": fixtures.HEAD}, "state": "open"}
      for n in range(1, 5)
    ]
    visited = []

    def finish(client, repository, number, head, sequence):
      visited.append(number)
      if number == 1:
        raise RuntimeError("one target is unreadable")
      return True

    with (
      patch.object(api, "pages", return_value=prs),
      patch.object(checker, "finish_pending", side_effect=finish),
      patch("sys.stderr", new_callable=io.StringIO),
    ):
      self.assertFalse(checker.recover_run(api, "org/repo", 999))
    self.assertEqual(sorted(visited), [1, 2, 3, 4])

  def test_local_journal_cleanup_closes_interrupted_checks(self):
    api = RecoveryStore()
    with tempfile.TemporaryDirectory() as directory:
      path = str(Path(directory) / "checks.jsonl")
      with patch.dict(
        os.environ, {"AI_REVIEW_CHECKS_FILE": path, "GITHUB_RUN_NUMBER": "2"}
      ):
        checker.register_check(api, "org/repo", 128, fixtures.HEAD)
        self.assertTrue(checker.cleanup_file(api, "org/repo", path))
      self.assertEqual(api.gate()[0]["conclusion"], "failure")

  def test_final_check_list_or_patch_failure_reports_recovery_instead_of_success(self):
    for fail_list in (False, True):
      api = RecoveryStore()
      request = api.request

      def fail_publish(method, path, body=None, request=request):
        if method == "PATCH" and (body or {}).get("conclusion") == "success":
          raise RuntimeError("write failed")
        return request(method, path, body)

      with (
        self.subTest(fail_list=fail_list),
        patch.object(api, "request", side_effect=fail_publish),
        patch.object(
          checker,
          "snapshot",
          return_value=fixtures.fixture("Disposition: Invalid\nReason: Test"),
        ),
        patch.object(
          checker,
          "current_check",
          side_effect=[True, RuntimeError("list failed")]
          if fail_list
          else [True, True],
        ),
        patch("sys.stderr", new_callable=io.StringIO) as stderr,
      ):
        self.assertFalse(checker.check_pr(api, "org/repo", 128, {fixtures.BOT}))
        self.assertIn("in_progress", stderr.getvalue())
        self.assertIn("dispatch", stderr.getvalue())
      self.assertEqual(api.gate()[0]["status"], "in_progress")

  def test_sweep_registers_every_pr_before_evaluation(self):
    prs = [{"number": n, "head": {"sha": fixtures.HEAD}} for n in range(1, 9)]
    registered = []
    evaluated = []

    def register(api, repository, number, head):
      registered.append(number)
      return {"id": number}

    def evaluate(api, repository, number, bots, prepared):
      self.assertEqual(len(registered), 8)
      self.assertEqual(prepared, (fixtures.HEAD, {"id": number}))
      evaluated.append(number)
      if number == 2:
        raise RuntimeError("one PR fails")
      return True

    with (
      patch.dict(os.environ, {"GH_TOKEN": "test-token"}),
      patch.object(
        sys,
        "argv",
        [
          "checker",
          "--repository",
          "org/repo",
          "--bots",
          fixtures.BOT,
          "--all-open-prs",
        ],
      ),
      patch.object(checker.GitHub, "pages", return_value=prs),
      patch.object(checker, "register_check", side_effect=register),
      patch.object(checker, "current_check", return_value=True),
      patch.object(checker, "check_pr", side_effect=evaluate),
      patch("sys.stderr", new_callable=io.StringIO),
    ):
      self.assertEqual(checker.main(), 1)
    self.assertEqual(sorted(evaluated), list(range(1, 9)))


if __name__ == "__main__":
  unittest.main()
