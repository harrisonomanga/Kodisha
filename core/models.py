from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class ImmutableRecordError(Exception):
    """Raised when code tries to change or delete an append-only record."""


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableRecordError("append-only records cannot be updated")

    def delete(self):
        raise ImmutableRecordError("append-only records cannot be deleted")

    def bulk_update(self, *args, **kwargs):
        raise ImmutableRecordError("append-only records cannot be updated")


class AppendOnlyModel(models.Model):
    """Application-level guard. On PostgreSQL a trigger enforces the same rule in the database."""

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableRecordError(f"{type(self).__name__} records are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(f"{type(self).__name__} records cannot be deleted")


class Organisation(TimeStamped):
    class Type(models.TextChoices):
        AGENT = "agent", "Agent / property management company"
        LANDLORD = "landlord", "Landlord"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=200)
    org_type = models.CharField(max_length=20, choices=Type.choices, default=Type.AGENT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    # Invoices are generated this many days before the due date.
    invoice_lead_days = models.PositiveSmallIntegerField(default=5)

    def __str__(self):
        return self.name


class Membership(TimeStamped):
    """A user's role inside one organisation. Property-level scope lives in portfolio.PropertyAccess."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner / admin"
        MANAGER = "manager", "Property manager"
        FINANCE = "finance", "Finance"
        CARETAKER = "caretaker", "Caretaker"
        LANDLORD_VIEWER = "landlord_viewer", "Landlord (read-only)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    org = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=30, choices=Role.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "org"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} @ {self.org} ({self.role})"


class AuditLog(AppendOnlyModel):
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
