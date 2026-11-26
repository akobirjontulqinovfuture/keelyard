"""Project configuration (.keelyard/config.toml) and on-disk layout."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """\
# keelyard configuration. Every field is optional.

[proof]
# Block the agent from finishing until a proof bundle exists.
gate = true
# Command used for before/after test runs. Empty = auto-detect.
test_cmd = ""
# URL of the running app; when set, a screenshot is required for proof.
app_url = ""
# Stop the gate from looping forever: give up after this many blocks per session.
max_blocks = 3
# No checklist written? A passing test run after the last edit (also one run in the agent's shell) proves the task.
auto_requirements = true
# Run the tests after every edit and append a compact result to the edit's output, so the agent needs no
# separate test call. The lean preset turns this on.
auto_test_after_edit = false

[rewind]
enabled = true
# Files larger than this are left out of snapshots and listed as skipped.
max_file_mb = 20
# Secrets (.env, *.pem, *.key, id_rsa*, credentials.json, ...) are never snapshotted. true = snapshot them too.
include_secrets = false
# More gitignore-style patterns to keep out of snapshots, e.g. ["data/", "*.sqlite"].
exclude = []
# Tools that trigger a snapshot after they run.
snapshot_tools = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "apply_patch", "write", "edit", "bash", "patch"]

[swarm]
# Max concurrent helper calls.
concurrency = 16
# Hard cap on tasks per swarm call.
max_tasks = 256
# Which [models.*] entry helpers use.
helper_model = "cheap"
# Samples per task (best-of-N), retries with verifier feedback, and the model tried once when all fail
# ("" = no escalation). Draft patches are also run against the tests in a temporary copy.
best_of = 2
retries = 1
escalate_model = "strong"
test_patches = true
# Cooperative mode (default; `keelyard swarm run --basic` turns it off for one run): models tried cheapest first
# ([] = helper_model then escalate_model), concurrent workers, a peer critic, and hard token caps
# (0 = no cap). Calls are refused before they are made once a cap would be crossed.
cooperative = true
ladder = []
workers = 64
critic = false
budget_tokens = 200000
task_budget_tokens = 20000

[brain]
# Give the agent a short keelyard brief at session start and again after context compaction.
brief = true
brief_tokens = 600
# Put the full text of small project files into the brief (stable, cache-friendly prefix), up to this many
# tokens; the agent then needs no read calls. 0 = off. The lean preset sets 1500.
inline_files_tokens = 0
# Add the most relevant past-session snippets to each prompt (0 hits = nothing added).
recall = true
recall_tokens = 250
# Short hints while the agent works: destructive commands, edits with no tests nearby, budget.
nudges = true
# Seconds before the same kind of hint can repeat, and a hard cap per session.
nudge_cooldown_s = 300
max_nudges_per_session = 12
# Your plan's context/session token budget for "delegate to helpers" hints. 0 = off.
plan_tokens = 0
budget_warn_at = 0.6


[audit]
# Compare what the agent claimed about tests with the recorded runs and the test inventory.
enabled = true
# When a false claim is found, the Stop hook also blocks (if the proof gate is on).
block = true

[risk]
# Snapshot *before* an edit at or above this level (low, medium, high, critical). off = never.
enabled = true
checkpoint = "medium"
# Nudge the agent at or above this level.
nudge = "high"
# Deny the edit at or above this level. off = never deny, only snapshot and nudge.
block = "off"

[budget]
# 0 = off. When the current session crosses a cap, further edits and shell commands are denied
# (Stop still works, and so does rewind). Spend is read from the agent transcript when its path is known.
max_tokens = 0
max_usd = 0
# Warn (do not block) at this fraction of a cap.
warn_at = 0.8
block = true

[mcp]
# Tools the MCP server offers. Every schema costs tokens in every session, so the default is the 14 the
# workflow needs. "all" adds outline, project_map, rewind_diff, usage_report, route_task, swarm_plan,
# swarm_verify, status (and proof_screenshot, which "core" adds by itself when [proof].app_url is set).
# "lazy" sends only read_file, run_compact, find_symbol, proof_status plus tool_search/tool_call (about 60%
# fewer schema tokens than "core"); every other tool is found and run on demand. It falls back to "core"
# by itself if it would not be smaller.
tools = "core"

[saver]
# Deny a repeat Read of an unchanged file (reset on context compaction).
dedupe_reads = true
# Filter large Bash results before the model reads them (raw output kept for `keelyard recall`).
filter_bash = true
filter_min_chars = 1200
# Max characters returned by compress_output before head/tail trimming.
max_output_chars = 6000

# Optional prices for `keelyard usage`, USD per million tokens. keelyard ships none.
# [prices."your-model-name"]
# input = 0.0
# output = 0.0
# cache_read = 0.0
# cache_write = 0.0

# Models are plain names you choose; keelyard never assumes a vendor's lineup.
# provider = "openai" (any OpenAI-compatible /chat/completions) or "anthropic".
[models.cheap]
provider = "openai"
base_url = "http://localhost:11434/v1"
model = "qwen2.5-coder:7b"
api_key_env = ""

