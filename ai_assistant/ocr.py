import logging
import math
import os
import shutil
from contextlib import contextmanager
from threading import Lock

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None

try:
    import tesserocr
except ImportError:
    tesserocr = None

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

PDFIUM_LOCK = Lock()
MAX_OCR_PAGES = 20
MAX_RENDER_PIXELS = 12_000_000
OCR_LANGUAGES = "vie+eng"
logger = logging.getLogger(__name__)


@contextmanager
def _recognizer():
    data_dir = os.getenv("OCR_TESSDATA_DIR", "").strip()
    engine = None
    if tesserocr is not None and data_dir:
        try:
            engine = tesserocr.PyTessBaseAPI(path=data_dir, lang=OCR_LANGUAGES, psm=tesserocr.PSM.AUTO)
        except RuntimeError as exc:
            logger.warning("Could not initialize Tesseract OCR (%s).", type(exc).__name__)
    if engine is not None:
        with engine:
            def recognize(image):
                engine.SetImage(image)
                return engine.GetUTF8Text().strip()
            yield recognize
    elif pytesseract is not None and shutil.which(pytesseract.pytesseract.tesseract_cmd):
        yield lambda image: pytesseract.image_to_string(image, lang=OCR_LANGUAGES, timeout=45).strip()
    else:
        yield None


def _page_image(document, index: int):
    # Serialize PDFium operations while separate OCR engines run independently.
    with PDFIUM_LOCK:
        page = document[index]
        try:
            width, height = page.get_size()
            scale = min(300 / 72, math.sqrt(MAX_RENDER_PIXELS / (width * height)))
            bitmap = page.render(scale=scale, grayscale=True)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()


def recognize_pdf_pages(file_path: str, indices: list[int]) -> tuple[dict[int, str], list[str]]:
    if pdfium is None or Image is None:
        return {}, ["Thiếu thư viện dựng ảnh trang PDF để chạy OCR."]
    with _recognizer() as recognize:
        if recognize is None:
            return {}, ["Chưa có cấu hình OCR tiếng Việt cho PDF scan."]
        recognized = {}
        warnings = []
        try:
            with PDFIUM_LOCK:
                document = pdfium.PdfDocument(file_path)
        except pdfium.PdfiumError:
            return {}, ["Không dựng được ảnh trang PDF để chạy OCR."]
        try:
            for index in indices[:MAX_OCR_PAGES]:
                try:
                    with _page_image(document, index) as image:
                        text = recognize(image)
                    if text:
                        recognized[index] = text
                    else:
                        warnings.append(f"OCR chưa nhận diện được chữ ở trang {index + 1}.")
                except Exception as exc:
                    logger.warning("OCR failed for PDF page %s (%s).", index + 1, type(exc).__name__)
                    warnings.append(f"Không nhận dạng được chữ ở trang {index + 1}.")
            if len(indices) > MAX_OCR_PAGES:
                warnings.append(f"OCR xử lý tối đa {MAX_OCR_PAGES} trang ảnh mỗi lượt.")
        finally:
            with PDFIUM_LOCK:
                document.close()
    return recognized, warnings


def recognize_image(file_path: str) -> tuple[str, list[str]]:
    if Image is None:
        return "", ["Thiếu thư viện xử lý ảnh để chạy OCR."]
    with _recognizer() as recognize:
        if recognize is None:
            return "", ["Chưa có cấu hình OCR tiếng Việt cho ảnh."]
        with Image.open(file_path) as image, ImageOps.exif_transpose(image).convert("L") as grayscale:
            if grayscale.width * grayscale.height > MAX_RENDER_PIXELS:
                scale = math.sqrt(MAX_RENDER_PIXELS / (grayscale.width * grayscale.height))
                grayscale.thumbnail((max(1, int(grayscale.width * scale)), max(1, int(grayscale.height * scale))))
            text = recognize(grayscale)
        return text, [] if text else ["OCR chưa nhận diện được chữ trong ảnh."]
