from django.conf import settings

from core.models import Membership
from payments.models import ReviewItem

from .access import WRITE_ROLES

SECTIONS = {
    "dashboard": "home", "properties": "properties", "property": "properties", "unit": "properties",
    "tenants": "tenants", "lease": "tenants", "move": "tenants", "invoices": "invoices", "invoice": "invoices",
    "payments": "payments", "payment": "payments", "review": "review", "report": "reports", "settings": "settings",
}


def shell(request):
    ctx = {"product_name": settings.PRODUCT_NAME, "debug_tools": (settings.DEBUG or settings.SANDBOX)}
    org = getattr(request, "org", None)
    if org is not None:
        match = getattr(request, "resolver_match", None)
        name = match.url_name if match and match.url_name else ""
        can_review = request.property_ids is None
        ctx.update(
            org=org, orgs=request.orgs, role=request.role,
            role_label=dict(Membership.Role.choices).get(request.role, "Owner"),
            can_write=request.role in WRITE_ROLES, can_review=can_review, section=SECTIONS.get(name.split("_")[0]),
            review_count=ReviewItem.objects.filter(payment__org=org, status="open").count() if can_review else 0,
        )
    return ctx
