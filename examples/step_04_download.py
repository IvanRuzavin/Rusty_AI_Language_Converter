"""Preview or download one real Click package into the local archive cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from rusty_ai_converter import download_package, load_catalog


PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_METADATA_PATH = PROJECT_ROOT / "metadata_clicks_c.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query",
        nargs="?",
        default="IPS Display 2 Click",
        help="exact or partial Click package name",
    )
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm that the script may perform the HTTP download",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="download again even when a validated cache entry exists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = load_catalog(args.metadata)
    matches = catalog.search(args.query)
    exact_matches = [
        package for package in matches if package.name.casefold() == args.query.casefold()
    ]

    if len(exact_matches) == 1:
        package = exact_matches[0]
    elif len(matches) == 1:
        package = matches[0]
    else:
        print(f"Expected one package for {args.query!r}, found {len(matches)}.")
        for match in matches[:10]:
            print(f"  {match.name} [{match.category}]")
        raise SystemExit(2)

    print(f"Selected: {package.name} [{package.category}]")
    print(f"URL:      {package.download_url}")
    print(f"Cache:    {args.cache_dir}")

    if not args.yes:
        print("\nPreview only: no network request was made.")
        print("Run again with --yes to permit the ordinary HTTP download.")
        return

    print("\nDownloading with the standard Python HTTP client...")
    archive = download_package(
        package,
        args.cache_dir,
        force=args.force,
    )
    source = "validated cache" if archive.cache_hit else "network download"
    print(f"Source:   {source}")
    print(f"Archive:  {archive.archive_path}")
    print(f"Size:     {archive.size_bytes:,} bytes")
    print(f"SHA-256:  {archive.sha256}")
    print("AI usage: none")


if __name__ == "__main__":
    main()
