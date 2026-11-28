from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.database import approvals_collection, users_collection
from backend.routers.auth import get_current_user, require_module

router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["E-Office Approvals"],
    dependencies=[Depends(require_module("approvals"))],
)


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=2, max_length=200)
    request_type: Literal["leave", "expense", "document", "procurement"] = "document"
    content: str = Field(min_length=5, max_length=10000)
    amount: Optional[float] = Field(default=0, ge=0)
    approver_id: str


class ApprovalDecision(BaseModel):
    action: Literal["approved", "rejected"]
    comment: Optional[str] = Field(default="", max_length=1000)


@router.get("/approvers")
def list_approvers():
    return [
        {"id": str(user["_id"]), "name": user.get("full_name") or user.get("username", "")}
        for user in users_collection.find({"is_active": {"$ne": False}}, {"full_name": 1, "username": 1})
        .sort("full_name", 1).limit(200)
    ]


@router.get("/")
def list_approvals(status: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    query: dict = {}
    if current_user.get("role") == "employee":
        query["$or"] = [
            {"requester_id": current_user["id"]},
            {"approver_id": current_user["id"]},
        ]
    if status:
        query["status"] = status

    items = list(approvals_collection.find(query).sort("created_at", -1).limit(100))
    return [
        {
            "id": str(a["_id"]),
            "code": a["code"],
            "title": a["title"],
            "request_type": a["request_type"],
            "content": a["content"],
            "amount": a.get("amount", 0),
            "requester_name": a["requester_name"],
            "approver_id": a["approver_id"],
            "approver_name": a["approver_name"],
            "status": a["status"],
            "history": a.get("history", []),
            "created_at": a["created_at"].isoformat(),
        }
        for a in items
    ]


@router.post("/")
def submit_approval(payload: ApprovalCreate, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(payload.approver_id):
        raise HTTPException(status_code=404, detail="Người phê duyệt không hợp lệ.")
    approver = users_collection.find_one({"_id": ObjectId(payload.approver_id)})
    if not approver or not approver.get("is_active", True):
        raise HTTPException(status_code=404, detail="Không tìm thấy người phê duyệt.")

    now = datetime.now(timezone.utc)
    approval_id = ObjectId()
    code = f"TK-{now:%Y%m%d}-{approval_id}"
    doc = {
        "_id": approval_id,
        "code": code,
        "title": payload.title.strip(),
        "request_type": payload.request_type,
        "content": payload.content.strip(),
        "amount": payload.amount or 0,
        "requester_id": current_user["id"],
        "requester_name": current_user.get("full_name", ""),
        "approver_id": str(approver["_id"]),
        "approver_name": approver.get("full_name", ""),
        "status": "pending",
        "history": [
            {
                "actor": current_user.get("full_name", ""),
                "action": "submitted",
                "comment": "Khởi tạo phiếu trình ký",
                "at": now.isoformat(),
            }
        ],
        "created_at": now,
    }
    res = approvals_collection.insert_one(doc)
    return {"id": str(res.inserted_id), "code": code, "status": "pending"}


@router.post("/{approval_id}/decide")
def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    current_user: dict = Depends(get_current_user),
):
    if not ObjectId.is_valid(approval_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ trình ký.")
    now = datetime.now(timezone.utc)
    log_entry = {
        "actor": current_user.get("full_name", ""),
        "action": payload.action,
        "comment": payload.comment or ("Đã ký duyệt điện tử" if payload.action == "approved" else "Từ chối phê duyệt"),
        "at": now.isoformat(),
    }
    condition = {"_id": ObjectId(approval_id), "status": "pending"}
    if current_user.get("role") != "admin":
        condition["approver_id"] = current_user["id"]
    result = approvals_collection.update_one(
        condition,
        {
            "$set": {"status": payload.action, "decided_at": now},
            "$push": {"history": log_entry},
        },
    )
    if not result.modified_count:
        item = approvals_collection.find_one({"_id": condition["_id"]}, {"approver_id": 1})
        if item is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ trình ký.")
        if "approver_id" in condition and item["approver_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Bạn không phải người được chỉ định ký duyệt hồ sơ này.")
        raise HTTPException(status_code=409, detail="Hồ sơ trình ký đã được xử lý.")
    return {"id": approval_id, "status": payload.action}
