"""Hook entry point shared by all agents.

Claude Code sends one JSON object on stdin per event; Codex and OpenCode
adapters translate their events into the same shape before calling us.
A hook must never break the agent, so every failure is logged and swallowed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import traceback
from pathlib import Path

from . import config, session
from .locking import file_lock
from .proof import Proof
from .report import render
from .saver import ReadCache
from .snapshot import Shadow

EDIT_TOOLS_READ = {"Read", "read", "view"}
EDIT_TOOLS_WRITE = {"Edit", "Write", "MultiEdit", "edit", "write", "patch", "apply_patch"}
SHELL_TOOLS = {"Bash", "bash", "shell", "local_shell", "exec_command"}


def _ctx(event: str, text: str, **extra) -> dict:
    """Text the model reads on its next step (Claude Code / Codex additionalContext; OpenCode plugin queues it)."""
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}, **extra}


def _merge(a: dict | None, b: dict | None) -> dict | None:
    if not a or not b:
        return a or b
    ha, hb = a.get("hookSpecificOutput", {}), b.get("hookSpecificOutput", {})
    if "additionalContext" in ha and "additionalContext" in hb:
        hb = {**hb, "additionalContext": ha["additionalContext"] + "\n" + hb["additionalContext"]}
    return {**a, **b, "hookSpecificOutput": {**ha, **hb}}


def _patch_text(tin: dict) -> str:
    """Patch body from Claude (`input`/`patch`) or Codex (`tool_input.command`)."""
    for key in ("patch", "input", "command"):
        val = tin.get(key)
        if isinstance(val, str) and "*** " in val:
            return val
    return ""


def _edited_files(tin: dict) -> list[str]:
    f = _file_arg(tin)
    if f:
        return [f]
    patch = _patch_text(tin)
    return re.findall(r"^\*\*\* (?:Update|Add) File: (.+)$", patch, re.M) if patch else []


def _file_arg(tool_input: dict) -> str | None:
    return tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path")


def _codex_output(out: dict | None) -> dict | None:
    """Rewrite Claude-only hook fields into the shape Codex CLI 0.165.0 accepts.

    `updatedToolOutput` makes Codex mark the hook failed. `continue: false` with
    `stopReason` replaces the tool result the model sees (`PostToolUse Stopped`).
    """
    if not isinstance(out, dict):
        return out
    spec = out.get("hookSpecificOutput")
    updated = spec.get("updatedToolOutput") if isinstance(spec, dict) else None
    if not isinstance(updated, dict) or not isinstance(updated.get("stdout"), str):
        return out
    parts = []
    extra = spec.get("additionalContext") if isinstance(spec, dict) else None
    if isinstance(extra, str) and extra.strip():
        parts.append(extra.strip())
    parts.append(updated["stdout"])
    kept = {k: v for k, v in out.items() if k != "hookSpecificOutput"}
    kept["continue"] = False
    kept["stopReason"] = "\n".join(parts)
    return kept


def _filter_bash(cfg: config.Config, tin: dict, resp) -> dict | None:
    """Shrink a finished Bash result in place (Claude Code PostToolUse updatedToolOutput).

    The command already ran unchanged; only what the model reads is filtered, and the raw
    output stays recoverable with `keelyard recall <id>`. No permission decision is touched.
    """
    if not isinstance(resp, dict) or resp.get("isImage") or not isinstance(resp.get("stdout"), str):
        return None
    from .filters import filter_output
    from .saver import approx_tokens

    raw_out, raw_err = resp.get("stdout", ""), resp.get("stderr", "") or ""
    raw = raw_out + ("\n" + raw_err if raw_err else "")
    if len(raw) < int(cfg.section("saver").get("filter_min_chars", 1200)):
        return None
    small = filter_output(tin.get("command", ""), raw)
    if len(small) > 0.8 * len(raw):
        return None
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    raw_dir = cfg.state / "raw"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / h).write_text(raw, encoding="utf-8")
    note = f"\n[keelyard filtered {approx_tokens(raw)}→{approx_tokens(small)} tokens; full output: keelyard recall {h}]"
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": {
        "stdout": small + note, "stderr": "", "interrupted": bool(resp.get("interrupted")), "isImage": False}}}


def _baseline_tests(cfg: config.Config, sid: str) -> str:
    """The test result before any edit, in the brief: the agent needs no 'before' run of its own."""
    from .filters import filter_output

    try:
        run = Proof(cfg, sid).run_tests("before")
    except Exception as e:  # noqa: BLE001
        return f"[keelyard: tests not run: {e}]"
    small = filter_output(run["cmd"], run["tail"])[-1500:].strip()
    return f"Tests before any edit (keelyard ran `{run['cmd']}`, exit {run['exit']}; no need to run them again):\n{small}"


def _auto_test(cfg: config.Config, sid: str, resp) -> dict | None:
    """Run the tests after an edit and hand the agent a compact result with the edit's own output."""
    from .filters import filter_output

    try:
        run = Proof(cfg, sid).run_tests("after")
    except Exception as e:  # noqa: BLE001 - no test command: say so once, never break the edit
        note = f"[keelyard: tests not run: {e}]"
    else:
        small = filter_output(run["cmd"], run["tail"])[-1200:].strip()
        note = f"[keelyard ran `{run['cmd']}` after this edit: exit {run['exit']}]\n{small}"
        hint = Proof(cfg, sid).repeat_hint(run)
        if hint:
            note += "\n" + hint
    orig = resp.get("stdout", "") if isinstance(resp, dict) and isinstance(resp.get("stdout"), str) else None
    if orig is None:
        return _ctx("PostToolUse", note)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": {
        "stdout": (orig.rstrip() + "\n" + note).strip(), "stderr": "", "interrupted": False, "isImage": False}}}


