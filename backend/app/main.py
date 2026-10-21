import logging

from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import configure_logging

settings = Settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

logger.info("application_started")


# init FastAPi as app
app = FastAPI()


# register a route
@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Hello World, this is my first fastAPI program"}
