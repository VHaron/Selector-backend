from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from .state import sessions, auth_state_lock

router = APIRouter()

class SmartGridWithTournamentsRequest(BaseModel):
    session_token: str
    budget: float
    max_tables: int
    start_time: str
    end_time: str
    tournaments: List[dict] = []

@router.post("/api/build_smart_grid")
def api_build_smart_grid(req: SmartGridWithTournamentsRequest):
    with auth_state_lock:
        user_session = sessions.get(req.session_token)
    if not user_session:
        raise HTTPException(status_code=401, detail="Сессия не найдена")

    try:
        start_dt = datetime.strptime(req.start_time.replace('T', ' ')[:16], "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(req.end_time.replace('T', ' ')[:16], "%Y-%m-%d %H:%M")
    except:
        start_dt = datetime.now()
        end_dt = start_dt + timedelta(hours=6)

    valid_tournaments = []
    for t in req.tournaments:
        try:
            buyin_str = str(t.get('buyin', '0')).replace('$', '').replace('€', '').replace(',', '.').strip()
            buyin = float(buyin_str) if buyin_str else 0.0
            
            ev_str = str(t.get('ev_mtt', '0')).replace('+', '').replace(',', '.').strip()
            ev = float(ev_str) if ev_str else 0.0
            
            dur_str = str(t.get('duration', '120')).replace(',', '.').strip()
            duration = float(dur_str) * 60 if dur_str else 120
            
            raw_time = t.get('time_start', '').strip()
            time_parts = raw_time.split()
            time_str = time_parts[-1] if time_parts else "00:00"
            
            if ':' not in time_str:
                continue
                
            t_start = datetime.strptime(f"{start_dt.strftime('%Y-%m-%d')} {time_str}:00", "%Y-%m-%d %H:%M:%S")
            if t_start < start_dt:
                t_start += timedelta(days=1)
                
            t_end = t_start + timedelta(minutes=duration)
            
            if t_start >= start_dt and t_start <= end_dt and 0 < buyin <= req.budget:
                valid_tournaments.append({
                    'data': t,
                    'buyin': buyin,
                    'ev': ev,
                    'start_min': int(t_start.timestamp() // 60),
                    'end_min': int(t_end.timestamp() // 60)
                })
        except Exception:
            continue

    valid_tournaments.sort(key=lambda x: x['ev'], reverse=True)

    selected_grid = []
    current_spent = 0
    timeline = {}

    for t in valid_tournaments:
        if current_spent + t['buyin'] > req.budget:
            continue

        can_add = True
        for minute in range(t['start_min'], t['end_min']):
            if timeline.get(minute, 0) >= req.max_tables:
                can_add = False
                break
        
        if can_add:
            selected_grid.append(t['data'])
            current_spent += t['buyin']
            for minute in range(t['start_min'], t['end_min']):
                timeline[minute] = timeline.get(minute, 0) + 1

    return {"status": "success", "grid": selected_grid}
