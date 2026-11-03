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

# === БАЗЫ ДАННЫХ ===
sessions: dict[str, requests.Session] = {}
session_to_user: dict[str, str] = {} 
session_to_domain: dict[str, str] = {} 

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
    'Pokerdom': '264', 'WPTGLobal': '266', 'CoinPoker': '265'
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
    clean_str = dt_str.replace("T", " ").strip()
    try:
        if "." in clean_str:
            parts = clean_str.split(" ")
            date_part = parts[0]
            time_part = parts[1] if len(parts) > 1 else "00:00"
            d, m, y = date_part.split(".")
            # Точный формат YYYY-MM-DDTHH:MM, который требует globusmvp.co
            return f"{y}-{m}-{d}T{time_part}"
        return clean_str
    except:
        return clean_str

# === ТУРНИРЫ ===
@app.post("/api/tournaments")
def fetch_tournaments(filters: FiltersRequest):
    user_session = sessions.get(filters.session_token)
    if not user_session: raise HTTPException(status_code=401, detail="Сессия не найдена")

    base_domain = session_to_domain.get(filters.session_token, "https://globusmvp.co")
    target_url = f"{base_domain}/tournament-selector/index-selector-player-old"

    params = [
        ('DailyTournamentAutoPlayerSearchOld[dateFrom]', format_date_for_target(filters.date_from)),
        ('DailyTournamentAutoPlayerSearchOld[dateTo]', format_date_for_target(filters.date_to)),
        ('DailyTournamentAutoPlayerSearchOld[timeZone]', 'Europe/Athens'),
        ('DailyTournamentAutoPlayerSearchOld[rang]', '15'),
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
    
    active_rooms = filters.rooms if filters.rooms else list(ROOMS.values())
    for room_id in active_rooms: 
        params.append(('DailyTournamentAutoPlayerSearchOld[networksIds][]', room_id))

    try:
        response = user_session.get(target_url, params=params)
        
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
                
                room = room_text
                room_lower = room_text.lower()
                
                if 'pokerstars' in room_lower or 'ps' in room_lower:
                    if 'es' in room_lower or 'fr' in room_lower or 'pt' in room_lower:
                        room = "PS(ES)"
                    else:
                        room = "PS(COM)"

                roi = cols[6].text.strip()
                try: roi_val = float(roi.replace('%', '').replace(',', '.').strip()); roi_str = f"+{roi_val}%" if roi_val > 0 else f"{roi_val}%"
                except: roi_str = f"{roi}%" if '%' not in roi else roi
                
                tournaments.append({
                    'id': f"{cols[5].text.strip()}_{cols[1].text.strip()}",
                    'room': room,
                    'name': cols[1].text.strip(), 'priority': cols[2].text.strip(),
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
