from core.models import Membership, Organisation
from portfolio.models import PropertyAccess


class OrgContextMiddleware:
    """Works out which organisation, role and property scope the signed-in user is acting in."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.org, request.role, request.orgs, request.property_ids = None, None, [], None
        user = request.user
        if user.is_authenticated:
            roles = {}
            if user.is_superuser:
                orgs = list(Organisation.objects.order_by("name"))
            else:
                memberships = list(Membership.objects.filter(user=user, org__status="active").select_related("org"))
                orgs = [m.org for m in memberships]
                roles = {m.org_id: m.role for m in memberships}
            request.orgs = orgs
            chosen = request.session.get("org_id")
            org = next((o for o in orgs if o.pk == chosen), orgs[0] if orgs else None)
            if org:
                request.org = org
                request.role = "owner" if user.is_superuser else roles.get(org.pk)
                if not user.is_superuser:
                    ids = list(PropertyAccess.objects.filter(
                        membership__user=user, membership__org=org).values_list("property_id", flat=True))
                    request.property_ids = ids or None  # no rows means all properties
        return self.get_response(request)
