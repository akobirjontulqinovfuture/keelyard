"""Per-change risk scoring and a snapshot *before* a risky edit.

Snapshots after the fact already happen on PostToolUse. This takes one first when
the agent is about to touch tests, CI, deploy files, lockfiles, auth or secrets,
so `keelyard rewind to <that step>` still has the pre-edit tree. Advisory unless
you set [risk].block to a level (off by default).
"""

from __future__ import annotations

import json
import re
import time

from .config import Config
from .snapshot import Shadow

LEVELS = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# More specific first. Rel paths are posix, lowercased for matching where noted.
_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"(^|/)\.env($|\.(local|prod|production|dev|development|staging|test)$)", re.I),
     "critical", "environment secrets"),
    (re.compile(r"(^|/)(\.netrc|\.npmrc|\.pypirc|\.git-credentials|\.dockercfg)$", re.I),
     "critical", "credentials file"),
    (re.compile(r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(-[a-z0-9]+)?$", re.I), "critical", "ssh key"),
    (re.compile(r"\.(pem|key|p12|pfx|jks|keystore)$", re.I), "critical", "key material"),
    (re.compile(r"(^|/)(credentials|service-account.*|secrets?)\.(json|ya?ml|toml)$", re.I),
     "critical", "credentials file"),
    (re.compile(r"(^|/)\.github/workflows/", re.I), "high", "CI workflow"),
    (re.compile(r"(^|/)(Dockerfile|docker-compose.+\.ya?ml|compose.+\.ya?ml)$", re.I), "high", "container deploy"),
    (re.compile(r"(^|/)(terraform|\.tf|helm/|k8s/|kubernetes/)", re.I), "high", "infra as code"),
    (re.compile(r"(^|/)(alembic/|migrations?/|prisma/migrations/)", re.I), "high", "database migration"),
    (re.compile(r"(^|/)(tests?|__tests__|spec)/.+\.(py|[cm]?[jt]sx?|go|rs|rb|php|java|kt|cs)$"
                r"|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$", re.I),
     "high", "test file"),
    (re.compile(r"(auth|oauth|passwd|password|secret|token|jwt|credential)", re.I), "high", "auth or secrets-related path"),
    (re.compile(r"(^|/)(pyproject\.toml|setup\.(cfg|py)|requirements.+\.txt|Pipfile(\.lock)?|"
                r"package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|go\.mod|go\.sum|"
                r"Cargo\.toml|Cargo\.lock|Gemfile(\.lock)?|composer\.(json|lock)|poetry\.lock)$", re.I),
     "medium", "dependencies or lockfile"),
    (re.compile(r"(^|/)(Makefile|justfile|Procfile|\.gitlab-ci\.yml)$", re.I), "medium", "build or CI entrypoint"),
]

# Paths that look like "secret" in a docs filename should stay low.
_DOCS = re.compile(r"\.(md|rst|txt|adoc)$", re.I)
_SAFE_ENV = re.compile(r"\.env\.(example|sample|template|dist)$", re.I)


def score_path(rel: str, deleting: bool = False) -> tuple[str, str]:
    rel = rel.replace("\\", "/")
    if _SAFE_ENV.search(rel):
        return "low", "example env file"
    if _DOCS.search(rel) and not re.search(r"(^|/)\.github/workflows/", rel, re.I):
        return "low", "documentation"
    for rx, level, why in _RULES:
        if rx.search(rel):
            if deleting and LEVELS[level] < LEVELS["critical"] and "test file" in why:
                return "critical", f"deleting a {why}"
            if deleting and LEVELS[level] < LEVELS["high"]:
                return "high", f"deleting {why}"
            return level, why
    if deleting:
        return "medium", "deleting a project file"
    return "low", "project file"


def deleted_in(tool_input: dict) -> list[str]:
    patch = tool_input.get("patch") or tool_input.get("input") or ""
    if isinstance(patch, str):
        return re.findall(r"^\*\*\* Delete File: (.+)$", patch, re.M)
    return []


def score(files: list[str], deleting: list[str] | None = None) -> dict:
    gone = {d.replace("\\", "/") for d in (deleting or [])}
    reasons = []
    worst, worst_n = "none", 0
    for f in list(dict.fromkeys([*files, *gone])):
        level, why = score_path(f, deleting=f in gone)
        reasons.append({"path": f, "level": level, "why": why, "deleting": f in gone})
        if LEVELS[level] > worst_n:
            worst, worst_n = level, LEVELS[level]
    return {"level": worst, "score": worst_n, "reasons": reasons}


def _at_least(level: str, want: str) -> bool:
    return LEVELS.get(level, 0) >= LEVELS.get(want, 99)  # unknown want: never


def log(cfg: Config, event: dict) -> None:
    p = cfg.state / "risk.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    if p.stat().st_size > 1_000_000:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recent(cfg: Config, n: int = 20) -> list[dict]:
    p = cfg.state / "risk.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def prepare(cfg: Config, session: str, files: list[str], tool_input: dict | None = None) -> dict:
    """Score a coming write, snapshot first when the level is high enough, and record it."""
    opts = cfg.section("risk")
    if not opts.get("enabled", True):
        return {"level": "none", "score": 0, "reasons": [], "step": None, "blocked": False}
    scored = score(files, deleted_in(tool_input or {}))
    step = None
    if _at_least(scored["level"], str(opts.get("checkpoint") or "medium")):
        sh = Shadow(cfg)
        taken = sh.snapshot(tool="pre-risk", session=session, note=scored["level"])
        if taken:
            step = taken.step
        elif sh.steps():
            # Tree unchanged since the last snapshot (common on the first edit of a session);
            # that existing step is the restore point for the edit that is about to happen.
            step = sh.steps()[-1].step
    event = {**scored, "ts": time.time(), "session": session, "step": step,
             "blocked": _at_least(scored["level"], str(opts.get("block") or "off"))}
    log(cfg, event)
    return event


def nudge(event: dict, cfg: Config) -> str | None:
    opts = cfg.section("risk")
    if not event or not _at_least(event.get("level", "none"), str(opts.get("nudge") or "high")):
        return None
    why = ", ".join(f"{r['path']} ({r['why']})" for r in event["reasons"] if LEVELS[r["level"]] >= LEVELS["high"])
    where = f" Snapshot #{event['step']} was taken first; `keelyard rewind to {event['step']}` restores the files." if event.get("step") else ""
    return f"keelyard risk: {event['level']} — {why}.{where}"


def deny(event: dict, cfg: Config) -> dict | None:
    """Hook output that denies a write when [risk].block is at or below this level."""
    if not event or not event.get("blocked"):
        return None
    msg = nudge(event, cfg) or f"keelyard risk: {event['level']} change blocked by [risk].block."
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": msg}, "systemMessage": msg}
