from __future__ import annotations

from .base import BaseExtractionProvider, normalize_invoice_result, normalize_ticket_result


class MockExtractionProvider(BaseExtractionProvider):
    name = "mock"

    def classify_document(self, file_or_text):
        text = str(file_or_text or "").lower()
        if "invoice" in text:
            return {"document_type": "SUPPLIER_INVOICE", "confidence": 0.95}
        return {"document_type": "FLIGHT_TICKET", "confidence": 0.95}

    def extract_ticket(self, file_or_text):
        text = str(file_or_text or "")
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
                "currency": "USD",
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
                    "supplier": 0.92,
                },
            }
        )

    def extract_invoice(self, file_or_text):
        return normalize_invoice_result(
            {
                "supplier_name": "Mock Travel Supplier",
                "supplier_invoice_number": "INV-2026-001",
                "invoice_date": "2026-07-05",
                "currency": "USD",
                "total_amount": "450.00",
                "lines": [
                    {
                        "passenger_name": "Aisha Mohamed",
                        "ticket_number": "1761234567890",
                        "route_from": "CAI",
                        "route_to": "TIP",
                        "amount": "450.00",
                        "currency": "USD",
                    }
                ],
                "confidence": {
                    "supplier_name": 0.97,
                    "supplier_invoice_number": 0.98,
                    "invoice_date": 0.94,
                    "total_amount": 0.96,
                    "lines": 0.93,
                },
            }
        )
