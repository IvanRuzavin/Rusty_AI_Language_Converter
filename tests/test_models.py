"""Tests for the converter's shared data models."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from rusty_ai_converter.models import PackageCatalog, PackageRecord


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
        ):
            schema_path = PROJECT_ROOT / "schemas" / schema_name
            schema = json.loads(schema_path.read_text(encoding="utf-8"))

            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
