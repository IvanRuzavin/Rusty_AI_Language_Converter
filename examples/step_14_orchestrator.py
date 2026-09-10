"""Run every implemented conversion stage with an offline fake model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    FakeModelClient,
    OrchestrationError,
    PIPELINE_STAGES,
    PipelineConfig,
    run_conversion,
)
from step_10_fake_model import (
    DEFAULT_CACHE_DIR,
    DEFAULT_C_SDK,
    DEFAULT_METADATA_PATH,
    DEFAULT_RUST_SDK,
    PROJECT_ROOT,
)
from step_13_validate import formatted_fixture


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output" / "step_14"


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
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=DEFAULT_MAX_REQUEST_BYTES,
    )
    parser.add_argument(
        "--validation-timeout",
        type=float,
        default=DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    )
    parser.add_argument("--rustfmt", default="rustfmt", help="rustfmt executable")
    parser.add_argument("--cargo", default="cargo", help="Cargo executable")
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated package cache exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete ConversionRun, including source and fake output",
    )
    return parser.parse_args()


def offline_client_factory(plan):
    """Create exact fixture output only after the translation plan exists."""
    return FakeModelClient(formatted_fixture(plan))


def progress(stage: str, detail: str) -> None:
    position = PIPELINE_STAGES.index(stage) + 1
    print(f"[{position}/{len(PIPELINE_STAGES)}] {stage}: {detail}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    config = PipelineConfig(
        query=args.query,
        metadata_path=args.metadata,
        cache_dir=args.cache_dir,
        c_sdk_path=args.c_sdk,
        rust_sdk_path=args.rust_sdk,
        output_root=args.output_root,
        allow_download=args.download,
        max_request_bytes=args.max_request_bytes,
        validation_timeout_seconds=args.validation_timeout,
        rustfmt_executable=args.rustfmt,
        cargo_executable=args.cargo,
    )
    try:
        result = asyncio.run(
            run_conversion(
                config,
                offline_client_factory,
                progress=progress,
            )
        )
    except OrchestrationError as error:
        print(f"Pipeline stopped at {error.stage}: {error.detail}", file=sys.stderr)
        raise SystemExit(2) from error

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Package:                {result.package.name}")
        print(f"Run status:             {result.status}")
        print(f"Downloaded this run:    {result.network_download_performed}")
        print(f"Model:                  {result.model_conversion.model_id}")
        print(f"Model tokens:           {result.model_conversion.usage.total_tokens}")
        print(f"Request bytes:          {result.model_request.request_bytes}")
        print(f"Rendered directory:     {result.rendered_package.output_directory}")
        print(f"Reused existing output: {result.rendered_package.reused_existing}")
        print(f"Local checks passed:    {result.validation_report.passed}")
        print("Full SDK compilation:   not_run")
        print(
            "\nThe fake output demonstrates orchestration, "
            "not C-to-Rust translation."
        )
        print("No network request or AI model call was made.")

    if not result.validation_report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
