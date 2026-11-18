
from pydantic import BaseModel, Field, field_validator
import re


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    phone: str = Field(min_length=8, max_length=30)
    email: str = Field(max_length=150)
    position: str = Field(min_length=2, max_length=150)
    company: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value):
        if not isinstance(value, str):
            raise ValueError("Email khong hop le")
        value = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Email khong hop le")
        return value.lower()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        normalized = re.sub(r"[\s().-]", "", value)
        if not re.fullmatch(r"\+?\d{8,15}", normalized):
            raise ValueError("So dien thoai khong hop le")
        return normalized

    @field_validator("full_name", "position", "company", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str

    @field_validator("username")
    @classmethod
    def normalize_login_identifier(cls, value):
        value = value.strip()
        if "@" not in value:
            return re.sub(r"[\s().-]", "", value)
        return value.lower()


class UserResponse(BaseModel):
    id: str
    full_name: str
    email: str
    username: str
    phone: str | None = None
    position: str | None = None
    company: str | None = None
    role: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
