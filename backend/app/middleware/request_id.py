from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request, Response

request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())

    # Request state survives when the ContextVar is reset during unwinding.
    request.state.request_id = request_id
    token = request_id_context.set(request_id)

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_context.reset(token)
