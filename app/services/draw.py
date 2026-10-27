"""Guaranteed-winner draw: always picks from tickets that were actually sold, using the OS CSPRNG."""
import logging
import secrets

from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import (
    DrawHistory, Raffle, RaffleStatus, Ticket, TicketStatus, User, format_ticket_number, utcnow,
)
from .pagomovil import pot_ves
from .telegram import post_winner_announcement

log = logging.getLogger("telerifa.draw")


class DrawError(Exception):
    pass


async def execute_raffle_draw(raffle_id: int) -> DrawHistory:
    # Txn 1: close sales. Once DRAWING, issue_tickets() refuses new tickets, so the ticket set is frozen.
    # Re-entrant: a raffle stuck in DRAWING (crash) is resumed.
    async with SessionLocal() as session, session.begin():
        raffle = (await session.execute(
            select(Raffle).where(Raffle.id == raffle_id).with_for_update()
        )).scalar_one_or_none()
        if raffle is None:
            raise DrawError(f"Sorteo {raffle_id} no existe")
        if raffle.status == RaffleStatus.COMPLETED:
            raise DrawError(f"Sorteo {raffle_id} ya fue completado")
        sold = (await session.execute(
            select(func.count(Ticket.id)).where(Ticket.raffle_id == raffle_id)
        )).scalar_one()
        if sold < 1:
            raise DrawError(f"Sorteo {raffle_id} no tiene tickets vendidos; no se sortea")
        raffle.status = RaffleStatus.DRAWING

    # Txn 2: pick the winner and persist everything atomically.
    async with SessionLocal() as session, session.begin():
        raffle = (await session.execute(
            select(Raffle).where(Raffle.id == raffle_id).with_for_update()
        )).scalar_one()
        tickets = (await session.execute(
            select(Ticket).where(Ticket.raffle_id == raffle_id, Ticket.status == TicketStatus.ACTIVE)
            .order_by(Ticket.id)
        )).scalars().all()
        if not tickets:
            raise DrawError("No hay tickets ACTIVE")

        winner_ticket = secrets.choice(tickets)           # CSPRNG, strictly from sold tickets
        winner_ticket.status = TicketStatus.WON

        user = await session.get(User, winner_ticket.user_id)
        username = (user.username if user and user.username else f"Player-{str(winner_ticket.user_id)[-4:]}")
        prize = await pot_ves(session, raffle_id)

        now = utcnow()
        raffle.winning_ticket_id = winner_ticket.id
        raffle.drawn_at = now
        raffle.prize_ves = prize
        raffle.status = RaffleStatus.COMPLETED

        record = DrawHistory(
            raffle_id=raffle.id,
            winning_ticket_number=format_ticket_number(winner_ticket.ticket_number),
            winner_username=username,
            prize_ves=prize,
            draw_date=now,
        )
        session.add(record)
        await session.flush()
        title, total = raffle.title, len(tickets)

    log.info("Sorteo %s: %s -> %s (%s Bs.)", raffle_id, record.winning_ticket_number, username, prize)
    await announce_winner(record.id, title, total)         # after commit: a Telegram outage can't undo the draw
    return record


async def announce_winner(draw_history_id: int, raffle_title: str, tickets_sold: int) -> bool:
    async with SessionLocal() as session:
        w = await session.get(DrawHistory, draw_history_id)
        if w is None or w.announced_at is not None:
            return False
        ok = await post_winner_announcement(w, raffle_title, tickets_sold)
        if ok:
            w.announced_at = utcnow()
            await session.commit()
        return ok
