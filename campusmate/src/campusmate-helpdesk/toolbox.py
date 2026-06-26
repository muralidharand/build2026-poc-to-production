"""MCP Toolbox integration for CampusMate helpdesk.

Builds an MCPStreamableHTTPTool from TOOLBOX_ENDPOINT when set.
Returns None when the endpoint is not configured so callers can
safely omit the tool.
"""
from __future__ import annotations

import os
from typing import Optional


def build_toolbox_tool() -> Optional[object]:
    """Return an MCPStreamableHTTPTool if TOOLBOX_ENDPOINT is set, else None."""
    endpoint = os.getenv("TOOLBOX_ENDPOINT", "").strip()
    if not endpoint:
        return None

    try:
        from mcp import MCPStreamableHTTPTool  # type: ignore[import]

        return MCPStreamableHTTPTool(endpoint=endpoint)
    except Exception as exc:  # pragma: no cover
        print(f"[toolbox] Could not build MCP tool from {endpoint!r}: {exc}")
        return None
