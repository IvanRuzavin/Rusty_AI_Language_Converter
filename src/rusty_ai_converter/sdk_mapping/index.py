"""Build a deterministic mapping between the checked-in C and Rust SDKs."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import tomllib

from rusty_ai_converter.models import SourceTextFile
from rusty_ai_converter.parsing import parse_c_file
from rusty_ai_converter.sdk_mapping.models import (
    SdkFunction,
    SdkFunctionMapping,
    SdkMappingDatabase,
)


_RUST_PUBLIC_FN_RE = re.compile(
    r"(?m)^[ \t]*pub[ \t]+"
    r"(?:(?:const|async|unsafe)[ \t]+)*"
    r"(?:extern[ \t]+\"[^\"]+\"[ \t]+)?"
    r"fn[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*"
)
_RUST_NAMED_EXPORT_RE = re.compile(
    r"(?m)^[ \t]*pub[ \t]+(?:struct|enum|type|const)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_REEXPORT_RE = re.compile(
    r"(?m)^[ \t]*pub[ \t]+use[ \t]+[^;\n]+[ \t]+as[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*;"
)

# These are semantic API adaptations, not fuzzy name matches. Each rule is
# enabled only when its crate and exported Rust type are present in rust_sdk.
_DEFAULT_ADAPTERS: dict[str, tuple[str, str, str]] = {
    "analog_in_configure_default": (
        "drv_analog_in",
        "analog_in_config_t",
        "Construct analog_in_config_t with Default::default().",
    ),
    "i2c_master_configure_default": (
        "drv_i2c_master",
        "i2c_master_config_t",
        "Construct i2c_master_config_t with Default::default().",
    ),
    "one_wire_configure_default": (
        "drv_one_wire",
        "one_wire_t",
        "Construct one_wire_t with Default::default().",
    ),
    "pwm_configure_default": (
        "drv_pwm",
        "pwm_config_t",
        "Construct pwm_config_t with Default::default().",
    ),
    "spi_master_configure_default": (
        "drv_spi_master",
        "spi_master_config_t",
        "Construct spi_master_config_t with Default::default().",
    ),
    "uart_configure_default": (
        "drv_uart",
        "uart_config_t",
        "Construct uart_config_t with Default::default().",
    ),
}


class SdkIndexError(Exception):
    """The local SDK references could not be indexed consistently."""


def _read_utf8(path: Path) -> tuple[bytes, str]:
    try:
        content = path.read_bytes()
        return content, content.decode("utf-8")
    except OSError as error:
        raise SdkIndexError(f"cannot read SDK file {path}: {error}") from error
    except UnicodeDecodeError as error:
        raise SdkIndexError(f"SDK file is not UTF-8: {path}") from error


def _logical_path(root: Path, path: Path) -> str:
    return (Path(root.name) / path.relative_to(root)).as_posix()


def _outside_code_characters(text: str, start: int):
    """Yield characters outside ordinary comments and quoted literals."""
    index = start
    state = "code"
    escaped = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line_comment":
            if character == "\n":
                state = "code"
        elif state == "block_comment":
            if character == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"string", "character"}:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif (state == "string" and character == '"') or (
                state == "character" and character == "'"
            ):
                state = "code"
        elif character == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif character == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif character == '"':
            state = "string"
        elif character == "'" and (
            (following == "\\" and "'" in text[index + 2 : index + 12])
            or (index + 2 < len(text) and text[index + 2] == "'")
        ):
            state = "character"
        else:
            yield index, character
        index += 1


def _is_top_level_code_offset(text: str, offset: int) -> bool:
    depth = 0
    for index, character in _outside_code_characters(text, 0):
        if index == offset:
            return depth == 0
        if index > offset:
            return False
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
    return False


def _rust_function_end(text: str, start: int) -> tuple[int, int]:
    open_brace: int | None = None
    depth = 0
    for index, character in _outside_code_characters(text, start):
        if open_brace is None:
            if character == "{":
                open_brace = index
                depth = 1
            elif character == ";":
                return index, index + 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return open_brace, index + 1
    raise SdkIndexError("unterminated public Rust function")


def _index_c_functions(c_sdk_root: Path) -> tuple[tuple[SdkFunction, ...], list[str]]:
    functions: list[SdkFunction] = []
    diagnostics: list[str] = []
    for path in sorted(c_sdk_root.glob("*.h"), key=lambda item: item.name.casefold()):
        encoded, content = _read_utf8(path)
        logical_path = _logical_path(c_sdk_root, path)
        parsed = parse_c_file(
            SourceTextFile(
                path=logical_path,
                purpose="driver_header",
                sha256=sha256(encoded).hexdigest(),
                size_bytes=len(encoded),
                content=content,
            )
        )
        for diagnostic in parsed.diagnostics:
            diagnostics.append(
                f"{logical_path}:{diagnostic.span.start_line}:"
                f"{diagnostic.span.start_column}: {diagnostic.severity}: "
                f"{diagnostic.message}"
            )
        for function in parsed.functions:
            functions.append(
                SdkFunction(
                    language="c",
                    name=function.name,
                    module=path.stem,
                    signature=function.signature,
                    source_path=logical_path,
                    source_sha256=sha256(encoded).hexdigest(),
                    start_line=function.span.start_line,
                    end_line=function.span.end_line,
                )
            )

    functions.sort(key=lambda item: item.name.casefold())
    duplicates = sorted(
        {item.name for item in functions if sum(other.name == item.name for other in functions) > 1}
    )
    if duplicates:
        raise SdkIndexError(f"duplicate C SDK function names: {', '.join(duplicates)}")
    return tuple(functions), diagnostics


def _crate_name(manifest_path: Path) -> str:
    _, content = _read_utf8(manifest_path)
    try:
        manifest = tomllib.loads(content)
        name = manifest["package"]["name"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise SdkIndexError(f"invalid Rust SDK manifest {manifest_path}: {error}") from error
    if not isinstance(name, str) or not name.strip():
        raise SdkIndexError(f"Rust SDK package name is invalid: {manifest_path}")
    return name.strip()


def _index_rust_functions(
    rust_sdk_root: Path,
) -> tuple[tuple[SdkFunction, ...], dict[str, frozenset[str]], dict[str, str]]:
    functions: list[SdkFunction] = []
    exports: dict[str, frozenset[str]] = {}
    crate_paths: dict[str, str] = {}
    for manifest_path in sorted(
        rust_sdk_root.glob("*/Cargo.toml"), key=lambda item: item.as_posix().casefold()
    ):
        crate_name = _crate_name(manifest_path)
        source_path = manifest_path.parent / "src" / "lib.rs"
        if not source_path.is_file():
            raise SdkIndexError(f"Rust SDK crate has no src/lib.rs: {crate_name}")
        encoded, content = _read_utf8(source_path)
        logical_path = _logical_path(rust_sdk_root, source_path)
        crate_paths[crate_name] = logical_path
        exported_names = {
            match.group("name")
            for match in _RUST_NAMED_EXPORT_RE.finditer(content)
            if _is_top_level_code_offset(content, match.start())
        }
        exported_names.update(
            match.group("name")
            for match in _RUST_REEXPORT_RE.finditer(content)
            if _is_top_level_code_offset(content, match.start())
        )
        for match in _RUST_PUBLIC_FN_RE.finditer(content):
            if not _is_top_level_code_offset(content, match.start()):
                continue
            open_brace, end = _rust_function_end(content, match.start())
            signature_end = open_brace if content[open_brace] == "{" else end
            functions.append(
                SdkFunction(
                    language="rust",
                    name=match.group("name"),
                    module=crate_name,
                    signature=content[match.start() : signature_end].strip(),
                    body=(
                        content[open_brace:end]
                        if content[open_brace] == "{"
                        else None
                    ),
                    source_path=logical_path,
                    source_sha256=sha256(encoded).hexdigest(),
                    start_line=content.count("\n", 0, match.start()) + 1,
                    end_line=content.count("\n", 0, end) + 1,
                    crate_name=crate_name,
                )
            )
            exported_names.add(match.group("name"))
        exports[crate_name] = frozenset(exported_names)

    functions.sort(key=lambda item: (item.crate_name or "", item.name))
    keys = [(item.crate_name, item.name) for item in functions]
    if len(keys) != len(set(keys)):
        raise SdkIndexError("a Rust SDK crate exports a duplicate public function")
    return tuple(functions), exports, crate_paths


def build_sdk_mapping_database(
    c_sdk_root: Path | str,
    rust_sdk_root: Path | str,
) -> SdkMappingDatabase:
    """Index SDK source trees and map exact functions plus reviewed adapters."""
    c_root = Path(c_sdk_root)
    rust_root = Path(rust_sdk_root)
    if not c_root.is_dir():
        raise SdkIndexError(f"C SDK directory does not exist: {c_root}")
    if not rust_root.is_dir():
        raise SdkIndexError(f"Rust SDK directory does not exist: {rust_root}")

    c_functions, diagnostics = _index_c_functions(c_root)
    rust_functions, rust_exports, crate_paths = _index_rust_functions(rust_root)
    rust_by_name: dict[str, list[SdkFunction]] = {}
    for function in rust_functions:
        rust_by_name.setdefault(function.name, []).append(function)

    mappings: list[SdkFunctionMapping] = []
    for c_function in c_functions:
        exact_matches = rust_by_name.get(c_function.name, [])
        if len(exact_matches) == 1:
            rust_function = exact_matches[0]
            mappings.append(
                SdkFunctionMapping(
                    c_function=c_function,
                    status="direct",
                    rust_function=rust_function,
                    rust_crate=rust_function.crate_name,
                    notes=(
                        "The public name exists in both SDKs; adapt ownership, slices, and Result handling to the Rust signature.",
                    ),
                )
            )
            continue
        if len(exact_matches) > 1:
            diagnostics.append(
                f"{c_function.name}: multiple Rust crates export the same function name"
            )
            mappings.append(
                SdkFunctionMapping(
                    c_function=c_function,
                    status="unsupported",
                    notes=("Rust target is ambiguous and requires human review.",),
                )
            )
            continue

        adapter = _DEFAULT_ADAPTERS.get(c_function.name)
        if adapter is not None:
            crate_name, type_name, guidance = adapter
            if type_name in rust_exports.get(crate_name, frozenset()):
                mappings.append(
                    SdkFunctionMapping(
                        c_function=c_function,
                        status="adapted",
                        rust_crate=crate_name,
                        rust_expression=f"{type_name}::default()",
                        notes=(guidance, f"Rust evidence: {crate_paths[crate_name]}"),
                    )
                )
                continue

        mappings.append(
            SdkFunctionMapping(
                c_function=c_function,
                status="unsupported",
                notes=("No public Rust SDK function or reviewed adapter was found.",),
            )
        )

    return SdkMappingDatabase(
        c_functions=c_functions,
        rust_functions=rust_functions,
        mappings=tuple(mappings),
        diagnostics=tuple(diagnostics),
    )
