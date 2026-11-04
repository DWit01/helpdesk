import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from mcp.server import MCPServer
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel, ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request

from app.core.exceptions import ApplicationError, AuthenticationRequired
from app.core.security import TokenVerifier
from app.mcp import tools as adapters
from app.mcp.auth import HelpdeskAccessToken, HelpdeskTokenVerifier, HTTPBoundary, SessionFactory
from app.mcp.config import MCPSettings
from app.models import TicketPriority, TicketStatus
from app.schemas.category import CategoryRead
from app.schemas.comment import CommentRead
from app.schemas.pagination import Page
from app.schemas.ticket import TicketCreate, TicketRead
from app.services.category_service import CategoryService
from app.services.comment_service import CommentService
from app.services.ticket_service import TicketService

audit_logger = logging.getLogger("helpdesk.mcp.audit")
audit_logger.setLevel(logging.INFO)

INPUTS: dict[str, type[BaseModel]] = {
    "search_tickets": adapters.SearchTicketsInput,
    "get_ticket": adapters.GetTicketInput,
    "create_ticket": TicketCreate,
    "add_comment": adapters.AddCommentInput,
    "update_ticket_status": adapters.UpdateTicketStatusInput,
    "list_categories": adapters.ListCategoriesInput,
}


class CategoriesResult(BaseModel):
    categories: list[CategoryRead]


def credentials(request: Request | None) -> HelpdeskAccessToken:
    if request is None:
        raise AuthenticationRequired()
    user = request.scope.get("user")
    if not isinstance(user, AuthenticatedUser) or not isinstance(user.access_token, HelpdeskAccessToken):
        raise AuthenticationRequired()
    return user.access_token


def failure(code: str, message: str, request_id: str) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "error": {"code": code, "message": message},
                        "request_id": request_id,
                    }
                ),
            )
        ],
    )


class ToolBoundary:
    """Validate raw arguments before SDK coercion; audit without argument values."""

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if ctx.method != "tools/call":
            return await call_next(ctx)

        request = ctx.request if isinstance(ctx.request, Request) else None
        request_id = getattr(request.state, "mcp_request_id", str(uuid4())) if request is not None else str(uuid4())
        actor_id: str | None = None
        tool_name = "unknown"
        outcome = "error"
        code = "INTERNAL_ERROR"
        try:
            actor_id = str(credentials(request).actor_id)
            params = ctx.params or {}
            name = params.get("name")
            if not isinstance(name, str) or name not in INPUTS:
                code = "UNKNOWN_TOOL"
                return failure(code, "The requested tool does not exist.", request_id)
            tool_name = name

            # Includes extra='forbid': requester_id/role/etc cannot be ignored.
            INPUTS[name].model_validate(params.get("arguments", {}))
            result = await call_next(ctx)
            if isinstance(result, dict):
                result = CallToolResult.model_validate(result)

            if isinstance(result, CallToolResult) and result.is_error:
                error = getattr(request.state, "mcp_error", None) if request is not None else None
                if isinstance(error, ApplicationError):
                    code = error.code
                    return failure(code, error.message, request_id)
                code = "VALIDATION_ERROR"
                return failure(code, "Tool arguments or result could not be validated.", request_id)
            outcome = "success"
            code = "OK"
            return result
        except ValidationError:
            code = "VALIDATION_ERROR"
            return failure(code, "Tool arguments do not satisfy the input contract.", request_id)
        except ApplicationError as error:
            code = error.code
            return failure(code, error.message, request_id)
        except Exception:
            # Never log exception text, tokens, arguments or ticket/comment bodies.
            return failure(code, "The tool could not complete the operation.", request_id)
        finally:
            audit_logger.info(
                json.dumps(
                    {
                        "event": "mcp_tool_call",
                        "tool": tool_name,
                        "user_id": actor_id,
                        "request_id": request_id,
                        "outcome": outcome,
                        "code": code,
                    }
                )
            )


class Runtime:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def execute[T](
        self,
        ctx: Context[Any, Request],
        operation: Callable[[Session, UUID], T],
    ) -> T:
        request = ctx.request_context.request
        try:
            actor_id = credentials(request).actor_id
            # Both construction and use occur on the SDK's sync worker thread.
            # No Session is retained globally or shared between tool calls.
            with self.sessions() as db:
                return operation(db, actor_id)
        except ApplicationError as error:
            if request is not None:
                request.state.mcp_error = error
            raise ToolError(error.code) from None
        except ValidationError:
            if request is not None:
                request.state.mcp_error = ApplicationError("VALIDATION_ERROR", "Invalid tool arguments.", 422)
            raise ToolError("VALIDATION_ERROR") from None
        except Exception:
            if request is not None:
                request.state.mcp_error = ApplicationError("INTERNAL_ERROR", "The operation could not complete.", 500)
            raise ToolError("INTERNAL_ERROR") from None


