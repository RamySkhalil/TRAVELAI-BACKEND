from django.utils import timezone


class LockedRecordError(ValueError):
    pass


def ensure_unlocked(instance) -> None:
    if getattr(instance, "is_locked", False):
        raise LockedRecordError(f"{instance.__class__.__name__} is locked and must be revised instead of edited.")


def lock_instance(instance, *, save: bool = True):
    instance.is_locked = True
    if hasattr(instance, "locked_at") and getattr(instance, "locked_at") is None:
        instance.locked_at = timezone.now()
    if save:
        fields = ["is_locked"]
        if hasattr(instance, "locked_at"):
            fields.append("locked_at")
        instance.save(update_fields=fields)
    return instance
