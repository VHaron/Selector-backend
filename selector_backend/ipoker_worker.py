"""Assisted in-memory ChampionPoker Socket.IO collector. Auth is never persisted."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import socketio

from .ipoker_socketio import build_tournament_subscription, extract_tournaments

SOCKET_URL = "https://schannel.ipoker.com"
ORIGIN = "https://games.championpoker.com"
REFERER = (
    "https://games.championpoker.com/poker/web/26.7.1.26/html/poker/"
    "index.html?launcherRedirect=true&hostedMode=3"
)
CASINO = "championpoker"
CLIENT_VERSION = "26.7.1.26"
logger = logging.getLogger("ipoker.worker")

REQUIRED_AUTH_FIELDS = {
    "casinoName",
    "usernameOrNickname",
    "usernameLogin",
    "languageCode",
    "tempToken",
    "clientPlatform",
    "clientVersion",
    "realMode",
    "supportedPokerServices",
    "userAgent",
    "classId",
}


def validate_auth_message(payload: dict[str, Any]) -> None:
    """Validate a native PokerLoginRequest, never the obsolete Hub2 envelope."""
    if not isinstance(payload, dict):
        raise ValueError("auth_message must be a JSON object")
    missing = sorted(REQUIRED_AUTH_FIELDS - payload.keys())
    if missing:
        raise ValueError("Missing required auth fields: " + ", ".join(missing))
    if payload.get("classId") != 60108:
        raise ValueError("auth_message.classId must be 60108 (PokerLoginRequest)")
    for key in ("casinoName", "usernameOrNickname", "languageCode", "tempToken", "userAgent"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"auth_message.{key} must be a non-empty string")
    if payload.get("usernameLogin") is not True:
        raise ValueError("auth_message.usernameLogin must be true")
    if not isinstance(payload.get("supportedPokerServices"), list):
        raise ValueError("auth_message.supportedPokerServices must be a list")


def build_register_client_request(auth: dict[str, Any]) -> dict[str, Any]:
    """Mirror SessionEngine.doRegisterClientRequest() from PokerRender.bundle.js."""
    client_type = int(auth.get("clientPlatform", 26))
    return {
        "casinoShortName": None,
        "languageCode": "EN",
        "clientType": client_type,
        "previewClient": False,
        "oldClientVersion": None,
        "nickName": None,
        "sessionId": None,
        "isBeginner": False,
        "isFun": False,
        "clientPlatform": client_type,
        "clientVersion": str(auth.get("clientVersion") or CLIENT_VERSION),
        "subscibeLobbyContentRequest": False,
        "subscribeClientServices": True,
        "classId": 50001,
        "casinoName": CASINO,
        "casinoSkin": CASINO,
    }


class AuthenticationRejected(Exception):
    """Permanent upstream auth rejection; a fresh temporary token is required."""


class IPokerWorker:
    """Socket.IO 2 / Engine.IO 3 client, matching the bundled browser transport."""

    def __init__(self, save_callback, remove_callback=None):
        self.save_callback = save_callback
        self.remove_callback = remove_callback
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.auth = None
        self.sio = None
        self.incoming = queue.Queue()
        self.disconnect_event = threading.Event()
        self.state = {
            "running": False,
            "connected": False,
            "authenticated": False,
            "subscribed": False,
            "last_message_at": None,
            "last_catalog_at": None,
            "catalog_count": 0,
            "last_error": None,
            "reconnects": 0,
        }

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def start(self, auth, cookie_header=None):
        # The browser uses a Socket.IO message event with JSON object payloads.
        # Login-site cookies are not forwarded to schannel.ipoker.com.
        validate_auth_message(auth)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("Collector is already running")
            self.auth = dict(auth)
            self.stop_event.clear()
            self.disconnect_event.clear()
            self.incoming = queue.Queue()
            self.set(
                running=True,
                connected=False,
                authenticated=False,
                subscribed=False,
                last_error=None,
            )
            self.thread = threading.Thread(
                target=self.run,
                daemon=True,
                name="ipoker-ws-collector",
            )
            self.thread.start()
        return {"status": "starting"}

    def stop(self):
        self.stop_event.set()
        client = self.sio
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        self.set(running=False)
        return {"status": "stopping"}

    def _on_message(self, payload):
        # Native server responses arrive as JSON strings; tolerate object payloads too.
        self.incoming.put(payload)

    def _on_disconnect(self):
        self.disconnect_event.set()
        self.set(connected=False, subscribed=False)

    def _headers(self):
        return {
            "Origin": ORIGIN,
            "Referer": REFERER,
            "User-Agent": str(self.auth.get("userAgent", "Mozilla/5.0")),
        }

    def _open_socketio(self):
        self.disconnect_event.clear()
        self.incoming = queue.Queue()
        sio = socketio.Client(
            reconnection=False,
            logger=False,
            engineio_logger=False,
            request_timeout=12,
        )
        sio.on("message", self._on_message)
        sio.on("disconnect", self._on_disconnect)
        # The actual PokerRender bundle uses Engine.IO protocol 3 and prefers
        # direct WebSocket transport before polling. Force that same first path.
        sio.connect(
            SOCKET_URL,
            headers=self._headers(),
            transports=["websocket"],
            socketio_path="socket.io",
        )
        self.sio = sio
        if not sio.connected:
            raise ConnectionError("Socket.IO namespace did not connect")
        self.set(connected=True, last_error=None)
        logger.info("ChampionPoker Socket.IO namespace connected using native client")
        return sio

    def _send(self, message):
        client = self.sio
        if client is None or not client.connected:
            raise ConnectionError("Socket.IO disconnected before application send")
        # Client.send() is the exact API used by PokerRender's SocketIOConnection.
        client.send(message)

    @staticmethod
    def _decode_message(payload):
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except (ValueError, TypeError):
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    @staticmethod
    def _class_id(message):
        return message.get("ID", message.get("classId"))

    def _process_message(self, message):
        class_id = self._class_id(message)
        if class_id == 60005:
            self._send({"messageSeqId": 0, "classId": 60006})
            return

        if class_id == 50006 and self.remove_callback:
            ids = message.get("containerIdsList") or []
            if ids:
                result = self.remove_callback(ids)
                logger.info("iPoker catalogue removals processed: count=%s", result.get("removed", 0))

        # Feed one native 'message' event into the common parser.
        records, report = extract_tournaments([["message", message]])
        if records:
            saved = self.save_callback(records, None, "0.6.0-socketio-client")
            self.set(
                last_catalog_at=datetime.now(timezone.utc).isoformat(),
                catalog_count=saved.get("count", len(records)),
                last_error=None,
            )
            logger.info(
                "iPoker tournament push processed: batches=%s occurrences=%s unique=%s catalogue=%s",
                report.get("messages_decoded", 0),
                report.get("tournament_occurrences", 0),
                report.get("unique_tournaments", 0),
                saved.get("count", len(records)),
            )

    def _wait_for(self, class_ids, timeout=25, check_error=False):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            if self.sio is None or not self.sio.connected or self.disconnect_event.is_set():
                raise ConnectionError("ChampionPoker disconnected the Socket.IO namespace")
            try:
                payload = self.incoming.get(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
            except queue.Empty:
                continue
            message = self._decode_message(payload)
            if not message:
                logger.info("iPoker ignored non-JSON message event")
                continue
            incoming_id = self._class_id(message)
            error_code = message.get("errorCode", 0)
            logger.info(
                "iPoker protocol response: classId=%s errorCode=%s",
                incoming_id,
                str(error_code)[:24],
            )
            self._process_message(message)
            if incoming_id not in class_ids:
                continue
            if check_error and str(error_code).strip() not in {"0", "0.0", "None"}:
                logger.warning(
                    "iPoker protocol request rejected: expected=%s response=%s",
                    ",".join(str(value) for value in sorted(class_ids)),
                    str(error_code)[:24],
                )
                if 60109 in class_ids:
                    raise AuthenticationRejected("ChampionPoker PokerLoginRequest was rejected")
                raise ConnectionError(f"Upstream rejected classId={next(iter(class_ids))}")
            return message
        raise TimeoutError(
            "Timed out waiting for upstream classId="
            + ",".join(str(value) for value in sorted(class_ids))
        )

    def connect_once(self):
        sio = self._open_socketio()
        try:
            logger.info("Socket.IO connected; sending ClientServerHandshakeRequest (60200)")
            self._send({
                "flag": 0,
                "casinoName": CASINO,
                "classId": 60200,
            })
            self._wait_for({60201}, timeout=20, check_error=True)
            logger.info("Server handshake accepted (60201); registering client (50001)")

            self._send(build_register_client_request(self.auth))
            self._wait_for({50003}, timeout=20, check_error=True)
            logger.info("Client registered (50003); sending native PokerLoginRequest (60108)")

            self._send(self.auth)
            self._wait_for({60109}, timeout=35, check_error=True)
            self.set(authenticated=True)
            logger.info("ChampionPoker PokerLoginResponse accepted (60109)")

            request_id = "Lobby_FilterSubscription_" + uuid.uuid4().hex
            self._send(build_tournament_subscription(request_id=request_id))
            ack = self._wait_for({50018}, timeout=25, check_error=True)
            self.set(subscribed=True)
            logger.info(
                "Scheduled tournament filter subscription accepted (50018); request_id_matches=%s",
                ack.get("requestId") == request_id,
            )

            next_ping = time.monotonic() + 25
            while not self.stop_event.is_set():
                if not sio.connected or self.disconnect_event.is_set():
                    raise ConnectionError("ChampionPoker Socket.IO disconnected during catalogue stream")
                timeout = max(0.1, next_ping - time.monotonic())
                try:
                    payload = self.incoming.get(timeout=min(1.0, timeout))
                except queue.Empty:
                    if time.monotonic() >= next_ping:
                        self._send({"messageSeqId": 0, "classId": 60005})
                        next_ping = time.monotonic() + 25
                    continue
                message = self._decode_message(payload)
                if message:
                    self.set(last_message_at=datetime.now(timezone.utc).isoformat())
                    self._process_message(message)
        finally:
            try:
                if sio.connected:
                    sio.disconnect()
            except Exception:
                pass
            self.sio = None

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.connect_once()
                except AuthenticationRejected:
                    logger.error("iPoker upstream authentication rejected")
                    self.set(
                        connected=False,
                        authenticated=False,
                        subscribed=False,
                        running=False,
                        last_error="AuthenticationRejected",
                    )
                    break
                except Exception as exc:
                    logger.warning(
                        "iPoker collector connection failed: %s",
                        type(exc).__name__,
                    )
                    self.set(
                        connected=False,
                        authenticated=False,
                        subscribed=False,
                        last_error=type(exc).__name__,
                    )
                    if self.stop_event.wait(10):
                        break
                    self.set(reconnects=self.status()["reconnects"] + 1)
        finally:
            with self.lock:
                self.auth = None
                self.sio = None
                self.state.update(
                    running=False,
                    connected=False,
                    authenticated=False,
                    subscribed=False,
                )
