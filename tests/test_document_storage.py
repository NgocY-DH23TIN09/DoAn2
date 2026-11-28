from datetime import date
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import unquote
from uuid import uuid4

import httplib2
import pytest
from bson import ObjectId
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from ai_assistant import document_analysis
from backend import document_storage, google_workspace
from backend.database import documents_collection, settings_collection
from main import app


def test_gemini_classifies_scanned_document_and_sanitizes_filename(monkeypatch):
    from google import genai

    class Model:
        def generate_content(self, *, model, contents, config):
            assert contents[1].inline_data.mime_type == "application/pdf"
            assert contents[1].inline_data.data == b"scan"
            assert config["response_mime_type"] == "application/json"
            return SimpleNamespace(text='{"kind":"BienBan","group":"/ Điều phối dự án /","title":"../Họp nhóm / Q1","summary":"Nội dung họp","reason":"Đầu đề là biên bản"}')

    class FakeClient:
        models = Model()

        def __init__(self, *, api_key, http_options):
            assert api_key == "unit-test"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setenv("GEMINI_API_KEY", "unit-test")
    monkeypatch.setattr(genai, "Client", FakeClient)
    result = document_analysis.analyze_document("scan.PDF", b"scan", "", date(2026, 11, 25))
    assert result.source == "gemini"
    assert result.category == "Điều phối dự án"
    assert result.filename == "BienBan_Họp_nhóm_Q1_2026-11-25.pdf"


@pytest.fixture
def drive_library(monkeypatch):
    records = {}
    fail_upload = {"enabled": False}
    key = f"drive-library-test-{uuid4().hex}"
    monkeypatch.setattr(document_storage, "LIBRARY_ID", key)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(google_workspace, "load_client", lambda: google_workspace.OAuthClient("client", "secret", "redirect"))
    monkeypatch.setattr(google_workspace, "connected_email", lambda user_id, client: "owner@example.com")

    class Files:
        def generateIds(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"ids": [uuid4().hex]})

        def get(self, *, fileId, fields, supportsAllDrives=False):
            return SimpleNamespace(execute=lambda: records[fileId])

        def list(self, *, q, pageSize, fields, supportsAllDrives, includeItemsFromAllDrives, pageToken=None):
            parent_id = q.split("'", 2)[1]
            return SimpleNamespace(execute=lambda: {
                "files": [
                    dict(record) for record in records.values()
                    if parent_id in record.get("parents", []) and not record.get("trashed")
                ],
            })

        def create(self, *, body, fields, media_body=None):
            def save():
                records[body["id"]] = dict(body)
                if media_body:
                    records[body["id"]]["content"] = media_body.getbytes(0, media_body.size())
                    if fail_upload["enabled"]:
                        raise HttpError(httplib2.Response({"status": "503"}), b"{}")
                return {"id": body["id"]}
            return SimpleNamespace(execute=save)

        def update(self, *, fileId, body, fields=None, addParents=None, removeParents=None, supportsAllDrives=False):
            def apply():
                record = records[fileId]
                record.update(body)
                if addParents:
                    assert record["parents"] == [removeParents]
                    record["parents"] = [addParents]
                return {"id": fileId}
            return SimpleNamespace(execute=apply)

        def get_media(self, *, fileId):
            return records[fileId]["content"]

    class FakeDownload:
        def __init__(self, stream, request, chunksize):
            stream.write(request)

        def next_chunk(self, num_retries):
            return None, True

    class FakeBatch:
        def __init__(self, callback):
            self.callback = callback
            self.requests = []

        def add(self, request, *, request_id):
            self.requests.append((request_id, request))

        def execute(self):
            for request_id, request in self.requests:
                try:
                    remote = request.execute()
                except KeyError:
                    self.callback(request_id, None, HttpError(httplib2.Response({"status": "404"}), b"{}"))
                else:
                    self.callback(request_id, remote, None)

    def service(user_id, api, version, *, expected_email, expected_client_id):
        assert api == "drive" and version == "v3"
        assert expected_email == "owner@example.com" and expected_client_id == "client"
        return SimpleNamespace(files=Files, new_batch_http_request=FakeBatch)

    monkeypatch.setattr(google_workspace, "get_service", service)
    monkeypatch.setattr(document_storage, "MediaIoBaseDownload", FakeDownload)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@123"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        try:
            yield client, headers, records, fail_upload
        finally:
            ids = [ObjectId(record["appProperties"]["nexusofficeDocumentId"]) for record in records.values() if "appProperties" in record]
            documents_collection.delete_many({"_id": {"$in": ids}})
            settings_collection.delete_many({"_id": {"$regex": f"^{key}(:|$)"}})


