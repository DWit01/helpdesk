from collections.abc import Callable
from uuid import UUID, uuid4

from mcp.server.auth.provider import AccessToken
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import ApplicationError, AuthenticationRequired, AuthenticationUnavailable
from app.core.security import TokenVerifier
from app.services.identity_service import IdentityService

SessionFactory = Callable[[], Session]


class HelpdeskAccessToken(AccessToken):
    """Transport credentials with a verified local user identifier."""

    actor_id: UUID


class HelpdeskTokenVerifier:
    def __init__(
        self,
        verifier: TokenVerifier,
        sessions: SessionFactory,
        *,
        issuer: str,
        resource: str,
    ) -> None:
        self.verifier = verifier
        self.sessions = sessions
        self.issuer = issuer
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await run_in_threadpool(self._verify, token)
        except AuthenticationRequired:
            return None
        except SQLAlchemyError:
            raise AuthenticationUnavailable() from None

    def _verify(self, token: str) -> HelpdeskAccessToken:
        # Signature/claim validation completes before opening a database Session.
        identity = self.verifier.verify(token)
        with self.sessions() as db:
            actor_id = IdentityService(db).resolve(identity).id

        return HelpdeskAccessToken(
            token=token,
            # This is a transport label, not OAuth client authentication.
            # HTTP is stateless; the verified issuer/subject identify the user.
            client_id="helpdesk-mcp",
            scopes=[],
            subject=identity.subject,
            claims={"iss": self.issuer},
            resource=self.resource,
            actor_id=actor_id,
        )


class HTTPBoundary:
    """Reject ambiguous credentials and return safe authentication failures."""

    def __init__(self, app: ASGIApp, metadata_url: str) -> None:
        self.app = app
        self.metadata_url = metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["mcp_request_id"] = request_id

        async def send_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode("ascii")),
                ]
            await send(message)

        try:
            request = Request(scope)
            if request.url.path.rstrip("/") == "/mcp":
                headers = request.headers.getlist("authorization")
                if len(headers) > 1 or "access_token" in request.query_params:
                    raise AuthenticationRequired()
                if headers:
                    parts = headers[0].split(" ")
                    if (
                        len(parts) != 2
                        or parts[0].lower() != "bearer"
                        or not parts[1]
                        or any(character.isspace() for character in parts[1])
                    ):
                        raise AuthenticationRequired()
            await self.app(scope, receive, send_response)
        except ApplicationError as error:
            response_headers: dict[str, str] = {}
            if error.status_code == 401:
                response_headers["WWW-Authenticate"] = f'Bearer resource_metadata="{self.metadata_url}"'
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.message}, "request_id": request_id},
                status_code=error.status_code,
                headers=response_headers,
            )
            await response(scope, receive, send_response)
