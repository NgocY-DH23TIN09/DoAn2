# Văn phòng số

Ứng dụng quản lý văn bản, công việc và biên bản họp; tích hợp tóm tắt và hỏi đáp tài liệu bằng Gemini.

## Yêu cầu

- Python 3.10 trở lên
- MongoDB bắt buộc để lưu tài khoản, văn bản, công việc và biên bản họp
- Gemini API key tùy chọn; khi chưa cấu hình key, ứng dụng dùng tóm tắt dự phòng
- Tesseract OCR tùy chọn nếu cần OCR ảnh scan (cài riêng chương trình Tesseract; các gói Python `Pillow` và `pytesseract` được cài cùng dependencies)

## Cài đặt và chạy trên Windows

```powershell
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Mở `.env`, đặt `SECRET_KEY` ngẫu nhiên dài tối thiểu 32 ký tự và cấu hình `MONGODB_URI`/`MONGODB_DATABASE`. Gemini API key là tùy chọn. Không đưa `.env`, thông tin đăng nhập MongoDB, Gemini key hoặc `credentials.json` lên Git.

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Mở `http://127.0.0.1:8000`. Giao diện đăng nhập ở `/login`; các API quản lý yêu cầu Bearer token. Trong môi trường phát triển mới, tài khoản mẫu là `admin / Admin@123`. Với `APP_ENV=production`, đặt `ADMIN_INITIAL_PASSWORD` dài ít nhất 12 ký tự để tạo admin; nếu database hiện có vẫn dùng mật khẩu mẫu, ứng dụng sẽ từ chối khởi động.

## Chức năng chính

- Đăng ký, đăng nhập JWT và phân quyền duyệt văn bản.
- Tạo văn bản, tải tệp PDF/DOCX/TXT/MD/CSV/JPG/PNG (tối đa 10 MB), trích xuất nội dung và tải tệp qua API đã xác thực.
- Giao việc, lọc trạng thái và cập nhật tiến độ.
- Tạo biên bản họp và trích xuất đầu việc.
- Kết nối Google Calendar để tạo sự kiện và sinh link Google Meet.
- Tóm tắt, phân loại và hỏi đáp tài liệu bằng trợ lý AI.
- MongoDB lưu tài khoản, văn bản, công việc và biên bản họp. API trả ObjectId dưới dạng chuỗi trong trường `id`.

## Kiểm tra cấu hình và kiểm thử

- `GET /api/health`: kiểm tra kết nối database.
- `GET /api/v1/assistant/integrations`: trạng thái MongoDB và Gemini.
- Chạy test: `.\venv\Scripts\python.exe -m pytest -q`

## Google Calendar / Google Meet

Đặt OAuth client file loại Desktop (`credentials.json`) ở thư mục dự án, bật Google Calendar API cho project Google Cloud và cấu hình OAuth consent screen. Mỗi người dùng đăng nhập vào ứng dụng rồi mở `/meetings`, chọn **Kết nối Google Calendar** và cấp quyền cho tài khoản Google cá nhân; cuộc họp sẽ được tạo trên lịch chính của tài khoản đó. Token được lưu riêng theo từng tài khoản ứng dụng trong `google_tokens/` (đã được ignore bởi Git). Thời gian cuộc họp dùng múi giờ `Asia/Ho_Chi_Minh`. Nếu màn hình đồng ý OAuth đang ở chế độ kiểm thử, thêm từng tài khoản Google vào danh sách test users. Không đưa `credentials.json` hoặc token trong `google_tokens/` lên Git hay gửi cho người khác.

## Cấu trúc mã nguồn

- `main.py`: khởi tạo ứng dụng FastAPI và các trang HTML.
- `backend/`: database, model, xác thực, seed và API router.
- `ai_assistant/`: trích xuất nội dung, tóm tắt và lưu lịch sử AI.
- `frontend/`: giao diện HTML/CSS/JavaScript.
- `tests/`: kiểm thử API và trợ lý AI.
