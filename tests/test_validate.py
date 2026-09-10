"""Tests for offline validation of rendered Rust packages."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rusty_ai_converter.render import render_package
from rusty_ai_converter.validate import _CommandResult, validate_rendered_package
from tests.test_render import conversion_for, render_inputs


def successful_results(target: Path) -> list[_CommandResult]:
    metadata = {
        "workspace_root": str(target.resolve()),
        "metadata": {"mikrobus-rust": {"entry": "main.rs"}},
    }
    return [
        _CommandResult(0, stdout="rustfmt 1.9.0-stable\n"),
        _CommandResult(0),
        _CommandResult(0, stdout="cargo 1.97.1\n"),
        _CommandResult(0, stdout=json.dumps(metadata)),
    ]


class ValidateRenderedPackageTests(unittest.TestCase):
    def test_passes_integrity_rustfmt_and_offline_cargo_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            rendered = render_package(
                plan,
                request,
                conversion_for(plan, request),
                root / "output",
            )

            with patch(
                "rusty_ai_converter.validate._run_tool",
                side_effect=successful_results(rendered.output_directory),
            ) as run_tool:
                report = validate_rendered_package(rendered)

            self.assertTrue(report.passed)
            self.assertEqual(
                tuple(item.status for item in report.checks),
                ("passed", "passed", "passed"),
            )
            self.assertEqual(report.rustfmt_version, "rustfmt 1.9.0-stable")
            self.assertEqual(report.cargo_version, "cargo 1.97.1")
            self.assertEqual(report.sdk_compilation_status, "not_run")
            cargo_command = run_tool.call_args_list[3].args[0]
            self.assertIn("--offline", cargo_command)
            self.assertEqual(report.to_dict()["scope"], "local")

    def test_changed_artifact_fails_integrity_and_skips_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            rendered = render_package(
                plan,
                request,
                conversion_for(plan, request),
                root / "output",
            )
            (rendered.output_directory / "library.rs").write_text(
                "changed after rendering\n",
                encoding="utf-8",
            )

            with patch("rusty_ai_converter.validate._run_tool") as run_tool:
                report = validate_rendered_package(rendered)

            self.assertFalse(report.passed)
            self.assertEqual(
                tuple(item.status for item in report.checks),
                ("failed", "skipped", "skipped"),
            )
            self.assertIn("changed after rendering", report.checks[0].summary)
            run_tool.assert_not_called()

    def test_rustfmt_failure_is_reported_while_cargo_still_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            rendered = render_package(
                plan,
                request,
                conversion_for(plan, request),
                root / "output",
            )
            results = successful_results(rendered.output_directory)
            results[1] = _CommandResult(1, stdout="Diff in main.rs\n")

            with patch(
                "rusty_ai_converter.validate._run_tool",
                side_effect=results,
            ):
                report = validate_rendered_package(rendered)

            self.assertFalse(report.passed)
            self.assertEqual(report.checks[1].status, "failed")
            self.assertIn("Diff in main.rs", report.checks[1].stdout)
            self.assertEqual(report.checks[2].status, "passed")

    def test_missing_local_tools_produce_failures_not_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            rendered = render_package(
                plan,
                request,
                conversion_for(plan, request),
                root / "output",
            )
            missing = [
                _CommandResult(127, stderr="rustfmt missing\n"),
                _CommandResult(127, stderr="cargo missing\n"),
            ]

            with patch(
                "rusty_ai_converter.validate._run_tool",
                side_effect=missing,
            ):
                report = validate_rendered_package(rendered)

            self.assertFalse(report.passed)
            self.assertEqual(report.checks[1].status, "failed")
            self.assertEqual(report.checks[2].status, "failed")
            self.assertIsNone(report.rustfmt_version)
            self.assertIsNone(report.cargo_version)

    def test_invalid_cargo_json_is_a_failed_metadata_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan, request = render_inputs(root)
            rendered = render_package(
                plan,
                request,
                conversion_for(plan, request),
                root / "output",
            )
            results = successful_results(rendered.output_directory)
            results[3] = _CommandResult(0, stdout="not JSON")

            with patch(
                "rusty_ai_converter.validate._run_tool",
                side_effect=results,
            ):
                report = validate_rendered_package(rendered)

            self.assertFalse(report.passed)
            self.assertEqual(report.checks[2].status, "failed")
            self.assertIn("invalid metadata", report.checks[2].summary)


if __name__ == "__main__":
    unittest.main()
