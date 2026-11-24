import csv
import json
import re
import uuid
from collections import OrderedDict
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, F, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from billing.models import Allocation, ChargeType, Invoice, LeaseCharge
from billing.reports import BUCKETS, arrears_ageing
from billing.services import generate_due_invoices, generate_invoice
from core.models import Membership, Organisation
from ledger.models import Account, Entry, Journal
from payments import services
from payments.adapters import to_minor
from payments.models import BankProfile, Payment, PaymentChannelConfig, ReviewItem
from portfolio.models import Landlord, Lease, Property, Tenant, Unit
from portfolio.services import create_lease, end_lease

from . import queries
from .access import org_view
from .forms import (AssignForm, BankProfileForm, ChargeForm, EndLeaseForm, ImportForm, ManualPaymentForm, MoveInForm,
                    MpesaForm, OrgForm, OrgSettingsForm, PropertyForm, ReasonForm, SimulateForm, UnitForm)


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def page(request, qs, per=25):
    return Paginator(qs, per).get_page(request.GET.get("page"))


def first_error(form):
    for field, errs in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        return f"{label}: {errs[0]}" if label else str(errs[0])
    return "Please check the form."


def unscoped_only(request):
    """Organisation-wide actions are closed to people limited to some properties."""
    if request.property_ids is not None:
        return render(request, "web/forbidden.html", status=403)
    return None


