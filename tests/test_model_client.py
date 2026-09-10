"""Tests for strict model output and the offline model client."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rusty_ai_converter.context import ModelMessage, ModelRequest
from rusty_ai_converter.model_client import (
    DEFAULT_OPENAI_MODEL,
    FakeModelClient,
    ModelClient,
    ModelClientConfigurationError,
    ModelClientError,
    OpenAIModelClient,
)
from rusty_ai_converter.model_output import (
    ModelOutputError,
    model_output_response_schema,
    parse_model_output,
)


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


class StubResponses:
    def __init__(self, response: object, *, delay: float = 0) -> None:
        self.response = response
        self.delay = delay
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


class StubOpenAIClient:
    def __init__(self, response: object, *, delay: float = 0) -> None:
        self.responses = StubResponses(response, delay=delay)


def completed_response(output: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp_test",
        model="gpt-5.6-luna",
        status="completed",
        output=[],
        output_text=json.dumps(output or valid_output()),
        usage=SimpleNamespace(input_tokens=120, output_tokens=80),
        error=None,
        incomplete_details=None,
    )


class OpenAIModelClientTests(unittest.TestCase):
    def test_defaults_to_luna_and_requires_explicit_permission(self) -> None:
        stub = StubOpenAIClient(completed_response())
        with patch.dict(os.environ, {}, clear=True):
            client = OpenAIModelClient(client=stub)

        self.assertEqual(client.model_id, DEFAULT_OPENAI_MODEL)
        self.assertFalse(client.api_call_allowed)
        with self.assertRaisesRegex(
            ModelClientConfigurationError,
            "explicitly set allow_api_call",
        ):
            asyncio.run(client.convert(model_request()))
        self.assertEqual(stub.responses.calls, [])

    def test_rejects_sol_even_when_selected_through_environment(self) -> None:
        for model_id in ("gpt-5.6-sol", "gpt-5.6"):
            with self.subTest(model_id=model_id):
                with patch.dict(os.environ, {"OPENAI_MODEL": model_id}):
                    with self.assertRaisesRegex(
                        ModelClientConfigurationError,
                        "Sol models are disabled",
                    ):
                        OpenAIModelClient(
                            client=StubOpenAIClient(completed_response())
                        )

        response = completed_response()
        response.model = "gpt-5.6-sol"
        client = OpenAIModelClient(
            client=StubOpenAIClient(response),
            allow_api_call=True,
        )
        with self.assertRaisesRegex(ModelClientError, "returned disabled Sol"):
            asyncio.run(client.convert(model_request()))

    def test_sends_bounded_nonstored_strict_request_and_records_usage(self) -> None:
        stub = StubOpenAIClient(completed_response())
        client = OpenAIModelClient(
            client=stub,
            allow_api_call=True,
            max_output_tokens=4096,
            reasoning_effort="low",
        )

        conversion = asyncio.run(client.convert(model_request()))

        self.assertEqual(conversion.response_id, "resp_test")
        self.assertEqual(conversion.usage.input_tokens, 120)
        self.assertEqual(conversion.usage.output_tokens, 80)
        self.assertEqual(conversion.usage.total_tokens, 200)
        call = stub.responses.calls[0]
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(call["max_output_tokens"], 4096)
        self.assertEqual(call["reasoning"], {"effort": "low"})
        self.assertFalse(call["store"])
        self.assertEqual(call["truncation"], "disabled")
        self.assertEqual(call["prompt_cache_key"], "a" * 64)
        text = call["text"]
        assert isinstance(text, dict)
        response_format = text["format"]
        self.assertTrue(response_format["strict"])
        self.assertEqual(response_format["schema"], model_output_response_schema())

    def test_rejects_incomplete_response_with_provider_reason(self) -> None:
        response = completed_response()
        response.status = "incomplete"
        response.incomplete_details = SimpleNamespace(reason="max_output_tokens")
        client = OpenAIModelClient(
            client=StubOpenAIClient(response),
            allow_api_call=True,
        )

        with self.assertRaisesRegex(ModelClientError, "max_output_tokens"):
            asyncio.run(client.convert(model_request()))

    def test_rejects_model_refusal(self) -> None:
        response = completed_response()
        response.output = [
            SimpleNamespace(
                content=[
                    SimpleNamespace(type="refusal", refusal="Cannot convert this.")
                ]
            )
        ]
        client = OpenAIModelClient(
            client=StubOpenAIClient(response),
            allow_api_call=True,
        )

        with self.assertRaisesRegex(ModelClientError, "Cannot convert this"):
            asyncio.run(client.convert(model_request()))

    def test_rejects_schema_invalid_completed_output(self) -> None:
        response = completed_response()
        response.output_text = '{"library_rs": "missing other fields"}'
        client = OpenAIModelClient(
            client=StubOpenAIClient(response),
            allow_api_call=True,
        )

        with self.assertRaisesRegex(ModelClientError, "invalid model output"):
            asyncio.run(client.convert(model_request()))

    def test_applies_a_whole_request_timeout(self) -> None:
        client = OpenAIModelClient(
            client=StubOpenAIClient(completed_response(), delay=0.02),
            allow_api_call=True,
            timeout_seconds=0.001,
        )

        with self.assertRaisesRegex(ModelClientError, "exceeded"):
            asyncio.run(client.convert(model_request()))


if __name__ == "__main__":
    unittest.main()
