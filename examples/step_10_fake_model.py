"""Send a real package request to an offline fake model client."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    ContextBuildError,
    CoverageEntry,
    DEFAULT_MAX_REQUEST_BYTES,
    FakeModelClient,
    ModelOutput,
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
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated cache entry exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete fake ModelConversion",
    )
    return parser.parse_args()


def select_package(query: str, metadata_path: Path):
    catalog = load_catalog(metadata_path)
    matches = catalog.search(query)
    exact = [item for item in matches if item.name.casefold() == query.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]

    print(f"Expected one package for {query!r}, found {len(matches)}.", file=sys.stderr)
    for match in matches[:10]:
        print(f"  {match.name} [{match.category}]", file=sys.stderr)
    raise SystemExit(2)


def build_request(args: argparse.Namespace):
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
    return package, plan, request


def fake_output(required_crates: tuple[str, ...]) -> ModelOutput:
    return ModelOutput(
        library_rs="#![no_std]\n\npub fn offline_fixture() {}\n",
        main_rs=(
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "#[no_mangle]\nfn main() -> ! { loop { core::hint::spin_loop(); } }\n"
        ),
        required_rust_crates=required_crates,
        api_coverage_ledger=(
            CoverageEntry(
                source_symbol="offline_fake_fixture",
                source_kind="resource",
                status="unsupported",
                rust_symbol=None,
                rationale="The fake client demonstrates the boundary only.",
            ),
        ),
        warnings=("This fixture is not a translated Click package.",),
        unsupported_items=(
            "Offline fake intentionally performs no package translation.",
        ),
    )


def main() -> None:
    args = parse_args()
    package, plan, request = build_request(args)
    client = FakeModelClient(fake_output(plan.required_rust_crates))
    conversion = asyncio.run(client.convert(request))

    if args.json:
        print(json.dumps(conversion.to_dict(), indent=2))
        return

    print(f"Package:               {package.name}")
    print(f"Client model ID:       {conversion.model_id}")
    print(f"Response ID:           {conversion.response_id}")
    print(f"Request hash retained: {conversion.request_context_sha256}")
    print(f"Prompt version:        {conversion.prompt_version}")
    print(f"Input tokens:          {conversion.usage.input_tokens}")
    print(f"Output tokens:         {conversion.usage.output_tokens}")
    print(f"library.rs bytes:      {len(conversion.output.library_rs.encode())}")
    print(f"main.rs bytes:         {len(conversion.output.main_rs.encode())}")
    print(f"Coverage entries:      {len(conversion.output.api_coverage_ledger)}")
    print("\nThe returned Rust is an intentionally incomplete fixture.")
    print("No OpenAI API call was made and no model tokens were consumed.")


if __name__ == "__main__":
    main()
