from types import SimpleNamespace

from docx import Document

from ai_assistant import extractor


def test_pdf_uses_ocr_for_empty_pages_and_preserves_native_text(tmp_path, monkeypatch):
    source = tmp_path / "mixed.pdf"
    source.write_bytes(b"simulated PDF")
    pages = [SimpleNamespace(extract_text=lambda: "Lớp chữ có sẵn"), SimpleNamespace(extract_text=lambda: "")]
    monkeypatch.setattr(extractor, "PdfReader", lambda path: SimpleNamespace(is_encrypted=False, pages=pages))

    def ocr(path, indices):
        assert path == str(source) and indices == [1]
        return {1: "OCR đọc được tiếng Việt"}, []

    monkeypatch.setattr(extractor, "recognize_pdf_pages", ocr)
    result = extractor.extract_text_from_file(str(source))
    assert result == {
        "text": "--- Trang 1 ---\nLớp chữ có sẵn\n--- Trang 2 ---\nOCR đọc được tiếng Việt",
        "page_count": 2, "ocr_pages": 1, "method": "mixed", "warnings": [],
    }


def test_pdf_continues_after_bad_page_and_ocr_checks_page_number_and_footer(tmp_path, monkeypatch):
    source = tmp_path / "pages.pdf"
    source.touch()

    def unreadable():
        raise ValueError("page could not be decoded")

    image = SimpleNamespace(get_object=lambda: {"/Subtype": "/Image", "/Width": 900, "/Height": 1400})
    objects = SimpleNamespace(get_object=lambda: {"/Img": image})
    resources = SimpleNamespace(get_object=lambda: {"/XObject": objects})
    pages = [
        SimpleNamespace(extract_text=lambda: "Nội dung gốc đầu tiên"),
        SimpleNamespace(extract_text=unreadable),
        SimpleNamespace(extract_text=lambda: "Trang 3 / 5"),
        SimpleNamespace(extract_text=lambda: "Văn phòng số • Tài liệu lưu hành nội bộ", get=lambda key: resources),
        SimpleNamespace(extract_text=lambda: "Nội dung gốc cuối cùng"),
    ]
    monkeypatch.setattr(extractor, "PdfReader", lambda _: SimpleNamespace(is_encrypted=False, pages=pages))

    def recognize(path, indices):
        assert indices == [1, 2, 3]
        return {index: f"Nội dung chính đọc từ ảnh trang {index + 1}, sau nhiều chữ ở phần đầu và chân trang." for index in indices}, []

    monkeypatch.setattr(extractor, "recognize_pdf_pages", recognize)
    result = extractor.extract_text_from_file(str(source))
    assert "Nội dung gốc đầu tiên" in result["text"]
    assert "Nội dung gốc cuối cùng" in result["text"]
    assert result["ocr_pages"] == 3 and result["method"] == "mixed"
    assert "Không đọc được lớp chữ ở trang 2." in result["warnings"]


def test_docx_extracts_in_paragraph_table_paragraph_order(tmp_path):
    source = tmp_path / "ordered.docx"
    doc = Document()
    doc.add_paragraph("Đoạn trước")
    doc.add_table(rows=1, cols=2).rows[0].cells[0].text = "Dữ liệu bảng"
    doc.add_paragraph("Đoạn sau")
    doc.save(source)
    result = extractor.extract_text_from_file(str(source))
    assert result["text"] == "Đoạn trước\nDữ liệu bảng | \nĐoạn sau"
