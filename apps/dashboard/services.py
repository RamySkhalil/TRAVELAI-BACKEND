from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Min, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.access_control.permissions import apply_scope_filter
from apps.ai_extraction.models import DocumentExtractionJob, DocumentType, ExtractionStatus
from apps.billing_confirmations.models import BillingConfirmationStatus, FinanceStatus, TravelBillingConfirmationNote
from apps.permits.models import Permit, PermitStatus, PermitType
from apps.supplier_invoices.models import MatchStatus, SupplierInvoice, SupplierInvoiceLine, SupplierInvoiceStatus
from apps.ticket_versions.models import TicketAction, TicketBillingState, TicketStatus, TicketVersion
from apps.travel_cases.models import TravelCase, TravelCaseStatus


ZERO = Decimal("0.00")

# Suppliers normally invoice one to two weeks after ticket activity, so a ticket
# still awaiting its invoice past this point is worth chasing.
TICKET_INVOICE_FOLLOW_UP_DAYS = 10

AGING_BUCKETS = [
    ("0_7", "0 to 7 days", 0, 7),
    ("8_14", "8 to 14 days", 8, 14),
    ("15_30", "15 to 30 days", 15, 30),
    ("over_30", "Over 30 days", 31, None),
]

AGING_STAGES = {
    "RECEIVED_NOT_HR_APPROVED": "Received, not HR approved",
    "HR_APPROVED_NO_TBCN": "HR approved, no TBCN",
    "TBCN_GENERATED_NOT_SENT": "TBCN generated, not sent to finance",
    "SENT_TO_FINANCE_NOT_PAID": "Sent to finance, not paid",
}

PENDING_GROUPS = {
    "travel_booking": ("Travel cases awaiting booking", "amber"),
    "assigned_cases": ("Travel cases assigned to you", "blue"),
    "ticket_extractions": ("Ticket extractions needing confirmation", "violet"),
    "invoice_extractions": ("Supplier invoice extractions needing confirmation", "violet"),
    "tickets_awaiting_invoice": ("Booked tickets awaiting a supplier invoice", "amber"),
    "invoice_exceptions": ("Invoice lines with exceptions", "red"),
    "hr_approval": ("Supplier invoices awaiting HR approval", "amber"),
    "tbcn_ready": ("HR-approved invoices ready for TBCN", "teal"),
    "tbcn_not_sent": ("TBCNs generated but not sent to finance", "teal"),
    "finance_acceptance": ("TBCNs sent to finance but not accepted", "blue"),
    "finance_payment": ("TBCNs accepted but not paid", "amber"),
    "permits": ("Permits pending or expired", "red"),
}


