from fastapi import APIRouter, Depends

from backend.database import (
    approvals_collection,
    documents_collection,
    meetings_collection,
    schedules_collection,
    tasks_collection,
    users_collection,
)
from backend.routers.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["Reports & Analytics"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/overview")
def get_analytics_overview():
    total_users = users_collection.count_documents({})
    total_docs = documents_collection.count_documents({})
    pending_docs = documents_collection.count_documents({"status": "pending"})
    approved_docs = documents_collection.count_documents({"status": "approved"})

    tasks_todo = tasks_collection.count_documents({"status": "todo"})
    tasks_in_progress = tasks_collection.count_documents({"status": "in_progress"})
    tasks_done = tasks_collection.count_documents({"status": "done"})
    total_tasks = tasks_todo + tasks_in_progress + tasks_done

    pending_approvals = approvals_collection.count_documents({"status": "pending"})
    approved_approvals = approvals_collection.count_documents({"status": "approved"})
    total_meetings = meetings_collection.count_documents({})
    total_schedules = schedules_collection.count_documents({})

    dept_pipeline = [
        {"$group": {"_id": {"$ifNull": ["$department", "Chưa phân phòng"]}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    by_department = [
        {"department": row["_id"], "count": row["count"]}
        for row in users_collection.aggregate(dept_pipeline)
    ]

    completion_rate = round((tasks_done / total_tasks) * 100, 1) if total_tasks > 0 else 0.0

    return {
        "kpis": {
            "total_employees": total_users,
            "total_documents": total_docs,
            "pending_documents": pending_docs,
            "approved_documents": approved_docs,
            "total_tasks": total_tasks,
            "tasks_done": tasks_done,
            "task_completion_rate": completion_rate,
            "pending_approvals": pending_approvals,
            "approved_approvals": approved_approvals,
            "total_meetings": total_meetings,
            "total_schedules": total_schedules,
        },
        "task_breakdown": {
            "todo": tasks_todo,
            "in_progress": tasks_in_progress,
            "done": tasks_done,
        },
        "employees_by_department": by_department,
    }