"""
MCU Datasheet section extraction related utilities.
"""

import re
import traceback
from urllib.parse import urljoin
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
)

from bs4 import BeautifulSoup
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from necto_assistant_html_utilities import get_html_text
from necto_assistant_pdf_utilities import (
    fetch_pdfs_if_not_present,
    get_pdf_text,
    get_pdf_toc,
    get_sections as get_pdf_sections
)

from necto_assistant_router.config.settings import (
    MCU_DATASHEET_DIR,
    PDF_BASE_URL
)
from necto_assistant_router.config.structures import (
    DatasheetSourceContext,
    McuResolution,
    SectionExtractionResult
)
from necto_assistant_router.models.datasheets.toc import (
    _emit_status,
    determine_toc_section
)
from necto_assistant_router.models.datasheets.section import get_section_title
from necto_assistant_router.util.general_utils import _log_error


def _validate_pdf_section(section: Any) -> dict:
    """
    Validate that a resolved PDF TOC section item has the required structure.
    """
    if not isinstance(section, dict):
        raise TypeError("PDF section must be a resolved TOC section dict.")

    required_keys = {"start_page", "end_page", "section_title"}
    missing_keys = required_keys - set(section.keys())
    if missing_keys:
        raise ValueError(
            f"PDF section is missing required keys: {sorted(missing_keys)}"
        )

    return section

def _validate_html_section(section: Any) -> dict:
    """
    Validate that a resolved HTML TOC section item has the required structure.
    """
    if not isinstance(section, dict):
        raise TypeError("HTML section must be a resolved TOC section dict.")

    required_keys = {"page_link", "section_title"}
    missing_keys = required_keys - set(section.keys())
    if missing_keys:
        raise ValueError(
            f"HTML section is missing required keys: {sorted(missing_keys)}"
        )

    return section

def extract_pdf_section_to_md(mcu_datasheet_path: str, section: dict) -> str:
    """
    Extract the resolved PDF section from the MCU datasheet into markdown/text.

    Args:
        mcu_datasheet_path (str): Path to the MCU datasheet.
        section (dict): Resolved TOC section item.

    Returns:
        str: Extracted PDF section content.
    """
    try:
        resolved_section = _validate_pdf_section(section)

        print(get_section_title(resolved_section))
        extracted_section_md = get_pdf_text(
            pdf_path=mcu_datasheet_path,
            start_page=resolved_section["start_page"],
            end_page=resolved_section["end_page"],
        )
    except Exception as e:
        _log_error(f"Error: Failed to extract section from PDF: {e}")
        traceback.print_exc()
        raise RuntimeError("Failed to extract section from PDF.") from e

    return extracted_section_md

def _extract_html_section_to_md_sync(html_toc, section: dict):
    """
    Sync implementation of HTML section extraction.

    This is intentionally sync because async Playwright freezes inside the
    Open WebUI / NECTO Assistant request runtime. The async public wrapper
    runs this function through run_in_threadpool().
    """
    try:
        resolved_section = _validate_html_section(section)

        print(get_section_title(resolved_section), flush=True)

        extracted_section_md = ""

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-http2",
                ],
            )

            try:
                context = browser.new_context(
                    ignore_https_errors=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                        "sec-ch-ua": '"Not.A/Brand";v="99", "Chromium";v="136"',
                    },
                )

                page = context.new_page()

                page.goto(
                    resolved_section["page_link"],
                    wait_until="networkidle",
                    timeout=15000,
                )

                rendered_html = page.content()
                page.close()

                extracted_section_md = get_html_text(
                    read_page=rendered_html,
                    append=False,
                )

                soup = BeautifulSoup(rendered_html, "lxml")
                target_div = soup.find("div", id="wh_publication_toc_content")

                if target_div is not None:
                    new_toc = target_div.find_all("a", href=True)

                    known_titles = [
                        toc["section_title"]
                        for toc in html_toc
                        if isinstance(toc, dict) and "section_title" in toc
                    ]

                    new_chapters = [
                        chapter
                        for chapter in new_toc
                        if (
                            chapter.get_text(strip=True) not in known_titles
                            and ".html" in (chapter.get("href") or "")
                        )
                    ]

                    for chapter in new_chapters:
                        href = chapter.get("href")

                        if not href:
                            continue

                        section_url = urljoin(
                            resolved_section["page_link"],
                            href,
                        )

                        sub_page = context.new_page()

                        try:
                            sub_page.goto(
                                section_url,
                                wait_until="networkidle",
                                timeout=15000,
                            )

                            page_html = sub_page.content()

                            extracted_section_md += "\n\n" + get_html_text(
                                read_page=page_html,
                                append=True,
                            )

                        finally:
                            sub_page.close()

            finally:
                browser.close()

    except PlaywrightTimeoutError as e:
        _log_error(f"Error: Playwright timeout while extracting section from HTML: {e}")
        traceback.print_exc()
        raise RuntimeError("Failed to extract section from HTML.") from e

    except Exception as e:
        _log_error(f"Error: Failed to extract section from HTML: {e}")
        traceback.print_exc()
        raise RuntimeError("Failed to extract section from HTML.") from e

    return extracted_section_md


