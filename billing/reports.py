from collections import defaultdict
from datetime import date

from .models import Invoice
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")


def bucket_for(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def arrears_ageing(org, as_of=None):
    """One row per lease with amounts owing grouped by how overdue they are (FR-REP-02, first cut)."""
    as_of = as_of or date.today()
    invoices = (
        Invoice.objects.filter(org=org, status=Invoice.Status.ISSUED)
        .annotate(paid=Coalesce(Sum("allocations__amount_minor", filter=Q(allocations__reversed=False)), 0))
        .filter(total_minor__gt=F("paid"))
        .select_related("lease__tenant", "lease__unit__property")
    )
    rows = defaultdict(lambda: {b: 0 for b in BUCKETS})
    meta = {}
    for inv in invoices:
        owing = inv.total_minor - inv.paid
        rows[inv.lease_id][bucket_for((as_of - inv.due_date).days)] += owing
        meta[inv.lease_id] = inv.lease
    result = []
    for lease_id, buckets in rows.items():
        lease = meta[lease_id]
        result.append({
            "lease_id": lease_id,
            "tenant": lease.tenant.full_name,
            "unit": str(lease.unit),
            "property": lease.unit.property.name,
            "buckets": buckets,
            "total": sum(buckets.values()),
        })
    return sorted(result, key=lambda r: -r["total"])
