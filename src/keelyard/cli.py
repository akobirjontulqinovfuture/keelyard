"""keelyard command line.

Every command is `keelyard <group> <action>`; the hidden `hook` and `mcp`
commands are what the agents call and are not meant for people.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import io
import json
import shlex
import sys
import textwrap
from pathlib import Path

from . import __version__, config, session
from .proc import join_cmd

SUPPRESS = argparse.SUPPRESS

OVERVIEW = """\
keelyard makes coding agents prove their work, lets you undo any step,
and keeps their context small. Works with Claude Code, Codex and OpenCode.

start here:
  init              set up this project and wire every agent it finds
  doctor            check that everything is installed and wired correctly
  status            what keelyard knows about this project right now

everyday:
  rewind            list, inspect and undo agent steps
  proof             the evidence an agent collected before saying "done"
  run               run a command and print a compact version of its output
  code              outline a file, map the project, find a symbol
  memory            search past sessions across all your agents
  usage             tokens and cost from your local agent logs

more:
  swarm             fan small, checkable tasks out to a cheap model
  arena             run one task across agents and models, compare results
  bench             measure savings on your own machine
  selftest          a scripted agent completes a full task through the agent interface
  install/uninstall wire or unwire one agent by hand
  filter            compact output piped on stdin
  recall            print the full output that `run` shortened
  state             compact work state: requirements, decisions, changes, open questions
"""

EXAMPLES = {
    "init": """examples:
  keelyard init                    # detect agents, wire them, take a first snapshot
  keelyard init --agent claude     # only Claude Code
  keelyard init --dry-run          # show what would change""",
    "rewind": """examples:
  keelyard rewind                  # every step agents took, newest last
  keelyard rewind list -i          # browse steps and diffs full-screen
  keelyard rewind show 12          # the diff that step 12 made
  keelyard rewind to 9             # put the files back as they were after step 9
  keelyard rewind fork 9 ../try-b  # copy step 9 into another folder
  keelyard rewind snap             # take a snapshot by hand""",
    "proof": """examples:
  keelyard proof status
  keelyard proof add "discount applies once" "empty cart costs 0"
  keelyard proof check 1 "test_discount_once passes"
  keelyard proof test after
  keelyard proof report            # writes an HTML report""",
    "run": """examples:
  keelyard run pytest -q
  keelyard run git log --stat
  keelyard run -- ruff check .     # use -- if the command has its own flags first""",
    "code": """examples:
  keelyard code outline src/app.py
  keelyard code map
  keelyard code find Cart""",
    "memory": """examples:
  keelyard memory index            # read new sessions from all agents
  keelyard memory search "rate limit retry"
  keelyard memory stats""",
}


class Fail(Exception):
    """A user-facing error; the message may include `Hint: ...`. code 2 means a usage error."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _cfg() -> config.Config:
    return config.load()


EXIT_CODES = """exit codes:
  0  success
  1  error (the message says what went wrong and how to fix it)
  2  usage error (bad command or arguments)
  3  a check did not pass (doctor found a problem, proof not complete, tests failed, selftest failed)
  `keelyard run` exits with the code of the command it ran."""


def _json_parent(suppress: bool = False) -> argparse.ArgumentParser:
    """suppress=True for actions under a group that has --json itself: an unset child flag must not reset
    `keelyard proof --json status` back to False."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False,
                   help="print one JSON object on stdout (for scripts and agents)")
    return p


