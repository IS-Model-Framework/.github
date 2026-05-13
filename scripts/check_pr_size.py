#!/usr/bin/env python3
#
# ====- check_pr_size, limits pull request additions from CI --*- python -*--==#
#
# ==-------------------------------------------------------------------------==#

import argparse
import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Tuple

"""
Check pull request size by counting added lines in git diff --numstat output.

Exit codes:
    0 - Pull request is within the blocking threshold
    1 - Pull request exceeds the blocking threshold
    2 - Error occurred during execution
"""


LOCK_FILE_NAMES = {
  "package-lock.json",
  "pnpm-lock.yaml",
  "yarn.lock",
  "poetry.lock",
  "pipfile.lock",
  "cargo.lock",
}

EXCLUDED_DIRS = {
  ".venv",
  "__generated__",
  "build",
  "dist",
  "generated",
  "node_modules",
  "vendor",
}

EXCLUDED_PATTERNS = (
  "*.min.css",
  "*.min.js",
  "*.map",
)

BINARY_EXTENSIONS = {
  ".7z",
  ".avif",
  ".bmp",
  ".bz2",
  ".eot",
  ".gif",
  ".gz",
  ".ico",
  ".jpeg",
  ".jpg",
  ".otf",
  ".pdf",
  ".png",
  ".rar",
  ".svg",
  ".tar",
  ".ttf",
  ".wasm",
  ".webp",
  ".woff",
  ".woff2",
  ".zip",
}


@dataclass
class FileChange:
  path: str
  additions: int
  excluded: bool
  reason: str = ""


def normalize_path(path: str) -> str:
  path = path.strip()
  if path.startswith('"') and path.endswith('"'):
    path = path[1:-1]
  return path.replace("\\", "/")


def is_excluded_file(path: str) -> Tuple[bool, str]:
  normalized_path = path.lower()
  parts = [part for part in normalized_path.split("/") if part]
  basename = parts[-1] if parts else normalized_path
  _, extension = os.path.splitext(basename)

  if basename in LOCK_FILE_NAMES:
    return True, "lock file"

  for part in parts:
    if part in EXCLUDED_DIRS:
      return True, f"excluded directory: {part}"

  for pattern in EXCLUDED_PATTERNS:
    if fnmatch.fnmatch(basename, pattern):
      return True, f"excluded pattern: {pattern}"

  if extension in BINARY_EXTENSIONS:
    return True, f"binary/static asset: {extension}"

  return False, ""


def get_git_numstat(start_rev: str, end_rev: str) -> str:
  try:
    result = subprocess.run(
      ["git", "diff", "--numstat", f"{start_rev}...{end_rev}"],
      capture_output=True,
      text=True,
      check=True,
    )
  except subprocess.SubprocessError as error:
    print(f"Error getting git diff stats: {error}", file=sys.stderr)
    sys.exit(2)

  return result.stdout


def parse_numstat(numstat: str) -> List[FileChange]:
  file_changes = []

  for raw_line in numstat.splitlines():
    if not raw_line.strip():
      continue

    parts = raw_line.split("\t")
    if len(parts) < 3:
      print(f"Warning: could not parse numstat line: {raw_line}")
      continue

    additions_raw, _, path = parts[0], parts[1], parts[2]
    path = normalize_path(path)

    if additions_raw == "-":
      file_changes.append(
        FileChange(path=path, additions=0, excluded=True, reason="binary diff")
      )
      continue

    try:
      additions = int(additions_raw)
    except ValueError:
      print(f"Warning: could not parse additions count: {raw_line}")
      continue

    excluded, reason = is_excluded_file(path)
    file_changes.append(
      FileChange(path=path, additions=additions, excluded=excluded, reason=reason)
    )

  return file_changes


def build_summary(
  total_additions: int,
  counted_files: List[FileChange],
  excluded_files: List[FileChange],
  warning_threshold: int,
  block_threshold: int,
) -> str:
  status = "PASS"
  if total_additions > block_threshold:
    status = "BLOCK"
  elif total_additions > warning_threshold:
    status = "WARNING"

  lines = [
    "# Pull Request Size Check",
    "",
    f"- Status: {status}",
    f"- Counted added lines: {total_additions}",
    f"- Warning threshold: {warning_threshold}",
    f"- Blocking threshold: {block_threshold}",
    f"- Counted files: {len(counted_files)}",
    f"- Excluded files: {len(excluded_files)}",
    "",
  ]

  if counted_files:
    lines.extend(["## Largest Counted Files", ""])
    for change in sorted(counted_files, key=lambda item: item.additions, reverse=True)[
      :10
    ]:
      lines.append(f"- `{change.path}`: +{change.additions}")
    lines.append("")

  if excluded_files:
    lines.extend(["## Excluded Files", ""])
    for change in excluded_files[:20]:
      lines.append(f"- `{change.path}` ({change.reason})")
    if len(excluded_files) > 20:
      lines.append(f"- ...and {len(excluded_files) - 20} more")
    lines.append("")

  return "\n".join(lines)


def write_step_summary(summary: str) -> None:
  summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
  if not summary_path:
    return

  with open(summary_path, "a", encoding="utf-8") as summary_file:
    summary_file.write(summary)
    summary_file.write("\n")


def check_pr_size(
  start_rev: str,
  end_rev: str,
  warning_threshold: int,
  block_threshold: int,
) -> bool:
  if warning_threshold >= block_threshold:
    print("Error: warning threshold must be lower than blocking threshold",
          file=sys.stderr)
    sys.exit(2)

  file_changes = parse_numstat(get_git_numstat(start_rev, end_rev))
  counted_files = [change for change in file_changes if not change.excluded]
  excluded_files = [change for change in file_changes if change.excluded]
  total_additions = sum(change.additions for change in counted_files)

  summary = build_summary(
    total_additions,
    counted_files,
    excluded_files,
    warning_threshold,
    block_threshold,
  )
  print(summary)
  write_step_summary(summary)

  if total_additions > block_threshold:
    print(
      "::error title=Pull Request too large::"
      f"This PR adds {total_additions} counted lines, which exceeds "
      f"the blocking threshold of {block_threshold}. Please split the PR "
      "or document why it cannot be split."
    )
    return False

  if total_additions > warning_threshold:
    print(
      "::warning title=Large Pull Request::"
      f"This PR adds {total_additions} counted lines, which exceeds "
      f"the warning threshold of {warning_threshold}. Consider splitting "
      "the change to keep review quality high."
    )

  return True


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--start-rev",
    type=str,
    required=True,
    help="Compute changes from this revision.",
  )
  parser.add_argument(
    "--end-rev",
    type=str,
    required=True,
    help="Compute changes to this revision.",
  )
  parser.add_argument(
    "--warning-threshold",
    type=int,
    default=300,
    help="Warn when counted added lines are greater than this value.",
  )
  parser.add_argument(
    "--block-threshold",
    type=int,
    default=500,
    help="Fail when counted added lines are greater than this value.",
  )

  args = parser.parse_args()
  if not check_pr_size(
    args.start_rev,
    args.end_rev,
    args.warning_threshold,
    args.block_threshold,
  ):
    sys.exit(1)
