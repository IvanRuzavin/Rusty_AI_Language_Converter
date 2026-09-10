"""Index the reference SDKs and resolve one Click package's dependencies."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    build_sdk_mapping_database,
    build_source_bundle,
    build_translation_plan,
    download_package,
    inspect_archive,
    load_cached_archive,
    load_catalog,
    parse_source_bundle,
)


PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_METADATA_PATH = PROJECT_ROOT / "metadata_clicks_c.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache"
DEFAULT_C_SDK = PROJECT_ROOT / "references" / "c_sdk"
DEFAULT_RUST_SDK = PROJECT_ROOT / "references" / "rust_sdk"


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
    parser.add_argument("--c-sdk", type=Path, default=DEFAULT_C_SDK)
    parser.add_argument("--rust-sdk", type=Path, default=DEFAULT_RUST_SDK)
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated cache entry exists",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="print the complete package TranslationPlan",
    )
    output.add_argument(
        "--sdk-json",
        action="store_true",
        help="print the complete SDK mapping database",
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

    print(f"Expected one package for {query!r}, found {len(matches)}.", file=sys.stderr)
    for match in matches[:10]:
        print(f"  {match.name} [{match.category}]", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    sdk_database = build_sdk_mapping_database(args.c_sdk, args.rust_sdk)
    if args.sdk_json:
        print(json.dumps(sdk_database.to_dict(), indent=2))
        return

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

    source_bundle = build_source_bundle(inspect_archive(cached_archive))
    package_ir = parse_source_bundle(source_bundle)
    plan = build_translation_plan(package_ir, sdk_database)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
        return

    mapping_counts = Counter(mapping.status for mapping in sdk_database.mappings)
    resolution_counts = Counter(item.status for item in plan.resolutions)
    print("SDK index")
    print(f"  C functions:          {len(sdk_database.c_functions)}")
    print(f"  Rust functions:       {len(sdk_database.rust_functions)}")
    print(f"  direct mappings:      {mapping_counts['direct']}")
    print(f"  reviewed adapters:    {mapping_counts['adapted']}")
    print(f"  unsupported C APIs:   {mapping_counts['unsupported']}")
    print(f"  parser diagnostics:   {len(sdk_database.diagnostics)}")

    print(f"\nPackage: {source_bundle.manifest.display_name}")
    print(f"Distinct calls: {len(plan.resolutions)}")
    for status, count in sorted(resolution_counts.items()):
        print(f"  {status:18} {count}")
    print("Required Rust crates:")
    for crate in plan.required_rust_crates:
        print(f"  {crate}")

    print("\nNon-local call decisions:")
    for resolution in plan.resolutions:
        if resolution.status in {"local_function", "local_macro"}:
            continue
        target = resolution.rust_target or "BLOCKED"
        print(f"  {resolution.call:36} {resolution.status:18} {target}")

    if plan.unresolved:
        print("\nUnresolved calls block model use:")
        for resolution in plan.unresolved:
            print(f"  {resolution.call}: {resolution.guidance}")
    else:
        print("\nAll calls are classified; this plan may proceed to context building.")
    print("No AI model was called.")


if __name__ == "__main__":
    main()
