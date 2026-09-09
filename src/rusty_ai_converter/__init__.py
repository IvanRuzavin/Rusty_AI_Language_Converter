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
    ArchiveFile,
    CachedArchive,
    PackageCatalog,
    PackageInventory,
    PackageRecord,
)

__all__ = [
    "CatalogError",
    "CatalogFormatError",
    "CatalogReadError",
    "ARCHIVE_FILE_ROLES",
    "ArchiveFile",
    "ArchiveInspectionError",
    "CachedArchive",
    "DownloadError",
    "PackageCatalog",
    "PackageInventory",
    "PackageRecord",
    "classify_archive_path",
    "download_package",
    "inspect_archive",
    "load_catalog",
    "load_cached_archive",
    "parse_catalog_data",
]

__version__ = "0.1.0"
