import os
import threading
import uvicorn
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from selector_backend.local_session import router as local_session_router
from selector_backend.profile import router as profile_router
from selector_backend.tournaments import router as tournaments_router
from selector_backend.smart_grid_api import router as smart_grid_router
from selector_backend.ipoker_catalog import router as ipoker_catalog_router

# Keep Telegram credentials in Railway environment variables.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

if bot:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        markup = InlineKeyboardMarkup()
        webapp_info = WebAppInfo(url="https://mvp-selector-bot-frontend.vercel.app")
        markup.add(InlineKeyboardButton("🚀 Открыть селектор MVP", web_app=webapp_info))
        bot.send_message(message.chat.id, "Привет! Селектор готов к работе:", reply_markup=markup)

app = FastAPI(title="MVP Selector API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Parser-Admin-Token"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(local_session_router)
app.include_router(profile_router)
app.include_router(tournaments_router)
app.include_router(smart_grid_router)
app.include_router(ipoker_catalog_router)

if __name__ == "__main__":
    if bot:
        threading.Thread(target=lambda: bot.infinity_polling(none_stop=True), daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


# Start the shared iPoker collector automatically. The public selector only reads
# the normalized catalogue; upstream credentials stay exclusively in Railway.
def _start_ipoker_collector():
    import logging
    from selector_backend.ipoker_catalog import _worker
    from selector_backend.ipoker_login import LoginProtocolError, build_auth_message, login_and_get_temp_token

    logger = logging.getLogger("ipoker.autostart")
    if os.getenv("IPOKER_AUTO_START", "1").strip().lower() in {"0", "false", "no", "off"}:
        logger.info("iPoker collector auto-start is disabled")
        return
    if not os.getenv("IPOKER_ADMIN_USERNAME") or not os.getenv("IPOKER_ADMIN_PASSWORD"):
        logger.warning("iPoker collector not started: Railway login variables are missing")
        return
    try:
        login = login_and_get_temp_token()
        result = _worker.start(build_auth_message(login), login.get("cookie_header"))
        logger.info("iPoker collector start requested: %s", result.get("status", "unknown"))
    except LoginProtocolError as exc:
        # LoginProtocolError messages are deliberately sanitized by the login module.
        logger.error("iPoker collector login failed: %s", str(exc))
    except (RuntimeError, ValueError) as exc:
        logger.error("iPoker collector could not start: %s", type(exc).__name__)
    except Exception as exc:
        logger.error("iPoker collector startup failed: %s", type(exc).__name__)


@app.on_event("startup")
def launch_ipoker_collector_on_startup():
    threading.Thread(target=_start_ipoker_collector, daemon=True, name="ipoker-login-autostart").start()
