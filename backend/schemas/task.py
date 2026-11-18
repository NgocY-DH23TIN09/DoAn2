from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: Literal["low", "medium", "high"] = "medium"
    deadline: Optional[datetime] = None
    assignee_id: str


class TaskStatusUpdate(BaseModel):
    status: Literal["todo", "in_progress", "done"]


class TaskResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    deadline: Optional[datetime] = None
    creator_id: str
    assignee_id: str
    assignee_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
