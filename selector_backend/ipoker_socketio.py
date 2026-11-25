"""Decode and build ChampionPoker/iPoker Socket.IO lobby messages.

This module handles protocol frames only. It does not authenticate, store credentials,
or establish a network connection.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

DEFAULT_TOURNAMENT_FILTER = (
    "@game_type@ in (1, 2, 18, 6, 3, 16, 19, 13, 5, 14, 20, 21) "
    "and @start_type@=1"
)


def _loads_socket_frame(frame: Any) -> Any | None:
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(frame, str):
        return frame if isinstance(frame, (dict, list)) else None
    text = frame.strip()
    # ChampionPoker browser capture uses the legacy Socket.IO message packet
    # (3:::<JSON>), not the newer event packet (42["message", ...]).
    if text.startswith("3:::"):
        text = text[4:]
    elif text.startswith("42"):
        text = text[2:]
    elif text.startswith("4") and len(text) > 1 and text[1] in "[{":
        text = text[1:]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def decode_application_message(frame: Any) -> dict[str, Any] | None:
    """Decode a Socket.IO 'message' event into its application object."""
    packet = _loads_socket_frame(frame)
    if isinstance(packet, dict):
        return packet
    if not isinstance(packet, list) or len(packet) < 2 or packet[0] != "message":
        return None
    payload = packet[1]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def encode_application_message(message: dict[str, Any]) -> str:
    """Encode an application object as the observed Engine.IO v3 Socket.IO event."""
    if not isinstance(message, dict):
        raise TypeError("message must be a dict")
    # Match the exact legacy packet observed in ChampionPoker DevTools:
    # 3:::{"ID":33036,...}
    return "3:::" + json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def build_tournament_subscription(
    request_id: str = "Lobby_FilterSubscription_7",
    filter_expression: str = DEFAULT_TOURNAMENT_FILTER,
) -> dict[str, Any]:
    """Build the observed classId 50017 tournament-container subscription."""
    if not request_id or not isinstance(request_id, str):
        raise ValueError("request_id must be a non-empty string")
    if not filter_expression or not isinstance(filter_expression, str):
        raise ValueError("filter_expression must be a non-empty string")
    return {
        "subscriptionType": 1,
        "containerType": 1,
        "filter": {"value": filter_expression, "condition": None},
        "fun": False,
        "requestId": request_id,
        "subContainerType": 4,
        "classId": 50017,
    }


def build_tournament_subscription_frame(
    request_id: str = "Lobby_FilterSubscription_7",
    filter_expression: str = DEFAULT_TOURNAMENT_FILTER,
) -> str:
    """Return the encoded Socket.IO frame for a 50017 subscription."""
    return encode_application_message(
        build_tournament_subscription(request_id, filter_expression)
    )


def parse_tournament_subscription_ack(frame: Any) -> dict[str, Any] | None:
    """Return a normalized 50018 acknowledgement, or None for another message."""
    message = decode_application_message(frame)
    if not message or message.get("classId") != 50018:
        return None
    error_code = message.get("errorCode")
    return {
        "request_id": message.get("requestId"),
        "error_code": error_code,
        "accepted": error_code == 0,
        "message": message.get("message"),
        "message_seq_id": message.get("messageSeqId"),
    }


def is_tournament_catalogue_frame(frame: Any) -> bool:
    """Whether a decoded application frame is a 40001 catalogue batch."""
    message = decode_application_message(frame)
    return bool(message and message.get("classId") == 40001)


def decode_socketio_frame(frame: Any) -> dict[str, Any] | None:
    """Return decoded iPoker catalogue message, or None for unrelated frames."""
    message = decode_application_message(frame)
    if message and isinstance(message.get("tournamentsList"), list):
        return message
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
