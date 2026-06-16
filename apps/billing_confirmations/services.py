from datetime import date
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.services import create_audit_log
from apps.common.services.locking import lock_instance
from apps.common.services.sequences import generate_sequence
from apps.supplier_invoices.models import SupplierInvoice, SupplierInvoiceStatus

from .models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote


def next_tbcn_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"TBCN-{country_code}", target_year, "{code}-{year}-{number:06d}")


def lock_records_after_tbcn(*records) -> None:
    for record in records:
        lock_instance(record)


def generate_tbcn(supplier_invoice, user) -> TravelBillingConfirmationNote:
    with transaction.atomic():
        supplier_invoice = SupplierInvoice.objects.select_for_update().get(pk=supplier_invoice.pk)
        if supplier_invoice.status != SupplierInvoiceStatus.HR_APPROVED:
            raise ValidationError("TBCN can only be generated for HR approved supplier invoices.")
        if supplier_invoice.billing_confirmations.exclude(
            status__in=[BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]
        ).exists():
            raise ValidationError("An active TBCN already exists for this supplier invoice.")
        lines = list(supplier_invoice.lines.select_related("travel_case__country", "ticket_version").all())
        if not lines:
            raise ValidationError("TBCN cannot be generated without invoice lines.")
        country_code = next((line.travel_case.country.code for line in lines if line.travel_case_id), "LY")
        year = supplier_invoice.invoice_date.year if supplier_invoice.invoice_date else date.today().year
        matched_amount = sum((line.invoiced_amount for line in lines), start=0)
        difference_amount = sum((line.difference_amount for line in lines), start=0)

        try:
            tbcn = TravelBillingConfirmationNote.objects.create(
                confirmation_no=next_tbcn_number(country_code, year),
                supplier_invoice=supplier_invoice,
                supplier=supplier_invoice.supplier,
                supplier_invoice_number=supplier_invoice.supplier_invoice_number,
                total_amount=supplier_invoice.total_amount,
                matched_amount=matched_amount,
                difference_amount=difference_amount,
                currency=supplier_invoice.currency,
                status=BillingConfirmationStatus.GENERATED,
                finance_status=FinanceStatus.NOT_SENT,
                generated_by=user,
                generated_at=timezone.now(),
                is_locked=True,
            )
        except IntegrityError as exc:
            raise ValidationError("An active TBCN already exists for this supplier invoice.") from exc
        lock_instance(supplier_invoice, save=False)
        supplier_invoice.status = SupplierInvoiceStatus.TBCN_GENERATED
        supplier_invoice.save(update_fields=["is_locked", "locked_at", "status", "updated_at"])
        for line in lines:
            line.is_locked = True
            line.save(update_fields=["is_locked", "updated_at"])
            if line.ticket_version_id:
                lock_instance(line.ticket_version)
    create_audit_log(
        user=user,
        action="TBCN Generated",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        new_value={"confirmation_no": tbcn.confirmation_no, "status": tbcn.status, "finance_status": tbcn.finance_status},
        metadata={"supplier_invoice": supplier_invoice.id},
    )
    return tbcn


def _ensure_generated_tbcn(tbcn):
    if tbcn.status in [BillingConfirmationStatus.DRAFT, BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]:
        raise ValidationError("Finance actions require a generated TBCN.")


def generate_tbcn_pdf(tbcn: TravelBillingConfirmationNote, user) -> TravelBillingConfirmationNote:
    _ensure_generated_tbcn(tbcn)
    tbcn = (
        TravelBillingConfirmationNote.objects.select_related("supplier", "supplier_invoice", "generated_by")
        .prefetch_related(
            "supplier_invoice__lines__travel_case",
            "supplier_invoice__lines__employee",
        )
        .get(pk=tbcn.pk)
    )
    pdf_bytes = _build_tbcn_pdf_bytes(tbcn, user)
    if tbcn.pdf_file:
        tbcn.pdf_file.delete(save=False)
    tbcn.pdf_file.save(f"{tbcn.confirmation_no}.pdf", ContentFile(pdf_bytes), save=True)
    create_audit_log(
        user=user,
        action="TBCN PDF Generated",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        new_value={"confirmation_no": tbcn.confirmation_no, "has_pdf": True},
    )
    return tbcn


