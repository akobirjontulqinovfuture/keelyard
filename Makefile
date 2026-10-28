# Reviewer shortcuts. Every target runs offline and costs $0.
PY ?= python

.PHONY: bench bench-sessions codex-e2e test
bench:            ## offline benchmarks on this machine + the committed session table, recomputed from raw logs
	keelyard bench
	$(PY) bench/session/report.py bench/session/runs_v4.jsonl

bench-sessions:   ## rerun the real-session benchmark (needs opencode; free model by default; takes a while)
	$(PY) bench/session/run.py --setups baseline,lean --reps 3 --jobs 3 --out bench/session/runs_local.jsonl
	$(PY) bench/session/report.py bench/session/runs_local.jsonl

codex-e2e:        ## keelyard inside the real Codex CLI with a local scripted model (needs codex on PATH)
	$(PY) scripts/codex_e2e.py

test:
	ruff check src tests bench scripts && mypy && pytest -q
