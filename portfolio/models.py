from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

from core.models import Membership, Organisation, TimeStamped

from .accounts import make_account_number


class Landlord(TimeStamped):
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="landlords")
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    commission_bps = models.PositiveIntegerField(default=0, help_text="Basis points: 1000 = 10%")

    def __str__(self):
        return self.name


class Property(TimeStamped):
    class Type(models.TextChoices):
        RESIDENTIAL = "residential", "Residential"
        COMMERCIAL = "commercial", "Commercial"
        MIXED = "mixed", "Mixed"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="properties")
    landlord = models.ForeignKey(Landlord, on_delete=models.PROTECT, related_name="properties")
    name = models.CharField(max_length=200)
    code = models.CharField(
        max_length=6,
        validators=[RegexValidator(r"^[A-Za-z0-9]{2,6}$", "2 to 6 letters or digits")],
        help_text="Short code used inside unit account numbers",
    )
    location = models.CharField(max_length=200, blank=True)
    ptype = models.CharField(max_length=20, choices=Type.choices, default=Type.RESIDENTIAL)

    class Meta:
        verbose_name_plural = "properties"
        constraints = [models.UniqueConstraint(fields=["org", "code"], name="uniq_property_code_per_org")]

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PropertyAccess(TimeStamped):
    """Restricts a membership to specific properties. A membership with no rows sees all properties."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="property_access")
    property = models.ForeignKey(Property, on_delete=models.CASCADE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["membership", "property"], name="uniq_property_access")]


class Unit(TimeStamped):
    class Status(models.TextChoices):
        OCCUPIED = "occupied", "Occupied"
        VACANT = "vacant", "Vacant"
        MAINTENANCE = "maintenance", "Under maintenance"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="units")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    identifier = models.CharField(max_length=12, help_text="For example A12")
    unit_type = models.CharField(max_length=40, blank=True)
    size = models.CharField(max_length=40, blank=True)
    rent_minor = models.BigIntegerField(default=0, help_text="Rent in cents (KES x 100)")
    deposit_minor = models.BigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VACANT)
    is_active = models.BooleanField(default=True)
    account_number = models.CharField(max_length=24, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["property", "identifier"], name="uniq_unit_identifier"),
            models.UniqueConstraint(fields=["org", "account_number"], name="uniq_unit_account_per_org"),
        ]

    def save(self, *args, **kwargs):
        if not self.org_id:
            self.org_id = self.property.org_id
        if not self.account_number:
            self.account_number = make_account_number(self.property.code, self.identifier)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.property.code}-{self.identifier}"


class Tenant(TimeStamped):
    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="tenants")
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32, blank=True)
    # TODO(security): field-level encryption for ID / passport numbers. See README open questions.
    id_number = models.CharField(max_length=40, blank=True)
    next_of_kin_name = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.full_name


class Lease(TimeStamped):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    org = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="leases")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="leases")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="leases")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Empty means month-to-month")
    rent_minor = models.BigIntegerField()
    billing_day = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(28)])
    deposit_minor = models.BigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["unit"], condition=Q(status="active"), name="one_active_lease_per_unit"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.org_id:
            self.org_id = self.unit.org_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tenant} in {self.unit}"
