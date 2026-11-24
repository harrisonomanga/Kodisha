from django.core.management.base import BaseCommand

from core.models import Organisation
from payments import services
from payments.models import BankProfile


class Command(BaseCommand):
    help = "Import a bank statement CSV using a saved column-mapping profile."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, required=True)
        parser.add_argument("--profile", required=True, help="BankProfile name")
        parser.add_argument("--label", required=True, help="Which bank account this statement belongs to")
        parser.add_argument("file")

    def handle(self, *args, **o):
        org = Organisation.objects.get(pk=o["org"])
        profile = BankProfile.objects.get(org=org, name=o["profile"])
        with open(o["file"], "rb") as fh:
            self.stdout.write(str(services.import_bank_statement(org, profile, fh, o["label"])))
