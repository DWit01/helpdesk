import json
import logging
from datetime import UTC, datetime
from traceback import extract_tb

from app.middleware.request_id import request_id_context


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }

        if record.exc_info is not None:
            exception_type, _, traceback = record.exc_info

            log_data["exception_type"] = exception_type.__name__ if exception_type is not None else "Unknown"

            # Include stack locations without exception messages, source
            # text or local values, which can contain sensitive data.
            log_data["stack_trace"] = (
                [
                    {
                        "file": frame.filename,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in extract_tb(traceback)
                ]
                if traceback is not None
                else []
            )

        return json.dumps(log_data)


def configure_logging(log_level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
