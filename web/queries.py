"""Every list the UI shows goes through here, so organisation and property scope can't be forgotten."""
from collections import defaultdict

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from billing.models import Allocation, Invoice
from ledger.models import Entry
from payments.models import Payment
from portfolio.models import Lease, Property, Unit


def _scoped(request, qs, path):
    ids = request.property_ids
    return qs if ids is None else qs.filter(**{f"{path}__in": ids})


def properties(request):
    return _scoped(request, Property.objects.filter(org=request.org), "id")


def units(request):
    return _scoped(request, Unit.objects.filter(org=request.org), "property_id")


def leases(request):
    return _scoped(request, Lease.objects.filter(org=request.org), "unit__property_id")


def invoices(request):
    return _scoped(request, Invoice.objects.filter(org=request.org), "lease__unit__property_id")


def payments(request):
    return _scoped(request, Payment.objects.filter(org=request.org), "unit__property_id")


def with_paid(qs):
    return qs.annotate(paid=Coalesce(Sum("allocations__amount_minor", filter=Q(allocations__reversed=False)), 0))


def invoice_state(total, paid, due_date, status, today):
    if status == "void":
        return "void"
    if paid >= total:
        return "paid"
    if due_date < today:
        return "overdue"
    return "partial" if paid > 0 else "open"


def decorate_invoices(invoice_list, today):
    for inv in invoice_list:
        inv.owing = inv.total_minor - inv.paid
        inv.state = invoice_state(inv.total_minor, inv.paid, inv.due_date, inv.status, today)
    return invoice_list


def lease_balances(org, lease_ids=None):
    """{lease_id: {'owing': cents, 'credit': cents}} in a handful of queries."""
    inv = Invoice.objects.filter(org=org, status="issued")
    alloc = Allocation.objects.filter(org=org, reversed=False, invoice__status="issued")
    ent = Entry.objects.filter(account__org=org, account__kind="tenant_credit")
    if lease_ids is not None:
        inv = inv.filter(lease_id__in=lease_ids)
        alloc = alloc.filter(invoice__lease_id__in=lease_ids)
        ent = ent.filter(account__lease_id__in=lease_ids)
    billed = dict(inv.values_list("lease_id").annotate(t=Sum("total_minor")))
    paid = dict(alloc.values_list("invoice__lease_id").annotate(t=Sum("amount_minor")))
    credit = defaultdict(int)
    for row in ent.values("account__lease_id", "direction").annotate(t=Sum("amount_minor")):
        credit[row["account__lease_id"]] += row["t"] if row["direction"] == "C" else -row["t"]
    return {lid: {"owing": billed.get(lid, 0) - paid.get(lid, 0), "credit": credit.get(lid, 0)}
            for lid in set(billed) | set(paid) | set(credit)}
