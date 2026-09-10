"""C parsing and intermediate-representation support."""

from rusty_ai_converter.parsing.models import (
    CComposite,
    CEnum,
    CEnumMember,
    CField,
    CFunction,
    CInclude,
    CMacro,
    CParameter,
    CPreprocessorCondition,
    CTypedef,
    CVariable,
    ClickPackageIR,
    ParseDiagnostic,
    ParsedCFile,
    SourceSpan,
)
from rusty_ai_converter.parsing.parser import (
    CParserError,
    parse_c_file,
    parse_source_bundle,
)

__all__ = [
    "CComposite",
    "CEnum",
    "CEnumMember",
    "CField",
    "CFunction",
    "CInclude",
    "CMacro",
    "CParameter",
    "CParserError",
    "CPreprocessorCondition",
    "CTypedef",
    "CVariable",
    "ClickPackageIR",
    "ParseDiagnostic",
    "ParsedCFile",
    "SourceSpan",
    "parse_c_file",
    "parse_source_bundle",
]
