import logging
import mimetypes
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote

import httplib2
from bson import ObjectId
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ai_assistant.document_analysis import group_key
from backend import google_workspace as workspace
from backend.database import settings_collection, users_collection

LIBRARY_ID = "document_library_storage"
LIBRARY_NAME = "NexusOffice - Thư viện tài liệu"
FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
OWNER_FIELDS = ("owner_user_id", "owner_email", "client_id")
DRIVE_BATCH_SIZE = 100
logger = logging.getLogger(__name__)


@contextmanager
def _drive_errors(*, download: bool = False):
    try:
        yield
    except HttpError as exc:
        if exc.status_code == 404:
            message = "Tệp đã bị xóa hoặc mất quyền truy cập trên Google Drive." if download else "Không tìm thấy thư mục lưu trữ trên Google Drive."
            raise workspace.GoogleWorkspaceError(message, 404 if download else 409) from exc
        if exc.status_code in {401, 403}:
            raise workspace.GoogleWorkspaceError("Google Drive từ chối thao tác. Hãy kiểm tra quyền của tài khoản lưu trữ.", 409) from exc
        logger.warning("Document storage Google Drive returned HTTP %s.", exc.status_code)
        raise workspace.GoogleWorkspaceError("Google Drive chưa xử lý được tệp. Vui lòng thử lại.", 502) from exc
    except (GoogleAuthError, httplib2.HttpLib2Error, OSError) as exc:
        logger.warning("Document storage connection failed (%s).", type(exc).__name__)
        raise workspace.GoogleWorkspaceError("Không thể kết nối Google Drive.", 502) from exc


def _bound_account_matches(binding: dict, client: workspace.OAuthClient) -> bool:
    user = users_collection.find_one({"_id": ObjectId(binding["owner_user_id"])}, {"is_active": 1})
    return (
        user is not None and user.get("is_active", True)
        and binding["client_id"] == client.client_id
        and workspace.connected_email(binding["owner_user_id"], client) == binding["owner_email"]
    )


def library_status(user: dict) -> dict:
    binding = settings_collection.find_one({"_id": LIBRARY_ID})
    admin = user.get("role") == "admin" and user.get("is_active", True)
    try:
        client = workspace.load_client()
    except workspace.GoogleWorkspaceError:
        client = None
    if binding:
        linked = client is not None and _bound_account_matches(binding, client)
        ready = linked and binding["ready"]
        counts = None
        inventory_error = None
        if ready:
            try:
                counts = library_item_counts(binding)
            except workspace.GoogleWorkspaceError as exc:
                inventory_error = str(exc)
        return {
            "ready": ready,
            "can_activate": admin and linked and not ready and binding["owner_user_id"] == user["id"],
            "show_google_link": admin and not linked,
            "folder_url": f"https://drive.google.com/drive/folders/{quote(binding['folder_id'], safe='')}" if admin and ready else None,
            "drive_file_count": counts["files"] if counts else None,
            "drive_folder_count": counts["folders"] if counts else None,
            "inventory_error": inventory_error,
            "message": (
                f"Tệp mới lưu tại {LIBRARY_NAME}." if ready else
                "Hãy kết nối lại tài khoản Google đang sở hữu kho tài liệu." if not linked else
                "Thư mục Google Drive đang chờ khởi tạo."
            ),
        }
    linked = admin and client is not None and bool(workspace.connected_email(user["id"], client))
    return {
        "ready": False, "can_activate": linked, "show_google_link": admin and not linked, "folder_url": None,
        "message": "Chọn Drive làm kho lưu trữ để nhận tệp mới." if linked else "Thư viện đang chờ quản trị viên kết nối kho Google Drive.",
    }


