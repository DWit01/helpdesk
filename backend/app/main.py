import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api.documentation import APPLICATION_DESCRIPTION
from app.api.error_handlers import error_response, register_exception_handlers
from app.api.router import api_router
from app.application import HelpdeskAPI
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.readiness import database_is_ready
from app.db.session import engine
from app.middleware.request_id import request_id_middleware

settings = Settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

logger.info("application_started")


app = HelpdeskAPI(
    debug=False,
    title="Helpdesk API",
    version="0.1.0",
    description=APPLICATION_DESCRIPTION,
    openapi_tags=[
        {"name": "Operations", "description": "Public process and database probes."},
        {"name": "Users", "description": "Current user and staff user management."},
        {"name": "Categories", "description": "Support categories and admin maintenance."},
        {"name": "Tickets", "description": "Visible tickets, assignment and lifecycle."},
        {"name": "Comments", "description": "Public comments and staff-only internal notes."},
    ],
)

app.cors_allowed_origins = tuple(settings.cors_allowed_origins)

register_exception_handlers(app)
app.middleware("http")(request_id_middleware)
app.include_router(api_router)


@app.get(
    "/",
    tags=["Operations"],
    summary="Get the application greeting",
)
async def root() -> dict[str, str]:
    """Return the existing introductory application message."""
    logger.info("root_endpoint_called")
    return {"message": "Hello World, this is my first fastAPI program"}


@app.get(
    "/health",
    tags=["Operations"],
    summary="Check process health",
)
async def health() -> dict[str, str]:
    """Public process check. Does not access the database."""
    return {"status": "ok"}


@app.get(
    "/ready",
    tags=["Operations"],
    summary="Check database readiness",
)
def ready(request: Request) -> JSONResponse:
    """Public SELECT 1 probe. Returns 200 when reachable or a safe 503 on database failure."""
    # Normal def handlers run in FastAPI's worker thread pool.
    if not database_is_ready(engine):
        return error_response(
            request,
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Database is unavailable.",
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(
        content={"status": "ready"},
        headers={"Cache-Control": "no-store"},
    )
