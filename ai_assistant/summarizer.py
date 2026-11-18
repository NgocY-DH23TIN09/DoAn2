import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logger = logging.getLogger(__name__)


class SummarizationError(Exception):
    """Raised when the configured AI provider cannot create a summary."""


def summarize_text(text: str, max_length: Optional[int] = None) -> str:
    """Tóm tắt nội dung văn bản hành chính bằng Gemini AI hoặc tự động fallback khi lỗi."""
    normalized = (text or "").strip()
    if not normalized:
        return "Nội dung văn bản trống."
    if len(normalized) < 30:
        return "Nội dung văn bản quá ngắn để tóm tắt."

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key.lower().startswith("your_"):
        api_key = ""

    def truncate(summary: str) -> str:
        if not max_length or len(summary) <= max_length:
            return summary
        if max_length <= 3:
            return summary[:max_length]
        return summary[: max_length - 3].rstrip() + "..."

    def fallback_summary() -> str:
        preview = normalized[:220].replace("\n", " ")
        return truncate(f"[Tóm tắt tự động]: {preview}...")

    if not api_key:
        return fallback_summary()

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = (
            "Bạn là trợ lý hành chính văn phòng. Hãy tóm tắt ngắn gọn (3-4 gạch đầu dòng) "
            "các ý chính, yêu cầu và thời hạn (nếu có) của văn bản sau bằng tiếng Việt:\n\n"
            f"{normalized[:8000]}"
        )
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
        )
        summary = (response.text or "").strip()
        if not summary:
            return fallback_summary()
        return truncate(summary)
    except Exception as exc:
        logger.warning("Gemini API lỗi, chuyển sang tóm tắt dự phòng: %s", exc)
        return fallback_summary()


def summarize_document_text(text: str) -> str:
    return summarize_text(text)