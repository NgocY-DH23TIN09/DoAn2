# Văn phòng số

Ứng dụng quản lý văn bản, công việc và biên bản họp; tích hợp tóm tắt và hỏi đáp tài liệu bằng Gemini.

## Yêu cầu

- Python 3.10 trở lên
- MongoDB bắt buộc để lưu tài khoản, văn bản, công việc và biên bản họp
- Gemini API key tùy chọn; khi chưa cấu hình key, ứng dụng dùng tóm tắt dự phòng
- Để đọc PDF scan và ảnh bằng OCR tiếng Việt, đặt `vie.traineddata` và `eng.traineddata` từ [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) vào cùng một thư mục rồi khai báo đường dẫn đó qua `OCR_TESSDATA_DIR` trong `.env`. Thư viện `tesserocr` dùng các mô hình này để nhận dạng trên máy.

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
- Tự động nhận dạng loại văn bản, đặt tên theo mẫu `Loai_TieuDe_NgayTai.ext` và lưu tệp mới vào thư mục nhóm trong Google Drive.
- Giao việc, lọc trạng thái và cập nhật tiến độ.
- Tạo biên bản họp và trích xuất đầu việc.
- Liên kết tài khoản Google để xem lịch, tạo sự kiện Google Meet và tải tệp lên Drive.
- Tóm tắt, phân loại và hỏi đáp tài liệu bằng trợ lý AI.
- MongoDB lưu tài khoản, văn bản, công việc và biên bản họp. API trả ObjectId dưới dạng chuỗi trong trường `id`.

## Kiểm tra cấu hình và kiểm thử

- `GET /api/health`: kiểm tra kết nối database.
- `GET /api/v1/assistant/integrations`: trạng thái MongoDB và Gemini.
- Chạy test với MongoDB cục bộ tại `127.0.0.1:27028`: `.\venv\Scripts\python.exe -m pytest -q`. Nếu dùng máy chủ kiểm thử khác, đặt `TEST_MONGODB_URI`. Mỗi lần chạy tạo một database ngẫu nhiên có tiền tố `nexusoffice_test_` và dọn database đó khi kết thúc.
- Index và dữ liệu khởi tạo được thiết lập khi ứng dụng chạy lifespan, sau khi kết nối MongoDB.
- Lịch làm việc nhập theo giờ Việt Nam. API lưu và trả UTC có offset. Phiếu trình ký hiện hỗ trợ một cấp duyệt.
- Trang [demo](/demo) dùng dữ liệu và chatbot mô phỏng. Giao diện làm việc chính mở tại `/dashboard` sau khi đăng nhập.

## Kết nối Google Lịch và Google Drive

