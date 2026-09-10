"""Resolve package call expressions against local and SDK evidence."""

from __future__ import annotations

from dataclasses import dataclass

from rusty_ai_converter.parsing import ClickPackageIR
from rusty_ai_converter.sdk_mapping.models import (
    CallResolution,
    SdkMappingDatabase,
    TranslationPlan,
)


@dataclass(frozen=True, slots=True)
class _BuiltinRule:
    status: str
    rust_crate: str | None
    rust_target: str
    guidance: str
    evidence_paths: tuple[str, ...] = ()


_BUILTIN_RULES: dict[str, _BuiltinRule] = {
    "Delay_1ms": _BuiltinRule(
        "platform_adapter",
        "system",
        "system::init_clock::delay_1ms()",
        "Replace each C 1 ms delay with one delay_1ms call.",
        ("examples/ips_display_2/ips_display_2.rs",),
    ),
    "Delay_10ms": _BuiltinRule(
        "platform_adapter",
        "system",
        "repeat system::init_clock::delay_1ms() 10 times",
        "Preserve the full 10 ms delay by looping over delay_1ms.",
        ("examples/ips_display_2/ips_display_2.rs",),
    ),
    "Delay_100ms": _BuiltinRule(
        "platform_adapter",
        "system",
        "repeat system::init_clock::delay_1ms() 100 times",
        "Preserve the full 100 ms delay by looping over delay_1ms.",
        ("examples/ips_display_2/ips_display_2.rs",),
    ),
    "Delay_ms": _BuiltinRule(
        "platform_adapter",
        "system",
        "loop over system::init_clock::delay_1ms()",
        "Translate the millisecond argument into the same number of delay_1ms calls.",
        ("examples/ips_display_2/ips_display_2.rs",),
    ),
    "preinit": _BuiltinRule(
        "platform_adapter",
        None,
        "Rust target runtime startup",
        "Do not emit an explicit preinit call; the generated Rust entrypoint uses the target runtime startup.",
        ("examples/ips_display_2/main.rs",),
    ),
    "strlen": _BuiltinRule(
        "standard_adapter",
        None,
        "byte slice or string .len()",
        "Use the byte length of the translated buffer; do not count a C NUL terminator.",
    ),
    "LOG_MAP_USB_UART": _BuiltinRule(
        "logging_adapter",
        None,
        "target logging configuration",
        "Logging transport configuration has no device-driver effect; use the available target logger or omit only the logging setup.",
    ),
    "log_init": _BuiltinRule(
        "logging_adapter",
        None,
        "target logging initialization",
        "Use the available target logger or omit only logging initialization.",
    ),
    "log_error": _BuiltinRule(
        "logging_adapter",
        None,
        "target error logging",
        "Preserve the diagnostic message when target logging is available; device operations must remain unchanged.",
    ),
    "log_info": _BuiltinRule(
        "logging_adapter",
        None,
        "target informational logging",
        "Preserve the diagnostic message when target logging is available; device operations must remain unchanged.",
    ),
    "log_printf": _BuiltinRule(
        "logging_adapter",
        None,
        "target formatted logging",
        "Translate formatting to the available target logger; device operations must remain unchanged.",
    ),
}


def _sdk_evidence_paths(database: SdkMappingDatabase, crate_name: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            function.source_path
            for function in database.rust_functions
            if function.crate_name == crate_name
        )
    )