def month_bounds(period):
    start = timezone.make_aware(datetime.combine(period, time.min))
    nxt = (period.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, timezone.make_aware(datetime.combine(nxt, time.min))


def arrears_rows(request, today=None):
    rows = arrears_ageing(request.org, today)
    if request.property_ids is not None:
        allowed = set(queries.leases(request).values_list("id", flat=True))
        rows = [r for r in rows if r["lease_id"] in allowed]
    return rows


def unit_options(request):
    leases = list(queries.leases(request).filter(status="active").select_related("tenant", "unit__property"))
    bal = queries.lease_balances(request.org, [l.id for l in leases])
    out = [{"unit": l.unit, "tenant": l.tenant.full_name, "lease": l, "owing": bal.get(l.id, {}).get("owing", 0),
            "credit": bal.get(l.id, {}).get("credit", 0)} for l in leases]
    out.sort(key=lambda o: (o["unit"].property.name, natural(o["unit"].identifier)))
    return out


def active_unit_qs(request):
    return queries.units(request).filter(is_active=True, leases__status="active").distinct()


# ------------------------------------------------------------ onboarding and switching

def onboarding(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return redirect("login")
    form = OrgForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        org = Organisation.objects.create(name=form.cleaned_data["name"], org_type=form.cleaned_data["org_type"])
        Membership.objects.get_or_create(user=request.user, org=org, defaults={"role": "owner"})
        request.session["org_id"] = org.pk
        messages.success(request, f"{org.name} is ready. Add your first property to get started.")
        return redirect("properties_new")
    return render(request, "web/onboarding.html", {"form": form})


@require_POST
def switch_org(request):
    if request.user.is_authenticated:
        try:
            org_id = int(request.POST.get("org_id", ""))
        except ValueError:
            org_id = None
        if org_id in [o.pk for o in request.orgs]:
            request.session["org_id"] = org_id
    return redirect("dashboard")


# ------------------------------------------------------------ rent board

@org_view
def dashboard(request):
    org, today = request.org, timezone.localdate()
    period = today.replace(day=1)
    active = list(queries.leases(request).filter(status="active").select_related("tenant", "unit__property"))
    by_unit = {l.unit_id: l for l in active}
    month_invoices = {i.lease_id: i for i in queries.with_paid(queries.invoices(request).filter(period=period, status="issued"))}
    rows = arrears_rows(request, today)
    overdue = {r["lease_id"]: r["total"] - r["buckets"]["current"] for r in rows}
    balances = queries.lease_balances(org, [l.id for l in active])

    boards = OrderedDict()
    for p in queries.properties(request).order_by("name"):
        boards[p.pk] = {"property": p, "tiles": [], "occupied": 0, "paid": 0}
    all_units = sorted(queries.units(request).filter(is_active=True).select_related("property"), key=lambda u: natural(u.identifier))
    counts = {"paid": 0, "partial": 0, "overdue": 0, "awaiting": 0, "unbilled": 0, "vacant": 0}
    for u in all_units:
        board = boards.get(u.property_id)
        if board is None:
            continue
        lease = by_unit.get(u.pk)
        if lease is None:
            state, owing = "vacant", 0
        else:
            inv = month_invoices.get(lease.pk)
            if overdue.get(lease.pk, 0) > 0:
                state = "overdue"
            elif inv is None:
                state = "unbilled"
            elif inv.total_minor - inv.paid <= 0:
                state = "paid"
            elif inv.paid > 0:
                state = "partial"
            else:
                state = "awaiting"
            owing = balances.get(lease.pk, {}).get("owing", 0)
            board["occupied"] += 1
            board["paid"] += state == "paid"
        counts[state] += 1
        board["tiles"].append({"unit": u, "lease": lease, "state": state, "owing": owing})

    billed = sum(i.total_minor for i in month_invoices.values())
    paid_on_billed = sum(min(i.paid, i.total_minor) for i in month_invoices.values())
    pct = round(100 * paid_on_billed / billed) if billed else 0

    periods, p = [], period
    for _ in range(6):
        periods.append(p)
        p = (p - timedelta(days=1)).replace(day=1)
    periods.reverse()
    inv_scope = queries.invoices(request).filter(status="issued", period__in=periods)
    billed_by = {r["period"]: r["t"] for r in inv_scope.values("period").annotate(t=Sum("total_minor"))}
    paid_by = {r["invoice__period"]: r["t"] for r in Allocation.objects.filter(org=org, reversed=False, invoice__in=inv_scope)
               .values("invoice__period").annotate(t=Sum("amount_minor"))}
    top = max([billed_by.get(x, 0) for x in periods] + [1])
    history = [{"period": x, "billed": billed_by.get(x, 0), "paid": paid_by.get(x, 0),
                "billed_h": round(100 * billed_by.get(x, 0) / top), "paid_h": round(100 * paid_by.get(x, 0) / top)} for x in periods]

    top_arrears = sorted([r for r in rows if r["total"] - r["buckets"]["current"] > 0],
                         key=lambda r: -(r["total"] - r["buckets"]["current"]))[:6]
    for r in top_arrears:
        r["overdue"] = r["total"] - r["buckets"]["current"]

    return render(request, "web/dashboard.html", {
        "today": today, "period": period, "boards": [b for b in boards.values() if b["tiles"]],
        "billed": billed, "paid_on_billed": paid_on_billed, "pct": pct,
        "overdue_total": sum(overdue.values()), "overdue_count": sum(1 for v in overdue.values() if v > 0),
        "counts": counts, "history": history, "top_arrears": top_arrears,
        "recent": queries.payments(request).select_related("unit__property", "lease__tenant").order_by("-paid_at")[:6],
        "has_properties": bool(boards),
    })


# ------------------------------------------------------------ properties and units

@org_view
def properties_list(request):
    props = list(queries.properties(request).select_related("landlord").order_by("name"))
    unit_stats = {r["property_id"]: r for r in queries.units(request).filter(is_active=True).values("property_id")
                  .annotate(n=Count("id"), occ=Count("id", filter=Q(status="occupied")))}
    rent = {r["unit__property_id"]: r["r"] for r in queries.leases(request).filter(status="active")
            .values("unit__property_id").annotate(r=Sum("rent_minor"))}
    for p in props:
        s = unit_stats.get(p.pk, {})
        p.unit_count, p.occupied, p.rent_roll = s.get("n", 0), s.get("occ", 0), rent.get(p.pk, 0)
        p.vacant = p.unit_count - p.occupied
    return render(request, "web/properties.html", {"properties": props})


@org_view(write=True)
def properties_new(request):
    denied = unscoped_only(request)
    if denied:
        return denied
    form = PropertyForm(request.POST or None, org=request.org)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        landlord = d["landlord"] or Landlord.objects.create(org=request.org, name=d["new_landlord"].strip())
        prop = Property.objects.create(org=request.org, landlord=landlord, name=d["name"], code=d["code"], location=d["location"], ptype=d["ptype"])
        messages.success(request, f"{prop.name} added. Now add its units.")
        return redirect("property_detail", pk=prop.pk)
    return render(request, "web/property_form.html", {"form": form})


@org_view
def property_detail(request, pk):
    prop = get_object_or_404(queries.properties(request).select_related("landlord"), pk=pk)
    units = sorted(prop.units.filter(is_active=True), key=lambda u: natural(u.identifier))
    leases = {l.unit_id: l for l in Lease.objects.filter(unit__property=prop, status="active").select_related("tenant")}
    bal = queries.lease_balances(request.org, [l.id for l in leases.values()])
    rows = []
    for u in units:
        lease = leases.get(u.pk)
        rows.append({"unit": u, "lease": lease, "owing": bal.get(lease.pk, {}).get("owing", 0) if lease else 0})
    return render(request, "web/property_detail.html", {"prop": prop, "rows": rows, "occupied": len(leases), "total": len(units),
                                                         "rent_roll": sum(l.rent_minor for l in leases.values())})


@org_view(write=True)
def unit_new(request, pk):
    prop = get_object_or_404(queries.properties(request), pk=pk)
    form = UnitForm(request.POST or None, prop=prop)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            unit = Unit.objects.create(org=request.org, property=prop, identifier=d["identifier"], unit_type=d["unit_type"],
                                       rent_minor=to_minor(d["rent"]), deposit_minor=to_minor(d["deposit"]) if d["deposit"] else 0)
        except IntegrityError:
            form.add_error("identifier", "That unit number clashes with another unit's payment account number. Try a different number.")
        else:
            messages.success(request, f"Unit {unit.identifier} added. Tenants pay to account number {unit.account_number}.")
            return redirect("property_detail", pk=prop.pk)
    return render(request, "web/unit_form.html", {"form": form, "prop": prop})


# ------------------------------------------------------------ tenants and leases

@org_view
def tenants_list(request):
    status = request.GET.get("status", "active")
    q = request.GET.get("q", "").strip()
    qs = queries.leases(request).select_related("tenant", "unit__property")
    if status in ("active", "ended"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(tenant__full_name__icontains=q) | Q(tenant__phone__icontains=q) | Q(unit__identifier__icontains=q) | Q(unit__account_number__icontains=q))
    pg = page(request, qs.order_by("tenant__full_name"))
    bal = queries.lease_balances(request.org, [l.id for l in pg.object_list])
    for l in pg.object_list:
        l.owing = bal.get(l.pk, {}).get("owing", 0)
        l.credit = bal.get(l.pk, {}).get("credit", 0)
    return render(request, "web/tenants.html", {"page": pg, "status": status, "q": q})


@org_view(write=True)
def move_in(request):
    vacant = queries.units(request).filter(is_active=True).exclude(leases__status="active")
    form = MoveInForm(request.POST or None, unit_qs=vacant, initial={"unit": request.GET.get("unit")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        tenant = Tenant.objects.create(org=request.org, full_name=d["full_name"].strip(), phone=d["phone"].strip(), id_number=d["id_number"],
                                       next_of_kin_name=d["next_of_kin_name"], next_of_kin_phone=d["next_of_kin_phone"])
        try:
            lease = create_lease(d["unit"], tenant, d["start_date"], rent_minor=to_minor(d["rent"]), billing_day=d["billing_day"],
                                 deposit_minor=to_minor(d["deposit"]) if d["deposit"] else 0)
        except IntegrityError:
            tenant.delete()
            form.add_error("unit", "This unit already has an active tenancy.")
        else:
            note, today = "", timezone.localdate()
            if d["start_date"] <= today:
                inv, created = generate_invoice(lease, today, issue_date=today)
                if created:
                    note = f" The invoice for {inv.period:%B} is KES {inv.total_minor / 100:,.0f}."
            messages.success(request, f"{tenant.full_name} moved into {d['unit']}.{note}")
            return redirect("lease_detail", pk=lease.pk)
    opts = {str(u.pk): {"rent": u.rent_minor / 100, "deposit": u.deposit_minor / 100} for u in vacant}
    return render(request, "web/move_in.html", {"form": form, "unit_defaults": opts})


@org_view
def lease_detail(request, pk):
    lease = get_object_or_404(queries.leases(request).select_related("tenant", "unit__property"), pk=pk)
    today = timezone.localdate()
    bal = queries.lease_balances(request.org, [lease.pk]).get(lease.pk, {"owing": 0, "credit": 0})
    recv = Account.objects.filter(org=request.org, kind="receivable", lease=lease).first()
    statement, running = [], 0
    if recv:
        for e in Entry.objects.filter(account=recv).select_related("journal").order_by("journal__posted_at", "id"):
            running += e.amount_minor if e.direction == "D" else -e.amount_minor
            statement.append({"date": e.journal.posted_at, "memo": e.journal.memo, "charge": e.amount_minor if e.direction == "D" else 0,
                              "paid": e.amount_minor if e.direction == "C" else 0, "running": running})
    statement.reverse()
    invoices = queries.decorate_invoices(list(queries.with_paid(Invoice.objects.filter(lease=lease)).order_by("-period")), today)
    return render(request, "web/lease_detail.html", {
        "lease": lease, "owing": bal["owing"], "credit": bal["credit"], "statement": statement, "invoices": invoices,
        "payments": lease.payments.exclude(status="reversed").order_by("-paid_at")[:20],
        "charges": lease.charges.filter(is_active=True).select_related("charge_type"),
        "end_form": EndLeaseForm(initial={"end_date": today}), "charge_form": ChargeForm(),
    })


@require_POST
@org_view(write=True)
def lease_end(request, pk):
    lease = get_object_or_404(queries.leases(request).filter(status="active"), pk=pk)
    form = EndLeaseForm(request.POST)
    if form.is_valid():
        end_lease(lease, form.cleaned_data["end_date"])
        messages.success(request, f"{lease.tenant.full_name}'s tenancy ended. {lease.unit} is now vacant.")
    else:
        messages.error(request, first_error(form))
    return redirect("lease_detail", pk=pk)


@require_POST
@org_view(write=True)
def lease_charge_add(request, pk):
    lease = get_object_or_404(queries.leases(request).filter(status="active"), pk=pk)
    form = ChargeForm(request.POST)
    if form.is_valid():
        name = form.cleaned_data["name"].strip()
        code = (slugify(name).upper().replace("-", "_") or "CHARGE")[:30]
        ct, _ = ChargeType.objects.get_or_create(org=request.org, code=code, defaults={"name": name})
        LeaseCharge.objects.create(lease=lease, charge_type=ct, amount_minor=to_minor(form.cleaned_data["amount"]))
        messages.success(request, f"{name} will be added to this tenant's invoices from next month.")
    else:
        messages.error(request, first_error(form))
    return redirect("lease_detail", pk=pk)


# ------------------------------------------------------------ invoices

@org_view
def invoices_list(request):
    today = timezone.localdate()
    state, month, q = request.GET.get("state", ""), request.GET.get("month", ""), request.GET.get("q", "").strip()
    base = queries.invoices(request).filter(status="issued")
    periods = sorted(set(base.values_list("period", flat=True)), reverse=True)
    qs = queries.with_paid(base).select_related("lease__tenant", "lease__unit__property")
    if month:
        try:
            qs = qs.filter(period=date.fromisoformat(month))
        except ValueError:
            pass
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(lease__tenant__full_name__icontains=q) | Q(lease__unit__identifier__icontains=q))
    if state == "paid":
        qs = qs.filter(total_minor__lte=F("paid"))
    elif state == "overdue":
        qs = qs.filter(total_minor__gt=F("paid"), due_date__lt=today)
    elif state == "open":
        qs = qs.filter(total_minor__gt=F("paid"), due_date__gte=today)
    pg = page(request, qs.order_by("-period", "lease__unit__property__name", "lease__unit__identifier"))
    queries.decorate_invoices(list(pg.object_list), today)
    return render(request, "web/invoices.html", {"page": pg, "state": state, "month": month, "q": q, "periods": periods})


@org_view
def invoice_detail(request, pk):
    inv = get_object_or_404(queries.with_paid(queries.invoices(request)).select_related("lease__tenant", "lease__unit__property"), pk=pk)
    queries.decorate_invoices([inv], timezone.localdate())
    return render(request, "web/invoice_detail.html", {"inv": inv, "lines": inv.lines.select_related("charge_type"),
                                                        "allocations": inv.allocations.select_related("payment").order_by("created_at")})


@require_POST
@org_view(write=True)
def invoices_generate(request):
    denied = unscoped_only(request)
    if denied:
        return denied
    result = generate_due_invoices(request.org, timezone.localdate())
    if result["created"]:
        messages.success(request, f"Created {result['created']} invoice{'s' if result['created'] != 1 else ''} for {timezone.localdate():%B}.")
    else:
        messages.info(request, "Nothing new to create. Every tenancy whose invoice date has arrived already has this month's invoice.")
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "invoices_list")


# ------------------------------------------------------------ payments

@org_view
def payments_list(request):
    status, channel, q = request.GET.get("status", ""), request.GET.get("channel", ""), request.GET.get("q", "").strip()
    qs = queries.payments(request).select_related("unit__property", "lease__tenant")
    if status == "review":
        qs = qs.filter(status__in=["unmatched", "ambiguous"])
    elif status:
        qs = qs.filter(status=status)
    if channel:
        qs = qs.filter(channel=channel)
    if q:
        qs = qs.filter(Q(channel_txn_ref__icontains=q) | Q(reference_raw__icontains=q) | Q(payer_name__icontains=q)
                       | Q(unit__account_number__icontains=q) | Q(lease__tenant__full_name__icontains=q))
    totals = qs.exclude(status="reversed").aggregate(s=Sum("amount_minor"), n=Count("id"))
    return render(request, "web/payments.html", {"page": page(request, qs.order_by("-paid_at", "-id")), "status": status, "channel": channel,
                                                  "q": q, "total": totals["s"] or 0, "count": totals["n"], "sim_form": SimulateForm()})


@org_view
def payment_detail(request, pk):
    payment = get_object_or_404(queries.payments(request).select_related("unit__property", "lease__tenant", "raw_event"), pk=pk)
    journals = Journal.objects.filter(org=request.org, source_type="payment", source_id=payment.pk).order_by("id").prefetch_related("entries__account")
    raw = json.dumps(payment.raw_event.payload, indent=2) if payment.raw_event_id else ""
    item = getattr(payment, "review_item", None)
    return render(request, "web/payment_detail.html", {
        "p": payment, "journals": journals, "raw": raw, "item": item,
        "allocations": payment.allocations.select_related("invoice").order_by("id"),
        "options": unit_options(request) if request.property_ids is None and item and item.status == "open" else [],
    })


@org_view(write=True)
def payment_new(request):
    options = unit_options(request)
    form = ManualPaymentForm(request.POST or None, unit_qs=active_unit_qs(request), initial={"unit": request.GET.get("unit")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        paid_at = timezone.make_aware(datetime.combine(d["paid_on"], time(12, 0))) if d["paid_on"] else None
        try:
            payment = services.record_manual_payment(request.org, d["unit"], str(d["amount"]), d["method"], d["proof"], request.user, paid_at)
        except services.PaymentError as exc:
            form.add_error("proof", str(exc))
        else:
            messages.success(request, f"Recorded KES {to_minor(d['amount']) / 100:,.0f} from {payment.unit}. It was applied to the tenant's oldest unpaid invoice.")
            return redirect("payment_detail", pk=payment.pk)
    info = {str(o["unit"].pk): {"tenant": o["tenant"], "owing": f"{o['owing'] / 100:,.0f}", "credit": f"{o['credit'] / 100:,.0f}"} for o in options}
    return render(request, "web/payment_new.html", {"form": form, "unit_info": info})


@require_POST
@org_view(write=True)
def payment_reverse(request, pk):
    payment = get_object_or_404(queries.payments(request), pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give a reason before reversing a payment.")
    else:
        try:
            services.reverse_payment(payment, request.user, form.cleaned_data["reason"])
            messages.success(request, "Payment reversed. The tenant's invoice is open again.")
        except services.PaymentError as exc:
            messages.error(request, str(exc))
    return redirect("payment_detail", pk=pk)


@org_view(write=True)
def payments_import(request):
    denied = unscoped_only(request)
    if denied:
        return denied
    form = ImportForm(request.POST or None, request.FILES or None, org=request.org)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            result = services.import_bank_statement(request.org, form.cleaned_data["profile"], form.cleaned_data["file"], form.cleaned_data["label"])
        except (services.PaymentError, UnicodeDecodeError, KeyError, ValueError, csv.Error) as exc:
            form.add_error(None, f"We couldn't read that file: {exc}")
    return render(request, "web/payments_import.html", {"form": form, "result": result, "profiles": BankProfile.objects.filter(org=request.org),
                                                         "profile_form": BankProfileForm()})


@require_POST
@org_view(write=True)
def bank_profile_add(request):
    denied = unscoped_only(request)
    if denied:
        return denied
    form = BankProfileForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        cols = {k: d[k] for k in ("date", "credit", "reference", "narration", "txn_id", "balance", "payer_name") if d[k]}
        try:
            BankProfile.objects.create(org=request.org, name=d["name"], date_format=d["date_format"], columns=cols)
            messages.success(request, f"{d['name']} format saved. You can now upload its statements.")
        except IntegrityError:
            messages.error(request, "A bank format with that name already exists.")
    else:
        messages.error(request, first_error(form))
    return redirect("payments_import")


@org_view
def import_sample(request):
    rows = unit_options(request)[:3]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sample-statement.csv"'
    w = csv.writer(response)
    w.writerow(["Date", "Description", "Reference", "Debit", "Credit", "Balance"])
    today, bal = timezone.localdate(), 100000
    for i, o in enumerate(rows):
        amt = o["owing"] // 100 or o["unit"].rent_minor // 100
        bal += amt
        w.writerow([(today - timedelta(days=i)).strftime("%d/%m/%Y"), f"PESALINK FROM {o['tenant'].upper()}",
                    o["unit"].account_number if i != 1 else "", "", f"{amt:.2f}", f"{bal:.2f}"])
    w.writerow([today.strftime("%d/%m/%Y"), "BANK CHARGES", "", "150.00", "", f"{bal - 150:.2f}"])
    return response


@require_POST
@org_view(write=True)
def simulate_payment(request):
    if not (settings.DEBUG or settings.SANDBOX):
        raise Http404
    denied = unscoped_only(request)
    if denied:
        return denied
    form = SimulateForm(request.POST)
    if not form.is_valid():
        messages.error(request, first_error(form))
        return redirect("payments_list")
    d = form.cleaned_data
    payload = {"TransactionType": "Pay Bill", "TransID": (d["txn"] or uuid.uuid4().hex[:10]).upper(),
               "TransTime": timezone.localtime().strftime("%Y%m%d%H%M%S"), "TransAmount": str(d["amount"]),
               "BusinessShortCode": "000000", "BillRefNumber": d["account"], "MSISDN": "2547****0000", "FirstName": "SIMULATED"}
    event = services.ingest_raw(request.org, "mpesa", payload, source_label="simulated")
    payment = services.process_raw_event(event.pk)
    if payment is None:
        messages.info(request, "That transaction code was already recorded, so no second payment was created. This is how duplicate notifications from Safaricom are handled.")
        return redirect("payments_list")
    if payment.status == "matched":
        messages.success(request, f"Payment matched to {payment.unit} and applied to the tenant's invoices.")
    else:
        messages.warning(request, "Payment received but the account number didn't match exactly. It is waiting in the review queue.")
    return redirect("payment_detail", pk=payment.pk)


# ------------------------------------------------------------ review queue

@org_view
def review_list(request):
    denied = unscoped_only(request)
    if denied:
        return denied
    options = unit_options(request)
    by_unit_id = {o["unit"].pk: o for o in options}
    items = list(ReviewItem.objects.filter(payment__org=request.org, status="open").select_related("payment").order_by("-payment__paid_at"))
    for it in items:
        it.chips = [dict(s, option=by_unit_id.get(s.get("unit_id"))) for s in (it.suggestions or []) if by_unit_id.get(s.get("unit_id"))]
    resolved = ReviewItem.objects.filter(payment__org=request.org, status="resolved").select_related("payment__unit", "resolved_by").order_by("-resolved_at")[:8]
    return render(request, "web/review.html", {"items": items, "resolved": resolved, "options": options,
                                               "reasons": ["Tenant confirmed by phone", "Matched by payer name", "Matched from statement narration", "Typing mistake in account number"]})


@require_POST
@org_view(write=True)
def review_assign(request, pk):
    denied = unscoped_only(request)
    if denied:
        return denied
    payment = get_object_or_404(queries.payments(request).select_related("unit"), pk=pk)
    form = AssignForm(request.POST, unit_qs=active_unit_qs(request))
    nxt = request.POST.get("next", "")
    target = nxt if nxt.startswith("/") and not nxt.startswith("//") else "review_list"
    if not form.is_valid():
        messages.error(request, first_error(form))
        return redirect(target)
    try:
        services.assign_payment(payment, form.cleaned_data["unit"], request.user, form.cleaned_data["reason"])
        messages.success(request, f"Payment assigned to {form.cleaned_data['unit']} and applied to their invoices.")
    except services.PaymentError as exc:
        messages.error(request, str(exc))
    return redirect(target)


# ------------------------------------------------------------ reports, settings, search

@org_view
def report_arrears(request):
    rows = arrears_rows(request)
    totals = {b: sum(r["buckets"][b] for r in rows) for b in BUCKETS}
    return render(request, "web/report_arrears.html", {"rows": rows, "buckets": BUCKETS, "totals": totals, "grand": sum(totals.values()), "today": timezone.localdate()})


@org_view
def report_arrears_csv(request):
    rows = arrears_rows(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="arrears-{timezone.localdate():%Y-%m-%d}.csv"'
    w = csv.writer(response)
    w.writerow(["Tenant", "Unit", "Property", *BUCKETS, "Total (KES)"])
    for r in rows:
        w.writerow([r["tenant"], r["unit"], r["property"], *[f"{r['buckets'][b] / 100:.2f}" for b in BUCKETS], f"{r['total'] / 100:.2f}"])
    return response


@org_view(owner_only=True)
def settings_page(request):
    org = request.org
    config = PaymentChannelConfig.objects.filter(org=org, channel="mpesa").first()
    org_form = OrgSettingsForm(initial={"invoice_lead_days": org.invoice_lead_days})
    mpesa_form = MpesaForm(initial={"shortcode": config.shortcode if config else ""})
    if request.method == "POST":
        if "invoice_lead_days" in request.POST:
            org_form = OrgSettingsForm(request.POST)
            if org_form.is_valid():
                org.invoice_lead_days = org_form.cleaned_data["invoice_lead_days"]
                org.save(update_fields=["invoice_lead_days", "updated_at"])
                messages.success(request, "Invoice timing saved.")
                return redirect("settings_page")
        elif "shortcode" in request.POST:
            mpesa_form = MpesaForm(request.POST)
            if mpesa_form.is_valid():
                code = mpesa_form.cleaned_data["shortcode"].strip()
                try:
                    if config:
                        config.shortcode = code
                        config.save(update_fields=["shortcode", "updated_at"])
                    else:
                        config = PaymentChannelConfig.objects.create(org=org, channel="mpesa", shortcode=code)
                    messages.success(request, "M-Pesa details saved.")
                    return redirect("settings_page")
                except IntegrityError:
                    mpesa_form.add_error("shortcode", "This Paybill or Till is already set up.")
    urls = {}
    if config:
        for name in ("confirmation", "validation"):
            urls[name] = request.build_absolute_uri(reverse(f"mpesa-{name}", args=[config.webhook_token]))
    members = Membership.objects.filter(org=org).select_related("user").order_by("role", "user__username")
    return render(request, "web/settings.html", {"org_form": org_form, "mpesa_form": mpesa_form, "config": config, "urls": urls, "members": members})


@org_view
def search(request):
    q = request.GET.get("q", "").strip()
    ctx = {"q": q, "units": [], "tenants": [], "payments": [], "invoices": []}
    if len(q) >= 2:
        ctx["units"] = queries.units(request).filter(Q(identifier__icontains=q) | Q(account_number__icontains=q)).select_related("property")[:8]
        ctx["tenants"] = queries.leases(request).filter(Q(tenant__full_name__icontains=q) | Q(tenant__phone__icontains=q)).select_related("tenant", "unit__property")[:8]
        ctx["payments"] = queries.payments(request).filter(Q(channel_txn_ref__icontains=q) | Q(reference_raw__icontains=q) | Q(payer_name__icontains=q)).order_by("-paid_at")[:8]
        ctx["invoices"] = queries.invoices(request).filter(number__icontains=q).select_related("lease__tenant", "lease__unit")[:8]
    ctx["found"] = any(ctx[k] for k in ("units", "tenants", "payments", "invoices"))
    return render(request, "web/search.html", ctx)
