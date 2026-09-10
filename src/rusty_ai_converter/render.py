"""Validate and atomically render one four-file Rust Click package."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, ClassVar, Mapping

from rusty_ai_converter.context import ModelRequest
from rusty_ai_converter.model_client import ModelConversion
from rusty_ai_converter.model_output import CoverageEntry
from rusty_ai_converter.sdk_mapping import TranslationPlan


REQUIRED_ARTIFACT_PATHS = (
    "Cargo.toml",
    "library.rs",
    "main.rs",
    "mikrobus.rs",
)
MIKROBUS_1_SIGNALS = (
    "AN",
    "RST",
    "CS",
    "SCK",
    "MISO",
    "MOSI",
    "PWM",
    "INT",
    "RX",
    "TX",
    "SCL",
    "SDA",
)
DEFAULT_MAX_RENDER_BYTES = 4 * 1024 * 1024
_OUTPUT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactRenderError(Exception):
    """A conversion cannot be safely rendered as a package."""


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class RenderedFile:
    """Checksum and size of one materialized artifact."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        path = _text(self.path, "path")
        if path not in REQUIRED_ARTIFACT_PATHS:
            raise ValueError(f"unsupported rendered file path: {path!r}")
        digest = _text(self.sha256, "sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")

        object.__setattr__(self, "path", path)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RenderedPackage:
    """Deterministic artifact report without duplicating file contents."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    catalog_id: str
    package_name: str
    output_name: str
    output_directory: Path
    source_archive_sha256: str
    request_context_sha256: str
    prompt_version: str
    model_id: str
    response_id: str
    required_rust_crates: tuple[str, ...]
    files: tuple[RenderedFile, ...]
    api_coverage_ledger: tuple[CoverageEntry, ...]
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reused_existing: bool = False
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        files = tuple(self.files)
        if any(not isinstance(item, RenderedFile) for item in files):
            raise TypeError("files must contain only RenderedFile values")
        if tuple(item.path for item in files) != REQUIRED_ARTIFACT_PATHS:
            raise ValueError("files must contain the four required artifacts in order")
        coverage = tuple(self.api_coverage_ledger)
        if not coverage or any(
            not isinstance(item, CoverageEntry) for item in coverage
        ):
            raise ValueError("api_coverage_ledger must contain CoverageEntry values")
        if not isinstance(self.reused_existing, bool):
            raise TypeError("reused_existing must be a boolean")
        for digest_name in ("source_archive_sha256", "request_context_sha256"):
            digest = _text(getattr(self, digest_name), digest_name).lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"{digest_name} must be a SHA-256 digest")
            object.__setattr__(self, digest_name, digest)
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported rendered package version: {self.schema_version!r}"
            )

        object.__setattr__(self, "catalog_id", _text(self.catalog_id, "catalog_id"))
        object.__setattr__(
            self,
            "package_name",
            _text(self.package_name, "package_name"),
        )
        output_name = _text(self.output_name, "output_name")
        if not _OUTPUT_NAME_RE.fullmatch(output_name):
            raise ValueError("output_name must be a safe lowercase package name")
        object.__setattr__(self, "output_name", output_name)
        object.__setattr__(self, "output_directory", Path(self.output_directory))
        object.__setattr__(
            self,
            "prompt_version",
            _text(self.prompt_version, "prompt_version"),
        )
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id"))
        object.__setattr__(self, "response_id", _text(self.response_id, "response_id"))
        object.__setattr__(
            self,
            "required_rust_crates",
            tuple(self.required_rust_crates),
        )
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "api_coverage_ledger", coverage)
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    @property
    def total_size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalog_id": self.catalog_id,
            "package_name": self.package_name,
            "output_name": self.output_name,
            "output_directory": str(self.output_directory),
            "source_archive_sha256": self.source_archive_sha256,
            "request_context_sha256": self.request_context_sha256,
            "prompt_version": self.prompt_version,
            "model_id": self.model_id,
            "response_id": self.response_id,
            "required_rust_crates": list(self.required_rust_crates),
            "files": [item.to_dict() for item in self.files],
            "total_size_bytes": self.total_size_bytes,
            "api_coverage_ledger": [
                item.to_dict() for item in self.api_coverage_ledger
            ],
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
            "reused_existing": self.reused_existing,
        }


def render_cargo_toml() -> str:
    """Return the project-root marker expected by MikroBUS Rust Tools."""
    return """# Project-root marker for MikroBUS Rust Tools.
