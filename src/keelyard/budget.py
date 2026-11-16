"""Session spend cap: stop further edits and shell commands when a token or dollar limit is crossed.

Caps are off by default (max_tokens = 0, max_usd = 0). Spend is read from the agent's
own session transcript when keelyard has been told its path (Claude Code sends
transcript_path on every hook). Cost is computed only from [prices] in config, or
from a cost field the log already stores. keelyard ships no price table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .audit import remember_transcript, transcript_path
from .config import Config
from .session import current

remember_transcript = remember_transcript  # re-export for hooks


@dataclass
class Spend:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    messages: int = 0
    source: str = ""

    @property
    def tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


def _priced_add(s: Spend, i: int, o: int, cr: int, cw: int, cost: float | None, prices: dict, model: str) -> None:
    s.input += int(i or 0)
    s.output += int(o or 0)
    s.cache_read += int(cr or 0)
    s.cache_write += int(cw or 0)
    s.messages += 1
    if cost is not None:
        s.cost_usd += float(cost)
        return
    if model in prices:
        pr = prices[model]
        s.cost_usd += (int(i or 0) * pr.get("input", 0) + int(o or 0) * pr.get("output", 0)
                       + int(cr or 0) * pr.get("cache_read", 0) + int(cw or 0) * pr.get("cache_write", 0)) / 1e6
        return
    s.priced = False


def spend_from_transcript(path: Path, prices: dict | None = None) -> Spend:
    prices = prices or {}
    s = Spend(source=str(path))
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return s
    model = "?"
    seen: set = set()
    for line in lines:
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Claude Code
        m = j.get("message") if isinstance(j.get("message"), dict) else {}
        u = m.get("usage")
        if j.get("type") == "assistant" and u:
            key = (m.get("id"), j.get("requestId"))
            if key[0] and key in seen:
                continue
            seen.add(key)
            _priced_add(s, u.get("input_tokens", 0), u.get("output_tokens", 0),
                        u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                        None, prices, m.get("model") or "?")
            continue
        # Codex
        p = j.get("payload") or {}
        if j.get("type") == "turn_context":
            model = p.get("model", model)
        elif j.get("type") == "event_msg" and p.get("type") == "token_count":
            u = (p.get("info") or {}).get("last_token_usage")
            if u:
                cached = u.get("cached_input_tokens", 0)
                _priced_add(s, u.get("input_tokens", 0) - cached, u.get("output_tokens", 0), cached, 0, None, prices, model)
    return s


def session_spend(cfg: Config, session: str | None = None) -> Spend:
    sid = session or current(cfg)
    path = transcript_path(cfg, sid)
    if not path:
        return Spend()
    return spend_from_transcript(path, cfg.raw.get("prices") or {})


def status(cfg: Config, session: str | None = None) -> dict:
    opts = cfg.section("budget")
    cap_t = int(opts.get("max_tokens") or 0)
    cap_usd = float(opts.get("max_usd") or 0)
    warn_at = float(opts.get("warn_at") or 0.8)
    block = bool(opts.get("block", True))
    s = session_spend(cfg, session)
    frac_t = (s.tokens / cap_t) if cap_t else 0.0
    frac_usd = (s.cost_usd / cap_usd) if cap_usd and s.priced else 0.0
    over = bool((cap_t and s.tokens >= cap_t) or (cap_usd and s.priced and s.cost_usd >= cap_usd))
    warn = bool(not over and ((cap_t and frac_t >= warn_at) or (cap_usd and s.priced and frac_usd >= warn_at)))
    return {
        "tokens": s.tokens, "input": s.input, "output": s.output, "cache_read": s.cache_read,
        "cache_write": s.cache_write, "cost_usd": round(s.cost_usd, 4) if (s.priced or s.cost_usd) else None,
        "priced": s.priced, "messages": s.messages, "source": s.source,
        "max_tokens": cap_t or None, "max_usd": cap_usd or None,
        "over": over, "warn": warn, "block": over and block and bool(cap_t or cap_usd),
        "enabled": bool(cap_t or cap_usd),
    }


def message(st: dict) -> str:
    bits = []
    if st.get("max_tokens"):
        bits.append(f"{st['tokens']:,} / {st['max_tokens']:,} tokens")
    if st.get("max_usd") and st.get("cost_usd") is not None:
        bits.append(f"${st['cost_usd']:.4f} / ${st['max_usd']}")
    used = ", ".join(bits) or f"{st['tokens']:,} tokens"
    if st.get("over"):
        return (f"keelyard budget: this session has used {used}, which is at the cap. "
                "Further edits and shell commands are blocked. Raise [budget].max_tokens / max_usd, "
                "or start a new session. `keelyard rewind` still works.")
    return f"keelyard budget: this session has used {used} (warning threshold)."


def guard(cfg: Config, session: str | None = None) -> dict | None:
    """Hook output that denies a mutating tool when the cap is crossed; None otherwise."""
    st = status(cfg, session)
    if not st["block"]:
        return None
    reason = message(st)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}, "systemMessage": reason}
