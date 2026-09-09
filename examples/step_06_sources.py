"""Select canonical source inputs from a cached Click package."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    build_source_bundle,
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
        help="print the complete source bundle, including selected contents",
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

    print(
        f"Expected one package for {query!r}, found {len(matches)}.",
        file=sys.stderr,
    )
    for match in matches[:10]:
        print(f"  {match.name} [{match.category}]", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    package = select_package(args.query, args.metadata)
    cached_archive = load_cached_archive(package, args.cache_dir)
    if cached_archive is None and not args.download:
        print(f"No validated cache entry exists for {package.name}.", file=sys.stderr)
        print("No network request was made.", file=sys.stderr)
        print("Run again with --download to permit ordinary HTTPS.", file=sys.stderr)
        raise SystemExit(2)
    if cached_archive is None:
        print("Downloading the package through ordinary HTTPS...", file=sys.stderr)
        cached_archive = download_package(package, args.cache_dir)

    inventory = inspect_archive(cached_archive)
    bundle = build_source_bundle(inventory)
    if args.json:
        print(json.dumps(bundle.to_dict(), indent=2))
        return

    purpose_counts = Counter(file.purpose for file in bundle.text_files)
    selected_bytes = sum(file.size_bytes for file in bundle.text_files)
    print(f"Package:       {bundle.manifest.display_name}")
    print(f"Version:       {bundle.manifest.version}")
    print(f"Product ID:    {bundle.manifest.product_id or '(none)'}")
    print(f"Libraries:     {len(bundle.manifest.libraries)}")
    print(f"Examples:      {len(bundle.manifest.example_paths)}")
    print(f"Selected text: {len(bundle.text_files)} files / {selected_bytes} bytes")
    print(f"Resources:     {len(bundle.resource_files)} metadata record(s)")

    print("\nSelected text files:")
    for source_file in bundle.text_files:
        print(
            f"  {source_file.purpose:18} {source_file.size_bytes:8}  "
            f"{source_file.path}"
        )
    if bundle.resource_files:
        print("\nResources retained as metadata (contents not loaded):")
        for resource in bundle.resource_files:
            print(f"  {resource.size_bytes:8}  {resource.path}")
    if bundle.diagnostics:
        print("\nDiagnostics:")
        for diagnostic in bundle.diagnostics:
            print(f"  - {diagnostic}")

    print("\nPurpose counts:")
    for purpose, count in sorted(purpose_counts.items()):
        print(f"  {purpose:18} {count}")
    print("\nNo files were extracted, and no AI model was called.")


if __name__ == "__main__":
    main()
