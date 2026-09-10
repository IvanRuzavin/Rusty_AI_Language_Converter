"""Tree-sitter based C extraction for canonical Click source bundles."""

from __future__ import annotations

from collections.abc import Iterator
from tree_sitter import Language, Node, Parser
import tree_sitter_c

from rusty_ai_converter.models import SourceBundle, SourceTextFile
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


PARSER_LANGUAGE = "tree-sitter@0.26.0/tree-sitter-c@0.24.2"
_PARSED_PURPOSES = frozenset(
    {"driver_header", "driver_source", "example_source"}
)
_CONDITION_NODE_TYPES = frozenset(
    {"preproc_if", "preproc_ifdef", "preproc_elif", "preproc_else"}
)
_COMPOSITE_NODE_TYPES = frozenset({"struct_specifier", "union_specifier"})


class CParserError(Exception):
    """The selected C sources could not be converted into a consistent IR."""


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _span(node: Node, file_path: str) -> SourceSpan:
    start_row, start_column = node.start_point
    end_row, end_column = node.end_point
    return SourceSpan(
        file_path=file_path,
        start_line=start_row + 1,
        start_column=start_column + 1,
        end_line=end_row + 1,
        end_column=end_column + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


def _children_by_field(node: Node, field_name: str) -> tuple[Node, ...]:
    return tuple(
        child
        for index, child in enumerate(node.children)
        if node.field_name_for_child(index) == field_name
    )


def _ancestor(node: Node, node_types: set[str] | frozenset[str]) -> Node | None:
    parent = node.parent
    while parent is not None:
        if parent.type in node_types:
            return parent
        parent = parent.parent
    return None


def _leading_documentation(node: Node, source: bytes) -> str | None:
    comments: list[str] = []
    cursor = node.prev_named_sibling
    boundary = node.start_byte
    while cursor is not None and cursor.type == "comment":
        between = source[cursor.end_byte : boundary]
        if between.strip():
            break
        comment = _node_text(cursor, source)
        if comment.lstrip().startswith(("/**", "/*!")):
            comments.append(comment)
        elif comments:
            break
        boundary = cursor.start_byte
        cursor = cursor.prev_named_sibling
    if not comments:
        return None
    return "\n".join(reversed(comments))


def _condition_parts(node: Node, source: bytes) -> tuple[str, str, str]:
    first_token = node.children[0].type if node.children else ""
    if node.type == "preproc_ifdef":
        kind = "ifndef" if first_token == "#ifndef" else "ifdef"
        expression_node = node.child_by_field_name("name")
        expression = (
            _node_text(expression_node, source) if expression_node is not None else "?"
        )
    elif node.type in {"preproc_if", "preproc_elif"}:
        kind = "elif" if node.type == "preproc_elif" else "if"
        expression_node = node.child_by_field_name("condition")
        expression = (
            _node_text(expression_node, source) if expression_node is not None else "?"
        )
    else:
        kind = "else"
        expression = "else"
    directive = _node_text(node, source).splitlines()[0].strip()
    return kind, expression.strip(), directive


def _conditions_for(node: Node, source: bytes) -> tuple[str, ...]:
    conditions: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type in _CONDITION_NODE_TYPES:
            kind, expression, _ = _condition_parts(parent, source)
            conditions.append(f"{kind} {expression}" if kind != "else" else "else")
        parent = parent.parent
    conditions.reverse()
    return tuple(dict.fromkeys(conditions))


def _identifier_in_declarator(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {"identifier", "type_identifier", "field_identifier"}:
        return node.text.decode("utf-8")
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        name = _identifier_in_declarator(declarator)
        if name is not None:
            return name
    for child in node.named_children:
        name = _identifier_in_declarator(child)
        if name is not None:
            return name
    return None


def _function_declarators(node: Node) -> tuple[Node, ...]:
    result: list[Node] = []
    roots = (
        (node,)
        if node.type.endswith("declarator")
        else _children_by_field(node, "declarator")
    )
    for root in roots:
        for candidate in _walk(root):
            if candidate.type != "function_declarator":
                continue
            if _ancestor(candidate, {"parameter_declaration"}) is not None:
                continue
            declarator = candidate.child_by_field_name("declarator")
            if declarator is not None and declarator.type == "identifier":
                result.append(candidate)
    return tuple(result)


def _parameters(
    function_declarator: Node,
    source: bytes,
) -> tuple[CParameter, ...]:
    parameter_list = function_declarator.child_by_field_name("parameters")
    if parameter_list is None:
        return ()
    parameters: list[CParameter] = []
    for parameter in parameter_list.named_children:
        declaration = _node_text(parameter, source).strip()
        if not declaration:
            continue
        parameters.append(
            CParameter(
                name=_identifier_in_declarator(
                    parameter.child_by_field_name("declarator")
                ),
                declaration=declaration,
            )
        )
    return tuple(parameters)


def _type_text(node: Node, source: bytes) -> str:
    parts: list[str] = []
    for index, child in enumerate(node.children):
        if not child.is_named:
            continue
        field_name = node.field_name_for_child(index)
        if field_name == "type" or child.type == "type_qualifier":
            parts.append(_node_text(child, source).strip())
    return " ".join(part for part in parts if part) or "unknown"


def _storage(node: Node, source: bytes) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _node_text(child, source).strip()
            for child in node.named_children
            if child.type in {"storage_class_specifier", "function_specifier"}
        )
    )


