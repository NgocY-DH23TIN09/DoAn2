from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pymongo import DESCENDING, ReturnDocument

from backend.database import tasks_collection, users_collection
from backend.routers.auth import get_current_user
from backend.schemas.task import TaskCreate, TaskResponse, TaskStatusUpdate

router = APIRouter(
    prefix="/api/tasks",
    tags=["Tasks"],
    dependencies=[Depends(get_current_user)],
)


def _object_id(value: str) -> ObjectId | None:
    return ObjectId(value) if ObjectId.is_valid(value) else None


def _task_response(task: dict, assignee: dict | None) -> dict:
    return {
        "id": str(task["_id"]),
        "title": task["title"],
        "description": task.get("description"),
        "priority": task.get("priority", "medium"),
        "status": task.get("status", "todo"),
        "deadline": task.get("deadline"),
        "creator_id": task["creator_id"],
        "assignee_id": task["assignee_id"],
        "assignee_name": assignee.get("full_name") if assignee else "Chưa giao",
        "created_at": task["created_at"],
    }


@router.get("/assignees")
def get_assignees():
    """Lấy danh sách nhân viên để chọn người thực hiện công việc."""
    users = users_collection.find({}, {"full_name": 1, "role": 1, "department": 1})
    return [
        {
            "id": str(user["_id"]),
            "full_name": user["full_name"],
            "role": user.get("role", "employee"),
            "department": user.get("department") or "Chưa phân phòng",
        }
        for user in users
    ]


@router.get("/", response_model=List[TaskResponse])
def get_tasks(
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    query = {}
    if current_user.get("role") == "employee":
        query["assignee_id"] = current_user["id"]
    if status:
        query["status"] = status

    tasks = list(tasks_collection.find(query).sort("created_at", DESCENDING))
    assignee_ids = {
        ObjectId(task["assignee_id"])
        for task in tasks
        if ObjectId.is_valid(task.get("assignee_id", ""))
    }
    assignees = {
        str(user["_id"]): user
        for user in users_collection.find({"_id": {"$in": list(assignee_ids)}})
    }
    return [
        _task_response(task, assignees.get(task["assignee_id"]))
        for task in tasks
    ]


@router.post("/", response_model=TaskResponse)
def create_task(
    task_in: TaskCreate,
    current_user: dict = Depends(get_current_user),
):
    assignee_object_id = _object_id(task_in.assignee_id)
    if assignee_object_id is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên được giao việc.")
    assignee = users_collection.find_one({"_id": assignee_object_id})
    if not assignee:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân viên được giao việc.")
    if current_user.get("role") == "employee" and task_in.assignee_id != current_user["id"]:
        raise HTTPException(status_code=403, detail="Nhân viên chỉ có thể giao việc cho chính mình.")

    task = {
        "title": task_in.title,
        "description": task_in.description,
        "priority": task_in.priority,
        "deadline": task_in.deadline,
        "creator_id": current_user["id"],
        "assignee_id": task_in.assignee_id,
        "status": "todo",
        "created_at": datetime.now(timezone.utc),
    }
    result = tasks_collection.insert_one(task)
    task["_id"] = result.inserted_id
    return _task_response(task, assignee)


@router.patch("/{task_id}/status", response_model=TaskResponse)
def update_task_status(
    task_id: str,
    payload: TaskStatusUpdate,
    current_user: dict = Depends(get_current_user),
):
    task_object_id = _object_id(task_id)
    if task_object_id is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy công việc.")
    task = tasks_collection.find_one({"_id": task_object_id})
    if not task:
        raise HTTPException(status_code=404, detail="Không tìm thấy công việc.")

    if current_user.get("role") == "employee" and task["assignee_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Bạn chỉ có thể cập nhật công việc do mình nhận.")

    task = tasks_collection.find_one_and_update(
        {"_id": task_object_id},
        {"$set": {"status": payload.status}},
        return_document=ReturnDocument.AFTER,
    )
    assignee_id = _object_id(task["assignee_id"])
    assignee = users_collection.find_one({"_id": assignee_id}) if assignee_id else None
    return _task_response(task, assignee)
