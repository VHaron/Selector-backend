"""Server-side ChampionPoker login using the observed browser integration contract.

Credentials, cookies and session tokens remain runtime-only. Never log request or
response bodies. The request shape mirrors the successful browser request.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode

import requests

LOGIN_BASE = "https://login.championpoker.com"
CASINO = "championpoker"
SITE_ORIGIN = "https://www.championpoker.com"
TIMEOUT = (8, 25)


class LoginProtocolError(RuntimeError):
    pass


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise LoginProtocolError(f"Missing Railway variable: {name}")
    return value


def _find_temp_token(value: Any) -> str | None:
    """Accept both legacy tempToken replies and the current nested sessionToken reply."""
    if isinstance(value, dict):
        session = value.get("sessionToken")
        if isinstance(session, dict):
            token = session.get("sessionToken")
            if isinstance(token, (str, int)) and str(token).strip():
                return str(token)
        for key, item in value.items():
            if str(key).lower() in {
                "temptoken", "temporarytoken", "authenticationtoken"
            } and isinstance(item, (str, int)) and str(item).strip():
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
        match = re.search(
            r"""["'](?:tempToken|temporaryToken|authenticationToken)["']\s*:\s*["']([^"']+)["']""",
            text, re.I,
        )
        if match:
            return match.group(1)
    return None


def _client_hints() -> str:
    """Build a compact Chromium client-hints JSON value for the form field."""
    configured = os.getenv("IPOKER_CLIENT_HINTS_JSON", "").strip()
    if configured:
        try:
            parsed = json.loads(configured)
            if isinstance(parsed, dict):
                return json.dumps(parsed, separators=(",", ":"))
        except ValueError:
            pass
    version = os.getenv("IPOKER_CHROME_MAJOR", "157")
    platform = os.getenv("IPOKER_CHROME_PLATFORM", "Windows")
    full_version = f"{version}.0.0.0"
    hints = {
        "Sec-CH-UA-Model": "",
        "Sec-CH-UA-Pixel-Ratio": "1",
        "Sec-CH-UA-Platform": platform,
        "Sec-CH-UA-Platform-Version": "10.0.0",
        "Sec-CH-UA-Full-Version-List": [
            {"brand": "Not_A Brand", "version": "99.0.0.0"},
            {"brand": "Google Chrome", "version": full_version},
            {"brand": "Chromium", "version": full_version},
        ],
        "Sec-CH-UA": (
            f'"Not_A Brand";v="99", "Google Chrome";v="{version}", '
            f'"Chromium";v="{version}"'
        ),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Arch": "x86",
        "Sec-CH-UA-Bitness": "64",
    }
    return json.dumps(hints, separators=(",", ":"))


def _parse_response(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        body = response.text[:2_000_000]
        for candidate in re.findall(r'\{[^{}]{1,20000}\}', body):
            try:
                return json.loads(candidate)
            except ValueError:
                continue
    return None


def login_and_get_temp_token() -> dict[str, Any]:
    """Perform the browser-shaped login and return a sanitized ephemeral auth result."""
    username = _required_env("IPOKER_ADMIN_USERNAME")
    password = _required_env("IPOKER_ADMIN_PASSWORD")
    real_mode = os.getenv("IPOKER_REAL_MODE", "1")
    device_id = os.getenv("IPOKER_DEVICE_ID", str(uuid.uuid4()))
    request_id = str(secrets.randbelow(9_000_000_000_000) + 1_000_000_000_000)

    session = requests.Session()
    user_agent = os.getenv(
        "IPOKER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
    )
    session.headers.update({
        "User-Agent": user_agent,
        "Origin": LOGIN_BASE,
        "Referer": f"{SITE_ORIGIN}/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": os.getenv("IPOKER_ACCEPT_LANGUAGE", "en-US,en;q=0.9"),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    try:
        # Match the browser's pre-auth configuration request and retain its cookies.
        preauth = session.get(
            f"{LOGIN_BASE}/PreAuthConf",
            params={"casino": CASINO},
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=TIMEOUT,
        )
        if not preauth.ok:
            raise LoginProtocolError(f"Pre-auth configuration HTTP status {preauth.status_code}")
        try:
            preauth_config = preauth.json()
        except ValueError:
            preauth_config = {}
        if isinstance(preauth_config, dict) and preauth_config.get("enforceCaptcha") is True:
            raise LoginProtocolError("ChampionPoker requires CAPTCHA; automatic login cannot continue")

        query = {
            "casinoname": CASINO,
            "realMode": real_mode,
            "serviceType": os.getenv("IPOKER_SERVICE_TYPE", "GamePlay"),
            "clientType": "casino",
            "clientPlatform": "web",
            "clientSkin": CASINO,
            "deviceId": device_id,
            "deliveryPlatform": os.getenv("IPOKER_DELIVERY_PLATFORM", "Hub2"),
            "languageCode": os.getenv("IPOKER_LANGUAGE_CODE", "en").lower(),
            "redirectUrl": f"{SITE_ORIGIN}/#?requestId={request_id}",
            "errorLevel": "1",
            "messagesSupported": "1",
        }
        form = {
            "eventOrigin": LOGIN_BASE,
            "hr": f"{SITE_ORIGIN}/",
            "requestType": "LoginAndGetTempToken",
            "username": username,
            "password": password,
            "loginType": "password",
            "clientHints": _client_hints(),
        }
        response = session.post(
            f"{LOGIN_BASE}/LoginAndGetTempToken.php",
            params=query,
            data=form,
            headers={
                "Origin": LOGIN_BASE,
                "Referer": f"{LOGIN_BASE}/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    except LoginProtocolError:
        raise
    except requests.RequestException as exc:
        raise LoginProtocolError(f"Login transport failed: {type(exc).__name__}") from None

    parsed = _parse_response(response)
    if not response.ok:
        # Do not include upstream body: it may contain credentials or session material.
        raise LoginProtocolError(f"Login HTTP status {response.status_code}")

    if isinstance(parsed, dict):
        code = parsed.get("errorCode")
        if code not in (None, 0, "0", "OK", "ok"):
            if code in (401, "401", 403, "403"):
                raise LoginProtocolError("ChampionPoker rejected the credentials or requires account verification")
            raise LoginProtocolError("ChampionPoker returned an authentication error")
    token = _find_temp_token(parsed)
    if not token:
        # Do not leak the body; distinguish the known 2FA requirement safely.
        conf = preauth_config if isinstance(preauth_config, dict) else {}
        two_factor = conf.get("twoFactorAuthenticationConf")
        if isinstance(two_factor, dict) and two_factor.get("enabled"):
            raise LoginProtocolError(
                "Login response contained no session token; account may require email two-factor verification"
            )
        raise LoginProtocolError("Login response did not contain a recognized session token")

    return {
        "temp_token": token,
        "username": username,
        "login_type": "password",
        "real_mode": real_mode not in {"0", "false", "False"},
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
        "loginType": os.getenv("IPOKER_LOGIN_TYPE", "password"),
        "realMode": bool(login["real_mode"]),
        "casinoName": CASINO,
        "clientPlatform": int(os.getenv("IPOKER_CLIENT_PLATFORM_ID", "26")),
        "clientVersion": os.getenv("IPOKER_CLIENT_VERSION", "26.7.1.26"),
        "deviceFamily": os.getenv("IPOKER_DEVICE_FAMILY", "desktop"),
        "deviceName": os.getenv("IPOKER_DEVICE_NAME", "Chrome on Windows"),
        "deviceType": os.getenv("IPOKER_DEVICE_TYPE", "desktop"),
        "hardSerial": os.getenv("IPOKER_HARD_SERIAL") or secrets.token_hex(16),
        "softSerial": os.getenv("IPOKER_SOFT_SERIAL") or secrets.token_hex(16),
        "languageCode": os.getenv("IPOKER_WS_LANGUAGE_CODE", "EN"),
        "osVersion": os.getenv("IPOKER_OS_VERSION", "Windows"),
        "platformType": int(os.getenv("IPOKER_PLATFORM_TYPE", "1")),
        "supportedPokerServices": json.loads(os.getenv("IPOKER_SUPPORTED_SERVICES", "[]")),
        "browser": os.getenv("IPOKER_BROWSER", "Chrome"),
        "userAgent": os.getenv("IPOKER_USER_AGENT", "Mozilla/5.0"),
    }
