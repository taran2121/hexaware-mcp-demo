"""The MCP client side of the agent: one MCP client per server (slides 21-22, 57).

MCPHub reads servers.json (same shape as .mcp.json / .cursor/mcp.json) and opens
one MCP client per server. Three kinds of server are supported:

  stdio            local server: the hub launches it as a child process
  http             a server already running elsewhere (e.g. the tracker in Terminal 1)
  http + oauth     a remote server that needs sign-in (Atlassian), slide 69:
                   knock (401) -> find the identity provider -> you sign in -> call with a token

For each server the hub does the client's six jobs:

  01 Connect   -> Client(...)
  02 Discover  -> server_info
  03 List      -> tools/list, resources/list, prompts/list
  04 Expose    -> tool definitions for the LLM, prefixed "server__tool"
                  (optionally only an allow-list: slide 55, "tool overload")
  05 Route     -> call(name, args) sends tools/call to the right server
  06 Return    -> content + structuredContent back to the agent

It also keeps a wire log of every request/response for the "On the wire" tab.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

LAB = Path(__file__).resolve().parent.parent
SECRETS = LAB / ".secrets"  # OAuth tokens live here: git-ignored, never shown in the UI
CLIENT_NAME = "MCP Test"  # the app name people see on the OAuth consent page
SEP = "__"  # hosts prefix tool names with the server name to avoid clashes (slide 55)


@dataclass
class ToolInfo:
    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    read_only: bool

    @property
    def qualified(self) -> str:
        return f"{self.server}{SEP}{self.name}"


@dataclass
class ServerInfo:
    name: str
    command: str
    args: list[str]
    transport: str = "stdio"
    auth: str | None = None  # "oauth" for servers that need sign-in
    connected: bool = False
    needs_sign_in: bool = False
    sign_in_url: str | None = None
    error: str | None = None
    server_name: str | None = None
    hidden_tools: int = 0  # tools the server offers but the allow-list keeps from the LLM
    tools: list[ToolInfo] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)


class SignInRequired(Exception):
    """Raised at start-up when a saved token is missing or no longer works."""


class FileTokenStorage:
    """Keeps one server's OAuth client registration and tokens in .secrets/<name>-oauth.json."""

    def __init__(self, path: Path, redirect_uri: str) -> None:
        self.path = path
        self.redirect_uri = redirect_uri

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
        os.chmod(self.path, 0o600)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        if not raw:
            return None
        # A registration made for another callback port, or under another app name, isn't reused:
        # returning None makes the SDK register again.
        if self.redirect_uri not in [str(u) for u in raw.get("redirect_uris") or []]:
            return None
        if raw.get("client_name") != CLIENT_NAME:
            return None
        return OAuthClientInformationFull.model_validate(raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap anyio's ExceptionGroup to the first real error, e.g. ConnectError."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _expand(value: str) -> str:
    return value.replace("${PY}", sys.executable).replace("${LAB}", str(LAB))


class MCPHub:
    def __init__(self, config_path: Path, public_url: str = "http://localhost:8600") -> None:
        self.config_path = config_path
        self.public_url = public_url  # where the OAuth provider sends the browser back to
        self.config: dict[str, dict[str, Any]] = {}
        self.servers: dict[str, ServerInfo] = {}
        self.clients: dict[str, Client] = {}
        self.wire: list[dict[str, Any]] = []
        self._stack: AsyncExitStack | None = None
        self._seq = 0
        self._stop = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._callbacks: dict[str, asyncio.Future[AuthorizationCodeResult]] = {}
        self._progress: dict[str, asyncio.Event] = {}

    # -- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        self._stack = AsyncExitStack()
        self.config = json.loads(self.config_path.read_text())["mcpServers"]
        for name, spec in self.config.items():
            if spec.get("auth") == "oauth":
                info = ServerInfo(name=name, command=spec["url"], args=[], transport="http", auth="oauth")
                self.servers[name] = info
                saved = SECRETS / f"{name}-oauth.json"
                if saved.exists() and json.loads(saved.read_text()).get("tokens"):
                    await self.connect(name, interactive=False)  # reuse the saved sign-in
                else:
                    info.needs_sign_in = True
                continue

            if "url" in spec:
                # Remote / already-running server: Streamable HTTP. Nothing is launched.
                info = ServerInfo(name=name, command=spec["url"], args=[], transport="http")
                target: Any = spec["url"]
            else:
                # Local server: the host launches it as a child process and talks over stdio.
                info = ServerInfo(name=name, command=_expand(spec["command"]), args=[_expand(a) for a in spec.get("args", [])])
                env = {**os.environ, **{k: _expand(v) for k, v in spec.get("env", {}).items()}}
                target = StdioServerParameters(command=info.command, args=info.args, env=env, cwd=str(LAB))
            self.servers[name] = info
            try:
                client = await self._stack.enter_async_context(Client(target))
                self.clients[name] = client
                info.connected = True
                info.server_name = client.server_info.name if client.server_info else name
                await self._discover(name)
            except Exception as exc:  # keep going: one broken server shouldn't sink the demo
                root = _root_cause(exc)
                info.error = f"{type(root).__name__}: {root}"
                if info.transport == "http":
                    info.error = (
                        f"Can't reach {spec['url']} ({info.error}). Is the tracker running in Terminal 1 "
                        "with TRACKER_HTTP=1? Start it, then restart the agent."
                    )
                print(f"[hub] could not start {name}: {info.error}", file=sys.stderr)

    async def stop(self) -> None:
        self._stop.set()
        for fut in self._callbacks.values():
            if not fut.done():
                fut.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._stack:
            await self._stack.aclose()

    # -- OAuth servers (slide 69) -------------------------------------------
    async def connect(self, name: str, interactive: bool = True) -> dict[str, Any]:
        """Open the client for an OAuth server. With interactive=True a missing or expired
        sign-in produces a sign-in URL for the browser; otherwise it just reports it."""
        info = self.servers[name]
        task = self._tasks.get(name)
        if info.connected:
            return {"connected": True}
        if task and not task.done():
            if info.sign_in_url:
                return {"sign_in_url": info.sign_in_url}
        else:
            info.error, info.sign_in_url = None, None
            self._progress[name] = asyncio.Event()
            self._tasks[name] = asyncio.create_task(self._run_oauth(name, interactive))
        try:
            await asyncio.wait_for(self._progress[name].wait(), timeout=45)
        except asyncio.TimeoutError:
            info.error = "Timed out talking to the server."
        if info.connected:
            return {"connected": True}
        if info.sign_in_url:
            return {"sign_in_url": info.sign_in_url}
        return {"error": info.error or "Sign-in did not start."}

    def complete_sign_in(self, code: str, state: str | None, iss: str | None) -> bool:
        """Called by the /oauth/callback page with what the identity provider sent back."""
        for fut in self._callbacks.values():
            if not fut.done():
                fut.set_result(AuthorizationCodeResult(code=code, state=state, iss=iss))
                return True
        return False

    async def _run_oauth(self, name: str, interactive: bool) -> None:
        """Owns one OAuth server's connection for the life of the app (one task, so the
        connection is opened and closed in the same task)."""
        spec, info = self.config[name], self.servers[name]
        loop = asyncio.get_running_loop()

        async def redirect_handler(url: str) -> None:
            # 02 FIND + 03 SIGN IN: the server said 401; the SDK found the identity provider,
            # registered this app, and built the consent URL. The browser has to open it.
            if not interactive:
                raise SignInRequired()
            info.needs_sign_in = True
            info.sign_in_url = url
            self._progress[name].set()

        async def callback_handler() -> AuthorizationCodeResult:
            fut: asyncio.Future[AuthorizationCodeResult] = loop.create_future()
            self._callbacks[name] = fut
            try:
                return await asyncio.wait_for(fut, timeout=600)
            finally:
                self._callbacks.pop(name, None)

        redirect_uri = f"{self.public_url}/oauth/callback"
        provider = OAuthClientProvider(
            server_url=spec["url"],
            client_metadata=OAuthClientMetadata(
                client_name=CLIENT_NAME,
                redirect_uris=[redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
            ),
            storage=FileTokenStorage(SECRETS / f"{name}-oauth.json", redirect_uri),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        try:
            async with AsyncExitStack() as stack:
                http = await stack.enter_async_context(httpx2.AsyncClient(auth=provider, timeout=httpx2.Timeout(30, read=300)))
                client = await stack.enter_async_context(Client(streamable_http_client(spec["url"], http_client=http)))
                self.clients[name] = client
                info.server_name = client.server_info.name if client.server_info else name
                await self._discover(name)
                info.connected, info.needs_sign_in, info.sign_in_url, info.error = True, False, None, None
                self._progress[name].set()
                await self._stop.wait()  # 04 CALL: stay connected until the app stops
        except Exception as exc:
            root = _root_cause(exc)
            if isinstance(root, SignInRequired):
                info.needs_sign_in, info.error = True, "Saved sign-in has expired. Click Connect to sign in again."
            elif not self._stop.is_set():
                info.error = f"{type(root).__name__}: {root}"
                print(f"[hub] {name}: {info.error}", file=sys.stderr)
        finally:
            self.clients.pop(name, None)
            info.connected = False
            if name in self._progress:
                self._progress[name].set()

    # -- discovery ----------------------------------------------------------
    async def _discover(self, name: str) -> None:
        client, info = self.clients[name], self.servers[name]
        spec = self.config.get(name, {})
        allow = set(spec.get("allowTools", []))
        read_only_override = set(spec.get("readOnlyTools", []))
        tools = await client.list_tools()
        self._log(name, "tools/list", {}, {"tools": [t.name for t in tools.tools]})
        offered = tools.tools
        kept = [t for t in offered if not allow or t.name in allow]
        info.hidden_tools = len(offered) - len(kept)
        info.tools = [
            ToolInfo(
                server=name,
                name=t.name,
                title=t.title,
                description=t.description or "",
                input_schema=t.input_schema,
                # No readOnlyHint means "may change data", so it waits for approval.
                read_only=t.name in read_only_override or bool(t.annotations and t.annotations.read_only_hint),
            )
            for t in kept
        ]
        try:
            res = await client.list_resources()
            tmpl = await client.list_resource_templates()
            info.resources = [{"uri": str(r.uri), "name": r.name, "description": r.description} for r in res.resources]
            info.resources += [{"uri": t.uri_template, "name": t.name, "description": t.description, "template": True} for t in tmpl.resource_templates]
        except Exception:
            info.resources = []
        try:
            prompts = await client.list_prompts()
            info.prompts = [
                {"name": p.name, "description": p.description, "arguments": [a.name for a in (p.arguments or [])]}
                for p in prompts.prompts
            ]
        except Exception:
            info.prompts = []

    # -- what the LLM sees ------------------------------------------------
    def all_tools(self) -> list[ToolInfo]:
        return [t for s in self.servers.values() if s.connected for t in s.tools]

    def tool(self, qualified: str) -> ToolInfo | None:
        return next((t for t in self.all_tools() if t.qualified == qualified), None)

    def is_connected(self, name: str) -> bool:
        info = self.servers.get(name)
        return bool(info and info.connected)

    def anthropic_tools(self, allow_writes: bool = True) -> list[dict[str, Any]]:
        """Turn MCP tool definitions into the model's list of available actions."""
        out = []
        for t in self.all_tools():
            if not allow_writes and not t.read_only:
                continue  # role-based access: a read-only agent never even sees write tools
            out.append(
                {
                    "name": t.qualified,
                    "description": f"[{t.server} server] {t.description}",
                    "input_schema": t.input_schema,
                    "eager_input_streaming": True,
                }
            )
        return out

    # -- routing ----------------------------------------------------------
    async def call(self, qualified: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server, _, tool = qualified.partition(SEP)
        client = self.clients.get(server)
        if client is None:
            return {"is_error": True, "text": f"Server {server!r} is not connected", "structured": None}
        started = time.perf_counter()
        result = await client.call_tool(tool, arguments)
        ms = round((time.perf_counter() - started) * 1000)
        text = "\n".join(getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text")
        out = {"is_error": bool(result.is_error), "text": text, "structured": result.structured_content, "ms": ms}
        self._log(
            server,
            "tools/call",
            {"name": tool, "arguments": arguments},
            {"content": [{"type": "text", "text": text[:600]}], "structuredContent": result.structured_content, "isError": bool(result.is_error)},
            ms,
        )
        return out

    async def read_resource(self, server: str, uri: str) -> str:
        result = await self.clients[server].read_resource(uri)
        text = "\n".join(getattr(c, "text", "") or "" for c in result.contents)
        self._log(server, "resources/read", {"uri": uri}, {"contents": [{"uri": uri, "text": text[:600]}]})
        return text

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str]) -> str:
        result = await self.clients[server].get_prompt(name, arguments)
        parts = []
        for m in result.messages:
            c = m.content
            parts.append(getattr(c, "text", "") if getattr(c, "type", "") == "text" else str(c))
        text = "\n\n".join(parts)
        self._log(server, "prompts/get", {"name": name, "arguments": arguments}, {"messages": [{"role": "user", "text": text[:600]}]})
        return text

    # -- wire log ---------------------------------------------------------
    def _log(self, server: str, method: str, params: dict[str, Any], result: dict[str, Any], ms: int | None = None) -> None:
        self._seq += 1
        self.wire.append(
            {
                "seq": self._seq,
                "at": time.strftime("%H:%M:%S"),
                "server": server,
                "ms": ms,
                "request": {"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params},
                "response": {"jsonrpc": "2.0", "id": self._seq, "result": result},
            }
        )
        del self.wire[:-200]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "server_name": s.server_name,
                "transport": "http + oauth" if s.auth == "oauth" else s.transport,
                "command": s.command if s.transport == "http" else " ".join([Path(s.command).name, *[str(Path(a).relative_to(LAB)) if a.startswith(str(LAB)) else a for a in s.args]]),
                "connected": s.connected,
                "auth": s.auth,
                "needs_sign_in": s.needs_sign_in and not s.connected,
                "error": s.error,
                "hidden_tools": s.hidden_tools,
                "tools": [
                    {"name": t.name, "qualified": t.qualified, "title": t.title, "description": t.description, "read_only": t.read_only, "input_schema": t.input_schema}
                    for t in s.tools
                ],
                "resources": s.resources,
                "prompts": s.prompts,
            }
            for s in self.servers.values()
        ]
