import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ai_assistant.summarizer import SummarizationError, summarize_text
from backend.mongo_db import MongoMeetingError, get_meetings_collection
from backend.routers.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/meetings",
    tags=["Smart Meeting Room"],
    dependencies=[Depends(get_current_user)],
)
LOCAL_MEETINGS: list[dict] = []
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]
GOOGLE_CLIENT_SECRETS = BASE_DIR / "credentials.json"
GOOGLE_TOKEN_DIR = BASE_DIR / "google_tokens"
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
MEETING_TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")
_oauth_lock = threading.Lock()
_oauth_statuses: dict[str, str] = {}


class GoogleMeetError(Exception):
    """Raised when Google Calendar or Meet cannot be used."""


class MeetingCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    participants: list[str] = Field(default_factory=list, max_length=100)
    meeting_date: date
    raw_notes: str = Field(min_length=1, max_length=50000)


class GoogleMeetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)


def _google_token_file(user_id: str) -> Path:
    return GOOGLE_TOKEN_DIR / f"{user_id}.json"


def _read_google_credentials(user_id: str):
    token_file = _google_token_file(user_id)
    if not token_file.is_file():
        return None
    try:
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_file(
            str(token_file),
            GOOGLE_CALENDAR_SCOPES,
        )
    except Exception as exc:
        raise GoogleMeetError("Không đọc được token Google Calendar đã lưu.") from exc


def _get_google_calendar_service(user_id: str):
    credentials = _read_google_credentials(user_id)
    if credentials is None:
        raise GoogleMeetError("Bạn chưa kết nối tài khoản Google Calendar.")
    if credentials.expired and credentials.refresh_token:
        try:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            token_file = _google_token_file(user_id)
            temporary_token = token_file.with_suffix(".tmp")
            temporary_token.write_text(credentials.to_json(), encoding="utf-8")
            temporary_token.replace(token_file)
        except Exception as exc:
            raise GoogleMeetError("Không làm mới được quyền truy cập Google Calendar.") from exc
    if not credentials.valid:
        raise GoogleMeetError("Quyền Google Calendar đã hết hạn. Hãy kết nối lại tài khoản.")

    try:
        from googleapiclient.discovery import build

        return build("calendar", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise GoogleMeetError("Không khởi tạo được Google Calendar API.") from exc


def _run_google_oauth(user_id: str):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(
            str(GOOGLE_CLIENT_SECRETS),
            GOOGLE_CALENDAR_SCOPES,
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
        )
        GOOGLE_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        token_file = _google_token_file(user_id)
        temporary_token = token_file.with_suffix(".tmp")
        temporary_token.write_text(credentials.to_json(), encoding="utf-8")
        temporary_token.replace(token_file)
        with _oauth_lock:
            _oauth_statuses[user_id] = "connected"
    except Exception:
        logger.exception("Google Calendar authorization failed.")
        with _oauth_lock:
            _oauth_statuses[user_id] = "error"


def _start_google_oauth(user_id: str) -> None:
    thread = threading.Thread(
        target=_run_google_oauth,
        args=(user_id,),
        daemon=True,
    )
    thread.start()


def _google_connection_status(user_id: str) -> dict[str, str | bool]:
    try:
        credentials = _read_google_credentials(user_id)
    except GoogleMeetError:
        return {
            "status": "error",
            "credentials_configured": GOOGLE_CLIENT_SECRETS.is_file(),
        }
    if credentials is not None and (credentials.valid or credentials.refresh_token):
        with _oauth_lock:
            _oauth_statuses[user_id] = "connected"
        return {
            "status": "connected",
            "credentials_configured": GOOGLE_CLIENT_SECRETS.is_file(),
        }

    with _oauth_lock:
        current_status = _oauth_statuses.get(user_id, "disconnected")
    return {
        "status": current_status,
        "credentials_configured": GOOGLE_CLIENT_SECRETS.is_file(),
    }


def _get_meetings_col():
    try:
        return get_meetings_collection()
    except MongoMeetingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/generate-minutes")
def create_and_summarize_meeting(
    req: MeetingCreateRequest,
):
    if not req.title.strip() or not req.raw_notes.strip():
        raise HTTPException(status_code=422, detail="Vui lòng nhập chủ đề và nội dung cuộc họp.")

    try:
        summary = summarize_text(req.raw_notes)
    except SummarizationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    action_keywords = [
        "cần", "phải", "giao", "hoàn thành", "trước ngày",
        "phụ trách", "triển khai", "kiểm tra",
    ]
    lines = [line.strip("-• ") for line in req.raw_notes.splitlines() if line.strip()]
    action_items = [
        line for line in lines
        if any(keyword in line.lower() for keyword in action_keywords)
    ] or ["Rà soát lại nội dung đã thống nhất trong cuộc họp."]

    clean_title = "".join(
        char if char.isalnum() or char in (" ", "_") else "" for char in req.title
    ).strip().replace(" ", "_")
    meeting_record = {
        "title": req.title.strip(),
        "standard_filename": (
            f"BienBan_{clean_title[:50] or 'cuoc_hop'}_{req.meeting_date.isoformat()}.docx"
        ),
        "meeting_date": req.meeting_date.isoformat(),
        "participants": [
            participant.strip()
            for participant in req.participants
            if participant.strip()
        ],
        "raw_notes": req.raw_notes.strip(),
        "ai_summary": summary,
        "action_items": action_items,
        "created_at": datetime.now(timezone.utc),
    }

    collection = _get_meetings_col()
    if collection is not None:
        try:
            result = collection.insert_one(meeting_record)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Không thể lưu biên bản cuộc họp.",
            ) from exc
        meeting_record["id"] = str(result.inserted_id)
        meeting_record.pop("_id", None)
    else:
        meeting_record["id"] = str(len(LOCAL_MEETINGS) + 1)
        LOCAL_MEETINGS.insert(0, meeting_record)
        del LOCAL_MEETINGS[1000:]
        meeting_record["storage_warning"] = (
            "MongoDB chưa được cấu hình; biên bản chỉ được lưu tạm trong tiến trình hiện tại."
        )

    meeting_record["created_at"] = meeting_record["created_at"].isoformat()
    return meeting_record


