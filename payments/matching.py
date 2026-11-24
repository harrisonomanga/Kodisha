"""Matching rules (BRD section 8). Auto-match ONLY when the reference resolves to exactly one active unit.

Everything else becomes a review item, optionally with suggestions. Suggestions are never auto-applied.
"""
import difflib
import re
from dataclasses import dataclass, field

from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

from billing.models import Invoice
from portfolio.accounts import is_valid, normalise
from portfolio.models import Lease, Unit

MAX_SUGGESTIONS = 5


@dataclass
class MatchResult:
    outcome: str  # "matched" | "none" | "ambiguous"
    unit: Unit | None = None
    suggestions: list = field(default_factory=list)


def _one_character_away(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(diffs) == 1:
        return True
    return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]


def _suggest(org, norm_ref, amount_minor, payer_name):
    out = []
    accounts = dict(Unit.objects.filter(org=org, is_active=True).values_list("account_number", "id"))
    if norm_ref:
        for acct, unit_id in accounts.items():
            if _one_character_away(norm_ref, acct):
                out.append({"unit_id": unit_id, "account_number": acct, "why": "reference is one character off"})
    if amount_minor:
        due = (
            Invoice.objects.filter(org=org, status=Invoice.Status.ISSUED)
            .annotate(paid=Coalesce(Sum("allocations__amount_minor", filter=Q(allocations__reversed=False)), 0))
            .filter(total_minor=F("paid") + amount_minor)
            .select_related("lease__unit")
        )
        for inv in due[:MAX_SUGGESTIONS]:
            out.append({"unit_id": inv.lease.unit_id, "account_number": inv.lease.unit.account_number,
                        "why": "amount equals an open invoice"})
    if payer_name:
        names = {l.tenant.full_name: l for l in Lease.objects.filter(org=org, status="active").select_related("tenant", "unit")}
        for name in difflib.get_close_matches(payer_name, list(names), n=3, cutoff=0.8):
            lease = names[name]
            out.append({"unit_id": lease.unit_id, "account_number": lease.unit.account_number,
                        "why": f"payer name resembles tenant {name}"})
    seen, unique = set(), []
    for s in out:
        if s["unit_id"] not in seen:
            seen.add(s["unit_id"])
            unique.append(s)
    return unique[:MAX_SUGGESTIONS]


def match(org, reference="", free_text="", amount_minor=0, payer_name=""):
    norm = normalise(reference)
    if norm:
        unit = Unit.objects.filter(org=org, account_number=norm, is_active=True).first()
        if unit:
            return MatchResult("matched", unit)

    if free_text:  # bank narrations: look for exactly one known account number inside the text
        accounts = dict(Unit.objects.filter(org=org, is_active=True).values_list("account_number", "id"))
        tokens = re.findall(r"[A-Za-z0-9]+", free_text.upper())
        candidates = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}  # "PLT-A1" splits in two
        hits = sorted(c for c in candidates if c in accounts and is_valid(c))
        if len(hits) == 1:
            return MatchResult("matched", Unit.objects.get(pk=accounts[hits[0]]))
        if len(hits) > 1:
            sugg = [{"unit_id": accounts[h], "account_number": h, "why": "found in narration"} for h in hits]
            return MatchResult("ambiguous", None, sugg[:MAX_SUGGESTIONS])

    return MatchResult("none", None, _suggest(org, norm, amount_minor, payer_name))
