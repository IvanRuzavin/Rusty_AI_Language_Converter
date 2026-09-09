"""
Evidence-driven system-library/clock configuration generator.

This module intentionally does not ask an LLM to translate one family into
another in a single pass.  It first learns the semantic clock-initialization
blueprint from an existing implementation and its reference manual, then maps
those semantic roles into a target reference manual, retrieves exact target
register evidence, generates the JSON/C artifacts, and validates them.

The LLM transport is injected as two callables (`llm_json` and `llm_text`) so
this core can be used from the existing Open WebUI/NECTO model plumbing or any
other model endpoint without embedding credentials in this module.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol

from necto_assistant_pdf_utilities import get_pdf_text, get_pdf_toc, get_sections as get_pdf_sections

from necto_assistant_router.models.datasheets.section import get_section_title
from necto_assistant_router.models.datasheets.system_library_database import (
    DEFAULT_DATABASE_ARCHIVE_URL,
    TargetDeviceSet,
    ensure_necto_database,
    resolve_target_devices,
)


JsonObject = dict[str, Any]
StatusCallback = Callable[[str], Any]


class JsonLLM(Protocol):
    async def __call__(self, prompt: str, stage: str) -> JsonObject: ...


class TextLLM(Protocol):
    async def __call__(self, prompt: str, stage: str) -> str: ...


@dataclass
class SystemLibraryGenerationOptions:
    output_dir: str | Path
    cache_dir: str | Path | None = None
    database_path: str | Path | None = None
    database_archive_path: str | Path | None = None
    database_archive_url: str = DEFAULT_DATABASE_ARCHIVE_URL
    explicit_reference_manual_url: str | None = None
    target_clock_mhz: int | None = None
    strict: bool = True
    max_selected_sections: int = 18
    max_section_chars: int = 100_000
    max_evidence_chars: int = 650_000
    status_callback: StatusCallback | None = None


@dataclass
class SystemLibraryGenerationResult:
    output_dir: Path
    json_files: list[Path]
    init_clock_file: Path
    report_file: Path
    target_devices: TargetDeviceSet
    warnings: list[str] = field(default_factory=list)


_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
_VALUE_MACRO_RE = re.compile(r"\bVALUE_([A-Z][A-Z0-9_]*)\b")
_FUNCTION_RE = re.compile(
    r"(?m)^\s*(?:static\s+)?(?:inline\s+)?(?:void|uint\d+_t|int\d+_t|unsigned\s+\w+|int|bool)\s+"
    r"([A-Za-z_]\w*)\s*\([^;]*\)\s*\{"
)
_REGISTER_ACCESS_RE = re.compile(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)")


async def _emit_status(options: SystemLibraryGenerationOptions, text: str) -> None:
    callback = options.status_callback
    if callback is None:
        return
    result = callback(text)
    if inspect.isawaitable(result):
        await result


def _load_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _load_json(path: str | Path) -> JsonObject:
    with Path(path).open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("Source MCU JSON must contain one top-level JSON object.")
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_pretty(value: Any) -> str:
    return json.dumps(value, indent=4, ensure_ascii=False) + "\n"


def _source_manifest(source_json: JsonObject) -> JsonObject:
    registers = source_json.get("config_registers")
    if not isinstance(registers, list) or not registers:
        raise ValueError("Source JSON has no non-empty config_registers array.")

    compact_registers: list[JsonObject] = []
    for register in registers:
        if not isinstance(register, dict):
            continue
        fields = []
        for field_value in register.get("fields", []):
            if not isinstance(field_value, dict):
                continue
            item = {
                key: copy.deepcopy(field_value[key])
                for key in (
                    "hidden", "init", "key", "label", "mask", "settings", "settings_array"
                )
                if key in field_value
            }
            fields.append(item)
        compact_registers.append(
            {
                key: copy.deepcopy(register[key])
                for key in ("address", "default", "key", "unused", "label")
                if key in register
            }
            | {"fields": fields}
        )

    return {
        "top_level": {
            key: copy.deepcopy(value)
            for key, value in source_json.items()
            if key != "config_registers"
        },
        "registers": compact_registers,
    }


def _source_schema_profile(source_json: JsonObject) -> JsonObject:
    registers = [item for item in source_json.get("config_registers", []) if isinstance(item, dict)]
    register_key_sets = sorted({tuple(item.keys()) for item in registers})
    field_key_sets = sorted({
        tuple(field.keys())
        for register in registers
        for field in register.get("fields", [])
        if isinstance(field, dict)
    })

    def widths_for(name: str, values: Iterable[dict[str, Any]]) -> list[int]:
        widths = {
            len(str(item[name]))
            for item in values
            if name in item and isinstance(item[name], str) and _HEX_RE.fullmatch(item[name])
        }
        return sorted(widths)

    all_fields = [
        field
        for register in registers
        for field in register.get("fields", [])
        if isinstance(field, dict)
    ]

    return {
        "top_level_key_order": list(source_json.keys()),
        "register_key_orders": [list(keys) for keys in register_key_sets],
        "field_key_orders": [list(keys) for keys in field_key_sets],
        "hex_widths": {
            "address": widths_for("address", registers),
            "default": widths_for("default", registers),
            "unused": widths_for("unused", registers),
            "mask": widths_for("mask", all_fields),
            "init": widths_for("init", all_fields),
        },
        "hex_is_uppercase": all(
            value == value.upper()
            for register in registers
            for value in [
                str(register.get("address", "")),
                str(register.get("default", "")),
                str(register.get("unused", "")),
            ]
            if value and _HEX_RE.fullmatch(value)
        ),
    }


def _source_c_manifest(source_c: str) -> JsonObject:
    return {
        "functions": _FUNCTION_RE.findall(source_c),
        "value_macros": sorted(set(_VALUE_MACRO_RE.findall(source_c))),
        "register_accesses": sorted(
            {f"{peripheral}->{register}" for peripheral, register in _REGISTER_ACCESS_RE.findall(source_c)}
        ),
    }


def _toc_as_indexed_text(toc_items: list[Any]) -> str:
    lines: list[str] = []
    for index, item in enumerate(toc_items):
        if not isinstance(item, dict):
            lines.append(f"[{index}] {item}")
            continue
        lines.append(
            "[{idx}] level={level} start={start} end={end} title={title}".format(
                idx=index,
                level=item.get("section_lvl", "?"),
                start=item.get("start_page", "?"),
                end=item.get("end_page", "?"),
                title=item.get("section_title", ""),
            )
        )
    return "\n".join(lines)


def _load_pdf_context(path: str | Path) -> tuple[str, list[Any]]:
    path_str = str(path)
    toc_items = get_pdf_sections(path_str)
    toc_text = _toc_as_indexed_text(toc_items)
    return toc_text, toc_items


def _valid_section_indexes(response: JsonObject, toc_items: list[Any], max_count: int) -> list[int]:
    raw = response.get("indexes", [])
    if not isinstance(raw, list):
        raise ValueError("Section-selection model response must contain indexes: [...].")
    indexes: list[int] = []
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(toc_items) and index not in indexes:
            indexes.append(index)
        if len(indexes) >= max_count:
            break
    if not indexes:
        raise RuntimeError("Model did not select any usable datasheet sections.")
    return indexes


def _section_selection_prompt(
    *,
    role: str,
    toc_text: str,
    request_context: str,
    max_sections: int,
) -> str:
    return f"""
