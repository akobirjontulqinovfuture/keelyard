"""The agent interface, end to end: a scripted MCP client + Claude-style hooks complete a task."""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from keelyard import selftest


def test_scripted_agent_completes_full_workflow():
    rep = selftest.run()
    failed = [f"{c.name}: {c.detail}" for c in rep.checks if not c.ok]
    assert not failed, failed
    assert len(rep.checks) >= 10


async def _tools(project, which="all"):
    env = {**os.environ, "KEELYARD_MCP_TOOLS": which} if which else {k: v for k, v in os.environ.items() if k != "KEELYARD_MCP_TOOLS"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=str(project), env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return (await s.list_tools()).tools


def test_every_tool_and_parameter_is_described(project):
    tools = asyncio.run(_tools(project))
    assert len(tools) >= 20
    for t in tools:
        assert t.description and len(t.description) > 40, t.name
        ann = getattr(t, "annotations", None)
        assert ann is not None and ann.title, t.name
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        for name, prop in schema.get("properties", {}).items():
            assert prop.get("description"), f"{t.name}.{name} has no description"
        out = getattr(t, "output_schema", None) or getattr(t, "outputSchema", None)
        assert out is not None, f"{t.name} has no output schema"


def test_default_toolset_is_small_and_cheap(project):
    """Every schema is sent in every session: the default exposes only the workflow tools, without pydantic titles."""
    import json

    tools = asyncio.run(_tools(project, None))
    names = {t.name for t in tools}
    assert names == {"proof_requirements", "proof_check", "proof_run_tests", "proof_status", "rewind_list", "rewind_to",
                     "read_file", "run_compact", "recall", "find_symbol", "memory_search", "swarm", "workflow", "work_state"}
    size = sum(len(json.dumps(t.model_dump(exclude_none=True, by_alias=True))) for t in tools) // 4
    every = sum(len(json.dumps(t.model_dump(exclude_none=True, by_alias=True))) for t in asyncio.run(_tools(project))) // 4
    assert size < every * 0.7, (size, every)
    assert '"title": "Path"' not in json.dumps([t.model_dump(by_alias=True) for t in tools])


def test_workflow_tool_returns_guides(project):
    from keelyard import mcp_server

    out = mcp_server.workflow("debug")
    assert "Reproduce" in out["steps"]


def test_lazy_toolset_is_smaller_and_still_reaches_every_tool(project):
    """Mechanism 6: only a few schemas up front; the rest is discoverable and callable, and never costs more."""
    import json

    def size(ts):
        return sum(len(json.dumps(t.model_dump(exclude_none=True, by_alias=True))) for t in ts) // 4

    lazy, core = asyncio.run(_tools(project, "lazy")), asyncio.run(_tools(project, None))
    assert {t.name for t in lazy} == {"read_file", "run_compact", "find_symbol", "proof_status", "tool_search", "tool_call"}
    assert size(lazy) < size(core) * 0.6, (size(lazy), size(core))

    async def flow():
        env = {**os.environ, "KEELYARD_MCP_TOOLS": "lazy"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=str(project), env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            found = json.loads((await s.call_tool("tool_search", {"query": "outline signatures of a file"})).content[0].text)
            ran = json.loads((await s.call_tool("tool_call", {"name": "outline", "arguments": {"path": "app.py"}})).content[0].text)
            bad = await s.call_tool("tool_call", {"name": "tool_call", "arguments": {}})
            return found, ran, bad
    found, ran, bad = asyncio.run(flow())
    assert found["tools"][0]["name"] == "outline" and "properties" in found["tools"][0]["input_schema"]
    assert "def add" in ran["outline"]
    assert getattr(bad, "is_error", None) or getattr(bad, "isError", None)


def test_lazy_falls_back_to_core_if_not_smaller(monkeypatch):
    from keelyard import mcp_server

    monkeypatch.setattr(mcp_server, "LAZY", mcp_server.LAZY + tuple(n for n in mcp_server._ALL or () if n not in mcp_server.META))
    mcp_server.configure_tools("core")
    monkeypatch.setattr(mcp_server, "LAZY", tuple(mcp_server._ALL))
    names = mcp_server.configure_tools("lazy")
    assert set(names) == set(mcp_server.CORE)
    mcp_server.configure_tools("all")


def test_handshake_reports_keelyard_version(project):
    from keelyard import __version__

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=str(project), env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            res = await s.initialize()
            return getattr(res, "server_info", None) or res.serverInfo
    info = asyncio.run(go())
    assert info.name == "keelyard" and info.version == __version__
