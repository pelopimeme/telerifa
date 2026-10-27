"""Telegram Bot API client (Spanish): admin alerts, user DMs and @telerifa channel posts.

`web_app` inline buttons only work in PRIVATE chats, so channel posts use `url` buttons that point to the
direct Mini App link (t.me/<bot>/<short_name>?startapp=...).
"""
import html
import logging
from decimal import Decimal

import httpx

from ..config import settings
from ..models import DrawHistory, Payment, Raffle, Ticket, format_ticket_number
from .links import fmt_datetime_es, is_handle, mask_username, miniapp_link
from .money import fmt_ves

log = logging.getLogger("telerifa.telegram")


async def _call_raw(method: str, payload: dict) -> dict | None:
    if not settings.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set; skipping %s", method)
        return None
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
        data = r.json()
        if r.status_code != 200 or not data.get("ok"):
            log.error("Telegram %s failed: %s %s", method, r.status_code, r.text[:300])
            return None
        return data["result"]
    except (httpx.HTTPError, ValueError):
        log.exception("Telegram %s error", method)
        return None


async def _call(method: str, payload: dict) -> bool:
    return await _call_raw(method, payload) is not None


def _url_buttons(*row: tuple[str, str]) -> dict:
    return {"inline_keyboard": [[{"text": t, "url": u} for t, u in row]]}


def _winner_label(name: str | None) -> str:
    if not name:
        return "—"
    if not is_handle(name):
        return html.escape(name)
    return f"@{name}" if settings.announce_full_handle else html.escape(mask_username(name))


# ---- Admin: payment review ------------------------------------------------------------------------

async def alert_admin(text: str) -> bool:
    if not settings.admin_chat_id:
        log.warning("ADMIN_CHAT_ID not set; admin alert dropped: %s", text)
        return False
    return await _call("sendMessage", {"chat_id": settings.admin_chat_id, "text": text, "parse_mode": "HTML"})


async def send_admin_payment_alert(payment: Payment, username: str | None, raffle_title: str) -> int | None:
    """Alert with [✅ Aprobar] [❌ Rechazar]. Returns the Telegram message id (so it can be edited later)."""
    if not settings.admin_chat_id:
        log.warning("ADMIN_CHAT_ID not set: payment %s cannot be approved from Telegram", payment.id)
        return None
    who = f"@{html.escape(username)}" if username else "(sin usuario)"
    text = (
        "💳 <b>Nuevo Pago Móvil por aprobar</b>\n\n"
        f"👤 Usuario: {who} (ID <code>{payment.user_id}</code>)\n"
        f"🎟 Sorteo: {html.escape(raffle_title)}\n"
        f"🔢 Tickets: <b>{payment.ticket_quantity}</b>\n"
        f"💵 Monto esperado: <b>{fmt_ves(payment.amount_ves)} Bs.</b>\n"
        f"🧾 Referencia: <code>{html.escape(payment.referencia)}</code>\n"
        f"📈 Tasa BCV: {fmt_ves(payment.rate_ves)} Bs/US$\n"
        f"🆔 Pago #{payment.id}\n\n"
        "Verifica en tu banco que el monto y la referencia coincidan antes de aprobar."
    )
    result = await _call_raw("sendMessage", {
        "chat_id": settings.admin_chat_id, "text": text, "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ Aprobar", "callback_data": f"pm:ok:{payment.id}"},
            {"text": "❌ Rechazar", "callback_data": f"pm:no:{payment.id}"},
        ]]},
    })
    return result["message_id"] if result else None


# ---- Users ----------------------------------------------------------------------------------------

async def notify_user(user_id: int, text: str) -> bool:
    return await _call("sendMessage", {"chat_id": user_id, "text": text, "parse_mode": "HTML"})


def tickets_text(tickets: list[Ticket]) -> str:
    nums = ", ".join(format_ticket_number(t.ticket_number) for t in tickets[:50])
    return nums + (f" (+{len(tickets) - 50} más)" if len(tickets) > 50 else "")


# ---- Channel --------------------------------------------------------------------------------------

async def announce_new_raffle(raffle: Raffle, precio_ticket_ves: Decimal) -> bool:
    """New raffle post for @telerifa with a one-tap 'Comprar Tickets' deep link."""
    draw_line = f"🗓 Sorteo: <b>{fmt_datetime_es(raffle.draw_at)}</b>\n" if raffle.draw_at else ""
    text = (
        "🎟️ <b>¡Nuevo sorteo de TeleRifa abierto!</b>\n\n"
        f"<b>{html.escape(raffle.title)}</b>\n"
        f"💵 Precio por ticket: <b>{fmt_ves(precio_ticket_ves)} Bs.</b> (tasa BCV de hoy)\n"
        f"{draw_line}"
        "🔐 Se elige un ticket ganador al azar entre los tickets vendidos.\n\n"
        "Toca el botón para comprar tus tickets 👇"
    )
    return await _call("sendMessage", {
        "chat_id": settings.telegram_channel, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _url_buttons(("🎟️ Comprar Tickets", miniapp_link("buy"))),
    })


async def post_winner_announcement(winner: DrawHistory, raffle_title: str, tickets_sold: int) -> bool:
    """Called by execute_raffle_draw() after the draw commits. The bot must be an admin of the channel."""
    text = (
        "🎉 <b>¡Resultado del sorteo TeleRifa!</b>\n\n"
        f"🎟 Sorteo: <b>{html.escape(raffle_title)}</b>\n"
        f"🏆 Ticket ganador: <b>{html.escape(winner.winning_ticket_number or '')}</b>\n"
        f"👤 Ganador: <b>{_winner_label(winner.winner_username)}</b> — ¡felicidades!\n"
        f"💰 Premio: <b>{fmt_ves(winner.prize_ves)} Bs.</b>\n"
        f"📊 Tickets vendidos: {tickets_sold}\n\n"
        "¡Pronto abriremos un nuevo sorteo!"
    )
    return await _call("sendMessage", {
        "chat_id": settings.telegram_channel, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _url_buttons(
            ("🎟️ Comprar Tickets", miniapp_link("buy")),
            ("🏆 Ganadores", miniapp_link("winners")),
        ),
    })
