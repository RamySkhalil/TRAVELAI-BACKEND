from django.db import transaction

from apps.common.models import ControlSequence


def generate_sequence(code: str, year: int, prefix_format: str) -> str:
    """Generate a database-safe sequence value.

    Example:
        generate_sequence("TRV-LY", 2026, "{code}-{year}-{number:06d}")
        -> "TRV-LY-2026-000001"
    """
    if "{code}" not in prefix_format or "{year}" not in prefix_format or "{number" not in prefix_format:
        raise ValueError("prefix_format must include {code}, {year}, and {number}")

    with transaction.atomic():
        sequence, _ = (
            ControlSequence.objects.select_for_update()
            .get_or_create(code=code, year=year, defaults={"last_number": 0})
        )
        sequence.last_number += 1
        sequence.save(update_fields=["last_number", "updated_at"])
        return prefix_format.format(code=code, year=year, number=sequence.last_number)
