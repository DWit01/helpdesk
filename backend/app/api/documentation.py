from copy import deepcopy
from typing import Any

from fastapi import FastAPI

REQUEST_ID_HEADER: dict[str, Any] = {
    "description": (
        "Request correlation ID. Echoes a supplied non-empty X-Request-ID, "
        "otherwise the application generates a UUID."
    ),
    "schema": {"type": "string"},
}

NO_STORE_HEADER: dict[str, Any] = {
    "description": "Readiness responses must not be cached.",
    "schema": {"type": "string", "enum": ["no-store"]},
}

ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message", "details", "request_id"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "details": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["loc", "type", "message"],
                        "properties": {
                            "loc": {
                                "type": "array",
                                "items": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "integer"},
                                    ]
                                },
                            },
                            "type": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
        }
    },
}

ERROR_DESCRIPTIONS = {
    "401": "Credentials or required identity/profile claims are invalid.",
    "403": "The current user is inactive or lacks permission.",
    "404": "The requested resource is absent or not visible to this user.",
    "409": "The operation conflicts with business rules or existing data.",
    "422": "Request validation or service input validation failed.",
    "500": "An unexpected server error occurred; internal details are omitted.",
    "503": "The database readiness check failed.",
}

# These examples use safe, synthetic values.
ERROR_EXAMPLES: dict[str, tuple[str, str]] = {
    "401": ("AUTH_TOKEN_INVALID", "A valid current user is required."),
    "403": ("ACCESS_DENIED", "This action is not permitted."),
    "409": ("DATA_CONFLICT", "The operation conflicts with existing data."),
    "422": ("VALIDATION_ERROR", "Request validation failed."),
    "500": ("INTERNAL_ERROR", "An unexpected error occurred."),
    "503": ("DATABASE_UNAVAILABLE", "Database is unavailable."),
}

# Every current operation accepting a JSON request body has an example.
BODY_EXAMPLES: dict[tuple[str, str], dict[str, Any]] = {
    ("/api/v1/tickets", "post"): {
        "title": "Cannot connect to VPN",
        "description": "The VPN connection fails when I try to sign in.",
        "category_id": 1,
        "priority": "HIGH",
    },
    ("/api/v1/tickets/{ticket_id}", "patch"): {
        "title": "VPN connection still fails",
    },
    ("/api/v1/tickets/{ticket_id}/assign", "post"): {
        "assignee_id": None,
    },
    ("/api/v1/tickets/{ticket_id}/status", "post"): {
        "status": "RESOLVED",
        "resolution_comment": "Updated the VPN configuration and verified access.",
    },
    ("/api/v1/categories", "post"): {
        "name": "Network",
        "description": "Connectivity and VPN support.",
    },
    ("/api/v1/categories/{category_id}", "put"): {
        "name": "Network",
        "description": "Connectivity and VPN support.",
        "is_active": True,
    },
    ("/api/v1/tickets/{ticket_id}/comments", "post"): {
        "body": "The connection still fails after restarting.",
        "is_internal": False,
    },
    ("/api/v1/comments/{comment_id}", "put"): {
        "body": "The connection now works after updating the configuration.",
    },
    ("/api/v1/users/{user_id}", "patch"): {
        "is_active": False,
    },
}

QUERY_DESCRIPTIONS = {
    "page": "Page number, starting at 1.",
    "page_size": "Results per page. Default 20; maximum 100.",
    "status": "Repeat the parameter for multiple values: status=OPEN&status=IN_PROGRESS.",
    "priority": "Repeat the parameter for multiple values: priority=HIGH&priority=CRITICAL.",
    "category_id": "Filter by category ID.",
    "assignee_id": "Filter by assignee UUID. Cannot be combined with assignee=me.",
    "assignee": "Use me to select the current staff user. Staff only.",
    "requester_id": "Filter by requester UUID. Requesters are always scoped to themselves.",
    "unassigned_only": "Select unassigned tickets. Cannot be combined with an assignee filter.",
    "q": "Case-insensitive literal substring search in title and description.",
    "created_from": "Inclusive creation boundary. Dates mean midnight UTC.",
    "created_to": "Exclusive creation boundary. Dates mean midnight UTC.",
    "sort": "Sort field; prefix with - for descending order. Ties use ascending ticket ID.",
}

EXTRA_DESCRIPTIONS = {
    ("/api/v1/tickets/{ticket_id}", "patch"): (
        "Omit unchanged fields. At least one field is required; explicit null "
        "is rejected. Requesters may edit their own OPEN tickets but cannot "
        "change priority. CLOSED tickets are read-only."
    ),
    ("/api/v1/tickets/{ticket_id}/assign", "post"): (
        "Staff only. assignee_id is required: provide an active agent/admin UUID "
        "or null to unassign. An IN_PROGRESS ticket must retain an assignee."
    ),
    ("/api/v1/tickets/{ticket_id}/status", "post"): (
        "Allowed transitions: OPEN to IN_PROGRESS; IN_PROGRESS to OPEN; "
        "IN_PROGRESS to RESOLVED; RESOLVED to CLOSED; RESOLVED to IN_PROGRESS; "
        "OPEN to CLOSED. Requesters may only close or reopen their own RESOLVED "
        "tickets. OPEN to CLOSED requires an admin. Entering IN_PROGRESS requires "
        "an active staff assignee. RESOLVED requires a non-blank resolution_comment; "
        "the comment and status are saved atomically. resolution_comment is "
        "rejected for other target statuses."
    ),
}

