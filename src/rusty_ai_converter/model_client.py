"""Provider-neutral conversion client interface and offline fake."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, ClassVar, Mapping, Protocol, runtime_checkable

from rusty_ai_converter.context import ModelRequest
from rusty_ai_converter.model_output import ModelOutput, parse_model_output


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ModelClientError(Exception):
    """A model backend could not produce a completed conversion."""


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
