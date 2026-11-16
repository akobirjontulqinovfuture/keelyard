"""Generate docs/cli.md and docs/mcp.md from the code, so the reference never drifts.

    python docs/demo/gen_reference.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def help_of(*args: str) -> str:
    env = {**os.environ, "COLUMNS": "100", "NO_COLOR": "1"}
    return subprocess.run([sys.executable, "-m", "keelyard.cli", *args, "--help"], capture_output=True, text=True, env=env).stdout.strip()


def cli() -> None:
    groups = {"init": [], "doctor": [], "status": [], "selftest": [], "rewind": ["list", "show", "to", "fork", "snap"],
              "proof": ["status", "add", "check", "test", "shot", "report"], "run": [], "filter": [], "recall": [],
              "code": ["outline", "map", "find"], "memory": ["index", "search", "stats"], "usage": [],
              "audit": [], "risk": [], "watch": [], "budget": [], "ci": [], "swarm": ["run"],
              "arena": ["run", "render"], "bench": [], "install": [], "uninstall": []}
    md = ["# CLI reference", "", "Generated from `keelyard --help` by `docs/demo/gen_reference.py`.", "",
          "Every command accepts `--json` and prints exactly one JSON object with `ok` and `exit` keys.", "",
          "```text", help_of(), "```", ""]
    for g, subs in groups.items():
        md += [f"## {g}", "", "```text", help_of(g), "```", ""]
        for s in subs:
            md += [f"### {g} {s}", "", "```text", help_of(g, s), "```", ""]
    (ROOT / "docs" / "cli.md").write_text("\n".join(md))


async def mcp() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    with tempfile.TemporaryDirectory() as t:
        subprocess.run(["git", "init", "-q"], cwd=t)
        params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=t, env={**os.environ})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            init = await s.initialize()
            tools = (await s.list_tools()).tools
    md = ["# MCP reference", "", "Generated from the running server by `docs/demo/gen_reference.py`.", "",
          "The server is `keelyard mcp` (stdio). It sends these instructions to the agent on connect:", "",
          "```text", init.instructions, "```", "",
          "Every tool returns a JSON object (MCP structured content). Errors are MCP tool errors whose text "
          "says what went wrong and, where there is one, a `Hint:` with the fix.", "",
          "These are the default (`core`) tools. `[mcp].tools = \"all\"` adds the rest; `\"lazy\"` sends only "
          "`read_file`, `run_compact`, `find_symbol` and `proof_status` plus `tool_search` (find a hidden tool and its "
          "schema) and `tool_call` (run it), and falls back to `core` if that would not be smaller.", "",
          "| tool | read-only | destructive | summary |", "|---|---|---|---|"]
    for t in tools:
        a = t.annotations
        ro = getattr(a, "read_only_hint", None) or getattr(a, "readOnlyHint", None)
        de = getattr(a, "destructive_hint", None) or getattr(a, "destructiveHint", None)
        md.append(f"| [`{t.name}`](#{t.name}) | {'yes' if ro else ''} | {'yes' if de else ''} | {t.description.splitlines()[0]} |")
    for t in tools:
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        md += ["", f"## {t.name}", "", t.description.strip(), ""]
        props = schema.get("properties", {})
        if props:
            md += ["| parameter | type | default | description |", "|---|---|---|---|"]
            req = set(schema.get("required", []))
            for name, p in props.items():
                typ = p.get("type") or " | ".join(x.get("type", "?") for x in p.get("anyOf", [])) or ("enum" if "enum" in p else "")
                if "enum" in p:
                    typ = " / ".join(json.dumps(v) for v in p["enum"])
                default = "required" if name in req else json.dumps(p.get("default"))
                md.append(f"| `{name}` | {typ} | {default} | {p.get('description', '')} |")
        else:
            md.append("No parameters.")
    (ROOT / "docs" / "mcp.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    cli()
    asyncio.run(mcp())
    print("wrote docs/cli.md and docs/mcp.md")
