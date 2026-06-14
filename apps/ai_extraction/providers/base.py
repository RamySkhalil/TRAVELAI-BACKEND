from __future__ import annotations

from copy import deepcopy
from typing import Any

from rest_framework.exceptions import ValidationError


TICKET_TEMPLATE: dict[str, Any] = {
    "document_type": "FLIGHT_TICKET",
    "passenger_name": "",
    "ticket_number": "",
    "pnr": "",
    "airline": "",
    "route_from": "",
    "route_to": "",
    "departure_date": "",
    "departure_time": "",
    "arrival_date": "",
    "arrival_time": "",
    "amount": "",
    "currency": "",
    "supplier": "",
    "booking_reference": "",
    "ticket_status": "",
    "original_ticket_number": "",
    "confidence": {
        "passenger_name": 0.0,
        "ticket_number": 0.0,
        "pnr": 0.0,
        "route": 0.0,
        "departure_date": 0.0,
        "amount": 0.0,
        "supplier": 0.0,
    },
    "missing_critical_fields": [],
}

INVOICE_LINE_TEMPLATE: dict[str, Any] = {
    "passenger_name": "",
    "ticket_number": "",
    "route_from": "",
    "route_to": "",
    "amount": "",
    "currency": "",
}

INVOICE_TEMPLATE: dict[str, Any] = {
    "document_type": "SUPPLIER_INVOICE",
    "supplier_name": "",
    "supplier_invoice_number": "",
    "invoice_date": "",
    "currency": "",
    "total_amount": "",
    "lines": [],
    "confidence": {
        "supplier_name": 0.0,
        "supplier_invoice_number": 0.0,
        "invoice_date": 0.0,
        "total_amount": 0.0,
        "lines": 0.0,
    },
    "missing_critical_fields": [],
}

TICKET_CRITICAL_FIELDS = (
    "passenger_name",
    "ticket_number",
    "pnr",
    "route_from",
    "route_to",
    "departure_date",
    "amount",
    "supplier",
)

INVOICE_CRITICAL_FIELDS = (
    "supplier_name",
    "supplier_invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
)


class BaseExtractionProvider:
    name = "base"

    def classify_document(self, file_or_text):
        raise NotImplementedError

    def extract_ticket(self, file_or_text):
        raise NotImplementedError

    def extract_invoice(self, file_or_text):
        raise NotImplementedError


def normalize_ticket_result(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("Ticket extraction result must be a JSON object.")

    normalized = deepcopy(TICKET_TEMPLATE)
    for key in normalized:
        if key in {"confidence", "missing_critical_fields"}:
            continue
        normalized[key] = data.get(key, normalized[key]) or ""

    confidence = data.get("confidence") or {}
    if not isinstance(confidence, dict):
        raise ValidationError("Ticket extraction confidence must be a JSON object.")
    normalized["confidence"].update({key: _safe_float(confidence.get(key, value)) for key, value in normalized["confidence"].items()})
    normalized["missing_critical_fields"] = _missing_fields(normalized, TICKET_CRITICAL_FIELDS)
    return normalized


def normalize_invoice_result(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("Invoice extraction result must be a JSON object.")

    normalized = deepcopy(INVOICE_TEMPLATE)
    for key in normalized:
        if key in {"confidence", "missing_critical_fields", "lines"}:
            continue
        normalized[key] = data.get(key, normalized[key]) or ""

    lines = data.get("lines") or []
    if not isinstance(lines, list):
        raise ValidationError("Invoice extraction lines must be a JSON array.")
    normalized["lines"] = [_normalize_invoice_line(line) for line in lines]

    confidence = data.get("confidence") or {}
    if not isinstance(confidence, dict):
        raise ValidationError("Invoice extraction confidence must be a JSON object.")
    normalized["confidence"].update({key: _safe_float(confidence.get(key, value)) for key, value in normalized["confidence"].items()})

    missing_fields = _missing_fields(normalized, INVOICE_CRITICAL_FIELDS)
    if any(not line.get("ticket_number") for line in normalized["lines"]):
        missing_fields.append("lines.ticket_number")
    if any(not line.get("amount") for line in normalized["lines"]):
        missing_fields.append("lines.amount")
    normalized["missing_critical_fields"] = missing_fields
    return normalized


def _normalize_invoice_line(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("Each invoice line must be a JSON object.")
    line = deepcopy(INVOICE_LINE_TEMPLATE)
    for key in line:
        line[key] = data.get(key, "") or ""
    return line


def _missing_fields(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if data.get(field) in ("", None, [])]


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
