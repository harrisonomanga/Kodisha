"""Unit payment account numbers: property code + unit code + one check character.

The check character (Luhn mod 36) lets us tell a mistyped reference from a valid one and offer
"did you mean" suggestions. It never lets us auto-correct: an inexact reference always goes to
the review queue.
"""
import re

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def normalise(ref: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


def check_char(body: str) -> str:
    n = len(ALPHABET)
    factor, total = 2, 0
    for ch in reversed(body):
        addend = factor * ALPHABET.index(ch)
        factor = 1 if factor == 2 else 2
        total += addend // n + addend % n
    return ALPHABET[(n - (total % n)) % n]


def make_account_number(property_code: str, unit_identifier: str) -> str:
    body = normalise(property_code) + normalise(unit_identifier)
    if not body:
        raise ValueError("property code and unit identifier cannot both be empty")
    return body + check_char(body)


def is_valid(account_number: str) -> bool:
    acct = normalise(account_number)
    return len(acct) >= 2 and check_char(acct[:-1]) == acct[-1]
