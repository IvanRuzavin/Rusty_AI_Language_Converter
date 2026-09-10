"""C-to-Rust conversion tools for MikroE Click packages."""

from rusty_ai_converter.catalog import (
    CatalogError,
    CatalogFormatError,
    CatalogReadError,
    load_catalog,
    parse_catalog_data,
)
from rusty_ai_converter.archive import (
    ArchiveInspectionError,
    classify_archive_path,
    inspect_archive,
)
from rusty_ai_converter.download import (
    DownloadError,
    download_package,
    load_cached_archive,
)
from rusty_ai_converter.models import (
    ARCHIVE_FILE_ROLES,
    SOURCE_TEXT_PURPOSES,
    ArchiveFile,
    CachedArchive,
    ExampleMetadata,
    ManifestLibrary,
    PackageCatalog,
    PackageInventory,
    PackageManifest,
    PackageRecord,
    SourceBundle,
    SourceTextFile,
)
from rusty_ai_converter.source import SourceSelectionError, build_source_bundle
from rusty_ai_converter.parsing import (
    CParserError,
    ClickPackageIR,
    parse_c_file,
    parse_source_bundle,
)

__all__ = [
    "CatalogError",
    "CatalogFormatError",
    "CatalogReadError",
    "ARCHIVE_FILE_ROLES",
    "SOURCE_TEXT_PURPOSES",
    "ArchiveFile",
    "ArchiveInspectionError",
    "CachedArchive",
    "CParserError",
    "ClickPackageIR",
    "DownloadError",
    "ExampleMetadata",
    "ManifestLibrary",
    "PackageCatalog",
    "PackageInventory",
    "PackageManifest",
    "PackageRecord",
    "SourceBundle",
    "SourceSelectionError",
    "SourceTextFile",
    "build_source_bundle",
    "classify_archive_path",
    "download_package",
    "inspect_archive",
    "load_catalog",
    "load_cached_archive",
    "parse_catalog_data",
    "parse_c_file",
    "parse_source_bundle",
]

__version__ = "0.1.0"
