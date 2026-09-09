"""
MCU Datasheet Table of Contents (TOC) related utilities.
"""

import ast
import re
import traceback
from typing import (
    List,
    Dict,
    Any,
    Callable,
    Awaitable,
)
from fastapi import Request

from open_webui.utils.chat import generate_chat_completion

from necto_assistant_router.models.datasheets.section import get_section_title
from necto_assistant_router.service.events import _emit_status
from necto_assistant_router.util.general_utils import (
    _log_info,
    _log_warning
)



def _normalize_text(value: str) -> str:
    value = value.strip().strip("`").strip('"').strip("'")
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def _strip_section_number_prefix(value: str) -> str:
    """
    Strip common numbering prefixes such as:
    4
    4.
    4.1
    4.1.2
    4 -
    """
    value = value.strip()
    value = re.sub(r"^\d+(?:\.\d+)*\s*[-.:]?\s*", "", value)
    return value.strip()


def _titles_match_tolerantly(candidate_title: str, actual_title: str) -> bool:
    """
    Tolerant title matching for cases where the model returns abbreviated
    titles such as 'PIC18F8XK22' while the actual TOC title is
    'Pin Diagrams - PIC18F8XK22'.
    """
    candidate_norm = _normalize_text(candidate_title)
    candidate_norm_no_prefix = _normalize_text(
        _strip_section_number_prefix(candidate_title)
    )

    actual_norm = _normalize_text(actual_title)
    actual_norm_no_prefix = _normalize_text(
        _strip_section_number_prefix(actual_title)
    )

    if not candidate_norm:
        return False

    return (
        actual_norm == candidate_norm
        or actual_norm_no_prefix == candidate_norm_no_prefix
        or candidate_norm in actual_norm
        or candidate_norm_no_prefix in actual_norm_no_prefix
        or actual_norm in candidate_norm
        or actual_norm_no_prefix in candidate_norm_no_prefix
    )


def _match_toc_item(toc_items: List[Any], model_output: str, is_html: bool) -> Any | None:
    """
    Match model output to a TOC item using tolerant strategies.

    Supported model outputs:
    - Exact dict/list string as shown in prompt
    - Title-only response
    - Slightly reformatted title response

    For PDF:
    model may return: [<level>, <title>, <start_page>]

    For HTML:
    model may return: [<page_link>, <title>]
    """
    if not model_output:
        return None

    cleaned_output = model_output.strip()
    normalized_output = _normalize_text(cleaned_output)
    normalized_output_no_prefix = _normalize_text(
        _strip_section_number_prefix(cleaned_output)
    )

    # 1. Try to parse structured model output like:
    #    PDF:  [1, '4 Pinouts and pin description', 42]
    #    HTML: ['some/page.html', 'Section title']
    try:
        parsed_output = ast.literal_eval(cleaned_output)

        if isinstance(parsed_output, (list, tuple)):
            if not is_html and len(parsed_output) >= 3:
                section_lvl = str(parsed_output[0])
                section_title = str(parsed_output[1]).strip()
                start_page = str(parsed_output[2])

                # 1a. Exact structured match
                for item in toc_items:
                    if not isinstance(item, dict):
                        continue

                    if (
                        str(item.get("section_lvl")) == section_lvl
                        and str(item.get("section_title", "")).strip().lower() == section_title.lower()
                        and str(item.get("start_page")) == start_page
                    ):
                        return item

                # 1b. Prefer candidates with same section level and start page
                level_page_matches = []
                for item in toc_items:
                    if not isinstance(item, dict):
                        continue

                    if (
                        str(item.get("section_lvl")) == section_lvl
                        and str(item.get("start_page")) == start_page
                    ):
                        level_page_matches.append(item)

                for item in level_page_matches:
                    item_title = str(item.get("section_title", "")).strip()
                    if _titles_match_tolerantly(section_title, item_title):
                        return item

                # 1c. Broader fallback: same page only
                # Useful if model got the correct page but slightly wrong section level.
                page_matches = []
                for item in toc_items:
                    if not isinstance(item, dict):
                        continue

                    if str(item.get("start_page")) == start_page:
                        page_matches.append(item)

                for item in page_matches:
                    item_title = str(item.get("section_title", "")).strip()
                    if _titles_match_tolerantly(section_title, item_title):
                        return item

                # 1d. Last resort: if there is exactly one item with this level+page, trust it
                if len(level_page_matches) == 1:
                    return level_page_matches[0]

            elif is_html and len(parsed_output) >= 2:
                page_link = str(parsed_output[0]).strip()
                section_title = str(parsed_output[1]).strip()

                for item in toc_items:
                    if not isinstance(item, dict):
                        continue

                    if (
                        str(item.get("page_link", "")).strip() == page_link
                        and str(item.get("section_title", "")).strip().lower() == section_title.lower()
                    ):
                        return item

                # Tolerant HTML fallback: exact page_link + tolerant title match
                for item in toc_items:
                    if not isinstance(item, dict):
                        continue

                    if str(item.get("page_link", "")).strip() != page_link:
                        continue

                    item_title = str(item.get("section_title", "")).strip()
                    if _titles_match_tolerantly(section_title, item_title):
                        return item
    except Exception:
        pass

    # 2. Exact title match
    for item in toc_items:
        if isinstance(item, dict):
            title = str(item.get("section_title", "")).strip()
            if title == cleaned_output:
                return item

    # 3. Normalized title match
    for item in toc_items:
        if isinstance(item, dict):
            title = str(item.get("section_title", ""))
            if _normalize_text(title) == normalized_output:
                return item

    # 4. Title match ignoring numbering prefixes
    for item in toc_items:
        if isinstance(item, dict):
            title = str(item.get("section_title", ""))
            if _normalize_text(_strip_section_number_prefix(title)) == normalized_output_no_prefix:
                return item

    # 5. Tolerant partial title match as final fallback
    for item in toc_items:
        if isinstance(item, dict):
            title = str(item.get("section_title", "")).strip()
            if _titles_match_tolerantly(cleaned_output, title):
                return item

    return None

