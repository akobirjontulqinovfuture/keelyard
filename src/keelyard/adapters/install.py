"""`keelyard install <agent>`: wire hooks + MCP into a project for one agent."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from importlib import resources
from pathlib import Path

CLAUDE_EVENTS = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PreCompact": None,
    "PreToolUse": "Read|Bash|Edit|Write|MultiEdit",  # dedupe reads, snapshot before destructive commands, nudges
    "PostToolUse": "Write|Edit|MultiEdit|NotebookEdit|Bash|Read",  # Bash: snapshot + output filter
    "Stop": None,
}
CODEX_EVENTS = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PreToolUse": "Bash|shell|apply_patch|Edit|Write",
    "PostToolUse": "Bash|apply_patch|Edit|Write",
    "Stop": None,
}


def _exe_path() -> str:
    """The keelyard executable, unquoted: for configs that exec a program directly (MCP)."""
    return shutil.which("keelyard") or "keelyard"


def _exe() -> str:
    """The keelyard command as a shell-safe string, for hook commands run by a shell (paths may contain spaces)."""
    exe = _exe_path()
    if os.name == "nt":  # pragma: no cover
        return subprocess.list2cmdline([exe])
    return shlex.quote(exe)


def _is_ours(cmd: str) -> bool:
    return "keelyard" in cmd and " hook " in cmd


class InstallError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise InstallError(f"{path} is not valid JSON ({e.msg}, line {e.lineno}). "
                           "Hint: fix or remove that file, then run `keelyard init` again. Nothing was changed.") from None
    except UnicodeDecodeError:
        raise InstallError(f"{path} is not UTF-8 text. "
                           "Hint: fix or remove that file, then run `keelyard init` again. Nothing was changed.") from None
    if not isinstance(data, dict):
        raise InstallError(f"{path} must contain a JSON object. Hint: fix it, then run `keelyard init` again.")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".keelyard-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _merge_hooks(path: Path, agent: str, events: dict[str, str | None]) -> None:
    data = _read_json(path)
    hooks = data.setdefault("hooks", {})
    for event, matcher in events.items():
        cmd = f"{_exe()} hook {agent} {event}"
        groups = hooks.setdefault(event, [])
        # idempotent: drop any previous keelyard entry for this event
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not _is_ours(h.get("command", ""))]
        groups[:] = [g for g in groups if g.get("hooks")]
        entry: dict = {"hooks": [{"type": "command", "command": cmd, "timeout": 900 if event == "Stop" else 30}]}
        if matcher:
            entry["matcher"] = matcher
        groups.append(entry)
    _write_json(path, data)


def _merge_json(path: Path, key: str, name: str, value: dict) -> None:
    data = _read_json(path)
    data.setdefault(key, {})[name] = value
    _write_json(path, data)


AGENTS = ("claude", "codex", "opencode")
FILES = {
    "claude": [(".claude/settings.json", "hooks"), (".mcp.json", "mcpServers")],
    "codex": [(".codex/hooks.json", "hooks")],
    "opencode": [("opencode.json", "mcp")],
}
BINARIES = {"claude": "claude", "codex": "codex", "opencode": "opencode"}
HOMES = {"claude": "~/.claude", "codex": "~/.codex", "opencode": "~/.local/share/opencode"}


def detect() -> dict[str, str]:
    """Agents found on this machine: name -> how we know (binary path or config dir)."""
    found = {}
    for agent in AGENTS:
        exe = shutil.which(BINARIES[agent])
        home = Path(os.path.expanduser(HOMES[agent]))
        if exe:
            found[agent] = exe
        elif home.is_dir():
            found[agent] = str(home)
    return found


def installed(agent: str, root: Path) -> bool:
    if agent == "opencode":
        return any((root / ".opencode" / d / "keelyard.js").exists() for d in ("plugins", "plugin"))
    path = root / FILES[agent][0][0]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "keelyard" in text and " hook " in text


START, END = "<!-- keelyard:start -->", "<!-- keelyard:end -->"
GUIDE_FILE = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "opencode": "AGENTS.md"}
CLAUDE_COMMANDS = {"status": "cmd_status.md", "prove": "cmd_prove.md", "undo": "cmd_undo.md"}


WORKFLOWS = {"plan": "plan, implement, verify (any multi-step change)",
             "debug": "reproduce, hypothesise, test, fix the cause",
             "review": "check a diff: correct, safe, tested, small, clear"}


def workflow_text(name: str) -> str:
    if name not in WORKFLOWS:
        raise ValueError(f"unknown workflow {name!r}. Hint: one of {', '.join(WORKFLOWS)}.")
    return _guide(f"workflows/{name}.md")


def _command(name: str) -> str:
    """A slash command file: only the one-line description is in context until the command is used."""
    return f"---\ndescription: keelyard workflow: {WORKFLOWS[name]}\n---\n{workflow_text(name)}"


PRESETS = {
    "lean": "default (keelyard init): fewest tokens: trimmed agent prompt and tools, no MCP catalog, short rules, capped tool output",
    "balanced": "every agent tool kept: hooks, proof from shell test runs, filtered output; MCP only where it is cheap",
    "max-quality": "every keelyard tool over MCP, full rules; costs more tokens per turn",
}
_PRESET = "balanced"  # the preset of the install in progress

# OpenCode 2 `agents.build` override for the lean preset. A non-empty `system` replaces OpenCode's base prompt;
# the denied tools drop out of the tool list. Shell, read, edit and write stay (the brief carries the file map).
LEAN_OPENCODE_BUILD = {
    "system": ("You are a coding agent in the user's repository. Your context holds the project files and test "
               "results (keelyard ran them): do not read, list, grep or test before editing. A question: answer from "
               "that context, no tools. A change: make it, all edits in one turn (parallel calls; one same change in many "
               "files, e.g. a rename: ONE sed/perl command). Never claim a change without a tool call for it. "
               "keelyard reruns the tests after each edit and appends the result: that is the test run the user "
               "asked for, whatever command they named; do not run tests yourself. Then reply in one line."),
    "permissions": [{"action": a, "resource": "*", "effect": "deny"}
                    for a in ("webfetch", "websearch", "subagent", "skill", "question", "todowrite", "execute", "glob", "grep")],
}
# The same override for OpenCode 1.x, which rejects V2 `agents`/`permissions` ("Configuration is invalid"):
# V1 uses `agent.build.prompt` and a `permission` map; todowrite/todoread have no permission key, so `tools` hides them.
LEAN_OPENCODE_BUILD_V1 = {
    "prompt": LEAN_OPENCODE_BUILD["system"],
    "permission": {k: "deny" for k in ("webfetch", "websearch", "task", "skill", "question", "glob", "grep", "list", "lsp")},
    "tools": {"todowrite": False, "todoread": False},
}


def _is_lean_build(build, want: dict) -> bool:
    """keelyard's lean build agent, this version's or an earlier one's (same keys, prompt from keelyard)."""
    if not isinstance(build, dict) or set(build) != set(want):
        return False
    text = build.get("system") or build.get("prompt") or ""
    return build == want or text.startswith("You are a coding agent in the user's repository. Your context")


