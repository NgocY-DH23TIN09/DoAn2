import logging
import os
import re

from ai_assistant.ocr import recognize_image, recognize_pdf_pages

logger = logging.getLogger(__name__)
PAGE_NUMBER = re.compile(r"(?:trang|page)?\s*\d+(?:\s*(?:/|of|trên)\s*\d+)?", re.IGNORECASE)

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    import docx
except ImportError:
    docx = None

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
    ocr_pages = 0
    text_pages = 0

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
            pages = {}
            candidates = []
            for index in range(page_count):
                try:
                    page = reader.pages[index]
                    text = (page.extract_text() or "").strip()
                    if text:
                        pages[index] = text
                    if not text or PAGE_NUMBER.fullmatch(text) or _looks_like_scanned_page(page, text):
                        candidates.append(index)
                except Exception as exc:
                    logger.warning("PDF text extraction failed on page %s (%s).", index + 1, type(exc).__name__)
                    warnings.append(f"Không đọc được lớp chữ ở trang {index + 1}.")
                    candidates.append(index)
            text_pages = len(pages)
            if candidates:
                try:
                    ocr_text, notices = recognize_pdf_pages(file_path, candidates)
                except Exception as exc:
                    logger.warning("PDF OCR could not start (%s).", type(exc).__name__)
                    warnings.append("Không chạy được OCR cho tệp PDF.")
                else:
                    for index in candidates:
                        recognized = ocr_text.get(index, "").strip()
                        if len(recognized) > len(pages.get(index, "")):
                            if index in pages:
                                text_pages -= 1
                            pages[index] = recognized
                            ocr_pages += 1
                    warnings.extend(notices)
            text_parts = [f"--- Trang {index + 1} ---\n{pages[index]}" for index in sorted(pages)]

        # 2. Xử lý Word (.docx)
        elif ext == ".docx":
            if docx is None:
                warnings.append("Thiếu thư viện python-docx để đọc DOCX.")
                return {"text": "", "page_count": 0, "warnings": warnings}
            doc = docx.Document(file_path)
            for block in doc.iter_inner_content():
                if isinstance(block, docx.table.Table):
                    for row in block.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        if any(cells):
                            text_parts.append(" | ".join(cells))
                elif block.text.strip():
                    text_parts.append(block.text)
            page_count = 1

        # 3. Xử lý tệp Text / Markdown
        elif ext in [".txt", ".md", ".csv"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text_parts.append(f.read())
            page_count = 1

        # 4. Xử lý Ảnh (JPG, PNG) bằng OCR
        elif ext in [".jpg", ".jpeg", ".png"]:
            page_count = 1
            ocr_text, notices = recognize_image(file_path)
            warnings.extend(notices)
            if ocr_text:
                text_parts.append(ocr_text)
                ocr_pages = 1

        else:
            warnings.append(f"UNSUPPORTED_FORMAT: Định dạng {ext} chưa hỗ trợ trích xuất tự động.")

    except Exception as exc:
        logger.warning("Document extraction failed (%s).", type(exc).__name__)
        warnings.append("Lỗi đọc nội dung tệp.")

    return {
        "text": "\n".join(text_parts).strip(),
        "page_count": page_count,
        "ocr_pages": ocr_pages,
        "method": ("mixed" if text_pages else "ocr") if ocr_pages else "native" if text_parts else None,
        "warnings": warnings,
    }


def _looks_like_scanned_page(page, text: str) -> bool:
    if len(text.split()) > 30:
        return False
    resources = page.get("/Resources") if hasattr(page, "get") else None
    objects = resources.get_object().get("/XObject") if resources else None
    if not objects:
        return False
    return any(
        obj.get_object().get("/Subtype") == "/Image"
        and obj.get_object().get("/Width", 0) >= 400
        and obj.get_object().get("/Height", 0) >= 400
        for obj in objects.get_object().values()
    )
