import secrets

from django.conf import settings
from django.db import models

from core.models import ImmutableRecordError, Organisation, TimeStamped


def new_webhook_token():
    return secrets.token_urlsafe(24)


class PaymentChannelConfig(TimeStamped):
    """Per-organisation channel settings. Credentials are NOT stored here yet (see README, open questions)."""

    class Channel(models.TextChoices):
        MPESA = "mpesa", "M-Pesa (Paybill/Till)"
        BANK = "bank", "Bank transfer / PesaLink"
        AIRTEL = "airtel", "Airtel Money"
        MANUAL = "manual", "Manual (cash, cheque, slip)"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="channel_configs")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    shortcode = models.CharField(max_length=40, blank=True, help_text="Paybill / Till number or bank account label")
    webhook_token = models.CharField(max_length=64, unique=True, default=new_webhook_token)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "channel", "shortcode"], name="uniq_channel_config")]

    def __str__(self):
        return f"{self.org} {self.channel} {self.shortcode}"


class BankProfile(TimeStamped):
    """Maps a bank's CSV columns to our standard fields. Adding a bank is data, not code."""

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="bank_profiles")
    name = models.CharField(max_length=100)
    date_format = models.CharField(max_length=30, default="%d/%m/%Y")
    columns = models.JSONField(
        help_text='e.g. {"date": "Date", "narration": "Description", "reference": "Reference", '
                  '"credit": "Credit", "txn_id": "Txn ID", "balance": "Balance", "payer_name": "Name"}'
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "name"], name="uniq_bank_profile")]

    def __str__(self):
        return self.name


class RawPaymentEvent(models.Model):
    """Every notification or statement line exactly as received, before any processing."""

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="raw_payment_events")
    channel = models.CharField(max_length=10)
    source_label = models.CharField(max_length=100, blank=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)

    MUTABLE = {"processed_at", "processing_error"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = set(kwargs.get("update_fields") or [])
            if not fields or not fields <= self.MUTABLE:
                raise ImmutableRecordError("only processing status can change on a raw payment event")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError("raw payment events cannot be deleted")


class Payment(TimeStamped):
    class Status(models.TextChoices):
        MATCHED = "matched", "Matched and allocated"
        UNMATCHED = "unmatched", "Unmatched (needs review)"
        AMBIGUOUS = "ambiguous", "Ambiguous (needs review)"
        REVERSED = "reversed", "Reversed"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="payments")
    channel = models.CharField(max_length=10)
    channel_txn_ref = models.CharField(max_length=120)
    amount_minor = models.BigIntegerField()
    payer_name = models.CharField(max_length=200, blank=True)
    payer_phone = models.CharField(max_length=64, blank=True)
    reference_raw = models.CharField(max_length=200, blank=True)
    paid_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNMATCHED)
    unit = models.ForeignKey("portfolio.Unit", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    lease = models.ForeignKey("portfolio.Lease", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    raw_event = models.ForeignKey(RawPaymentEvent, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "channel", "channel_txn_ref"], name="uniq_payment_ref_per_channel")
        ]
        indexes = [models.Index(fields=["org", "status"])]

    def __str__(self):
        return f"{self.channel}:{self.channel_txn_ref} {self.amount_minor / 100:.2f}"


class ReviewItem(TimeStamped):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="review_item")
    reason = models.CharField(max_length=40)
    suggestions = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)
