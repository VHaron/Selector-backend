import threading
import time
import requests
from fastapi import HTTPException

sessions: dict[str, requests.Session] = {}
session_to_user: dict[str, str] = {}
session_to_site: dict[str, str] = {}
session_mfa_url: dict[str, str] = {}
site_selection_groups: dict[str, dict] = {}
auth_state_lock = threading.RLock()
SITE_SELECTION_TTL_SECONDS = 300

def _close_session_token(token: str):
    """Remove all server-side references to a login session and close it."""
    with auth_state_lock:
        user_session = sessions.pop(token, None)
        session_to_user.pop(token, None)
        session_to_site.pop(token, None)
        session_mfa_url.pop(token, None)
    if user_session:
        try:
            user_session.close()
        except Exception:
            pass


def _discard_selection_group(selection_id: str):
    with auth_state_lock:
        group = site_selection_groups.pop(selection_id, None)
    if group:
        for token in group.get("tokens", {}).values():
            _close_session_token(token)


def _cleanup_expired_site_selections():
    """Close temporary login sessions when a user abandons the site chooser."""
    now = time.monotonic()
    with auth_state_lock:
        expired_ids = [
            selection_id
            for selection_id, group in site_selection_groups.items()
            if now - group.get("created_at", now) > SITE_SELECTION_TTL_SECONDS
        ]
    for selection_id in expired_ids:
        _discard_selection_group(selection_id)


def get_user(token: str):
    with auth_state_lock:
        if token not in sessions:
            raise HTTPException(status_code=401, detail="Сессия не найдена.")
        username = session_to_user.get(token)
        site = session_to_site.get(token, "globusgo")
    if not username:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    # Keep existing GlobusGo profile keys intact; isolate GlobusMVP data.
    return username if site == "globusgo" else f"{site}:{username}"


