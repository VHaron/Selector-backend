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

# Keep bot credentials in deployment secrets, never in source control.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

if bot:
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
session_to_site: dict[str, str] = {}
session_mfa_url: dict[str, str] = {}

SITE_BASES = {"globusgo": "https://globusgo.pro", "globusmvp": "https://globusmvp.co"}


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
    'Pokerdom': '264', 'CoinPoker': '265',
    'ClubGG': '267', 'WPT': '268'
}

# === СХЕМЫ ===
class BaseAuthReq(BaseModel): session_token: str
class LoginRequest(BaseModel):
    username: str
    password: str
    site: str = "auto"  # auto | globusgo | globusmvp
class MFARequest(BaseAuthReq): otp: str

class StructsFilter(BaseModel):
    isFreezeout: str = '1'; isPko: str = '1'; isTurbo: str = '1'
    isHyperTurbo: str = '1'; is6MaxTurboPko: str = '1'; isSeries: str = '1'
class PrioritiesFilter(BaseModel): priority1: str = '1'; priority2: str = '1'; priority3: str = '1'

class FiltersRequest(BaseAuthReq):
    date_from: str = ''; date_to: str = ''
    # Frontend sends canonical GlobusGo room IDs; they are translated to each site's own IDs.
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
    if token not in sessions:
        raise HTTPException(status_code=401, detail="Сессия не найдена.")
    username = session_to_user.get(token)
    if not username:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    # Keep existing GlobusGo profile keys intact; isolate GlobusMVP data.
    site = session_to_site.get(token, "globusgo")
    return username if site == "globusgo" else f"{site}:{username}"


def _csrf_from_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", {"name": "_csrf-backend"})
    if not field or not field.get("value"):
        raise ValueError("На странице авторизации не найден CSRF-токен")
    return field["value"]


def _try_site_login(req: LoginRequest, site: str):
    base = SITE_BASES[site]
    user_session = requests.Session()
    user_session.verify = False
    user_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": f"{base}/site/login",
    })
    response = user_session.get(f"{base}/site/login", timeout=25)
    csrf_value = _csrf_from_page(response.text)
    post_response = user_session.post(
        f"{base}/site/login",
        data={
            "_csrf-backend": csrf_value,
            "LoginForm[username]": req.username,
            "LoginForm[password]": req.password,
            "LoginForm[rememberMe]": "1",
        },
        allow_redirects=True,
        timeout=25,
    )
    final_url = post_response.url.lower()
    page = BeautifulSoup(post_response.text, "html.parser")
    login_form_still_visible = bool(page.find("input", {"name": "LoginForm[password]"}))
    if "/site/login" in final_url and login_form_still_visible:
        user_session.close()
        return None

    token = str(uuid.uuid4())
    sessions[token] = user_session
    session_to_user[token] = req.username
    session_to_site[token] = site

    if "mfa" in final_url or "otp" in final_url or "двухфактор" in post_response.text.lower():
        session_mfa_url[token] = post_response.url if "mfa" in final_url or "otp" in final_url else f"{base}/site/mfa-verify"
        return {"status": "mfa_required", "session_token": token, "site": site}

    # A login redirect back to the login page is a failed login even if the HTML differs.
    if "/site/login" in final_url:
        sessions.pop(token, None)
        session_to_user.pop(token, None)
        session_to_site.pop(token, None)
        user_session.close()
        return None
    return {"status": "success", "session_token": token, "site": site}


# === ЭНДПОИНТЫ АВТОРИЗАЦИИ ===
@app.post("/api/login")
def login(req: LoginRequest):
    requested_site = (req.site or "auto").lower()
    if requested_site not in ("auto", *SITE_BASES.keys()):
        raise HTTPException(status_code=400, detail="Неизвестный сайт авторизации")
    sites_to_try = list(SITE_BASES) if requested_site == "auto" else [requested_site]
    errors = []
    for site in sites_to_try:
        try:
            result = _try_site_login(req, site)
            if result:
                return result
        except requests.RequestException as e:
            errors.append(f"{site}: сайт временно недоступен")
        except Exception as e:
            errors.append(f"{site}: {str(e)}")
    if errors and len(errors) == len(sites_to_try):
        raise HTTPException(status_code=502, detail="Не удалось проверить авторизацию: " + "; ".join(errors))
    raise HTTPException(status_code=401, detail="Логин или пароль не подошли ни к GlobusGo, ни к GlobusMVP")


