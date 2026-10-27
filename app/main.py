"""TeleRifa API (FastAPI) - Pago Móvil edition.  Run:  uvicorn app.main:app --port 8000

One origin serves everything: /api/* is the API and, when the Mini App has been built, "/" serves it
(so a single localtunnel URL -> port 8000 is enough for Telegram).
"""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .auth import TgUser, current_user
from .config import settings
from .db import SessionLocal
from .jobs import build_scheduler, ensure_fresh_rate
from .models import (
    DrawHistory, ExchangeRate, Payment, PaymentStatus, Raffle, RaffleStatus, Ticket, TicketStatus, User, utcnow,
)
from .services.links import mask_username
from .services.money import RATE_Q, calc_total_ves, fmt_ves
from .services.pagomovil import (
    NoActiveRaffle, RateChanged, TooManyPending, active_raffle, create_payment, pot_ves,
)
from .services.rates import RateUnavailable, get_active_rate
from .services.telegram import announce_new_raffle, send_admin_payment_alert

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telerifa.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic (`alembic upgrade head`), never by create_all, so existing data is untouched.
    scheduler = build_scheduler()
    scheduler.start()
    app.state.rate_task = asyncio.create_task(ensure_fresh_rate())   # catch up if the 17:00 job was missed
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="TeleRifa API", lifespan=lifespan)

# CORS: the configured Mini App origin, plus any https://*.loca.lt tunnel while developing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_app_origin],
    allow_origin_regex=r"^https://[a-z0-9-]+\.loca\.lt$" if settings.cors_allow_localtunnel else None,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "bypass-tunnel-reminder"],
)


def _require_admin(token: str) -> None:
    if not settings.admin_token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(403, "Prohibido")


@app.get("/api/health")
async def health():
    return {"ok": True}


# ---- Reads for the Mini App -------------------------------------------------------------------------

@app.get("/api/raffles/active")
async def get_active_raffle():
    async with SessionLocal() as session:
        try:
            raffle = await active_raffle(session)
        except NoActiveRaffle:
            raise HTTPException(404, "No hay un sorteo activo en este momento.")
        try:
            rate = (await get_active_rate(session)).rate
        except RateUnavailable:
            raise HTTPException(503, "La tasa del BCV no está disponible. Inténtalo más tarde.")
        sold = (await session.execute(
            select(func.count(Ticket.id)).where(Ticket.raffle_id == raffle.id)
        )).scalar_one()
        acumulado = await pot_ves(session, raffle.id)

    return {
        "id": raffle.id,
        "title": raffle.title,
        "tickets_vendidos": sold,
        "precio_usd": f"{settings.ticket_price_usd:.2f}",
        "tasa_bcv": str(rate.quantize(RATE_Q)),
        "precio_ticket_ves": str(calc_total_ves(1, settings.ticket_price_usd, rate)),
        "acumulado_ves": str(acumulado),               # only the pot is exposed, never gross sales
        "draw_at": raffle.draw_at.isoformat() if raffle.draw_at else None,
        "pago_movil": {
            "banco": settings.pago_movil_bank,
            "cedula": settings.pago_movil_id,
            "telefono": settings.pago_movil_phone,
        },
    }


@app.get("/api/me/tickets")
async def my_tickets(tg: TgUser = Depends(current_user)):
    prize_fallback = select(func.max(DrawHistory.prize_ves)).where(DrawHistory.raffle_id == Raffle.id).scalar_subquery()
    drawn_fallback = select(func.max(DrawHistory.draw_date)).where(DrawHistory.raffle_id == Raffle.id).scalar_subquery()
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Ticket, Raffle, prize_fallback, drawn_fallback)
            .join(Raffle, Raffle.id == Ticket.raffle_id)
            .where(Ticket.user_id == tg.id)
            .order_by((Ticket.status == TicketStatus.WON).desc(), Ticket.purchased_at.desc(), Ticket.id.desc())
            .limit(500)
        )).all()
    out = []
    for t, r, prize_dh, drawn_dh in rows:
        won = t.status == TicketStatus.WON
        prize = r.prize_ves if r.prize_ves is not None else prize_dh      # legacy winners: fall back to draw_history
        drawn = r.drawn_at or drawn_dh
        out.append({
            "id": t.id,
            "raffle_id": r.id,
            "raffle_title": r.title,
            "raffle_status": r.status,
            "ticket_number": t.display_number,
            "status": t.status,
            "purchased_at": t.purchased_at.isoformat(),
            "prize_ves": str(prize) if won and prize is not None else None,
            "drawn_at": drawn.isoformat() if won and drawn else None,
        })
    return out


@app.get("/api/winners")
async def past_winners():
    """Last 5 draws from draw_history (public, usernames masked). Older rows are kept, just not returned."""
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(DrawHistory).order_by(DrawHistory.draw_date.desc(), DrawHistory.id.desc()).limit(5)
        )).scalars().all()
    return [{
        "id": w.id,
        "raffle_id": w.raffle_id,
        "draw_date": w.draw_date.isoformat(),
        "winning_ticket_number": w.winning_ticket_number,
        "winner_username_masked": mask_username(w.winner_username),
        "prize_ves": str(w.prize_ves) if w.prize_ves is not None else None,
    } for w in rows]


