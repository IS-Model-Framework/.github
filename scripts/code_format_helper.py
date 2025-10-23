#!/usr/bin/env python3
#
# ====- code_format_helper, runs code formatters from the ci --*- python -*--==#
#
# ==-------------------------------------------------------------------------==#

import argparse
import os
import subprocess
import sys
from typing import List, Optional

"""
For C/C++ code it uses clang-format and for Python code it uses ruff.

You can learn more about the LLVM coding style on llvm.org:
https://llvm.org/docs/CodingStandards.html

You can control the exact path to clang-format or ruff with the following
environment variables: $CLANG_FORMAT_PATH and $RUFF_FORMAT_PATH.

Usage:
    python code_format_helper.py --start-rev <start_rev> --end-rev <end_rev> --changed-files <files>

Exit codes:
    0 - All code formatter completed successfully
    1 - At least one code formatter completed with failure
"""


class FormatArgs:
  start_rev: Optional[str] = None
  end_rev: Optional[str] = None
  changed_files: Optional[str] = None
  py_style_config: Optional[str] = None
  verbose: bool = True

  def __init__(self, args: Optional[argparse.Namespace] = None) -> None:
    if not args is None:
      self.start_rev = args.start_rev
      self.end_rev = args.end_rev
      self.changed_files = args.changed_files
      self.py_style_config = args.py_style_config


class FormatHelper:
  name: str
  friendly_name: str

  @property
  def instructions(self) -> str:
    raise NotImplementedError()

  def has_tool(self) -> bool:
    raise NotImplementedError()

  def format_run(self, changed_files: List[str], args: FormatArgs) -> Optional[str]:
    raise NotImplementedError()

  def run(self, changed_files: List[str], args: FormatArgs) -> bool:
    diff = self.format_run(changed_files, args)

    if diff is None:
      return True
    elif len(diff) > 0:
      print(
        f"Warning: {self.friendly_name}, {self.name} detected "
        "some issues with your code formatting..."
      )
      return False
    else:
      # The formatter failed but didn't output a diff (e.g. some sort of
      # infrastructure failure).
      print(
        f"Warning: The {self.friendly_name} failed without printing "
        "a diff. Check the logs for stderr output. :warning:"
      )
      return False


class ClangFormatHelper(FormatHelper):
  name = "clang-format"
  friendly_name = "C/C++ code formatter"

  @property
  def instructions(self) -> str:
    return " ".join(self.cf_cmd)

  def should_include_extensionless_file(self, path: str) -> bool:
    return path.startswith("libcxx/include")

  def filter_changed_files(self, changed_files: List[str]) -> List[str]:
    filtered_files = []
    for path in changed_files:
      _, ext = os.path.splitext(path)
      if ext in (
        ".cpp",
        ".c",
        ".cc",
        ".h",
        ".hpp",
        ".hxx",
        ".cxx",
        ".inc",
        ".cppm",
        ".cl",
      ):
        filtered_files.append(path)
      elif ext == "" and self.should_include_extensionless_file(path):
        filtered_files.append(path)
    return filtered_files

  @property
  def clang_fmt_path(self) -> str:
    if "CLANG_FORMAT_PATH" in os.environ:
      return os.environ["CLANG_FORMAT_PATH"]
    return "git-clang-format"

  def has_tool(self) -> bool:
    cmd = [self.clang_fmt_path, "-h"]
    proc = None
    try:
      proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
      return False
    return proc.returncode == 0

  def format_run(self, changed_files: List[str], args: FormatArgs) -> Optional[str]:
    cpp_files = self.filter_changed_files(changed_files)
    if not cpp_files:
      return None

    cf_cmd = [self.clang_fmt_path, "--diff"]

    if args.start_rev and args.end_rev:
      cf_cmd.append(args.start_rev)
      cf_cmd.append(args.end_rev)

    # Gather the extension of all modified files and pass them explicitly to git-clang-format.
    # This prevents git-clang-format from applying its own filtering rules on top of ours.
    extensions = set()
    for file in cpp_files:
      _, ext = os.path.splitext(file)
      extensions.add(
        ext.strip(".")
      )  # Exclude periods since git-clang-format takes extensions without them
    cf_cmd.append("--extensions")
    cf_cmd.append(",".join(extensions))

    cf_cmd.append("--")
    cf_cmd += cpp_files

    if args.verbose:
      print(f"Running: {' '.join(cf_cmd)}")
    self.cf_cmd = cf_cmd
    proc = subprocess.run(cf_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sys.stdout.write(proc.stderr.decode("utf-8"))

    if proc.returncode != 0:
      # formatting needed, or the command otherwise failed
      if args.verbose:
        print(f"error: {self.name} exited with code {proc.returncode}")
        # Print the diff in the log so that it is viewable there
        print(proc.stdout.decode("utf-8"))
      return proc.stdout.decode("utf-8")
    else:
      return None


class RuffFormatHelper(FormatHelper):
  name = "ruff"
  friendly_name = "Python code formatter"

  @property
  def instructions(self) -> str:
    return " ".join(self.ruff_cmd)

  def filter_changed_files(self, changed_files: List[str]) -> List[str]:
    filtered_files = []
    for path in changed_files:
      name, ext = os.path.splitext(path)
      if ext == ".py":
        filtered_files.append(path)

    return filtered_files

  @property
  def ruff_fmt_path(self) -> str:
    if "RUFF_FORMAT_PATH" in os.environ:
      return os.environ["RUFF_FORMAT_PATH"]
    return "ruff"

  def has_tool(self) -> bool:
    cmd = [self.ruff_fmt_path, "--version"]
    proc = None
    try:
      proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except:
      return False
    return proc.returncode == 0

  def format_run(self, changed_files: List[str], args: FormatArgs) -> Optional[str]:
    py_files = self.filter_changed_files(changed_files)
    if not py_files:
      return None
    ruff_cmd = [
      self.ruff_fmt_path,
      "format",
      "--check",
      "--diff",
    ]
    if args.py_style_config:
      ruff_cmd += ["--config", args.py_style_config]
    ruff_cmd.append("--")
    ruff_cmd += py_files
    if args.verbose:
      print(f"Running: {' '.join(ruff_cmd)}")
    self.ruff_cmd = ruff_cmd
    proc = subprocess.run(ruff_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if args.verbose:
      sys.stdout.write(proc.stderr.decode("utf-8"))

    if proc.returncode != 0:
      # formatting needed, or the command otherwise failed
      if args.verbose:
        print(f"error: {self.name} exited with code {proc.returncode}")
        # Print the diff in the log so that it is viewable there
        print(proc.stdout.decode("utf-8"))
      return proc.stdout.decode("utf-8")
    else:
      sys.stdout.write(proc.stdout.decode("utf-8"))
      return None


ALL_FORMATTERS = (RuffFormatHelper(), ClangFormatHelper())


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
  parser.add_argument(
    "--changed-files",
    type=str,
    help="Comma separated list of files that has been changed",
  )
  parser.add_argument(
    "--py-style-config",
    type=str,
    required=True,
    help="Path to the python style configuration file (ruff.toml)",
  )

  args = FormatArgs(parser.parse_args())

  changed_files = []
  if args.changed_files:
    changed_files = args.changed_files.split(",")

  failed_formatters = []
  for fmt in ALL_FORMATTERS:
    if not fmt.run(changed_files, args):
      failed_formatters.append(fmt.name)

  if len(failed_formatters) > 0:
    print(f"error: some formatters failed: {' '.join(failed_formatters)}")
    sys.exit(1)
