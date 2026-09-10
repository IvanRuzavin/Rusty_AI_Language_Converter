"""Render an offline fixture through the real deterministic artifact stage."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    ArtifactRenderError,
    CoverageEntry,
    DEFAULT_MAX_REQUEST_BYTES,
    FakeModelClient,
    ModelOutput,
    render_package,
    required_coverage_symbols,
)
from step_10_fake_model import (
    DEFAULT_CACHE_DIR,
    DEFAULT_C_SDK,
    DEFAULT_METADATA_PATH,
    DEFAULT_RUST_SDK,
    PROJECT_ROOT,
    build_request,
)


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output" / "step_12"


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
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated package cache exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete RenderedPackage report",
    )
    return parser.parse_args()


def renderer_fixture(plan) -> ModelOutput:
    coverage = tuple(
        CoverageEntry(
            source_symbol=name,
            source_kind=kind,
            status="not_applicable",
            rust_symbol=None,
            rationale=(
                "Offline fixture exercises rendering; semantic translation was not run."
            ),
        )
        for kind, name in required_coverage_symbols(plan)
    )
    return ModelOutput(
        library_rs="//! Offline renderer fixture.\n\npub fn offline_fixture() {}\n",
        main_rs=(
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "use library::*;\nuse mikrobus::*;\n\n"
            "#[unsafe(no_mangle)]\n"
            "fn main() -> ! { loop { core::hint::spin_loop(); } }\n"
        ),
        required_rust_crates=plan.required_rust_crates,
        api_coverage_ledger=coverage,
        warnings=(
            "Offline fixture only; generated Rust has not translated the C package.",
        ),
    )


def main() -> None:
    args = parse_args()
    package, plan, request = build_request(args)
    conversion = asyncio.run(
        FakeModelClient(renderer_fixture(plan)).convert(request)
    )
    try:
        rendered = render_package(
            plan,
            request,
            conversion,
            args.output_root,
        )
    except ArtifactRenderError as error:
        print(f"Cannot render package: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    if args.json:
        print(json.dumps(rendered.to_dict(), indent=2))
        return

    action = "reused identical" if rendered.reused_existing else "created"
    print(f"Package:          {package.name}")
    print(f"Output directory: {rendered.output_directory}")
    print(f"Action:           {action}")
    print(f"Files:            {len(rendered.files)}")
    print(f"Total bytes:      {rendered.total_size_bytes}")
    print(f"Coverage entries: {len(rendered.api_coverage_ledger)}")
    print("\nRendered files:")
    for item in rendered.files:
        print(f"  {item.path:<12} {item.size_bytes:>6} bytes  {item.sha256[:12]}...")
    print("\nThis is an offline rendering fixture, not a translated driver.")
    print("No network request or AI model call was made.")


if __name__ == "__main__":
    main()