# ---- Pago Móvil ---------------------------------------------------------------------------------------

class PagoMovilIn(BaseModel):
    user_id: int = Field(description="Telegram user id (must match the signed initData)")
    cantidad_tickets: int = Field(ge=1, le=settings.max_tickets_per_purchase)
    referencia: str = Field(pattern=r"^\d{6}$")
    tasa_bcv: Decimal = Field(gt=0, description="Rate the user was shown; the amount is computed with it")
    raffle_id: int | None = None


@app.post("/api/pay/pagomovil/submit", status_code=201)
async def submit_pago_movil(body: PagoMovilIn, tg: TgUser = Depends(current_user)):
    if body.user_id != tg.id:
        raise HTTPException(403, "El usuario no coincide con la sesión de Telegram.")
    try:
        async with SessionLocal() as session, session.begin():
            row = await session.get(User, tg.id)
            if row is None:
                session.add(User(telegram_id=tg.id, username=tg.username))
            elif tg.username and row.username != tg.username:
                row.username = tg.username
            payment, raffle = await create_payment(
                session, user_id=tg.id, cantidad=body.cantidad_tickets, referencia=body.referencia,
                tasa_cotizada=body.tasa_bcv, raffle_id=body.raffle_id,
            )
    except NoActiveRaffle:
        raise HTTPException(404, "No hay un sorteo activo en este momento.")
    except TooManyPending:
        raise HTTPException(429, "Tienes varios pagos pendientes de aprobación. Espera a que se revisen.")
    except RateChanged as e:
        raise HTTPException(409, {"code": "RATE_CHANGED",
                                  "message": f"La tasa del BCV cambió a {fmt_ves(e.current)} Bs/US$. Revisa el nuevo monto."})
    except RateUnavailable:
        raise HTTPException(503, "La tasa del BCV no está disponible. Inténtalo más tarde.")
    except IntegrityError:
        raise HTTPException(409, "Esa referencia ya fue registrada para este sorteo.")

    # Alert the admin AFTER commit so a Telegram hiccup never loses the payment.
    msg_id = await send_admin_payment_alert(payment, tg.username, raffle.title)
    if msg_id:
        async with SessionLocal() as session, session.begin():
            p = await session.get(Payment, payment.id)
            p.admin_message_id = msg_id
    return {"payment_id": payment.id, "status": payment.status, "total_ves": str(payment.amount_ves)}


@app.get("/api/pay/{payment_id}")
async def payment_status(payment_id: int, tg: TgUser = Depends(current_user)):
    """Polled by the Mini App until the admin approves/rejects."""
    async with SessionLocal() as session:
        p = await session.get(Payment, payment_id)
        if p is None or p.user_id != tg.id:
            raise HTTPException(404, "Pago no encontrado.")
        issued = (await session.execute(
            select(func.count(Ticket.id)).where(Ticket.payment_id == p.id)
        )).scalar_one()
    return {"payment_id": p.id, "status": p.status, "tickets_issued": issued > 0}


# ---- Admin --------------------------------------------------------------------------------------------

class RateIn(BaseModel):
    tasa_bcv: Decimal = Field(gt=0, max_digits=14, decimal_places=4)


@app.post("/api/admin/rate")
async def set_rate(body: RateIn, x_admin_token: str = Header(default="")):
    """Manual override of the USD/VES rate. It stays active until the next successful BCV fetch."""
    _require_admin(x_admin_token)
    async with SessionLocal() as session, session.begin():
        session.add(ExchangeRate(rate_ves=body.tasa_bcv, source="MANUAL"))
    return {"ok": True, "tasa_bcv": str(body.tasa_bcv)}


class RaffleIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    draw_at: datetime | None = None


@app.post("/api/admin/raffles", status_code=201)
async def create_raffle(body: RaffleIn, x_admin_token: str = Header(default="")):
    """Open a raffle and announce it in @telerifa with a one-tap 'Comprar Tickets' deep link."""
    _require_admin(x_admin_token)
    draw_at = body.draw_at
    if draw_at is not None:
        draw_at = draw_at if draw_at.tzinfo else draw_at.replace(tzinfo=timezone.utc)
        if draw_at <= utcnow():
            raise HTTPException(422, "draw_at debe estar en el futuro")
    async with SessionLocal() as session, session.begin():
        if (await session.execute(select(Raffle.id).where(Raffle.status == RaffleStatus.ACTIVE).limit(1))).first():
            raise HTTPException(409, "Ya hay un sorteo ACTIVE")
        try:
            rate = (await get_active_rate(session)).rate
        except RateUnavailable:
            raise HTTPException(503, "Define la tasa primero (POST /api/admin/rate)")
        raffle = Raffle(title=body.title.strip(), draw_at=draw_at)
        session.add(raffle)
    announced = await announce_new_raffle(raffle, calc_total_ves(1, settings.ticket_price_usd, rate))
    return {"id": raffle.id, "announced": announced}


# ---- Mini App static files (must be mounted LAST so /api/* routes win) -----------------------------------
_dist = Path(settings.miniapp_dist)
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="miniapp")
else:
    log.warning("Mini App build not found at %s - run `npm run build` in the miniapp project", _dist.resolve())