def registered_tools(runtime: Runtime) -> list[Tool]:
    def search_tickets(
        ctx: Context[Any, Request],
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
        category_id: int | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[TicketRead]:
        """Find visible, non-deleted tickets. Requesters see only their own tickets."""
        return runtime.execute(
            ctx,
            lambda db, actor: adapters.search_tickets(
                TicketService(db, actor),
                status=status,
                priority=priority,
                category_id=category_id,
                query=query,
                page=page,
                page_size=page_size,
            ),
        )

    def get_ticket(ctx: Context[Any, Request], ticket_id: int) -> adapters.TicketDetail:
        """Read a visible ticket by ID with public comments only. Hidden/missing tickets share an error."""
        return runtime.execute(
            ctx, lambda db, actor: adapters.get_ticket(TicketService(db, actor), ticket_id=ticket_id)
        )

    def create_ticket(
        ctx: Context[Any, Request],
        title: str,
        description: str,
        category_id: int,
        priority: TicketPriority = TicketPriority.MEDIUM,
    ) -> TicketRead:
        """Create an OPEN ticket for the authenticated caller in an active category."""
        return runtime.execute(
            ctx,
            lambda db, actor: adapters.create_ticket(
                TicketService(db, actor),
                title=title,
                description=description,
                category_id=category_id,
                priority=priority,
            ),
        )

    def add_comment(
        ctx: Context[Any, Request],
        ticket_id: int,
        body: str,
        is_internal: bool = False,
    ) -> CommentRead:
        """Add a public comment, or a staff-only internal note. Closed tickets reject comments."""
        return runtime.execute(
            ctx,
            lambda db, actor: adapters.add_comment(
                CommentService(db, actor),
                ticket_id=ticket_id,
                body=body,
                is_internal=is_internal,
            ),
        )

    def update_ticket_status(
        ctx: Context[Any, Request],
        ticket_id: int,
        status: TicketStatus,
        resolution_comment: str | None = None,
    ) -> TicketRead:
        """Apply an authorised lifecycle transition. Resolving requires a non-blank public resolution comment.
        Requesters may close/reopen their RESOLVED tickets. Starting/reopening requires an active staff assignee.
        Only admins may close an OPEN ticket. Invalid transitions fail without changing the ticket.
        """
        return runtime.execute(
            ctx,
            lambda db, actor: adapters.update_ticket_status(
                TicketService(db, actor),
                ticket_id=ticket_id,
                status=status,
                resolution_comment=resolution_comment,
            ),
        )

    def list_categories(ctx: Context[Any, Request]) -> CategoriesResult:
        """List active categories available for ticket creation. Inactive categories are excluded for every role."""
        return runtime.execute(
            ctx,
            lambda db, actor: CategoriesResult(
                categories=adapters.list_categories(CategoryService(db, actor)),
            ),
        )

    descriptions = {
        "ticket_id": "Positive numeric ticket ID; this tool does not accept a ticket number.",
        "title": "Ticket summary, 5 to 120 characters.",
        "description": "Problem details, 10 to 5000 characters.",
        "category_id": "Positive category ID. Creation requires an active category.",
        "priority": "LOW, MEDIUM, HIGH or CRITICAL; creation defaults to MEDIUM.",
        "status": "OPEN, IN_PROGRESS, RESOLVED or CLOSED.",
        "query": "Optional text to search in ticket titles and descriptions.",
        "page": "Page number, starting at 1.",
        "page_size": "Results per page, from 1 to 100; default 20.",
        "body": "Comment text, 1 to 2000 characters.",
        "is_internal": "False for public comments; True requires agent/admin permission.",
        "resolution_comment": "Non-blank public resolution text when resolving; otherwise omit.",
    }
    result: list[Tool] = []
    for fn in (search_tickets, get_ticket, create_ticket, add_comment, update_ticket_status, list_categories):
        name = fn.__name__
        read_only = name in {"search_tickets", "get_ticket", "list_categories"}
        tool = Tool.from_function(
            fn,
            annotations=ToolAnnotations(
                read_only_hint=read_only,
                destructive_hint=name == "update_ticket_status",
                idempotent_hint=read_only,
                open_world_hint=False,
            ),
        )
        # Publish the same authoritative Pydantic contract the boundary validates.
        # The hidden SDK Context/service/identity never appear in this schema.
        schema = INPUTS[name].model_json_schema()
        for field, info in schema.get("properties", {}).items():
            if field in descriptions:
                info["description"] = descriptions[field]
        tool.parameters = schema
        if name == "search_tickets":
            tool.fn_metadata.output_schema = Page[TicketRead].model_json_schema(mode="serialization")
        result.append(tool)
    return result


def create_mcp_server(
    settings: MCPSettings,
    sessions: SessionFactory,
    verifier: TokenVerifier,
) -> MCPServer[Any]:
    issuer = settings.keycloak_issuer
    assert issuer is not None
    resource = str(settings.mcp_resource_url)
    auth = HelpdeskTokenVerifier(verifier, sessions, issuer=issuer, resource=resource)
    return MCPServer(
        "Helpdesk",
        log_level=settings.log_level,
        instructions=(
            "Act as the authenticated user. Use only permitted operations; "
            "never infer identity or role from tool input."
        ),
        tools=registered_tools(Runtime(sessions)),
        middleware=[ToolBoundary()],
        token_verifier=auth,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=settings.mcp_resource_url,
            validate_token_resource=True,
            required_scopes=[],
        ),
    )


def create_mcp_app(
    settings: MCPSettings | None = None,
    sessions: SessionFactory | None = None,
) -> Starlette:
    configuration = settings if settings is not None else MCPSettings()
    owned_engine: Engine | None = None
    if sessions is None:
        from app.db.session import build_engine

        owned_engine = build_engine(configuration.database_url)
        sessions = sessionmaker(bind=owned_engine)
    assert configuration.keycloak_issuer is not None
    assert configuration.keycloak_jwks_url is not None
    verifier = TokenVerifier(
        issuer=configuration.keycloak_issuer,
        jwks_url=configuration.keycloak_jwks_url,
        audience=str(configuration.mcp_resource_url),
    )
    server = create_mcp_server(configuration, sessions, verifier)
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    app.add_middleware(
        HTTPBoundary,
        metadata_url=str(build_resource_metadata_url(configuration.mcp_resource_url)),
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[None]:
        try:
            async with original_lifespan(application):
                yield
        finally:
            if owned_engine is not None:
                owned_engine.dispose()

    app.router.lifespan_context = lifespan
    return app
