import logging

from pymongo.errors import PyMongoError

from backend.database import db, meetings_collection

logger = logging.getLogger(__name__)


class MongoMeetingError(Exception):
    """Raised when the MongoDB meeting store is unavailable."""


def get_meetings_collection():
    try:
        db.command("ping")
        return meetings_collection
    except (PyMongoError, ValueError) as exc:
        logger.warning("MongoDB meeting storage is unavailable.", exc_info=exc)
        raise MongoMeetingError("Không thể kết nối kho dữ liệu cuộc họp.") from exc