def _format_chat_history(messages: List[Dict], max_messages: int = 6) -> str:
    """
    Format recent chat history into a cleaner prompt-friendly text block.
    """
    formatted_messages = []
    for msg in messages[-max_messages:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        formatted_messages.append(f"{role.upper()}: {content}")
    return "\n".join(formatted_messages)

async def determine_toc_section(
    necto_semantic_proxy,
    mcu_model: str,
    query: str,
    toc_text: str,
    toc_items: List[Any],
    is_html: bool,
    messages: List[Dict],
    __request__: Request,
    user: Dict,
    __event_emitter__: Callable[[Any], Awaitable[None]] | None = None,
) -> Any | None:
    """
    Determine the datasheet TOC section most likely to contain the answer.

    Args:
        necto_semantic_proxy: Semantic proxy object.
        mcu_model (str): MCU model name.
        query (str): User query.
        toc_text (str): Serialized TOC text shown to the model.
        toc_items (List[Any]): Parsed TOC items used for validation/matching.
        is_html (bool): Whether TOC comes from HTML datasheet.
        messages (List[Dict]): Chat history.
        __request__ (Request): Request object.
        user (Dict): User info.
        __event_emitter__: Optional event emitter.

    Returns:
        Any | None: Matched TOC item from toc_items, or None if no section matched.
    """
    _log_info(f"Determining TOC section for MCU={mcu_model}, query={query}")

    await _emit_status(
        necto_semantic_proxy,
        __event_emitter__,
        "Determining datasheet section",
    )

    try:
        if not toc_items:
            raise ValueError("TOC items are empty.")

        if is_html:
            toc_format = "[<page_link>, <title>]"
            toc_elements = "page link and title"
        else:
            toc_format = "[<level>, <title>, <start_page>]"
            toc_elements = "level, title and start page"

        formatted_history = _format_chat_history(messages)

        payload = {
            "model": necto_semantic_proxy.valves.TOC_ANALYZER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "<system_prompt>\n"
                        "You are an assistant tasked to determine the single datasheet section "
                        "that best matches the user query and recent chat context.\n"
                        f"Each TOC item is represented by {toc_elements} in format {toc_format}.\n\n"
                        "TABLE OF CONTENTS:\n"
                        f"{toc_text}\n"
                        "END OF TABLE OF CONTENTS\n\n"
                        "<start_of_turn>user\n"
                        f"USER QUERY: {query}\n"
                        f"RECENT CHAT HISTORY:\n{formatted_history}\n"
                        "<end_of_turn>\n"
                        "<start_of_turn>model\n"
                        "Return exactly one of the following:\n"
                        "1. One COMPLETE TOC item copied verbatim from the provided TOC, in the original format {toc_format}\n"
                        "2. None\n\n"
                        "Rules:\n"
                        "- Do not explain\n"
                        "- Do not add quotes\n"
                        "- Do not use markdown\n"
                        "- Do not return only the title\n"
                        "- Do not return a partial TOC entry\n"
                        "- Copy the full TOC item exactly as shown in the TOC\n"
                        "- Prefer the single most specific matching section\n"
                    ),
                }
            ],
            "stream": False,
        }

        response = await generate_chat_completion(
            __request__,
            form_data=payload,
            user=user,
        )

        toc_section_text = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

        _log_info(f"Model-selected TOC section raw output: {toc_section_text}")

        if toc_section_text == "None":
            await _emit_status(
                necto_semantic_proxy,
                __event_emitter__,
                "No matching TOC section found",
                done=True,
            )
            return None

        matched_item = _match_toc_item(toc_items, toc_section_text, is_html)

        if matched_item is None:
            _log_warning(
                f"Model returned section that could not be matched to TOC: {toc_section_text}"
            )
            await _emit_status(
                necto_semantic_proxy,
                __event_emitter__,
                "No matching TOC section found",
                done=True,
            )
            return None

        await _emit_status(
            necto_semantic_proxy,
            __event_emitter__,
            f"Identified TOC section: {get_section_title(matched_item)}",
            translation_key="Identified TOC section: {{TOC_SECTION}}",
            translation_params={
                "TOC_SECTION": get_section_title(matched_item),
            },
            done=True,
        )

        return matched_item

    except Exception as e:
        _log_warning(f"Error in determine_toc_section: {e}")
        traceback.print_exc()
        await _emit_status(
            necto_semantic_proxy,
            __event_emitter__,
            "TOC section identification failed",
            done=True,
        )
        raise RuntimeError("TOC section identification failed.") from e