You are selecting EVIDENCE sections from an MCU reference manual for an automated
system-library clock generator. This is retrieval, not implementation.

MANUAL ROLE: {role}

Select every TOC entry needed to establish the relevant facts, but no more than
{max_sections} entries. Prefer the most specific sub-sections that contain the
facts rather than only a broad chapter.

For clock/system-library work, evidence may be split across multiple modules.
Include, when relevant:
- clock tree / clock system overview
- oscillator, PLL/DFLL/DPLL source configuration and startup/ready status
- generic/main/system clock controller and prescalers
- flash/NVM wait states and frequency constraints
- register summaries and exact register descriptions for required registers
- peripheral/base address mapping needed to calculate absolute addresses
- synchronization, ready/lock polling, write protection and safe switching rules
- USB clocking when the source implementation configures USB clocks
- watchdog/core post-initialization only when it belongs to system initialization
- electrical frequency constraints that affect legal clock values
- errata that changes the required initialization sequence

REQUEST CONTEXT:
{request_context}

INDEXED TABLE OF CONTENTS:
{toc_text}
END INDEXED TABLE OF CONTENTS

Return JSON only:
{{
  "indexes": [1, 2, 3],
  "reason": "short retrieval rationale"
}}
Do not invent indexes and do not return prose outside JSON.
""".strip()


def _extract_sections(
    pdf_path: str | Path,
    toc_items: list[Any],
    indexes: Iterable[int],
    *,
    max_section_chars: int,
    max_total_chars: int,
) -> str:
    chunks: list[str] = []
    seen_ranges: set[tuple[Any, Any, str]] = set()
    total = 0

    for index in indexes:
        item = toc_items[index]
        if not isinstance(item, dict):
            continue
        title = get_section_title(item)
        start_page = item.get("start_page")
        end_page = item.get("end_page")
        key = (start_page, end_page, str(title))
        if key in seen_ranges:
            continue
        seen_ranges.add(key)

        text = get_pdf_text(
            pdf_path=str(pdf_path),
            start_page=start_page,
            end_page=end_page,
        )
        if not text:
            continue
        if len(text) > max_section_chars:
            text = text[:max_section_chars] + "\n[SECTION TRUNCATED BY GENERATOR]\n"

        chunk = (
            f"\n<<<MANUAL_SECTION index={index} title={title!r} "
            f"pages={start_page}-{end_page}>>>\n{text}\n<<<END_MANUAL_SECTION>>>\n"
        )
        if total + len(chunk) > max_total_chars:
            break
        chunks.append(chunk)
        total += len(chunk)

    if not chunks:
        raise RuntimeError("Selected PDF sections could not be extracted.")
    return "".join(chunks)


def _source_blueprint_prompt(
    source_manifest: JsonObject,
    source_c_manifest: JsonObject,
    source_c: str,
    source_evidence: str,
) -> str:
    return f"""