def test_library_creates_group_and_renames_uploads_from_both_forms(drive_library):
    client, headers, records, _ = drive_library
    unbound = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("raw.txt", b"content")})
    assert unbound.status_code == 409
    storage = client.post("/api/v1/documents/storage", headers=headers).json()
    assert storage["ready"]
    assert client.post("/api/v1/documents/storage", headers=headers).json()["folder_url"] == storage["folder_url"]

    payload = "QUYẾT ĐỊNH\nChủ đề: Vận hành mạng nội bộ\nVề việc giao nhiệm vụ".encode()
    uploaded = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("raw.txt", payload)})
    assert uploaded.status_code == 200
    first = uploaded.json()
    manual = client.post("/api/documents/", headers=headers, data={
        "title": "Tên văn bản thủ công", "doc_number": f"QA-{uuid4().hex[:16]}",
    }, files={"file": ("manual.txt", "BIÊN BẢN\nChủ đề: VAN HANH MANG NOI BO\nNội dung họp".encode())})
    assert manual.status_code == 200
    assert manual.json()["title"] == "Tên văn bản thủ công"
    assert first["file_name"].startswith("QuyetDinh_raw_")
    assert first["storage_category"] == "Vận hành mạng nội bộ" and first["analysis_source"] == "rules"
    assert manual.json()["storage_category"] == first["storage_category"]
    assert manual.json()["document_kind"] == "BienBan"
    group, = [record for record in records.values() if record["name"] == "Vận hành mạng nội bộ"]
    assert sum(record.get("parents") == [group["id"]] for record in records.values()) == 2
    saved = documents_collection.find_one({"_id": ObjectId(first["id"])})
    assert not saved.get("file_path") and saved["drive_file"]["id"] in records
    download = client.get(first["file_path"], headers=headers)
    assert download.status_code == 200 and download.content == payload


def test_failed_drive_upload_creates_no_document_record(drive_library):
    client, headers, records, fail = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    fail["enabled"] = True
    number = f"QA-{uuid4().hex[:16]}"
    error = client.post("/api/documents/", headers=headers, data={"title": "Test", "doc_number": number}, files={"file": ("test.txt", b"BIEU MAU")})
    assert error.status_code == 502
    assert documents_collection.find_one({"doc_number": number}) is None
    assert any(record.get("trashed") for record in records.values())


def test_admin_delete_moves_only_selected_file_to_trash(drive_library):
    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    payload = b"QUYET DINH\nChu de: Van hanh mang noi bo\nCan cu."
    first = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("first.txt", payload)}).json()
    second = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("second.txt", payload)}).json()
    first_record = documents_collection.find_one({"_id": ObjectId(first["id"])})
    second_record = documents_collection.find_one({"_id": ObjectId(second["id"])})
    first_file_id = first_record["drive_file"]["id"]
    second_file_id = second_record["drive_file"]["id"]
    folder_id = first_record["drive_file"]["folder_id"]

    deleted = client.delete(f"/api/v1/documents/{first['id']}", headers=headers)

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted_document": True, "drive_file_status": "moved_to_trash"}
    assert records[first_file_id]["trashed"] is True
    assert not records[second_file_id].get("trashed")
    assert not records[folder_id].get("trashed")
    assert documents_collection.find_one({"_id": ObjectId(first["id"])}) is None
    assert documents_collection.find_one({"_id": ObjectId(second["id"])}) is not None


