"""Versioned SDK indexes and per-package dependency resolutions."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, ClassVar

from rusty_ai_converter.parsing import ClickPackageIR


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAPPING_STATUSES = frozenset({"direct", "adapted", "unsupported"})
_RESOLUTION_STATUSES = frozenset(
    {
        "local_function",
        "local_macro",
        "sdk_direct",
        "sdk_adapter",
        "platform_adapter",
        "standard_adapter",
        "logging_adapter",
        "unresolved",
    }
)


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _optional_text(value: str | None, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name)


def _strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class SdkFunction:
    """One callable declaration found in a C or Rust SDK source file."""

    language: str
    name: str
    module: str
    signature: str
    source_path: str
    source_sha256: str
    start_line: int
    end_line: int
    body: str | None = None
    crate_name: str | None = None

    def __post_init__(self) -> None:
        language = _text(self.language, "language")
        if language not in {"c", "rust"}:
            raise ValueError("language must be 'c' or 'rust'")
        digest = _text(self.source_sha256, "source_sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.start_line, int) or self.start_line < 1:
            raise ValueError("start_line must be a positive integer")
        if not isinstance(self.end_line, int) or self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.body is not None and not isinstance(self.body, str):
            raise TypeError("body must be a string or None")
        if language == "rust" and self.crate_name is None:
            raise ValueError("Rust SDK functions must identify their crate")
        if language == "c" and self.crate_name is not None:
            raise ValueError("C SDK functions must not identify a Rust crate")

        object.__setattr__(self, "language", language)
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "module", _text(self.module, "module"))
        object.__setattr__(self, "signature", _text(self.signature, "signature"))
        object.__setattr__(self, "source_path", _text(self.source_path, "source_path"))
        object.__setattr__(self, "source_sha256", digest)
        object.__setattr__(
            self, "crate_name", _optional_text(self.crate_name, "crate_name")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "name": self.name,
            "module": self.module,
            "signature": self.signature,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "body": self.body,
            "crate_name": self.crate_name,
        }


@dataclass(frozen=True, slots=True)
class SdkFunctionMapping:
    """Verified relation between one C SDK function and the Rust SDK."""

    c_function: SdkFunction
    status: str
    rust_function: SdkFunction | None = None
    rust_crate: str | None = None
    rust_expression: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.c_function, SdkFunction):
            raise TypeError("c_function must be an SdkFunction")
        if self.c_function.language != "c":
            raise ValueError("c_function must come from the C SDK")
        status = _text(self.status, "status")
        if status not in _MAPPING_STATUSES:
            raise ValueError(f"unsupported SDK mapping status: {status!r}")
        if self.rust_function is not None:
            if not isinstance(self.rust_function, SdkFunction):
                raise TypeError("rust_function must be an SdkFunction or None")
            if self.rust_function.language != "rust":
                raise ValueError("rust_function must come from the Rust SDK")
        expression = _optional_text(self.rust_expression, "rust_expression")
        rust_crate = _optional_text(self.rust_crate, "rust_crate")
        if status == "direct" and self.rust_function is None:
            raise ValueError("direct mappings require a Rust function")
        if status == "direct" and rust_crate != self.rust_function.crate_name:
            raise ValueError("direct mapping crate must match its Rust function")
        if status == "adapted" and (expression is None or rust_crate is None):
            raise ValueError("adapted mappings require a Rust crate and expression")
        if status == "unsupported" and (
            self.rust_function is not None or expression is not None or rust_crate is not None
        ):
            raise ValueError("unsupported mappings cannot claim a Rust target")

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "rust_crate", rust_crate)
        object.__setattr__(self, "rust_expression", expression)
        object.__setattr__(self, "notes", _strings(self.notes, "note"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "c_function": self.c_function.to_dict(),
            "status": self.status,
            "rust_function": (
                self.rust_function.to_dict() if self.rust_function is not None else None
            ),
            "rust_crate": self.rust_crate,
            "rust_expression": self.rust_expression,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class SdkMappingDatabase:
    """Deterministically generated inventory of the reference SDK pair."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    c_functions: tuple[SdkFunction, ...]
    rust_functions: tuple[SdkFunction, ...]
    mappings: tuple[SdkFunctionMapping, ...]
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        c_functions = tuple(self.c_functions)
        rust_functions = tuple(self.rust_functions)
        mappings = tuple(self.mappings)
        if any(item.language != "c" for item in c_functions):
            raise ValueError("c_functions may contain only C SDK functions")
        if any(item.language != "rust" for item in rust_functions):
            raise ValueError("rust_functions may contain only Rust SDK functions")
        if any(not isinstance(item, SdkFunctionMapping) for item in mappings):
            raise TypeError("mappings must contain only SdkFunctionMapping instances")
        c_names = [item.name for item in c_functions]
        rust_keys = [(item.crate_name, item.name) for item in rust_functions]
        mapping_names = [item.c_function.name for item in mappings]
        if c_names != sorted(c_names, key=str.casefold) or len(c_names) != len(set(c_names)):
            raise ValueError("C SDK functions must have unique deterministic ordering")
        if rust_keys != sorted(rust_keys, key=lambda item: (item[0] or "", item[1])):
            raise ValueError("Rust SDK functions must have deterministic ordering")
        if len(rust_keys) != len(set(rust_keys)):
            raise ValueError("Rust SDK functions must be unique within each crate")
        if mapping_names != c_names:
            raise ValueError("mappings must cover every C function in the same order")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported SDK mapping version: {self.schema_version!r}")

        object.__setattr__(self, "c_functions", c_functions)
        object.__setattr__(self, "rust_functions", rust_functions)
        object.__setattr__(self, "mappings", mappings)
        object.__setattr__(self, "diagnostics", _strings(self.diagnostics, "diagnostic"))

    def mapping_for(self, name: str) -> SdkFunctionMapping | None:
        return next(
            (mapping for mapping in self.mappings if mapping.c_function.name == name),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "c_functions": [item.to_dict() for item in self.c_functions],
            "rust_functions": [item.to_dict() for item in self.rust_functions],
            "mappings": [item.to_dict() for item in self.mappings],
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class CallResolution:
    """Resolution of one distinct call expression used by a Click package."""

    call: str
    status: str
    called_by: tuple[str, ...]
    rust_crate: str | None
    rust_target: str | None
    guidance: str
    evidence_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        status = _text(self.status, "status")
        if status not in _RESOLUTION_STATUSES:
            raise ValueError(f"unsupported call resolution status: {status!r}")
        callers = _strings(self.called_by, "called_by")
        if not callers:
            raise ValueError("called_by must identify at least one function")
        object.__setattr__(self, "call", _text(self.call, "call"))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "called_by", callers)
        object.__setattr__(self, "rust_crate", _optional_text(self.rust_crate, "rust_crate"))
        object.__setattr__(self, "rust_target", _optional_text(self.rust_target, "rust_target"))
        object.__setattr__(self, "guidance", _text(self.guidance, "guidance"))
        object.__setattr__(
            self, "evidence_paths", _strings(self.evidence_paths, "evidence_path")
        )

    @property
    def is_resolved(self) -> bool:
        return self.status != "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "call": self.call,
            "status": self.status,
            "called_by": list(self.called_by),
            "rust_crate": self.rust_crate,
            "rust_target": self.rust_target,
            "guidance": self.guidance,
            "evidence_paths": list(self.evidence_paths),
        }


