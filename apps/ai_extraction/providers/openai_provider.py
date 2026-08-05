from __future__ import annotations

import base64
import json
from urllib import error, request

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .base import (
    BaseExtractionProvider,
    ExtractionDocument,
    INVOICE_TEMPLATE,
    TICKET_TEMPLATE,
    document_text,
    normalize_invoice_result,
    normalize_ticket_result,
)


class OpenAIExtractionProvider(BaseExtractionProvider):
    """OpenAI-backed extraction.

    Uses the Responses API so a vision-capable model (e.g. GPT-5) can read
    PDFs and scanned images directly, removing the need for a separate OCR
    step. Plain text documents are still supported.
    """

    name = "openai"

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.model = getattr(settings, "OPENAI_EXTRACTION_MODEL", None) or settings.OPENAI_MODEL
        self.base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self.timeout_seconds = getattr(
            settings, "OPENAI_EXTRACTION_TIMEOUT_SECONDS", settings.OPENAI_TIMEOUT_SECONDS
        )

    def classify_document(self, file_or_text):
        response = self._request_json(
            "Classify this travel operations document. Return strict JSON with document_type "
            "(one of FLIGHT_TICKET, SUPPLIER_INVOICE, UNKNOWN) and a numeric confidence between 0 and 1.",
            file_or_text,
        )
        document_type = response.get("document_type")
        if document_type not in {"FLIGHT_TICKET", "SUPPLIER_INVOICE", "UNKNOWN"}:
            document_type = "UNKNOWN"
        return {"document_type": document_type, "confidence": _safe_float(response.get("confidence"))}

    def extract_ticket(self, file_or_text):
        response = self._request_json(_ticket_prompt(), file_or_text)
        return normalize_ticket_result(response)

    def extract_invoice(self, file_or_text):
        response = self._request_json(_invoice_prompt(), file_or_text)
        return normalize_invoice_result(response)

    def _request_json(self, system_prompt: str, file_or_text) -> dict:
        if not self.api_key:
            raise ValidationError("OpenAI API key is not configured. Set OPENAI_API_KEY.")

        content = self._build_user_content(file_or_text)
        payload = {
            "model": self.model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_object"}},
        }
        http_request = request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise ValidationError(f"OpenAI extraction request failed with status {exc.code}.") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValidationError("OpenAI extraction request failed safely.") from exc

        text = _output_text(body)
        if not text:
            raise ValidationError("OpenAI extraction response did not include JSON content.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError("OpenAI extraction response was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValidationError("OpenAI extraction response must be a JSON object.")
        return parsed

    def _build_user_content(self, file_or_text) -> list[dict]:
        instruction = "Extract the requested fields from the attached document. Return JSON only."

        if isinstance(file_or_text, ExtractionDocument) and file_or_text.has_binary:
            document = file_or_text
            encoded = base64.b64encode(document.file_bytes).decode("ascii")
            if document.is_image:
                return [
                    {"type": "input_text", "text": instruction},
                    {"type": "input_image", "image_url": f"data:{document.mime_type};base64,{encoded}"},
                ]
            if document.is_pdf:
                return [
                    {"type": "input_text", "text": instruction},
                    {
                        "type": "input_file",
                        "filename": document.filename or "document.pdf",
                        "file_data": f"data:application/pdf;base64,{encoded}",
                    },
                ]
            raise ValidationError("Unsupported document type for OpenAI extraction.")

        text = document_text(file_or_text).strip()
        if not text:
            raise ValidationError("Document text is required for OpenAI extraction.")
        return [{"type": "input_text", "text": f"{instruction}\n\nDOCUMENT:\n{text}"}]


def _output_text(body: dict) -> str:
    """Collect assistant text from an OpenAI Responses API payload."""
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts: list[str] = []
    for item in body.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for chunk in item.get("content", []) or []:
            if isinstance(chunk, dict) and chunk.get("type") == "output_text":
                text = chunk.get("text")
                if text:
                    parts.append(text)
    return "".join(parts)


def _ticket_prompt() -> str:
    return (
        "You are a precise travel operations document parser. Extract a flight ticket / e-ticket. "
        "Return strict JSON only, no prose. "
        "Read every field directly from the document; never invent values. Leave a field as an empty "
        "string when it is not present. Normalize dates to YYYY-MM-DD and times to HH:MM (24h). "
        "Use IATA airport codes for route_from/route_to when available. "
        "Supported currencies are USD and EGP; if the currency is absent leave it blank. "
        "For each field in 'confidence', return a number between 0 and 1 reflecting how certain you are. "
        "Do not create business records. Use exactly this JSON shape, preserving all keys: "
        f"{json.dumps(TICKET_TEMPLATE)}"
    )


def _invoice_prompt() -> str:
    return (
        "You are a precise travel operations document parser. Extract a supplier invoice. "
        "Return strict JSON only, no prose. "
        "Read every field directly from the document; never invent values. Leave a field as an empty "
        "string when it is not present. Normalize dates to YYYY-MM-DD. "
        "Supported currencies are USD and EGP; if a currency is absent leave it blank. "
        "For each field in 'confidence', return a number between 0 and 1 reflecting how certain you are. "
        "Do not create business records. Use exactly this JSON shape, preserving all keys: "
        f"{json.dumps(INVOICE_TEMPLATE)}"
    )


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
