from datetime import date

from django.test import TestCase

from billing.models import ChargeType, LeaseCharge
from billing.reports import arrears_ageing
from billing.services import generate_due_invoices, generate_invoice, outstanding, prorate
from core.factories import add_tenant_lease, build_org
from ledger.models import Account
from ledger.services import tenant_position
from portfolio.services import end_lease


class BillingTests(TestCase):
    def setUp(self):
        self.t = build_org()
        self.org = self.t.org
        self.unit = self.t.units[0]

    def test_prorate_full_month_is_exact_and_partial_rounds_half_up(self):
        self.assertEqual(prorate(1_500_000, 30, 30), 1_500_000)
        self.assertEqual(prorate(1_500_000, 21, 30), 1_050_000)
        self.assertEqual(prorate(100, 1, 3), 33)

    def test_mid_month_move_in_is_prorated(self):
        lease = add_tenant_lease(self.org, self.unit, start=date(2026, 11, 10))
        inv, created = generate_invoice(lease, date(2026, 11, 1))
        self.assertTrue(created)
        self.assertEqual(inv.total_minor, 1_050_000)  # 21 of 30 days
        self.assertIn("pro-rata", inv.lines.first().description)

    def test_move_out_mid_month_is_prorated(self):
        lease = add_tenant_lease(self.org, self.unit, start=date(2026, 9, 1))
        end_lease(lease, date(2026, 11, 15))
        inv, _ = generate_invoice(lease, date(2026, 11, 1))
        self.assertEqual(inv.total_minor, 750_000)  # 15 of 30 days
        self.assertEqual(generate_invoice(lease, date(2026, 12, 1)), (None, False))

    def test_invoice_generation_is_idempotent_and_posts_to_ledger_once(self):
        lease = add_tenant_lease(self.org, self.unit)
        first, c1 = generate_invoice(lease, date(2026, 11, 1))
        second, c2 = generate_invoice(lease, date(2026, 11, 15))
        self.assertEqual((c1, c2), (True, False))
        self.assertEqual(first.pk, second.pk)
        recv, credit = tenant_position(lease)
        self.assertEqual((recv, credit), (1_500_000, 0))

    def test_recurring_charges_are_added(self):
        lease = add_tenant_lease(self.org, self.unit)
        garbage = ChargeType.objects.create(org=self.org, code="GARB", name="Garbage")
        LeaseCharge.objects.create(lease=lease, charge_type=garbage, amount_minor=50_000)
        inv, _ = generate_invoice(lease, date(2026, 11, 1))
        self.assertEqual(inv.total_minor, 1_550_000)
        self.assertEqual(inv.lines.count(), 2)

    def test_due_invoices_respect_the_lead_time(self):
        lease = add_tenant_lease(self.org, self.unit, billing_day=10)  # due 10th, lead 5 days
        self.assertEqual(generate_due_invoices(self.org, date(2026, 12, 4)), {"created": 0, "not_yet_due": 1})
        self.assertEqual(generate_due_invoices(self.org, date(2026, 12, 5)), {"created": 1, "not_yet_due": 0})
        self.assertEqual(generate_due_invoices(self.org, date(2026, 12, 6))["created"], 0)

    def test_arrears_ageing_buckets(self):
        lease = add_tenant_lease(self.org, self.unit, start=date(2026, 9, 1))
        generate_invoice(lease, date(2026, 9, 1))   # due 1 Sep
        generate_invoice(lease, date(2026, 11, 1))  # due 1 Nov
        rows = arrears_ageing(self.org, as_of=date(2026, 11, 23))
        self.assertEqual(len(rows), 1)
        b = rows[0]["buckets"]
        self.assertEqual(b["61-90"], 1_500_000)  # Sep invoice, 83 days overdue
        self.assertEqual(b["1-30"], 1_500_000)   # Nov invoice, 22 days overdue
        self.assertEqual(rows[0]["total"], 3_000_000)