def _calls(body: Node | None, source: bytes) -> tuple[str, ...]:
    if body is None:
        return ()
    calls: list[str] = []
    for node in _walk(body):
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        if function is not None:
            calls.append(_node_text(function, source).strip())
    return tuple(dict.fromkeys(call for call in calls if call))


def _function(
    owner: Node,
    declarator: Node,
    source: bytes,
    file_path: str,
) -> CFunction:
    name_node = declarator.child_by_field_name("declarator")
    if name_node is None:
        raise CParserError("function declarator has no name")
    body = owner.child_by_field_name("body") if owner.type == "function_definition" else None
    owner_text = _node_text(owner, source)
    signature = (
        source[owner.start_byte : body.start_byte].decode("utf-8").strip()
        if body is not None
        else owner_text.rstrip().removesuffix(";").rstrip()
    )
    return CFunction(
        name=_node_text(name_node, source),
        return_type=_type_text(owner, source),
        parameters=_parameters(declarator, source),
        signature=signature,
        body=_node_text(body, source) if body is not None else None,
        storage=_storage(owner, source),
        calls=_calls(body, source),
        documentation=_leading_documentation(owner, source),
        conditions=_conditions_for(owner, source),
        span=_span(owner, file_path),
    )


def _includes(root: Node, source: bytes, file_path: str) -> tuple[CInclude, ...]:
    result: list[CInclude] = []
    for node in _walk(root):
        if node.type != "preproc_include":
            continue
        path_node = node.child_by_field_name("path")
        if path_node is None:
            continue
        raw_path = _node_text(path_node, source).strip()
        result.append(
            CInclude(
                path=raw_path.strip('<>"'),
                is_system=raw_path.startswith("<"),
                directive=_node_text(node, source).strip(),
                conditions=_conditions_for(node, source),
                span=_span(node, file_path),
            )
        )
    return tuple(result)


def _macros(root: Node, source: bytes, file_path: str) -> tuple[CMacro, ...]:
    result: list[CMacro] = []
    for node in _walk(root):
        if node.type not in {"preproc_def", "preproc_function_def"}:
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        parameters_node = node.child_by_field_name("parameters")
        value_node = node.child_by_field_name("value")
        parameters = (
            tuple(
                _node_text(child, source).strip()
                for child in parameters_node.named_children
                if _node_text(child, source).strip()
            )
            if parameters_node is not None
            else ()
        )
        result.append(
            CMacro(
                name=_node_text(name_node, source),
                parameters=parameters,
                replacement=(
                    _node_text(value_node, source).strip()
                    if value_node is not None
                    else ""
                ),
                definition=_node_text(node, source).strip(),
                documentation=_leading_documentation(node, source),
                conditions=_conditions_for(node, source),
                span=_span(node, file_path),
            )
        )
    return tuple(result)


def _functions(root: Node, source: bytes, file_path: str) -> tuple[CFunction, ...]:
    result: list[CFunction] = []
    for node in _walk(root):
        if node.type == "function_definition":
            result.extend(
                _function(node, declarator, source, file_path)
                for declarator in _function_declarators(node)
            )
        elif node.type == "declaration" and _ancestor(
            node, {"function_definition"}
        ) is None:
            result.extend(
                _function(node, declarator, source, file_path)
                for declarator in _function_declarators(node)
            )
    return tuple(result)


def _typedef_alias(node: Node, source: bytes) -> str | None:
    for declarator in _children_by_field(node, "declarator"):
        name = _identifier_in_declarator(declarator)
        if name is not None:
            return name
    return None