def _ingest_memory(cfg: config.Config, every_s: int = 600) -> None:
    """Keep the memory index fresh for UserPromptSubmit recall: incremental, at most every 10 minutes."""
    mark = cfg.state / "memory.ingested"
    try:
        if mark.exists() and time.time() - mark.stat().st_mtime < every_s:
            return
        mark.touch()
        from .memory import Memory

        Memory().ingest()
    except Exception:  # noqa: BLE001 - memory is a bonus
        pass


def handle(event: str, payload: dict, cfg: config.Config) -> dict | None:
    sid = session.set_current(cfg, payload.get("session_id") or payload.get("sessionID"))
    tool = payload.get("tool_name") or payload.get("tool") or ""
    tin = payload.get("tool_input") or payload.get("args") or {}
    rew = cfg.section("rewind")
    sav = cfg.section("saver")

    brain = cfg.section("brain")

    if event == "SessionStart":
        if payload.get("source") in ("compact", "clear"):
            ReadCache(cfg, sid).reset()
        if rew["enabled"] and payload.get("source") not in ("compact",):
            Shadow(cfg).snapshot(tool="baseline", session=sid)
        if brain["brief"]:
            from .brain import brief

            task = Proof(cfg, sid).load().task
            head = "keelyard brief (re-sent after compaction)" if payload.get("source") == "compact" else "keelyard brief"
            text = f"[{head}]\n" + brief(cfg, sid, query=task, lean=bool(payload.get("lean")))
            if payload.get("source") == "startup" and cfg.section("proof").get("auto_test_after_edit"):
                text += "\n\n" + _baseline_tests(cfg, sid)
            return _ctx("SessionStart", text)
        return None

    if event == "UserPromptSubmit":
        prompt = payload.get("prompt", "")
        Proof(cfg, sid).set_task(prompt)
        notes = []
        if brain["recall"]:
            from .brain import recall

            r = recall(cfg, prompt, exclude_session=sid)
            if r:
                notes.append(r)
        from .brain import Nudger, budget_nudge

        b = budget_nudge(cfg, payload)
        if b and Nudger(cfg, sid).allow("budget"):
            notes.append(b)
        return _ctx("UserPromptSubmit", "\n\n".join(notes)) if notes else None

    if event == "PreToolUse" and tool in SHELL_TOOLS:
        from .brain import Nudger, destructive

        cmd = tin.get("command") or tin.get("cmd") or ""
        cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        kind = destructive(cmd)
        if not kind:
            return None
        step = None
        if rew["enabled"]:
            sh = Shadow(cfg)
            steps = sh.steps()
            step = sh.snapshot(tool="pre-destructive", session=sid) or (steps[-1] if steps else None)
        where = f" Snapshot #{step.step} was taken first; `keelyard rewind to {step.step}` restores the files." \
            if step else ""
        if not Nudger(cfg, sid).allow("destructive", kind):
            return None
        msg = f"keelyard: `{kind}` is destructive.{where} Prefer a narrower command if you can."
        return _ctx("PreToolUse", msg, systemMessage=msg)

    if event == "PreToolUse" and tool in EDIT_TOOLS_WRITE:
        from .brain import Nudger, has_tests_nearby

        for edit_arg in _edited_files(tin):
            try:
                rel = ReadCache(cfg, sid).resolve(edit_arg).relative_to(cfg.root).as_posix()
            except (PermissionError, ValueError):
                continue
            if not has_tests_nearby(cfg.root, rel) and Nudger(cfg, sid).allow("no-tests", rel):
                return _ctx("PreToolUse", f"keelyard: no tests found for {rel}. Add or point to a test that covers "
                                          "this change, so proof_run_tests can show it works.")
        return None

    if event == "PreCompact":
        ReadCache(cfg, sid).reset()
        return None

    if event == "PreToolUse" and tool in EDIT_TOOLS_READ and sav.get("dedupe_reads", True):
        f = _file_arg(tin)
        if not f or tin.get("offset") or tin.get("limit"):
            return None
        rc = ReadCache(cfg, sid)
        try:
            p = rc.resolve(f)
        except (PermissionError, ValueError):
            return None
        prev = rc.seen(p)
        if prev and p.is_file():
            def deny(reason: str) -> dict:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                               "permissionDecisionReason": reason}}
            if hashlib.sha256(p.read_bytes()).hexdigest()[:16] == prev["sha"]:
                from .brain import _cap
                from .codemap import outline

                try:
                    ol = _cap(outline(p), 300)
                except Exception:  # noqa: BLE001
                    ol = ""
                return deny(f"keelyard: {f} is unchanged since you read it this session; use the copy in your context."
                            + (f"\nOutline, to find your place:\n{ol}" if ol.strip() else "")
                            + "\nIf it was compacted away, read with an offset/limit or keelyard read_file force=true.")
            text = rc.read(str(p))  # changed: returns a diff when that is much smaller than the file
            if text.startswith("[keelyard]") and "diff only" in text:
                return deny(text.replace("[keelyard]", "keelyard:", 1)
                            + "\n(The diff is the whole change. Read with an offset/limit for full lines.)")
        return None

    if event == "PostToolUse":
        out = None
        resp = payload.get("tool_response")
        if isinstance(resp, str):  # Codex sends the shell output as one string, with no exit code
            resp = {"stdout": resp}
        if tool in SHELL_TOOLS and isinstance(resp, dict) and isinstance(resp.get("stdout"), str):
            cmd = tin.get("command") or tin.get("cmd") or ""
            cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            code = next((resp[k] for k in ("exit_code", "exitCode", "exit") if isinstance(resp.get(k), int)), None)
            hint = None
            try:
                pf = Proof(cfg, sid)
                hint = pf.repeat_hint(pf.record_shell_run(cmd, resp["stdout"] + "\n" + (resp.get("stderr") or ""), code))
            except Exception:  # noqa: BLE001 - proof capture must never break the agent
                pass
            if hint:
                out = _ctx("PostToolUse", hint)
        if tool in SHELL_TOOLS and sav.get("filter_bash", True):
            # Filter the normalized response so Codex output is filtered too; a raw str is never replaced.
            out = _merge(out, _filter_bash(cfg, tin, resp))
        if tool in EDIT_TOOLS_READ:
            f = _file_arg(tin)
            if f and not tin.get("offset") and not tin.get("limit"):
                rc = ReadCache(cfg, sid)
                try:
                    p = rc.resolve(f)
                    if p.is_file():
                        rc.remember(p, p.read_bytes())
                except (PermissionError, ValueError):
                    pass
        if rew["enabled"] and tool in rew["snapshot_tools"]:
            Shadow(cfg).snapshot(tool=tool, session=sid)
        if tool in EDIT_TOOLS_WRITE and cfg.section("proof").get("auto_test_after_edit"):
            out = _merge(out, _auto_test(cfg, sid, resp))
        return out

    if event == "Stop":
        if rew["enabled"]:
            Shadow(cfg).snapshot(tool="stop", session=sid)
        if brain["recall"]:
            _ingest_memory(cfg)
        pf = Proof(cfg, sid)
        gaps = pf.missing()
        if not gaps:
            pf.auto_complete()
        report = render(pf)
        opts = cfg.section("proof")
        edited = pf.last_edit_ts() is not None

        def decide(b):
            blocked = bool(gaps and opts["gate"] and b.blocks < int(opts["max_blocks"]))
            if edited or b.requirements:
                b.gate.append({"ts": time.time(), "blocked": blocked, "open": len(gaps)})
                b.gate = b.gate[-50:]
            if blocked:
                b.blocks += 1
            return blocked, b.blocks

        blocked, blocks = pf._update(decide)
        if not gaps or not opts["gate"]:
            return None
        if not blocked:
            return {"systemMessage": f"keelyard: proof incomplete after {blocks} reminders; see {report}"}
        reason = ("keelyard proof gate: you changed files but have not proven the work yet.\n- "
                  + "\n- ".join(gaps)
                  + "\nRun the project's tests in your shell (that counts as proof), or use the keelyard proof tools / "
                    "`keelyard proof` CLI, then finish.")
        return {"decision": "block", "reason": reason}
    return None