You are analyzing an ALREADY IMPLEMENTED MCU system clock library. Build a
semantic blueprint of what the implementation does and why. The target family
will be different, so do not reduce this to source register-name copying.

SOURCE CLOCK JSON MANIFEST:
{_compact_json(source_manifest)}

SOURCE C MANIFEST:
{_compact_json(source_c_manifest)}

SOURCE init_clock.c:
```c
{source_c}
```

SOURCE REFERENCE-MANUAL EVIDENCE:
{source_evidence}

Rules:
1. Ground every hardware claim in the source manual evidence.
2. Use init_clock.c to recover actual ordering, branching, ready/lock polling,
   read-modify-write behavior and post-clock actions.
3. Use the JSON to identify which register fields are user-configurable and
   which VALUE_* values drive the implementation.
4. Express the result in SEMANTIC roles such as flash wait-state preparation,
   source oscillator enable/select, PLL configuration, main-clock switching,
   prescaling, USB/generic clocks, synchronization and core-specific actions.
5. Mark source-family-only behavior explicitly. Do not assume it belongs on the
   target family.
6. If source evidence is incomplete, put that in unresolved; do not guess.

Return JSON only with this shape:
{{
  "source_family_summary": "...",
  "configured_clock_mhz": 0,
  "steps": [
    {{
      "order": 1,
      "purpose": "...",
      "c_functions": ["..."],
      "source_registers": ["..."],
      "source_fields": ["..."],
      "actions": ["..."],
      "preconditions": ["..."],
      "wait_conditions": ["..."],
      "postconditions": ["..."],
      "manual_evidence": ["section/title/page facts"]
    }}
  ],
  "required_target_roles": ["..."],
  "source_only_features": ["..."],
  "target_search_terms": ["..."],
  "unresolved": ["..."]
}}
""".strip()


def _target_identity_prompt(first_pages: str, target_filename: str) -> str:
    return f"""
Extract family identity needed to correlate this target reference manual with
NECTO DeviceDetails. Use only the manual excerpt.

TARGET PDF FILENAME: {target_filename}
TARGET MANUAL FRONT MATTER:
{first_pages}

Return JSON only:
{{
  "family_name": "...",
  "manufacturer_family_name": "...",
  "core_description": "...",
  "max_cpu_mhz": 0,
  "uid_prefixes": ["exact likely NECTO/ordering-code prefix such as ATSAMD11"],
  "explicit_device_names": ["device names explicitly visible in the excerpt"],
  "notes": ["..."]
}}
Do not manufacture part numbers that are not supported by the excerpt.
""".strip()


def _target_mapping_prompt(
    *,
    source_blueprint: JsonObject,
    target_identity: JsonObject,
    target_architecture_evidence: str,
    target_clock_mhz: int | None,
) -> str:
    requested_clock = (
        f"The user explicitly requested {target_clock_mhz} MHz."
        if target_clock_mhz is not None
        else "No target clock override was supplied. Choose a clock only when the manual evidence and source intent make it unambiguous."
    )
    return f"""
Map an existing system-clock implementation's SEMANTIC blueprint onto a new
MCU family. The target architecture may use completely different peripherals,
registers and sequencing. Do not preserve source register names merely because
they look similar.

SOURCE SEMANTIC BLUEPRINT:
{_compact_json(source_blueprint)}

TARGET FAMILY IDENTITY:
{_compact_json(target_identity)}

TARGET ARCHITECTURE EVIDENCE:
{target_architecture_evidence}

CLOCK POLICY:
{requested_clock}

Build a target implementation plan. Every target register/field/status bit must
be supported by the supplied target evidence. Include exact candidate register
and field names so a second retrieval pass can fetch their register descriptions.
Pay special attention to safe frequency changes, flash/NVM wait states,
source-before-switch ordering, lock/ready/synchronization waits, write
protection, reset defaults, indirect-addressed clock registers, USB clocks,
electrical limits and errata.

Do NOT carry M7/FPU/cache behavior to a core that does not support it.