def _composites(
    root: Node,
    source: bytes,
    file_path: str,
) -> tuple[CComposite, ...]:
    result: list[CComposite] = []
    for node in _walk(root):
        if node.type not in _COMPOSITE_NODE_TYPES:
            continue
        if _ancestor(node, {"function_definition"}) is not None:
            continue
        owner = _ancestor(node, {"type_definition", "declaration"}) or node
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node, source) if name_node is not None else None
        owner_type = owner.child_by_field_name("type")
        if (
            name is None
            and owner.type == "type_definition"
            and owner_type is not None
            and owner_type.id == node.id
        ):
            name = _typedef_alias(owner, source)
        fields: list[CField] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for field_node in body.named_children:
                if field_node.type != "field_declaration":
                    continue
                declarators = _children_by_field(field_node, "declarator")
                if declarators:
                    fields.extend(
                        CField(
                            name=_identifier_in_declarator(declarator),
                            declaration=_node_text(field_node, source),
                        )
                        for declarator in declarators
                    )
                else:
                    fields.append(
                        CField(
                            name=None,
                            declaration=_node_text(field_node, source),
                        )
                    )
        result.append(
            CComposite(
                kind="union" if node.type == "union_specifier" else "struct",
                name=name,
                fields=tuple(fields),
                definition=_node_text(node, source),
                documentation=_leading_documentation(owner, source),
                conditions=_conditions_for(node, source),
                span=_span(node, file_path),
            )
        )
    return tuple(result)


def _enums(root: Node, source: bytes, file_path: str) -> tuple[CEnum, ...]:
    result: list[CEnum] = []
    for node in _walk(root):
        if node.type != "enum_specifier":
            continue
        if _ancestor(node, {"function_definition"}) is not None:
            continue
        owner = _ancestor(node, {"type_definition", "declaration"}) or node
        name_node = node.child_by_field_name("name")
        name = _node_text(name_node, source) if name_node is not None else None
        owner_type = owner.child_by_field_name("type")
        if (
            name is None
            and owner.type == "type_definition"
            and owner_type is not None
            and owner_type.id == node.id
        ):
            name = _typedef_alias(owner, source)
        members: list[CEnumMember] = []
        body = node.child_by_field_name("body")
        if body is not None:
            for enumerator in body.named_children:
                if enumerator.type != "enumerator":
                    continue
                member_name = enumerator.child_by_field_name("name")
                value = enumerator.child_by_field_name("value")
                if member_name is not None:
                    members.append(
                        CEnumMember(
                            name=_node_text(member_name, source),
                            value=_node_text(value, source) if value is not None else None,
                        )
                    )
        result.append(
            CEnum(
                name=name,
                members=tuple(members),
                definition=_node_text(node, source),
                documentation=_leading_documentation(owner, source),
                conditions=_conditions_for(node, source),
                span=_span(node, file_path),
            )
        )
    return tuple(result)


def _typedefs(root: Node, source: bytes, file_path: str) -> tuple[CTypedef, ...]:
    result: list[CTypedef] = []
    for node in _walk(root):
        if node.type != "type_definition":
            continue
        type_node = node.child_by_field_name("type")
        if type_node is None:
            continue
        for declarator in _children_by_field(node, "declarator"):
            name = _identifier_in_declarator(declarator)
            if name is None:
                continue
            result.append(
                CTypedef(
                    name=name,
                    target=_node_text(type_node, source),
                    declaration=_node_text(node, source),
                    documentation=_leading_documentation(node, source),
                    conditions=_conditions_for(node, source),
                    span=_span(node, file_path),
                )
            )
    return tuple(result)


def _direct_declarators(node: Node) -> tuple[Node, ...]:
    declarators: list[Node] = []
    for child in _children_by_field(node, "declarator"):
        if child.type == "init_declarator":
            nested = child.child_by_field_name("declarator")
            if nested is not None:
                declarators.append(nested)
        else:
            declarators.append(child)
    return tuple(declarators)


def _global_variables(
    root: Node,
    source: bytes,
    file_path: str,
) -> tuple[CVariable, ...]:
    result: list[CVariable] = []
    for node in _walk(root):
        if node.type != "declaration" or _ancestor(
            node, {"function_definition"}
        ) is not None:
            continue
        type_text = _type_text(node, source)
        for direct_declarator in _children_by_field(node, "declarator"):
            value_node = (
                direct_declarator.child_by_field_name("value")
                if direct_declarator.type == "init_declarator"
                else None
            )
            variable_declarator = (
                direct_declarator.child_by_field_name("declarator")
                if direct_declarator.type == "init_declarator"
                else direct_declarator
            )
            if variable_declarator is None:
                continue
            if _function_declarators(variable_declarator):
                continue
            name = _identifier_in_declarator(variable_declarator)
            if name is None:
                continue
            result.append(
                CVariable(
                    name=name,
                    type_text=type_text,
                    declaration=_node_text(node, source),
                    initializer=(
                        _node_text(value_node, source) if value_node is not None else None
                    ),
                    storage=_storage(node, source),
                    conditions=_conditions_for(node, source),
                    span=_span(node, file_path),
                )
            )
    return tuple(result)


