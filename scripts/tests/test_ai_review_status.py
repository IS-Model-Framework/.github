import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_ai_review_disposition as checker
from scripts.tests import test_ai_review_disposition as fixtures


class EvidenceStore(fixtures.StatusStore):
  def __init__(self):
    super().__init__()
    self.issue = {
      "state": "open",
      "repository_url": "https://api.github.com/repos/org/repo",
    }
    self.permission = "write"

  def request(self, method, path, body=None):
    if method == "GET" and "/issues/" in path:
      return copy.deepcopy(self.issue)
    if method == "GET" and "/collaborators/" in path:
      return {"permission": self.permission}
    return super().request(method, path, body)


class StatusPollingTests(unittest.TestCase):
  def setUp(self):
    self.api = EvidenceStore()
    self.run = 0
    self.ready = fixtures.fixture("Disposition: Invalid\nReason: Covered upstream")
    self.environment = patch.dict(
      os.environ,
      {
        "AI_REVIEW_CHECKS_FILE": "",
        "GITHUB_STEP_SUMMARY": "",
        "GITHUB_RUN_ATTEMPT": "1",
      },
    )
    self.environment.start()
    self.addCleanup(self.environment.stop)

  def invoke(self, data=None, poll=False, bots=None):
    self.run += 1
    with (
      patch.dict(os.environ, {"GITHUB_RUN_ID": str(self.run)}),
      patch.object(checker, "snapshot", return_value=data or self.ready),
      patch("builtins.print"),
    ):
      return checker.check_pr(
        self.api,
        "org/repo",
        128,
        bots or {fixtures.BOT},
        only_changed=poll,
      )

  def assert_states(self, states):
    self.assertEqual([item["state"] for item in self.api.statuses], states)
    self.assertEqual(len(self.api.gate()), 1)
    self.assertEqual(self.api.gate()[0]["context"], checker.CHECK_NAME)

  def test_unchanged_polls_do_not_consume_status_writes(self):
    for data, expected in ((self.ready, True), (fixtures.fixture(), False)):
      with self.subTest(expected=expected):
        self.api = EvidenceStore()
        self.assertEqual(self.invoke(data), expected)
        for _ in range(20):
          self.assertEqual(self.invoke(copy.deepcopy(data), poll=True), expected)
        self.assert_states(["pending", "success" if expected else "failure"])

  def test_explicit_rechecks_always_revoke_success_with_pending(self):
    self.assertTrue(self.invoke())
    for _ in range(3):
      self.assertTrue(self.invoke())
    self.assert_states(["pending", "success"] * 4)

  def test_new_finding_invalidates_the_same_visible_result(self):
    self.assertTrue(self.invoke())
    changed = copy.deepcopy(self.ready)
    changed["threads"].append(fixtures.fixture()["threads"][0])
    self.assertFalse(self.invoke(changed, poll=True))
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_tracking_issue_change_is_detected_without_a_pr_comment_change(self):
    data = fixtures.fixture(
      "Disposition: Deferred\nReason: Follow up\n"
      "Tracking: https://github.com/org/repo/issues/42"
    )
    self.assertTrue(self.invoke(data))
    self.api.issue["state"] = "closed"
    self.assertFalse(self.invoke(data, poll=True))
    self.assertFalse(self.invoke(data, poll=True))
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_revoked_human_permission_invalidates_an_unchanged_reply(self):
    data = copy.deepcopy(self.ready)
    data["threads"][0]["comments"][-1]["author"]["login"] = "maintainer"
    self.assertTrue(self.invoke(data))
    self.api.permission = "read"
    self.assertFalse(self.invoke(data, poll=True))
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_requiring_another_bot_invalidates_old_success(self):
    self.assertTrue(self.invoke())
    self.assertFalse(self.invoke(poll=True, bots={fixtures.BOT, "infiscale-bot"}))
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_both_configured_bots_complete_then_a_new_round_blocks(self):
    data = copy.deepcopy(self.ready)
    data["comments"] = [fixtures.comment(fixtures.INFISCALE_COMPLETE, "infiscale-bot")]
    bots = {fixtures.BOT, "infiscale-bot"}
    self.assertTrue(self.invoke(data, bots=bots))
    data["comments"].append(
      fixtures.comment(
        fixtures.INFISCALE_START,
        "infiscale-bot",
        "2026-10-10T00:00:00Z",
      )
    )
    self.assertFalse(self.invoke(data, poll=True, bots=bots))
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_poll_read_failure_revokes_success_instead_of_skipping(self):
    self.assertTrue(self.invoke())
    with patch.object(checker, "snapshot", side_effect=RuntimeError("read failed")):
      # invoke's snapshot patch is intentionally not used for this failing read.
      with patch.dict(os.environ, {"GITHUB_RUN_ID": "2"}), patch("builtins.print"):
        self.assertFalse(
          checker.check_pr(
            self.api,
            "org/repo",
            128,
            {fixtures.BOT},
            only_changed=True,
          )
        )
    self.assert_states(["pending", "success", "pending", "failure"])

  def test_interrupted_pending_result_is_retried_even_when_evidence_is_unchanged(self):
    self.api.add(0)
    self.assertTrue(self.invoke(poll=True))
    self.assert_states(["pending", "pending", "success"])

  def test_retry_after_temporary_read_failure_can_pass(self):
    self.api.add(0, state="failure")
    self.assertTrue(self.invoke(poll=True))
    self.assert_states(["failure", "pending", "success"])

  def test_status_limit_cannot_leave_an_irreversible_success(self):
    self.api.add(0, state="success", writes=997)
    self.assertFalse(self.invoke())
    self.assert_states(["success", "pending", "failure"])
    self.assertIn("push a new commit", self.api.gate()[0]["description"])

  def test_approaching_limit_replaces_success_with_failure_before_refusing(self):
    self.api.add(0, state="success", writes=998)
    with self.assertRaises(RuntimeError):
      self.invoke()
    self.assert_states(["success", "failure"])
    self.assertIn("push a new commit", self.api.gate()[0]["description"])

  def test_foreign_target_url_is_not_trusted_even_with_the_bot_login(self):
    status = self.api.add(0, state="success")
    status["target_url"] = "https://example.test/runs/1"
    with self.assertRaises(ValueError):
      checker.status_identity(status, "org/repo")

  def test_status_lookup_paginates_past_other_contexts(self):
    self.api.add(1)
    for n in range(101):
      self.api.add(2, context=f"other-{n}")
    self.assertEqual(
      checker.latest_status(self.api, "org/repo", fixtures.HEAD)["id"], 1
    )

  def test_ownership_uses_full_status_records_when_combined_omits_creator(self):
    self.api.add(1, state="success")
    self.assertNotIn("creator", self.api.gate()[0])
    status = checker.latest_status(self.api, "org/repo", fixtures.HEAD)
    self.assertEqual(status["creator"]["login"], "github-actions[bot]")
    self.assertEqual(checker.status_identity(status, "org/repo")[:3], (1, 1, 128))

  def test_latest_context_stops_before_reading_older_history_pages(self):
    for n in range(150):
      self.api.add(n)
    with patch.object(self.api, "request", wraps=self.api.request) as request:
      status = checker.latest_status(self.api, "org/repo", fixtures.HEAD)
    self.assertEqual(status["id"], 150)
    self.assertEqual(request.call_count, 1)


class SingleRowWorkflowTests(unittest.TestCase):
  def test_caller_and_recovery_never_use_pr_associated_triggers(self):
    root = Path(__file__).resolve().parents[2]
    for filename in ("ai-review-disposition.yml", "ai-review-recovery.yml"):
      with self.subTest(filename=filename):
        workflow = (root / "docs/examples" / filename).read_text(encoding="utf-8")
        triggers = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
        self.assertNotRegex(
          triggers,
          r"(?m)^  (push|pull_request(?:_target|_review(?:_comment)?)?"
          r"|deployment(?:_status)?):",
        )
        self.assertIn("statuses: write", workflow)
        self.assertNotIn("checks: write", workflow)
    caller = (root / "docs/examples/ai-review-disposition.yml").read_text(
      encoding="utf-8"
    )
    self.assertIn("schedule:", caller)
    self.assertIn("workflows: [CI]", caller)
    self.assertFalse((root / "docs/examples/ai-review-events.yml").exists())


if __name__ == "__main__":
  unittest.main()
