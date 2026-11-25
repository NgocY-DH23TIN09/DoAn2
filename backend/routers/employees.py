from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from backend.database import departments_collection, users_collection
from backend.routers.auth import get_current_user, require_roles
from backend.security import hash_password

router = APIRouter(
    prefix="/api/v1/employees",
    tags=["Employees & HR"],
    dependencies=[Depends(get_current_user)],
)


class EmployeeCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=150)
    phone: Optional[str] = Field(default=None, max_length=30)
    position: str = Field(default="Nhân viên", max_length=100)
    department: str = Field(default="Phòng Hành chính", max_length=100)
    role: str = Field(default="employee")
    password: str = Field(default="Welcome@123", min_length=8)


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    role: Optional[Literal["admin", "manager", "employee"]] = None
    is_active: Optional[bool] = None


def _serialize_user(u: dict) -> dict:
    return {
        "id": str(u["_id"]),
        "username": u.get("username", ""),
        "full_name": u.get("full_name", ""),
        "email": u.get("email", ""),
        "phone": u.get("phone"),
        "position": u.get("position") or "Nhân viên",
        "department": u.get("department") or "Chưa phân phòng",
        "company": u.get("company") or "Văn phòng số",
        "role": u.get("role", "employee"),
        "is_active": u.get("is_active", True),
        "created_at": u.get("created_at").isoformat() if isinstance(u.get("created_at"), datetime) else None,
    }


@router.get("/")
def list_employees(department: Optional[str] = None, search: Optional[str] = None):
    query: dict = {}
    if department:
        query["department"] = department
    if search:
        query["$or"] = [
            {"full_name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
            {"position": {"$regex": search, "$options": "i"}},
        ]
    users = list(users_collection.find(query).sort("full_name", 1))
    return [_serialize_user(u) for u in users]


@router.post("/", dependencies=[Depends(require_roles("admin", "manager"))])
def create_employee(payload: EmployeeCreate):
    if users_collection.find_one({"email": payload.email.lower().strip()}):
        raise HTTPException(status_code=400, detail="Email nhân viên đã tồn tại.")

    doc = {
        "username": f"emp_{uuid4().hex[:8]}",
        "full_name": payload.full_name.strip(),
        "email": payload.email.lower().strip(),
        "phone": payload.phone.strip() if payload.phone else None,
        "position": payload.position.strip(),
        "department": payload.department.strip(),
        "company": "Văn phòng số",
        "role": payload.role if payload.role in {"admin", "manager", "employee"} else "employee",
        "password_hash": hash_password(payload.password),
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    res = users_collection.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _serialize_user(doc)


@router.patch("/{emp_id}", dependencies=[Depends(require_roles("admin"))])
def update_employee(emp_id: str, payload: EmployeeUpdate):
    if not ObjectId.is_valid(emp_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên.")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="Không có dữ liệu cập nhật.")
    updated = users_collection.find_one_and_update(
        {"_id": ObjectId(emp_id)},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên.")
    return _serialize_user(updated)


@router.get("/departments")
def list_departments():
    depts = list(departments_collection.find().sort("name", 1))
    if not depts:
        default_depts = [
            {"name": "Ban Giám đốc", "description": "Điều hành chiến lược"},
            {"name": "Phòng Công Nghệ Thông Tin", "description": "Hạ tầng & Phát triển phần mềm"},
            {"name": "Phòng Hành chính - Nhân sự", "description": "Quản trị nhân sự & Văn thư"},
            {"name": "Phòng Kinh doanh", "description": "Phát triển thị trường & Khách hàng"},
            {"name": "Phòng Kế toán - Tài chính", "description": "Quản lý ngân sách & Chứng từ"},
        ]
        departments_collection.insert_many(default_depts)
        depts = list(departments_collection.find().sort("name", 1))
    return [{"id": str(d["_id"]), "name": d["name"], "description": d.get("description", "")} for d in depts]