def build_translation_plan(
    package_ir: ClickPackageIR,
    sdk_database: SdkMappingDatabase,
) -> TranslationPlan:
    """Resolve every distinct call made by a package function definition."""
    if not isinstance(package_ir, ClickPackageIR):
        raise TypeError("package_ir must be a ClickPackageIR")
    if not isinstance(sdk_database, SdkMappingDatabase):
        raise TypeError("sdk_database must be an SdkMappingDatabase")

    definitions_by_name: dict[str, list[str]] = {}
    calls_by_name: dict[str, set[str]] = {}
    macros_by_name: dict[str, list[str]] = {}
    for parsed_file in package_ir.files:
        for macro in parsed_file.macros:
            macros_by_name.setdefault(macro.name, []).append(parsed_file.path)
        for function in parsed_file.functions:
            if not function.is_definition:
                continue
            definitions_by_name.setdefault(function.name, []).append(parsed_file.path)
            for call in function.calls:
                calls_by_name.setdefault(call, set()).add(function.name)

    resolutions: list[CallResolution] = []
    for call in sorted(calls_by_name, key=str.casefold):
        called_by = tuple(sorted(calls_by_name[call], key=str.casefold))
        if call in definitions_by_name:
            resolutions.append(
                CallResolution(
                    call=call,
                    status="local_function",
                    called_by=called_by,
                    rust_crate=None,
                    rust_target=call,
                    guidance="Translate this package-defined function with its callers.",
                    evidence_paths=tuple(dict.fromkeys(definitions_by_name[call])),
                )
            )
            continue
        if call in macros_by_name:
            resolutions.append(
                CallResolution(
                    call=call,
                    status="local_macro",
                    called_by=called_by,
                    rust_crate=None,
                    rust_target=None,
                    guidance="Translate the package macro semantics from its retained definition.",
                    evidence_paths=tuple(dict.fromkeys(macros_by_name[call])),
                )
            )
            continue

        sdk_mapping = sdk_database.mapping_for(call)
        if sdk_mapping is not None and sdk_mapping.status == "direct":
            rust_function = sdk_mapping.rust_function
            assert rust_function is not None
            resolutions.append(
                CallResolution(
                    call=call,
                    status="sdk_direct",
                    called_by=called_by,
                    rust_crate=rust_function.crate_name,
                    rust_target=f"{rust_function.crate_name}::{rust_function.name}",
                    guidance=(
                        f"C: {sdk_mapping.c_function.signature} Rust: {rust_function.signature}"
                    ),
                    evidence_paths=(
                        sdk_mapping.c_function.source_path,
                        rust_function.source_path,
                    ),
                )
            )
            continue
        if sdk_mapping is not None and sdk_mapping.status == "adapted":
            assert sdk_mapping.rust_crate is not None
            resolutions.append(
                CallResolution(
                    call=call,
                    status="sdk_adapter",
                    called_by=called_by,
                    rust_crate=sdk_mapping.rust_crate,
                    rust_target=sdk_mapping.rust_expression,
                    guidance=" ".join(sdk_mapping.notes),
                    evidence_paths=(
                        sdk_mapping.c_function.source_path,
                        *_sdk_evidence_paths(sdk_database, sdk_mapping.rust_crate),
                    ),
                )
            )
            continue
        if sdk_mapping is not None:
            resolutions.append(
                CallResolution(
                    call=call,
                    status="unresolved",
                    called_by=called_by,
                    rust_crate=None,
                    rust_target=None,
                    guidance=" ".join(sdk_mapping.notes),
                    evidence_paths=(sdk_mapping.c_function.source_path,),
                )
            )
            continue

        builtin = _BUILTIN_RULES.get(call)
        if builtin is not None:
            resolutions.append(
                CallResolution(
                    call=call,
                    status=builtin.status,
                    called_by=called_by,
                    rust_crate=builtin.rust_crate,
                    rust_target=builtin.rust_target,
                    guidance=builtin.guidance,
                    evidence_paths=builtin.evidence_paths,
                )
            )
            continue

        resolutions.append(
            CallResolution(
                call=call,
                status="unresolved",
                called_by=called_by,
                rust_crate=None,
                rust_target=None,
                guidance="No package definition, verified SDK mapping, or reviewed platform adapter was found.",
            )
        )

    crates = tuple(
        sorted(
            {
                resolution.rust_crate
                for resolution in resolutions
                if resolution.rust_crate is not None
            },
            key=str.casefold,
        )
    )
    unresolved_count = sum(not resolution.is_resolved for resolution in resolutions)
    diagnostics = (
        (f"{unresolved_count} call(s) require human mapping before model use",)
        if unresolved_count
        else ()
    )
    return TranslationPlan(
        package_ir=package_ir,
        sdk_mapping_version=sdk_database.schema_version,
        resolutions=tuple(resolutions),
        required_rust_crates=crates,
        diagnostics=diagnostics,
    )