[models.strong]
provider = "anthropic"
base_url = "https://api.anthropic.com"
model = "set-me"
api_key_env = "ANTHROPIC_API_KEY"
"""


class ProjectError(RuntimeError):
    """Raised when keelyard is asked to act outside a real project."""


VENDOR_DIRS = {".git", ".keelyard", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
               ".ruff_cache", "dist", "build", "target", ".next", ".tox", ".idea", ".gradle"}


def find_root(start: Path | None = None) -> Path:
    """Project root: nearest ancestor with .keelyard or .git, else cwd."""
    env = os.environ.get("KEELYARD_ROOT")
    if env:
        return Path(env).resolve()
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / ".keelyard").is_dir() or (p / ".git").exists():
            return p
    return cur


def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "keelyard"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    root: Path
    raw: dict[str, Any] = field(default_factory=dict)

    def require_project(self) -> None:
        """Refuse to snapshot or write state in $HOME or / unless the user ran `keelyard init` there."""
        marker = (self.root / ".keelyard").is_dir()
        risky = self.root in (Path.home().resolve(), Path(self.root.anchor))
        if not marker and (risky or not (self.root / ".git").exists()):
            raise ProjectError(f"{self.root} is not a git repository or keelyard project. "
                               "Hint: run `keelyard init` in your project folder.")

    @property
    def state(self) -> Path:
        d = self.root / ".keelyard"
        if not (d / ".gitignore").exists():
            d.mkdir(parents=True, exist_ok=True)  # hooks race here; never fail on "already exists"
            # Never let agent state leak into the user's own commits.
            (d / ".gitignore").write_text("*\n", encoding="utf-8")
        return d

    def section(self, name: str) -> dict[str, Any]:
        defaults = tomllib.loads(DEFAULT_CONFIG).get(name, {})
        return {**defaults, **self.raw.get(name, {})}

    def model(self, key: str) -> dict[str, Any]:
        models = {**tomllib.loads(DEFAULT_CONFIG)["models"], **self.raw.get("models", {})}
        if key not in models:
            raise KeyError(f"no [models.{key}] in .keelyard/config.toml")
        return models[key]


def load(root: Path | None = None) -> Config:
    root = root or find_root()
    path = root / ".keelyard" / "config.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path} is not valid TOML: {e}. Hint: fix the line, or delete the file and run `keelyard init`.") from None
    return Config(root=root, raw=raw)


# The lean defaults the session benchmark measured (bench/session). Each pair: the old default line -> the lean one.
# Only lines still at the old default are changed, so a value the user set by hand is never touched.
LEAN_LINES = {"inline_files_tokens = 0": "inline_files_tokens = 1500",
              "auto_test_after_edit = false": "auto_test_after_edit = true"}


def has_tests(root: Path, text: str = "") -> bool:
    """True when the project has a test command: `[proof].test_cmd` in the config text, or one keelyard detects."""
    from .proof import detect_test_cmd

    if re.search(r'(?m)^test_cmd\s*=\s*"[^"]+"', text):
        return True
    return bool(detect_test_cmd(root))


def apply_lean(text: str, tests: bool = True) -> str:
    """Lean defaults; the test-after-edit line only when the project has tests (nothing to run otherwise)."""
    for old, new in LEAN_LINES.items():
        if not tests and old.startswith("auto_test_after_edit"):
            continue
        text = re.sub(rf"(?m)^{re.escape(old)}$", new, text)
    return text


def lean_drift(text: str, tests: bool = True) -> list[str]:
    """Lean settings missing from a config text (a default line still there, or a line set to another value)."""
    out = []
    for new in LEAN_LINES.values():
        if not tests and new.startswith("auto_test_after_edit"):
            continue
        key = new.split(" = ")[0]
        m = re.search(rf"(?m)^{key} = (.*)$", text)
        if not m or f"{key} = {m.group(1).strip()}" != new:
            out.append(f"{key} = {m.group(1).strip() if m else '(missing)'} (lean: {new.split(' = ')[1]})")
    return out


def init(root: Path, lean: bool = True) -> Path:
    """Create .keelyard/config.toml (lean defaults unless lean=False). An existing file is left as it is."""
    cfg = Config(root=root)
    if root in (Path.home().resolve(), Path(root.anchor)):
        raise ProjectError(f"refusing to set up keelyard in {root}. Hint: cd into a project folder first.")
    path = cfg.state / "config.toml"
    if not path.exists():
        path.write_text(apply_lean(DEFAULT_CONFIG, has_tests(root)) if lean else DEFAULT_CONFIG, encoding="utf-8")
    return path


def migrate_lean(cfg: Config) -> bool:
    """Upgrade path: a project on the lean preset (or set up before presets existed) whose config still has the old
    default lines gets the lean values. Returns True when the file changed."""
    import json as _json

    pf = cfg.state / "preset.json"
    try:
        preset = _json.loads(pf.read_text(encoding="utf-8")).get("preset", "lean") if pf.exists() else "lean"
    except (OSError, ValueError):
        return False
    path = cfg.state / "config.toml"
    if preset != "lean" or not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new = apply_lean(text, has_tests(cfg.root, text))
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True
