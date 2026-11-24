import csv
import io
import logging
from collections import Counter

from django.db import transaction
from django.utils import timezone

from billing.models import Allocation
from billing.services import allocate_to_invoices
from core import audit
from ledger.models import Account, Entry, Journal
from ledger.services import get_account, post_journal, reverse_journal
from portfolio.models import Lease

from .adapters import PARSERS, StandardPayment, bank_row_is_credit, to_minor
from .matching import match
from .models import BankProfile, Payment, RawPaymentEvent, ReviewItem

log = logging.getLogger(__name__)


class PaymentError(Exception):
    pass


# --- Intake -----------------------------------------------------------------------------------

def ingest_raw(org, channel, payload, source_label=""):
    """Store exactly what arrived. This is the first and only thing a webhook does before replying."""
    return RawPaymentEvent.objects.create(org=org, channel=channel, payload=payload, source_label=source_label)


def process_raw_event(event_id):
    """Turn one raw event into a payment. Safe to call repeatedly (replay tool, retries, duplicates)."""
    with transaction.atomic():
        event = RawPaymentEvent.objects.select_for_update().get(pk=event_id)
        if event.processed_at:
            return None
        try:
            sp = PARSERS[event.channel](event.payload)
        except (KeyError, ValueError) as exc:  # a malformed payload will not fix itself: park it for a human
            event.processing_error = f"{type(exc).__name__}: {exc}"
            event.save(update_fields=["processing_error"])
            log.warning("raw payment event %s could not be parsed: %s", event.pk, exc)
            return None
        payment, created = register_payment(event.org, sp, raw_event=event)
        event.processed_at = timezone.now()
        event.processing_error = ""
        event.save(update_fields=["processed_at", "processing_error"])
        return payment if created else None  # None = nothing new (duplicate, already processed or unparseable)


# --- Core: one path for every channel ---------------------------------------------------------

@transaction.atomic
def register_payment(org, sp: StandardPayment, raw_event=None, forced_unit=None, actor=None):
    """Create the payment, post the receipt, then match and allocate. Returns (payment, created)."""
    payment, created = Payment.objects.get_or_create(
        org=org, channel=sp.channel, channel_txn_ref=sp.txn_ref,
        defaults=dict(
            amount_minor=sp.amount_minor, payer_name=sp.payer_name, payer_phone=sp.payer_phone,
            reference_raw=sp.reference, paid_at=sp.paid_at, raw_event=raw_event,
        ),
    )
    if not created:  # duplicate notification: the raw event is kept, no second payment is created
        return payment, False

    # Money has arrived. Park it in "unallocated" until it is assigned to a tenant.
    post_journal(
        org,
        [(get_account(org, Account.Kind.CASH), Entry.DEBIT, payment.amount_minor),
         (get_account(org, Account.Kind.UNALLOCATED), Entry.CREDIT, payment.amount_minor)],
        memo=f"Received {payment}", source_type="payment", source_id=payment.pk,
        key=f"payment-receipt:{payment.pk}", posted_at=payment.paid_at,
    )

    if forced_unit is not None:
        unit = forced_unit
        reason, suggestions = None, []
    else:
        result = match(org, sp.reference, sp.free_text, sp.amount_minor, sp.payer_name)
        unit = result.unit
        reason = None if result.outcome == "matched" else ("ambiguous_reference" if result.outcome == "ambiguous" else "no_match")
        suggestions = result.suggestions

    lease = unit.leases.filter(status=Lease.Status.ACTIVE).first() if unit else None
    if unit and not lease:
        reason = "no_active_lease"

    if reason:
        payment.status = Payment.Status.AMBIGUOUS if reason == "ambiguous_reference" else Payment.Status.UNMATCHED
        payment.save(update_fields=["status", "updated_at"])
        ReviewItem.objects.create(payment=payment, reason=reason, suggestions=suggestions)
    else:
        _allocate(payment, unit, lease)
    return payment, True


def _allocate(payment, unit, lease):
    """Move a payment out of 'unallocated' into the tenant's account: invoices first, remainder as credit."""
    org = payment.org
    lease = Lease.objects.select_for_update().get(pk=lease.pk)
    applied = allocate_to_invoices(lease, payment.amount_minor, payment=payment, source=Allocation.Source.PAYMENT)
    credit = payment.amount_minor - applied
    lines = [(get_account(org, Account.Kind.UNALLOCATED), Entry.DEBIT, payment.amount_minor)]
    if applied:
        lines.append((get_account(org, Account.Kind.RECEIVABLE, lease), Entry.CREDIT, applied))
    if credit:
        lines.append((get_account(org, Account.Kind.TENANT_CREDIT, lease), Entry.CREDIT, credit))
    post_journal(
        org, lines, memo=f"Allocated {payment}", source_type="payment", source_id=payment.pk,
        key=f"payment-alloc:{payment.pk}",
    )
    payment.unit, payment.lease, payment.status = unit, lease, Payment.Status.MATCHED
    payment.save(update_fields=["unit", "lease", "status", "updated_at"])


