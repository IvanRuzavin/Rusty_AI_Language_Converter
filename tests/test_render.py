"""Tests for deterministic and atomic Rust package rendering."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import tomllib
import unittest

from rusty_ai_converter.context import build_model_request
from rusty_ai_converter.model_client import FakeModelClient
from rusty_ai_converter.model_output import CoverageEntry, ModelOutput
from rusty_ai_converter.render import (
    MIKROBUS_1_SIGNALS,
    REQUIRED_ARTIFACT_PATHS,
    ArtifactRenderError,
    render_package,
    required_coverage_symbols,
)
from rusty_ai_converter.sdk_mapping import (
    build_sdk_mapping_database,
    build_translation_plan,
)
from tests.test_sdk_mapping import _package_ir, _write_test_sdks


def render_inputs(root: Path):
    c_sdk, rust_sdk = _write_test_sdks(root)
    database = build_sdk_mapping_database(c_sdk, rust_sdk)
    plan = build_translation_plan(
        _package_ir(include_mystery=False),
        database,
    )
    request = build_model_request(plan, database)
    return plan, request


def model_output_for(
    plan,
    *,
    crates: tuple[str, ...] | None = None,
    omit_first_coverage: bool = False,
    unsupported: bool = False,
) -> ModelOutput:
    required = required_coverage_symbols(plan)
    if omit_first_coverage:
        required = required[1:]
    entries = tuple(
        CoverageEntry(
            source_symbol=name,
            source_kind=kind,
            status="unsupported" if unsupported and index == 0 else "translated",
            rust_symbol=None if unsupported and index == 0 else f"fixture::{name}",
            rationale="Deterministic renderer test fixture.",
        )
        for index, (kind, name) in enumerate(required)
    )
    return ModelOutput(
        library_rs="//! Renderer fixture.\n\npub fn run() {}\n",
        main_rs=(
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "fn main() -> ! { loop {} }\n"
        ),
        required_rust_crates=crates or plan.required_rust_crates,
        api_coverage_ledger=entries,
        unsupported_items=("Fixture is unsupported.",) if unsupported else (),
    )


def conversion_for(plan, request, **output_options):
    client = FakeModelClient(model_output_for(plan, **output_options))
    return asyncio.run(client.convert(request))


class RenderPackageTests(unittest.TestCase):
    def test_writes_exactly_four_deterministic_files_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)

            rendered = render_package(plan, request, conversion, root / "output")

            self.assertFalse(rendered.reused_existing)
            self.assertEqual(rendered.output_name, "demo")
            self.assertEqual(
                {item.name for item in rendered.output_directory.iterdir()},
                set(REQUIRED_ARTIFACT_PATHS),
            )
            self.assertEqual(
                tuple(item.path for item in rendered.files),
                REQUIRED_ARTIFACT_PATHS,
            )
            self.assertEqual(
                rendered.total_size_bytes,
                sum(item.size_bytes for item in rendered.files),
            )
            cargo = tomllib.loads(
                (rendered.output_directory / "Cargo.toml").read_text()
            )
            self.assertEqual(
                cargo["workspace"]["metadata"]["mikrobus-rust"]["entry"],
                "main.rs",
            )

    def test_default_mikrobus_has_only_socket_one_with_blank_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)

            rendered = render_package(plan, request, conversion, root / "output")
            mikrobus = (rendered.output_directory / "mikrobus.rs").read_text()

            for signal in MIKROBUS_1_SIGNALS:
                self.assertIn(
                    f"MIKROBUS_1_{signal}: pin_name_t = 0xFF;",
                    mikrobus,
                )
            self.assertNotIn("MIKROBUS_2_", mikrobus)
            self.assertNotRegex(mikrobus, r"GPIO_[A-Z]")

    def test_identical_existing_package_is_reused_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)
            first = render_package(plan, request, conversion, root / "output")
            original_hashes = tuple(item.sha256 for item in first.files)

            second = render_package(plan, request, conversion, root / "output")

            self.assertTrue(second.reused_existing)
            self.assertEqual(
                tuple(item.sha256 for item in second.files),
                original_hashes,
            )

    def test_refuses_to_overwrite_a_changed_existing_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)
            rendered = render_package(plan, request, conversion, root / "output")
            library = rendered.output_directory / "library.rs"
            library.write_text("user-owned change\n", encoding="utf-8")

            with self.assertRaisesRegex(ArtifactRenderError, "different library.rs"):
                render_package(plan, request, conversion, root / "output")

            self.assertEqual(library.read_text(), "user-owned change\n")

    def test_rejects_mismatched_request_or_dependencies_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)
            mismatched = replace(conversion, request_context_sha256="b" * 64)

            with self.assertRaisesRegex(ArtifactRenderError, "context hash"):
                render_package(plan, request, mismatched, root / "first")

            wrong_crates = conversion_for(
                plan,
                request,
                crates=("different_crate",),
            )
            with self.assertRaisesRegex(ArtifactRenderError, "dependency list"):
                render_package(plan, request, wrong_crates, root / "second")

            self.assertFalse((root / "first").exists())
            self.assertFalse((root / "second").exists())

    def test_rejects_missing_or_unsupported_coverage_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            incomplete = conversion_for(
                plan,
                request,
                omit_first_coverage=True,
            )
            unsupported = conversion_for(plan, request, unsupported=True)

            with self.assertRaisesRegex(ArtifactRenderError, "coverage ledger"):
                render_package(plan, request, incomplete, root / "incomplete")
            with self.assertRaisesRegex(ArtifactRenderError, "unsupported"):
                render_package(plan, request, unsupported, root / "unsupported")

            self.assertFalse((root / "incomplete").exists())
            self.assertFalse((root / "unsupported").exists())

    def test_enforces_total_size_before_creating_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            conversion = conversion_for(plan, request)

            with self.assertRaisesRegex(ArtifactRenderError, "limit is 10"):
                render_package(
                    plan,
                    request,
                    conversion,
                    root / "output",
                    max_total_bytes=10,
                )

            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