def _parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    jp, jp_child = _json_parent(), _json_parent(suppress=True)
    ap = argparse.ArgumentParser(prog="keelyard", usage="keelyard <command> [options]", description=OVERVIEW, formatter_class=fmt,
                                 epilog=EXIT_CODES + "\n\nRun `keelyard <command> --help` for examples. Every command accepts --json.")
    ap.add_argument("--version", action="version", version=f"keelyard {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>", title="commands", prog="keelyard")

    def cmd(name, help, examples=None, leaf=True, **kw):
        return sub.add_parser(name, help=help, description=help, epilog=examples or EXAMPLES.get(name), formatter_class=fmt,
                              parents=[jp] if leaf else [], **kw)

    class _Group:
        def __init__(self, s):
            self.s = s

        def add_parser(self, name, **kw):
            return self.s.add_parser(name, parents=[jp_child], description=kw.get("help"), **kw)

    def group(parent, name, help):
        s = parent.add_subparsers(dest=name, metavar="<action>", title="actions", prog=parent.prog)
        s.required = True
        return _Group(s)

    p = cmd("init", "set up this project and wire the agents found on this machine")
    p.add_argument("--agent", action="append", choices=["claude", "codex", "opencode"], help="wire only this agent (repeatable)")
    p.add_argument("--no-agents", action="store_true", help="create the config but wire no agent")
    p.add_argument("--dry-run", action="store_true", help="print what would change and stop")
    p.add_argument("--preset", choices=["lean", "balanced", "max-quality"], default="lean",
                   help="lean = default, fewest tokens (trimmed agent prompt/tools, capped output); balanced = all agent tools; "
                        "max-quality = every keelyard tool over MCP. `keelyard uninstall <agent>` reverts it")
    p.add_argument("--codex-project-mcp", action="store_true",
                   help="Codex: register the keelyard MCP server in this project's .codex/config.toml instead of asking you to edit "
                        "~/.codex/config.toml (Codex reads it only for trusted projects)")
    p.add_argument("--no-terse", dest="terse", action="store_false",
                   help="leave out the terse-output rule (results only, no filler) from CLAUDE.md/AGENTS.md")
    p.add_argument("--global", dest="glob", action="store_true",
                   help="wire the agents for every project of this user (~/.claude, ~/.codex, ~/.config/opencode); "
                        "projects set themselves up on their first session")

    cmd("doctor", "check the installation and wiring; exits 3 if something is broken",
        "examples:\n  keelyard doctor\n  keelyard doctor --json")
    cmd("status", "show what keelyard knows about this project", "examples:\n  keelyard status\n  keelyard status --json")
    p = cmd("selftest", "a scripted agent runs a full task through MCP and hooks in a temp folder",
            "examples:\n  keelyard selftest\n  keelyard selftest --keep   # keep the temp project to inspect")
    p.add_argument("--keep", action="store_true", help="keep the temporary project")

    for name, verb in (("install", "wire"), ("uninstall", "unwire")):
        p = cmd(name, f"{verb} one agent in this project", f"examples:\n  keelyard {name} claude\n  keelyard {name} --global")
        p.add_argument("agent", nargs="?", choices=["claude", "codex", "opencode"])
        p.add_argument("--global", dest="glob", action="store_true", help=f"{verb} at user level (all agents if none named)")
        if name == "install":
            p.add_argument("--no-terse", dest="terse", action="store_false", help="leave out the terse-output rule")
            p.add_argument("--preset", choices=["lean", "balanced", "max-quality"], default="lean",
                           help="lean = default, fewest tokens; see `keelyard init --help`")
            p.add_argument("--codex-project-mcp", action="store_true",
                           help="Codex: register the keelyard MCP server in this project's .codex/config.toml instead of asking you to edit "
                        "~/.codex/config.toml (Codex reads it only for trusted projects)")

    r = group(cmd("rewind", "list, inspect and undo agent steps"), "action", "")
    r.s.required = False  # bare `keelyard rewind` lists steps
    x = r.add_parser("list", help="list snapshots")
    x.add_argument("-i", "--interactive", action="store_true", help="open the timeline browser")
    x.add_argument("-n", type=int, default=30, help="show the last N steps (default 30)")
    x = r.add_parser("show", help="show the diff a step made")
    x.add_argument("step", type=int)
    x = r.add_parser("to", help="restore files to a step (undoable)")
    x.add_argument("step", type=int)
    x = r.add_parser("fork", help="copy a step into a new folder")
    x.add_argument("step", type=int)
    x.add_argument("dest", type=Path)
    r.add_parser("snap", help="take a snapshot now")
    r.add_parser("recover", help="finish a rewind that was interrupted (puts back the state from just before it)")

    r = group(cmd("proof", "the evidence an agent collected before saying done"), "action", "")
    r.s.required = False  # bare `keelyard proof` shows the status
    r.add_parser("status", help="what is proven and what is missing")
    x = r.add_parser("add", help="add requirements")
    x.add_argument("items", nargs="+")
    x = r.add_parser("check", help="mark a requirement met, with evidence")
    x.add_argument("id", type=int)
    x.add_argument("evidence")
    x = r.add_parser("test", help="run the test command and record the result")
    x.add_argument("phase", nargs="?", choices=["before", "after"], default="after",
                   help="before or after the change (default: after)")
    x.add_argument("--cmd", dest="test_cmd", help="override [proof].test_cmd")
    x = r.add_parser("shot", help="screenshot the app")
    x.add_argument("url", nargs="?")
    x = r.add_parser("accept-tests", help="acknowledge changed tests (a real refactor); shown as agent-claimed")
    x.add_argument("reason")
    r.add_parser("reset", help="start this session's proof over (e.g. after a failed signature check)")
    x = r.add_parser("report", help="write the HTML report")
    x.add_argument("--open", action="store_true", help="open it in the browser")

    p = cmd("run", "run a command and print a compact version of its output")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = cmd("filter", "compact output piped on stdin", "examples:\n  pytest -q 2>&1 | keelyard filter --for 'pytest -q'")
    p.add_argument("--for", dest="for_cmd", default="", help="the command that produced the output (picks the rules)")
    p = cmd("recall", "print the full output that `run` shortened", "examples:\n  keelyard recall 3f9a1c2b7d4e")
    p.add_argument("id")

    r = group(cmd("code", "outline a file, map the project, find a symbol"), "action", "")
    x = r.add_parser("outline", help="signatures only")
    x.add_argument("path", type=Path)
    r.add_parser("map", help="compact project map")
    x = r.add_parser("find", help="where a symbol is defined (best first, paged)")
    x.add_argument("name")
    x.add_argument("--refs", action="store_true", help="also list call sites and uses")
    x.add_argument("--offset", type=int, default=0, help="skip this many results (next page)")
    x.add_argument("--limit", type=int, default=20, help="results per page (default 20)")
    x = r.add_parser("related", help="what a file imports, who imports it, and its tests")
    x.add_argument("path")

    r = group(cmd("memory", "search past sessions across all your agents"), "action", "")
    r.add_parser("index", help="read new sessions from Claude Code, Codex and OpenCode")
    r.add_parser("stats", help="what is indexed")
    x = r.add_parser("search", help="full-text search")
    x.add_argument("query")
    x.add_argument("--project")
    x.add_argument("--budget", type=int, default=2400, help="max characters returned (default 2400)")
    x.add_argument("--offset", type=int, default=0, help="skip this many hits (next page)")

    p = cmd("state", "compact work state: requirements, decisions, changes, open questions",
            "examples:\n  keelyard state\n  keelyard state decision \"keep float prices\" --ref src/cart.py:42\n"
            "  keelyard state question \"is the 10% cap per order?\"\n  keelyard state resolve 2")
    p.add_argument("action", nargs="?", choices=["show", "decision", "question", "resolve"], default="show")
    p.add_argument("text", nargs="?", default="", help="the decision/question, or the id to resolve")
    p.add_argument("--ref", default="", help="evidence: path:line, 'cmd: <command>' or #<proof item>")

    p = cmd("usage", "tokens and cost from your local agent logs", "examples:\n  keelyard usage\n  keelyard usage --by model")
    p.add_argument("--by", choices=["day", "model", "agent"], default="day")

    r = group(cmd("swarm", "fan small, checkable tasks out to a cheap model",
                  'examples:\n  keelyard swarm plan "fix total() for SAVE10" --json > tasks.json\n'
                  '  keelyard swarm run tasks.json   # [{"kind": "ask", "prompt": "...", "files": ["a.py"], "symbols": ["total"]}]\n'
                  '  keelyard swarm plan "where is auth checked" --subagents'), "action", "")
    x = r.add_parser("run", help="run tasks from a JSON file")
    x.add_argument("tasks", type=Path)
    x.add_argument("--model", help="a [models.<key>] from the config (default: [swarm].helper_model)")
    x.add_argument("--best-of", type=int, help="samples per task (default: [swarm].best_of)")
    x.add_argument("--retries", type=int, help="retries with checker feedback (default: [swarm].retries)")
    x.add_argument("--basic", action="store_true", help="no blackboard, critic, routing or budget (the plain swarm)")
    x = r.add_parser("plan", help="split a goal into focused helper tasks (no model call)")
    x.add_argument("goal")
    x.add_argument("--files", nargs="*", default=None, help="limit to these files")
    x.add_argument("--subagents", action="store_true", help="print ready prompts for your agent's own subagents")

    r = group(cmd("arena", "run one task across agents and models, compare results",
                  "examples:\n  keelyard arena run examples/arena/discount/task.toml\n  keelyard arena render arena-results"), "action", "")
    x = r.add_parser("run", help="run a task spec")
    x.add_argument("spec", type=Path)
    x.add_argument("--out", type=Path, default=Path("arena-results"))
    x.add_argument("--only", nargs="*", metavar="NAME", help="only these contenders")
    x = r.add_parser("render", help="re-render the report from results.json")
    x.add_argument("out", type=Path)

    cmd("bench", "measure savings on your own machine", "examples:\n  keelyard bench\n  keelyard bench --json")

    p = sub.add_parser("hook", help=SUPPRESS)
    p.add_argument("agent")
    p.add_argument("event")
    sub.add_parser("mcp", help=SUPPRESS)
    sub._choices_actions = [a for a in sub._choices_actions if a.help != SUPPRESS]  # hide internals from --help
    return ap


