import hashlib
import hmac
import json
import logging
import os
import re
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httplib2
import requests
from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from backend.database import BASE_DIR, google_workspace_collection
from backend.security import get_secret_key

CALLBACK_PATH = "/auth/google/callback"
LEGACY_CALLBACK_PATH = "/api/v1/google/callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email", CALENDAR_SCOPE, DRIVE_SCOPE)
REQUEST_TIMEOUT = 15
CLIENT_CONFIG_ID = "oauth_client_config"
CLIENT_SECRET_PURPOSE = b"nexusoffice/google-workspace/client/v1"
logger = logging.getLogger(__name__)


class GoogleWorkspaceError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str


def _allowed_redirect(uri: str) -> bool:
    try:
        url = urlsplit(uri)
    except ValueError:
        return False
    secure_origin = url.scheme == "https" or (
        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    return bool(
        secure_origin and url.hostname and url.path == CALLBACK_PATH
        and not (url.username or url.password or url.query or url.fragment)
    )


def load_client(origin: str | None = None) -> OAuthClient:
    saved = google_workspace_collection.find_one({"_id": CLIENT_CONFIG_ID})
    if saved:
        try:
            encrypted = saved["client_secret"]
            secret = _credential_cipher(CLIENT_SECRET_PURPOSE).decrypt(encrypted.encode()).decode()
            redirect_uri = saved["redirect_uri"]
            parsed_redirect = urlsplit(redirect_uri)
            if parsed_redirect.path == LEGACY_CALLBACK_PATH:
                redirect_uri = parsed_redirect._replace(path=CALLBACK_PATH).geturl()
            if not _allowed_redirect(redirect_uri):
                raise ValueError("Invalid saved OAuth redirect URI")
            return OAuthClient(saved["client_id"], secret, redirect_uri)
        except (InvalidToken, KeyError, AttributeError, TypeError, UnicodeError, ValueError) as exc:
            raise GoogleWorkspaceError("Không đọc được cấu hình OAuth đã lưu. Quản trị viên cần nhập lại cấu hình.") from exc
    return _load_client_file(origin)


def _load_client_file(origin: str | None) -> OAuthClient:
    filename = os.getenv("GOOGLE_WORKSPACE_CLIENT_FILE", "").strip() or "google_workspace_credentials.json"
    try:
        client = json.loads((BASE_DIR / filename).read_text(encoding="utf-8-sig"))["web"]
        client_id = client["client_id"]
        client_secret = client["client_secret"]
        if not all(isinstance(value, str) and value.strip() for value in (client_id, client_secret)):
            raise ValueError("Incomplete OAuth client")
    except FileNotFoundError as exc:
        raise GoogleWorkspaceError("NexusOffice chưa có cấu hình OAuth cho Google Lịch và Drive.") from exc
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise GoogleWorkspaceError("Tệp OAuth Google Workspace phải là JSON của client loại Web application.") from exc

    redirect_uri = os.getenv("GOOGLE_WORKSPACE_REDIRECT_URI", "").strip()
    if not redirect_uri:
        registered = client.get("redirect_uris") or []
        if not isinstance(registered, list):
            raise GoogleWorkspaceError("Danh sách redirect URI trong tệp OAuth Google không hợp lệ.")
        redirects = [uri for uri in registered if isinstance(uri, str) and _allowed_redirect(uri)]
        redirect_uri = next((uri for uri in redirects if uri == f"{origin}{CALLBACK_PATH}"), "")
        if not redirect_uri and redirects:
            redirect_uri = redirects[0]
    if not _allowed_redirect(redirect_uri):
        raise GoogleWorkspaceError(f"Cần đăng ký redirect URI kết thúc bằng {CALLBACK_PATH} trên Google Cloud.")
    return OAuthClient(client_id, client_secret, redirect_uri)


def validate_client_config(client_id: str, client_secret: str, redirect_uri: str) -> OAuthClient:
    client_id = client_id.strip()
    client_secret = client_secret.strip()
    if not _allowed_redirect(redirect_uri):
        raise GoogleWorkspaceError("Địa chỉ nhận cấp quyền phải dùng HTTPS hoặc localhost.", 422)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}\.apps\.googleusercontent\.com", client_id):
        raise GoogleWorkspaceError("Client ID OAuth phải kết thúc bằng .apps.googleusercontent.com.", 422)
    if not re.fullmatch(r"[!-~]{12,256}", client_secret):
        raise GoogleWorkspaceError("Client Secret OAuth chưa hợp lệ. Hãy kiểm tra lại giá trị từ Google Cloud.", 422)
    return OAuthClient(client_id, client_secret, redirect_uri)


def save_client_config(client: OAuthClient, user_id: str) -> None:
    encrypted = _credential_cipher(CLIENT_SECRET_PURPOSE).encrypt(client.client_secret.encode()).decode()
    google_workspace_collection.update_one({"_id": CLIENT_CONFIG_ID}, {"$set": {
        "client_id": client.client_id,
        "client_secret": encrypted,
        "redirect_uri": client.redirect_uri,
        "updated_at": datetime.now(timezone.utc),
        "updated_by": user_id,
    }}, upsert=True)


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def authorization_url(client: OAuthClient, state: str, verifier: str) -> str:
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return f"{AUTH_URL}?" + urlencode({
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account consent",
    })


