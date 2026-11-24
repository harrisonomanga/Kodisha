from django.contrib import admin

from .models import Landlord, Lease, Property, PropertyAccess, Tenant, Unit

admin.site.register(Landlord)
admin.site.register(PropertyAccess)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "org", "landlord", "ptype")
    list_filter = ("org",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("__str__", "org", "account_number", "rent_minor", "status", "is_active")
    list_filter = ("org", "status")
    search_fields = ("account_number", "identifier")
    readonly_fields = ("account_number",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "org")
    search_fields = ("full_name", "phone")


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("__str__", "org", "start_date", "end_date", "rent_minor", "status")
    list_filter = ("org", "status")
