
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

password_hash = PasswordHash.recommended()


def get_secret_key() -> str:
    secret_key = os.getenv("SECRET_KEY", "").strip()
    if len(secret_key) < 32 or "replace_with" in secret_key.lower():
        raise RuntimeError("SECRET_KEY phải được đặt thành chuỗi ngẫu nhiên dài tối thiểu 32 ký tự.")
    return secret_key


def get_algorithm() -> str:
    algorithm = os.getenv("ALGORITHM", "HS256").strip() or "HS256"
    if algorithm not in {"HS256", "HS384", "HS512"}:
        raise RuntimeError("ALGORITHM chỉ hỗ trợ HS256, HS384 hoặc HS512.")
    return algorithm


def get_access_token_expire_minutes() -> int:
    value = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120").strip()
    try:
        minutes = int(value)
    except ValueError as exc:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES phải là số nguyên dương.") from exc
    if minutes <= 0:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES phải là số nguyên dương.")
    return minutes


# Mã hóa mật khẩu
def hash_password(password: str):
    return password_hash.hash(password)


# Kiểm tra mật khẩu
def verify_password(password: str, hashed_password: str):
    try:
        return password_hash.verify(password, hashed_password)
    except (AttributeError, ValueError, TypeError):
        return False


# Tạo JWT token
def create_access_token(data: dict):
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=get_access_token_expire_minutes())
    payload.update({"exp": expire})
    return jwt.encode(payload, get_secret_key(), algorithm=get_algorithm())
