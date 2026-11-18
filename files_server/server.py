"""Files MCP server: a small, sandboxed stand-in for @modelcontextprotocol/server-filesystem.

Same tool names as the official server (read_text_file, list_directory, write_file)
so prompts from the slides work unchanged, but written in Python with the MCP SDK v2
so the demo needs no network download on the day.

Boundary (what Lab 01 shows): every path must resolve inside FILES_ROOT (the lab
folder). Reading /etc/hosts or C:/Windows/win.ini is refused. Writes are further
limited to FILES_WRITABLE (default: reports/ and inbox/).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

ROOT = Path(os.environ.get("FILES_ROOT", Path(__file__).resolve().parent.parent)).resolve()
WRITABLE = [(ROOT / p.strip()).resolve() for p in os.environ.get("FILES_WRITABLE", "reports,inbox").split(",") if p.strip()]
HIDDEN = {".venv", "__pycache__", ".git", "node_modules"}

logging.basicConfig(stream=sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s files %(levelname)s %(message)s")
log = logging.getLogger("files")

mcp = MCPServer("files")


def resolve(path: str) -> Path:
    p = Path(path)
    target = (p if p.is_absolute() else ROOT / p).resolve()
    if not target.is_relative_to(ROOT):
        raise ToolError(f"Access denied: {path} is outside the allowed folder {ROOT.name}/")
    if any(part in HIDDEN for part in target.relative_to(ROOT).parts):
        raise ToolError(f"Access denied: {path} is in a hidden folder")
    return target


@mcp.tool(title="Read a text file", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def read_text_file(path: str) -> dict[str, Any]:
    """Read a UTF-8 text file inside the lab folder, e.g. inbox/northwind-call.md. Returns its content."""
    log.info("read_text_file %s", path)
    target = resolve(path)
    if not target.is_file():
        raise ToolError(f"No such file: {path}")
    return {"path": str(target.relative_to(ROOT)), "content": target.read_text(encoding="utf-8")}


@mcp.tool(title="List a directory", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def list_directory(path: str = ".") -> dict[str, Any]:
    """List files and folders in a directory inside the lab folder, e.g. inbox or reports."""
    log.info("list_directory %s", path)
    target = resolve(path)
    if not target.is_dir():
        raise ToolError(f"Not a directory: {path}")
    entries = [
        {"name": c.name, "type": "dir" if c.is_dir() else "file"}
        for c in sorted(target.iterdir())
        if c.name not in HIDDEN and not c.name.startswith(".")
    ]
    return {"path": str(target.relative_to(ROOT)) or ".", "entries": entries}


@mcp.tool(title="Write a text file", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def write_file(path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a text file. Only allowed under reports/ or inbox/. Use for status reports."""
    log.info("write_file %s (%d chars)", path, len(content))
    target = resolve(path)
    if not any(target.is_relative_to(w) for w in WRITABLE):
        allowed = ", ".join(str(w.relative_to(ROOT)) + "/" for w in WRITABLE)
        raise ToolError(f"Write denied: {path} is outside the writable folders ({allowed})")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": str(target.relative_to(ROOT)), "bytes": len(content.encode("utf-8"))}


if __name__ == "__main__":
    log.info("starting files server on stdio, root=%s", ROOT)
    mcp.run(transport="stdio")
