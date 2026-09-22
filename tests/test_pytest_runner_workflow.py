"""Exercise workflow dispatch, argument quoting, and exit-code propagation."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_test_step():
  document = yaml.load(
    (ROOT / ".github/workflows/reusable-tests.yml").read_text(), Loader=yaml.BaseLoader
  )
  steps = document["jobs"]["comprehensive-tests"]["steps"]
  return next(step for step in steps if step.get("id") == "run-tests")


@pytest.mark.parametrize(
  "event,runner,exists,exit_code,expected",
  [
    ("pull_request", "", False, 0, "pytest"),
    ("push", "tools/runner.py", True, 0, "pytest"),
    ("merge_group", "tools/runner.py", True, 0, "pytest"),
    ("pull_request", "tools/missing.py", False, 0, "pytest"),
    ("pull_request", "tools/runner with spaces.py", True, 0, "python"),
    ("pull_request", "tools/runner.py", True, 1, "python"),
    ("pull_request", "tools/runner.py", True, 5, "python"),
    ("push", "", False, 1, "pytest"),
  ],
)
def test_dispatch_preserves_pytest_arguments_and_failure(
  tmp_path, event, runner, exists, exit_code, expected
):
  if exists:
    script = tmp_path / runner
    script.parent.mkdir(parents=True)
    script.write_text("# stub\n")
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  for name in ("python", "pytest"):
    executable = bin_dir / name
    executable.write_text(
      f"#!{sys.executable}\n"
      "import json, os, pathlib, sys\n"
      "with pathlib.Path('calls.jsonl').open('a') as output:\n"
      "  output.write(json.dumps(sys.argv) + '\\n')\n"
      "sys.exit(int(os.environ['TEST_EXIT_CODE']))\n"
    )
    executable.chmod(0o755)
  step = load_test_step()
  assert "continue-on-error" not in step
  result = subprocess.run(
    ["bash", "-eo", "pipefail", "-c", step["run"]],
    cwd=tmp_path,
    env={
      **os.environ,
      "PATH": f"{bin_dir}:{os.environ['PATH']}",
      "GITHUB_EVENT_NAME": event,
      "PYTEST_RUNNER": runner,
      "TEST_EXIT_CODE": str(exit_code),
    },
    capture_output=True,
    text=True,
  )
  assert result.returncode == exit_code, result.stderr
  calls = [
    json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
  ]
  assert len(calls) == 1
  assert Path(calls[0][0]).name == expected
  prefix = ["--", runner] if expected == "python" else []
  assert calls[0][1:] == [
    *prefix,
    "--cov=.",
    "--cov-report=xml:coverage.xml",
    "--cov-report=term-missing",
    "--junitxml=pytest.xml",
    "-v",
    "-s",
    "--tb=short",
  ]