def _opencode_major() -> int:
    """Major version of the installed OpenCode (2 when unknown: the V2 config is what OpenCode 2 needs)."""
    exe = shutil.which("opencode")
    if not exe:
        return 2
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return 2
    m = re.search(r"(\d+)\.\d+", out or "")
    return int(m.group(1)) if m else 2


# Claude Code settings.json env for the lean preset (documented Claude Code variables).
LEAN_CLAUDE_ENV = {"BASH_MAX_OUTPUT_LENGTH": "12000", "MAX_MCP_OUTPUT_TOKENS": "8000",
                   "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1"}


def _preset_file(root: Path) -> Path:
    return root / ".keelyard" / "preset.json"


def _apply_preset(agent: str, root: Path, preset: str) -> list[str]:
    """Write the preset's agent settings and remember exactly what was added, so uninstall can take it back."""
    pf = _preset_file(root)
    rec = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    rec["preset"] = preset
    rec["terse"] = _TERSE
    added = rec.setdefault("added", {})
    done = []
    cfg_path = root / ".keelyard" / "config.toml"
    if preset == "lean" and cfg_path.exists():
        from ..config import apply_lean

        cfg_path.write_text(apply_lean(cfg_path.read_text(encoding="utf-8")), encoding="utf-8")
        done.append(".keelyard/config.toml (lean: project files in the brief, tests run after each edit)")
    if preset == "lean" and agent == "opencode":
        path = root / "opencode.json"
        data = _read_json(path)
        v1 = _opencode_major() < 2
        key, want = ("agent", LEAN_OPENCODE_BUILD_V1) if v1 else ("agents", LEAN_OPENCODE_BUILD)
        agents = data.setdefault(key, {})
        if "build" not in agents or _is_lean_build(agents["build"], want):
            agents["build"] = want
            _write_json(path, data)
            added["opencode_build"] = key
            done.append("opencode.json (lean build agent: short prompt, 9 tools off)")
    if preset == "lean" and agent == "claude":
        path = root / ".claude" / "settings.json"
        data = _read_json(path)
        env = data.setdefault("env", {})
        keys = [k for k, v in LEAN_CLAUDE_ENV.items() if k not in env]
        for k in keys:
            env[k] = LEAN_CLAUDE_ENV[k]
        _write_json(path, data)
        added["claude_env"] = sorted(set(added.get("claude_env", [])) | set(keys))
        done.append(".claude/settings.json (lean env: capped tool output, no extra model calls)")
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return done


