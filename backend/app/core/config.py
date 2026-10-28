from typing import Literal
from uuid import UUID

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: Literal["local", "development", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str

    fake_auth_enabled: bool = False
    fake_current_user_id: UUID | None = None
