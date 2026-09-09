"""
MCU Datasheet section (title) related utilities.
"""

import ast
from typing import Any


def section_string_to_item(data_toc, section_str, is_html: bool = False) -> dict | None | Any:
    """
    Converts a section string to a section item in the datasheet TOC if it exists.

    Args:
        data_toc (List[Dict]): The datasheet TOC.
        section_str (Dict | List[str] | str): The section string.
        is_html (bool): Is section format for html or pdf datasheet.

    Returns:
        dict | None: The section item in the datasheet TOC if it exists, otherwise None.
    """
    if isinstance(section_str, dict):
        return section_str

    found_toc_section = None

    if not is_html:
        if isinstance(section_str, list):
            section_lvl = section_str[0]
            section_title = section_str[1]
            section_start_page = section_str[2]
        else:
            try:
                section_lst = ast.literal_eval(section_str)
            except Exception:
                return None

            if not isinstance(section_lst, (list, tuple)) or len(section_lst) < 3:
                return None

            section_lvl = section_lst[0]
            section_title = section_lst[1]
            section_start_page = section_lst[2]

        for section_curr in data_toc:
            if (
                str(section_lvl) == str(section_curr["section_lvl"])
                and str(section_title).lower() == str(section_curr["section_title"]).lower()
                and str(section_start_page) == str(section_curr["start_page"])
            ):
                found_toc_section = section_curr
                break
    else:
        if isinstance(section_str, list):
            section_link = section_str[0]
            section_title = section_str[1]
        else:
            try:
                section_lst = ast.literal_eval(section_str)
            except Exception:
                return None

            if not isinstance(section_lst, (list, tuple)) or len(section_lst) < 2:
                return None

            section_link = section_lst[0]
            section_title = section_lst[1]

        for section_curr in data_toc:
            if (
                str(section_link) == str(section_curr["page_link"])
                and str(section_title).lower() == str(section_curr["section_title"]).lower()
            ):
                found_toc_section = section_curr
                break

    return found_toc_section


def get_section_title(toc_section_item):
    """
    Returns the section title from the TOC section item.

    Args:
        toc_section_item (Any): The TOC section item.
    Returns:
        str: The section title.
    """
    section_title = (
        toc_section_item["section_title"]
        if isinstance(toc_section_item, dict) and "section_title" in toc_section_item
        else toc_section_item[1]
        if isinstance(toc_section_item, list) and len(toc_section_item) > 1
        else str(toc_section_item)
    )
    return section_title