# -- commands ------------------------------------------------------------------
# Each do_* returns (exit_code, data). Human output is printed only when --json is off;
# with --json, main() prints `data` as one JSON object and nothing else goes to stdout.

CHECK_FAILED = 3


def _human(a) -> bool:
    return not getattr(a, "json", False)


def _do_global(a, remove: bool):
    from . import ui
    from .adapters.install import AGENTS, InstallError, detect, install_global, uninstall_global

    agents = [a.agent] if getattr(a, "agent", None) else list(AGENTS)
    if isinstance(getattr(a, "agent", None), list):
        agents = a.agent
    changed: dict[str, list[str]] = {}
    data = {"global": True, "agents": agents, "changed": changed, "detected": detect()}
    h = _human(a)
    if h:
        ui.heading("keelyard uninstall --global" if remove else "keelyard init --global", "all projects of this user")
    for x in agents:
        try:
            files = uninstall_global(x) if remove else install_global(x, terse=None if getattr(a, "terse", True) else False)
        except InstallError as e:
            raise Fail(str(e)) from None
        changed[x] = files
        if h:
            ui.line("ok", x, ", ".join(ui.tilde(f) for f in files) or "nothing to remove")
    if h and not remove:
        ui.out.print("\n  backups: <file>.keelyard-backup next to each changed file")
        ui.out.print("  each git project sets itself up on its first agent session. Check: [key]keelyard doctor --json[/]")
    return 0, data


def do_init(a):
    from . import ui
    from .adapters.install import AGENTS, detect, install, installed
    from .snapshot import Shadow

    if a.glob:
        return _do_global(a, remove=False)

    root = config.find_root()
    found = detect()
    agents = a.agent or ([] if a.no_agents else [x for x in AGENTS if x in found or installed(x, root)])
    data = {"root": str(root), "detected": found, "agents": agents, "dry_run": a.dry_run, "changed": [], "snapshot": None}
    h = _human(a)
    if h:
        ui.heading("keelyard init", ui.tilde(root))
        if not (root / ".git").exists():
            ui.out.print("  [warn]not a git repository; keelyard still works here.[/]")
        if not a.agent:
            ui.out.print("\n  [muted]agents on this machine[/]")
            for x in AGENTS:
                ui.line("ok" if x in found else "skip", x, found.get(x, "not found"))
    if a.dry_run:
        data["changed"] = [".keelyard/config.toml"] + [f"wire {x}" for x in agents]
        if h:
            ui.out.print("\n  [muted]would create[/] .keelyard/config.toml")
            for x in agents:
                ui.out.print(f"  [muted]would wire[/]   {x}")
        return 0, data
    if h:
        ui.out.print()
    cfg_path = config.init(root, lean=getattr(a, "preset", "lean") == "lean")
    data["changed"].append(str(cfg_path.relative_to(root)))
    if h:
        ui.line("ok", "config", str(cfg_path.relative_to(root)))
    for x in agents:
        was = installed(x, root)
        items = install(x, root, preset=getattr(a, "preset", "lean"), codex_project_mcp=getattr(a, "codex_project_mcp", False),
                        terse=None if getattr(a, "terse", True) else False)
        data["changed"] += [f"{x}: {i.splitlines()[0]}" for i in items]
        if h:
            short = []
            for i in items:
                first = i.splitlines()[0]
                if " (" not in first:
                    continue
                path, label = first.split(" (", 1)
                label = label.rstrip(")").split(";")[0]
                short.append(path if label == "keelyard section" else label)
            ui.line("ok", x, ("updated: " if was else "") + ", ".join(dict.fromkeys(short)))
            for i in items:
                if "\n" in i:
                    for r in i.splitlines():
                        ui.out.print("      ", end="")
                        ui.out.print(r, style="muted", markup=False)
            if x == "codex":
                ui.out.print("      [muted]then run /hooks once inside Codex to trust the new hooks[/]")
    if not agents and h:
        ui.line("skip", "agents", "none wired; run `keelyard install <agent>` later")
    step = Shadow(config.load(root)).snapshot("baseline", note="keelyard init")
    data["snapshot"] = {"step": step.step, "files": len(step.files), "skipped": step.skipped} if step else None
    if h:
        ui.line("ok", "first snapshot", f"#{step.step}, {len(step.files)} files" if step else "already up to date")
        if step and step.skipped:
            ui.line("warn", "skipped", f"{len(step.skipped)} files over [rewind].max_file_mb")
        ui.out.print("\n  next: start your agent as usual, then run [key]keelyard status[/].")
    return 0, data


