"""Server-side ChampionPoker credential login.

Credentials and temporary tokens are runtime-only. Never log request bodies or response bodies.
The login endpoint contract is taken from the official integration.js flow:
POST login.championpoker.com/LoginAndGetTempToken.php with form fields.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any
from urllib.parse import urlencode

import requests

LOGIN_BASE = "https://login.championpoker.com"
CASINO = "championpoker"
TIMEOUT = (8, 25)


class LoginProtocolError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise LoginProtocolError(f"Missing Railway variable: {name}")
    return value


def _find_temp_token(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"temptoken", "temporarytoken", "authenticationtoken"} and isinstance(item, (str, int)) and str(item).strip():
                return str(item)
            found = _find_temp_token(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_temp_token(item)
            if found:
                return found
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return _find_temp_token(json.loads(text))
            except (ValueError, TypeError):
                pass
        match = re.search(r'["\'](?:tempToken|temporaryToken|authenticationToken)["\']\\s*:\\s*["\']([^"\']+)["\']', text, re.I)
        if match:
            return match.group(1)
    return None


def _response_error(value: Any, body: str) -> str | None:
    if isinstance(value, dict):
        code = value.get("errorCode")
        message = value.get("errorMessage") or value.get("message") or value.get("error")
        if code not in (None, 0, "0", "OK", "ok"):
            return "upstream_error"
        if message and not _find_temp_token(value):
            return "upstream_rejected"
    lower = body.lower()
    if any(term in lower for term in ("captcha", "challenge", "twofactor", "two-factor", "otp", "verification required")):
        return "additional_verification_required"
    return None


def login_and_get_temp_token() -> dict[str, Any]:
    """Perform one admin-account login attempt and return only a sanitized auth result."""
    username = _required_env("IPOKER_ADMIN_USERNAME")
    password = _required_env("IPOKER_ADMIN_PASSWORD")

    # Mirrors iapiLoginAndGetTempToken() from the room's published integration script.
    params = {
        "username": username,
        "password": password,
        "loginType": "password",
    }
    query = {
        "casinoname": CASINO,
        "realMode": os.getenv("IPOKER_REAL_MODE", "1"),
        "serviceType": os.getenv("IPOKER_SERVICE_TYPE", "GamePlay"),
        "systemId": os.getenv("IPOKER_SYSTEM_ID", "5"),
        "clientType": "casino",
        "clientPlatform": os.getenv("IPOKER_CLIENT_PLATFORM", "mobile"),
        "clientVersion": os.getenv("IPOKER_CLIENT_VERSION", "26.7.1.26"),
        "clientSkin": CASINO,
        "languageCode": os.getenv("IPOKER_LANGUAGE_CODE", "EN"),
        "errorLevel": "1",
    }
    url = f"{LOGIN_BASE}/LoginAndGetTempToken.php?{urlencode(query)}"

    session = requests.Session()
    session.headers.update({
        "User-Agent": os.getenv("IPOKER_USER_AGENT", "Mozilla/5.0"),
        "Origin": "https://games.championpoker.com",
        "Referer": "https://games.championpoker.com/",
        "Accept": "text/html,application/json, text/plain, */*",
    })
    try:
        response = session.post(url, data=params, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        raise LoginProtocolError(f"Login transport failed: {type(exc).__name__}") from None

    body = response.text[:2_000_000]
    parsed: Any = None
    try:
        parsed = response.json()
    except ValueError:
        # The browser integration can receive a postMessage wrapper/HTML document.
        # Extract only JSON objects; never expose or log the response body.
        for candidate in re.findall(r'\\{[^{}]{1,20000}\\}', body):
            try:
                parsed = json.loads(candidate)
                if _find_temp_token(parsed):
                    break
            except ValueError:
                continue

    token = _find_temp_token(parsed) or _find_temp_token(body)
    if not response.ok:
        raise LoginProtocolError(f"Login HTTP status {response.status_code}")
    if not token:
        reason = _response_error(parsed, body)
        if reason == "additional_verification_required":
            raise LoginProtocolError("Room requires an additional verification step; automatic login cannot continue")
        raise LoginProtocolError("Login response did not contain a recognized temporary token; response format needs analysis")

    return {
        "temp_token": token,
        "username": username,
        "login_type": "password",
        "real_mode": os.getenv("IPOKER_REAL_MODE", "1") not in {"0", "false", "False"},
        "session_cookie_count": len(session.cookies),
    }



def build_auth_message(login: dict[str, Any]) -> dict[str, Any]:
    """Build the observed 60108 Socket.IO auth envelope from server config."""
    username = str(login["username"])
    return {
        "classId": 60108,
        "tempToken": str(login["temp_token"]),
        "usernameOrNickname": username,
        "usernameLogin": True,
        "loginType": int(os.getenv("IPOKER_LOGIN_TYPE", "1")),
        "realMode": bool(login["real_mode"]),
        "casinoName": CASINO,
        "clientPlatform": int(os.getenv("IPOKER_CLIENT_PLATFORM_ID", "26")),
        "clientVersion": os.getenv("IPOKER_CLIENT_VERSION", "26.7.1.26"),
        "deviceFamily": os.getenv("IPOKER_DEVICE_FAMILY", "desktop"),
        "deviceName": os.getenv("IPOKER_DEVICE_NAME", "Railway server"),
        "deviceType": os.getenv("IPOKER_DEVICE_TYPE", "desktop"),
        "hardSerial": os.getenv("IPOKER_HARD_SERIAL") or secrets.token_hex(16),
        "softSerial": os.getenv("IPOKER_SOFT_SERIAL") or secrets.token_hex(16),
        "languageCode": os.getenv("IPOKER_LANGUAGE_CODE", "EN"),
        "osVersion": os.getenv("IPOKER_OS_VERSION", "Linux"),
        "platformType": int(os.getenv("IPOKER_PLATFORM_TYPE", "1")),
        "supportedPokerServices": json.loads(os.getenv("IPOKER_SUPPORTED_SERVICES", "[]")),
        "browser": os.getenv("IPOKER_BROWSER", "Chrome"),
        "userAgent": os.getenv("IPOKER_USER_AGENT", "Mozilla/5.0"),
    }
