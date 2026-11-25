"""Central iPoker tournament catalog.

The collector runs with the administrator's authenticated upstream session.
This module deliberately separates collection from public selector reads:
players never receive or need the upstream iPoker credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .storage import get_db_path, load_json_db, save_json_db
from .ipoker_socketio import extract_tournaments

router = APIRouter()
CATALOG_FILE = get_db_path("ipoker_tournaments_db.json")
ADMIN_TOKEN_ENV = "IPOKER_PARSER_ADMIN_TOKEN"
MAX_TOURNAMENTS = 10000


class CatalogImport(BaseModel):
    tournaments: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_TOURNAMENTS)
    collected_at: str | None = None
    collector_version: str = "0.2.0"


class SocketIOImport(BaseModel):
    frames: list[Any] = Field(min_length=1, max_length=500)
    collected_at: str | None = None
    collector_version: str = "0.2.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return default


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "pko", "knockout"}:
        return True
    if text in {"0", "false", "no", "n", ""}:
        return False
    return None


def normalize_tournament(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a native ChampionPoker container while retaining its raw shape."""
    grid = raw.get("tournamentGrid") if isinstance(raw.get("tournamentGrid"), dict) else raw
    upstream_id = _first(raw, "containerId", "tournamentId", "tournament_id", "id", "uniqueId", "gameId")
    if upstream_id is None:
        stable = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        upstream_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]

    start = _first(grid, "startDate", "startDateTime", "startTime", "start_time", "scheduledStart")
    if isinstance(start, (int, float)):
        timestamp = start / 1000 if start > 10_000_000_000 else start
        start = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    buyin = _first(grid, "buyIn", "buyin", "buy_in", "totalBuyIn", "entryFee")
    fee = _first(grid, "fee", "rake", "buyInFee")
    total_buyin = (buyin + fee) if isinstance(buyin, (int, float)) and isinstance(fee, (int, float)) else buyin
    currency = _first(grid, "cur", "currency", "currencyCode", "buyInCurrency")
    knockout_fee = _first(grid, "knockOutFee", "knockoutFee")
    pko = _as_bool(_first(grid, "isPko", "isPKO", "progressiveKnockout", "knockout", "isKnockout"))
    if pko is None and isinstance(knockout_fee, (int, float)):
        pko = knockout_fee > 0

    return {
        "id": f"ipoker_{upstream_id}",
        "upstream_id": str(upstream_id),
        "source": "ipoker",
        "room": "ChampionPoker",
        "room_id": "247",
        "name": str(_first(grid, "name", "tournamentName", "title", "displayName", default="iPoker tournament")),
        "time_start": str(start or ""),
        "buyin": buyin,
        "fee": fee,
        "total_buyin": total_buyin,
        "currency": currency,
        "gtd": _first(grid, "guaranteedPrize", "guarantee", "gtd", "prizePoolGuaranteed"),
        "prize_pool": _first(grid, "prizePool", "currentPrizePool"),
        "afs": _first(grid, "maxPlayers", "maxRegistrations", "capacity"),
        "registered_players": _first(grid, "registeredPlayers", "totalPlayers", "playersRegistered"),
        "active_players": _first(grid, "activePlayers", "playersRemaining"),
        "table_size": _first(grid, "tableSize", "maxPlayersPerTable", "seats"),
        "game_type": _first(grid, "gameType", "game"),
        "bet_type": _first(grid, "betType", "limitType"),
        "status": _first(grid, "status", "tournamentStatus"),
        "color_index": _first(grid, "colorIndex"),
        "buyin_type": _first(grid, "buyInType"),
        "prize_type": _first(grid, "prizeType"),
        "is_pko": pko,
        "knockout_fee": knockout_fee,
        "speed": _first(grid, "speed", "structure", "tournamentSpeed", "speedMode"),
        "late_registration": _first(grid, "lateRegistration"),
        "late_registration_status": _first(grid, "lateRegistrationStatus"),
        "registration_start": _first(grid, "registrationStartDate"),
        "registration_end": _first(grid, "registrationEndDate"),
        "max_players": _first(grid, "maxPlayers"),
        "start_stack": _first(grid, "startStack"),
        "level_duration": _first(grid, "levelDuration"),
        "raw": raw,
    }


def _require_admin(token: str | None) -> None:
    expected = os.getenv(ADMIN_TOKEN_ENV, "")
    if not expected:
        raise HTTPException(status_code=503, detail=f"Админский доступ парсера не настроен: задайте {ADMIN_TOKEN_ENV} в Railway Variables")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Недействительный токен администратора парсера")


def _read_store() -> dict[str, Any]:
    return load_json_db(CATALOG_FILE)


def _public_record(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "raw"}


