from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    app_env: Literal["local", "development", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str
