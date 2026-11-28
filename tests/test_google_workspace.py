import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from backend import google_workspace as workspace
from backend.database import google_workspace_collection, users_collection
from main import app

ORIGIN = "http://127.0.0.1:8001"
EMAIL = "connected@example.com"


@pytest.fixture
def workspace_user(monkeypatch, tmp_path):
    config_id = f"oauth_config_test_{uuid4().hex}"
    monkeypatch.setattr(workspace, "CLIENT_CONFIG_ID", config_id)
    credentials_file = tmp_path / "oauth_web.json"
    credentials_file.write_text(json.dumps({"web": {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "redirect_uris": [ORIGIN + workspace.CALLBACK_PATH],
    }}), encoding="utf-8")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLIENT_FILE", str(credentials_file))
    monkeypatch.delenv("GOOGLE_WORKSPACE_REDIRECT_URI", raising=False)

    with TestClient(app, base_url=ORIGIN) as client:
        username = f"workspace.{uuid4().hex}@example.com"
        registration = client.post("/api/auth/register", json={
            "full_name": "Workspace QA",
            "email": username,
            "phone": f"086{uuid4().int % 10**9:09d}",
            "position": "Nhân viên",
            "company": "Test",
            "password": "WorkspaceTest@123",
        })
        assert registration.is_success
        user_id = registration.json()["id"]
        try:
            login = client.post("/api/auth/login", json={"username": username, "password": "WorkspaceTest@123"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            yield client, headers, user_id
        finally:
            google_workspace_collection.delete_one({"_id": config_id})
            google_workspace_collection.delete_one({"_id": user_id})
            users_collection.delete_one({"_id": ObjectId(user_id)})


def _stub_google_authorization(monkeypatch, *, scopes=workspace.SCOPES):
    exchanges = []

    def exchange(url, *, data, timeout, allow_redirects):
        assert url == workspace.TOKEN_URL
        assert data["redirect_uri"] == ORIGIN + workspace.CALLBACK_PATH
        assert data["code"] == "test-authorization-code"
        assert len(data["code_verifier"]) >= 43
        assert not allow_redirects and timeout > 0
        exchanges.append(data["code"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "expires_in": 3600,
            "scope": " ".join(scopes),
        })

    def profile(url, *, headers, timeout):
        assert url == workspace.USERINFO_URL
        assert headers["Authorization"] == "Bearer test-access-token"
        assert timeout > 0
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "email": EMAIL, "email_verified": True,
        })

    monkeypatch.setattr(workspace.requests, "post", exchange)
    monkeypatch.setattr(workspace.requests, "get", profile)
    return exchanges


def _start(client, headers):
    start = client.post("/api/v1/google/connect", headers=headers)
    assert start.is_success
    assert "HttpOnly" in start.headers["set-cookie"]
    authorization = urlsplit(start.json()["authorization_url"])
    assert authorization.hostname == "accounts.google.com"
    query = parse_qs(authorization.query)
    assert set(query["scope"][0].split()) == set(workspace.SCOPES)
    assert query["code_challenge_method"] == ["S256"]
    assert "client_secret" not in query
    return {"state": query["state"][0], "code": "test-authorization-code"}


def test_google_callback_binds_browser_consumes_state_and_encrypts_token(workspace_user, monkeypatch):
    client, headers, user_id = workspace_user
    exchanges = _stub_google_authorization(monkeypatch)
    assert client.post("/api/v1/google/connect").status_code == 401
    params = _start(client, headers)

    with TestClient(app, base_url=ORIGIN) as other_browser:
        reply = other_browser.get(workspace.CALLBACK_PATH, params=params, follow_redirects=False)
        assert reply.headers["location"].endswith("=expired")
    assert exchanges == []

    nonce = client.cookies.get("nexus_google_oauth")
    reply = client.get(workspace.CALLBACK_PATH, params=params, follow_redirects=False)
    assert reply.headers["location"].endswith("=connected")
    assert reply.headers["cache-control"] == "no-store"
    replay = client.get(workspace.CALLBACK_PATH, params=params, headers={"Cookie": f"nexus_google_oauth={nonce}"}, follow_redirects=False)
    assert replay.headers["location"].endswith("=expired")
    assert len(exchanges) == 1
    status = client.get("/api/v1/google/status", headers=headers).json()
    assert status["connected"] and status["email"] == EMAIL
    record = google_workspace_collection.find_one({"_id": user_id})
    assert "test-refresh-token" not in str(record)
    assert "test-client-secret" not in str(record)
    assert "pending" not in record


