"""Download the official Microsoft Planning sample assets at a pinned commit and verify SHA-256.

Usage:
    python scripts/fetch_sample_assets.py            # download missing/mismatched files into assets/official/
    python scripts/fetch_sample_assets.py --verify   # verify only, no network

Assets come from https://github.com/microsoft/fabric-samples (MIT licensed). Nothing is fabricated or
committed to this repository: files land in the git-ignored assets/ directory.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from scripts.lib.config import REPO_ROOT, load_sample  # noqa: E402

ASSET_DIR = REPO_ROOT / "assets" / "official"
CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def raw_url(repository: str, commit: str, repo_path: str, name: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{commit}/{repo_path}/{urllib.parse.quote(name)}"


def verify(asset_dir: Path, files) -> list[str]:
    problems: list[str] = []
    for sample in files:
        target = asset_dir / sample.name
        if not target.is_file():
            problems.append(f"missing: {sample.name}")
        elif target.stat().st_size != sample.bytes:
            problems.append(f"size mismatch: {sample.name} ({target.stat().st_size} != {sample.bytes})")
        elif sha256_of(target) != sample.sha256:
            problems.append(f"sha256 mismatch: {sample.name}")
    return problems


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for block in response.iter_content(CHUNK):
                handle.write(block)
    partial.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="verify existing files only")
    args = parser.parse_args(argv)

    sample = load_sample()
    if not sample.commit or not sample.files:
        print("No pinned sample assets in configuration", file=sys.stderr)
        return 2

    if not args.verify:
        for item in sample.files:
            target = ASSET_DIR / item.name
            if target.is_file() and target.stat().st_size == item.bytes and sha256_of(target) == item.sha256:
                print(f"ok (cached)  {item.name}")
                continue
            print(f"downloading  {item.name} @ {sample.commit[:8]}")
            download(raw_url(sample.repository, sample.commit, sample.path, item.name), target)

    problems = verify(ASSET_DIR, sample.files)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if not problems:
        print(f"All {len(sample.files)} assets verified against pinned SHA-256 in {ASSET_DIR}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
