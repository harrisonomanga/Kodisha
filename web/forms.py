from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from payments.models import BankProfile
from portfolio.models import Landlord, Property, Unit


class KES(forms.DecimalField):
    def __init__(self, **kw):
        kw.setdefault("min_value", Decimal("0.01"))
        kw.setdefault("max_digits", 12)
        kw.setdefault("decimal_places", 2)
        kw.setdefault("widget", forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "0.00"}))
        super().__init__(**kw)


class Styled(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.Select):
                w.attrs.setdefault("class", "select")
            elif not isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "input")


class UnitChoice(forms.ModelChoiceField):
    def label_from_instance(self, u):
        return f"{u.property.code}-{u.identifier}  ({u.account_number})"


def _unit_qs(qs):
    return qs.select_related("property") if qs is not None else Unit.objects.none()


class OrgForm(Styled):
    name = forms.CharField(max_length=200, label="Organisation name")
    org_type = forms.ChoiceField(label="You are a", choices=[("agent", "Property management company or agent"), ("landlord", "Landlord managing your own units")])


class PropertyForm(Styled):
    name = forms.CharField(max_length=200, label="Property name")
    code = forms.RegexField(r"^[A-Za-z0-9]{2,6}$", label="Short code", max_length=6,
                            help_text="2 to 6 letters or digits. It starts every unit's payment account number, for example PLT.")
    location = forms.CharField(max_length=200, required=False)
    ptype = forms.ChoiceField(label="Type", choices=Property.Type.choices, initial="residential")
    landlord = forms.ModelChoiceField(queryset=Landlord.objects.none(), required=False, label="Landlord")
    new_landlord = forms.CharField(max_length=200, required=False, label="Or add a new landlord")

    def __init__(self, *a, org=None, **kw):
        super().__init__(*a, **kw)
        self.org = org
        self.fields["landlord"].queryset = Landlord.objects.filter(org=org)

    def clean(self):
        data = super().clean()
        if not data.get("landlord") and not (data.get("new_landlord") or "").strip():
            raise forms.ValidationError("Choose a landlord or enter the name of a new one.")
        return data

    def clean_code(self):
        code = self.cleaned_data["code"].upper()
        if Property.objects.filter(org=self.org, code=code).exists():
            raise forms.ValidationError("Another property already uses this code.")
        return code


class UnitForm(Styled):
    identifier = forms.CharField(max_length=12, label="Unit number", help_text="For example A12 or 3B.")
    unit_type = forms.CharField(max_length=40, required=False, label="Type", help_text="For example bedsitter, 2 bedroom, shop.")
    rent = KES(label="Monthly rent (KES)")
    deposit = KES(label="Deposit (KES)", min_value=Decimal("0"), required=False)

    def __init__(self, *a, prop=None, **kw):
        super().__init__(*a, **kw)
        self.prop = prop

    def clean_identifier(self):
        ident = self.cleaned_data["identifier"].strip()
        if Unit.objects.filter(property=self.prop, identifier__iexact=ident).exists():
            raise forms.ValidationError("This property already has a unit with that number.")
        return ident


class MoveInForm(Styled):
    unit = UnitChoice(queryset=Unit.objects.none(), label="Unit")
    full_name = forms.CharField(max_length=200, label="Tenant's full name")
    phone = forms.CharField(max_length=32, label="Phone number", help_text="Used for reminders and receipts.")
    id_number = forms.CharField(max_length=40, required=False, label="ID or passport number")
    next_of_kin_name = forms.CharField(max_length=200, required=False, label="Next of kin")
    next_of_kin_phone = forms.CharField(max_length=32, required=False, label="Next of kin phone")
    start_date = forms.DateField(label="Move-in date", widget=forms.DateInput(attrs={"type": "date"}), initial=date.today)
    rent = KES(label="Monthly rent (KES)")
    deposit = KES(label="Deposit (KES)", min_value=Decimal("0"), required=False)
    billing_day = forms.IntegerField(min_value=1, max_value=28, initial=1, label="Rent due on day", help_text="Day of the month, 1 to 28.")

    def __init__(self, *a, unit_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["unit"].queryset = _unit_qs(unit_qs)


class ManualPaymentForm(Styled):
    unit = UnitChoice(queryset=Unit.objects.none(), label="Unit")
    amount = KES(label="Amount received (KES)")
    method = forms.ChoiceField(choices=[("cash", "Cash"), ("cheque", "Cheque"), ("bank_slip", "Bank deposit slip"), ("other", "Other")])
    proof = forms.CharField(max_length=100, label="Receipt or slip number", help_text="Required. Each number can only be recorded once.")
    paid_on = forms.DateField(required=False, label="Date received", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *a, unit_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["unit"].queryset = _unit_qs(unit_qs)


class AssignForm(Styled):
    unit = UnitChoice(queryset=Unit.objects.none(), label="Unit")
    reason = forms.CharField(max_length=255, label="Why this unit?")

    def __init__(self, *a, unit_qs=None, **kw):
        super().__init__(*a, **kw)
        self.fields["unit"].queryset = _unit_qs(unit_qs)


class ReasonForm(Styled):
    reason = forms.CharField(max_length=255)


class EndLeaseForm(Styled):
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Move-out date")


class ChargeForm(Styled):
    name = forms.CharField(max_length=100, label="Charge name", help_text="For example Garbage, Security, Water.")
    amount = KES(label="Amount every month (KES)")


class SimulateForm(Styled):
    account = forms.CharField(max_length=40, label="Account number the tenant typed")
    amount = KES(label="Amount (KES)")
    txn = forms.CharField(max_length=30, required=False, label="Transaction code", help_text="Leave empty for a new one. Reuse a code to test duplicate handling.")


class ImportForm(Styled):
    profile = forms.ModelChoiceField(queryset=BankProfile.objects.none(), label="Bank format")
    label = forms.CharField(max_length=100, label="Which bank account is this?", help_text="For example Equity main account.")
    file = forms.FileField(label="Statement (CSV)")

    def __init__(self, *a, org=None, **kw):
        super().__init__(*a, **kw)
        self.fields["profile"].queryset = BankProfile.objects.filter(org=org)


class BankProfileForm(Styled):
    name = forms.CharField(max_length=100, label="Bank name")
    date_format = forms.CharField(max_length=30, initial="%d/%m/%Y", label="Date format", help_text="%d/%m/%Y reads 24/11/2026. %Y-%m-%d reads 2026-11-24.")
    date = forms.CharField(max_length=80, label="Date column")
    credit = forms.CharField(max_length=80, label="Money-in column", help_text="The column that holds amounts received.")
    reference = forms.CharField(max_length=80, required=False, label="Reference column")
    narration = forms.CharField(max_length=80, required=False, label="Description column")
    txn_id = forms.CharField(max_length=80, required=False, label="Transaction ID column")
    balance = forms.CharField(max_length=80, required=False, label="Balance column")
    payer_name = forms.CharField(max_length=80, required=False, label="Payer name column")


class OrgSettingsForm(Styled):
    invoice_lead_days = forms.IntegerField(min_value=0, max_value=28, label="Create invoices this many days before they are due")


class MpesaForm(Styled):
    shortcode = forms.CharField(max_length=40, label="Paybill or Till number")


class LoginForm(AuthenticationForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["username"].widget.attrs.update({"class": "input", "autofocus": True, "autocomplete": "username"})
        self.fields["password"].widget.attrs.update({"class": "input", "autocomplete": "current-password"})
