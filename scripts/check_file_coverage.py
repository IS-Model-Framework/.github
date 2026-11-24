import argparse
import os
from pathlib import Path
import subprocess
import sys

from coverage import Coverage


SOURCE_DIRECTORIES = {
  "IS-Model-Framework/PallasKernels": {"jax_ops", "pallas_kernels", "tools"},
  "IS-Model-Framework/ProtonSamples": {"proton_samples", "tools"},
}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--threshold", type=float, default=85)
  args = parser.parse_args()
  if not 0 <= args.threshold <= 100:
    parser.error("--threshold must be between 0 and 100")

  included = SOURCE_DIRECTORIES.get(os.environ.get("GITHUB_REPOSITORY"))
  if included is None:
    return 0

  base = os.environ["PR_BASE_SHA"]
  head = os.environ["PR_HEAD_SHA"]
  added = subprocess.check_output([
    "git",
    "diff",
    "--name-only",
    "--diff-filter=AMR",
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
    if path.endswith(".py") and path.split("/", 1)[0] in included
  )

  if not files:
    return 0

  cov = Coverage()
  cov.load()
  if not Path(cov.get_data().data_filename()).is_file():
    raise RuntimeError("Coverage data file is missing")

  failed = False
  for path in files:
    _, statements, _, missing, _ = cov.analysis2(path)
    total = len(statements)
    covered = total - len(missing)

    if total and covered * 100 < args.threshold * total:
      print(
        f"{path}: {covered * 100 / total:.2f}% ({covered}/{total}), "
        f"required {args.threshold:g}%",
        file=sys.stderr,
      )
      failed = True
  return int(failed)


if __name__ == "__main__":
  try:
    sys.exit(main())
  except Exception as error:
    print(f"Coverage check error: {error}", file=sys.stderr)
    sys.exit(2)
