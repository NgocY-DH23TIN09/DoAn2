from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.database import approvals_collection, users_collection
from backend.routers.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/approvals",
    tags=["E-Office Approvals"],
    dependencies=[Depends(get_current_user)],
)


class ApprovalCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    request_type: Literal["leave", "expense", "document", "procurement"] = "document"
    content: str = Field(min_length=5, max_length=10000)
    amount: Optional[float] = Field(default=0, ge=0)
    approver_id: str


class ApprovalDecision(BaseModel):
    action: Literal["approved", "rejected"]
    comment: Optional[str] = Field(default="", max_length=1000)


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
    if not approver:
        raise HTTPException(status_code=404, detail="Không tìm thấy người phê duyệt.")

    now = datetime.now(timezone.utc)
    code = f"TK-{now.strftime('%Y%m%d')}-{approvals_collection.count_documents({}) + 1:03d}"
    doc = {
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
    item = approvals_collection.find_one({"_id": ObjectId(approval_id)})
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ trình ký.")

    if item["approver_id"] != current_user["id"] and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Bạn không phải người được chỉ định ký duyệt hồ sơ này.")

    now = datetime.now(timezone.utc)
    log_entry = {
        "actor": current_user.get("full_name", ""),
        "action": payload.action,
        "comment": payload.comment or ("Đã ký duyệt điện tử" if payload.action == "approved" else "Từ chối phê duyệt"),
        "at": now.isoformat(),
    }
    approvals_collection.update_one(
        {"_id": ObjectId(approval_id)},
        {
            "$set": {"status": payload.action, "decided_at": now},
            "$push": {"history": log_entry},
        },
    )
    return {"id": approval_id, "status": payload.action}