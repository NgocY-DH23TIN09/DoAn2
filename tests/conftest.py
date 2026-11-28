import os
from uuid import uuid4

from pymongo import MongoClient

TEST_DATABASE = f"nexusoffice_test_{uuid4().hex}"
TEST_URI = os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27028/")
os.environ.update(
    MONGODB_URI=TEST_URI,
    MONGODB_DATABASE=TEST_DATABASE,
    APP_ENV="development",
    ADMIN_INITIAL_PASSWORD="",
    SECRET_KEY="nexusoffice-test-key-only-" + uuid4().hex,
    GEMINI_API_KEY="",
)


def pytest_sessionfinish(session, exitstatus):
    with MongoClient(TEST_URI, serverSelectionTimeoutMS=1500) as client:
        from pymongo.errors import PyMongoError

        try:
            client.drop_database(TEST_DATABASE)
        except PyMongoError:
            if exitstatus == 0:
                raise