def _remove_preset(agent: str, root: Path) -> list[str]:
    pf = _preset_file(root)
    if not pf.exists():
        return []
    rec = json.loads(pf.read_text(encoding="utf-8"))
    added, done = rec.get("added", {}), []
    if agent == "opencode" and added.pop("opencode_build", None):
        path = root / "opencode.json"
        data = _read_json(path) if path.exists() else {}
        for key, want in (("agents", LEAN_OPENCODE_BUILD), ("agent", LEAN_OPENCODE_BUILD_V1)):
            if _is_lean_build(data.get(key, {}).get("build"), want):
                del data[key]["build"]
                if not data[key]:
                    del data[key]
                _write_json(path, data)
                done.append("opencode.json (lean build agent)")
    if agent == "claude" and added.get("claude_env"):
        path = root / ".claude" / "settings.json"
        data = _read_json(path) if path.exists() else {}
        env = data.get("env", {})
        for k in added.pop("claude_env"):
            if env.get(k) == LEAN_CLAUDE_ENV[k]:
                env.pop(k)
        if "env" in data and not env:
            del data["env"]
        _write_json(path, data)
        done.append(".claude/settings.json (lean env)")
    if added:
        pf.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    else:
        pf.unlink()
    return done


def opencode_mcp() -> bool:
    """OpenCode 2 puts every MCP tool in a code-mode catalog in context, which costs more than it saves on most
    tasks. keelyard works without it there (proof from shell test runs); KEELYARD_OPENCODE_MCP=1 adds the server."""
    return _PRESET == "max-quality" or os.environ.get("KEELYARD_OPENCODE_MCP", "") in ("1", "true", "yes")


def _plugin_js() -> str:
    js = resources.files("keelyard.adapters").joinpath("opencode_plugin.js").read_text(encoding="utf-8")
    js = js.replace('const KEELYARD = "keelyard";', f"const KEELYARD = {json.dumps(_exe_path())};", 1)
    return js.replace("const MCP = false;", f"const MCP = {'true' if opencode_mcp() else 'false'};", 1)


def _guide(name: str) -> str:
    return resources.files("keelyard.adapters").joinpath("guides", name).read_text(encoding="utf-8")


_TERSE = True  # the terse-output rule of the install in progress (opt out: --no-terse or KEELYARD_TERSE=0)


def terse_default() -> bool:
    return os.environ.get("KEELYARD_TERSE", "1").strip().lower() not in ("0", "false", "no", "off")


def _rules(name: str, terse: bool | None = None) -> str:
    """The agent instructions block, with the terse-output rule unless the user opted out."""
    body = _guide(name)
    if _TERSE if terse is None else terse:
        body = body.rstrip("\n") + "\n" + _guide("terse.md")
    return body


