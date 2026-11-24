from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from payments import services
from payments.models import Payment
from portfolio.models import Unit


class Command(BaseCommand):
    help = "Review queue action: assign an unmatched payment to a unit (until the review UI exists)."

    def add_arguments(self, parser):
        parser.add_argument("payment_id", type=int)
        parser.add_argument("account_number")
        parser.add_argument("--user", required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **o):
        payment = Payment.objects.get(pk=o["payment_id"])
        unit = Unit.objects.get(org=payment.org, account_number=o["account_number"].upper())
        user = get_user_model().objects.get(username=o["user"])
        services.assign_payment(payment, unit, user, o["reason"])
        self.stdout.write(self.style.SUCCESS("assigned"))