def dashboard_summary(user=None):
    today = timezone.localdate()
    expiring_soon = today + timedelta(days=14)
    travel_cases = _scoped_travel_cases(user)
    supplier_invoices = _scoped_supplier_invoices(user)
    tbcn_notes = _scoped_tbcn(user)
    permits = _scoped_permits(user)

    unpaid_by_currency = _amounts_by_currency(
        tbcn_notes.exclude(finance_status=FinanceStatus.PAID).exclude(
            status__in=[BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]
        )
    )
    paid_by_currency = _amounts_by_currency(tbcn_notes.filter(finance_status=FinanceStatus.PAID))
    awaiting_invoice = _tickets_awaiting_invoice(user)

    return {
        "travel": {
            "total": travel_cases.count(),
            "draft": travel_cases.filter(current_status=TravelCaseStatus.DRAFT).count(),
            "submitted": travel_cases.filter(current_status=TravelCaseStatus.SUBMITTED_BY_HR).count(),
            "under_booking": travel_cases.filter(current_status=TravelCaseStatus.UNDER_BOOKING).count(),
            "closed": travel_cases.filter(current_status=TravelCaseStatus.CLOSED).count(),
            "cancelled": travel_cases.filter(current_status=TravelCaseStatus.CANCELLED).count(),
        },
        "tickets": {
            "total": TicketVersion.objects.count(),
            "active": TicketVersion.objects.filter(ticket_status=TicketStatus.ACTIVE).count(),
            "cancelled": TicketVersion.objects.filter(ticket_status=TicketStatus.CANCELLED).count(),
            "changed_reissued": TicketVersion.objects.filter(ticket_action__in=[TicketAction.DATE_CHANGE, TicketAction.REISSUE]).count(),
            "no_show": TicketVersion.objects.filter(Q(ticket_status=TicketStatus.NO_SHOW) | Q(ticket_action=TicketAction.NO_SHOW)).count(),
            "awaiting_invoice": awaiting_invoice.count(),
            "awaiting_invoice_overdue": _overdue_awaiting_invoice(awaiting_invoice).count(),
            "unbilled_by_currency": _ticket_amounts_by_currency(awaiting_invoice),
        },
        "permits": {
            "pending_egypt": permits.filter(permit_type=PermitType.EGYPT_PERMIT, status=PermitStatus.PENDING).count(),
            "pending_libya": permits.filter(permit_type=PermitType.LIBYA_PERMIT, status=PermitStatus.PENDING).count(),
            "expired": permits.filter(Q(status=PermitStatus.EXPIRED) | Q(expiry_date__lt=today)).count(),
            "expiring_soon": permits.filter(
                status=PermitStatus.APPROVED,
                expiry_date__gte=today,
                expiry_date__lte=expiring_soon,
            ).count(),
        },
        "supplier_invoices": {
            "total": supplier_invoices.count(),
            "awaiting_matching": supplier_invoices.filter(status=SupplierInvoiceStatus.AWAITING_MATCHING).count(),
            "matched": supplier_invoices.filter(status=SupplierInvoiceStatus.MATCHED).count(),
            "exception_found": supplier_invoices.filter(status=SupplierInvoiceStatus.EXCEPTION_FOUND).count(),
            "hr_approved": supplier_invoices.filter(status=SupplierInvoiceStatus.HR_APPROVED).count(),
            "tbcn_generated": supplier_invoices.filter(status=SupplierInvoiceStatus.TBCN_GENERATED).count(),
        },
        "tbcn_finance": {
            "total": tbcn_notes.count(),
            "generated_not_sent": tbcn_notes.filter(
                status=BillingConfirmationStatus.GENERATED,
                finance_status=FinanceStatus.NOT_SENT,
            ).count(),
            "sent_to_finance": tbcn_notes.filter(finance_status=FinanceStatus.SENT).count(),
            "finance_accepted": tbcn_notes.filter(finance_status=FinanceStatus.ACCEPTED).count(),
            "paid": tbcn_notes.filter(finance_status=FinanceStatus.PAID).count(),
            "unpaid_by_currency": unpaid_by_currency,
            "paid_by_currency": paid_by_currency,
        },
    }