def _set_block(path: Path, body: str | None) -> bool:
    """Insert, replace or (body=None) remove the keelyard block; the rest of the file is untouched."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = text.find(START), text.find(END)
    if start != -1 and end > start:
        before, after = text[:start].rstrip("\n"), text[end + len(END):].lstrip("\n")
    else:
        if body is None:
            return False
        before, after = text.rstrip("\n"), ""
    parts = [x for x in (before, f"{START}\n{body.strip()}\n{END}" if body is not None else "", after) if x]
    new = "\n\n".join(parts) + "\n" if parts else ""
    if body is None and not new.strip():
        path.unlink()
        return True
    path.write_text(new, encoding="utf-8")
    return True


def install_guides(agent: str, root: Path) -> list[str]:
    """Teach the agent the workflow: an instructions block, plus a skill and slash commands for Claude Code."""
    done = []
    lean = (_PRESET == "lean" or (agent == "opencode" and not opencode_mcp())) and _PRESET != "max-quality" \
        and not (agent == "opencode" and installed("codex", root) and _PRESET != "lean")
    _set_block(root / GUIDE_FILE[agent], _rules("agents_block_lean.md" if lean else "agents_block.md"))
    done.append(f"{GUIDE_FILE[agent]} (keelyard section)")
    if agent == "claude":
        skill = root / ".claude" / "skills" / "keelyard" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(_guide("SKILL.md"), encoding="utf-8")
        cmds = root / ".claude" / "commands" / "keelyard"
        cmds.mkdir(parents=True, exist_ok=True)
        for name, src in CLAUDE_COMMANDS.items():
            (cmds / f"{name}.md").write_text(_guide(src), encoding="utf-8")
        for name in WORKFLOWS:
            (cmds / f"{name}.md").write_text(_command(name), encoding="utf-8")
        done += [".claude/skills/keelyard/SKILL.md (skill)",
                 "/keelyard:status, :prove, :undo, :plan, :debug, :review (slash commands)"]
    if agent == "opencode":
        cmds = root / ".opencode" / "command"
        cmds.mkdir(parents=True, exist_ok=True)
        for name in WORKFLOWS:
            (cmds / f"keelyard-{name}.md").write_text(_command(name), encoding="utf-8")
        done.append("/keelyard-plan, /keelyard-debug, /keelyard-review (commands)")
    return done


def uninstall_guides(agent: str, root: Path) -> list[str]:
    done = []
    others = [a for a in AGENTS if a != agent and GUIDE_FILE[a] == GUIDE_FILE[agent] and installed(a, root)]
    if not others and _set_block(root / GUIDE_FILE[agent], None):
        done.append(f"{GUIDE_FILE[agent]} (keelyard section)")
    if agent == "claude":
        skill = root / ".claude" / "skills" / "keelyard"
        cmds = root / ".claude" / "commands" / "keelyard"
        for d in (skill, cmds):
            if d.is_dir():
                shutil.rmtree(d)
                done.append(str(d.relative_to(root)))
    if agent == "opencode":
        for name in WORKFLOWS:
            f = root / ".opencode" / "command" / f"keelyard-{name}.md"
            if f.exists():
                f.unlink()
                done.append(str(f.relative_to(root)))
    return done


def install(agent: str, root: Path, preset: str = "lean", codex_project_mcp: bool = False,
            terse: bool | None = None) -> list[str]:
    global _PRESET, _TERSE
    if agent not in AGENTS:
        raise InstallError(f"unknown agent {agent!r}. Hint: choose one of {', '.join(AGENTS)}.")
    if preset not in PRESETS:
        raise InstallError(f"unknown preset {preset!r}. Hint: choose one of {', '.join(PRESETS)}.")
    _PRESET = preset
    _TERSE = terse_default() if terse is None else terse
    _remember_originals(agent, root)
    try:
        return _install(agent, root, codex_project_mcp) + _apply_preset(agent, root, preset)
    finally:
        _PRESET, _TERSE = "balanced", True


def codex_mcp_block() -> str:
    return f"[mcp_servers.keelyard]\ncommand = {json.dumps(_exe_path())}\nargs = [\"mcp\"]"


def _install(agent: str, root: Path, codex_project_mcp: bool = False) -> list[str]:
    for rel, _ in FILES[agent]:  # validate everything first, so a bad file never leaves a half install
        _read_json(root / rel)
    done = []
    if agent == "claude":
        _merge_hooks(root / ".claude" / "settings.json", "claude", CLAUDE_EVENTS)
        _merge_json(root / ".mcp.json", "mcpServers", "keelyard", {"command": _exe_path(), "args": ["mcp"]})
        done += [".claude/settings.json (hooks)", ".mcp.json (MCP server)"]
    elif agent == "codex":
        _merge_hooks(root / ".codex" / "hooks.json", "codex", CODEX_EVENTS)
        done.append(".codex/hooks.json (hooks; approve them in Codex with /hooks)")
        if codex_project_mcp:
            _toml_block(root / ".codex" / "config.toml", codex_mcp_block())
            done.append(".codex/config.toml (MCP server; Codex loads it once you trust this project)")
        else:
            done.append(f"add to ~/.codex/config.toml:\n{codex_mcp_block()}\n"
                        "or rerun with --codex-project-mcp to put it in this project's .codex/config.toml")
    elif agent == "opencode":
        legacy = root / ".opencode" / "plugin" / "keelyard.js"  # keelyard <= 0.1 put it here; OpenCode 2 loads both dirs
        if legacy.exists():
            legacy.unlink()
        dest = root / ".opencode" / "plugins" / "keelyard.js"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_plugin_js(), encoding="utf-8")
        done.append(".opencode/plugins/keelyard.js (events)")
        if opencode_mcp():
            _merge_json(root / "opencode.json", "mcp", "keelyard", {"type": "local", "command": [_exe_path(), "mcp"], "enabled": True})
            done.append("opencode.json (MCP server)")
    return done + install_guides(agent, root)


def uninstall(agent: str, root: Path) -> list[str]:
    """Remove only keelyard's entries; everything else in those files is left as it was."""
    if agent not in AGENTS:
        raise InstallError(f"unknown agent {agent!r}. Hint: choose one of {', '.join(AGENTS)}.")
    done = _remove_preset(agent, root)
    for rel, key in FILES[agent]:
        path = root / rel
        if not path.exists():
            continue
        data = _read_json(path)
        if key == "hooks":
            for event, groups in list(data.get("hooks", {}).items()):
                for g in groups:
                    g["hooks"] = [h for h in g.get("hooks", []) if not _is_ours(h.get("command", ""))]
                groups[:] = [g for g in groups if g.get("hooks")]
                if not groups:
                    del data["hooks"][event]
            if not data.get("hooks"):
                data.pop("hooks", None)
        elif data.get(key, {}).pop("keelyard", None) is not None and not data[key]:
            data.pop(key)
        _write_json(path, data)
        done.append(rel)
    for d in ("plugins", "plugin"):
        plugin = root / ".opencode" / d / "keelyard.js"
        if agent == "opencode" and plugin.exists():
            plugin.unlink()
            done.append(f".opencode/{d}/keelyard.js")
    toml = root / ".codex" / "config.toml"
    if agent == "codex" and toml.exists() and TOML_START in toml.read_text(encoding="utf-8"):
        _toml_block(toml, None)
        if not toml.read_text(encoding="utf-8").strip():
            toml.unlink()
        done.append(".codex/config.toml (MCP server)")
    done += uninstall_guides(agent, root)
    _restore_originals(agent, root)
    return done


