import io
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import Client, TestCase
from django.utils import timezone

from billing.models import Invoice
from billing.services import generate_invoice, outstanding
from core.factories import add_tenant_lease, build_org
from core.models import AuditLog
from ledger.models import Account, Entry
from ledger.services import balance, get_account, tenant_position
from payments import services
from payments.models import BankProfile, Payment, RawPaymentEvent, ReviewItem
from portfolio.models import Unit

NOV = date(2026, 11, 1)


def mpesa(txn, amount, ref, shortcode="123456", name="JOHN"):
    return {
        "TransactionType": "Pay Bill", "TransID": txn, "TransTime": "20261105101530",
        "TransAmount": amount, "BusinessShortCode": shortcode, "BillRefNumber": ref,
        "MSISDN": "2547****1234", "FirstName": name, "MiddleName": "", "LastName": "",
    }


class Base(TestCase):
    def setUp(self):
        self.t = build_org()
        self.org, self.cfg = self.t.org, self.t.cfg
        self.unit = self.t.units[0]
        self.lease = add_tenant_lease(self.org, self.unit)  # rent KES 15,000
        self.invoice, _ = generate_invoice(self.lease, NOV)
        self.client = Client()
        self.url = f"/webhooks/mpesa/{self.cfg.webhook_token}/confirmation/"
        self.user = get_user_model().objects.create_user("clerk", password="x")

    def pay(self, txn, amount, ref=None):
        resp = self.client.post(self.url, json.dumps(mpesa(txn, amount, self.unit.account_number if ref is None else ref)),
                                content_type="application/json")
        self.assertEqual(resp.json()["ResultCode"], 0)
        return Payment.objects.filter(channel_txn_ref=txn).first()

    def assertLedgerIsSound(self):
        totals = {d: Entry.objects.filter(direction=d).aggregate(s=Sum("amount_minor"))["s"] or 0 for d in "DC"}
        self.assertEqual(totals["D"], totals["C"], "debits must equal credits across the whole ledger")
        for lease in {self.lease}:
            recv, _ = tenant_position(lease)
            owing = sum(outstanding(i) for i in Invoice.objects.filter(lease=lease))
            self.assertEqual(recv, owing, "receivable must equal what open invoices say is owed")