def operational_kpis(user=None):
    summary = dashboard_summary(user)
    travel_cases = _scoped_travel_cases(user)
    supplier_invoices = _scoped_supplier_invoices(user)
    invoice_lines = _scoped_invoice_lines(user)
    unpaid_amount_kpis = [
        {
            "key": f"unpaid_finance_amount_{row['currency'].lower()}",
            "label": f"Unpaid finance amount ({row['currency']})",
            "value": row["amount"],
            "unit": "amount",
            "currency": row["currency"],
            "tone": "red",
        }
        for row in summary["tbcn_finance"]["unpaid_by_currency"]
    ]
    unbilled_amount_kpis = [
        {
            "key": f"unbilled_ticket_amount_{row['currency'].lower()}",
            "label": f"Booked, not yet invoiced ({row['currency']})",
            "value": row["amount"],
            "unit": "amount",
            "currency": row["currency"],
            "tone": "amber",
        }
        for row in summary["tickets"]["unbilled_by_currency"]
    ]
    return {
        "results": [
            {
                "key": "open_travel_cases",
                "label": "Open travel cases",
                "value": travel_cases.exclude(current_status__in=[TravelCaseStatus.CLOSED, TravelCaseStatus.CANCELLED]).count(),
                "unit": "cases",
                "tone": "blue",
            },
            {
                "key": "tickets_needing_confirmation",
                "label": "Tickets needing confirmation",
                "value": TicketVersion.objects.filter(confirmed_at__isnull=True).count(),
                "unit": "tickets",
                "tone": "violet",
            },
            {
                "key": "invoice_lines_with_exceptions",
                "label": "Invoice lines with exceptions",
                "value": invoice_lines.filter(match_status__in=[MatchStatus.EXCEPTION, MatchStatus.DIFFERENCE]).count(),
                "unit": "lines",
                "tone": "red",
            },
            {
                "key": "pending_or_expired_permits",
                "label": "Pending or expired permits",
                "value": summary["permits"]["pending_egypt"] + summary["permits"]["pending_libya"] + summary["permits"]["expired"],
                "unit": "permits",
                "tone": "amber",
            },
            {
                "key": "supplier_invoices_pending_control",
                "label": "Supplier invoices pending control",
                "value": supplier_invoices.exclude(status__in=[SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.CLOSED]).count(),
                "unit": "invoices",
                "tone": "teal",
            },
            {
                "key": "tickets_awaiting_invoice",
                "label": "Tickets awaiting a supplier invoice",
                "value": summary["tickets"]["awaiting_invoice"],
                "unit": "tickets",
                "tone": "amber",
            },
            *unbilled_amount_kpis,
            *unpaid_amount_kpis,
        ]
    }


def cost_by_month(user=None):
    rows = (
        _scoped_supplier_invoices(user).annotate(month=TruncMonth("invoice_date"))
        .values("month", "currency")
        .annotate(invoice_count=Count("id"), total_amount=Sum("total_amount"))
        .order_by("month", "currency")
    )
    return {
        "results": [
            {
                "month": row["month"].strftime("%Y-%m") if row["month"] else "",
                "currency": row["currency"],
                "invoice_count": row["invoice_count"],
                "total_amount": row["total_amount"] or ZERO,
            }
            for row in rows
        ]
    }


def cost_by_project(user=None):
    rows = list(
        _scoped_invoice_lines(user).filter(travel_case__isnull=False)
        .values(
            "travel_case__project_id",
            "travel_case__project__code",
            "travel_case__project__name",
            "currency",
        )
        .annotate(line_count=Count("id"), total_amount=Sum("invoiced_amount"))
        .order_by("-total_amount", "travel_case__project__code")
    )
    return {"results": _with_share(_project_cost_row(row) for row in rows)}


def cost_by_supplier(user=None):
    rows = list(
        _scoped_supplier_invoices(user).values("supplier_id", "supplier__code", "supplier__name", "currency")
        .annotate(invoice_count=Count("id"), total_amount=Sum("total_amount"))
        .order_by("-total_amount", "supplier__name")
    )
    return {"results": _with_share(_supplier_cost_row(row) for row in rows)}


def cost_by_route(user=None):
    rows = list(
        _scoped_invoice_lines(user).exclude(route_from="")
        .exclude(route_to="")
        .values("route_from", "route_to", "currency")
        .annotate(ticket_count=Count("id"), total_amount=Sum("invoiced_amount"))
        .order_by("-total_amount", "route_from", "route_to")
    )
    return {"results": _with_share(_route_cost_row(row) for row in rows)}


