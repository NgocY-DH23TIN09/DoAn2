import logging
import re
import unicodedata
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from ai_assistant.document_analysis import summarize_extracted_document
from ai_assistant.history import (
    MongoHistoryError,
    get_integration_status,
    record_ai_interaction,
)
from ai_assistant.summarizer import SummarizationError, answer_from_sources, get_gemini_api_key, summarize_text
from backend.database import documents_collection
from backend.routers.auth import require_module, require_roles

router = APIRouter(
    prefix="/api/v1/assistant",
    tags=["AI Assistant"],
    dependencies=[Depends(require_module("assistant")), Depends(require_module("documents"))],
)
logger = logging.getLogger(__name__)
SEARCH_DOCUMENT_LIMIT = 500
QUESTION_WORDS = {"ai", "gi", "nao", "la", "cua", "cho", "hay", "ve", "co", "the", "da", "va", "nhu"}
SIGNATURE_LINE = re.compile(r"\b(?:nguoi ky|da ky|ky ten|chu ky|ky boi|ky thay)\b")


class SummarizeRequest(BaseModel):
    document_id: str
    max_length: int = Field(default=300, ge=50, le=5000)


class ProposalApproveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    document_id: str
    approved_title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=150)


class ChatAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] | None = Field(default=None, max_length=100)


def _document_text(doc: dict) -> str:
    return (doc.get("content") or "").strip()


def _search_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return re.findall(r"[a-z0-9]+", "".join(char for char in folded if not unicodedata.combining(char)))


def _relevant_source(doc: dict, terms: set[str], signature_question: bool):
    text = _document_text(doc)
    if not text:
        return None
    title = doc.get("title", "")
    title_score = len(terms.intersection(_search_words(title)))
    best_score = 0
    best_snippet = ""
    for start in range(0, len(text), 400):
        snippet = text[start:start + 700].strip()
        words = _search_words(snippet)
        hits = terms.intersection(words)
        signature_hit = signature_question and SIGNATURE_LINE.search(" ".join(words))
        score = len(hits) * 5 + (8 if signature_hit else 0)
        if score > best_score:
            best_score = score
            best_snippet = snippet
    if not best_score:
        return None
    return best_score + title_score, {"document_id": str(doc["_id"]), "title": title, "snippet": best_snippet}


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

    if doc.get("ocr_pages") and not get_gemini_api_key():
        summary = summarize_extracted_document(content) or ""
        if len(summary) > req.max_length:
            summary = summary[:req.max_length - 1].rstrip() + "…"
    else:
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

    if doc.get("drive_file"):
        return {
            "document_id": document_id,
            "original_name": doc["title"],
            "proposed_name": doc["title"],
            "proposed_category": doc.get("category"),
            "stored_filename": doc["drive_file"]["name"],
            "summary": doc.get("ai_summary"),
            "status": "READY" if doc.get("status") == "approved" else "AWAITING_REVIEW",
            "evidence": [{"field": "document_type", "quote": doc.get("analysis_reason", "")}],
        }

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
    if req.document_id != document_id:
        raise HTTPException(status_code=409, detail="Đề xuất không thuộc tài liệu đang duyệt.")
    doc = documents_collection.find_one({"_id": ObjectId(document_id)})
    if doc is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    old_title = doc.get("title")
    old_category = doc.get("category")
    updated = documents_collection.find_one_and_update(
        {"_id": doc["_id"], "status": "pending"},
        {"$set": {
            "title": req.approved_title.strip(),
            "category": req.category.strip(),
            "status": "approved",
            "approver_id": current_user["id"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="Tài liệu đã được xử lý.")
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
    if req.document_ids is not None:
        if any(not ObjectId.is_valid(document_id) for document_id in req.document_ids):
            raise HTTPException(status_code=422, detail="Mã tài liệu không hợp lệ.")
        query["_id"] = {"$in": [ObjectId(document_id) for document_id in req.document_ids]}

    question_words = _search_words(question)
    keywords = set(question_words) - QUESTION_WORDS or set(question_words)
    signature_question = "ky" in keywords and bool({"ai", "nguoi", "chu"}.intersection(question_words))
    ranked = []
    searched_count = 0
    limit_reached = False
    documents = documents_collection.find(query, {"content": 1, "title": 1}).sort(
        [("created_at", -1), ("_id", -1)]
    ).limit(SEARCH_DOCUMENT_LIMIT + 1)
    for index, doc in enumerate(documents):
        if index == SEARCH_DOCUMENT_LIMIT:
            limit_reached = True
            break
        searched_count += 1
        source = _relevant_source(doc, keywords, signature_question)
        if source:
            ranked.append(source)
    matched_sources = [source for _, source in sorted(ranked, key=lambda item: item[0], reverse=True)[:5]]
    scope = {"documents_searched": searched_count, "limit_reached": limit_reached, "document_limit": SEARCH_DOCUMENT_LIMIT}
    scope_warning = (
        f"Chỉ tra cứu {SEARCH_DOCUMENT_LIMIT} tài liệu mới nhất. Hãy chọn tài liệu cụ thể để tìm trong hồ sơ cũ hơn."
        if limit_reached else None
    )

    if not matched_sources:
        return {
            "answer": "Chưa tìm thấy căn cứ phù hợp trong phạm vi tài liệu được chọn. Vui lòng thử từ khóa khác hoặc mở rộng phạm vi.",
            "sources": [],
            "search_scope": scope,
            "scope_warning": scope_warning,
        }

    history_input = f"Câu hỏi: {question}\nNguồn:\n" + "\n".join(source["snippet"] for source in matched_sources)
    answer = answer_from_sources(question, matched_sources)
    history_warning = _save_ai_history("ask", None, history_input, answer)
    return {
        "answer": answer,
        "sources": matched_sources,
        "history_warning": history_warning,
        "search_scope": scope,
        "scope_warning": scope_warning,
    }
