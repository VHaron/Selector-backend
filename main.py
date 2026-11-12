import os
import threading
import uvicorn
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from selector_backend.auth import router as auth_router
from selector_backend.profile import router as profile_router
from selector_backend.tournaments import router as tournaments_router
from pydantic import BaseModel
from typing import List, Optional

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
    allow_headers=["Content-Type", "Authorization"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(tournaments_router)

if __name__ == "__main__":
    if bot:
        threading.Thread(target=lambda: bot.infinity_polling(none_stop=True), daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    class SmartGridRequest(BaseModel):
    session_token: str
    budget: float
    max_tables: int
    start_time: str
    end_time: str
    rooms: List[str]

@app.post("/api/build_smart_grid")
async def api_build_smart_grid(req: SmartGridRequest):
    user_id = state.sessions.get(req.session_token)
    if not user_id:
        return {"status": "error", "detail": "Invalid token", "status_code": 401}
        
    # 1. Получаем все доступные турниры по фильтрам румов (используем уже готовую функцию бэкенда)
    # Например: all_tournaments = tournaments.get_tournaments(rooms=req.rooms)
    all_tournaments = [] # ЗАМЕНИ НА РЕАЛЬНЫЙ ВЫЗОВ ФУНКЦИИ ИЗ БАЗЫ
    
    # 2. Прогоняем через математический оптимизатор
    optimal_grid = build_smart_grid(
        tournaments=all_tournaments,
        budget=req.budget,
        max_tables=req.max_tables,
        start_time_str=req.start_time,
        end_time_str=req.end_time
    )
    
    return {"status": "success", "grid": optimal_grid}
