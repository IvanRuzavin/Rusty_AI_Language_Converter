"""
Datasheet processing model handler.
Datasheet information extraction flow.
"""

import traceback
from typing import (
    List,
    Dict,
    Callable,
    Awaitable,
    Any,
    Tuple,
)

from fastapi import Request
from fastapi.responses import StreamingResponse

from necto_assistant_router.config.settings import (
    PDF_BASE_URL,
    MCU_DATASHEET_DIR
)
from necto_assistant_pdf_utilities import (
    get_pdf_toc,
    get_sections as get_pdf_sections,
    fetch_pdfs_if_not_present
)
from necto_assistant_html_utilities import get_sections as get_html_sections

from necto_assistant_router.config.structures import (
    DatasheetSourceContext,
    HandlerResponse,
    McuResolution,
    SectionExtractionResult,
)
from necto_assistant_router.mcu.detector import determine_mcu
from necto_assistant_router.models.datasheets.section_extractor import (
    _extract_section_from_html,
    _extract_section_from_pdf,
    _load_pdf_source_context,
)
from necto_assistant_router.models.datasheets.utils import (
    _build_datasheet_messages,
    _build_repeat_missing_fallback_result,
    _datasheet_not_found_response,
    _normalize_datasheet_query,
    _was_same_mcu_already_reported_missing,
    build_mcu_note,
    use_pdf_or_html_datasheet,
    determine_mcu_model_and_datasheet_path
)

from necto_assistant_router.service.chat_completion import (
    chat_completion,
    stream_with_prefix
)
from necto_assistant_router.service.events import (
    _emit_status,
    _emit_necto_note
)

from necto_assistant_router.util.general_utils import (
    _log_error,
    _log_info
)
from necto_assistant_router.util.terms_extractor import get_model_id_and_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_mcu_context(
    necto_semantic_proxy,
    query: str,
    messages: List[Dict],
    request: Request,
    user: Dict,
    __metadata__: Any = None,
    event_emitter: Callable[[Any], Awaitable[None]] | None = None,
) -> McuResolution | None:
    mcus, uses_chat_mcu, uses_history_mcu = await determine_mcu(
        necto_semantic_proxy,
        query,
        messages,
        request,
        user,
        __metadata__,
        event_emitter,
    )

    if not mcus:
        return None

    mcu_model, datasheet_path = determine_mcu_model_and_datasheet_path(
        mcus,
        MCU_DATASHEET_DIR,
    )

    return McuResolution(
        mcus=mcus,
        mcu_model=mcu_model,
        datasheet_path=datasheet_path,
        uses_chat_mcu=uses_chat_mcu,
        uses_history_mcu=uses_history_mcu,
    )

def _is_html_datasheet_extraction_disabled(request: Request) -> bool:
    """
    Read DISABLE_HTML_DATASHEET_EXTRACTION from Open WebUI app config.

    The value is expected to be available as:
        request.app.state.config.DISABLE_HTML_DATASHEET_EXTRACTION

    Dict-style access is also supported as a fallback.
    """
    app = getattr(request, "app", None)
    app_state = getattr(app, "state", None)
    app_config = getattr(app_state, "config", None)

    if app_config is None:
        return False

    if isinstance(app_config, dict):
        return bool(app_config.get("DISABLE_HTML_DATASHEET_EXTRACTION", False))

    return bool(getattr(app_config, "DISABLE_HTML_DATASHEET_EXTRACTION", False))

async def _load_preferred_datasheet_source(
    mcu_resolution: McuResolution,
    disable_html_datasheet_extraction: bool = False,
) -> DatasheetSourceContext:
    """
    Load preferred datasheet source once and prepare both:
    - toc_text: serialized TOC for LLM prompt
    - toc_items: parsed TOC structure for lookup/extraction

    When HTML datasheet extraction is disabled, skip HTML lookup/extraction
    completely and force the PDF flow.
    """
    if disable_html_datasheet_extraction:
        _log_info(
            "HTML datasheet extraction is disabled by configuration. "
            "Using PDF datasheet source."
        )
        return await _load_pdf_source_context(mcu_resolution)

    is_html, html_toc = await use_pdf_or_html_datasheet(mcu_resolution.mcu_model)

    if is_html:
        toc_text = str(html_toc)
        toc_items = get_html_sections(html_toc)
        return DatasheetSourceContext(
            is_html=True,
            toc_text=toc_text,
            toc_items=toc_items,
        )

    return await _load_pdf_source_context(mcu_resolution)

