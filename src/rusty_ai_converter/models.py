"""Versioned data models shared by converter pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, ClassVar, Iterable, Mapping
import unicodedata
from urllib.parse import urlparse


_WHITESPACE_RE = re.compile(r"\s+")
_IDENTIFIER_RE = re.compile(r"[^a-z0-9]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ARCHIVE_FILE_ROLES = frozenset(
    {
        "manifest",
        "example_metadata",
        "c_header",
        "c_source",
        "example_source",
        "build_file",
        "documentation",
        "generated_documentation",
        "resource",
        "other",
    }
)


def _required_text(value: str, field_name: str) -> str:
    """Return normalized non-empty text or raise a useful validation error."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    normalized = _WHITESPACE_RE.sub(" ", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _logical_archive_file_path(value: str, field_name: str) -> str:
    """Validate a ZIP-internal regular-file path without changing its text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise ValueError(f"{field_name} must be a safe relative archive path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} must be a safe relative archive path")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise ValueError(f"{field_name} must not contain a drive prefix")
    return value


def _normalized_identifier(value: str) -> str:
    """Convert human-readable catalog text into a stable lowercase token."""
    return _IDENTIFIER_RE.sub("-", value.lower()).strip("-")


@dataclass(frozen=True, slots=True)
class PackageRecord:
    """One downloadable Click package listed in the source catalog.

    This model describes where an archive can be fetched. It deliberately does
    not download the archive and contains no model-related behavior.
    """

    category: str
    name: str
    download_url: str

    def __post_init__(self) -> None:
        category = _required_text(self.category, "category")
        name = _required_text(self.name, "name")
        download_url = _required_text(self.download_url, "download_url")

        parsed_url = urlparse(download_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise ValueError("download_url must be an absolute HTTPS URL")
        if not parsed_url.path.lower().endswith(".zip"):
            raise ValueError("download_url must point to a ZIP archive")

        object.__setattr__(self, "category", category)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "download_url", download_url)

    @property
    def normalized_name(self) -> str:
        """Return a filesystem- and identifier-friendly Click name."""
        name_without_click = re.sub(r"\s+click$", "", self.name, flags=re.IGNORECASE)
        return _normalized_identifier(name_without_click)

    @property
    def catalog_id(self) -> str:
        """Return an immutable identity derived from the complete catalog row."""
        identity = "\0".join((self.category, self.name, self.download_url))
        digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"{_normalized_identifier(self.category)}/{self.normalized_name}-{digest}"

    def to_dict(self) -> dict[str, str]:
        """Serialize this record using the versioned schema field names."""
        return {
            "catalog_id": self.catalog_id,
            "category": self.category,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "download_url": self.download_url,
        }

    @classmethod
    def from_metadata(cls, category: str, value: Mapping[str, Any]) -> PackageRecord:
        """Create a record from one entry in ``metadata_clicks_c.json``."""
        if not isinstance(value, Mapping):
            raise TypeError("metadata entry must be an object")

        return cls(
            category=category,
            name=value.get("name", ""),
            download_url=value.get("download_link", ""),
        )


@dataclass(frozen=True, slots=True)
class PackageCatalog:
    """Validated collection of Click package records."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    packages: tuple[PackageRecord, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        packages = tuple(self.packages)
        if any(not isinstance(package, PackageRecord) for package in packages):
            raise TypeError("packages must contain only PackageRecord instances")

        catalog_ids = [package.catalog_id for package in packages]
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("catalog contains duplicate package records")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported catalog schema version: {self.schema_version!r}"
            )

        object.__setattr__(self, "packages", packages)

    @classmethod
    def from_records(cls, records: Iterable[PackageRecord]) -> PackageCatalog:
        """Build a catalog from any iterable without retaining mutable input."""
        return cls(packages=tuple(records))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the catalog in a stable, JSON-compatible shape."""
        return {
            "schema_version": self.schema_version,
            "packages": [package.to_dict() for package in self.packages],
        }

    @property
    def categories(self) -> tuple[str, ...]:
        """Return category names in deterministic, case-insensitive order."""
        return tuple(sorted({package.category for package in self.packages}, key=str.casefold))

    def in_category(self, category: str) -> tuple[PackageRecord, ...]:
        """Return packages in a category, accepting spaces or punctuation variants."""
        category_id = _normalized_identifier(_required_text(category, "category"))
        return tuple(
            package
            for package in self.packages
            if _normalized_identifier(package.category) == category_id
        )

    def search(self, query: str) -> tuple[PackageRecord, ...]:
        """Find packages by display-name text or normalized package name."""
        query_text = _required_text(query, "query")
        query_casefolded = query_text.casefold()
        query_id = _normalized_identifier(
            re.sub(r"\s+click$", "", query_text, flags=re.IGNORECASE)
        )

        return tuple(
            package
            for package in self.packages
            if query_casefolded in package.name.casefold()
            or query_id in package.normalized_name
        )

    def __len__(self) -> int:
        return len(self.packages)


@dataclass(frozen=True, slots=True)
class CachedArchive:
    """A validated package archive stored in the local content cache."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    package: PackageRecord
    archive_path: Path
    sha256: str
    size_bytes: int
    cache_hit: bool
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.package, PackageRecord):
            raise TypeError("package must be a PackageRecord")

        archive_path = Path(self.archive_path)
        digest = _required_text(self.sha256, "sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if not isinstance(self.cache_hit, bool):
            raise TypeError("cache_hit must be a boolean")
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported cached archive schema version: {self.schema_version!r}"
            )

        object.__setattr__(self, "archive_path", archive_path)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        """Serialize archive metadata without including archive bytes."""
        return {
            "schema_version": self.schema_version,
            "package": self.package.to_dict(),
            "archive_path": str(self.archive_path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """One safe, regular file discovered inside a Click ZIP archive."""

    path: str
    role: str
    size_bytes: int
    compressed_size_bytes: int
    sha256: str
    duplicate_of: str | None = None

    def __post_init__(self) -> None:
        path = _logical_archive_file_path(self.path, "path")
        role = _required_text(self.role, "role")
        digest = _required_text(self.sha256, "sha256").lower()

        if role not in ARCHIVE_FILE_ROLES:
            raise ValueError(f"unsupported archive file role: {role!r}")
        for field_name, value in (
            ("size_bytes", self.size_bytes),
            ("compressed_size_bytes", self.compressed_size_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must not be negative")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")

        duplicate_of = self.duplicate_of
        if duplicate_of is not None:
            duplicate_of = _logical_archive_file_path(duplicate_of, "duplicate_of")
            if duplicate_of == path:
                raise ValueError("duplicate_of must identify a different file")

        object.__setattr__(self, "path", path)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "duplicate_of", duplicate_of)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the logical file metadata without including file content."""
        return {
            "path": self.path,
            "role": self.role,
            "size_bytes": self.size_bytes,
            "compressed_size_bytes": self.compressed_size_bytes,
            "sha256": self.sha256,
            "duplicate_of": self.duplicate_of,
        }


@dataclass(frozen=True, slots=True)
class PackageInventory:
    """Versioned, extraction-free inventory of a validated Click archive."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    archive: CachedArchive
    files: tuple[ArchiveFile, ...] = field(default_factory=tuple)
    total_expanded_bytes: int = 0
    common_root: str | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.archive, CachedArchive):
            raise TypeError("archive must be a CachedArchive")

        files = tuple(self.files)
        if any(not isinstance(file, ArchiveFile) for file in files):
            raise TypeError("files must contain only ArchiveFile instances")
        paths = [file.path for file in files]
        collision_keys = [unicodedata.normalize("NFC", path).casefold() for path in paths]
        if len(collision_keys) != len(set(collision_keys)):
            raise ValueError("inventory contains duplicate logical paths")
        if paths != sorted(paths, key=str.casefold):
            raise ValueError("inventory files must use deterministic path ordering")

        files_by_path: dict[str, ArchiveFile] = {}
        for file in files:
            if file.duplicate_of is not None:
                original = files_by_path.get(file.duplicate_of)
                if original is None:
                    raise ValueError("duplicate_of must reference an earlier inventory file")
                if original.sha256 != file.sha256:
                    raise ValueError("duplicate files must have matching SHA-256 values")
            files_by_path[file.path] = file

        if (
            not isinstance(self.total_expanded_bytes, int)
            or isinstance(self.total_expanded_bytes, bool)
        ):
            raise TypeError("total_expanded_bytes must be an integer")
        if self.total_expanded_bytes < 0:
            raise ValueError("total_expanded_bytes must not be negative")
        if self.total_expanded_bytes != sum(file.size_bytes for file in files):
            raise ValueError("total_expanded_bytes does not match inventory files")

        common_root = self.common_root
        if common_root is not None:
            common_root = _logical_archive_file_path(common_root, "common_root")
            if "/" in common_root:
                raise ValueError("common_root must contain exactly one path segment")
            if any(not path.startswith(f"{common_root}/") for path in paths):
                raise ValueError("common_root does not contain every inventory file")
        diagnostics = tuple(
            _required_text(diagnostic, "diagnostic")
            for diagnostic in self.diagnostics
        )
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported package inventory schema version: {self.schema_version!r}"
            )

        object.__setattr__(self, "files", files)
        object.__setattr__(self, "common_root", common_root)
        object.__setattr__(self, "diagnostics", diagnostics)

    def files_with_role(self, role: str) -> tuple[ArchiveFile, ...]:
        """Return files classified with one supported role."""
        normalized_role = _required_text(role, "role")
        if normalized_role not in ARCHIVE_FILE_ROLES:
            raise ValueError(f"unsupported archive file role: {normalized_role!r}")
        return tuple(file for file in self.files if file.role == normalized_role)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete inventory as JSON-compatible data."""
        return {
            "schema_version": self.schema_version,
            "archive": self.archive.to_dict(),
            "files": [file.to_dict() for file in self.files],
            "total_expanded_bytes": self.total_expanded_bytes,
            "common_root": self.common_root,
            "diagnostics": list(self.diagnostics),
        }
