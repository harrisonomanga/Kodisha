"""Channel adapters: each turns a raw payload into one StandardPayment. Nothing downstream knows the channel."""
import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.utils import timezone

EAT = ZoneInfo("Africa/Nairobi")


@dataclass
class StandardPayment:
    channel: str
    txn_ref: str
    amount_minor: int
    paid_at: datetime
    reference: str = ""
    payer_name: str = ""
    payer_phone: str = ""
    free_text: str = ""  # narration; only searched for account numbers on bank lines


def to_minor(value) -> int:
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"bad amount {value!r}") from exc
    minor = amount * 100
    if minor != minor.to_integral_value() or minor <= 0:
        raise ValueError(f"amount must be positive with at most 2 decimals: {value!r}")
    return int(minor)


def parse_mpesa_c2b(payload: dict) -> StandardPayment:
    """Safaricom Daraja C2B confirmation. Phone numbers may arrive masked, so they are never used to match."""
    txn = str(payload["TransID"]).strip()
    if not txn:
        raise ValueError("missing TransID")
    raw_time = payload.get("TransTime")
    paid_at = (
        timezone.make_aware(datetime.strptime(str(raw_time), "%Y%m%d%H%M%S"), EAT) if raw_time else timezone.now()
    )
    name = " ".join(p for p in (payload.get("FirstName"), payload.get("MiddleName"), payload.get("LastName")) if p)
    return StandardPayment(
        channel="mpesa",
        txn_ref=txn,
        amount_minor=to_minor(payload["TransAmount"]),
        paid_at=paid_at,
        reference=str(payload.get("BillRefNumber", "")).strip(),
        payer_name=name,
        payer_phone=str(payload.get("MSISDN", "")),
    )


def bank_row_is_credit(row: dict, columns: dict) -> bool:
    raw = (row.get(columns["credit"]) or "").replace(",", "").strip()
    try:
        return bool(raw) and Decimal(raw) > 0
    except InvalidOperation:
        return False


def parse_bank_row(payload: dict) -> StandardPayment:
    """payload = {"row": {...}, "columns": {...}, "date_format": "...", "label": "...", "occurrence": n}"""
    row, cols = payload["row"], payload["columns"]
    get = lambda key: (row.get(cols[key], "") if key in cols else "").strip()
    amount = to_minor(get("credit"))
    paid_at = timezone.make_aware(datetime.strptime(get("date"), payload["date_format"]), EAT)
    txn = get("txn_id")
    if not txn:
        # No bank transaction id: build a stable fingerprint. Identical lines in one file are numbered
        # so two genuine same-day, same-amount payments stay distinct, while re-uploading an
        # overlapping statement reproduces the same references.
        sig = "|".join([payload["label"], get("date"), str(amount), get("narration"), get("balance")])
        txn = hashlib.sha256(sig.encode()).hexdigest()[:24] + f"-{payload.get('occurrence', 1)}"
    return StandardPayment(
        channel="bank",
        txn_ref=txn,
        amount_minor=amount,
        paid_at=paid_at,
        reference=get("reference"),
        payer_name=get("payer_name"),
        free_text=get("narration"),
    )


PARSERS = {"mpesa": parse_mpesa_c2b, "bank": parse_bank_row}
