"""Tests for the controlled end-to-end conversion orchestrator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from rusty_ai_converter import (
    CoverageEntry,
    FakeModelClient,
    ModelOutput,
    OrchestrationError,
    PIPELINE_STAGES,
    PipelineConfig,
    required_coverage_symbols,
    run_conversion,
)
from rusty_ai_converter.validate import _CommandResult
from tests.test_download import FakeHttpResponse
from tests.test_sdk_mapping import _write_test_sdks
from tests.test_source import write_complete_package


DOWNLOAD_URL = "https://example.com/example.zip"


def pipeline_fixture(root: Path, *, allow_download: bool = True):
    archive_path = root / "source.zip"
    write_complete_package(archive_path)
    zip_bytes = archive_path.read_bytes()
    metadata_path = root / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "test": [
                    {
                        "name": "Example Click",
                        "download_link": DOWNLOAD_URL,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    c_sdk, rust_sdk = _write_test_sdks(root)
    config = PipelineConfig(
        query="Example Click",
        metadata_path=metadata_path,
        cache_dir=root / "cache",
        c_sdk_path=c_sdk,
        rust_sdk_path=rust_sdk,
        output_root=root / "output",
        allow_download=allow_download,
    )
    return config, zip_bytes


def offline_factory(plan):
    coverage = tuple(
        CoverageEntry(
            source_symbol=name,
            source_kind=kind,
            status="translated",
            rust_symbol=f"fixture::{name}",
            rationale="Offline orchestration fixture.",
        )
        for kind, name in required_coverage_symbols(plan)
    )
    output = ModelOutput(
        library_rs="#![no_std]\n\npub fn example() {}\n",
        main_rs=(
            "#![no_std]\n#![no_main]\n\n"
            "mod library;\nmod mikrobus;\n\n"
            "#[unsafe(no_mangle)]\n"
            "fn main() -> ! {\n"
            "    loop {\n"
            "        core::hint::spin_loop();\n"
            "    }\n"
            "}\n"
        ),
        required_rust_crates=plan.required_rust_crates,
        api_coverage_ledger=coverage,
    )
    return FakeModelClient(output)


def local_tool(command, *, cwd, **_options):
    if command == ("rustfmt", "--version"):
        return _CommandResult(0, stdout="rustfmt test\n")
    if command[0] == "rustfmt":
        return _CommandResult(0)
    if command == ("cargo", "--version"):
        return _CommandResult(0, stdout="cargo test\n")
    metadata = {
        "workspace_root": str(cwd.resolve()),
        "metadata": {"mikrobus-rust": {"entry": "main.rs"}},
    }
    return _CommandResult(0, stdout=json.dumps(metadata))


class RunConversionTests(unittest.TestCase):
    def test_runs_all_stages_then_reuses_cache_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, zip_bytes = pipeline_fixture(root)
            events: list[str] = []
            response = FakeHttpResponse(zip_bytes, final_url=DOWNLOAD_URL)

            with (
                patch("rusty_ai_converter.download.urlopen", return_value=response),
                patch(
                    "rusty_ai_converter.validate._run_tool",
                    side_effect=local_tool,
                ),
            ):
                first = asyncio.run(
                    run_conversion(
                        config,
                        offline_factory,
                        progress=lambda stage, detail: events.append(stage),
                    )
                )

            self.assertEqual(tuple(events), PIPELINE_STAGES)
            self.assertEqual(first.status, "local_validation_passed")
            self.assertTrue(first.network_download_performed)
            self.assertFalse(first.rendered_package.reused_existing)
            self.assertEqual(first.model_conversion.usage.total_tokens, 0)

            cached_config = PipelineConfig(
                query=config.query,
                metadata_path=config.metadata_path,
                cache_dir=config.cache_dir,
                c_sdk_path=config.c_sdk_path,
                rust_sdk_path=config.rust_sdk_path,
                output_root=config.output_root,
            )
            with (
                patch("rusty_ai_converter.download.urlopen") as open_url,
                patch(
                    "rusty_ai_converter.validate._run_tool",
                    side_effect=local_tool,
                ),
            ):
                second = asyncio.run(run_conversion(cached_config, offline_factory))

            open_url.assert_not_called()
            self.assertFalse(second.network_download_performed)
            self.assertTrue(second.rendered_package.reused_existing)
            self.assertTrue(second.validation_report.passed)

    def test_stops_before_network_and_model_without_download_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, _ = pipeline_fixture(root, allow_download=False)
            factory = Mock()

            with patch("rusty_ai_converter.download.urlopen") as open_url:
                with self.assertRaisesRegex(
                    OrchestrationError,
                    "download permission was not granted",
                ) as raised:
                    asyncio.run(run_conversion(config, factory))

            self.assertEqual(raised.exception.stage, "archive")
            open_url.assert_not_called()
            factory.assert_not_called()
            self.assertFalse(config.output_root.exists())

    def test_ambiguous_catalog_stops_at_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            metadata = root / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "test": [
                            {
                                "name": "Demo One Click",
                                "download_link": "https://example.com/one.zip",
                            },
                            {
                                "name": "Demo Two Click",
                                "download_link": "https://example.com/two.zip",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = PipelineConfig(
                query="Demo",
                metadata_path=metadata,
                cache_dir=root / "cache",
                c_sdk_path=root / "c_sdk",
                rust_sdk_path=root / "rust_sdk",
                output_root=root / "output",
            )

            with self.assertRaises(OrchestrationError) as raised:
                asyncio.run(run_conversion(config, Mock()))

            self.assertEqual(raised.exception.stage, "catalog")
            self.assertIn("found 2", raised.exception.detail)

    def test_model_factory_runs_only_after_context_is_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, zip_bytes = pipeline_fixture(root)
            events: list[str] = []
            response = FakeHttpResponse(zip_bytes, final_url=DOWNLOAD_URL)

            def broken_factory(_plan):
                self.assertEqual(events[-1], "context")
                raise RuntimeError("fixture client failed")

            with patch(
                "rusty_ai_converter.download.urlopen",
                return_value=response,
            ):
                with self.assertRaises(OrchestrationError) as raised:
                    asyncio.run(
                        run_conversion(
                            config,
                            broken_factory,
                            progress=lambda stage, detail: events.append(stage),
                        )
                    )

            self.assertEqual(raised.exception.stage, "model")
            self.assertFalse(config.output_root.exists())

    def test_validation_failure_returns_an_auditable_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, zip_bytes = pipeline_fixture(root)
            response = FakeHttpResponse(zip_bytes, final_url=DOWNLOAD_URL)

            def failing_rustfmt(command, *, cwd, **options):
                if command[0] == "rustfmt" and "--check" in command:
                    return _CommandResult(1, stdout="Diff in main.rs\n")
                return local_tool(command, cwd=cwd, **options)

            with (
                patch("rusty_ai_converter.download.urlopen", return_value=response),
                patch(
                    "rusty_ai_converter.validate._run_tool",
                    side_effect=failing_rustfmt,
                ),
            ):
                result = asyncio.run(run_conversion(config, offline_factory))

            self.assertEqual(result.status, "local_validation_failed")
            self.assertFalse(result.validation_report.passed)
            self.assertEqual(result.validation_report.checks[1].status, "failed")
            self.assertEqual(result.to_dict()["status"], result.status)


if __name__ == "__main__":
    unittest.main()
