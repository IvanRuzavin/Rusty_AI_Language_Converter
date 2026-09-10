"""Run bounded, offline checks on one rendered Rust Click package."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Any, ClassVar

from rusty_ai_converter.render import (
    REQUIRED_ARTIFACT_PATHS,
    RenderedFile,
    RenderedPackage,
    render_cargo_toml,
    render_default_mikrobus,
)


VALIDATION_CHECK_STATUSES = ("passed", "failed", "skipped")
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_DIAGNOSTIC_BYTES = 64 * 1024
_CHECK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _strings(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One reproducible local validation decision and its diagnostics."""

    name: str
    status: str
    summary: str
    command: tuple[str, ...] = field(default_factory=tuple)
    stdout: str = ""
    stderr: str = ""

    def __post_init__(self) -> None:
        name = _text(self.name, "name")
        if not _CHECK_NAME_RE.fullmatch(name):
            raise ValueError("validation check name must be lowercase snake_case")
        status = _text(self.status, "status")
        if status not in VALIDATION_CHECK_STATUSES:
            raise ValueError(f"unsupported validation status: {status!r}")
        command = tuple(self.command)
        if any(not isinstance(item, str) or not item for item in command):
            raise ValueError("command must contain non-empty strings")
        if status == "skipped" and command:
            raise ValueError("a skipped check must not contain a command")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise TypeError("stdout and stderr must be strings")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        object.__setattr__(self, "command", command)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of local checks; full SDK compilation remains a later gate."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    catalog_id: str
    package_name: str
    output_directory: Path
    source_archive_sha256: str
    request_context_sha256: str
    files: tuple[RenderedFile, ...]
    checks: tuple[ValidationCheck, ...]
    rustfmt_version: str | None = None
    cargo_version: str | None = None
    sdk_compilation_status: str = "not_run"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        files = tuple(self.files)
        if any(not isinstance(item, RenderedFile) for item in files):
            raise TypeError("files must contain only RenderedFile values")
        if tuple(item.path for item in files) != REQUIRED_ARTIFACT_PATHS:
            raise ValueError("files must contain the four required artifacts in order")
        checks = tuple(self.checks)
        if not checks or any(not isinstance(item, ValidationCheck) for item in checks):
            raise ValueError("checks must contain ValidationCheck values")
        check_names = tuple(item.name for item in checks)
        if len(check_names) != len(set(check_names)):
            raise ValueError("validation check names must be unique")
        if self.sdk_compilation_status != "not_run":
            raise ValueError("local validation cannot claim SDK compilation")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported validation report version: {self.schema_version!r}"
            )

        object.__setattr__(self, "catalog_id", _text(self.catalog_id, "catalog_id"))
        object.__setattr__(
            self,
            "package_name",
            _text(self.package_name, "package_name"),
        )
        object.__setattr__(self, "output_directory", Path(self.output_directory))
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "warnings", _strings(self.warnings, "warning"))
        for name in ("source_archive_sha256", "request_context_sha256"):
            digest = _text(getattr(self, name), name).lower()
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError(f"{name} must be a SHA-256 digest")
            object.__setattr__(self, name, digest)
        for name in ("rustfmt_version", "cargo_version"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))

    @property
    def passed(self) -> bool:
        """Whether every local check passed."""
        return all(item.status == "passed" for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": "local",
            "catalog_id": self.catalog_id,
            "package_name": self.package_name,
            "output_directory": str(self.output_directory),
            "source_archive_sha256": self.source_archive_sha256,
            "request_context_sha256": self.request_context_sha256,
            "files": [item.to_dict() for item in self.files],
            "checks": [item.to_dict() for item in self.checks],
            "passed": self.passed,
            "rustfmt_version": self.rustfmt_version,
            "cargo_version": self.cargo_version,
            "sdk_compilation_status": self.sdk_compilation_status,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _bounded_output(value: bytes | str | None, max_bytes: int) -> str:
    if value is None:
        return ""
    data = value.encode("utf-8", errors="replace") if isinstance(value, str) else value
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    suffix = f"\n... diagnostic truncated after {max_bytes} bytes ...\n"
    return data[:max_bytes].decode("utf-8", errors="replace") + suffix


def _run_tool(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_diagnostic_bytes: int,
) -> _CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return _CommandResult(127, stderr=f"executable not found: {command[0]}\n")
    except subprocess.TimeoutExpired as error:
        stderr = _bounded_output(error.stderr, max_diagnostic_bytes)
        stderr += f"command timed out after {timeout_seconds:g} seconds\n"
        return _CommandResult(
            124,
            stdout=_bounded_output(error.stdout, max_diagnostic_bytes),
            stderr=stderr,
        )
    except OSError as error:
        return _CommandResult(126, stderr=f"cannot execute {command[0]}: {error}\n")
    return _CommandResult(
        completed.returncode,
        stdout=_bounded_output(completed.stdout, max_diagnostic_bytes),
        stderr=_bounded_output(completed.stderr, max_diagnostic_bytes),
    )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_integrity_error(rendered: RenderedPackage) -> str | None:
    target = rendered.output_directory
    if target.is_symlink() or not target.is_dir():
        return f"output is not a regular directory: {target}"
    try:
        entries = tuple(target.iterdir())
    except OSError as error:
        return f"cannot inspect output directory: {error}"
    if {item.name for item in entries} != set(REQUIRED_ARTIFACT_PATHS):
        return "output directory does not contain exactly the four rendered files"

    expected_files = {item.path: item for item in rendered.files}
    for name in REQUIRED_ARTIFACT_PATHS:
        path = target / name
        expected = expected_files[name]
        if path.is_symlink() or not path.is_file():
            return f"rendered artifact is not a regular file: {name}"
        try:
            size = path.stat().st_size
            digest = _sha256_file(path)
        except OSError as error:
            return f"cannot read rendered artifact {name}: {error}"
        if size != expected.size_bytes or digest != expected.sha256:
            return f"rendered artifact changed after rendering: {name}"

    deterministic = {
        "Cargo.toml": render_cargo_toml().encode("utf-8"),
        "mikrobus.rs": render_default_mikrobus().encode("utf-8"),
    }
    for name, expected in deterministic.items():
        try:
            if (target / name).read_bytes() != expected:
                return f"deterministic artifact has unexpected content: {name}"
        except OSError as error:
            return f"cannot read deterministic artifact {name}: {error}"
    return None


def _version(
    executable: str,
    *,
    cwd: Path,
    timeout_seconds: float,
    max_diagnostic_bytes: int,
) -> tuple[str | None, _CommandResult]:
    result = _run_tool(
        (executable, "--version"),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    if result.returncode != 0:
        return None, result
    version = result.stdout.strip()
    if not version:
        return None, _CommandResult(
            1,
            stdout=result.stdout,
            stderr="version command returned no version text\n",
        )
    return version, result


def _rustfmt_check(
    target: Path,
    executable: str,
    *,
    timeout_seconds: float,
    max_diagnostic_bytes: int,
) -> tuple[ValidationCheck, str | None]:
    version, version_result = _version(
        executable,
        cwd=target,
        timeout_seconds=timeout_seconds,
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    if version is None:
        return (
            ValidationCheck(
                name="rustfmt",
                status="failed",
                summary="rustfmt is unavailable or its version cannot be read",
                command=(executable, "--version"),
                stdout=version_result.stdout,
                stderr=version_result.stderr,
            ),
            None,
        )

    command = (
        executable,
        "--check",
        "--edition",
        "2024",
        "--config",
        "skip_children=true",
        "library.rs",
        "main.rs",
        "mikrobus.rs",
    )
    result = _run_tool(
        command,
        cwd=target,
        timeout_seconds=timeout_seconds,
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    passed = result.returncode == 0
    return (
        ValidationCheck(
            name="rustfmt",
            status="passed" if passed else "failed",
            summary=(
                "Rust sources parse and satisfy rustfmt"
                if passed
                else "rustfmt found invalid syntax or formatting differences"
            ),
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        ),
        version,
    )


def _cargo_metadata_check(
    target: Path,
    executable: str,
    *,
    timeout_seconds: float,
    max_diagnostic_bytes: int,
) -> tuple[ValidationCheck, str | None]:
    version, version_result = _version(
        executable,
        cwd=target,
        timeout_seconds=timeout_seconds,
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    if version is None:
        return (
            ValidationCheck(
                name="cargo_metadata",
                status="failed",
                summary="Cargo is unavailable or its version cannot be read",
                command=(executable, "--version"),
                stdout=version_result.stdout,
                stderr=version_result.stderr,
            ),
            None,
        )

    command = (
        executable,
        "metadata",
        "--offline",
        "--no-deps",
        "--format-version",
        "1",
        "--manifest-path",
        "Cargo.toml",
    )
    result = _run_tool(
        command,
        cwd=target,
        timeout_seconds=timeout_seconds,
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    error: str | None = None
    if result.returncode != 0:
        error = "Cargo rejected the generated workspace metadata"
    else:
        try:
            metadata = json.loads(result.stdout)
            workspace_root = Path(metadata["workspace_root"])
            entry = metadata["metadata"]["mikrobus-rust"]["entry"]
            if workspace_root.resolve() != target.resolve():
                error = "Cargo reported an unexpected workspace root"
            elif entry != "main.rs":
                error = "Cargo metadata does not select main.rs"
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as exception:
            error = f"Cargo returned invalid metadata: {exception}"

    return (
        ValidationCheck(
            name="cargo_metadata",
            status="passed" if error is None else "failed",
            summary=error or "Cargo accepts the offline workspace metadata",
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        ),
        version,
    )


def validate_rendered_package(
    rendered: RenderedPackage,
    *,
    rustfmt_executable: str = "rustfmt",
    cargo_executable: str = "cargo",
    timeout_seconds: float = DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    max_diagnostic_bytes: int = DEFAULT_MAX_DIAGNOSTIC_BYTES,
) -> ValidationReport:
    """Validate artifacts locally without network access or SDK compilation."""
    if not isinstance(rendered, RenderedPackage):
        raise TypeError("rendered must be a RenderedPackage")
    rustfmt_executable = _text(rustfmt_executable, "rustfmt_executable")
    cargo_executable = _text(cargo_executable, "cargo_executable")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(
        timeout_seconds,
        bool,
    ):
        raise TypeError("timeout_seconds must be a number")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not isinstance(max_diagnostic_bytes, int) or isinstance(
        max_diagnostic_bytes,
        bool,
    ):
        raise TypeError("max_diagnostic_bytes must be an integer")
    if max_diagnostic_bytes < 1:
        raise ValueError("max_diagnostic_bytes must be positive")

    integrity_error = _artifact_integrity_error(rendered)
    if integrity_error is not None:
        checks = (
            ValidationCheck("artifact_integrity", "failed", integrity_error),
            ValidationCheck(
                "rustfmt",
                "skipped",
                "Skipped because artifact integrity failed",
            ),
            ValidationCheck(
                "cargo_metadata",
                "skipped",
                "Skipped because artifact integrity failed",
            ),
        )
        return _report(rendered, checks)

    integrity = ValidationCheck(
        "artifact_integrity",
        "passed",
        "All four files match the renderer report",
    )
    rustfmt, rustfmt_version = _rustfmt_check(
        rendered.output_directory,
        rustfmt_executable,
        timeout_seconds=float(timeout_seconds),
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    cargo, cargo_version = _cargo_metadata_check(
        rendered.output_directory,
        cargo_executable,
        timeout_seconds=float(timeout_seconds),
        max_diagnostic_bytes=max_diagnostic_bytes,
    )
    return _report(
        rendered,
        (integrity, rustfmt, cargo),
        rustfmt_version=rustfmt_version,
        cargo_version=cargo_version,
    )


def _report(
    rendered: RenderedPackage,
    checks: tuple[ValidationCheck, ...],
    *,
    rustfmt_version: str | None = None,
    cargo_version: str | None = None,
) -> ValidationReport:
    return ValidationReport(
        catalog_id=rendered.catalog_id,
        package_name=rendered.package_name,
        output_directory=rendered.output_directory,
        source_archive_sha256=rendered.source_archive_sha256,
        request_context_sha256=rendered.request_context_sha256,
        files=rendered.files,
        checks=checks,
        rustfmt_version=rustfmt_version,
        cargo_version=cargo_version,
        warnings=(
            "Full cargo check was not run; it requires the extension-generated "
            "SDK and target configuration.",
        ),
    )
