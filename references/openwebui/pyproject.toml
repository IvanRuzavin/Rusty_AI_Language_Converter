"""
Utility functions for Click boards.
"""


import json
from typing import (
    Dict,
    Tuple,
    List,
    Any
)
import re

from necto_assistant_router.util.general_utils import _log_success

try:
    from tree_sitter import Language, Parser
    import tree_sitter_c
except ImportError:  # pragma: no cover - exercised only in minimal installations
    Language = None
    Parser = None
    tree_sitter_c = None


def _ast_source_slice(source: str, start_byte: int, end_byte: int | None = None) -> str:
    source_bytes = source.encode('utf-8')
    return source_bytes[start_byte:end_byte].decode('utf-8', errors='replace')


def _ast_node_text(node, source: str) -> str:
    return _ast_source_slice(source, node.start_byte, node.end_byte)


def _normalize_c_text(value: str) -> str:
    return re.sub(r'\s+', ' ', value or '').strip()


def _clean_doxygen_text(value: str) -> str:
    value = re.sub(r'@(li|c)\b', '', value or '')
    value = re.sub(r'#([A-Za-z_]\w*)', r'\1', value)
    value = re.sub(r'\s+', ' ', value).strip(' :.\n\t ')
    if not value:
        return ''
    return value if value.endswith(('.', ':', ';', ',')) else f'{value}.'


def _parse_doxygen_comment_for_ast(raw_comment: str) -> Dict[str, Any]:
    """Parse the Doxygen fields needed by the generation context."""
    doc: Dict[str, Any] = {
        'brief': '',
        'details': '',
        'params': {},
        'return': '',
        'note': '',
    }
    lines = []
    for raw_line in raw_comment.splitlines():
        line = raw_line.strip()
        line = re.sub(r'^/(?:\*\*<?|\*!<?)', '', line)
        line = re.sub(r'\*/$', '', line)
        line = re.sub(r'^\*', '', line).strip()
        if line:
            lines.append(line)

    current_field = None
    current_param = None

    def append_field(field: str, value: str) -> None:
        value = _clean_doxygen_text(value)
        if value:
            doc[field] = f'{doc[field]} {value}'.strip()

    def append_param(name: str, value: str) -> None:
        value = _clean_doxygen_text(value)
        if value:
            doc['params'][name] = f'{doc["params"].get(name, "")} {value}'.strip()

    for line in lines:
        match = re.match(r'@brief\s+(.*)', line)
        if match:
            current_field, current_param = 'brief', None
            append_field('brief', match.group(1))
            continue

        match = re.match(r'@details\s+(.*)', line)
        if match:
            current_field, current_param = 'details', None
            append_field('details', match.group(1))
            continue

        match = re.match(r'@param(?:\[[^\]]+\])?\s+([A-Za-z_]\w*)\s*:?\s*(.*)', line)
        if match:
            current_field, current_param = 'params', match.group(1)
            append_param(current_param, match.group(2))
            continue

        match = re.match(r'@return\s+(.*)', line)
        if match:
            current_field, current_param = 'return', None
            append_field('return', match.group(1))
            continue

        match = re.match(r'@note\s+(.*)', line)
        if match:
            current_field, current_param = 'note', None
            append_field('note', match.group(1))
            continue

        match = re.match(r'@(li|c)\b\s*(.*)', line)
        if match:
            if current_field == 'params' and current_param:
                append_param(current_param, match.group(2))
            elif current_field in {'brief', 'details', 'return', 'note'}:
                append_field(current_field, match.group(2))
            continue

        if line.startswith('@'):
            continue
        if current_field == 'params' and current_param:
            append_param(current_param, line)
        elif current_field in {'brief', 'details', 'return', 'note'}:
            append_field(current_field, line)
        elif not doc['brief']:
            append_field('brief', line)

    return {key: value for key, value in doc.items() if value}


def _doxygen_comments_for_ast(source: str) -> List[tuple[int, int, str, Dict[str, Any]]]:
    comments = []
    for match in re.finditer(r'(?:/\*\*|/\*!)[\s\S]*?\*/', source, re.DOTALL):
        raw_comment = match.group(0)
        if re.search(r'@(addtogroup|defgroup|file|\{|\})\b', raw_comment):
            continue
        parsed = _parse_doxygen_comment_for_ast(raw_comment)
        if parsed:
            comments.append((
                len(source[:match.start()].encode('utf-8')),
                len(source[:match.end()].encode('utf-8')),
                raw_comment,
                parsed,
            ))
    return comments


def _leading_doxygen_doc(
    source: str,
    node,
    comments: List[tuple[int, int, str, Dict[str, Any]]],
) -> Dict[str, Any]:
    candidates = [item for item in comments if item[1] <= node.start_byte]
    if not candidates:
        return {}

    start, end, _, parsed = candidates[-1]
    gap = _ast_source_slice(source, end, node.start_byte)
    gap = re.sub(r'^\s*#.*$', '', gap, flags=re.MULTILINE)
    return parsed if not gap.strip() else {}


def _find_named_child(node, node_types: set[str]):
    if node.type in node_types:
        return node
    for child in node.named_children:
        found = _find_named_child(child, node_types)
        if found is not None:
            return found
    return None


def _find_function_declarators(node) -> List[Any]:
    result = []
    if node.type == 'function_declarator':
        result.append(node)
    for child in node.named_children:
        result.extend(_find_function_declarators(child))
    return result


def _function_name_node(function_declarator):
    declarator = function_declarator.child_by_field_name('declarator')
    # Function pointers are declarations of variables, not callable APIs.
    if declarator is None or declarator.type != 'identifier':
        return None
    return declarator


def _ast_function_entry(source: str, node, function_declarator, doc: Dict[str, Any]) -> Dict[str, Any] | None:
    name_node = _function_name_node(function_declarator)
    if name_node is None:
        return None

    name = _ast_node_text(name_node, source)
    params_node = function_declarator.child_by_field_name('parameters')
    params = ''
    if params_node is not None:
        params = _normalize_c_text(
            _ast_source_slice(source, params_node.start_byte + 1, params_node.end_byte - 1)
        )

    prefix = _normalize_c_text(_ast_source_slice(source, node.start_byte, function_declarator.start_byte))
    return_type = re.sub(r'\b(?:static|inline|extern|__inline__)\b', '', prefix)
    return_type = _normalize_c_text(return_type)
    prototype = f'{name}({params})'
    entry = {
        'name': name,
        'return_type': return_type,
        'params': params,
        'prototype': prototype,
        'full_signature': f'{prefix} {prototype}'.strip(),
    }
    if doc:
        entry['doc'] = doc
    return entry


def _ast_field_entry(source: str, field_node, inline_doc: Dict[str, Any]) -> Dict[str, str] | None:
    name_node = _find_named_child(field_node, {'field_identifier', 'identifier'})
    if name_node is None:
        return None

    name = _ast_node_text(name_node, source)
    field_type = _normalize_c_text(
        _ast_source_slice(source, field_node.start_byte, name_node.start_byte)
    ).rstrip(',')
    entry = {'type': field_type, 'name': name}
    description = inline_doc.get('brief') or inline_doc.get('details')
    if description:
        entry['description'] = description
    return entry


def _ast_struct_entry(source: str, node, doc: Dict[str, Any]) -> Dict[str, Any] | None:
    struct_specifier = _find_named_child(node, {'struct_specifier'})
    name_node = node.child_by_field_name('declarator')
    if struct_specifier is None or name_node is None:
        return None

    name = _ast_node_text(name_node, source)
    field_list = struct_specifier.child_by_field_name('body')
    members = []
    if field_list is not None:
        children = field_list.children
        for index, child in enumerate(children):
            if child.type != 'field_declaration':
                continue
            inline_doc = {}
            if index + 1 < len(children) and children[index + 1].type == 'comment':
                raw = _ast_node_text(children[index + 1], source)
                if raw.startswith('/**<') or raw.startswith('/*!<'):
                    inline_doc = _parse_doxygen_comment_for_ast(raw)
            member = _ast_field_entry(source, child, inline_doc)
            if member:
                members.append(member)

    entry = {'name': name, 'kind': 'struct', 'members': members}
    if doc:
        entry['doc'] = doc
    return entry


def _ast_enum_entry(source: str, node, doc: Dict[str, Any]) -> Dict[str, Any] | None:
    enum_specifier = _find_named_child(node, {'enum_specifier'})
    name_node = node.child_by_field_name('declarator')
    if enum_specifier is None or name_node is None:
        return None

    enum_name = _ast_node_text(name_node, source)
    members = []
    enum_list = enum_specifier.child_by_field_name('body')
    if enum_list is not None:
        for enumerator in enum_list.named_children:
            if enumerator.type != 'enumerator':
                continue
            identifier = enumerator.child_by_field_name('name')
            if identifier is None:
                identifier = _find_named_child(enumerator, {'identifier'})
            if identifier is None:
                continue
            value = _normalize_c_text(
                _ast_source_slice(source, identifier.end_byte, enumerator.end_byte)
            ).lstrip('=').strip()
            members.append({'name': _ast_node_text(identifier, source), 'value': value})

    entry = {'name': enum_name, 'kind': 'enum', 'members': members}
    if doc:
        entry['doc'] = doc
    return entry


