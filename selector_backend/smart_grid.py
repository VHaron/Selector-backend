"""Server-side smart tournament schedule optimizer.

The engine only optimizes the tournament snapshot sent by the frontend. It never
fetches tournaments from Globus and never mutates the user's saved/manual grid.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any
import re


ROOM_TARGETS = {"gg": 0.425, "ipoker": 0.30, "other": 0.275}


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"[^0-9,.-]", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(",", "" if len(tail) == 3 else ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _currency(tournament: dict, settings: dict) -> tuple[str, float]:
    source = f"{tournament.get('room', '')} {tournament.get('buyin', '')}".lower()
    if any(x in source for x in ("€", "eur", "euro")):
        return "EUR", float(settings.get("usd_per_eur", 1.08))
    if any(x in source for x in ("₽", "rub", "руб")):
        return "RUB", float(settings.get("usd_per_rub", 0.011))
    room = str(tournament.get("room", "")).lower()
    if any(x in room for x in ("winamax", "wmx", "ps(es)", "pokerstars.fr", "ipoker", "redstar")):
        return "EUR", float(settings.get("usd_per_eur", 1.08))
    return "USD", 1.0


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except ValueError:
            continue
    return None


def _duration_minutes(value: Any) -> float:
    raw = str(value or "").strip().lower().replace(",", ".")
    if not raw:
        return 0.0
    clock = re.fullmatch(r"(\d{1,3}):(\d{1,2})", raw)
    if clock:
        return int(clock.group(1)) * 60 + int(clock.group(2))
    hours = re.search(r"([\d.]+)\s*(?:h|ч(?:ас(?:а|ов)?)?)", raw)
    minutes = re.search(r"([\d.]+)\s*(?:m|min|мин|м(?:ин(?:ут(?:а|ы)?)?)?)", raw)
    if hours or minutes:
        return (float(hours.group(1)) * 60 if hours else 0.0) + (float(minutes.group(1)) if minutes else 0.0)
    amount = _number(raw)
    return amount * 60 if amount > 0 else 0.0


def _room_group(room: Any) -> str:
    name = re.sub(r"[^a-z0-9]", "", str(room or "").lower())
    if "gg" in name:
        return "gg"
    if "ipoker" in name or "redstar" in name:
        return "ipoker"
    return "other"


def _format_flags(name: Any) -> tuple[bool, bool, bool]:
    text = str(name or "").lower()
    return ("pko" in text or "progressive" in text,
            "turbo" in text or "hyper" in text,
            "freezeout" in text or "regular" in text)


def _score(t: dict, duration_minutes: float, buyin_usd: float) -> float:
    buyin = max(0.01, buyin_usd)
    ev = _number(t.get("ev_mtt"))
    roi_raw = str(t.get("roi_str") or "")
    roi = _number(roi_raw) / 100.0 if "%" in roi_raw else _number(roi_raw)
    gtd = _number(t.get("gtd"))
    field = max(1.0, _number(t.get("afs")))
    overlay = min(1.0, gtd / max(1.0, buyin * field)) if gtd > 0 else 0.0
    priority = {"1": 1.0, "2": 0.55, "3": 0.2}.get(str(t.get("priority", "3")), 0.0)
    # EV is an available estimate, not a guaranteed or independently verified result.
    ev_ratio = max(-1.0, min(2.0, ev / buyin))
    roi_score = max(-1.0, min(2.0, roi))
    duration_score = max(0.0, 1.0 - duration_minutes / 600.0)
    return ev_ratio * 35 + roi_score * 25 + overlay * 15 + priority * 15 + duration_score * 10


def _peak_tables(intervals: list[tuple[datetime, datetime]]) -> int:
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    active = peak = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def optimize_smart_grid(payload: dict) -> dict:
    settings = payload["settings"]
    candidates = payload.get("tournaments", [])
    locked = payload.get("locked", [])
    budget = max(0, round(float(settings.get("budget_usd", 0)) * 100))
    max_tables = max(1, min(24, int(settings.get("max_tables", 8))))
    session_start = _parse_datetime(settings.get("session_start"))
    session_end = _parse_datetime(settings.get("session_end"))
    if not session_start or not session_end or session_end <= session_start:
        raise ValueError("Некорректный интервал игровой сессии")
    if len(candidates) > 800 or len(locked) > 300:
        raise ValueError("Слишком большой список турниров для одного расчёта")

    pool = candidates + locked
    durations: dict[int, float] = {}
    by_room: dict[str, list[float]] = {}
    for item in pool:
        duration = _duration_minutes(item.get("duration"))
        if duration > 0:
            by_room.setdefault(str(item.get("room", "")).lower(), []).append(duration)
    for index, item in enumerate(pool):
        duration = _duration_minutes(item.get("duration"))
        if duration <= 0:
            room_values = by_room.get(str(item.get("room", "")).lower(), [])
            duration = median(room_values) if room_values else 0
        durations[index] = duration

    locked_items = []
    locked_intervals = []
    spent_locked = 0
    for item in locked:
        start = _parse_datetime(item.get("smart_start_at") or item.get("time_start"))
        duration = durations[pool.index(item)] if item in pool else 0
        _, rate = _currency(item, settings)
        price = max(0, round(_number(item.get("buyin")) * rate * 100))
        spent_locked += price
        if start and duration > 0:
            locked_intervals.append((start, start + timedelta(minutes=duration)))
        locked_items.append(item)

    if spent_locked > budget:
        raise ValueError("Уже добавленные вручную турниры превышают указанный бюджет")

    ranked = []
    seen = set()
    for index, item in enumerate(candidates):
        identifier = str(item.get("id") or f"{item.get('room')}|{item.get('name')}|{item.get('time_start')}")
        if identifier in seen:
            continue
        seen.add(identifier)
        start = _parse_datetime(item.get("smart_start_at") or item.get("time_start"))
        duration = durations[index]
        _, rate = _currency(item, settings)
        price = round(_number(item.get("buyin")) * rate * 100)
        if not start or duration <= 0 or price <= 0:
            continue
        end = start + timedelta(minutes=duration)
        if start < session_start or end > session_end:
            continue
        if price / 100 > 5.500001 or price + spent_locked > budget:
            continue
        ranked.append({
            "item": item, "start": start, "end": end, "price": price,
            "score": _score(item, duration, price / 100),
            "group": _room_group(item.get("room")),
        })

    ranked.sort(key=lambda x: (x["start"], -x["score"]))
    locked_peak = _peak_tables(locked_intervals)
    if locked_peak > max_tables:
        raise ValueError("Текущая сетка уже превышает ограничение по одновременным столам")

    # Beam-search over include/skip decisions. This evaluates combinations rather
    # than greedily taking the next highest-rated tournament.
    width = 220
    states = [((), spent_locked, tuple(locked_intervals), 0.0)]
    for candidate in ranked:
        next_states = list(states)
        for chosen, spent, intervals, score in states:
            if spent + candidate["price"] > budget:
                continue
            proposed = list(intervals) + [(candidate["start"], candidate["end"])]
            if _peak_tables(proposed) > max_tables:
                continue
            next_states.append((
                chosen + (candidate,), spent + candidate["price"],
                tuple(proposed), score + candidate["score"]
            ))
        def state_value(state):
            chosen, spent, _, score = state
            count = len(chosen) + len(locked_items)
            counts = {"gg": 0, "ipoker": 0, "other": 0}
            for entry in chosen:
                counts[entry["group"]] += 1
            for item in locked_items:
                counts[_room_group(item.get("room"))] += 1
            if count:
                deviation = sum(abs(counts[key] / count - target) for key, target in ROOM_TARGETS.items())
            else:
                deviation = 0
            usage = spent / max(1, budget)
            return score + usage * 4 - deviation * 9
        next_states.sort(key=state_value, reverse=True)
        states = next_states[:width]

    best = max(states, key=lambda state: state_value(state))
    chosen, spent, intervals, score = best
    all_intervals = list(intervals)
    room_counts = {"gg": 0, "ipoker": 0, "other": 0}
    for item in locked_items + [entry["item"] for entry in chosen]:
        room_counts[_room_group(item.get("room"))] += 1
    peak = _peak_tables(all_intervals)
    selected = []
    for entry in sorted(chosen, key=lambda entry: entry["start"]):
        clean_item = dict(entry["item"])
        clean_item.pop("smart_start_at", None)
        selected.append(clean_item)
    end_at = max((end for _, end in all_intervals), default=None)
    return {
        "status": "success",
        "selected": selected,
        "summary": {
            "selected_count": len(selected),
            "locked_count": len(locked_items),
            "spent_usd": round(spent / 100, 2),
            "remaining_usd": round(max(0, budget - spent) / 100, 2),
            "peak_tables": peak,
            "estimated_end": end_at.isoformat(timespec="minutes") if end_at else None,
            "room_counts": room_counts,
            "candidates_considered": len(ranked),
            "candidates_rejected": max(0, len(candidates) - len(ranked)),
            "objective_score": round(score, 2),
            "optimizer": "beam-search-v1",
        },
    }
