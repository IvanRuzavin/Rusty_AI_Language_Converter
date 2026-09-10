"""Preview or explicitly send one conversion request to OpenAI."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from rusty_ai_converter import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT_SECONDS,
    ModelClientError,
    OpenAIModelClient,
)
from step_10_fake_model import (
    DEFAULT_CACHE_DIR,
    DEFAULT_C_SDK,
    DEFAULT_METADATA_PATH,
    DEFAULT_RUST_SDK,
    build_request,
)


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
        "--model",
        default=None,
        help=f"override OPENAI_MODEL (default: {DEFAULT_OPENAI_MODEL})",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default=DEFAULT_REASONING_EFFORT,
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="permit ordinary HTTPS when no validated package cache exists",
    )
    parser.add_argument(
        "--yes-use-openai",
        action="store_true",
        help="authorize one paid OpenAI Responses API request",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete validated response, including generated code",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        client = OpenAIModelClient(
            model_id=args.model,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            reasoning_effort=args.reasoning_effort,
            allow_api_call=args.yes_use_openai,
        )
    except (ModelClientError, TypeError, ValueError) as error:
        print(f"Invalid model configuration: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    package, _, request = build_request(args)
    print(f"Package:              {package.name}")
    print(f"Model:                {client.model_id}")
    print(f"Approx. input tokens: {request.estimated_input_tokens}")
    print(f"Maximum output tokens:{client.max_output_tokens:>8}")
    print(f"Reasoning effort:     {client.reasoning_effort}")
    print(f"Timeout:              {client.timeout_seconds:g} seconds")

    if not args.yes_use_openai:
        print("\nPreview only: no OpenAI API request was made.")
        print("Add --yes-use-openai to authorize one paid request.")
        return

    print("\nSending one paid request to the OpenAI Responses API...", file=sys.stderr)
    try:
        conversion = asyncio.run(client.convert(request))
    except ModelClientError as error:
        print(f"Conversion request failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    if args.json:
        print(json.dumps(conversion.to_dict(), indent=2))
        return

    print(f"Response ID:          {conversion.response_id}")
    print(f"Actual model:         {conversion.model_id}")
    print(f"Input tokens:         {conversion.usage.input_tokens}")
    print(f"Output tokens:        {conversion.usage.output_tokens}")
    print(f"Total tokens:         {conversion.usage.total_tokens}")
    print(f"Latency:              {conversion.latency_ms} ms")
    print(f"library.rs bytes:     {len(conversion.output.library_rs.encode())}")
    print(f"main.rs bytes:        {len(conversion.output.main_rs.encode())}")
    print(f"Coverage entries:     {len(conversion.output.api_coverage_ledger)}")
    print("\nNo Rust files were written. Use --json to inspect the full response.")


if __name__ == "__main__":
    main()
