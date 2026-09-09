"""Load and query the local Click package metadata catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from rusty_ai_converter.models import PackageCatalog, PackageRecord


class CatalogError(Exception):
    """Base class for errors raised while loading a package catalog."""


class CatalogReadError(CatalogError):
    """The metadata file could not be read."""


class CatalogFormatError(CatalogError):
    """The metadata content does not match the expected catalog shape."""


def parse_catalog_data(
    data: Any,
    *,
    source: str = "<memory>",
) -> PackageCatalog:
    """Validate decoded metadata and return a deterministic package catalog.

    The source metadata shape is an object whose keys are category names and
    whose values are arrays of objects containing ``name`` and
    ``download_link``. Package records are sorted so the same input facts
    produce the same catalog regardless of JSON object insertion order.
    """
    if not isinstance(data, Mapping):
        raise CatalogFormatError(f"{source}: top-level value must be an object")

    records: list[PackageRecord] = []

    for category, entries in data.items():
        if not isinstance(category, str) or not category.strip():
            raise CatalogFormatError(f"{source}: category names must be non-empty strings")
        if not isinstance(entries, list):
            raise CatalogFormatError(
                f"{source}: category {category!r} must contain an array"
            )

        for index, entry in enumerate(entries):
            location = f"{source}: category {category!r}, entry {index}"
            try:
                record = PackageRecord.from_metadata(category, entry)
            except (TypeError, ValueError) as error:
                raise CatalogFormatError(f"{location}: {error}") from error
            records.append(record)

    if not records:
        raise CatalogFormatError(f"{source}: catalog must contain at least one package")

    records.sort(
        key=lambda record: (
            record.category.casefold(),
            record.name.casefold(),
            record.download_url,
        )
    )

    try:
        return PackageCatalog.from_records(records)
    except (TypeError, ValueError) as error:
        raise CatalogFormatError(f"{source}: {error}") from error


def load_catalog(path: str | Path) -> PackageCatalog:
    """Read and validate a UTF-8 JSON metadata file from local storage."""
    source_path = Path(path)

    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CatalogReadError(f"could not read catalog {source_path}: {error}") from error

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CatalogFormatError(
            f"{source_path}: invalid JSON at line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error

    return parse_catalog_data(data, source=str(source_path))
