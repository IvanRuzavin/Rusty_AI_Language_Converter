"""Tests for Tree-sitter C extraction into normalized IR records."""

from __future__ import annotations

from hashlib import sha256
import unittest

from rusty_ai_converter.models import SourceTextFile
from rusty_ai_converter.parsing import parse_c_file


def source_file(content: str, *, path: str = "package/driver.c") -> SourceTextFile:
    encoded = content.encode("utf-8")
    return SourceTextFile(
        path=path,
        purpose="driver_source",
        sha256=sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
        content=content,
    )


class ParseCFileTests(unittest.TestCase):
    def test_extracts_core_c_constructs_and_provenance(self) -> None:
        content = """#include <stdint.h>
#ifdef FEATURE_ENABLED
/** Scale one value. */
#define SCALE(value) ((value) * 2)
#endif

/** Driver state. */
typedef struct {
    int value;
} demo_t;

typedef enum {
    MODE_OFF = 0,
    MODE_ON
} mode_t;

/** Add two values. */
int add(int lhs, int rhs);

static int state = 3;

int add(int lhs, int rhs)
{
    helper();
    return SCALE(lhs + rhs);
}
"""
        parsed = parse_c_file(source_file(content))

        self.assertEqual(
            parsed.parser,
            "tree-sitter@0.26.0/tree-sitter-c@0.24.2",
        )
        self.assertEqual(parsed.includes[0].path, "stdint.h")
        self.assertTrue(parsed.includes[0].is_system)
        scale = next(macro for macro in parsed.macros if macro.name == "SCALE")
        self.assertEqual(scale.parameters, ("value",))
        self.assertEqual(scale.conditions, ("ifdef FEATURE_ENABLED",))
        self.assertIn("Scale one value", scale.documentation or "")
        self.assertEqual(parsed.composites[0].name, "demo_t")
        self.assertEqual(parsed.composites[0].fields[0].name, "value")
        self.assertEqual(parsed.enums[0].name, "mode_t")
        self.assertEqual(parsed.enums[0].members[0].value, "0")
        self.assertEqual({item.name for item in parsed.typedefs}, {"demo_t", "mode_t"})
        self.assertEqual(parsed.global_variables[0].name, "state")
        self.assertEqual(parsed.global_variables[0].initializer, "3")

        declarations = [item for item in parsed.functions if item.name == "add"]
        self.assertEqual(len(declarations), 2)
        prototype = next(item for item in declarations if not item.is_definition)
        definition = next(item for item in declarations if item.is_definition)
        self.assertIn("Add two values", prototype.documentation or "")
        self.assertEqual(definition.calls, ("helper", "SCALE"))
        self.assertIn("return SCALE", definition.body or "")
        self.assertEqual(definition.span.file_path, "package/driver.c")
        self.assertEqual(
            content.encode("utf-8")[
                definition.span.start_byte : definition.span.end_byte
            ].decode("utf-8"),
            definition.signature + "\n" + definition.body,
        )

    def test_treats_function_pointer_as_global_variable(self) -> None:
        parsed = parse_c_file(
            source_file("void (*callback)(int);\nint actual(void);\n")
        )

        self.assertEqual([item.name for item in parsed.functions], ["actual"])
        self.assertEqual([item.name for item in parsed.global_variables], ["callback"])

    def test_preserves_preprocessor_conditions_on_declarations(self) -> None:
        parsed = parse_c_file(
            source_file(
                "#if API_LEVEL >= 2\nint enabled(void);\n#else\nint fallback(void);\n#endif\n"
            )
        )

        conditions = {item.name: item.conditions for item in parsed.functions}
        self.assertEqual(conditions["enabled"], ("if API_LEVEL >= 2",))
        self.assertIn("else", conditions["fallback"])
        self.assertEqual(
            [item.kind for item in parsed.preprocessor_conditions],
            ["if", "else"],
        )

    def test_returns_recoverable_diagnostics_for_invalid_syntax(self) -> None:
        parsed = parse_c_file(source_file("int broken( {\n"))

        self.assertTrue(parsed.diagnostics)
        self.assertTrue(
            any(item.severity in {"warning", "error"} for item in parsed.diagnostics)
        )


if __name__ == "__main__":
    unittest.main()
