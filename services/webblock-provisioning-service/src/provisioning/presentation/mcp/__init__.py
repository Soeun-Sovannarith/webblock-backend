"""The MCP endpoint over the official SDK (decision 0006 section 4, decision 0013 section 9 S3),
with every tool of src/contracts/mcp/tools.json, build_release included."""

from provisioning.presentation.mcp.endpoint import MCPEndpoint
from provisioning.presentation.mcp.tools import ToolFileError, ToolSpec, parse_tools

__all__ = ["MCPEndpoint", "ToolFileError", "ToolSpec", "parse_tools"]