TOUCHED = {"claude": [".claude/settings.json", ".mcp.json", "CLAUDE.md"],
           "codex": [".codex/hooks.json", ".codex/config.toml", "AGENTS.md"],
           "opencode": ["opencode.json", "AGENTS.md"]}
EMPTY_DIRS = {"claude": [".claude/commands", ".claude/skills", ".claude"], "codex": [".codex"],
              "opencode": [".opencode/plugins", ".opencode/plugin", ".opencode/command", ".opencode/agent", ".opencode"]}
ABSENT = b"\0keelyard: file did not exist\0"


def _original(root: Path, rel: str) -> Path:
    return root / ".keelyard" / "originals" / (rel.replace("/", "__") + ".orig")


def _remember_originals(agent: str, root: Path) -> None:
    """Before the first install, keep each config file's exact bytes so uninstall can give them back untouched."""
    for rel in TOUCHED[agent]:
        keep = _original(root, rel)
        if keep.exists():
            continue
        keep.parent.mkdir(parents=True, exist_ok=True)
        src = root / rel
        keep.write_bytes(src.read_bytes() if src.exists() else ABSENT)


def _same(cur: bytes, orig: bytes, rel: str) -> bool:
    if rel.endswith(".json"):
        try:
            return json.loads(cur or b"{}") == json.loads(orig or b"{}")
        except ValueError:
            return False
    norm = lambda b: b.replace(b"\r\n", b"\n").strip()  # noqa: E731 - our block edits may change newlines only
    return norm(cur) == norm(orig)