def exchange_code(client: OAuthClient, code: str, verifier: str) -> tuple[Credentials, str]:
    try:
        response = requests.post(TOKEN_URL, data={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": client.redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
        }, timeout=REQUEST_TIMEOUT, allow_redirects=False)
        response.raise_for_status()
        token = response.json()
        granted = set(token.get("scope", "").split())
        if not set(SCOPES) <= granted:
            raise GoogleWorkspaceError("Hãy cấp đủ quyền Google Lịch, Google Drive và email tài khoản.", 422)
        if not token.get("access_token") or not token.get("refresh_token"):
            raise GoogleWorkspaceError("Google chưa trả về quyền truy cập ngoại tuyến.", 502)

        credentials = Credentials(
            token=token["access_token"],
            refresh_token=token["refresh_token"],
            token_uri=TOKEN_URL,
            client_id=client.client_id,
            client_secret=client.client_secret,
            scopes=SCOPES,
            expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(token["expires_in"])),
        )
        profile_response = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=REQUEST_TIMEOUT,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        if profile.get("email_verified") is not True or not isinstance(profile.get("email"), str) or not profile["email"]:
            raise GoogleWorkspaceError("Google chưa xác minh địa chỉ email của tài khoản này.", 422)
        return credentials, profile["email"]
    except (requests.RequestException, KeyError, AttributeError, TypeError, ValueError) as exc:
        raise GoogleWorkspaceError("Không thể hoàn tất cấp quyền từ Google.", 502) from exc


def _credential_cipher(purpose: bytes = b"nexusoffice/google-workspace/v1") -> Fernet:
    key = hmac.digest(get_secret_key().encode(), purpose, "sha256")
    return Fernet(urlsafe_b64encode(key))


def _seal_credentials(credentials: Credentials) -> str:
    payload = credentials.to_json(strip=("client_id", "client_secret", "token_uri", "scopes"))
    return _credential_cipher().encrypt(payload.encode()).decode()


def save_connection(user_id: str, state_hash: str, client: OAuthClient, credentials: Credentials, email: str) -> bool:
    result = google_workspace_collection.update_one(
        {"_id": user_id, "pending.state_hash": state_hash, "pending.claimed": True},
        {"$set": {"connection": {
            "id": uuid4().hex,
            "client_id": client.client_id,
            "email": email,
            "credentials": _seal_credentials(credentials),
        }}, "$unset": {"pending": ""}},
    )
    return result.matched_count == 1


def connected_email(user_id: str, client: OAuthClient) -> str | None:
    record = google_workspace_collection.find_one(
        {"_id": user_id, "connection.client_id": client.client_id}, {"connection.email": 1}
    )
    return record["connection"]["email"] if record else None


def get_service(
    user_id: str, api: str, version: str, *,
    expected_email: str | None = None, expected_client_id: str | None = None,
):
    client = load_client()
    if expected_client_id is not None and client.client_id != expected_client_id:
        raise GoogleWorkspaceError("Hãy khôi phục OAuth client đã dùng để kết nối kho Drive.", 409)
    query = {"_id": user_id, "connection.client_id": client.client_id}
    if expected_email is not None:
        query["connection.email"] = expected_email
    record = google_workspace_collection.find_one(query)
    if not record:
        message = "Hãy kết nối lại đúng tài khoản Google đang sở hữu kho Drive." if expected_email else "Bạn chưa kết nối tài khoản Google Workspace."
        raise GoogleWorkspaceError(message, 409)

    connection = record["connection"]
    try:
        info = json.loads(_credential_cipher().decrypt(connection["credentials"].encode()))
        info.update(client_id=client.client_id, client_secret=client.client_secret)
        credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
    except (InvalidToken, KeyError, TypeError, ValueError) as exc:
        raise GoogleWorkspaceError("Không đọc được quyền Google đã lưu. Hãy kết nối lại.", 409) from exc

    if not credentials.valid:
        try:
            credentials.refresh(GoogleAuthRequest())
        except GoogleAuthError as exc:
            logger.warning("Google Workspace token refresh failed (%s).", type(exc).__name__)
            status = 502 if exc.retryable else 409
            raise GoogleWorkspaceError("Không làm mới được quyền Google. Hãy thử kết nối lại.", status) from exc
        result = google_workspace_collection.update_one(
            {"_id": user_id, "connection.id": connection["id"], "connection.credentials": connection["credentials"]},
            {"$set": {"connection.credentials": _seal_credentials(credentials)}},
        )
        if not result.matched_count and not google_workspace_collection.find_one(
            {"_id": user_id, "connection.id": connection["id"]}, {"_id": 1}
        ):
            raise GoogleWorkspaceError("Kết nối Google đã thay đổi. Hãy tải lại trang.", 409)

    transport = AuthorizedHttp(credentials, http=httplib2.Http(timeout=REQUEST_TIMEOUT))
    return build(api, version, http=transport, cache_discovery=False, static_discovery=True)
