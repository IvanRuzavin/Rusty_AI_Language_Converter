"""Tests for the converter's shared data models."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from rusty_ai_converter.models import CachedArchive, PackageCatalog, PackageRecord


PROJECT_ROOT = Path(__file__).parents[1]


class PackageRecordTests(unittest.TestCase):
    def test_builds_normalized_record_from_metadata(self) -> None:
        record = PackageRecord.from_metadata(
            "display-and-led",
            {
                "name": "  IPS   Display 2 Click  ",
                "download_link": (
                    "https://dbp-cdn.mikroe.com/catalog/ide/packages/example/"
                    "mikroe-click-ipsdisplay2.zip"
                ),
            },
        )

        self.assertEqual(record.name, "IPS Display 2 Click")
        self.assertEqual(record.normalized_name, "ips-display-2")
        self.assertTrue(record.catalog_id.startswith("display-and-led/ips-display-2-"))
        self.assertEqual(len(record.catalog_id.rsplit("-", 1)[1]), 16)

    def test_rejects_non_https_download_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTPS URL"):
            PackageRecord(
                category="sensor",
                name="Example Click",
                download_url="http://example.com/example.zip",
            )

    def test_rejects_non_zip_download_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "ZIP archive"):
            PackageRecord(
                category="sensor",
                name="Example Click",
                download_url="https://example.com/example.tar.gz",
            )


class PackageCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = PackageRecord(
            category="sensor",
            name="Example Click",
            download_url="https://example.com/example.zip",
        )

    def test_materializes_iterable_and_serializes(self) -> None:
        catalog = PackageCatalog.from_records(record for record in [self.record])

        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog.to_dict()["schema_version"], "1")
        self.assertEqual(catalog.to_dict()["packages"], [self.record.to_dict()])

    def test_rejects_duplicate_records(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            PackageCatalog(packages=(self.record, self.record))

    def test_json_schemas_are_valid_json_and_use_closed_objects(self) -> None:
        for schema_name in (
            "package-record.schema.json",
            "package-catalog.schema.json",
            "cached-archive.schema.json",
            "archive-file.schema.json",
            "package-inventory.schema.json",
            "source-bundle.schema.json",
            "click-package-ir.schema.json",
        ):
            schema_path = PROJECT_ROOT / "schemas" / schema_name
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])


class CachedArchiveTests(unittest.TestCase):
    def test_serializes_archive_metadata_without_archive_bytes(self) -> None:
        package = PackageRecord(
            category="sensor",
            name="Example Click",
            download_url="https://example.com/example.zip",
        )
        archive = CachedArchive(
            package=package,
            archive_path=Path(".cache/archives/example.zip"),
            sha256="a" * 64,
            size_bytes=123,
            cache_hit=False,
        )

        serialized = archive.to_dict()
        self.assertEqual(serialized["package"], package.to_dict())
        self.assertEqual(serialized["size_bytes"], 123)
        self.assertNotIn("content", serialized)

    def test_rejects_invalid_sha256(self) -> None:
        package = PackageRecord(
            category="sensor",
            name="Example Click",
            download_url="https://example.com/example.zip",
        )
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            CachedArchive(
                package=package,
                archive_path=Path("archive.zip"),
                sha256="not-a-hash",
                size_bytes=123,
                cache_hit=False,
            )


if __name__ == "__main__":
    unittest.main()