def _restore_originals(agent: str, root: Path) -> None:
    """After keelyard's entries are stripped: a file keelyard created and left empty is deleted, a file that is
    back to its original content gets its exact original bytes back (formatting, newlines), and empty folders
    keelyard made go away. A file you changed since install is left alone. Shared files (AGENTS.md) wait until
    the last agent using them is uninstalled."""
    for rel in TOUCHED[agent]:
        keep, path = _original(root, rel), root / rel
        if not keep.exists():
            continue
        cur = path.read_bytes() if path.exists() else None
        if cur is not None and (START.encode() in cur or TOML_START.encode() in cur):
            continue  # another agent still has its block here
        orig = keep.read_bytes()
        if orig == ABSENT:
            if cur is not None and _same(cur, b"", rel):
                path.unlink()
        elif cur is not None and _same(cur, orig, rel):
            path.write_bytes(orig)
        keep.unlink()
    for rel in EMPTY_DIRS[agent]:
        d = root / rel
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


# --------------------------------------------------------------------------- global (user-level) install
TOML_START, TOML_END = "# keelyard:start", "# keelyard:end"


def global_paths(home: Path | None = None) -> dict[str, Path]:
    h = home or Path.home()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    oc = Path(xdg) / "opencode" if xdg else h / ".config" / "opencode"
    return {"claude_settings": h / ".claude" / "settings.json", "claude_json": h / ".claude.json",
            "claude_md": h / ".claude" / "CLAUDE.md", "claude_dir": h / ".claude",
            "codex_toml": h / ".codex" / "config.toml", "codex_hooks": h / ".codex" / "hooks.json",
            "codex_md": h / ".codex" / "AGENTS.md",
            "opencode_json": oc / "opencode.json", "opencode_plugin": oc / "plugins" / "keelyard.js",
            "opencode_md": oc / "AGENTS.md", "opencode_dir": oc}


def _backup(path: Path) -> None:
    """Keep the user's original once: <file>.keelyard-backup (never overwritten)."""
    bak = path.with_name(path.name + ".keelyard-backup")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def _toml_block(path: Path, body: str | None) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = re.sub(rf"\n?{re.escape(TOML_START)}\n.*?{re.escape(TOML_END)}\n?", "\n", text, flags=re.S).rstrip("\n")
    if body:
        text = (text + "\n\n" if text else "") + f"{TOML_START}\n{body.strip()}\n{TOML_END}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n" if text else "", encoding="utf-8")


def _strip_hooks(path: Path) -> bool:
    if not path.exists():
        return False
    data = _read_json(path)
    for event, groups in list(data.get("hooks", {}).items()):
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not _is_ours(h.get("command", ""))]
        groups[:] = [g for g in groups if g.get("hooks")]
        if not groups:
            del data["hooks"][event]
    if not data.get("hooks"):
        data.pop("hooks", None)
    _write_json(path, data)
    return True


def _drop_key(path: Path, key: str) -> bool:
    if not path.exists():
        return False
    data = _read_json(path)
    if data.get(key, {}).pop("keelyard", None) is None:
        return False
    if not data[key]:
        data.pop(key)
    _write_json(path, data)
    return True


