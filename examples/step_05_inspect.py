"""Inspect and classify one cached Click archive without extracting it."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    download_package,
    inspect_archive,
    load_cached_archive,
    load_catalog,
)


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
        "--download",
        action="store_true",
        help="permit an ordinary HTTPS download when no valid cache entry exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete versioned inventory as JSON",
    )
    return parser.parse_args()


def select_package(query: str, metadata_path: Path):
    catalog = load_catalog(metadata_path)
    matches = catalog.search(query)
    exact_matches = [
        package for package in matches if package.name.casefold() == query.casefold()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(matches) == 1:
        return matches[0]

    print(f"Expected one package for {query!r}, found {len(matches)}.")
    for match in matches[:10]:
        print(f"  {match.name} [{match.category}]")
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    package = select_package(args.query, args.metadata)
    cached_archive = load_cached_archive(package, args.cache_dir)

    if cached_archive is None and not args.download:
        print(
            f"No validated cached archive exists for {package.name}.",
            file=sys.stderr,
        )
        print("No network request was made.", file=sys.stderr)
        print(
            "Run again with --download, or first run "
            f"examples/step_04_download.py {args.query!r} --yes.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if cached_archive is None:
        print(
            "No cached archive found; performing the permitted HTTPS download...",
            file=sys.stderr,
        )
        cached_archive = download_package(package, args.cache_dir)

    inventory = inspect_archive(cached_archive)
    if args.json:
        print(json.dumps(inventory.to_dict(), indent=2))
        return

    role_counts = Counter(file.role for file in inventory.files)
    duplicate_count = sum(file.duplicate_of is not None for file in inventory.files)
    print(f"Package:        {package.name} [{package.category}]")
    print(f"Archive:        {cached_archive.archive_path}")
    print(f"Archive SHA-256: {cached_archive.sha256}")
    print(f"Files:          {len(inventory.files)}")
    print(f"Expanded bytes: {inventory.total_expanded_bytes}")
    print(f"Common root:    {inventory.common_root or '(none)'}")
    print(f"Duplicates:     {duplicate_count}")
    print("\nRoles:")
    for role, count in sorted(role_counts.items()):
        print(f"  {role:24} {count}")
    if inventory.diagnostics:
        print("\nDiagnostics:")
        for diagnostic in inventory.diagnostics:
            print(f"  - {diagnostic}")

    print("\nNo archive member was extracted, and no AI model was called.")


if __name__ == "__main__":
    main()