Return JSON only:
{{
  "family_name": "...",
  "core_description": "...",
  "configured_clock_mhz": 0,
  "family_shared_clock_logic": true,
  "target_registers": [
    {{
      "semantic_role": "...",
      "peripheral": "...",
      "register_name": "...",
      "fields": ["..."],
      "status_or_sync_fields": ["..."],
      "purpose": "...",
      "evidence_terms": ["exact terms for retrieval"]
    }}
  ],
  "sequence": [
    {{
      "order": 1,
      "purpose": "...",
      "writes": ["..."],
      "waits": ["..."],
      "constraints": ["..."]
    }}
  ],
  "top_level_metadata": {{
    "core": "preferred NECTO core token if directly inferable, otherwise empty",
    "delay_src_path": "preferred path if directly inferable, otherwise empty"
  }},
  "must_retrieve": ["additional exact register/section names"],
  "assumptions": ["..."],
  "unresolved": ["..."]
}}
""".strip()


def _target_register_search_context(target_plan: JsonObject) -> str:
    terms: list[str] = []
    for register in target_plan.get("target_registers", []):
        if not isinstance(register, dict):
            continue
        for key in ("peripheral", "register_name"):
            value = register.get(key)
            if value:
                terms.append(str(value))
        for key in ("fields", "status_or_sync_fields", "evidence_terms"):
            value = register.get(key, [])
            if isinstance(value, list):
                terms.extend(str(item) for item in value if item)
    must_retrieve = target_plan.get("must_retrieve", [])
    if isinstance(must_retrieve, list):
        terms.extend(str(item) for item in must_retrieve if item)
    unique = list(dict.fromkeys(terms))
    return "TARGET PLAN REQUIRES EXACT EVIDENCE FOR:\n" + "\n".join(f"- {item}" for item in unique)


def _artifact_generation_prompt(
    *,
    source_json: JsonObject,
    source_schema_profile: JsonObject,
    source_blueprint: JsonObject,
    target_identity: JsonObject,
    target_plan: JsonObject,
    target_register_evidence: str,
    database_metadata_sample: JsonObject,
) -> str:
    # A few source objects teach exact JSON conventions without forcing the model
    # to duplicate the source architecture.
    source_examples = source_json.get("config_registers", [])[:3]
    return f"""
Generate the FAMILY TEMPLATE JSON for a new MCU system library.

The JSON shape and conventions MUST match the already implemented source family,
but the register content must come only from TARGET evidence/plan.

SOURCE JSON SCHEMA PROFILE:
{_compact_json(source_schema_profile)}

SOURCE JSON REGISTER EXAMPLES (FORMAT ONLY, NOT TARGET HARDWARE):
{_compact_json(source_examples)}

SOURCE SEMANTIC BLUEPRINT:
{_compact_json(source_blueprint)}

TARGET IDENTITY:
{_compact_json(target_identity)}

TARGET IMPLEMENTATION PLAN:
{_compact_json(target_plan)}

TARGET EXACT REGISTER / ADDRESS / CONSTRAINT EVIDENCE:
{target_register_evidence}

SAMPLE DeviceDetails METADATA FOR TARGET FAMILY (may help with core naming; do
not treat DB metadata as register evidence):
{_compact_json(database_metadata_sample)}

Requirements:
- Return the same config_registers/register/field/settings schema used by the
  source JSON.
- Addresses, reset/default values, masks, field values and labels must be
  supported by target manual evidence. Never fabricate missing values.
- Preserve the source JSON's hexadecimal formatting conventions where possible.
- Include only registers that are needed to expose/configure the target clock
  system represented by the semantic blueprint and target plan.
- Handle indirect/indexed clock registers explicitly. If multiple logical
  configuration entries share one physical address, use distinct logical keys
  only when the target plan/evidence justifies it and make the selector ID a
  fixed/hidden field when appropriate.
- The top-level mcu value must be "__FAMILY_TEMPLATE__". It will be replaced
  deterministically for each DeviceDetails UID later.
- Set top-level clock to the target configured MHz value.
- For core/delay_src_path, use DeviceDetails metadata or well-supported source
  conventions only. If not safely resolvable, leave an unresolved entry rather
  than guessing.
- Do not include source-family register names unless the target evidence itself
  uses the same name.

Return JSON only:
{{
  "json_document": {{
    "config_registers": [],
    "core": "...",
    "delay_src_path": "...",
    "mcu": "__FAMILY_TEMPLATE__",
    "clock": 0
  }},
  "assumptions": ["..."],
  "unresolved": ["..."]
}}
""".strip()


def _c_generation_prompt(
    *,
    source_c: str,
    source_blueprint: JsonObject,
    target_plan: JsonObject,
    generated_json: JsonObject,
    target_register_evidence: str,
) -> str:
    return f"""
Generate init_clock.c for the TARGET MCU family.

Use the source C only as a coding-style/API-pattern reference. Hardware logic,
registers, ordering, waits and constraints must come from the TARGET plan and
TARGET evidence.

SOURCE init_clock.c STYLE/STRUCTURE REFERENCE:
```c
{source_c}
```

