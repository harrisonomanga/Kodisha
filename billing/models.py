from django.db import models

from core.models import Organisation, TimeStamped


class ChargeType(TimeStamped):
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="charge_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    is_taxable = models.BooleanField(default=False, help_text="Reserved for commercial leases / VAT (Phase 2)")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "code"], name="uniq_charge_type_code")]

    def __str__(self):
        return self.name


class LeaseCharge(TimeStamped):
    """A recurring charge on a lease (service charge, garbage, security ...). Rent itself is on the lease."""

    lease = models.ForeignKey("portfolio.Lease", on_delete=models.CASCADE, related_name="charges")
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT)
    amount_minor = models.BigIntegerField()
    is_active = models.BooleanField(default=True)


class Invoice(TimeStamped):
    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        VOID = "void", "Void"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="invoices")
    lease = models.ForeignKey("portfolio.Lease", on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=30, blank=True)
    period = models.DateField(help_text="First day of the billing month")
    issue_date = models.DateField()
    due_date = models.DateField()
    total_minor = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ISSUED)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lease", "period"], name="one_invoice_per_lease_period")]
        indexes = [models.Index(fields=["org", "due_date"])]

    def __str__(self):
        return self.number or f"Invoice {self.pk}"


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT)
    description = models.CharField(max_length=200)
    amount_minor = models.BigIntegerField()


class Allocation(models.Model):
    """Amount of a payment (or of tenant credit) applied to an invoice. Reversals flag, never delete."""

    class Source(models.TextChoices):
        PAYMENT = "payment", "Payment"
        CREDIT = "credit", "Tenant credit"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    payment = models.ForeignKey("payments.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="allocations")
    source = models.CharField(max_length=10, choices=Source.choices)
    amount_minor = models.BigIntegerField()
    reversed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
