from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.database import settings_collection
from backend.routers.auth import get_current_user, require_roles

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["Settings & Permissions"],
    dependencies=[Depends(get_current_user)],
)

DEFAULT_PERMISSIONS = {
    "admin": ["dashboard", "tasks", "documents", "schedules", "meetings", "comms", "approvals", "employees", "reports", "assistant", "settings"],
    "manager": ["dashboard", "tasks", "documents", "schedules", "meetings", "comms", "approvals", "employees", "reports", "assistant"],
    "employee": ["dashboard", "tasks", "documents", "schedules", "meetings", "comms", "approvals", "assistant"],
}


class SystemSettingsPayload(BaseModel):
    organization_name: str = Field(default="Văn phòng số NexusOffice", max_length=200)
    language: Literal["vi", "en"] = "vi"
    theme: Literal["light", "dark"] = "light"
    ai_auto_summarize: bool = True
    require_two_step_approval: bool = False
    permissions: dict[str, list[str]] = DEFAULT_PERMISSIONS


@router.get("/")
def get_system_settings():
    doc = settings_collection.find_one({"_id": "global_config"})
    if not doc:
        return {
            "organization_name": "Văn phòng số NexusOffice",
            "language": "vi",
            "theme": "light",
            "ai_auto_summarize": True,
            "require_two_step_approval": False,
            "permissions": DEFAULT_PERMISSIONS,
        }
    doc.pop("_id", None)
    doc.setdefault("language", "vi")
    doc.setdefault("theme", "light")
    return doc


@router.put("/", dependencies=[Depends(require_roles("admin"))])
def update_system_settings(payload: SystemSettingsPayload, current_user: dict = Depends(get_current_user)):
    data = payload.model_dump()
    data["updated_by"] = current_user["id"]
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    settings_collection.update_one(
        {"_id": "global_config"},
        {"$set": data},
        upsert=True,
    )
    return {"message": "Đã lưu cấu hình hệ thống & giao diện.", "settings": data}