from datetime import datetime
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from .config import SITE_BASES, ROOMS
from .models import FiltersRequest
from .state import sessions, session_to_site, auth_state_lock, sessions as global_sessions
from .smart_builder import build_smart_grid

router = APIRouter()

def format_date_for_target(dt_str: str) -> str:
    if not dt_str:
        return ""
    normalized = dt_str.strip().replace("T", " ")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return normalized

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
    for canonical_id, room_label in {v: k for k, v in ROOMS.items()}.items():
        if value == room_label.lower():
            return canonical_id
    return None


def _resolve_room_ids(soup: BeautifulSoup, requested_ids: list[str], site: str) -> list[str]:
    requested = {str(room_id) for room_id in requested_ids}
    if not requested:
        return []
    found = {}
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
        for room_id in requested:
            found.setdefault(room_id, room_id)
    if not found and site == "globusmvp":
        raise HTTPException(status_code=502, detail="Не удалось определить ID румов GlobusMVP из формы фильтров. Ничего не отправлено, чтобы не подставить неверные ID.")
    return list(dict.fromkeys(found.values()))


# === ТУРНИРЫ ===
@router.post("/api/tournaments")
def fetch_tournaments(filters: FiltersRequest):
    with auth_state_lock:
        user_session = sessions.get(filters.session_token)
        site = session_to_site.get(filters.session_token, "globusgo")
    if not user_session:
        raise HTTPException(status_code=401, detail="Сессия не найдена")
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
        if val == '1':
            params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '1'))
    for key, val in filters.priorities.model_dump().items():
        params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '0'))
        if val == '1':
            params.append((f'DailyTournamentAutoPlayerSearchOld[{key}]', '1'))
    try:
        selector_url = f"{base}/tournament-selector/index-selector-player-old"
        page_response = user_session.get(selector_url, timeout=30)
        if "/site/login" in page_response.url.lower():
            raise HTTPException(status_code=401, detail="Авторизация на исходном сайте истекла")
        page_soup = BeautifulSoup(page_response.text, 'html.parser')
        site_room_ids = _resolve_room_ids(page_soup, filters.rooms, site)
        params.append(('DailyTournamentAutoPlayerSearchOld[networksIds]', ''))
        for room_id in site_room_ids:
            params.append(('DailyTournamentAutoPlayerSearchOld[networksIds][]', room_id))
        response = user_session.get(selector_url, params=params, timeout=45)
        if "/site/login" in response.url.lower():
            raise HTTPException(status_code=401, detail="Авторизация на исходном сайте истекла")
        soup = BeautifulSoup(response.text, 'html.parser')
        target_table = None
        for table in soup.find_all('table'):
            if 'Название' in table.text and 'ABI' in table.text:
                target_table = table
                break
        if not target_table:
            return {"status": "success", "data": []}

        tournaments = []
        for row in target_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 13:
                room_text = cols[0].text.strip()
                if 'Ничего не найдено' in room_text:
                    continue

                room = room_text
                room_lower = room_text.lower()

                if 'pokerstars' in room_lower or 'ps' in room_lower:
                    if 'es' in room_lower or 'fr' in room_lower or 'pt' in room_lower:
                        room = "PS(ES)"
                    else:
                        room = "PS(COM)"

                roi = cols[6].text.strip()
                try:
                    roi_val = float(roi.replace('%', '').replace(',', '.').strip())
                    roi_str = f"+{roi_val}%" if roi_val > 0 else f"{roi_val}%"
                except:
                    roi_str = f"{roi}%" if '%' not in roi else roi

                tournaments.append({
                    'id': f"{site}_{cols[5].text.strip()}_{cols[1].text.strip()}",
                    'source': site,
                    'room': room,
                    'name': cols[1].text.strip(),
                    'priority': cols[2].text.strip(),
                    'buyin': cols[3].text.strip(),
                    'reentry': cols[4].text.strip(),
                    'time_start': cols[5].text.strip(),
                    'roi_str': roi_str,
                    'ev_mtt': cols[7].text.strip(),
                    'day_week': cols[8].text.strip(),
                    'duration': cols[10].text.strip(),
                    'gtd': cols[11].text.strip(),
                    'afs': cols[12].text.strip()
                })
        return {"status": "success", "data": tournaments}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# === НОВЫЙ ЭНДПОИНТ: ИИ-АВТОСБОРКА СЕТКИ ===