def test_google_calendar_drive_and_meet_use_linked_user_credentials(workspace_user, monkeypatch):
    client, headers, _ = workspace_user
    _stub_google_authorization(monkeypatch)
    callback = client.get(workspace.CALLBACK_PATH, params=_start(client, headers), follow_redirects=False)
    assert callback.headers["location"].endswith("=connected")
    calls = []

    class CalendarEvents:
        def list(self, **kwargs):
            assert kwargs["calendarId"] == "primary"
            return SimpleNamespace(execute=lambda: {"items": [{"id": "event", "summary": "Test"}]})

        def insert(self, **kwargs):
            assert kwargs["calendarId"] == "primary"
            return SimpleNamespace(execute=lambda: {"id": "meet", "hangoutLink": "https://meet.google.com/test"})

    class DriveFiles:
        def list(self, **kwargs):
            assert kwargs["pageToken"] == "next"
            assert kwargs["q"] == "trashed = false"
            return SimpleNamespace(execute=lambda: {"files": [{"id": "drive", "name": "Existing"}]})

        def create(self, **kwargs):
            assert kwargs["media_body"].getbytes(0, 4) == b"test"
            return SimpleNamespace(execute=lambda: {"name": kwargs["body"]["name"]})

    def build(api, version, *, http, cache_discovery, static_discovery):
        assert http.credentials.token == "test-access-token"
        assert version == "v3" and not cache_discovery and static_discovery
        calls.append(api)
        if api == "calendar":
            return SimpleNamespace(events=CalendarEvents)
        return SimpleNamespace(files=DriveFiles)

    monkeypatch.setattr(workspace, "build", build)
    assert client.get("/api/v1/google/calendar/events", headers=headers).json()["items"][0]["id"] == "event"
    assert client.get("/api/v1/google/drive/files?page_token=next", headers=headers).json()["items"][0]["id"] == "drive"
    upload = client.post("/api/v1/google/drive/files", headers=headers, files={"file": ("test.txt", b"test")})
    assert upload.status_code == 201 and upload.json()["name"] == "test.txt"
    meeting = client.post("/api/v1/meetings/google/create", headers=headers, json={
        "title": "Workspace test", "start_at": "2099-05-20T09:30:00", "duration_minutes": 30,
    })
    assert meeting.json()["event_id"] == "meet"
    assert calls == ["calendar", "drive", "drive", "calendar"]
    assert client.delete("/api/v1/google/connection", headers=headers).json() == {"connected": False}
    assert client.get("/api/v1/google/calendar/events", headers=headers).status_code == 409


def test_partial_google_consent_does_not_create_connection(workspace_user, monkeypatch):
    client, headers, _ = workspace_user
    _stub_google_authorization(monkeypatch, scopes=workspace.SCOPES[:-1])
    result = client.get(workspace.CALLBACK_PATH, params=_start(client, headers), follow_redirects=False)
    assert result.headers["location"].endswith("=permissions")
    assert not client.get("/api/v1/google/status", headers=headers).json()["connected"]


def _admin_headers(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@123"})
    assert response.is_success
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_only_admin_can_save_google_oauth_config_and_secret_is_encrypted(workspace_user):
    client, employee_headers, _ = workspace_user
    data = {
        "client_id": "999-nexus-qa.apps.googleusercontent.com",
        "client_secret": "qa-value-for-client-secret",
    }
    assert not client.get("/api/v1/google/status", headers=employee_headers).json()["can_configure"]
    assert client.put("/api/v1/google/config", headers=employee_headers, data=data).status_code == 403
    assert google_workspace_collection.find_one({"_id": workspace.CLIENT_CONFIG_ID}) is None

    headers = _admin_headers(client)
    initial_status = client.get("/api/v1/google/status", headers=headers).json()
    assert initial_status["can_configure"]
    assert initial_status["setup_redirect_uri"] == "http://127.0.0.1:8001/auth/google/callback"
    save = client.put("/api/v1/google/config", headers=headers, data=data)
    assert save.json() == {"configured": True, "redirect_uri": ORIGIN + workspace.CALLBACK_PATH}
    assert save.headers["cache-control"] == "no-store"
    assert data["client_secret"] not in save.text
    record = google_workspace_collection.find_one({"_id": workspace.CLIENT_CONFIG_ID})
    assert data["client_secret"] not in str(record)
    assert workspace.load_client().client_secret == data["client_secret"]

    response = client.post("/api/v1/google/connect", headers=employee_headers)
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert query["client_id"] == [data["client_id"]]
    assert "client_secret" not in query


def test_saved_legacy_callback_uri_uses_new_callback_path(workspace_user):
    client, _, _ = workspace_user
    headers = _admin_headers(client)
    data = {
        "client_id": "999-nexus-qa.apps.googleusercontent.com",
        "client_secret": "qa-value-for-client-secret",
    }
    assert client.put("/api/v1/google/config", headers=headers, data=data).is_success

    google_workspace_collection.update_one(
        {"_id": workspace.CLIENT_CONFIG_ID},
        {"$set": {"redirect_uri": ORIGIN + workspace.LEGACY_CALLBACK_PATH}},
    )

    assert workspace.load_client().redirect_uri == ORIGIN + workspace.CALLBACK_PATH
    status = client.get("/api/v1/google/status", headers=headers)
    assert status.json()["redirect_uri"] == ORIGIN + workspace.CALLBACK_PATH


def test_upload_accepts_google_web_client_json_with_the_current_callback(workspace_user):
    client, _, _ = workspace_user
    headers = _admin_headers(client)
    web = {
        "client_id": "998-nexus-qa.apps.googleusercontent.com",
        "client_secret": "qa-secret-from-web-client-json",
        "redirect_uris": ["http://127.0.0.1:8000/api/v1/google/callback"],
    }

    def upload(payload):
        return client.put("/api/v1/google/config", headers=headers, files={
            "oauth_json": ("client_secret.json", json.dumps(payload), "application/json"),
        })

    invalid = upload({"web": web})
    assert invalid.status_code == 422 and "Redirect URI" in invalid.json()["detail"]
    assert web["client_secret"] not in invalid.text
    assert upload({"installed": web}).status_code == 422
    assert google_workspace_collection.find_one({"_id": workspace.CLIENT_CONFIG_ID}) is None
    web["redirect_uris"] = [ORIGIN + workspace.CALLBACK_PATH]
    assert upload({"web": web}).is_success
    assert workspace.load_client().client_id == web["client_id"]
