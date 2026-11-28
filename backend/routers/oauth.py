"""OAuth authorization-code login for Google and Microsoft."""

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pymongo.errors import DuplicateKeyError

from backend.database import oauth_login_states, users_collection
from backend.security import create_access_token, hash_password

router = APIRouter(prefix="/api/auth", tags=["OAuth"])
logger = logging.getLogger(__name__)
STATE_LIFETIME = 600
PAGE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    authorize_url: str
    token_url: str
    profile_url: str
    scope: str
    client_id: str
    secret: str
    redirect_uri: str

    @property
    def cookie(self) -> str:
        return f"nexus_login_{self.key}"

    @property
    def callback_path(self) -> str:
        return f"/api/auth/{self.key}/callback"


def provider_config(key: str) -> Provider:
    if key == "google":
        label, authorize_url, token_url, profile_url, scope = (
            "Google", "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token", "https://openidconnect.googleapis.com/v1/userinfo",
            "openid email profile",
        )
    else:
        tenant = os.getenv("MICROSOFT_TENANT", "common").strip()
        endpoint = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
        label, authorize_url, token_url, profile_url, scope = (
            "Microsoft", f"{endpoint}/authorize", f"{endpoint}/token",
            "https://graph.microsoft.com/v1.0/me", "openid email profile User.Read",
        )
    prefix = key.upper()
    return Provider(
        key, label, authorize_url, token_url, profile_url, scope,
        os.getenv(f"{prefix}_CLIENT_ID", "").strip(),
        os.getenv(f"{prefix}_CLIENT_SECRET", "").strip(),
        os.getenv(f"{prefix}_REDIRECT_URI", f"http://127.0.0.1:8000/api/auth/{key}/callback").strip(),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _start_login(key: str, request: Request):
    provider = provider_config(key)
    if not (provider.client_id and provider.secret):
        raise HTTPException(status_code=503, detail=f"Đăng nhập {provider.label} chưa được cấu hình.")
    binding = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    oauth_login_states.insert_one({
        "_id": _digest(state),
        "provider": key,
        "client_id": provider.client_id,
        "redirect_uri": provider.redirect_uri,
        "binding": _digest(binding),
        "verifier": verifier,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=STATE_LIFETIME),
    })
    params = {
        "client_id": provider.client_id, "redirect_uri": provider.redirect_uri, "response_type": "code",
        "scope": provider.scope, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    response = RedirectResponse(f"{provider.authorize_url}?{urlencode(params)}", headers=PAGE_HEADERS)
    response.set_cookie(
        provider.cookie, binding, max_age=STATE_LIFETIME, path=provider.callback_path,
        httponly=True, samesite="lax",
        secure=request.url.scheme == "https" or urlsplit(provider.redirect_uri).scheme == "https",
    )
    return response


def _login_callback(key: str, request: Request, state: str | None, code: str | None, error: str | None):
    provider = provider_config(key)
    binding = request.cookies.get(provider.cookie)
    record = None
    if state and len(state) <= 128 and binding and len(binding) <= 128:
        record = oauth_login_states.find_one_and_delete({
            "_id": _digest(state), "provider": key, "binding": _digest(binding),
            "client_id": provider.client_id, "redirect_uri": provider.redirect_uri,
            "expires_at": {"$gt": datetime.now(timezone.utc)},
        })
    if record is None:
        return _oauth_error_page("Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Vui lòng đăng nhập lại.")

    if error or not code:
        response = _oauth_error_page(f"{provider.label} chưa cấp quyền đăng nhập. Vui lòng thử lại.")
    else:
        response = _exchange_and_login(provider, code, record["verifier"])
    response.delete_cookie(provider.cookie, path=provider.callback_path)
    return response


def _exchange_and_login(provider: Provider, code: str, verifier: str):
    try:
        token = requests.post(
            provider.token_url,
            data={
                "code": code, "client_id": provider.client_id, "client_secret": provider.secret,
                "redirect_uri": provider.redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
            },
            timeout=15, allow_redirects=False,
        )
        if token.status_code != 200:
            return _oauth_error_page(f"{provider.label} không chấp nhận yêu cầu đăng nhập.")
        token_data = token.json()
        access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
        if not isinstance(access_token, str) or not access_token:
            return _oauth_error_page(f"{provider.label} không trả về quyền đăng nhập hợp lệ.")
        profile = requests.get(
            provider.profile_url, headers={"Authorization": f"Bearer {access_token}"},
            timeout=10, allow_redirects=False,
        )
        if profile.status_code != 200:
            return _oauth_error_page(f"Không thể lấy thông tin tài khoản {provider.label}.")
        info = profile.json()
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.warning("%s login connection failed (%s).", provider.label, type(exc).__name__)
        return _oauth_error_page(f"Không kết nối được với {provider.label}. Vui lòng thử lại.", status_code=503)
    except (requests.RequestException, ValueError):
        return _oauth_error_page(f"Phản hồi đăng nhập từ {provider.label} không hợp lệ.", status_code=502)

    if not isinstance(info, dict):
        return _oauth_error_page(f"Thông tin tài khoản {provider.label} không hợp lệ.")
    if provider.key == "google":
        subject, email, name = info.get("sub"), info.get("email"), info.get("name")
        if info.get("email_verified") is not True:
            return _oauth_error_page("Google chưa xác minh địa chỉ email của tài khoản.")
    else:
        subject = info.get("id")
        email = info.get("mail") or info.get("userPrincipalName")
        name = info.get("displayName")
    if not isinstance(subject, str) or not subject or not isinstance(email, str) or "@" not in email:
        return _oauth_error_page(f"Tài khoản {provider.label} thiếu mã định danh hoặc email.")
    email = email.strip().lower()
    try:
        full_name = name.strip() if isinstance(name, str) else ""
        user = _find_or_create_oauth_user(email, full_name or email.split("@")[0], provider.key, subject)
    except HTTPException as exc:
        return _oauth_error_page(exc.detail, status_code=exc.status_code)
    if not user.get("is_active", True):
        return _oauth_error_page("Tài khoản đã bị khóa.", status_code=403)
    token = create_access_token({"sub": str(user["_id"]), "role": user.get("role", "employee")})
    return _oauth_success_page(token)


@router.get("/google/login")
def google_login(request: Request):
    return _start_login("google", request)


@router.get("/microsoft/login")
def microsoft_login(request: Request):
    return _start_login("microsoft", request)


@router.get("/google/callback")
def google_callback(request: Request, state: str | None = None, code: str | None = None, error: str | None = None):
    return _login_callback("google", request, state, code, error)


@router.get("/microsoft/callback")
def microsoft_callback(request: Request, state: str | None = None, code: str | None = None, error: str | None = None):
    return _login_callback("microsoft", request, state, code, error)


def _find_or_create_oauth_user(email: str, full_name: str, provider: str, subject: str) -> dict:
    identity = {"oauth_provider": provider, "oauth_subject": subject}
    user = users_collection.find_one(identity)
    if user:
        return user
    by_email = users_collection.find_one({"email": email})
    if by_email:
        if not by_email.get("is_active", True):
            raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
        raise HTTPException(status_code=409, detail="Email đã thuộc một tài khoản NexusOffice. Hãy đăng nhập bằng phương thức đã liên kết.")

    new_user = {
        **identity, "full_name": full_name, "email": email, "username": f"{provider}_{uuid4().hex}",
        "phone": None, "position": "", "company": "", "department": "",
        "password_hash": hash_password(secrets.token_urlsafe(32)), "role": "employee",
        "is_active": True, "created_at": datetime.now(timezone.utc),
    }
    try:
        result = users_collection.insert_one(new_user)
    except DuplicateKeyError as exc:
        user = users_collection.find_one(identity)
        if user:
            return user
        raise HTTPException(status_code=409, detail="Email đã thuộc một tài khoản NexusOffice.") from exc
    new_user["_id"] = result.inserted_id
    return new_user


def _oauth_success_page(token: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="vi"><head><meta charset="UTF-8"><title>Đăng nhập thành công</title></head>
<body><p>Đang chuyển hướng…</p><script>
localStorage.setItem("access_token", "{token}");
window.location.replace("/dashboard");
</script></body></html>"""
    return HTMLResponse(html, headers=PAGE_HEADERS)


def _oauth_error_page(message: str, *, status_code: int = 400) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="vi"><head><meta charset="UTF-8"><title>Lỗi đăng nhập</title></head>
<body><p>{escape(message)}</p><a href="/login">Quay lại trang đăng nhập</a></body></html>"""
    return HTMLResponse(html, status_code=status_code, headers=PAGE_HEADERS)
