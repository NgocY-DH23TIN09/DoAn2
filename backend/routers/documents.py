import logging
from datetime import datetime, timedelta, timezone
from ntpath import basename
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from lxml.etree import XMLSyntaxError
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ai_assistant.document_analysis import analyze_document
from ai_assistant.extractor import extract_text_from_file
from ai_assistant.summarizer import summarize_document_text
from backend import document_storage
from backend.database import documents_collection
from backend.document_preview import word_preview_blocks
from backend.google_workspace import GoogleWorkspaceError
from backend.permissions import system_config
from backend.routers.auth import get_current_user, require_module, require_roles
from backend.schemas.document import DocumentResponse

router = APIRouter(
    tags=["Documents"],
    dependencies=[Depends(require_module("documents"))],
)

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".jpg", ".jpeg", ".png"}
UPLOAD_TIMEZONE = timezone(timedelta(hours=7))
logger = logging.getLogger(__name__)


async def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = basename(file.filename or "")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Định dạng tệp chưa được hỗ trợ.")

    content = await file.read(document_storage.MAX_ATTACHMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Tệp tải lên đang trống.")
    if len(content) > document_storage.MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Kích thước tệp tối đa là 10 MB.")

    return filename, content


def _document_response(document: dict, *, drive_names: dict[str, str] | None = None) -> dict:
    attachment = document.get("drive_file")
    local_path = document.get("file_path")
    document_id = str(document["_id"])
    live_name = drive_names.get(document_id) if drive_names is not None else None
    file_name = (live_name or attachment["name"]) if attachment else Path(local_path).name if local_path else None
    return {
        "id": document_id,
        "title": document["title"],
        "doc_number": document["doc_number"],
        "doc_type": document.get("doc_type", "internal"),
        "content": document.get("content"),
        "file_path": f"/api/documents/{document['_id']}/file" if attachment else local_path,
        "file_name": file_name,
        "file_mime_type": attachment.get("mime_type") if attachment else None,
        "drive_name_verified": live_name is not None if attachment and drive_names is not None else None,
        "storage_provider": "google_drive" if attachment else "local" if local_path else None,
        "storage_category": attachment.get("category") if attachment else None,
        "document_kind": document.get("document_kind"),
        "analysis_source": document.get("analysis_source"),
        "analysis_reason": document.get("analysis_reason"),
        "extraction_method": document.get("extraction_method"),
        "ocr_pages": document.get("ocr_pages", 0),
        "page_count": document.get("page_count", 0),
        "extraction_warnings": document.get("extraction_warnings", []),
        "category": document.get("category"),
        "status": document.get("status", "pending"),
        "ai_summary": document.get("ai_summary"),
        "creator_id": document["creator_id"],
        "approver_id": document.get("approver_id"),
        "created_at": document["created_at"],
    }


def _read_and_analyze(
    filename: str, payload: bytes, created_at: datetime, binding: dict,
    content: str | None = None, *, include_summary: bool = True,
):
    with TemporaryDirectory(prefix="nexusoffice-doc-") as scratch:
        temporary_file = Path(scratch) / f"document{Path(filename).suffix.lower()}"
        temporary_file.write_bytes(payload)
        extraction = extract_text_from_file(str(temporary_file))
    extracted = extraction["text"][:250000]
    timestamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    analysis = analyze_document(
        filename, payload, extracted or content or "", timestamp.astimezone(UPLOAD_TIMEZONE).date(),
        existing_groups=document_storage.library_groups(binding),
        include_summary=include_summary,
    )
    return analysis, {
        "content": content or extracted or None,
        "ai_summary": analysis.summary,
        "category": analysis.category,
        "document_kind": analysis.kind,
        "analysis_source": analysis.source,
        "analysis_reason": analysis.reason,
        "extraction_method": extraction.get("method"),
        "ocr_pages": extraction.get("ocr_pages", 0),
        "page_count": extraction["page_count"],
        "extraction_warnings": extraction["warnings"],
    }


def _create_document(
    *,
    current_user: dict,
    title: str,
    doc_number: str,
    doc_type: str,
    content: str | None,
    upload: tuple[str, bytes] | None,
) -> dict:
    normalized_number = doc_number.strip()
    if not (title.strip() and normalized_number and doc_type.strip()):
        raise HTTPException(status_code=422, detail="Tiêu đề, số hiệu và loại văn bản không được để trống.")
    if documents_collection.find_one({"doc_number": normalized_number}):
        raise HTTPException(status_code=400, detail="Số hiệu văn bản đã tồn tại.")

    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "title": title.strip(),
        "doc_number": normalized_number,
        "doc_type": doc_type.strip(),
        "content": content,
        "status": "pending",
        "creator_id": current_user["id"],
        "created_at": now,
    }
    attachment = None
    auto_summarize = system_config().get("ai_auto_summarize", True)
    if upload is None:
        document["ai_summary"] = summarize_document_text(content) if auto_summarize and content else None
    else:
        try:
            binding = document_storage.require_library()
        except GoogleWorkspaceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        filename, payload = upload
        analysis, fields = _read_and_analyze(filename, payload, now, binding, content, include_summary=auto_summarize)
        try:
            attachment = document_storage.upload_attachment(binding, str(document["_id"]), analysis.category, analysis.filename, payload)
        except GoogleWorkspaceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        document.update({
            **fields,
            "category": attachment.metadata["category"],
            "original_filename": filename,
            "drive_file": attachment.metadata,
        })
    try:
        documents_collection.insert_one(document)
    except DuplicateKeyError as exc:
        if attachment:
            attachment.discard()
        raise HTTPException(status_code=400, detail="Số hiệu văn bản đã được sử dụng.") from exc
    except PyMongoError as exc:
        try:
            saved = documents_collection.find_one({"_id": document["_id"]})
            if saved is not None:
                return saved
            if attachment:
                attachment.discard()
        except PyMongoError:
            logger.warning("Could not verify final insert result for document %s.", document["_id"])
        raise HTTPException(status_code=503, detail="Chưa xác nhận được bản ghi tài liệu. Vui lòng kiểm tra danh sách trước khi thử lại.") from exc
    return document


