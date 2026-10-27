"""Pago Móvil flow: quote validation, payment creation, admin decision, accumulated pot."""
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..models import Payment, PaymentStatus, Raffle, RaffleStatus, Ticket, utcnow
from .money import CENT, RATE_Q, calc_total_ves
from .rates import get_active_rate, latest_rates
from .tickets import issue_tickets

MAX_PENDING_PER_USER = 5


class NoActiveRaffle(Exception): ...
class TooManyPending(Exception): ...


class RateChanged(Exception):
    def __init__(self, current: Decimal):
        self.current = current


async def active_raffle(session: AsyncSession, raffle_id: int | None = None) -> Raffle:
    q = select(Raffle).where(Raffle.status == RaffleStatus.ACTIVE)
    q = q.where(Raffle.id == raffle_id) if raffle_id else q.order_by(Raffle.id.desc()).limit(1)
    raffle = (await session.execute(q)).scalars().first()
    if raffle is None:
        raise NoActiveRaffle
    return raffle


async def resolve_quoted_rate(session: AsyncSession, quoted: Decimal) -> Decimal:
    """The user pays the amount they were SHOWN. Accept the quoted rate if it is the current one, or the
    previous one shortly after a rate change (so a 17:00 update can't invalidate payments in flight)."""
    quoted = quoted.quantize(RATE_Q)
    rows = await latest_rates(session, 2)
    if not rows:
        current = (await get_active_rate(session)).rate        # env fallback (or RateUnavailable)
        if quoted == current.quantize(RATE_Q):
            return current
        raise RateChanged(current)
    cur = rows[0]
    if quoted == cur.rate_ves.quantize(RATE_Q):
        return cur.rate_ves
    if (len(rows) > 1 and quoted == rows[1].rate_ves.quantize(RATE_Q)
            and cur.fetched_at >= utcnow() - timedelta(minutes=settings.rate_grace_minutes)):
        return rows[1].rate_ves
    raise RateChanged(cur.rate_ves)


async def create_payment(session: AsyncSession, *, user_id: int, cantidad: int, referencia: str,
                         tasa_cotizada: Decimal, raffle_id: int | None) -> tuple[Payment, Raffle]:
    raffle = await active_raffle(session, raffle_id)
    pending = (await session.execute(
        select(func.count(Payment.id)).where(Payment.user_id == user_id, Payment.status == PaymentStatus.PENDIENTE)
    )).scalar_one()
    if pending >= MAX_PENDING_PER_USER:
        raise TooManyPending
    tasa = await resolve_quoted_rate(session, tasa_cotizada)
    payment = Payment(
        user_id=user_id, raffle_id=raffle.id, ticket_quantity=cantidad,
        amount_usd=(settings.ticket_price_usd * cantidad).quantize(CENT),
        rate_ves=tasa, amount_ves=calc_total_ves(cantidad, settings.ticket_price_usd, tasa),
        referencia=referencia, status=PaymentStatus.PENDIENTE,
    )
    session.add(payment)
    await session.flush()          # raises IntegrityError if the referencia is already registered for this raffle
    return payment, raffle


async def decide_payment(payment_id: int, approve: bool, admin_id: int) -> tuple[Payment | None, list[Ticket], str]:
    """Admin decision. Idempotent: a second press just reports YA_PROCESADO.
    Returns (payment, new_tickets, outcome) with outcome in APROBADO | RECHAZADO | YA_PROCESADO | NO_ENCONTRADO."""
    async with SessionLocal() as session, session.begin():
        payment = (await session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if payment is None:
            return None, [], "NO_ENCONTRADO"
        if payment.status != PaymentStatus.PENDIENTE:
            return payment, [], "YA_PROCESADO"
        payment.decided_at, payment.decided_by = utcnow(), admin_id
        tickets: list[Ticket] = []
        if approve:
            payment.status = PaymentStatus.APROBADO
            tickets = await issue_tickets(session, payment)
        else:
            payment.status = PaymentStatus.RECHAZADO
    return payment, tickets, payment.status


async def pot_ves(session: AsyncSession, raffle_id: int) -> Decimal:
    """Pote acumulado: the configured share of what was actually collected (approved payments, in Bs. at the
    rate each payment locked), plus any pre-Pago-Móvil tickets valued at today's price."""
    collected = Decimal((await session.execute(
        select(func.coalesce(func.sum(Payment.amount_ves), 0))
        .where(Payment.raffle_id == raffle_id, Payment.status == PaymentStatus.APROBADO)
    )).scalar_one())
    legacy = (await session.execute(
        select(func.count(Ticket.id)).where(Ticket.raffle_id == raffle_id, Ticket.payment_id.is_(None))
    )).scalar_one()
    if legacy:
        collected += calc_total_ves(legacy, settings.ticket_price_usd, (await get_active_rate(session)).rate)
    return (collected * settings.pot_percent / Decimal(100)).quantize(CENT, ROUND_DOWN)
