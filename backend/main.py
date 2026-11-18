from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# Gắn thư mục chứa file tĩnh (css, js, ảnh) - nếu có
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Khai báo thư mục chứa các file HTML giao diện của bạn
templates = Jinja2Templates(directory="frontend/templates")

# === BỔ SUNG ROUTE NÀY ĐỂ HIỂN THỊ GIAO DIỆN TRANG CHỦ ===
@app.get("/")
async def read_root(request: Request):
    # Thay "login.html" bằng tên file giao diện chính của bạn (ví dụ: index.html, home.html...)
    return templates.TemplateResponse("login.html", {"request": request})