@router.get("/api/documents/", response_model=list[DocumentResponse])
@router.get("/api/v1/documents", response_model=list[DocumentResponse])
def get_documents(response: Response, status: str | None = None, drive_only: bool = False):
    query = {"status": status} if status else {}
    documents = list(documents_collection.find(query).sort("created_at", DESCENDING))
    attachments = {str(document["_id"]): document["drive_file"] for document in documents if document.get("drive_file")}
    drive_names, missing_drive_documents = document_storage.current_attachment_names(attachments)
    missing_documents = [
        document for document in documents if str(document["_id"]) in missing_drive_documents
    ]
    for document in missing_documents:
        documents_collection.delete_one({
            "_id": document["_id"],
            "drive_file.id": document["drive_file"].get("id"),
        })
    not_on_drive = {
        str(document["_id"]) for document in documents if not document.get("drive_file")
    }
    excluded_documents = missing_drive_documents | (not_on_drive if drive_only else set())
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Drive-Missing-Count"] = str(len(missing_drive_documents))
    response.headers["X-Drive-Not-Stored-Count"] = str(len(not_on_drive) if drive_only else 0)
    return [
        _document_response(document, drive_names=drive_names)
        for document in documents
        if str(document["_id"]) not in excluded_documents
    ]


@router.delete("/api/v1/documents/{doc_id}")
def delete_document_and_drive_file(doc_id: str, current_user: dict = Depends(require_roles("admin"))):
    if not current_user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    document = documents_collection.find_one({"_id": ObjectId(doc_id)})
    if document is None:
        raise HTTPException(status_code=404, detail="Tài liệu không còn trong ứng dụng.")
    attachment = document.get("drive_file")
    if not attachment:
        raise HTTPException(status_code=409, detail="Tài liệu này không có tệp trên Google Drive.")

    try:
        drive_status = document_storage.trash_library_attachment(attachment)
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    result = documents_collection.delete_one({
        "_id": document["_id"],
        "drive_file.id": attachment["id"],
    })
    return {
        "deleted_document": result.deleted_count == 1,
        "drive_file_status": drive_status,
    }


@router.get("/api/v1/documents/storage")
def get_document_library_storage(current_user: dict = Depends(get_current_user)):
    try:
        return document_storage.library_status(current_user)
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/v1/documents/storage")
def activate_document_library_storage(current_user: dict = Depends(require_roles("admin"))):
    if not current_user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    try:
        return document_storage.activate_library(current_user)
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _attachment_document(doc_id: str) -> dict:
    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    document = documents_collection.find_one({"_id": ObjectId(doc_id)})
    if document is None or not (document.get("drive_file") or document.get("file_path")):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    return document


def _read_drive_attachment(attachment: dict) -> tuple[str, bytes]:
    try:
        return document_storage.download_named_attachment(attachment)
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _local_attachment_path(document: dict) -> Path:
    upload_root = UPLOAD_DIR.resolve()
    file_path = (upload_root.parent / document["file_path"]).resolve()
    if upload_root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    return file_path


