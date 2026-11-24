from datetime import date

from django.core.management.base import BaseCommand

from billing.services import generate_due_invoices
from core.models import Organisation


class Command(BaseCommand):
    help = "Generate this month's invoices for every live lease whose invoice date has arrived (safe to re-run)."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, help="Organisation id (default: all)")
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD")

    def handle(self, *args, **opts):
        today = date.fromisoformat(opts["date"]) if opts["date"] else None
        orgs = Organisation.objects.filter(pk=opts["org"]) if opts["org"] else Organisation.objects.all()
        for org in orgs:
            self.stdout.write(f"{org}: {generate_due_invoices(org, today)}")
