import logging
from http import HTTPStatus
from typing import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import ApplicationError
from app.middleware.request_id import request_id_context

logger = logging.getLogger(__name__)

HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "AUTH_TOKEN_INVALID",
    403: "ACCESS_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def get_request_id(request: Request) -> str:
    """Keep the same ID even after the middleware context has reset."""
    stored_id = getattr(request.state, "request_id", None)

    if isinstance(stored_id, str) and stored_id:
        return stored_id

    request_id = request_id_context.get() or str(uuid4())
    request.state.request_id = request_id
    return request_id


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the single error shape used by every handler."""
    request_id = get_request_id(request)

    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else [],
                "request_id": request_id,
            }
        },
        headers=headers,
    )

    # Override any conflicting exception header with the actual request ID.
    response.headers["X-Request-ID"] = request_id
    return response


async def handle_exception(request: Request, error: Exception) -> JSONResponse:
    """Translate known errors and safely report unexpected failures."""
    if isinstance(error, ApplicationError):
        return error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.message,
            headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None,
        )

    if isinstance(error, RequestValidationError):
        # Do not return raw input, validator context or custom error messages.
        # A custom validator's message can contain the submitted value.
        details: list[dict[str, object]] = [
            {
                "loc": list(item.get("loc", ())),
                "type": item["type"],
                "message": "Invalid or missing value.",
            }
            for item in error.errors()
        ]

        return error_response(
            request,
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details=details,
        )

    if isinstance(error, StarletteHTTPException):
        if error.status_code >= 500:
            code = "INTERNAL_ERROR"
            message = "An unexpected error occurred."
        else:
            code = HTTP_ERROR_CODES.get(
                error.status_code,
                f"HTTP_{error.status_code}",
            )

            try:
                message = HTTPStatus(error.status_code).phrase
            except ValueError:
                message = "The request could not be completed."

        # Preserve headers such as Allow, Retry-After and WWW-Authenticate.
        # Raw HTTPException.detail is intentionally not echoed.
        return error_response(
            request,
            status_code=error.status_code,
            code=code,
            message=message,
            headers=error.headers,
        )

    # Unexpected failures can reach this handler after the middleware's
    # finally block has reset its ContextVar. Restore the ID while logging.
    token = request_id_context.set(get_request_id(request))

    try:
        logger.error(
            "unexpected_error",
            exc_info=(type(error), error, error.__traceback__),
        )
    finally:
        request_id_context.reset(token)

    return error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register one translator for each relevant exception family."""
    for exception_class in (
        ApplicationError,
        RequestValidationError,
        StarletteHTTPException,
        Exception,
    ):
        app.add_exception_handler(exception_class, handle_exception)