def unbilled_tickets_by_supplier(user=None):
    """Committed ticket spend per supplier that has not been invoiced yet.

    This is the accrual side of the finance picture: the finance control report
    only sees money that already reached a TBCN, so without this a booked ticket
    is invisible until its supplier invoice arrives.
    """
    today = timezone.localdate()
    cutoff = timezone.now() - timedelta(days=TICKET_INVOICE_FOLLOW_UP_DAYS)
    rows = list(
        _tickets_awaiting_invoice(user)
        .values("supplier_id", "supplier__code", "supplier__name", "currency")
        .annotate(
            ticket_count=Count("id"),
            total_amount=Sum("amount"),
            overdue_count=Count("id", filter=Q(billing_state_changed_at__lt=cutoff)),
            oldest_awaiting_since=Min("billing_state_changed_at"),
        )
        .order_by("-total_amount", "supplier__name")
    )
    results = []
    for row in rows:
        oldest = row["oldest_awaiting_since"]
        results.append(
            {
                "supplier_id": row["supplier_id"],
                "supplier_code": row["supplier__code"],
                "supplier_name": row["supplier__name"],
                "currency": row["currency"],
                "ticket_count": row["ticket_count"],
                "total_amount": row["total_amount"] or ZERO,
                "overdue_count": row["overdue_count"],
                "oldest_age_days": max((today - timezone.localtime(oldest).date()).days, 0) if oldest else 0,
            }
        )
    return {"results": _with_share(results), "follow_up_days": TICKET_INVOICE_FOLLOW_UP_DAYS}


def supplier_aging(user=None):
    today = timezone.localdate()
    grouped = defaultdict(lambda: {"invoice_count": 0, "total_amount": ZERO})
    invoices = _scoped_supplier_invoices(user).exclude(status__in=[SupplierInvoiceStatus.PAID, SupplierInvoiceStatus.CLOSED]).prefetch_related(
        "billing_confirmations"
    )

    for invoice in invoices:
        stage = _invoice_aging_stage(invoice)
        if not stage:
            continue
        age_days = max((today - invoice.received_date).days, 0)
        bucket, bucket_label = _aging_bucket(age_days)
        key = (stage, bucket, invoice.currency)
        grouped[key]["invoice_count"] += 1
        grouped[key]["total_amount"] += invoice.total_amount or ZERO

    results = []
    for (stage, bucket, currency), values in grouped.items():
        results.append(
            {
                "stage": stage,
                "stage_label": AGING_STAGES[stage],
                "bucket": bucket,
                "bucket_label": dict((item[0], item[1]) for item in AGING_BUCKETS)[bucket],
                "currency": currency,
                "invoice_count": values["invoice_count"],
                "total_amount": values["total_amount"],
            }
        )
    results.sort(key=lambda row: (row["stage"], _bucket_sort(row["bucket"]), row["currency"]))
    return {"results": results}


def pending_actions_for_user(user):
    roles = set(user.groups.values_list("name", flat=True))
    is_all = user.is_staff or user.is_superuser or "SuperAdmin" in roles or "Admin" in roles or "Auditor" in roles
    items = []

    def include(*allowed_roles):
        return is_all or bool(roles.intersection(allowed_roles))

    if include("HR", "BookingOfficer", "BookingManager"):
        items.extend(_travel_cases_awaiting_booking(user))
        items.extend(_assigned_travel_cases(user))

    if include("BookingOfficer", "BookingManager"):
        items.extend(_ticket_extraction_actions())

    # Chasing the supplier for a missing invoice is shared between the booking
    # desk that placed the order and HR that controls the billing.
    if include("HR", "BookingOfficer", "BookingManager"):
        items.extend(_tickets_awaiting_invoice_actions(user))

    if include("HR"):
        items.extend(_invoice_extraction_actions())
        items.extend(_invoice_exception_actions(user))
        items.extend(_invoice_hr_approval_actions(user))
        items.extend(_tbcn_ready_actions(user))
        items.extend(_tbcn_not_sent_actions(user))
        items.extend(_permit_actions(user))

    if include("Finance"):
        items.extend(_finance_acceptance_actions(user))
        items.extend(_finance_payment_actions(user))

    if is_all:
        items.extend(_ticket_extraction_actions())
        items.extend(_invoice_extraction_actions())
        items.extend(_tickets_awaiting_invoice_actions(user))
        items.extend(_invoice_exception_actions(user))
        items.extend(_invoice_hr_approval_actions(user))
        items.extend(_tbcn_ready_actions(user))
        items.extend(_tbcn_not_sent_actions(user))
        items.extend(_finance_acceptance_actions(user))
        items.extend(_finance_payment_actions(user))
        items.extend(_permit_actions(user))

    items = _dedupe_items(items)
    grouped = _group_pending_items(items)
    public_items = [_public_action(item) for item in sorted(items, key=lambda item: (-item["age_days"], item["priority"], item["title"]))]
    return {"items": public_items, "groups": grouped}


