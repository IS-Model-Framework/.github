#!/usr/bin/env python3

# ====- git_log_checker, runs git log checkers from the ci --*- python -*--==#
#
# ==-----------------------------------------------------------------------==#

import sys
import re
import argparse
import subprocess

"""
Check if all git log messages in a MR match the specified template.
Link to template: https://alidocs.dingtalk.com/i/nodes/y20BglGWO27abk2lTl1v3odb8A7depqY?utm_scene=team_space

Usage:
    python check_pr_logs.py <start_rev> <end_rev>

Exit codes:
    0 - All commit messages match the template
    1 - At least one commit message doesn't match the template
    2 - Error occurred during execution
"""


def get_git_commits(start_rev, end_rev):
  try:
    # Get all commit hashes
    result = subprocess.run(
      ["git", "log", "--pretty=format:%H", f"{start_rev}..{end_rev}"],
      capture_output=True,
      text=True,
      check=True,
    )
    commit_hashes = result.stdout.strip().split("\n")
    return commit_hashes
  except subprocess.SubprocessError as e:
    print(f"Error getting commit history: {e}")
    sys.exit(2)


def get_commit_log(commit_hash):
  try:
    result = subprocess.run(
      ["git", "show", "-s", f"--format=%B%nAuthor: %an <%ae>", commit_hash],
      capture_output=True,
      text=True,
      check=True,
    )
    return result.stdout.strip()
  except subprocess.SubprocessError as e:
    print(f"Error getting log for commit {commit_hash}: {e}")
    return None


def check_header(commit_msg):
  header_lines = [line for line in commit_msg.split("\n") if line]
  if not header_lines:
    return False, "Empty commit message"

  header = header_lines[0]
  header_pattern = r"^(\w+)\[(\w+)\]: (.+)$"
  header_match = re.match(header_pattern, header)

  if not header_match:
    return (
      False,
      f"Invalid header format. Should be: <type>[<SCOPE>]: <short-summary>",
    )

  return True, ""


def check_problem_task_section(commit_msg):
  section_started = False
  for line in commit_msg.split("\n"):
    if line.startswith("Problem:") or line.startswith("Task:"):
      section_started = True
      break

  if not section_started:
    return False, "Missing 'Problem/Task:' section"

  return True, ""


def check_solution_section(commit_msg):
  section_started = False
  for line in commit_msg.split("\n"):
    if line.startswith("Solution:"):
      section_started = True
      break

  if not section_started:
    return False, "Missing 'Solution:' section"

  return True, ""


def check_test_section(commit_msg):
  section_started = False
  for line in commit_msg.split("\n"):
    if line.startswith("Test:"):
      section_started = True
      continue

  if not section_started:
    return False, "Missing 'Test:' section"

  return True, ""


def check_jira_section(commit_msg):
  section_started = False
  jira = ""
  for line in commit_msg.split("\n"):
    if line.startswith("JIRA:"):
      section_started = True
      jira = line.split(":")[1].strip()
      break

  if not section_started:
    return False, "Missing 'JIRA:' section"

  jira_pattern = r"^[A-Z0-9]+-[0-9]+"
  jira_match = re.match(jira_pattern, jira)

  if not jira_match:
    return False, "JIRA reference should be in format: <PROJ-123>"

  return True, ""


def check_author_email(commit_msg):
  # section_started = False
  # author = ""
  # email = ""
  # for line in commit_msg.split("\n"):
  #     if line.startswith("Author:"):
  #         section_started = True
  #         author = line.split()[1].strip()
  #         email = line.split()[2].strip()
  #         continue
  #
  # if not section_started:
  #     return False, "Missing author or email"
  #
  # if f"<{author}@is.ic>" != email:
  #     return False, f"{author} {email}: email does not format: <username>@is.ic"
  #
  return True, ""


def validate_commit(commit_msg):
  checks = [
    check_header,
    check_problem_task_section,
    check_solution_section,
    check_test_section,
    check_jira_section,
    check_author_email,
  ]

  error_msgs = []
  for check_func in checks:
    is_valid, error_msg = check_func(commit_msg)
    if not is_valid:
      error_msgs.append(error_msg)

  if error_msgs:
    return False, error_msgs

  return True, []


def check_mr_logs(start_rev, end_rev):
  commit_hashes = get_git_commits(start_rev, end_rev)
  error_commits = []

  for commit_hash in commit_hashes:
    commit_msg = get_commit_log(commit_hash)
    if not commit_msg:
      print(f"Could not get log for commit {commit_hash}")
      return False

    is_valid, error_msgs = validate_commit(commit_msg)
    if not is_valid:
      error_commits.extend([(commit_hash, error_msg) for error_msg in error_msgs])

  if error_commits:
    print(f"Found {len(error_commits)} commits that don't match the template:")
    for commit_hash, error_msg in error_commits:
      print(f"- Commit {commit_hash}: {error_msg}")
    return False

  print("All commits match the template!")
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
    "--end-rev", type=str, required=True, help="Compute changes to this revision"
  )

  args = parser.parse_args()
  if not check_mr_logs(args.start_rev, args.end_rev):
    sys.exit(1)
