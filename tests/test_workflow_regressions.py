from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from bson import ObjectId
from docx import Document
from fastapi.testclient import TestClient
from requests import Timeout

from backend.database import approvals_collection, oauth_login_states, settings_collection, users_collection
from backend.routers import assistant, meetings, oauth
from backend.security import create_access_token
from main import app


@pytest.fixture
def client():
    with TestClient(app) as http:
        yield http


def account(role, **fields):
    suffix = uuid4().hex
    user = {"username": suffix, "full_name": role, "role": role, "email": f"{suffix}@example.com", **fields}
    user["_id"] = users_collection.insert_one(user).inserted_id
    return user, authorization(user)


def authorization(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user['_id']), 'role': user['role']})}"}


def test_module_permissions_and_role_ceiling(client):
    _, manager = account("manager")
    _, employee = account("employee")
    admin = authorization(users_collection.find_one({"username": "admin"}))
    payload = {"full_name": "Test member", "email": f"{uuid4().hex}@example.com", "role": "admin"}
    assert client.post("/api/v1/employees/", headers=manager, json=payload).status_code == 403
    assert client.post("/api/v1/employees/", headers=manager, json={**payload, "role": "employee"}).status_code == 200
    assert client.get("/api/v1/employees/", headers=employee).status_code == 403
    assert client.get("/api/v1/reports/overview", headers=employee).status_code == 403
    assert client.get("/api/v1/reports/overview", headers=manager).status_code == 200
    try:
        response = client.put("/api/v1/settings/", headers=admin, json={"permissions": {"manager": ["documents"]}})
        assert response.is_success
        assert client.get("/api/v1/reports/overview", headers=manager).status_code == 403
        assert client.get("/api/v1/employees/", headers=manager).status_code == 403
        assert client.get("/api/auth/me", headers=manager).json()["permissions"] == ["documents"]
        response = client.put("/api/v1/settings/", headers=admin, json={"ai_auto_summarize": False})
        assert response.is_success
        assert client.get("/api/v1/settings/", headers=admin).json()["permissions"]["manager"] == ["documents"]
        assert client.put("/api/v1/settings/", headers=admin, json={"require_two_step_approval": True}).status_code == 422
        doc = client.post("/api/documents/", headers=admin, data={
            "title": "Tài liệu không tự tóm tắt", "doc_number": uuid4().hex,
            "content": "Nội dung này đủ dài để có thể được tự động tóm tắt.",
        })
        assert doc.is_success and doc.json()["ai_summary"] is None
    finally:
        settings_collection.delete_one({"_id": "global_config"})


def test_blocked_token_is_denied_by_shared_dependency(client):
    user, headers = account("employee")
    assert client.get("/api/auth/me", headers=headers).is_success
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_active": False}})
    for path in ("/api/auth/me", "/api/tasks/", "/api/v1/documents", "/api/v1/schedules/"):
        assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_oauth_binds_pkce_to_browser_and_denies_locked_accounts(client, provider, monkeypatch):
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_ID", "test-id")
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_SECRET", "test-secret")
    user, _ = account("employee", oauth_provider=provider, oauth_subject=uuid4().hex, is_active=False)
    starts_at = f"/api/auth/{provider}/login"
    callback = f"/api/auth/{provider}/callback"
    redirect = client.get(starts_at, follow_redirects=False)
    params = parse_qs(urlsplit(redirect.headers["Location"]).query)
    state = params["state"][0]
    assert params["code_challenge_method"] == ["S256"]
    calls = []

    def exchange(url, *, data, **kwargs):
        assert data["code_verifier"]
        import base64
        from hashlib import sha256
        assert params["code_challenge"] == [base64.urlsafe_b64encode(sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")]
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "provider-token"})

    profile = {"email": user["email"], "email_verified": True, "sub": user["oauth_subject"], "id": user["oauth_subject"], "mail": user["email"]}
    monkeypatch.setattr(oauth.requests, "post", exchange)
    monkeypatch.setattr(oauth.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: profile))
    query = {"state": state, "code": "provider-code"}
    with TestClient(app) as different_browser:
        assert different_browser.get(callback, params=query).status_code == 400
    assert calls == []
    denied = client.get(callback, params=query)
    assert denied.status_code == 403 and "localStorage" not in denied.text
    assert client.get(callback, params=query).status_code == 400
    assert len(calls) == 1

    users_collection.update_one({"_id": user["_id"]}, {"$set": {"is_active": True}})
    redirect = client.get(starts_at, follow_redirects=False)
    params = parse_qs(urlsplit(redirect.headers["Location"]).query)
    assert client.get(callback, params={"state": params["state"][0], "code": "provider-code"}).status_code == 200


