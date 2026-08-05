from rest_framework import serializers

from apps.common.currency import normalize_currency

from .models import TicketVersion


class TicketVersionSerializer(serializers.ModelSerializer):
    uploaded_ticket_file_url = serializers.SerializerMethodField()
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    confirmed_by_username = serializers.CharField(source="confirmed_by.username", read_only=True)

    def validate_currency(self, value):
        return normalize_currency(value)

    def get_uploaded_ticket_file_url(self, obj) -> str | None:
        if not obj.uploaded_ticket_file:
            return None
        url = obj.uploaded_ticket_file.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    class Meta:
        model = TicketVersion
        fields = "__all__"
        read_only_fields = (
            "version_number",
            "supplier_name",
            "confirmed_by_username",
            "is_locked",
            "locked_at",
            # Billing state is owned by the confirmation and matching services so
            # a payable can never be opened or closed by a direct write.
            "billing_state",
            "billing_state_changed_at",
            "billing_state_note",
            "created_at",
            "updated_at",
        )
