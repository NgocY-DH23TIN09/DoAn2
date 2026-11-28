from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest

from main import app

@pytest.fixture(scope="module", autouse=True)
def start_test_app():
    global client
    with TestClient(app) as client:
        yield


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_login_admin_default_user():
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "access_token" in payload
    assert payload["token_type"] == "bearer"


def test_register_new_user():
    unique_suffix = str(uuid4().int % 10**8).zfill(8)
    unique_email = f"qa.user.{unique_suffix}@example.com"
    unique_phone = f"0999{unique_suffix}"
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "QA Tester",
            "phone": unique_phone,
            "email": unique_email,
            "position": "Nhân viên",
            "company": "Test Company",
            "password": "Abcdef12",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert ObjectId.is_valid(data["id"])
    assert data["email"] == unique_email
    assert data["role"] == "employee"


def test_public_pages_load():
    for path in ["/", "/login", "/dashboard", "/tasks", "/documents", "/meetings"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")


def test_dashboard_shows_document_and_meeting_features():
    response = client.get("/dashboard")

    assert "Chức năng chính" in response.text
    assert response.text.count('href="/documents"') == 2
    assert "Tài liệu & Wiki AI" in response.text
    assert response.text.count('href="/meetings"') >= 2
    assert "Phòng họp thông minh" in response.text


def test_protected_endpoints_require_login():
    assert client.get("/api/documents/").status_code == 401
    assert client.get("/api/tasks/assignees").status_code == 401
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/meetings/").status_code == 401
    assert client.get("/api/v1/meetings/google/status").status_code == 401
    assert client.post("/api/v1/meetings/google/connect").status_code == 401
    assert client.post("/api/v1/assistant/ask", json={"question": "Tìm tài liệu"}).status_code == 401


def test_google_meet_creation_returns_mocked_calendar_link(monkeypatch):
    from backend.routers import meetings as meetings_router

    calls = {}

    class FakeRequest:
        def execute(self):
            return {
                "id": "test-event-id",
                "hangoutLink": "https://meet.google.com/abc-defg-hij",
                "htmlLink": "https://calendar.google.com/calendar/event?eid=test",
            }

    class FakeEvents:
        def insert(self, **kwargs):
            calls.update(kwargs)
            return FakeRequest()

    class FakeCalendarService:
        def events(self):
            return FakeEvents()

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def fake_calendar_service(user_id):
        calls["user_id"] = user_id
        return FakeCalendarService()

    monkeypatch.setattr(
        meetings_router,
        "_get_google_calendar_service",
        fake_calendar_service,
    )

    response = client.post(
        "/api/v1/meetings/google/create",
        headers=headers,
        json={
            "title": "Cuộc họp kiểm thử",
            "start_at": "2099-05-20T09:30:00",
            "duration_minutes": 60,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["meet_url"] == "https://meet.google.com/abc-defg-hij"
    assert result["event_id"] == "test-event-id"
    assert calls["user_id"] == client.get("/api/auth/me", headers=headers).json()["id"]
    assert calls["calendarId"] == "primary"
    assert calls["conferenceDataVersion"] == 1
    assert calls["body"]["summary"] == "Cuộc họp kiểm thử"
    assert calls["body"]["start"]["timeZone"] == "Asia/Ho_Chi_Minh"
    assert calls["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"


def test_employee_can_connect_and_create_personal_google_meet(monkeypatch, tmp_path):
    from backend.routers import meetings as meetings_router

    suffix = str(uuid4().int % 10**8).zfill(8)
    phone = f"0777{suffix}"
    email = f"meeting.user.{suffix}@example.com"
    registration = client.post(
        "/api/auth/register",
        json={
            "full_name": "Meeting User",
            "phone": phone,
            "email": email,
            "position": "Nhân viên",
            "company": "Test Company",
            "password": "Abcdef12",
        },
    )
    assert registration.status_code == 200
    login = client.post(
        "/api/auth/login",
        json={"username": phone, "password": "Abcdef12"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(meetings_router, "GOOGLE_CLIENT_SECRETS", credentials_file)
    started_oauth_for = []
    monkeypatch.setattr(
        meetings_router,
        "_start_google_oauth",
        lambda requested_user_id: (
            started_oauth_for.append(requested_user_id)
            or "https://accounts.google.com/o/oauth2/v2/auth?state=test"
        ),
    )

    connect_response = client.post(
        "/api/v1/meetings/google/connect",
        headers=headers,
    )

    assert connect_response.status_code == 200
    assert connect_response.json()["status"] == "connecting"
    assert connect_response.json()["authorization_url"].startswith(
        "https://accounts.google.com/"
    )
    assert started_oauth_for == [user_id]

    class FakeRequest:
        def execute(self):
            return {
                "id": "employee-event-id",
                "hangoutLink": "https://meet.google.com/employee-link",
            }

    class FakeEvents:
        def insert(self, **kwargs):
            return FakeRequest()

    class FakeCalendarService:
        def events(self):
            return FakeEvents()

    service_user_ids = []
    monkeypatch.setattr(
        meetings_router,
        "_get_google_calendar_service",
        lambda requested_user_id: (
            service_user_ids.append(requested_user_id) or FakeCalendarService()
        ),
    )
    create_response = client.post(
        "/api/v1/meetings/google/create",
        headers=headers,
        json={
            "title": "Họp cá nhân",
            "start_at": "2099-05-20T09:30:00",
            "duration_minutes": 30,
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["meet_url"] == "https://meet.google.com/employee-link"
    assert service_user_ids == [user_id]
    meetings_router._oauth_statuses.pop(user_id, None)
    meetings_router._oauth_authorization_urls.pop(user_id, None)


def test_google_calendar_authorization_url_uses_browser_without_server_browser(
    monkeypatch, tmp_path
):
    import json
    import threading
    from google_auth_oauthlib.flow import Flow, InstalledAppFlow
    from backend.routers import meetings as meetings_router

    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps({
            "installed": {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }),
        encoding="utf-8",
    )
    token_dir = tmp_path / "tokens"
    monkeypatch.setattr(meetings_router, "GOOGLE_CLIENT_SECRETS", credentials_file)
    monkeypatch.setattr(meetings_router, "GOOGLE_TOKEN_DIR", token_dir)
    monkeypatch.setattr(
        Flow,
        "authorization_url",
        lambda self, **kwargs: (
            "https://accounts.google.com/o/oauth2/v2/auth?state=test",
            "test-state",
        ),
    )
    flow_options = {}

    class FakeCredentials:
        def to_json(self):
            return '{"token":"test-only"}'

    def fake_run_local_server(self, **kwargs):
        flow_options.update(kwargs)
        self.authorization_url()
        return FakeCredentials()

    monkeypatch.setattr(InstalledAppFlow, "run_local_server", fake_run_local_server)
    ready = threading.Event()
    result = {}

    meetings_router._run_google_oauth("test-user", ready, result)

    assert ready.is_set()
    assert result["authorization_url"].startswith("https://accounts.google.com/")
    assert flow_options["open_browser"] is False
    assert flow_options["timeout_seconds"] == 600
    assert (token_dir / "test-user.json").read_text(encoding="utf-8") == (
        '{"token":"test-only"}'
    )
    assert meetings_router._oauth_statuses["test-user"] == "connected"
    meetings_router._oauth_statuses.pop("test-user", None)
    meetings_router._oauth_authorization_urls.pop("test-user", None)


def test_google_meet_rejects_past_start_without_calling_calendar(monkeypatch):
    from backend.routers import meetings as meetings_router

    def unexpected_calendar_call():
        raise AssertionError("Calendar should not be called for a past meeting.")

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    monkeypatch.setattr(
        meetings_router,
        "_get_google_calendar_service",
        unexpected_calendar_call,
    )

    response = client.post(
        "/api/v1/meetings/google/create",
        headers=headers,
        json={
            "title": "Cuộc họp đã qua",
            "start_at": "2000-01-01T09:30:00",
            "duration_minutes": 60,
        },
    )

    assert response.status_code == 422


def test_upload_document_and_download_requires_auth(monkeypatch):
    from types import SimpleNamespace

    from backend import document_storage
    from backend.database import documents_collection
    from backend.routers import assistant as assistant_router
    from ai_assistant.history import MongoHistoryError

    monkeypatch.setenv("GEMINI_API_KEY", "")
    payload = b"Noi dung tai lieu kiem thu de tao tom tat."
    monkeypatch.setattr(document_storage, "require_library", lambda: {})
    monkeypatch.setattr(document_storage, "library_groups", lambda binding: ())
    monkeypatch.setattr(
        document_storage, "upload_attachment",
        lambda binding, document_id, category, name, content: SimpleNamespace(metadata={
            "id": "test-drive-file", "name": name, "mime_type": "text/plain", "category": category,
        }),
    )
    monkeypatch.setattr(document_storage, "download_named_attachment", lambda attachment: (attachment["name"], payload))
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    filename = f"qa-{uuid4().hex}.txt"
    response = client.post(
        "/api/v1/documents/upload",
        headers=headers,
        files={"file": (filename, payload, "text/plain")},
    )
    assert response.status_code == 200
    document = response.json()
    assert ObjectId.is_valid(document["id"])
    assert document["title"] == filename.removesuffix(".txt")
    assert document["category"] == "Cần phân loại"
    assert document["storage_provider"] == "google_drive"
    assert document["creator_id"]

    def fail_history(**kwargs):
        assert kwargs["action"] == "summarize"
        raise MongoHistoryError("MongoDB unavailable")

    def summarize_for_test(text, max_length=None):
        assert text
        assert max_length == 300
        return "Tóm tắt AI"

    monkeypatch.setattr(assistant_router, "summarize_text", summarize_for_test)
    monkeypatch.setattr(assistant_router, "record_ai_interaction", fail_history)
    summary_response = client.post(
        "/api/v1/assistant/summarize",
        headers=headers,
        json={"document_id": document["id"]},
    )
    assert summary_response.status_code == 200
    assert summary_response.json()["summary"] == "Tóm tắt AI"
    assert summary_response.json()["history_warning"]

    file_response = client.get(f"/api/documents/{document['id']}/file", headers=headers)
    assert file_response.status_code == 200
    assert file_response.content == payload
    assert client.get(f"/api/documents/{document['id']}/file").status_code == 401

    documents_collection.delete_one({"_id": ObjectId(document["id"])})


def test_employee_cannot_assign_task_to_another_user():
    suffix = str(uuid4().int % 10**8).zfill(8)
    phone = f"0888{suffix}"
    email = f"task.user.{suffix}@example.com"
    registration = client.post(
        "/api/auth/register",
        json={
            "full_name": "Task User",
            "phone": phone,
            "email": email,
            "position": "Nhân viên",
            "company": "Test Company",
            "password": "Abcdef12",
        },
    )
    assert registration.status_code == 200
    login = client.post("/api/auth/login", json={"username": phone, "password": "Abcdef12"})
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    admin_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    admin_id = client.get("/api/auth/me", headers=admin_headers).json()["id"]

    response = client.post(
        "/api/tasks/",
        headers=user_headers,
        json={"title": "Không được giao", "assignee_id": admin_id},
    )
    assert response.status_code == 403


def test_task_crud_uses_string_object_ids():
    from backend.database import tasks_collection

    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    admin_id = client.get("/api/auth/me", headers=headers).json()["id"]
    task_id = None

    try:
        create_response = client.post(
            "/api/tasks/",
            headers=headers,
            json={"title": "Kiểm thử Mongo task", "assignee_id": admin_id},
        )
        assert create_response.status_code == 200
        created_task = create_response.json()
        task_id = created_task["id"]
        assert ObjectId.is_valid(task_id)
        assert created_task["assignee_id"] == admin_id

        list_response = client.get("/api/tasks/", headers=headers)
        assert list_response.status_code == 200
        assert any(task["id"] == task_id for task in list_response.json())

        update_response = client.patch(
            f"/api/tasks/{task_id}/status",
            headers=headers,
            json={"status": "done"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["status"] == "done"
    finally:
        if task_id:
            tasks_collection.delete_one({"_id": ObjectId(task_id)})


def test_upload_rejects_unsupported_file_types():
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = client.post(
        "/api/v1/documents/upload",
        headers=headers,
        files={"file": ("malware.exe", b"not an executable", "application/octet-stream")},
    )
    assert response.status_code == 415
