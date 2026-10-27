"""Command-aware output filters.

Ideas surveyed from rtk (Apache-2.0), claude-context-optimizer and repomix (MIT):
group similar items, keep failures and drop passes, collapse repeats, and keep a
way back to the raw output. This module is an independent Python implementation;
no code was copied. See NOTICE.

Design rule that differs from most compressors: a filter must never drop a line
that names a failure, an error, or a file:line location. bench.retention checks it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex  # noqa: F401
import subprocess
import time
from collections import OrderedDict, defaultdict
from collections.abc import Callable
from pathlib import Path

from .saver import ANSI, IMPORTANT, approx_tokens, compress_output

MAX_LINE = 180


def _clip(line: str, n: int = MAX_LINE) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def _clean(text: str) -> list[str]:
    text = ANSI.sub("", text).replace("\r\n", "\n")
    return [ln.rsplit("\r", 1)[-1].rstrip() for ln in text.split("\n")]


# ----------------------------------------------------------------- git
def git_status(out: str) -> str:
    groups: dict[str, list[str]] = OrderedDict()
    branch, section = "", ""
    names = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "??": "untracked", "U": "conflict"}
    for ln in _clean(out):
        if ln.startswith("## "):
            branch = ln[3:]
        elif ln.startswith("On branch "):
            branch = ln[10:]
        elif ln.startswith("Untracked files"):
            section = "untracked"
        elif ln.startswith(("Changes to be committed", "Changes not staged")):
            section = ""
        elif m := re.match(r"^([ MADRCU?!]{2}) (.+)$", ln):
            code = m.group(1).strip() or "M"
            groups.setdefault(names.get(code, names.get(code[0], code)), []).append(m.group(2))
        elif m := re.match(r"^\t(modified|new file|deleted|renamed|both modified):\s+(.+)$", ln):
            groups.setdefault(m.group(1).replace("new file", "added"), []).append(m.group(2))
        elif section == "untracked" and ln.startswith("\t"):
            groups.setdefault("untracked", []).append(ln.strip())
    if not groups:
        return (f"branch {branch}: " if branch else "") + "clean"
    parts = [f"branch {branch}"] if branch else []
    for k, files in groups.items():
        shown = ", ".join(files[:30]) + (f" (+{len(files) - 30})" if len(files) > 30 else "")
        parts.append(f"{k} ({len(files)}): {shown}")
    return "\n".join(parts)


def git_log(out: str, limit: int = 30) -> str:
    rows, cur = [], {}
    for ln in _clean(out):
        if ln.startswith("commit "):
            if cur:
                rows.append(cur)
            cur = {"sha": ln.split()[1][:9], "msg": ""}
        elif ln.startswith("Author:"):
            cur["who"] = re.sub(r"\s*<.*>", "", ln[7:].strip())
        elif ln.startswith("Date:"):
            cur["date"] = ln[5:].strip()
        elif ln.startswith("    ") and cur is not None and not cur.get("msg"):
            cur["msg"] = ln.strip()
    if cur:
        rows.append(cur)
    if not rows:  # already --oneline or custom format
        lines = [ln for ln in _clean(out) if ln.strip()]
        return "\n".join(_clip(ln) for ln in lines[:limit]) + (f"\n… {len(lines) - limit} more" if len(lines) > limit else "")
    body = [f"{r['sha']} {r.get('date', '')[:16]} {r.get('who', '')}: {_clip(r['msg'], 120)}" for r in rows[:limit]]
    if len(rows) > limit:
        body.append(f"… {len(rows) - limit} more commits")
    return "\n".join(body)


def git_diff(out: str, context: int = 1) -> str:
    keep, ctx_buf = [], []
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    cur = ""
    for ln in _clean(out):
        if ln.startswith("diff --git"):
            cur = ln.split(" b/")[-1]
            keep.append(f"=== {cur}")
            ctx_buf = []
        elif ln.startswith(("index ", "--- ", "+++ ", "new file mode", "deleted file mode", "similarity")):
            continue
        elif ln.startswith("@@"):
            keep.append(re.sub(r"^(@@[^@]*@@).*$", r"\1", ln))
            ctx_buf = []
        elif ln.startswith(("+", "-")):
            keep.extend(ctx_buf[-context:] if context else [])
            ctx_buf = []
            keep.append(_clip(ln, 220))
            stats[cur][0 if ln[0] == "+" else 1] += 1
        else:
            ctx_buf.append(_clip(ln, 120))
    head = " ".join(f"{f} +{a}/-{d}" for f, (a, d) in stats.items())
    return (head + "\n" if head else "") + "\n".join(keep)


# ----------------------------------------------------------------- tests
PY_SUMMARY = re.compile(r"^=+ .*(passed|failed|error|skipped|no tests ran).* =+$|^\d+ (passed|failed)")
FAIL_LINE = re.compile(r"^(FAILED|ERROR) |^E\s|^\S+\.py:\d+:|^>\s|Error\b|^_{3,} .* _{3,}$")


def pytest(out: str) -> str:
    lines = _clean(out)
    keep, in_fail = [], False
    for ln in lines:
        if re.match(r"^=+ (FAILURES|ERRORS) =+$", ln):
            in_fail = True
            keep.append(ln)
            continue
        if re.match(r"^=+ (short test summary info|warnings summary)", ln):
            in_fail = "warnings summary" in ln and "warn"
            keep.append(ln)
            continue
        if in_fail == "warn":  # the warning text and where it came from; the docs link is the only noise
            if ln.strip() and not ln.startswith("-- Docs:"):
                keep.append(_clip(ln, 300))
            continue
        if PY_SUMMARY.search(ln) or ln.startswith(("FAILED ", "ERROR ")):
            keep.append(_clip(ln, 300))
        elif in_fail and FAIL_LINE.search(ln) and not LIB_FRAME.search(ln):
            keep.append(_clip(ln, 300))
    if not keep:
        return compress_output(out)
    keep = [BANNER.sub(r"\1\1 \2 \1\1", ln) for ln in keep]  # "==== X ====" -> "== X ==": same meaning, fewer tokens
    return "\n".join(dict.fromkeys(keep))  # dedupe, keep order


LIB_FRAME = re.compile(r"(site-packages|dist-packages|[\\/]lib[\\/]python3?[\d.]*[\\/]|<frozen )")
BANNER = re.compile(r"^([=_])\1{2,} (.*?) \1{3,}$")


BLOCK_START = re.compile(r"^warning(\[\w+\])?: (?!`[^`]+` \(\w+( test)?\) generated)|"
                         r"^---- .* stdout ----|^\s*● |^\s*--- FAIL|^\s*(FAIL|✕|×|✗)\s|^\s*\d+\) |panicked at")
SUMMARY = re.compile(r"(?i)^test result:|^(FAIL|ok)\s+\S+\s+[\d.]+s|^tests?:\s|^test suites?:|^\s*(passed|failed|skipped)\b|"
                     r"^error(\[|:)|^\s*failures:$|^\s{4}\S+::\S+$|\b\d+ (passing|failing|passed|failed)\b")
GO_RUN = re.compile(r"^\s*=== (RUN|PAUSE|CONT|NAME)\s")
GO_PASS = re.compile(r"^\s*--- (PASS|SKIP):")
NOISE = re.compile(r"^note: run with `RUST_BACKTRACE|^\s+at (node:|.*node_modules)")


def generic_tests(out: str, block_cap: int = 40) -> str:
    """cargo test, go test, jest, vitest, mocha, npm test: whole failure blocks + summaries, passes dropped."""
    keep: list[str] = []
    in_block, blank, n = False, 0, 0
    pending: list[str] = []  # go test -v prints a test's messages *before* its "--- FAIL" line
    for ln in _clean(out):
        if GO_RUN.match(ln):
            pending, in_block = [], False
            continue
        if GO_PASS.match(ln):
            pending, in_block = [], False
            continue
        if BLOCK_START.search(ln):
            if ln.lstrip().startswith("--- FAIL"):
                keep.extend(_clip(p, 240) for p in pending[-block_cap:])
            pending = []
            in_block, blank, n = True, 0, 0
            keep.append(_clip(ln, 240))
            continue
        if not in_block and ln.startswith(("    ", "\t")) and ln.strip():
            pending.append(ln)
        if in_block:
            if not ln.strip():
                blank += 1
                if blank >= 2:
                    in_block = False
                continue
            blank = 0
            n += 1
            if n <= block_cap and not NOISE.search(ln):
                keep.append(_clip(ln, 240))
            continue
        if SUMMARY.search(ln) or IMPORTANT.search(ln) and not re.search(r"\.\.\. ok$|✓|√|PASS\b", ln):
            keep.append(_clip(ln, 240))
    if not keep:
        return compress_output(out)
    return "\n".join(dict.fromkeys(keep))


# ----------------------------------------------------------------- search & listing
def grep(out: str, per_file: int = 12) -> str:
    by_file: dict[str, list[str]] = OrderedDict()
    other = []
    for ln in _clean(out):
        m = re.match(r"^(.+?):(\d+):(.*)$", ln)
        if m:
            by_file.setdefault(m.group(1), []).append(f"  {m.group(2)}: {_clip(m.group(3).strip(), 140)}")
        elif ln.strip():
            other.append(ln)
    if not by_file:
        return compress_output(out)
    body = []
    for f, hits in by_file.items():
        body.append(f"{f} ({len(hits)})")
        body.extend(hits[:per_file])
        if len(hits) > per_file:
            body.append(f"  … {len(hits) - per_file} more in {f}")
    return "\n".join(body + other[:20])


def listing(out: str, per_dir: int = 15) -> str:
    """find / ls -R / git ls-files: group paths by directory."""
    paths = [ln.strip() for ln in _clean(out) if ln.strip()]
    if not paths or not all("/" in p or "." in p for p in paths[:50]):
        return compress_output(out)
    dirs: dict[str, list[str]] = OrderedDict()
    for p in paths:
        d, _, name = p.rstrip("/").rpartition("/")
        dirs.setdefault(d or ".", []).append(name)
    body = [f"{len(paths)} paths in {len(dirs)} dirs"]
    for d, names in dirs.items():
        shown = " ".join(names[:per_dir]) + (f" (+{len(names) - per_dir})" if len(names) > per_dir else "")
        body.append(f"{d}/: {shown}")
    return "\n".join(body)


def lint(out: str) -> str:
    """ruff/eslint/flake8/pyright style 'path:line:col: CODE message' grouped by rule."""
    rules: dict[str, list[str]] = OrderedDict()
    rest = []
    lines = _clean(out)
    # ruff's default "full" format: "CODE message" then " --> path:line:col" and a code frame
    for i, ln in enumerate(lines[:-1]):
        m = re.match(r"^([A-Z]+\d+) (.*)$", ln)
        loc = re.match(r"^\s*--> (.+?:\d+:\d+)", lines[i + 1])
        if m and loc:
            rules.setdefault(f"{m.group(1)} {_clip(m.group(2), 80)}", []).append(loc.group(1))
    if rules:
        tail = [ln for ln in lines if re.match(r"^(Found \d+|\[\*\]|No fixes|\d+ fixable)", ln)]
        return "\n".join([f"{r} ×{len(v)}: {', '.join(v)}" for r, v in rules.items()] + tail)
    for ln in lines:
        m = re.match(r"^(.+?:\d+(?::\d+)?):?\s+([A-Z]+\d+|error|warning)\b:?\s*(.*)$", ln)
        if m:
            rules.setdefault(f"{m.group(2)} {_clip(m.group(3), 80)}", []).append(m.group(1))
        elif ln.strip():
            rest.append(ln)
    if not rules:
        return compress_output(out)
    body = [f"{rule} ×{len(locs)}: {', '.join(locs[:10])}" + (" …" if len(locs) > 10 else "") for rule, locs in rules.items()]
    return "\n".join(body + [ln for ln in rest if IMPORTANT.search(ln) or "Found" in ln][:10])


def mypy(out: str) -> str:
    """mypy: one diagnostic per line, 'path:line: error: message  [code]'. Grouping by message would clip the
    trailing [code], so every diagnostic is kept whole (long lines clipped in the middle, never at the end)."""
    keep = []
    for ln in _clean(out):
        if re.match(r"^.+?:\d+(?::\d+)?: (error|warning|note): ", ln) or re.match(r"^(Found \d+|Success:)", ln):
            keep.append(ln if len(ln) <= MAX_LINE else ln[: MAX_LINE - 40] + " … " + ln[-37:])
    return "\n".join(keep) if keep else compress_output(out)


# ----------------------------------------------------------------- dispatch
RULES: list[tuple[re.Pattern[str], Callable[[str], str]]] = [
    (re.compile(r"^git status\b"), git_status),
    (re.compile(r"^git log\b"), lambda out: git_log(out, 1000)),
    (re.compile(r"^git (diff|show)\b"), git_diff),
    (re.compile(r"^(python3? -m )?pytest\b|^uv run pytest\b"), pytest),
    (re.compile(r"^(cargo test|go test|npm (run )?test|pnpm test|yarn test|npx (jest|vitest)|jest|vitest|bun test)\b"), generic_tests),
    (re.compile(r"^(rg|grep|git grep)\b"), grep),
    (re.compile(r"^(find|fd|git ls-files|ls -R)\b"), listing),
    (re.compile(r"^(python3? -m |uv run )?d?mypy\b"), mypy),
    (re.compile(r"^(ruff( check)?|eslint|npx eslint|flake8|pyright|npx pyright|tsc|npx tsc)\b"), lint),
]


def pick(cmd: str) -> Callable[[str], str] | None:
    c = cmd.strip()
    for pat, fn in RULES:
        if pat.search(c):
            return fn
    return None


def _tabulate(v):
    """Lossless: a list of objects that share their keys becomes {"columns": [...], "rows": [[...], ...]}."""
    if isinstance(v, dict):
        return {k: _tabulate(x) for k, x in v.items()}
    if isinstance(v, list):
        v = [_tabulate(x) for x in v]
        if len(v) >= 3 and all(isinstance(x, dict) for x in v):
            cols = list(v[0])
            if cols and all(list(x) == cols for x in v) and "columns" not in cols:
                return {"columns": cols, "rows": [[x[c] for c in cols] for x in v]}
        return v
    return v


def compact_json(out: str) -> str | None:
    """JSON output (an API response, `--json` CLIs, `gh ... --json`): minified, uniform lists as tables.
    Every value is kept; None when the output is not one JSON document or nothing would be saved."""
    text = out.strip()
    if not text or text[0] not in "[{" or len(text) < 200:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    small = json.dumps(_tabulate(data), ensure_ascii=False, separators=(",", ":"))
    note = "[keelyard: JSON minified; lists of same-key objects as {columns, rows}]\n"
    return note + small if len(note) + len(small) < len(text) else None


def filter_output(cmd: str, out: str) -> str:
    js = compact_json(out)
    if js is not None:
        return js
    fn = pick(cmd)
    try:
        small = fn(out) if fn else compress_output(out)
    except Exception:  # noqa: BLE001 - a filter bug must never hide output
        small = compress_output(out)
    return small if len(small) < len(out) else out


from .proc import split_cmd  # noqa: E402


def run_filtered(cmd: str, cwd: Path, raw_dir: Path, timeout: int = 900) -> tuple[int, str]:
    t0 = time.time()
    try:
        p = subprocess.run(split_cmd(cmd), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, raw = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return 127, str(e)
    small = filter_output(cmd, raw)
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    omitted = len(raw.splitlines()) - len(small.splitlines())
    footer = (f"\n[keelyard: exit {code}; {approx_tokens(raw)}→{approx_tokens(small)} tokens"
              f"{f', {omitted} passing/noise lines collapsed' if omitted > 0 else ''} in {time.time() - t0:.1f}s; "
              f"full output: keelyard recall {h}]")
    if len(small) + len(footer) >= len(raw.rstrip()):
        return code, raw.rstrip("\n")  # filtering would not save anything once the footer is counted: never add tokens
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / h).write_text(raw, encoding="utf-8")
    return code, small + footer