def _strip_c_comments_and_strings_for_ast(source: str) -> str:
    result = []
    index = 0
    while index < len(source):
        if source.startswith('//', index):
            end = source.find('\n', index + 2)
            index = len(source) if end < 0 else end
            continue
        if source.startswith('/*', index):
            end = source.find('*/', index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        if source[index] in {'"', "'"}:
            quote = source[index]
            result.append(' ')
            index += 1
            while index < len(source):
                if source[index] == '\\':
                    index += 2
                elif source[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            continue
        result.append(source[index])
        index += 1
    return ''.join(result)


def _extract_c_symbols_with_tree_sitter(
    content: str,
    full_function_signatures: bool = True,
    include_used_symbols: bool = False,
) -> Dict[str, Any] | None:
    """Extract C symbols with Tree-sitter while retaining Doxygen metadata."""
    if Parser is None or tree_sitter_c is None:
        return None

    parser = Parser(Language(tree_sitter_c.language()))
    tree = parser.parse(content.encode('utf-8'))
    if tree.root_node.has_error:
        return None

    comments = _doxygen_comments_for_ast(content)
    includes = []
    macros = []
    functions = []
    structs = []
    enums = []
    docs = {'functions': {}, 'macros': {}, 'structs': {}, 'enums': {}}

    for node in tree.root_node.named_children:
        node_text = _ast_node_text(node, content)
        if node.type == 'preproc_include':
            match = re.search(r'#\s*include\s*(<[^>]+>|"[^"]+")', node_text)
            if match:
                includes.append(match.group(1).strip('<>"'))
            continue

        if node.type in {'preproc_def', 'preproc_function_def'}:
            name_node = node.child_by_field_name('name') or _find_named_child(node, {'identifier'})
            if name_node is not None:
                name = _ast_node_text(name_node, content)
                macros.append(name)
                doc = _leading_doxygen_doc(content, node, comments)
                if doc:
                    docs['macros'][name] = doc
            continue

        if node.type == 'type_definition':
            struct_entry = _ast_struct_entry(content, node, _leading_doxygen_doc(content, node, comments))
            enum_entry = _ast_enum_entry(content, node, _leading_doxygen_doc(content, node, comments))
            if struct_entry:
                structs.append(struct_entry)
                if struct_entry.get('doc'):
                    docs['structs'][struct_entry['name']] = struct_entry['doc']
            elif enum_entry:
                enums.append(enum_entry)
                if enum_entry.get('doc'):
                    docs['enums'][enum_entry['name']] = enum_entry['doc']
            continue

        if node.type not in {'declaration', 'function_definition'}:
            continue
        doc = _leading_doxygen_doc(content, node, comments)
        for function_declarator in _find_function_declarators(node):
            entry = _ast_function_entry(content, node, function_declarator, doc)
            if entry:
                functions.append(entry)
                if doc:
                    docs['functions'][entry['name']] = doc

    result_functions = functions if full_function_signatures else [entry['prototype'] for entry in functions]
    result: Dict[str, Any] = {
        'includes': list(dict.fromkeys(includes)),
        'macros': list(dict.fromkeys(macros)),
        'functions': result_functions,
        'structs': structs,
        'enums': enums,
        'docs': docs,
    }

    if include_used_symbols:
        clean_source = _strip_c_comments_and_strings_for_ast(content)
        used_functions = []
        for call in _find_nodes_by_type(tree.root_node, 'call_expression'):
            function = call.child_by_field_name('function')
            if function is not None and function.type == 'identifier':
                used_functions.append(_ast_node_text(function, content))
        result['used_functions'] = list(dict.fromkeys(used_functions))
        result['used_macro_like_tokens'] = list(dict.fromkeys(re.findall(r'\b[A-Z][A-Z0-9_]{2,}\b', clean_source)))

    return result


def _find_nodes_by_type(node, node_type: str) -> List[Any]:
    nodes = [node] if node.type == node_type else []
    for child in node.named_children:
        nodes.extend(_find_nodes_by_type(child, node_type))
    return nodes


def _normalize_click_token(value: str) -> str:
    """
    Normalize click token into the same compact style used elsewhere.
    Example:
        "Temp Hum 6" -> "temphum6"
        "relay-2" -> "relay2"

    Args:
        value (str): The Click name.

    Returns:
        str: The normalized Click name.
    """
    return (
        (value or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("&", "")
        .replace("-", "")
        .replace("click", "")
    )

def _replace_case_insensitive(text: str, old: str, new: str) -> str:
    """
    Replaces a substring case-insensitively.

    Args:
        text (str): The text to replace in.
        old (str): The substring to replace.
        new (str): The replacement substring.

    Returns:
        str: The text with the replaced substring.
    """
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)

def transform_board_name(board_name):
    """
    Transforms a Click board name into a valid Click board name (as present in the Elasticsearch index).

    Args:
        board_name (str): The Click board name.

    Returns:
        str: The transformed Click board name.
    """
    transformed = board_name.lower().replace("&", "").replace("-", "")
    if re.search(r'(?<=\s)click', transformed, re.I):
        transformed = re.sub(r'(?<=\s)click\S*', '', transformed, re.I)
    if re.search(r'(?<=\s)board', transformed, re.I):
        transformed = re.sub(r'(?<=\s)board\S*', '', transformed, re.I)
    transformed = transformed.replace(" ", "")
    return f"mikroe.click.{transformed}"

def click_name_from_elastic_item(es_item: Dict[str, Any]) -> str:
    """
    Extracts the Click name from an Elasticsearch item.

    Args:
        es_item (str): The Elasticsearch item.

    Returns:
        str: The Click name, without the 'mikroe.click.' prefix.
    """
    return f"{es_item['name'].replace('mikroe.click.', '').strip()}"

def _extract_header_symbols_flat(header_contents: List[str], full_function_signatures: bool = True) -> Dict[str, Any]:
    """
    Extract symbols from C header file contents.

    This function performs lightweight parsing of header files to collect commonly
    used API symbols and type definitions. It is designed for typical embedded
    driver headers (such as mikroSDK-style drivers) and works using robust
    regex-based heuristics rather than a full C parser.

    The following elements are extracted:

        1. Includes
           - Header imports from `#include` directives.

        2. Macros
           - Macro names defined using `#define`.
           - Supports multi-line macros using backslash continuations.

        3. Functions
           - Function declarations (prototypes ending with `;`)
           - Inline or static inline function definitions.
           - Captures return type, parameters, and full signature.

        4. Structs
           - `typedef struct { ... } name_t;` definitions.
           - Extracts struct name and its member fields.

        5. Enums
           - `typedef enum { ... } name_t;` definitions.
           - Extracts enum name and enumerator values.

    Notes / heuristics:
        - Commented-out code (`//` and `/* ... */`) is ignored before parsing.
        - The function uses regular-expression heuristics rather than full C syntax parsing.
        - Common control keywords (`if`, `for`, `while`, `switch`, `return`, etc.)
          are excluded when detecting functions.
        - Complex constructs (nested structs/unions, function pointers, bitfields,
          or multiple variables declared on one line) may not be fully parsed.
        - The function is optimized for typical embedded driver headers and API
          definitions rather than arbitrary C code.

    Args:
        header_contents (List[str]):
            List containing the textual contents of one or more header files.

        full_function_signatures (bool, optional):
            Controls the format of the returned function entries.

            - True (default):
                Returns detailed function metadata including return type,
                parameters, prototype, and full signature.

            - False:
                Returns only simplified prototypes such as:
                `func_name(arg1, arg2)`.

    Returns:
        Dict[str, Any]:
            Dictionary containing extracted symbols with the structure:

            {
                "includes": List[str],
                "macros": List[str],
                "functions": List[Dict] | List[str],
                "structs": List[Dict],
                "enums": List[Dict]
            }

            Example:
                {
                    "includes": ["drv_spi_master.h", "drv_digital_out.h"],
                    "macros": ["IPSDISPLAY2_CMD_NOP", "IPSDISPLAY2_CMD_SWRESET"],
                    "functions": [
                        {
                            "name": "ipsdisplay2_init",
                            "return_type": "err_t",
                            "params": "ipsdisplay2_t *ctx, ipsdisplay2_cfg_t *cfg",
                            "prototype": "ipsdisplay2_init(ipsdisplay2_t *ctx, ipsdisplay2_cfg_t *cfg)",
                            "full_signature": "err_t ipsdisplay2_init(ipsdisplay2_t *ctx, ipsdisplay2_cfg_t *cfg)"
                        }
                    ],
                    "structs": [
                        {
                            "name": "ipsdisplay2_cfg_t",
                            "members": [...]
                        }
                    ],
                    "enums": [
                        {
                            "name": "ipsdisplay2_return_value_t",
                            "members": [...]
                        }
                    ]
                }
    """

    if len(header_contents) == 1 and isinstance(header_contents[0], str):
        ast_symbols = _extract_c_symbols_with_tree_sitter(
            header_contents[0],
            full_function_signatures=full_function_signatures,
        )
        if ast_symbols is not None:
            return ast_symbols

    # HELPER FUNCTIONS

    def strip_comments_keep_strings(code: str) -> str:
        """
        Removes // and /* */ comments, but keeps string/char literals intact.

        Args:
            code (str): The code to strip comments from.

        Returns:
            str: The stripped code.
        """
        out = []
        i, n = 0, len(code)
        in_squote = False
        in_dquote = False
        while i < n:
            c = code[i]

            # Handle string/char literals
            if c == "'" and not in_dquote:
                out.append(c)
                i += 1
                # toggle unless escaped
                in_squote = not in_squote
                continue
            if c == '"' and not in_squote:
                out.append(c)
                i += 1
                in_dquote = not in_dquote
                continue

            if in_squote or in_dquote:
                # keep escapes
                if c == "\\" and i + 1 < n:
                    out.append(code[i:i+2])
                    i += 2
                else:
                    out.append(c)
                    i += 1
                continue

            # Line comment //
            if c == "/" and i + 1 < n and code[i + 1] == "/":
                i += 2
                while i < n and code[i] != "\n":
                    i += 1
                # keep newline if present
                if i < n and code[i] == "\n":
                    out.append("\n")
                    i += 1
                continue

            # Block comment /* ... */
            if c == "/" and i + 1 < n and code[i + 1] == "*":
                comment_start = i
                i += 2

                while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                    i += 1

                comment_end = i + 2 if i + 1 < n else i
                comment_text = code[comment_start:comment_end]

                # Preserve inline member docs like:
                #   field; /**< Description. */
                #   field; /*!< Description. */
                if comment_text.startswith("/**<") or comment_text.startswith("/*!<"):
                    out.append(comment_text)

                i = comment_end
                continue

            out.append(c)
            i += 1

        return "".join(out)

    include_re = re.compile(r'^[ \t]*#\s*include\s*(<[^>]+>|"[^"]+")\s*$', re.MULTILINE)

    # Capture #define NAME ... possibly spanning multiple lines with trailing backslashes.
    # Example:
    #   #define X(a) \
    #     a+1 \
    #     a+2
    macro_re = re.compile(
        r'^[ \t]*#\s*define[ \t]+(?P<name>[A-Za-z_]\w*(?:\s*\([^)]*\))?)'
        r'(?P<body>(?:[^\n\\]|\\(?!\r?\n))*(?:\\\r?\n(?:[^\n\\]|\\(?!\r?\n))*)*)',
        re.MULTILINE
    )

    typedef_struct_re = re.compile(
        r'''
        typedef\s+struct
        (?:\s+[A-Za-z_]\w*)?                # optional tagged struct name
        \s*\{
        (?P<body>.*?)
        \}\s*(?P<name>[A-Za-z_]\w*)\s*;
        ''',
        re.DOTALL | re.VERBOSE
    )

    typedef_enum_re = re.compile(
        r'''
        typedef\s+enum
        (?:\s+[A-Za-z_]\w*)?                # optional tagged enum name
        \s*\{
        (?P<body>.*?)
        \}\s*(?P<name>[A-Za-z_]\w*)\s*;
        ''',
        re.DOTALL | re.VERBOSE
    )

    # Very common C/C++ qualifiers and attributes seen in headers.
    # Allowed in front; also allowed - return types like "const char *" or "TEMPHIM_RETVAL".
    # Then match name(args) and either ";" or "{...}" (inline definition).
    func_start_re = re.compile(
        r'''
        ^[ \t]*                                                     # line start
        (?:extern[ \t]+"C"[ \t]*)?                                  # optional extern "C"
        (?P<prefix>(?:(?:static|inline|__inline__)\b[ \t]+)*)       # optional prefix qualifiers
        (?:__attribute__\s*\(\([^)]*\)\)\s*)*                       # optional GCC attributes
        (?P<ret>
            (?:
                [A-Za-z_]\w* | const | volatile | signed | unsigned |
                short | long | struct | enum | union
            )
            [^;(){}]*?
        )
        \b(?P<name>[A-Za-z_]\w*)\s*                                 # function name
        \(
            (?P<args>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)             # args (best-effort)
        \)
        [ \t\n\r]*
        (?P<tail>;|\{)                                              # declaration or definition
        ''',
        re.MULTILINE | re.VERBOSE
    )

    control_keywords = {"if", "for", "while", "switch", "return", "sizeof", "do"}

    # Improved extraction: including Doxygen comments for better context
    def _clean_doc_text(text: str) -> str:
        text = text or ""
        text = re.sub(r"@(li|c)\b", "", text)
        text = re.sub(r"#([A-Za-z_]\w*)", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip(" :.\n\t ")

        if not text:
            return ""

        return text if text.endswith((".", ":", ";", ",")) else f"{text}."


    def _parse_doxygen_comment(raw_comment: str) -> Dict[str, Any]:
        doc = {
            "brief": "",
            "details": "",
            "params": {},
            "return": "",
            "note": "",
        }

        lines = raw_comment.splitlines()
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            line = re.sub(r"^/\*\*?", "", line)
            line = re.sub(r"^/\*!", "", line)
            line = re.sub(r"\*/$", "", line)
            line = re.sub(r"^\*", "", line).strip()

            if line:
                cleaned_lines.append(line)

        current_field = None
        current_param = None

        def append_field(field: str, value: str):
            value = _clean_doc_text(value)
            if not value:
                return

            if doc[field]:
                doc[field] += " " + value
            else:
                doc[field] = value

        def append_param(param_name: str, value: str):
            value = _clean_doc_text(value)
            if not value:
                return

            if doc["params"].get(param_name):
                doc["params"][param_name] += " " + value
            else:
                doc["params"][param_name] = value

        for line in cleaned_lines:
            brief_match = re.match(r"@brief\s+(.*)", line)
            details_match = re.match(r"@details\s+(.*)", line)
            param_match = re.match(r"@param(?:\[[^\]]+\])?\s+([A-Za-z_]\w*)\s*:?\s*(.*)", line)
            return_match = re.match(r"@return\s+(.*)", line)
            note_match = re.match(r"@note\s+(.*)", line)

            if brief_match:
                current_field = "brief"
                current_param = None
                append_field("brief", brief_match.group(1))
                continue

            if details_match:
                current_field = "details"
                current_param = None
                append_field("details", details_match.group(1))
                continue

            if param_match:
                current_field = "params"
                current_param = param_match.group(1)
                append_param(current_param, param_match.group(2))
                continue

            if return_match:
                current_field = "return"
                current_param = None
                append_field("return", return_match.group(1))
                continue

            if note_match:
                current_field = "note"
                current_param = None
                append_field("note", note_match.group(1))
                continue

            # Handle Doxygen list/inline markers used as continuation lines, e.g.
            #   @return @li @c  0 - Success,
            #           @li @c -1 - Error.
            list_item_match = re.match(r"@(li|c)\b\s*(.*)", line)
            if list_item_match:
                continuation = list_item_match.group(2)

                if current_field == "params" and current_param:
                    append_param(current_param, continuation)
                elif current_field in {"brief", "details", "return", "note"}:
                    append_field(current_field, continuation)

                continue

            if line.startswith("@"):
                # Ignore unrelated Doxygen tags such as @addtogroup, @defgroup, @file, @{, @}.
                continue

            if current_field == "params" and current_param:
                append_param(current_param, line)
            elif current_field in {"brief", "details", "return", "note"}:
                append_field(current_field, line)
            elif not doc["brief"]:
                append_field("brief", line)

        return {
            key: value
            for key, value in doc.items()
            if value
        }


    def _doc_has_content(doc: Dict[str, Any]) -> bool:
        if not doc:
            return False

        for value in doc.values():
            if isinstance(value, dict) and value:
                return True
            if isinstance(value, str) and value.strip():
                return True

        return False


    def _find_documented_symbol(tail: str) -> tuple[str, str] | None:
        tail = tail.lstrip()

        macro_match = re.match(
            r"#\s*define\s+(?P<name>[A-Za-z_]\w*)",
            tail,
            flags=re.MULTILINE,
        )
        if macro_match:
            return "macros", macro_match.group("name")

        struct_match = typedef_struct_re.match(tail)
        if struct_match:
            return "structs", struct_match.group("name").strip()

        enum_match = typedef_enum_re.match(tail)
        if enum_match:
            return "enums", enum_match.group("name").strip()

        func_match = func_start_re.match(tail)
        if func_match:
            name = func_match.group("name").strip()
            if name not in control_keywords:
                return "functions", name

        return None


    def _collect_documented_symbols(content: str) -> Dict[str, Dict[str, Any]]:
        docs = {
            "functions": {},
            "macros": {},
            "structs": {},
            "enums": {},
        }

        comment_re = re.compile(
            r"(?:/\*\*|/\*!)[\s\S]*?\*/",
            re.DOTALL,
        )

        for match in comment_re.finditer(content):
            raw_comment = match.group(0)

            # Group-level comments should not be attached to the next concrete symbol.
            if re.search(r"@(addtogroup|defgroup)\b", raw_comment):
                continue
            if re.search(r"@\}\s*", raw_comment):
                continue

            parsed_doc = _parse_doxygen_comment(raw_comment)
            if not _doc_has_content(parsed_doc):
                continue

            symbol = _find_documented_symbol(content[match.end():])
            if not symbol:
                continue

            kind, name = symbol
            docs[kind][name] = parsed_doc

        return docs
    # # # # # # # # # #

    def extract_inlines_from(clean: str, start_idx: int) -> str:
        """
        Given index at '{', extract balanced-brace block.

        Args:
            clean (str): The code to extract from.
            start_idx (int): The index to start extracting from.

        Returns:
            str: The extracted code.
        """
        i = start_idx
        n = len(clean)
        if i >= n or clean[i] != "{":
            return ""
        depth = 0
        while i < n:
            if clean[i] == "{":
                depth += 1
            elif clean[i] == "}":
                depth -= 1
                if depth == 0:
                    return clean[start_idx:i + 1]
            i += 1
        # If unbalanced, return up to end (best-effort)
        return clean[start_idx:]

    def strip_inline_block_comments(s: str) -> str:
        """
        Strips inline block comments from a string.

        Args:
            s (str): The string to strip comments from.

        Returns:
            str: The stripped string.
        """
        return re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)

    def parse_struct_members(body: str) -> List[Dict[str, str]]:
        """
        Parses struct members from a string.

        Args:
            body (str): The string to parse.

        Returns:
            List[Dict[str, str]]: The parsed struct members.
        """
        members: List[Dict[str, str]] = []

        for raw_line in body.splitlines():
            line = raw_line.strip()

            if not line:
                continue
            if line.startswith("//"):
                continue
            if line in {"{", "}"}:
                continue
            if line.startswith("#"):
                continue
            if re.match(r"^(typedef|struct|enum|union)\b", line):
                continue

            description = ""

            inline_doc_match = re.search(
                r"/\*\*<\s*(.*?)\s*\*/|/\*!<\s*(.*?)\s*\*/",
                line,
                flags=re.DOTALL,
            )
            if inline_doc_match:
                description = _clean_doc_text(
                    inline_doc_match.group(1) or inline_doc_match.group(2) or ""
                )
                line = line[:inline_doc_match.start()].strip()
            else:
                line = strip_inline_block_comments(line).strip()

            if not line.endswith(";"):
                continue

            line = line[:-1].strip()
            line = re.sub(r"\s+", " ", line)

            m = re.match(
                r"^(?P<type>.+?)\s+(?P<name>\*?[A-Za-z_]\w*(?:\[[^\]]*\])*)$",
                line,
            )

            if not m:
                m = re.match(
                    r"^(?P<type>.+?[*\s])(?P<name>[A-Za-z_]\w*(?:\[[^\]]*\])*)$",
                    line,
                )

            if m:
                field_type = m.group("type").strip()
                field_name = m.group("name").strip()

                while field_name.startswith("*"):
                    field_type += " *"
                    field_name = field_name[1:].strip()

                member = {
                    "type": re.sub(r"\s+", " ", field_type).strip(),
                    "name": field_name,
                }

                if description:
                    member["description"] = description

                members.append(member)

        return members

    def parse_enum_members(body: str) -> List[Dict[str, str]]:
        """
        Parses enum members from a string.

        Args:
            body (str): The string to parse.

        Returns:
            List[Dict[str, str]]: The parsed enum members.
        """
        members: List[Dict[str, str]] = []

        body = strip_inline_block_comments(body)
        lines = body.splitlines()

        current = ""
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            current += (" " + line if current else line)

            if "," in line:
                parts = current.split(",")
                for part in parts[:-1]:
                    entry = part.strip()
                    if not entry:
                        continue

                    m = re.match(r'^(?P<name>[A-Za-z_]\w*)(?:\s*=\s*(?P<value>.+))?$', entry)
                    if m:
                        members.append({
                            "name": m.group("name").strip(),
                            "value": (m.group("value") or "").strip()
                        })
                current = parts[-1].strip()

        # tail without trailing comma
        if current:
            entry = current.strip()
            m = re.match(r'^(?P<name>[A-Za-z_]\w*)(?:\s*=\s*(?P<value>.+))?$', entry)
            if m:
                members.append({
                    "name": m.group("name").strip(),
                    "value": (m.group("value") or "").strip()
                })

        return members

    # Deduplicate while keeping order
    def dedupe(seq: List[str]) -> List[str]:
        """
        Deduplicates a list while keeping order.

        Args:
            seq (List[str]): The list to deduplicate.

        Returns:
            List[str]: The deduplicated list.
        """
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    includes: List[str] = []
    macros: List[Dict[str, str]] = []
    functions: List[Dict[str, str]] = []
    structs: List[Dict[str, Any]] = []
    enums: List[Dict[str, Any]] = []

    for content in header_contents:
        if not isinstance(content, str):
            content = str(content)

        # Collect all documented symbols
        symbol_docs = _collect_documented_symbols(content)

        clean = strip_comments_keep_strings(content)

        # --- includes
        for m in include_re.finditer(clean):
            inc = m.group(1)
            if inc.startswith('"') and inc.endswith('"'):
                inc = inc[1:-1]
            includes.append(inc)

        # --- macros
        for m in macro_re.finditer(clean):
            raw_name = m.group("name").strip()
            full = m.group(0).rstrip()

            # normalize function-like macro names:
            # "ACCEL14_MAP_MIKROBUS( cfg, mikrobus )" -> "ACCEL14_MAP_MIKROBUS"
            macro_name = re.sub(r'\s*\(.*$', '', raw_name).strip()

            if macro_name:
                macros.append({
                    "name": macro_name,
                    "definition": full
                })

        # Remove macro definitions before parsing structs/enums/functions.
        # Otherwise function-like expressions inside macro bodies, such as MIKROBUS(...),
        # can be falsely detected by the function parser.
        clean_for_symbols = macro_re.sub("", clean)

        # --- typedef structs
        for m in typedef_struct_re.finditer(clean_for_symbols):
            struct_name = m.group("name").strip()
            struct_body = m.group("body")
            struct_item = {
                "name": struct_name,
                "kind": "struct",
                "members": parse_struct_members(struct_body)
            }
            struct_doc = symbol_docs["structs"].get(struct_name)
            if struct_doc:
                struct_item["doc"] = struct_doc
            structs.append(struct_item)

        # --- typedef enums
        for m in typedef_enum_re.finditer(clean_for_symbols):
            enum_name = m.group("name").strip()
            enum_body = m.group("body")
            enum_item = {
                "name": enum_name,
                "kind": "enum",
                "members": parse_enum_members(enum_body)
            }
            enum_doc = symbol_docs["enums"].get(enum_name)
            if enum_doc:
                enum_item["doc"] = enum_doc
            enums.append(enum_item)

        # --- functions (prototypes + inline defs)
        # We'll scan from start; when we match a definition tail '{', we extract its full block.
        idx = 0
        while True:
            m = func_start_re.search(clean_for_symbols, idx)
            if not m:
                break

            fname = m.group("name").strip()
            fargs = " ".join(m.group("args").split())
            fret = " ".join(m.group("ret").split())
            fprefix = " ".join(m.group("prefix").split())

            if fname in control_keywords:
                idx = m.end()
                continue

            tail = m.group("tail")
            start = m.start()
            end = m.end()

            prototype = f"{fname}({fargs})"
            full_signature = f"{fret} {prototype}".strip()
            if fprefix:
                full_signature = f"{fprefix} {full_signature}".strip()

            if tail == ";":
                decl = clean_for_symbols[start:end].strip()
                functions.append({
                    "name": fname,
                    "return_type": fret,
                    "params": fargs,
                    "prototype": prototype,
                    "full_signature": full_signature,
                    "signature": decl,
                    "kind": "declaration"
                })
                idx = end
            else:
                block = extract_inlines_from(clean_for_symbols, end - 1)
                full_def = (clean_for_symbols[start:end - 1] + block).strip()
                functions.append({
                    "name": fname,
                    "return_type": fret,
                    "params": fargs,
                    "prototype": prototype,
                    "full_signature": full_signature,
                    "signature": full_def,
                    "kind": "definition"
                })
                idx = end - 1 + len(block)

    includes = dedupe(includes)

    # For macros/functions, dedupe by exact text (stable, avoids losing overload-ish variations)
    def dedupe_dicts(items: List[Dict[str, str]], key: str) -> List[Dict[str, str]]:
        seen = set()
        out = []
        for it in items:
            v = it.get(key, "")
            if v and v not in seen:
                seen.add(v)
                out.append(it)
        return out

    macros = dedupe_dicts(macros, "definition")
    macros = [macro.get("name", "") for macro in macros if macro.get("name", "")]

    structs = dedupe_dicts(structs, "name")
    enums = dedupe_dicts(enums, "name")

    functions = dedupe_dicts(functions, "signature")
    functions = dedupe_dicts(functions, "full_signature")
    functions_full = [
        {
            "name": func.get("name", ""),
            "return_type": func.get("return_type", ""),
            "params": func.get("params", ""),
            "prototype": func.get("prototype", ""),
            "full_signature": func.get("full_signature", ""),
            **(
                {"doc": symbol_docs["functions"].get(func.get("name", ""))}
                if symbol_docs["functions"].get(func.get("name", ""))
                else {}
            ),
        }
        for func in functions
        if func.get("name", "")
    ]

    if not full_function_signatures:
        functions = [func.get("prototype") if func.get("prototype", "") else func.get("name", "") for func in functions_full]
    else:
        functions = functions_full

    return {
        "includes": includes,
        "macros": macros,
        "functions": functions,
        "structs": structs,
        "enums": enums,
        "docs": symbol_docs,
    }

def merge_symbol_entries(
    extracted_maps: List[Dict[str, Any]],
    include_used_symbols: bool = False,
) -> Dict[str, Any]:
    """
    Merge multiple flat symbol dictionaries into one flat symbol dictionary.

    Args:
        extracted_maps (List[Dict[str, Any]]): The list of extracted symbol dictionaries.
        include_used_symbols (bool, optional): Whether to include used symbols. Defaults to False.

    Returns:
        Dict[str, Any]: The merged symbol dictionary.
    """

    def dedupe(seq):
        seen = set()
        out = []
        for item in seq:
            key = json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    def dedupe_by_name(seq):
        seen = set()
        out = []
        for item in seq:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(item)
        return out

    merged = {
        "includes": [],
        "macros": [],
        "functions": [],
        "structs": [],
        "enums": [],
        "docs": {
            "functions": {},
            "macros": {},
            "structs": {},
            "enums": {},
        },
    }

    if include_used_symbols:
        merged["used_functions"] = []
        merged["used_macro_like_tokens"] = []

    for item in extracted_maps:
        merged["includes"].extend(item.get("includes", []))
        merged["macros"].extend(item.get("macros", []))
        merged["functions"].extend(item.get("functions", []))
        merged["structs"].extend(item.get("structs", []))
        merged["enums"].extend(item.get("enums", []))

        docs = item.get("docs", {}) or {}
        for doc_kind in ["functions", "macros", "structs", "enums"]:
            merged["docs"][doc_kind].update(docs.get(doc_kind, {}) or {})

        if include_used_symbols:
            merged["used_functions"].extend(item.get("used_functions", []))
            merged["used_macro_like_tokens"].extend(item.get("used_macro_like_tokens", []))

    merged["includes"] = dedupe(merged["includes"])
    merged["macros"] = dedupe(merged["macros"])
    merged["functions"] = dedupe(merged["functions"])
    merged["structs"] = dedupe_by_name(merged["structs"])
    merged["enums"] = dedupe_by_name(merged["enums"])

    if include_used_symbols:
        merged["used_functions"] = dedupe(merged["used_functions"])
        merged["used_macro_like_tokens"] = dedupe(merged["used_macro_like_tokens"])

    return merged

def extract_header_symbols(
    header_contents: List[str] | Dict[str, str],
    full_function_signatures: bool = True
) -> Dict[str, Any]:
    """
    Always returns rich structure:
    {
        "merged": {...},
        "by_source": {...}
    }

    Args:
        header_contents (List[str] | Dict[str, str]): The header contents to extract symbols from.
        full_function_signatures (bool, optional): Whether to extract full function signatures. Defaults to True.

    Returns:
        Dict[str, Any]: The extracted symbols.
    """

    if isinstance(header_contents, dict):
        by_source = {
            source_name: _extract_header_symbols_flat([content], full_function_signatures=full_function_signatures)
            for source_name, content in header_contents.items()
        }
    else:
        by_source = {
            f"__source_{idx}__": _extract_header_symbols_flat([content], full_function_signatures=full_function_signatures)
            for idx, content in enumerate(header_contents)
        }

    merged = merge_symbol_entries(list(by_source.values()), include_used_symbols=False)

    return {
        "merged": merged,
        "by_source": by_source,
    }

def ensure_includes_in_c_file(main_c: str, symbols: Dict[str, Any]) -> Tuple[str, bool]:
    """
    Ensures all includes from symbols["includes"] exist in the given C file content.
    Adds missing ones in a sensible place:
      - after any leading file comment block / banner
      - after #pragma once / include guards (if present)
      - after existing includes (preferred)
      - otherwise near the top

    Notes:
      - Treats "board.h" etc as #include "board.h" (quoted) unless the include string
        already contains <...> or "..."
      - Deduplicates and keeps original include lines as-is.

    Args:
        main_c (str): The C file content.
        symbols (Dict[str, Any]): The symbols dictionary.

    Returns:
        str, bool: The updated C file content and whether it was modified.
    """
    file_modified = False
    includes: List[str] = list(symbols.get("includes") or [])

    def normalize_include_token(x: str) -> str:
        x = x.strip()
        # if already looks like <...> or "..."
        if (len(x) >= 2 and ((x[0] == "<" and x[-1] == ">") or (x[0] == '"' and x[-1] == '"'))):
            return x
        # bare header -> quoted include
        return f'"{x}"'

    # Build a set of already included headers (both <...> and "...")
    include_line_re = re.compile(r'^[ \t]*#\s*include\s*(?P<hdr><[^>]+>|"[^"]+")\s*$', re.MULTILINE)
    existing_hdrs = {m.group("hdr") for m in include_line_re.finditer(main_c)}

    desired_hdrs = [normalize_include_token(h) for h in includes]
    missing_hdrs = [h for h in desired_hdrs if h not in existing_hdrs]

    # If no headers to add, return unchanged file
    if not missing_hdrs:
        return main_c, file_modified
    # Otherwise, set file_modified to True and proceed
    file_modified = True

    # Decide insertion point:
    # Prefer: after last existing #include line.
    last_inc = None
    for m in include_line_re.finditer(main_c):
        last_inc = m

    insertion_idx = None
    prefix = ""
    suffix = ""

    if last_inc:
        insertion_idx = last_inc.end()
        prefix = "\n"
        suffix = ""
    else:
        # No includes present. Insert after initial banner comment if any.
        banner_re = re.compile(
            r'^\s*(?:/\*.*?\*/\s*|//[^\n]*\n\s*)+',
            re.DOTALL
        )
        bm = banner_re.match(main_c)
        if bm:
            insertion_idx = bm.end()
            # ensure one blank line after banner before includes
            prefix = "\n" if not main_c[:insertion_idx].endswith("\n") else ""
            suffix = "\n"
        else:
            insertion_idx = 0
            prefix = ""
            suffix = "\n"

    # Compose block (keep stable order; add each include once)
    block_lines = [f'#include {h}' for h in missing_hdrs]
    block = prefix + "\n".join(block_lines) + suffix

    return main_c[:insertion_idx] + block + main_c[insertion_idx:], file_modified

def extract_code_blocks(
    text: str,
    largest_only: bool = False
) -> List[Dict[str, str]] | Dict[str, str]:
    """
    Extract Markdown fenced code blocks from text.

    If largest_only is False:
        returns a list of:
        {
            "language": str,
            "code": str,
            "full_match": str,
            "start": int,
            "end": int,
            "prefix": str,
            "suffix": str,
        }

    If largest_only is True:
        returns the single largest block with the same structure,
        or {} if none found.

    Args:
        text (str): The text to extract code blocks from.
        largest_only (bool, optional): Whether to return the largest code block only. Defaults to False.

    Returns:
        Dict[str, str] | Dict[str, str]: The extracted code blocks.
    """
    code_block_re = re.compile(
        r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<code>.*?)```",
        re.DOTALL
    )

    blocks: List[Dict[str, str]] = []

    for m in code_block_re.finditer(text):
        start, end = m.span()
        blocks.append({
            "language": m.group("lang") or "",
            "code": m.group("code").rstrip(),
            "full_match": m.group(0),
            "start": start,
            "end": end,
            "prefix": text[:start].rstrip(),
            "suffix": text[end:].lstrip(),
        })

    if not blocks:
        return {} if largest_only else []

    if largest_only:
        return max(blocks, key=lambda b: len(b["code"]))

    return blocks

def _extract_c_source_symbols_flat(
    source_contents: List[str],
    full_function_signatures: bool = True,
    include_used_symbols: bool = False,
) -> Dict[str, Any]:
    """
    Extract symbols from C source-like contents (templates, main.c examples, etc.).

    Extracted elements:
        - includes
        - macros
        - functions
        - structs
        - enums

    Optionally also extracts:
        - used_functions
        - used_macro_like_tokens

    This is intended for `.c` template files or generated code where function
    definitions are present, unlike pure headers which mostly contain prototypes.

    Args:
        souce_contents (List[str]): The source code to parse.
        full_function_signatures (bool, optional): Whether to extract full function signatures. Defaults to True.
        include_used_symbols (bool, optional): Whether to extract used symbols. Defaults to False.

    Returns:
        Dict[str, Any]: The parsed code - extracted function signatures, used symbols, etc.
    """

    if len(source_contents) == 1 and isinstance(source_contents[0], str):
        ast_symbols = _extract_c_symbols_with_tree_sitter(
            source_contents[0],
            full_function_signatures=full_function_signatures,
            include_used_symbols=include_used_symbols,
        )
        if ast_symbols is not None:
            return ast_symbols

    def strip_comments_keep_strings(code: str) -> str:
        """
        Strips comments from the code.

        Args:
            code (str): The code to strip comments from.

        Returns:
            str: The stripped code.
        """
        out = []
        i, n = 0, len(code)
        in_squote = False
        in_dquote = False

        while i < n:
            c = code[i]

            if c == "'" and not in_dquote:
                out.append(c)
                i += 1
                in_squote = not in_squote
                continue

            if c == '"' and not in_squote:
                out.append(c)
                i += 1
                in_dquote = not in_dquote
                continue

            if in_squote or in_dquote:
                if c == "\\" and i + 1 < n:
                    out.append(code[i:i+2])
                    i += 2
                else:
                    out.append(c)
                    i += 1
                continue

            if c == "/" and i + 1 < n and code[i + 1] == "/":
                i += 2
                while i < n and code[i] != "\n":
                    i += 1
                if i < n and code[i] == "\n":
                    out.append("\n")
                    i += 1
                continue

            if c == "/" and i + 1 < n and code[i + 1] == "*":
                i += 2
                while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                    i += 1
                i += 2 if i + 1 < n else 0
                continue

            out.append(c)
            i += 1

        return "".join(out)

    def strip_comments_and_strings(code: str) -> str:
        """
        Removes comments and replaces string/char literals with placeholders
        so usage scanning does not pick up words from user-facing text.

        Args:
            code (str): The code to strip comments and strings from.

        Returns:
            str: The stripped code.
        """
        out = []
        i, n = 0, len(code)
        in_squote = False
        in_dquote = False

        while i < n:
            c = code[i]

            # line comment
            if not in_squote and not in_dquote and c == "/" and i + 1 < n and code[i + 1] == "/":
                i += 2
                while i < n and code[i] != "\n":
                    i += 1
                if i < n:
                    out.append("\n")
                    i += 1
                continue

            # block comment
            if not in_squote and not in_dquote and c == "/" and i + 1 < n and code[i + 1] == "*":
                i += 2
                while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                    i += 1
                i += 2 if i + 1 < n else 0
                continue

            # string literal
            if not in_squote and c == '"':
                out.append('""')
                i += 1
                while i < n:
                    if code[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if code[i] == '"':
                        i += 1
                        break
                    i += 1
                continue

            # char literal
            if not in_dquote and c == "'":
                out.append("'_'")
                i += 1
                while i < n:
                    if code[i] == "\\" and i + 1 < n:
                        i += 2
                        continue
                    if code[i] == "'":
                        i += 1
                        break
                    i += 1
                continue

            out.append(c)
            i += 1

        return "".join(out)

    def strip_inline_block_comments(s: str) -> str:
        """
        Strips inline block comments from the code.

        Args:
            s (str): The code to strip comments from.

        Returns:
            str: The stripped code.
        """
        return re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)

    def parse_struct_members(body: str) -> List[Dict[str, str]]:
        """
        Parses struct members from a string.

        Args:
            body (str): The string to parse.

        Returns:
            List[Dict[str, str]]: The parsed struct members.
        """
        members: List[Dict[str, str]] = []
        body = strip_inline_block_comments(body)

        for raw_line in body.splitlines():
            line = raw_line.strip()

            if not line or line.startswith("//") or line in {"{", "}"}:
                continue
            if line.startswith("#"):
                continue
            if re.match(r'^(typedef|struct|enum|union)\b', line):
                continue
            if not line.endswith(";"):
                continue

            line = line[:-1].strip()
            line = re.sub(r'\s+', ' ', line)

            m = re.match(
                r'^(?P<type>.+?)\s+(?P<name>\*?[A-Za-z_]\w*(?:\[[^\]]*\])*)$',
                line
            )
            if not m:
                m = re.match(
                    r'^(?P<type>.+?[*\s])(?P<name>[A-Za-z_]\w*(?:\[[^\]]*\])*)$',
                    line
                )

            if m:
                field_type = m.group("type").strip()
                field_name = m.group("name").strip()

                while field_name.startswith("*"):
                    field_type += " *"
                    field_name = field_name[1:].strip()

                members.append({
                    "type": re.sub(r'\s+', ' ', field_type).strip(),
                    "name": field_name
                })

        return members

    def parse_enum_members(body: str) -> List[Dict[str, str]]:
        """
        Parses enum members from a string.

        Args:
            body (str): The string to parse.

        Returns:
            List[Dict[str, str]]: The parsed enum members.
        """
        members: List[Dict[str, str]] = []
        body = strip_inline_block_comments(body)
        lines = body.splitlines()

        current = ""
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            current += (" " + line if current else line)

            if "," in line:
                parts = current.split(",")
                for part in parts[:-1]:
                    entry = part.strip()
                    if not entry:
                        continue

                    m = re.match(r'^(?P<name>[A-Za-z_]\w*)(?:\s*=\s*(?P<value>.+))?$', entry)
                    if m:
                        members.append({
                            "name": m.group("name").strip(),
                            "value": (m.group("value") or "").strip()
                        })
                current = parts[-1].strip()

        if current:
            entry = current.strip()
            m = re.match(r'^(?P<name>[A-Za-z_]\w*)(?:\s*=\s*(?P<value>.+))?$', entry)
            if m:
                members.append({
                    "name": m.group("name").strip(),
                    "value": (m.group("value") or "").strip()
                })

        return members

    def extract_inlines_from(clean: str, start_idx: int) -> str:
        """
        Extracts inline symbols from the code.

        Args:
            clean (str): The code to extract symbols from.
            start_idx (int): The start index to extract symbols from.

        Returns:
            str: The extracted symbols.
        """
        i = start_idx
        n = len(clean)
        if i >= n or clean[i] != "{":
            return ""
        depth = 0
        while i < n:
            if clean[i] == "{":
                depth += 1
            elif clean[i] == "}":
                depth -= 1
                if depth == 0:
                    return clean[start_idx:i + 1]
            i += 1
        return clean[start_idx:]

    include_re = re.compile(r'^[ \t]*#\s*include\s*(<[^>]+>|"[^"]+")\s*$', re.MULTILINE)

    macro_re = re.compile(
        r'^[ \t]*#\s*define[ \t]+(?P<name>[A-Za-z_]\w*(?:\s*\([^)]*\))?)'
        r'(?P<body>[^\n]*?)'
        r'(?:\\\n[^\n]*)*',
        re.MULTILINE
    )

    typedef_struct_re = re.compile(
        r'''
        typedef\s+struct
        (?:\s+[A-Za-z_]\w*)?
        \s*\{
        (?P<body>.*?)
        \}\s*(?P<name>[A-Za-z_]\w*)\s*;
        ''',
        re.DOTALL | re.VERBOSE
    )

    typedef_enum_re = re.compile(
        r'''
        typedef\s+enum
        (?:\s+[A-Za-z_]\w*)?
        \s*\{
        (?P<body>.*?)
        \}\s*(?P<name>[A-Za-z_]\w*)\s*;
        ''',
        re.DOTALL | re.VERBOSE
    )

    func_re = re.compile(
        r'''
        ^[ \t]*
        (?:extern[ \t]+"C"[ \t]*)?
        (?P<prefix>(?:(?:static|inline|__inline__)\b[ \t]+)*)
        (?:__attribute__\s*\(\([^)]*\)\)\s*)*
        (?P<ret>
            (?:
                [A-Za-z_]\w* | const | volatile | signed | unsigned |
                short | long | struct | enum | union
            )
            [^;(){}]*?
        )
        \b(?P<name>[A-Za-z_]\w*)\s*
        \(
            (?P<args>[^;{}()]*(?:\([^()]*\)[^;{}()]*)*)
        \)
        [ \t\n\r]*
        (?P<tail>;|\{)
        ''',
        re.MULTILINE | re.VERBOSE
    )

    control_keywords = {"if", "for", "while", "switch", "return", "sizeof", "do"}

    includes: List[str] = []
    macros: List[Dict[str, str]] = []
    functions: List[Dict[str, str]] = []
    structs: List[Dict[str, Any]] = []
    enums: List[Dict[str, Any]] = []

    used_functions: List[str] = []
    used_macro_like_tokens: List[str] = []

    for content in source_contents:
        if not isinstance(content, str):
            content = str(content)

        clean = strip_comments_keep_strings(content)

        for m in include_re.finditer(clean):
            inc = m.group(1)
            if inc.startswith('"') and inc.endswith('"'):
                inc = inc[1:-1]
            includes.append(inc)

        for m in macro_re.finditer(clean):
            raw_name = m.group("name").strip()
            full = m.group(0).rstrip()
            macro_name = re.sub(r'\s*\(.*$', '', raw_name).strip()
            if macro_name:
                macros.append({
                    "name": macro_name,
                    "definition": full
                })

        for m in typedef_struct_re.finditer(clean):
            structs.append({
                "name": m.group("name").strip(),
                "kind": "struct",
                "members": parse_struct_members(m.group("body"))
            })

        for m in typedef_enum_re.finditer(clean):
            enums.append({
                "name": m.group("name").strip(),
                "kind": "enum",
                "members": parse_enum_members(m.group("body"))
            })

        idx = 0
        while True:
            m = func_re.search(clean, idx)
            if not m:
                break

            fname = m.group("name").strip()
            if fname in control_keywords:
                idx = m.end()
                continue

            fargs = " ".join(m.group("args").split())
            fret = " ".join(m.group("ret").split())
            fprefix = " ".join(m.group("prefix").split())

            tail = m.group("tail")
            start = m.start()
            end = m.end()

            prototype = f"{fname}({fargs})"
            full_signature = f"{fret} {prototype}".strip()
            if fprefix:
                full_signature = f"{fprefix} {full_signature}".strip()

            if tail == ";":
                decl = clean[start:end].strip()
                functions.append({
                    "name": fname,
                    "return_type": fret,
                    "params": fargs,
                    "prototype": prototype,
                    "full_signature": full_signature,
                    "signature": decl,
                    "kind": "declaration"
                })
                idx = end
            else:
                block = extract_inlines_from(clean, end - 1)
                full_def = (clean[start:end - 1] + block).strip()
                functions.append({
                    "name": fname,
                    "return_type": fret,
                    "params": fargs,
                    "prototype": prototype,
                    "full_signature": full_signature,
                    "signature": full_def,
                    "kind": "definition"
                })
                idx = end - 1 + len(block)

        if include_used_symbols:
            clean_for_usage = strip_comments_and_strings(content)

            curr_used_functions = [
                m.group(1)
                for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\(', clean_for_usage)
                if m.group(1) not in control_keywords
            ]
            used_functions.extend(curr_used_functions)

            curr_used_macro_like_tokens = re.findall(
                r'\b[A-Z][A-Z0-9_]{2,}\b',
                clean_for_usage
            )
            used_macro_like_tokens.extend(curr_used_macro_like_tokens)

    def dedupe(seq: List[str]) -> List[str]:
        """
        Deduplicates a list.

        Args:
            seq (List[str]): The list to deduplicate.

        Returns:
            List[str]: The deduplicated list.
        """
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def dedupe_dicts(items: List[Dict[str, str]], key: str) -> List[Dict[str, str]]:
        """
        Deduplicates a list of dictionaries by a key.

        Args:
            items (List[Dict[str, str]]): The list of dictionaries to deduplicate.
            key (str): The key to deduplicate by.

        Returns:
            List[Dict[str, str]]: The deduplicated list of dictionaries.
        """
        seen = set()
        out = []
        for it in items:
            v = it.get(key, "")
            if v and v not in seen:
                seen.add(v)
                out.append(it)
        return out

    includes = dedupe(includes)
    macros = dedupe_dicts(macros, "definition")
    macros = [macro.get("name", "") for macro in macros if macro.get("name", "")]
    structs = dedupe_dicts(structs, "name")
    enums = dedupe_dicts(enums, "name")
    functions = dedupe_dicts(functions, "signature")
    functions = dedupe_dicts(functions, "full_signature")

    functions_full = [
        {
            "name": func.get("name", ""),
            "return_type": func.get("return_type", ""),
            "params": func.get("params", ""),
            "prototype": func.get("prototype", ""),
            "full_signature": func.get("full_signature", "")
        }
        for func in functions
        if func.get("name", "")
    ]

    if not full_function_signatures:
        functions_out = [
            func.get("prototype") if func.get("prototype", "") else func.get("name", "")
            for func in functions_full
        ]
    else:
        functions_out = functions_full

    result = {
        "includes": includes,
        "macros": macros,
        "functions": functions_out,
        "structs": structs,
        "enums": enums,
    }

    if include_used_symbols:
        result["used_functions"] = dedupe(used_functions)
        result["used_macro_like_tokens"] = dedupe(used_macro_like_tokens)

    return result

def extract_c_source_symbols(
    source_contents: List[str] | Dict[str, str],
    full_function_signatures: bool = True,
    include_used_symbols: bool = False,
) -> Dict[str, Any]:
    """
    Always returns rich structure:
    {
        "merged": {...},
        "by_source": {...}
    }

    Args:
        source_contents (List[str] | Dict[str, str]): The source code to parse.
        full_function_signatures (bool, optional): Whether to extract full function signatures. Defaults to True.
        include_used_symbols (bool, optional): Whether to extract used symbols. Defaults to False.

    Returns:
        Dict[str, Any]: The parsed code - extracted function signatures, used symbols, etc.
    """

    if isinstance(source_contents, dict):
        by_source = {
            source_name: _extract_c_source_symbols_flat(
                [content],
                full_function_signatures=full_function_signatures,
                include_used_symbols=include_used_symbols,
            )
            for source_name, content in source_contents.items()
        }
    else:
        by_source = {
            f"__source_{idx}__": _extract_c_source_symbols_flat(
                [content],
                full_function_signatures=full_function_signatures,
                include_used_symbols=include_used_symbols,
            )
            for idx, content in enumerate(source_contents)
        }

    merged = merge_symbol_entries(
        list(by_source.values()),
        include_used_symbols=include_used_symbols,
    )

    return {
        "merged": merged,
        "by_source": by_source,
    }

def flatten_extracted_symbols(symbols: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten rich extracted symbol structure into the legacy merged format.

    Accepts either:
    - already-flat legacy structure
    - rich structure with {"merged": ..., "by_source": ...}

    Args:
        symbols (Dict[str, Any]): The symbols to flatten.

    Returns:
        Dict[str, Any]: merged/flat symbols only
    """
    if not isinstance(symbols, dict):
        return symbols

    if "merged" in symbols and isinstance(symbols["merged"], dict):
        return symbols["merged"]

    return symbols

def strip_extracted_symbol_docs(symbols: Any) -> Any:
    """
    Remove documentation-only fields from extracted symbol structures.

    Generation benefits from docs, but deterministic/LLM verification should stay
    symbol-focused and token-light.
    """

    if isinstance(symbols, dict):
        return {
            key: strip_extracted_symbol_docs(value)
            for key, value in symbols.items()
            if key not in {"doc", "docs", "description"}
        }

    if isinstance(symbols, list):
        return [
            strip_extracted_symbol_docs(item)
            for item in symbols
        ]

    return symbols
# # # # # # # # # #

# ---------------------------------------------------------------------------
# Code Generation Session (CLICK/LVGL/LCD Group)
# ---------------------------------------------------------------------------

# LEGACY:
# def generate_code_context(
#     mikrobus_pairs: str | list | tuple | None,
#     example_codes: Dict[str, Tuple[str, str]],
# ):
#     def normalize_mikrobus_pairs(value) -> str:
#         if value is None:
#             return ""
#         if isinstance(value, str):
#             return value
#         if isinstance(value, (list, tuple)):
#             return " ".join(str(item) for item in value if item is not None)
#         return str(value)

#     def extract_base_folder_name(click_name: str) -> str:
#         folder_name = click_name.replace("mikroe.click.", "").strip()
#         match = re.search(r'\S+[^0-9](?=[0-9]+$)', folder_name)
#         if match:
#             folder_name = match.group(0)
#         return folder_name

#     def find_assigned_mikrobus(folder_name: str, mikrobus_pairs_text: str) -> str | None:
#         escaped_folder = re.escape(folder_name)
#         match = re.search(rf'(?<={escaped_folder})[0-9]*[\s/]+([0-9]+)', mikrobus_pairs_text)
#         if match:
#             return match.group(1)
#         return None

#     mikrobus_pairs_text = normalize_mikrobus_pairs(mikrobus_pairs)

#     code_context = ""
#     free_mikrobus = ['1', '2', '3', '4', '5']

#     # Form the list of free mikroBUS sockets
#     for click_name, code in example_codes.items():
#         folder_name = extract_base_folder_name(click_name)
#         mikrobus = find_assigned_mikrobus(folder_name, mikrobus_pairs_text)
#         if mikrobus in free_mikrobus:
#             free_mikrobus.remove(mikrobus)

#     # Form context with example code changed to adjust the MIKROBUS sockets
#     for click_name, code in example_codes.items():
#         folder_name = extract_base_folder_name(click_name)
#         _log_success(f"FOUND CLICK:   {folder_name}\n")

#         changed_code = code[0]
#         mikrobus = find_assigned_mikrobus(folder_name, mikrobus_pairs_text)

#         if mikrobus is not None:
#             if mikrobus == '0':
#                 if free_mikrobus:
#                     mikrobus = free_mikrobus.pop(0)
#             changed_code = re.sub(r'(?<=MIKROBUS_)[0-9]+', mikrobus, changed_code)

#         code_context += (
#             f"\n--- Click Board: {click_name} "
#             f"(Folder: {click_name.replace('mikroe.click.', '').strip()}) ---\n"
#             f"{changed_code}\n"
#         )

#     if code_context:
#         code_context = (
#             "Below are the example main.c source codes for each validated Click board:\n"
#             + code_context
#         )

#     return code_context

# NEW:
def generate_code_context_data(
    mikrobus_map: Dict[str, int] | None,
    example_codes: Dict[str, Tuple[str, str]],
) -> Dict[str, Any]:
    """
    Structured version of code context generation.

    Args:
        mikrobus_map (Dict[str, int] | None): The MIKROBUS map.
        example_codes (Dict[str, Tuple[str, str]]): The example codes.

    Returns:
        Dict[str, Any]: The code context data.
    """
    mikrobus_map = mikrobus_map or {}

    def extract_base_folder_name(click_name: str) -> str:
        """
        Extracts the base folder name from a Click board name.

        Args:
            click_name (str): The Click board name.

        Returns:
            str: The base folder name.
        """
        folder_name = click_name.replace("mikroe.click.", "").strip()
        match = re.search(r'\S+[^0-9](?=[0-9]+$)', folder_name)
        if match:
            folder_name = match.group(0)
        return folder_name

    adjusted_examples: Dict[str, str] = {}
    free_mikrobus = ["1", "2", "3", "4", "5"]

    for click_name in example_codes.keys():
        folder_name = extract_base_folder_name(click_name)
        assigned = mikrobus_map.get(_normalize_click_token(folder_name))
        if assigned is not None and str(assigned) in free_mikrobus:
            free_mikrobus.remove(str(assigned))

    for click_name, code_pair in example_codes.items():
        folder_name = extract_base_folder_name(click_name)
        changed_code = code_pair[0]
        assigned = mikrobus_map.get(_normalize_click_token(folder_name))

        if assigned is None and free_mikrobus:
            assigned = int(free_mikrobus[0])

        if assigned is not None:
            changed_code = re.sub(r'(?<=MIKROBUS_)[0-9]+', str(assigned), changed_code)

        adjusted_examples[click_name] = changed_code

    prompt_text = ""
    if adjusted_examples:
        sections = []
        for click_name, changed_code in adjusted_examples.items():
            sections.append(
                f"\n--- Click Board: {click_name} "
                f"(Folder: {click_name.replace('mikroe.click.', '').strip()}) ---\n"
                f"{changed_code}\n"
            )
        prompt_text = (
            "Below are the example main.c source codes for each validated Click board:\n"
            + "".join(sections)
        )

    return {
        "mikrobus_map": mikrobus_map,
        "free_mikrobus": free_mikrobus,
        "adjusted_examples": adjusted_examples,
        "prompt_text": prompt_text,
    }

# Compact usage-pattern context from Click example main.c files
def click_generation_usage_reference(
    mikrobus_map: Dict[str, int] | None,
    example_codes: Dict[str, Tuple[str, str]],
    active_clicks: List[str] | None = None,
    intention: str | None = None,
    max_body_lines: int = 80,
) -> str:
    """
    Build compact usage-pattern context from fetched example main.c files.

    This complements click_generation_api_reference():
    - API reference explains what public APIs exist.
    - Usage reference explains how example code normally sequences those APIs.
    """

    active_clicks = active_clicks or []

    def normalize(value: Any) -> str:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("mikroe.click.", "")
            .replace(" ", "")
            .replace("&", "")
            .replace("-", "")
            .replace("click", "")
        )

    def extract_balanced_function_block(code: str, function_name: str) -> str:
        pattern = re.compile(
            rf'\b(?:void|int|err_t|uint8_t|uint16_t|uint32_t|float|double|static\s+void)\s+'
            rf'{re.escape(function_name)}\s*\([^)]*\)\s*\{{',
            re.MULTILINE,
        )

        match = pattern.search(code)
        if not match:
            return ""

        brace_start = code.find("{", match.start())
        if brace_start < 0:
            return ""

        depth = 0
        i = brace_start

        while i < len(code):
            char = code[i]

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return code[brace_start + 1:i].strip()

            i += 1

        return ""

    def clean_snippet(snippet: str, max_lines: int) -> str:
        if not snippet:
            return ""

        lines = []

        for raw_line in snippet.splitlines():
            line = raw_line.rstrip()

            if not line.strip():
                continue

            # Skip large decorative comments, but keep useful inline comments.
            if line.strip().startswith("// ----------------------------------------------------------------"):
                continue

            lines.append(line)

        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... (+{len(lines) - max_lines} more lines)"]

        return "\n".join(lines).strip()

    def extract_includes(code: str) -> List[str]:
        includes = []

        for match in re.finditer(r'^[ \t]*#\s*include\s*(<[^>]+>|"[^"]+")', code, re.MULTILINE):
            includes.append(match.group(1))

        return includes

    def extract_global_declarations(code: str) -> List[str]:
        """
        Extract likely useful global Click context/config variables declared before application_init.
        This is intentionally conservative and only meant as generation guidance.
        """

        init_match = re.search(r'\bapplication_init\s*\(', code)
        prefix = code[:init_match.start()] if init_match else code[:2000]

        declarations = []

        for raw_line in prefix.splitlines():
            line = raw_line.strip()

            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith("//"):
                continue
            if line.startswith("/*") or line.startswith("*"):
                continue
            if "(" in line:
                continue
            if not line.endswith(";"):
                continue

            # Click globals are usually context/config objects or simple buffers/flags.
            if re.search(r'\b[A-Za-z_]\w*_t\s+\*?[A-Za-z_]\w+\s*;', line):
                declarations.append(line)
                continue

            if re.search(r'\b(?:float|double|uint8_t|uint16_t|uint32_t|int8_t|int16_t|int32_t|char)\b', line):
                declarations.append(line)
                continue

        return declarations[:30]

    def extract_local_helper_signatures(code: str) -> List[str]:
        helpers = []

        function_re = re.compile(
            r'^[ \t]*(?P<signature>'
            r'(?:static\s+)?'
            r'(?:void|int|err_t|uint8_t|uint16_t|uint32_t|float|double|char|bool|[A-Za-z_]\w*_t)'
            r'[^;{}]*\b(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\))\s*\{',
            re.MULTILINE,
        )

        ignored = {
            "application_init",
            "application_task",
            "main",
        }

        for match in function_re.finditer(code):
            name = match.group("name")
            signature = re.sub(r"\s+", " ", match.group("signature")).strip()

            if name in ignored:
                continue

            helpers.append(signature)

        return helpers[:30]

    data = generate_code_context_data(
        mikrobus_map=mikrobus_map,
        example_codes=example_codes,
    )

    adjusted_examples = data.get("adjusted_examples", {}) or {}
    active_norm = {normalize(click) for click in active_clicks if normalize(click)}

    lines = [
        "CLICK EXAMPLE USAGE PATTERNS",
        "",
        "Use these compact patterns to understand how fetched Click examples normally initialize and use the drivers.",
        "Rules:",
        "- Treat these snippets as usage guidance, not as a complete file to copy blindly.",
        "- Prefer public APIs from the Click API reference when generating final code.",
        "- Preserve the normal setup order shown in application_init when possible.",
        "- Preserve the normal polling/read/logging pattern shown in application_task when relevant.",
        "- If a helper function appears here, either define it in generated code or avoid calling it.",
        f"- Current generation intention: {intention or 'unknown'}",
        "",
    ]

    for click_name, code in adjusted_examples.items():
        click_norm = normalize(click_name)

        if active_norm and click_norm not in active_norm:
            continue

        includes = extract_includes(code)
        globals_ = extract_global_declarations(code)
        init_body = clean_snippet(
            extract_balanced_function_block(code, "application_init"),
            max_body_lines,
        )
        task_body = clean_snippet(
            extract_balanced_function_block(code, "application_task"),
            max_body_lines,
        )
        helpers = extract_local_helper_signatures(code)

        lines.append(f"[Click example: {click_name}]")

        if includes:
            lines.append("Observed includes:")
            lines.extend(f"  - {include}" for include in includes)

        if globals_:
            lines.append("Observed global declarations:")
            lines.extend(f"  - {decl}" for decl in globals_)

        if init_body:
            lines.append("Observed application_init pattern:")
            lines.append("```c")
            lines.append(init_body)
            lines.append("```")

        if task_body:
            lines.append("Observed application_task pattern:")
            lines.append("```c")
            lines.append(task_body)
            lines.append("```")

        if helpers:
            lines.append("Observed local helper functions:")
            lines.extend(f"  - {helper}" for helper in helpers)

        lines.append("")

    return "\n".join(lines).strip()
# # # # # # # # # #

# Improved context build: including click .h/.c
def click_generation_api_reference(
    click_header_elements: Dict[str, Any],
    click_source_elements: Dict[str, Any],
    mikrobus_map: Dict[str, int] | None = None,
    active_clicks: List[str] | None = None,
    intention: str | None = None,
    max_functions_per_click: int = 35,
    max_macros_per_click: int = 35,
    max_types_per_click: int = 20,
    max_used_symbols_per_click: int = 30,
) -> str:
    """
    Build compact generation-side Click API context.

    This is intentionally different from verification context:
    - generation needs preferred APIs and usage rules
    - verification needs complete legality checks

    The goal is to expose useful .h/.c information without injecting full raw files.
    """

    mikrobus_map = mikrobus_map or {}
    active_clicks = active_clicks or []

    def normalize(value: Any) -> str:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("mikroe.click.", "")
            .replace(" ", "")
            .replace("&", "")
            .replace("-", "")
            .replace("click", "")
        )

    def source_to_click_name(source_name: str) -> str:
        return str(source_name or "").replace("mikroe.click.", "").strip()

    def normalize_sources(data: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}

        by_source = data.get("by_source")
        if isinstance(by_source, dict) and by_source:
            return by_source

        return {fallback_name: data}

    def function_name(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("name", "")).strip()
        if isinstance(item, str):
            text = item.strip()
            return text.split("(", 1)[0].strip()
        return ""

    def function_label(item: Any) -> str:
        if isinstance(item, dict):
            return (
                str(
                    item.get("full_signature")
                    or item.get("prototype")
                    or item.get("signature")
                    or item.get("name")
                    or ""
                )
                .strip()
            )
        return str(item or "").strip()

    def name_list(items: List[Any]) -> List[str]:
        result = []
        for item in items or []:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
            else:
                name = str(item or "").strip()

            if name:
                result.append(name)

        return dedupe(result)

    def function_label_list(items: List[Any]) -> List[str]:
        return dedupe([label for label in (function_label(item) for item in items or []) if label])

    def dedupe(items: List[str]) -> List[str]:
        seen = set()
        result = []

        for item in items:
            key = str(item)
            if not key or key in seen:
                continue

            seen.add(key)
            result.append(key)

        return result

    def short(items: List[str], limit: int) -> str:
        items = [item for item in items if item]

        if not items:
            return "None"

        if len(items) <= limit:
            return "\n".join(f"  - {item}" for item in items)

        visible = items[:limit]
        visible.append(f"... (+{len(items) - limit} more)")
        return "\n".join(f"  - {item}" for item in visible)

    def format_structs(structs: List[Any], limit: int) -> List[str]:
        result = []

        for item in structs or []:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            if not name:
                continue

            members = item.get("members", []) or []
            member_names = [
                str(member.get("name", "")).strip()
                for member in members
                if isinstance(member, dict) and member.get("name")
            ]

            if member_names:
                preview = ", ".join(member_names[:10])
                if len(member_names) > 10:
                    preview += f", ... (+{len(member_names) - 10} more)"
                result.append(f"{name} {{{preview}}}")
            else:
                result.append(name)

        return dedupe(result)[:limit]

    def format_enums(enums: List[Any], limit: int) -> List[str]:
        result = []

        for item in enums or []:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            if not name:
                continue

            members = item.get("members", []) or []
            member_names = [
                str(member.get("name", "")).strip()
                for member in members
                if isinstance(member, dict) and member.get("name")
            ]

            if member_names:
                preview = ", ".join(member_names[:12])
                if len(member_names) > 12:
                    preview += f", ... (+{len(member_names) - 12} more)"
                result.append(f"{name}: {preview}")
            else:
                result.append(name)

        return dedupe(result)[:limit]

    def doc_brief(doc: Dict[str, Any] | None) -> str:
        if not isinstance(doc, dict):
            return ""

        return str(
            doc.get("brief")
            or doc.get("details")
            or ""
        ).strip()


    def doc_details(doc: Dict[str, Any] | None) -> str:
        if not isinstance(doc, dict):
            return ""

        brief = str(doc.get("brief") or "").strip()
        details = str(doc.get("details") or "").strip()

        if details and details != brief:
            return details

        return ""


    def format_functions_with_docs(
        functions: List[Any],
        docs_map: Dict[str, Any] | None,
        limit: int,
    ) -> str:
        docs_map = docs_map or {}
        output_lines = []
        count = 0

        for item in functions or []:
            name = function_name(item)
            label = function_label(item)

            if not name or not label:
                continue

            count += 1
            if count > limit:
                output_lines.append(f"  - ... (+{len(functions) - limit} more)")
                break

            doc = item.get("doc") if isinstance(item, dict) else None
            doc = doc or docs_map.get(name) or {}

            output_lines.append(f"  - {label}")

            brief = doc_brief(doc)
            details = doc_details(doc)
            params = doc.get("params", {}) if isinstance(doc, dict) else {}
            return_text = doc.get("return", "") if isinstance(doc, dict) else ""

            if brief:
                output_lines.append(f"    Purpose: {brief}")
            if details:
                output_lines.append(f"    Details: {details}")

            if isinstance(params, dict) and params:
                param_parts = [
                    f"{param_name}: {param_description}"
                    for param_name, param_description in params.items()
                    if param_name and param_description
                ]
                if param_parts:
                    output_lines.append(f"    Params: {'; '.join(param_parts)}")

            if return_text:
                output_lines.append(f"    Returns: {return_text}")

        return "\n".join(output_lines) if output_lines else "  - None"


    def format_macros_with_docs(
        macros: List[str],
        docs_map: Dict[str, Any] | None,
        limit: int,
    ) -> str:
        docs_map = docs_map or {}
        output_lines = []

        for idx, macro_name in enumerate(macros or []):
            if idx >= limit:
                output_lines.append(f"  - ... (+{len(macros) - limit} more)")
                break

            output_lines.append(f"  - {macro_name}")

            doc = docs_map.get(macro_name) or {}
            brief = doc_brief(doc)
            details = doc_details(doc)

            if brief:
                output_lines.append(f"    Purpose: {brief}")
            if details:
                output_lines.append(f"    Details: {details}")

        return "\n".join(output_lines) if output_lines else "  - None"


    def format_structs_with_docs(
        structs: List[Any],
        docs_map: Dict[str, Any] | None,
        limit: int,
    ) -> str:
        docs_map = docs_map or {}
        output_lines = []

        for idx, item in enumerate(structs or []):
            if idx >= limit:
                output_lines.append(f"  - ... (+{len(structs) - limit} more)")
                break

            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            if not name:
                continue

            doc = item.get("doc") or docs_map.get(name) or {}
            brief = doc_brief(doc)

            if brief:
                output_lines.append(f"  - {name} — {brief}")
            else:
                output_lines.append(f"  - {name}")

            members = item.get("members", []) or []
            member_parts = []

            for member in members[:10]:
                if not isinstance(member, dict):
                    continue

                member_name = str(member.get("name", "")).strip()
                member_type = str(member.get("type", "")).strip()
                member_desc = str(member.get("description", "")).strip()

                if not member_name:
                    continue

                if member_desc:
                    member_parts.append(f"{member_name}: {member_desc}")
                elif member_type:
                    member_parts.append(f"{member_name}: {member_type}")
                else:
                    member_parts.append(member_name)

            if member_parts:
                output_lines.append(f"    Members: {'; '.join(member_parts)}")

        return "\n".join(output_lines) if output_lines else "  - None"


    def format_enums_with_docs(
        enums: List[Any],
        docs_map: Dict[str, Any] | None,
        limit: int,
    ) -> str:
        docs_map = docs_map or {}
        output_lines = []

        for idx, item in enumerate(enums or []):
            if idx >= limit:
                output_lines.append(f"  - ... (+{len(enums) - limit} more)")
                break

            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()
            if not name:
                continue

            doc = item.get("doc") or docs_map.get(name) or {}
            brief = doc_brief(doc)

            members = [
                str(member.get("name", "")).strip()
                for member in item.get("members", []) or []
                if isinstance(member, dict) and member.get("name")
            ]

            line = f"  - {name}"
            if brief:
                line += f" — {brief}"

            output_lines.append(line)

            if members:
                output_lines.append(f"    Values: {', '.join(members[:16])}")

        return "\n".join(output_lines) if output_lines else "  - None"

    header_by_source = normalize_sources(click_header_elements, "__headers__")
    source_by_source = normalize_sources(click_source_elements, "__sources__")

    source_names = list(dict.fromkeys(list(header_by_source.keys()) + list(source_by_source.keys())))

    active_norm = {normalize(click) for click in active_clicks if normalize(click)}

    lines = [
        "CLICK API GENERATION REFERENCE",
        "",
        "Use this reference as the authoritative Click-board API guide while generating main.c.",
        "Rules:",
        "- Prefer exact header-declared Click APIs, types, macros, and enum constants.",
        "- Do not invent Click-specific functions, macros, fields, typedefs, enum constants, or helper APIs.",
        "- Header-declared symbols may be used directly after including the required Click header.",
        "- Symbols defined only inside example/source files are example-local; do not call them unless you also define them in the generated file.",
        "- Use the raw example main.c context only as a usage pattern, not as proof that source-local helpers are globally available.",
        "- Keep the final output as one complete main.c file with includes/definitions, application_init, and application_task.",
        f"- Current generation intention: {intention or 'unknown'}",
        "",
    ]

    for source_name in source_names:
        click_name = source_to_click_name(source_name)
        click_norm = normalize(click_name)

        if active_norm and click_norm not in active_norm and normalize(source_name) not in active_norm:
            continue

        header_data = header_by_source.get(source_name, {}) or {}
        source_data = source_by_source.get(source_name, {}) or {}

        bus = mikrobus_map.get(click_norm)
        if bus is None:
            bus = mikrobus_map.get(click_name)
        if bus is None:
            bus = mikrobus_map.get(source_name)

        header_includes = name_list(header_data.get("includes", []))
        header_macros = name_list(header_data.get("macros", []))
        header_functions = function_label_list(header_data.get("functions", []))
        header_structs = format_structs(header_data.get("structs", []), max_types_per_click)
        header_enums = format_enums(header_data.get("enums", []), max_types_per_click)

        source_functions_raw = source_data.get("functions", []) or []
        source_function_names = {
            function_name(item)
            for item in source_functions_raw
            if function_name(item)
        }

        source_macros = name_list(source_data.get("macros", []))
        source_functions = function_label_list(source_functions_raw)

        source_used_functions = name_list(source_data.get("used_functions", []))
        source_used_macro_like_tokens = name_list(source_data.get("used_macro_like_tokens", []))

        source_external_functions = [
            name for name in source_used_functions
            if name not in source_function_names
        ]

        lines.append(f"[Click: {click_name}]")
        if bus is not None:
            lines.append(f"Assigned mikroBUS: MIKROBUS_{bus}")

        lines.append("Required / observed includes:")
        lines.append(short(header_includes, 12))

        header_docs = header_data.get("docs", {}) or {}

        lines.append("Header-declared functions / directly usable APIs:")
        lines.append(
            format_functions_with_docs(
                header_data.get("functions", []),
                header_docs.get("functions", {}),
                max_functions_per_click,
            )
        )

        lines.append("Header-declared macros / directly usable macro constants:")
        lines.append(
            format_macros_with_docs(
                header_data.get("macros", []),
                header_docs.get("macros", {}),
                max_macros_per_click,
            )
        )

        lines.append("Header-declared structs / typedefs:")
        lines.append(
            format_structs_with_docs(
                header_data.get("structs", []),
                header_docs.get("structs", {}),
                max_types_per_click,
            )
        )

        lines.append("Header-declared enums / enum constants:")
        lines.append(
            format_enums_with_docs(
                header_data.get("enums", []),
                header_docs.get("enums", {}),
                max_types_per_click,
            )
        )

        lines.append("Example-local source functions:")
        lines.append(short(source_functions, max_functions_per_click))

        lines.append("Example-local source macros:")
        lines.append(short(source_macros, max_macros_per_click))

        lines.append("External framework/helper calls observed in example source:")
        lines.append(short(source_external_functions, max_used_symbols_per_click))

        lines.append("Macro-like tokens observed in example source:")
        lines.append(short(source_used_macro_like_tokens, max_used_symbols_per_click))

        lines.append("")

    return "\n".join(lines).strip()


def generate_code_context(
    mikrobus_pairs: str | list | tuple | None,
    example_codes: Dict[str, Tuple[str, str]],
):
    """
    Legacy compatibility wrapper.

    Args:
        mikrobus_pairs (str | list | tuple | None): The MIKROBUS pairs.
        example_codes (Dict[str, Tuple[str, str]]): The example codes.

    Returns:
        Dict[str, Any]: The code context data.
    """
    mikrobus_map: Dict[str, int] = {}

    if isinstance(mikrobus_pairs, str):
        parts = [p for p in mikrobus_pairs.split(",") if p.strip()]
        for part in parts:
            if "/" not in part:
                continue
            name, mb = part.split("/", 1)
            name = name.strip().lower()
            try:
                mikrobus_map[name] = int(mb.strip())
            except Exception:
                continue

    data = generate_code_context_data(mikrobus_map, example_codes)
    return data["prompt_text"]

# ---------------------------------------------------------------------------