"""Versioned data models shared by converter pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, ClassVar, Iterable, Mapping
from urllib.parse import urlparse


_WHITESPACE_RE = re.compile(r"\s+")
_IDENTIFIER_RE = re.compile(r"[^a-z0-9]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: str, field_name: str) -> str:
    """Return normalized non-empty text or raise a useful validation error."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    normalized = _WHITESPACE_RE.sub(" ", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


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
