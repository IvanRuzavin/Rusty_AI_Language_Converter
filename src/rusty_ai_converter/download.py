"""Download Click package archives with validation and local caching."""

from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

from rusty_ai_converter.models import CachedArchive, PackageRecord


DEFAULT_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
DOWNLOAD_CHUNK_BYTES = 64 * 1024
USER_AGENT = "RustyAILanguageConverter/0.1"
_INDEX_SCHEMA_VERSION = "1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DownloadError(Exception):
    """A package could not be downloaded or safely cached."""


def _url_cache_key(package: PackageRecord) -> str:
    return sha256(package.download_url.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cached_archive(
    package: PackageRecord,
    cache_dir: Path,
) -> CachedArchive | None:
    index_path = cache_dir / "index" / f"{_url_cache_key(package)}.json"
    try:
        index_data: Any = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(index_data, dict):
        return None
    if index_data.get("schema_version") != _INDEX_SCHEMA_VERSION:
        return None
    if index_data.get("catalog_id") != package.catalog_id:
        return None
    if index_data.get("source_url") != package.download_url:
        return None

    digest = index_data.get("sha256")
    size_bytes = index_data.get("size_bytes")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        return None
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 0
    ):
        return None

    archive_path = cache_dir / "archives" / f"{digest}.zip"
    try:
        if archive_path.stat().st_size != size_bytes:
            return None
        if _file_sha256(archive_path) != digest:
            return None
    except OSError:
        return None

    if not zipfile.is_zipfile(archive_path):
        return None

    return CachedArchive(
        package=package,
        archive_path=archive_path,
        sha256=digest,
        size_bytes=size_bytes,
        cache_hit=True,
    )


def _write_index(
    package: PackageRecord,
    cache_dir: Path,
    digest: str,
    size_bytes: int,
) -> None:
    index_dir = cache_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / f"{_url_cache_key(package)}.json"
    index_data = {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "catalog_id": package.catalog_id,
        "source_url": package.download_url,
        "sha256": digest,
        "size_bytes": size_bytes,
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=index_dir,
            prefix="index-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(index_data, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, index_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def download_package(
    package: PackageRecord,
    cache_dir: str | Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    force: bool = False,
) -> CachedArchive:
    """Download one package through ordinary HTTP and cache it by SHA-256.

    A valid cache entry is returned without network access unless ``force`` is
    true. Downloads are streamed into a temporary file and moved into the
    archive store only after their size, checksum, and ZIP structure have been
    validated.
    """
    if not isinstance(package, PackageRecord):
        raise TypeError("package must be a PackageRecord")
    if not isinstance(max_archive_bytes, int) or isinstance(max_archive_bytes, bool):
        raise TypeError("max_archive_bytes must be an integer")
    if max_archive_bytes <= 0:
        raise ValueError("max_archive_bytes must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    resolved_cache_dir = Path(cache_dir).resolve()
    if not force:
        cached = _load_cached_archive(package, resolved_cache_dir)
        if cached is not None:
            return cached

    archive_dir = resolved_cache_dir / "archives"
    temporary_dir = resolved_cache_dir / "tmp"
    archive_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir.mkdir(parents=True, exist_ok=True)

    request = Request(package.download_url, headers={"User-Agent": USER_AGENT})
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=temporary_dir,
            prefix="download-",
            suffix=".part",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

            try:
                response_context = urlopen(request, timeout=timeout_seconds)
                with closing(response_context) as response:
                    final_url = response.geturl()
                    if urlparse(final_url).scheme != "https":
                        raise DownloadError(
                            f"download redirected to a non-HTTPS URL: {final_url}"
                        )

                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared_size = int(content_length)
                        except ValueError as error:
                            raise DownloadError(
                                f"server returned an invalid Content-Length: {content_length!r}"
                            ) from error
                        if declared_size > max_archive_bytes:
                            raise DownloadError(
                                f"archive is larger than the {max_archive_bytes}-byte limit"
                            )

                    digest = sha256()
                    size_bytes = 0
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        size_bytes += len(chunk)
                        if size_bytes > max_archive_bytes:
                            raise DownloadError(
                                f"archive exceeded the {max_archive_bytes}-byte limit"
                            )
                        digest.update(chunk)
                        temporary_file.write(chunk)
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise DownloadError(f"download failed for {package.name}: {error}") from error

            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if size_bytes == 0:
            raise DownloadError("downloaded archive is empty")
        if not zipfile.is_zipfile(temporary_path):
            raise DownloadError("downloaded content is not a valid ZIP archive")

        digest_text = digest.hexdigest()
        archive_path = archive_dir / f"{digest_text}.zip"
        os.replace(temporary_path, archive_path)
        temporary_path = None
        _write_index(package, resolved_cache_dir, digest_text, size_bytes)

        return CachedArchive(
            package=package,
            archive_path=archive_path,
            sha256=digest_text,
            size_bytes=size_bytes,
            cache_hit=False,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