# Build, flash, and debug use the reusable SDK setup applied by the extension.
[workspace]
resolver = "3"

[workspace.metadata.mikrobus-rust]
entry = "main.rs"
"""


def render_default_mikrobus() -> str:
    """Return a replaceable mikroBUS 1 mapping with no guessed board pins."""
    declarations = "\n".join(
        f"pub const MIKROBUS_1_{signal}: pin_name_t = 0xFF;"
        for signal in MIKROBUS_1_SIGNALS
    )
    return f"""//! Default unconfigured MikroBUS mapping.
//! Replace this file with the mapping generated by MikroBUS Rust Tools.

#![allow(dead_code)]

use drv_name::*;

// mikroBUS 1: deliberately unconfigured
{declarations}
"""


def required_coverage_symbols(plan: TranslationPlan) -> tuple[tuple[str, str], ...]:
    """Return semantic public symbols that require one coverage decision."""
    if not isinstance(plan, TranslationPlan):
        raise TypeError("plan must be a TranslationPlan")
    symbols: set[tuple[str, str]] = set()
    for parsed_file in plan.package_ir.files:
        if parsed_file.purpose != "driver_header":
            continue
        composite_names = {item.name for item in parsed_file.composites if item.name}
        enum_names = {item.name for item in parsed_file.enums if item.name}
        symbols.update(("function", item.name) for item in parsed_file.functions)
        symbols.update(
            ("macro", item.name)
            for item in parsed_file.macros
            if item.replacement.strip()
        )
        symbols.update(
            (item.kind, item.name)
            for item in parsed_file.composites
            if item.name
        )
        symbols.update(
            ("enum", item.name) for item in parsed_file.enums if item.name
        )
        symbols.update(
            ("typedef", item.name)
            for item in parsed_file.typedefs
            if item.name not in composite_names | enum_names
        )
        symbols.update(
            ("global", item.name)
            for item in parsed_file.global_variables
            if "static" not in item.storage
        )
    symbols.update(
        ("resource", item.path)
        for item in plan.package_ir.source_bundle.resource_files
    )
    return tuple(sorted(symbols, key=lambda item: (item[0], item[1].casefold())))


def _validate_inputs(
    plan: TranslationPlan,
    request: ModelRequest,
    conversion: ModelConversion,
) -> None:
    if not isinstance(plan, TranslationPlan):
        raise TypeError("plan must be a TranslationPlan")
    if not isinstance(request, ModelRequest):
        raise TypeError("request must be a ModelRequest")
    if not isinstance(conversion, ModelConversion):
        raise TypeError("conversion must be a ModelConversion")
    if plan.unresolved:
        unresolved = ", ".join(item.call for item in plan.unresolved)
        raise ArtifactRenderError(f"translation plan remains unresolved: {unresolved}")
    expected_sources = tuple(item.path for item in plan.package_ir.files)
    if request.included_source_files != expected_sources:
        raise ArtifactRenderError("model request does not belong to translation plan")
    if conversion.request_context_sha256 != request.context_sha256:
        raise ArtifactRenderError("model response context hash does not match request")
    if conversion.prompt_version != request.prompt_version:
        raise ArtifactRenderError(
            "model response prompt version does not match request"
        )
    if conversion.output.required_rust_crates != plan.required_rust_crates:
        raise ArtifactRenderError(
            "model dependency list does not match deterministic translation plan"
        )
    if conversion.output.unsupported_items or any(
        item.status == "unsupported"
        for item in conversion.output.api_coverage_ledger
    ):
        raise ArtifactRenderError("model output contains unsupported functionality")

    expected_coverage = set(required_coverage_symbols(plan))
    actual_coverage = {
        (item.source_kind, item.source_symbol)
        for item in conversion.output.api_coverage_ledger
    }
    missing = sorted(expected_coverage - actual_coverage)
    if missing:
        summary = ", ".join(f"{kind}:{name}" for kind, name in missing[:10])
        if len(missing) > 10:
            summary += f", and {len(missing) - 10} more"
        raise ArtifactRenderError(f"coverage ledger is missing {summary}")


def _artifact_bytes(conversion: ModelConversion) -> dict[str, bytes]:
    contents = {
        "Cargo.toml": render_cargo_toml(),
        "library.rs": conversion.output.library_rs,
        "main.rs": conversion.output.main_rs,
        "mikrobus.rs": render_default_mikrobus(),
    }
    for path, content in contents.items():
        if "\x00" in content:
            raise ArtifactRenderError(f"{path} contains a null byte")
    return {path: content.encode("utf-8") for path, content in contents.items()}


def _rendered_files(artifacts: Mapping[str, bytes]) -> tuple[RenderedFile, ...]:
    return tuple(
        RenderedFile(
            path=path,
            sha256=sha256(artifacts[path]).hexdigest(),
            size_bytes=len(artifacts[path]),
        )
        for path in REQUIRED_ARTIFACT_PATHS
    )


def _matches_existing(target: Path, artifacts: Mapping[str, bytes]) -> bool:
    if target.is_symlink() or not target.is_dir():
        raise ArtifactRenderError(f"output target is not a regular directory: {target}")
    entries = tuple(sorted(target.iterdir(), key=lambda item: item.name.casefold()))
    if {item.name for item in entries} != set(REQUIRED_ARTIFACT_PATHS):
        raise ArtifactRenderError(
            f"output target already exists with different contents: {target}"
        )
    for entry in entries:
        expected = artifacts[entry.name]
        if entry.is_symlink() or not entry.is_file():
            raise ArtifactRenderError(f"output target contains unsafe entry: {entry}")
        if entry.stat().st_size != len(expected) or entry.read_bytes() != expected:
            raise ArtifactRenderError(
                f"output target already contains a different {entry.name}"
            )
    return True


def render_package(
    plan: TranslationPlan,
    request: ModelRequest,
    conversion: ModelConversion,
    output_root: str | Path,
    *,
    max_total_bytes: int = DEFAULT_MAX_RENDER_BYTES,
) -> RenderedPackage:
    """Validate and atomically materialize exactly four package files."""
    _validate_inputs(plan, request, conversion)
    if not isinstance(max_total_bytes, int) or isinstance(max_total_bytes, bool):
        raise TypeError("max_total_bytes must be an integer")
    if max_total_bytes < 1:
        raise ValueError("max_total_bytes must be positive")

    package = plan.package_ir.source_bundle.inventory.archive.package
    output_name = package.normalized_name
    if not _OUTPUT_NAME_RE.fullmatch(output_name):
        raise ArtifactRenderError(f"unsafe normalized package name: {output_name!r}")
    root = Path(output_root)
    target = root / output_name
    artifacts = _artifact_bytes(conversion)
    total_size = sum(len(content) for content in artifacts.values())
    if total_size > max_total_bytes:
        raise ArtifactRenderError(
            f"rendered files total {total_size} bytes; limit is {max_total_bytes}"
        )

    reused_existing = False
    if target.exists() or target.is_symlink():
        reused_existing = _matches_existing(target, artifacts)
    else:
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output_name}-", dir=root))
        try:
            for path in REQUIRED_ARTIFACT_PATHS:
                (staging / path).write_bytes(artifacts[path])
            try:
                staging.rename(target)
            except FileExistsError as error:
                raise ArtifactRenderError(
                    f"output target appeared during rendering: {target}"
                ) from error
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    source_bundle = plan.package_ir.source_bundle
    return RenderedPackage(
        catalog_id=package.catalog_id,
        package_name=package.name,
        output_name=output_name,
        output_directory=target,
        source_archive_sha256=source_bundle.inventory.archive.sha256,
        request_context_sha256=request.context_sha256,
        prompt_version=request.prompt_version,
        model_id=conversion.model_id,
        response_id=conversion.response_id,
        required_rust_crates=plan.required_rust_crates,
        files=_rendered_files(artifacts),
        api_coverage_ledger=conversion.output.api_coverage_ledger,
        assumptions=conversion.output.assumptions,
        warnings=conversion.output.warnings,
        reused_existing=reused_existing,
    )