@router.get("/")
def list_meetings(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    collection = _get_meetings_col()
    if collection is not None:
        try:
            cursor = collection.find().sort("created_at", -1).limit(limit)
            items = []
            for doc in cursor:
                doc["id"] = str(doc.pop("_id"))
                if isinstance(doc.get("created_at"), datetime):
                    doc["created_at"] = doc["created_at"].isoformat()
                items.append(doc)
            return {"items": items}
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Không thể tải lịch sử cuộc họp.",
            ) from exc
    return {
        "items": LOCAL_MEETINGS[:limit],
        "storage_warning": "Lịch sử chỉ được lưu tạm trong tiến trình hiện tại.",
    }


@router.get("/google/status")
def google_calendar_status(current_user: dict = Depends(get_current_user)):
    return _google_connection_status(current_user["id"])


@router.post("/google/connect")
def connect_google_calendar(current_user: dict = Depends(get_current_user)):
    if not GOOGLE_CLIENT_SECRETS.is_file():
        raise HTTPException(
            status_code=503,
            detail="Không tìm thấy credentials.json trong thư mục dự án.",
        )
    user_id = current_user["id"]
    connection_status = _google_connection_status(user_id)["status"]
    with _oauth_lock:
        if _oauth_statuses.get(user_id) == "connecting":
            return {"status": "connecting"}
        if connection_status == "connected":
            return {"status": "connected"}
        _oauth_statuses[user_id] = "connecting"
    _start_google_oauth(user_id)
    return {
        "status": "connecting",
        "message": "Trình duyệt sẽ mở trang Google để cấp quyền Calendar.",
    }


def _meeting_link(event: dict) -> str | None:
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    for entry_point in event.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video" and entry_point.get("uri"):
            return entry_point["uri"]
    return None


@router.post("/google/create")
def create_google_meet(
    req: GoogleMeetCreateRequest,
    current_user: dict = Depends(get_current_user),
):
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Vui lòng nhập chủ đề cuộc họp.")

    start_at = req.start_at
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=MEETING_TIMEZONE)
    else:
        start_at = start_at.astimezone(MEETING_TIMEZONE)
    if start_at <= datetime.now(MEETING_TIMEZONE):
        raise HTTPException(status_code=422, detail="Thời gian họp phải ở tương lai.")

    end_at = start_at + timedelta(minutes=req.duration_minutes)
    event_body = {
        "summary": title,
        "start": {
            "dateTime": start_at.isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        },
        "end": {
            "dateTime": end_at.isoformat(),
            "timeZone": "Asia/Ho_Chi_Minh",
        },
        "conferenceData": {
            "createRequest": {
                "requestId": uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            },
        },
    }

    try:
        service = _get_google_calendar_service(current_user["id"])
    except GoogleMeetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        event = service.events().insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
        ).execute()
        meet_url = _meeting_link(event)
        event_id = event.get("id")
        if not meet_url and event_id:
            for _ in range(5):
                time.sleep(1)
                event = service.events().get(
                    calendarId="primary",
                    eventId=event_id,
                ).execute()
                meet_url = _meeting_link(event)
                if meet_url:
                    break
    except Exception as exc:
        logger.exception("Google Calendar event creation failed.")
        raise HTTPException(
            status_code=502,
            detail="Google Calendar không tạo được sự kiện. Hãy kiểm tra kết nối và quyền Calendar.",
        ) from exc

    return {
        "status": "created" if meet_url else "conference_pending",
        "title": title,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "meet_url": meet_url,
        "calendar_url": event.get("htmlLink"),
        "event_id": event_id,
        "message": (
            "Đã tạo sự kiện và link Google Meet."
            if meet_url
            else "Sự kiện đã tạo; link Meet đang được Google chuẩn bị. Hãy mở sự kiện trên Google Calendar."
        ),
    }
