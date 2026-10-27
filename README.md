# TeleRifa backend (FastAPI) - Pago Móvil edition

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # fill in secrets + your localtunnel URL
python -m scripts.inspect_db         # compare YOUR schema with the models (read-only)
alembic upgrade head                 # additive migration: never deletes/alters existing rows
uvicorn app.main:app --port 8000     # API + Mini App (if built)
python -m app.bot.run                # bot (second terminal)
lt --port 8000 --subdomain your-subdomain   # localtunnel (third terminal)
```
Open raffle:  `curl -X POST $APP_BASE_URL/api/admin/raffles -H "X-Admin-Token: …" -H "Content-Type: application/json" -d '{"title":"Sorteo semanal"}'`
Manual rate:  `curl -X POST $APP_BASE_URL/api/admin/rate -H "X-Admin-Token: …" -H "Content-Type: application/json" -d '{"tasa_bcv":"36.5000"}'`
Probe the BCV feed (prints raw JSON + parsed rate): `python -m app.services.rates`
