"""Tests for safe, extraction-free Click archive inspection."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from rusty_ai_converter.archive import ArchiveInspectionError, inspect_archive
from rusty_ai_converter.models import CachedArchive, PackageRecord


def cached_archive_for(path: Path) -> CachedArchive:
    content = path.read_bytes()
    package = PackageRecord(
        category="test",
        name="Example Click",
        download_url="https://example.com/example.zip",
    )
    return CachedArchive(
        package=package,
        archive_path=path,
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
        cache_hit=True,
    )


class InspectArchiveTests(unittest.TestCase):
    def test_classifies_files_and_marks_content_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "example.zip"
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("Example/manifest.json", "{}")
                archive.writestr("Example/include/example.h", "void example(void);")
                archive.writestr("Example/src/example.c", "void example(void) {}")
                archive.writestr(
                    "Example/src/example_resources.c", "const char data[] = {0};"
                )
                archive.writestr("Example/examples/main.c", "int main(void) {}")
                archive.writestr("Example/examples/manifest.exm", "example metadata")
                archive.writestr("Example/Makefile", "all:\n")
                archive.writestr("Example/include/Click.Example", "build metadata")
                archive.writestr("Example/README.md", "Example package")
                archive.writestr("Example/help/doc/html/index.html", "generated docs")
                archive.writestr("Example/assets/a-image.bin", b"same resource")
                archive.writestr("Example/assets/z-image-copy.bin", b"same resource")
                archive.writestr("Example/misc.unknown", "miscellaneous")

            inventory = inspect_archive(cached_archive_for(archive_path))

            roles = {file.path: file.role for file in inventory.files}
            self.assertEqual(roles["Example/manifest.json"], "manifest")
            self.assertEqual(roles["Example/include/example.h"], "c_header")
            self.assertEqual(roles["Example/src/example.c"], "c_source")
            self.assertEqual(roles["Example/src/example_resources.c"], "resource")
            self.assertEqual(roles["Example/examples/main.c"], "example_source")
            self.assertEqual(
                roles["Example/examples/manifest.exm"], "example_metadata"
            )
            self.assertEqual(roles["Example/Makefile"], "build_file")
            self.assertEqual(roles["Example/include/Click.Example"], "build_file")
            self.assertEqual(roles["Example/README.md"], "documentation")
            self.assertEqual(
                roles["Example/help/doc/html/index.html"],
                "generated_documentation",
            )
            self.assertEqual(roles["Example/assets/a-image.bin"], "resource")
            self.assertEqual(roles["Example/misc.unknown"], "other")
            self.assertEqual(inventory.common_root, "Example")
            duplicate = next(
                file
                for file in inventory.files
                if file.path == "Example/assets/z-image-copy.bin"
            )
            self.assertEqual(duplicate.duplicate_of, "Example/assets/a-image.bin")
            self.assertTrue(
                any("1 duplicate file(s)" in message for message in inventory.diagnostics)
            )

    def test_rejects_parent_directory_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "traversal.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.c", "unsafe")

            with self.assertRaisesRegex(ArchiveInspectionError, "not safe"):
                inspect_archive(cached_archive_for(archive_path))

    def test_rejects_backslash_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "backslash.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("folder\\outside.c", "unsafe")

            with self.assertRaisesRegex(ArchiveInspectionError, "backslash"):
                inspect_archive(cached_archive_for(archive_path))

    def test_rejects_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "symlink.zip"
            link = zipfile.ZipInfo("Example/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(link, "../outside")

            with self.assertRaisesRegex(ArchiveInspectionError, "Symbolic link|symbolic link"):
                inspect_archive(cached_archive_for(archive_path))

    def test_rejects_case_colliding_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "collision.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Example/Driver.c", "first")
                archive.writestr("Example/driver.c", "second")

            with self.assertRaisesRegex(ArchiveInspectionError, "collide"):
                inspect_archive(cached_archive_for(archive_path))

    def test_enforces_entry_and_expanded_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "limits.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Example/one.c", "12345")
                archive.writestr("Example/two.h", "67890")
            cached = cached_archive_for(archive_path)

            with self.assertRaisesRegex(ArchiveInspectionError, "entry limit"):
                inspect_archive(cached, max_entries=1)
            with self.assertRaisesRegex(ArchiveInspectionError, "expanded-size"):
                inspect_archive(cached, max_expanded_bytes=9)

    def test_rejects_suspicious_compression_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "ratio.zip"
            with zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("Example/zeros.bin", b"\x00" * 10_000)

            with self.assertRaisesRegex(ArchiveInspectionError, "compression-ratio"):
                inspect_archive(
                    cached_archive_for(archive_path),
                    max_compression_ratio=2,
                )

    def test_rejects_archive_that_no_longer_matches_cache_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "changed.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Example/driver.c", "original")
            cached = cached_archive_for(archive_path)
            with archive_path.open("ab") as archive_file:
                archive_file.write(b"changed")

            with self.assertRaisesRegex(ArchiveInspectionError, "size"):
                inspect_archive(cached)


if __name__ == "__main__":
    unittest.main()