# --- Human actions ----------------------------------------------------------------------------

@transaction.atomic
def assign_payment(payment, unit, user, reason):
    """Review queue: an authorised user assigns an unmatched payment to a unit. Reason is mandatory."""
    if not reason.strip():
        raise PaymentError("a reason is required")
    if payment.status not in (Payment.Status.UNMATCHED, Payment.Status.AMBIGUOUS):
        raise PaymentError(f"payment is {payment.status}, only unmatched or ambiguous payments can be assigned")
    if unit.org_id != payment.org_id:
        raise PaymentError("unit belongs to another organisation")
    lease = unit.leases.filter(status=Lease.Status.ACTIVE).first()
    if not lease:
        raise PaymentError("that unit has no active lease")
    before = {"status": payment.status, "unit": None}
    _allocate(payment, unit, lease)
    item = payment.review_item
    item.status, item.resolved_by, item.resolved_at, item.resolution_note = (
        ReviewItem.Status.RESOLVED, user, timezone.now(), reason[:255])
    item.save()
    audit.log(payment.org, user, "payment.assigned", payment, before, {"status": payment.status, "unit": unit.pk, "reason": reason})
    return payment


@transaction.atomic
def reverse_payment(payment, user, reason):
    """Refund or bounced payment: mirror every journal and free the allocations. Nothing is edited or deleted."""
    if not reason.strip():
        raise PaymentError("a reason is required")
    if payment.status == Payment.Status.REVERSED:
        return payment
    before = {"status": payment.status}
    journals = Journal.objects.filter(org=payment.org, source_type="payment", source_id=payment.pk, reverses__isnull=True)
    for journal in journals:
        reverse_journal(journal, memo=f"Reversal: {reason}"[:255])
    payment.allocations.update(reversed=True)
    payment.status = Payment.Status.REVERSED
    payment.save(update_fields=["status", "updated_at"])
    item = getattr(payment, "review_item", None)
    if item and item.status == ReviewItem.Status.OPEN:
        item.status, item.resolved_by, item.resolved_at, item.resolution_note = (
            ReviewItem.Status.RESOLVED, user, timezone.now(), f"payment reversed: {reason}"[:255])
        item.save()
    audit.log(payment.org, user, "payment.reversed", payment, before, {"status": payment.status, "reason": reason})
    return payment


def record_manual_payment(org, unit, amount, method, proof_ref, user, paid_at=None):
    """Cash, cheque or deposit slip. The proof reference is mandatory and unique, so a slip cannot be posted twice.

    Approval rules by amount are NOT built yet (open question in the README).
    """
    if not proof_ref.strip():
        raise PaymentError("a proof reference is required")
    sp = StandardPayment(
        channel="manual", txn_ref=f"{method}:{proof_ref.strip()}", amount_minor=to_minor(amount),
        paid_at=paid_at or timezone.now(), reference=unit.account_number, payer_name="",
    )
    payment, created = register_payment(org, sp, forced_unit=unit, actor=user)
    if not created:
        raise PaymentError("that proof reference has already been recorded")
    audit.log(org, user, "payment.manual_recorded", payment, None, {"method": method, "proof": proof_ref, "amount_minor": payment.amount_minor})
    return payment


# --- Bank statement import --------------------------------------------------------------------

def import_bank_statement(org, profile: BankProfile, fileobj, label, replay_inline=True):
    """Upload a CSV statement. Credits become payments through the same pipeline; debits are ignored."""
    content = fileobj.read()
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for k, c in profile.columns.items() if k in ("date", "credit") and c not in (reader.fieldnames or [])]
    if missing:
        raise PaymentError(f"statement is missing expected columns: {missing}")
    seen = Counter()
    summary = Counter()
    for row in reader:
        if not bank_row_is_credit(row, profile.columns):
            summary["ignored_non_credit"] += 1
            continue
        sig = tuple(sorted(row.items()))
        seen[sig] += 1
        payload = {
            "row": row, "columns": profile.columns, "date_format": profile.date_format,
            "label": label, "occurrence": seen[sig],
        }
        event = ingest_raw(org, "bank", payload, source_label=label)
        payment = process_raw_event(event.pk) if replay_inline else None
        if payment is None:
            event.refresh_from_db()
            summary["errors" if event.processing_error else "duplicates"] += 1
        elif payment.status == Payment.Status.MATCHED:
            summary["matched"] += 1
        else:
            summary["needs_review"] += 1
    return dict(summary)