def _preprocessor_conditions(
    root: Node,
    source: bytes,
    file_path: str,
) -> tuple[CPreprocessorCondition, ...]:
    result: list[CPreprocessorCondition] = []
    for node in _walk(root):
        if node.type not in _CONDITION_NODE_TYPES:
            continue
        kind, expression, directive = _condition_parts(node, source)
        result.append(
            CPreprocessorCondition(
                kind=kind,
                expression=expression,
                directive=directive,
                span=_span(node, file_path),
            )
        )
    return tuple(result)


def _diagnostics(root: Node, file_path: str) -> tuple[ParseDiagnostic, ...]:
    diagnostics: list[ParseDiagnostic] = []
    seen: set[tuple[str, int, int]] = set()
    for node in _walk(root):
        if not node.is_error and not node.is_missing:
            continue
        key = (node.type, node.start_byte, node.end_byte)
        if key in seen:
            continue
        seen.add(key)
        if node.is_missing:
            message = f"Tree-sitter inserted missing token {node.type!r}"
            severity = "warning"
        else:
            message = f"Tree-sitter could not parse syntax node {node.type!r}"
            severity = "error"
        diagnostics.append(
            ParseDiagnostic(
                severity=severity,
                message=message,
                span=_span(node, file_path),
            )
        )
    return tuple(diagnostics)


def _parse_c_file(parser: Parser, source_file: SourceTextFile) -> ParsedCFile:
    source = source_file.content.encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node
    return ParsedCFile(
        path=source_file.path,
        purpose=source_file.purpose,
        sha256=source_file.sha256,
        parser=PARSER_LANGUAGE,
        includes=_includes(root, source, source_file.path),
        macros=_macros(root, source, source_file.path),
        functions=_functions(root, source, source_file.path),
        composites=_composites(root, source, source_file.path),
        enums=_enums(root, source, source_file.path),
        typedefs=_typedefs(root, source, source_file.path),
        global_variables=_global_variables(root, source, source_file.path),
        preprocessor_conditions=_preprocessor_conditions(
            root, source, source_file.path
        ),
        diagnostics=_diagnostics(root, source_file.path),
    )


def parse_c_file(source_file: SourceTextFile) -> ParsedCFile:
    """Parse one selected driver or example C text file."""
    if not isinstance(source_file, SourceTextFile):
        raise TypeError("source_file must be a SourceTextFile")
    if source_file.purpose not in _PARSED_PURPOSES:
        raise ValueError(
            f"source file purpose is not C-parseable: {source_file.purpose!r}"
        )
    parser = Parser(Language(tree_sitter_c.language()))
    return _parse_c_file(parser, source_file)


def parse_source_bundle(source_bundle: SourceBundle) -> ClickPackageIR:
    """Parse canonical C text while preserving recoverable syntax diagnostics."""
    if not isinstance(source_bundle, SourceBundle):
        raise TypeError("source_bundle must be a SourceBundle")

    parser = Parser(Language(tree_sitter_c.language()))
    parsed_files = tuple(
        sorted(
            (
                _parse_c_file(parser, source_file)
                for source_file in source_bundle.text_files
                if source_file.purpose in _PARSED_PURPOSES
            ),
            key=lambda item: item.path.casefold(),
        )
    )
    diagnostics: list[str] = []
    assembly_files = source_bundle.files_with_purpose("driver_assembly")
    if assembly_files:
        diagnostics.append(
            f"retained {len(assembly_files)} assembly file(s) without C parsing"
        )
    parser_diagnostic_count = sum(len(file.diagnostics) for file in parsed_files)
    if parser_diagnostic_count:
        diagnostics.append(
            f"Tree-sitter reported {parser_diagnostic_count} recoverable diagnostic(s)"
        )
    if not any(file.functions for file in parsed_files):
        diagnostics.append("no C functions were extracted from selected sources")

    return ClickPackageIR(
        source_bundle=source_bundle,
        files=parsed_files,
        diagnostics=tuple(diagnostics),
    )
