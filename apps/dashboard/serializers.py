from rest_framework import serializers


class DashboardCountSerializer(serializers.Serializer):
    total = serializers.IntegerField()


class DashboardTravelSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    draft = serializers.IntegerField()
    submitted = serializers.IntegerField()
    under_booking = serializers.IntegerField()
    closed = serializers.IntegerField()
    cancelled = serializers.IntegerField()


class DashboardTicketSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    active = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    changed_reissued = serializers.IntegerField()
    no_show = serializers.IntegerField()


class DashboardPermitSummarySerializer(serializers.Serializer):
    pending_egypt = serializers.IntegerField()
    pending_libya = serializers.IntegerField()
    expired = serializers.IntegerField()
    expiring_soon = serializers.IntegerField()


class DashboardSupplierInvoiceSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    awaiting_matching = serializers.IntegerField()
    matched = serializers.IntegerField()
    exception_found = serializers.IntegerField()
    hr_approved = serializers.IntegerField()
    tbcn_generated = serializers.IntegerField()


class DashboardCurrencyAmountSerializer(serializers.Serializer):
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class DashboardTbcnFinanceSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    generated_not_sent = serializers.IntegerField()
    sent_to_finance = serializers.IntegerField()
    finance_accepted = serializers.IntegerField()
    paid = serializers.IntegerField()
    unpaid_by_currency = DashboardCurrencyAmountSerializer(many=True)
    paid_by_currency = DashboardCurrencyAmountSerializer(many=True)


class DashboardSummarySerializer(serializers.Serializer):
    travel = DashboardTravelSummarySerializer()
    tickets = DashboardTicketSummarySerializer()
    permits = DashboardPermitSummarySerializer()
    supplier_invoices = DashboardSupplierInvoiceSummarySerializer()
    tbcn_finance = DashboardTbcnFinanceSummarySerializer()


class OperationalKpiSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit = serializers.CharField()
    currency = serializers.CharField(required=False, allow_blank=True)
    tone = serializers.CharField()


class OperationalKpisResponseSerializer(serializers.Serializer):
    results = OperationalKpiSerializer(many=True)


class CostByMonthSerializer(serializers.Serializer):
    month = serializers.CharField()
    currency = serializers.CharField()
    invoice_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class CostByMonthResponseSerializer(serializers.Serializer):
    results = CostByMonthSerializer(many=True)


class CostByProjectSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    project_code = serializers.CharField()
    project_name = serializers.CharField()
    currency = serializers.CharField()
    line_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    share_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class CostByProjectResponseSerializer(serializers.Serializer):
    results = CostByProjectSerializer(many=True)


class CostBySupplierSerializer(serializers.Serializer):
    supplier_id = serializers.IntegerField()
    supplier_code = serializers.CharField()
    supplier_name = serializers.CharField()
    currency = serializers.CharField()
    invoice_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    share_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class CostBySupplierResponseSerializer(serializers.Serializer):
    results = CostBySupplierSerializer(many=True)


class CostByRouteSerializer(serializers.Serializer):
    route_from = serializers.CharField()
    route_to = serializers.CharField()
    currency = serializers.CharField()
    ticket_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    share_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class CostByRouteResponseSerializer(serializers.Serializer):
    results = CostByRouteSerializer(many=True)


class DashboardResultsSerializer(serializers.Serializer):
    results = serializers.ListField(child=serializers.DictField())


class SupplierAgingSerializer(serializers.Serializer):
    stage = serializers.CharField()
    stage_label = serializers.CharField()
    bucket = serializers.CharField()
    bucket_label = serializers.CharField()
    currency = serializers.CharField()
    invoice_count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class SupplierAgingResponseSerializer(serializers.Serializer):
    results = SupplierAgingSerializer(many=True)


class PendingActionSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    priority = serializers.CharField()
    age_days = serializers.IntegerField()
    assigned_to_me = serializers.BooleanField()
    url = serializers.CharField()


class PendingActionGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    count = serializers.IntegerField()
    tone = serializers.CharField()
    items = PendingActionSerializer(many=True)


class PendingActionsResponseSerializer(serializers.Serializer):
    items = PendingActionSerializer(many=True)
    groups = PendingActionGroupSerializer(many=True)
