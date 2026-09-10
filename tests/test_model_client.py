"""Tests for strict model output and the offline model client."""

from __future__ import annotations

import asyncio
import json
import unittest

from rusty_ai_converter.context import ModelMessage, ModelRequest
from rusty_ai_converter.model_client import FakeModelClient, ModelClient
from rusty_ai_converter.model_output import ModelOutputError, parse_model_output


def valid_output() -> dict[str, object]:
    return {
        "schema_version": "1",
        "library_rs": "#![no_std]\n\npub fn example() {}\n",
        "main_rs": (
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "fn main() -> ! { loop {} }\n"
        ),
        "required_rust_crates": ["system", "drv_spi_master"],
        "api_coverage_ledger": [
            {
                "source_symbol": "example",
                "source_kind": "function",
                "status": "translated",
                "rust_symbol": "example",
                "rationale": "The C function has a direct Rust implementation.",
            }
        ],
        "assumptions": [],
        "warnings": [],
        "unsupported_items": [],
    }


def model_request() -> ModelRequest:
    return ModelRequest(
        prompt_version="test-prompt-v1",
        context_sha256="a" * 64,
        messages=(
            ModelMessage(role="system", content="Trusted instructions."),
            ModelMessage(role="user", content="Untrusted data."),
        ),
        request_bytes=36,
        estimated_input_tokens=9,
        included_source_files=("example.c",),
        included_rust_functions=("system::system_init",),
    )


class ModelOutputTests(unittest.TestCase):
    def test_parses_strict_json_and_normalizes_crate_order(self) -> None:
        output = parse_model_output(json.dumps(valid_output()))

        self.assertEqual(output.required_rust_crates, ("drv_spi_master", "system"))
        self.assertEqual(output.api_coverage_ledger[0].rust_symbol, "example")

    def test_rejects_unexpected_top_level_and_nested_fields(self) -> None:
        top_level = valid_output()
        top_level["explanation"] = "extra"
        with self.assertRaisesRegex(ModelOutputError, "unexpected explanation"):
            parse_model_output(top_level)

        nested = valid_output()
        ledger = nested["api_coverage_ledger"]
        assert isinstance(ledger, list)
        entry = ledger[0]
        assert isinstance(entry, dict)
        entry["confidence"] = 0.9
        with self.assertRaisesRegex(ModelOutputError, "unexpected confidence"):
            parse_model_output(nested)

    def test_rejects_markdown_fences_and_missing_required_main_modules(self) -> None:
        fenced = valid_output()
        fenced["library_rs"] = "```rust\npub fn example() {}\n```"
        with self.assertRaisesRegex(ModelOutputError, "without Markdown fences"):
            parse_model_output(fenced)

        missing_module = valid_output()
        missing_module["main_rs"] = "#![no_std]\n#![no_main]\nmod library;"
        with self.assertRaisesRegex(ModelOutputError, "mod mikrobus"):
            parse_model_output(missing_module)

    def test_rejects_fenced_json_instead_of_repairing_it(self) -> None:
        fenced_json = f"```json\n{json.dumps(valid_output())}\n```"

        with self.assertRaisesRegex(ModelOutputError, "invalid model output"):
            parse_model_output(fenced_json)

    def test_unsupported_coverage_requires_an_explanation(self) -> None:
        value = valid_output()
        ledger = value["api_coverage_ledger"]
        assert isinstance(ledger, list)
        entry = ledger[0]
        assert isinstance(entry, dict)
        entry["status"] = "unsupported"
        entry["rust_symbol"] = None

        with self.assertRaisesRegex(ModelOutputError, "unsupported_items"):
            parse_model_output(value)


class FakeModelClientTests(unittest.TestCase):
    def test_returns_validated_fixture_with_zero_usage_and_provenance(self) -> None:
        request = model_request()
        client = FakeModelClient(valid_output())

        first = asyncio.run(client.convert(request))
        second = asyncio.run(client.convert(request))

        self.assertIsInstance(client, ModelClient)
        self.assertEqual(client.request_count, 2)
        self.assertIs(client.last_request, request)
        self.assertEqual(first.response_id, "fake-response-1")
        self.assertEqual(second.response_id, "fake-response-2")
        self.assertEqual(first.request_context_sha256, request.context_sha256)
        self.assertEqual(first.prompt_version, request.prompt_version)
        self.assertEqual(first.usage.total_tokens, 0)
        self.assertEqual(first.latency_ms, 0)


if __name__ == "__main__":
    unittest.main()
