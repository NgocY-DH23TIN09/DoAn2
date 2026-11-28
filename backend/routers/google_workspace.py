import json
import logging
import mimetypes
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from ntpath import basename

import httplib2
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from starlette.datastructures import UploadFile as FormUploadFile

from backend import google_workspace as workspace
from backend.database import google_workspace_collection, users_collection
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/v1/google", tags=["Google Workspace"])
callback_alias_router = APIRouter(tags=["Google Workspace"])
CALLBACK_COOKIE = "nexus_google_oauth"
STATE_LIFETIME = 600
FILE_FIELDS = "id,name,mimeType,webViewLink,modifiedTime,size"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_OAUTH_JSON_SIZE = 16 * 1024
logger = logging.getLogger(__name__)


class _CallbackAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn logs the request target, including its query, as the third argument.
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            if args[2].partition("?")[0] in {
                workspace.CALLBACK_PATH,
                workspace.LEGACY_CALLBACK_PATH,
            }:
                record.args = (args[0], args[1], workspace.CALLBACK_PATH, args[3], args[4])
        return True


logging.getLogger("uvicorn.access").addFilter(_CallbackAccessLogFilter())


def _active_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa.")
    return user


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


@router.get("/status")
def status(request: Request, user: dict = Depends(_active_user)):
    setup_uri = f"{_origin(request)}{workspace.CALLBACK_PATH}"
    response = {
        "configured": False, "connected": False, "email": None,
        "can_configure": user.get("role") == "admin", "setup_redirect_uri": setup_uri,
    }
    try:
        client = workspace.load_client(_origin(request))
    except workspace.GoogleWorkspaceError as exc:
        return {**response, "message": str(exc), "redirect_uri": setup_uri}
    email = workspace.connected_email(user["id"], client)
    return {
        **response,
        "configured": True,
        "connected": email is not None,
        "email": email,
        "redirect_uri": client.redirect_uri,
    }


async def _read_oauth_json(file: FormUploadFile, redirect_uri: str) -> tuple[str, str]:
    content = await file.read(MAX_OAUTH_JSON_SIZE + 1)
    if len(content) > MAX_OAUTH_JSON_SIZE:
        raise HTTPException(status_code=413, detail="Tệp cấu hình OAuth tối đa 16 KB.")
    try:
        web = json.loads(content.decode("utf-8-sig"))["web"]
        client_id, client_secret = web["client_id"], web["client_secret"]
        redirect_uris = web.get("redirect_uris")
        if not isinstance(client_id, str) or not isinstance(client_secret, str):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Hãy chọn JSON OAuth của client loại Web application.") from exc
    if redirect_uris and (not isinstance(redirect_uris, list) or redirect_uri not in redirect_uris):
        raise HTTPException(
            status_code=422,
            detail="Redirect URI trong JSON chưa khớp địa chỉ NexusOffice hiện tại. Hãy cập nhật URI trong Google Cloud rồi nhập Client ID và Client Secret.",
        )
    return client_id, client_secret