@app.post("/api/mfa")
def verify_mfa(req: MFARequest):
    user_session = sessions.get(req.session_token)
    if not user_session:
        raise HTTPException(status_code=401, detail="Сессия авторизации истекла. Войдите заново.")
    site = session_to_site.get(req.session_token, "globusgo")
    base = SITE_BASES[site]
    mfa_url = session_mfa_url.get(req.session_token, f"{base}/site/mfa-verify")
    try:
        response = user_session.get(mfa_url, timeout=25)
        csrf_value = _csrf_from_page(response.text)
        mfa_soup = BeautifulSoup(response.text, "html.parser")
        otp_input = next((field for field in mfa_soup.find_all("input")
                          if any(part in (field.get("name") or "").lower() for part in ("otp", "code"))), None)
        otp_field_name = otp_input.get("name") if otp_input else "OtpForm[otp]"
        post_response = user_session.post(
            response.url,
            data={"_csrf-backend": csrf_value, otp_field_name: req.otp},
            allow_redirects=True,
            timeout=25,
        )
        if "mfa" in post_response.url.lower() or "otp" in post_response.url.lower():
            raise HTTPException(status_code=401, detail="Неверный код 2FA")
        if "/site/login" in post_response.url.lower():
            raise HTTPException(status_code=401, detail="Сессия авторизации истекла. Войдите заново.")
        session_mfa_url.pop(req.session_token, None)
        return {"status": "success", "site": site}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка проверки 2FA на {site}: {str(e)}")

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

# === СОПОСТАВЛЕНИЕ РУМОВ МЕЖДУ САЙТАМИ ===
ROOM_ALIASES = {
    "244": ("888", "888poker"), "245": ("chico", "betonline"),
    "246": ("ggnetwork", "ggpoker", "ggnet"), "247": ("ipoker", "redstar"),
    "248": ("partypoker", "party poker"), "249": ("pokerstars.com", "pokerstars (com)", "ps(com)"),
    "250": ("pokerstars.fr", "pokerstars.es", "pokerstars.pt", "ps(es)", "ps(fr)"),
    "251": ("winamax",), "252": ("wpn", "americas cardroom", "acr"),
    "264": ("pokerdom",), "265": ("coinpoker", "coin poker"),
    "267": ("clubgg", "club gg"), "268": ("wpt",),
}


def _canonical_room_id(label: str):
    value = " ".join((label or "").lower().replace("_", " ").split())
    if not value:
        return None
    if "pokerstars" in value or value.startswith("ps"):
        if any(region in value for region in (" es", "(es", " fr", "(fr", " pt", "(pt", "fr-es-pt")):
            return "250"
        if "com" in value or value.strip() in ("pokerstars", "ps", "ps.com"):
            return "249"
    for canonical_id, aliases in ROOM_ALIASES.items():
        if any(alias in value for alias in aliases):
            return canonical_id
    # Exact canonical labels are useful on GlobusGo and on pages with short labels.
    for canonical_id, room_label in {v: k for k, v in ROOMS.items()}.items():
        if value == room_label.lower():
            return canonical_id
    return None


