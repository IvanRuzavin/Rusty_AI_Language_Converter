"""Coordinate the complete Click conversion pipeline through local validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from rusty_ai_converter.archive import inspect_archive
from rusty_ai_converter.catalog import load_catalog
from rusty_ai_converter.context import DEFAULT_MAX_REQUEST_BYTES, ModelRequest
from rusty_ai_converter.context import build_model_request
from rusty_ai_converter.download import (
    DEFAULT_MAX_ARCHIVE_BYTES,
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    download_package,
    load_cached_archive,
)
from rusty_ai_converter.model_client import ModelClient, ModelConversion
from rusty_ai_converter.models import CachedArchive, PackageCatalog, PackageRecord
from rusty_ai_converter.parsing import parse_source_bundle
from rusty_ai_converter.render import (
    DEFAULT_MAX_RENDER_BYTES,
    RenderedPackage,
    render_package,
)
from rusty_ai_converter.sdk_mapping import (
    TranslationPlan,
    build_sdk_mapping_database,
    build_translation_plan,
)
from rusty_ai_converter.source import build_source_bundle
from rusty_ai_converter.validate import (
    DEFAULT_MAX_DIAGNOSTIC_BYTES,
    DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    ValidationReport,
    validate_rendered_package,
)


PIPELINE_STAGES = (
    "catalog",
    "archive",
    "source",
    "parser",
    "sdk_mapping",
    "context",
    "model",
    "render",
    "validate",
)
ProgressCallback = Callable[[str, str], None]
ModelClientFactory = Callable[[TranslationPlan], ModelClient]


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _positive_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be positive")
    return value


def _positive_number(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be a number")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return float(value)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Paths, limits, and explicit side-effect permissions for one run."""

    query: str
    metadata_path: Path
    cache_dir: Path
    c_sdk_path: Path
    rust_sdk_path: Path
    output_root: Path
    allow_download: bool = False
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    download_timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_render_bytes: int = DEFAULT_MAX_RENDER_BYTES
    validation_timeout_seconds: float = DEFAULT_VALIDATION_TIMEOUT_SECONDS
    max_diagnostic_bytes: int = DEFAULT_MAX_DIAGNOSTIC_BYTES
    rustfmt_executable: str = "rustfmt"
    cargo_executable: str = "cargo"

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _text(self.query, "query"))
        for name in (
            "metadata_path",
            "cache_dir",
            "c_sdk_path",
            "rust_sdk_path",
            "output_root",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))
        if not isinstance(self.allow_download, bool):
            raise TypeError("allow_download must be a boolean")
        for name in (
            "max_archive_bytes",
            "max_request_bytes",
            "max_render_bytes",
            "max_diagnostic_bytes",
        ):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        for name in ("download_timeout_seconds", "validation_timeout_seconds"):
            object.__setattr__(
                self,
                name,
                _positive_number(getattr(self, name), name),
            )
        object.__setattr__(
            self,
            "rustfmt_executable",
            _text(self.rustfmt_executable, "rustfmt_executable"),
        )
        object.__setattr__(
            self,
            "cargo_executable",
            _text(self.cargo_executable, "cargo_executable"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "metadata_path": str(self.metadata_path),
            "cache_dir": str(self.cache_dir),
            "c_sdk_path": str(self.c_sdk_path),
            "rust_sdk_path": str(self.rust_sdk_path),
            "output_root": str(self.output_root),
            "allow_download": self.allow_download,
            "max_archive_bytes": self.max_archive_bytes,
            "download_timeout_seconds": self.download_timeout_seconds,
            "max_request_bytes": self.max_request_bytes,
            "max_render_bytes": self.max_render_bytes,
            "validation_timeout_seconds": self.validation_timeout_seconds,
            "max_diagnostic_bytes": self.max_diagnostic_bytes,
            "rustfmt_executable": self.rustfmt_executable,
            "cargo_executable": self.cargo_executable,
        }


class OrchestrationError(Exception):
    """A named pipeline stage could not produce its validated result."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = _text(stage, "stage")
        self.detail = _text(message, "message")
        super().__init__(f"{self.stage}: {self.detail}")


@dataclass(frozen=True, slots=True)
class ConversionRun:
    """Auditable result connecting every implemented conversion boundary."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    config: PipelineConfig
    package: PackageRecord
    archive: CachedArchive
    translation_plan: TranslationPlan
    model_request: ModelRequest
    model_conversion: ModelConversion
    rendered_package: RenderedPackage
    validation_report: ValidationReport
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        expected_types = (
            ("config", self.config, PipelineConfig),
            ("package", self.package, PackageRecord),
            ("archive", self.archive, CachedArchive),
            ("translation_plan", self.translation_plan, TranslationPlan),
            ("model_request", self.model_request, ModelRequest),
            ("model_conversion", self.model_conversion, ModelConversion),
            ("rendered_package", self.rendered_package, RenderedPackage),
            ("validation_report", self.validation_report, ValidationReport),
        )
        for name, value, expected_type in expected_types:
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be a {expected_type.__name__}")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported conversion run version: {self.schema_version!r}"
            )
        if self.archive.package != self.package:
            raise ValueError("archive does not belong to selected package")
        plan_package = (
            self.translation_plan.package_ir.source_bundle.inventory.archive.package
        )
        if plan_package != self.package:
            raise ValueError("translation plan does not belong to selected package")
        if self.model_conversion.request_context_sha256 != (
            self.model_request.context_sha256
        ):
            raise ValueError("model conversion does not belong to model request")
        if self.rendered_package.request_context_sha256 != (
            self.model_request.context_sha256
        ):
            raise ValueError("rendered package does not belong to model request")
        if self.rendered_package.source_archive_sha256 != self.archive.sha256:
            raise ValueError("rendered package does not belong to cached archive")
        if self.validation_report.request_context_sha256 != (
            self.model_request.context_sha256
        ):
            raise ValueError("validation report does not belong to model request")
        if self.validation_report.source_archive_sha256 != self.archive.sha256:
            raise ValueError("validation report does not belong to cached archive")
        if self.validation_report.files != self.rendered_package.files:
            raise ValueError("validation report does not cover rendered files")
        if self.validation_report.output_directory != (
            self.rendered_package.output_directory
        ):
            raise ValueError("validation report points to a different output")

    @property
    def status(self) -> str:
        return (
            "local_validation_passed"
            if self.validation_report.passed
            else "local_validation_failed"
        )

    @property
    def network_download_performed(self) -> bool:
        return not self.archive.cache_hit

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "network_download_performed": self.network_download_performed,
            "config": self.config.to_dict(),
            "package": self.package.to_dict(),
            "archive": self.archive.to_dict(),
            "translation_plan": self.translation_plan.to_dict(),
            "model_request": self.model_request.to_dict(),
            "model_conversion": self.model_conversion.to_dict(),
            "rendered_package": self.rendered_package.to_dict(),
            "validation_report": self.validation_report.to_dict(),
        }


