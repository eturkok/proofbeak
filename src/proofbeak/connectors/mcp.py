"""An actual MCP client for operator-registered stdio or HTTP target tools."""

import asyncio
import json
import os
import re
import threading
from contextlib import AsyncExitStack
from datetime import timedelta
from urllib.parse import urlsplit

from proofbeak.adapters.base import ProviderError
from proofbeak.capabilities import matches
from proofbeak.connectors import ScopedConnector
from proofbeak.connectors.rest import template


class MCPConnector(ScopedConnector):
    def __init__(self, name, settings, secrets=None):
        super().__init__(name, settings, secrets)
        if set(settings) - {"kind", "connection", "operations", "lifecycle", "provider_version"}:
            raise ValueError("unknown MCP profile field")
        connection = settings["connection"]
        if connection.get("transport") == "stdio":
            if set(connection) - {"transport", "command", "args", "secret_env"} or \
                    not isinstance(connection.get("command"), str) or not os.path.isabs(connection["command"]) or \
                    not isinstance(connection.get("args", []), list) or any(not isinstance(a, str) for a in connection.get("args", [])):
                raise ValueError("MCP stdio requires an operator-owned absolute executable and argument list")
        elif connection.get("transport") == "http":
            if set(connection) - {"transport", "url", "secret_env"}:
                raise ValueError("unknown MCP HTTP connection field")
            url = urlsplit(connection.get("url", ""))
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.fragment or \
                    url.query or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError("MCP HTTP requires HTTPS or loopback HTTP")
        else:
            raise ValueError("MCP transport must be stdio or http")
        for definition in self.catalog.operations.values():
            binding = definition["binding"]
            if set(binding) - {"tool", "arguments", "project"} or not isinstance(binding.get("tool"), str) or \
                    not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", binding["tool"]):
                raise ValueError("invalid MCP tool binding")
            if not definition["read_only"] and {"scope": True} not in self._nodes(binding.get("arguments", {})):
                raise ValueError("MCP mutations must carry the operator-injected scope")
        self.configure_lifecycle()
        self.thread, self.session, self.loop, self.stop = None, None, None, None

    @staticmethod
    def _nodes(value):
        result = [value]
        if isinstance(value, dict):
            for item in value.values():
                result.extend(MCPConnector._nodes(item))
        elif isinstance(value, list):
            for item in value:
                result.extend(MCPConnector._nodes(item))
        return result

    def _start(self):
        if self.thread:
            return
        ready = threading.Event()
        self.failure = False
        async def serve():
            try:
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
                async with AsyncExitStack() as stack:
                    c = self.settings["connection"]
                    if c["transport"] == "stdio":
                        log = stack.enter_context(open(os.devnull, "w"))
                        params = StdioServerParameters(command=c["command"], args=c.get("args", []),
                            env={"PATH": os.defpath, "PYTHONUTF8": "1", **self.secrets})
                        streams = await stack.enter_async_context(stdio_client(params, errlog=log))
                    else:
                        from mcp.client.streamable_http import streamablehttp_client
                        streams = await stack.enter_async_context(streamablehttp_client(c["url"], headers=self.secrets,
                            timeout=timedelta(seconds=5), sse_read_timeout=timedelta(seconds=5)))
                    self.session = await stack.enter_async_context(ClientSession(streams[0], streams[1],
                                                                  read_timeout_seconds=timedelta(seconds=5)))
                    await self.session.initialize()
                    self.tools, cursor = {}, None
                    while True:
                        page = await self.session.list_tools(cursor=cursor)
                        for tool in page.tools:
                            if tool.name in self.tools or len(self.tools) >= 128:
                                raise ValueError("unbounded or duplicate MCP discovery")
                            self.tools[tool.name] = tool.inputSchema
                        cursor = page.nextCursor
                        if not cursor:
                            break
                    self.loop, self.stop = asyncio.get_running_loop(), asyncio.Event()
                    ready.set()
                    await self.stop.wait()
            except BaseException:
                self.failure = True
                ready.set()
        self.thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
        self.thread.start()
        if not ready.wait(8) or self.failure:
            self.close()
            raise ProviderError("mcp_connection_failed")

    def close(self):
        if self.loop and self.stop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop.set)
        if self.thread:
            self.thread.join(timeout=6)
        self.thread, self.session, self.loop, self.stop = None, None, None, None

    def cleanup(self, resource):
        try:
            super().cleanup(resource)
        finally:
            self.close()

    def _invoke(self, resource, operation, arguments):
        self._start()
        definition = self.catalog.validate(operation, arguments)
        binding = definition["binding"]
        payload = template(binding.get("arguments", {}), arguments, self.scope(resource))
        if binding["tool"] not in self.tools or not matches(self.tools[binding["tool"]], payload):
            raise ProviderError("mcp_tool_schema_mismatch")
        async def call():
            return await self.session.call_tool(binding["tool"], arguments=payload)
        future = asyncio.run_coroutine_threadsafe(call(), self.loop)
        try:
            result = future.result(timeout=7)
        except Exception:
            future.cancel()
            raise ProviderError("observation_unavailable" if definition["read_only"] else "write_outcome_unknown") from None
        if result.isError:
            raise ProviderError("mcp_tool_failed")
        data = result.structuredContent
        if data is None:
            try:
                if len(result.content) != 1 or result.content[0].type != "text":
                    raise ValueError("expected JSON text")
                data = json.loads(result.content[0].text)
            except (ValueError, AttributeError):
                raise ProviderError("invalid_observation") from None
        if not isinstance(data, dict):
            raise ProviderError("invalid_observation")
        projection = binding.get("project")
        if projection:
            from proofbeak.packs.scenario import resolve
            projected = {}
            for key, parts in projection.items():
                try:
                    projected[key] = resolve({"ref": parts}, data)
                except ProviderError:
                    pass
            return projected
        return data
