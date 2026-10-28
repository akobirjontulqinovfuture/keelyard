"""MCP stdio server: the agent-facing interface to every keelyard module.

Every tool returns a JSON object (MCP structured content) with stable keys, so an
agent never has to parse prose. Errors come back as MCP tool errors whose text
starts with what went wrong and ends with a `Hint:` saying what to do next.
"""

from __future__ import annotations

import functools
import inspect
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore
from mcp.types import ToolAnnotations

try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore

from . import __version__, config, session
from .codemap import outline as _outline
from .codemap import project_map as _project_map
from .filters import run_filtered
from .memory import Memory
from .proof import Proof
from .report import render
from .saver import ReadCache, compress_output, route
from .snapshot import Shadow
from .swarm import Swarm, Task, plan, subagent_tasks, verify_citations, verify_patch
from .usage import collect as usage_collect

INSTRUCTIONS = """keelyard: proof, rewind, smaller context. For a change: proof_requirements -> find_symbol,
read_file(ranges) -> edit (auto-snapshotted; rewind_to undoes) -> run_compact -> proof_run_tests(phase="after")
-> proof_check each item -> proof_status proven. Step-by-step guides: workflow("plan"|"debug"|"review")."""

mcp = _Server("keelyard", instructions=INSTRUCTIONS)
for _attr in ("_lowlevel_server", "_mcp_server"):  # report keelyard's version in the MCP handshake (serverInfo)
    if hasattr(mcp, _attr):
        getattr(mcp, _attr).version = __version__