def test_oauth_expiry_and_network_errors(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")

    def start_flow():
        redirect = client.get("/api/auth/google/login", follow_redirects=False)
        return parse_qs(urlsplit(redirect.headers["Location"]).query)["state"][0]

    stale_state = start_flow()
    oauth_login_states.update_one({"_id": oauth._digest(stale_state)}, {
        "$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    })
    assert client.get("/api/auth/google/callback", params={"state": stale_state, "code": "expired"}).status_code == 400

    def timeout(*args, **kwargs):
        raise Timeout()

    monkeypatch.setattr(oauth.requests, "post", timeout)
    bad = client.get("/api/auth/google/callback", params={"state": start_flow(), "code": "provider-code"})
    assert bad.status_code == 503 and "Không kết nối được" in bad.text
    assert "&lt;" in oauth._oauth_error_page("ký tự < trong văn bản").body.decode()


def test_approval_concurrency_and_unique_codes(client):
    user, headers = account("employee")
    payload = {"title": "Phiếu kiểm thử", "content": "Xác nhận quy trình.", "approver_id": str(user["_id"])}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.post("/api/v1/approvals/", headers=headers, json=payload), range(8)))
        assert all(response.is_success for response in results)
        codes = {item.json()["code"] for item in results}
        assert len(codes) == 8
        item_id = results[0].json()["id"]

        def decide(action):
            return client.post(f"/api/v1/approvals/{item_id}/decide", headers=headers, json={"action": action})

        decisions = list(pool.map(decide, ("approved", "rejected")))
    assert sorted(response.status_code for response in decisions) == [200, 409]
    stored = approvals_collection.find_one({"_id": ObjectId(item_id)})
    assert len(stored["history"]) == 2
    assert stored["status"] == next(response.json()["status"] for response in decisions if response.is_success)


def test_bound_proposal_schedule_and_input_validation(client):
    admin = authorization(users_collection.find_one({"username": "admin"}))
    endpoint = "/api/v1/schedules/"
    starts = {"title": "Lịch kiểm thử", "start_time": "2026-11-30T09:00", "end_time": "2026-11-30T10:00:00+07:00"}
    created = client.post(endpoint, headers=admin, json=starts)
    assert created.is_success
    event = created.json()
    assert event["start_time"] == "2026-11-30T02:00:00+00:00"
    assert event in client.get(endpoint, headers=admin).json()
    assert client.post(endpoint, headers=admin, json={**starts, "end_time": "2026-11-30T02:00:00Z"}).status_code == 422
    assert client.post(endpoint, headers=admin, json={**starts, "title": "   "}).status_code == 422
    assert client.get("/api/v1/comms/chat/general?limit=0", headers=admin).status_code == 422

    payload = {"title": "Tên văn bản", "doc_number": uuid4().hex, "doc_type": "internal"}
    for key in payload:
        assert client.post("/api/documents/", headers=admin, data={**payload, key: "   "}).status_code == 422
    document = client.post("/api/documents/", headers=admin, data=payload).json()
    proposal = {"approved_title": "Tên duyệt", "category": "Nội bộ"}
    url = f"/api/v1/assistant/proposals/{document['id']}/approve"
    assert client.post(url, headers=admin, json={**proposal, "document_id": str(ObjectId())}).status_code == 409
    assert client.post(url, headers=admin, json={**proposal, "document_id": document["id"]}).status_code == 200
    assert client.post(url, headers=admin, json={**proposal, "document_id": document["id"]}).status_code == 409


def test_question_retrieves_signature_late_in_document_and_reports_scope(client, monkeypatch):
    monkeypatch.setattr(assistant, "SEARCH_DOCUMENT_LIMIT", 1)
    _, headers = account("employee")

    def create_document(text):
        return client.post("/api/documents/", headers=headers, data={
            "title": "Biên bản kiểm thử", "doc_number": uuid4().hex, "content": text,
        }).json()["id"]

    older = create_document("Người ký: Nguyễn Văn A")
    latest = create_document("Nội dung đầu văn bản. " * 90 + "\nNgười ký: Trần Văn B")
    result = client.post("/api/v1/assistant/ask", headers=headers, json={"question": "Ai ký?"}).json()
    assert result["sources"][0]["document_id"] == latest
    assert "Trần Văn B" in result["sources"][0]["snippet"]
    assert result["search_scope"]["limit_reached"] and result["scope_warning"]
    selected = client.post("/api/v1/assistant/ask", headers=headers, json={"question": "ai ky", "document_ids": [older]}).json()
    assert "Nguyễn Văn A" in selected["sources"][0]["snippet"] and selected["scope_warning"] is None


def test_meeting_link_lookup_failure_preserves_event_and_word_export(client, monkeypatch):
    _, headers = account("employee")
    inserted = {"id": "calendar-id", "htmlLink": "https://calendar.google.com/calendar/event?eid=test"}
    create = SimpleNamespace(execute=lambda: inserted)

    def fail_lookup(**kwargs):
        raise Timeout()

    events = SimpleNamespace(insert=lambda **kwargs: create, get=fail_lookup)
    monkeypatch.setattr(meetings, "_get_google_calendar_service", lambda user_id: SimpleNamespace(events=lambda: events))
    monkeypatch.setattr(meetings, "time", SimpleNamespace(sleep=lambda _: None))
    future = datetime.now(timezone.utc) + timedelta(days=1)
    response = client.post("/api/v1/meetings/google/create", headers=headers, json={"title": "Kiểm thử", "start_at": future.isoformat()})
    assert response.is_success
    event = response.json()
    assert event["status"] == "conference_pending" and event["event_id"] == inserted["id"]
    assert event["calendar_url"] == inserted["htmlLink"]

    generated = client.post("/api/v1/meetings/generate-minutes", headers=headers, json={
        "title": "Họp kiểm thử", "meeting_date": "2026-11-27", "participants": ["Nguyễn A"],
        "raw_notes": "Giao công việc và cần hoàn thành trước ngày đề ra.",
    }).json()
    url = f"/api/v1/meetings/{generated['id']}/docx"
    assert client.get(url).status_code == 401
    output = client.get(url, headers=headers)
    assert output.is_success
    exported = Document(BytesIO(output.content))
    assert exported.paragraphs[0].text == generated["title"]
    assert any("Nguyễn A" in paragraph.text for paragraph in exported.paragraphs)
