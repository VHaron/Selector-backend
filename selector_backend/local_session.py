"""App-only session endpoints; intentionally no poker-room authentication."""
import uuid
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .state import sessions, session_to_user, session_to_site, auth_state_lock

router = APIRouter()

class LocalSessionRequest(BaseModel):
    client_id: str = Field(min_length=16, max_length=128)

@router.post("/api/session")
def create_local_session(req: LocalSessionRequest):
    client_id = req.client_id.strip()
    if not client_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in client_id):
        raise HTTPException(status_code=400, detail="Некорректный идентификатор клиента")
    token = str(uuid.uuid4())
    local_session = requests.Session()
    with auth_state_lock:
        sessions[token] = local_session
        session_to_user[token] = f"local:{client_id}"
        session_to_site[token] = "local"
    return {"status": "success", "session_token": token, "site": "local"}
