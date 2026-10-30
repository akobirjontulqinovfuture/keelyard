"""Arena: run the same task across agents and models, then measure what happened.

For each (agent, run) the task's template repo is copied fresh, the agent's CLI is
run headless with the prompt, and the task's check command decides pass/fail.
Tokens and cost come from the agent's own machine-readable output. Nothing is estimated:
if an agent does not report usage, the cell says "n/a".

Results go to results.json (shareable, re-renderable) and report.html (+ chart.png
when matplotlib is installed).
"""

from __future__ import annotations

import html
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Run:
    agent: str
    model: str
    run: int
    passed: bool
    seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cost_usd: float | None
    files_changed: int
    exit_code: int
    note: str = ""


def parse_usage(stdout: str) -> dict:
    """Understands Claude Code `-p --output-format json`, Codex `exec --json` and OpenCode-style JSON lines."""
    found = {"input": None, "output": None, "cache_read": None, "cost": None}

    def add(k, v):
        if isinstance(v, (int, float)):
            found[k] = (found[k] or 0) + v

    docs = []
    try:
        docs.append(json.loads(stdout))
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    docs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    flat = [x for d in docs for x in (d if isinstance(d, list) else [d])]
    for d in flat:
        if not isinstance(d, dict):
            continue
        if d.get("type") == "result" or "total_cost_usd" in d:  # Claude Code
            u = d.get("usage") or {}
            add("input", u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0))
            add("output", u.get("output_tokens"))
            add("cache_read", u.get("cache_read_input_tokens"))
            add("cost", d.get("total_cost_usd"))
        elif d.get("type") == "turn.completed" and isinstance(d.get("usage"), dict):  # Codex exec --json
            u = d["usage"]
            add("input", u.get("input_tokens", 0) - u.get("cached_input_tokens", 0))
            add("output", u.get("output_tokens"))
            add("cache_read", u.get("cached_input_tokens"))
        elif isinstance(d.get("tokens"), dict):  # OpenCode message
            t = d["tokens"]
            add("input", t.get("input"))
            add("output", (t.get("output") or 0) + (t.get("reasoning") or 0))
            add("cache_read", (t.get("cache") or {}).get("read"))
            add("cost", d.get("cost"))
    return found


def _changed(repo: Path) -> int:
    p = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return len([ln for ln in p.stdout.splitlines() if ln.strip()])


def run_task(spec_path: Path, out_dir: Path, only: list[str] | None = None, progress=None) -> list[Run]:
    spec = tomllib.loads(spec_path.read_text(encoding="utf-8"))
    task = spec["task"]
    template = (spec_path.parent / task["repo"]).resolve()
    runs = int(task.get("runs", 1))
    timeout = int(task.get("timeout", 900))
    results: list[Run] = []
    for agent in spec.get("agent", []):
        if only and agent["name"] not in only:
            continue
        exe = agent["cmd"][0]
        if not shutil.which(exe) and not (spec_path.parent / exe).exists():
            results.append(Run(agent["name"], agent.get("model", ""), 0, False, 0, None, None, None, None, 0, 127,
                               f"{exe} not installed"))
            continue
        for i in range(1, runs + 1):
            with tempfile.TemporaryDirectory(prefix="keelyard-arena-") as tmp:
                repo = Path(tmp) / "repo"
                shutil.copytree(template, repo)
                subprocess.run(["git", "init", "-q"], cwd=repo)
                subprocess.run(["git", "add", "-A"], cwd=repo)
                subprocess.run(["git", "-c", "user.name=arena", "-c", "user.email=a@a", "commit", "-qm", "base"], cwd=repo)
                fmt = {"prompt": task["prompt"], "model": agent.get("model", ""), "repo": str(repo),
                       "here": str(spec_path.parent.resolve())}
                cmd = [c.format(**fmt) for c in agent["cmd"]]
                t0 = time.time()
                try:
                    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
                    code, out, note = p.returncode, p.stdout, ""
                except subprocess.TimeoutExpired:
                    code, out, note = 124, "", f"timeout {timeout}s"
                secs = time.time() - t0
                changed = _changed(repo)  # before the check: test caches it writes are not the agent's edits
                if progress:
                    progress(agent["name"], i, runs)
                try:
                    chk = subprocess.run(task["check"], shell=True, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
                    check_ok = chk.returncode == 0
                except subprocess.TimeoutExpired:
                    check_ok, note = False, (note + "; " if note else "") + "check timed out"
                u = parse_usage(out)
                results.append(Run(agent["name"], agent.get("model", ""), i, check_ok, round(secs, 1),
                                   u["input"], u["output"], u["cache_read"],
                                   round(u["cost"], 4) if u["cost"] is not None else None, changed, code, note))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(
        {"task": task, "generated": time.strftime("%Y-%m-%d %H:%M:%S %z"), "runs": [asdict(r) for r in results]}, indent=2), encoding="utf-8")
    render(out_dir)
    return results


