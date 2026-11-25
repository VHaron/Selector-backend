"""Decode ChampionPoker/iPoker Socket.IO tournament lobby messages.

Accepts Engine.IO/Socket.IO text frames (not credentials or cookies), extracts
tournamentsList, and preserves each complete upstream record for auditing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def _loads_socket_frame(frame: Any) -> Any | None:
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(frame, str):
        return frame if isinstance(frame, (dict, list)) else None
    text = frame.strip()
    # Engine.IO packet type 4 + Socket.IO event packet type 2.
    if text.startswith("42"):
        text = text[2:]
    elif text.startswith("4") and len(text) > 1 and text[1] in "[{":
        text = text[1:]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def decode_socketio_frame(frame: Any) -> dict[str, Any] | None:
    """Return decoded iPoker application message, or None for unrelated frames."""
    packet = _loads_socket_frame(frame)
    if isinstance(packet, dict):
        if isinstance(packet.get("tournamentsList"), list):
            return packet
        return None
    if not isinstance(packet, list) or len(packet) < 2 or packet[0] != "message":
        return None
    payload = packet[1]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict) and isinstance(payload.get("tournamentsList"), list):
        return payload
    return None


def extract_tournaments(frames: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract records and de-duplicate by containerId; the latest observation wins."""
    by_id: dict[str, dict[str, Any]] = {}
    messages = 0
    ignored = 0
    occurrences = 0
    for frame in frames:
        message = decode_socketio_frame(frame)
        if message is None:
            ignored += 1
            continue
        messages += 1
        for item in message.get("tournamentsList", []):
            occurrences += 1
            if not isinstance(item, dict) or not isinstance(item.get("tournamentGrid"), dict):
                continue
            upstream_id = item.get("containerId")
            if upstream_id is None:
                stable = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                upstream_id = "hash_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
            by_id[str(upstream_id)] = item
    result = list(by_id.values())
    return result, {
        "messages_decoded": messages,
        "frames_ignored": ignored,
        "tournament_occurrences": occurrences,
        "unique_tournaments": len(result),
    }
