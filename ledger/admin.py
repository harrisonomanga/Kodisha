from django.contrib import admin

from .models import Account, Entry, Journal


class ReadOnly:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EntryInline(ReadOnly, admin.TabularInline):
    model = Entry
    extra = 0


@admin.register(Journal)
class JournalAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("id", "org", "posted_at", "memo", "source_type", "source_id")
    list_filter = ("org", "source_type")
    inlines = [EntryInline]


@admin.register(Account)
class AccountAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("id", "org", "kind", "lease")
    list_filter = ("org", "kind")