def library_item_counts(binding: dict) -> dict[str, int]:
    try:
        with _drive_errors():
            files = _bound_files(binding)
            pending_folders = [binding["folder_id"]]
            seen_items = set()
            seen_folders = {binding["folder_id"]}
            file_count = 0
            folder_count = 0

            while pending_folders:
                parent_id = pending_folders.pop()
                page_token = None
                while True:
                    params = {
                        "q": f"'{parent_id}' in parents and trashed = false",
                        "pageSize": 1000,
                        "fields": "nextPageToken,files(id,mimeType)",
                        "supportsAllDrives": True,
                        "includeItemsFromAllDrives": True,
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    result = files.list(**params).execute()
                    for item in result.get("files", []):
                        item_id = item.get("id")
                        if not item_id or item_id in seen_items:
                            continue
                        seen_items.add(item_id)
                        if item.get("mimeType") == FOLDER_MIME:
                            folder_count += 1
                            if item_id not in seen_folders:
                                seen_folders.add(item_id)
                                pending_folders.append(item_id)
                        else:
                            file_count += 1
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break
            return {"files": file_count, "folders": folder_count}
    except workspace.GoogleWorkspaceError:
        raise


def _bound_drive(binding: dict):
    user = users_collection.find_one({"_id": ObjectId(binding["owner_user_id"])}, {"is_active": 1})
    if user is None or not user.get("is_active", True):
        raise workspace.GoogleWorkspaceError("Tài khoản sở hữu kho tài liệu đang bị khóa hoặc đã bị xóa.", 409)
    return workspace.get_service(
        binding["owner_user_id"], "drive", "v3",
        expected_client_id=binding["client_id"], expected_email=binding["owner_email"],
    )


def _bound_files(binding: dict):
    return _bound_drive(binding).files()


def current_attachment_names(attachments: dict[str, dict]) -> tuple[dict[str, str], set[str]]:
    groups = {}
    names = {}
    missing = set()
    for document_id, attachment in attachments.items():
        owner = tuple(attachment.get(field) for field in OWNER_FIELDS)
        if all(owner) and attachment.get("id"):
            groups.setdefault(owner, []).append((document_id, attachment))

    def record_name(document_id, remote, error):
        if error is not None:
            if isinstance(error, HttpError) and error.status_code == 404:
                missing.add(document_id)
            return
        if remote.get("trashed"):
            missing.add(document_id)
        elif remote.get("name"):
            names[document_id] = remote["name"]

    for entries in groups.values():
        try:
            with _drive_errors():
                drive = _bound_drive(entries[0][1])
                files = drive.files()
                for offset in range(0, len(entries), DRIVE_BATCH_SIZE):
                    batch = drive.new_batch_http_request(callback=record_name)
                    for document_id, attachment in entries[offset:offset + DRIVE_BATCH_SIZE]:
                        request = files.get(fileId=attachment["id"], fields="name,trashed", supportsAllDrives=True)
                        batch.add(request, request_id=document_id)
                    batch.execute()
        except workspace.GoogleWorkspaceError as exc:
            logger.warning("Could not check current Drive names for a library account (status %s).", exc.status_code)
    return names, missing


def trash_library_attachment(attachment: dict) -> str:
    owner = {field: attachment.get(field) for field in OWNER_FIELDS}
    folder_id = attachment.get("folder_id")
    file_id = attachment.get("id")
    if not all(owner.values()) or not folder_id or not file_id:
        raise workspace.GoogleWorkspaceError("Không đủ thông tin để xóa an toàn tệp Drive.", 409)

    library = settings_collection.find_one({"_id": LIBRARY_ID, "ready": True})
    if library is None or any(library.get(field) != owner[field] for field in OWNER_FIELDS):
        raise workspace.GoogleWorkspaceError("Không tìm thấy thư viện Drive gắn với tài liệu này.", 409)
    if folder_id == library["folder_id"]:
        raise workspace.GoogleWorkspaceError("Không thể xóa tệp nằm trực tiếp trong thư mục gốc của thư viện.", 409)

    try:
        with _drive_errors():
            files = _bound_files(owner)
            folder = files.get(
                fileId=folder_id,
                fields="id,mimeType,trashed,parents",
                supportsAllDrives=True,
            ).execute()
            if folder.get("trashed"):
                return "already_in_trash"
            if folder.get("mimeType") != FOLDER_MIME or library["folder_id"] not in folder.get("parents", []):
                raise workspace.GoogleWorkspaceError("Thư mục tài liệu không còn nằm trong thư viện Drive.", 409)
            remote = files.get(
                fileId=file_id,
                fields="id,mimeType,trashed,parents",
                supportsAllDrives=True,
            ).execute()
            if remote.get("trashed"):
                return "already_in_trash"
            if remote.get("mimeType") == FOLDER_MIME or folder_id not in remote.get("parents", []):
                raise workspace.GoogleWorkspaceError("Tệp không còn nằm trong thư mục Drive đã lưu cho tài liệu.", 409)
            files.update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,trashed",
                supportsAllDrives=True,
            ).execute()
            return "moved_to_trash"
    except workspace.GoogleWorkspaceError as exc:
        cause = exc.__cause__
        if isinstance(cause, HttpError) and cause.status_code == 404:
            return "already_missing"
        raise


