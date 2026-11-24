import io
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.services import generate_invoice
from core.factories import add_tenant_lease, build_org
from core.models import Membership
from payments import services
from payments.models import BankProfile, Payment
from portfolio.models import Lease, PropertyAccess, Property, Landlord, Unit

User = get_user_model()
PW = "pw-123456"


def mpesa(org, account, amount="15000.00", txn="TXN0000001"):
    payload = {"TransactionType": "Pay Bill", "TransID": txn, "TransTime": timezone.localtime().strftime("%Y%m%d%H%M%S"),
               "TransAmount": amount, "BusinessShortCode": "123456", "BillRefNumber": account, "MSISDN": "2547****0000", "FirstName": "T"}
    return services.process_raw_event(services.ingest_raw(org, "mpesa", payload).pk)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.t = build_org(units=("A1", "A2", "B1"), rent_minor=1_500_000)
        cls.org = cls.t.org
        today = timezone.localdate()
        cls.start = today.replace(day=1)
        cls.l1 = add_tenant_lease(cls.org, cls.t.units[0], name="Jane Wanjiru", start=cls.start, billing_day=1)
        cls.l2 = add_tenant_lease(cls.org, cls.t.units[1], name="Peter Otieno", start=cls.start, billing_day=1)
        generate_invoice(cls.l1, today, issue_date=today)
        cls.owner = User.objects.create_user("owner", password=PW)
        Membership.objects.create(user=cls.owner, org=cls.org, role="owner")
        cls.viewer = User.objects.create_user("view", password=PW)
        Membership.objects.create(user=cls.viewer, org=cls.org, role="landlord_viewer")

    def login(self, user="owner"):
        self.client.login(username=user, password=PW)


class AuthTests(Base):
    def test_anonymous_redirected(self):
        r = self.client.get("/")
        self.assertRedirects(r, "/login/?next=/")

    def test_login_page_and_login(self):
        self.assertEqual(self.client.get("/login/").status_code, 200)
        r = self.client.post("/login/", {"username": "owner", "password": PW})
        self.assertRedirects(r, "/")

    def test_user_without_org_gets_no_access(self):
        User.objects.create_user("nobody", password=PW)
        self.client.login(username="nobody", password=PW)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_viewer_cannot_write_but_can_read(self):
        self.login("view")
        self.assertEqual(self.client.get("/payments/").status_code, 200)
        self.assertEqual(self.client.get("/payments/new/").status_code, 403)
        self.assertEqual(self.client.post(f"/tenancies/{self.l1.pk}/end/", {"end_date": "2026-12-01"}).status_code, 403)

    def test_settings_owner_only(self):
        m = User.objects.create_user("mgr", password=PW)
        Membership.objects.create(user=m, org=self.org, role="manager")
        self.client.login(username="mgr", password=PW)
        self.assertEqual(self.client.get("/settings/").status_code, 403)
        self.login()
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    def test_cross_org_isolation(self):
        other = build_org(name="Other", prop_code="OTH", units=("Z1",))
        lease = add_tenant_lease(other.org, other.units[0], name="Stranger", start=self.start)
        self.login()
        self.assertEqual(self.client.get(f"/tenancies/{lease.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/tenants/"), "Stranger")

    def test_property_scope(self):
        p2 = Property.objects.create(org=self.org, landlord=Landlord.objects.create(org=self.org, name="L2"), name="Hidden Tower", code="HID")
        u = Unit.objects.create(org=self.org, property=p2, identifier="1", rent_minor=100000)
        hidden = add_tenant_lease(self.org, u, name="Hidden Tenant", start=self.start)
        scoped = User.objects.create_user("scoped", password=PW)
        m = Membership.objects.create(user=scoped, org=self.org, role="manager")
        PropertyAccess.objects.create(membership=m, property=self.t.prop)
        self.client.login(username="scoped", password=PW)
        self.assertEqual(self.client.get(f"/tenancies/{hidden.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/properties/"), "Hidden Tower")
        self.assertEqual(self.client.get("/review/").status_code, 403)
        self.assertEqual(self.client.get("/payments/import/").status_code, 403)