SOURCE SEMANTIC BLUEPRINT:
{_compact_json(source_blueprint)}

TARGET IMPLEMENTATION PLAN:
{_compact_json(target_plan)}

GENERATED TARGET CLOCK JSON:
{_compact_json(generated_json)}

TARGET REGISTER / SEQUENCING EVIDENCE:
{target_register_evidence}

Rules:
- Keep the source file's license/header style and public SystemInit /
  SystemCoreClock behavior where appropriate.
- Use mcu.h/header symbols consistent with target register names. Do not use
  source-family PMC/CKGR/SUPC/EFC names unless they genuinely exist on target.
- Drive user-configurable registers from VALUE_<JSON_REGISTER_KEY> style macros
  in the same spirit as the source implementation.
- Apply flash/NVM wait states before raising the CPU/AHB clock when required.
- Enable and stabilize a source before selecting it.
- Respect target synchronization/ready/lock rules after writes.
- Follow safe clock-source switching order from the target manual.
- Do not emit FPU/cache setup for cores that do not support it.
- If a header spelling cannot be established from the supplied material, add a
  concise TODO comment and report the assumption in the generation report; do
  not silently invent unrelated symbols.
- Do not wrap the answer in markdown fences. Return C source only.
""".strip()


def _validation_prompt(
    *,
    source_schema_profile: JsonObject,
    target_identity: JsonObject,
    target_plan: JsonObject,
    generated_json: JsonObject,
    generated_c: str,
    target_evidence: str,
) -> str:
    return f"""
Act as a strict reviewer of generated MCU clock-system artifacts. Find factual
or sequencing errors. Do not be generous.

SOURCE JSON FORMAT PROFILE:
{_compact_json(source_schema_profile)}
TARGET IDENTITY:
{_compact_json(target_identity)}
TARGET PLAN:
{_compact_json(target_plan)}
GENERATED JSON:
{_compact_json(generated_json)}
GENERATED init_clock.c:
```c
{generated_c}
```
TARGET MANUAL EVIDENCE:
{target_evidence}

Verify:
1. every register, address, reset/default, field mask/value and ready/sync bit is
   supported by target evidence;
2. configured frequency is legal;
3. wait states are established before any required frequency increase;
4. sources are enabled/stable before clock switching;
5. PLL/DFLL/DPLL lock and GCLK synchronization rules are followed;
6. safe clock switching/prescaler order is followed;
7. source-only core features did not leak into target;
8. every configurable JSON register used by C has a coherent VALUE_* linkage;
9. no unsupported source-family register identifiers remain;
10. unresolved header-symbol assumptions are explicitly called out.

