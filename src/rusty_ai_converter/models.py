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
        "assembly_source",
        "example_source",
        "build_file",
        "documentation",
        "generated_documentation",
        "resource",
        "other",
    }
)
SOURCE_TEXT_PURPOSES = frozenset(
    {
        "package_manifest",
        "example_metadata",
        "driver_header",
        "driver_source",
        "driver_assembly",
        "example_source",
        "build_metadata",
        "documentation",
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


@dataclass(frozen=True, slots=True)
class ManifestLibrary:
    """One canonical library directory declared by a package manifest."""

    alias: str
    subdir_name: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "alias", _required_text(self.alias, "alias"))
        object.__setattr__(
            self,
            "subdir_name",
            _required_text(self.subdir_name, "subdir_name"),
        )
        object.__setattr__(
            self,
            "path",
            _logical_archive_file_path(self.path, "path"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "alias": self.alias,
            "subdir_name": self.subdir_name,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Validated identity and canonical directories from ``manifest.json``."""

    manifest_path: str
    display_name: str
    package_name: str
    package_type: str
    version: str
    product_id: str | None
    libraries: tuple[ManifestLibrary, ...] = field(default_factory=tuple)
    example_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        manifest_path = _logical_archive_file_path(
            self.manifest_path,
            "manifest_path",
        )
        display_name = _required_text(self.display_name, "display_name")
        package_name = _required_text(self.package_name, "package_name")
        package_type = _required_text(self.package_type, "package_type")
        version = _required_text(self.version, "version")
        product_id = self.product_id
        if product_id is not None:
            product_id = _required_text(product_id, "product_id")

        libraries = tuple(self.libraries)
        if not libraries:
            raise ValueError("package manifest must declare at least one library")
        if any(not isinstance(library, ManifestLibrary) for library in libraries):
            raise TypeError("libraries must contain only ManifestLibrary instances")
        library_paths = [library.path for library in libraries]
        if len(library_paths) != len(set(library_paths)):
            raise ValueError("package manifest contains duplicate library paths")

        example_paths = tuple(
            _logical_archive_file_path(path, "example_path")
            for path in self.example_paths
        )
        if len(example_paths) != len(set(example_paths)):
            raise ValueError("package manifest contains duplicate example paths")

        object.__setattr__(self, "manifest_path", manifest_path)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "package_name", package_name)
        object.__setattr__(self, "package_type", package_type)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "libraries", libraries)
        object.__setattr__(self, "example_paths", example_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "display_name": self.display_name,
            "package_name": self.package_name,
            "package_type": self.package_type,
            "version": self.version,
            "product_id": self.product_id,
            "libraries": [library.to_dict() for library in self.libraries],
            "example_paths": list(self.example_paths),
        }


@dataclass(frozen=True, slots=True)
class ExampleMetadata:
    """Validated fields from one MikroE ``manifest.exm`` file."""

    path: str
    name: str
    toolchains: tuple[str, ...] = field(default_factory=tuple)
    hardware: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _logical_archive_file_path(self.path, "path"))
        object.__setattr__(self, "name", _required_text(self.name, "name"))
        object.__setattr__(
            self,
            "toolchains",
            tuple(_required_text(item, "toolchain") for item in self.toolchains),
        )
        object.__setattr__(
            self,
            "hardware",
            tuple(_required_text(item, "hardware item") for item in self.hardware),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "toolchains": list(self.toolchains),
            "hardware": list(self.hardware),
        }


@dataclass(frozen=True, slots=True)
class SourceTextFile:
    """One selected UTF-8 source or metadata file with exact provenance."""

    path: str
    purpose: str
    sha256: str
    size_bytes: int
    content: str

    def __post_init__(self) -> None:
        path = _logical_archive_file_path(self.path, "path")
        purpose = _required_text(self.purpose, "purpose")
        digest = _required_text(self.sha256, "sha256").lower()
        if purpose not in SOURCE_TEXT_PURPOSES:
            raise ValueError(f"unsupported source text purpose: {purpose!r}")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        encoded_content = self.content.encode("utf-8")
        if len(encoded_content) != self.size_bytes:
            raise ValueError("size_bytes does not match UTF-8 content")
        if sha256(encoded_content).hexdigest() != digest:
            raise ValueError("sha256 does not match UTF-8 content")

        object.__setattr__(self, "path", path)
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "purpose": self.purpose,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class SourceBundle:
    """Canonical text inputs and resource metadata selected from an inventory."""

    SCHEMA_VERSION: ClassVar[str] = "1"

    inventory: PackageInventory
    manifest: PackageManifest
    examples: tuple[ExampleMetadata, ...] = field(default_factory=tuple)
    text_files: tuple[SourceTextFile, ...] = field(default_factory=tuple)
    resource_files: tuple[ArchiveFile, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.inventory, PackageInventory):
            raise TypeError("inventory must be a PackageInventory")
        if not isinstance(self.manifest, PackageManifest):
            raise TypeError("manifest must be a PackageManifest")

        examples = tuple(self.examples)
        if any(not isinstance(item, ExampleMetadata) for item in examples):
            raise TypeError("examples must contain only ExampleMetadata instances")
        text_files = tuple(self.text_files)
        if any(not isinstance(item, SourceTextFile) for item in text_files):
            raise TypeError("text_files must contain only SourceTextFile instances")
        resource_files = tuple(self.resource_files)
        if any(not isinstance(item, ArchiveFile) for item in resource_files):
            raise TypeError("resource_files must contain only ArchiveFile instances")

        text_paths = [file.path for file in text_files]
        resource_paths = [file.path for file in resource_files]
        if text_paths != sorted(text_paths, key=str.casefold):
            raise ValueError("text_files must use deterministic path ordering")
        if resource_paths != sorted(resource_paths, key=str.casefold):
            raise ValueError("resource_files must use deterministic path ordering")
        if len(text_paths + resource_paths) != len(set(text_paths + resource_paths)):
            raise ValueError("source bundle selects a path more than once")

        inventory_by_path = {file.path: file for file in self.inventory.files}
        for selected in (*text_files, *resource_files):
            inventory_file = inventory_by_path.get(selected.path)
            if inventory_file is None or inventory_file.sha256 != selected.sha256:
                raise ValueError("selected file does not match the package inventory")
        if any(file.role != "resource" for file in resource_files):
            raise ValueError("resource_files must contain only resource-role files")
        if self.manifest.manifest_path not in text_paths:
            raise ValueError("text_files must include the parsed package manifest")

        diagnostics = tuple(
            _required_text(diagnostic, "diagnostic")
            for diagnostic in self.diagnostics
        )
        if self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"unsupported source bundle schema version: {self.schema_version!r}"
            )

        object.__setattr__(self, "examples", examples)
        object.__setattr__(self, "text_files", text_files)
        object.__setattr__(self, "resource_files", resource_files)
        object.__setattr__(self, "diagnostics", diagnostics)

    def files_with_purpose(self, purpose: str) -> tuple[SourceTextFile, ...]:
        normalized_purpose = _required_text(purpose, "purpose")
        if normalized_purpose not in SOURCE_TEXT_PURPOSES:
            raise ValueError(f"unsupported source text purpose: {normalized_purpose!r}")
        return tuple(
            file for file in self.text_files if file.purpose == normalized_purpose
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "inventory": self.inventory.to_dict(),
            "manifest": self.manifest.to_dict(),
            "examples": [example.to_dict() for example in self.examples],
            "text_files": [file.to_dict() for file in self.text_files],
            "resource_files": [file.to_dict() for file in self.resource_files],
            "diagnostics": list(self.diagnostics),
        }