def _allocate_id(files) -> str:
    return files.generateIds(count=1, space="drive", type="files").execute()["ids"][0]


def _check_folder(files, folder_id: str) -> None:
    folder = files.get(fileId=folder_id, fields="id,mimeType,trashed").execute()
    if folder.get("trashed") or folder.get("mimeType") != FOLDER_MIME:
        raise workspace.GoogleWorkspaceError("Thư mục lưu trữ trên Drive không còn khả dụng.", 409)


def _ensure_folder(files, key: str, name: str, owner: dict, parent_id: str | None = None) -> dict:
    record = settings_collection.find_one({"_id": key})
    if record is None:
        candidate = {
            **owner, "folder_id": _allocate_id(files), "folder_name": name,
            "parent_id": parent_id, "ready": False,
        }
        try:
            record = settings_collection.find_one_and_update(
                {"_id": key}, {"$setOnInsert": candidate}, upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            record = settings_collection.find_one({"_id": key})
            if record is None:
                raise
    if any(record[field] != value for field, value in owner.items()):
        raise workspace.GoogleWorkspaceError("Kho tài liệu đã được gắn với một tài khoản Google khác.", 409)
    folder_id = record["folder_id"]
    if record["ready"]:
        _check_folder(files, folder_id)
        return record

    body = {"id": folder_id, "name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    try:
        files.create(body=body, fields="id").execute()
    except HttpError as exc:
        if exc.status_code != 409:
            raise
        # A retry uses the same pre-allocated ID if the folder was already created.
        _check_folder(files, folder_id)
    settings_collection.update_one({"_id": key, "folder_id": folder_id}, {"$set": {"ready": True}})
    return {**record, "ready": True}


def activate_library(user: dict) -> dict:
    with _drive_errors():
        client = workspace.load_client()
        email = workspace.connected_email(user["id"], client)
        if not email:
            raise workspace.GoogleWorkspaceError("Hãy liên kết Google Drive trước khi tạo kho tài liệu.", 409)
        owner = {"owner_user_id": user["id"], "owner_email": email, "client_id": client.client_id}
        _ensure_folder(_bound_files(owner), LIBRARY_ID, LIBRARY_NAME, owner)
    return library_status(user)


@dataclass
class UploadedAttachment:
    metadata: dict
    files: object

    def discard(self) -> None:
        try:
            self.files.update(fileId=self.metadata["id"], body={"trashed": True}).execute()
        except (HttpError, GoogleAuthError, httplib2.HttpLib2Error, OSError) as exc:
            if not isinstance(exc, HttpError) or exc.status_code != 404:
                logger.warning("Could not trash an unfinished document upload (%s).", type(exc).__name__)


def require_library() -> dict:
    binding = settings_collection.find_one({"_id": LIBRARY_ID, "ready": True})
    if binding is None:
        raise workspace.GoogleWorkspaceError("Quản trị viên cần kết nối kho Google Drive trước khi tải tệp.", 409)
    return binding


def library_groups(binding: dict) -> tuple[str, ...]:
    groups = settings_collection.find(
        {"parent_id": binding["folder_id"], "ready": True, "folder_name": {"$type": "string"}},
        {"folder_name": 1},
    ).sort("folder_name", 1).limit(40)
    return tuple(group["folder_name"] for group in groups)


def _category_folder(files, binding: dict, category: str, owner: dict) -> dict:
    _check_folder(files, binding["folder_id"])
    return _ensure_folder(
        files, f"{LIBRARY_ID}:{binding['folder_id']}:group:{group_key(category)}", category, owner, binding["folder_id"],
    )


def upload_attachment(binding: dict, document_id: str, category: str, filename: str, content: bytes) -> UploadedAttachment:
    owner = {field: binding[field] for field in OWNER_FIELDS}
    with _drive_errors():
        files = _bound_files(owner)
        parent = _category_folder(files, binding, category, owner)
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_id = _allocate_id(files)
        attachment = UploadedAttachment({
            **owner, "id": file_id, "name": filename, "mime_type": media_type, "folder_id": parent["folder_id"],
            "category": parent["folder_name"],
        }, files)
        try:
            files.create(body={
                "id": file_id, "name": filename, "parents": [parent["folder_id"]],
                "appProperties": {"nexusofficeDocumentId": document_id},
            }, media_body=MediaInMemoryUpload(content, mimetype=media_type, resumable=True), fields="id").execute()
        except (HttpError, GoogleAuthError, httplib2.HttpLib2Error, OSError):
            attachment.discard()
            raise
    return attachment


def reclassify_attachment(binding: dict, attachment: dict, category: str, filename: str) -> dict:
    owner = {field: binding[field] for field in OWNER_FIELDS}
    if any(attachment[field] != value for field, value in owner.items()):
        raise workspace.GoogleWorkspaceError("Tệp thuộc kho Drive khác với kho đang dùng.", 409)
    with _drive_errors():
        files = _bound_files(owner)
        remote = files.get(fileId=attachment["id"], fields="name,parents,trashed").execute()
        if remote.get("trashed"):
            raise workspace.GoogleWorkspaceError("Tệp đang nằm trong thùng rác Google Drive.", 404)
        parent = _category_folder(files, binding, category, owner)
        target = parent["folder_id"]
        parents = remote.get("parents", [])
        if parents not in ([attachment["folder_id"]], [target]):
            raise workspace.GoogleWorkspaceError("Tệp đã được chuyển sang thư mục khác trên Drive. Cần kiểm tra vị trí tệp.", 409)
        updates = {"addParents": target, "removeParents": parents[0]} if parents != [target] else {}
        if updates or remote.get("name") != filename:
            files.update(fileId=attachment["id"], body={"name": filename}, fields="id", **updates).execute()
    return {**attachment, "name": filename, "category": parent["folder_name"], "folder_id": target}


def download_attachment(attachment: dict) -> bytes:
    return download_named_attachment(attachment)[1]


def download_named_attachment(attachment: dict) -> tuple[str, bytes]:
    with _drive_errors(download=True):
        files = _bound_files(attachment)
        metadata = files.get(fileId=attachment["id"], fields="name,size,trashed").execute()
        if metadata.get("trashed"):
            raise workspace.GoogleWorkspaceError("Tệp đã được chuyển vào thùng rác Google Drive.", 404)
        if int(metadata.get("size", 0)) > MAX_ATTACHMENT_BYTES:
            raise workspace.GoogleWorkspaceError("Tệp Drive vượt quá giới hạn tải xuống 10 MB.", 413)
        buffer = BytesIO()
        request = files.get_media(fileId=attachment["id"])
        downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
        complete = False
        while not complete:
            _, complete = downloader.next_chunk(num_retries=1)
            if buffer.tell() > MAX_ATTACHMENT_BYTES:
                raise workspace.GoogleWorkspaceError("Tệp Drive vượt quá giới hạn tải xuống 10 MB.", 413)
        return metadata.get("name") or attachment["name"], buffer.getvalue()
