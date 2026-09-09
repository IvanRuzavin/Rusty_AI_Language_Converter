"""C-to-Rust conversion tools for MikroE Click packages."""

from rusty_ai_converter.catalog import (
    CatalogError,
    CatalogFormatError,
    CatalogReadError,
    load_catalog,
    parse_catalog_data,
)
from rusty_ai_converter.download import DownloadError, download_package
from rusty_ai_converter.models import CachedArchive, PackageCatalog, PackageRecord

__all__ = [
    "CatalogError",
    "CatalogFormatError",
    "CatalogReadError",
    "CachedArchive",
    "DownloadError",
    "PackageCatalog",
    "PackageRecord",
    "download_package",
    "load_catalog",
    "parse_catalog_data",
]

__version__ = "0.1.0"