@dataclass(frozen=True, slots=True)
class TranslationPlan:
    """Package IR plus deterministic dependency decisions for model context."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    package_ir: ClickPackageIR
    sdk_mapping_version: str
    resolutions: tuple[CallResolution, ...]
    required_rust_crates: tuple[str, ...]
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.package_ir, ClickPackageIR):
            raise TypeError("package_ir must be a ClickPackageIR")
        resolutions = tuple(self.resolutions)
        if any(not isinstance(item, CallResolution) for item in resolutions):
            raise TypeError("resolutions must contain only CallResolution instances")
        calls = [item.call for item in resolutions]
        if calls != sorted(calls, key=str.casefold) or len(calls) != len(set(calls)):
            raise ValueError("call resolutions must have unique deterministic ordering")
        crates = _strings(self.required_rust_crates, "required_rust_crate")
        if crates != tuple(sorted(crates, key=str.casefold)):
            raise ValueError("required Rust crates must have deterministic ordering")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported translation plan version: {self.schema_version!r}")

        object.__setattr__(
            self, "sdk_mapping_version", _text(self.sdk_mapping_version, "sdk_mapping_version")
        )
        object.__setattr__(self, "resolutions", resolutions)
        object.__setattr__(self, "required_rust_crates", crates)
        object.__setattr__(self, "diagnostics", _strings(self.diagnostics, "diagnostic"))

    @property
    def unresolved(self) -> tuple[CallResolution, ...]:
        return tuple(item for item in self.resolutions if not item.is_resolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "package_ir": self.package_ir.to_dict(),
            "sdk_mapping_version": self.sdk_mapping_version,
            "resolutions": [item.to_dict() for item in self.resolutions],
            "required_rust_crates": list(self.required_rust_crates),
            "diagnostics": list(self.diagnostics),
        }
