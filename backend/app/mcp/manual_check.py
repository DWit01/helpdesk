"""Manual, local-only PKCE login and MCP checks. Tokens remain in memory."""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, TextContent

from app.core.security import TokenVerifier
from app.mcp.config import MCPSettings

CALLBACK = "http://127.0.0.1:8765/callback"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def callback_code(path: str, state: str, issuer: str) -> str:
    parsed = urlsplit(path)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.path != "/callback"
        or any(len(values) != 1 for values in parameters.values())
        or "error" in parameters
        or not secrets.compare_digest(parameters.get("state", [""])[0], state)
        or parameters.get("iss", [""])[0] != issuer
        or not parameters.get("code", [""])[0]
    ):
        raise ValueError("Invalid login callback.")
    return parameters["code"][0]


def login(settings: MCPSettings) -> str:
    issuer = settings.keycloak_issuer
    jwks = settings.keycloak_jwks_url
    assert issuer is not None and jwks is not None
    expected_issuer: str = issuer
    verifier = TokenVerifier(issuer=issuer, jwks_url=jwks, audience=str(settings.mcp_resource_url))
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    received: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Never log callback URLs containing one-time codes.
            return

        def do_GET(self) -> None:
            try:
                received["code"] = callback_code(self.path, state, expected_issuer)
                self.send_response(200)
                body = b"Login received. Return to PowerShell."
            except ValueError:
                received["failed"] = "true"
                self.send_response(400)
                body = b"Login callback rejected. Restart the check."
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    parameters = {
        "client_id": settings.mcp_login_client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "scope": "openid profile email",
        "resource": str(settings.mcp_resource_url),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
    }
    # Bind before opening the browser; fail safely if the callback port is occupied.
    with HTTPServer(("127.0.0.1", 8765), CallbackHandler) as server:
        server.timeout = 1
        if not webbrowser.open(f"{issuer.rstrip('/')}/protocol/openid-connect/auth?{urlencode(parameters)}"):
            raise RuntimeError("Browser could not be opened.")
        deadline = time.monotonic() + 180
        while not received and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in received:
        raise RuntimeError("Login did not complete.")

    request = Request(
        f"{issuer.rstrip('/')}/protocol/openid-connect/token",
        data=urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": settings.mcp_login_client_id,
                "redirect_uri": CALLBACK,
                "code": received["code"],
                "code_verifier": code_verifier,
                "resource": str(settings.mcp_resource_url),
            }
        ).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        payload = json.load(response)
    token = payload.get("access_token")
    if not isinstance(token, str):
        raise RuntimeError("An access token was not returned.")
    verifier.verify(token)
    return token


async def check(settings: MCPSettings, arguments: argparse.Namespace, token: str) -> None:
    assert settings.keycloak_issuer is not None and settings.keycloak_jwks_url is not None
    identity = TokenVerifier(
        issuer=settings.keycloak_issuer,
        jwks_url=settings.keycloak_jwks_url,
        audience=str(settings.mcp_resource_url),
    ).verify(token)
    effective_role = next((role for role in ("admin", "agent", "requester") if role in identity.roles), None)
    if effective_role != arguments.expect_role:
        raise RuntimeError("Login did not have the expected effective role.")

    name = arguments.action
    inputs: dict[str, Any] = {}
    if name in {"get_ticket", "add_comment", "update_ticket_status"}:
        if arguments.ticket_id is None:
            raise ValueError("This action requires --ticket-id.")
        inputs["ticket_id"] = arguments.ticket_id
    if name == "search_tickets":
        inputs["status"] = "OPEN"
    elif name == "create_ticket":
        if arguments.category_id is None:
            raise ValueError("Creation requires --category-id.")
        inputs.update(
            {
                "title": "Manual MCP keyboard issue",
                "description": "Synthetic disposable test: the keyboard does not respond.",
                "category_id": arguments.category_id,
            }
        )
    elif name == "add_comment":
        inputs.update({"body": "Synthetic manual MCP update.", "is_internal": arguments.internal})
    elif name == "update_ticket_status":
        inputs["status"] = arguments.status
        if arguments.status == "RESOLVED":
            inputs["resolution_comment"] = "Synthetic manual resolution."

    async with asyncio.timeout(30):
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            trust_env=False,
            follow_redirects=False,
        ) as http:
            transport = streamable_http_client(str(settings.mcp_resource_url), http_client=http)
            async with Client(transport, mode="2026-07-28") as client:
                discovered = await client.list_tools()
                print("Discovered tools:", ", ".join(tool.name for tool in discovered.tools))
                result = await client.call_tool(name, inputs)
                if not isinstance(result, CallToolResult):
                    raise RuntimeError("Unexpected multi-round tool response.")
                if result.is_error:
                    first = result.content[0]
                    if not isinstance(first, TextContent):
                        raise RuntimeError("Unexpected tool error shape.")
                    safe = json.loads(first.text)
                    print("Tool denied/failed:", safe["error"]["code"], safe["error"]["message"])
                    return

                payload = result.structured_content or {}
                # Print identifiers/counts rather than tokens or full ticket/comment text.
                summary = {
                    key: payload[key] for key in ("id", "ticket_number", "status", "is_internal") if key in payload
                }
                for key in ("items", "categories", "comments"):
                    if isinstance(payload.get(key), list):
                        summary[f"{key}_ids"] = [item["id"] for item in payload[key]]
                if isinstance(payload.get("ticket"), dict):
                    summary["ticket_id"] = payload["ticket"]["id"]
                print("Tool succeeded:", json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=[
            "search_tickets",
            "get_ticket",
            "create_ticket",
            "add_comment",
            "update_ticket_status",
            "list_categories",
        ],
        required=True,
    )
    parser.add_argument("--expect-role", choices=["requester", "agent", "admin"], required=True)
    parser.add_argument("--ticket-id", type=int)
    parser.add_argument("--category-id", type=int)
    parser.add_argument("--internal", action="store_true")
    parser.add_argument("--status", choices=["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"], default="CLOSED")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        settings = MCPSettings()
        asyncio.run(check(settings, arguments, login(settings)))
    except Exception:
        # Do not print HTTP token responses, callback codes or exception details.
        raise SystemExit(
            "Manual check failed. Check the selected account, trust settings and local server state."
        ) from None


if __name__ == "__main__":
    main()