def _auto_setup(cfg: config.Config) -> bool:
    """With a global install, a git project sets itself up on its first session: config + first snapshot."""
    from .adapters.install import global_agents
    from .snapshot import Shadow

    if not global_agents() or not (cfg.root / ".git").exists():
        return False
    config.init(cfg.root)
    try:
        Shadow(config.load(cfg.root)).snapshot("baseline", note="first session (global install)")
    except Exception:  # noqa: BLE001 - a failed snapshot must not block the session
        pass
    return True


def _seen(cfg: config.Config, agent: str, event: str) -> None:
    """Heartbeat: proves the agent really calls keelyard (status and doctor say so when it never has)."""
    p = cfg.state / "hooks_seen.json"
    try:
        with file_lock(cfg.state / "hooks_seen.lock", timeout=2):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            d.setdefault(agent, {})[event] = time.time()
            p.write_text(json.dumps(d), encoding="utf-8")
    except (OSError, TimeoutError):
        pass


def main(agent: str, event: str) -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or payload.get("directory")
    try:
        start = Path(cwd) if cwd and Path(cwd).is_dir() else None
        cfg = config.load(config.find_root(start))
        cfg.require_project()
        if not (cfg.root / ".keelyard").is_dir() and _auto_setup(cfg):
            cfg = config.load(cfg.root)
        elif event == "SessionStart" and config.migrate_lean(cfg):  # upgrade from an older version
            cfg = config.load(cfg.root)
    except Exception:  # noqa: BLE001 - not a project, or a broken config: stay out of the agent's way
        return 0
    _seen(cfg, agent, event)
    try:
        out = handle(event, payload, cfg)
        if agent == "codex":
            out = _codex_output(out)
    except Exception:  # noqa: BLE001
        log = cfg.state / "hook.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{time.ctime()} {agent} {event}\n{traceback.format_exc()}\n")
        if log.stat().st_size > 1_000_000:  # keep the log bounded
            log.write_text(log.read_text(encoding="utf-8", errors="replace")[-200_000:], encoding="utf-8")
        return 0
    if out:
        print(json.dumps(out))
    return 0
