"""Pure money helpers (no I/O, no settings): Bolívares formatting, totals, BCV payload parsing."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
RATE_Q = Decimal("0.0001")


def calc_total_ves(cantidad: int, precio_usd: Decimal, tasa_bcv: Decimal) -> Decimal:
    """total_ves = cantidad_tickets * precio_usd * tasa_bcv  (2 decimals, half-up).

    The Mini App implements the identical formula with integer maths so the amount shown to the
    user always equals the amount stored here.
    """
    return (Decimal(cantidad) * precio_usd * tasa_bcv).quantize(CENT, ROUND_HALF_UP)


def fmt_ves(value) -> str:
    """Venezuelan number format: 1234.5 -> '1.234,50'."""
    return f"{Decimal(value):,.2f}".translate(str.maketrans(",.", ".,"))


# ---- BCV payload parsing --------------------------------------------------------------------------

_BAD = ("eur", "euro", "previous", "anterior", "change", "variation", "percent", "diff", "date", "fecha", "time")
_USD = ("usd", "dolar", "dollar")
_NOW = ("current", "actual", "today", "hoy")
_VALUE_KEYS = ("price", "rate", "value", "valor", "promedio", "average", "usd", "dolar", "dollar")


def _to_decimal(v) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        d = Decimal(str(v))
    elif isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")      # '1.234,56'
        elif "," in s:
            s = s.replace(",", ".")                       # '36,5012'
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
    else:
        return None
    return d if d > 0 else None


def extract_rate(payload) -> Decimal | None:
    """Pull the USD/VES number out of whatever JSON shape the provider returns.

    The exact schema of the provider can change, so this scores every numeric leaf by its key path
    (must look like USD/dólar, must not look like EUR/previous/date) and returns the best one.
    `python -m app.services.rates` prints what was parsed so you can verify it against the live feed.
    """
    found: list[tuple[int, Decimal]] = []

    def walk(node, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + (str(k).lower(),))
        elif isinstance(node, list):
            for v in node:
                walk(v, path)
        else:
            d = _to_decimal(node)
            joined = ".".join(path)
            if d is None or any(b in joined for b in _BAD):
                return
            score = (2 if any(a in joined for a in _USD) else 0) \
                + (1 if any(a in joined for a in _NOW) else 0) \
                + (1 if path and path[-1] in _VALUE_KEYS else 0)
            if score >= 2:
                found.append((score, d))

    walk(payload, ())
    if not found:
        return None
    best = max(s for s, _ in found)
    return next(d for s, d in found if s == best).quantize(RATE_Q)
