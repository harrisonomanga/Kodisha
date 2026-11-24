from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import AppendOnlyModel, Organisation


class Account(models.Model):
    class Kind(models.TextChoices):
        RECEIVABLE = "receivable", "Tenant receivable"          # per lease, debit-normal
        TENANT_CREDIT = "tenant_credit", "Tenant credit"        # per lease, credit-normal
        INCOME = "income", "Rental income"                      # per org, credit-normal
        CASH = "cash", "Collections (received funds)"           # per org, debit-normal
        UNALLOCATED = "unallocated", "Unallocated receipts"     # per org, credit-normal (suspense)

    DEBIT_NORMAL = {Kind.RECEIVABLE, Kind.CASH}

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="ledger_accounts")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    lease = models.ForeignKey("portfolio.Lease", null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_accounts")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "kind"], condition=Q(lease__isnull=True), name="uniq_org_account"),
            models.UniqueConstraint(fields=["org", "kind", "lease"], condition=Q(lease__isnull=False), name="uniq_lease_account"),
        ]

    def __str__(self):
        return f"{self.kind}{f' lease={self.lease_id}' if self.lease_id else ''} (org {self.org_id})"


class Journal(AppendOnlyModel):
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="journals")
    posted_at = models.DateTimeField(default=timezone.now, db_index=True)
    memo = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.BigIntegerField(null=True, blank=True)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    idempotency_key = models.CharField(max_length=120, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["org", "source_type", "source_id"])]
        constraints = [models.UniqueConstraint(fields=["org", "idempotency_key"], name="uniq_journal_key_per_org")]

    def __str__(self):
        return f"Journal {self.pk} {self.memo}"


class Entry(AppendOnlyModel):
    DEBIT, CREDIT = "D", "C"

    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    direction = models.CharField(max_length=1, choices=[(DEBIT, "Debit"), (CREDIT, "Credit")])
    amount_minor = models.BigIntegerField()

    class Meta:
        verbose_name_plural = "entries"
        constraints = [models.CheckConstraint(condition=Q(amount_minor__gt=0), name="entry_amount_positive")]
        indexes = [models.Index(fields=["account", "direction"])]
