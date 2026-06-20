from rest_framework.exceptions import ValidationError

from apps.common.models import CurrencyChoices


SUPPORTED_CURRENCY_LABEL = ", ".join(CurrencyChoices.values)


def normalize_currency(value, field_name: str = "currency") -> str:
    currency = str(value or "").strip().upper()
    if not currency:
        raise ValidationError({field_name: f"Currency is required. Supported currencies are {SUPPORTED_CURRENCY_LABEL}."})
    if currency not in CurrencyChoices.values:
        raise ValidationError({field_name: f"Unsupported currency. Use one of: {SUPPORTED_CURRENCY_LABEL}."})
    return currency
