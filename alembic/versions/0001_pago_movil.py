"""Pago Móvil edition - ADDITIVE ONLY on top of the existing database.

Existing rows in users / tickets / draw_history / appscheduler_jobs are never modified or deleted:
  * missing tables are created (CREATE TABLE only when absent)
  * missing columns are added with ADD COLUMN IF NOT EXISTS
  * column DEFAULTs are set so inserts from this app always succeed
The APScheduler table (appscheduler_jobs) is left to APScheduler itself.
"""
import sqlalchemy as sa
from alembic import op

from app.config import settings

revision = "0001_pago_movil"
down_revision = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    num_type = "INTEGER" if settings.ticket_number_storage == "int" else "VARCHAR(16)"

    # ---------------- users ----------------
    if "users" not in existing:
        op.execute(
            "CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, username VARCHAR(64), "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    else:
        op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
        op.execute("ALTER TABLE users ALTER COLUMN created_at SET DEFAULT now()")

    # ---------------- tickets ----------------
    if "tickets" not in existing:
        op.execute(
            "CREATE TABLE tickets (id SERIAL PRIMARY KEY, raffle_id INTEGER NOT NULL, user_id BIGINT NOT NULL, "
            f"ticket_number {num_type} NOT NULL, status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', "
            "purchased_at TIMESTAMPTZ NOT NULL DEFAULT now(), payment_id INTEGER)"
        )
    else:
        op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS payment_id INTEGER")
        op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS raffle_id INTEGER")
        op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS user_id BIGINT")
        op.execute(f"ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ticket_number {num_type}")
        op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS purchased_at TIMESTAMPTZ NOT NULL DEFAULT now()")

    try:
        op.execute("ALTER TABLE tickets ALTER COLUMN purchase_time SET DEFAULT now()")
    except Exception:
        pass
    try:
        op.execute("ALTER TABLE tickets ALTER COLUMN purchased_at SET DEFAULT now()")
    except Exception:
        pass

    op.execute("ALTER TABLE tickets ALTER COLUMN status SET DEFAULT 'ACTIVE'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_raffle_user ON tickets (raffle_id, user_id)")

    # ---------------- raffles ----------------
    if "raffles" not in existing:
        op.execute(
            "CREATE TABLE raffles (id SERIAL PRIMARY KEY, title VARCHAR(200) NOT NULL, "
            "status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', winning_ticket_id INTEGER, draw_at TIMESTAMPTZ, "
            "drawn_at TIMESTAMPTZ, prize_ves NUMERIC(18,2), created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )

    # ---------------- draw_history ----------------
    if "draw_history" not in existing:
        op.execute(
            "CREATE TABLE draw_history (id SERIAL PRIMARY KEY, raffle_id INTEGER, winning_ticket_number VARCHAR(16), "
            "winner_username VARCHAR(64), prize_ves NUMERIC(18,2), draw_date TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "announced_at TIMESTAMPTZ)"
        )
    else:
        for col in (
            "raffle_id INTEGER",
            "winning_ticket_number VARCHAR(16)",
            "winner_username VARCHAR(64)",
            "prize_ves NUMERIC(18,2)",
            "draw_date TIMESTAMPTZ NOT NULL DEFAULT now()",
            "announced_at TIMESTAMPTZ",
        ):
            op.execute(f"ALTER TABLE draw_history ADD COLUMN IF NOT EXISTS {col}")
        op.execute("ALTER TABLE draw_history ALTER COLUMN draw_date SET DEFAULT now()")

    # ---------------- payments (Pago Móvil) ----------------
    if "payments" not in existing:
        op.execute(
            "CREATE TABLE payments (id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, "
            "raffle_id INTEGER NOT NULL REFERENCES raffles(id), ticket_quantity INTEGER NOT NULL, "
            "amount_usd NUMERIC(12,2) NOT NULL, rate_ves NUMERIC(18,4) NOT NULL, amount_ves NUMERIC(18,2) NOT NULL, "
            "referencia VARCHAR(6) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'PENDIENTE_APROBACION', "
            "note TEXT, admin_message_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "decided_at TIMESTAMPTZ, decided_by BIGINT)"
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_ref_per_raffle ON payments (raffle_id, referencia) "
        "WHERE status <> 'RECHAZADO'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_user_status ON payments (user_id, status)")

    # ---------------- exchange_rates ----------------
    if "exchange_rates" not in existing:
        op.execute(
            "CREATE TABLE exchange_rates (id SERIAL PRIMARY KEY, rate_ves NUMERIC(18,4) NOT NULL, "
            "source VARCHAR(16) NOT NULL, fetched_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )


def downgrade() -> None:
    pass