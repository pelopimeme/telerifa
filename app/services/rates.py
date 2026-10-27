"""USD/VES exchange-rate storage and BCV fetching.   Probe the live feed:  python -m app.services.rates"""
import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ExchangeRate
from .money import RATE_Q, extract_rate


class RateUnavailable(Exception):
    """No rate in the database and no USD_VES_RATE fallback configured."""


@dataclass
class ActiveRate:
    rate: Decimal
    source: str
    fetched_at: datetime | None


async def latest_rates(session: AsyncSession, n: int = 2) -> list[ExchangeRate]:
    return list((await session.execute(
        select(ExchangeRate).order_by(ExchangeRate.id.desc()).limit(n)
    )).scalars().all())


async def get_active_rate(session: AsyncSession) -> ActiveRate:
    """Newest stored rate; falls back to USD_VES_RATE when the database has none (BCV offline on day one)."""
    rows = await latest_rates(session, 1)
    if rows:
        return ActiveRate(rows[0].rate_ves, rows[0].source, rows[0].fetched_at)
    if settings.usd_ves_rate:
        return ActiveRate(settings.usd_ves_rate.quantize(RATE_Q), "ENV", None)
    raise RateUnavailable("No exchange rate available")


async def fetch_bcv_payload():
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(settings.bcv_rate_url, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


async def _probe() -> None:
    payload = await fetch_bcv_payload()
    print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])
    print("\nParsed USD/VES rate:", extract_rate(payload))


if __name__ == "__main__":
    asyncio.run(_probe())
