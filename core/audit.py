from .models import AuditLog


def log(org, actor, action, obj=None, before=None, after=None):
    """Write one audit record: who did what, to which object, with before and after values."""
    return AuditLog.objects.create(
        org=org,
        actor=actor,
        action=action,
        object_type=type(obj).__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        before=before,
        after=after,
    )