def _build_tbcn_pdf_bytes(tbcn: TravelBillingConfirmationNote, user) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Travel Billing Confirmation Note {tbcn.confirmation_no}",
    )
    styles = getSampleStyleSheet()
    normal = styles["BodyText"]
    small = styles["BodyText"]
    small.fontSize = 8
    small.leading = 10
    generated_at = timezone.now()
    supplier_invoice = tbcn.supplier_invoice
    generated_by = getattr(user, "get_username", lambda: "")() or getattr(tbcn.generated_by, "username", "System")

    story = [
        Paragraph("Travel Billing Confirmation Note", styles["Title"]),
        Paragraph("System Generated Document", styles["Heading3"]),
        Spacer(1, 4 * mm),
    ]

    header_rows = [
        ("TBCN number", tbcn.confirmation_no, "Generated date", _format_datetime(tbcn.generated_at)),
        ("Supplier", tbcn.supplier.name, "Supplier invoice number", tbcn.supplier_invoice_number),
        ("Invoice date", _format_date(supplier_invoice.invoice_date), "Currency", tbcn.currency),
        ("Total amount", _money(tbcn.total_amount, tbcn.currency), "Matched amount", _money(tbcn.matched_amount, tbcn.currency)),
        ("Difference amount", _money(tbcn.difference_amount, tbcn.currency), "Status", tbcn.status),
        ("Finance status", tbcn.finance_status, "Prepared/generated by", generated_by),
    ]
    header_table = Table(
        [[Paragraph(f"<b>{left_label}</b>", normal), left_value, Paragraph(f"<b>{right_label}</b>", normal), right_value] for left_label, left_value, right_label, right_value in header_rows],
        colWidths=[38 * mm, 75 * mm, 44 * mm, 75 * mm],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E1EC")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F7FA")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F5F7FA")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([header_table, Spacer(1, 5 * mm)])

    line_rows = [
        [
            "#",
            "Travel Case",
            "Employee/passenger",
            "Ticket",
            "Route",
            "Booked",
            "Invoiced",
            "Difference",
            "Match status",
        ]
    ]
    for index, line in enumerate(supplier_invoice.lines.all(), start=1):
        route = " - ".join(part for part in [line.route_from, line.route_to] if part) or "-"
        line_rows.append(
            [
                str(index),
                getattr(line.travel_case, "case_number", "-") if line.travel_case_id else "-",
                getattr(line.employee, "full_name", "") or "-",
                line.ticket_number or "-",
                route,
                _money(line.booked_amount, tbcn.currency),
                _money(line.invoiced_amount, tbcn.currency),
                _money(line.difference_amount, tbcn.currency),
                line.match_status,
            ]
        )

    line_table = Table(line_rows, repeatRows=1, colWidths=[10 * mm, 37 * mm, 42 * mm, 32 * mm, 28 * mm, 25 * mm, 25 * mm, 25 * mm, 30 * mm])
    line_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F1B2D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E1EC")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (5, 1), (7, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([Paragraph("Linked Invoice Lines", styles["Heading3"]), line_table, Spacer(1, 6 * mm)])
    story.extend(
        [
            Paragraph("System generated by Travel Operations & Billing Control System", small),
            Paragraph("No TBCN, No Finance", styles["Heading4"]),
            Paragraph(f"PDF generated at {_format_datetime(generated_at)}. This document contains no internal credentials or storage configuration.", small),
        ]
    )

    document.build(story)
    return buffer.getvalue()


def _money(value: Decimal, currency: str) -> str:
    return f"{currency} {Decimal(value):,.2f}"


def _format_date(value) -> str:
    return value.strftime("%Y-%m-%d") if value else "-"


def _format_datetime(value) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M:%S %Z") if value else "-"


def send_tbcn_to_finance(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    if tbcn.status not in [BillingConfirmationStatus.GENERATED, BillingConfirmationStatus.REVIEWED]:
        raise ValidationError("Only generated TBCNs can be sent to finance.")
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.sent_to_finance_by = user
    tbcn.sent_to_finance_at = timezone.now()
    tbcn.finance_status = FinanceStatus.SENT
    tbcn.status = BillingConfirmationStatus.SENT_TO_FINANCE
    tbcn.save(update_fields=["sent_to_finance_by", "sent_to_finance_at", "finance_status", "status", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Sent To Finance",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn


def mark_finance_accepted(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    if tbcn.status != BillingConfirmationStatus.SENT_TO_FINANCE or tbcn.finance_status != FinanceStatus.SENT:
        raise ValidationError("TBCN must be sent to finance before acceptance.")
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.finance_status = FinanceStatus.ACCEPTED
    tbcn.status = BillingConfirmationStatus.FINANCE_ACCEPTED
    tbcn.reviewed_by = tbcn.reviewed_by or user
    tbcn.reviewed_at = tbcn.reviewed_at or timezone.now()
    tbcn.save(update_fields=["finance_status", "status", "reviewed_by", "reviewed_at", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Finance Accepted",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn


def mark_tbcn_paid(tbcn, user):
    _ensure_generated_tbcn(tbcn)
    if tbcn.status != BillingConfirmationStatus.FINANCE_ACCEPTED or tbcn.finance_status != FinanceStatus.ACCEPTED:
        raise ValidationError("TBCN must be finance accepted before marking paid.")
    old_value = {"status": tbcn.status, "finance_status": tbcn.finance_status}
    tbcn.finance_status = FinanceStatus.PAID
    tbcn.status = BillingConfirmationStatus.PAID
    tbcn.save(update_fields=["finance_status", "status", "updated_at"])
    create_audit_log(
        user=user,
        action="TBCN Paid",
        entity_type="TravelBillingConfirmationNote",
        entity_id=tbcn.id,
        old_value=old_value,
        new_value={"status": tbcn.status, "finance_status": tbcn.finance_status},
    )
    return tbcn