def install_global(agent: str, home: Path | None = None, terse: bool | None = None) -> list[str]:
    """Wire one agent for every project of this user. Existing files are backed up once and merged, never replaced."""
    if agent not in AGENTS:
        raise InstallError(f"unknown agent {agent!r}. Hint: choose one of {', '.join(AGENTS)}.")
    p = global_paths(home)
    for k in ("claude_settings", "claude_json", "codex_hooks", "opencode_json"):
        _read_json(p[k])  # validate first: a broken file must never leave a half install
    terse = terse_default() if terse is None else terse
    rules = _rules("agents_block.md", terse)
    done = []
    if agent == "claude":
        for k in ("claude_settings", "claude_json", "claude_md"):
            _backup(p[k])
        _merge_hooks(p["claude_settings"], "claude", CLAUDE_EVENTS)
        _merge_json(p["claude_json"], "mcpServers", "keelyard", {"type": "stdio", "command": _exe_path(), "args": ["mcp"]})
        _set_block(p["claude_md"], rules)
        skill = p["claude_dir"] / "skills" / "keelyard" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(_guide("SKILL.md"), encoding="utf-8")
        cmds = p["claude_dir"] / "commands" / "keelyard"
        cmds.mkdir(parents=True, exist_ok=True)
        for name, src in CLAUDE_COMMANDS.items():
            (cmds / f"{name}.md").write_text(_guide(src), encoding="utf-8")
        for name in WORKFLOWS:
            (cmds / f"{name}.md").write_text(_command(name), encoding="utf-8")
        done += [str(p["claude_settings"]), str(p["claude_json"]), str(p["claude_md"])]
        _global_lean(agent, p, True)
    elif agent == "codex":
        for k in ("codex_toml", "codex_hooks", "codex_md"):
            _backup(p[k])
        _merge_hooks(p["codex_hooks"], "codex", CODEX_EVENTS)
        _toml_block(p["codex_toml"], f"[mcp_servers.keelyard]\ncommand = {json.dumps(_exe_path())}\nargs = [\"mcp\"]")
        _set_block(p["codex_md"], rules)
        done += [str(p["codex_toml"]), str(p["codex_hooks"]), str(p["codex_md"])]
    else:
        for k in ("opencode_json", "opencode_md"):
            _backup(p[k])
        p["opencode_plugin"].parent.mkdir(parents=True, exist_ok=True)
        p["opencode_plugin"].write_text(_plugin_js(), encoding="utf-8")
        if opencode_mcp():
            _merge_json(p["opencode_json"], "mcp", "keelyard", {"type": "local", "command": [_exe_path(), "mcp"], "enabled": True})
        _set_block(p["opencode_md"], rules if opencode_mcp() else _rules("agents_block_lean.md", terse))
        cmds = p["opencode_dir"] / "command"
        cmds.mkdir(parents=True, exist_ok=True)
        for name in WORKFLOWS:
            (cmds / f"keelyard-{name}.md").write_text(_command(name), encoding="utf-8")
        done += [str(p["opencode_json"]), str(p["opencode_plugin"]), str(p["opencode_md"])]
        if not opencode_mcp():
            _global_lean(agent, p, True)
    _set_global_marker(agent, True)
    return done


def _global_lean(agent: str, p: dict[str, Path], on: bool) -> None:
    """The lean preset at user level: OpenCode's trimmed build agent, Claude Code's lean env. Only what is absent
    is added, and what was added is recorded so uninstall_global takes back exactly that."""
    m = _marker()
    rec = json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}
    added = rec.setdefault("lean_added", {})
    if agent == "opencode":
        path = p["opencode_json"]
        data = _read_json(path) if path.exists() or on else {}
        if on:
            key, want = ("agent", LEAN_OPENCODE_BUILD_V1) if _opencode_major() < 2 else ("agents", LEAN_OPENCODE_BUILD)
            agents = data.setdefault(key, {})
            if "build" not in agents or _is_lean_build(agents["build"], want):
                agents["build"] = want
                _write_json(path, data)
                added["opencode"] = key
        elif added.pop("opencode", None) and path.exists():
            for key, want in (("agents", LEAN_OPENCODE_BUILD), ("agent", LEAN_OPENCODE_BUILD_V1)):
                if _is_lean_build(data.get(key, {}).get("build"), want):
                    del data[key]["build"]
                    if not data[key]:
                        del data[key]
            _write_json(path, data)
    elif agent == "claude":
        path = p["claude_settings"]
        data = _read_json(path) if path.exists() or on else {}
        if on:
            env = data.setdefault("env", {})
            keys = [k for k in LEAN_CLAUDE_ENV if k not in env]
            for k in keys:
                env[k] = LEAN_CLAUDE_ENV[k]
            _write_json(path, data)
            added["claude"] = sorted(set(added.get("claude", [])) | set(keys))
        elif added.get("claude") and path.exists():
            env = data.get("env", {})
            for k in added.pop("claude"):
                if env.get(k) == LEAN_CLAUDE_ENV[k]:
                    env.pop(k)
            if "env" in data and not env:
                del data["env"]
            _write_json(path, data)
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps(rec), encoding="utf-8")


