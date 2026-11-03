import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.mcp.server import create_mcp_app, create_mcp_server


def test_mcp_server_lists_no_tools() -> None:
    """Verify that the initial server exposes no tools."""

    async def check() -> None:
        async with Client(create_mcp_server()) as client:
            result = await client.list_tools()

            assert result.tools == []

    asyncio.run(check())


def test_mcp_http_app_lists_no_tools() -> None:
    """Verify tool discovery through the Streamable HTTP application."""

    async def check() -> None:
        app = create_mcp_app()

        async with asyncio.timeout(15):
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://127.0.0.1:8001",
                    trust_env=False,
                ) as http_client:
                    transport = streamable_http_client(
                        "http://127.0.0.1:8001/mcp/",
                        http_client=http_client,
                    )

                    async with Client(
                        transport,
                        mode="2026-07-28",
                    ) as client:
                        result = await client.list_tools()

                        assert result.tools == []

    asyncio.run(check())