@router.put("/config")
async def configure(request: Request, response: Response, user: dict = Depends(_active_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên được lưu cấu hình OAuth Google.")
    redirect_uri = f"{_origin(request)}{workspace.CALLBACK_PATH}"
    length = request.headers.get("content-length", "")
    if length.isdecimal() and int(length) > MAX_OAUTH_JSON_SIZE * 2:
        raise HTTPException(status_code=413, detail="Dữ liệu cấu hình OAuth quá lớn.")

    async with request.form(max_files=1, max_fields=3, max_part_size=MAX_OAUTH_JSON_SIZE) as form:
        file = form.get("oauth_json")
        if isinstance(file, FormUploadFile) and file.filename:
            client_id, client_secret = await _read_oauth_json(file, redirect_uri)
        else:
            client_id, client_secret = form.get("client_id"), form.get("client_secret")
            if not isinstance(client_id, str) or not isinstance(client_secret, str):
                raise HTTPException(status_code=422, detail="Chọn tệp JSON OAuth hoặc nhập Client ID và Client Secret.")

    try:
        client = workspace.validate_client_config(client_id, client_secret, redirect_uri)
    except workspace.GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await run_in_threadpool(workspace.save_client_config, client, user["id"])
    response.headers["Cache-Control"] = "no-store"
    return {"configured": True, "redirect_uri": redirect_uri}


@router.post("/connect")
def connect(request: Request, response: Response, user: dict = Depends(_active_user)):
    origin = _origin(request)
    try:
        client = workspace.load_client(origin)
    except workspace.GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if client.redirect_uri != f"{origin}{workspace.CALLBACK_PATH}":
        expected_origin = client.redirect_uri.removesuffix(workspace.CALLBACK_PATH)
        raise HTTPException(status_code=409, detail=f"Hãy mở NexusOffice tại {expected_origin} để kết nối Google.")

    state = secrets.token_urlsafe(32)
    browser_nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    google_workspace_collection.update_one({"_id": user["id"]}, {"$set": {"pending": {
        "state_hash": workspace.secret_hash(state),
        "browser_hash": workspace.secret_hash(browser_nonce),
        "verifier": verifier,
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=STATE_LIFETIME),
    }}}, upsert=True)
    response.set_cookie(
        CALLBACK_COOKIE, browser_nonce, max_age=STATE_LIFETIME, path=workspace.CALLBACK_PATH,
        httponly=True, samesite="lax", secure=origin.startswith("https://"),
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authorization_url": workspace.authorization_url(client, state, verifier)}


def _callback_result(result: str, *, clear_cookie: bool = True) -> RedirectResponse:
    response = RedirectResponse(f"/google-workspace?google={result}", status_code=303)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    if clear_cookie:
        response.delete_cookie(CALLBACK_COOKIE, path=workspace.CALLBACK_PATH, httponly=True, samesite="lax")
    return response


@router.get("/callback", include_in_schema=False)
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    nonce = request.cookies.get(CALLBACK_COOKIE)
    if not nonce or not state:
        return _callback_result("expired")
    state_hash = workspace.secret_hash(state)
    record = google_workspace_collection.find_one_and_update({
        "pending.state_hash": state_hash,
        "pending.browser_hash": workspace.secret_hash(nonce),
        "pending.expires_at": {"$gt": datetime.now(timezone.utc)},
        "pending.claimed": {"$exists": False},
    }, {"$set": {"pending.claimed": True}})
    if not record:
        # An unrelated callback should not clear a valid flow in the same browser.
        return _callback_result("expired", clear_cookie=False)

    user_id = record["_id"]
    pending = record["pending"]
    try:
        if error:
            return _callback_result("cancelled" if error == "access_denied" else "error")
        if not code:
            return _callback_result("cancelled")
        user = users_collection.find_one({"_id": ObjectId(user_id)}, {"is_active": 1})
        if user is None or not user.get("is_active", True):
            return _callback_result("expired")
        client = workspace.load_client(_origin(request))
        if (client.client_id, client.redirect_uri) != (pending["client_id"], pending["redirect_uri"]):
            return _callback_result("expired")
        credentials, email = workspace.exchange_code(client, code, pending["verifier"])
        saved = workspace.save_connection(user_id, state_hash, client, credentials, email)
        return _callback_result("connected" if saved else "expired")
    except workspace.GoogleWorkspaceError as exc:
        logger.warning("Google Workspace callback failed with status %s.", exc.status_code)
        return _callback_result("permissions" if exc.status_code == 422 else "error")
    finally:
        google_workspace_collection.update_one({"_id": user_id, "pending.state_hash": state_hash}, {"$unset": {"pending": ""}})


@callback_alias_router.get("/auth/google/callback", include_in_schema=False)
def callback_alias(request: Request, state: str = "", code: str = "", error: str = ""):
    return callback(request, state, code, error)


@router.delete("/connection")
def disconnect(user: dict = Depends(_active_user)):
    google_workspace_collection.delete_one({"_id": user["id"]})
    return {"connected": False}


@contextmanager
def _google_error_response():
    try:
        yield
    except workspace.GoogleWorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except HttpError as exc:
        logger.warning("Google Workspace API returned HTTP %s.", exc.status_code)
        if exc.status_code == 401:
            raise HTTPException(status_code=409, detail="Quyền Google đã hết hạn. Hãy kết nối lại.") from exc
        if exc.status_code == 403:
            raise HTTPException(status_code=403, detail="Google từ chối yêu cầu. Kiểm tra API đã được bật và tài khoản đã cấp quyền.") from exc
        raise HTTPException(status_code=502, detail="Google chưa xử lý được yêu cầu. Vui lòng thử lại.") from exc
    except (GoogleAuthError, httplib2.HttpLib2Error, OSError) as exc:
        logger.warning("Google Workspace request failed (%s).", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Không thể kết nối dịch vụ Google.") from exc


@router.get("/calendar/events")
def calendar_events(user: dict = Depends(_active_user)):
    with _google_error_response():
        calendar = workspace.get_service(user["id"], "calendar", "v3")
        result = calendar.events().list(
            calendarId="primary", maxResults=20,
            timeMin=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            singleEvents=True, orderBy="startTime",
            fields="items(id,summary,start,end,htmlLink,hangoutLink)",
        ).execute()
    return {"items": result.get("items", [])}


@router.get("/drive/files")
def drive_files(page_token: str | None = Query(default=None, max_length=2048), user: dict = Depends(_active_user)):
    with _google_error_response():
        drive = workspace.get_service(user["id"], "drive", "v3")
        result = drive.files().list(
            q="trashed = false", spaces="drive", pageSize=25, pageToken=page_token,
            orderBy="modifiedTime desc", fields=f"nextPageToken,files({FILE_FIELDS})",
        ).execute()
    return {"items": result.get("files", []), "next_page_token": result.get("nextPageToken")}


@router.post("/drive/files", status_code=201)
def upload_drive_file(file: UploadFile = File(...), user: dict = Depends(_active_user)):
    name = basename(file.filename or "").strip()
    size = file.file.seek(0, 2)
    if not name or size == 0:
        raise HTTPException(status_code=422, detail="Vui lòng chọn một tệp có nội dung.")
    if size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Kích thước tệp tối đa là 10 MB.")
    file.file.seek(0)
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with _google_error_response():
        drive = workspace.get_service(user["id"], "drive", "v3")
        media = MediaIoBaseUpload(file.file, mimetype=mime_type, resumable=True)
        return drive.files().create(body={"name": name}, media_body=media, fields=FILE_FIELDS).execute()
