from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.database import schedules_collection
from backend.routers.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/schedules",
    tags=["Work Schedule"],
    dependencies=[Depends(get_current_user)],
)


class ScheduleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    start_time: datetime
    end_time: datetime
    location: Optional[str] = Field(default="Văn phòng", max_length=200)
    scope: Literal["personal", "department", "company"] = "personal"
    department: Optional[str] = None


def _serialize_schedule(item: dict) -> dict:
    return {
        "id": str(item["_id"]),
        "title": item["title"],
        "description": item.get("description", ""),
        "start_time": item["start_time"].isoformat() if isinstance(item["start_time"], datetime) else item["start_time"],
        "end_time": item["end_time"].isoformat() if isinstance(item["end_time"], datetime) else item["end_time"],
        "location": item.get("location", ""),
        "scope": item.get("scope", "personal"),
        "department": item.get("department"),
        "owner_id": item["owner_id"],
        "owner_name": item.get("owner_name", ""),
    }


@router.get("/")
def list_schedules(scope: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if scope:
        # When filtering by scope, still respect visibility rules
        if scope == "personal":
            visibility_filter = {"scope": "personal", "owner_id": current_user["id"]}
        elif scope == "department":
            visibility_filter = {"scope": "department", "department": current_user.get("department")}
        else:
            visibility_filter = {"scope": scope}
    else:
        visibility_filter = {
            "$or": [
                {"scope": "company"},
                {"scope": "department", "department": current_user.get("department")},
                {"owner_id": current_user["id"]},
            ]
        }

    items = list(schedules_collection.find(visibility_filter).sort("start_time", 1).limit(200))
    return [_serialize_schedule(i) for i in items]


@router.post("/")
def create_schedule(payload: ScheduleCreate, current_user: dict = Depends(get_current_user)):
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=422, detail="Thời gian kết thúc phải sau thời gian bắt đầu.")

    doc = {
        "title": payload.title.strip(),
        "description": (payload.description or "").strip(),
        "start_time": payload.start_time,
        "end_time": payload.end_time,
        "location": (payload.location or "Văn phòng").strip(),
        "scope": payload.scope,
        "department": payload.department or current_user.get("department"),
        "owner_id": current_user["id"],
        "owner_name": current_user.get("full_name", "Người dùng"),
        "created_at": datetime.now(timezone.utc),
    }
    res = schedules_collection.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize_schedule(doc)


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(schedule_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch làm việc.")
    item = schedules_collection.find_one({"_id": ObjectId(schedule_id)})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch làm việc.")
    if item["owner_id"] != current_user["id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa lịch này.")
    schedules_collection.delete_one({"_id": ObjectId(schedule_id)})
    return {"message": "Đã xóa lịch làm việc."}