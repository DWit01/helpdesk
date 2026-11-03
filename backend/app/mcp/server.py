from mcp.server import MCPServer
from starlette.applications import Starlette


def create_mcp_server() -> MCPServer:
    """Create an independent Helpdesk MCP server instance."""
    mcp = MCPServer("Helpdesk")
    return mcp


def create_mcp_app() -> Starlette:
    """Create the Helpdesk MCP HTTP application."""
    mcp = create_mcp_server()
    app = mcp.streamable_http_app()
    return app