def _save_tournaments(tournaments: list[dict[str, Any]], collected_at: str | None, collector_version: str) -> dict[str, Any]:
    normalized = [normalize_tournament(item) for item in tournaments]
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in normalized:
        deduplicated[item["id"]] = item
    if not deduplicated:
        raise HTTPException(status_code=422, detail="Коллектор не передал ни одного турнира")
    collected_at = collected_at or _utc_now()
    store = {
        "source": "ChampionPoker iPoker",
        "updated_at": collected_at,
        "received_at": _utc_now(),
        "collector_version": collector_version,
        "tournaments": list(deduplicated.values()),
    }
    save_json_db(CATALOG_FILE, store)
    return {"status": "saved", "updated_at": collected_at, "count": len(deduplicated), "file": os.path.basename(CATALOG_FILE)}


@router.get("/api/tournaments/catalog")
def get_tournament_catalog():
    """Public read endpoint consumed by every selector user."""
    store = _read_store()
    updated_at = store.get("updated_at")
    tournaments = store.get("tournaments", [])
    stale = True
    if updated_at:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))).total_seconds()
            stale = age > 20 * 60
        except (TypeError, ValueError):
            stale = True
    return {"status": "ready" if tournaments else "empty", "source": "ipoker", "updated_at": updated_at,
            "stale": stale, "count": len(tournaments), "data": [_public_record(item) for item in tournaments]}


@router.get("/api/admin/ipoker/status")
def get_ipoker_parser_status(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    store = _read_store()
    return {"configured": True, "updated_at": store.get("updated_at"), "count": len(store.get("tournaments", [])),
            "collector_version": store.get("collector_version"), "source": store.get("source", "iPoker")}


@router.post("/api/admin/ipoker/import")
def import_ipoker_tournaments(payload: CatalogImport, x_parser_admin_token: str | None = Header(default=None)):
    """Import already extracted raw iPoker tournament records."""
    _require_admin(x_parser_admin_token)
    return _save_tournaments(payload.tournaments, payload.collected_at, payload.collector_version)


@router.post("/api/admin/ipoker/import-frames")
def import_ipoker_socketio_frames(payload: SocketIOImport, x_parser_admin_token: str | None = Header(default=None)):
    """Decode Socket.IO lobby frames and atomically replace the shared catalog."""
    _require_admin(x_parser_admin_token)
    tournaments, report = extract_tournaments(payload.frames)
    if not tournaments:
        raise HTTPException(status_code=422, detail="В переданных WebSocket frames не найден tournamentsList")
    result = _save_tournaments(tournaments, payload.collected_at, payload.collector_version)
    return {**result, "decode": report}


class RawCaptureBatch(BaseModel):
    """Raw browser-observed iPoker WebSocket frames; binary payloads stay base64."""
    frames: list[dict[str, Any]] = Field(min_length=1, max_length=500)


CAPTURE_FILE = get_db_path("ipoker_ws_capture_db.json")
MAX_CAPTURE_FRAMES = 20000


@router.post("/api/admin/ipoker/capture")
def ingest_ipoker_capture(payload: RawCaptureBatch, x_parser_admin_token: str | None = Header(default=None)):
    """Receive live frames from the administrator's logged-in browser; never from players."""
    _require_admin(x_parser_admin_token)
    capture = load_json_db(CAPTURE_FILE)
    frames = capture.get("frames", []) if isinstance(capture, dict) else []
    accepted = []
    for frame in payload.frames:
        direction = str(frame.get("direction", "unknown"))[:16]
        data = frame.get("data")
        if not isinstance(data, (str, dict, list, int, float, bool)) and data is not None:
            continue
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded) > 2_000_000:
            continue
        accepted.append({"captured_at": str(frame.get("captured_at") or _utc_now()), "direction": direction,
                         "kind": str(frame.get("kind", "message"))[:32], "url": str(frame.get("url", ""))[:1000], "data": data})
    if not accepted:
        raise HTTPException(status_code=422, detail="Нет допустимых WebSocket frames")
    frames.extend(accepted)
    frames = frames[-MAX_CAPTURE_FRAMES:]
    store = {"source": "ChampionPoker iPoker WebSocket", "updated_at": _utc_now(), "count": len(frames), "frames": frames}
    save_json_db(CAPTURE_FILE, store)
    return {"status": "captured", "accepted": len(accepted), "total": len(frames), "updated_at": store["updated_at"]}


@router.get("/api/admin/ipoker/capture/status")
def get_ipoker_capture_status(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    capture = load_json_db(CAPTURE_FILE)
    frames = capture.get("frames", []) if isinstance(capture, dict) else []
    by_direction: dict[str, int] = {}
    for frame in frames:
        direction = str(frame.get("direction", "unknown"))
        by_direction[direction] = by_direction.get(direction, 0) + 1
    return {"count": len(frames), "updated_at": capture.get("updated_at") if isinstance(capture, dict) else None,
            "by_direction": by_direction, "source": "ChampionPoker iPoker WebSocket"}