APPLICATION_DESCRIPTION = """
Helpdesk REST API. Business endpoints use `/api/v1`.

**Authentication:** business endpoints require a Keycloak Bearer access token.
The API verifies the signature, expiry, issuer and helpdesk-api audience.
The verified subject identifies the local user; email is never used to link accounts.

On first authenticated access, the identity service creates the local user.
For existing active users, it synchronises username, email, optional name and
the highest recognised token role: admin, then agent, then requester.
No recognised application role means access is denied.
Inactive local users remain blocked and are never reactivated by login.
Profile synchronisation is committed before endpoint business work.
Role changes reflect the presented valid token, so older unexpired tokens
can retain their earlier role claims.

**Visibility:** requesters see only their own tickets and public comments.
Absent, soft-deleted and hidden tickets return the same safe 404 response.

**Errors:** failures use the shared `ErrorEnvelope` schema. The body's
`error.request_id` matches the `X-Request-ID` response header.
Raw request values and database exception details are omitted.

**Examples:** IDs are illustrative and must refer to suitable local records.
A documented request can still fail permission or business-rule checks.

`/health` checks the process. `/ready` checks database reachability using
`SELECT 1`; it does not verify application tables, migrations or every feature.
Both endpoints are public.
""".strip()


def error_documentation(status: str) -> dict[str, Any]:
    """Describe the envelope produced by the exception handlers."""
    media: dict[str, Any] = {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}

    if status in ERROR_EXAMPLES:
        code, message = ERROR_EXAMPLES[status]
        media["example"] = {
            "error": {
                "code": code,
                "message": message,
                "details": [],
                "request_id": "example-request-id",
            }
        }

    headers: dict[str, Any] = {"X-Request-ID": deepcopy(REQUEST_ID_HEADER)}

    if status == "401":
        headers["WWW-Authenticate"] = {
            "description": "Bearer authentication challenge.",
            "schema": {"type": "string"},
            "example": "Bearer",
        }

    return {
        "description": ERROR_DESCRIPTIONS[status],
        "headers": headers,
        "content": {"application/json": media},
    }


class DocumentedAPI(FastAPI):
    """Extend generated documentation without changing endpoint execution."""

    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is not None:
            return self.openapi_schema

        schema = super().openapi()
        schema["components"]["schemas"]["ErrorEnvelope"] = deepcopy(ERROR_SCHEMA)

        for path, path_item in schema["paths"].items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue

                responses = operation["responses"]

                if path.startswith("/api/v1/"):
                    for status in ("401", "403", "409", "422", "500"):
                        responses[status] = error_documentation(status)

                    authentication_failure = error_documentation("503")
                    authentication_failure["description"] = (
                        "Authentication configuration or signing keys are unavailable."
                    )
                    authentication_failure["content"]["application/json"]["example"]["error"].update(
                        {
                            "code": "AUTH_SERVICE_UNAVAILABLE",
                            "message": "Authentication is temporarily unavailable.",
                        }
                    )
                    responses["503"] = authentication_failure

                    # Resource lookups and ticket creation can return 404.
                    has_resource_id = "{" in path
                    creates_ticket = path == "/api/v1/tickets" and method == "post"
                    if has_resource_id or creates_ticket:
                        responses["404"] = error_documentation("404")

                    # All current writes use the service transaction boundary.
                    if method in {"post", "put", "patch", "delete"}:
                        responses["409"] = error_documentation("409")

                    example = BODY_EXAMPLES.get((path, method))
                    if example is not None:
                        media = operation["requestBody"]["content"]["application/json"]
                        media["example"] = deepcopy(example)

                    extra = EXTRA_DESCRIPTIONS.get((path, method))
                    if extra is not None:
                        description = operation.get("description", "")
                        operation["description"] = f"{description}\n\n{extra}".strip()

                # Normal responses and the global error handler expose this ID.
                for response in responses.values():
                    response.setdefault("headers", {})["X-Request-ID"] = deepcopy(REQUEST_ID_HEADER)

        search = schema["paths"]["/api/v1/tickets"]["get"]
        for parameter in search.get("parameters", []):
            description = QUERY_DESCRIPTIONS.get(parameter["name"])
            if description is not None:
                parameter["description"] = description

        # /ready returns JSONResponse directly, so describe its body explicitly.
        ready_responses = schema["paths"]["/ready"]["get"]["responses"]
        ready_responses["200"] = {
            "description": "The database answered the readiness query.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {"status": {"type": "string", "enum": ["ready"]}},
                    },
                    "example": {"status": "ready"},
                }
            },
        }
        ready_responses["503"] = error_documentation("503")
        ready_responses["500"] = error_documentation("500")

        for status in ("200", "503"):
            ready_responses[status]["headers"] = {
                "X-Request-ID": deepcopy(REQUEST_ID_HEADER),
                "Cache-Control": deepcopy(NO_STORE_HEADER),
            }

        self.openapi_schema = schema
        return schema