def global_lean_state(home: Path | None = None) -> dict[str, list[str]]:
    """Per globally wired agent: which lean settings are missing or overridden at user level (empty = lean)."""
    p, out = global_paths(home), {}
    for agent in global_agents():
        gaps = []
        if agent == "opencode" and not os.environ.get("KEELYARD_OPENCODE_MCP"):
            data = _read_json(p["opencode_json"]) if p["opencode_json"].exists() else {}
            b = data.get("agents", {}).get("build") or data.get("agent", {}).get("build")
            if not (_is_lean_build(b, LEAN_OPENCODE_BUILD) or _is_lean_build(b, LEAN_OPENCODE_BUILD_V1)):
                gaps.append("opencode.json build agent is not keelyard's lean one")
        if agent == "claude":
            env = (_read_json(p["claude_settings"]) if p["claude_settings"].exists() else {}).get("env", {})
            gaps += [f"{k}={env.get(k, '(unset)')} (lean: {v})" for k, v in LEAN_CLAUDE_ENV.items() if env.get(k) != v]
        out[agent] = gaps
    return out


def uninstall_global(agent: str, home: Path | None = None) -> list[str]:
    p = global_paths(home)
    done = []
    if agent in ("claude", "opencode"):
        _global_lean(agent, p, False)
    if agent == "claude":
        if _strip_hooks(p["claude_settings"]):
            done.append(str(p["claude_settings"]))
        if _drop_key(p["claude_json"], "mcpServers"):
            done.append(str(p["claude_json"]))
        if _set_block(p["claude_md"], None):
            done.append(str(p["claude_md"]))
        for d in (p["claude_dir"] / "skills" / "keelyard", p["claude_dir"] / "commands" / "keelyard"):
            if d.is_dir():
                shutil.rmtree(d)
                done.append(str(d))
    elif agent == "codex":
        if _strip_hooks(p["codex_hooks"]):
            done.append(str(p["codex_hooks"]))
        if p["codex_toml"].exists():
            _toml_block(p["codex_toml"], None)
            done.append(str(p["codex_toml"]))
        if _set_block(p["codex_md"], None):
            done.append(str(p["codex_md"]))
    else:
        if _drop_key(p["opencode_json"], "mcp"):
            done.append(str(p["opencode_json"]))
        for f in (p["opencode_plugin"], p["opencode_dir"] / "plugin" / "keelyard.js"):
            if f.exists():
                f.unlink()
                done.append(str(f))
        if _set_block(p["opencode_md"], None):
            done.append(str(p["opencode_md"]))
        for name in WORKFLOWS:
            f = p["opencode_dir"] / "command" / f"keelyard-{name}.md"
            if f.exists():
                f.unlink()
    _set_global_marker(agent, False)
    return done


def _marker() -> Path:
    from ..config import data_home

    return data_home() / "global.json"


def _set_global_marker(agent: str, on: bool) -> None:
    m = _marker()
    rec = json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}
    agents = set(rec.get("agents", []))
    (agents.add if on else agents.discard)(agent)
    rec["agents"] = sorted(agents)
    m.write_text(json.dumps(rec), encoding="utf-8")


def global_agents() -> list[str]:
    try:
        return list(json.loads(_marker().read_text(encoding="utf-8")).get("agents", []))
    except (OSError, ValueError):
        return []


def installed_global(agent: str, home: Path | None = None) -> bool:
    p = global_paths(home)
    if agent == "opencode":
        return p["opencode_plugin"].exists()
    f = p["claude_settings"] if agent == "claude" else p["codex_hooks"]
    try:
        t = f.read_text(encoding="utf-8")
    except OSError:
        return False
    return "keelyard" in t and " hook " in t
