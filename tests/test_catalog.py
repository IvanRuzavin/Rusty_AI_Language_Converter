"""Tests for loading and querying Click package metadata."""

from __future__ import annotations

from pathlib import Path
import unittest

from rusty_ai_converter.catalog import CatalogFormatError, load_catalog, parse_catalog_data


PROJECT_ROOT = Path(__file__).parents[1]
METADATA_PATH = PROJECT_ROOT / "metadata_clicks_c.json"


class RealCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_catalog(METADATA_PATH)

    def test_loads_expected_package_and_category_counts(self) -> None:
        self.assertEqual(len(self.catalog), 1_945)
        self.assertEqual(len(self.catalog.categories), 102)

    def test_finds_ips_display_2(self) -> None:
        matches = self.catalog.search("IPS Display 2")
        exact_matches = [record for record in matches if record.name == "IPS Display 2 Click"]

        self.assertEqual(len(exact_matches), 1)
        self.assertEqual(exact_matches[0].category, "display-and-led")
        self.assertTrue(exact_matches[0].download_url.endswith("mikroe-click-ipsdisplay2.zip"))

    def test_queries_category_with_punctuation_variant(self) -> None:
        packages = self.catalog.in_category("display and led")

        self.assertEqual(len(packages), 40)
        self.assertTrue(all(package.category == "display-and-led" for package in packages))


class CatalogValidationTests(unittest.TestCase):
    def test_rejects_non_object_top_level_value(self) -> None:
        with self.assertRaisesRegex(CatalogFormatError, "top-level value"):
            parse_catalog_data([], source="test-data")

    def test_rejects_non_array_category(self) -> None:
        with self.assertRaisesRegex(CatalogFormatError, "category 'sensors'"):
            parse_catalog_data({"sensors": {}}, source="test-data")

    def test_reports_location_of_invalid_entry(self) -> None:
        with self.assertRaisesRegex(
            CatalogFormatError,
            "category 'sensors', entry 0: download_url must be an absolute HTTPS URL",
        ):
            parse_catalog_data(
                {
                    "sensors": [
                        {
                            "name": "Broken Click",
                            "download_link": "not-a-url",
                        }
                    ]
                },
                source="test-data",
            )

    def test_rejects_empty_catalog(self) -> None:
        with self.assertRaisesRegex(CatalogFormatError, "at least one package"):
            parse_catalog_data({}, source="test-data")


if __name__ == "__main__":
    unittest.main()
