"""Comandos del bot (aiogram 3): /start, /billetera, /ganadores + botones Aprobar/Rechazar del admin."""
import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import func, select

from ..config import settings
from ..db import SessionLocal
from ..models import DrawHistory, Payment, PaymentStatus, Ticket, TicketStatus, User, format_ticket_number
from ..services.links import fmt_date_es, mask_username
from ..services.money import fmt_ves
from ..services.pagomovil import NoActiveRaffle, active_raffle, decide_payment, pot_ves
from ..services.telegram import notify_user, tickets_text

router = Router(name="telerifa")
private = F.chat.type == ChatType.PRIVATE   # web_app buttons only work in private chats

WELCOME = (
    "🎟️ <b>¡Bienvenido a TeleRifa{name}!</b>\n\n"
    "<b>Cómo funciona</b>\n"
    "1️⃣ Elige cuántos tickets quieres en la app\n"
    "2️⃣ Paga por Pago Móvil y envía el número de referencia\n"
    "3️⃣ Verificamos tu pago y, al aprobarlo, recibes tus tickets numerados\n"
    "4️⃣ Al cerrar el sorteo se elige al azar un ticket ganador entre los vendidos. "
    "El resultado se anuncia en @telerifa y el pote acumulado se paga al ganador\n\n"
    "Todos los tickets tienen la misma probabilidad. Mira tus tickets con /billetera y los últimos premios con /ganadores.\n\n"
    "<i>Juega con responsabilidad y solo donde las rifas sean legales para ti.</i>"
)


def app_keyboard(label: str, tab: str | None = None) -> InlineKeyboardMarkup:
    url = settings.web_app_url
    if tab:
        url += ("&" if "?" in url else "?") + f"tab={tab}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, web_app=WebAppInfo(url=url))]])


async def _pote_line() -> tuple[str, str | None]:
    """('🏆 <b>Pote Acumulado:</b> X Bs.', raffle_title) for the active raffle, or ('', None)."""
    async with SessionLocal() as s:
        try:
            raffle = await active_raffle(s)
            pote = await pot_ves(s, raffle.id)
        except Exception:      # no raffle / no rate yet
            return "", None
    return f"🏆 <b>Pote Acumulado:</b> {fmt_ves(pote)} Bs.", raffle.title


@router.message(CommandStart(), private)
async def cmd_start(message: Message) -> None:
    u = message.from_user
    async with SessionLocal() as session, session.begin():
        row = await session.get(User, u.id)
        if row is None:
            session.add(User(telegram_id=u.id, username=u.username))
        elif u.username and row.username != u.username:
            row.username = u.username
    name = f", {html.escape(u.first_name)}" if u.first_name else ""
    pote, _ = await _pote_line()
    text = WELCOME.format(name=name) + (f"\n\n{pote}" if pote else "")
    await message.answer(text, reply_markup=app_keyboard("🎟️ Abrir TeleRifa"))


@router.message(Command("billetera"), private)
async def cmd_billetera(message: Message) -> None:
    uid = message.from_user.id
    pote, title = await _pote_line()
    async with SessionLocal() as s:
        current: list[int] = []
        if title:
            raffle = await active_raffle(s)
            current = list((await s.execute(
                select(Ticket.ticket_number).where(Ticket.raffle_id == raffle.id, Ticket.user_id == uid)
                .order_by(Ticket.id)
            )).scalars().all())
        total = (await s.execute(select(func.count(Ticket.id)).where(Ticket.user_id == uid))).scalar_one()
        wins = (await s.execute(
            select(func.count(Ticket.id)).where(Ticket.user_id == uid, Ticket.status == TicketStatus.WON)
        )).scalar_one()
        pending = (await s.execute(
            select(func.count(Payment.id)).where(Payment.user_id == uid, Payment.status == PaymentStatus.PENDIENTE)
        )).scalar_one()

    lines = ["🎫 <b>Tu billetera TeleRifa</b>\n"]
    if title:
        if current:
            shown = ", ".join(format_ticket_number(n) for n in current[:15])
            extra = f" (+{len(current) - 15} más)" if len(current) > 15 else ""
            lines.append(f"<b>{html.escape(title)}</b>\nTienes {len(current)} ticket(s): {shown}{extra}")
        else:
            lines.append(f"Aún no tienes tickets en <b>{html.escape(title)}</b>.")
        lines.append(pote)
    else:
        lines.append("No hay un sorteo activo en este momento.")
    lines.append(f"\nTickets en total: <b>{total}</b> · Premios ganados: <b>{wins}</b> 🏆")
    if pending:
        lines.append(f"⏳ Pagos pendientes de aprobación: <b>{pending}</b>")
    await message.answer("\n".join(lines), reply_markup=app_keyboard("🎫 Abrir mi billetera", "wallet"))


