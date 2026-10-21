import logging

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import configure_logging
from app.middleware.request_id import request_id_middleware

settings = Settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

logger.info("application_started")


# init FastAPi as app
app = FastAPI()

app.middleware("http")(request_id_middleware)


# register a route
@app.get("/")
async def root() -> dict[str, str]:
    logger.info("root_endpoint_called")
    return {"message": "Hello World, this is my first fastAPI program"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