@router.get("/api/documents/{doc_id}/file")
def download_document_file(doc_id: str):
    document = _attachment_document(doc_id)

    attachment = document.get("drive_file")
    if attachment:
        name, payload = _read_drive_attachment(attachment)
        filename = quote(name, safe="")
        return Response(payload, media_type=attachment["mime_type"], headers={
            "Content-Disposition": f"attachment; filename*=utf-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    file_path = _local_attachment_path(document)
    return FileResponse(file_path, filename=file_path.name)


@router.get("/api/documents/{doc_id}/word-preview")
def preview_word_document(doc_id: str, response: Response):
    document = _attachment_document(doc_id)
    attachment = document.get("drive_file")
    name = attachment["name"] if attachment else document["file_path"]
    if Path(name).suffix.lower() != ".docx":
        raise HTTPException(status_code=415, detail="Chế độ này chỉ hỗ trợ tệp Word DOCX.")

    if attachment:
        _, payload = _read_drive_attachment(attachment)
    else:
        with _local_attachment_path(document).open("rb") as source:
            payload = source.read(document_storage.MAX_ATTACHMENT_BYTES + 1)
        if len(payload) > document_storage.MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="Tệp vượt quá giới hạn xem trước 10 MB.")
    try:
        blocks = word_preview_blocks(payload)
    except (BadZipFile, KeyError, ValueError, XMLSyntaxError) as exc:
        raise HTTPException(status_code=422, detail="Không đọc được bố cục của tệp Word này.") from exc
    response.headers["Cache-Control"] = "private, no-store"
    return {"blocks": blocks}


@router.post("/api/documents/", response_model=DocumentResponse)
async def create_document(
    title: str = Form(..., min_length=1, max_length=200),
    doc_number: str = Form(..., min_length=1, max_length=50),
    doc_type: str = Form("internal", max_length=20),
    content: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    upload = await _read_upload(file) if file and file.filename else None
    document = await run_in_threadpool(
        _create_document,
        current_user=current_user, title=title, doc_number=doc_number, doc_type=doc_type,
        content=(content or "").strip() or None, upload=upload,
    )
    return _document_response(document)


@router.post("/api/v1/documents/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    upload = await _read_upload(file)
    title = Path(upload[0]).stem.strip()[:200] or "Tài liệu mới"
    document = await run_in_threadpool(
        _create_document,
        current_user=current_user, title=title, doc_number=f"UPL-{uuid4().hex[:16].upper()}",
        doc_type="internal", content=None, upload=upload,
    )
    return _document_response(document)


@router.post("/api/v1/documents/{doc_id}/reanalyze")
def reanalyze_document(doc_id: str, current_user: dict = Depends(require_roles("admin", "manager"))):
    if not current_user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    key = {"_id": ObjectId(doc_id)}
    task = uuid4().hex
    now = datetime.now(timezone.utc)
    document = documents_collection.find_one_and_update({
        **key, "status": "pending", "drive_file": {"$exists": True},
        "$or": [{"analysis_job": {"$exists": False}}, {"analysis_job.until": {"$lt": now}}],
    }, {"$set": {"analysis_job": {"id": task, "until": now + timedelta(minutes=15)}}}, return_document=ReturnDocument.AFTER)
    if document is None:
        if documents_collection.find_one(key, {"_id": 1}) is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
        raise HTTPException(status_code=409, detail="Chỉ đọc lại tệp Drive đang chờ duyệt và chưa có lượt xử lý khác.")

    claim = {**key, "analysis_job.id": task}
    attachment = document["drive_file"]
    filename = document.get("original_filename") or f"{document['title']}{Path(attachment['name']).suffix}"
    try:
        binding = document_storage.require_library()
        payload = document_storage.download_attachment(attachment)
        analysis, fields = _read_and_analyze(filename, payload, document["created_at"], binding)
        if not (fields["content"] or analysis.summary):
            warning = next(iter(fields["extraction_warnings"]), "Chưa đọc được nội dung tài liệu.")
            raise HTTPException(status_code=422, detail=warning)
        saved = documents_collection.find_one_and_update(
            {**claim, "status": "pending"}, {"$set": {**fields, "original_filename": filename}},
            return_document=ReturnDocument.AFTER,
        )
        if saved is None:
            raise HTTPException(status_code=409, detail="Tài liệu đã đổi trạng thái trong khi xử lý.")
        try:
            updated_attachment = document_storage.reclassify_attachment(binding, attachment, analysis.category, analysis.filename)
        except GoogleWorkspaceError as exc:
            return {"document": _document_response(saved), "drive_warning": str(exc)}
        updated = documents_collection.find_one_and_update(
            claim, {"$set": {"drive_file": updated_attachment}}, return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise HTTPException(status_code=409, detail="Tài liệu đã đổi trong khi lưu kết quả.")
        return {"document": _document_response(updated), "drive_warning": None}
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        documents_collection.update_one(claim, {"$unset": {"analysis_job": ""}})


@router.put("/api/documents/{doc_id}/approve", response_model=DocumentResponse)
def review_document(
    doc_id: str,
    action: str,
    current_user: dict = Depends(require_roles("admin", "manager")),
):
    if action not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Trạng thái duyệt không hợp lệ.")
    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản.")

    document = documents_collection.find_one_and_update(
        {"_id": ObjectId(doc_id), "status": "pending"},
        {"$set": {"status": action, "approver_id": current_user["id"]}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        if documents_collection.find_one({"_id": ObjectId(doc_id)}, {"_id": 1}):
            raise HTTPException(status_code=409, detail="Văn bản đã được xử lý.")
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản.")
    return _document_response(document)
