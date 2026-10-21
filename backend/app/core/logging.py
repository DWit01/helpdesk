import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:

        message = record.getMessage()
        timestamp = datetime.now(UTC)
        ts = timestamp.isoformat()

        log_data = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        return json.dumps(log_data)


def configure_logging(log_level: str) -> None:

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
