"""Offline scenarios using synthetic actors and the real disposition checker."""

import copy
from urllib.error import HTTPError

from scripts.tests.test_ai_review_disposition import (
  BOT,
  CODEX,
  CODEX_SUMMARY,
  INFISCALE_COMPLETE,
  INFISCALE_START,
  ApiTests,
  comment,
  fixture,
  result,
)
from scripts.check_ai_review_disposition import passed


def scenarios():
  """Yield names, actual outcomes, and independently specified expectations."""
  ai = fixture(
    "Disposition: Rejected\nReason: Already handled", reviewer="infiscale-bot"
  )
  ai["reviews"][0]["author"] = {"login": "infiscale-bot", "__typename": "User"}
  ai["comments"] = [comment(INFISCALE_START, "infiscale-bot")]
  yield (
    "Infiscale started; old review cannot finish new round",
    passed(result(ai, {"infiscale-bot"})),
    False,
  )
  latest = {**ai["reviews"][0], "id": "new", "submittedAt": "2026-10-09T03:00:00Z"}
  ai["reviews"].append(latest)
  yield (
    "Infiscale publishes findings after start",
    passed(result(ai, {"infiscale-bot"})),
    True,
  )
  latest["state"] = "DISMISSED"
  yield (
    "Withdrawn round can use older valid review",
    passed(result(ai, {"infiscale-bot"})),
    True,
  )
  ai["reviews"] = [latest]
  yield (
    "All reviews dismissed without valid history",
    passed(result(ai, {"infiscale-bot"})),
    False,
  )
  ai["threads"] = []
  ai["comments"].append(
    comment(INFISCALE_COMPLETE, "infiscale-bot", "2026-10-09T04:00:00Z")
  )
  yield (
    "Infiscale exact zero-finding completion",
    passed(result(ai, {"infiscale-bot"})),
    True,
  )
  ai["comments"].append(
    comment(INFISCALE_START, "infiscale-bot", "2026-10-09T05:00:00Z")
  )
  yield (
    "Later Infiscale round requires new completion",
    passed(result(ai, {"infiscale-bot"})),
    False,
  )

  codex = fixture()
  codex.update(threads=[], reviews=[])
  for label, status, expected in (
    ("in progress", "⏳ **In progress**", False),
    ("failed", "⚠️ **Failed**", False),
    ("completed", "✅ **Completed**", True),
  ):
    codex["comments"] = [
      comment(CODEX_SUMMARY.replace("✅ **Completed**", status), CODEX)
    ]
    codex["comments"][0]["author"] = {
      "login": CODEX.removesuffix("[bot]"),
      "__typename": "Bot",
    }
    yield "Codex " + label, passed(result(codex, {CODEX})), expected

  # check_pr() performs actual authorization and final-cache logic; only its API
  # transport and snapshots are replaced. No GitHub credential/account is used.
  harness = ApiTests()
  for role, expected in (
    ("write", True),
    ("admin", True),
    ("read", False),
    ("none", False),
  ):
    data = fixture("Disposition: Rejected\nReason: Second human account")
    data["threads"][0]["comments"][-1]["author"]["login"] = "second-human"
    _, ok = harness.run_check([data, copy.deepcopy(data)], {"permission": role})
    yield "Second human has " + role + " permission", ok, expected
  data = fixture("Disposition: Rejected\nReason: Previously authorized")
  data["threads"][0]["comments"][-1]["author"]["login"] = "second-human"
  _, ok = harness.run_check(
    [data, copy.deepcopy(data)], [{"permission": "write"}, {"permission": "read"}]
  )
  yield "Second human loses write access during final check", ok, False
  data = fixture("Disposition: Rejected\nReason: Bot cannot self-dispose")
  data["threads"][0]["comments"][-1]["author"] = {"login": BOT, "__typename": "Bot"}
  yield "Bot reply cannot replace a human disposition", passed(result(data)), False

  data = fixture(
    "Disposition: Deferred\nReason: Follow up\nTracking: https://github.com/org/repo/issues/42"
  )
  for label, lookup, expected in (
    ("open Issue", {"state": "open"}, True),
    ("closed Issue", {"state": "closed"}, False),
    (
      "deleted Issue",
      HTTPError("https://api.github.com/issue", 410, "Gone", {}, None),
      False,
    ),
    (
      "Issue closes during final check",
      [{"state": "open"}, {"state": "closed"}],
      False,
    ),
  ):
    _, ok = harness.run_check([data, copy.deepcopy(data)], lookup)
    yield label, ok, expected


def main():
  print("Simulation only: synthetic AI and human identities; no GitHub requests.\n")
  print("| Scenario | Expected gate | Actual gate | Matches |")
  print("| --- | --- | --- | --- |")
  failures = 0
  count = 0
  for name, actual, expected in scenarios():
    failures += actual != expected
    count += 1
    expected_gate = "pass" if expected else "block"
    actual_gate = "pass" if actual else "block"
    matches = "yes" if actual == expected else "NO"
    print(f"| {name} | {expected_gate} | {actual_gate} | {matches} |")
  print(f"\n{count - failures}/{count} scenarios matched expectations.")
  return int(bool(failures))


if __name__ == "__main__":
  raise SystemExit(main())