async def extract_html_section_to_md(html_toc, section: dict):
    """
    Extract the resolved HTML section from the datasheet into markdown/text.

    Args:
        html_toc (list): Parsed HTML TOC items.
        section (dict): Resolved TOC section item.

    Returns:
        str | None: Extracted HTML section content.
    """
    return await run_in_threadpool(
        _extract_html_section_to_md_sync,
        html_toc,
        section,
    )

# ---------------------------------------------------------------------------
# model_handler utils
# ---------------------------------------------------------------------------

async def _extract_section_from_html(
    necto_semantic_proxy,
    mcu_resolution: McuResolution,
    query: str,
    source_context: DatasheetSourceContext,
    messages: List[Dict],
    request: Request,
    user: Dict,
    event_emitter: Callable[[Any], Awaitable[None]] | None,
) -> SectionExtractionResult | None:
    toc_section = await determine_toc_section(
        necto_semantic_proxy=necto_semantic_proxy,
        mcu_model=mcu_resolution.mcu_model,
        query=query,
        toc_text=source_context.toc_text,
        toc_items=source_context.toc_items,
        is_html=source_context.is_html,
        messages=messages,
        __request__=request,
        user=user,
        __event_emitter__=event_emitter,
    )

    if toc_section is None:
        return None

    section_md = await extract_html_section_to_md(source_context.toc_items, toc_section)
    if section_md is None:
        return None

    return SectionExtractionResult(
        is_html=True,
        toc_section=toc_section,
        section_md=section_md,
    )

async def _load_pdf_source_context(
    mcu_resolution: McuResolution,
) -> DatasheetSourceContext:
    fetch_pdfs_if_not_present(
        mcu_resolution.mcus,
        PDF_BASE_URL,
        MCU_DATASHEET_DIR,
    )

    toc_text = get_pdf_toc(mcu_resolution.datasheet_path, return_as_text=True)
    toc_items = get_pdf_sections(mcu_resolution.datasheet_path)

    return DatasheetSourceContext(
        is_html=False,
        toc_text=toc_text,
        toc_items=toc_items,
    )

async def _extract_section_from_pdf(
    necto_semantic_proxy,
    mcu_resolution: McuResolution,
    query: str,
    source_context: DatasheetSourceContext,
    messages: List[Dict],
    request: Request,
    user: Dict,
    event_emitter: Callable[[Any], Awaitable[None]] | None,
) -> SectionExtractionResult :
    toc_section = await determine_toc_section(
        necto_semantic_proxy=necto_semantic_proxy,
        mcu_model=mcu_resolution.mcu_model,
        query=query,
        toc_text=source_context.toc_text,
        toc_items=source_context.toc_items,
        is_html=source_context.is_html,
        messages=messages,
        __request__=request,
        user=user,
        __event_emitter__=event_emitter,
    )

    if toc_section is None:
        return None

    await _emit_status(
        necto_semantic_proxy,
        event_emitter,
        f"Extracting datasheet section: {get_section_title(toc_section)}",
        translation_key="Extracting datasheet section: {{TOC_SECTION}}",
        translation_params={
            "TOC_SECTION": get_section_title(toc_section),
        },
    )

    section_md = extract_pdf_section_to_md(
        mcu_resolution.datasheet_path,
        toc_section,
    )

    if not section_md:
        return None

    return SectionExtractionResult(
        is_html=False,
        toc_section=toc_section,
        section_md=section_md,
    )
