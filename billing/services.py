import calendar
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

from ledger.models import Account, Entry
from ledger.services import balance, get_account, post_journal
from portfolio.models import Lease

from .models import Allocation, ChargeType, Invoice, InvoiceLine, LeaseCharge


def month_start(d):
    return d.replace(day=1)


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def get_rent_charge_type(org):
    ct, _ = ChargeType.objects.get_or_create(org=org, code="RENT", defaults={"name": "Rent"})
    return ct


def prorate(amount_minor, days, total_days):
    """Integer maths, round half up, so a full month returns the amount unchanged."""
    return (amount_minor * days * 2 + total_days) // (2 * total_days)


def billable_days(lease, period):
    """(days billed, days in month) for a lease in a month, or None if the lease is not live then."""
    dim = days_in_month(period)
    first, last = period, period.replace(day=dim)
    start = max(lease.start_date, first)
    end = min(lease.end_date or last, last)
    if end < start:
        return None
    return (end - start).days + 1, dim


def open_invoices(lease):
    """Issued invoices with something still owing, oldest due first, each annotated with `paid`."""
    return (
        Invoice.objects.filter(lease=lease, status=Invoice.Status.ISSUED)
        .annotate(paid=Coalesce(Sum("allocations__amount_minor", filter=Q(allocations__reversed=False)), 0))
        .filter(total_minor__gt=F("paid"))
        .order_by("due_date", "period", "id")
    )


def outstanding(invoice):
    paid = invoice.allocations.filter(reversed=False).aggregate(s=Sum("amount_minor"))["s"] or 0
    return invoice.total_minor - paid


def allocate_to_invoices(lease, amount_minor, payment=None, source=Allocation.Source.PAYMENT):
    """Apply money to open invoices, oldest first. Returns the amount actually applied."""
    remaining = amount_minor
    applied = 0
    for invoice in list(open_invoices(lease)):
        if remaining <= 0:
            break
        take = min(remaining, invoice.total_minor - invoice.paid)
        Allocation.objects.create(org=lease.org, invoice=invoice, payment=payment, source=source, amount_minor=take)
        remaining -= take
        applied += take
    return applied


def apply_credit(lease):
    """Use any tenant credit against open invoices. Returns the amount applied."""
    credit_acct = get_account(lease.org, Account.Kind.TENANT_CREDIT, lease)
    available = balance(credit_acct)
    if available <= 0:
        return 0
    applied = allocate_to_invoices(lease, available, payment=None, source=Allocation.Source.CREDIT)
    if applied:
        recv = get_account(lease.org, Account.Kind.RECEIVABLE, lease)
        post_journal(
            lease.org,
            [(credit_acct, Entry.DEBIT, applied), (recv, Entry.CREDIT, applied)],
            memo="Tenant credit applied to invoice", source_type="lease", source_id=lease.pk,
        )
    return applied


@transaction.atomic
def generate_invoice(lease, period, issue_date=None):
    """Create the invoice for one lease and month. Safe to run twice: returns (invoice, created)."""
    period = month_start(period)
    existing = Invoice.objects.filter(lease=lease, period=period).first()
    if existing:
        return existing, False
    days = billable_days(lease, period)
    if days is None:
        return None, False
    billed, dim = days

    rent_type = get_rent_charge_type(lease.org)
    lines = []
    rent = prorate(lease.rent_minor, billed, dim)
    if rent > 0:
        label = f"Rent {period:%B %Y}" + ("" if billed == dim else f" (pro-rata {billed}/{dim} days)")
        lines.append((rent_type, label, rent))
    for lc in lease.charges.filter(is_active=True).select_related("charge_type"):
        lines.append((lc.charge_type, lc.charge_type.name, lc.amount_minor))
    total = sum(amount for _, _, amount in lines)
    if total <= 0:
        return None, False

    due = date(period.year, period.month, min(lease.billing_day, dim))
    try:
        with transaction.atomic():
            invoice = Invoice.objects.create(
                org=lease.org, lease=lease, period=period,
                issue_date=issue_date or date.today(), due_date=due, total_minor=total,
            )
    except IntegrityError:
        return Invoice.objects.get(lease=lease, period=period), False
    invoice.number = f"INV-{lease.org_id}-{invoice.pk:07d}"
    invoice.save(update_fields=["number", "updated_at"])
    InvoiceLine.objects.bulk_create(
        [InvoiceLine(invoice=invoice, charge_type=ct, description=desc, amount_minor=amt) for ct, desc, amt in lines]
    )
    post_journal(
        lease.org,
        [
            (get_account(lease.org, Account.Kind.RECEIVABLE, lease), Entry.DEBIT, total),
            (get_account(lease.org, Account.Kind.INCOME), Entry.CREDIT, total),
        ],
        memo=f"Invoice {invoice.number}", source_type="invoice", source_id=invoice.pk,
        key=f"invoice:{invoice.pk}",
    )
    apply_credit(lease)
    return invoice, True


def generate_due_invoices(org, today=None):
    """Create this month's invoice for every live lease whose invoice date has arrived."""
    today = today or date.today()
    period = month_start(today)
    dim = days_in_month(period)
    created = skipped = 0
    leases = Lease.objects.filter(org=org, status=Lease.Status.ACTIVE).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=period)
    )
    for lease in leases:
        due = date(period.year, period.month, min(lease.billing_day, dim))
        if today < due - timedelta(days=org.invoice_lead_days):
            skipped += 1
            continue
        _, was_created = generate_invoice(lease, period, issue_date=today)
        created += int(was_created)
    return {"created": created, "not_yet_due": skipped}
