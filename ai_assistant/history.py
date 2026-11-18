import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

_client: MongoClient | None = None
_client_uri: str | None = None


class MongoHistoryError(Exception):
    """Raised when AI history cannot be stored in MongoDB."""


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _get_mongo_config() -> tuple[str, str]:
    uri = _env_first("MONGODB_URI", "MONGODB_URL", default="")
    if not uri:
        raise MongoHistoryError("Chưa cấu hình MONGODB_URI trong file .env.")

    database_name = _env_first("MONGODB_DATABASE", "DATABASE_NAME", default="vanphongso")
    if not database_name:
        raise MongoHistoryError("MONGODB_DATABASE không được để trống.")
    return uri, database_name


def _get_collection() -> Collection:
    global _client, _client_uri

    uri, database_name = _get_mongo_config()

    try:
        if _client is None or _client_uri != uri:
            if _client is not None:
                _client.close()
            _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            _client_uri = uri

        _client.admin.command("ping")
        return _client[database_name]["ai_interactions"]
    except MongoHistoryError:
        raise
    except PyMongoError as exc:
        raise MongoHistoryError("Không thể kết nối MongoDB. Hãy kiểm tra URI và quyền truy cập.") from exc
    except ValueError as exc:
        raise MongoHistoryError("Cấu hình MongoDB không hợp lệ.") from exc


def record_ai_interaction(
    *,
    action: str,
    document_id: str | int | None,
    input_text: str,
    output_text: str,
) -> None:
    interaction: dict[str, Any] = {
        "action": action,
        "document_id": document_id,
        "input_text": input_text,
        "output_text": output_text,
        "created_at": datetime.now(timezone.utc),
    }

    try:
        _get_collection().insert_one(interaction)
    except MongoHistoryError:
        raise
    except PyMongoError as exc:
        raise MongoHistoryError("Không thể lưu lịch sử AI vào MongoDB.") from exc


def get_integration_status() -> dict[str, str | bool]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_configured = bool(api_key and not api_key.lower().startswith("your_"))
    try:
        _get_collection()
    except MongoHistoryError as exc:
        return {
            "mongodb": "unavailable",
            "mongodb_message": str(exc),
            "gemini_configured": gemini_configured,
        }

    return {
        "mongodb": "connected",
        "gemini_configured": gemini_configured,
    }