class MpesaFlowTests(Base):
    def test_exact_reference_settles_invoice(self):
        p = self.pay("QK1", "15000.00")
        self.assertEqual(p.status, Payment.Status.MATCHED)
        self.assertEqual(outstanding(self.invoice), 0)
        self.assertEqual(balance(get_account(self.org, Account.Kind.UNALLOCATED)), 0)
        self.assertEqual(balance(get_account(self.org, Account.Kind.CASH)), 1_500_000)
        self.assertEqual(timezone.localtime(p.paid_at).hour, 10)  # Safaricom times are East Africa Time
        self.assertLedgerIsSound()

    def test_reference_variations_still_match(self):
        p = self.pay("QK2", "15000.00", ref=" " + self.unit.account_number.lower()[:3] + "-" + self.unit.account_number[3:] + " ")
        self.assertEqual(p.status, Payment.Status.MATCHED)

    def test_duplicate_callback_stores_both_events_but_one_payment(self):
        self.pay("QK3", "15000.00")
        self.pay("QK3", "15000.00")
        self.assertEqual(RawPaymentEvent.objects.filter(channel="mpesa").count(), 2)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(balance(get_account(self.org, Account.Kind.CASH)), 1_500_000)
        self.assertLedgerIsSound()

    def test_partial_payment_leaves_balance(self):
        self.pay("QK4", "5000.00")
        self.assertEqual(outstanding(self.invoice), 1_000_000)
        self.assertLedgerIsSound()

    def test_overpayment_becomes_credit_and_is_applied_to_next_invoice(self):
        self.pay("QK5", "20000.00")
        self.assertEqual(outstanding(self.invoice), 0)
        self.assertEqual(tenant_position(self.lease), (0, 500_000))
        dec, _ = generate_invoice(self.lease, date(2026, 12, 1))
        self.assertEqual(outstanding(dec), 1_000_000)
        self.assertEqual(tenant_position(self.lease)[1], 0)
        self.assertLedgerIsSound()

    def test_payment_is_allocated_oldest_invoice_first(self):
        oct_inv, _ = generate_invoice(self.lease, date(2026, 10, 1))
        self.pay("QK6", "15000.00")
        self.assertEqual(outstanding(oct_inv), 0)
        self.assertEqual(outstanding(self.invoice), 1_500_000)

    def test_wrong_reference_goes_to_review_with_a_did_you_mean(self):
        typo = self.unit.account_number[:-1] + ("0" if self.unit.account_number[-1] != "0" else "1")
        p = self.pay("QK7", "15000.00", ref=typo)
        self.assertEqual(p.status, Payment.Status.UNMATCHED)
        item = p.review_item
        self.assertEqual(item.reason, "no_match")
        self.assertIn(self.unit.account_number, [s["account_number"] for s in item.suggestions])
        self.assertEqual(balance(get_account(self.org, Account.Kind.UNALLOCATED)), 1_500_000)
        self.assertEqual(outstanding(self.invoice), 1_500_000)  # a near miss is never auto-applied
        self.assertLedgerIsSound()

    def test_unit_without_active_lease_goes_to_review(self):
        vacant = self.t.units[1]
        p = self.pay("QK8", "15000.00", ref=vacant.account_number)
        self.assertEqual(p.review_item.reason, "no_active_lease")

    def test_assign_from_review_queue_is_audited_and_requires_a_reason(self):
        p = self.pay("QK9", "15000.00", ref="WRONG1")
        with self.assertRaises(services.PaymentError):
            services.assign_payment(p, self.unit, self.user, "  ")
        services.assign_payment(p, self.unit, self.user, "Tenant confirmed by phone")
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.MATCHED)
        self.assertEqual(p.review_item.status, ReviewItem.Status.RESOLVED)
        self.assertEqual(outstanding(self.invoice), 0)
        self.assertEqual(balance(get_account(self.org, Account.Kind.UNALLOCATED)), 0)
        self.assertTrue(AuditLog.objects.filter(action="payment.assigned", actor=self.user).exists())
        with self.assertRaises(services.PaymentError):  # cannot assign twice
            services.assign_payment(p, self.unit, self.user, "again")
        self.assertLedgerIsSound()

    def test_reversal_restores_invoice_and_nets_ledger_to_zero(self):
        p = self.pay("QK10", "15000.00")
        services.reverse_payment(p, self.user, "bounced")
        p.refresh_from_db()
        self.assertEqual(p.status, Payment.Status.REVERSED)
        self.assertEqual(outstanding(self.invoice), 1_500_000)
        self.assertEqual(balance(get_account(self.org, Account.Kind.CASH)), 0)
        self.assertEqual(balance(get_account(self.org, Account.Kind.UNALLOCATED)), 0)
        services.reverse_payment(p, self.user, "again")  # second call changes nothing
        self.assertEqual(balance(get_account(self.org, Account.Kind.CASH)), 0)
        self.assertLedgerIsSound()

    def test_malformed_payload_is_parked_not_lost_and_not_retried_forever(self):
        resp = self.client.post(self.url, json.dumps({"TransID": "BAD", "TransAmount": "abc"}), content_type="application/json")
        self.assertEqual(resp.json()["ResultCode"], 0)
        event = RawPaymentEvent.objects.get()
        self.assertIsNone(event.processed_at)
        self.assertIn("bad amount", event.processing_error)
        self.assertEqual(Payment.objects.count(), 0)

    def test_replay_processes_an_event_that_was_stored_but_never_processed(self):
        event = services.ingest_raw(self.org, "mpesa", mpesa("QK11", "15000.00", self.unit.account_number))
        payment = services.process_raw_event(event.pk)
        self.assertEqual(payment.status, Payment.Status.MATCHED)
        self.assertIsNone(services.process_raw_event(event.pk))  # already processed
        self.assertEqual(Payment.objects.count(), 1)

    def test_unknown_or_disabled_webhook_token_is_404(self):
        self.assertEqual(self.client.post("/webhooks/mpesa/nope/confirmation/", "{}", content_type="application/json").status_code, 404)
        self.cfg.enabled = False
        self.cfg.save()
        self.assertEqual(self.client.post(self.url, "{}", content_type="application/json").status_code, 404)

    def test_raw_events_cannot_be_edited_or_deleted(self):
        self.pay("QK12", "100.00")
        event = RawPaymentEvent.objects.first()
        event.payload = {}
        with self.assertRaises(Exception):
            event.save()
        with self.assertRaises(Exception):
            event.delete()


