import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "vanphongso_db").strip()

if not MONGODB_URI:
    raise ValueError("Chưa cấu hình MONGODB_URI trong tệp .env")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000, tz_aware=True)
db: Database = client[MONGODB_DATABASE]

# Các collection gốc
users_collection: Collection = db["users"]
documents_collection: Collection = db["documents"]
tasks_collection: Collection = db["tasks"]
meetings_collection: Collection = db["meetings"]

# Các collection bổ sung cho 6 phân hệ mới
schedules_collection: Collection = db["schedules"]
messages_collection: Collection = db["internal_messages"]
emails_collection: Collection = db["internal_emails"]
approvals_collection: Collection = db["approval_workflows"]
departments_collection: Collection = db["departments"]
settings_collection: Collection = db["system_settings"]
google_workspace_collection: Collection = db["google_workspace_connections"]
oauth_login_states: Collection = db["oauth_login_states"]


def get_db() -> Database:
    return db


def ensure_database_schema() -> None:
    db.command("ping")
    users_collection.create_index("username", unique=True)
    users_collection.create_index("email", unique=True)
    users_collection.create_index(
        "phone",
        unique=True,
        partialFilterExpression={"phone": {"$type": "string"}},
    )
    documents_collection.create_index("doc_number", unique=True)
    schedules_collection.create_index([("start_time", 1), ("owner_id", 1)])
    emails_collection.create_index([("recipient_id", 1), ("created_at", -1)])
    messages_collection.create_index([("channel", 1), ("created_at", -1)])
    approvals_collection.create_index([("status", 1), ("created_at", -1)])
    approvals_collection.create_index("code", unique=True, partialFilterExpression={"code": {"$type": "string"}})
    google_workspace_collection.create_index("pending.state_hash", unique=True, sparse=True)
    oauth_login_states.create_index("expires_at", expireAfterSeconds=0)
    users_collection.create_index(
        [("oauth_provider", 1), ("oauth_subject", 1)],
        unique=True,
        partialFilterExpression={"oauth_subject": {"$type": "string"}},
    )
