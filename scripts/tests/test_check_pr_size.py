import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.check_pr_size import check_pr_size, is_excluded_file


class PrSizeTests(unittest.TestCase):
  def test_test_directories_and_file_names_are_excluded(self):
    for path in (
      "tests/fixtures/input.json",
      "test/helpers.py",
      "src/module/tests/helpers.py",
      "src/__tests__/helpers.ts",
      "src/test_api.py",
      "src/api_test.py",
      "conftest.py",
      "src/api.test.ts",
      "src/api.spec.tsx",
      "Tests/TEST_API.PY",
    ):
      with self.subTest(path=path):
        excluded, reason = is_excluded_file(path)
        self.assertTrue(excluded)
        self.assertTrue(reason.startswith("test "))

  def test_development_files_are_still_counted(self):
    for path in (
      "src/api.py",
      "src/testing.py",
      "src/test_utils/helpers.py",
      "src/tests_support/helpers.py",
      "src/contest.py",
      "src/specification.ts",
      "README.md",
      ".github/workflows/ci.yml",
    ):
      with self.subTest(path=path):
        self.assertEqual(is_excluded_file(path), (False, ""))

  def test_existing_exclusions_are_preserved(self):
    for path in ("poetry.lock", "vendor/api.py", "app.min.js", "image.png"):
      with self.subTest(path=path):
        self.assertTrue(is_excluded_file(path)[0])

  def test_thresholds_and_summary_only_use_counted_additions(self):
    for additions, status, passed in (
      (300, "PASS", True),
      (301, "WARNING", True),
      (500, "WARNING", True),
      (501, "BLOCK", False),
    ):
      with self.subTest(additions=additions):
        numstat = (
          f"{additions}\t10\tsrc/api.py\n"
          "1000\t0\ttests/test_api.py\n"
          "1000\t0\tsrc/api_test.py\n"
        )
        output = io.StringIO()
        with (
          patch("scripts.check_pr_size.get_git_numstat", return_value=numstat),
          patch("scripts.check_pr_size.write_step_summary") as write_summary,
          redirect_stdout(output),
        ):
          self.assertEqual(check_pr_size("base", "head", 300, 500), passed)
        report = write_summary.call_args.args[0]
        self.assertIn(f"- Status: {status}", report)
        self.assertIn(f"- Counted added lines: {additions}\n", report)
        self.assertIn("- Counted files: 1", report)
        self.assertIn("- Excluded files: 2", report)
        self.assertIn("`tests/test_api.py` (test directory: tests)", report)


if __name__ == "__main__":
  unittest.main()