def test_library_status_counts_drive_files_and_nested_folders(drive_library):
    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    uploaded = client.post(
        "/api/v1/documents/upload",
        headers=headers,
        files={"file": ("counted.txt", b"QUYET DINH\nChu de: Van hanh noi bo\nCan cu.")},
    ).json()
    document = documents_collection.find_one({"_id": ObjectId(uploaded["id"])})
    group_id = document["drive_file"]["folder_id"]
    nested_folder_id = uuid4().hex
    records[nested_folder_id] = {
        "id": nested_folder_id,
        "mimeType": document_storage.FOLDER_MIME,
        "parents": [group_id],
    }
    nested_file_id = uuid4().hex
    records[nested_file_id] = {"id": nested_file_id, "parents": [nested_folder_id]}
    trashed_file_id = uuid4().hex
    records[trashed_file_id] = {
        "id": trashed_file_id,
        "parents": [group_id],
        "trashed": True,
    }

    status = client.get("/api/v1/documents/storage", headers=headers)

    assert status.status_code == 200
    assert status.json()["drive_file_count"] == 2
    assert status.json()["drive_folder_count"] == 2


def test_drive_library_view_excludes_local_documents_but_general_list_keeps_them(drive_library):
    client, headers, _, _ = drive_library
    local = client.post("/api/documents/", headers=headers, data={
        "title": "Local-only document", "doc_number": f"LOCAL-{uuid4().hex[:12]}",
        "content": "Not stored in Google Drive.",
    })
    assert local.is_success
    local_id = local.json()["id"]

    general_list = client.get("/api/v1/documents", headers=headers)
    assert any(document["id"] == local_id for document in general_list.json())

    drive_list = client.get("/api/v1/documents?drive_only=true", headers=headers)
    assert drive_list.is_success
    assert all(document["id"] != local_id for document in drive_list.json())
    assert drive_list.headers["X-Drive-Not-Stored-Count"] == "1"


def test_library_reads_current_drive_filename_and_downloads_by_same_id(drive_library):
    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    upload = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("ban-dau.txt", b"QUYET DINH\nCan cu")})
    assert upload.is_success
    document = upload.json()
    stored = documents_collection.find_one({"_id": ObjectId(document["id"])})
    file_id = stored["drive_file"]["id"]
    renamed = "Quyết định đã sửa trên Drive"
    records[file_id]["name"] = renamed

    def listed_document():
        response = client.get("/api/v1/documents", headers=headers)
        assert response.is_success
        assert response.headers["Cache-Control"] == "private, no-store"
        return next(doc for doc in response.json() if doc["id"] == document["id"])

    live = listed_document()
    assert live["file_name"] == renamed and live["drive_name_verified"] is True
    assert live["file_mime_type"] == "text/plain"
    assert live["title"] == document["title"]
    assert live["file_path"] == document["file_path"]
    downloaded = client.get(live["file_path"], headers=headers)
    assert downloaded.is_success
    assert unquote(downloaded.headers["Content-Disposition"]).endswith(renamed)
    assert downloaded.content == records[file_id]["content"]
    assert documents_collection.find_one({"_id": stored["_id"]})["drive_file"]["name"] == document["file_name"]

    records[file_id]["trashed"] = True
    scan = client.get("/api/v1/documents", headers=headers)
    assert scan.status_code == 200
    assert scan.headers["X-Drive-Missing-Count"] == "1"
    assert all(item["id"] != document["id"] for item in scan.json())
    assert documents_collection.find_one({"_id": stored["_id"]}) is None


