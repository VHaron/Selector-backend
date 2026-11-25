"""Assisted in-memory ChampionPoker Socket.IO collector. Never persists auth material."""
from __future__ import annotations
import json, threading, time
from datetime import datetime, timezone
from typing import Any
import websocket
from .ipoker_socketio import build_tournament_subscription, decode_application_message, encode_application_message, extract_tournaments

WS_URL = "wss://schannel.ipoker.com/socket.io/?EIO=3&transport=websocket"
ORIGIN = "https://games.championpoker.com"
REQUIRED_AUTH_FIELDS = {"classId","tempToken","usernameOrNickname","usernameLogin","loginType","realMode","casinoName","clientPlatform","clientVersion","deviceFamily","deviceName","deviceType","hardSerial","softSerial","languageCode","osVersion","platformType","supportedPokerServices","browser","userAgent"}

def validate_auth_message(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict): raise ValueError("auth_message must be a JSON object")
    missing = sorted(REQUIRED_AUTH_FIELDS - payload.keys())
    if missing: raise ValueError("Missing required auth fields: " + ", ".join(missing))
    if payload["classId"] != 60108: raise ValueError("auth_message.classId must be 60108")
    for key in ("tempToken","usernameOrNickname","casinoName","clientVersion"):
        if not isinstance(payload.get(key),(str,int)) or not str(payload[key]).strip(): raise ValueError(f"auth_message.{key} must be non-empty")
    if not isinstance(payload["supportedPokerServices"],(list,dict,str)): raise ValueError("Invalid supportedPokerServices")

def build_lobby_init(session_id: str) -> dict[str, Any]:
    return {"classId":50001,"casinoName":"championpoker","casinoShortName":None,"casinoSkin":"championpoker","clientPlatform":26,"clientType":26,"clientVersion":"26.7.1.26","oldClientVersion":None,"isFun":False,"languageCode":"EN","previewClient":False,"sessionId":session_id,"subscibeLobbyContentRequest":False,"subscribeClientServices":True}

class AuthenticationRejected(Exception):
    """Permanent upstream auth rejection; a fresh user handoff is required."""


class IPokerWorker:
    def __init__(self, save_callback):
        self.save_callback=save_callback; self.lock=threading.RLock(); self.stop_event=threading.Event(); self.thread=None; self.auth=None
        self.state={"running":False,"connected":False,"authenticated":False,"subscribed":False,"last_message_at":None,"last_catalog_at":None,"catalog_count":0,"last_error":None,"reconnects":0}
    def status(self):
        with self.lock: return dict(self.state)
    def set(self, **kw):
        with self.lock: self.state.update(kw)
    def start(self, auth):
        validate_auth_message(auth)
        with self.lock:
            if self.thread and self.thread.is_alive(): raise RuntimeError("Collector is already running")
            self.auth=dict(auth); self.stop_event.clear(); self.set(running=True,connected=False,authenticated=False,subscribed=False,last_error=None)
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
            msg=decode_application_message(self.recv(ws,max(1,end-time.monotonic())))
            if msg and msg.get("classId") in class_ids:
                if check_error and msg.get("errorCode")!=0:
                    if 60109 in class_ids: raise AuthenticationRejected("ChampionPoker authentication rejected")
                    raise ConnectionError("Upstream rejected request")
                return msg
        raise TimeoutError("Expected upstream response timed out")
    def connect_once(self):
        ws=websocket.create_connection(WS_URL,timeout=2,origin=ORIGIN,enable_multithread=True)
        try:
            hello=self.recv(ws,20)
            if not hello.startswith("0"): raise ConnectionError("Invalid Engine.IO handshake")
            json.loads(hello[1:])
            ws.send("40")
            end=time.monotonic()+15
            while time.monotonic()<end:
                frame=self.recv(ws,max(1,end-time.monotonic()))
                if frame.startswith("40"): break
                if frame.startswith("44"): raise ConnectionError("Socket.IO namespace rejected")
            else: raise TimeoutError("Namespace handshake timeout")
            self.set(connected=True,last_error=None)
            ws.send(encode_application_message(self.auth))
            auth=self.wait_for(ws,{60109},25,True)
            sid=auth.get("sessionId")
            if not isinstance(sid,str) or not sid: raise ConnectionError("Auth response omitted sessionId")
            self.set(authenticated=True)
            ws.send(encode_application_message(build_lobby_init(sid)))
            # The observed skin sends 50003. 50026 may precede it.
            try: self.wait_for(ws,{50003},20)
            except TimeoutError: pass
            ws.send(encode_application_message(build_tournament_subscription()))
            self.wait_for(ws,{50018},25,True)
            self.set(subscribed=True)
            while not self.stop_event.is_set():
                frame=self.recv(ws,60)
                records,_=extract_tournaments([frame])
                if records:
                    saved=self.save_callback(records,None,"0.4.0-ws-worker")
                    self.set(last_catalog_at=datetime.now(timezone.utc).isoformat(),catalog_count=saved.get("count",len(records)),last_error=None)
        finally:
            try: ws.close()
            except Exception: pass
    def run(self):
        try:
            while not self.stop_event.is_set():
                try: self.connect_once()
                except AuthenticationRejected as exc:
                    self.set(connected=False,authenticated=False,subscribed=False,running=False,last_error=type(exc).__name__)
                    break
                except Exception as exc:
                    self.set(connected=False,authenticated=False,subscribed=False,last_error=type(exc).__name__)
                    if self.stop_event.wait(10): break
                    self.set(reconnects=self.status()["reconnects"]+1)
        finally:
            with self.lock:
                self.auth=None
                self.state.update(running=False,connected=False,authenticated=False,subscribed=False)
