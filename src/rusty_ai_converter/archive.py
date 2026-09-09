"""Safely inspect and classify Click package ZIP archives without extraction."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
import zipfile
import zlib

from rusty_ai_converter.models import ArchiveFile, CachedArchive, PackageInventory


DEFAULT_MAX_ENTRIES = 4_096
DEFAULT_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PATH_BYTES = 512
DEFAULT_MAX_COMPRESSION_RATIO = 1_000.0
READ_CHUNK_BYTES = 64 * 1024

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_MANIFEST_NAMES = frozenset(
    {"manifest.json", "package.json", "click_manifest.json"}
)
_EXAMPLE_DIRECTORIES = frozenset({"example", "examples", "demo", "demos"})
_C_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp"})
_C_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp"})
_BUILD_NAMES = frozenset(
    {"cmakelists.txt", "makefile", "meson.build", "platformio.ini"}
)
_BUILD_SUFFIXES = frozenset({".cmake", ".mk"})
_DOCUMENT_SUFFIXES = frozenset({".md", ".rst"})
_DOCUMENT_PREFIXES = (
    "readme",
    "license",
    "licence",
    "copying",
    "changelog",
    "authors",
)
_RESOURCE_SUFFIXES = frozenset(
    {
        ".bin",
        ".bmp",
        ".csv",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".json",
        ".pdf",
        ".png",
        ".raw",
        ".svg",
        ".ttf",
        ".wav",
        ".xml",
        ".yaml",
        ".yml",
    }
)


class ArchiveInspectionError(Exception):
    """A cached ZIP cannot be represented as a safe package inventory."""


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_member_path(name: str, *, max_path_bytes: int) -> str:
    if not name or "\x00" in name:
        raise ArchiveInspectionError("archive contains an empty or NUL-containing path")
    if "\\" in name:
        raise ArchiveInspectionError(
            f"archive path uses a backslash separator: {name!r}"
        )
    if len(name.encode("utf-8")) > max_path_bytes:
        raise ArchiveInspectionError(
            f"archive path exceeds the {max_path_bytes}-byte limit: {name!r}"
        )

    path_without_directory_marker = name[:-1] if name.endswith("/") else name
    if not path_without_directory_marker:
        raise ArchiveInspectionError(f"archive contains an invalid root path: {name!r}")

    candidate = PurePosixPath(path_without_directory_marker)
    parts = path_without_directory_marker.split("/")
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ArchiveInspectionError(f"archive path is not safe: {name!r}")
    if _DRIVE_PREFIX_RE.match(parts[0]):
        raise ArchiveInspectionError(f"archive path contains a drive prefix: {name!r}")

    return "/".join(parts)


def _validate_member_type(info: zipfile.ZipInfo, path: str) -> None:
    if info.flag_bits & 0x1:
        raise ArchiveInspectionError(f"encrypted ZIP member is not supported: {path}")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise ArchiveInspectionError(f"symbolic link is not allowed in archive: {path}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ArchiveInspectionError(
            f"special filesystem entry is not allowed in archive: {path}"
        )


def classify_archive_path(path: str) -> str:
    """Classify a previously validated logical archive path by explicit rules."""
    logical_path = PurePosixPath(path)
    base_name = logical_path.name.casefold()
    suffix = logical_path.suffix.casefold()
    directory_names = {part.casefold() for part in logical_path.parts[:-1]}
    is_example = bool(directory_names & _EXAMPLE_DIRECTORIES)

    if tuple(part.casefold() for part in logical_path.parts[:3]) == (
        "help",
        "doc",
        "html",
    ):
        return "generated_documentation"
    if base_name in _MANIFEST_NAMES:
        return "manifest"
    if base_name == "manifest.exm":
        return "example_metadata"
    if suffix == ".c" and logical_path.stem.casefold().endswith(
        ("_resource", "_resources")
    ):
        return "resource"
    if suffix in (_C_HEADER_SUFFIXES | _C_SOURCE_SUFFIXES) and (
        is_example or base_name in {"main.c", "main.cc", "main.cpp"}
    ):
        return "example_source"
    if suffix in _C_HEADER_SUFFIXES:
        return "c_header"
    if suffix in _C_SOURCE_SUFFIXES:
        return "c_source"
    if (
        base_name in _BUILD_NAMES
        or base_name.startswith("click.")
        or suffix in _BUILD_SUFFIXES
    ):
        return "build_file"
    if suffix in _DOCUMENT_SUFFIXES or base_name.startswith(_DOCUMENT_PREFIXES):
        return "documentation"
    if suffix in _RESOURCE_SUFFIXES:
        return "resource"
    return "other"


def _common_root(paths: list[str]) -> str | None:
    if not paths:
        return None
    split_paths = [path.split("/") for path in paths]
    first_part = split_paths[0][0]
    if all(len(parts) > 1 and parts[0] == first_part for parts in split_paths):
        return first_part
    return None


def inspect_archive(
    cached_archive: CachedArchive,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_path_bytes: int = DEFAULT_MAX_PATH_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> PackageInventory:
    """Validate, hash, and classify a cached ZIP without extracting its files."""
    if not isinstance(cached_archive, CachedArchive):
        raise TypeError("cached_archive must be a CachedArchive")
    for field_name, value in (
        ("max_entries", max_entries),
        ("max_expanded_bytes", max_expanded_bytes),
        ("max_file_bytes", max_file_bytes),
        ("max_path_bytes", max_path_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{field_name} must be an integer")
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
    if not isinstance(max_compression_ratio, (int, float)) or isinstance(
        max_compression_ratio, bool
    ):
        raise TypeError("max_compression_ratio must be a number")
    if max_compression_ratio <= 0:
        raise ValueError("max_compression_ratio must be positive")

    archive_path = cached_archive.archive_path.resolve()
    try:
        actual_archive_size = archive_path.stat().st_size
        actual_archive_sha256 = _file_sha256(archive_path)
    except OSError as error:
        raise ArchiveInspectionError(
            f"cannot read cached archive {archive_path}: {error}"
        ) from error

    if actual_archive_size != cached_archive.size_bytes:
        raise ArchiveInspectionError("cached archive size does not match its metadata")
    if actual_archive_sha256 != cached_archive.sha256:
        raise ArchiveInspectionError("cached archive SHA-256 does not match its metadata")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            if len(members) > max_entries:
                raise ArchiveInspectionError(
                    f"archive contains more than the {max_entries}-entry limit"
                )

            safe_members: list[tuple[str, zipfile.ZipInfo]] = []
            collision_keys: dict[str, str] = {}
            declared_total = 0
            for info in members:
                path = _validated_member_path(
                    info.filename,
                    max_path_bytes=max_path_bytes,
                )
                _validate_member_type(info, path)

                collision_key = unicodedata.normalize("NFC", path).casefold()
                previous_path = collision_keys.get(collision_key)
                if previous_path is not None:
                    raise ArchiveInspectionError(
                        f"archive paths collide: {previous_path!r} and {path!r}"
                    )
                collision_keys[collision_key] = path

                if info.is_dir():
                    continue
                if info.file_size > max_file_bytes:
                    raise ArchiveInspectionError(
                        f"archive member exceeds the {max_file_bytes}-byte limit: {path}"
                    )
                declared_total += info.file_size
                if declared_total > max_expanded_bytes:
                    raise ArchiveInspectionError(
                        "archive exceeds the expanded-size limit"
                    )
                if info.file_size and (
                    info.file_size / max(info.compress_size, 1)
                    > max_compression_ratio
                ):
                    raise ArchiveInspectionError(
                        f"archive member exceeds the compression-ratio limit: {path}"
                    )
                safe_members.append((path, info))

            safe_members.sort(key=lambda member: member[0].casefold())
            paths = [path for path, _ in safe_members]
            common_root = _common_root(paths)
            files: list[ArchiveFile] = []
            first_path_by_digest: dict[str, str] = {}
            actual_total = 0
            for path, info in safe_members:
                digest = sha256()
                actual_file_size = 0
                with archive.open(info, "r") as member_file:
                    while True:
                        chunk = member_file.read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        actual_file_size += len(chunk)
                        actual_total += len(chunk)
                        if actual_file_size > max_file_bytes:
                            raise ArchiveInspectionError(
                                f"archive member exceeded its size limit: {path}"
                            )
                        if actual_total > max_expanded_bytes:
                            raise ArchiveInspectionError(
                                "archive exceeded the expanded-size limit while reading"
                            )
                        digest.update(chunk)

                if actual_file_size != info.file_size:
                    raise ArchiveInspectionError(
                        f"archive member size does not match its directory entry: {path}"
                    )
                digest_text = digest.hexdigest()
                duplicate_of = first_path_by_digest.get(digest_text)
                if duplicate_of is None:
                    first_path_by_digest[digest_text] = path
                files.append(
                    ArchiveFile(
                        path=path,
                        role=classify_archive_path(
                            path.removeprefix(f"{common_root}/")
                            if common_root is not None
                            else path
                        ),
                        size_bytes=actual_file_size,
                        compressed_size_bytes=info.compress_size,
                        sha256=digest_text,
                        duplicate_of=duplicate_of,
                    )
                )
    except ArchiveInspectionError:
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        raise ArchiveInspectionError(f"cannot inspect ZIP archive: {error}") from error

    diagnostics: list[str] = []
    manifest_count = sum(file.role == "manifest" for file in files)
    if manifest_count == 0:
        diagnostics.append("archive contains no recognized package manifest")
    elif manifest_count > 1:
        diagnostics.append(f"archive contains {manifest_count} recognized manifests")
    if not any(file.role == "c_header" for file in files):
        diagnostics.append("archive contains no classified C header")
    if not any(file.role == "c_source" for file in files):
        diagnostics.append("archive contains no classified C implementation")
    duplicate_count = sum(file.duplicate_of is not None for file in files)
    if duplicate_count:
        diagnostics.append(f"archive contains {duplicate_count} duplicate file(s)")

    return PackageInventory(
        archive=cached_archive,
        files=tuple(files),
        total_expanded_bytes=actual_total,
        common_root=common_root,
        diagnostics=tuple(diagnostics),
    )
