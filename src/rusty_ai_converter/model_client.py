"""Provider-neutral conversion client interface and offline fake."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import re
from time import perf_counter
from typing import Any, ClassVar, Mapping, Protocol, runtime_checkable

from rusty_ai_converter.context import ModelRequest
from rusty_ai_converter.model_output import (
    ModelOutput,
    ModelOutputError,
    model_output_response_schema,
    parse_model_output,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_REASONING_EFFORT = "low"
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_MISSING = object()


class ModelClientError(Exception):
    """A model backend could not produce a completed conversion."""


class ModelClientConfigurationError(ModelClientError):
    """The model client is not safely configured for an API call."""


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _nonnegative_integer(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    return value


def _positive_integer(value: int, field_name: str) -> int:
    value = _nonnegative_integer(value, field_name)
    if value == 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _is_sol_model(model_id: str) -> bool:
    unqualified = model_id.casefold().split("/")[-1].split(":")[0]
    return unqualified == "gpt-5.6" or bool(
        re.search(r"(?:^|[-_.])sol(?:$|[-_.])", unqualified)
    )


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        result = value.get(name, _MISSING)
    else:
        result = getattr(value, name, _MISSING)
    if result is _MISSING:
        if default is _MISSING:
            raise ModelClientError(f"OpenAI response is missing {name!r}")
        return default
    return result


def _response_refusal(response: Any) -> str | None:
    for output_item in _field(response, "output", ()) or ():
        for content_item in _field(output_item, "content", ()) or ():
            if _field(content_item, "type", None) == "refusal":
                refusal = _field(content_item, "refusal", "Model refused the request")
                return str(refusal).strip() or "Model refused the request"
    return None


def _response_failure_detail(response: Any) -> str:
    error = _field(response, "error", None)
    if error is not None:
        code = _field(error, "code", None)
        message = _field(error, "message", None)
        details = ": ".join(str(item) for item in (code, message) if item)
        if details:
            return details
    incomplete = _field(response, "incomplete_details", None)
    if incomplete is not None:
        reason = _field(incomplete, "reason", None)
        if reason:
            return str(reason)
    return "no provider detail"


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        _nonnegative_integer(self.input_tokens, "input_tokens")
        _nonnegative_integer(self.output_tokens, "output_tokens")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True, slots=True)
class ModelConversion:
    """Validated model output plus request and provider provenance."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    model_id: str
    response_id: str
    request_context_sha256: str
    prompt_version: str
    output: ModelOutput
    usage: ModelUsage
    latency_ms: int
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        digest = _text(self.request_context_sha256, "request_context_sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("request_context_sha256 must be a SHA-256 digest")
        if not isinstance(self.output, ModelOutput):
            raise TypeError("output must be a ModelOutput")
        if not isinstance(self.usage, ModelUsage):
            raise TypeError("usage must be a ModelUsage")
        _nonnegative_integer(self.latency_ms, "latency_ms")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported model conversion version: {self.schema_version!r}"
            )

        object.__setattr__(self, "model_id", _text(self.model_id, "model_id"))
        object.__setattr__(self, "response_id", _text(self.response_id, "response_id"))
        object.__setattr__(self, "request_context_sha256", digest)
        object.__setattr__(
            self,
            "prompt_version",
            _text(self.prompt_version, "prompt_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "response_id": self.response_id,
            "request_context_sha256": self.request_context_sha256,
            "prompt_version": self.prompt_version,
            "output": self.output.to_dict(),
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
        }


@runtime_checkable
class ModelClient(Protocol):
    """Small asynchronous boundary implemented by fake and real providers."""

    async def convert(self, request: ModelRequest) -> ModelConversion: ...


class FakeModelClient:
    """Return one validated fixture without network access or token usage."""

    def __init__(
        self,
        output: ModelOutput | str | Mapping[str, Any],
        *,
        model_id: str = "fake-local",
        response_id_prefix: str = "fake-response",
    ) -> None:
        self._output = (
            output if isinstance(output, ModelOutput) else parse_model_output(output)
        )
        self._model_id = _text(model_id, "model_id")
        self._response_id_prefix = _text(response_id_prefix, "response_id_prefix")
        self._request_count = 0
        self._last_request: ModelRequest | None = None

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def last_request(self) -> ModelRequest | None:
        return self._last_request

    async def convert(self, request: ModelRequest) -> ModelConversion:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        self._request_count += 1
        self._last_request = request
        return ModelConversion(
            model_id=self._model_id,
            response_id=f"{self._response_id_prefix}-{self._request_count}",
            request_context_sha256=request.context_sha256,
            prompt_version=request.prompt_version,
            output=self._output,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            latency_ms=0,
        )


class OpenAIModelClient:
    """Cost-bounded OpenAI Responses API implementation of `ModelClient`."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        allow_api_call: bool = False,
        client: Any | None = None,
    ) -> None:
        selected_model = (
            model_id
            if model_id is not None
            else os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        )
        self._model_id = _text(selected_model, "model_id")
        if _is_sol_model(self._model_id):
            raise ModelClientConfigurationError(
                "Sol models are disabled for this cost-sensitive converter"
            )
        self._max_output_tokens = _positive_integer(
            max_output_tokens,
            "max_output_tokens",
        )
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number")
        effort = _text(reasoning_effort, "reasoning_effort").casefold()
        if effort not in _REASONING_EFFORTS:
            raise ValueError(f"unsupported reasoning_effort: {effort!r}")

        self._timeout_seconds = float(timeout_seconds)
        self._reasoning_effort = effort
        self._allow_api_call = allow_api_call
        self._client = client

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def reasoning_effort(self) -> str:
        return self._reasoning_effort

    @property
    def api_call_allowed(self) -> bool:
        return self._allow_api_call

    def _create_sdk_client(self) -> Any:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise ModelClientConfigurationError(
                "OPENAI_API_KEY is required for an OpenAI API call"
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as error:
            raise ModelClientConfigurationError(
                "OpenAI SDK is not installed; install project dependencies"
            ) from error
        return AsyncOpenAI(
            api_key=api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
        )

    def _conversion_from_response(
        self,
        request: ModelRequest,
        response: Any,
        latency_ms: int,
    ) -> ModelConversion:
        refusal = _response_refusal(response)
        if refusal is not None:
            raise ModelClientError(f"OpenAI model refused the request: {refusal}")

        status = _field(response, "status", None)
        if status != "completed":
            detail = _response_failure_detail(response)
            raise ModelClientError(
                f"OpenAI response status is {status!r}: {detail}"
            )

        output_text = _field(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ModelClientError("completed OpenAI response contains no output text")
        try:
            output = parse_model_output(output_text)
        except ModelOutputError as error:
            raise ModelClientError(str(error)) from error

        usage = _field(response, "usage", None)
        if usage is None:
            raise ModelClientError("completed OpenAI response contains no usage data")
        input_tokens = _field(usage, "input_tokens")
        output_tokens = _field(usage, "output_tokens")
        actual_model = _text(
            _field(response, "model", self._model_id),
            "response model",
        )
        if _is_sol_model(actual_model):
            raise ModelClientError(
                f"OpenAI returned disabled Sol model {actual_model!r}"
            )

        try:
            return ModelConversion(
                model_id=actual_model,
                response_id=_field(response, "id"),
                request_context_sha256=request.context_sha256,
                prompt_version=request.prompt_version,
                output=output,
                usage=ModelUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
                latency_ms=latency_ms,
            )
        except (TypeError, ValueError) as error:
            raise ModelClientError(
                f"invalid OpenAI response metadata: {error}"
            ) from error

    async def convert(self, request: ModelRequest) -> ModelConversion:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not self._allow_api_call:
            raise ModelClientConfigurationError(
                "OpenAI API calls are disabled; explicitly set allow_api_call=True"
            )

        owns_client = self._client is None
        client = self._client or self._create_sdk_client()
        input_messages = [message.to_dict() for message in request.messages]
        started = perf_counter()
        try:
            response = await asyncio.wait_for(
                client.responses.create(
                    model=self._model_id,
                    input=input_messages,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "click_conversion_v1",
                            "schema": model_output_response_schema(),
                            "strict": True,
                        }
                    },
                    reasoning={"effort": self._reasoning_effort},
                    max_output_tokens=self._max_output_tokens,
                    prompt_cache_key=request.context_sha256,
                    truncation="disabled",
                    store=False,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            raise ModelClientError(
                f"OpenAI request exceeded {self._timeout_seconds:g} seconds"
            ) from error
        except ModelClientError:
            raise
        except Exception as error:
            raise ModelClientError(
                f"OpenAI request failed with {type(error).__name__}: {error}"
            ) from error
        finally:
            if owns_client:
                try:
                    await client.close()
                except Exception:
                    pass
        latency_ms = round((perf_counter() - started) * 1000)
        return self._conversion_from_response(request, response, latency_ms)