def select_catalog_package(catalog: PackageCatalog, query: str) -> PackageRecord:
    """Return exactly one case-insensitive exact or unique partial match."""
    if not isinstance(catalog, PackageCatalog):
        raise TypeError("catalog must be a PackageCatalog")
    query = _text(query, "query")
    matches = catalog.search(query)
    exact = tuple(item for item in matches if item.name.casefold() == query.casefold())
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    suggestions = ", ".join(item.name for item in matches[:10]) or "none"
    raise OrchestrationError(
        "catalog",
        f"expected one package for {query!r}, found {len(matches)}; "
        f"matches: {suggestions}",
    )


def _emit(callback: ProgressCallback | None, stage: str, detail: str) -> None:
    if callback is not None:
        callback(stage, detail)


def _stage(stage: str, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except OrchestrationError:
        raise
    except Exception as error:
        raise OrchestrationError(stage, str(error)) from error


async def run_conversion(
    config: PipelineConfig,
    model_client_factory: ModelClientFactory,
    *,
    progress: ProgressCallback | None = None,
) -> ConversionRun:
    """Run every implemented stage with explicit download and model controls."""
    if not isinstance(config, PipelineConfig):
        raise TypeError("config must be a PipelineConfig")
    if not callable(model_client_factory):
        raise TypeError("model_client_factory must be callable")

    catalog = _stage("catalog", lambda: load_catalog(config.metadata_path))
    package = select_catalog_package(catalog, config.query)
    _emit(progress, "catalog", f"selected {package.name}")

    archive = _stage(
        "archive",
        lambda: load_cached_archive(package, config.cache_dir),
    )
    if archive is None:
        if not config.allow_download:
            raise OrchestrationError(
                "archive",
                "no validated cache entry; download permission was not granted",
            )
        archive = _stage(
            "archive",
            lambda: download_package(
                package,
                config.cache_dir,
                max_archive_bytes=config.max_archive_bytes,
                timeout_seconds=config.download_timeout_seconds,
            ),
        )
        archive_detail = "downloaded and cached through ordinary HTTPS"
    else:
        archive_detail = "reused validated local cache"
    _emit(progress, "archive", archive_detail)

    inventory = _stage("source", lambda: inspect_archive(archive))
    source_bundle = _stage("source", lambda: build_source_bundle(inventory))
    _emit(
        progress,
        "source",
        f"selected {len(source_bundle.text_files)} canonical text files",
    )

    package_ir = _stage("parser", lambda: parse_source_bundle(source_bundle))
    _emit(progress, "parser", f"parsed {len(package_ir.files)} source files")

    sdk_database = _stage(
        "sdk_mapping",
        lambda: build_sdk_mapping_database(
            config.c_sdk_path,
            config.rust_sdk_path,
        ),
    )
    plan = _stage(
        "sdk_mapping",
        lambda: build_translation_plan(package_ir, sdk_database),
    )
    _emit(
        progress,
        "sdk_mapping",
        f"resolved {len(plan.resolutions)} calls into "
        f"{len(plan.required_rust_crates)} Rust crates",
    )

    request = _stage(
        "context",
        lambda: build_model_request(
            plan,
            sdk_database,
            max_request_bytes=config.max_request_bytes,
        ),
    )
    _emit(
        progress,
        "context",
        f"built {request.request_bytes}-byte request "
        f"(~{request.estimated_input_tokens} tokens)",
    )

    client = _stage("model", lambda: model_client_factory(plan))
    if not isinstance(client, ModelClient):
        raise OrchestrationError(
            "model",
            "model client factory did not return a ModelClient",
        )
    try:
        conversion = await client.convert(request)
    except Exception as error:
        raise OrchestrationError("model", str(error)) from error
    if not isinstance(conversion, ModelConversion):
        raise OrchestrationError(
            "model",
            "model client did not return a ModelConversion",
        )
    _emit(
        progress,
        "model",
        f"received {conversion.model_id} response using "
        f"{conversion.usage.total_tokens} tokens",
    )

    rendered = _stage(
        "render",
        lambda: render_package(
            plan,
            request,
            conversion,
            config.output_root,
            max_total_bytes=config.max_render_bytes,
        ),
    )
    action = "reused identical output" if rendered.reused_existing else "created output"
    _emit(progress, "render", f"{action} at {rendered.output_directory}")

    validation = _stage(
        "validate",
        lambda: validate_rendered_package(
            rendered,
            rustfmt_executable=config.rustfmt_executable,
            cargo_executable=config.cargo_executable,
            timeout_seconds=config.validation_timeout_seconds,
            max_diagnostic_bytes=config.max_diagnostic_bytes,
        ),
    )
    _emit(
        progress,
        "validate",
        "local checks passed" if validation.passed else "local checks failed",
    )
    return ConversionRun(
        config=config,
        package=package,
        archive=archive,
        translation_plan=plan,
        model_request=request,
        model_conversion=conversion,
        rendered_package=rendered,
        validation_report=validation,
    )