def _amounts_by_currency(queryset):
    rows = queryset.values("currency").annotate(amount=Sum("total_amount")).order_by("currency")
    return [{"currency": row["currency"], "amount": row["amount"] or ZERO} for row in rows]


def _ticket_amounts_by_currency(queryset):
    rows = queryset.values("currency").annotate(amount=Sum("amount")).order_by("currency")
    return [{"currency": row["currency"], "amount": row["amount"] or ZERO} for row in rows]


def _tickets_awaiting_invoice(user=None):
    """Confirmed tickets that are a committed cost with no supplier invoice yet."""
    return _scoped_tickets(user).filter(billing_state=TicketBillingState.AWAITING_INVOICE)


def _overdue_awaiting_invoice(queryset):
    cutoff = timezone.now() - timedelta(days=TICKET_INVOICE_FOLLOW_UP_DAYS)
    return queryset.filter(billing_state_changed_at__lt=cutoff)


def _scoped_travel_cases(user=None):
    return apply_scope_filter(TravelCase.objects.all(), user, "travel_case")


def _scoped_tickets(user=None):
    return TicketVersion.objects.filter(travel_case__in=_scoped_travel_cases(user))


def _scoped_supplier_invoices(user=None):
    return apply_scope_filter(SupplierInvoice.objects.all(), user, "supplier_invoice")


def _scoped_tbcn(user=None):
    return apply_scope_filter(TravelBillingConfirmationNote.objects.all(), user, "tbcn")


def _scoped_invoice_lines(user=None):
    return SupplierInvoiceLine.objects.filter(supplier_invoice__in=_scoped_supplier_invoices(user))


def _scoped_permits(user=None):
    return Permit.objects.filter(travel_case__in=_scoped_travel_cases(user))


def _with_share(rows):
    rows = list(rows)
    totals = defaultdict(Decimal)
    for row in rows:
        totals[row["currency"]] += row["total_amount"] or ZERO
    for row in rows:
        total = totals[row["currency"]]
        row["share_percent"] = ((row["total_amount"] or ZERO) / total * Decimal("100")).quantize(Decimal("0.01")) if total else ZERO
    return rows


def _project_cost_row(row):
    return {
        "project_id": row["travel_case__project_id"],
        "project_code": row["travel_case__project__code"],
        "project_name": row["travel_case__project__name"],
        "currency": row["currency"],
        "line_count": row["line_count"],
        "total_amount": row["total_amount"] or ZERO,
    }


def _supplier_cost_row(row):
    return {
        "supplier_id": row["supplier_id"],
        "supplier_code": row["supplier__code"],
        "supplier_name": row["supplier__name"],
        "currency": row["currency"],
        "invoice_count": row["invoice_count"],
        "total_amount": row["total_amount"] or ZERO,
    }


def _route_cost_row(row):
    return {
        "route_from": row["route_from"],
        "route_to": row["route_to"],
        "currency": row["currency"],
        "ticket_count": row["ticket_count"],
        "total_amount": row["total_amount"] or ZERO,
    }


