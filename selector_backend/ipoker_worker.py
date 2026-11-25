"""Assisted in-memory ChampionPoker Socket.IO collector. Never persists auth material."""
from __future__ import annotations
import json, logging, threading, time, uuid
from datetime import datetime, timezone
from typing import Any
import websocket
import requests
from .ipoker_socketio import build_tournament_subscription, decode_application_message, encode_application_message, encode_legacy_application_message, extract_tournaments

WS_URL = "wss://oapi-cur.techonlinecorp.com/socket.io/"
ORIGIN = "https://games.championpoker.com"
logger = logging.getLogger("ipoker.worker")
REQUIRED_AUTH_FIELDS = {"ID","responses","sessionToken","userName","casinoName","clientType","deliveryPlatform","correlationId"}

def validate_auth_message(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict): raise ValueError("auth_message must be a JSON object")
    missing = sorted(REQUIRED_AUTH_FIELDS - payload.keys())
    if missing: raise ValueError("Missing required auth fields: " + ", ".join(missing))
    if payload["ID"] != 33036: raise ValueError("auth_message.ID must be 33036")
    for key in ("sessionToken","userName","casinoName","clientType","deliveryPlatform","correlationId"):
        if not isinstance(payload.get(key),(str,int)) or not str(payload[key]).strip(): raise ValueError(f"auth_message.{key} must be non-empty")

def build_lobby_init(session_id: str | None = None) -> dict[str, Any]:
    return {"classId":50001,"casinoName":"championpoker","casinoShortName":None,"casinoSkin":"championpoker","clientPlatform":26,"clientType":26,"clientVersion":"26.7.1.26","oldClientVersion":None,"isFun":False,"languageCode":"EN","previewClient":False,"sessionId":session_id,"subscibeLobbyContentRequest":False,"subscribeClientServices":True}

class AuthenticationRejected(Exception):
    """Permanent upstream auth rejection; a fresh user handoff is required."""


