import uuid
import time
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from .config import SITE_BASES, UPSTREAM_TLS_VERIFY
from .models import LoginRequest, SiteSelectionRequest, MFARequest
from .state import (sessions, session_to_user, session_to_site, session_mfa_url, site_selection_groups,
                    auth_state_lock, _close_session_token, _cleanup_expired_site_selections)

router = APIRouter()

def _csrf_from_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find("input", {"name": "_csrf-backend"})
    if not field or not field.get("value"):
        raise ValueError("На странице авторизации не найден CSRF-токен")
    return field["value"]


def _try_site_login(req: LoginRequest, site: str):
    base = SITE_BASES[site]
    user_session = requests.Session()
    user_session.verify = UPSTREAM_TLS_VERIFY
    user_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": f"{base}/site/login",
    })

    registered_token = None
    try:
        response = user_session.get(f"{base}/site/login", timeout=25)
        csrf_value = _csrf_from_page(response.text)
        post_response = user_session.post(
            f"{base}/site/login",
            data={
                "_csrf-backend": csrf_value,
                "LoginForm[username]": req.username,
                "LoginForm[password]": req.password,
                "LoginForm[rememberMe]": "1",
            },
            allow_redirects=True,
            timeout=25,
        )
        final_url = post_response.url.lower()
        page = BeautifulSoup(post_response.text, "html.parser")
        login_form_still_visible = bool(page.find("input", {"name": "LoginForm[password]"}))
        if "/site/login" in final_url and login_form_still_visible:
            user_session.close()
            return None

        token = str(uuid.uuid4())
        with auth_state_lock:
            sessions[token] = user_session
            session_to_user[token] = req.username
            session_to_site[token] = site
        registered_token = token

        if "mfa" in final_url or "otp" in final_url or "двухфактор" in post_response.text.lower():
            with auth_state_lock:
                session_mfa_url[token] = (
                    post_response.url
                    if "mfa" in final_url or "otp" in final_url
                    else f"{base}/site/mfa-verify"
                )
            return {"status": "mfa_required", "session_token": token, "site": site}

        # A login redirect back to the login page is a failed login even if the HTML differs.
        if "/site/login" in final_url:
            _close_session_token(token)
            return None
        return {"status": "success", "session_token": token, "site": site}
    except Exception:
        if registered_token:
            _close_session_token(registered_token)
        else:
            try:
                user_session.close()
            except Exception:
                pass
        raise


# === ЭНДПОИНТЫ АВТОРИЗАЦИИ ===
@router.post("/api/login")
def login(req: LoginRequest):
    _cleanup_expired_site_selections()
    requested_site = (req.site or "auto").lower()
    if requested_site not in ("auto", *SITE_BASES.keys()):
        raise HTTPException(status_code=400, detail="Неизвестный сайт авторизации")

    # Explicit site requests retain the original one-site behavior.
    if requested_site != "auto":
        try:
            result = _try_site_login(req, requested_site)
        except requests.RequestException:
            raise HTTPException(status_code=502, detail=f"{requested_site}: сайт временно недоступен")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ошибка авторизации на {requested_site}: {str(e)}")
        if result:
            return result
        raise HTTPException(status_code=401, detail="Логин или пароль не подошли")

    # In auto mode both sites must be checked. If either cannot be checked,
    # we cannot safely conclude that the account exists on only one site.
    candidates: dict[str, dict[str, str]] = {}
    errors = []
    for site in SITE_BASES:
        try:
            result = _try_site_login(req, site)
            if result:
                candidates[site] = result
        except requests.RequestException:
            errors.append(f"{site}: сайт временно недоступен")
        except Exception as e:
            errors.append(f"{site}: ошибка проверки авторизации ({str(e)})")

    if errors:
        for result in candidates.values():
            _close_session_token(result["session_token"])
        raise HTTPException(
            status_code=502,
            detail="Не удалось проверить оба сайта, поэтому выбор аккаунта не может быть определён. " + "; ".join(errors)
        )

    if not candidates:
        raise HTTPException(
            status_code=401,
            detail="Логин или пароль не подошли ни к GlobusGo, ни к GlobusMVP"
        )

    if len(candidates) == 1:
        return next(iter(candidates.values()))

    selection_id = str(uuid.uuid4())
    token_map = {site: result["session_token"] for site, result in candidates.items()}
    with auth_state_lock:
        site_selection_groups[selection_id] = {
            "username": req.username,
            "tokens": token_map,
            "created_at": time.monotonic(),
        }

    return {
        "status": "site_selection_required",
        "selection_id": selection_id,
        "candidates": [
            {"site": site, "auth_status": result["status"]}
            for site, result in candidates.items()
        ],
    }


@router.post("/api/select_site")
def select_site(req: SiteSelectionRequest):
    _cleanup_expired_site_selections()
    site = (req.site or "").lower()
    if site not in SITE_BASES:
        raise HTTPException(status_code=400, detail="Неизвестный сайт авторизации")

    with auth_state_lock:
        group = site_selection_groups.pop(req.selection_id, None)

    if not group:
        raise HTTPException(status_code=401, detail="Выбор сайта истёк. Войдите заново.")

    selected_token = group["tokens"].get(site)
    if not selected_token or selected_token not in sessions:
        for token in group["tokens"].values():
            _close_session_token(token)
        raise HTTPException(status_code=401, detail="Сессия выбранного сайта истекла. Войдите заново.")

    # Keep only the selected site's authenticated session.
    for other_site, token in group["tokens"].items():
        if other_site != site:
            _close_session_token(token)

    selected_status = "mfa_required" if selected_token in session_mfa_url else "success"
    return {
        "status": selected_status,
        "session_token": selected_token,
        "site": site,
    }


@router.post("/api/mfa")
def verify_mfa(req: MFARequest):
    with auth_state_lock:
        user_session = sessions.get(req.session_token)
        site = session_to_site.get(req.session_token, "globusgo")
        mfa_url = session_mfa_url.get(req.session_token)
    if not user_session:
        raise HTTPException(status_code=401, detail="Сессия авторизации истекла. Войдите заново.")
    base = SITE_BASES[site]
    mfa_url = mfa_url or f"{base}/site/mfa-verify"
    try:
        response = user_session.get(mfa_url, timeout=25)
        csrf_value = _csrf_from_page(response.text)
        mfa_soup = BeautifulSoup(response.text, "html.parser")
        otp_input = next((field for field in mfa_soup.find_all("input")
                          if any(part in (field.get("name") or "").lower() for part in ("otp", "code"))), None)
        otp_field_name = otp_input.get("name") if otp_input else "OtpForm[otp]"
        post_response = user_session.post(
            response.url,
            data={"_csrf-backend": csrf_value, otp_field_name: req.otp},
            allow_redirects=True,
            timeout=25,
        )
        if "mfa" in post_response.url.lower() or "otp" in post_response.url.lower():
            raise HTTPException(status_code=401, detail="Неверный код 2FA")
        if "/site/login" in post_response.url.lower():
            raise HTTPException(status_code=401, detail="Сессия авторизации истекла. Войдите заново.")
        session_mfa_url.pop(req.session_token, None)
        return {"status": "success", "site": site}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка проверки 2FA на {site}: {str(e)}")


