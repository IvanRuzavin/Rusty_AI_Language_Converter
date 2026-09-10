"""Render an offline fixture and run the local Rust validation gate."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    ArtifactRenderError,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    FakeModelClient,
    render_package,
    validate_rendered_package,
)
from step_10_fake_model import (
    DEFAULT_CACHE_DIR,
    DEFAULT_C_SDK,
    DEFAULT_METADATA_PATH,
    DEFAULT_RUST_SDK,
    PROJECT_ROOT,
    build_request,
)
from step_12_render import renderer_fixture


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output" / "step_13"


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
    parser.add_argument("--rustfmt", default="rustfmt", help="rustfmt executable")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VALIDATION_TIMEOUT_SECONDS,
        help="maximum seconds for each local tool command",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated package cache exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete ValidationReport",
    )
    return parser.parse_args()


def formatted_fixture(plan):
    """Make the Step 12 offline fixture satisfy the formatting gate."""
    return replace(
        renderer_fixture(plan),
        main_rs=(
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "use library::*;\nuse mikrobus::*;\n\n"
            "#[unsafe(no_mangle)]\n"
            "fn main() -> ! {\n"
            "    loop {\n"
            "        core::hint::spin_loop();\n"
            "    }\n"
            "}\n"
        ),
        warnings=(
            "Offline fixture only; generated Rust has not translated the C package.",
        ),
    )


def main() -> None:
    args = parse_args()
    package, plan, request = build_request(args)
    conversion = asyncio.run(
        FakeModelClient(formatted_fixture(plan)).convert(request)
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

    report = validate_rendered_package(
        rendered,
        rustfmt_executable=args.rustfmt,
        cargo_executable=args.cargo,
        timeout_seconds=args.timeout,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status = "PASSED" if report.passed else "FAILED"
        print(f"Package:              {package.name}")
        print(f"Output directory:     {report.output_directory}")
        print(f"Local validation:     {status}")
        print(f"Rustfmt:              {report.rustfmt_version or 'unavailable'}")
        print(f"Cargo:                {report.cargo_version or 'unavailable'}")
        print(f"Full SDK compilation: {report.sdk_compilation_status}")
        print("\nChecks:")
        for check in report.checks:
            marker = check.status.upper()
            print(f"  [{marker:<7}] {check.name}: {check.summary}")
            diagnostics = (check.stdout + check.stderr).strip()
            if check.status == "failed" and diagnostics:
                for line in diagnostics.splitlines():
                    print(f"             {line}")
        print("\nNo network request or AI model call was made.")
        print("This fixture is not a translated or SDK-compiled driver.")

    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