def _checks() -> list[tuple[str, str, str, str]]:
    """(section, state, label, detail)."""
    import importlib.util
    import shutil
    import subprocess

    from .adapters.install import AGENTS, detect, installed

    rows = []
    v = sys.version_info
    rows.append(("system", "ok" if v >= (3, 11) else "bad", "python", f"{v.major}.{v.minor}.{v.micro}"))
    if shutil.which("git"):
        ver = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip().removeprefix("git version ")
        rows.append(("system", "ok", "git", ver))
    else:
        rows.append(("system", "bad", "git", "not found. Hint: install git; rewind needs it."))
    for mod, why, needed, extra in (("mcp", "MCP server", True, ""), ("httpx", "swarm", True, ""), ("rich", "terminal output", True, ""),
                                    ("textual", "timeline browser", True, ""), ("matplotlib", "arena chart", False, "report"),
                                    ("playwright", "proof screenshots", False, "web")):
        ok = importlib.util.find_spec(mod) is not None
        if ok:
            rows.append(("system", "ok", mod, why))
        elif needed:
            rows.append(("system", "bad", mod, f"missing. Hint: pip install {mod}"))
        else:
            rows.append(("system", "skip", mod, f"optional, for {why}: pip install 'keelyard[{extra}]'"))
    from . import ui
    from .adapters.install import global_agents, global_lean_state, installed_global

    for x, gaps in global_lean_state().items():
        if gaps:
            rows.append(("global", "warn", f"{x} lean", "; ".join(gaps) + ". Hint: keelyard init --global"))
    for x in global_agents():
        rows.append(("global", "ok" if installed_global(x) else "bad", x,
                     "wired for all projects" if installed_global(x) else "marked global but not wired. Hint: keelyard init --global"))
    exe = shutil.which("keelyard")
    rows.append(("system", "ok" if exe else "warn", "keelyard on PATH",
                 ui.tilde(exe) if exe else "not found; hooks call `keelyard`. Hint: pipx install keelyard"))

    root = config.find_root()
    rows.append(("project", "ok", "root", ui.tilde(root)))
    cfg = None
    has = False
    try:
        cfg = config.load(root)
        has = (root / ".keelyard" / "config.toml").exists()
        rows.append(("project", "ok" if has else "warn", "config", ".keelyard/config.toml" if has else "none yet. Hint: run `keelyard init`"))
        if has:
            pf = root / ".keelyard" / "preset.json"
            preset = json.loads(pf.read_text(encoding="utf-8")).get("preset", "lean") if pf.exists() else "lean"
            drift = config.lean_drift((root / ".keelyard" / "config.toml").read_text(encoding="utf-8")) if preset == "lean" else []
            rows.append(("project", "warn" if drift else "ok", "lean defaults",
                         ("overridden: " + "; ".join(drift) + ". The ~80% bench saving assumes these") if drift else f"preset {preset}"))
    except config.ProjectError as e:
        rows.append(("project", "bad", "config", str(e)))
    if cfg is not None and (root / ".keelyard").is_dir():
        from .snapshot import Shadow

        sh = Shadow(cfg)
        if (sh.gitdir / "HEAD").exists():
            try:
                sh._git("rev-parse", "--verify", "-q", "refs/heads/timeline")
                n_steps = len(sh.steps())
                rows.append(("project", "ok", "timeline", f"{n_steps} step{'s' * (n_steps != 1)}"))
            except Exception as e:  # noqa: BLE001
                rows.append(("project", "bad", "timeline", f"{e}. Hint: move .keelyard/shadow.git aside and run `keelyard rewind snap`"))
        else:
            rows.append(("project", "skip", "timeline", "no snapshots yet"))
        log = cfg.state / "hook.log"
        if log.exists() and log.stat().st_size:
            last = log.read_text(errors="replace", encoding="utf-8").strip().splitlines()[0][:100]
            rows.append(("project", "warn", "hook errors", f"see .keelyard/hook.log ({last})"))
        if cfg.raw.get("models", {}).get("strong", {}).get("model") == "set-me":
            rows.append(("project", "skip", "models.strong", "not set (only swarm/route use it)"))
    if has and cfg is not None:
        from .proof import gate_status

        g = gate_status(cfg)
        rows.append(("project", "ok" if g["state"] == "hooked" else "warn", "proof gate",
                     f"live ({', '.join(g['agents'])})" if g["state"] == "hooked" else f"{g['state']}: {g['why']}"))
        if Shadow(cfg).interrupted():
            rows.append(("project", "bad", "rewind", "an earlier rewind did not finish. Hint: keelyard rewind recover"))
    found = detect()
    for x in AGENTS:
        if installed(x, root):
            rows.append(("agents", "ok", x, "wired" + ("" if x in found else " (agent itself not found on PATH)")))
            if x == "codex" and not _codex_mcp_registered(root):
                rows.append(("agents", "warn", "codex mcp", "hooks are wired but ~/.codex/config.toml has no [mcp_servers.keelyard]. "
                             "Hint: `keelyard install codex --codex-project-mcp` (this project) or `keelyard install codex --global` (all projects)"))
        elif x in found:
            rows.append(("agents", "warn", x, f"installed but not wired. Hint: keelyard install {x}"))
        else:
            rows.append(("agents", "skip", x, "not installed"))
    return rows


def _codex_mcp_registered(root: Path) -> bool:
    import os
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for f in (home / "config.toml", root / ".codex" / "config.toml"):
        try:
            if "[mcp_servers.keelyard]" in f.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    return False


def do_doctor(a):
    from . import ui

    rows = _checks()
    bad = sum(r[1] == "bad" for r in rows)
    warn = sum(r[1] == "warn" for r in rows)
    data = {"ok": not bad, "problems": bad, "warnings": warn,
            "checks": [dict(zip(("section", "state", "check", "detail"), r, strict=True)) for r in rows]}
    if _human(a):
        ui.heading("keelyard doctor", f"v{__version__}")
        section = None
        for sec, state, label, detail in rows:
            if sec != section:
                ui.out.print(f"\n  [muted]{sec}[/]")
                section = sec
            ui.line(state, label, detail)
        ui.out.print()
        w = f"{warn} warning{'s' * (warn != 1)}"
        if bad:
            ui.out.print(f"  [bad]{bad} problem{'s' * (bad > 1)}[/] [muted]· {w}[/]")
        else:
            ui.out.print("  [ok]all good[/]" + (f" [muted]· {w}[/]" if warn else ""))
    return (CHECK_FAILED if bad else 0), data


