"""Assisted in-memory ChampionPoker Socket.IO collector. Auth material is never persisted."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
import websocket

from .ipoker_socketio import (
    build_tournament_subscription,
    decode_application_message,
    encode_application_message,
    extract_tournaments,
)

WS_URL = "wss://schannel.ipoker.com/socket.io/"
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


def build_register_client_request() -> dict[str, Any]:
    """Mirror SessionEngine.doRegisterClientRequest() from PokerRender.bundle.js."""
    return {
        "casinoShortName": None,
        "languageCode": "EN",
        "clientType": 26,
        "previewClient": False,
        "oldClientVersion": None,
        "nickName": None,
        "sessionId": None,
        "isBeginner": False,
        "isFun": False,
        "clientPlatform": 26,
        "clientVersion": CLIENT_VERSION,
        "subscibeLobbyContentRequest": False,
        "subscribeClientServices": True,
        "classId": 50001,
        "casinoName": CASINO,
        "casinoSkin": CASINO,
    }


class AuthenticationRejected(Exception):
    """Permanent upstream auth rejection; a fresh token is required."""


class IPokerWorker:
    def __init__(self, save_callback, remove_callback=None):
        self.save_callback = save_callback
        self.remove_callback = remove_callback
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.auth = None
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
        self._heartbeat_count = 0
        self._transport_frame_count = 0
        self._last_subscription_ack = None

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def start(self, auth, cookie_header=None):
        # Cookies from login.championpoker.com are deliberately not forwarded
        # to schannel.ipoker.com. PokerLoginRequest authenticates with tempToken.
        validate_auth_message(auth)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("Collector is already running")
            self.auth = dict(auth)
            self.stop_event.clear()
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
        self.set(running=False)
        return {"status": "stopping"}

    def _headers(self):
        return [
            "Origin: " + ORIGIN,
            "Referer: " + REFERER,
            "User-Agent: " + str(self.auth.get("userAgent", "Mozilla/5.0")),
        ]

    def recv(self, ws, timeout=25):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            if time.monotonic() >= deadline:
                raise TimeoutError("Upstream timeout")
            try:
                frame = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if frame is None:
                raise ConnectionError("Socket closed")
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8", "replace")
            if frame == "2":
                ws.send("3")
                continue
            if isinstance(frame, str) and frame.startswith("2"):
                ws.send("3" + frame[1:])
                continue
            if frame == "41" or (isinstance(frame, str) and frame.startswith("41::")):
                raise ConnectionError("Socket.IO namespace disconnected")
            self._transport_frame_count += 1
            packet_prefix = frame[:2] if isinstance(frame, str) else "binary"
            logger.info(
                "iPoker upstream frame received; packet=%s length=%s count=%s",
                packet_prefix,
                len(frame) if hasattr(frame, "__len__") else 0,
                self._transport_frame_count,
            )
            self.set(last_message_at=datetime.now(timezone.utc).isoformat())
            return frame
        raise RuntimeError("Stopped")

    def _open_engineio3_direct(self):
        """Use the transport order in PokerRender: websocket first, polling second."""
        ws_url = WS_URL + "?EIO=3&transport=websocket&t=" + str(int(time.time() * 1000))
        ws = websocket.create_connection(
            ws_url,
            timeout=12,
            header=self._headers(),
            enable_multithread=True,
        )
        try:
            opening = ws.recv()
            if isinstance(opening, bytes):
                opening = opening.decode("utf-8", "replace")
            if not isinstance(opening, str) or not opening.startswith("0"):
                raise ConnectionError("Direct Engine.IO websocket did not return an opening packet")
            try:
                handshake = json.loads(opening[1:])
            except (ValueError, TypeError):
                raise ConnectionError("Direct Engine.IO opening packet was malformed") from None
            if not isinstance(handshake, dict) or not handshake.get("sid"):
                raise ConnectionError("Direct Engine.IO opening packet omitted session id")
            self._engineio_sid = str(handshake["sid"])
            self._engineio_ping_interval = int(handshake.get("pingInterval", 25000))
            ws.send("40")
            namespace = self.recv(ws, 12)
            if namespace != "40":
                raise ConnectionError("Socket.IO namespace connection was not accepted")
            logger.info("ChampionPoker direct Engine.IO v3 websocket and namespace connected")
            return ws
        except Exception:
            try:
                ws.close()
            except Exception:
                pass
            raise

    def _open_engineio3_polling(self):
        """Fallback transport, matching the same EIO=3 Socket.IO protocol."""
        session = requests.Session()
        session.headers.update({
            "Origin": ORIGIN,
            "Referer": REFERER,
            "User-Agent": str(self.auth.get("userAgent", "Mozilla/5.0")),
            "Accept": "*/*",
        })
        poll_url = WS_URL.replace("wss://", "https://") + (
            "?EIO=3&transport=polling&t=" + str(int(time.time() * 1000))
        )
        response = session.get(poll_url, timeout=12)
        response.raise_for_status()
        opening = response.text
        if not isinstance(opening, str) or not opening.startswith("0"):
            logger.warning(
                "iPoker polling handshake returned a non-Engine.IO response; status=%s content_type=%s body_length=%s",
                response.status_code,
                response.headers.get("Content-Type", "")[:80],
                len(opening) if isinstance(opening, str) else 0,
            )
            raise ConnectionError("Engine.IO polling opening packet was not received")
        try:
            handshake = json.loads(opening[1:])
        except (ValueError, TypeError):
            raise ConnectionError("Engine.IO polling handshake was malformed") from None
        sid = handshake.get("sid") if isinstance(handshake, dict) else None
        if not sid:
            raise ConnectionError("Engine.IO polling handshake omitted session id")
        ws_url = (
            WS_URL
            + "?EIO=3&transport=websocket&sid="
            + str(sid)
            + "&t="
            + str(int(time.time() * 1000))
        )
        ws = websocket.create_connection(
            ws_url,
            timeout=12,
            header=self._headers(),
            enable_multithread=True,
        )
        try:
            ws.send("2probe")
            probe = ws.recv()
            if isinstance(probe, bytes):
                probe = probe.decode("utf-8", "replace")
            if probe != "3probe":
                raise ConnectionError("Engine.IO websocket upgrade probe was not accepted")
            ws.send("5")
            ws.send("40")
            namespace = self.recv(ws, 12)
            if namespace != "40":
                raise ConnectionError("Socket.IO namespace connection was not accepted")
            logger.info("ChampionPoker Engine.IO v3 polling fallback connected")
            return ws
        except Exception:
            try:
                ws.close()
            except Exception:
                pass
            raise

    def _open_engineio3(self):
        logger.info("Opening ChampionPoker Socket.IO: websocket transport first")
        try:
            return self._open_engineio3_direct()
        except Exception as direct_error:
            logger.warning(
                "Direct iPoker websocket attempt failed: %s; trying Engine.IO polling fallback",
                type(direct_error).__name__,
            )
        try:
            return self._open_engineio3_polling()
        except Exception as polling_error:
            raise ConnectionError(
                "Both Engine.IO transports failed "
                f"(websocket={type(direct_error).__name__}, polling={type(polling_error).__name__})"
            ) from None

    @staticmethod
    def _class_id(message):
        return message.get("ID", message.get("classId"))

    def _send(self, ws, message):
        ws.send(encode_application_message(message))

    def _process_frame(self, ws, frame):
        message = decode_application_message(frame)
        if message:
            class_id = self._class_id(message)
            if class_id == 60005:
                # PokerRender's NetworkAliveTimeOutPingHandler replies with 60006.
                self._send(ws, {
                    "messageSeqId": message.get("messageSeqId", 0),
                    "classId": 60006,
                })
                self._heartbeat_count += 1
            if class_id == 50006 and self.remove_callback:
                ids = message.get("containerIdsList") or []
                if ids:
                    result = self.remove_callback(ids)
                    logger.info("iPoker catalogue removals processed: count=%s", result.get("removed", 0))
        records, report = extract_tournaments([frame])
        if records:
            saved = self.save_callback(records, None, "0.5.0-native-protocol")
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
        return message

    def wait_for(self, ws, class_ids, timeout=25, check_error=False):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            remaining = max(1, deadline - time.monotonic())
            try:
                frame = self.recv(ws, remaining)
            except TimeoutError:
                break
            message = self._process_frame(ws, frame)
            if not message:
                continue
            incoming_id = self._class_id(message)
            if incoming_id not in class_ids:
                logger.info("iPoker protocol stage received classId=%s", incoming_id)
                continue
            error_code = message.get("errorCode", 0)
            if check_error and str(error_code).strip() not in {"0", "0.0", "None"}:
                if 60109 in class_ids:
                    raise AuthenticationRejected("ChampionPoker PokerLoginRequest was rejected")
                raise ConnectionError(f"Upstream rejected classId={next(iter(class_ids))}")
            return message
        raise TimeoutError(
            "Timed out waiting for upstream classId="
            + ",".join(str(value) for value in sorted(class_ids))
        )

    def connect_once(self):
        ws = self._open_engineio3()
        try:
            self.set(connected=True, last_error=None)
            logger.info("Socket.IO connected; sending ClientServerHandshakeRequest (60200)")
            self._send(ws, {
                "flag": 0,
                "casinoName": CASINO,
                "classId": 60200,
            })
            self.wait_for(ws, {60201}, timeout=20, check_error=True)
            logger.info("Server handshake accepted (60201); registering client (50001)")

            self._send(ws, build_register_client_request())
            self.wait_for(ws, {50003}, timeout=20, check_error=True)
            logger.info("Client registered (50003); sending native PokerLoginRequest (60108)")

            self._send(ws, self.auth)
            login_response = self.wait_for(ws, {60109}, timeout=35, check_error=True)
            self.set(authenticated=True)
            logger.info(
                "ChampionPoker PokerLoginResponse accepted (60109); starting scheduled tournament feed"
            )

            request_id = "Lobby_FilterSubscription_" + uuid.uuid4().hex
            subscription = build_tournament_subscription(request_id=request_id)
            self._send(ws, subscription)
            ack = self.wait_for(ws, {50018}, timeout=25, check_error=True)
            self._last_subscription_ack = {
                "request_id": ack.get("requestId"),
                "error_code": ack.get("errorCode", 0),
            }
            self.set(subscribed=True)
            logger.info(
                "Scheduled tournament filter subscription accepted (50018); request_id_matches=%s",
                ack.get("requestId") == request_id,
            )

            next_ping = time.monotonic() + 25
            while not self.stop_event.is_set():
                timeout = max(1, next_ping - time.monotonic())
                try:
                    frame = self.recv(ws, timeout)
                    self._process_frame(ws, frame)
                except TimeoutError:
                    self._send(ws, {"messageSeqId": 0, "classId": 60005})
                    next_ping = time.monotonic() + 25
        finally:
            try:
                ws.close()
            except Exception:
                pass

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
                self.state.update(
                    running=False,
                    connected=False,
                    authenticated=False,
                    subscribed=False,
                )
