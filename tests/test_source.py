"""Tests for manifest-driven, selective source loading."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from rusty_ai_converter.archive import inspect_archive
from rusty_ai_converter.models import CachedArchive, PackageRecord
from rusty_ai_converter.source import SourceSelectionError, build_source_bundle


def cached_archive_for(path: Path) -> CachedArchive:
    content = path.read_bytes()
    return CachedArchive(
        package=PackageRecord(
            category="test",
            name="Example Click",
            download_url="https://example.com/example.zip",
        ),
        archive_path=path,
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
        cache_hit=True,
    )


def package_manifest(*, library_path: str = "Example Click/data/lib_example") -> str:
    return json.dumps(
        {
            "display_name": "Example Click",
            "name": "mikroe.click.example",
            "type": "click",
            "version": "1.2.3",
            "pid": "MIKROE-0000",
            "contents": {
                "libraries": [
                    {
                        "alias": "Click.Example",
                        "subdir_name": "lib_example",
                        "path": library_path,
                    }
                ],
                "examples": [{"path": "Example Click/examples/example"}],
            },
        }
    )


def write_complete_package(
    path: Path,
    *,
    manifest_content: str | None = None,
    library_cmake: str | None = None,
    extra_entries: dict[str, str | bytes] | None = None,
) -> None:
    driver_source = "void example(void) {}\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "Example Click/manifest.json",
            manifest_content or package_manifest(),
        )
        archive.writestr("Example Click/README.md", "# Example Click\n")
        archive.writestr(
            "Example Click/data/lib_example/CMakeLists.txt",
            library_cmake
            or """add_library(lib_example STATIC
src/example.c
include/example.h
src/example_resources.c
src/startup.S
)
""",
        )
        archive.writestr(
            "Example Click/data/lib_example/include/Click.Example",
            '#include "example.h"\n',
        )
        archive.writestr(
            "Example Click/data/lib_example/include/example.h",
            "void example(void);\n",
        )
        archive.writestr(
            "Example Click/data/lib_example/src/example.c",
            driver_source,
        )
        archive.writestr(
            "Example Click/data/lib_example/src/example_resources.c",
            "const unsigned char image[] = { 1, 2, 3 };\n",
        )
        archive.writestr(
            "Example Click/data/lib_example/src/startup.S",
            ".global example_startup\n",
        )
        archive.writestr(
            "Example Click/examples/example/CMakeLists.txt",
            "add_executable(example_app main.c)\n",
        )
        archive.writestr(
            "Example Click/examples/example/main.c",
            "int main(void) { return 0; }\n",
        )
        archive.writestr(
            "Example Click/examples/example/manifest.exm",
            json.dumps(
                {
                    "name": "Example Click",
                    "toolchain": ["gcc-arm"],
                    "hw": ["Click", "EXAMPLE-DEVICE"],
                }
            ),
        )
        archive.writestr(
            "Example Click/examples/example/lib_example/src/example.c",
            driver_source,
        )
        archive.writestr(
            "Example Click/help/doc/html/index.html",
            "generated documentation",
        )
        for entry_path, content in (extra_entries or {}).items():
            archive.writestr(entry_path, content)


class BuildSourceBundleTests(unittest.TestCase):
    def test_selects_manifest_declared_sources_without_extracting_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "package.zip"
            write_complete_package(archive_path)
            inventory = inspect_archive(cached_archive_for(archive_path))

            bundle = build_source_bundle(inventory)

            purposes = {file.path: file.purpose for file in bundle.text_files}
            self.assertEqual(bundle.manifest.version, "1.2.3")
            self.assertEqual(bundle.manifest.product_id, "MIKROE-0000")
            self.assertEqual(bundle.examples[0].toolchains, ("gcc-arm",))
            self.assertEqual(
                purposes["Example Click/data/lib_example/src/example.c"],
                "driver_source",
            )
            self.assertEqual(
                purposes["Example Click/data/lib_example/include/example.h"],
                "driver_header",
            )
            self.assertEqual(
                purposes["Example Click/examples/example/main.c"],
                "example_source",
            )
            self.assertEqual(
                purposes["Example Click/data/lib_example/src/startup.S"],
                "driver_assembly",
            )
            self.assertNotIn(
                "Example Click/examples/example/lib_example/src/example.c",
                purposes,
            )
            self.assertEqual(
                [file.path for file in bundle.resource_files],
                ["Example Click/data/lib_example/src/example_resources.c"],
            )
            self.assertTrue(
                any("generated documentation" in item for item in bundle.diagnostics)
            )

    def test_rejects_unsafe_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "unsafe-manifest.zip"
            write_complete_package(
                archive_path,
                manifest_content=package_manifest(library_path="../outside"),
            )

            with self.assertRaisesRegex(SourceSelectionError, "safe archive directory"):
                build_source_bundle(inspect_archive(cached_archive_for(archive_path)))

    def test_rejects_non_utf8_selected_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "non-utf8.zip"
            write_complete_package(
                archive_path,
                library_cmake="add_library(lib_example STATIC src/bad.c)\n",
                extra_entries={
                    "Example Click/data/lib_example/src/bad.c": b"\xff\xfe",
                },
            )

            inventory = inspect_archive(cached_archive_for(archive_path))
            with self.assertRaisesRegex(SourceSelectionError, "not valid UTF-8"):
                build_source_bundle(inventory)

    def test_enforces_total_selected_text_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "limit.zip"
            write_complete_package(archive_path)
            inventory = inspect_archive(cached_archive_for(archive_path))

            with self.assertRaisesRegex(SourceSelectionError, "total byte limit"):
                build_source_bundle(inventory, max_selected_text_bytes=100)

    def test_rechecks_archive_hash_after_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "changed.zip"
            write_complete_package(archive_path)
            inventory = inspect_archive(cached_archive_for(archive_path))
            with archive_path.open("ab") as archive_file:
                archive_file.write(b"changed")

            with self.assertRaisesRegex(SourceSelectionError, "size changed"):
                build_source_bundle(inventory)


if __name__ == "__main__":
    unittest.main()
