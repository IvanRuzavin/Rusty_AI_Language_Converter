"""Build a bounded, provider-neutral model request without calling a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    ContextBuildError,
    DEFAULT_MAX_REQUEST_BYTES,
    build_model_request,
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
        "--max-request-bytes",
        type=int,
        default=DEFAULT_MAX_REQUEST_BYTES,
        help="fail locally if the two message contents exceed this many bytes",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated cache entry exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete ModelRequest, including both message contents",
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

    package_ir = parse_source_bundle(
        build_source_bundle(inspect_archive(cached_archive))
    )
    sdk_database = build_sdk_mapping_database(args.c_sdk, args.rust_sdk)
    plan = build_translation_plan(package_ir, sdk_database)
    try:
        request = build_model_request(
            plan,
            sdk_database,
            max_request_bytes=args.max_request_bytes,
        )
    except ContextBuildError as error:
        print(f"Cannot build model request: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    if args.json:
        print(json.dumps(request.to_dict(), indent=2))
        return

    print(f"Package:                 {package.name}")
    print(f"Prompt version:          {request.prompt_version}")
    print(f"Context SHA-256:         {request.context_sha256}")
    print(f"Messages:                {len(request.messages)} (system + user)")
    print(f"Request content bytes:   {request.request_bytes}")
    print(f"Approx. input tokens:    {request.estimated_input_tokens}")
    print(f"Configured byte limit:   {args.max_request_bytes}")
    print(f"C source files included: {len(request.included_source_files)}")
    print(f"Rust functions included: {len(request.included_rust_functions)}")

    print("\nSelected Rust SDK evidence:")
    for function in request.included_rust_functions:
        print(f"  {function}")

    print("\nTrusted system instructions:")
    for line in request.messages[0].content.splitlines():
        print(f"  {line}")

    print("\nThe full untrusted JSON payload is available with --json.")
    print("No network request or AI model call was made.")


if __name__ == "__main__":
    main()