class IsolationTests(TestCase):
    def test_same_account_number_in_two_organisations_never_crosses(self):
        a = build_org(name="A", prop_code="PLT", shortcode="111")
        b = build_org(name="B", prop_code="PLT", shortcode="222")
        self.assertEqual(a.units[0].account_number, b.units[0].account_number)
        add_tenant_lease(a.org, a.units[0])
        lease_b = add_tenant_lease(b.org, b.units[0], name="Other tenant")
        generate_invoice(lease_b, NOV)
        resp = Client().post(f"/webhooks/mpesa/{b.cfg.webhook_token}/confirmation/",
                             json.dumps(mpesa("ISO1", "15000.00", b.units[0].account_number, "222")),
                             content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        p = Payment.objects.get(channel_txn_ref="ISO1")
        self.assertEqual(p.org, b.org)
        self.assertEqual(p.unit, b.units[0])
        self.assertEqual(Payment.objects.filter(org=a.org).count(), 0)


class ManualAndBankTests(Base):
    def test_manual_payment_needs_unique_proof(self):
        p = services.record_manual_payment(self.org, self.unit, "15000", "cash", "SLIP-001", self.user)
        self.assertEqual(p.status, Payment.Status.MATCHED)
        self.assertEqual(outstanding(self.invoice), 0)
        with self.assertRaises(services.PaymentError):
            services.record_manual_payment(self.org, self.unit, "15000", "cash", "SLIP-001", self.user)
        with self.assertRaises(services.PaymentError):
            services.record_manual_payment(self.org, self.unit, "15000", "cash", " ", self.user)
        self.assertLedgerIsSound()

    def make_profile(self):
        return BankProfile.objects.create(
            org=self.org, name="Demo Bank", date_format="%d/%m/%Y",
            columns={"date": "Date", "narration": "Description", "reference": "Reference",
                     "credit": "Credit", "balance": "Balance"},
        )

    def statement(self):
        acct = self.unit.account_number
        return (
            "Date,Description,Reference,Debit,Credit,Balance\n"
            f"05/11/2026,PESALINK FROM JOHN,{acct},,5000.00,5000.00\n"
            f"06/11/2026,TRANSFER JOHN DOE {acct[:3]}-{acct[3:]} RENT,,,6000.00,11000.00\n"
            "07/11/2026,BANK CHARGES,,150.00,,10850.00\n"
            "08/11/2026,TRANSFER FROM SOMEONE,,,2000.00,12850.00\n"
        )

    def test_statement_import_matches_by_reference_then_narration_and_ignores_debits(self):
        summary = services.import_bank_statement(self.org, self.make_profile(), io.StringIO(self.statement()), "Demo Bank 001")
        self.assertEqual(summary, {"matched": 2, "ignored_non_credit": 1, "needs_review": 1})
        self.assertEqual(outstanding(self.invoice), 1_500_000 - 500_000 - 600_000)
        self.assertEqual(ReviewItem.objects.filter(status="open").count(), 1)
        self.assertLedgerIsSound()

    def test_reuploading_an_overlapping_statement_creates_no_duplicates(self):
        profile = self.make_profile()
        services.import_bank_statement(self.org, profile, io.StringIO(self.statement()), "Demo Bank 001")
        again = services.import_bank_statement(self.org, profile, io.StringIO(self.statement()), "Demo Bank 001")
        self.assertEqual(again.get("duplicates"), 3)
        self.assertEqual(Payment.objects.filter(channel="bank").count(), 3)
        self.assertLedgerIsSound()

    def test_two_identical_lines_in_one_file_are_two_payments(self):
        acct = self.unit.account_number
        csv_text = f"Date,Description,Reference,Debit,Credit,Balance\n05/11/2026,CASH DEP,{acct},,1000.00,\n05/11/2026,CASH DEP,{acct},,1000.00,\n"
        services.import_bank_statement(self.org, self.make_profile(), io.StringIO(csv_text), "Demo Bank 001")
        self.assertEqual(Payment.objects.filter(channel="bank").count(), 2)
