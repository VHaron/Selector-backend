from fastapi import APIRouter
from .config import ROOMS
from .models import (BaseAuthReq, SavePresetsRequest, SaveSettingsRequest, SaveNotesRequest,
                     SaveSessionHistoryRequest, ClearSessionHistoryRequest, SaveGridRequest, SaveBalancesRequest)
from .state import get_user
from .storage import (db_lock, save_json_db, PRESETS_FILE, SETTINGS_FILE, NOTES_FILE,
                      SESSIONS_HISTORY_FILE, GRID_FILE, BALANCES_FILE, user_presets, user_settings,
                      user_notes, user_sessions_history, user_grid, user_balances)

router = APIRouter()

# === ПРОФИЛЬ (Шаблоны, Настройки, Заметки, Статистика, Сетка, Балансы) ===
@router.post("/api/get_presets")
def get_presets(req: BaseAuthReq):
    return {"status": "success", "presets": user_presets.get(get_user(req.session_token), [])}


@router.post("/api/save_presets")
def save_presets(req: SavePresetsRequest):
    with db_lock:
        user_presets[get_user(req.session_token)] = [p.model_dump() for p in req.presets]
        save_json_db(PRESETS_FILE, user_presets)
    return {"status": "success"}


@router.post("/api/get_settings")
def get_settings(req: BaseAuthReq):
    u_settings = user_settings.get(get_user(req.session_token), {})
    return {
        "status": "success",
        "selected_rooms": u_settings.get("selected_rooms", list(ROOMS.values())),
        "active_formats": u_settings.get("active_formats", {"isPko": True, "isFreezeout": True, "isTurbo": True}),
        "is_compact": u_settings.get("is_compact", False),
        "sort_order": u_settings.get("sort_order", "time")
    }


@router.post("/api/save_settings")
def save_settings(req: SaveSettingsRequest):
    u = get_user(req.session_token)
    with db_lock:
        if u not in user_settings:
            user_settings[u] = {}
        user_settings[u]["selected_rooms"] = req.selected_rooms
        user_settings[u]["active_formats"] = req.active_formats
        user_settings[u]["is_compact"] = req.is_compact
        user_settings[u]["sort_order"] = req.sort_order
        save_json_db(SETTINGS_FILE, user_settings)
    return {"status": "success"}


@router.post("/api/get_notes")
def get_notes(req: BaseAuthReq):
    return {"status": "success", "notes": user_notes.get(get_user(req.session_token), {})}


@router.post("/api/save_notes")
def save_notes(req: SaveNotesRequest):
    with db_lock:
        user_notes[get_user(req.session_token)] = req.notes
        save_json_db(NOTES_FILE, user_notes)
    return {"status": "success"}


@router.post("/api/get_sessions_history")
def get_sessions_history(req: BaseAuthReq):
    return {"status": "success", "history": user_sessions_history.get(get_user(req.session_token), [])}


@router.post("/api/save_session_history")
def save_session_history(req: SaveSessionHistoryRequest):
    u = get_user(req.session_token)
    with db_lock:
        user_sessions_history.setdefault(u, []).append(req.session_data.model_dump())
        save_json_db(SESSIONS_HISTORY_FILE, user_sessions_history)
    return {"status": "success"}


@router.post("/api/clear_sessions_history")
def clear_sessions_history(req: ClearSessionHistoryRequest):
    u = get_user(req.session_token)
    with db_lock:
        user_sessions_history[u] = []
        save_json_db(SESSIONS_HISTORY_FILE, user_sessions_history)
    return {"status": "success"}


@router.post("/api/get_grid")
def get_grid(req: BaseAuthReq):
    return {"status": "success", "grid": user_grid.get(get_user(req.session_token), [])}


@router.post("/api/save_grid")
def save_grid(req: SaveGridRequest):
    with db_lock:
        user_grid[get_user(req.session_token)] = req.grid
        save_json_db(GRID_FILE, user_grid)
    return {"status": "success"}


@router.post("/api/get_balances")
def get_balances(req: BaseAuthReq):
    return {"status": "success", "balances": user_balances.get(get_user(req.session_token), {})}


@router.post("/api/save_balances")
def save_balances(req: SaveBalancesRequest):
    with db_lock:
        user_balances[get_user(req.session_token)] = req.balances
        save_json_db(BALANCES_FILE, user_balances)
    return {"status": "success"}