def _ann(title: str, read_only: bool = False, destructive: bool = False, idempotent: bool = False, open_world: bool = False):
    return ToolAnnotations.model_validate({"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                                           "idempotentHint": idempotent, "openWorldHint": open_world})


def _tool(annotations: ToolAnnotations):
    """Register a tool; expected failures reach the agent as a ToolError with their hint text."""
    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except ToolError:
                    raise
                except (ValueError, KeyError, LookupError, OSError, RuntimeError, config.ProjectError) as exc:
                    raise ToolError(str(exc).strip("'\"")) from exc
            return mcp.tool(annotations=annotations)(awrapper)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except (ValueError, KeyError, LookupError, OSError, RuntimeError, config.ProjectError) as exc:
                raise ToolError(str(exc).strip("'\"")) from exc
        return mcp.tool(annotations=annotations)(wrapper)
    return deco


def _ctx() -> tuple[config.Config, str]:
    cfg = config.load()
    cfg.require_project()
    return cfg, session.current(cfg)


Step = Annotated[int, Field(ge=1, description="A step number from rewind_list, e.g. 3.")]


# ---------------------------------------------------------------- proof
@_tool(_ann("Record requirements", idempotent=True))
def proof_requirements(
    items: Annotated[list[str], Field(min_length=1, description='One short, checkable item per user ask, e.g. ["discount applies once", "empty cart costs 0"].')],
) -> dict[str, Any]:
    """Record what the user asked for as a checklist. Call this first. Duplicate items are ignored.

    Returns {"requirements": [{"id", "text", "done", "evidence"}]}."""
    cfg, sid = _ctx()
    return {"requirements": Proof(cfg, sid).add_requirements(items)}


@_tool(_ann("Mark requirement verified", idempotent=True))
def proof_check(
    requirement_id: Annotated[int, Field(ge=1, description="The id from proof_requirements, e.g. 1.")],
    evidence: Annotated[str, Field(min_length=1, description='What you ran or saw, e.g. "pytest tests/test_cart.py::test_once passed" or "cart.py:14 caps at one use".')],
) -> dict[str, Any]:
    """Mark one requirement as verified, with concrete evidence (a command and its result, or file:line).

    Returns the updated requirement."""
    cfg, sid = _ctx()
    return Proof(cfg, sid).check(requirement_id, evidence)


@_tool(_ann("Run tests", open_world=True))
def proof_run_tests(
    phase: Annotated[Literal["before", "after"], Field(description='"before" your change (baseline) or "after" it (required by the gate).')] = "after",
    command: Annotated[str, Field(description='Optional override, e.g. "pytest -q tests/test_cart.py". Default: [proof].test_cmd or auto-detected.')] = "",
) -> dict[str, Any]:
    """Run the project's tests and record the result as evidence.

    Returns {"exit", "passed", "seconds", "cmd", "output"} where output is a compact tail."""
    cfg, sid = _ctx()
    pf = Proof(cfg, sid)
    r = pf.run_tests(phase, command or None)
    out = {"exit": r["exit"], "passed": r["exit"] == 0, "seconds": r["seconds"], "cmd": r["cmd"],
           "output": compress_output(r["tail"], 3000)}
    hint = pf.repeat_hint(r)
    if hint:
        out["hint"] = hint
    return out


@_tool(_ann("Screenshot app", open_world=True))
def proof_screenshot(
    url: Annotated[str, Field(description='Page to capture, e.g. "http://localhost:3000/cart". Default: [proof].app_url.')] = "",
) -> dict[str, Any]:
    """Screenshot the running web app and attach it to the proof bundle. Returns {"ok", "path"} or an error."""
    cfg, sid = _ctx()
    return Proof(cfg, sid).screenshot(url or None)


@_tool(_ann("Proof status", read_only=True))
def proof_status() -> dict[str, Any]:
    """Is the work proven? Returns {"proven", "missing": [what to do next], "requirements", "report"}."""
    cfg, sid = _ctx()
    pf = Proof(cfg, sid)
    gaps = pf.missing()
    return {"proven": not gaps, "missing": gaps, "requirements": pf.load().requirements, "report": str(render(pf))}


# ---------------------------------------------------------------- rewind
def _step(s) -> dict[str, Any]:
    return {"step": s.step, "tool": s.tool, "ts": round(s.ts, 3), "files": s.files[:20], "n_files": len(s.files)}


@_tool(_ann("List snapshots", read_only=True))
def rewind_list(last: Annotated[int, Field(ge=1, le=500, description="How many recent steps, e.g. 20.")] = 20) -> dict[str, Any]:
    """List recent workspace snapshots, oldest first. One is taken after every edit or shell command.

    Returns {"steps": [{"step", "tool", "ts", "files", "n_files"}], "total"}."""
    cfg, _ = _ctx()
    steps = Shadow(cfg).steps()
    return {"steps": [_step(s) for s in steps[-last:]], "total": len(steps)}


@_tool(_ann("Show a step's diff", read_only=True))
def rewind_diff(step: Step) -> dict[str, Any]:
    """The unified diff a snapshot step made. Returns {"step", "files", "diff"} (long diffs are shortened)."""
    cfg, _ = _ctx()
    sh = Shadow(cfg)
    return {"step": step, "files": sh.get(step).files, "diff": compress_output(sh.diff(step), 8000)}


@_tool(_ann("Restore files to a step", destructive=True))
def rewind_to(step: Step) -> dict[str, Any]:
    """Restore project files to how they were after `step`. A safety snapshot is taken first,
    so calling rewind_to(safety_step) undoes this. Returns {"restored", "removed", "safety_step", "new_step"}."""
    cfg, _ = _ctx()
    return Shadow(cfg).rewind(step)


# ---------------------------------------------------------------- context saver
@_tool(_ann("Read file (deduplicated)", read_only=True))
def read_file(
    path: Annotated[str, Field(description='Project-relative path, e.g. "src/cart.py". Windows separators are accepted.')],
    start: Annotated[int, Field(ge=1, description="First line, 1-based.")] = 1,
    end: Annotated[int, Field(ge=0, description="Last line, inclusive; 0 = end of file.")] = 0,
    force: Annotated[bool, Field(description="Return the full text even if you read it before (e.g. after compaction).")] = False,
) -> dict[str, Any]:
    """Read a project file. A repeat read of an unchanged file returns a one-line notice instead of the
    text; a changed file returns only the diff since your last read. Returns {"path", "content"}."""
    cfg, sid = _ctx()
    return {"path": path, "content": ReadCache(cfg, sid).read(path, start, end or None, force)}


@_tool(_ann("Run command, compact output", open_world=True))
def run_compact(
    command: Annotated[str, Field(description='Command line, run without a shell, e.g. "pytest -q" or "git log -n 20".')],
    timeout: Annotated[int, Field(ge=1, le=3600, description="Seconds before the command is killed.")] = 600,
) -> dict[str, Any]:
    """Run a command and return output with failures, errors and file:line kept and noise dropped.
    Returns {"exit", "output"}; the output ends with an id for recall() when anything was cut."""
    cfg, _ = _ctx()
    code, out = run_filtered(command, cfg.root, cfg.state / "raw", timeout)
    return {"exit": code, "output": out}


@_tool(_ann("Full output by id", read_only=True))
def recall(output_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,64}$", description='The id printed by run_compact, e.g. "3f9a1c2b7d4e".')]) -> dict[str, Any]:
    """Return the full raw output that run_compact or the shell-output filter shortened."""
    cfg, _ = _ctx()
    p = cfg.state / "raw" / output_id
    if not p.exists():
        raise ValueError(f"unknown output id {output_id!r}. Hint: use the id from the last line of run_compact output.")
    return {"id": output_id, "output": p.read_text(errors="replace", encoding="utf-8")}


@_tool(_ann("Outline a file", read_only=True))
def outline(
    path: Annotated[str, Field(description='Project-relative path, e.g. "src/cart.py".')],
    related: Annotated[bool, Field(description="Also list what it imports, who imports it, and its tests.")] = False,
) -> dict[str, Any]:
    """Signatures and classes with line numbers. Read this before reading a whole file."""
    cfg, sid = _ctx()
    out = {"path": path, "outline": _outline(ReadCache(cfg, sid).resolve(path))}
    if related:
        from .codemap import related as _related

        out["related"] = _related(cfg.root, path)
    return out


@_tool(_ann("Project map", read_only=True))
def project_map() -> dict[str, Any]:
    """Files per directory with approximate token sizes. Start here in an unfamiliar repo."""
    cfg, _ = _ctx()
    return {"map": _project_map(cfg.root)}


@_tool(_ann("Find a definition", read_only=True))
def find_symbol(
    name: Annotated[str, Field(min_length=1, description='Function, class or type name, e.g. "Cart".')],
    refs: Annotated[bool, Field(description="Also list call sites and uses (source before tests).")] = False,
    offset: Annotated[int, Field(ge=0, description="Skip this many results; use next_offset from the last call.")] = 0,
) -> dict[str, Any]:
    """Where is a symbol defined (best match first), and with refs=true where it is used.
    Returns {"matches": [...], "remaining", "next_offset"?}; results are paged, never cut silently."""
    from .codemap import find_symbol_ranked, page, references

    cfg, _ = _ctx()
    hits = find_symbol_ranked(cfg.root, name)
    if refs:
        hits = hits + [f"use {r}" for r in references(cfg.root, name)]
    pg = page(hits, offset, 20)
    out = {"name": name, "matches": pg["items"], "total": pg["total"], "remaining": pg["remaining"]}
    if "next_offset" in pg:
        out["next_offset"] = pg["next_offset"]
    return out


@_tool(_ann("Token usage", read_only=True))
def usage_report(by: Annotated[Literal["day", "model", "agent"], Field(description="How to group rows.")] = "day") -> dict[str, Any]:
    """Token usage and cost from local Claude Code, Codex and OpenCode logs. Cost is null for unpriced models."""
    cfg, _ = _ctx()
    rows = usage_collect(cfg.raw.get("prices", {}), by=by)
    return {"by": by, "rows": [{"key": k, "input": r.input, "output": r.output, "cache_read": r.cache_read,
                                "cache_write": r.cache_write, "cost_usd": round(r.cost, 4) if r.priced else None}
                               for k, r in rows.items()]}


@_tool(_ann("Which model for a subtask", read_only=True))
def route_task(
    task: Annotated[str, Field(description='The subtask, e.g. "summarise these 30 log files".')],
    n_files: Annotated[int, Field(ge=0, description="How many files it touches.")] = 0,
) -> dict[str, Any]:
    """Suggest the strong or the cheap model for a subtask (a transparent keyword heuristic, not a model call)."""
    return route(task, n_files)


# ---------------------------------------------------------------- swarm
@_tool(_ann("Run helper tasks", open_world=True))
async def swarm(
    tasks: Annotated[list[dict[str, Any]], Field(min_length=1, description=(
        'Each: {"kind": "ask"|"summarize"|"draft_edit"|"grep", "prompt": str, "files": [paths], "symbols": [names]}. '
        '"symbols" narrows the context to those definitions. Tip: swarm_plan builds this list for you. '
        'Example: [{"kind": "ask", "prompt": "Where is the discount applied?", "files": ["cart.py"]}]'))],
) -> dict[str, Any]:
    """Run small tasks on the configured helper model in parallel. Each answer is checked: citations (path:line)
    must exist; drafts must apply and pass the tests in a throwaway copy (never applied to your files).
    Failed answers are retried with the checker's notes, best-of-N, then escalated to the strong model if set.
    With no helper model configured, returns {"mode": "plan", "subagent_tasks": [...]} for your own subagents."""
    cfg, _ = _ctx()
    ts = [Task(kind=t.get("kind", "ask"), prompt=t.get("prompt", ""), files=list(t.get("files", [])),
               symbols=list(t.get("symbols", []))) for t in tasks]
    coop = bool(cfg.section("swarm").get("cooperative", True))
    try:
        if coop:
            from .coop import Coop

            sw = Coop(cfg)
        else:
            sw = Swarm(cfg)
    except (KeyError, ValueError):
        sw = None
    if sw is None or sw.model.get("model") in ("", "set-me", None):
        return {"mode": "plan", "note": "No helper model configured. Send each subagent_prompt to your own subagents "
                "(Claude Code Task tool, OpenCode subagents), then check answers with swarm_verify.",
                "subagent_tasks": subagent_tasks(cfg, ts)}
    results = await sw.run(ts)
    extra = {}
    if coop:
        from .coop import summary

        extra = summary(sw, results)
    return {"mode": "ran", "verified": sum(r.verified for r in results), "total": len(results), **extra,
            "results": [{"id": r.id, "kind": r.kind, "ok": r.ok, "verified": r.verified, "notes": r.notes,
                         "output": r.output[:1500], "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                         "attempts": r.attempts, "candidates": r.candidates, "escalated": r.escalated,
                         "tests_passed": r.tests_passed, "model": r.model, "critic": r.critic} for r in results]}


@_tool(_ann("Split a goal into helper tasks", read_only=True))
def swarm_plan(
    goal: Annotated[str, Field(min_length=3, description='What you want done, e.g. "fix total() so SAVE10 takes 10% off".')],
    files: Annotated[list[str], Field(description="Limit to these files; empty = find the files that define the symbols the goal names.")] = [],  # noqa: B006
    max_tasks: Annotated[int, Field(ge=1, le=64, description="Upper bound on tasks.")] = 8,
) -> dict[str, Any]:
    """Split a goal into small tasks, one per relevant file, each with a minimal context pack (only the named
    definitions plus imports). Returns {"tasks"} to pass to swarm, and {"subagent_tasks"} with ready prompts
    for your own subagents. Deterministic: no model call."""
    cfg, _ = _ctx()
    ts = plan(cfg, goal, files or None, max_tasks)
    return {"tasks": [{"kind": t.kind, "prompt": t.prompt, "files": t.files, "symbols": t.symbols} for t in ts],
            "subagent_tasks": subagent_tasks(cfg, ts)}


@_tool(_ann("Verify a helper answer", read_only=True))
def swarm_verify(
    answer: Annotated[str, Field(description="The answer or unified diff to check.")],
    files: Annotated[list[str], Field(description='Files the answer may cite, e.g. ["cart.py"].')] = [],  # noqa: B006
    kind: Annotated[Literal["ask", "draft_edit"], Field(description='"ask": check path:line citations; "draft_edit": check the patch applies.')] = "ask",
) -> dict[str, Any]:
    """Verify an answer from any helper, including your own subagents. Returns {"verified", "notes"}."""
    cfg, _ = _ctx()
    if kind == "draft_edit":
        ok, _, notes = verify_patch(cfg.root, answer)
    else:
        ok, notes = verify_citations(cfg.root, answer, files)
    return {"verified": ok, "notes": notes}


# ---------------------------------------------------------------- memory
@_tool(_ann("Search past sessions", read_only=True))
def memory_search(
    query: Annotated[str, Field(min_length=1, description='Words to look for, e.g. "stripe webhook signature".')],
    this_project_only: Annotated[bool, Field(description="Only sessions that ran in this project.")] = True,
    budget_chars: Annotated[int, Field(ge=200, le=20000, description="Max characters of snippets returned.")] = 2400,
    offset: Annotated[int, Field(ge=0, description="Skip this many hits; use next_offset from the last call.")] = 0,
) -> dict[str, Any]:
    """Search past Claude Code, Codex and OpenCode sessions, best match first. New sessions are indexed first.
    Returns {"hits": [{"agent", "session", "project", "ts", "role", "snippet"}], "remaining", "next_offset"?}."""
    cfg, _ = _ctx()
    m = Memory()
    m.ingest()
    return {"query": query, **m.search_page(query, str(cfg.root) if this_project_only else None, budget_chars, offset=offset)}


@_tool(_ann("Work state"))
def work_state(
    action: Annotated[Literal["show", "decision", "question", "resolve"], Field(description="show, record, or resolve.")] = "show",
    text: Annotated[str, Field(description="The decision or question, one line.")] = "",
    ref: Annotated[str, Field(description='Evidence: "path:line", "cmd: <command>" or "#<proof item>". Required for decisions.')] = "",
    item: Annotated[int, Field(ge=0, description="Id to resolve.")] = 0,
) -> dict[str, Any]:
    """Compact state that survives compaction: requirements, decisions, changed files, open questions.
    Record each key decision with a ref to the file line or command that backs it."""
    from .workstate import WorkState

    cfg, sid = _ctx()
    ws = WorkState(cfg, sid)
    try:
        if action in ("decision", "question"):
            ws.add(action, text, ref)
        elif action == "resolve":
            ws.resolve(item)
    except (ValueError, KeyError) as e:
        raise ToolError(str(e)) from None
    return {"state": ws.render(600) or "empty"}


@_tool(_ann("Find a tool", read_only=True))
def tool_search(
    query: Annotated[str, Field(description='What you want to do, e.g. "undo my last edit" or "screenshot".')],
    limit: Annotated[int, Field(ge=1, le=10, description="Max tools returned.")] = 3,
) -> dict[str, Any]:
    """Find a keelyard tool that is not loaded yet: the best match with its input schema, then other names.
    Run it with tool_call(name, arguments)."""
    pool = _ALL or getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
    words = set(re.findall(r"[a-z]{3,}", query.lower()))
    scored = []
    for name, t in pool.items():
        if name in META:
            continue
        hay = f"{name.replace('_', ' ')} {t.description or ''}".lower()
        score = sum(3 if w in name else 1 for w in words if w in hay)
        if score:
            scored.append((-score, name, t))
    scored.sort(key=lambda x: (x[0], x[1]))
    # full schema for the best match only; the others get one line (load one with a more specific query)
    found = [{"name": n, "description": (t.description or "").strip(), "input_schema": _strip_titles(t.parameters)}
             if k == 0 else {"name": n, "description": (t.description or "").strip().split("\n")[0][:120]}
             for k, (_, n, t) in enumerate(scored[:limit])]
    return {"query": query, "tools": found, "remaining": max(0, len(scored) - limit)}


@_tool(_ann("Run a tool"))
async def tool_call(
    name: Annotated[str, Field(description="Tool name from tool_search.")],
    arguments: Annotated[dict[str, Any], Field(description="Arguments matching that tool's input_schema.")] = {},  # noqa: B006
) -> dict[str, Any]:
    """Run any keelyard tool by name, including ones not loaded up front (find them with tool_search)."""
    pool = _ALL or getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
    if name in META or name not in pool:
        raise ToolError(f"unknown tool {name!r}. Hint: tool_search")
    ctx = mcp.get_context() if hasattr(mcp, "get_context") else None  # none of our tools take a context
    result = await pool[name].run(arguments, context=ctx, convert_result=False)
    return result if isinstance(result, dict) else {"result": result}


@_tool(_ann("keelyard status", read_only=True))
def status() -> dict[str, Any]:
    """One call to orient yourself: version, project root, snapshot count, proof state."""
    cfg, sid = _ctx()
    steps = Shadow(cfg).steps()
    pf = Proof(cfg, sid)
    return {"version": __version__, "root": str(cfg.root), "session": sid, "steps": len(steps),
            "last_step": _step(steps[-1]) if steps else None, "proven": not pf.missing(), "missing": pf.missing()}


@_tool(_ann("Workflow guide", read_only=True))
def workflow(name: Annotated[Literal["plan", "debug", "review"], Field(description="Which guide.")]) -> dict[str, Any]:
    """A short step-by-step guide: plan (plan-implement-verify), debug (loop), review (diff checklist)."""
    from .adapters.install import workflow_text

    return {"name": name, "steps": workflow_text(name)}


# Loaded by default: the tools the workflow needs. Hooks do the rest without any schema cost.
CORE = ("proof_requirements", "proof_check", "proof_run_tests", "proof_status", "rewind_list", "rewind_to", "read_file",
        "run_compact", "recall", "find_symbol", "memory_search", "swarm", "workflow", "work_state")

# "lazy": only these schemas are sent up front; every other tool is found with tool_search and run with tool_call
LAZY = ("read_file", "run_compact", "find_symbol", "proof_status", "tool_search", "tool_call")
META = ("tool_search", "tool_call")


def _schema_size(tools) -> int:
    """Approximate tokens the tool list costs in every request (what tools/list sends)."""
    return sum(len(json.dumps({"name": t.name, "description": t.description, "inputSchema": _strip_titles(t.parameters),
                               "outputSchema": getattr(t, "output_schema", None)})) for t in tools) // 4


def _strip_titles(schema):
    """Pydantic adds a "title" to every schema node; the model never needs them (about 10% of schema tokens)."""
    if isinstance(schema, dict):
        return {k: _strip_titles(v) for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


_ALL: dict | None = None


def configure_tools(selection: str | list[str] | None = None, app_url: bool = False) -> list[str]:
    """Keep only the selected tools ("core", "all", or a list of names) and compact their schemas.

    Every tool schema is sent to the model at session start, so unused tools cost tokens in every session."""
    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    if tools is None:  # unknown SDK internals: leave everything as it is
        return []
    global _ALL
    if _ALL is None:
        _ALL = dict(tools)
    tools.clear()
    tools.update(_ALL)
    core = set(CORE) | ({"proof_screenshot"} if app_url else set())
    if selection in (None, "core"):
        keep = core
    elif selection == "all":
        keep = set(tools) - set(META)
    elif selection == "lazy":
        keep = set(LAZY)
        # never increase tokens: lazy must cost less up front than core, overhead (the two meta tools) included
        if _schema_size(_ALL[n] for n in keep) >= _schema_size(_ALL[n] for n in core):
            keep = core
    else:
        keep = set(selection)
    for name in [n for n in tools if n not in keep]:
        del tools[name]
    for t in tools.values():
        if isinstance(getattr(t, "parameters", None), dict):
            t.parameters = _strip_titles(t.parameters)
    return sorted(tools)


def main() -> None:
    import logging
    import os

    sel: str | list[str] | None = os.environ.get("KEELYARD_MCP_TOOLS") or None
    app_url = False
    try:
        cfg = config.load()
        sel = sel or cfg.section("mcp").get("tools", "core")
        app_url = bool(cfg.section("proof").get("app_url"))
    except Exception:  # noqa: BLE001 - no project yet: core tools
        pass
    if isinstance(sel, str) and "," in sel:
        sel = [x.strip() for x in sel.split(",") if x.strip()]
    configure_tools(sel, app_url)

    for name in ("httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
