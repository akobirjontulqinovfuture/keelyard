# Benchmarks

## Rerun everything in one command

```bash
git clone https://github.com/akobirjontulqinovfuture/keelyard && cd keelyard
pip install -e ".[dev]"
make bench        # offline: output compression + snapshot speed on your machine, and the session table below
                  # recomputed from the committed raw logs (bench/session/runs_v4.jsonl)
make codex-e2e    # keelyard inside the real Codex CLI, no API key (npm i -g @openai/codex first)
make bench-sessions  # rerun the real OpenCode sessions yourself (free model, slow)
```

Without `make`: `keelyard bench`, `python bench/session/report.py bench/session/runs_v4.jsonl`,
`python scripts/codex_e2e.py`.

**Methodology, in short.** `keelyard bench` runs on a fixed corpus of real logs shipped in the package, so the
`compress` numbers are the same on every machine; tokens are approximated as characters / 4. Snapshot timings and
the `reads`/`memory` sections depend on your machine and your own agent transcripts, so they will differ. The session
table comes from real agent runs on one free model with 3–8 repetitions per task: it is indicative, not general.
Every raw run is in `bench/session/*.jsonl`, and `report.py` exits 1 if a task misses the target, so the table
cannot be edited by hand without the check failing.

**Measured on Oct 28, 2026** (Linux box, keelyard 1.0.3, `keelyard bench`): output compression 8,887 → 4,253
approx tokens on the shipped corpus (−52.1%). Biggest wins: `git clone` progress 2,101 → 220, verbose failing
pytest 1,811 → 658, `npm install --verbose` 2,740 → 1,142. Logs with nothing to drop (a short `cargo build`, a
passing pytest run) stay the same size, as they should. Snapshot of a 253-file repo: 13 ms median warm, 34 ms
median cold. `make bench` reproduced the session table below byte for byte from the raw logs.

What is **not** measured yet: Claude Code and Codex sessions with a real model (the Codex check above uses a
scripted model, so it proves the wiring, not token savings), and cost in dollars (the model was free).

## Whole-session tokens, keelyard lean vs none (Oct 17, 2026)

Real `opencode run` sessions (OpenCode 1.19.0, free `opencode/big-pickle`, $0) on six task types, each in a fresh
repo; hidden checks decide success. Total = input + cache read + cache write + output + reasoning, keelyard's brief
and hints included; every attempt counts. Target: ≥70% saving per task with success not lower than baseline
(`python bench/session/report.py bench/session/runs_v4.jsonl` exits 1 otherwise). v4 = commit 2b5e877.

| task | runs (base/lean) | baseline tokens (mean) | lean tokens (mean) | saving | correct base | correct lean | gate |
|---|---|---:|---:|---:|---:|---:|---|
| edit-heavy | 8/8 | 47,958 | 10,626 | 78% | 88% | 100% | pass |
| log-heavy | 3/3 | 57,614 | 10,373 | 82% | 100% | 100% | pass |
| qa-large | 3/3 | 20,863 | 3,053 | 85% | 100% | 100% | pass |
| read-heavy-large | 3/3 | 48,533 | 10,595 | 78% | 100% | 100% | pass |
| small-feature | 3/3 | 51,916 | 11,965 | 77% | 100% | 100% | pass |
| tiny-fix | 3/3 | 40,642 | 6,134 | 85% | 100% | 100% | pass |
| **all (sum of means)** | | 267,525 | 52,745 | 80% | | | |

edit-heavy fix (v3 → v4): in v3 one of 8 lean runs made 17 per-file read+edit calls (71k tokens) and missed a use
(62% saving, 88% correct). The lean brief now carries a rename hint for "rename X to Y" tasks: every tracked file
holding X and one `perl -pi` command over them; the lean prompt asks for one bulk command. All 8 v4 lean runs used it
(2–4 calls, 7–16k tokens). v4 re-ran the other tasks with 3 reps each to check for regressions (v3: 5 reps, see
`runs_v3.jsonl`). One model, one agent, 3–8 reps per task: indicative, not general. Claude Code and Codex are not
measured here.

## Terse-output rule, measured on local models (Oct 17, 2026)