def _invoice_aging_stage(invoice):
    active_tbcn = next(
        (
            tbcn
            for tbcn in invoice.billing_confirmations.all()
            if tbcn.status not in [BillingConfirmationStatus.CANCELLED, BillingConfirmationStatus.REVISED]
        ),
        None,
    )
    if active_tbcn:
        if active_tbcn.finance_status == FinanceStatus.NOT_SENT:
            return "TBCN_GENERATED_NOT_SENT"
        if active_tbcn.finance_status in [FinanceStatus.SENT, FinanceStatus.ACCEPTED, FinanceStatus.REJECTED]:
            return "SENT_TO_FINANCE_NOT_PAID"
        return None
    if invoice.status in [
        SupplierInvoiceStatus.INVOICE_RECEIVED,
        SupplierInvoiceStatus.DATA_EXTRACTED,
        SupplierInvoiceStatus.AWAITING_MATCHING,
        SupplierInvoiceStatus.MATCHED,
        SupplierInvoiceStatus.EXCEPTION_FOUND,
    ]:
        return "RECEIVED_NOT_HR_APPROVED"
    if invoice.status == SupplierInvoiceStatus.HR_APPROVED:
        return "HR_APPROVED_NO_TBCN"
    if invoice.status == SupplierInvoiceStatus.TBCN_GENERATED:
        return "TBCN_GENERATED_NOT_SENT"
    if invoice.status in [SupplierInvoiceStatus.SENT_TO_FINANCE, SupplierInvoiceStatus.FINANCE_ACCEPTED]:
        return "SENT_TO_FINANCE_NOT_PAID"
    return None


def _aging_bucket(age_days):
    for key, label, minimum, maximum in AGING_BUCKETS:
        if age_days >= minimum and (maximum is None or age_days <= maximum):
            return key, label
    return "over_30", "Over 30 days"


def _bucket_sort(bucket):
    return [item[0] for item in AGING_BUCKETS].index(bucket)


def _travel_cases_awaiting_booking(user):
    queryset = apply_scope_filter(TravelCase.objects.all(), user, "travel_case")
    return [
        _action(
            item_id=f"travel-case-booking-{case.id}",
            item_type="TRAVEL_CASE",
            title="Travel case awaiting booking",
            description=f"{case.case_number} needs booking",
            status=case.current_status,
            priority=case.priority,
            source_date=case.submitted_at or case.created_at,
            assigned_to_me=case.assigned_to_id == user.id,
            url=f"/travel-requests/{case.id}",
            group_key="travel_booking",
        )
        for case in queryset.filter(current_status__in=[TravelCaseStatus.SUBMITTED_BY_HR, TravelCaseStatus.UNDER_BOOKING]).select_related(
            "assigned_to"
        )[:25]
    ]


def _assigned_travel_cases(user):
    if not user or not user.is_authenticated:
        return []
    return [
        _action(
            item_id=f"travel-case-assigned-{case.id}",
            item_type="TRAVEL_CASE",
            title="Travel case assigned to you",
            description=f"{case.case_number} is assigned to you",
            status=case.current_status,
            priority=case.priority,
            source_date=case.updated_at,
            assigned_to_me=True,
            url=f"/travel-requests/{case.id}",
            group_key="assigned_cases",
        )
        for case in TravelCase.objects.filter(assigned_to=user)
        .exclude(current_status__in=[TravelCaseStatus.CLOSED, TravelCaseStatus.CANCELLED])
        .select_related("assigned_to")[:25]
    ]


def _ticket_extraction_actions():
    return [
        _action(
            item_id=f"ticket-extraction-{job.id}",
            item_type="TICKET_EXTRACTION",
            title="Ticket extraction needs confirmation",
            description=f"Review extracted {job.get_document_type_display().lower()} data",
            status=job.status,
            priority="NORMAL",
            source_date=job.created_at,
            assigned_to_me=False,
            url=f"/upload-ticket/{job.id}/review",
            group_key="ticket_extractions",
        )
        for job in DocumentExtractionJob.objects.filter(
            document_type__in=[DocumentType.FLIGHT_TICKET, DocumentType.CHANGED_TICKET, DocumentType.REISSUED_TICKET],
            status__in=[ExtractionStatus.EXTRACTED, ExtractionStatus.NEEDS_REVIEW],
        )[:25]
    ]


