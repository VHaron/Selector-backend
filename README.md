# MVP Tournament Selector — Backend

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Deploy this folder as the Railway service root. Keep `TELEGRAM_BOT_TOKEN` in Railway Variables; it is optional for the HTTP API and must not be committed to source control.

## Source structure

- `main.py` — ASGI app, CORS, health endpoint, Telegram bot startup and router registration. The `main:app` entry point remains unchanged for deployment compatibility.
- `selector_backend/config.py` — site URLs, room IDs, TLS and logging configuration.
- `selector_backend/models.py` — request schemas.
- `selector_backend/state.py` — in-memory authenticated sessions, MFA state and site-selection cleanup.
- `selector_backend/storage.py` — JSON database paths, loading and atomic writes.
- `selector_backend/auth.py` — automatic GlobusGo/GlobusMVP login, site selection and MFA endpoints.
- `selector_backend/profile.py` — presets, settings, notes, history, grid and bankroll endpoints.
- `selector_backend/tournaments.py` — room ID mapping and tournament scraping.

All existing API routes remain under the same `/api/...` paths. GlobusGo profile keys remain unchanged; GlobusMVP data continues to use its own namespace.
