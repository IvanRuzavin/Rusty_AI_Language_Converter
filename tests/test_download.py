"""Tests for deterministic package downloading and caching."""

from __future__ import annotations

from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from rusty_ai_converter.download import DownloadError, download_package
from rusty_ai_converter.models import PackageRecord


class FakeHttpResponse(BytesIO):
    """Small urllib-compatible response used to keep tests offline."""

    def __init__(
        self,
        content: bytes,
        *,
        final_url: str,
        content_length: str | None = None,
    ) -> None:
        super().__init__(content)
        self._final_url = final_url
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def geturl(self) -> str:
        return self._final_url


def make_zip_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Example Click/manifest.json", "{}")
    return buffer.getvalue()


class DownloadPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = PackageRecord(
            category="test",
            name="Example Click",
            download_url="https://example.com/example.zip",
        )
        self.zip_bytes = make_zip_bytes()

    def test_downloads_valid_zip_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            response = FakeHttpResponse(
                self.zip_bytes,
                final_url=self.package.download_url,
                content_length=str(len(self.zip_bytes)),
            )
            with patch("rusty_ai_converter.download.urlopen", return_value=response) as open_url:
                first = download_package(self.package, temporary_directory)
                second = download_package(self.package, temporary_directory)

            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.archive_path, second.archive_path)
            self.assertEqual(first.archive_path.read_bytes(), self.zip_bytes)
            self.assertEqual(open_url.call_count, 1)

    def test_rejects_declared_archive_larger_than_limit(self) -> None:
        response = FakeHttpResponse(
            self.zip_bytes,
            final_url=self.package.download_url,
            content_length=str(len(self.zip_bytes) + 1),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("rusty_ai_converter.download.urlopen", return_value=response):
                with self.assertRaisesRegex(DownloadError, "larger than"):
                    download_package(
                        self.package,
                        temporary_directory,
                        max_archive_bytes=len(self.zip_bytes),
                    )

    def test_rejects_stream_that_exceeds_limit(self) -> None:
        response = FakeHttpResponse(
            self.zip_bytes,
            final_url=self.package.download_url,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("rusty_ai_converter.download.urlopen", return_value=response):
                with self.assertRaisesRegex(DownloadError, "exceeded"):
                    download_package(
                        self.package,
                        temporary_directory,
                        max_archive_bytes=len(self.zip_bytes) - 1,
                    )

    def test_rejects_non_zip_response(self) -> None:
        response = FakeHttpResponse(
            b"not a zip archive",
            final_url=self.package.download_url,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("rusty_ai_converter.download.urlopen", return_value=response):
                with self.assertRaisesRegex(DownloadError, "not a valid ZIP"):
                    download_package(self.package, temporary_directory)

    def test_rejects_redirect_to_non_https_url(self) -> None:
        response = FakeHttpResponse(
            self.zip_bytes,
            final_url="http://example.com/example.zip",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("rusty_ai_converter.download.urlopen", return_value=response):
                with self.assertRaisesRegex(DownloadError, "non-HTTPS"):
                    download_package(self.package, temporary_directory)

    def test_force_download_bypasses_valid_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            responses = [
                FakeHttpResponse(self.zip_bytes, final_url=self.package.download_url),
                FakeHttpResponse(self.zip_bytes, final_url=self.package.download_url),
            ]
            with patch("rusty_ai_converter.download.urlopen", side_effect=responses) as open_url:
                download_package(self.package, temporary_directory)
                forced = download_package(self.package, temporary_directory, force=True)

            self.assertFalse(forced.cache_hit)
            self.assertEqual(open_url.call_count, 2)

    def test_ignores_cache_index_with_unsafe_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            index_dir = cache_dir / "index"
            index_dir.mkdir(parents=True)
            url_key = sha256(self.package.download_url.encode("utf-8")).hexdigest()
            index_path = index_dir / f"{url_key}.json"
            index_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "catalog_id": self.package.catalog_id,
                        "source_url": self.package.download_url,
                        "sha256": "../" * 21 + "x",
                        "size_bytes": 1,
                    }
                ),
                encoding="utf-8",
            )
            response = FakeHttpResponse(
                self.zip_bytes,
                final_url=self.package.download_url,
            )

            with patch(
                "rusty_ai_converter.download.urlopen", return_value=response
            ) as open_url:
                result = download_package(self.package, cache_dir)

            self.assertFalse(result.cache_hit)
            self.assertEqual(open_url.call_count, 1)


if __name__ == "__main__":
    unittest.main()
