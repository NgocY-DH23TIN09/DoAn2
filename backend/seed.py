import logging
import os

from pymongo.errors import DuplicateKeyError

from backend.database import users_collection
from backend.security import hash_password, verify_password

logger = logging.getLogger(__name__)


def seed_data() -> None:
    admin_user = users_collection.find_one({"username": "admin"})
    production = os.getenv("APP_ENV", "development").strip().lower() == "production"
    initial_password = os.getenv("ADMIN_INITIAL_PASSWORD", "").strip()
    if production and admin_user and verify_password("Admin@123", admin_user["password_hash"]):
        raise RuntimeError("Tài khoản admin vẫn dùng mật khẩu mặc định; hãy đổi trước khi chạy production.")

    if admin_user:
        return
    if production and (
        len(initial_password) < 12
        or "replace_with" in initial_password.lower()
    ):
        raise RuntimeError(
            "Production cần ADMIN_INITIAL_PASSWORD dài ít nhất 12 ký tự để tạo admin ban đầu."
        )

    admin_user = {
        "username": "admin",
        "email": "admin@vanphongso.vn",
        "full_name": "Quản Trị Viên",
        "password_hash": hash_password(initial_password or "Admin@123"),
        "role": "admin",
        "phone": "0900000000",
        "position": "Quản trị viên",
        "company": "Văn phòng số",
        "department": "Phòng Công Nghệ Thông Tin",
    }
    try:
        users_collection.insert_one(admin_user)
    except DuplicateKeyError:
        if users_collection.find_one({"username": "admin"}) is None:
            raise
    logger.info("Initial seed data created.")


if __name__ == "__main__":
    seed_data()
