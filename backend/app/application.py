from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from app.api.documentation import DocumentedAPI


class HelpdeskAPI(DocumentedAPI):
    """Keep CORS outside the error handler while retaining the FastAPI interface."""

    cors_allowed_origins: tuple[str, ...] = ()

    def build_middleware_stack(self) -> ASGIApp:
        return CORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=self.cors_allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "WWW-Authenticate"],
        )
