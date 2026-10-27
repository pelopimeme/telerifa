"""Small helpers shared by the API, the bot and channel posts (no I/O)."""
import re
from datetime import datetime, timedelta, timezone

from ..config import settings

VET = timezone(timedelta(hours=-4))   # Venezuela has no DST
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
HANDLE_RE = re.compile(r"[A-Za-z0-9_]{5,32}")   # real Telegram usernames


def is_handle(name: str) -> bool:
    """False for fallback labels such as 'Player-1234' ('-' is not valid in usernames)."""
    return bool(HANDLE_RE.fullmatch(name or ""))


def mask_username(name: str | None) -> str:
    """'alexander' -> '@alex***'. Non-handles are returned unchanged."""
    if not name:
        return "—"
    if not is_handle(name):
        return name
    return f"@{name[: min(4, max(1, len(name) // 2))]}***"


def _vet(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(VET)


def fmt_date_es(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    d = _vet(dt)
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def fmt_datetime_es(dt: datetime) -> str:
    d = _vet(dt)
    return f"{d.day} {MESES[d.month - 1]} {d.year}, {d:%H:%M} (hora de Venezuela)"


def miniapp_link(start_param: str | None = None) -> str:
    """Direct Mini App link (t.me/<bot>/<short_name>). Channel posts must use a *url* button with this."""
    base = f"https://t.me/{settings.bot_username}/{settings.miniapp_short_name}"
    return f"{base}?startapp={start_param}" if start_param else base
