from django.contrib import admin

from .models import BankProfile, Payment, PaymentChannelConfig, RawPaymentEvent, ReviewItem

admin.site.register(PaymentChannelConfig)
admin.site.register(BankProfile)


class ReadOnly:
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("id", "org", "channel", "channel_txn_ref", "amount_minor", "reference_raw", "status", "unit", "paid_at")
    list_filter = ("org", "channel", "status")
    search_fields = ("channel_txn_ref", "reference_raw", "payer_name")

    def has_change_permission(self, request, obj=None):
        return False  # corrections go through assign / reverse, which write ledger entries and audit


@admin.register(ReviewItem)
class ReviewItemAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("id", "payment", "reason", "status", "resolved_by")
    list_filter = ("status", "reason")

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RawPaymentEvent)
class RawPaymentEventAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("id", "org", "channel", "received_at", "processed_at", "processing_error")
    list_filter = ("org", "channel")

    def has_change_permission(self, request, obj=None):
        return False
