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


def _translated_name(value: Any) -> str | None:
    """Extract a display name from the native iPoker nameTranslationsList."""
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return None
    entries = [item for item in value if isinstance(item, dict)]
    if not entries:
        return None
    # Prefer English while retaining a deterministic fallback to first translation.
    for item in entries:
        language = str(_first(item, "lang", "language", "languageCode", default="")).lower()
        text = _first(item, "value", "name", "text")
        if text is not None and language in {"en", "eng", "english"}:
            return str(text)
    for item in entries:
        text = _first(item, "value", "name", "text")
        if text is not None:
            return str(text)
    return None


def normalize_tournament(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the observed native ChampionPoker container; preserve raw values."""
    grid = raw.get("tournamentGrid") if isinstance(raw.get("tournamentGrid"), dict) else raw
    upstream_id = _first(raw, "containerId", "tournamentId", "tournament_id", "id", "uniqueId", "gameId")
    if upstream_id is None:
        stable = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        upstream_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]

    start_ms = _first(
        grid,
        "startDate",
        "startDateTime",
        "startTime",
        "start_time",
        "scheduledStart",
        "originalStartDate",
    )
    start = start_ms
    if isinstance(start_ms, (int, float)):
        timestamp = start_ms / 1000 if start_ms > 10_000_000_000 else start_ms
        start = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    buyin = _first(grid, "buyIn", "buyin", "buy_in", "totalBuyIn", "entryFee")
    fee = _first(grid, "fee", "rake", "buyInFee")
    fee_inclusive = _first(grid, "buyInFeeJackpotFee")
    total_buyin = fee_inclusive if isinstance(fee_inclusive, (int, float)) else (
        buyin + fee if isinstance(buyin, (int, float)) and isinstance(fee, (int, float)) else buyin
    )
    currency = _first(grid, "cur", "currency", "currencyCode", "buyInCurrency")
    knockout_fee = _first(grid, "knockOutFee", "knockoutFee")
    progression = _first(grid, "knockOutProgressionFactor")

    # Native 40001 batches expose knockOutProgressionFactor. A positive
    # progression factor is the observed PKO signal; mystery bounty is separate.
    mystery_bounty = _as_bool(_first(grid, "mysteryBountyTournament"))
    pko = _as_bool(_first(grid, "isPko", "isPKO", "progressiveKnockout"))
    is_knockout = isinstance(knockout_fee, (int, float)) and knockout_fee > 0
    if pko is None:
        if isinstance(progression, (int, float)) and progression > 0 and mystery_bounty is not True:
            pko = True
        elif mystery_bounty is True or not is_knockout:
            pko = False

    return {
        "id": f"ipoker_{upstream_id}",
        "upstream_id": str(upstream_id),
        "source": "ipoker",
        "room": "ChampionPoker",
        "room_id": "247",
        "name": str(
            _first(grid, "name", "tournamentName", "title", "displayName")
            or _translated_name(_first(grid, "nameTranslationsList"))
            or "iPoker tournament"
        ),
        "time_start": str(start or ""),
        "start_date_ms": start_ms,
        "buyin": buyin,
        "fee": fee,
        "total_buyin": total_buyin,
        "currency": currency,
        "gtd": _first(grid, "guaranteedPrize", "guarantee", "gtd", "prizePoolGuaranteed"),
        "prize_pool": _first(grid, "prizePool", "currentPrizePool"),
        "prize_pool_left": _first(grid, "prizePoolLeft"),
        "afs": _first(grid, "maxPlayers"),
        "registered_players": _first(grid, "registeredPlayers", "totalPlayers", "uniqueRegisteredPlayers"),
        "active_players": _first(grid, "activePlayers", "playersRemaining"),
        "table_size": _first(grid, "tableSize", "maxPlayersPerTable", "seats"),
        "game_type": _first(grid, "gameType"),
        "bet_type": _first(grid, "betType"),
        "status": _first(grid, "status", "tournamentStatus"),
        "color_index": _first(grid, "colorIndex"),
        "start_type": _first(grid, "startType"),
        "buyin_type": _first(grid, "buyInType"),
        "prize_type": _first(grid, "prizeType"),
        "tournament_type": _first(grid, "tournamentType"),
        "promo_tags": _first(grid, "promoTags", "tagList", default=[]),
        "is_pko": pko,
        "is_knockout": is_knockout,
        "knockout_fee": knockout_fee,
        "knockout_progression_factor": progression,
        "is_mystery_bounty": mystery_bounty,
        "is_bounty_in_tournament_money": _first(grid, "isBountyInTournamentMoney"),
        "is_battle_royale": _first(grid, "isBattleRoyale"),
        "is_satellite": _first(grid, "satellite"),
        "is_shootout": _first(grid, "shootout"),
        "is_fast_progress": _first(grid, "isFastProgress"),
        "speed_mode": _first(grid, "speedMode"),
        "late_registration": _first(grid, "lateRegistration"),
        "late_registration_status": _first(grid, "lateRegistrationStatus"),
        "registration_start_ms": _first(grid, "registrationStartDate"),
        "registration_end_ms": _first(grid, "registrationEndDate"),
        "late_registration_stop_ms": _first(grid, "lrStopTime"),
        "start_stack": _first(grid, "startStack"),
        "level_duration": _first(grid, "levelDuration"),
        "re_entry_enabled": _first(grid, "reEntryEnabled"),
        "multi_entry_enabled": _first(grid, "multiEntryEnabled"),
        "direct_registration_allowed": _first(grid, "directRegistrationAllowed"),
        "presumable_duration": _first(grid, "presumableDuration"),
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


def _merge_raw_values(previous: Any, incoming: Any) -> Any:
    """Apply a partial publisher update without erasing fields it did not send.

    PokerRender's TournamentMerger preserves old values for absent, null and
    empty-array fields. We mirror that behavior while retaining raw payloads.
    """
    if incoming is None or incoming == []:
        return previous
    if isinstance(previous, dict) and isinstance(incoming, dict):
        merged = dict(previous)
        for key, value in incoming.items():
            if value is None or value == []:
                continue
            merged[key] = _merge_raw_values(previous.get(key), value)
        return merged
    return incoming


def _merge_tournaments(tournaments: list[dict[str, Any]], collected_at: str | None, collector_version: str) -> dict[str, Any]:
    """Merge incremental TournamentPush batches by containerId."""
    store = _read_store()
    by_id = {
        str(item.get("id")): item
        for item in store.get("tournaments", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    received = 0
    for raw in tournaments:
        if not isinstance(raw, dict):
            continue
        item = normalize_tournament(raw)
        previous = by_id.get(item["id"])
        previous_raw = previous.get("raw") if isinstance(previous, dict) else None
        merged_raw = _merge_raw_values(previous_raw, raw) if isinstance(previous_raw, dict) else raw
        by_id[item["id"]] = normalize_tournament(merged_raw)
        received += 1
    if not by_id:
        raise HTTPException(status_code=422, detail="Коллектор не передал ни одного турнира")
    merged = list(by_id.values())[-MAX_TOURNAMENTS:]
    collected_at = collected_at or _utc_now()
    saved = {
        "source": "ChampionPoker iPoker",
        "updated_at": collected_at,
        "received_at": _utc_now(),
        "collector_version": collector_version,
        "tournaments": merged,
    }
    save_json_db(CATALOG_FILE, saved)
    return {
        "status": "merged",
        "updated_at": collected_at,
        "count": len(merged),
        "received": received,
        "file": os.path.basename(CATALOG_FILE),
    }


def _remove_tournaments(container_ids: list[Any]) -> dict[str, Any]:
    """Remove publisher-expired container ids from the active catalogue."""
    ids = {str(value) for value in container_ids if value is not None}
    if not ids:
        return {"removed": 0, "count": len(_read_store().get("tournaments", []))}
    store = _read_store()
    current = store.get("tournaments", [])
    kept = [
        item for item in current
        if str(item.get("upstream_id")) not in ids
        and str(item.get("id")) not in ids
        and str(item.get("id")) not in {f"ipoker_{value}" for value in ids}
    ]
    removed = len(current) - len(kept)
    if removed:
        store["tournaments"] = kept
        store["updated_at"] = _utc_now()
        store["received_at"] = _utc_now()
        save_json_db(CATALOG_FILE, store)
    return {"removed": removed, "count": len(kept)}


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

class CollectorImport(BaseModel):
    """Sanitized browser relay records; this endpoint never accepts WebSocket/auth frames."""
    tournaments: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    collector_version: str = "0.7.0-browser-relay"


_COLLECTOR_GRID_FIELDS = {
    "name", "nameTranslationsList", "originalStartDate", "startDate", "startDateTime",
    "startTime", "buyIn", "buyin", "buy_in", "totalBuyIn", "entryFee", "fee", "rake",
    "buyInFee", "buyInFeeJackpotFee", "cur", "currency", "currencyCode", "buyInCurrency",
    "guaranteedPrize", "guarantee", "gtd", "prizePool", "currentPrizePool", "prizePoolLeft",
    "maxPlayers", "registeredPlayers", "totalPlayers", "uniqueRegisteredPlayers",
    "activePlayers", "playersRemaining", "tableSize", "maxPlayersPerTable", "seats",
    "gameType", "betType", "status", "tournamentStatus", "colorIndex", "startType",
    "buyInType", "prizeType", "tournamentType", "tagList", "promoTags", "knockOutFee",
    "knockoutFee", "knockOutProgressionFactor", "mysteryBountyTournament",
    "isBountyInTournamentMoney", "isBattleRoyale", "satellite", "shootout",
    "isFastProgress", "speedMode", "lateRegistration", "lateRegistrationStatus",
    "registrationStartDate", "registrationEndDate", "lrStopTime", "startStack",
    "levelDuration", "reEntryEnabled", "multiEntryEnabled", "directRegistrationAllowed",
    "presumableDuration", "playersNeededToStart", "delayEndTime", "unregisterNotAllowed",
    "showLateRegTime", "finalTableSize", "currentTournamentLevel", "isBroadcast",
    "speedGameplay",
}


def _sanitize_collector_tournament(raw: Any) -> dict[str, Any] | None:
    """Server-side allowlist: discard every field not needed for tournament selection."""
    if not isinstance(raw, dict) or raw.get("containerId") is None:
        return None
    grid = raw.get("tournamentGrid")
    if not isinstance(grid, dict):
        return None
    clean_grid: dict[str, Any] = {}
    for key, value in grid.items():
        if key not in _COLLECTOR_GRID_FIELDS or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean_grid[key] = value
        elif key == "nameTranslationsList" and isinstance(value, list):
            clean_grid[key] = [
                {"lang": item.get("lang"), "value": item.get("value")}
                for item in value
                if isinstance(item, dict)
                and isinstance(item.get("lang"), str)
                and isinstance(item.get("value"), str)
            ]
        elif key in {"tagList", "promoTags"} and isinstance(value, list):
            clean_grid[key] = [
                item for item in value if isinstance(item, (str, int, float, bool))
            ]
    if not clean_grid:
        return None
    return {"containerId": raw["containerId"], "tournamentGrid": clean_grid}


@router.post("/api/admin/ipoker/capture")
def ingest_ipoker_capture(payload: RawCaptureBatch, x_parser_admin_token: str | None = Header(default=None)):
    """Accept only server-to-browser WebSocket frames; never store raw frames or auth material."""
    _require_admin(x_parser_admin_token)
    received_payloads = []
    accepted = 0
    ignored_outgoing = 0
    for frame in payload.frames:
        direction = str(frame.get("direction", "")).lower()
        if direction not in {"received", "in", "server"}:
            ignored_outgoing += 1
            continue
        data = frame.get("data")
        if data is None:
            continue
        received_payloads.append(data)
        accepted += 1

    if not accepted:
        raise HTTPException(status_code=422, detail="Пакет не содержит входящих WebSocket frames")

    tournaments, decode_report = extract_tournaments(received_payloads)
    catalog_result = None
    if tournaments:
        catalog_result = _merge_tournaments(tournaments, _utc_now(), "0.4.0-live-capture")

    # Deliberately persist only aggregate diagnostics. Raw Socket.IO frames can
    # contain session identifiers and are never written to disk or returned.
    return {
        "status": "captured",
        "accepted_incoming": accepted,
        "ignored_outgoing": ignored_outgoing,
        "catalog": catalog_result,
        "decode": decode_report,
        "received_at": _utc_now(),
    }


@router.post("/api/collector/ipoker/ingest")
def ingest_browser_collector(
    payload: CollectorImport,
    x_collector_token: str | None = Header(default=None),
):
    """Receive only allowlisted tournament records from the local browser relay."""
    expected = os.getenv("IPOKER_CAPTURE_TOKEN", "")
    if not expected or not x_collector_token or not hmac.compare_digest(x_collector_token, expected):
        raise HTTPException(status_code=401, detail="Недействительный токен сборщика")
    clean = [
        item for item in (_sanitize_collector_tournament(raw) for raw in payload.tournaments)
        if item is not None
    ]
    if not clean:
        raise HTTPException(status_code=422, detail="Не найдено допустимых записей турниров")
    result = _merge_tournaments(clean, _utc_now(), payload.collector_version)
    return {
        **result,
        "accepted": len(clean),
        "rejected": len(payload.tournaments) - len(clean),
        "source": "ChampionPoker browser relay",
    }


@router.get("/api/admin/ipoker/capture/status")
def get_ipoker_capture_status(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    store = _read_store()
    return {
        "catalog_count": len(store.get("tournaments", [])),
        "catalog_updated_at": store.get("updated_at"),
        "source": "ChampionPoker iPoker live capture",
    }


# Assisted WebSocket worker controls. Runtime auth material is never persisted or returned.
from .ipoker_worker import IPokerWorker
from .ipoker_login import CaptchaRequired, LoginProtocolError, build_auth_message, login_and_get_temp_token
_worker = IPokerWorker(_merge_tournaments, _remove_tournaments)

class WorkerStart(BaseModel):
    auth_message: dict[str, Any]

@router.post("/api/admin/ipoker/worker/start")
def start_ipoker_worker(payload: WorkerStart, x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    try:
        return _worker.start(payload.auth_message)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

@router.post("/api/admin/ipoker/worker/stop")
def stop_ipoker_worker(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    return _worker.stop()

@router.get("/api/admin/ipoker/worker/status")
def get_ipoker_worker_status(x_parser_admin_token: str | None = Header(default=None)):
    _require_admin(x_parser_admin_token)
    return _worker.status()



@router.post("/api/admin/ipoker/worker/login")
def login_and_start_ipoker_worker(x_parser_admin_token: str | None = Header(default=None)):
    """Log in with the server-side admin account and start the live collector."""
    _require_admin(x_parser_admin_token)
    try:
        login = login_and_get_temp_token()
        auth_message = build_auth_message(login)
        result = _worker.start(auth_message)
        # Never return the token, credentials, or full auth envelope.
        return {
            **result,
            "authentication": "token_obtained",
            "session_cookie_count": login.get("session_cookie_count", 0),
        }
    except CaptchaRequired as exc:
        raise HTTPException(status_code=428, detail=str(exc))
    except LoginProtocolError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
