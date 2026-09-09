"""
Database helpers for system-library generation.

The generator uses NECTO's DeviceDetails.reference_manual_url relationship to
fan a family-level clock implementation out to the exact MCU UIDs that share a
reference manual.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


DEFAULT_DATABASE_ARCHIVE_URL = (
    "https://github.com/MikroElektronika/core_packages/"
    "releases/download/v2.0.0/database.7z"
)


@dataclass
class TargetDeviceSet:
    """Resolved set of MCU UIDs that use one reference manual."""

    reference_manual_url: str
    uids: list[str]
    match_reason: str
    rows: list[dict[str, Any]] = field(default_factory=list)


def _normalise_name(value: str | None) -> str:
    if not value:
        return ""
    value = unquote(value)
    value = os.path.basename(urlparse(value).path or value)
    value = value.casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def _download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NECTO-system-library-generator/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    return destination


def _extract_7z(archive_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            archive.extractall(path=output_dir)
        return
    except ImportError:
        pass

    executable = shutil.which("7zz") or shutil.which("7z")
    if executable:
        subprocess.run(
            [executable, "x", "-y", f"-o{output_dir}", str(archive_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return

    raise RuntimeError(
        "Cannot extract database.7z. Install py7zr or make 7z/7zz available."
    )


def _find_necto_db(root: Path) -> Path:
    candidates = sorted(root.rglob("necto_db.db"))
    if not candidates:
        raise FileNotFoundError("necto_db.db was not found in the extracted database archive.")
    return candidates[0]


def ensure_necto_database(
    *,
    cache_dir: str | Path,
    database_path: str | Path | None = None,
    database_archive_path: str | Path | None = None,
    database_archive_url: str = DEFAULT_DATABASE_ARCHIVE_URL,
) -> Path:
    """
    Return a usable necto_db.db path.

    Resolution order:
      1. Explicit database_path.
      2. Explicit local database archive.
      3. Cached extracted database.
      4. Download database_archive_url and extract it.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    if database_path:
        path = Path(database_path)
        if not path.is_file():
            raise FileNotFoundError(f"Database not found: {path}")
        return path

    extracted_root = cache / "database"
    cached_db = extracted_root / "necto_db.db"
    if cached_db.is_file():
        return cached_db

    if database_archive_path:
        archive = Path(database_archive_path)
        if not archive.is_file():
            raise FileNotFoundError(f"Database archive not found: {archive}")
    else:
        archive = cache / "database.7z"
        if not archive.is_file():
            _download(database_archive_url, archive)

    if extracted_root.exists():
        shutil.rmtree(extracted_root)
    _extract_7z(archive, extracted_root)

    found = _find_necto_db(extracted_root)
    if found != cached_db:
        cached_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, cached_db)
    return cached_db


def _load_device_rows(db_path: Path) -> tuple[list[dict[str, Any]], str, str]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        table_info = connection.execute("PRAGMA table_info(DeviceDetails)").fetchall()
        if not table_info:
            raise RuntimeError("DeviceDetails table was not found in necto_db.db.")

        columns = [str(row[1]) for row in table_info]
        by_lower = {column.casefold(): column for column in columns}

        uid_col = by_lower.get("uid")
        manual_col = by_lower.get("reference_manual_url")
        if uid_col is None or manual_col is None:
            raise RuntimeError(
                "DeviceDetails must contain uid and reference_manual_url columns. "
                f"Available columns: {columns}"
            )

        quoted_columns = ", ".join(f'"{name}"' for name in columns)
        rows = [dict(row) for row in connection.execute(
            f'SELECT {quoted_columns} FROM "DeviceDetails"'  # nosec B608
        ).fetchall()]
        return rows, uid_col, manual_col
    finally:
        connection.close()


def _prefix_matches(uid: str, prefixes: Iterable[str]) -> bool:
    uid_norm = re.sub(r"[^A-Za-z0-9]", "", uid).upper()
    for prefix in prefixes:
        prefix_norm = re.sub(r"[^A-Za-z0-9]", "", prefix).upper()
        if prefix_norm and uid_norm.startswith(prefix_norm):
            return True
    return False


