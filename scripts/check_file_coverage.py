import argparse
import os
from pathlib import Path
import subprocess
import sys

from coverage import Coverage


EXCLUDED = {
  "IS-Model-Framework/PallasKernels": {
    "tests",
    "samples",
    "third_party",
    "benchmarks",
    "scripts",
  },
  "IS-Model-Framework/ProtonSamples": {
    "tests",
    "samples",
    "third_party",
  },
}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--threshold", type=float, default=85)
  args = parser.parse_args()
  if not 0 <= args.threshold <= 100:
    parser.error("--threshold must be between 0 and 100")

  excluded = EXCLUDED.get(os.environ.get("GITHUB_REPOSITORY"))
  if excluded is None:
    return 0

  base = os.environ["PR_BASE_SHA"]
  head = os.environ["PR_HEAD_SHA"]
  added = subprocess.check_output([
    "git",
    "diff",
    "--name-only",
    "--diff-filter=A",
    "--find-renames",
    "-z",
    f"{base}...{head}",
    "--",
  ])
  files = sorted(
    path
    for name in added.split(b"\0")
    if name
    for path in [os.fsdecode(name)]
    if path.endswith(".py") and path.split("/", 1)[0] not in excluded
  )

  rows = []
  if files:
    cov = Coverage()
    cov.load()
    if not Path(cov.get_data().data_filename()).is_file():
      raise RuntimeError("Coverage data file is missing")

    for path in files:
      _, statements, _, missing, _ = cov.analysis2(path)
      total = len(statements)
      rows.append((path, total - len(missing), total))

  failed = [
    path
    for path, covered, total in rows
    if total and covered * 100 < args.threshold * total
  ]
  status = "SKIP" if not rows else ("FAIL" if failed else "PASS")
  lines = [
    "# Added-file coverage",
    "",
    f"- Status: {status}",
    f"- Threshold: {args.threshold:g}%",
    f"- Checked files: {len(rows)}",
    f"- Files below threshold: {len(failed)}",
    "",
  ]
  for path, covered, total in rows:
    percent = covered * 100 / total if total else 100
    lines.append(f"- `{path}`: {percent:.2f}% ({covered}/{total})")

  summary = "\n".join(lines) + "\n"
  print(summary)
  if destination := os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(destination, "a", encoding="utf-8") as output:
      output.write(summary)
  return int(bool(failed))


if __name__ == "__main__":
  try:
    sys.exit(main())
  except Exception as error:
    print(f"Coverage check error: {error}", file=sys.stderr)
    sys.exit(2)
