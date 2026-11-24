import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from billing.models import ChargeType, LeaseCharge
from billing.services import generate_invoice
from core.factories import add_tenant_lease, build_org
from core.models import Membership, Organisation
from payments import services
from payments.models import BankProfile
from portfolio.models import Landlord, Property, Unit

PLAZA = [("A1", "Jane Wanjiru"), ("A2", "Peter Otieno"), ("A3", "Amina Hassan"), ("A4", "Brian Kiptoo"),
         ("B1", "Grace Mutheu"), ("B2", "Samuel Njoroge"), ("B3", None)]
MWANGAZA = [("1", "Lucy Achieng"), ("2", "David Mwangi"), ("3", "Fatuma Ali"), ("4", None), ("5", "Joseph Kamau")]


class Command(BaseCommand):
    help = "Create a realistic demo organisation: 2 properties, 12 units, tenants, invoices, payments and review items."

    @transaction.atomic
    def handle(self, *args, **opts):
        if Organisation.objects.filter(name="Demo Agents").exists():
            self.stdout.write("Demo data already exists.")
            return
        today = timezone.localdate()
        first = today.replace(day=1)
        start = (first - timedelta(days=62)).replace(day=1)
        t = build_org(name="Demo Agents", prop_code="PLT", units=[u for u, _ in PLAZA], rent_minor=1_800_000)
        org = t.org
        t.prop.name, t.prop.location = "Plaza Heights", "Kilimani, Nairobi"
        t.prop.save()
        t.prop.landlord.name = "Mrs. Wambui Karanja"
        t.prop.landlord.save()
        rents = [1_500_000, 1_800_000, 1_800_000, 2_200_000, 2_500_000, 2_500_000, 2_500_000]
        for u, r in zip(t.units, rents):
            u.rent_minor = r
            u.unit_type = "1 bedroom" if r < 2_000_000 else "2 bedroom"
            u.save()
        ll2 = Landlord.objects.create(org=org, name="Kamau & Sons Ltd")
        p2 = Property.objects.create(org=org, landlord=ll2, name="Mwangaza Court", code="MWC", location="Thika Road")
        units2 = [Unit.objects.create(org=org, property=p2, identifier=i, rent_minor=1_200_000, unit_type="Bedsitter") for i, _ in MWANGAZA]

        leases = []
        for unit, (_, name) in zip(t.units + units2, PLAZA + MWANGAZA):
            if name:
                leases.append(add_tenant_lease(org, unit, name=name, start=start, billing_day=28 if len(leases) in (5, 6) else (1 if len(leases) % 3 else 5)))
        ct = ChargeType.objects.create(org=org, code="GARBAGE", name="Garbage")
        for lease in leases[:6]:
            LeaseCharge.objects.create(lease=lease, charge_type=ct, amount_minor=50_000)

        for lease in leases:
            for m in range(4):
                d = (start + timedelta(days=32 * m)).replace(day=lease.billing_day)
                if d <= today + timedelta(days=lease.billing_day):
                    generate_invoice(lease, d, issue_date=d - timedelta(days=5))

        def pay(lease, amount, days_ago, account=None, txn=None):
            payload = {"TransactionType": "Pay Bill", "TransID": txn or uuid.uuid4().hex[:10].upper(),
                       "TransTime": (timezone.now() - timedelta(days=days_ago)).strftime("%Y%m%d%H%M%S"),
                       "TransAmount": f"{amount:.2f}", "BusinessShortCode": "123456",
                       "BillRefNumber": account or lease.unit.account_number, "MSISDN": "2547****0000", "FirstName": lease.tenant.full_name.split()[0].upper()}
            services.process_raw_event(services.ingest_raw(org, "mpesa", payload, source_label="demo").pk)

        for i, lease in enumerate(leases):
            monthly = lease.rent_minor / 100 + (500 if i < 6 else 0)
            this_month = max(0, today.day - 2)
            if i % 5 == 4:          # behind: stopped paying after two months
                pay(lease, monthly, 85)
                pay(lease, monthly, 55)
            elif i % 5 == 3:        # part payer this month
                for d in (85, 55, 25):
                    pay(lease, monthly, d)
                pay(lease, monthly / 2, this_month)
            elif lease.billing_day == 28:   # not due yet this month
                for d in (85, 55, 25):
                    pay(lease, monthly, d)
            else:                   # up to date
                for d in (85, 55, 25, this_month):
                    pay(lease, monthly, d)
        # Typed wrongly, unknown reference, and a one-character slip: all land in the review queue
        pay(leases[0], 15000, 1, account="PLTA1X")
        pay(leases[1], 18000, 1, account="RENT")
        pay(leases[2], 18500, 0, account=leases[2].unit.account_number[:-1] + ("Z" if leases[2].unit.account_number[-1] != "Z" else "Y"))
        services.record_manual_payment(org, leases[3].unit, "5000", "cash", "RCPT-0001", None)

        BankProfile.objects.create(org=org, name="Equity Bank", date_format="%d/%m/%Y",
                                   columns={"date": "Date", "credit": "Credit", "reference": "Reference", "narration": "Description", "balance": "Balance"})

        self.stdout.write(self.style.SUCCESS(f"Created organisation id={org.pk} with {len(leases)} tenants across 2 properties."))
        self.stdout.write(f"M-Pesa confirmation URL path: /webhooks/mpesa/{t.cfg.webhook_token}/confirmation/")
