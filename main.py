import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo.errors import PyMongoError

from backend.database import MONGODB_DATABASE, db, ensure_database_schema
from backend.routers import (
    approvals,
    assistant,
    auth,
    communications,
    documents,
    employees,
    google_workspace,
    meetings,
    oauth,
    reports,
    schedules,
    settings,
    tasks,
)
from backend.security import (
    get_access_token_expire_minutes,
    get_algorithm,
    get_secret_key,
)
from backend.seed import seed_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_in_threadpool(ensure_database_schema)
    await run_in_threadpool(seed_data)
    yield


app = FastAPI(
    title="Văn phòng số tích hợp trợ lý AI",
    description="Hệ thống quản lý văn bản, workflow và công việc hành chính nội bộ",
    version="1.0.0",
    lifespan=lifespan,
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
get_secret_key()
get_algorithm()
get_access_token_expire_minutes()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "frontend" / "static")), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))

# Đăng ký toàn bộ router của hệ thống
app.include_router(assistant.router)
app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(google_workspace.router)
app.include_router(google_workspace.callback_alias_router)
app.include_router(documents.router)
app.include_router(meetings.router)
app.include_router(tasks.router)
app.include_router(schedules.router)
app.include_router(communications.router)
app.include_router(approvals.router)
app.include_router(employees.router)
app.include_router(reports.router)
app.include_router(settings.router)


@app.get("/frontend")
@app.get("/frontend/")
@app.get("/frontend/index.html")
@app.get("/index.html")
def legacy_demo_redirect():
    return RedirectResponse(url="/demo")


@app.get("/demo")
def demo_page():
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/")
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})


@app.get("/dashboard")
def dashboard_page(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})


@app.get("/google-workspace")
def google_workspace_page(request: Request):
    return templates.TemplateResponse(request=request, name="google_workspace.html", context={})


@app.get("/tasks")
def tasks_page(request: Request):
    return templates.TemplateResponse(request=request, name="tasks.html", context={})


@app.get("/documents")
def documents_page(request: Request):
    return templates.TemplateResponse(request=request, name="documents.html", context={})


@app.get("/meetings")
def meetings_page(request: Request):
    return templates.TemplateResponse(request=request, name="meetings.html", context={})


@app.get("/api/health")
def health_check():
    try:
        db.command("ping")
    except PyMongoError:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": MONGODB_DATABASE},
        )
    return {"status": "healthy", "database": MONGODB_DATABASE}


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    logger.debug("Handled 404 for %s (%s)", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=404, content={"detail": "Không tìm thấy tài nguyên."})


if __name__ == "__main__":
    import uvicorn, webbrowser, threading

    def open_browser():
        webbrowser.open("http://127.0.0.1:8000")

    threading.Timer(1.5, open_browser).start()
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
