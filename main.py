import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import requests
from bs4 import BeautifulSoup
import urllib3
import os
import threading
import uvicorn
import uuid
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any

# force deploy

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8853116642:AAHCRMXQXowMsN9BdMShfxWltWqspo2pY1A")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    webapp_info = WebAppInfo(url="https://mvp-selector-bot-frontend.vercel.app")
    markup.add(InlineKeyboardButton("🚀 Открыть селектор MVP", web_app=webapp_info))
    bot.send_message(message.chat.id, "Привет! Селектор готов к работе:", reply_markup=markup)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="MVP Selector API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# === БАЗЫ ДАННЫХ (в JSON) ===
sessions: dict[str, requests.Session] = {}
session_to_user: dict[str, str] = {} 

def get_db_path(filename):
    return f"/data/{filename}" if os.path.exists("/data") else filename

PRESETS_FILE = get_db_path("presets_db.json")
SETTINGS_FILE = get_db_path("settings_db.json")
NOTES_FILE = get_db_path("notes_db.json")
SESSIONS_HISTORY_FILE = get_db_path("sessions_history_db.json")
GRID_FILE = get_db_path("grid_db.json")
BALANCES_FILE = get_db_path("balances_db.json")

def load_json_db(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_json_db(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

user_presets = load_json_db(PRESETS_FILE)
user_settings = load_json_db(SETTINGS_FILE)
user_notes = load_json_db(NOTES_FILE)
user_sessions_history = load_json_db(SESSIONS_HISTORY_FILE)
user_grid = load_json_db(GRID_FILE)
user_balances = load_json_db(BALANCES_FILE)

ROOMS = {
    '888Poker': '244', 'Chico': '245', 'GGNetwork': '246',
    'iPoker': '247', 'PartyPoker': '248', 'PokerStars': '249',
    'PokerStars(FR-ES-PT)': '250', 'Winamax.fr': '251', 'WPN': '252',
    'Pokerdom': '264', 'RPTBet': '262', 'CoinPoker': '265'
}

# === СХЕМЫ ===
class BaseAuthReq(BaseModel): session_token: str
class LoginRequest(BaseModel): username: str; password: str
class MFARequest(BaseAuthReq): otp: str

class StructsFilter(BaseModel):
    isFreezeout: str = '1'; isPko: str = '1'; isTurbo: str = '1'
    isHyperTurbo: str = '1'; is6MaxTurboPko: str = '1'; isSeries: str = '1'
class PrioritiesFilter(BaseModel): priority1: str = '1'; priority2: str = '1'; priority3: str = '1'

class FiltersRequest(BaseAuthReq):
    date_from: str = ''; date_to: str = ''
    rooms: list[str] = list(ROOMS.values())
    structs: StructsFilter = StructsFilter()
    priorities: PrioritiesFilter = PrioritiesFilter()

class PresetData(BaseModel): name: str; items: List[Dict[str, Any]]
class SavePresetsRequest(BaseAuthReq): presets: List[PresetData]
class SaveSettingsRequest(BaseAuthReq):
    selected_rooms: list[str]
    active_formats: dict = {"isPko": True, "isFreezeout": True, "isTurbo": True}
    is_compact: bool = False
    sort_order: str = "time"
class SaveNotesRequest(BaseAuthReq): notes: dict
class SessionData(BaseModel): id: str; date: str; total_buyin: float; total_cashes: float; profit: float; tournaments_count: int
class SaveSessionHistoryRequest(BaseAuthReq): session_data: SessionData
class ClearSessionHistoryRequest(BaseAuthReq): pass
class SaveGridRequest(BaseAuthReq): grid: List[Dict[str, Any]]
class SaveBalancesRequest(BaseAuthReq): balances: Dict[str, float]

def format_date_for_target(dt_str: str) -> str:
    if not dt_str: return ""
    try:
        clean_str = dt_str.replace("T", " ")
        date_part, time_part = clean_str.split(" ")
        y, m, d = date_part.split("-")
        return f"{d}.{m}.{y} {time_part}"
    except: 
        return dt_str.replace("T", " ")

def get_user(token: str):
    if token not in sessions: raise HTTPException(status_code=401, detail="Сессия не найдена.")
    user = session_to_user.get(token)
    if not user: raise HTTPException(status_code=400, detail="Пользователь не найден")
    return user

# === ЭНДПОИНТЫ АВТОРИЗАЦИИ ===
@app.post("/api/login")
def login(req: LoginRequest):
    user_session = requests.Session()
    user_session.verify = False 
    user_session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://globusgo.pro/site/login'})
    try:
        response = user_session.get("https://globusgo.pro/site/login")
        soup = BeautifulSoup(response.text, 'html.parser')
        csrf_value = soup.find('input', {'name': '_csrf-backend'}).get('value')
        post_response = user_session.post("https://globusgo.pro/site/login", data={'_csrf-backend': csrf_value, 'LoginForm[username]': req.username, 'LoginForm[password]': req.password, 'LoginForm[rememberMe]': '1'})
        token = str(uuid.uuid4())
        sessions[token] = user_session
        session_to_user[token] = req.username
        
        if post_response.url == "https://globusgo.pro/site/login": raise HTTPException(status_code=401, detail="Неверный логин")
        elif "mfa" in post_response.url.lower(): return {"status": "mfa_required", "session_token": token}
        else: return {"status": "success", "session_token": token}
    except HTTPException: raise
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/mfa")
def verify_mfa(req: MFARequest):
    user_session = sessions.get(req.session_token)
    try:
        response = user_session.get("https://globusgo.pro/site/mfa-verify")
        csrf_value = BeautifulSoup(response.text, 'html.parser').find('input', {'name': '_csrf-backend'}).get('value')
        post_response = user_session.post("https://globusgo.pro/site/mfa-verify", data={'_csrf-backend': csrf_value, 'OtpForm[otp]': req.otp})
        if post_response.url == "https://globusgo.pro/site/mfa-verify": raise HTTPException(status_code=401, detail="Неверный код 2FA")
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# === ПРОФИЛЬ (Шаблоны, Настройки, Заметки, Статистика, Сетка, Балансы) ===
@app.post("/api/get_presets")
def get_presets(req: BaseAuthReq): return {"status": "success", "presets": user_presets.get(get_user(req.session_token), [])}
@app.post("/api/save_presets")
def save_presets(req: SavePresetsRequest):
    user_presets[get_user(req.session_token)] = [p.model_dump() for p in req.presets]
    save_json_db(PRESETS_FILE, user_presets); return {"status": "success"}

@app.post("/api/get_settings")
def get_settings(req: BaseAuthReq):
    u_settings = user_settings.get(get_user(req.session_token), {})
    return {
        "status": "success", 
        "selected_rooms": u_settings.get("selected_rooms", list(ROOMS.values())),
        "active_formats": u_settings.get("active_formats", {"isPko": True, "isFreezeout": True, "isTurbo": True}),
        "is_compact": u_settings.get("is_compact", False),
        "sort_order": u_settings.get("sort_order", "time")
    }

@app.post("/api/save_settings")
def save_settings(req: SaveSettingsRequest):
    u = get_user(req.session_token)
    if u not in user_settings: user_settings[u] = {}
    user_settings[u]["selected_rooms"] = req.selected_rooms
    user_settings[u]["active_formats"] = req.active_formats
    user_settings[u]["is_compact"] = req.is_compact
    user_settings[u]["sort_order"] = req.sort_order
    save_json_db(SETTINGS_FILE, user_settings)
    return {"status": "success"}

@app.post("/api/get_notes")
def get_notes(req: BaseAuthReq): return {"status": "success", "notes": user_notes.get(get_user(req.session_token), {})}
@app.post("/api/save_notes")
def save_notes(req: SaveNotesRequest):
    user_notes[get_user(req.session_token)] = req.notes
    save_json_db(NOTES_FILE, user_notes); return {"status": "success"}

@app.post("/api/get_sessions_history")
def get_sessions_history(req: BaseAuthReq): return {"status": "success", "history": user_sessions_history.get(get_user(req.session_token), [])}
@app.post("/api/save_session_history")
def save_session_history(req: SaveSessionHistoryRequest):
    u = get_user(req.session_token)
    if u not in user_sessions_history: user_sessions_history[u] = []
    user_sessions_history[u].append(req.session_data.model_dump())
    save_json_db(SESSIONS_HISTORY_FILE, user_sessions_history); return {"status": "success"}

@app.post("/api/clear_sessions_history")
def clear_sessions_history(req: ClearSessionHistoryRequest):
    u = get_user(req.session_token)
    user_sessions_history[u] = []
    save_json_db(SESSIONS_HISTORY_FILE, user_sessions_history)
    return {"status": "success"}

@app.post("/api/get_grid")
def get_grid(req: BaseAuthReq):
    return {"status": "success", "grid": user_grid.get(get_user(req.session_token), [])}
@app.post("/api/save_grid")
def save_grid(req: SaveGridRequest):
    user_grid[get_user(req.session_token)] = req.grid
    save_json_db(GRID_FILE, user_grid)
    return {"status": "success"}

@app.post("/api/get_balances")
def get_balances(req: BaseAuthReq):
    return {"status": "success", "balances": user_balances.get(get_user(req.session_token), {})}
@app.post("/api/save_balances")
def save_balances(req: SaveBalancesRequest):
    user_balances[get_user(req.session_token)] = req.balances
    save_json_db(BALANCES_FILE, user_balances)
    return {"status": "success"}

# === ТУРНИРЫ ===
@app.post("/api/tournaments")
def fetch_tournaments(filters: FiltersRequest):
    user_session = sessions.get(filters.session_token)
    if not user_session: raise HTTPException(status_code=401, detail="Сессия не найдена")

    params = [
        ('DailyTournamentAutoPlayerSearchOld[dateFrom]', format_date_for_target(filters.date_from)),
        ('DailyTournamentAutoPlayerSearchOld[dateTo]', format_date_for_target(filters.date_to)),
        ('DailyTournamentAutoPlayerSearchOld[timeZone]', 'Asia/Amman'),
        ('DailyTournamentAutoPlayerSearchOld[rang]', '16'),
        ('DailyTournamentAutoPlayerSearchOld[endSessionAt]', ''),
        ('DailyTournamentAutoPlayerSearchOld[buyInFrom]', ''),
        ('DailyTournamentAutoPlayerSearchOld[afsFrom]', ''),
        ('DailyTournamentAutoPlayerSearchOld[buyInTo]', ''),
        ('DailyTournamentAutoPlayerSearchOld[afsTo]', ''),
        ('formChanged', '1'), ('per-page', '1000'), ('page', '1')
    ]
    for key, val in filters.structs.model_dump().items():
        params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '0'))
        if val == '1': params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '1'))
    for key, val in filters.priorities.model_dump().items():
        params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '0'))
        if val == '1': params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '1'))
    params.append(('DailyTournamentAutoPlayerSearchOld[networksIds]', ''))
    for room_id in filters.rooms: params.append(('DailyTournamentAutoPlayerSearchOld[networksIds][]', room_id))

    try:
        response = user_session.get("https://globusgo.pro/tournament-selector/index-selector-player-old", params=params)
        if "login" in response.url: raise HTTPException(status_code=401)
        soup = BeautifulSoup(response.text, 'html.parser')
        target_table = None
        for table in soup.find_all('table'):
            if 'Название' in table.text and 'ABI' in table.text: target_table = table; break
        if not target_table: return {"status": "success", "data": []}

        tournaments = []
        for row in target_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 13:
                room_text = cols[0].text.strip()
                if 'Ничего не найдено' in room_text: continue
                
                # Жестко определяем имя и валюту по ID сети, прилетевшему в запросе
                room = room_text
                if 'pokerstars' in room_text.lower() or 'ps' in room_text.lower() or room_text.startswith('PS'):
                    if '250' in filters.rooms:
                        room = "PS(ES)"
                    elif '249' in filters.rooms:
                        room = "PS(COM)"

                roi = cols[6].text.strip()
                try: roi_val = float(roi.replace('%', '').replace(',', '.').strip()); roi_str = f"+{roi_val}%" if roi_val > 0 else f"{roi_val}%"
                except: roi_str = f"{roi}%" if '%' not in roi else roi
                
                tournaments.append({
                    'id': f"{cols[5].text.strip()}_{cols[1].text.strip()}",
                    'room': room, 'name': cols[1].text.strip(), 'priority': cols[2].text.strip(),
                    'buyin': cols[3].text.strip(), 'reentry': cols[4].text.strip(),
                    'time_start': cols[5].text.strip(), 'roi_str': roi_str, 'ev_mtt': cols[7].text.strip(),
                    'day_week': cols[8].text.strip(), 'duration': cols[10].text.strip(),
                    'gtd': cols[11].text.strip(), 'afs': cols[12].text.strip()
                })
        return {"status": "success", "data": tournaments}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(none_stop=True), daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
