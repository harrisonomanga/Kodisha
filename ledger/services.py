from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

from .models import Account, Entry, Journal


class LedgerError(Exception):
    pass


def get_account(org, kind, lease=None):
    try:
        account, _ = Account.objects.get_or_create(org=org, kind=kind, lease=lease)
    except IntegrityError:  # concurrent creation
        account = Account.objects.get(org=org, kind=kind, lease=lease)
    return account


def post_journal(org, lines, memo="", source_type="", source_id=None, key=None, reverses=None, posted_at=None):
    """Post one balanced journal. `lines` is a list of (account, 'D' or 'C', amount_minor).

    If `key` was already used for this organisation, the existing journal is returned and nothing
    is posted. That makes every posting safe to retry.
    """
    if key:
        existing = Journal.objects.filter(org=org, idempotency_key=key).first()
        if existing:
            return existing
    debits = credits = 0
    for account, direction, amount in lines:
        if amount <= 0:
            raise LedgerError("amounts must be positive")
        if account.org_id != org.pk:
            raise LedgerError("account belongs to another organisation")
        if direction == Entry.DEBIT:
            debits += amount
        elif direction == Entry.CREDIT:
            credits += amount
        else:
            raise LedgerError(f"bad direction {direction!r}")
    if not lines or debits != credits:
        raise LedgerError(f"journal does not balance (debits {debits}, credits {credits})")
    try:
        with transaction.atomic():
            extra = {"posted_at": posted_at} if posted_at else {}
            journal = Journal.objects.create(
                org=org, memo=memo, source_type=source_type, source_id=source_id,
                idempotency_key=key, reverses=reverses, **extra,
            )
            Entry.objects.bulk_create(
                [Entry(journal=journal, account=a, direction=d, amount_minor=amt) for a, d, amt in lines]
            )
    except IntegrityError:
        if key:
            existing = Journal.objects.filter(org=org, idempotency_key=key).first()
            if existing:
                return existing
        raise
    return journal


def reverse_journal(journal, memo=""):
    """Corrections are new journals that mirror the original with debits and credits swapped."""
    existing = Journal.objects.filter(reverses=journal).first()
    if existing:
        return existing
    swapped = [
        (e.account, Entry.CREDIT if e.direction == Entry.DEBIT else Entry.DEBIT, e.amount_minor)
        for e in journal.entries.select_related("account")
    ]
    return post_journal(
        journal.org, swapped, memo=memo or f"Reversal of journal {journal.pk}",
        source_type=journal.source_type, source_id=journal.source_id,
        key=f"reverse:{journal.pk}", reverses=journal,
    )


def balance(account):
    """Balance in the account's normal direction (positive means the usual side)."""
    agg = Entry.objects.filter(account=account).aggregate(
        d=Sum("amount_minor", filter=Q(direction=Entry.DEBIT)),
        c=Sum("amount_minor", filter=Q(direction=Entry.CREDIT)),
    )
    debits, credits = agg["d"] or 0, agg["c"] or 0
    return debits - credits if account.kind in Account.DEBIT_NORMAL else credits - debits


def tenant_position(lease):
    """(receivable, credit) for a lease. Net owed = receivable - credit."""
    recv = balance(get_account(lease.org, Account.Kind.RECEIVABLE, lease))
    cred = balance(get_account(lease.org, Account.Kind.TENANT_CREDIT, lease))
    return recv, cred
