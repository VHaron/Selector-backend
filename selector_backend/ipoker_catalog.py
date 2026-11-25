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

router = APIRouter()
CATALOG_FILE = get_db_path("ipoker_tournaments_db.json")
ADMIN_TOKEN_ENV = "IPOKER_PARSER_ADMIN_TOKEN"
MAX_TOURNAMENTS = 10000


class CatalogImport(BaseModel):
    tournaments: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_TOURNAMENTS)
    collected_at: str | None = None
    collector_version: str = "0.1.0"


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
    upstream_id = _first(raw, "tournamentId", "tournament_id", "id", "uniqueId", "gameId")
    if upstream_id is None:
        stable = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        upstream_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]

    start = _first(raw, "startDateTime", "startTime", "start_time", "startDate", "scheduledStart")
    if isinstance(start, (int, float)):
        # iPoker payloads commonly use Unix milliseconds.
        timestamp = start / 1000 if start > 10_000_000_000 else start
        start = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    buyin = _first(raw, "buyIn", "buyin", "buy_in", "totalBuyIn", "entryFee")
    fee = _first(raw, "fee", "rake", "buyInFee")
    currency = _first(raw, "currency", "currencyCode", "buyInCurrency")
    pko = _as_bool(_first(raw, "isPko", "isPKO", "progressiveKnockout", "knockout", "isKnockout"))

    return {
        "id": f"ipoker_{upstream_id}",
        "source": "ipoker",
        "room": "iPoker",
        "room_id": "247",
        "name": str(_first(raw, "name", "tournamentName", "title", "displayName", default="iPoker tournament")),
        "time_start": str(start or ""),
        "buyin": buyin,
        "fee": fee,
        "currency": currency,
        "gtd": _first(raw, "guaranteedPrize", "guarantee", "gtd", "prizePoolGuaranteed"),
        "prize_pool": _first(raw, "prizePool", "currentPrizePool"),
        "afs": _first(raw, "maxPlayers", "maxRegistrations", "capacity"),
        "registered_players": _first(raw, "registeredPlayers", "totalPlayers", "playersRegistered"),
        "active_players": _first(raw, "activePlayers", "playersRemaining"),
        "table_size": _first(raw, "tableSize", "maxPlayersPerTable", "seats"),
        "game_type": _first(raw, "gameType", "game"),
        "bet_type": _first(raw, "betType", "limitType"),
        "status": _first(raw, "status", "tournamentStatus"),
        "is_pko": pko,
        "speed": _first(raw, "speed", "structure", "tournamentSpeed"),
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
    return {
        "status": "ready" if tournaments else "empty",
        "source": "ipoker",
        "updated_at": updated_at,
        "stale": stale,
        "count": len(tournaments),
        "data": [_public_record(item) for item in tournaments],
    }


@router.get("/api/admin/ipoker/status")
def get_ipoker_parser_status(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    store = _read_store()
    return {
        "configured": True,
        "updated_at": store.get("updated_at"),
        "count": len(store.get("tournaments", [])),
        "collector_version": store.get("collector_version"),
        "source": store.get("source", "iPoker"),
    }


@router.post("/api/admin/ipoker/import")
def import_ipoker_tournaments(
    payload: CatalogImport,
    x_parser_admin_token: str | None = Header(default=None),
):
    """Private collector ingestion endpoint; never expose the admin token to players."""
    _require_admin(x_parser_admin_token)
    normalized = [normalize_tournament(item) for item in payload.tournaments]
    deduplicated: dict[str, dict[str, Any]] = {}
    for item in normalized:
        deduplicated[item["id"]] = item
    if not deduplicated:
        raise HTTPException(status_code=422, detail="Коллектор не передал ни одного турнира")

    collected_at = payload.collected_at or _utc_now()
    store = {
        "source": "iPoker",
        "updated_at": collected_at,
        "received_at": _utc_now(),
        "collector_version": payload.collector_version,
        "tournaments": list(deduplicated.values()),
    }
    save_json_db(CATALOG_FILE, store)
    return {
        "status": "saved",
        "updated_at": collected_at,
        "count": len(deduplicated),
        "file": os.path.basename(CATALOG_FILE),
    }
