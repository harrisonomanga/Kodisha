"""Small helpers for tests and the demo seed command."""
from datetime import date
from types import SimpleNamespace

from core.models import Organisation
from payments.models import PaymentChannelConfig
from portfolio.models import Landlord, Property, Tenant, Unit
from portfolio.services import create_lease


def build_org(name="Acme Agents", prop_code="PLT", units=("A1", "A2", "B1"), rent_minor=1_500_000, shortcode="123456"):
    org = Organisation.objects.create(name=name, org_type="agent")
    landlord = Landlord.objects.create(org=org, name=f"{name} landlord")
    prop = Property.objects.create(org=org, landlord=landlord, name=f"{name} Plot", code=prop_code)
    made = [Unit.objects.create(org=org, property=prop, identifier=i, rent_minor=rent_minor) for i in units]
    cfg = PaymentChannelConfig.objects.create(org=org, channel="mpesa", shortcode=shortcode)
    return SimpleNamespace(org=org, prop=prop, units=made, cfg=cfg)


def add_tenant_lease(org, unit, name="Jane Wanjiru", start=date(2026, 10, 1), **kw):
    tenant = Tenant.objects.create(org=org, full_name=name, phone="2547XXXXXX01")
    return create_lease(unit, tenant, start, **kw)
