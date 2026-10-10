import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.check_pr_size import (
  FileChange,
  build_summary,
  check_pr_size,
  is_excluded_file,
  parse_numstat,
)


class PrSizeTests(unittest.TestCase):
  def test_test_directories_and_file_names_are_excluded(self):
    for path in (
      "tests/fixtures/input.json",
      "test/helpers.py",
      "src/module/tests/helpers.py",
      "src/__tests__/helpers.ts",
      "spec/helpers.rb",
      "src/module/specs/helpers.js",
      "testdata/input.json",
      "src/module/testdata/input.txt",
      "src/TestData/input.json",
      "src/test_api.py",
      "src/api_test.py",
      "conftest.py",
      "src/api_test.go",
      "src/api_test.c",
      "src/api_test.cc",
      "src/api_test.cpp",
      "src/api_test.java",
      "src/api.test.ts",
      "src/api.spec.tsx",
      "src/api_spec.rb",
      "Tests/TEST_API.PY",
    ):
      with self.subTest(path=path):
        excluded, reason = is_excluded_file(path)
        self.assertTrue(excluded)
        self.assertTrue(reason.startswith("test "))

  def test_bare_test_names_are_reserved_even_under_src(self):
    # A production src/test.py is still excluded by the naming convention;
    # renaming it to src/testing.py keeps its additions counted.
    for path in ("test.py", "tests.py", "src/test.py", "src/tests.py"):
      with self.subTest(path=path):
        excluded, reason = is_excluded_file(path)
        self.assertTrue(excluded)
        self.assertTrue(reason.startswith("test file: "))
    self.assertEqual(is_excluded_file("src/testing.py"), (False, ""))

  def test_development_files_are_still_counted(self):
    # Only the documented conventions are excluded; similar names stay counted.
    # src/test.py and src/tests.py are reserved (see the dedicated test above).
    for path in (
      "src/api.py",
      "src/testing.py",
      "src/test_utils/helpers.py",
      "src/tests_support/helpers.py",
      "src/contest.py",
      "src/latest.go",
      "src/test.go",
      "src/testdata.py",
      "src/testdata_support/helpers.py",
      "src/spec.py",
      "src/spec_helpers/helpers.rb",
      "src/specification.ts",
      "src/api_test_helpers.go",
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
        self.assertIn("- Excluded added lines: 2000\n", report)
        self.assertIn("- Counted files: 1", report)
        self.assertIn("- Excluded files: 2", report)
        self.assertIn("`tests/test_api.py` (test directory: tests)", report)

  def test_excluded_additions_include_all_exclusion_types(self):
    excluded = parse_numstat(
      "125\t0\ttests/test_api.py\n"
      "10\t0\tpoetry.lock\n"
      "15\t0\tgenerated/api.py\n"
      "-\t-\timage.png\n"
    )
    report = build_summary(0, [], excluded, 300, 500)
    self.assertIn("- Excluded added lines: 150\n", report)
    self.assertIn("- Status: PASS", report)
    empty_report = build_summary(0, [], [], 300, 500)
    self.assertIn("- Excluded added lines: 0\n", empty_report)

  def test_excluded_total_includes_files_beyond_display_limit(self):
    excluded = [
      FileChange(f"tests/test_{i}.py", i, True, "test directory: tests")
      for i in range(1, 26)
    ]
    report = build_summary(0, [], excluded, 300, 500)
    self.assertIn("- Excluded added lines: 325\n", report)
    self.assertIn("...and 5 more", report)


if __name__ == "__main__":
  unittest.main()