def _invoice_extraction_actions():
    return [
        _action(
            item_id=f"invoice-extraction-{job.id}",
            item_type="SUPPLIER_INVOICE_EXTRACTION",
            title="Supplier invoice extraction needs confirmation",
            description="Review extracted supplier invoice data",
            status=job.status,
            priority="NORMAL",
            source_date=job.created_at,
            assigned_to_me=False,
            url="/supplier-invoices/upload",
            group_key="invoice_extractions",
        )
        for job in DocumentExtractionJob.objects.filter(
            document_type=DocumentType.SUPPLIER_INVOICE,
            status__in=[ExtractionStatus.EXTRACTED, ExtractionStatus.NEEDS_REVIEW],
        )[:25]
    ]


def _tickets_awaiting_invoice_actions(user):
    """Chase list for booked tickets whose supplier invoice is overdue.

    Only overdue tickets are surfaced so the queue stays actionable; the full
    awaiting-invoice list is available through the unbilled-tickets report.
    """
    queryset = _overdue_awaiting_invoice(_tickets_awaiting_invoice(user))
    return [
        _action(
            item_id=f"ticket-awaiting-invoice-{ticket.id}",
            item_type="TICKET_AWAITING_INVOICE",
            title="Booked ticket awaiting a supplier invoice",
            description=f"{ticket.ticket_number} on {ticket.travel_case.case_number} has no supplier invoice yet",
            status=ticket.billing_state,
            priority="NORMAL",
            source_date=ticket.billing_state_changed_at,
            assigned_to_me=False,
            url=f"/travel-requests/{ticket.travel_case_id}/tickets",
            group_key="tickets_awaiting_invoice",
        )
        for ticket in queryset.select_related("travel_case", "supplier").order_by("billing_state_changed_at")[:25]
    ]


def _invoice_exception_actions(user):
    return [
        _action(
            item_id=f"invoice-line-{line.id}",
            item_type="INVOICE_LINE_EXCEPTION",
            title="Invoice line needs matching",
            description=f"{line.ticket_number} has status {line.match_status}",
            status=line.match_status,
            priority="HIGH" if line.match_status == MatchStatus.EXCEPTION else "NORMAL",
            source_date=line.updated_at,
            assigned_to_me=False,
            url=f"/supplier-invoices/{line.supplier_invoice_id}/matching",
            group_key="invoice_exceptions",
        )
        for line in _scoped_invoice_lines(user).filter(match_status__in=[MatchStatus.UNMATCHED, MatchStatus.DIFFERENCE, MatchStatus.EXCEPTION])
        .select_related("supplier_invoice")[:25]
    ]


def _invoice_hr_approval_actions(user):
    return [
        _action(
            item_id=f"invoice-hr-approval-{invoice.id}",
            item_type="SUPPLIER_INVOICE",
            title="Supplier invoice awaiting HR approval",
            description=f"{invoice.invoice_record_number} is matched and ready for HR approval",
            status=invoice.status,
            priority="NORMAL",
            source_date=invoice.received_date,
            assigned_to_me=False,
            url=f"/supplier-invoices/{invoice.id}/matching",
            group_key="hr_approval",
        )
        for invoice in _scoped_supplier_invoices(user).filter(status=SupplierInvoiceStatus.MATCHED).select_related("supplier")[:25]
    ]


def _tbcn_ready_actions(user):
    return [
        _action(
            item_id=f"invoice-tbcn-ready-{invoice.id}",
            item_type="TBCN_READY",
            title="HR-approved invoice ready for TBCN",
            description=f"{invoice.invoice_record_number} can generate a TBCN",
            status=invoice.status,
            priority="HIGH",
            source_date=invoice.approved_at or invoice.updated_at,
            assigned_to_me=False,
            url=f"/supplier-invoices/{invoice.id}/matching",
            group_key="tbcn_ready",
        )
        for invoice in _scoped_supplier_invoices(user).filter(status=SupplierInvoiceStatus.HR_APPROVED, billing_confirmations__isnull=True)[:25]
    ]


