"""Strict records and parsing for AI-produced conversion data."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, ClassVar, Mapping


COVERAGE_KINDS = frozenset(
    {"function", "struct", "union", "enum", "typedef", "macro", "global", "resource"}
)
COVERAGE_STATUSES = frozenset(
    {
        "translated",
        "replaced_by_rust_construct",
        "private_internal",
        "not_applicable",
        "unsupported",
    }
)
_MODEL_OUTPUT_KEYS = frozenset(
    {
        "schema_version",
        "library_rs",
        "main_rs",
        "required_rust_crates",
        "api_coverage_ledger",
        "assumptions",
        "warnings",
        "unsupported_items",
    }
)
_COVERAGE_KEYS = frozenset(
    {"source_symbol", "source_kind", "status", "rust_symbol", "rationale"}
)


class ModelOutputError(Exception):
    """Model text or structured data does not satisfy the output contract."""


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _code(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if value.lstrip().startswith("```") or value.rstrip().endswith("```"):
        raise ValueError(
            f"{field_name} must contain source code without Markdown fences"
        )
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _string_array(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be an array")
    values = tuple(_text(item, f"{field_name} item") for item in value)
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _string_sequence(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise TypeError(f"{field_name} must be a list or tuple")
    values = tuple(_text(item, f"{field_name} item") for item in value)
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise ValueError(f"{name} fields are invalid: {'; '.join(details)}")


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """One explicit decision for a public or meaningful C input symbol."""

    source_symbol: str
    source_kind: str
    status: str
    rust_symbol: str | None
    rationale: str

    def __post_init__(self) -> None:
        kind = _text(self.source_kind, "source_kind")
        status = _text(self.status, "status")
        rust_symbol = _optional_text(self.rust_symbol, "rust_symbol")
        if kind not in COVERAGE_KINDS:
            raise ValueError(f"unsupported coverage kind: {kind!r}")
        if status not in COVERAGE_STATUSES:
            raise ValueError(f"unsupported coverage status: {status!r}")
        if (
            status in {"translated", "replaced_by_rust_construct"}
            and rust_symbol is None
        ):
            raise ValueError(f"coverage status {status!r} requires rust_symbol")

        object.__setattr__(
            self,
            "source_symbol",
            _text(self.source_symbol, "source_symbol"),
        )
        object.__setattr__(self, "source_kind", kind)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "rust_symbol", rust_symbol)
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))

    @classmethod
    def from_dict(cls, value: Any) -> CoverageEntry:
        if not isinstance(value, Mapping):
            raise TypeError("coverage entry must be an object")
        _exact_keys(value, _COVERAGE_KEYS, "coverage entry")
        return cls(
            source_symbol=value["source_symbol"],
            source_kind=value["source_kind"],
            status=value["status"],
            rust_symbol=value["rust_symbol"],
            rationale=value["rationale"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_symbol": self.source_symbol,
            "source_kind": self.source_kind,
            "status": self.status,
            "rust_symbol": self.rust_symbol,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ModelOutput:
    """Strict structured output returned by a conversion model."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    library_rs: str
    main_rs: str
    required_rust_crates: tuple[str, ...]
    api_coverage_ledger: tuple[CoverageEntry, ...]
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    unsupported_items: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        library_rs = _code(self.library_rs, "library_rs")
        main_rs = _code(self.main_rs, "main_rs")
        for required_fragment in (
            "#![no_std]",
            "#![no_main]",
            "mod library;",
            "mod mikrobus;",
        ):
            if required_fragment not in main_rs:
                raise ValueError(f"main_rs must contain {required_fragment!r}")

        crates = tuple(
            sorted(
                _string_sequence(
                    self.required_rust_crates,
                    "required_rust_crates",
                ),
                key=str.casefold,
            )
        )
        coverage = tuple(self.api_coverage_ledger)
        if not coverage or any(
            not isinstance(item, CoverageEntry) for item in coverage
        ):
            raise ValueError("api_coverage_ledger must contain CoverageEntry values")
        coverage_keys = [(item.source_kind, item.source_symbol) for item in coverage]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError("api_coverage_ledger contains duplicate source symbols")
        coverage = tuple(
            sorted(
                coverage,
                key=lambda item: (item.source_kind, item.source_symbol.casefold()),
            )
        )
        assumptions = _string_sequence(self.assumptions, "assumptions")
        warnings = _string_sequence(self.warnings, "warnings")
        unsupported_items = _string_sequence(
            self.unsupported_items,
            "unsupported_items",
        )
        if (
            any(item.status == "unsupported" for item in coverage)
            and not unsupported_items
        ):
            raise ValueError(
                "unsupported coverage requires an unsupported_items explanation"
            )
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported model output version: {self.schema_version!r}"
            )

        object.__setattr__(self, "library_rs", library_rs)
        object.__setattr__(self, "main_rs", main_rs)
        object.__setattr__(self, "required_rust_crates", crates)
        object.__setattr__(self, "api_coverage_ledger", coverage)
        object.__setattr__(self, "assumptions", assumptions)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "unsupported_items", unsupported_items)

    @classmethod
    def from_dict(cls, value: Any) -> ModelOutput:
        if not isinstance(value, Mapping):
            raise TypeError("model output must be an object")
        _exact_keys(value, _MODEL_OUTPUT_KEYS, "model output")
        raw_coverage = value["api_coverage_ledger"]
        if not isinstance(raw_coverage, list):
            raise TypeError("api_coverage_ledger must be an array")
        return cls(
            schema_version=value["schema_version"],
            library_rs=value["library_rs"],
            main_rs=value["main_rs"],
            required_rust_crates=_string_array(
                value["required_rust_crates"],
                "required_rust_crates",
            ),
            api_coverage_ledger=tuple(
                CoverageEntry.from_dict(item) for item in raw_coverage
            ),
            assumptions=_string_array(value["assumptions"], "assumptions"),
            warnings=_string_array(value["warnings"], "warnings"),
            unsupported_items=_string_array(
                value["unsupported_items"],
                "unsupported_items",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "library_rs": self.library_rs,
            "main_rs": self.main_rs,
            "required_rust_crates": list(self.required_rust_crates),
            "api_coverage_ledger": [
                item.to_dict() for item in self.api_coverage_ledger
            ],
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
            "unsupported_items": list(self.unsupported_items),
        }


def parse_model_output(value: str | Mapping[str, Any]) -> ModelOutput:
    """Parse strict JSON or an already-decoded object without repair heuristics."""
    try:
        decoded: Any = json.loads(value) if isinstance(value, str) else value
        return ModelOutput.from_dict(decoded)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
        raise ModelOutputError(f"invalid model output: {error}") from error
