from io import BytesIO
from zipfile import ZipFile

from docx import Document
from docx.table import Table

MAX_WORD_PACKAGE_BYTES = 64 * 1024 * 1024
ALIGNMENTS = {"LEFT": "left", "CENTER": "center", "RIGHT": "right", "JUSTIFY": "justify"}


def word_preview_blocks(payload: bytes) -> list[dict]:
    source = BytesIO(payload)
    with ZipFile(source) as package:
        if sum(item.file_size for item in package.infolist()) > MAX_WORD_PACKAGE_BYTES:
            raise ValueError("Word package is too large for preview")

    source.seek(0)
    document = Document(source)
    blocks = []
    for element in document.iter_inner_content():
        if isinstance(element, Table):
            blocks.append({"type": "table", "rows": [[cell.text for cell in row.cells] for row in element.rows]})
            continue
        text = element.text
        if not text.strip():
            continue
        style = element.style
        style_name = (style.name or "") if style else ""
        alignment = element.alignment
        if alignment is None and style:
            alignment = style.paragraph_format.alignment
        blocks.append({
            "type": "heading" if style_name == "Title" or style_name.startswith("Heading ") else "paragraph",
            "text": text,
            "alignment": ALIGNMENTS.get(getattr(alignment, "name", ""), "left"),
        })
    return blocks
