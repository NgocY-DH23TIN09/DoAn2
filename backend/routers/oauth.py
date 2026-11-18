"""OAuth2 login with Google and Microsoft."""

import logging
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

import requests as http_requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.database import users_collection
from backend.security import create_access_token, hash_password

router = APIRouter(prefix="/api/auth", tags=["OAuth"])
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/google/callback"
).strip()

# ---------------------------------------------------------------------------
# Microsoft OAuth2
# ---------------------------------------------------------------------------
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
MICROSOFT_REDIRECT_URI = os.getenv(
    "MICROSOFT_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/microsoft/callback"
).strip()
MICROSOFT_TENANT = os.getenv("MICROSOFT_TENANT", "common").strip()


# ========================  GOOGLE  ========================


@router.get("/google/login")
def google_login():
    """Redirect user to Google consent screen."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth chưa được cấu hình. Vui lòng thiết lập GOOGLE_CLIENT_ID và GOOGLE_CLIENT_SECRET trong .env",
        )

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/google/callback")
def google_callback(code: str = None, error: str = None):
    """Handle Google OAuth2 callback."""
    if error:
        logger.warning("Google OAuth error: %s", error)
        return _oauth_error_page(f"Google từ chối đăng nhập: {error}")
    if not code:
        return _oauth_error_page("Không nhận được mã xác thực từ Google.")

    # Exchange authorization code for tokens
    token_resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if token_resp.status_code != 200:
        logger.error("Google token exchange failed: %s", token_resp.text)
        return _oauth_error_page("Không thể xác thực với Google. Vui lòng thử lại.")

    access_token = token_resp.json().get("access_token")

    # Fetch user profile
    profile_resp = http_requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if profile_resp.status_code != 200:
        return _oauth_error_page("Không thể lấy thông tin tài khoản Google.")

    info = profile_resp.json()
    email = info.get("email", "")
    full_name = info.get("name", email.split("@")[0])

    if not email:
        return _oauth_error_page("Tài khoản Google không có email.")

    user = _find_or_create_oauth_user(email, full_name, "google")
    token = create_access_token({"sub": str(user["_id"]), "role": user.get("role", "employee")})
    return _oauth_success_page(token)


# ========================  MICROSOFT  ========================


@router.get("/microsoft/login")
def microsoft_login():
    """Redirect user to Microsoft consent screen."""
    if not MICROSOFT_CLIENT_ID or not MICROSOFT_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Microsoft OAuth chưa được cấu hình. Vui lòng thiết lập MICROSOFT_CLIENT_ID và MICROSOFT_CLIENT_SECRET trong .env",
        )

    params = {
        "client_id": MICROSOFT_CLIENT_ID,
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile User.Read",
        "response_mode": "query",
    }
    url = f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/authorize?{urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/microsoft/callback")
def microsoft_callback(code: str = None, error: str = None, error_description: str = None):
    """Handle Microsoft OAuth2 callback."""
    if error:
        logger.warning("Microsoft OAuth error: %s – %s", error, error_description)
        return _oauth_error_page(f"Microsoft từ chối đăng nhập: {error_description or error}")
    if not code:
        return _oauth_error_page("Không nhận được mã xác thực từ Microsoft.")

    # Exchange authorization code for tokens
    token_resp = http_requests.post(
        f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/token",
        data={
            "code": code,
            "client_id": MICROSOFT_CLIENT_ID,
            "client_secret": MICROSOFT_CLIENT_SECRET,
            "redirect_uri": MICROSOFT_REDIRECT_URI,
            "grant_type": "authorization_code",
            "scope": "openid email profile User.Read",
        },
        timeout=15,
    )
    if token_resp.status_code != 200:
        logger.error("Microsoft token exchange failed: %s", token_resp.text)
        return _oauth_error_page("Không thể xác thực với Microsoft. Vui lòng thử lại.")

    access_token = token_resp.json().get("access_token")

    # Fetch user profile from Microsoft Graph
    profile_resp = http_requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if profile_resp.status_code != 200:
        return _oauth_error_page("Không thể lấy thông tin tài khoản Microsoft.")

    info = profile_resp.json()
    email = info.get("mail") or info.get("userPrincipalName", "")
    full_name = info.get("displayName", email.split("@")[0])

    if not email:
        return _oauth_error_page("Tài khoản Microsoft không có email.")

    user = _find_or_create_oauth_user(email, full_name, "microsoft")
    token = create_access_token({"sub": str(user["_id"]), "role": user.get("role", "employee")})
    return _oauth_success_page(token)


# ========================  HELPERS  ========================


def _find_or_create_oauth_user(email: str, full_name: str, provider: str) -> dict:
    """Look up user by email; create a new account if first-time OAuth login."""
    user = users_collection.find_one({"email": email})
    if user:
        # Update provider info if not set
        if not user.get("oauth_provider"):
            users_collection.update_one(
                {"_id": user["_id"]}, {"$set": {"oauth_provider": provider}}
            )
        return user

    new_user = {
        "full_name": full_name,
        "email": email,
        "username": f"{provider}_{uuid4().hex[:12]}",
        "phone": None,
        "position": "",
        "company": "",
        "department": "",
        "password_hash": hash_password(secrets.token_urlsafe(32)),
        "role": "employee",
        "oauth_provider": provider,
        "created_at": datetime.now(timezone.utc),
    }
    result = users_collection.insert_one(new_user)
    new_user["_id"] = result.inserted_id
    logger.info("Created OAuth user %s via %s", email, provider)
    return new_user


def _oauth_success_page(token: str) -> HTMLResponse:
    """Store JWT in localStorage and redirect to dashboard."""
    html = f"""\
<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Đăng nhập thành công</title></head>
<body>
    <p style="text-align:center;margin-top:40px;font-family:sans-serif">Đang chuyển hướng…</p>
    <script>
        localStorage.setItem("access_token", "{token}");
        window.location.replace("/dashboard");
    </script>
</body>
</html>"""
    return HTMLResponse(html)


def _oauth_error_page(message: str) -> HTMLResponse:
    """Show error message then redirect back to login after 3 seconds."""
    html = f"""\
<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Lỗi đăng nhập</title>
<style>body{{font-family:sans-serif;text-align:center;margin-top:60px}}
.msg{{color:#c0392b;font-size:1.1rem}}.sub{{color:#666;margin-top:12px}}</style></head>
<body>
    <p class="msg">{message}</p>
    <p class="sub">Tự động quay lại trang đăng nhập sau 3 giây…</p>
    <script>setTimeout(function(){{ window.location.replace("/login"); }}, 3000);</script>
</body>
</html>"""
    return HTMLResponse(html, status_code=400)