def summarize(runs: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in runs:
        by.setdefault(f"{r['agent']}|{r['model']}", []).append(r)
    rows = []
    for key, rs in by.items():
        agent, model = key.split("|", 1)

        def med(k, rs=rs):
            v = [r[k] for r in rs if r[k] is not None]
            return statistics.median(v) if v else None

        rows.append({"agent": agent, "model": model, "runs": len(rs), "pass_rate": sum(r["passed"] for r in rs) / len(rs),
                     "median_seconds": med("seconds"), "median_input": med("input_tokens"), "median_output": med("output_tokens"),
                     "median_cache_read": med("cache_read_tokens"), "median_cost": med("cost_usd"),
                     "note": "; ".join(sorted({r["note"] for r in rs if r["note"]}))})
    return rows


def _fmt(v, kind="int"):
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "usd":
        return f"${v:.4f}"
    if kind == "s":
        return f"{v:.1f}s"
    return f"{int(v):,}"


ARENA_CSS = """
.runs{display:inline-flex;gap:3px;vertical-align:middle}.runs i{width:9px;height:9px;border-radius:2px;background:var(--line)}
.runs i.p{background:var(--green)}.runs i.f{background:var(--red)}
.who b{font-weight:500;display:block}.who span{font:12px 'Geist Mono',monospace;color:var(--muted)}
.bar{height:6px;border-radius:3px;background:var(--ink);opacity:.85;margin-top:6px}
.meta{display:grid;grid-template-columns:120px 1fr;gap:6px 16px;font-size:14px;margin-top:26px}
.meta dt{color:var(--muted)}.meta dd{margin:0}
"""


def render(out_dir: Path) -> Path:
    from . import __version__
    from .theme import page

    data = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    runs = data["runs"]
    rows = sorted(summarize(runs), key=lambda r: (-r["pass_rate"], (r["median_input"] or 0) + (r["median_output"] or 0)))
    e = html.escape
    task = data["task"]
    maxtok = max([(r["median_input"] or 0) + (r["median_output"] or 0) for r in rows] + [1])
    body = [f"<style>{ARENA_CSS}</style><div class=brand><i></i>keelyard arena</div><h1>{e(task['name'])}</h1>",
            f"<dl class=meta><dt>Prompt</dt><dd>{e(task['prompt'])}</dd><dt>Pass if</dt><dd><code>{e(task['check'])}</code> exits 0</dd>"
            f"<dt>Runs</dt><dd>{len(runs)} total, a fresh copy of the repo each time</dd><dt>Generated</dt><dd>{e(data['generated'])}</dd></dl>",
            "<h2>Results</h2><div class=box><table><tr><th>Contender</th><th>Passed</th><th class=n>Median time</th>"
            "<th class=n>Tokens in / out</th><th class=n>Cost</th></tr>"]
    for r in rows:
        mine = [x for x in runs if x["agent"] == r["agent"] and x["model"] == r["model"]]
        dots = "".join(f"<i class={'p' if x['passed'] else 'f'}></i>" for x in mine if x["run"])
        tok = (r["median_input"] or 0) + (r["median_output"] or 0)
        bar = f"<div class=bar style='width:{max(2, 100 * tok / maxtok):.0f}%'></div>" if tok else ""
        passed = sum(x["passed"] for x in mine)
        n_real = len([x for x in mine if x["run"]])
        cell = (f"<span class=runs>{dots}</span>&nbsp; <span class=mono>{passed}/{n_real}</span>" if n_real
                else f"<span style='color:var(--muted)'>{e(r['note'] or 'no runs')}</span>")
        toks = f"{_fmt(r['median_input'])} / {_fmt(r['median_output'])}" if r["median_input"] is not None else "n/a"
        cost = _fmt(r["median_cost"], "usd") if r["median_cost"] else ("$0" if r["median_cost"] == 0 else "n/a")
        body.append(f"<tr><td class=who><b>{e(r['agent'])}</b><span>{e(r['model'])}</span></td><td>{cell}</td>"
                    f"<td class=n>{_fmt(r['median_seconds'], 's') if n_real else ''}</td><td class=n style='min-width:170px'>{toks}{bar}</td>"
                    f"<td class=n>{cost if n_real else ''}</td></tr>")
    body.append("</table></div>")
    body.append("<footer><span>Tokens and cost are what each contender reported about itself; n/a means it reported nothing. "
                f"Raw data is in results.json.</span><span class=mono>keelyard {__version__}</span></footer>")
    (out_dir / "report.html").write_text(page(f"Arena · {task['name']}", "".join(body)), encoding="utf-8")
    try:
        _chart(rows, task["name"], out_dir / "chart.png")
    except ImportError:
        pass
    return out_dir / "report.html"


def _chart(rows: list[dict], title: str, path: Path) -> None:
    import logging

    import matplotlib  # type: ignore[import-not-found]  # optional extra keelyard[report], not installed for type checking

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # "findfont: ..." is noise, not a problem
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]  # same optional extra
    from matplotlib import font_manager

    for f in font_manager.findSystemFonts():
        if "Geist-" in f:
            font_manager.fontManager.addfont(f)
    plt.rcParams.update({"font.family": ["Geist", "DejaVu Sans"], "font.size": 10, "axes.edgecolor": "#E7E5E4",
                         "axes.labelcolor": "#6B6B66", "xtick.color": "#6B6B66", "ytick.color": "#111111"})
    rows = [r for r in rows if r["median_input"] is not None]
    fig, ax = plt.subplots(figsize=(8, 0.9 + 0.55 * max(1, len(rows))), dpi=200)
    fig.patch.set_facecolor("#FAFAF9")
    ax.set_facecolor("#FAFAF9")
    labels = [f"{r['agent']}  ({r['pass_rate'] * 100:.0f}% pass)" for r in rows][::-1]
    vals = [(r["median_input"] or 0) + (r["median_output"] or 0) for r in rows][::-1]
    colors = ["#1F6F4A" if r["pass_rate"] >= 0.5 else "#B42318" for r in rows][::-1]
    ax.barh(labels, vals, color=colors, height=0.5)
    for i, v in enumerate(vals):
        ax.text(v, i, f"  {v:,.0f}", va="center", color="#111111", fontsize=9)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("median tokens per run (input + output)")
    ax.set_title(title, loc="left", fontsize=12, color="#111111", fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
