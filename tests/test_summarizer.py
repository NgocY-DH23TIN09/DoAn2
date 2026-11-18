from types import SimpleNamespace

from google import genai

from ai_assistant.summarizer import summarize_text


def test_summarize_text_uses_configured_gemini_credentials_and_model(monkeypatch):
    received = {}

    class FakeModels:
        def generate_content(self, *, model, contents):
            received["model"] = model
            received["contents"] = contents
            return SimpleNamespace(text="Tóm tắt từ Gemini")

    class FakeClient:
        def __init__(self, *, api_key):
            received["api_key"] = api_key
            self.models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    monkeypatch.setattr(genai, "Client", FakeClient)

    result = summarize_text("Nội dung văn bản đủ dài để gửi đến Gemini và tạo bản tóm tắt.")

    assert result == "Tóm tắt từ Gemini"
    assert received["api_key"] == "test-key"
    assert received["model"] == "test-model"
    assert "Nội dung văn bản" in received["contents"]


def test_summarize_text_treats_example_key_as_unconfigured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key")

    summary = summarize_text("Nội dung văn bản đủ dài để kiểm tra cơ chế tóm tắt dự phòng.", max_length=40)

    assert summary.startswith("[Tóm tắt tự động]:")
    assert len(summary) <= 40