def _tbcn_not_sent_actions(user):
    return [
        _action(
            item_id=f"tbcn-not-sent-{tbcn.id}",
            item_type="TBCN",
            title="TBCN generated but not sent to finance",
            description=f"{tbcn.confirmation_no} is ready to send",
            status=tbcn.status,
            priority="HIGH",
            source_date=tbcn.generated_at,
            assigned_to_me=False,
            url=f"/tbcn/{tbcn.id}",
            group_key="tbcn_not_sent",
        )
        for tbcn in _scoped_tbcn(user).filter(
            status=BillingConfirmationStatus.GENERATED,
            finance_status=FinanceStatus.NOT_SENT,
        )[:25]
    ]


def _finance_acceptance_actions(user):
    return [
        _action(
            item_id=f"tbcn-finance-acceptance-{tbcn.id}",
            item_type="TBCN",
            title="TBCN sent to finance",
            description=f"{tbcn.confirmation_no} awaits finance acceptance",
            status=tbcn.finance_status,
            priority="HIGH",
            source_date=tbcn.sent_to_finance_at or tbcn.updated_at,
            assigned_to_me=False,
            url=f"/tbcn/{tbcn.id}",
            group_key="finance_acceptance",
        )
        for tbcn in _scoped_tbcn(user).filter(finance_status=FinanceStatus.SENT)[:25]
    ]


def _finance_payment_actions(user):
    return [
        _action(
            item_id=f"tbcn-payment-{tbcn.id}",
            item_type="TBCN",
            title="TBCN accepted but not paid",
            description=f"{tbcn.confirmation_no} awaits payment",
            status=tbcn.finance_status,
            priority="NORMAL",
            source_date=tbcn.updated_at,
            assigned_to_me=False,
            url=f"/tbcn/{tbcn.id}",
            group_key="finance_payment",
        )
        for tbcn in _scoped_tbcn(user).filter(finance_status=FinanceStatus.ACCEPTED)[:25]
    ]


def _permit_actions(user):
    today = timezone.localdate()
    return [
        _action(
            item_id=f"permit-{permit.id}",
            item_type="PERMIT",
            title="Permit pending or expired",
            description=f"{permit.travel_case.case_number} {permit.get_permit_type_display()} is {permit.status.lower()}",
            status=permit.status,
            priority="HIGH" if permit.status == PermitStatus.EXPIRED or (permit.expiry_date and permit.expiry_date < today) else "NORMAL",
            source_date=permit.expiry_date or permit.updated_at,
            assigned_to_me=False,
            url=f"/travel-requests/{permit.travel_case_id}/permits",
            group_key="permits",
        )
        for permit in _scoped_permits(user).filter(Q(status__in=[PermitStatus.PENDING, PermitStatus.EXPIRED]) | Q(expiry_date__lt=today)).select_related(
            "travel_case"
        )[:25]
    ]


def _action(item_id, item_type, title, description, status, priority, source_date, assigned_to_me, url, group_key):
    return {
        "id": item_id,
        "type": item_type,
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "age_days": _age_days(source_date),
        "assigned_to_me": assigned_to_me,
        "url": url,
        "_group_key": group_key,
    }


def _age_days(value):
    if isinstance(value, datetime):
        source_date = timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    elif isinstance(value, date):
        source_date = value
    else:
        source_date = timezone.localdate()
    return max((timezone.localdate() - source_date).days, 0)


def _dedupe_items(items):
    deduped = {}
    for item in items:
        deduped[item["id"]] = item
    return list(deduped.values())


def _group_pending_items(items):
    grouped = []
    for key, (title, tone) in PENDING_GROUPS.items():
        group_items = [_public_action(item) for item in items if item["_group_key"] == key]
        if group_items:
            grouped.append({"key": key, "title": title, "count": len(group_items), "tone": tone, "items": group_items})
    return grouped


def _public_action(item):
    return {field: value for field, value in item.items() if field != "_group_key"}