async def _extract_best_section(
    necto_semantic_proxy,
    mcu_resolution: McuResolution,
    source_context: DatasheetSourceContext,
    query: str,
    messages: List[Dict],
    request: Request,
    user: Dict,
    event_emitter: Callable[[Any], Awaitable[None]] | None,
) -> SectionExtractionResult:
    """
    Extract best matching section.
    Preferred source is tried first.
    If HTML fails, fallback to PDF with a newly loaded PDF TOC context.
    """
    if source_context.is_html:
        html_result = await _extract_section_from_html(
            necto_semantic_proxy=necto_semantic_proxy,
            mcu_resolution=mcu_resolution,
            query=query,
            source_context=source_context,
            messages=messages,
            request=request,
            user=user,
            event_emitter=event_emitter,
        )
        if html_result is not None:
            return html_result

        pdf_source_context = await _load_pdf_source_context(mcu_resolution)
        pdf_result = await _extract_section_from_pdf(
            necto_semantic_proxy=necto_semantic_proxy,
            mcu_resolution=mcu_resolution,
            query=query,
            source_context=pdf_source_context,
            messages=messages,
            request=request,
            user=user,
            event_emitter=event_emitter,
        )
        if pdf_result is not None:
            return pdf_result

        raise Exception("Neither HTML nor PDF datasheet section could be extracted.")

    pdf_result = await _extract_section_from_pdf(
        necto_semantic_proxy=necto_semantic_proxy,
        mcu_resolution=mcu_resolution,
        query=query,
        source_context=source_context,
        messages=messages,
        request=request,
        user=user,
        event_emitter=event_emitter,
    )
    if pdf_result is not None:
        return pdf_result

    raise Exception("PDF datasheet section could not be extracted.")

async def _generate_model_response(
    necto_semantic_proxy,
    model_id: str,
    datasheet_messages: List[Dict[str, str]],
    request: Request,
    user: Dict,
    event_emitter: Callable[[Any], Awaitable[None]] | None,
):
    return await chat_completion(
        necto_semantic_proxy=necto_semantic_proxy,
        model_id=model_id,
        messages=datasheet_messages,
        __request__=request,
        user=user,
        stream=True,
        __event_emitter__=event_emitter,
    )

# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def model_handler(
    intention: str,
    necto_semantic_proxy,
    query: str,
    messages: List[Dict],
    __request__: Request,
    user: Dict,
    __metadata__: Any = None,
    __event_emitter__: Callable[[Any], Awaitable[None]] | None = None,
):
    """
    Datasheet information extraction flow.

    Returns:
        HandlerResponse
    """
    _log_info("Extracting datasheet information...")

    model_id, model_display_name = get_model_id_and_name(
        necto_semantic_proxy,
        intention,
    )

    try:
        # ------------------------------------------------------------------
        # Resolve MCU
        # ------------------------------------------------------------------
        mcu_resolution = await _resolve_mcu_context(
            necto_semantic_proxy=necto_semantic_proxy,
            query=query,
            messages=messages,
            request=__request__,
            user=user,
            __metadata__=__metadata__,
            event_emitter=__event_emitter__,
        )

        if mcu_resolution is None:
            await _emit_status(
                necto_semantic_proxy,
                __event_emitter__,
                "No MCU name specified in question",
                done=True,
            )
            await _emit_necto_note(
                necto_semantic_proxy,
                __event_emitter__,
                build_mcu_note(),
            )
            return HandlerResponse(
                model_id=model_id,
                model_display_name=model_display_name,
                response="",
            )

        if not mcu_resolution.mcu_model or not mcu_resolution.datasheet_path:
            same_mcu_failed_before = _was_same_mcu_already_reported_missing(
                messages,
                mcu_resolution.mcu_model or "unknown MCU",
            )

            if same_mcu_failed_before:
                return _build_repeat_missing_fallback_result(
                    model_id=model_id,
                    model_display_name=model_display_name,
                    messages=messages,
                )

            missing_response = await _datasheet_not_found_response(
                necto_semantic_proxy=necto_semantic_proxy,
                event_emitter=__event_emitter__,
                model_id=model_id,
                model_display_name=model_display_name,
                mcu_model=mcu_resolution.mcu_model or "unknown MCU",
                uses_chat_mcu=mcu_resolution.uses_chat_mcu,
                uses_history_mcu=mcu_resolution.uses_history_mcu,
            )
            return HandlerResponse(
                model_id=missing_response[0],
                model_display_name=missing_response[1],
                response=missing_response[2],
            )

        _log_info(f"Fetching datasheets for {mcu_resolution.mcus}")
        _log_info(
            f"{mcu_resolution.mcu_model.upper()} datasheet path: "
            f"{mcu_resolution.datasheet_path}"
        )

        # ------------------------------------------------------------------
        # Normalize user query for datasheet retrieval
        # ------------------------------------------------------------------
        query_normalization = await _normalize_datasheet_query(
            necto_semantic_proxy=necto_semantic_proxy,
            mcu_model=mcu_resolution.mcu_model,
            query=query,
            messages=messages,
            request=__request__,
            user=user,
            event_emitter=__event_emitter__,
        )

        retrieval_query = query_normalization.normalized_query

        # ------------------------------------------------------------------
        # Load preferred source once
        # ------------------------------------------------------------------
        html_datasheet_extraction_disabled = _is_html_datasheet_extraction_disabled(__request__)

        if html_datasheet_extraction_disabled:
            _log_info("DISABLE_HTML_DATASHEET_EXTRACTION=true")

        try:
            source_context = await _load_preferred_datasheet_source(
                mcu_resolution=mcu_resolution,
                disable_html_datasheet_extraction=html_datasheet_extraction_disabled,
            )
        except Exception:
            traceback.print_exc()

            same_mcu_failed_before = _was_same_mcu_already_reported_missing(
                messages,
                mcu_resolution.mcu_model,
            )

            if same_mcu_failed_before:
                return _build_repeat_missing_fallback_result(
                    model_id=model_id,
                    model_display_name=model_display_name,
                    messages=messages,
                )

            missing_response = await _datasheet_not_found_response(
                necto_semantic_proxy=necto_semantic_proxy,
                event_emitter=__event_emitter__,
                model_id=model_id,
                model_display_name=model_display_name,
                mcu_model=mcu_resolution.mcu_model,
                uses_chat_mcu=mcu_resolution.uses_chat_mcu,
                uses_history_mcu=mcu_resolution.uses_history_mcu,
            )
            return HandlerResponse(
                model_id=missing_response[0],
                model_display_name=missing_response[1],
                response=missing_response[2],
            )

        # ------------------------------------------------------------------
        # Extract best section using reused TOC
        # ------------------------------------------------------------------
        section_result = await _extract_best_section(
            necto_semantic_proxy=necto_semantic_proxy,
            mcu_resolution=mcu_resolution,
            source_context=source_context,
            query=retrieval_query,
            messages=messages,
            request=__request__,
            user=user,
            event_emitter=__event_emitter__,
        )

        await _emit_status(
            necto_semantic_proxy,
            __event_emitter__,
            f"Analyzing datasheet information",
        )

        # ------------------------------------------------------------------
        # Build note and prompt
        # ------------------------------------------------------------------
        await _emit_necto_note(
            necto_semantic_proxy,
            __event_emitter__,
            build_mcu_note(
                mcu_resolution.mcu_model,
                mcu_resolution.uses_chat_mcu,
                mcu_resolution.uses_history_mcu,
            ),
        )

        datasheet_messages = _build_datasheet_messages(
            mcu_model=mcu_resolution.mcu_model,
            original_query=query_normalization.original_query,
            normalized_query=query_normalization.normalized_query,
            section_md=section_result.section_md,
        )

        # ------------------------------------------------------------------
        # Generate final answer
        # ------------------------------------------------------------------
        try:
            response = await _generate_model_response(
                necto_semantic_proxy=necto_semantic_proxy,
                model_id=model_id,
                datasheet_messages=datasheet_messages,
                request=__request__,
                user=user,
                event_emitter=__event_emitter__,
            )

            if isinstance(response, StreamingResponse):
                return HandlerResponse(
                    model_id=model_id,
                    model_display_name=model_display_name,
                    response=response,
                )

            _log_info(f"INFO: Raw model response: {str(response)}")
            return HandlerResponse(
                model_id=model_id,
                model_display_name=model_display_name,
                response=response,
            )

        except Exception as e:
            _log_error(f"Error: Failed to generate response: {e}")
            traceback.print_exc()
            await _emit_status(
                necto_semantic_proxy,
                __event_emitter__,
                "Datasheet information extraction failed",
                done=True,
            )
            raise Exception("Datasheet information extraction failed.")

    except Exception as e:
        _log_error(f"Unhandled datasheet processing error: {e}")
        traceback.print_exc()
        await _emit_status(
            necto_semantic_proxy,
            __event_emitter__,
            "Datasheet information extraction failed",
            done=True,
        )
        raise