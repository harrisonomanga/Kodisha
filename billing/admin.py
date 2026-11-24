from django.contrib import admin

from .models import Allocation, ChargeType, Invoice, InvoiceLine, LeaseCharge

admin.site.register(ChargeType)
admin.site.register(LeaseCharge)


class LineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "org", "lease", "period", "due_date", "total_minor", "status")
    list_filter = ("org", "status")
    inlines = [LineInline]


@admin.register(Allocation)
class AllocationAdmin(admin.ModelAdmin):
    list_display = ("id", "org", "invoice", "payment", "source", "amount_minor", "reversed")
    list_filter = ("org", "source", "reversed")
