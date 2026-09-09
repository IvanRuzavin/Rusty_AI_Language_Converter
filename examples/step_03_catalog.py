"""Load and search the real Click package metadata catalog."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from rusty_ai_converter import load_catalog


PROJECT_ROOT = Path(__file__).parents[1]
DEFAULT_METADATA_PATH = PROJECT_ROOT / "metadata_clicks_c.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query",
        nargs="?",
        default="IPS Display 2",
        help="Click name or partial name to search for",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA_PATH,
        help="path to metadata_clicks_c.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = load_catalog(args.metadata)

    category_sizes = Counter(package.category for package in catalog.packages)
    largest_categories = sorted(
        category_sizes.items(),
        key=lambda item: (-item[1], item[0]),
    )[:5]

    print(f"Loaded {len(catalog):,} packages in {len(catalog.categories)} categories.")
    print("Largest categories:")
    for category, package_count in largest_categories:
        print(f"  {category}: {package_count}")

    matches = catalog.search(args.query)
    print(f"\nSearch results for {args.query!r}: {len(matches)}")
    for package in matches[:10]:
        print(f"  {package.name} [{package.category}]")
        print(f"    ID:  {package.catalog_id}")
        print(f"    URL: {package.download_url}")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")


if __name__ == "__main__":
    main()
