from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: str
    title: str
    doc_number: str
    doc_type: str
    content: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_mime_type: Optional[str] = None
    drive_name_verified: Optional[bool] = None
    storage_provider: Optional[str] = None
    storage_category: Optional[str] = None
    document_kind: Optional[str] = None
    analysis_source: Optional[str] = None
    analysis_reason: Optional[str] = None
    extraction_method: Optional[str] = None
    ocr_pages: int = 0
    page_count: int = 0
    extraction_warnings: list[str] = Field(default_factory=list)
    category: Optional[str] = None
    status: str
    ai_summary: Optional[str] = None
    creator_id: str
    approver_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
