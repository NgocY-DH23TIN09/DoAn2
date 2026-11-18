from datetime import datetime, timezone
from uuid import uuid4

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.errors import DuplicateKeyError

from backend.database import users_collection
from backend.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from backend.security import (
    create_access_token,
    get_algorithm,
    get_secret_key,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
bearer_scheme = HTTPBearer(auto_error=False)


def _user_response(user: dict) -> dict:
    return {
        "id": str(user.get("_id", user.get("id"))),
        "full_name": user["full_name"],
        "email": user["email"],
        "username": user["username"],
        "phone": user.get("phone"),
        "position": user.get("position"),
        "company": user.get("company"),
        "role": user.get("role", "employee"),
    }


@router.post("/register", response_model=UserResponse)
def register(data: RegisterRequest):
    existing_user = users_collection.find_one({
        "$or": [{"email": data.email}, {"phone": data.phone}],
    })
    if existing_user:
        raise HTTPException(status_code=400, detail="Email hoặc số điện thoại đã được sử dụng.")

    new_user = {
        "full_name": data.full_name,
        "email": data.email,
        "username": f"user_{uuid4().hex}",
        "phone": data.phone,
        "position": data.position,
        "company": data.company,
        "password_hash": hash_password(data.password),
        "role": "employee",
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = users_collection.insert_one(new_user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=400, detail="Tài khoản hoặc email đã được sử dụng") from exc

    new_user["_id"] = result.inserted_id
    return _user_response(new_user)


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest):
    user = users_collection.find_one({
        "$or": [
            {"username": data.username},
            {"email": data.username},
            {"phone": data.username},
        ],
    })
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không đúng")

    token = create_access_token({"sub": str(user["_id"]), "role": user.get("role", "employee")})
    return {"access_token": token, "token_type": "bearer"}


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập")

    try:
        payload = jwt.decode(
            credentials.credentials,
            get_secret_key(),
            algorithms=[get_algorithm()],
        )
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not ObjectId.is_valid(user_id):
            raise HTTPException(status_code=401, detail="Token không hợp lệ")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc đã hết hạn") from exc

    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if user is None:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại")

    user["id"] = str(user.pop("_id"))
    return user


def require_roles(*allowed_roles: str):
    def role_checker(current_user: dict = Depends(get_current_user)):
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="Bạn không có quyền thực hiện thao tác này.",
            )
        return current_user

    return role_checker


@router.get("/me", response_model=UserResponse)
def get_my_profile(current_user: dict = Depends(get_current_user)):
    return _user_response(current_user)
