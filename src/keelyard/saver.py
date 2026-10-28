"""Usage saver: spend fewer tokens without proxying the agent's subscription.

Everything here works *inside* the agent: MCP tools that return smaller results,
and hooks that stop redundant reads. Nothing sits between the agent and its API.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex  # noqa: F401
import subprocess
import threading
import time
from pathlib import Path

from .config import Config

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
IMPORTANT = re.compile(r"(?i)\b(error|fail(ed|ure)?|exception|traceback|panic|assert|warning|denied|not found|fatal)\b")


def approx_tokens(text: str) -> int:
    """Rough token estimate (chars/4). Used only for relative comparisons."""
    return (len(text) + 3) // 4


# --------------------------------------------------------------------------- output
def compress_output(text: str, max_chars: int = 6000) -> str:
    """Shrink command output while keeping what matters (errors, the tail, the head)."""
    text = ANSI.sub("", text)
    # progress bars rewrite themselves with \r; keep only the final state of each line
    lines = [ln.rsplit("\r", 1)[-1].rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    prev, run = None, 0
    for ln in lines:
        key = re.sub(r"\d+", "#", ln)
        if key == prev and ln.strip():
            run += 1
            continue
        if run:
            out.append(f"    ... (+{run} similar lines)")
        out.append(ln)
        prev, run = key, 0
    if run:
        out.append(f"    ... (+{run} similar lines)")
    # drop runs of blank lines
    squeezed = [ln for i, ln in enumerate(out) if ln.strip() or (i and out[i - 1].strip())]
    body = "\n".join(squeezed).strip("\n")
    if len(body) <= max_chars:
        return body
    keep_head, keep_tail = int(max_chars * 0.3), int(max_chars * 0.45)
    head, tail = body[:keep_head], body[-keep_tail:]
    middle = body[keep_head:-keep_tail].split("\n")
    budget = max_chars - keep_head - keep_tail
    picked = []
    for ln in middle:
        if IMPORTANT.search(ln) and budget - len(ln) > 0:
            picked.append(ln)
            budget -= len(ln) + 1
    omitted = len(middle) - len(picked)
    mid = "\n".join(picked)
    return f"{head}\n... [{omitted} lines omitted by keelyard; error/warning lines kept] ...\n{mid}\n{tail}".replace("\n\n\n", "\n")


from .proc import split_cmd  # noqa: E402


def run_compact(cfg: Config, cmd: str, timeout: int = 600) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(split_cmd(cmd), cwd=cfg.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, raw = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        code, raw = 124, f"timeout after {timeout}s\n{_text(e.stdout)}"
    except FileNotFoundError as e:
        code, raw = 127, str(e)
    small = compress_output(raw, cfg.section("saver")["max_output_chars"])
    return {"exit": code, "seconds": round(time.time() - t0, 2), "output": small,
            "raw_tokens": approx_tokens(raw), "returned_tokens": approx_tokens(small)}


# --------------------------------------------------------------------------- reads

def normalize_path(file: str) -> str:
    """Accept Windows-style separators from agents on any OS."""
    if os.sep == "/" and "\\" in file:
        file = file.replace("\\", "/")
    return file


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]

class ReadCache:
    """Per-session memory of what the agent already read, keyed by content hash."""

    def __init__(self, cfg: Config, session: str):
        self.cfg = cfg
        self.path = cfg.state / "reads" / f"{session}.json"
        self.blobs = cfg.state / "blobs"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(exist_ok=True)

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: concurrent hooks never see half a file

    def reset(self) -> None:
        """Called when the agent's context is compacted: earlier reads are gone from its window."""
        self.path.unlink(missing_ok=True)

    def resolve(self, file: str) -> Path:
        file = normalize_path(file)
        p = (self.cfg.root / file).resolve() if not Path(file).is_absolute() else Path(file).resolve()
        if self.cfg.root not in p.parents and p != self.cfg.root:
            raise PermissionError(f"{file} is outside the project root")
        if ".keelyard" in p.relative_to(self.cfg.root).parts:
            raise PermissionError("refusing to read keelyard internal state")
        return p

    def seen(self, p: Path) -> dict | None:
        return self._load().get(str(p))

    def remember(self, p: Path, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()[:16]
        (self.blobs / sha).write_bytes(data)
        d = self._load()
        d[str(p)] = {"sha": sha, "ts": time.time(), "count": d.get(str(p), {}).get("count", 0) + 1}
        self._save(d)
        return sha

    def read(self, file: str, start: int = 1, end: int | None = None, force: bool = False) -> str:
        p = self.resolve(file)
        if not p.is_file():
            raise FileNotFoundError(f"{file}: no such file in {self.cfg.root}")
        data = p.read_bytes()
        if is_binary(data):
            return f"[keelyard] {file} is a binary file ({len(data):,} bytes); not shown."
        sha = hashlib.sha256(data).hexdigest()[:16]
        prev = self.seen(p)
        whole = start <= 1 and end is None
        text = data.decode(errors="replace").replace("\r\n", "\n")
        if prev and whole and not force:
            if prev["sha"] == sha:
                self.remember(p, data)
                note = (f"[keelyard] {file} is unchanged since you read it "
                        f"({len(text.splitlines())} lines, sha {sha}). Use the copy already in your context, "
                        f"or call again with force=true if it was compacted away.")
                if len(note) < len(text) + 6 * len(text.splitlines()):  # a tiny file is cheaper to resend than to describe
                    return note
            old = self.blobs / prev["sha"]
            if prev["sha"] != sha and old.exists():
                diff = "".join(difflib.unified_diff(
                    old.read_bytes().decode(errors="replace").replace("\r\n", "\n").splitlines(True), text.splitlines(True),
                    f"{file} (your previous read)", f"{file} (now)", n=2))
                if len(diff) < len(text) * 0.6:
                    self.remember(p, data)
                    return f"[keelyard] {file} changed since your previous read; diff only:\n{diff}"
        self.remember(p, data)
        lines = text.splitlines()
        stop = len(lines) if end is None else min(end, len(lines))
        sel = lines[max(start, 1) - 1:stop]
        return "\n".join(f"{i:>5}\t{ln}" for i, ln in enumerate(sel, start=max(start, 1)))


# --------------------------------------------------------------------------- routing
HARD = re.compile(r"(?i)\b(architect|design|refactor|race|deadlock|concurren|security|vulnerab|migrat|debug|why|prove|"
                  r"algorithm|optimi[sz]e|performance|memory leak|root cause|trade-?off|plan)\w*")
EASY = re.compile(r"(?i)\b(rename|typo|format|lint|summari[sz]e|list|find|search|grep|read|docstring|comment|bump|"
                  r"translate|boilerplate|stub|test run|changelog)\w*")


def route(task: str, n_files: int = 0) -> dict:
    """Heuristic: send only hard reasoning to the strong model. Transparent on purpose."""
    hard, easy = len(HARD.findall(task)), len(EASY.findall(task))
    score = hard * 2 - easy + (1 if n_files > 5 else 0) + (1 if len(task) > 600 else 0)
    tier = "strong" if score > 0 else "cheap"
    return {"tier": tier, "score": score, "hard_signals": hard, "easy_signals": easy,
            "why": "keyword heuristic; override freely"}


def _text(data: str | bytes | None) -> str:
    """TimeoutExpired.stdout is bytes even when the process ran with text=True."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""