`python bench/terse.py --models qwen2.5-0.5b,llama3.2-1b,qwen-coder-1.5b --seeds 1,2` (Ollama, Q4, temperature 0.3):
32 coding prompts x 2 seeds = 64 answers per model and rule. Tokens are Ollama's own counts, input
(`prompt_eval_count`, so the rule's cost is included) + output (`eval_count`). "Facts" = the answer contains
what the prompt needs (identifier, fix, error name). Raw numbers: `bench/terse.json`. $0, no paid calls.

| rule | total tokens (3 models) | facts (of 192) |
|---|---:|---:|
| old "Be terse: no preamble, no restating the question, no summary of what you are about to do." | 24,053 | 142 |
| previous rule ("Reply with results only: ... exact and complete.", ~33 tokens) | 31,870 | 150 |
| **shipped: "Be terse: no preamble or recap. Keep code exact."** (~12 tokens) | **23,428 (−2.6% vs old)** | **155** |
| "Be terse. Keep code and facts." | 27,310 | 158 |
| "Be terse: answer first, no preamble or recap; keep code and facts complete." | 28,827 | 155 |

Per model (total / facts): qwen2.5-0.5b old 9,444/44, shipped 8,271/45; llama3.2-1b old 6,884/45,
previous 10,799/52, shipped 7,022/49; qwen-coder-1.5b old 7,725/53, previous 12,342/51, shipped 8,135/61.

- The shipped rule beats the old line on total tokens and the previous rule on facts summed over three models,
  but not on every model: on llama3.2-1b it uses 2% more tokens than the old line and keeps fewer facts than the
  previous rule. The token margin over the old line is small. Not measured with Claude or GPT models.
- Lossless JSON compaction: pretty `gh search repos --json` output 2,121 → 1,236 tokens (−42%), decoded back
  exactly (`bench/mechanisms.py`). An already-minified GitHub issue list shrank only 3%.
- The repeated-failure hint (about 40 tokens, only when it fires) was not measured for savings.

## Token-saving mechanisms, measured locally (Oct 16, 2026)

`python bench/mechanisms.py` (no model, no network, $0). Tokens are `chars / 4`. Fixtures: this repo,
the installed `httpx` package source, and the two-file `examples/arena/discount/repo`. The keelyard row shifts a little with every commit (it measures itself). Each row compares
keelyard with what an agent does without it, and checks the agent still gets what it needs.
Raw numbers: [`bench/mechanisms.json`](https://github.com/akobirjontulqinovfuture/keelyard/blob/main/bench/mechanisms.json).

| mechanism | baseline | keelyard | keelyard repo | httpx | discount (2 files) | correctness check |
|---|---|---|---|---|---|---|
| 1 find code | grep the name, read the whole defining file | ranked `find_symbol` page + outline + the definition's lines + `related` | 16,969 → 3,691 (−78%) | 54,731 → 16,717 (−69%) | 107 → 108 (**+1%**) | the definition is in the slice read |
| 2 re-reads | read, re-read, read after a 1-line edit (5 largest files) | full, "unchanged" notice, diff | 127,392 → 43,289 (−66%) | 146,937 → 49,689 (−66%) | 456 → 387 (−15%) | diff applied to the first read = the edited file; `force=true` returns all of it |
| 3 logs, real `pytest -v` | raw output | `filter_output` | 1,233 → 9 (all passed) | – | 401 → 149 (−63%, 2 failures) | every error/failure/count line kept; failing test ids kept |
| 3 logs, corpus (10 tools, pytest padded with 300 passes) | raw | filtered | 7,965 → 1,502 (−81%) | | | same |
| 4 paging | every match in one response | first page of 20 + `remaining`/`next_offset` | 8,849 → 416 (−95%) | 7,007 → 324 (−95%) | 99 → 99 (0%) | walking every page returns every result in order |
| 5 work state | re-read the 3 changed files after a compaction (modeled) | `work_state` render | 26,574 → 241 | 33,055 → 182 | 177 → 111 | every ref resolves to a real file line or command |
| 6 tool schemas per request | `core` (14 tools) | `lazy` (6 tools) | 2,319 → 927 (−60%) | | | every hidden core tool is found by `tool_search` |

What this does not show:
- Row 1 on a tiny repo is **one token worse**: when the file is smaller than an outline, just read it.
- Row 4 saves only when the answer is on the first page; reading every page costs slightly more than one dump.
- Row 5's baseline is a model of what an agent re-reads, not a measured session.
- Row 6: each on-demand tool use (search + call) adds about 309 tokens that stay in context, so `lazy` is
  smaller only while a session uses fewer than 4 distinct hidden tools. The proof workflow alone uses about 4,
  which is why `core` stays the default and `lazy` is opt-in.
- None of this was measured in a live agent session.

## Real agent run: OpenCode 2.0.22 on Windows 11 (Oct 15, 2026)

One run each, same task and same free model (`opencode/big-pickle`, cost reported by OpenCode: $0).
Task: fix a SAVE10 coupon, reject unknown coupons, floor `inventory.remove` at zero (3 failing of 66 tests).
Raw data: [`bench/real/`](bench/real/); numbers produced by `bench/real/summarize.py` from `opencode session export`.

| | baseline (no keelyard) | with keelyard |
|---|---|---|
| tests after run | 66/66 pass | 66/66 pass |
| model calls | 5 | 6 |
| input tokens, uncached | 16,436 | 13,654 |
| input tokens, cache read | 28,864 | 68,544 |
| input tokens, total | 45,300 | 82,198 |
| output + reasoning | 897 | 1,568 |
| wall time | 65 s | 122 s |
| proof | none | proven, 4 requirements with evidence |

Honest reading: on this small task keelyard **cost more tokens** (+81% total input), not fewer.
The main causes seen in the export: OpenCode 2 "code mode" injects an ~8.9k-char catalog for keelyard's
13 MCP tools, and the agent spent one call (19.7k chars) searching that catalog. Test output itself was
smaller with keelyard (`proof_run_tests`: 2,967 chars vs 7,008 + 5,789 raw `pytest -v` in the baseline),
but the shell-output filter did not fire because in code mode the agent never called the shell tool directly.
The 56k→6k savings measured earlier apply to noisy command output, which this task did not produce.
Rewind was verified on the same project: `rewind to 1` → 3 failed; `rewind to 2` → 66 passed
([screenshot](docs/img/real/rewind.svg)). Status/proof: [screenshot](docs/img/real/status.svg).

### After the lean-mode fix (commit f93dbaa), same PC, same free model, n=1 per row

keelyard no longer puts an MCP tool catalog into OpenCode's context, counts the agent's own shell test runs as
proof, and filters shell output. Data: `bench/real/opencode_bigpickle_lean.json`, `opencode_bigpickle_noisy.json`.

| task | run | tests after | model calls | input total (incl. cache) | input uncached | tool output chars |
|---|---|---|---|---|---|---|
| shop (66 tests) | baseline | 66/66 | 5 | 44,075 | 6,571 | 14,502 |
| shop (66 tests) | keelyard before fix | 66/66 | 6 | 82,198 | 13,654 | 26,746 |
| shop (66 tests) | **keelyard lean** | 66/66, proven | 5 | **37,493 (−15%)** | 4,021 | 2,806 (−81%) |
| noisy shop (1,566 tests, `pytest -v`) | baseline | 1566/1566 | 6 | 87,168 | 18,496 | 54,407 |
| noisy shop (1,566 tests, `pytest -v`) | **keelyard lean** | 1566/1566, proven | 5 | **37,257 (2.3× fewer)** | 7,049 (2.6× fewer) | 2,854 (19× fewer) |

What limits the ratio: OpenCode's own system prompt and tool definitions are resent on every model call
(about 6k tokens per call here), so five calls cost ~30k tokens before any project content. keelyard cuts
what it controls (tool output 19×, e.g. `pytest -v` 12,849 → 279 tokens) but cannot reach 10× on whole-session
tokens for short tasks. The gap grows with longer sessions and noisier output.

## Presets, measured on OpenCode 2

Windows 11, OpenCode 2.0.22, free `opencode/big-pickle` ($0), same task text, all runs ended with every test passing
and (keelyard runs) proof "proven". Session totals from `opencode session export`; raw summaries in
`bench/real/r6/summary.json` and `bench/real/r7/summary.json`. One run per row, two baseline runs per task.

| task | run | model calls | input incl. cache | uncached | output+reasoning | tool output chars |
|---|---|---|---|---|---|---|
| small (66 tests) | baseline, run A | 5 | 46,916 | 11,076 | 1,217 | 14,389 |
| small (66 tests) | baseline, run B | 5 | 43,849 | 6,921 | 815 | 14,512 |
| small (66 tests) | keelyard balanced | 7 | 51,652 | 9,801 | 1,162 | 2,502 |
| small (66 tests) | keelyard lean (7 tools off) | 4 | 11,851 | 3,723 | 493 | 2,654 |
| small (66 tests) | **keelyard lean (final, 9 tools off)** | 5 | **12,132** | 2,660 | 494 | 2,654 |
| noisy (1,566 tests, `pytest -v`) | baseline, run A | 8 | 101,528 | 33,122 | 1,270 | 54,887 |
| noisy (1,566 tests, `pytest -v`) | baseline, run B | 5 | 93,397 | 18,581 | 887 | 54,795 |
| noisy (1,566 tests, `pytest -v`) | keelyard balanced | 4 | 31,211 | 8,171 | 948 | 3,074 |
| noisy (1,566 tests, `pytest -v`) | keelyard lean (7 tools off) | 6 | 16,769 | 4,337 | 864 | 2,854 |
| noisy (1,566 tests, `pytest -v`) | **keelyard lean (final, 9 tools off)** | 4 | **9,995** | 1,867 | 465 | 2,570 |

Ratios against the mean of the two baselines: noisy task **9.7× fewer** input tokens (97,463 → 9,995),
small task **3.7× fewer** (45,383 → 12,132). Balanced mode on the small task was *above* baseline in this run
(7 model calls); the session-to-session spread of the same setup is large, so treat single runs as indicative.
Where the savings come from: the lean OpenCode agent's first request is ~1.5k tokens instead of ~5.5k
(prompt and tool list), and `pytest -v` output reaches the model as ~300 tokens instead of ~13k.

### Lean preset v2, repeated runs (Oct 15, 2026)

`keelyard init --preset lean` now also puts small project files and the pre-edit test result into the brief and
reruns the tests after each edit (result appended to the edit's output), so the agent needs no read or "before"
test calls. Installed through the real `keelyard init --agent opencode --preset lean` on the PC. Same task, free
`opencode/big-pickle`, $0. Raw data: `bench/real/r6`–`r9/summary.json`.

| task | setup | runs | input tokens incl. cache, per run | mean | model calls |
|---|---|---|---|---|---|
| small (66 tests) | baseline | 5 | 46,916 · 43,849 · 43,946 · 43,964 · 68,450 | 49,425 | 5–9 |
| small (66 tests) | **lean v2** | 3 | 7,884 · 8,950 · 7,973 | **8,269** | 3 |
| noisy (1,566 tests) | baseline | 5 | 101,528 · 93,397 · 139,837 · 94,923 · 129,229 | 111,783 | 5–11 |
| noisy (1,566 tests) | **lean v2** | 3 | 8,283 · 7,079 · 8,208 | **7,857** | 3 |

Mean ratio: **noisy 14.2× fewer, small 6.0× fewer** input tokens (cached + uncached together). Every counted run ended with all tests
passing; every lean run has proof "proven". Two lean runs from an earlier 12-way parallel batch (r8) are not
counted: OpenCode crashed at startup ("Standalone server exited before reporting readiness") before the agent
did anything.

Split by cache (same runs, means; from the same `summary.json` files):

| task | | baseline mean | lean v2 mean | ratio |
|---|---|---|---|---|
| small | input, uncached | 11,189 | 2,509 | 4.5× |
| small | input, cache read | 38,236 | 5,760 | 6.6× |
| noisy | input, uncached | 28,354 | 2,983 | 9.5× |
| noisy | input, cache read | 83,428 | 4,874 | 17.1× |

Read these numbers with three limits in mind:
- **No cost claim.** The model was free (`opencode/big-pickle`, $0 reported). On a paid model, cache reads are
  billed far below uncached input, so the saving in money is closer to the uncached ratio than to the total.
- **Lean turns tools off.** The lean OpenCode build agent disables 9 built-in tools and keelyard's MCP tool list.
  That is a large part of the saving, and it means the agent can do less (see docs/guide/presets.md).
- **Small n, one task family, one agent.** 5 baseline and 3 lean runs per task, OpenCode only. Claude Code and
  Codex were not measured this way.

The small task does not reach 10×: three model calls (edit, the test run the task text asks for,
final reply) at ~2.2–3k tokens each are the floor we could reach; the remaining per-call cost is OpenCode's
tool definitions plus the brief.
