"""The feature matrix: every CLI command and flag, every hook event per agent, every adapter.

Each case runs the real `keelyard` entry point in a subprocess against a throwaway project,
then checks the exit code and the --json payload. Used by tests/test_matrix.py and by
bench/feature_matrix.py, which writes docs/feature-matrix.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

KY = [sys.executable, "-m", "keelyard.cli"]


@dataclass
class Case:
    area: str
    name: str
    argv: list[str]
    exit: int = 0
    check: Callable[[dict, Path], bool] | None = None
    stdin: str | None = None
    hook: bool = False  # hooks print agent JSON (or nothing), not the --json envelope


def make_project(tmp: Path) -> tuple[Path, dict]:
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("def add(a, b):\n    return a + b\n\n\nclass Cart:\n    pass\n")
    (proj / "test_app.py").write_text("from app import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    home = tmp / "home"
    (home / ".claude" / "projects" / "p").mkdir(parents=True)
    (home / ".claude" / "projects" / "p" / "s.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "s", "cwd": str(proj), "timestamp": "2026-09-01T00:00:00Z",
         "message": {"role": "user", "content": "flaky websocket reconnect"}}) + "\n" + json.dumps(
        {"type": "assistant", "sessionId": "s", "cwd": str(proj), "timestamp": "2026-09-01T00:00:01Z",
         "message": {"role": "assistant", "model": "m1", "content": "done", "usage": {"input_tokens": 10, "output_tokens": 5}}}) + "\n")
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(tmp / "xdg"), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
           "CODEX_HOME": str(home / ".codex"), "NO_COLOR": "1", "KEELYARD_ROOT": str(proj)}
    env.pop("FORCE_COLOR", None)
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True, env=env)
    return proj, env


def has(*keys):
    return lambda d, p: all(k in d for k in keys)


def ev(event, **payload):
    return json.dumps({"session_id": "m", **payload})


CASES: list[Case] = [
    # setup
    Case("setup", "init --dry-run changes nothing", ["init", "--dry-run", "--json"],
         check=lambda d, p: d["dry_run"] and not (p / ".keelyard").exists()),
    Case("setup", "init --no-agents", ["init", "--no-agents", "--json"], check=lambda d, p: d["snapshot"]["step"] == 1),
    Case("setup", "init --agent claude (hooks, MCP, skill, commands, CLAUDE.md)", ["init", "--agent", "claude", "--json"],
         check=lambda d, p: (p / ".claude/skills/keelyard/SKILL.md").exists() and (p / ".claude/commands/keelyard/prove.md").exists()
         and "keelyard:start" in (p / "CLAUDE.md").read_text() and (p / ".mcp.json").exists()),
    Case("setup", "init twice is idempotent", ["init", "--agent", "claude", "--json"],
         check=lambda d, p: (p / "CLAUDE.md").read_text().count("keelyard:start") == 1
         and json.loads((p / ".claude/settings.json").read_text())["hooks"]["Stop"].__len__() == 1),
    Case("setup", "install codex", ["install", "codex", "--json"],
         check=lambda d, p: (p / ".codex/hooks.json").exists() and "keelyard:start" in (p / "AGENTS.md").read_text()),
    Case("setup", "install opencode", ["install", "opencode", "--json"],
         check=lambda d, p: (p / ".opencode/plugins/keelyard.js").exists() and "const MCP" in (p / ".opencode/plugins/keelyard.js").read_text()),
    Case("setup", "install unknown agent is a usage error", ["install", "cursor", "--json"], exit=2),
    Case("setup", "doctor", ["doctor", "--json"], check=lambda d, p: {c["check"] for c in d["checks"]} >= {"python", "git", "claude"}),
    Case("setup", "status", ["status", "--json"], check=lambda d, p: d["agents"]["claude"] and d["steps"] >= 1),
    # rewind
    Case("rewind", "snap picks up the installed files", ["rewind", "snap", "--json"], check=lambda d, p: d["step"] is not None),
    Case("rewind", "snap with no change", ["rewind", "snap", "--json"], check=lambda d, p: d["step"] is None),
    Case("rewind", "snap after an edit", ["rewind", "snap", "--json"], check=lambda d, p: d["step"]["files"] == ["app.py"]),
    Case("rewind", "list", ["rewind", "list", "--json"], check=lambda d, p: d["total"] >= 2),
    Case("rewind", "list -n 1", ["rewind", "list", "-n", "1", "--json"], check=lambda d, p: len(d["steps"]) == 1),
    Case("rewind", "show", ["rewind", "show", "4", "--json"], check=lambda d, p: "+# edited" in d["diff"]),
    Case("rewind", "fork", ["rewind", "fork", "1", "../fork", "--json"], check=lambda d, p: (p.parent / "fork" / "app.py").exists()),
    Case("rewind", "to", ["rewind", "to", "1", "--json"], check=lambda d, p: "# edited" not in (p / "app.py").read_text()),
    Case("rewind", "to an unknown step", ["rewind", "to", "999", "--json"], exit=1, check=lambda d, p: "rewind list" in d["hint"]),
    Case("rewind", "show a non-number", ["rewind", "show", "x", "--json"], exit=2),
    # proof
    Case("proof", "add", ["proof", "add", "adds numbers", "has a Cart", "--json"], check=lambda d, p: len(d["requirements"]) == 2),
    Case("proof", "status (open) exits 3", ["proof", "status", "--json"], exit=3, check=lambda d, p: not d["proven"]),
    Case("proof", "check needs evidence", ["proof", "check", "1", " ", "--json"], exit=1),
    Case("proof", "check unknown id", ["proof", "check", "9", "x", "--json"], exit=1),
    Case("proof", "check", ["proof", "check", "1", "test_add passes", "--json"], check=lambda d, p: d["done"]),
    Case("proof", "test after (pass)", ["proof", "test", "after", "--cmd", f"{sys.executable} -m pytest -q -p no:cacheprovider", "--json"],
         check=lambda d, p: d["run"]["exit"] == 0),
    Case("proof", "test with a missing tool exits 3", ["proof", "test", "after", "--cmd", "no-such-tool-xyz", "--json"], exit=3,
         check=lambda d, p: "not found" in d["run"]["tail"]),
    Case("proof", "shot without url", ["proof", "shot", "--json"], exit=1),
    Case("proof", "report", ["proof", "report", "--json"], check=lambda d, p: Path(d["report"]).exists()),
    # context saver
    Case("context", "run (pass-through exit)", ["run", "--json", "--", sys.executable, "-c", "print('hi')"],
         check=lambda d, p: "hi" in d["output"]),
    Case("context", "run a failing command", ["run", "--json", "--", sys.executable, "-c", "import sys; sys.exit(4)"], exit=4),
    Case("context", "run a missing command", ["run", "--json", "--", "no-such-tool-xyz"], exit=127),
    Case("context", "run with nothing", ["run", "--json"], exit=2),
    Case("context", "filter --for", ["filter", "--for", "pytest -q", "--json"], stdin="x\n" * 10, check=has("output")),
    Case("context", "recall unknown id", ["recall", "abc", "--json"], exit=1),
    Case("context", "recall bad id", ["recall", "../etc", "--json"], exit=1),
    Case("context", "code outline", ["code", "outline", "app.py", "--json"], check=lambda d, p: "class Cart" in d["outline"]),
    Case("context", "code outline missing file", ["code", "outline", "nope.py", "--json"], exit=1),
    Case("context", "code map", ["code", "map", "--json"], check=lambda d, p: "app.py" in d["map"]),
    Case("context", "code find", ["code", "find", "Cart", "--json"], check=lambda d, p: d["matches"] and "app.py:5" in d["matches"][0]),
    # memory and usage
    Case("memory", "index", ["memory", "index", "--json"], check=lambda d, p: d["new_chunks"]["claude-code"] >= 1),
    Case("memory", "stats", ["memory", "stats", "--json"], check=lambda d, p: "claude-code" in d["agents"]),
    Case("memory", "search", ["memory", "search", "websocket", "--json"], check=lambda d, p: len(d["hits"]) == 1),
    Case("memory", "search with odd input", ["memory", "search", 'NEAR( "*', "--json"], check=has("hits")),
    Case("usage", "usage --by day", ["usage", "--json"], check=lambda d, p: d["rows"] and d["rows"][0]["input"] == 10),
    Case("usage", "usage --by model", ["usage", "--by", "model", "--json"], check=lambda d, p: d["rows"][0]["key"].endswith("m1")),
    Case("usage", "usage --by agent", ["usage", "--by", "agent", "--json"], check=has("rows")),
    Case("trust", "audit", ["audit", "--json"], check=lambda d, p: d["ok"] and d["claims"] == []),
    Case("trust", "risk", ["risk", "--json"], check=has("events")),
    Case("trust", "watch --once", ["watch", "--once", "--json"], check=lambda d, p: d["session"] and "proof" in d),
    Case("trust", "budget", ["budget", "--json"], check=has("tokens")),
    Case("trust", "ci --diff-only", ["ci", "--diff-only", "--json"], check=has("ok")),
    # swarm, arena, bench
    Case("swarm", "swarm run missing file", ["swarm", "run", "nope.json", "--json"], exit=1),
    Case("swarm", "swarm run bad JSON", ["swarm", "run", "app.py", "--json"], exit=1),
    Case("swarm", "swarm plan", ["swarm", "plan", "fix add for strings", "--json"], exit=0),
    Case("swarm", "swarm plan --subagents", ["swarm", "plan", "where is add defined", "--subagents"], exit=0),
    Case("arena", "arena run missing spec", ["arena", "run", "nope.toml", "--json"], exit=1),
    Case("arena", "arena render without results", ["arena", "render", "nowhere", "--json"], exit=1),
    Case("bench", "bench leaves the timeline alone", ["bench", "--json"], check=lambda d, p: d["snapshot"]["median_ms"] > 0),
    # cli basics
    Case("cli", "--version", ["--version"]),
    Case("cli", "no command prints help", []),
    Case("cli", "unknown command", ["frobnicate", "--json"], exit=2),
    Case("cli", "reinstall codex (rewind removed it)", ["install", "codex", "--json"]),
    Case("cli", "reinstall opencode", ["install", "opencode", "--json"]),
    Case("cli", "uninstall codex keeps AGENTS.md for opencode", ["uninstall", "codex", "--json"],
         check=lambda d, p: "keelyard:start" in (p / "AGENTS.md").read_text()),
    Case("cli", "uninstall opencode removes the AGENTS.md block", ["uninstall", "opencode", "--json"],
         check=lambda d, p: not (p / "AGENTS.md").exists()),
]

HOOKS: list[Case] = [
    Case("hooks", "claude SessionStart", ["hook", "claude", "SessionStart"], stdin=ev("SessionStart", source="startup"), hook=True),
    Case("hooks", "claude UserPromptSubmit", ["hook", "claude", "UserPromptSubmit"], stdin=ev("x", prompt="add a Cart"), hook=True),
    Case("hooks", "claude PostToolUse Read", ["hook", "claude", "PostToolUse"], hook=True,
         stdin=ev("x", tool_name="Read", tool_input={"file_path": "app.py"})),
    Case("hooks", "claude PreToolUse Read (repeat read denied)", ["hook", "claude", "PreToolUse"], hook=True,
         stdin=ev("x", tool_name="Read", tool_input={"file_path": "app.py"}),
         check=lambda d, p: d["hookSpecificOutput"]["permissionDecision"] == "deny"),
    Case("hooks", "claude PostToolUse Write (snapshot)", ["hook", "claude", "PostToolUse"], hook=True,
         stdin=ev("x", tool_name="Write", tool_input={"file_path": "app.py"})),
    Case("hooks", "claude PostToolUse Bash (output filtered)", ["hook", "claude", "PostToolUse"], hook=True,
         stdin=ev("x", tool_name="Bash", tool_input={"command": "pytest -v"},
                  tool_response={"stdout": "".join(f"test_{i} PASSED\n" for i in range(300)) + "FAILED test_x - boom\n",
                                 "stderr": "", "interrupted": False, "isImage": False}),
         check=lambda d, p: "FAILED test_x" in d["hookSpecificOutput"]["updatedToolOutput"]["stdout"]),
    Case("hooks", "claude PreCompact", ["hook", "claude", "PreCompact"], stdin=ev("x"), hook=True),
    Case("hooks", "claude Stop (blocks without proof)", ["hook", "claude", "Stop"], stdin=ev("x"), hook=True,
         check=lambda d, p: d["decision"] == "block"),
    Case("hooks", "codex PostToolUse apply_patch", ["hook", "codex", "PostToolUse"], hook=True,
         stdin=ev("x", tool_name="apply_patch", tool_input={})),
    # lean (the default) reruns the tests after the apply_patch above; where pytest runs and passes that is proof, so
    # Stop lets the agent finish. Without a working pytest, the gate must block.
    Case("hooks", "codex Stop", ["hook", "codex", "Stop"], stdin=ev("x"), hook=True,
         check=lambda d, p: d.get("decision") == "block" or (not d and _tests_pass(p))),
    Case("hooks", "opencode PostToolUse edit", ["hook", "opencode", "PostToolUse"], hook=True,
         stdin=json.dumps({"sessionID": "o", "tool": "edit", "args": {"filePath": "app.py"}})),
    Case("hooks", "unknown event is ignored", ["hook", "claude", "Future"], stdin="{}", hook=True),
    Case("hooks", "garbage stdin is ignored", ["hook", "claude", "Stop"], stdin="not json", hook=True),
    Case("hooks", "cwd outside any project is ignored", ["hook", "claude", "Stop"], stdin=json.dumps({"cwd": "/"}), hook=True),
]

EDITS = {"snap after an edit": lambda p: (p / "app.py").write_text((p / "app.py").read_text() + "# edited\n"),
         "claude PostToolUse Write (snapshot)": lambda p: (p / "app.py").write_text((p / "app.py").read_text() + "# hook edit\n")}


@dataclass
class Outcome:
    case: Case
    ok: bool
    exit: int
    ms: float
    note: str = ""


def run_case(c: Case, proj: Path, env: dict) -> Outcome:
    if c.name in EDITS:
        EDITS[c.name](proj)
    t0 = time.perf_counter()
    e = dict(env)
    if c.hook and "outside" in c.name:
        e.pop("KEELYARD_ROOT", None)
    p = subprocess.run(KY + c.argv, input=c.stdin or "", capture_output=True, text=True, cwd=proj, env=e, timeout=300)
    ms = round(1000 * (time.perf_counter() - t0))
    note = ""
    ok = p.returncode == c.exit
    if not ok:
        note = f"exit {p.returncode}, wanted {c.exit}: {(p.stderr or p.stdout)[-200:]}"
    data = None
    if ok and "Traceback" in p.stderr:
        ok, note = False, "traceback on stderr"
    if ok and ("--json" in c.argv or c.hook):
        try:
            data = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            ok, note = False, f"stdout is not JSON: {p.stdout[:120]!r}"
        if ok and not c.hook and (data.get("exit") != p.returncode or "ok" not in data):
            ok, note = False, "JSON envelope missing ok/exit"
    if ok and c.check:
        try:
            ok = bool(c.check(data or {}, proj))
        except Exception as ex:  # noqa: BLE001
            ok = False
            note = f"{type(ex).__name__}: {ex}"
        if not ok and not note:
            note = f"check failed: {p.stdout[:200]}"
    return Outcome(c, ok, p.returncode, ms, note)


def _tests_pass(proj: Path) -> bool:
    return subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=proj, capture_output=True).returncode == 0


def run_all(tmp: Path) -> list[Outcome]:
    proj, env = make_project(tmp)
    return [run_case(c, proj, env) for c in CASES + HOOKS]


# ---------------------------------------------------------------- MCP tools
MCP_CASES: list[tuple[str, dict, bool, Callable[[dict], bool]]] = [
    # (tool, arguments, expect_error, check)
    ("status", {}, False, lambda d: "steps" in d),
    ("workflow", {"name": "plan"}, False, lambda d: "proof_requirements" in d["steps"]),
    ("proof_requirements", {"items": ["adds numbers"]}, False, lambda d: d["requirements"][0]["id"] == 1),
    ("proof_requirements", {"items": []}, True, lambda d: True),
    ("proof_check", {"requirement_id": 1, "evidence": "test_add passes"}, False, lambda d: d["done"]),
    ("proof_check", {"requirement_id": 7, "evidence": "x"}, True, lambda d: True),
    ("proof_run_tests", {"phase": "after", "command": f"{sys.executable} -m pytest -q -p no:cacheprovider"}, False, lambda d: d["passed"]),
    ("proof_run_tests", {"phase": "later"}, True, lambda d: True),
    ("proof_screenshot", {}, True, lambda d: True),
    ("proof_status", {}, False, lambda d: "proven" in d and Path(d["report"]).exists()),
    ("rewind_list", {"last": 5}, False, lambda d: d["total"] >= 1),
    ("rewind_diff", {"step": 1}, False, lambda d: "app.py" in d["files"]),
    ("rewind_diff", {"step": 0}, True, lambda d: True),
    ("rewind_to", {"step": 1}, False, lambda d: d["restored"] == 1),
    ("read_file", {"path": "app.py"}, False, lambda d: "def add" in d["content"]),
    # app.py is tiny: resending it is cheaper than the "unchanged" notice, so either answer is right
    ("read_file", {"path": "app.py"}, False, lambda d: "unchanged" in d["content"] or "def add" in d["content"]),
    ("read_file", {"path": "../../etc/passwd"}, True, lambda d: True),
    ("read_file", {"path": ".keelyard/config.toml"}, True, lambda d: True),
    ("run_compact", {"command": f"{sys.executable} -c \"print('ok')\""}, False, lambda d: d["exit"] == 0 and "ok" in d["output"]),
    ("recall", {"output_id": "nope"}, True, lambda d: True),
    ("outline", {"path": "app.py"}, False, lambda d: "Cart" in d["outline"]),
    ("project_map", {}, False, lambda d: "app.py" in d["map"]),
    ("find_symbol", {"name": "add"}, False, lambda d: d["matches"]),
    ("usage_report", {"by": "model"}, False, lambda d: "rows" in d),
    ("route_task", {"task": "design the concurrency architecture", "n_files": 9}, False, lambda d: d["tier"] == "strong"),
    ("swarm", {"tasks": [{"kind": "ask", "prompt": "where is add?", "files": ["app.py"]}]}, False, lambda d: d["mode"] in ("plan", "ran")),
    ("swarm_plan", {"goal": "fix add so it handles strings"}, False,
     lambda d: d["tasks"][0]["files"] == ["app.py"] and d["tasks"][0]["symbols"] == ["add"] and d["subagent_tasks"]),
    ("swarm_verify", {"answer": "app.py:2 `return a + b`", "files": ["app.py"]}, False, lambda d: d["verified"]),
    ("swarm_verify", {"answer": "app.py:99 nothing", "files": ["app.py"]}, False, lambda d: not d["verified"]),
    ("memory_search", {"query": "websocket", "this_project_only": False}, False, lambda d: len(d["hits"]) >= 1),
]


async def run_mcp(proj: Path, env: dict) -> list[tuple[str, dict, bool, str]]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    out = []
    all_env = {**env, "KEELYARD_MCP_TOOLS": "all"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "keelyard.cli", "mcp"], cwd=str(proj), env=all_env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        for tool, args, want_err, check in MCP_CASES:
            res = await s.call_tool(tool, args)
            err = bool(getattr(res, "is_error", None) or getattr(res, "isError", None))
            sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None) or {}
            text = "".join(getattr(c, "text", "") for c in res.content)
            ok, note = err == want_err, ""
            if not ok:
                note = f"error={err}, wanted {want_err}: {text[:160]}"
            elif not err:
                try:
                    ok = bool(check(sc))
                except Exception as ex:  # noqa: BLE001
                    ok, note = False, f"{type(ex).__name__}: {ex}"
                if not ok and not note:
                    note = text[:160]
            else:
                note = text.splitlines()[0][:100] if text else ""
            out.append((tool, args, ok, note))
    return out