Return JSON only:
{{
  "valid": true,
  "errors": [],
  "warnings": [],
  "unresolved": []
}}
""".strip()


def _looks_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and bool(_HEX_RE.fullmatch(value))


def _deterministic_validate(
    source_json: JsonObject,
    target_identity: JsonObject,
    target_plan: JsonObject,
    generated_json: JsonObject,
    generated_c: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    registers = generated_json.get("config_registers")
    if not isinstance(registers, list) or not registers:
        errors.append("Generated JSON has no config_registers entries.")
        return errors, warnings

    source_registers = [item for item in source_json.get("config_registers", []) if isinstance(item, dict)]
    required_register_keys = set(source_registers[0].keys()) if source_registers else {
        "address", "default", "fields", "key", "unused", "label"
    }

    seen_keys: set[str] = set()
    for index, register in enumerate(registers):
        if not isinstance(register, dict):
            errors.append(f"config_registers[{index}] is not an object.")
            continue
        missing = required_register_keys - set(register.keys())
        if missing:
            errors.append(f"Register #{index} missing source-schema keys: {sorted(missing)}")
        key = str(register.get("key", ""))
        if not key:
            errors.append(f"Register #{index} has no key.")
        elif key in seen_keys:
            errors.append(f"Duplicate register key: {key}")
        seen_keys.add(key)

        for name in ("address", "default", "unused"):
            if name in register and not _looks_hex(register[name]):
                errors.append(f"{key or index}.{name} is not a hexadecimal string: {register[name]!r}")

        fields = register.get("fields")
        if not isinstance(fields, list):
            errors.append(f"{key or index}.fields is not an array.")
            continue
        field_keys: set[str] = set()
        for field_index, field_value in enumerate(fields):
            if not isinstance(field_value, dict):
                errors.append(f"{key}.fields[{field_index}] is not an object.")
                continue
            for required in ("hidden", "init", "key", "label", "mask"):
                if required not in field_value:
                    errors.append(f"{key}.{field_index} missing field key {required}.")
            fkey = str(field_value.get("key", ""))
            if fkey in field_keys:
                errors.append(f"Duplicate field key {key}.{fkey}")
            field_keys.add(fkey)
            for name in ("init", "mask"):
                if name in field_value and not _looks_hex(field_value[name]):
                    errors.append(f"{key}.{fkey}.{name} is not hex: {field_value[name]!r}")
            if "settings" not in field_value and "settings_array" not in field_value:
                errors.append(f"{key}.{fkey} has neither settings nor settings_array.")
            settings = field_value.get("settings")
            if settings is not None:
                if not isinstance(settings, list):
                    errors.append(f"{key}.{fkey}.settings is not an array.")
                else:
                    for setting in settings:
                        if isinstance(setting, dict) and "value" in setting and not _looks_hex(setting["value"]):
                            errors.append(f"{key}.{fkey} contains non-hex setting value {setting['value']!r}")

    clock = generated_json.get("clock")
    max_cpu = target_identity.get("max_cpu_mhz")
    try:
        if clock is not None and max_cpu not in (None, "", 0) and float(clock) > float(max_cpu):
            errors.append(f"Generated clock {clock} MHz exceeds manual max {max_cpu} MHz.")
    except (TypeError, ValueError):
        warnings.append("Could not numerically compare generated clock with target max CPU frequency.")

    target_core = str(target_plan.get("core_description") or target_identity.get("core_description") or "").casefold()
    if "m0" in target_core or "cortex-m0" in target_core:
        forbidden = ["SCB_EnableICache", "SCB_EnableDCache", "CPACR"]
        leaked = [token for token in forbidden if token in generated_c]
        if leaked:
            errors.append(f"Core-specific source features leaked into Cortex-M0 target C: {leaked}")

    if "SystemInit" not in generated_c:
        errors.append("Generated C does not define SystemInit.")
    if "SystemCoreClock" not in generated_c:
        warnings.append("Generated C does not reference SystemCoreClock.")

    source_keys = {str(item.get("key")) for item in source_registers if item.get("key")}
    target_keys = seen_keys
    suspicious = sorted(
        key for key in source_keys
        if key in generated_c and key not in target_keys
    )
    if suspicious:
        warnings.append(f"Source register identifiers still appear in target C: {suspicious}")

    return errors, warnings


def _db_metadata_sample(device_set: TargetDeviceSet, limit: int = 4) -> JsonObject:
    return {
        "reference_manual_url": device_set.reference_manual_url,
        "match_reason": device_set.match_reason,
        "uids": device_set.uids[:20],
        "sample_rows": device_set.rows[:limit],
    }


def _merge_top_level_like_source(source_json: JsonObject, generated_document: JsonObject) -> JsonObject:
    """Preserve source top-level key ordering and any unrelated metadata."""
    result: JsonObject = {}
    for key, value in source_json.items():
        if key == "config_registers":
            result[key] = copy.deepcopy(generated_document.get(key, []))
        elif key in generated_document:
            result[key] = copy.deepcopy(generated_document[key])
        else:
            result[key] = copy.deepcopy(value)

    for key, value in generated_document.items():
        if key not in result:
            result[key] = copy.deepcopy(value)
    return result


def _write_outputs(
    *,
    output_dir: Path,
    family_template: JsonObject,
    init_clock_c: str,
    device_set: TargetDeviceSet,
    report: JsonObject,
) -> tuple[list[Path], Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_dir = output_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    family_template_path = output_dir / "family_template.json"
    family_template_path.write_text(_json_pretty(family_template), encoding="utf-8")

    json_files: list[Path] = []
    for uid in device_set.uids:
        document = copy.deepcopy(family_template)
        document["mcu"] = uid
        path = json_dir / f"{uid}.json"
        path.write_text(_json_pretty(document), encoding="utf-8")
        json_files.append(path)

    init_clock_path = output_dir / "init_clock.c"
    init_clock_path.write_text(init_clock_c.rstrip() + "\n", encoding="utf-8")

    report_path = output_dir / "generation_report.json"
    report_path.write_text(_json_pretty(report), encoding="utf-8")
    return json_files, init_clock_path, report_path


async def generate_system_library(
    *,
    source_json_path: str | Path,
    source_init_clock_path: str | Path,
    source_reference_manual_path: str | Path,
    target_reference_manual_path: str | Path,
    llm_json: JsonLLM,
    llm_text: TextLLM,
    options: SystemLibraryGenerationOptions,
) -> SystemLibraryGenerationResult:
    """
    Generate clock JSONs for every target DeviceDetails UID plus init_clock.c.

    The function is intentionally strict: when target evidence/model review
    reports unresolved factual errors, strict mode raises instead of silently
    creating likely-wrong MCU files.
    """
    output_dir = Path(options.output_dir)
    cache_dir = Path(options.cache_dir) if options.cache_dir else output_dir / ".cache"

    await _emit_status(options, "Reading implemented system-library artifacts")
    source_json = _load_json(source_json_path)
    source_c = _load_text(source_init_clock_path)
    source_manifest = _source_manifest(source_json)
    schema_profile = _source_schema_profile(source_json)
    c_manifest = _source_c_manifest(source_c)

    await _emit_status(options, "Indexing source reference manual")
    source_toc_text, source_toc_items = _load_pdf_context(source_reference_manual_path)
    source_request_context = (
        "SOURCE JSON REGISTERS:\n"
        + "\n".join(
            f"- {reg.get('key')}: fields "
            + ", ".join(str(field.get('key')) for field in reg.get('fields', []) if isinstance(field, dict))
            for reg in source_manifest["registers"]
        )
        + "\n\nSOURCE C MANIFEST:\n"
        + _compact_json(c_manifest)
    )
    source_selection = await llm_json(
        _section_selection_prompt(
            role="already implemented/source family",
            toc_text=source_toc_text,
            request_context=source_request_context,
            max_sections=options.max_selected_sections,
        ),
        "source_section_selection",
    )
    source_indexes = _valid_section_indexes(
        source_selection, source_toc_items, options.max_selected_sections
    )
    source_evidence = _extract_sections(
        source_reference_manual_path,
        source_toc_items,
        source_indexes,
        max_section_chars=options.max_section_chars,
        max_total_chars=options.max_evidence_chars,
    )

    await _emit_status(options, "Learning source clock initialization blueprint")
    source_blueprint = await llm_json(
        _source_blueprint_prompt(source_manifest, c_manifest, source_c, source_evidence),
        "source_blueprint",
    )

    await _emit_status(options, "Identifying target MCU family")
    try:
        front_matter = get_pdf_text(
            pdf_path=str(target_reference_manual_path), start_page=1, end_page=20
        )
    except Exception:
        front_matter = get_pdf_text(
            pdf_path=str(target_reference_manual_path), start_page=1, end_page=8
        )
    target_identity = await llm_json(
        _target_identity_prompt(front_matter, Path(target_reference_manual_path).name),
        "target_identity",
    )

    await _emit_status(options, "Resolving target MCUs from NECTO database")
    db_path = await asyncio.to_thread(
        ensure_necto_database,
        cache_dir=cache_dir,
        database_path=options.database_path,
        database_archive_path=options.database_archive_path,
        database_archive_url=options.database_archive_url,
    )
    prefixes = target_identity.get("uid_prefixes", [])
    if not isinstance(prefixes, list):
        prefixes = []
    device_set = await asyncio.to_thread(
        resolve_target_devices,
        db_path,
        target_manual_filename=Path(target_reference_manual_path).name,
        uid_prefixes=[str(value) for value in prefixes],
        explicit_reference_manual_url=options.explicit_reference_manual_url,
    )
    if not device_set.uids:
        raise RuntimeError("No MCU UIDs were resolved for the target reference manual.")

    await _emit_status(options, "Indexing target reference manual")
    target_toc_text, target_toc_items = _load_pdf_context(target_reference_manual_path)
    target_arch_context = (
        "SOURCE BLUEPRINT:\n"
        + _compact_json(source_blueprint)
        + "\n\nTARGET IDENTITY:\n"
        + _compact_json(target_identity)
        + "\n\nMandatory: include address mapping, exact clock-source/control sections, "
          "NVM/flash wait-state evidence, synchronization/safe switching, electrical limits, and errata when present."
    )
    target_selection = await llm_json(
        _section_selection_prompt(
            role="new/target family architecture",
            toc_text=target_toc_text,
            request_context=target_arch_context,
            max_sections=options.max_selected_sections,
        ),
        "target_architecture_section_selection",
    )
    target_arch_indexes = _valid_section_indexes(
        target_selection, target_toc_items, options.max_selected_sections
    )
    target_arch_evidence = _extract_sections(
        target_reference_manual_path,
        target_toc_items,
        target_arch_indexes,
        max_section_chars=options.max_section_chars,
        max_total_chars=options.max_evidence_chars,
    )

    await _emit_status(options, "Mapping source clock semantics to target architecture")
    target_plan = await llm_json(
        _target_mapping_prompt(
            source_blueprint=source_blueprint,
            target_identity=target_identity,
            target_architecture_evidence=target_arch_evidence,
            target_clock_mhz=options.target_clock_mhz,
        ),
        "target_mapping",
    )

    await _emit_status(options, "Retrieving exact target register descriptions")
    register_context = _target_register_search_context(target_plan)
    target_register_selection = await llm_json(
        _section_selection_prompt(
            role="new/target family exact register evidence",
            toc_text=target_toc_text,
            request_context=register_context,
            max_sections=options.max_selected_sections,
        ),
        "target_register_section_selection",
    )
    target_register_indexes = _valid_section_indexes(
        target_register_selection, target_toc_items, options.max_selected_sections
    )
    target_register_evidence = _extract_sections(
        target_reference_manual_path,
        target_toc_items,
        target_register_indexes,
        max_section_chars=options.max_section_chars,
        max_total_chars=options.max_evidence_chars,
    )

    combined_target_evidence = target_arch_evidence + "\n" + target_register_evidence

    await _emit_status(options, "Generating family clock configuration JSON")
    artifact_response = await llm_json(
        _artifact_generation_prompt(
            source_json=source_json,
            source_schema_profile=schema_profile,
            source_blueprint=source_blueprint,
            target_identity=target_identity,
            target_plan=target_plan,
            target_register_evidence=combined_target_evidence,
            database_metadata_sample=_db_metadata_sample(device_set),
        ),
        "target_json_generation",
    )
    generated_document = artifact_response.get("json_document")
    if not isinstance(generated_document, dict):
        raise RuntimeError("Target JSON generation did not return json_document object.")

    family_template = _merge_top_level_like_source(source_json, generated_document)
    family_template["mcu"] = "__FAMILY_TEMPLATE__"
    if options.target_clock_mhz is not None:
        family_template["clock"] = options.target_clock_mhz

    await _emit_status(options, "Generating target init_clock.c")
    generated_c = await llm_text(
        _c_generation_prompt(
            source_c=source_c,
            source_blueprint=source_blueprint,
            target_plan=target_plan,
            generated_json=family_template,
            target_register_evidence=combined_target_evidence,
        ),
        "target_c_generation",
    )
    generated_c = generated_c.strip()
    if generated_c.startswith("```"):
        generated_c = re.sub(r"^```(?:c|C)?\s*", "", generated_c)
        generated_c = re.sub(r"\s*```$", "", generated_c)

    deterministic_errors, deterministic_warnings = _deterministic_validate(
        source_json,
        target_identity,
        target_plan,
        family_template,
        generated_c,
    )

    await _emit_status(options, "Reviewing generated system library against target manual")
    model_validation = await llm_json(
        _validation_prompt(
            source_schema_profile=schema_profile,
            target_identity=target_identity,
            target_plan=target_plan,
            generated_json=family_template,
            generated_c=generated_c,
            target_evidence=combined_target_evidence,
        ),
        "target_validation",
    )

    model_errors = model_validation.get("errors", [])
    if not isinstance(model_errors, list):
        model_errors = [str(model_errors)]
    model_warnings = model_validation.get("warnings", [])
    if not isinstance(model_warnings, list):
        model_warnings = [str(model_warnings)]
    unresolved = []
    for source in (
        source_blueprint.get("unresolved", []),
        target_plan.get("unresolved", []),
        artifact_response.get("unresolved", []),
        model_validation.get("unresolved", []),
    ):
        if isinstance(source, list):
            unresolved.extend(str(value) for value in source if value)

    all_errors = deterministic_errors + [str(value) for value in model_errors if value]
    all_warnings = deterministic_warnings + [str(value) for value in model_warnings if value]

    report: JsonObject = {
        "source": {
            "json": str(source_json_path),
            "init_clock": str(source_init_clock_path),
            "reference_manual": str(source_reference_manual_path),
            "selected_sections": source_indexes,
            "blueprint": source_blueprint,
        },
        "target": {
            "reference_manual": str(target_reference_manual_path),
            "identity": target_identity,
            "reference_manual_url": device_set.reference_manual_url,
            "device_resolution_reason": device_set.match_reason,
            "uids": device_set.uids,
            "architecture_sections": target_arch_indexes,
            "register_sections": target_register_indexes,
            "plan": target_plan,
        },
        "generation": {
            "assumptions": artifact_response.get("assumptions", []),
            "errors": all_errors,
            "warnings": all_warnings,
            "unresolved": list(dict.fromkeys(unresolved)),
            "model_validation": model_validation,
        },
    }

    if options.strict and (all_errors or unresolved or model_validation.get("valid") is False):
        failure_dir = output_dir / "failed_generation"
        failure_dir.mkdir(parents=True, exist_ok=True)
        (failure_dir / "family_template.json").write_text(_json_pretty(family_template), encoding="utf-8")
        (failure_dir / "init_clock.c").write_text(generated_c.rstrip() + "\n", encoding="utf-8")
        (failure_dir / "generation_report.json").write_text(_json_pretty(report), encoding="utf-8")
        raise RuntimeError(
            "System-library generation failed strict validation. Review "
            f"{failure_dir / 'generation_report.json'}"
        )

    await _emit_status(options, f"Writing {len(device_set.uids)} MCU JSON files")
    json_files, init_clock_file, report_file = _write_outputs(
        output_dir=output_dir,
        family_template=family_template,
        init_clock_c=generated_c,
        device_set=device_set,
        report=report,
    )

    await _emit_status(options, "System-library generation complete")
    return SystemLibraryGenerationResult(
        output_dir=output_dir,
        json_files=json_files,
        init_clock_file=init_clock_file,
        report_file=report_file,
        target_devices=device_set,
        warnings=all_warnings,
    )
