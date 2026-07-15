"""Materialize private GitHub Release wheel direct refs for pip installs.

pip cannot authenticate GitHub's private ``/releases/download/`` URLs with the
token header style used by the GitHub API. This helper keeps ``pyproject.toml``
as the single source of dependency versions, downloads matching wheel assets via
``gh release download``, and rewrites the current workspace's direct URLs to
local ``file://`` URLs before ``pip install`` runs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

DEFAULT_OUT_DIR = Path("/tmp/private-release-assets")
GITHUB_RELEASE_MARKER = ("releases", "download")


@dataclass(frozen=True)
class GitHubReleaseWheel:
  requirement: str
  url: str
  owner: str
  repo: str
  tag: str
  asset: str

  @property
  def repo_slug(self) -> str:
    return f"{self.owner}/{self.repo}"


def _iter_strings(value: Any) -> list[str]:
  if isinstance(value, str):
    return [value]
  if isinstance(value, list):
    strings: list[str] = []
    for item in value:
      strings.extend(_iter_strings(item))
    return strings
  if isinstance(value, dict):
    strings = []
    for item in value.values():
      strings.extend(_iter_strings(item))
    return strings
  return []


def parse_github_release_wheel(requirement: str) -> GitHubReleaseWheel | None:
  if " @ " not in requirement:
    return None

  _, url = requirement.split(" @ ", 1)
  parsed = urlparse(url)
  if parsed.scheme != "https" or parsed.netloc != "github.com":
    return None

  parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
  if len(parts) != 6:
    return None
  owner, repo, releases, download, tag, asset = parts
  if (releases, download) != GITHUB_RELEASE_MARKER:
    return None
  if not asset.endswith(".whl"):
    return None

  return GitHubReleaseWheel(
    requirement=requirement,
    url=url,
    owner=owner,
    repo=repo,
    tag=tag,
    asset=asset,
  )


def load_release_wheels(pyproject_path: Path) -> list[GitHubReleaseWheel]:
  with pyproject_path.open("rb") as f:
    pyproject = tomllib.load(f)

  wheels: list[GitHubReleaseWheel] = []
  seen_urls: set[str] = set()
  for value in _iter_strings(pyproject):
    wheel = parse_github_release_wheel(value)
    if wheel is None or wheel.url in seen_urls:
      continue
    wheels.append(wheel)
    seen_urls.add(wheel.url)
  return wheels


def _target_dir(out_dir: Path, wheel: GitHubReleaseWheel) -> Path:
  return out_dir / wheel.owner / wheel.repo / wheel.tag


def _target_path(out_dir: Path, wheel: GitHubReleaseWheel) -> Path:
  return _target_dir(out_dir, wheel) / wheel.asset


def download_wheel(out_dir: Path, wheel: GitHubReleaseWheel) -> Path:
  target_dir = _target_dir(out_dir, wheel)
  target_dir.mkdir(parents=True, exist_ok=True)
  subprocess.run(
    [
      "gh",
      "release",
      "download",
      wheel.tag,
      "--repo",
      wheel.repo_slug,
      "--pattern",
      wheel.asset,
      "--dir",
      str(target_dir),
      "--clobber",
    ],
    check=True,
  )

  target_path = _target_path(out_dir, wheel)
  if not target_path.is_file():
    raise FileNotFoundError(
      f"Downloaded asset not found: {target_path}. "
      f"Check release {wheel.repo_slug}@{wheel.tag} asset {wheel.asset}."
    )
  return target_path


def rewrite_pyproject(
  pyproject_path: Path,
  replacements: dict[str, Path],
) -> None:
  text = pyproject_path.read_text(encoding="utf-8")
  for url, local_path in replacements.items():
    local_url = local_path.resolve().as_uri()
    if url not in text:
      raise ValueError(f"Release URL is not present in {pyproject_path}: {url}")
    text = text.replace(url, local_url)
  pyproject_path.write_text(text, encoding="utf-8")


def materialize_private_release_wheels(pyproject_path: Path, out_dir: Path) -> int:
  wheels = load_release_wheels(pyproject_path)
  if not wheels:
    print(f"No GitHub Release wheel direct refs found in {pyproject_path}.")
    return 0

  if not os.environ.get("GH_TOKEN"):
    print(
      "GH_TOKEN is required to download private GitHub Release wheels.",
      file=sys.stderr,
    )
    return 1

  replacements: dict[str, Path] = {}
  for wheel in wheels:
    local_path = download_wheel(out_dir, wheel)
    replacements[wheel.url] = local_path
    print(f"Materialized {wheel.repo_slug}@{wheel.tag}: {wheel.asset}")

  rewrite_pyproject(pyproject_path, replacements)
  return 0


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--pyproject",
    default="pyproject.toml",
    type=Path,
    help="Path to the pyproject.toml to rewrite in-place.",
  )
  parser.add_argument(
    "--out-dir",
    default=DEFAULT_OUT_DIR,
    type=Path,
    help="Directory for downloaded private release wheels.",
  )
  args = parser.parse_args(argv)
  return materialize_private_release_wheels(args.pyproject, args.out_dir)


if __name__ == "__main__":
  raise SystemExit(main())
