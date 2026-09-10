"""Parse canonical Click C sources into the first ClickPackageIR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    build_source_bundle,
    download_package,
    inspect_archive,
    load_cached_archive,
    load_catalog,
    parse_source_bundle,
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
        help="permit ordinary HTTPS when no validated cache entry exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete ClickPackageIR, including source slices",
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
    source_bundle = build_source_bundle(inventory)
    package_ir = parse_source_bundle(source_bundle)
    if args.json:
        print(json.dumps(package_ir.to_dict(), indent=2))
        return

    print(f"Package: {source_bundle.manifest.display_name}")
    print(f"Version: {source_bundle.manifest.version}")
    print(f"Parsed C files: {len(package_ir.files)}")
    for parsed_file in package_ir.files:
        print(f"\n{parsed_file.path}")
        print(f"  includes:      {len(parsed_file.includes)}")
        print(f"  macros:        {len(parsed_file.macros)}")
        print(f"  functions:     {len(parsed_file.functions)}")
        print(f"  structs/unions:{len(parsed_file.composites):>6}")
        print(f"  enums:         {len(parsed_file.enums)}")
        print(f"  typedefs:      {len(parsed_file.typedefs)}")
        print(f"  globals:       {len(parsed_file.global_variables)}")
        print(f"  conditions:    {len(parsed_file.preprocessor_conditions)}")
        print(f"  diagnostics:   {len(parsed_file.diagnostics)}")

    definitions = package_ir.function_definitions
    defined_names = {function.name for function in definitions}
    external_calls = sorted(
        {
            call
            for function in definitions
            for call in function.calls
            if call not in defined_names
        },
        key=str.casefold,
    )
    print(f"\nFunction definitions: {len(definitions)}")
    for function in definitions:
        print(
            f"  {function.name:36} "
            f"{function.span.file_path}:{function.span.start_line}"
        )
    print(f"\nPotential external calls: {len(external_calls)}")
    for call in external_calls:
        print(f"  {call}")
    if package_ir.diagnostics:
        print("\nPackage diagnostics:")
        for diagnostic in package_ir.diagnostics:
            print(f"  - {diagnostic}")

    print("\nNo AI model was called.")


if __name__ == "__main__":
    main()