1. Trong [Google Cloud Console](https://console.cloud.google.com/), tạo hoặc chọn project rồi bật [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com) và [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com).
2. Trong **Google Auth Platform**, cấu hình **Branding**, **Audience** và **Data Access**. Nếu chỉ dùng tài khoản của người tạo một project mới, Google có [Personal mode](https://support.google.com/cloud/answer/15544987#personal-mode). Nếu dùng **External / Testing**, thêm địa chỉ Google muốn kết nối vào **Test users**. Ứng dụng yêu cầu `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/calendar.events.owned` và `https://www.googleapis.com/auth/drive.file`.
3. Trong [Clients](https://console.cloud.google.com/auth/clients), tạo OAuth client loại **Web application**. Thêm **Authorized redirect URI** bằng địa chỉ ứng dụng cộng với `/auth/google/callback`. Ví dụ với cổng `8000`, URI là `http://127.0.0.1:8000/auth/google/callback`. Dùng đúng máy chủ và cổng đã đăng ký khi mở NexusOffice.
4. Đăng nhập bằng tài khoản quản trị viên, mở **Google Lịch & Drive** và chọn **Cấu hình Google Calendar API và Google Drive API**. Sao chép **Authorized redirect URI** từ giao diện vào Google Cloud, sau đó tải JSON OAuth Web lên hoặc nhập **Client ID** và **Client Secret**. Chọn **Lưu cấu hình Google**. NexusOffice mã hóa Client Secret trước khi ghi vào MongoDB.
5. Đăng nhập NexusOffice, mở **Google Lịch & Drive**, chọn **Kết nối Google**, chọn tài khoản muốn dùng và cấp đủ các quyền. Trang kết nối hiển thị email đang dùng và các sự kiện sắp tới trên lịch chính. Tạo sự kiện có link Meet từ **Phòng họp**. Ở mục Drive, có thể tải tệp tối đa 10 MB và mở các tệp mà Google cấp cho NexusOffice qua `drive.file`. Nút **Mở toàn bộ Drive trên Google** dẫn đến giao diện Drive.

NexusOffice mã hóa token của từng tài khoản ứng dụng trước khi ghi vào MongoDB. Khi thay `SECRET_KEY`, người dùng cần liên kết lại. **Ngắt liên kết** xóa quyền truy cập đã lưu trong NexusOffice. Có thể thu hồi quyền đã cấp trong [trang quản lý kết nối của tài khoản Google](https://myaccount.google.com/connections). Với OAuth External ở chế độ Testing, Google có thể yêu cầu kết nối lại sau bảy ngày. Giữ JSON OAuth và `.env` riêng tư.

Khi chưa có cấu hình lưu từ giao diện, máy chủ vẫn đọc `google_workspace_credentials.json` trong thư mục dự án hoặc tệp được chỉ định trong `GOOGLE_WORKSPACE_CLIENT_FILE`. Với cấu hình bằng tệp, có thể chỉ định URI qua `GOOGLE_WORKSPACE_REDIRECT_URI`. Khi thay `SECRET_KEY`, nhập lại Client Secret đã lưu từ giao diện. Khi đổi sang OAuth client khác, người dùng cần liên kết lại tài khoản Google.

## Kho thư viện tài liệu trên Google Drive

Sau khi liên kết Google, tài khoản quản trị viên mở **Thư viện tài liệu** và chọn **Dùng Drive làm kho tài liệu**. NexusOffice tạo thư mục `NexusOffice - Thư viện tài liệu` trên Drive của tài khoản đã chọn. Tài liệu mới từ thư viện hoặc mục Trình ký sẽ dùng kho này. Người dùng đọc và tải tệp theo quyền của thư viện NexusOffice. Nếu quyền Drive hết hạn, quản trị viên kết nối lại đúng tài khoản sở hữu kho.

Khi tải tệp, hệ thống đọc nội dung và dùng Gemini nếu có `GEMINI_API_KEY`. PDF hoặc ảnh chưa trích xuất được chữ có thể được gửi trực tiếp cho Gemini. Gemini đặt tên nhóm theo chủ đề và ưu tiên nhóm đã dùng khi phù hợp. Khi Gemini chưa sẵn sàng, hệ thống nhận diện các nhóm đã có, tiêu đề chủ đề, một số từ khóa lĩnh vực và loại văn bản từ nội dung trích xuất. Tài liệu thiếu căn cứ được gom vào nhóm `Cần phân loại`.

NexusOffice tự tạo thư mục trên Google Drive cho nhóm mới và dùng chung thư mục khi tên nhóm tương đương sau khi bỏ khác biệt về dấu, hoa thường và khoảng trắng. Danh mục bên trái được tạo từ các nhóm trong thư viện. Tệp được lưu theo tên `Loai_TieuDe_NgayTai.ext`. Nội dung trích xuất, kết quả phân loại và chỉ mục tệp Drive lưu trong MongoDB. Tệp đính kèm trước đây trong `uploads/` vẫn tải được qua thư viện.

Trang thư viện chỉ hiển thị tài liệu có tệp trên Google Drive. Danh sách kiểm tra ID tệp trực tiếp trên Drive khi tải trang hoặc chọn **Quét lại tệp từ Drive**. Khung kho hiển thị số tệp và thư mục thực tế trong thư mục thư viện Drive, tính cả các thư mục con và bỏ qua mục trong thùng rác. Tệp đã bị xóa hoặc chuyển vào thùng rác sẽ xóa bản ghi ứng dụng; tài liệu chưa lưu trên Drive được ẩn khỏi thư viện nhưng vẫn giữ nguyên trong cơ sở dữ liệu. Lỗi kết nối tạm thời không làm xóa tài liệu. Quản trị viên có thể xóa một tệp từ phần chi tiết; thao tác chuyển riêng tệp đó vào Thùng rác Drive, giữ thư mục và các tệp khác.

NexusOffice dùng OCR cho các trang PDF thiếu lớp chữ, tối đa 20 trang ảnh mỗi lượt. Tóm tắt dự phòng trích các nội dung chính từ văn bản nhận dạng được. Với tệp Drive đang chờ duyệt, quản trị viên hoặc quản lý có thể chọn **Đọc lại tệp trên Drive** để cập nhật tóm tắt, loại và nhóm. Tệp được đổi tên và chuyển nhóm theo cùng ID trên Drive, giữ nguyên dữ liệu PDF.

### Luồng Google Calendar Desktop đã có

Đặt OAuth client file loại Desktop (`credentials.json`) ở thư mục dự án, bật Google Calendar API cho project Google Cloud và cấu hình OAuth consent screen. Mỗi người dùng đăng nhập vào ứng dụng rồi mở `/meetings`, chọn **Kết nối Google Calendar**; ứng dụng mở cửa sổ Google riêng để đăng nhập và cấp quyền Calendar. Cho phép cửa sổ bật lên nếu trình duyệt chặn popup. Sau khi cấp quyền, đóng cửa sổ xác nhận và quay lại ứng dụng; nút tạo sự kiện và link Meet sẽ được bật. Token được lưu riêng theo từng tài khoản ứng dụng trong `google_tokens/` (đã được ignore bởi Git). Thời gian cuộc họp dùng múi giờ `Asia/Ho_Chi_Minh`. Nếu màn hình đồng ý OAuth đang ở chế độ kiểm thử, thêm từng tài khoản Google vào danh sách test users. Không đưa `credentials.json` hoặc token trong `google_tokens/` lên Git hay gửi cho người khác.

## Cấu trúc mã nguồn

- `main.py`: khởi tạo ứng dụng FastAPI và các trang HTML.
- `backend/`: database, model, xác thực, seed và API router.
- `ai_assistant/`: trích xuất nội dung, tóm tắt và lưu lịch sử AI.
- `frontend/`: giao diện HTML/CSS/JavaScript.
- `tests/`: kiểm thử API và trợ lý AI.
