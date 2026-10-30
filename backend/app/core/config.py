from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: Literal["local", "development", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str

    fake_auth_enabled: bool = False
    fake_current_user_id: UUID | None = None

    keycloak_issuer: str | None = None
    keycloak_jwks_url: str | None = None
    keycloak_audience: str = "helpdesk-api"

    # Only explicitly configured website origins receive CORS permission.
    cors_allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        """Require explicit HTTP(S) origins, with no paths or credentials."""
        for origin in origins:
            if not origin or any(character.isspace() for character in origin) or "\\" in origin or "*" in origin:
                raise ValueError("CORS origins must be explicit HTTP(S) origins.")
            try:
                parsed = urlsplit(origin)
                port = parsed.port
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and origin == f"{parsed.scheme}://{parsed.netloc}"
                    and not parsed.netloc.endswith(":")
                    and (port is None or port > 0)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("CORS origins must contain only an HTTP(S) scheme, host and optional valid port.")
        return origins
