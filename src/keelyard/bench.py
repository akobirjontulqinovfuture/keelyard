"""Benchmark harness. Reports only what it measured on this machine.

1. compress   - token reduction of run_compact on the real command logs in bench/corpus/
2. reads      - repeated reads of unchanged files in your own Claude Code transcripts
                (what the dedupe hook would have blocked; an upper bound, see notes)
3. memory     - snippet size vs full transcript, and recall@8 of the source session
4. snapshot   - wall time of a snapshot after a one-file edit in this project
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from .config import Config
from .memory import Memory, default_sources
from .saver import approx_tokens, compress_output
from .snapshot import Shadow


def bench_compress(corpus: Path) -> dict:
    rows: list[dict[str, Any]] = []
    for f in sorted(corpus.glob("*.log")):
        raw = f.read_text(errors="replace", encoding="utf-8")
        small = compress_output(raw)
        rows.append({"file": f.name, "raw": approx_tokens(raw), "out": approx_tokens(small)})
    if not rows:
        return {"skipped": f"no *.log files in {corpus}"}
    tot_raw, tot_out = sum(r["raw"] for r in rows), sum(r["out"] for r in rows)
    return {"files": rows, "raw_tokens": tot_raw, "out_tokens": tot_out,
            "reduction_pct": round(100 * (1 - tot_out / tot_raw), 1)}


def bench_reads() -> dict:
    base = dict(default_sources())["claude-code"]
    files = list(base.glob("*/*.jsonl")) if base.exists() else []
    if not files:
        return {"skipped": f"no Claude Code transcripts under {base}"}
    total_read_tokens = repeat_tokens = reads = repeats = 0
    for f in files:
        pending: dict[str, str] = {}
        seen: dict[str, str] = {}
        for line in f.read_text(errors="replace", encoding="utf-8").splitlines():
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (j.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use" and part.get("name") == "Read":
                    inp = part.get("input") or {}
                    use_id = part.get("id")
                    if isinstance(use_id, str) and not inp.get("offset") and not inp.get("limit"):
                        pending[use_id] = inp.get("file_path", "")
                elif part.get("type") == "tool_result" and part.get("tool_use_id") in pending:
                    path = pending.pop(part["tool_use_id"])
                    body = part.get("content")
                    body = body if isinstance(body, str) else json.dumps(body)
                    t = approx_tokens(body)
                    reads += 1
                    total_read_tokens += t
                    if seen.get(path) == body:  # identical content returned again
                        repeats += 1
                        repeat_tokens += t
                    seen[path] = body
    return {"transcripts": len(files), "full_reads": reads, "identical_repeat_reads": repeats,
            "read_tokens": total_read_tokens, "repeat_tokens": repeat_tokens,
            "repeat_share_pct": round(100 * repeat_tokens / total_read_tokens, 1) if total_read_tokens else 0.0,
            "note": "identical content re-read in the same transcript; compaction resets are not modelled"}


def bench_memory(mem: Memory) -> dict:
    sessions = mem.db.execute(
        "SELECT session, min(id) FROM chunks WHERE role='user' GROUP BY session ORDER BY random() LIMIT 50").fetchall()
    if not sessions:
        return {"skipped": "memory store is empty (run: keelyard memory index)"}
    hits, ratio = 0, []
    for sid, first in sessions:
        q = mem.db.execute("SELECT text FROM chunks WHERE id=?", (first,)).fetchone()[0][:200]
        found = mem.search(q, limit=8)
        hits += any(h["session"] == sid for h in found)
        full = approx_tokens(mem.session_text(sid))
        got = sum(approx_tokens(h["snippet"]) for h in found)
        if full:
            ratio.append(got / full)
    return {"sessions_sampled": len(sessions), "recall_at_8": round(hits / len(sessions), 3),
            "median_snippet_to_transcript_pct": round(100 * statistics.median(ratio), 2) if ratio else None,
            "note": "query = first 200 chars of the session's first user message (an easy query)"}


def bench_snapshot(cfg: Config, runs: int = 5) -> dict:
    """Time snapshots of this worktree into a throwaway shadow repo.

    Nothing is written to the project: no probe files and no steps on the user's timeline.
    Each timed run re-stages the whole tree (the cost an agent pays per edit hook).
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="keelyard-bench-") as tmp:
        sh = Shadow(cfg)
        sh.gitdir, sh.log, sh.lock = Path(tmp) / "shadow.git", Path(tmp) / "steps.jsonl", Path(tmp) / "lock"
        t0 = time.perf_counter()
        first = sh.snapshot("bench-baseline")
        first_ms = 1000 * (time.perf_counter() - t0)
        warm, cold = [], []
        for _ in range(runs):  # warm: the index is reused, as it is between two agent edits
            t0 = time.perf_counter()
            sh.snapshot("bench")
            warm.append(time.perf_counter() - t0)
        for _ in range(runs):  # cold: the index is thrown away and every file is re-hashed
            (Path(tmp) / "shadow.git" / "index").unlink(missing_ok=True)
            t0 = time.perf_counter()
            sh.snapshot("bench")
            cold.append(time.perf_counter() - t0)
        n_files = len(first.files) if first else 0
    ms = lambda xs: round(1000 * statistics.median(xs), 1)  # noqa: E731
    return {"files": n_files, "runs": runs, "first_ms": round(first_ms, 1), "median_ms": ms(warm),
            "max_ms": round(1000 * max(warm), 1), "cold_median_ms": ms(cold)}


def run_all(cfg: Config) -> dict:
    corpus = Path(__file__).resolve().parents[2] / "bench" / "corpus"
    mem = Memory()
    mem.ingest()
    res: dict[str, Any] = {"compress": bench_compress(corpus), "reads": bench_reads(), "memory": bench_memory(mem),
           "snapshot": bench_snapshot(cfg)}
    lines = ["keelyard bench (approx tokens = chars/4)"]
    for k, v in res.items():
        lines.append(f"\n[{k}]")
        lines += [f"  {kk}: {vv}" for kk, vv in v.items() if not isinstance(vv, list)]
        for r in v.get("files", []) if isinstance(v.get("files"), list) else []:
            lines.append(f"    {r['file']}: {r['raw']} -> {r['out']}")
    res["text"] = "\n".join(lines)
    return res