def _ago(ts: float) -> str:
    s = max(0, int(dt.datetime.now().timestamp() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def do_status(a):
    from . import ui
    from .adapters.install import AGENTS, installed
    from .proof import Proof, gate_status
    from .snapshot import Shadow

    cfg = _cfg()
    root = cfg.root
    if not (root / ".keelyard").is_dir():
        raise Fail(f"keelyard is not set up in {root}. Hint: run `keelyard init`.")
    steps = Shadow(cfg).steps()
    sid = session.current(cfg)
    pf = Proof(cfg, sid)
    b = pf.load()
    gaps = pf.missing()
    data = {"version": __version__, "root": str(root), "agents": {x: installed(x, root) for x in AGENTS}, "steps": len(steps),
            "last_step": steps[-1].__dict__ if steps else None, "session": sid, "task": b.task,
            "requirements": b.requirements, "state": pf.state(), "proven": pf.state() == "proven", "missing": gaps,
            "gate": gate_status(cfg), "evidence": pf.levels()}
    data["guarded"] = data["gate"]["state"] == "hooked"
    if _human(a):
        ui.heading("keelyard status", ui.tilde(root))
        ui.out.print("\n  [muted]agents[/]    " + "   ".join(
            f"[ok]{ui.OK}[/] {x}" if v else f"[muted]{ui.DOT} {x}[/]" for x, v in data["agents"].items()))
        if steps:
            last = steps[-1]
            n = len(last.files)
            ui.out.print(f"  [muted]timeline[/]  {len(steps)} steps · last [key]#{last.step}[/] {last.tool}, {_ago(last.ts)}, "
                         f"{n} file{'s' * (n != 1)}")
        else:
            ui.out.print("  [muted]timeline[/]  no snapshots yet")
        blocked = sum(bool(g.get("blocked")) for g in b.gate)
        times = "once" if blocked == 1 else f"{blocked} times"
        proven_txt = "[ok]proven[/]" if data["guarded"] else "[warn]proven, not guarded[/]"
        ui.out.print("  [muted]proof[/]     " + ("[muted]no work yet[/]" if data["state"] == "no_work" else
                                               proven_txt if not gaps else f"[warn]{len(gaps)} open[/]")
                     + (f" · the gate stopped the agent {times}" if blocked else "")
                     + (f" [muted]· session {sid}[/]" if sid != "default" else ""))
        if data["state"] != "no_work":
            ui.out.print(f"  [muted]evidence  {_levels_text(data['evidence'])}[/]")
        if Shadow(cfg).interrupted():
            ui.out.print("  [bad]✗ rewind[/]   an earlier rewind did not finish. Run `keelyard rewind recover`.")
        if not data["guarded"]:
            ui.out.print(f"  [warn]{ui.WARN} gate[/]    [warn]{data['gate']['state']}[/]: {data['gate']['why']}", soft_wrap=True)
        if b.task:
            ui.out.print("  [muted]task[/]      ", end="")
            ui.plain(textwrap.shorten(b.task, 72, placeholder=" …"))
        if b.requirements or gaps:
            ui.out.print()
        for r in b.requirements:
            ui.line("ok" if r["done"] else "skip", f"{r['id']}. {textwrap.shorten(r['text'], 60, placeholder=' …')}", width=0)
        for g in gaps:
            if not g.startswith("Requirement #"):
                ui.line("warn", textwrap.shorten(g, 80, placeholder=" …"), width=0)
    return 0, data


def do_rewind(a):
    from . import ui
    from .snapshot import Shadow

    sh = Shadow(_cfg())
    h = _human(a)
    if a.action is None:
        a.action, a.interactive, a.n = "list", False, 30
    if a.action == "list":
        if a.interactive and h:
            from .tui import run

            msg = run(sh)
            if msg:
                ui.out.print(msg)
            return 0, {}
        steps = sh.steps()
        shown = steps[-a.n:]
        data = {"total": len(steps), "steps": [s.__dict__ for s in shown]}
        if not h:
            return 0, data
        if not steps:
            ui.out.print("[muted]no snapshots yet. They appear after your agent edits files, or run `keelyard rewind snap`.[/]")
            return 0, data
        if len(steps) > len(shown):
            ui.out.print(f"[muted]{len(steps) - len(shown)} earlier steps hidden; use -n {len(steps)}[/]")
        day = None
        quiet = ("baseline", "pre-rewind", "rewind", "stop", "manual")
        for s in shown:
            t = dt.datetime.fromtimestamp(s.ts)
            if t.date() != day:
                day = t.date()
                ui.out.print(f"[muted]{day:%a %d %b}[/]")
            room = max(20, ui.out.width - 36)
            # the user's own files first; agent wiring (.claude/, AGENTS.md, ...) last
            from .snapshot import WIRING, user_first

            ordered = user_first(s.files)
            mine = [f for f in ordered if not f.startswith(WIRING)]
            setup = len(ordered) - len(mine)
            if mine and setup:  # keelyard/agent setup files are not your work: count them, don't name them
                ordered = mine
            files = ", ".join(ordered[:3])
            more = f"  +{len(ordered) - 3}" if len(ordered) > 3 else ""
            if mine and setup:
                more += f"  (+{setup} agent setup)"
            if len(files) + len(more) > room:
                files = files[: max(0, room - len(more) - 1)] + "…"
            files += more
            tool = f"[muted]{s.tool:<12}[/]" if s.tool in quiet else f"{s.tool:<12}"
            ui.out.print(f"  [key]#{s.step:<4}[/][muted]{t:%H:%M:%S}[/]  {tool}  ", end="")
            ui.plain(files)
        ui.out.print("[muted]show a step: keelyard rewind show <n> · go back: keelyard rewind to <n>[/]")
        return 0, data
    if a.action == "show":
        text = sh.diff(a.step)
        if h:
            if sys.stdout.isatty():
                from rich.syntax import Syntax

                ui.out.print(Syntax(text, "diff", theme="ansi_dark", background_color="default"))
            else:
                sys.stdout.write(text)
        return 0, {"step": a.step, "files": sh.get(a.step).files, "diff": text}
    if a.action in ("to", "recover"):
        res = sh.rewind(a.step) if a.action == "to" else sh.recover()
        if h:
            if res.get("backed_up"):
                ui.line("warn", f"{len(res['backed_up'])} file(s) not in snapshots copied aside first", res["backup_dir"], width=0)
            extra = f"{len(res['removed'])} new file{'s' * (len(res['removed']) != 1)} removed" if res["removed"] else ""
            ui.line("ok", f"restored step #{res['restored']}", extra, width=0)
            ui.out.print(f"  [muted]changed your mind? keelyard rewind to {res['safety_step']}[/]")
        return 0, res
    if a.action == "fork":
        dest = sh.fork(a.step, a.dest)
        if h:
            ui.line("ok", f"step #{a.step} copied to", str(dest), width=0)
        return 0, {"step": a.step, "dest": str(dest)}
    s = sh.snapshot("manual")
    if h:
        ui.out.print(f"[ok]{ui.OK}[/] snapshot #{s.step}, {len(s.files)} changed" if s else "[muted]no changes since the last snapshot[/]")
    return 0, {"step": s.__dict__ if s else None}


def _levels_text(levels: dict) -> str:
    return " · ".join(f"{n} {k}" for k, n in reversed(list(levels.items())) if n) or "none yet"


def do_proof(a):
    from . import ui
    from .proof import Proof
    from .report import render

    cfg = _cfg()
    pf = Proof(cfg, session.current(cfg))
    h = _human(a)
    if a.action is None:
        a.action = "status"
    if a.action == "status":
        from .proof import gate_status

        gaps, state, gate, levels = pf.missing(), pf.state(), gate_status(cfg), pf.levels()
        if h:
            if state == "no_work":
                ui.out.print(f"[muted]{ui.DOT} no work yet[/]  [muted]no edits and no checklist in this session; nothing to prove[/]")
            elif gaps:
                ui.out.print("[warn]not proven yet[/]")
                for g in gaps:
                    ui.line("skip", g, width=0)
            elif gate["state"] != "hooked":
                ui.out.print(f"[warn]{ui.WARN} proven, but not guarded[/]  [muted]{gate['why']}[/]", soft_wrap=True)
            else:
                ui.out.print(f"[ok]{ui.OK} proven[/]  [muted]tests pass on the current files; every requirement has evidence[/]")
            if state != "no_work":
                ui.out.print(f"  [muted]evidence  {_levels_text(levels)}[/]")
        return (0 if not gaps else CHECK_FAILED), {"state": state, "proven": state == "proven", "missing": gaps,
                                                   "guarded": gate["state"] == "hooked", "gate": gate, "evidence": levels,
                                                   "bundle": pf.load().__dict__}
    if a.action == "accept-tests":
        ack = pf.accept_tests(a.reason)
        if h:
            ui.line("warn", "tests changed: accepted", f"recorded as agent-claimed: {ack['reason']}", width=0)
        return 0, ack
    if a.action == "reset":
        pf.path.unlink(missing_ok=True)
        if h:
            ui.line("ok", "proof reset", f"session {pf.session}: start again with a test run", width=0)
        return 0, {"reset": pf.session}
    if a.action == "add":
        reqs = pf.add_requirements(a.items)
        if h:
            for r in reqs:
                ui.line("ok" if r["done"] else "skip", f"{r['id']}. {r['text']}", width=0)
        return 0, {"requirements": reqs}
    if a.action == "check":
        r = pf.check(a.id, a.evidence)
        if h:
            ui.line("ok", f"{r['id']}. {r['text']}", r["evidence"], width=0)
        return 0, r
    if a.action == "test":
        r = pf.run_tests(a.phase, a.test_cmd)
        if h:
            ui.plain(r["tail"][-1500:].rstrip())
            ui.line("ok" if r["exit"] == 0 else "bad", f"{a.phase}: {r['cmd']}", f"exit {r['exit']} in {r['seconds']}s", width=0)
        return (0 if r["exit"] == 0 else CHECK_FAILED), {"passed": r["exit"] == 0, "run": r}
    if a.action == "shot":
        r = pf.screenshot(a.url)
        if h:
            ui.line("ok" if r.get("ok", True) else "bad", "screenshot", str(r.get("path", r)), width=0)
        return (0 if r.get("ok", True) else CHECK_FAILED), r
    path = render(pf)
    if h:
        ui.line("ok", "report", str(path), width=0)
        if a.open:
            import webbrowser

            webbrowser.open(path.as_uri())
    return 0, {"report": str(path)}


def do_run(a):
    from .filters import run_filtered

    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if cmd and cmd[0] == "--json":  # `keelyard run --json pytest` (REMAINDER swallows our own flag)
        a.json, cmd = True, cmd[1:]
    if not cmd:
        raise Fail("nothing to run. Hint: keelyard run pytest -q", code=2)
    cfg = _cfg()
    code, out = run_filtered(join_cmd(cmd), Path.cwd(), cfg.state / "raw")
    if _human(a):
        print(out)
    return code, {"exit": code, "output": out}


def do_filter(a):
    from .filters import filter_output

    out = filter_output(a.for_cmd, sys.stdin.read())
    if _human(a):
        print(out)
    return 0, {"output": out}


def do_recall(a):
    p = _cfg().state / "raw" / a.id
    if not a.id.isalnum() or not p.exists():
        raise Fail(f"no saved output {a.id!r}. Hint: use the id printed at the end of `keelyard run` output.")
    text = p.read_text(errors="replace", encoding="utf-8")
    if _human(a):
        sys.stdout.write(text)
    return 0, {"id": a.id, "output": text}


def do_code(a):
    from . import codemap
    from .saver import normalize_path

    if a.action == "outline":
        path = Path(normalize_path(str(a.path)))
        if not path.is_file():
            raise Fail(f"{a.path}: no such file")
        text, data = codemap.outline(path), {"path": str(a.path)}
        data["outline"] = text
    elif a.action == "map":
        text = codemap.project_map(_cfg().root)
        data = {"map": text}
    elif a.action == "related":
        try:
            data = codemap.related(_cfg().root, str(a.path))
        except FileNotFoundError as e:
            raise Fail(str(e)) from None
        text = "\n".join(f"{k.replace('_', ' ')}: {', '.join(v) or '-'}" for k, v in data.items() if k != "file")
    else:
        root = _cfg().root
        hits = codemap.find_symbol_ranked(root, a.name)
        if a.refs:
            hits += [f"use {r}" for r in codemap.references(root, a.name)]
        pg = codemap.page(hits, a.offset, a.limit)
        data = {"name": a.name, "matches": pg["items"], "total": pg["total"], "remaining": pg["remaining"],
                **({"next_offset": pg["next_offset"]} if "next_offset" in pg else {})}
        text = "\n".join(pg["items"]) or f"no definition of {a.name} found"
        if pg["remaining"]:
            text += (f"\n… {pg['remaining']} more; next page: keelyard code find {a.name}"
                     f"{' --refs' if a.refs else ''} --offset {pg['next_offset']}")
    if _human(a):
        print(text)
    return 0, data


def do_memory(a):
    from . import ui
    from .memory import Memory, format_hits

    mem = Memory()
    h = _human(a)
    if a.action == "index":
        if h:
            with ui.err.status("reading sessions from Claude Code, Codex and OpenCode"):
                res = mem.ingest()
            for k, v in res.items():
                ui.line("ok" if v else "skip", k, f"{v} new chunks")
        else:
            res = mem.ingest()
        return 0, {"new_chunks": res}
    if a.action == "stats":
        st = mem.stats()
        if h:
            if not st:
                ui.out.print("[muted]nothing indexed yet. Run `keelyard memory index`.[/]")
            for k, v in st.items():
                ui.line("ok", k, f"{v['sessions']} sessions, {v['chunks']} chunks")
        return 0, {"agents": st}
    res = mem.search_page(a.query, a.project, a.budget, offset=a.offset)
    if h:
        print(format_hits(res["hits"]))
        if res["remaining"]:
            print(f"… {res['remaining']} more; next page: keelyard memory search {shlex.quote(a.query)} --offset {res['next_offset']}")
    return 0, {"query": a.query, **res}


def do_state(a):
    from .workstate import WorkState

    cfg = _cfg()
    ws = WorkState(cfg, session.current(cfg))
    try:
        if a.action in ("decision", "question"):
            ws.add(a.action, a.text, a.ref)
        elif a.action == "resolve":
            ws.resolve(int(a.text or 0))
    except (ValueError, KeyError) as e:
        raise Fail(str(e).strip("'\"")) from None
    text = ws.render(2000)
    if _human(a):
        print(text or "no work state yet. Record a decision: keelyard state decision \"...\" --ref path:line")
    return 0, {"state": text, "items": ws.load()}


def do_usage(a):
    from .usage import collect, table

    rows = collect(_cfg().raw.get("prices", {}), by=a.by)
    if _human(a):
        print(table(rows))
    return 0, {"by": a.by, "rows": [{"key": k, "input": r.input, "output": r.output, "cache_read": r.cache_read,
                                     "cache_write": r.cache_write, "cost_usd": round(r.cost, 4) if r.priced else None}
                                    for k, r in rows.items()]}


def do_swarm(a):
    from .swarm import Swarm, Task, format_results, plan, subagent_tasks

    if a.action == "plan":
        cfg = _cfg()
        ts = plan(cfg, a.goal, a.files)
        if not ts:
            raise Fail("found no file for that goal. Hint: name a function or file, or pass --files.")
        if a.subagents:
            data = {"subagent_tasks": subagent_tasks(cfg, ts)}
            if _human(a):
                for s in data["subagent_tasks"]:
                    print(f"--- task {s['id']} [{s['kind']}] {', '.join(s['files'])}\n{s['subagent_prompt']}\n")
            return 0, data
        tasks = [{"kind": t.kind, "prompt": t.prompt, "files": t.files, "symbols": t.symbols} for t in ts]
        if _human(a):
            print(json.dumps(tasks, indent=2))
        return 0, {"tasks": tasks}
    try:
        raw = json.loads(a.tasks.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("ok") is False:
            raise Fail(f"{a.tasks} holds an error from `swarm plan`, not tasks: {raw.get('error')}. "
                       f"Hint: {raw.get('hint') or 'fix the plan first'}")
        if isinstance(raw, dict) and "tasks" in raw:  # straight from `keelyard swarm plan --json > tasks.json`
            raw = raw["tasks"]
        if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
            raise TypeError("expected a list of objects")
        known = {f.name for f in dataclasses.fields(Task)}
        tasks = [Task(**{k: v for k, v in t.items() if k in known}) for t in raw]
        if not tasks:
            raise Fail(f"{a.tasks} has no tasks. Hint: `keelyard swarm plan \"<goal>\" --json > {a.tasks}`")
    except FileNotFoundError:
        raise Fail(f"{a.tasks}: no such file") from None
    except (ValueError, TypeError) as e:
        raise Fail(f"{a.tasks} must be a JSON list of {{kind, prompt, files}} objects ({e}). "
                   f"Hint: `keelyard swarm plan \"<goal>\" --json > {a.tasks}` writes one") from None
    cfg = _cfg()
    from .swarm import preflight

    key = a.model or cfg.section("swarm").get("helper_model", "cheap")
    try:
        problem = preflight(cfg.model(key))
    except KeyError as e:
        raise Fail(f"{e.args[0]}. Hint: add a [models.{key}] section, or pick one with --model") from None
    if problem:
        raise Fail(f"helper model [models.{key}]: {problem}")
    extra = {}
    if cfg.section("swarm").get("cooperative", True) and not a.basic:
        from .coop import Coop, summary

        sw = Coop(cfg, model_key=a.model, best_of=a.best_of, retries=a.retries)
        results = asyncio.run(sw.run(tasks))
        extra = summary(sw, results)
    else:
        results = asyncio.run(Swarm(cfg, a.model, best_of=a.best_of, retries=a.retries).run(tasks))
    if _human(a):
        print(format_results(results))
        if extra:
            print(f"tokens {extra['tokens']:,} · {extra['calls']} calls · by model {extra['tokens_by_model']}")
    if results and not any(r.ok for r in results):
        code = CHECK_FAILED  # every helper call failed: scripts must not read that as success
    else:
        code = 0
    return code, {"verified": sum(r.verified for r in results), "total": len(results), **extra,
               "results": [r.__dict__ for r in results]}


def do_arena(a):
    from . import arena, ui

    if a.action == "run":
        if not a.spec.is_file():
            raise Fail(f"{a.spec}: no such file. Hint: see examples/arena/discount/task.toml")
        h = _human(a)

        def tick(name, i, n):
            if h:
                ui.err.print(f"  [muted]{name}: run {i}/{n} done[/]", soft_wrap=True)

        res = arena.run_task(a.spec, a.out, a.only, progress=tick)
        report = a.out / "report.html"
        rows = arena.summarize([dataclasses.asdict(r) for r in res])
        data = {"runs": len(res), "report": str(report), "summary": rows}
        if h:
            ui.out.print()
            for r in rows:
                passed = round(r["pass_rate"] * r["runs"])
                tok = arena._fmt(r["median_input"])
                detail = (f"{passed}/{r['runs']} passed · median {arena._fmt(r['median_seconds'], 's')}"
                          + (f" · input {tok} tokens" if r["median_input"] is not None else "")
                          + (f" · {r['note']}" if r["note"] else ""))
                ui.line("ok" if passed == r["runs"] else ("warn" if passed else "bad"), f"{r['agent']} ({r['model']})", detail,
                        width=0)
    else:
        if not (a.out / "results.json").exists():
            raise Fail(f"no results.json in {a.out}. Hint: run `keelyard arena run <spec>` first.")
        report = arena.render(a.out)
        data = {"report": str(report)}
    if _human(a):
        ui.line("ok", "report", str(report), width=0)
    return 0, data


def do_bench(a):
    from .bench import run_all

    res = run_all(_cfg())
    if _human(a):
        print(res["text"])
    return 0, {k: v for k, v in res.items() if k != "text"}


def do_selftest(a):
    from . import selftest, ui

    h = _human(a)
    if h:
        with ui.err.status("a scripted agent is working through a task"):
            rep = selftest.run(keep=a.keep)
        ui.heading("keelyard selftest", "MCP over stdio + Claude Code hook payloads, in a temp project")
        ui.out.print()
        for c in rep.checks:
            ui.line("ok" if c.ok else "bad", c.name, c.detail, width=48)
        n = sum(c.ok for c in rep.checks)
        ui.out.print(f"\n  {'[ok]' if rep.ok else '[bad]'}{n}/{len(rep.checks)} passed[/]" + (f"  [muted]kept in {rep.workdir}[/]" if a.keep else ""))
    else:
        rep = selftest.run(keep=a.keep)
    return (0 if rep.ok else CHECK_FAILED), {"ok": rep.ok, "checks": [c.__dict__ for c in rep.checks],
                                              "workdir": rep.workdir if a.keep else None}


def do_install(a):
    from . import ui
    from .adapters import install as inst

    if a.glob:
        return _do_global(a, remove=a.cmd == "uninstall")
    if not a.agent:
        raise Fail(f"name an agent: keelyard {a.cmd} claude|codex|opencode, or use --global.")
    root = config.find_root()
    if a.cmd == "install":
        config.init(root, lean=getattr(a, "preset", "lean") == "lean")
    if a.cmd == "install":
        done = inst.install(a.agent, root, preset=getattr(a, "preset", "lean"), codex_project_mcp=getattr(a, "codex_project_mcp", False),
                            terse=None if getattr(a, "terse", True) else False)
    else:
        done = inst.uninstall(a.agent, root)
    if _human(a):
        for n, item in enumerate(done or ["nothing to remove"]):
            first, *rest = item.splitlines()
            ui.line("ok", a.agent if n == 0 else "", first)
            for r in rest:
                ui.out.print("      ", end="")
                ui.out.print(r, style="muted", markup=False)
    return 0, {"agent": a.agent, "action": a.cmd, "changed": done}


do_uninstall = do_install


def _print_json(data) -> None:
    sys.stdout.write(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows pipes default to cp1252: → … and non-Latin text break
        if not isinstance(stream, io.TextIOWrapper):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass
    ap = _parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--json"] and len(argv) > 1:  # `keelyard --json status` == `keelyard status --json`
        argv = [argv[1], "--json", *argv[2:]]
    want_json = "--json" in argv[:argv.index("--")] if "--" in argv else "--json" in argv
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        if want_json and e.code not in (0, None):
            _print_json({"ok": False, "exit": 2, "error": "usage error; run with --help"})
        return int(e.code or 0)
    if not a.cmd:
        ap.print_help()
        return 0
    if a.cmd == "hook":
        from .hooks import main as hook_main

        return hook_main(a.agent, a.event)
    if a.cmd == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0

    from . import ui
    from .adapters.install import InstallError
    from .snapshot import SnapshotError

    try:
        code, data = globals()[f"do_{a.cmd}"](a)
    except KeyboardInterrupt:
        ui.err.print("[muted]interrupted[/]")
        return 130
    except (Fail, SnapshotError, InstallError, config.ProjectError, ValueError, KeyError, PermissionError,
            FileNotFoundError, NotADirectoryError, TimeoutError) as e:
        msg = str(e.args[0] if isinstance(e, KeyError) and e.args else e)
        code = e.code if isinstance(e, Fail) else 1
        if getattr(a, "json", False):
            text, _, hint = msg.partition("Hint:")
            _print_json({"ok": False, "exit": code, "error": text.strip(), "hint": hint.strip() or None})
        else:
            ui.error(msg)
        return code
    if getattr(a, "json", False):
        _print_json({**(data if isinstance(data, dict) else {"data": data}), "ok": code == 0, "exit": code})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
