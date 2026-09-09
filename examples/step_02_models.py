"""Interactive demonstration of the Step 2 catalog data models."""

from __future__ import annotations

import json

from rusty_ai_converter import PackageCatalog, PackageRecord


def main() -> None:
    """Create, validate, and serialize one example package record."""
    record = PackageRecord.from_metadata(
        category="display-and-led",
        value={
            "name": "  IPS   Display 2 Click  ",
            "download_link": (
                "https://dbp-cdn.mikroe.com/catalog/ide/packages/example/"
                "mikroe-click-ipsdisplay2.zip"
            ),
        },
    )
    catalog = PackageCatalog.from_records([record])

    print("Validated package:")
    print(json.dumps(catalog.to_dict(), indent=2))

    print("\nValidation example:")
    try:
        PackageRecord(
            category="display-and-led",
            name="Broken Click",
            download_url="http://example.com/not-a-zip.txt",
        )
    except ValueError as error:
        print(f"Rejected invalid metadata: {error}")


if __name__ == "__main__":
    main()
