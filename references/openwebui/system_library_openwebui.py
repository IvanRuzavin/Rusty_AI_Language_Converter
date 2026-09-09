"""
Open WebUI adapter for system_library_generator.

This adapter deliberately reuses the same Open WebUI model plumbing as the
existing datasheet handler. It does not store or accept API keys itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from open_webui.utils.chat import generate_chat_completion


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _extract_content(response: Any) -> str:
    if isinstance(response, dict):
        return (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        ).strip()
    return str(response).strip()


def _parse_json_response(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Be tolerant of a model adding a short lead-in; isolate the outermost
        # object but still reject malformed JSON rather than repairing values.
        first = text.find("{")
        last = text.rfind("}")
        if first < 0 or last <= first:
            raise
        value = json.loads(text[first:last + 1])

    if not isinstance(value, dict):
        raise ValueError("Expected model to return one JSON object.")
    return value


@dataclass
class OpenWebUISystemLibraryBackend:
    request: Request
    user: dict
    model_id: str

    async def json(self, prompt: str, stage: str) -> dict[str, Any]:
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the structured reasoning backend for a NECTO MCU "
                        "system-library generator. Follow the supplied evidence rules. "
                        "Return valid JSON only for this stage."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        response = await generate_chat_completion(
            self.request,
            form_data=payload,
            user=self.user,
        )
        return _parse_json_response(_extract_content(response))

    async def text(self, prompt: str, stage: str) -> str:
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the code-generation backend for a NECTO MCU "
                        "system-library generator. Use only supplied target evidence "
                        "for hardware facts."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        response = await generate_chat_completion(
            self.request,
            form_data=payload,
            user=self.user,
        )
        return _extract_content(response)
