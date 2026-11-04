from typing import Self

from pydantic import AnyHttpUrl, model_validator

from app.core.config import Settings


class MCPSettings(Settings):
    """MCP settings, separate from the unchanged REST audience."""

    mcp_resource_url: AnyHttpUrl
    mcp_login_client_id: str = "helpdesk-mcp-local"

    @model_validator(mode="after")
    def validate_resource(self) -> Self:
        resource = self.mcp_resource_url
        if (
            resource.path != "/mcp"
            or resource.query is not None
            or resource.fragment is not None
            or resource.username is not None
            or resource.password is not None
            or (resource.scheme == "http" and resource.host not in {"127.0.0.1", "localhost", "[::1]"})
            or str(resource) == self.keycloak_audience
        ):
            raise ValueError(
                "MCP requires its own /mcp resource URL, HTTPS or loopback HTTP, without credentials or query."
            )
        if self.keycloak_issuer is None or self.keycloak_jwks_url is None:
            raise ValueError("Configure the Keycloak issuer and JWKS URL before starting MCP.")
        return self
