"""SQLAlchemy models mapped onto the EXISTING database.

users / tickets / draw_history already exist, so these mappings use their column names and are never
created or altered by the app (schema changes go through Alembic, additive only - see alembic/versions).
Status columns are plain strings so legacy values can never break reads.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def format_ticket_number(n: int) -> str:
    """42 -> '#0042'"""
    return f"#{n:04d}"


class RaffleStatus:
    ACTIVE, DRAWING, COMPLETED = "ACTIVE", "DRAWING", "COMPLETED"


class TicketStatus:
    ACTIVE, WON = "ACTIVE", "WON"


class PaymentStatus:
    PENDIENTE = "PENDIENTE_APROBACION"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


class TicketNumber(TypeDecorator):
    """Python side is always an int. The column may be VARCHAR '#0042' or INTEGER 42 (TICKET_NUMBER_STORAGE)."""

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Integer() if settings.ticket_number_storage == "int" else String(16))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int(value) if settings.ticket_number_storage == "int" else format_ticket_number(int(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        digits = re.sub(r"\D", "", str(value))
        return int(digits) if digits else 0


TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(TS, default=utcnow, server_default=func.now())


class Raffle(Base):
    __tablename__ = "raffles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=RaffleStatus.ACTIVE, index=True)
    winning_ticket_id: Mapped[int | None] = mapped_column(Integer)
    draw_at: Mapped[datetime | None] = mapped_column(TS)
    drawn_at: Mapped[datetime | None] = mapped_column(TS)
    prize_ves: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    raffle_id: Mapped[int] = mapped_column(ForeignKey("raffles.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id"), index=True)
    ticket_number: Mapped[int] = mapped_column(TicketNumber)
    status: Mapped[str] = mapped_column(String(16), default=TicketStatus.ACTIVE)
    purchased_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    payment_id: Mapped[int | None] = mapped_column(Integer)   # NULL for tickets that predate Pago Móvil

    @property
    def display_number(self) -> str:
        return format_ticket_number(self.ticket_number)


class DrawHistory(Base):
    """Past winners log. Rows are never deleted: the API/bot just read the 5 most recent."""
    __tablename__ = "draw_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    raffle_id: Mapped[int | None] = mapped_column(Integer)
    winning_ticket_number: Mapped[str | None] = mapped_column(String(16))
    winner_username: Mapped[str | None] = mapped_column(String(64))
    prize_ves: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    draw_date: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    announced_at: Mapped[datetime | None] = mapped_column(TS)


class Payment(Base):
    """A Pago Móvil payment awaiting / after admin review. Money is always in Bolívares."""
    __tablename__ = "payments"
    __table_args__ = (
        Index("uq_payment_ref_per_raffle", "raffle_id", "referencia", unique=True,
              postgresql_where=text("status <> 'RECHAZADO'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    raffle_id: Mapped[int] = mapped_column(ForeignKey("raffles.id"), index=True)
    ticket_quantity: Mapped[int] = mapped_column(Integer)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    rate_ves: Mapped[Decimal] = mapped_column(Numeric(18, 4))      # BCV rate locked at purchase time
    amount_ves: Mapped[Decimal] = mapped_column(Numeric(18, 2))    # total_ves the user must pay
    referencia: Mapped[str] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(24), default=PaymentStatus.PENDIENTE)
    note: Mapped[str | None] = mapped_column(Text)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)


class ExchangeRate(Base):
    """USD/VES rate history. The newest row is the active rate."""
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_ves: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String(16))   # BCV | MANUAL
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow, server_default=func.now())
