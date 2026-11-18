from datetime import datetime
from typing import NotRequired, TypedDict

from bson import ObjectId


class Department(TypedDict):
    _id: ObjectId
    name: str
    description: NotRequired[str | None]


class User(TypedDict):
    _id: ObjectId
    username: str
    email: str
    full_name: str
    password_hash: str
    role: str
    phone: NotRequired[str | None]
    position: NotRequired[str | None]
    company: NotRequired[str | None]
    department: NotRequired[str | None]
    created_at: NotRequired[datetime]


class Document(TypedDict):
    _id: ObjectId
    title: str
    doc_number: str
    doc_type: str
    content: NotRequired[str | None]
    file_path: NotRequired[str | None]
    category: NotRequired[str | None]
    status: str
    ai_summary: NotRequired[str | None]
    creator_id: str
    approver_id: NotRequired[str | None]
    created_at: datetime


class Task(TypedDict):
    _id: ObjectId
    title: str
    description: NotRequired[str | None]
    priority: str
    status: str
    deadline: NotRequired[datetime | None]
    creator_id: str
    assignee_id: str
    created_at: datetime
