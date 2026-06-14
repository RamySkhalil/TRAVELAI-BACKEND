from datetime import date

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit_logs.models import AuditLog
from apps.audit_logs.services import create_audit_log
from apps.common.services.sequences import generate_sequence
from apps.travel_cases.models import TravelCase, TravelCaseStatus


def next_travel_case_number(country_code: str, year: int | None = None) -> str:
    target_year = year or date.today().year
    return generate_sequence(f"TRV-{country_code}", target_year, "{code}-{year}-{number:06d}")


def create_travel_case(data, user) -> TravelCase:
    employee = data["employee"]
    country = data["country"]
    requested_date = data["requested_travel_date"]
    data["case_number"] = next_travel_case_number(country.code, requested_date.year)
    data["badge_number"] = employee.badge_number
    data["employee_name"] = employee.full_name
    data["current_status"] = TravelCaseStatus.DRAFT
    data["created_by"] = user
    travel_case = TravelCase.objects.create(**data)
    create_audit_log(
        user=user,
        action="Travel Case Created",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        new_value={"case_number": travel_case.case_number, "status": travel_case.current_status},
    )
    return travel_case


def submit_travel_case(travel_case: TravelCase, user) -> TravelCase:
    if travel_case.current_status != TravelCaseStatus.DRAFT:
        raise ValidationError("Only draft travel cases can be submitted.")
    old_status = travel_case.current_status
    travel_case.current_status = TravelCaseStatus.SUBMITTED_BY_HR
    travel_case.submitted_at = timezone.now()
    travel_case.save(update_fields=["current_status", "submitted_at", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Submitted",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status},
    )
    return travel_case


def assign_travel_case(travel_case: TravelCase, assigned_to, user) -> TravelCase:
    if travel_case.current_status in [TravelCaseStatus.CANCELLED, TravelCaseStatus.CLOSED, TravelCaseStatus.PAID]:
        raise ValidationError("Closed, paid, or cancelled travel cases cannot be assigned.")
    old_value = {"assigned_to": travel_case.assigned_to_id, "status": travel_case.current_status}
    travel_case.assigned_to = assigned_to
    if travel_case.current_status in [TravelCaseStatus.SUBMITTED_BY_HR, TravelCaseStatus.DRAFT]:
        travel_case.current_status = TravelCaseStatus.UNDER_BOOKING
    travel_case.save(update_fields=["assigned_to", "current_status", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Assigned",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value=old_value,
        new_value={"assigned_to": assigned_to.id if assigned_to else None, "status": travel_case.current_status},
    )
    return travel_case


def close_travel_case(travel_case: TravelCase, user) -> TravelCase:
    valid_final_statuses = [TravelCaseStatus.PAID, TravelCaseStatus.SENT_TO_FINANCE, TravelCaseStatus.INVOICE_MATCHED]
    if travel_case.current_status == TravelCaseStatus.CANCELLED:
        raise ValidationError("Cancelled travel cases cannot be closed through this action.")
    if travel_case.current_status not in valid_final_statuses:
        raise ValidationError("Travel case must reach a valid final status before closing.")
    old_status = travel_case.current_status
    travel_case.current_status = TravelCaseStatus.CLOSED
    travel_case.closed_at = timezone.now()
    travel_case.save(update_fields=["current_status", "closed_at", "updated_at"])
    create_audit_log(
        user=user,
        action="Travel Case Closed",
        entity_type="TravelCase",
        entity_id=travel_case.id,
        old_value={"status": old_status},
        new_value={"status": travel_case.current_status},
    )
    return travel_case


def get_travel_case_timeline(travel_case: TravelCase):
    return [
        {
            "action": log.action,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "metadata": log.metadata,
            "created_at": log.created_at,
            "user": log.user_id,
        }
        for log in AuditLog.objects.filter(entity_type="TravelCase", entity_id=str(travel_case.id)).order_by("created_at")
    ]
