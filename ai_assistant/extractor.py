import os

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    import docx
except ImportError:
    docx = None

try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

OCR_AVAILABLE = Image is not None and pytesseract is not None


def extract_text_from_file(file_path: str) -> dict:
    """
    Trích xuất nội dung văn bản từ tệp tải lên.
    Trả về dict gồm: text (nội dung), page_count (số trang), warnings (cảnh báo chất lượng).
    """
    if not os.path.exists(file_path):
        return {"text": "", "page_count": 0, "warnings": ["Không tìm thấy tệp trên ổ đĩa"]}

    ext = os.path.splitext(file_path)[1].lower()
    text_parts = []
    warnings = []
    page_count = 0

    try:
        # 1. Xử lý PDF
        if ext == ".pdf":
            if PdfReader is None:
                warnings.append("Thiếu thư viện pypdf để đọc PDF.")
                return {"text": "", "page_count": 0, "warnings": warnings}
            reader = PdfReader(file_path)
            if reader.is_encrypted:
                return {"text": "", "page_count": 0, "warnings": ["PASSWORD_PROTECTED: Tệp PDF có mật khẩu"]}

            page_count = len(reader.pages)
            for idx, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(f"--- Trang {idx + 1} ---\n{page_text}")

            if not text_parts:
                warnings.append("LOW_EXTRACTION_QUALITY: PDF dạng ảnh scan chưa có lớp chữ (cần OCR).")

        # 2. Xử lý Word (.docx)
        elif ext == ".docx":
            if docx is None:
                warnings.append("Thiếu thư viện python-docx để đọc DOCX.")
                return {"text": "", "page_count": 0, "warnings": warnings}
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                if para.text.strip():
                    text_parts.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        text_parts.append(row_text)
            page_count = 1

        # 3. Xử lý tệp Text / Markdown
        elif ext in [".txt", ".md", ".csv"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_parts.append(f.read())
            page_count = 1

        # 4. Xử lý Ảnh (JPG, PNG) bằng OCR
        elif ext in [".jpg", ".jpeg", ".png"]:
            page_count = 1
            if OCR_AVAILABLE:
                img = Image.open(file_path)
                ocr_text = pytesseract.image_to_string(img, lang="vie+eng")
                if ocr_text.strip():
                    text_parts.append(ocr_text)
                else:
                    warnings.append("LOW_EXTRACTION_QUALITY: Ảnh mờ hoặc không nhận diện được chữ.")
            else:
                warnings.append("Chưa cài đặt pytesseract/Pillow để đọc chữ từ ảnh.")

        else:
            warnings.append(f"UNSUPPORTED_FORMAT: Định dạng {ext} chưa hỗ trợ trích xuất tự động.")

    except Exception as e:
        warnings.append(f"Lỗi đọc tệp: {str(e)}")

    return {
        "text": "\n".join(text_parts).strip(),
        "page_count": page_count,
        "warnings": warnings
    }