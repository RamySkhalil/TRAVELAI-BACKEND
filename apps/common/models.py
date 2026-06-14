import uuid

from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True


class ControlSequence(TimeStampedModel):
    code = models.CharField(max_length=20)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["code", "year"], name="unique_control_sequence_code_year"),
        ]
        indexes = [
            models.Index(fields=["code", "year"]),
        ]
        ordering = ["code", "year"]

    def __str__(self) -> str:
        return f"{self.code}-{self.year}: {self.last_number}"

# Create your models here.
