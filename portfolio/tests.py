from django.db import IntegrityError, transaction
from django.test import TestCase

from core.factories import add_tenant_lease, build_org
from portfolio.accounts import check_char, is_valid, make_account_number, normalise


class AccountNumberTests(TestCase):
    def test_generated_numbers_validate_and_typos_do_not(self):
        acct = make_account_number("plt", "a-12")
        self.assertTrue(acct.startswith("PLTA12"))
        self.assertTrue(is_valid(acct))
        self.assertFalse(is_valid(acct[:-1] + ("0" if acct[-1] != "0" else "1")))
        # swapping two different adjacent characters is caught too
        swapped = acct[:3] + acct[4] + acct[3] + acct[5:]
        self.assertFalse(is_valid(swapped))

    def test_normalise_handles_case_spaces_and_hyphens(self):
        self.assertEqual(normalise(" plt-a1 c "), "PLTA1C")

    def test_units_get_account_numbers_automatically(self):
        t = build_org()
        self.assertTrue(all(is_valid(u.account_number) for u in t.units))
        self.assertEqual(len({u.account_number for u in t.units}), 3)


class LeaseRuleTests(TestCase):
    def test_one_active_lease_per_unit(self):
        t = build_org()
        add_tenant_lease(t.org, t.units[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            add_tenant_lease(t.org, t.units[0], name="Second tenant")
