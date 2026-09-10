"""Versioned C parsing records used by the conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, ClassVar

from rusty_ai_converter.models import SourceBundle


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PARSED_PURPOSES = frozenset(
    {"driver_header", "driver_source", "example_source"}
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


def _string_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One-based line/column range and zero-based byte range in a source file."""

    file_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_byte: int
    end_byte: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_path", _text(self.file_path, "file_path"))
        for field_name in (
            "start_line",
            "start_column",
            "end_line",
            "end_column",
            "start_byte",
            "end_byte",
        ):
            value = getattr(self, field_name)
            minimum = 0 if field_name.endswith("byte") else 1
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field_name} must be an integer")
            if value < minimum:
                raise ValueError(f"{field_name} must be at least {minimum}")
        if self.end_byte < self.start_byte:
            raise ValueError("end_byte must not precede start_byte")
        if (self.end_line, self.end_column) < (
            self.start_line,
            self.start_column,
        ):
            raise ValueError("source span end must not precede its start")

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
        }


@dataclass(frozen=True, slots=True)
class CInclude:
    path: str
    is_system: bool
    directive: str
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "path"))
        object.__setattr__(self, "directive", _text(self.directive, "directive"))
        if not isinstance(self.is_system, bool):
            raise TypeError("is_system must be a boolean")
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "is_system": self.is_system,
            "directive": self.directive,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CMacro:
    name: str
    parameters: tuple[str, ...]
    replacement: str
    definition: str
    documentation: str | None
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(
            self, "parameters", _string_tuple(self.parameters, "macro parameter")
        )
        if not isinstance(self.replacement, str):
            raise TypeError("replacement must be a string")
        object.__setattr__(self, "definition", _text(self.definition, "definition"))
        object.__setattr__(
            self,
            "documentation",
            _optional_text(self.documentation, "documentation"),
        )
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": list(self.parameters),
            "replacement": self.replacement,
            "definition": self.definition,
            "documentation": self.documentation,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CParameter:
    name: str | None
    declaration: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        object.__setattr__(
            self, "declaration", _text(self.declaration, "declaration")
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "declaration": self.declaration}


@dataclass(frozen=True, slots=True)
class CFunction:
    name: str
    return_type: str
    parameters: tuple[CParameter, ...]
    signature: str
    body: str | None
    storage: tuple[str, ...]
    calls: tuple[str, ...]
    documentation: str | None
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "return_type", _text(self.return_type, "return_type"))
        parameters = tuple(self.parameters)
        if any(not isinstance(item, CParameter) for item in parameters):
            raise TypeError("parameters must contain only CParameter instances")
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "signature", _text(self.signature, "signature"))
        if self.body is not None and not isinstance(self.body, str):
            raise TypeError("body must be a string or None")
        object.__setattr__(self, "storage", _string_tuple(self.storage, "storage"))
        object.__setattr__(self, "calls", _string_tuple(self.calls, "call"))
        object.__setattr__(
            self,
            "documentation",
            _optional_text(self.documentation, "documentation"),
        )
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    @property
    def is_definition(self) -> bool:
        return self.body is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "return_type": self.return_type,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "signature": self.signature,
            "body": self.body,
            "storage": list(self.storage),
            "calls": list(self.calls),
            "documentation": self.documentation,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CField:
    name: str | None
    declaration: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        object.__setattr__(
            self, "declaration", _text(self.declaration, "declaration")
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "declaration": self.declaration}


@dataclass(frozen=True, slots=True)
class CComposite:
    kind: str
    name: str | None
    fields: tuple[CField, ...]
    definition: str
    documentation: str | None
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        kind = _text(self.kind, "kind")
        if kind not in {"struct", "union"}:
            raise ValueError("composite kind must be 'struct' or 'union'")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        fields = tuple(self.fields)
        if any(not isinstance(item, CField) for item in fields):
            raise TypeError("fields must contain only CField instances")
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "definition", _text(self.definition, "definition"))
        object.__setattr__(
            self,
            "documentation",
            _optional_text(self.documentation, "documentation"),
        )
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "fields": [field.to_dict() for field in self.fields],
            "definition": self.definition,
            "documentation": self.documentation,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CEnumMember:
    name: str
    value: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "value", _optional_text(self.value, "value"))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True, slots=True)
