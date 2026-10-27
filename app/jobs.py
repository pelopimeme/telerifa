"""Scheduled tasks (APScheduler). Jobs persist in the existing `appscheduler_jobs` table.

Run a SINGLE app instance: one scheduler is the simplest way to avoid duplicate runs.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import ExchangeRate, Raffle, RaffleStatus, utcnow
from .services.draw import DrawError, execute_raffle_draw
from .services.money import RATE_Q, extract_rate, fmt_ves
from .services.rates import fetch_bcv_payload, latest_rates
from .services.telegram import alert_admin

log = logging.getLogger("telerifa.jobs")


async def fetch_bcv_rate() -> None:
    """Daily: store the official BCV USD/VES rate. On any failure the previous rate (or USD_VES_RATE) stays active."""
    try:
        rate = extract_rate(await fetch_bcv_payload())
        if rate is None:
            raise ValueError("no se encontró la tasa USD en la respuesta")
    except Exception as exc:
        log.exception("BCV fetch failed")
        await alert_admin(f"⚠️ No se pudo obtener la tasa BCV ({exc}). Se mantiene la tasa actual. "
                          "Puedes fijarla con POST /api/admin/rate.")
        return

    async with SessionLocal() as session, session.begin():
        last = (await latest_rates(session, 1) or [None])[0]
        if last is not None:
            change = abs(rate / last.rate_ves - 1) * 100
            if change > settings.bcv_max_change_pct:      # guard against a bad scrape changing every price
                log.error("BCV rate %s rejected (%.1f%% vs %s)", rate, change, last.rate_ves)
                await alert_admin(f"⚠️ Tasa BCV rechazada por variación sospechosa: {fmt_ves(rate)} vs "
                                  f"{fmt_ves(last.rate_ves)} Bs/US$. Revísala y fíjala manualmente si es correcta.")
                return
            if last.rate_ves.quantize(RATE_Q) == rate:
                return                                   # unchanged: keep history clean
        session.add(ExchangeRate(rate_ves=rate, source="BCV"))
    log.info("Tasa BCV actualizada: %s", rate)


async def ensure_fresh_rate() -> None:
    """Startup catch-up: if no rate was stored in the last 26h, try BCV now (the cron may have been missed)."""
    async with SessionLocal() as session:
        last = (await latest_rates(session, 1) or [None])[0]
    if last is None or last.fetched_at < utcnow() - timedelta(hours=26):
        await fetch_bcv_rate()


async def draw_due_raffles() -> None:
    """Draw every raffle whose draw_at has passed (also resumes ones stuck in DRAWING)."""
    async with SessionLocal() as s:
        ids = (await s.execute(
            select(Raffle.id).where(
                Raffle.status.in_([RaffleStatus.ACTIVE, RaffleStatus.DRAWING]),
                Raffle.draw_at.is_not(None), Raffle.draw_at <= utcnow(),
            )
        )).scalars().all()
    for rid in ids:
        try:
            await execute_raffle_draw(rid)
        except DrawError as e:
            log.info("Sorteo %s omitido: %s", rid, e)
        except Exception:
            log.exception("Falló el sorteo %s", rid)


def build_scheduler() -> AsyncIOScheduler:
    sched = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.sync_database_url, tablename="appscheduler_jobs")},
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    # 17:00 hora de Venezuela (VET = UTC-4, i.e. 21:00 UTC). BCV publishes the next business day's rate around then.
    sched.add_job(fetch_bcv_rate, CronTrigger(hour=17, minute=0, timezone="America/Caracas"),
                  id="bcv_rate_daily", replace_existing=True)
    sched.add_job(draw_due_raffles, "interval", seconds=60, id="draw_due_raffles", replace_existing=True)
    return sched
