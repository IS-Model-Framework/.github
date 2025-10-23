#!/usr/bin/env python3
#
# ====- typing_helper, runs type checkers from the ci --*- python -*--==#
#
# ==------------------------------------------------------------------==#

import argparse
import os
import subprocess
import sys
from typing import List, Optional

"""
Static typing for python using mypy.

You can control the exact path to mypy with the following
environment variables: $MYPY_PATH.

Usage:
    python code_format_helper.py --changed-files <files>

Exit codes:
    0 - Static typing for python successfully
    1 - Static typing for python with failure
"""


class TypingArgs:
  changed_files: Optional[str] = None
  verbose: bool = True

  def __init__(self, args: Optional[argparse.Namespace] = None) -> None:
    if not args is None:
      self.changed_files = args.changed_files


class TypingHelper:
  name: str
  friendly_name: str

  @property
  def instructions(self) -> str:
    raise NotImplementedError()

  def has_tool(self) -> bool:
    raise NotImplementedError()

  def typing_run(self, changed_files: List[str], args: TypingArgs) -> bool:
    raise NotImplementedError()

  def run(self, changed_files: List[str], args: TypingArgs) -> bool:
    if self.typing_run(changed_files, args):
      return True
    else:
      print(
        f"Warning: {self.friendly_name}, {self.name} detected "
        "some issues with your code typing..."
      )
      return False


class MypyHelper(TypingHelper):
  name = "mypy"
  friendly_name = "Static Typing for Python"

  @property
  def instructions(self) -> str:
    return " ".join(self.mypy_cmd)

  def filter_changed_files(self, changed_files: List[str]) -> List[str]:
    filtered_files = []
    for path in changed_files:
      name, ext = os.path.splitext(path)
      if ext == ".py":
        filtered_files.append(path)

    return filtered_files

  @property
  def mypy_path(self) -> str:
    if "MYPY_PATH" in os.environ:
      return os.environ["MYPY_PATH"]
    return "mypy"

  def has_tool(self) -> bool:
    cmd = [self.mypy_path, "--version"]
    proc = None
    try:
      proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
      return False
    return proc.returncode == 0

  def typing_run(self, changed_files: List[str], args: TypingArgs) -> bool:
    py_files = self.filter_changed_files(changed_files)
    if not py_files:
      print("No python files changed, skipping static typing...")
      return True
    mypy_cmd = [
      self.mypy_path,
    ]

    mypy_cmd += py_files
    if args.verbose:
      print(f"Running: {' '.join(mypy_cmd)}")
    self.mypy_cmd = mypy_cmd
    proc = subprocess.run(mypy_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if args.verbose:
      sys.stdout.write(proc.stderr.decode("utf-8"))

    if proc.returncode != 0:
      if args.verbose:
        print(f"error: {self.name} exited with code {proc.returncode}")
        print(proc.stdout.decode("utf-8"))
      return False
    else:
      sys.stdout.write(proc.stdout.decode("utf-8"))
      return True


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--changed-files",
    type=str,
    help="Comma separated list of files that has been changed",
  )

  args = TypingArgs(parser.parse_args())

  changed_files = []
  if args.changed_files:
    changed_files = args.changed_files.split(",")

  # Filter out file test/unittests/lit.cfg.py
  changed_files = [f for f in changed_files if f != "test/unittests/lit.cfg.py"]

  helper = MypyHelper()
  if not helper.has_tool():
    print(f"error: {helper.name} is not installed or not found in PATH")
    sys.exit(1)

  if not helper.run(changed_files, args):
    print(f"error: static typing for python failed")
    sys.exit(1)