def test_drive_scan_removes_record_when_file_is_missing(drive_library):
    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    upload = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("missing.txt", b"QUYET DINH\nNoi dung")})
    assert upload.is_success
    document_id = upload.json()["id"]
    stored = documents_collection.find_one({"_id": ObjectId(document_id)})
    records.pop(stored["drive_file"]["id"])

    scan = client.get("/api/v1/documents", headers=headers)

    assert scan.status_code == 200
    assert scan.headers["X-Drive-Missing-Count"] == "1"
    assert documents_collection.find_one({"_id": ObjectId(document_id)}) is None


def test_word_preview_reads_stored_attachment_in_document_order(drive_library):
    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    word = Document()
    word.add_heading("Vận hành hệ thống", level=1)
    word.add_paragraph("Chuẩn bị tài liệu.").alignment = WD_ALIGN_PARAGRAPH.CENTER
    word.add_table(rows=1, cols=2).rows[0].cells[1].text = "Biên bản họp"
    word.add_paragraph("Kết thúc.")
    payload = BytesIO()
    word.save(payload)
    uploaded = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("word.docx", payload.getvalue())})
    assert uploaded.is_success
    document = uploaded.json()
    assert "Kết thúc." in document["content"]
    saved = documents_collection.find_one({"_id": ObjectId(document["id"])})
    records[saved["drive_file"]["id"]]["name"] = "Tệp Word được đổi tên không có đuôi"

    url = f"/api/documents/{document['id']}/word-preview"
    assert client.get(url).status_code == 401
    preview = client.get(url, headers=headers)
    assert preview.is_success
    assert preview.headers["Cache-Control"] == "private, no-store"
    heading, paragraph, table, closing = preview.json()["blocks"]
    assert (heading["type"], heading["text"]) == ("heading", "Vận hành hệ thống")
    assert (paragraph["alignment"], paragraph["text"]) == ("center", "Chuẩn bị tài liệu.")
    assert table == {"type": "table", "rows": [["", "Biên bản họp"]]}
    assert closing["text"] == "Kết thúc."


def test_reanalyze_scan_updates_summary_and_moves_same_drive_file(drive_library, monkeypatch):
    from backend.routers import documents as documents_router

    client, headers, records, _ = drive_library
    assert client.post("/api/v1/documents/storage", headers=headers).is_success
    raw_pdf = b"test only - simulated scanned PDF"
    monkeypatch.setattr(documents_router, "extract_text_from_file", lambda _: {
        "text": "", "page_count": 2, "ocr_pages": 0, "method": None, "warnings": ["Chưa đọc được chữ"],
    })
    upload = client.post("/api/v1/documents/upload", headers=headers, files={"file": ("qd.pdf", raw_pdf)})
    assert upload.is_success
    before = upload.json()
    assert before["storage_category"] == "Cần phân loại" and before["ai_summary"] is None
    record = documents_collection.find_one({"_id": ObjectId(before["id"])})
    drive_id = record["drive_file"]["id"]
    assert records[drive_id]["content"] == raw_pdf

    monkeypatch.setattr(documents_router, "extract_text_from_file", lambda _: {
        "text": "QUYẾT ĐỊNH\nVề việc tổ chức kỳ thi\nĐiều 1. Thành lập Ban tổ chức.\nĐiều 2. Phân công nhiệm vụ.",
        "page_count": 2, "ocr_pages": 2, "method": "ocr", "warnings": [],
    })
    replay = client.post(f"/api/v1/documents/{before['id']}/reanalyze", headers=headers)
    assert replay.is_success and replay.json()["drive_warning"] is None
    after = replay.json()["document"]
    assert after["ocr_pages"] == 2 and after["extraction_method"] == "ocr"
    assert "Điều 1. Thành lập Ban tổ chức." in after["ai_summary"]
    assert after["category"] == after["storage_category"] == "Tổ chức kỳ thi"
    assert after["file_name"].startswith("QuyetDinh_")
    updated = documents_collection.find_one({"_id": record["_id"]})
    assert updated["drive_file"]["id"] == drive_id and records[drive_id]["content"] == raw_pdf
    assert "analysis_job" not in updated