class SmartGridRequest(BaseModel):
    session_token: str
    budget: float
    max_tables: int
    start_time: str
    end_time: str
    rooms: List[str]

@router.post("/api/build_smart_grid")
def api_build_smart_grid(req: SmartGridRequest):
    with auth_state_lock:
        user_session = sessions.get(req.session_token)
        site = session_to_site.get(req.session_token, "globusgo")
    if not user_session:
        raise HTTPException(status_code=401, detail="Сессия не найдена")
    base = SITE_BASES[site]

    try:
        # Получаем полный список турниров с учетом выбранных румов через штатную логику парсера
        # Формируем базовый запрос к селектору без жестких дат, чтобы забрать все доступные турниры диапазона
        selector_url = f"{base}/tournament-selector/index-selector-player-old"
        page_response = user_session.get(selector_url, timeout=30)
        page_soup = BeautifulSoup(page_response.text, 'html.parser')
        site_room_ids = _resolve_room_ids(page_soup, req.rooms, site)

        params = [
            ('DailyTournamentAutoPlayerSearchOld[dateFrom]', ''),
            ('DailyTournamentAutoPlayerSearchOld[dateTo]', ''),
            ('DailyTournamentAutoPlayerSearchOld[timeZone]', 'Asia/Amman'),
            ('DailyTournamentAutoPlayerSearchOld[rang]', '16'),
            ('DailyTournamentAutoPlayerSearchOld[endSessionAt]', ''),
            ('DailyTournamentAutoPlayerSearchOld[buyInFrom]', ''),
            ('DailyTournamentAutoPlayerSearchOld[afsFrom]', ''),
            ('DailyTournamentAutoPlayerSearchOld[buyInTo]', ''),
            ('DailyTournamentAutoPlayerSearchOld[afsTo]', ''),
            ('formChanged', '1'), ('per-page', '1000'), ('page', '1')
        ]
        params.append(('DailyTournamentAutoPlayerSearchOld[networksIds]', ''))
        for room_id in site_room_ids:
            params.append(('DailyTournamentAutoPlayerSearchOld[networksIds][]', room_id))

        response = user_session.get(selector_url, params=params, timeout=45)
        soup = BeautifulSoup(response.text, 'html.parser')
        target_table = None
        for table in soup.find_all('table'):
            if 'Название' in table.text and 'ABI' in table.text:
                target_table = table
                break
        
        if not target_table:
            return {"status": "success", "grid": []}

        all_tournaments = []
        for row in target_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) >= 13:
                room_text = cols[0].text.strip()
                if 'Ничего не найдено' in room_text:
                    continue
                room = room_text
                room_lower = room_text.lower()
                if 'pokerstars' in room_lower or 'ps' in room_lower:
                    if 'es' in room_lower or 'fr' in room_lower or 'pt' in room_lower:
                        room = "PS(ES)"
                    else:
                        room = "PS(COM)"

                all_tournaments.append({
                    'id': f"{site}_{cols[5].text.strip()}_{cols[1].text.strip()}",
                    'source': site,
                    'room': room,
                    'name': cols[1].text.strip(),
                    'priority': cols[2].text.strip(),
                    'buyin': cols[3].text.strip(),
                    'reentry': cols[4].text.strip(),
                    'time_start': cols[5].text.strip(),
                    'ev_mtt': cols[7].text.strip(),
                    'duration': cols[10].text.strip(),
                    'gtd': cols[11].text.strip(),
                    'afs': cols[12].text.strip()
                })

        # Прогоняем через математический оптимизатор таймлайна
        optimal_grid = build_smart_grid(
            tournaments=all_tournaments,
            budget=req.budget,
            max_tables=req.max_tables,
            start_time_str=req.start_time,
            end_time_str=req.end_time
        )
        
        return {"status": "success", "grid": optimal_grid}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
