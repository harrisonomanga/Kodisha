from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect, render

WRITE_ROLES = {"owner", "manager", "finance"}


def org_view(view=None, write=False, owner_only=False):
    """Sign-in, organisation and role checks in one place. Read-only roles can look but not change."""

    def decorate(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.org is None:
                if request.user.is_superuser:
                    return redirect("onboarding")
                return render(request, "web/no_access.html", status=403)
            if (write and request.role not in WRITE_ROLES) or (owner_only and request.role != "owner"):
                return render(request, "web/forbidden.html", status=403)
            return fn(request, *args, **kwargs)

        return wrapper

    return decorate(view) if view else decorate
