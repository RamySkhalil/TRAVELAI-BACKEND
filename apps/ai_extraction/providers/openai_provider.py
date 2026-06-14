from __future__ import annotations

import json
from urllib import error, request

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .base import BaseExtractionProvider, INVOICE_TEMPLATE, TICKET_TEMPLATE, normalize_invoice_result, normalize_ticket_result


class OpenAIExtractionProvider(BaseExtractionProvider):
    name = "openai"

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.OPENAI_MODEL
        self.base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self.timeout_seconds = settings.OPENAI_TIMEOUT_SECONDS

    def classify_document(self, file_or_text):
        response = self._request_json(
            "Classify this travel operations document. Return strict JSON with document_type and confidence.",
            str(file_or_text or ""),
        )
        document_type = response.get("document_type")
        if document_type not in {"FLIGHT_TICKET", "SUPPLIER_INVOICE", "UNKNOWN"}:
            document_type = "UNKNOWN"
        return {"document_type": document_type, "confidence": _safe_float(response.get("confidence"))}

    def extract_ticket(self, file_or_text):
        # TODO: Add PDF/image OCR before this provider when binary document parsing is introduced.
        response = self._request_json(_ticket_prompt(), str(file_or_text or ""))
        return normalize_ticket_result(response)

    def extract_invoice(self, file_or_text):
        # TODO: Add PDF/image OCR before this provider when binary document parsing is introduced.
        response = self._request_json(_invoice_prompt(), str(file_or_text or ""))
        return normalize_invoice_result(response)

    def _request_json(self, system_prompt: str, document_text: str) -> dict:
        if not self.api_key:
            raise ValidationError("OpenAI API key is not configured. Set OPENAI_API_KEY.")
        if not document_text:
            raise ValidationError("Document text is required for OpenAI extraction.")

        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": document_text},
            ],
        }
        http_request = request.Request(
            f"{self.base_url}/chat/completions",
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

        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            raise ValidationError("OpenAI extraction response did not include JSON content.")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValidationError("OpenAI extraction response was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ValidationError("OpenAI extraction response must be a JSON object.")
        return parsed


def _ticket_prompt() -> str:
    return (
        "Extract a flight ticket for a travel operations system. Return strict JSON only. "
        "Do not create business records. Use exactly this shape, preserving keys: "
        f"{json.dumps(TICKET_TEMPLATE)}"
    )


def _invoice_prompt() -> str:
    return (
        "Extract a supplier invoice for a travel operations system. Return strict JSON only. "
        "Do not create business records. Use exactly this shape, preserving keys: "
        f"{json.dumps(INVOICE_TEMPLATE)}"
    )


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
