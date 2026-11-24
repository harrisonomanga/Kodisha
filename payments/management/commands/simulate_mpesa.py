import uuid
from datetime import datetime

from django.core.management.base import BaseCommand

from payments import services
from portfolio.models import Unit


class Command(BaseCommand):
    help = "Simulate an M-Pesa C2B confirmation for a unit account number (goes through the real pipeline)."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, required=True)
        parser.add_argument("--account", required=True, help="Reference the tenant 'typed' (may be wrong on purpose)")
        parser.add_argument("--amount", required=True, help="KES, e.g. 15000.00")
        parser.add_argument("--txn", default=None, help="Reuse a transaction id to test duplicate handling")

    def handle(self, *args, **o):
        from core.models import Organisation
        org = Organisation.objects.get(pk=o["org"])
        payload = {
            "TransactionType": "Pay Bill", "TransID": o["txn"] or uuid.uuid4().hex[:10].upper(),
            "TransTime": datetime.now().strftime("%Y%m%d%H%M%S"), "TransAmount": o["amount"],
            "BusinessShortCode": "123456", "BillRefNumber": o["account"], "MSISDN": "2547****0000",
            "FirstName": "SIMULATED",
        }
        event = services.ingest_raw(org, "mpesa", payload, source_label="simulated")
        payment = services.process_raw_event(event.pk)
        self.stdout.write(f"raw event {event.pk} -> " + (f"payment {payment.pk} {payment.status}" if payment else "no new payment"))