def _resolve_room_ids(soup: BeautifulSoup, requested_ids: list[str], site: str) -> list[str]:
    requested = {str(room_id) for room_id in requested_ids}
    if not requested:
        return []
    found = {}
    # The selector may render the room filter as a multi-select or as checkboxes.
    for option in soup.find_all("option"):
        canonical = _canonical_room_id(option.get_text(" ", strip=True))
        value = option.get("value")
        if canonical in requested and value:
            found[canonical] = value
    for field in soup.find_all(["input"]):
        name = (field.get("name") or "").lower()
        if "networksids" not in name:
            continue
        label_text = ""
        if field.get("id"):
            label = soup.find("label", attrs={"for": field.get("id")})
            if label:
                label_text = label.get_text(" ", strip=True)
        parent = field.parent
        if parent:
            label_text += " " + parent.get_text(" ", strip=True)
        canonical = _canonical_room_id(label_text)
        if canonical in requested and field.get("value"):
            found[canonical] = field.get("value")
    if site == "globusgo":
        # Preserve the original site's established IDs as a compatibility fallback.
        for room_id in requested:
            found.setdefault(room_id, room_id)
    if not found and site == "globusmvp":
        raise HTTPException(status_code=502, detail="Не удалось определить ID румов GlobusMVP из формы фильтров. Ничего не отправлено, чтобы не подставить неверные ID.")
    return list(dict.fromkeys(found.values()))


# === ТУРНИРЫ ===
@app.post("/api/tournaments")
def fetch_tournaments(filters: FiltersRequest):
    user_session = sessions.get(filters.session_token)
    if not user_session: raise HTTPException(status_code=401, detail="Сессия не найдена")
    site = session_to_site.get(filters.session_token, "globusgo")
    base = SITE_BASES[site]

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
    try:
        selector_url = f"{base}/tournament-selector/index-selector-player-old"
        # Read the authenticated selector first: room IDs are site-specific and must not
        # be copied from GlobusGo to GlobusMVP.
        page_response = user_session.get(selector_url, timeout=30)
        if "/site/login" in page_response.url.lower():
            raise HTTPException(status_code=401, detail="Авторизация на исходном сайте истекла")
        page_soup = BeautifulSoup(page_response.text, 'html.parser')
        site_room_ids = _resolve_room_ids(page_soup, filters.rooms, site)
        params.append(('DailyTournamentAutoPlayerSearchOld[networksIds]', ''))
        for room_id in site_room_ids:
            params.append(('DailyTournamentAutoPlayerSearchOld[networksIds][]', room_id))
        response = user_session.get(selector_url, params=params, timeout=45)
        if "/site/login" in response.url.lower(): raise HTTPException(status_code=401, detail="Авторизация на исходном сайте истекла")
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
                
                # Определяем PS(ES) и PS(COM) по фактическому названию из парсера
                room = room_text
                room_lower = room_text.lower()
                
                # Сначала убеждаемся, что это вообще семейство PokerStars
                if 'pokerstars' in room_lower or 'ps' in room_lower:
                    # И только теперь проверяем, Европа это или COM
                    if 'es' in room_lower or 'fr' in room_lower or 'pt' in room_lower:
                        room = "PS(ES)"
                    else:
                        room = "PS(COM)"

                roi = cols[6].text.strip()
                try: roi_val = float(roi.replace('%', '').replace(',', '.').strip()); roi_str = f"+{roi_val}%" if roi_val > 0 else f"{roi_val}%"
                except: roi_str = f"{roi}%" if '%' not in roi else roi
                
                tournaments.append({
                    'id': f"{site}_{cols[5].text.strip()}_{cols[1].text.strip()}",
                    'source': site,
                    'room': room,  # теперь сюда улетит точное имя PS(ES) или PS(COM)
                    'name': cols[1].text.strip(), 'priority': cols[2].text.strip(),
                    'buyin': cols[3].text.strip(), 'reentry': cols[4].text.strip(),
                    'time_start': cols[5].text.strip(), 'roi_str': roi_str, 'ev_mtt': cols[7].text.strip(),
                    'day_week': cols[8].text.strip(), 'duration': cols[10].text.strip(),
                    'gtd': cols[11].text.strip(), 'afs': cols[12].text.strip()
                })
        return {"status": "success", "data": tournaments}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    if bot:
        threading.Thread(target=lambda: bot.infinity_polling(none_stop=True), daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