@router.message(Command("ganadores"), private)
async def cmd_ganadores(message: Message) -> None:
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(DrawHistory).order_by(DrawHistory.draw_date.desc(), DrawHistory.id.desc()).limit(5)
        )).scalars().all()
    if not rows:
        await message.answer("🏆 Aún no hay sorteos realizados. ¡El primer ganador podrías ser tú!",
                             reply_markup=app_keyboard("🎟️ Comprar tickets"))
        return
    lines = ["🏆 <b>Últimos 5 ganadores</b>\n"]
    for i, w in enumerate(rows, 1):
        premio = f"{fmt_ves(w.prize_ves)} Bs." if w.prize_ves is not None else "—"
        lines.append(
            f"{i}. <b>{html.escape(w.winning_ticket_number or '—')}</b> — {html.escape(mask_username(w.winner_username))}\n"
            f"    💰 {premio} · {fmt_date_es(w.draw_date)}"
        )
    await message.answer("\n".join(lines), reply_markup=app_keyboard("🏆 Ver ganadores en la app", "winners"))


# ---- Admin: Aprobar / Rechazar -----------------------------------------------------------------------------

@router.callback_query(F.data.regexp(r"^pm:(ok|no):\d+$"))
async def on_payment_decision(cb: CallbackQuery) -> None:
    if not settings.admin_chat_id or cb.from_user.id != settings.admin_chat_id:
        await cb.answer("No autorizado.", show_alert=True)
        return
    _, action, pid = cb.data.split(":")
    payment, tickets, outcome = await decide_payment(int(pid), action == "ok", cb.from_user.id)

    if outcome == "NO_ENCONTRADO":
        await cb.answer("Pago no encontrado.", show_alert=True)
        return
    if outcome == "YA_PROCESADO":
        await cb.answer(f"Este pago ya fue procesado ({payment.status}).", show_alert=True)
        return

    if outcome == PaymentStatus.APROBADO and tickets:
        await notify_user(payment.user_id,
                          f"✅ <b>¡Pago aprobado!</b>\nTus tickets: {tickets_text(tickets)}\n¡Mucha suerte! 🍀")
        verdict = f"✅ <b>APROBADO</b> — {len(tickets)} ticket(s) asignados"
    elif outcome == PaymentStatus.APROBADO:      # raffle closed before approval
        await notify_user(payment.user_id,
                          "✅ Tu pago fue aprobado, pero el sorteo ya cerró y no pudimos asignarte tickets. "
                          "Te contactaremos para resolverlo.")
        verdict = "⚠️ <b>APROBADO pero SIN tickets</b> (sorteo cerrado): reembolsa al usuario"
    else:
        await notify_user(payment.user_id,
                          f"❌ Tu pago con referencia <code>{html.escape(payment.referencia)}</code> fue rechazado. "
                          "Verifica los datos del Pago Móvil e inténtalo de nuevo.")
        verdict = "❌ <b>RECHAZADO</b>"

    if isinstance(cb.message, Message):          # drop the buttons and stamp the decision on the alert
        await cb.message.edit_text(f"{cb.message.html_text}\n\n{verdict}", reply_markup=None)
    await cb.answer("Listo.")
