"""A live view of the current session: proof, edits, spend, risk and false claims.

`keelyard watch --once` prints one snapshot (scripts and tests). Without --once it
refreshes until you press Ctrl-C. This is a read-only view; it never talks to a model.
"""

from __future__ import annotations

import time

from . import audit, budget, risk
from .config import Config
from .proof import Proof
from .session import current
from .snapshot import Shadow

QUIET = {"baseline", "pre-rewind", "rewind", "stop", "manual", "pre-destructive", "pre-risk"}


def dashboard(cfg: Config, session: str | None = None) -> dict:
    sid = session or current(cfg)
    pf = Proof(cfg, sid)
    steps = Shadow(cfg).steps()
    mine = [s for s in steps if not sid or s.session in ("", sid)]
    edits = [s for s in mine if s.tool not in QUIET]
    last = steps[-1] if steps else None
    au = audit.evaluate(cfg, sid)
    bd = budget.status(cfg, sid)
    risky = [e for e in risk.recent(cfg, 20) if not sid or e.get("session") in (None, sid)]
    return {
        "session": sid,
        "proof": {"state": pf.state(), "missing": pf.missing(), "task": pf.load().task,
                  "requirements": pf.load().requirements, "evidence": pf.levels()},
        "steps": len(steps),
        "edits": len(edits),
        "last_step": None if not last else {"step": last.step, "tool": last.tool, "ts": last.ts,
                                            "files": last.files, "note": last.note},
        "audit": {"ok": au["ok"], "claims": len(au["claims"]), "lies": au["lies"]},
        "risk": {"recent": risky[-8:], "highest": max((e.get("level") or "none" for e in risky),
                                                      key=lambda lv: risk.LEVELS.get(lv, 0), default="none")},
        "budget": bd,
    }


def render(d: dict) -> str:
    p, b = d["proof"], d["budget"]
    proof = p["state"]
    if p["missing"]:
        proof += f" ({len(p['missing'])} open)"
    last = d["last_step"]
    last_s = f"#{last['step']} {last['tool']}" if last else "none"
    audit_s = "ok" if d["audit"]["ok"] else f"{len(d['audit']['lies'])} false claim(s)"
    spend = f"{b['tokens']:,} tokens"
    if b.get("max_tokens"):
        spend += f" / {b['max_tokens']:,}"
    if b.get("over"):
        spend += "  CAP"
    elif b.get("warn"):
        spend += "  warn"
    lines = [
        f"session  {d['session']}",
        f"proof    {proof}",
        f"timeline {d['edits']} edits / {d['steps']} steps · last {last_s}",
        f"audit    {audit_s}",
        f"risk     highest {d['risk']['highest']}",
        f"spend    {spend}",
    ]
    if p.get("task"):
        lines.append(f"task     {p['task'][:80]}")
    for g in p["missing"][:5]:
        lines.append(f"  - {g}")
    for lie in d["audit"]["lies"][:3]:
        lines.append(f"  ! claimed “{lie['text']}”, but {lie['evidence']}")
    return "\n".join(lines)


def run(cfg: Config, once: bool = False, interval: float = 1.0) -> dict:
    data = dashboard(cfg)
    if once:
        print(render(data))
        return data
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        console = Console()
        with Live(Text(render(data)), console=console, refresh_per_second=max(1, int(1 / max(interval, 0.2)))) as live:
            while True:
                time.sleep(interval)
                data = dashboard(cfg)
                live.update(Text(render(data)))
    except KeyboardInterrupt:
        print()
    return data
