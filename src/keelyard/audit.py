"""Compare what the agent *said* about tests with what keelyard actually recorded.

Only clear, completed claims are scored (not "I will run the tests" or "if they pass").
A mismatch is a lie: "all tests passed" when the last run failed or was before the last
edit, or "I didn't change the tests" when the test inventory got weaker. Conservative
on purpose: vague praise is ignored.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .config import Config
from .locking import file_lock
from .proof import Proof
from .session import current

# Completed claims only. Hypotheticals are dropped when a cue sits immediately before the claim.
_PASS = re.compile(
    r"(?i)((?:all\s+)?(?:the\s+)?(?:tests?|test suite|pytest|jest|vitest|go test)\s+"
    r"(?:all\s+)?(?:(?:have|has|are|is)\s+)?(?:now\s+)?(?:pass(?:ed|ing)?|green|successful)"
    r"|(?:just\s+)?(?:re-?)?ran\s+(?:all\s+)?(?:the\s+)?tests?\b.{0,80}\b(?:pass(?:ed|ing)?|green)"
    r"|tests?\s+are\s+(?:all\s+)?green)"
)
_UNCHANGED = re.compile(
    r"(?i)\b(?:did not|didn't|haven't|have not|never)\s+"
    r"(?:change|changed|delete|deleted|remove|removed|weaken|weakened|skip|skipped)\s+"
    r".{0,24}tests?\b"
)
# Only the last few characters before the match: "if all tests pass" is skipped,
# but "Please review this. All tests passed." and "After that, all tests passed." are kept.
_HYPOTHETICAL = re.compile(
    r"(?i)\b(if|when|once|should|will|going to|let me|i['’]ll|i will|to make sure|make sure|want to)\b"
)
_MAX_LINE = 1_000_000


def _claims_in(text: str) -> list[dict]:
    out = []
    for kind, rx in (("tests_passed", _PASS), ("tests_unchanged", _UNCHANGED)):
        for m in rx.finditer(text or ""):
            left = text[max(0, m.start() - 16):m.start()]
            if _HYPOTHETICAL.search(left):
                continue
            out.append({"kind": kind, "text": re.sub(r"\s+", " ", m.group(0)).strip()})
    return out


def allowed_transcript(path: Path, root: Path | None = None) -> bool:
    """True if `path` is a regular file under the project, the user's home, or a well-known agent log dir."""
    try:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            return False
        # A symlink is allowed only if its resolved target stays under the allowlist.
        homes = [Path.home().resolve()]
        if root is not None:
            homes.append(Path(root).resolve())
        xdg = Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else Path.home() / ".local" / "share"
        homes.extend([
            Path.home() / ".claude",
            Path.home() / ".codex",
            xdg.resolve() / "opencode",
            xdg.resolve() / "claude",
        ])
        return any(_is_under(resolved, base) for base in homes)
    except (OSError, RuntimeError, ValueError):
        return False


def _is_under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def iter_jsonl(path: Path, root: Path | None = None):
    """Yield parsed JSON objects from a session log, streaming, with a per-line size cap."""
    if not allowed_transcript(path, root):
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip() or len(line) > _MAX_LINE:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def remember_transcript(cfg: Config, session: str, payload: dict) -> None:
    raw = payload.get("transcript_path") or payload.get("transcriptPath")
    if not raw or not isinstance(raw, str) or "\x00" in raw:
        return
    p = cfg.state / "transcripts.json"
    try:
        with file_lock(cfg.state / "transcripts.lock", timeout=2):
            try:
                data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            except (OSError, ValueError):
                data = {}
            data[session] = raw
            p.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, TimeoutError):
        pass


def transcript_path(cfg: Config, session: str) -> Path | None:
    p = cfg.state / "transcripts.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    raw = data.get(session)
    if not raw or not isinstance(raw, str):
        return None
    path = Path(raw)
    return path if allowed_transcript(path, cfg.root) else None


def assistant_turns(path: Path, root: Path | None = None) -> list[tuple[str, str]]:
    """(timestamp, text) of assistant messages in a Claude Code or Codex session log."""
    from .memory import _CODEX_INJECTED, _flatten

    out: list[tuple[str, str]] = []
    for j in iter_jsonl(path, root):
        if j.get("type") in ("user", "assistant") and isinstance(j.get("message"), dict):
            role = j["message"].get("role", j["type"])
            text = _flatten(j["message"].get("content"))
            if role == "assistant" and text.strip():
                out.append((j.get("timestamp", ""), text))
            continue
        p = j.get("payload") or {}
        if j.get("type") == "response_item" and p.get("type") == "message":
            text = _flatten(p.get("content"))
            if p.get("role") == "assistant" and text.strip() and not text.lstrip().startswith(_CODEX_INJECTED):
                out.append((j.get("timestamp", ""), text))
    return out


def evaluate(cfg: Config, session: str | None = None, texts: list[str] | None = None, persist: bool = True) -> dict:
    """Score claims in `texts` or in the remembered transcript for `session`.

    `persist=False` is for read-only views (`watch`, `status`) so they do not rewrite audit.json.
    """
    sid = session or current(cfg)
    pf = Proof(cfg, sid)
    last_edit = pf.last_edit_ts()
    b = pf.load()
    after = [t for t in b.test_runs if t["phase"] == "after"]
    last_after = after[-1] if after else None
    weakened = pf.weakened()
    claims: list[dict] = []
    if texts is not None:
        for text in texts:
            for c in _claims_in(text):
                claims.append({**c, "ts": ""})
    else:
        path = transcript_path(cfg, sid)
        if path:
            for ts, text in assistant_turns(path, cfg.root):
                for c in _claims_in(text):
                    claims.append({**c, "ts": ts})
    lies = []
    for c in claims:
        if c["kind"] == "tests_passed":
            if last_after is None:
                why = "no test run is recorded after the last edit" if last_edit else "no test run is recorded"
            elif last_after["exit"] != 0:
                why = f"the last test run failed with exit {last_after['exit']}"
            elif last_edit is not None and last_after["ts"] < last_edit:
                why = "the last passing test run was before the last edit"
            else:
                continue
            lies.append({**c, "evidence": why})
        elif c["kind"] == "tests_unchanged" and weakened:
            lies.append({**c, "evidence": "; ".join(weakened)})
    result = {
        "session": sid,
        "claims": claims,
        "lies": lies,
        "ok": not lies,
        "tests_after": None if not last_after else {"exit": last_after["exit"], "ts": last_after["ts"],
                                                    "cmd": last_after.get("cmd", "")},
        "weakened": weakened,
    }
    if persist:
        try:
            with file_lock(cfg.state / "audit.lock", timeout=2):
                (cfg.state / "audit.json").write_text(json.dumps(result, default=str), encoding="utf-8")
        except (OSError, TimeoutError):
            pass
    return result


def block_lines(result: dict) -> list[str]:
    """Extra Stop-hook lines when a false claim should keep the gate closed."""
    if not result.get("lies"):
        return []
    out = []
    for lie in result["lies"]:
        if lie["kind"] == "tests_unchanged" and not result.get("weakened"):
            continue  # already accepted as a real refactor
        out.append(f"You claimed “{lie['text']}”, but {lie['evidence']}.")
    return out
