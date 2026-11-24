from django import template

register = template.Library()
PAYMENT_STATES = {"matched": ("Matched", "ok"), "unmatched": ("Needs review", "warn"), "ambiguous": ("Needs review", "warn"), "reversed": ("Reversed", "muted")}
INVOICE_STATES = {"paid": ("Paid", "ok"), "partial": ("Part paid", "warn"), "overdue": ("Overdue", "bad"), "open": ("Awaiting payment", "info"), "void": ("Void", "muted")}
TILE_STATES = {"paid": "Paid", "partial": "Part paid", "overdue": "Overdue", "awaiting": "Awaiting", "unbilled": "Not billed", "vacant": "Vacant"}


def _fmt(minor, decimals=None):
    if minor in (None, ""):
        return "0"
    whole, cents = divmod(abs(int(minor)), 100)
    sign = "-" if int(minor) < 0 else ""
    if decimals is None:
        decimals = 2 if cents else 0
    out = f"{whole:,}"
    if decimals:
        out += f".{cents:02d}"
    return sign + out


@register.filter
def kes(minor):
    return f"KES {_fmt(minor)}"


@register.filter
def money(minor):
    return _fmt(minor)


@register.filter
def money2(minor):
    return _fmt(minor, 2)


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def first_name(name):
    return str(name).split()[0] if str(name).strip() else ""


@register.filter
def payment_label(s):
    return PAYMENT_STATES.get(s, (s, "muted"))[0]


@register.filter
def payment_tone(s):
    return PAYMENT_STATES.get(s, (s, "muted"))[1]


@register.filter
def invoice_label(s):
    return INVOICE_STATES.get(s, (s, "muted"))[0]


@register.filter
def invoice_tone(s):
    return INVOICE_STATES.get(s, (s, "muted"))[1]


@register.filter
def tile_label(s):
    return TILE_STATES.get(s, s)


@register.filter
def channel_label(c):
    return {"mpesa": "M-Pesa", "bank": "Bank", "manual": "Manual", "airtel": "Airtel Money"}.get(c, c)


@register.simple_tag(takes_context=True)
def qs(context, **kw):
    q = context["request"].GET.copy()
    for k, v in kw.items():
        q[k] = v
    return "?" + q.urlencode()


@register.filter
def reason_label(r):
    return {"no_match": "No matching account", "ambiguous": "More than one match", "no_lease": "Unit has no tenant"}.get(r, str(r).replace("_", " ").capitalize())