class PageTests(Base):
    def test_every_page_renders(self):
        self.login()
        mpesa(self.org, "NOPE1")
        pay = Payment.objects.first()
        inv = self.l1.invoices.first()
        for url in ["/", "/search/?q=jane", "/properties/", "/properties/new/", f"/properties/{self.t.prop.pk}/",
                    f"/properties/{self.t.prop.pk}/units/new/", "/tenants/", "/tenants/move-in/", f"/tenancies/{self.l1.pk}/",
                    "/invoices/", f"/invoices/{inv.pk}/", "/payments/", "/payments/new/", "/payments/import/", f"/payments/{pay.pk}/",
                    "/review/", "/reports/arrears/", "/reports/arrears.csv", "/settings/", "/payments/import/sample.csv"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_empty_org_dashboard_guides_user(self):
        e = User.objects.create_user("empty", password=PW)
        from core.models import Organisation
        o = Organisation.objects.create(name="Empty Co", org_type="agent")
        Membership.objects.create(user=e, org=o, role="owner")
        self.client.login(username="empty", password=PW)
        self.assertContains(self.client.get("/"), "Add property")

    def test_superuser_without_org_goes_to_onboarding(self):
        User.objects.create_superuser("root", "r@x.co", PW)
        self.client.login(username="root", password=PW)
        self.assertEqual(self.client.get("/").status_code, 200)  # superuser sees all orgs


class WorkflowTests(Base):
    def test_add_property_and_unit(self):
        self.login()
        r = self.client.post("/properties/new/", {"name": "New Place", "code": "NEW", "ptype": "residential", "new_landlord": "Mr X"})
        prop = Property.objects.get(code="NEW")
        self.assertRedirects(r, f"/properties/{prop.pk}/")
        r = self.client.post(f"/properties/{prop.pk}/units/new/", {"identifier": "1A", "rent": "12000"})
        self.assertRedirects(r, f"/properties/{prop.pk}/")
        self.assertEqual(Unit.objects.get(property=prop).rent_minor, 1_200_000)

    def test_move_in_creates_lease_and_invoice(self):
        self.login()
        r = self.client.post("/tenants/move-in/", {"unit": self.t.units[2].pk, "full_name": "New Tenant", "phone": "0712345678",
                                                    "start_date": timezone.localdate().isoformat(), "rent": "15000", "billing_day": 1})
        lease = Lease.objects.get(tenant__full_name="New Tenant")
        self.assertRedirects(r, f"/tenancies/{lease.pk}/")
        self.assertTrue(lease.invoices.exists())

    def test_move_in_rejects_occupied_unit(self):
        self.login()
        r = self.client.post("/tenants/move-in/", {"unit": self.t.units[0].pk, "full_name": "X", "phone": "1",
                                                    "start_date": "2026-01-01", "rent": "1000", "billing_day": 1})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Lease.objects.filter(tenant__full_name="X").exists())

    def test_manual_payment_allocates_and_duplicate_proof_rejected(self):
        self.login()
        data = {"unit": self.t.units[0].pk, "amount": "15000", "method": "cash", "proof": "RCPT-9"}
        r = self.client.post("/payments/new/", data)
        pay = Payment.objects.get(channel="manual")
        self.assertRedirects(r, f"/payments/{pay.pk}/")
        self.assertEqual(self.client.post("/payments/new/", data).status_code, 200)
        self.assertEqual(Payment.objects.filter(channel="manual").count(), 1)

    def test_reverse_requires_reason_and_works(self):
        self.login()
        pay = mpesa(self.org, self.t.units[0].account_number)
        self.client.post(f"/payments/{pay.pk}/reverse/", {"reason": ""})
        pay.refresh_from_db(); self.assertEqual(pay.status, "matched")
        self.client.post(f"/payments/{pay.pk}/reverse/", {"reason": "Wrong tenant"})
        pay.refresh_from_db(); self.assertEqual(pay.status, "reversed")

    def test_review_assign_flow(self):
        self.login()
        pay = mpesa(self.org, "RENT")
        self.assertEqual(pay.status, "unmatched")
        self.assertContains(self.client.get("/review/"), "RENT")
        self.client.post(f"/review/{pay.pk}/assign/", {"unit": self.t.units[0].pk, "reason": "Tenant called"})
        pay.refresh_from_db(); self.assertEqual(pay.status, "matched")
        self.assertEqual(pay.unit, self.t.units[0])

    def test_assign_to_other_org_unit_rejected(self):
        self.login()
        other = build_org(name="O2", prop_code="OTW", units=("Q1",))
        add_tenant_lease(other.org, other.units[0], start=self.start)
        pay = mpesa(self.org, "RENT")
        self.client.post(f"/review/{pay.pk}/assign/", {"unit": other.units[0].pk, "reason": "x"})
        pay.refresh_from_db(); self.assertEqual(pay.status, "unmatched")

    def test_end_lease_and_add_charge(self):
        self.login()
        self.client.post(f"/tenancies/{self.l2.pk}/charges/", {"name": "Security", "amount": "500"})
        self.assertEqual(self.l2.charges.count(), 1)
        self.client.post(f"/tenancies/{self.l2.pk}/end/", {"end_date": timezone.localdate().isoformat()})
        self.l2.refresh_from_db(); self.assertEqual(self.l2.status, "ended")

    def test_generate_invoices_idempotent(self):
        self.login()
        self.client.post("/invoices/generate/")
        n = self.l2.invoices.count()
        self.client.post("/invoices/generate/")
        self.assertEqual(self.l2.invoices.count(), n)

    def test_bank_import_and_dedupe(self):
        self.login()
        BankProfile.objects.create(org=self.org, name="Eq", date_format="%d/%m/%Y",
                                   columns={"date": "Date", "credit": "Credit", "reference": "Reference", "narration": "Description"})
        csv = f"Date,Description,Reference,Debit,Credit\n{date.today():%d/%m/%Y},PESALINK,{self.t.units[1].account_number},,15000.00\n"
        prof = BankProfile.objects.get(org=self.org).pk
        for _ in range(2):
            f = io.BytesIO(csv.encode()); f.name = "s.csv"
            self.client.post("/payments/import/", {"profile": prof, "label": "Main", "file": f})
        self.assertEqual(Payment.objects.filter(channel="bank").count(), 1)

    def test_arrears_csv(self):
        self.login()
        r = self.client.get("/reports/arrears.csv")
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("Tenant", r.content.decode())

    @override_settings(DEBUG=True)
    def test_simulate_payment_and_duplicate(self):
        self.login()
        acct = self.t.units[0].account_number
        self.client.post("/payments/simulate/", {"account": acct, "amount": "15000", "txn": "SIM1"})
        self.client.post("/payments/simulate/", {"account": acct, "amount": "15000", "txn": "SIM1"})
        self.assertEqual(Payment.objects.filter(channel_txn_ref="SIM1").count(), 1)

    @override_settings(DEBUG=False)
    def test_simulate_disabled_outside_debug(self):
        self.login()
        self.assertEqual(self.client.post("/payments/simulate/", {"account": "x", "amount": "1"}).status_code, 404)

    def test_open_redirect_blocked(self):
        self.login()
        r = self.client.post("/invoices/generate/", {"next": "//evil.com"})
        self.assertEqual(r["Location"], reverse("invoices_list"))
