import json
import logging
from datetime import UTC, datetime

from app.middleware.request_id import request_id_context


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:

        message = record.getMessage()
        timestamp = datetime.now(UTC)
        ts = timestamp.isoformat()
        request_id = request_id_context.get()

        log_data = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": request_id,
        }

        return json.dumps(log_data)


def configure_logging(log_level: str) -> None:

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
