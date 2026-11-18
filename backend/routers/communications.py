from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.database import emails_collection, messages_collection, users_collection
from backend.routers.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/comms",
    tags=["Internal Chat & Email"],
    dependencies=[Depends(get_current_user)],
)


class ChatMessageCreate(BaseModel):
    channel: str = Field(default="general", min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=4000)


class InternalEmailCreate(BaseModel):
    recipient_email: str = Field(min_length=3, max_length=150)
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=20000)


@router.get("/chat/{channel}")
def get_channel_messages(channel: str, limit: int = 50):
    msgs = list(messages_collection.find({"channel": channel}).sort("created_at", -1).limit(limit))
    msgs.reverse()
    return [
        {
            "id": str(m["_id"]),
            "channel": m["channel"],
            "sender_id": m["sender_id"],
            "sender_name": m["sender_name"],
            "sender_role": m.get("sender_role", ""),
            "content": m["content"],
            "created_at": m["created_at"].isoformat(),
        }
        for m in msgs
    ]


@router.post("/chat")
def send_chat_message(payload: ChatMessageCreate, current_user: dict = Depends(get_current_user)):
    msg = {
        "channel": payload.channel.strip(),
        "sender_id": current_user["id"],
        "sender_name": current_user.get("full_name", "Thành viên"),
        "sender_role": current_user.get("position") or current_user.get("role", ""),
        "content": payload.content.strip(),
        "created_at": datetime.now(timezone.utc),
    }
    res = messages_collection.insert_one(msg)
    msg["id"] = str(res.inserted_id)
    msg.pop("_id", None)
    msg["created_at"] = msg["created_at"].isoformat()
    return msg


@router.get("/emails")
def list_emails(box: str = "inbox", current_user: dict = Depends(get_current_user)):
    query = (
        {"sender_id": current_user["id"]}
        if box == "sent"
        else {"recipient_id": current_user["id"]}
    )
    items = list(emails_collection.find(query).sort("created_at", -1).limit(100))
    return [
        {
            "id": str(e["_id"]),
            "sender_name": e["sender_name"],
            "sender_email": e["sender_email"],
            "recipient_name": e["recipient_name"],
            "recipient_email": e["recipient_email"],
            "subject": e["subject"],
            "body": e["body"],
            "is_read": e.get("is_read", False),
            "created_at": e["created_at"].isoformat(),
        }
        for e in items
    ]


@router.post("/emails")
def send_internal_email(payload: InternalEmailCreate, current_user: dict = Depends(get_current_user)):
    recipient = users_collection.find_one({"email": payload.recipient_email.lower().strip()})
    if not recipient:
        raise HTTPException(status_code=404, detail="Không tìm thấy người nhận có email này trong hệ thống.")

    email_doc = {
        "sender_id": current_user["id"],
        "sender_name": current_user.get("full_name", ""),
        "sender_email": current_user["email"],
        "recipient_id": str(recipient["_id"]),
        "recipient_name": recipient.get("full_name", ""),
        "recipient_email": recipient["email"],
        "subject": payload.subject.strip(),
        "body": payload.body.strip(),
        "is_read": False,
        "created_at": datetime.now(timezone.utc),
    }
    res = emails_collection.insert_one(email_doc)
    return {"id": str(res.inserted_id), "message": "Đã gửi thư nội bộ thành công."}


@router.patch("/emails/{email_id}/read")
def mark_email_read(email_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(email_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy thư.")
    emails_collection.update_one(
        {"_id": ObjectId(email_id), "recipient_id": current_user["id"]},
        {"$set": {"is_read": True}},
    )
    return {"status": "read"}