"""Streamable HTTP MCP adapter over core interfaces."""

from adapters.mcp.server import create_mcp_server, mount_mcp

__all__ = ["create_mcp_server", "mount_mcp"]
