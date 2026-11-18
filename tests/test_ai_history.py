import pytest

from ai_assistant import history


def test_record_ai_interaction_writes_to_mongodb(monkeypatch):
    class FakeCollection:
        inserted = None

        def insert_one(self, document):
            self.inserted = document

    class FakeDatabase:
        def __init__(self, collection):
            self.collection = collection

        def __getitem__(self, name):
            assert name == "ai_interactions"
            return self.collection

    class FakeAdmin:
        def command(self, name):
            assert name == "ping"

    class FakeClient:
        admin = FakeAdmin()

        def __init__(self, uri, serverSelectionTimeoutMS):
            assert uri == "mongodb://example"
            assert serverSelectionTimeoutMS == 5000

        def __getitem__(self, name):
            assert name == "vanphongso"
            return FakeDatabase(collection)

    collection = FakeCollection()
    monkeypatch.setenv("MONGODB_URI", "mongodb://example")
    monkeypatch.setenv("MONGODB_DATABASE", "vanphongso")
    monkeypatch.setattr(history, "_client", None)
    monkeypatch.setattr(history, "_client_uri", None)
    monkeypatch.setattr(history, "MongoClient", FakeClient)

    history.record_ai_interaction(
        action="summarize",
        document_id=7,
        input_text="Document content",
        output_text="Document summary",
    )

    assert collection.inserted["action"] == "summarize"
    assert collection.inserted["document_id"] == 7
    assert collection.inserted["input_text"] == "Document content"
    assert collection.inserted["output_text"] == "Document summary"
    assert collection.inserted["created_at"].tzinfo is not None


def test_record_ai_interaction_requires_mongodb_uri(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(history, "_client", None)
    monkeypatch.setattr(history, "_client_uri", None)

    with pytest.raises(history.MongoHistoryError, match="MONGODB_URI"):
        history.record_ai_interaction(
            action="summarize",
            document_id=7,
            input_text="Document content",
            output_text="Document summary",
        )
