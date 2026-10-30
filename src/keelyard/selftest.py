"""`keelyard selftest`: a scripted agent completes a full task using only the agent interface.

It plays the part of Claude Code: hooks are fed the same JSON on stdin that Claude Code
sends, and every other action goes through the MCP server over stdio. Nothing touches
your project or your agent logs: it runs in a temporary folder with a temporary HOME, and
the helper model is a tiny local OpenAI-compatible stub that answers with a citation.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CART = "def total(prices, discount=0):\n    return sum(prices)\n"
# plain asserts, so the self-test needs nothing but Python (no pytest in a fresh install)
TEST = ("from cart import total\n\n"
        "assert total([1, 2]) == 3, 'sum'\n"
        "assert total([10, 10], discount=0.1) == 18, 'discount'\n"
        "print('2 checks passed')\n")
FIXED = "def total(prices, discount=0):\n    return round(sum(prices) * (1 - discount), 2)\n"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"choices": [{"message": {"content": "The sum is computed at cart.py:2 `return`"}}],
                           "usage": {"prompt_tokens": 40, "completion_tokens": 9}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _setup(tmp: Path, port: int) -> tuple[Path, dict]:
    proj = tmp / "shop"
    proj.mkdir()
    (proj / "cart.py").write_text(CART, encoding="utf-8")
    (proj / "check_cart.py").write_text(TEST, encoding="utf-8")
    env = {**os.environ, "HOME": str(tmp / "home"), "XDG_DATA_HOME": str(tmp / "xdg"), "KEELYARD_ROOT": str(proj),
           "CLAUDE_CONFIG_DIR": str(tmp / "home" / ".claude"), "CODEX_HOME": str(tmp / "home" / ".codex"),
           "GIT_CONFIG_NOSYSTEM": "1", "NO_COLOR": "1"}
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
    (proj / ".keelyard").mkdir()
    (proj / ".keelyard" / "config.toml").write_text(
        f'[proof]\ntest_cmd = "{Path(sys.executable).as_posix()} check_cart.py"\n'
        f'[models.cheap]\nprovider = "openai"\nbase_url = "http://127.0.0.1:{port}/v1"\nmodel = "stub"\n', encoding="utf-8")
    # a past session for memory_search to find
    past = tmp / "home" / ".claude" / "projects" / "shop"
    past.mkdir(parents=True)
    (past / "old.jsonl").write_text(json.dumps({
        "type": "assistant", "sessionId": "old", "cwd": str(proj), "timestamp": "2026-09-01T10:00:00Z",
        "message": {"role": "assistant", "content": "Rounding bug: always round money with round(x, 2) after the discount."}}) + "\n",
        encoding="utf-8")
    return proj, env


def _hook(event: str, payload: dict, env: dict, proj: Path) -> dict | None:
    p = subprocess.run([sys.executable, "-m", "keelyard.cli", "hook", "claude", event], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=proj, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"hook {event} exited {p.returncode}: {p.stderr[-300:]}")
    return json.loads(p.stdout) if p.stdout.strip() else None


async def _agent(proj: Path, env: dict, rep: Report) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    sid = "selftest"
    base = {"session_id": sid, "cwd": str(proj)}

    def check(name, ok, detail="", t0=None):
        rep.checks.append(Check(name, bool(ok), detail, round(1000 * (time.perf_counter() - t0), 1) if t0 else 0.0))

    params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=str(proj), env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}

        async def call(name, args=None):
            res = await s.call_tool(name, args or {})
            if getattr(res, "is_error", None) or getattr(res, "isError", None):
                raise RuntimeError("".join(getattr(c, "text", "") for c in res.content))
            sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
            if sc is not None:
                return sc.get("result", sc) if set(sc) == {"result"} else sc
            return json.loads("".join(getattr(c, "text", "") for c in res.content))

        described = all(t.description and len(t.description) > 30 for t in tools.values())
        check("mcp: server starts and lists tools", len(tools) >= 12 and bool(init.instructions),
              f"{len(tools)} tools, all described" if described else "some tools lack descriptions")
        t0 = time.perf_counter()
        start = _hook("SessionStart", {**base, "source": "startup"}, env, proj) or {}
        brief = start.get("hookSpecificOutput", {}).get("additionalContext", "")
        _hook("UserPromptSubmit", {**base, "prompt": "Make total() apply the discount."}, env, proj)
        check("hooks: session start + prompt", (await call("rewind_list"))["total"] == 1 and "keelyard brief" in brief,
              f"baseline snapshot, {len(brief) // 4}-token brief injected", t0)

        t0 = time.perf_counter()
        req = await call("proof_requirements", {"items": ["total() applies the discount", "existing tests still pass"]})
        check("proof: requirements recorded", len(req["requirements"]) == 2, "", t0)

        t0 = time.perf_counter()
        found = await call("find_symbol", {"name": "total"})
        first = await call("read_file", {"path": "cart.py"})
        again = await call("read_file", {"path": "cart.py"})
        check("context: find_symbol + deduplicated read", found["matches"] and len(again["content"]) <= len(first["content"])
              and "sum(prices)" in first["content"], found["matches"][0] if found["matches"] else "no match", t0)

        t0 = time.perf_counter()
        mem = await call("memory_search", {"query": "round discount"})
        check("memory: finds the fix from a past session", any("round(x, 2)" in h["snippet"] for h in mem["hits"]),
              f"{len(mem['hits'])} hit(s)", t0)

        t0 = time.perf_counter()
        sw = await call("swarm", {"tasks": [{"kind": "ask", "prompt": "Where is the sum computed?", "files": ["cart.py"]}]})
        check("swarm: helper answer is citation-checked", sw.get("mode") == "ran" and sw.get("verified") == 1,
              f"{sw.get('verified')}/{sw.get('total')} verified", t0)

        # a wrong edit first, so there is something to rewind
        t0 = time.perf_counter()
        (proj / "cart.py").write_text("def total(prices, discount=0):\n    return 0  # oops\n", encoding="utf-8")
        _hook("PostToolUse", {**base, "tool_name": "Write", "tool_input": {"file_path": "cart.py"}}, env, proj)
        bad = await call("rewind_list", {})
        block = _hook("Stop", {**base}, env, proj)
        check("proof gate: blocks 'done' without evidence", block and block.get("decision") == "block",
              (block or {}).get("reason", "").splitlines()[1][:70] if block else "not blocked", t0)

        t0 = time.perf_counter()
        back = await call("rewind_to", {"step": bad["steps"][0]["step"]})
        restored = (proj / "cart.py").read_text(encoding="utf-8") == CART
        check("rewind: wrong edit undone", restored, f"restored #{back['restored']}, undo with #{back['safety_step']}", t0)

        t0 = time.perf_counter()
        (proj / "cart.py").write_text(FIXED, encoding="utf-8")
        _hook("PostToolUse", {**base, "tool_name": "Edit", "tool_input": {"file_path": "cart.py"}}, env, proj)
        run = await call("proof_run_tests", {"phase": "after"})
        await call("proof_check", {"requirement_id": 1, "evidence": "test_discount passes: total([10,10],0.1)==18"})
        await call("proof_check", {"requirement_id": 2, "evidence": f"pytest exit {run['exit']}: {run['output'][-60:].strip()}"})
        st = await call("proof_status")
        done = _hook("Stop", {**base}, env, proj)
        check("proof: tests pass, gate lets the agent finish", run["passed"] and st["proven"] and done is None,
              f"exit {run['exit']}, report {Path(st['report']).name}", t0)

        t0 = time.perf_counter()
        noisy = await call("run_compact", {"command": f"{sys.executable} check_cart.py"})
        check("context: run_compact output", noisy["exit"] == 0 and "2 checks passed" in noisy["output"], f"{len(noisy['output'])} chars", t0)


def run(keep: bool = False) -> Report:
    rep = Report()
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="keelyard-selftest-"))
    try:
        proj, env = _setup(tmp, srv.server_address[1])
        try:
            asyncio.run(_agent(proj, env, rep))
        except Exception as e:  # noqa: BLE001
            err: BaseException = e
            while isinstance(err, BaseExceptionGroup) and err.exceptions:
                err = err.exceptions[0]
            rep.checks.append(Check("selftest aborted", False, f"{type(err).__name__}: {err}"[:400]))
    finally:
        srv.shutdown()
        if not keep:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
    rep.workdir = str(tmp)  # type: ignore[attr-defined]
    return rep
