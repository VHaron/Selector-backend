"""Tournament feed served only from the selector's central catalog.

No Globus or poker-room website is contacted by this endpoint.
New room collectors can add records to the same catalog format over time.
"""
from datetime import datetime
from fastapi import APIRouter
from .models import FiltersRequest
from .storage import get_db_path, load_json_db

router = APIRouter()
CATALOG_FILE = get_db_path("ipoker_tournaments_db.json")

@router.post("/api/tournaments")
def fetch_tournaments(filters: FiltersRequest):
    store = load_json_db(CATALOG_FILE)
    requested = {str(value) for value in (filters.rooms or [])}
    items = store.get("tournaments", []) if isinstance(store, dict) else []
    data = []
    for item in items:
        room_id = str(item.get("room_id") or "247")
        if requested and room_id not in requested:
            continue
        data.append({
            "id": str(item.get("id") or ""),
            "source": str(item.get("source") or "central"),
            "room": str(item.get("room") or "iPoker"),
            "name": str(item.get("name") or "Tournament"),
            "priority": str(item.get("priority") or "3"),
            "buyin": str(item.get("buyin") or ""),
            "reentry": str(item.get("reentry") or "0"),
            "time_start": str(item.get("time_start") or ""),
            "roi_str": str(item.get("roi_str") or ""),
            "ev_mtt": str(item.get("ev_mtt") or "0"),
            "day_week": str(item.get("day_week") or ""),
            "duration": str(item.get("duration") or ""),
            "gtd": str(item.get("gtd") or ""),
            "afs": str(item.get("afs") or ""),
        })
    return {"status": "success", "data": data, "catalog_updated_at": store.get("updated_at") if isinstance(store, dict) else None}
