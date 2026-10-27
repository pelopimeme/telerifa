"""Compare the REAL database schema with what app/models.py expects.   python -m scripts.inspect_db"""
import asyncio

from sqlalchemy import text

from app.db import engine

OK_TEXT = ("character varying", "text")
OK_TS = ("timestamp with time zone",)
EXPECTED = {
    "users": {"telegram_id": ("bigint",), "username": OK_TEXT},
    "tickets": {"id": ("integer", "bigint"), "raffle_id": ("integer", "bigint"), "user_id": ("bigint",),
                "ticket_number": OK_TEXT + ("integer",), "status": OK_TEXT + ("USER-DEFINED",), "purchased_at": OK_TS},
    "draw_history": {"raffle_id": ("integer", "bigint"), "winning_ticket_number": OK_TEXT,
                     "winner_username": OK_TEXT, "prize_ves": ("numeric",), "draw_date": OK_TS},
}


async def main() -> None:
    async with engine.connect() as c:
        for table, expected in EXPECTED.items():
            rows = (await c.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = :t ORDER BY ordinal_position"
            ), {"t": table})).all()
            print(f"\n== {table} ==" if rows else f"\n== {table} == (does not exist yet; the migration will create it)")
            have = dict(rows)
            for name, typ in have.items():
                print(f"  {name:24} {typ}")
            for col, ok in expected.items():
                if rows and col not in have:
                    print(f"  ! missing column '{col}' (migration adds it if it is one of the new columns)")
                elif col in have and have[col] not in ok:
                    print(f"  ! '{col}' is {have[col]}, models expect one of {ok}")
                    if have[col] == "timestamp without time zone":
                        print("    -> convert: ALTER TABLE %s ALTER COLUMN %s TYPE timestamptz USING %s AT TIME ZONE 'UTC';" % (table, col, col))
            if table == "tickets" and have.get("ticket_number") == "integer":
                print("  -> ticket_number is INTEGER: set TICKET_NUMBER_STORAGE=int in .env")
    await engine.dispose()


asyncio.run(main())
