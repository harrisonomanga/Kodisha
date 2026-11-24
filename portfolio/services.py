from django.db import transaction

from .models import Lease, Unit


@transaction.atomic
def create_lease(unit, tenant, start_date, rent_minor=None, billing_day=1, end_date=None, deposit_minor=None):
    lease = Lease.objects.create(
        org=unit.org,
        unit=unit,
        tenant=tenant,
        start_date=start_date,
        end_date=end_date,
        rent_minor=unit.rent_minor if rent_minor is None else rent_minor,
        billing_day=billing_day,
        deposit_minor=unit.deposit_minor if deposit_minor is None else deposit_minor,
    )
    unit.status = Unit.Status.OCCUPIED
    unit.save(update_fields=["status", "updated_at"])
    return lease


@transaction.atomic
def end_lease(lease, end_date):
    """Move-out. The final statement and deposit reconciliation are built on top of this (not yet)."""
    lease.status = Lease.Status.ENDED
    lease.end_date = end_date
    lease.save(update_fields=["status", "end_date", "updated_at"])
    lease.unit.status = Unit.Status.VACANT
    lease.unit.save(update_fields=["status", "updated_at"])
    return lease
