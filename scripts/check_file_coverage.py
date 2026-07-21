#!/usr/bin/env python3
#
# ====- check_file_coverage, enforce per-file coverage --*- python -*--==#
#
# ==-------------------------------------------------------------------------==#

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List

"""
Check per-file coverage from coverage.py XML output.

Exit codes:
    0 - All measured files meet the threshold
    1 - One or more files are below the threshold
    2 - Error occurred during execution
"""


@dataclass
class FileCoverage:
  path: str
  covered_lines: int
  total_lines: int

  @property
  def percent(self) -> float:
    if self.total_lines == 0:
      return 100.0
    return self.covered_lines / self.total_lines * 100


def parse_coverage_xml(coverage_file: str) -> List[FileCoverage]:
  try:
    root = ET.parse(coverage_file).getroot()
  except (OSError, ET.ParseError) as error:
    print(f"Error reading coverage XML: {error}", file=sys.stderr)
    sys.exit(2)

  files = []

  for class_node in root.findall(".//class"):
    filename = class_node.attrib.get("filename", "").strip()
    if not filename:
      continue

    lines = class_node.findall("./lines/line")
    if not lines:
      continue

    total_lines = len(lines)
    covered_lines = sum(
      1
      for line in lines
      if int(line.attrib.get("hits", "0")) > 0
    )

    files.append(
      FileCoverage(
        path=filename.replace("\\", "/"),
        covered_lines=covered_lines,
        total_lines=total_lines,
      )
    )

  return files


def build_summary(
  files: List[FileCoverage],
  failed_files: List[FileCoverage],
  threshold: float,
) -> str:
  lines = [
    "# Per-file Coverage Check",
    "",
    f"- Status: {'FAIL' if failed_files else 'PASS'}",
    f"- Threshold: {threshold:.2f}%",
    f"- Checked files: {len(files)}",
    f"- Files below threshold: {len(failed_files)}",
    "",
  ]

  if failed_files:
    lines.extend(["## Files Below Threshold", ""])
    for item in sorted(failed_files, key=lambda file: file.percent):
      lines.append(
        f"- `{item.path}`: {item.percent:.2f}% "
        f"({item.covered_lines}/{item.total_lines})"
      )
    lines.append("")

  return "\n".join(lines)


def write_step_summary(summary: str) -> None:
  summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
  if not summary_path:
    return

  with open(summary_path, "a", encoding="utf-8") as summary_file:
    summary_file.write(summary)
    summary_file.write("\n")


def check_file_coverage(coverage_file: str, threshold: float) -> bool:
  if threshold < 0 or threshold > 100:
    print("Error: threshold must be between 0 and 100", file=sys.stderr)
    sys.exit(2)

  files = parse_coverage_xml(coverage_file)
  failed_files = [file for file in files if file.percent < threshold]

  summary = build_summary(files, failed_files, threshold)
  print(summary)
  write_step_summary(summary)

  if failed_files:
    print(
      "::error title=Per-file coverage below threshold::"
      f"{len(failed_files)} file(s) are below the required "
      f"{threshold:.2f}% coverage."
    )
    return False

  return True


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--coverage-file",
    type=str,
    default="coverage.xml",
    help="Path to the coverage XML file.",
  )
  parser.add_argument(
    "--threshold",
    type=float,
    default=85.0,
    help="Minimum required coverage percentage per file.",
  )

  args = parser.parse_args()
  if not check_file_coverage(args.coverage_file, args.threshold):
    sys.exit(1)
