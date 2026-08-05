from django.db import migrations
from django.db.models import Exists, OuterRef, F, Q
from django.db.models.functions import Coalesce


NON_BILLABLE_ACTIONS = ["WRONG_UPLOAD"]


def backfill_billing_state(apps, schema_editor):
    """Derive the billing state of tickets that existed before the field.

    A ticket already carrying a supplier invoice line has been invoiced; every
    other confirmed, billable ticket is an open payable dated from when it was
    confirmed. Draft and wrongly uploaded tickets stay not billable.
    """
    TicketVersion = apps.get_model("ticket_versions", "TicketVersion")
    SupplierInvoiceLine = apps.get_model("supplier_invoices", "SupplierInvoiceLine")

    has_invoice_line = Exists(SupplierInvoiceLine.objects.filter(ticket_version=OuterRef("pk")))

    TicketVersion.objects.annotate(invoiced=has_invoice_line).filter(invoiced=True).update(
        billing_state="INVOICED",
        billing_state_changed_at=Coalesce(F("confirmed_at"), F("created_at")),
    )

    TicketVersion.objects.annotate(invoiced=has_invoice_line).filter(
        ~Q(ticket_status="DRAFT"),
        ~Q(ticket_action__in=NON_BILLABLE_ACTIONS),
        invoiced=False,
    ).update(
        billing_state="AWAITING_INVOICE",
        billing_state_changed_at=Coalesce(F("confirmed_at"), F("created_at")),
    )


def clear_billing_state(apps, schema_editor):
    TicketVersion = apps.get_model("ticket_versions", "TicketVersion")
    TicketVersion.objects.update(billing_state="NOT_BILLABLE", billing_state_changed_at=None, billing_state_note="")


class Migration(migrations.Migration):

    dependencies = [
        ("ticket_versions", "0003_ticketversion_billing_state_and_more"),
        ("supplier_invoices", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_billing_state, clear_billing_state),
    ]
