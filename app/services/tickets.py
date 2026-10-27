"""Ticket issuing - the ONE place tickets are created (called when an admin approves a payment)."""
from sqlalchemy import Integer, String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Payment, Raffle, RaffleStatus, Ticket, TicketStatus


async def next_ticket_number(session: AsyncSession, raffle_id: int) -> int:
    """Highest existing number + 1, whether the column stores '#0042' text or an integer."""
    digits = func.substring(cast(Ticket.ticket_number, String), r"[0-9]+")
    last = (await session.execute(
        select(func.coalesce(func.max(cast(digits, Integer)), 0)).where(Ticket.raffle_id == raffle_id)
    )).scalar_one()
    return last + 1


async def issue_tickets(session: AsyncSession, payment: Payment) -> list[Ticket]:
    """Create the tickets for an APROBADO payment. Caller commits.

    Locks the raffle row so ticket numbers stay unique and the draw can't start mid-issue.
    If the raffle already closed, no tickets are created and the payment is flagged for a manual refund.
    """
    raffle = (await session.execute(
        select(Raffle).where(Raffle.id == payment.raffle_id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one()

    if raffle.status != RaffleStatus.ACTIVE:
        payment.note = "Aprobado después del cierre del sorteo: NO se asignaron tickets, reembolsar."
        return []

    first = await next_ticket_number(session, raffle.id)
    tickets = [
        Ticket(raffle_id=raffle.id, user_id=payment.user_id, payment_id=payment.id,
               ticket_number=first + i, status=TicketStatus.ACTIVE)
        for i in range(payment.ticket_quantity)
    ]
    session.add_all(tickets)
    await session.flush()
    return tickets