class CEnum:
    name: str | None
    members: tuple[CEnumMember, ...]
    definition: str
    documentation: str | None
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _optional_text(self.name, "name"))
        members = tuple(self.members)
        if any(not isinstance(item, CEnumMember) for item in members):
            raise TypeError("members must contain only CEnumMember instances")
        object.__setattr__(self, "members", members)
        object.__setattr__(self, "definition", _text(self.definition, "definition"))
        object.__setattr__(
            self,
            "documentation",
            _optional_text(self.documentation, "documentation"),
        )
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "members": [member.to_dict() for member in self.members],
            "definition": self.definition,
            "documentation": self.documentation,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CTypedef:
    name: str
    target: str
    declaration: str
    documentation: str | None
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "target", _text(self.target, "target"))
        object.__setattr__(
            self, "declaration", _text(self.declaration, "declaration")
        )
        object.__setattr__(
            self,
            "documentation",
            _optional_text(self.documentation, "documentation"),
        )
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "declaration": self.declaration,
            "documentation": self.documentation,
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CVariable:
    name: str
    type_text: str
    declaration: str
    initializer: str | None
    storage: tuple[str, ...]
    conditions: tuple[str, ...]
    span: SourceSpan

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "type_text", _text(self.type_text, "type_text"))
        object.__setattr__(
            self, "declaration", _text(self.declaration, "declaration")
        )
        object.__setattr__(
            self, "initializer", _optional_text(self.initializer, "initializer")
        )
        object.__setattr__(self, "storage", _string_tuple(self.storage, "storage"))
        object.__setattr__(
            self, "conditions", _string_tuple(self.conditions, "condition")
        )
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_text": self.type_text,
            "declaration": self.declaration,
            "initializer": self.initializer,
            "storage": list(self.storage),
            "conditions": list(self.conditions),
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CPreprocessorCondition:
    kind: str
    expression: str
    directive: str
    span: SourceSpan

    def __post_init__(self) -> None:
        kind = _text(self.kind, "kind")
        if kind not in {"if", "ifdef", "ifndef", "elif", "else"}:
            raise ValueError("unsupported preprocessor condition kind")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "expression", _text(self.expression, "expression"))
        object.__setattr__(self, "directive", _text(self.directive, "directive"))
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expression": self.expression,
            "directive": self.directive,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ParseDiagnostic:
    severity: str
    message: str
    span: SourceSpan

    def __post_init__(self) -> None:
        severity = _text(self.severity, "severity")
        if severity not in {"warning", "error"}:
            raise ValueError("diagnostic severity must be warning or error")
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "message", _text(self.message, "message"))
        if not isinstance(self.span, SourceSpan):
            raise TypeError("span must be a SourceSpan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ParsedCFile:
    path: str
    purpose: str
    sha256: str
    parser: str
    includes: tuple[CInclude, ...] = field(default_factory=tuple)
    macros: tuple[CMacro, ...] = field(default_factory=tuple)
    functions: tuple[CFunction, ...] = field(default_factory=tuple)
    composites: tuple[CComposite, ...] = field(default_factory=tuple)
    enums: tuple[CEnum, ...] = field(default_factory=tuple)
    typedefs: tuple[CTypedef, ...] = field(default_factory=tuple)
    global_variables: tuple[CVariable, ...] = field(default_factory=tuple)
    preprocessor_conditions: tuple[CPreprocessorCondition, ...] = field(
        default_factory=tuple
    )
    diagnostics: tuple[ParseDiagnostic, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "path"))
        purpose = _text(self.purpose, "purpose")
        if purpose not in _PARSED_PURPOSES:
            raise ValueError(f"unsupported parsed C file purpose: {purpose!r}")
        digest = _text(self.sha256, "sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "parser", _text(self.parser, "parser"))
        typed_fields = (
            ("includes", CInclude),
            ("macros", CMacro),
            ("functions", CFunction),
            ("composites", CComposite),
            ("enums", CEnum),
            ("typedefs", CTypedef),
            ("global_variables", CVariable),
            ("preprocessor_conditions", CPreprocessorCondition),
            ("diagnostics", ParseDiagnostic),
        )
        for field_name, expected_type in typed_fields:
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, expected_type) for value in values):
                raise TypeError(
                    f"{field_name} must contain only {expected_type.__name__} instances"
                )
            object.__setattr__(self, field_name, values)
            if any(
                hasattr(value, "span") and value.span.file_path != self.path
                for value in values
            ):
                raise ValueError(f"{field_name} contains a span for another file")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "purpose": self.purpose,
            "sha256": self.sha256,
            "parser": self.parser,
            "includes": [item.to_dict() for item in self.includes],
            "macros": [item.to_dict() for item in self.macros],
            "functions": [item.to_dict() for item in self.functions],
            "composites": [item.to_dict() for item in self.composites],
            "enums": [item.to_dict() for item in self.enums],
            "typedefs": [item.to_dict() for item in self.typedefs],
            "global_variables": [item.to_dict() for item in self.global_variables],
            "preprocessor_conditions": [
                item.to_dict() for item in self.preprocessor_conditions
            ],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class ClickPackageIR:
    """First normalized C representation for one selected Click package."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    source_bundle: SourceBundle
    files: tuple[ParsedCFile, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.source_bundle, SourceBundle):
            raise TypeError("source_bundle must be a SourceBundle")
        files = tuple(self.files)
        if any(not isinstance(file, ParsedCFile) for file in files):
            raise TypeError("files must contain only ParsedCFile instances")
        paths = [file.path for file in files]
        if paths != sorted(paths, key=str.casefold) or len(paths) != len(set(paths)):
            raise ValueError("parsed files must have unique deterministic path ordering")
        selected_by_path = {
            file.path: file for file in self.source_bundle.text_files
        }
        for parsed_file in files:
            selected = selected_by_path.get(parsed_file.path)
            if selected is None or selected.sha256 != parsed_file.sha256:
                raise ValueError("parsed file does not match the source bundle")
        diagnostics = tuple(_text(item, "diagnostic") for item in self.diagnostics)
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(f"unsupported ClickPackageIR version: {self.schema_version!r}")

        object.__setattr__(self, "files", files)
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def function_definitions(self) -> tuple[CFunction, ...]:
        return tuple(
            function
            for parsed_file in self.files
            for function in parsed_file.functions
            if function.is_definition
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_bundle": self.source_bundle.to_dict(),
            "files": [file.to_dict() for file in self.files],
            "diagnostics": list(self.diagnostics),
        }