def resolve_target_devices(
    db_path: str | Path,
    *,
    target_manual_filename: str,
    uid_prefixes: Iterable[str] = (),
    explicit_reference_manual_url: str | None = None,
) -> TargetDeviceSet:
    """
    Resolve all DeviceDetails rows belonging to the target reference manual.

    Exact reference_manual_url wins. Otherwise the local PDF basename is matched
    against URL basenames. MCU prefixes extracted from the target manual are used
    as a fallback/disambiguator. A fuzzy basename match is used only when it is
    unambiguous; ambiguous results fail rather than guessing.
    """
    rows, uid_col, manual_col = _load_device_rows(Path(db_path))

    usable = [
        row for row in rows
        if row.get(uid_col) and row.get(manual_col)
    ]
    if not usable:
        raise RuntimeError("DeviceDetails contains no rows with uid/reference_manual_url.")

    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        by_url[str(row[manual_col])].append(row)

    if explicit_reference_manual_url:
        exact = by_url.get(explicit_reference_manual_url)
        if not exact:
            raise RuntimeError(
                "No DeviceDetails rows use the explicitly supplied reference_manual_url: "
                f"{explicit_reference_manual_url}"
            )
        return TargetDeviceSet(
            reference_manual_url=explicit_reference_manual_url,
            uids=sorted({str(row[uid_col]) for row in exact}),
            match_reason="explicit reference_manual_url",
            rows=exact,
        )

    target_name = _normalise_name(target_manual_filename)
    exact_urls = [url for url in by_url if _normalise_name(url) == target_name]

    prefixes = [prefix for prefix in uid_prefixes if prefix]
    if exact_urls:
        if len(exact_urls) == 1:
            selected = exact_urls[0]
        else:
            # Prefer the URL whose MCU rows agree with the family prefixes.
            scores = []
            for url in exact_urls:
                prefix_count = sum(
                    _prefix_matches(str(row[uid_col]), prefixes)
                    for row in by_url[url]
                ) if prefixes else 0
                scores.append((prefix_count, len(by_url[url]), url))
            scores.sort(reverse=True)
            if len(scores) > 1 and scores[0][:2] == scores[1][:2]:
                raise RuntimeError(
                    "Multiple reference_manual_url values have the same target PDF basename; "
                    "provide explicit_reference_manual_url."
                )
            selected = scores[0][2]

        selected_rows = by_url[selected]
        return TargetDeviceSet(
            reference_manual_url=selected,
            uids=sorted({str(row[uid_col]) for row in selected_rows}),
            match_reason="reference manual basename",
            rows=selected_rows,
        )

    if prefixes:
        prefix_rows = [
            row for row in usable
            if _prefix_matches(str(row[uid_col]), prefixes)
        ]
        if prefix_rows:
            counts = Counter(str(row[manual_col]) for row in prefix_rows)
            ranked = counts.most_common()
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
                selected = ranked[0][0]
                selected_rows = by_url[selected]
                return TargetDeviceSet(
                    reference_manual_url=selected,
                    uids=sorted({str(row[uid_col]) for row in selected_rows}),
                    match_reason="target manual MCU prefix",
                    rows=selected_rows,
                )

    # Final fallback: fuzzy URL-basename match. Require a clear margin.
    ranked_urls = sorted(
        (
            SequenceMatcher(None, target_name, _normalise_name(url)).ratio(),
            url,
        )
        for url in by_url
    )
    ranked_urls.reverse()

    if not ranked_urls or ranked_urls[0][0] < 0.72:
        raise RuntimeError(
            "Could not correlate the target PDF with DeviceDetails.reference_manual_url. "
            "Provide an explicit reference_manual_url or a target MCU family prefix."
        )
    if len(ranked_urls) > 1 and ranked_urls[0][0] - ranked_urls[1][0] < 0.05:
        raise RuntimeError(
            "Reference-manual URL matching is ambiguous. Provide explicit_reference_manual_url."
        )

    selected = ranked_urls[0][1]
    selected_rows = by_url[selected]
    return TargetDeviceSet(
        reference_manual_url=selected,
        uids=sorted({str(row[uid_col]) for row in selected_rows}),
        match_reason=f"fuzzy reference manual basename ({ranked_urls[0][0]:.2f})",
        rows=selected_rows,
    )
