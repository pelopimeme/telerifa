import os
from dotenv import load_dotenv

load_dotenv()

"""Central configuration (env / .env) - Pago Móvil + BCV edition."""
from decimal import Decimal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Existing PostgreSQL database ---
    database_url: str = os.getenv("DATABASE_URL", "")
    ticket_number_storage: str = "text"   # "text" => '#0042' | "int" => 42  (must match tickets.ticket_number)

    # --- URLs (localtunnel in dev) ---
    app_base_url: str = "https://telerifa-miniapp.pelopimeme.workers.dev/"
    miniapp_url: str = "https://telerifa-miniapp.pelopimeme.workers.dev/"                 # defaults to app_base_url
    miniapp_origin: str = "https://telerifa-miniapp.pelopimeme.workers.dev/"              # defaults to miniapp_url
    cors_allow_localtunnel: bool = False   # allow any https://*.loca.lt origin (dev only)
    miniapp_dist: str = "../telerifa-miniapp/dist"

    # --- Telegram ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_channel: str = "@telerifa"
    bot_username: str = "TeleRifa_bot"
    miniapp_short_name: str = "app"
    admin_chat_id: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
    admin_token: str = ""                 # protects /api/admin/*
    announce_full_handle: bool = True

    # --- Pricing / Pago Móvil ---
    ticket_price_usd: Decimal = Decimal("1.00")
    usd_ves_rate: Decimal | None = None   # fallback when BCV is unreachable and the DB has no rate yet
    pago_movil_bank: str = "0102"
    pago_movil_id: str = os.getenv("PAGO_MOVIL_ID", "")
    pago_movil_phone: str = os.getenv("PAGO_MOVIL_PHONE", "")
    max_tickets_per_purchase: int = 100
    pot_percent: Decimal = Decimal("80")  # internal only - never shown to users
    rate_grace_minutes: int = 120         # a quote made with the previous rate stays valid this long after a rate change

    # --- BCV ---
    bcv_rate_url: str = "https://rates.dolarvzla.com/bcv/current.json"
    bcv_max_change_pct: Decimal = Decimal("30")   # reject scraped rates that jump more than this vs the last one

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, v: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @field_validator("usd_ves_rate", mode="before")
    @classmethod
    def _blank_rate(cls, v):
        return None if v in ("", None) else v

    @property
    def sync_database_url(self) -> str:   # APScheduler's SQLAlchemy job store needs a sync driver
        return self.database_url.replace("+asyncpg", "+psycopg2", 1)

    @property
    def web_app_url(self) -> str:
        return (self.miniapp_url or self.app_base_url).rstrip("/")

    @property
    def web_app_origin(self) -> str:
        return (self.miniapp_origin or self.web_app_url).rstrip("/")


settings = Settings()
