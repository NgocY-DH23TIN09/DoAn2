import logging
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from ai_assistant.history import (
    MongoHistoryError,
    get_integration_status,
    record_ai_interaction,
)
from ai_assistant.summarizer import SummarizationError, summarize_text
from backend.database import documents_collection
from backend.routers.auth import get_current_user, require_roles

router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["AI Assistant"],
    dependencies=[Depends(get_current_user)],
)
logger = logging.getLogger(__name__)


class SummarizeRequest(BaseModel):
    document_id: str
    max_length: int = Field(default=300, ge=50, le=5000)


class ProposalApproveRequest(BaseModel):
    approved_title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=150)


class ChatAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = Field(default=None, max_length=100)


def _document_text(doc: dict) -> str:
    return (doc.get("content") or "").strip()


def _save_ai_history(
    action: str,
    document_id: str | None,
    input_text: str,
    output_text: str,
) -> str | None:
    try:
        record_ai_interaction(
            action=action,
            document_id=document_id,
            input_text=input_text,
            output_text=output_text,
        )
    except MongoHistoryError as exc:
        logger.warning("AI history was not saved: %s", exc)
        return "Không lưu được lịch sử AI vào MongoDB."
    return None


def _summarize(text: str, max_length: int | None = None) -> str:
    try:
        return summarize_text(text, max_length=max_length)
    except SummarizationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/integrations")
def api_integration_status():
    return get_integration_status()


@router.post("/summarize")
def api_summarize_document(
    req: SummarizeRequest,
):
    if not ObjectId.is_valid(req.document_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    doc = documents_collection.find_one({"_id": ObjectId(req.document_id)})
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    content = _document_text(doc)
    if not content:
        raise HTTPException(status_code=422, detail="Tài liệu chưa có nội dung văn bản để tóm tắt.")

    summary = _summarize(content, req.max_length)
    document_id = str(doc["_id"])
    history_warning = _save_ai_history("summarize", document_id, content, summary)
    documents_collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {"ai_summary": summary}},
    )
    return {
        "document_id": document_id,
        "title": doc["title"],
        "summary": summary,
        "history_warning": history_warning,
    }


@router.post("/analyze/{document_id}")
def api_analyze_and_propose(
    document_id: str,
):
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    doc = documents_collection.find_one({"_id": ObjectId(document_id)})
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    content = _document_text(doc) or doc["title"]
    summary = _summarize(content)
    document_id = str(doc["_id"])
    history_warning = _save_ai_history("analyze", document_id, content, summary)

    lower_text = content.lower()
    if "quyết định" in lower_text:
        doc_type, category = "QuyetDinh", "02_HanhChinh/QuyetDinh"
    elif "biên bản" in lower_text:
        doc_type, category = "BienBan", "02_HanhChinh/BienBan"
    elif "giáo trình" in lower_text:
        doc_type, category = "GiaoTrinh", "01_HocLieu"
    elif "bài giảng" in lower_text:
        doc_type, category = "BaiGiang", "01_HocLieu"
    else:
        doc_type, category = "TaiLieu", "90_CanPhanLoai"

    extension = Path(doc["file_path"]).suffix if doc.get("file_path") else ".pdf"
    clean_title = "".join(
        char if char.isalnum() or char in (" ", "_") else "" for char in doc["title"]
    ).strip().replace(" ", "_")
    proposed_name = f"{doc_type}_{clean_title[:60] or f'tai_lieu_{document_id}'}{extension}"
    evidence_quote = content[:200].strip()

    return {
        "document_id": document_id,
        "original_name": doc["title"],
        "proposed_name": proposed_name,
        "proposed_category": category,
        "summary": summary,
        "status": "AWAITING_REVIEW",
        "history_warning": history_warning,
        "evidence": [{
            "field": "document_type",
            "quote": evidence_quote or "Dựa trên tên tài liệu (chưa có nội dung trích xuất).",
        }],
    }


@router.post("/proposals/{document_id}/approve")
def api_approve_proposal(
    document_id: str,
    req: ProposalApproveRequest,
    current_user: dict = Depends(require_roles("admin", "manager")),
):
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    doc = documents_collection.find_one({"_id": ObjectId(document_id)})
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    old_title = doc.get("title")
    old_category = doc.get("category")
    updated = documents_collection.find_one_and_update(
        {"_id": doc["_id"]},
        {"$set": {
            "title": req.approved_title.strip(),
            "category": req.category.strip(),
            "status": "approved",
            "approver_id": current_user["id"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    return {
        "message": "Đã áp dụng tên và phân loại mới.",
        "before": {"title": old_title, "category": old_category},
        "after": {"title": updated["title"], "category": updated["category"]},
        "status": "READY",
    }


@router.post("/ask")
def api_ask_documents(
    req: ChatAskRequest,
):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Vui lòng nhập câu hỏi.")

    query = {}
    if req.document_ids:
        if any(not ObjectId.is_valid(document_id) for document_id in req.document_ids):
            raise HTTPException(status_code=422, detail="Mã tài liệu không hợp lệ.")
        query["_id"] = {"$in": [ObjectId(document_id) for document_id in req.document_ids]}

    keywords = [word.lower() for word in question.split() if len(word) > 2]
    matched_sources = []
    context_snippets = []
    documents = documents_collection.find(query).sort("created_at", -1).limit(500)
    for doc in documents:
        source_text = f"{_document_text(doc)} {doc.get('title', '')}"
        if any(keyword in source_text.lower() for keyword in keywords):
            snippet = source_text[:350].strip()
            matched_sources.append({
                "document_id": str(doc["_id"]),
                "title": doc.get("title", ""),
                "snippet": snippet,
            })
            context_snippets.append(f"[{doc.get('title', '')}]: {snippet}")
            if len(matched_sources) == 5:
                break

    if not matched_sources:
        return {
            "answer": "Chưa tìm thấy căn cứ phù hợp trong phạm vi tài liệu được chọn. Vui lòng thử từ khóa khác hoặc mở rộng phạm vi.",
            "sources": [],
        }

    history_input = f"Câu hỏi: {question}\nNgữ cảnh:\n" + "\n".join(context_snippets)
    answer = _summarize(history_input)
    history_warning = _save_ai_history("ask", None, history_input, answer)
    return {
        "answer": answer,
        "sources": matched_sources,
        "history_warning": history_warning,
    }
