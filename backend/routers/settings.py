from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.database import settings_collection
from backend.permissions import DEFAULT_PERMISSIONS, MODULES, system_config
from backend.routers.auth import get_current_user, require_roles

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["Settings & Permissions"],
    dependencies=[Depends(get_current_user)],
)

class SystemSettingsPayload(BaseModel):
    organization_name: str = Field(default="Văn phòng số NexusOffice", max_length=200)
    language: Literal["vi", "en"] = "vi"
    theme: Literal["light", "dark"] = "light"
    ai_auto_summarize: bool = True
    require_two_step_approval: bool = False
    permissions: dict[str, list[str]] | None = None


@router.get("/", dependencies=[Depends(require_roles("admin"))])
def get_system_settings():
    config = system_config()
    return {
        **SystemSettingsPayload().model_dump(),
        **config,
        "permissions": {**DEFAULT_PERMISSIONS, **(config.get("permissions") or {}), "admin": list(MODULES)},
        "require_two_step_approval": False,
        "two_step_approval_supported": False,
    }


@router.put("/", dependencies=[Depends(require_roles("admin"))])
def update_system_settings(payload: SystemSettingsPayload, current_user: dict = Depends(get_current_user)):
    if payload.require_two_step_approval:
        raise HTTPException(status_code=422, detail="Quy trình trình ký hai cấp chưa được hỗ trợ.")
    data = payload.model_dump(exclude={"permissions"})
    if payload.permissions is not None:
        configured = dict(get_system_settings()["permissions"])
        for role, modules in payload.permissions.items():
            if role == "admin" and set(modules) == set(MODULES):
                continue
            if role not in {"manager", "employee"} or not set(modules) <= set(MODULES) - {"settings"}:
                raise HTTPException(status_code=422, detail="Vai trò hoặc phân hệ trong bảng quyền không hợp lệ.")
            configured[role] = list(dict.fromkeys(modules))
        data["permissions"] = configured
    data["updated_by"] = current_user["id"]
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    settings_collection.update_one(
        {"_id": "global_config"},
        {"$set": data},
        upsert=True,
    )
    return {"message": "Đã lưu cấu hình hệ thống & giao diện.", "settings": data}