class IPokerWorker:
    def __init__(self, save_callback):
        self.save_callback=save_callback; self.lock=threading.RLock(); self.stop_event=threading.Event(); self.thread=None; self.auth=None; self.cookie_header=None; self.legacy_socketio=False
        self.state={"running":False,"connected":False,"authenticated":False,"subscribed":False,"last_message_at":None,"last_catalog_at":None,"catalog_count":0,"last_error":None,"reconnects":0}
    def status(self):
        with self.lock: return dict(self.state)
    def set(self, **kw):
        with self.lock: self.state.update(kw)
    def start(self, auth, cookie_header=None):
        validate_auth_message(auth)
        with self.lock:
            if self.thread and self.thread.is_alive(): raise RuntimeError("Collector is already running")
            self.auth=dict(auth); self.cookie_header=cookie_header; self.stop_event.clear(); self.set(running=True,connected=False,authenticated=False,subscribed=False,last_error=None)
            self.thread=threading.Thread(target=self.run,daemon=True,name="ipoker-ws-collector"); self.thread.start()
        return {"status":"starting"}
    def stop(self):
        self.stop_event.set(); self.set(running=False); return {"status":"stopping"}
    def recv(self, ws, timeout=25):
        deadline=time.monotonic()+timeout
        while not self.stop_event.is_set():
            if time.monotonic()>=deadline: raise TimeoutError("Upstream timeout")
            try: frame=ws.recv()
            except websocket.WebSocketTimeoutException: continue
            if frame is None: raise ConnectionError("Socket closed")
            if isinstance(frame,bytes): frame=frame.decode("utf-8","replace")
            if frame=="2": ws.send("3"); continue
            if frame.startswith("2"): ws.send("3"+frame[1:]); continue
            self.set(last_message_at=datetime.now(timezone.utc).isoformat())
            return frame
        raise RuntimeError("Stopped")
    def wait_for(self, ws, class_ids, timeout=25, check_error=False):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            frame = self.recv(ws,max(1,end-time.monotonic()))
            msg=decode_application_message(frame)
            if msg:
                incoming_id = msg.get("ID", msg.get("classId"))
                if incoming_id not in class_ids:
                    logger.info("iPoker upstream message while waiting: classId=%s", incoming_id)
            else:
                # Log only the Socket.IO packet shape, never frame contents or auth material.
                packet_type = frame[:2] if isinstance(frame, str) else type(frame).__name__
                logger.info("iPoker upstream non-application frame: packet=%s length=%s", packet_type, len(frame) if hasattr(frame, "__len__") else 0)
            if msg and msg.get("ID", msg.get("classId")) in class_ids:
                if check_error and str(msg.get("errorCode", 0)).strip() not in {"0", "0.0"}:
                    if 60109 in class_ids: raise AuthenticationRejected("ChampionPoker authentication rejected")
                    raise ConnectionError("Upstream rejected request")
                return msg
        raise TimeoutError("Expected upstream response timed out")
    def _open_engineio3(self):
        # The browser first obtains an Engine.IO session over HTTP polling,
        # then upgrades that same SID to WebSocket. A direct WS open is rejected
        # by this upstream with HTTP 400.
        user_agent = str(self.auth.get("userAgent", "Mozilla/5.0"))
        session = requests.Session()
        session.headers.update({
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/poker",
            "User-Agent": user_agent,
            "Accept": "*/*",
        })
        if self.cookie_header:
            session.headers["Cookie"] = self.cookie_header
        poll_url = WS_URL.replace("wss://", "https://").replace("ws://", "http://")
        poll_url += "?EIO=3&transport=polling&t=" + str(int(time.time() * 1000))
        try:
            response = session.get(poll_url, timeout=12)
            response.raise_for_status()
        except requests.HTTPError as exc:
            # Browser captures also show the legacy Socket.IO handshake route.
            status = getattr(exc.response, "status_code", None)
            if status != 400:
                raise
            logger.warning("Engine.IO polling rejected; testing captured legacy handshake route")
            legacy_url = WS_URL.replace("wss://", "https://").replace("ws://", "http://") + "1/"
            legacy = session.get(legacy_url, params={"t": str(int(time.time() * 1000))}, timeout=12)
            legacy.raise_for_status()
            parts = legacy.text.strip().split(":")
            if len(parts) < 4 or not parts[0] or "websocket" not in parts[3].split(","):
                raise ConnectionError("Legacy Socket.IO handshake response was invalid")
            sid = parts[0]
            legacy_ws_url = WS_URL + "1/websocket/" + sid
            ws = websocket.create_connection(
                legacy_ws_url, timeout=10,
                header=["Origin: " + ORIGIN, "Referer: " + ORIGIN + "/poker",
                        "User-Agent: " + user_agent],
                cookie=self.cookie_header, enable_multithread=True,
            )
            self.legacy_socketio = True
            first_packet = self.recv(ws, 10)
            if first_packet not in {"1::", "1::/"}:
                try: ws.close()
                except Exception: pass
                raise ConnectionError("Legacy Socket.IO namespace connection was not accepted")
            logger.info("Captured legacy Socket.IO namespace connected")
            return ws
        self.legacy_socketio = False
        opening = response.text
        if not isinstance(opening, str) or not opening.startswith("0"):
            raise ConnectionError("Engine.IO v3 polling opening packet was not received")
        try:
            handshake = json.loads(opening[1:])
        except (ValueError, TypeError):
            raise ConnectionError("Engine.IO v3 polling handshake was malformed")
        sid = handshake.get("sid") if isinstance(handshake, dict) else None
        if not sid:
            raise ConnectionError("Engine.IO v3 polling handshake omitted sid")
        if "websocket" not in (handshake.get("upgrades") or []):
            raise ConnectionError("Engine.IO v3 session does not offer websocket upgrade")

        headers = [
            "Origin: " + ORIGIN,
            "Referer: " + ORIGIN + "/poker",
            "User-Agent: " + user_agent,
        ]
        cookie = session.cookies.get_dict()
        if cookie:
            headers.append("Cookie: " + "; ".join(k + "=" + v for k, v in cookie.items()))
        elif self.cookie_header:
            headers.append("Cookie: " + self.cookie_header)
        ws_url = WS_URL + "?EIO=3&transport=websocket&sid=" + str(sid)
        ws_url += "&t=" + str(int(time.time() * 1000))
        ws = websocket.create_connection(
            ws_url, timeout=10, header=headers, enable_multithread=True
        )
        try:
            ws.send("2probe")
            probe = ws.recv()
            if isinstance(probe, bytes):
                probe = probe.decode("utf-8", "replace")
            if probe != "3probe":
                raise ConnectionError("Engine.IO v3 websocket probe was not accepted")
            ws.send("5")
            ws.send("40")
            namespace = self.recv(ws, 10)
            if namespace != "40":
                raise ConnectionError("Socket.IO namespace connection was not accepted")
            return ws
        except Exception:
            try:
                ws.close()
            except Exception:
                pass
            raise

    def _encode_application_message(self, message):
        if self.legacy_socketio:
            return encode_legacy_application_message(message)
        return encode_application_message(message)

    def _send_initial_client_messages(self, ws):
        # These two requests are sent by the browser before Hub2 login.
        country_correlation = "correlationId_" + str(uuid.uuid4())
        ip_correlation = "correlationId_" + str(uuid.uuid4())
        ws.send(self._encode_application_message({
            "ID": 35685, "casino": "championpoker",
            "description": "Country by Geo IP detection",
            "responses": [35686, 35687], "errorLevel": 1,
            "relativeURI": "/poker", "correlationId": country_correlation,
        }))
        ws.send(encode_application_message({
            "ID": 81405, "description": "IP address detection",
            "responses": [81406, 81407], "errorLevel": 1,
            "relativeURI": "/poker", "correlationId": ip_correlation,
        }))

    def connect_once(self):
        logger.info("iPoker Engine.IO v3 polling handshake and websocket upgrade starting")
        ws = self._open_engineio3()
        try:
            self.set(connected=True,last_error=None)
            logger.info("iPoker Engine.IO v3 websocket upgraded; sending browser initialization and Hub2 auth")
            self._send_initial_client_messages(ws)
            ws.send(self._encode_application_message(self.auth))
            auth=self.wait_for(ws,{31002,31009},30,True)
            self.set(authenticated=True)
            # The browser trace proceeds from 31002 to lobby initialization;
            # it does not wait for a 31009 response.
            auth_data = auth.get("data") if isinstance(auth.get("data"), dict) else {}
            refreshed_token = auth_data.get("sessionToken")
            if refreshed_token:
                self.auth["sessionToken"] = str(refreshed_token)
            logger.info("ChampionPoker Hub2 login accepted (31002); initializing lobby")
            ws.send(self._encode_application_message(build_lobby_init(None)))
            self.wait_for(ws,{50003},20,True)
            logger.info("ChampionPoker lobby initialized (50003); requesting lobby tournament data")
            # Captured browser flow sends classId 60005 after 50003; server replies 60006.
            # Browser sends this particular classId as a JSON string inside the event.
            ws.send(self._encode_application_message({"classId": 60005}))
            self.set(subscribed=True)
            while not self.stop_event.is_set():
                frame=self.recv(ws,60)
                records,_=extract_tournaments([frame])
                if records:
                    saved=self.save_callback(records,None,"0.4.0-ws-worker")
                    self.set(last_catalog_at=datetime.now(timezone.utc).isoformat(),catalog_count=saved.get("count",len(records)),last_error=None)
                    logger.info("iPoker catalog updated: received=%s total=%s", len(records), saved.get("count",len(records)))
        finally:
            try: ws.close()
            except Exception: pass
    def run(self):
        try:
            while not self.stop_event.is_set():
                try: self.connect_once()
                except AuthenticationRejected as exc:
                    logger.error("iPoker upstream authentication rejected")
                    self.set(connected=False,authenticated=False,subscribed=False,running=False,last_error=type(exc).__name__)
                    break
                except Exception as exc:
                    # Log only transport diagnostics; never headers, cookies, or payloads.
                    status_code = getattr(exc, "status_code", getattr(exc, "status", None))
                    response = getattr(exc, "response", None)
                    if status_code is None and response is not None:
                        status_code = getattr(response, "status_code", None)
                    reason = getattr(exc, "resp", None)
                    reason = getattr(reason, "reason", None) if reason is not None else None
                    # Never log response headers or body: they may contain session material.
                    logger.warning("iPoker collector connection failed: %s status=%s reason=%s", type(exc).__name__, status_code, str(reason)[:120] if reason else None)
                    self.set(connected=False,authenticated=False,subscribed=False,last_error=type(exc).__name__)
                    if self.stop_event.wait(10): break
                    self.set(reconnects=self.status()["reconnects"]+1)
        finally:
            with self.lock:
                self.auth=None
                self.cookie_header=None
                self.state.update(running=False,connected=False,authenticated=False,subscribed=False)
