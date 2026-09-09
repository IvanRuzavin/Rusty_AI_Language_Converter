"""Select canonical Click sources from a validated package inventory."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import posixpath
import re
import shlex
from typing import Any, Iterable
import zipfile

from rusty_ai_converter.models import (
    ArchiveFile,
    ExampleMetadata,
    ManifestLibrary,
    PackageInventory,
    PackageManifest,
    SourceBundle,
    SourceTextFile,
)


DEFAULT_MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_SELECTED_TEXT_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
_SOURCE_SUFFIXES = frozenset(
    {".asm", ".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".s"}
)
_CMAKE_SOURCE_COMMANDS = frozenset({"add_library", "add_executable"})
_CMAKE_NON_SOURCE_TOKENS = frozenset(
    {
        "alias",
        "exclude_from_all",
        "imported",
        "interface",
        "macosx_bundle",
        "module",
        "object",
        "shared",
        "static",
        "unknown",
        "win32",
    }
)


class SourceSelectionError(Exception):
    """Canonical source inputs could not be selected without ambiguity."""


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceSelectionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SourceSelectionError(f"{field_name} must be an array")
    return tuple(
        _required_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


def _parse_json_object(content: str, path: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(content.removeprefix("\ufeff"))
    except json.JSONDecodeError as error:
        raise SourceSelectionError(
            f"invalid JSON in {path} at line {error.lineno}, column {error.colno}"
        ) from error
    if not isinstance(value, dict):
        raise SourceSelectionError(f"JSON document must be an object: {path}")
    return value


def _is_within(path: str, directory: str) -> bool:
    return path.startswith(f"{directory}/")


def _resolve_manifest_directory(
    raw_path: Any,
    inventory: PackageInventory,
    field_name: str,
) -> str:
    path = _required_string(raw_path, field_name).rstrip("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or re.match(r"^[A-Za-z]:", path)
    ):
        raise SourceSelectionError(f"{field_name} is not a safe archive directory")

    candidates = [path]
    if inventory.common_root is not None and not path.startswith(
        f"{inventory.common_root}/"
    ):
        candidates.append(f"{inventory.common_root}/{path}")

    matches = [
        candidate
        for candidate in candidates
        if any(_is_within(file.path, candidate) for file in inventory.files)
    ]
    if len(matches) != 1:
        raise SourceSelectionError(
            f"{field_name} does not identify exactly one archive directory: {path!r}"
        )
    return matches[0]


def _read_text_member(
    archive: zipfile.ZipFile,
    archive_file: ArchiveFile,
    *,
    max_bytes: int,
    purpose: str,
) -> SourceTextFile:
    if archive_file.size_bytes > max_bytes:
        raise SourceSelectionError(
            f"selected text file exceeds the {max_bytes}-byte limit: {archive_file.path}"
        )

    digest = sha256()
    content = bytearray()
    try:
        with archive.open(archive_file.path, "r") as member:
            while True:
                chunk = member.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                content.extend(chunk)
                digest.update(chunk)
                if len(content) > max_bytes:
                    raise SourceSelectionError(
                        f"selected text file exceeded its size limit: {archive_file.path}"
                    )
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise SourceSelectionError(
            f"cannot read selected archive member {archive_file.path}: {error}"
        ) from error

    if len(content) != archive_file.size_bytes:
        raise SourceSelectionError(
            f"selected member size changed after inventory: {archive_file.path}"
        )
    if digest.hexdigest() != archive_file.sha256:
        raise SourceSelectionError(
            f"selected member hash changed after inventory: {archive_file.path}"
        )
    try:
        decoded = bytes(content).decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceSelectionError(
            f"selected text file is not valid UTF-8: {archive_file.path}"
        ) from error

    return SourceTextFile(
        path=archive_file.path,
        purpose=purpose,
        sha256=archive_file.sha256,
        size_bytes=archive_file.size_bytes,
        content=decoded,
    )


def _cmake_call_bodies(content: str) -> Iterable[tuple[str, str]]:
    command_re = re.compile(r"\b(add_library|add_executable)\s*\(", re.IGNORECASE)
    position = 0
    while match := command_re.search(content, position):
        depth = 1
        index = match.end()
        body_start = index
        quote: str | None = None
        escaped = False
        in_comment = False
        while index < len(content) and depth:
            character = content[index]
            if in_comment:
                if character == "\n":
                    in_comment = False
            elif escaped:
                escaped = False
            elif character == "\\" and quote is not None:
                escaped = True
            elif quote is not None:
                if character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
            elif character == "#":
                in_comment = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            index += 1
        if depth:
            raise SourceSelectionError(
                f"unterminated {match.group(1)} call in CMake metadata"
            )
        yield match.group(1).casefold(), content[body_start : index - 1]
        position = index


def _cmake_tokens(body: str) -> tuple[str, ...]:
    try:
        lexer = shlex.shlex(body, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        raw_tokens = tuple(lexer)
    except ValueError as error:
        raise SourceSelectionError(f"cannot tokenize CMake metadata: {error}") from error

    tokens: list[str] = []
    for token in raw_tokens:
        tokens.extend(part for part in token.split(";") if part)
    return tuple(tokens)


def _declared_cmake_sources(content: str, cmake_path: str) -> tuple[str, ...]:
    declared: set[str] = set()
    cmake_directory = str(PurePosixPath(cmake_path).parent)
    for command, body in _cmake_call_bodies(content):
        if command not in _CMAKE_SOURCE_COMMANDS:
            continue
        tokens = _cmake_tokens(body)
        if len(tokens) < 2 or tokens[1].casefold() == "alias":
            continue
        for token in tokens[1:]:
            token_casefolded = token.casefold()
            suffix = PurePosixPath(token_casefolded).suffix
            if token_casefolded in _CMAKE_NON_SOURCE_TOKENS:
                continue
            if "$" in token or suffix not in _SOURCE_SUFFIXES:
                continue
            if token.startswith("/") or "\\" in token:
                raise SourceSelectionError(
                    f"CMake source path is not relative in {cmake_path}: {token!r}"
                )
            resolved = posixpath.normpath(posixpath.join(cmake_directory, token))
            if resolved == ".." or resolved.startswith("../"):
                raise SourceSelectionError(
                    f"CMake source path escapes the archive root: {token!r}"
                )
            declared.add(resolved)
    return tuple(sorted(declared, key=str.casefold))


def _parse_package_manifest(
    content: str,
    manifest_path: str,
    inventory: PackageInventory,
) -> PackageManifest:
    data = _parse_json_object(content, manifest_path)
    contents = data.get("contents")
    if not isinstance(contents, dict):
        raise SourceSelectionError("package manifest contents must be an object")

    raw_libraries = contents.get("libraries")
    if not isinstance(raw_libraries, list) or not raw_libraries:
        raise SourceSelectionError("package manifest must declare at least one library")
    libraries: list[ManifestLibrary] = []
    for index, raw_library in enumerate(raw_libraries):
        if not isinstance(raw_library, dict):
            raise SourceSelectionError(f"contents.libraries[{index}] must be an object")
        path = _resolve_manifest_directory(
            raw_library.get("path"),
            inventory,
            f"contents.libraries[{index}].path",
        )
        subdir_name = raw_library.get("subdir_name") or PurePosixPath(path).name
        alias = raw_library.get("alias") or subdir_name
        libraries.append(
            ManifestLibrary(
                alias=_required_string(alias, f"contents.libraries[{index}].alias"),
                subdir_name=_required_string(
                    subdir_name,
                    f"contents.libraries[{index}].subdir_name",
                ),
                path=path,
            )
        )

    raw_examples = contents.get("examples", [])
    if not isinstance(raw_examples, list):
        raise SourceSelectionError("package manifest contents.examples must be an array")
    example_paths: list[str] = []
    for index, raw_example in enumerate(raw_examples):
        if not isinstance(raw_example, dict):
            raise SourceSelectionError(f"contents.examples[{index}] must be an object")
        example_paths.append(
            _resolve_manifest_directory(
                raw_example.get("path"),
                inventory,
                f"contents.examples[{index}].path",
            )
        )

    product_id = data.get("pid")
    if product_id is not None:
        product_id = _required_string(product_id, "pid")
    return PackageManifest(
        manifest_path=manifest_path,
        display_name=_required_string(data.get("display_name"), "display_name"),
        package_name=_required_string(data.get("name"), "name"),
        package_type=_required_string(data.get("type"), "type"),
        version=_required_string(data.get("version"), "version"),
        product_id=product_id,
        libraries=tuple(sorted(libraries, key=lambda item: item.path.casefold())),
        example_paths=tuple(sorted(example_paths, key=str.casefold)),
    )


def _parse_example_metadata(content: str, path: str) -> ExampleMetadata:
    data = _parse_json_object(content, path)
    return ExampleMetadata(
        path=path,
        name=_required_string(data.get("name"), "example name"),
        toolchains=_string_list(data.get("toolchain"), "toolchain"),
        hardware=_string_list(data.get("hw"), "hw"),
    )


def _direct_file(path: str, directory: str) -> bool:
    return str(PurePosixPath(path).parent) == directory


def build_source_bundle(
    inventory: PackageInventory,
    *,
    max_text_file_bytes: int = DEFAULT_MAX_TEXT_FILE_BYTES,
    max_selected_text_bytes: int = DEFAULT_MAX_SELECTED_TEXT_BYTES,
) -> SourceBundle:
    """Parse manifests and load only canonical UTF-8 text from an inventory."""
    if not isinstance(inventory, PackageInventory):
        raise TypeError("inventory must be a PackageInventory")
    for field_name, value in (
        ("max_text_file_bytes", max_text_file_bytes),
        ("max_selected_text_bytes", max_selected_text_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{field_name} must be an integer")
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")

    archive_path = inventory.archive.archive_path.resolve()
    archive_digest = sha256()
    try:
        actual_archive_size = archive_path.stat().st_size
        with archive_path.open("rb") as archive_file:
            for chunk in iter(lambda: archive_file.read(READ_CHUNK_BYTES), b""):
                archive_digest.update(chunk)
    except OSError as error:
        raise SourceSelectionError(f"cannot read cached archive: {error}") from error
    if actual_archive_size != inventory.archive.size_bytes:
        raise SourceSelectionError("cached archive size changed after inventory")
    if archive_digest.hexdigest() != inventory.archive.sha256:
        raise SourceSelectionError("cached archive hash changed after inventory")

    files_by_path = {file.path: file for file in inventory.files}
    manifest_files = [
        file
        for file in inventory.files
        if file.role == "manifest" and file.duplicate_of is None
    ]
    if len(manifest_files) != 1:
        raise SourceSelectionError(
            f"expected exactly one canonical package manifest, found {len(manifest_files)}"
        )

    selected: dict[str, SourceTextFile] = {}
    resource_files: dict[str, ArchiveFile] = {}
    example_metadata: list[ExampleMetadata] = []
    diagnostics: list[str] = []
    total_selected_bytes = 0

    def read_and_select(
        archive: zipfile.ZipFile,
        archive_file: ArchiveFile,
        purpose: str,
    ) -> SourceTextFile:
        nonlocal total_selected_bytes
        existing = selected.get(archive_file.path)
        if existing is not None:
            if existing.purpose != purpose:
                raise SourceSelectionError(
                    f"selected file has conflicting purposes: {archive_file.path}"
                )
            return existing
        if total_selected_bytes + archive_file.size_bytes > max_selected_text_bytes:
            raise SourceSelectionError(
                "selected source text exceeds the total byte limit"
            )
        text_file = _read_text_member(
            archive,
            archive_file,
            max_bytes=max_text_file_bytes,
            purpose=purpose,
        )
        selected[archive_file.path] = text_file
        total_selected_bytes += archive_file.size_bytes
        return text_file

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            raw_manifest = read_and_select(
                archive,
                manifest_files[0],
                "package_manifest",
            )
            manifest = _parse_package_manifest(
                raw_manifest.content,
                raw_manifest.path,
                inventory,
            )

            if (
                manifest.display_name.casefold()
                != inventory.archive.package.name.casefold()
            ):
                diagnostics.append(
                    "catalog name and package manifest display_name do not match"
                )
            if manifest.package_type.casefold() != "click":
                diagnostics.append(
                    f"package manifest type is {manifest.package_type!r}, not 'click'"
                )

            for library in manifest.libraries:
                library_files = [
                    file
                    for file in inventory.files
                    if _is_within(file.path, library.path)
                ]
                cmake_files = [
                    file
                    for file in library_files
                    if _direct_file(file.path, library.path)
                    and PurePosixPath(file.path).name.casefold() == "cmakelists.txt"
                ]
                declared_paths: set[str] = set()
                for cmake_file in cmake_files:
                    cmake_text = read_and_select(
                        archive,
                        cmake_file,
                        "build_metadata",
                    )
                    declared_paths.update(
                        _declared_cmake_sources(cmake_text.content, cmake_text.path)
                    )

                if not declared_paths:
                    diagnostics.append(
                        f"library {library.alias} has no parsed CMake source list; "
                        "using classified files below its manifest path"
                    )
                    declared_paths.update(
                        file.path
                        for file in library_files
                        if file.role
                        in {
                            "assembly_source",
                            "c_header",
                            "c_source",
                            "resource",
                        }
                    )

                for path in sorted(declared_paths, key=str.casefold):
                    archive_file = files_by_path.get(path)
                    if archive_file is None or not _is_within(path, library.path):
                        diagnostics.append(
                            f"library {library.alias} declares a missing or external file: {path}"
                        )
                        continue
                    if archive_file.role == "resource":
                        resource_files[path] = archive_file
                    elif archive_file.role == "c_header":
                        read_and_select(archive, archive_file, "driver_header")
                    elif archive_file.role == "c_source":
                        read_and_select(archive, archive_file, "driver_source")
                    elif archive_file.role == "assembly_source":
                        read_and_select(archive, archive_file, "driver_assembly")
                    else:
                        diagnostics.append(
                            f"library {library.alias} declares an unclassified source: {path}"
                        )

                for build_file in library_files:
                    if build_file.role == "build_file":
                        read_and_select(archive, build_file, "build_metadata")
                    elif build_file.role == "resource":
                        resource_files[build_file.path] = build_file

            for example_path in manifest.example_paths:
                example_files = [
                    file
                    for file in inventory.files
                    if _is_within(file.path, example_path)
                ]
                cmake_files = [
                    file
                    for file in example_files
                    if _direct_file(file.path, example_path)
                    and PurePosixPath(file.path).name.casefold() == "cmakelists.txt"
                ]
                declared_paths: set[str] = set()
                for cmake_file in cmake_files:
                    cmake_text = read_and_select(
                        archive,
                        cmake_file,
                        "build_metadata",
                    )
                    declared_paths.update(
                        _declared_cmake_sources(cmake_text.content, cmake_text.path)
                    )
                if not declared_paths:
                    diagnostics.append(
                        f"example {example_path} has no parsed CMake source list; "
                        "using direct classified example files"
                    )
                    declared_paths.update(
                        file.path
                        for file in example_files
                        if _direct_file(file.path, example_path)
                        and file.role == "example_source"
                    )
                for path in sorted(declared_paths, key=str.casefold):
                    archive_file = files_by_path.get(path)
                    if archive_file is None or not _is_within(path, example_path):
                        diagnostics.append(
                            f"example declares a missing or external file: {path}"
                        )
                        continue
                    read_and_select(archive, archive_file, "example_source")

                metadata_files = [
                    file
                    for file in example_files
                    if _direct_file(file.path, example_path)
                    and file.role == "example_metadata"
                ]
                if len(metadata_files) > 1:
                    raise SourceSelectionError(
                        f"example contains multiple manifest.exm files: {example_path}"
                    )
                for metadata_file in metadata_files:
                    metadata_text = read_and_select(
                        archive,
                        metadata_file,
                        "example_metadata",
                    )
                    example_metadata.append(
                        _parse_example_metadata(metadata_text.content, metadata_text.path)
                    )

            for documentation_file in inventory.files:
                if (
                    documentation_file.role == "documentation"
                    and documentation_file.duplicate_of is None
                ):
                    read_and_select(
                        archive,
                        documentation_file,
                        "documentation",
                    )
    except SourceSelectionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise SourceSelectionError(f"cannot select sources from archive: {error}") from error

    generated_documentation_count = sum(
        file.role == "generated_documentation" for file in inventory.files
    )
    if generated_documentation_count:
        diagnostics.append(
            f"excluded {generated_documentation_count} generated documentation file(s)"
        )
    duplicate_count = sum(file.duplicate_of is not None for file in inventory.files)
    if duplicate_count:
        diagnostics.append(f"inventory records {duplicate_count} duplicate file(s)")

    return SourceBundle(
        inventory=inventory,
        manifest=manifest,
        examples=tuple(sorted(example_metadata, key=lambda item: item.path.casefold())),
        text_files=tuple(sorted(selected.values(), key=lambda item: item.path.casefold())),
        resource_files=tuple(
            sorted(resource_files.values(), key=lambda item: item.path.casefold())
        ),
        diagnostics=tuple(diagnostics),
    )
