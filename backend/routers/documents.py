from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ai_assistant.extractor import extract_text_from_file
from ai_assistant.summarizer import summarize_document_text
from backend.database import documents_collection
from backend.routers.auth import get_current_user, require_roles
from backend.schemas.document import DocumentResponse

router = APIRouter(
    tags=["Documents"],
    dependencies=[Depends(get_current_user)],
)

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".jpg", ".jpeg", ".png"}


async def _store_upload(file: UploadFile) -> tuple[str, str, str]:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Định dạng tệp chưa được hỗ trợ.")

    content = await file.read(MAX_UPLOAD_SIZE + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Tệp tải lên đang trống.")
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Kích thước tệp tối đa là 10 MB.")

    stored_name = f"{uuid4().hex}{extension}"
    destination = UPLOAD_DIR / stored_name
    destination.write_bytes(content)
    original_name = Path(file.filename or "").stem.strip()
    title = original_name[:200] or "Tài liệu mới"
    return str(destination), f"uploads/{stored_name}", title


def _document_response(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "title": document["title"],
        "doc_number": document["doc_number"],
        "doc_type": document.get("doc_type", "internal"),
        "content": document.get("content"),
        "file_path": document.get("file_path"),
        "category": document.get("category"),
        "status": document.get("status", "pending"),
        "ai_summary": document.get("ai_summary"),
        "creator_id": document["creator_id"],
        "approver_id": document.get("approver_id"),
        "created_at": document["created_at"],
    }


def _create_document(
    *,
    current_user: dict,
    title: str,
    doc_number: str,
    doc_type: str,
    content: str | None,
    file_path: str | None,
    category: str | None = None,
) -> dict:
    normalized_number = doc_number.strip()
    if documents_collection.find_one({"doc_number": normalized_number}):
        raise HTTPException(status_code=400, detail="Số hiệu văn bản đã tồn tại.")

    document = {
        "title": title.strip(),
        "doc_number": normalized_number,
        "doc_type": doc_type.strip(),
        "content": content,
        "file_path": file_path,
        "category": category,
        "status": "pending",
        "ai_summary": summarize_document_text(content) if content else None,
        "creator_id": current_user["id"],
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = documents_collection.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=400, detail="Số hiệu văn bản đã được sử dụng.") from exc
    document["_id"] = result.inserted_id
    return document


@router.get("/api/documents/", response_model=list[DocumentResponse])
@router.get("/api/v1/documents", response_model=list[DocumentResponse])
def get_documents(status: str | None = None):
    query = {"status": status} if status else {}
    documents = documents_collection.find(query).sort("created_at", DESCENDING)
    return [_document_response(document) for document in documents]


@router.get("/api/documents/{doc_id}/file")
def download_document_file(doc_id: str):
    if not ObjectId.is_valid(doc_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    document = documents_collection.find_one({"_id": ObjectId(doc_id)})
    if document is None or not document.get("file_path"):
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")

    upload_root = UPLOAD_DIR.resolve()
    file_path = (upload_root.parent / document["file_path"]).resolve()
    if upload_root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp đính kèm.")
    return FileResponse(file_path, filename=file_path.name)


@router.post("/api/documents/", response_model=DocumentResponse)
async def create_document(
    title: str = Form(..., min_length=1, max_length=200),
    doc_number: str = Form(..., min_length=1, max_length=50),
    doc_type: str = Form("internal", max_length=20),
    content: str | None = Form(None),
    file: UploadFile | None = File(None),
    current_user: dict = Depends(get_current_user),
):
    stored_path = None
    file_path = None
    extracted_text = ""
    if file and file.filename:
        stored_path, file_path, _ = await _store_upload(file)
        extracted_text = extract_text_from_file(stored_path)["text"]

    try:
        document = _create_document(
            current_user=current_user,
            title=title,
            doc_number=doc_number,
            doc_type=doc_type,
            content=content.strip() if content and content.strip() else extracted_text,
            file_path=file_path,
        )
    except Exception:
        if stored_path:
            Path(stored_path).unlink(missing_ok=True)
        raise
    return _document_response(document)


@router.post("/api/v1/documents/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    stored_path, file_path, title = await _store_upload(file)
    extracted_text = extract_text_from_file(stored_path)["text"]
    try:
        document = _create_document(
            current_user=current_user,
            title=title,
            doc_number=f"UPL-{uuid4().hex[:16].upper()}",
            doc_type="internal",
            content=extracted_text or None,
            file_path=file_path,
            category="00_TiepNhan",
        )
    except Exception:
        Path(stored_path).unlink(missing_ok=True)
        raise
    return _document_response(document)


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
        {"_id": ObjectId(doc_id)},
        {"$set": {"status": action, "approver_id": current_user["id"]}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản.")
    return _document_response(document)
