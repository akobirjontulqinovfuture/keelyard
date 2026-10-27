"""Real tool output (tests/corpus/*.log, captured from the real tools) through every output filter:
an error or warning line must never be dropped, even when the output is padded with thousands of passes."""

import re
from pathlib import Path

import pytest

from keelyard.filters import filter_output
from keelyard.saver import compress_output

CORPUS = Path(__file__).parent / "corpus"
CMDS = {
    "pytest_mixed": "python -m pytest -q", "pytest_verbose": "python -m pytest", "python_traceback": "python tb.py",
    "ruff": "ruff check .", "go_test": "go test -v ./...", "cargo_test": "cargo test", "node_test": "node --test",
    "eslint": "eslint .", "npm_test": "npm test", "git_merge_conflict": "git merge b", "jest_fail": "npx jest",
    "mypy": "mypy src", "pyright": "pyright src",
}
# test counts and stack-trace frames: the facts an agent needs to judge a run
COUNTS = re.compile(r"(?i)(\b\d+ (passed|failed|total)\b|test result:|^# (pass|fail|tests) \d+|^(ok|FAIL)\s+\S+\s+[\d.]+s$)")
FRAME = re.compile(r'^\s*(at .+:\d+:\d+\)?$|File ".+", line \d+|\S+\.(py|go|rs|js|ts):\d+)')
SIGNAL = re.compile(r"(?i)\b(error|errors|warning|warn|fail|failed|failure|failures|panicked|exception|traceback|"
                    r"conflict|deprecated|assertionerror|not ok)\b|^E\s{2,}")
PAD = {"pytest": "tests/test_bulk.py::test_p{i} PASSED", "go": "--- PASS: TestBulk{i} (0.00s)",
       "cargo": "test bulk::t{i} ... ok", "node": "ok {i} - bulk {i}"}


def signals(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if SIGNAL.search(ln) and ln.strip()]


def _norm(text: str) -> str:
    # "===== ERRORS =====" may be shortened to "== ERRORS ==": same line, fewer tokens
    return re.sub(r"\s+", " ", re.sub(r"[=_]{2,}", "", text))


def kept(line: str, out: str) -> bool:
    return _norm(line[:120]).strip() in _norm(out)


def cases():
    for f in sorted(CORPUS.glob("*.log")):
        yield pytest.param(f.stem, f.read_text(encoding="utf-8"), id=f.stem)


@pytest.mark.parametrize("name, raw", list(cases()))
def test_no_signal_line_is_dropped(name, raw):
    assert name in CMDS, f"add the command for {name}"
    out = filter_output(CMDS[name], raw)
    missing = [s for s in signals(raw) if not kept(s, out)]
    assert not missing, f"{name}: dropped {missing}"


@pytest.mark.parametrize("name, raw", list(cases()))
def test_signal_lines_survive_thousands_of_passing_lines(name, raw):
    kind = next((k for k in PAD if name.startswith(k)), None)
    if kind is None:
        pytest.skip("no passing-line format for this tool")
    noisy = "\n".join(PAD[kind].format(i=i) for i in range(3000)) + "\n" + raw
    out = filter_output(CMDS[name], noisy)
    assert len(out) < len(noisy) / 5, "padding should be filtered away"
    missing = [s for s in signals(raw) if not kept(s, out)]
    assert not missing, f"{name}: dropped {missing}"


@pytest.mark.parametrize("name, raw", list(cases()))
def test_generic_compressor_keeps_signals_under_a_small_budget(name, raw):
    noisy = "\n".join(f"compiling module {i} ok" for i in range(3000)) + "\n" + raw
    out = compress_output(noisy, 3000)
    missing = [s for s in signals(raw) if not kept(s, out)]
    assert not missing, f"{name}: dropped {missing}"


@pytest.mark.parametrize("name, raw", list(cases()))
def test_counts_and_stack_frames_are_kept(name, raw):
    out = filter_output(CMDS[name], "\n".join(PAD.get(name.split("_")[0], "x {i} ok").format(i=i) for i in range(500)) + "\n" + raw)
    need = [ln.strip() for ln in raw.splitlines() if ln.strip() and (COUNTS.search(ln.strip()) or FRAME.search(ln))]
    missing = [ln for ln in need if not kept(ln, out)]
    assert not missing, f"{name}: dropped {missing}"


def test_run_footer_reports_exit_code_and_collapsed_lines(tmp_path):
    import sys

    from keelyard.filters import run_filtered

    script = tmp_path / "t.py"
    script.write_text("import sys\nfor i in range(400): print(f'tests/test_a.py::test_{i} PASSED')\n"
                      "print('E   assert 1 == 2')\nprint('=== 1 failed, 400 passed in 0.1s ===')\nsys.exit(1)\n", encoding="utf-8")
    code, out = run_filtered(f'"{sys.executable}" "{script}" -m pytest', tmp_path, tmp_path / "raw")
    assert code == 1
    assert "exit 1;" in out and "lines collapsed" in out and "1 failed, 400 passed" in out and "assert 1 == 2" in out


def _untable(v):
    if isinstance(v, dict) and set(v) == {"columns", "rows"}:
        return [dict(zip(v["columns"], r, strict=True)) for r in v["rows"]]
    if isinstance(v, dict):
        return {k: _untable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_untable(x) for x in v]
    return v


def test_json_output_is_compacted_losslessly():
    import json

    data = {"total": 40, "items": [{"name": f"repo{i}", "stars": i * 10, "license": {"key": "mit"}, "tags": ["a", "b"]}
                                   for i in range(40)], "mixed": [{"a": 1}, {"b": 2}, {"a": 3}]}
    raw = json.dumps(data, indent=2)
    out = filter_output("gh api repos", raw)
    assert len(out) < len(raw) * 0.6
    assert _untable(json.loads(out.split("\n", 1)[1])) == data  # every value survives
    assert filter_output("x", '{"a": 1}') == '{"a": 1}'  # tiny or already-minimal JSON is left alone
