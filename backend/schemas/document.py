from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    title: str
    doc_number: str
    doc_type: str
    content: Optional[str] = None
    file_path: Optional[str] = None
    category: Optional[str] = None
    status: str
    ai_summary: Optional[str] = None
    creator_id: str
    approver_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
