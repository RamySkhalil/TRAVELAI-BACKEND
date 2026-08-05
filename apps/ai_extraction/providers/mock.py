from __future__ import annotations

from .base import BaseExtractionProvider, document_text, normalize_invoice_result, normalize_ticket_result


class MockExtractionProvider(BaseExtractionProvider):
    name = "mock"

    def classify_document(self, file_or_text):
        text = document_text(file_or_text).lower()
        if "invoice" in text:
            return {"document_type": "SUPPLIER_INVOICE", "confidence": 0.95}
        return {"document_type": "FLIGHT_TICKET", "confidence": 0.95}

    def extract_ticket(self, file_or_text):
        text = document_text(file_or_text)
        if "missing critical" in text.lower():
            return normalize_ticket_result(
                {
                    "passenger_name": "Test Passenger",
                    "ticket_number": "",
                    "pnr": "",
                    "route_from": "CAI",
                    "route_to": "TIP",
                    "departure_date": "",
                    "amount": "",
                    "supplier": "",
                    "confidence": {"passenger_name": 0.9, "route": 0.8},
                }
            )

        currency = _currency_from_text(text) or "USD"
        if "missing currency" in text.lower():
            currency = ""
        return normalize_ticket_result(
            {
                "passenger_name": "Aisha Mohamed",
                "ticket_number": "1761234567890",
                "pnr": "ABC123",
                "airline": "Libyan Wings",
                "route_from": "CAI",
                "route_to": "TIP",
                "departure_date": "2026-07-01",
                "departure_time": "09:30",
                "arrival_date": "2026-07-01",
                "arrival_time": "11:30",
                "amount": "450.00",
                "currency": currency,
                "supplier": "Mock Travel Supplier",
                "booking_reference": "BR-001",
                "ticket_status": "DRAFT",
                "original_ticket_number": "",
                "confidence": {
                    "passenger_name": 0.98,
                    "ticket_number": 0.99,
                    "pnr": 0.96,
                    "route": 0.94,
                    "departure_date": 0.93,
                    "amount": 0.95,
                    "currency": 0.95 if currency else 0.0,
                    "supplier": 0.92,
                },
            }
        )

    def extract_invoice(self, file_or_text):
        text = document_text(file_or_text)
        currency = _currency_from_text(text) or "USD"
        if "missing currency" in text.lower():
            currency = ""
        return normalize_invoice_result(
            {
                "supplier_name": "Mock Travel Supplier",
                "supplier_invoice_number": "INV-2026-001",
                "invoice_date": "2026-07-05",
                "currency": currency,
                "total_amount": "450.00",
                "lines": [
                    {
                        "passenger_name": "Aisha Mohamed",
                        "ticket_number": "1761234567890",
                        "route_from": "CAI",
                        "route_to": "TIP",
                        "amount": "450.00",
                        "currency": currency,
                    }
                ],
                "confidence": {
                    "supplier_name": 0.97,
                    "supplier_invoice_number": 0.98,
                    "invoice_date": 0.94,
                    "currency": 0.95 if currency else 0.0,
                    "total_amount": 0.96,
                    "lines": 0.93,
                },
            }
        )


def _currency_from_text(text: str) -> str:
    upper_text = text.upper()
    if "EGP" in upper_text:
        return "EGP"
    if "USD" in upper_text:
        return "USD"
    return ""
