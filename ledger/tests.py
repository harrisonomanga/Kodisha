from django.test import TestCase

from core.factories import build_org
from core.models import ImmutableRecordError
from ledger.models import Account, Entry, Journal
from ledger.services import LedgerError, balance, get_account, post_journal, reverse_journal


class LedgerTests(TestCase):
    def setUp(self):
        self.t = build_org()
        self.org = self.t.org
        self.cash = get_account(self.org, Account.Kind.CASH)
        self.inc = get_account(self.org, Account.Kind.INCOME)

    def post(self, amount=1000, **kw):
        return post_journal(self.org, [(self.cash, "D", amount), (self.inc, "C", amount)], **kw)

    def test_balanced_journal_posts_and_balances_move(self):
        self.post(1000)
        self.assertEqual(balance(self.cash), 1000)
        self.assertEqual(balance(self.inc), 1000)

    def test_unbalanced_zero_and_cross_org_journals_are_rejected(self):
        with self.assertRaises(LedgerError):
            post_journal(self.org, [(self.cash, "D", 1000), (self.inc, "C", 900)])
        with self.assertRaises(LedgerError):
            post_journal(self.org, [(self.cash, "D", 0), (self.inc, "C", 0)])
        other = build_org(name="Other", prop_code="OTH", shortcode="999")
        foreign = get_account(other.org, Account.Kind.INCOME)
        with self.assertRaises(LedgerError):
            post_journal(self.org, [(self.cash, "D", 5), (foreign, "C", 5)])
        self.assertEqual(Journal.objects.count(), 0)

    def test_idempotency_key_posts_once(self):
        a = self.post(key="k1")
        b = self.post(key="k1")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(balance(self.cash), 1000)

    def test_records_are_append_only(self):
        j = self.post()
        entry = j.entries.first()
        entry.amount_minor = 1
        with self.assertRaises(ImmutableRecordError):
            entry.save()
        with self.assertRaises(ImmutableRecordError):
            entry.delete()
        with self.assertRaises(ImmutableRecordError):
            Entry.objects.update(amount_minor=1)
        with self.assertRaises(ImmutableRecordError):
            Journal.objects.all().delete()

    def test_reversal_nets_to_zero_and_is_idempotent(self):
        j = self.post(2500)
        r1 = reverse_journal(j)
        r2 = reverse_journal(j)
        self.assertEqual(r1.pk, r2.pk)
        self.assertEqual(balance(self.cash), 0)
        self.assertEqual(balance(self.inc), 0)
