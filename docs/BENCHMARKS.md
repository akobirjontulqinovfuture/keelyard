# Benchmarks

!!! tip "Rerun them yourself"
    `make bench` (offline, $0) and `make codex-e2e` reproduce these numbers and the Codex check. Commands and
    methodology: [BENCHMARKS.md in the repo](https://github.com/akobirjontulqinovfuture/keelyard/blob/main/BENCHMARKS.md#rerun-everything-in-one-command).

Machine: 8 vCPU Linux box, no GPU, Python 3.13. Measured on 2026-10-12. Token counts are `chars / 4`.

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

## Head-to-head with rtk (`bench/vs_rtk.py`)

```bash
gh release download -R rtk-ai/rtk -p 'rtk-x86_64-unknown-linux-musl.tar.gz' && tar xzf rtk-*.tar.gz
git clone --depth 1 https://github.com/django/django /tmp/djb
git clone --depth 80 https://github.com/psf/requests /tmp/req
python bench/vs_rtk.py --rtk ./rtk --django /tmp/djb --history /tmp/req --json bench/vs_rtk_results.json
python bench/plot_vs_rtk.py
```

The script builds scratch repos: a Django clone with 5 edited files, 3 untracked and 1 deleted; a pytest project with
4 failing tests out of 125; and a Rust crate with 2 failing tests out of 42. It runs each command once and feeds the
same raw output to keelyard, while rtk runs the command itself. `FORCE_COLOR` is removed and `NO_COLOR=1` is set
for all runs. "Facts" are extracted from the raw output with fixed rules (see `facts_*` in the script) and count as
kept when every part appears verbatim in the filtered text.

| command | facts | raw tok | keelyard tok | keelyard kept | rtk tok | rtk kept |
|---|---|---|---|---|---|---|
| git status | 9 | 161 | 61 | 100% | 58 | 100% |
| git log -n 40 (requests) | 40 | 6,807 | 950 | 100% | 2,378 | 100% |
| git diff | 91 | 1,653 | 1,064 | 100% | 1,557 | 100% |
| grep -rn (django) | 15 | 277 | 230 | 100% | 277 | 100% |
| find *.py (admin) | 30 | 299 | 136 | 100% | 113 | 100% |
| ruff check (django/utils) | 307 | 43,582 | 3,022 | 100% | 1,050 | 24% |
| pytest -v (4 failing of 125) | 7 | 3,289 | 312 | 100% | 195 | 71% |
| cargo test (2 failing of 42) | 6 | 454 | 126 | 100% | 119 | 100% |
| **total** | | 56,522 | 5,901 | | 5,747 | |

Caveats: one run per command, eight commands, fact rules written by us. rtk drops line numbers from ruff results and
some assertion lines from pytest, which is why its retention is lower on those two. rtk handles many more tools.

## Output compression (`keelyard bench`, section `compress`)

The corpus is in `bench/corpus/`. Each file is real output from a real command run on the box, with temporary paths
rewritten to `/tmp/demo`:

| Log | Command | Raw | Compressed |
|---|---|---|---|
| cargo-build.log | `cargo build` (serde, serde_json) | 198 | 198 |
| git-clone-progress.log | `git clone --progress psf/requests` | 2,101 | 220 |
| npm-install-verbose.log | `npm install express --loglevel=verbose` | 2,744 | 1,142 |
| pip-install-verbose.log | `pip install -v httpx rich` | 1,159 | 1,158 |
| pytest-verbose-failing.log | `pytest -v` with 60 passing and 3 failing tests | 1,814 | 661 |
| pytest-verbose-passing.log | `pytest -v -rA` on this repo | 884 | 884 |
| **total** | | **8,900** | **4,263 (−52.1%)** |

All 12 `FAILED`/`assert`/`KeyError` lines of the failing pytest log survive compression.
The corpus is small. Please contribute more real logs.

## Snapshots

`keelyard bench` snapshots into a temporary shadow repo (your timeline is untouched). *Warm*: one file edited, snapshot. *Cold*: the index is dropped first. Five runs each, Linux box, Oct 2026.

| Repo | Tracked files | First snapshot | Warm median | Cold median |
|---|---|---|---|---|
| keelyard | 112 | 174 ms | 8 ms | 24 ms |
| django (shallow clone) | 7,083 | 2.34 s | 88 ms | 427 ms |

Only files changed since the last snapshot are size-checked, so warm cost tracks the edit, not the repo.

## Arena, local run

`examples/arena/discount`, five hidden checks, qwen-coder 1.5B on Ollama: do nothing 0/5, one-shot qwen 0/5 (226 in / 63 out tokens), reference fix 5/5. No Claude Code, Codex or OpenCode runs yet.

## Feature matrix

`python bench/feature_matrix.py` runs every CLI command and flag, every hook and every MCP tool; results in [feature-matrix.md](feature-matrix.md).

## Swarm with a small local model

These are real calls to `qwen2.5-coder-1.5b-instruct` (Q4_K_M GGUF) served by Ollama on CPU, with concurrency 4.
There were 12 tasks against this repo: 8 Q&A or summaries, 2 patch drafts and 2 greps.

- Wall time: 4 min 15 s; helper tokens: 14,452 in, 1,409 out.
- Verified: 3/12. Both greps are local and need no model. One patch passed `git apply --check`; the other did not apply.
- Q&A: 0/8 answers contained a valid `path:line` citation, and the verifier marked all 8 UNVERIFIED.
  A second run with a format example in the prompt also verified 0/8. One answer copied the example's
  file name, so the example was removed again.

Conclusion: the verification layer does its job, but a 1.5B model is not a useful helper. Use a 7B+ local model or
a hosted cheap model. We have not yet measured how well larger models verify.

## Not yet measured

- `reads`: repeated identical reads in real Claude Code transcripts (harness exists, no transcripts on the build box).
- `memory`: recall@8 and snippet/transcript ratio on real sessions (harness exists, same reason).

## Swarm quality

`python bench/swarm_quality.py --runs 2`, local qwen-coder 1.5B on Ollama, 2026-10-12. Five one-function bug
fixes and five "which line does X" questions, in a repo where each file also carries 25 unrelated helpers.
*Single* is the old swarm: whole file, one sample, diff output, `git apply --check` only. *Pipeline* is the
new one: context pack, one-definition rewrite, tests on a copy, best-of-3, one retry with feedback, no
escalation (no strong model configured). Ground truth for fixes is that module's own tests passing with the
patch; for questions, citing the expected line.

| | single, run 1 / 2 | pipeline, run 1 / 2 |
|---|---|---|
| fixes actually correct | 0 / 0 of 5 | 4 / 4 of 5 |
| fixes marked verified but wrong | 3 / 2 | 0 / 0 |
| questions citing the right line | 1 / 1 of 5 | 2 / 3 of 5 |
| questions marked verified | 1 / 1 | 5 / 5 |
| model calls | 10 / 10 | 33 / 39 |
| input tokens | 12,435 / 12,435 | 5,034 / 6,309 |
| output tokens | 1,679 / 1,712 | 2,835 / 3,626 |

Checking patches against tests removed every false "verified" fix, and the rewrite format plus retries took a
1.5B model from 0 to 4 correct fixes out of 5. Context packs used about half the input tokens despite 3-4x
more calls. What it does not show: a "verified" answer to a question means its citations exist, not that it is
right (5 verified, 2-3 right). Ten tasks on one tiny model is a smoke test, not a benchmark; escalation and
7B+ models are not measured here.


### Cooperative swarm vs the best local setup

Same 10 tasks (5 bug fixes, 5 questions), qwen-coder-1.5b on Ollama, 2 runs each. `python bench/swarm_quality.py`.

| mode | correct of 20 (2 runs × 10) | tokens | tokens per correct | wrong patches marked verified |
|---|---|---|---|---|
| single | 2 | 27,394 | 13,697 | 5 |
| pipeline | 15 | 19,073 | 1,272 | 0 |
| best-local | 18 | 28,081 | 1,560 | 0 |
| coop-nocritic | 15 | 19,686 | 1,312 | 0 |
| coop | 15 | 44,149 | 2,943 | 0 |

- `best-local` is the same model with more samples and retries (best_of 5, retries 2). It stands in for "one
  strong agent" because no stronger model or API key was available. That comparison is still open.
- `coop` adds the critic. It doubled the tokens and did not improve quality, so `critic` is off by default.
- `coop-nocritic` (blackboard, voting, budget) costs about the same as the pipeline per correct answer.
  With one model the ladder had one step, so routing was not exercised here (it is covered by tests).

### Free OpenRouter helper (one run)

Same 10 tasks with `cohere/north-mini-code:free` as the helper, cost $0:

| mode | correct of 10 | tokens | tokens per correct |
|---|---|---|---|
| single | 6 | 13,699 | 2,283 |
| pipeline | 10 | 8,504 | 850 |
| coop-nocritic | 10 | 12,915 | 1,292 |

One run only: the free tier allows 50 requests a day and the limit was reached during the strong-model modes
(`nvidia/nemotron-3-ultra-550b-a55b:free`), so there is no strong-model comparison yet. `bench/swarm_openrouter_free.json`.

## keelyard's own overhead

`python bench/overhead.py` counts what keelyard adds to every session (characters / 4 of compact JSON or text):

| item | tokens |
|---|---|
| MCP tool schemas, 13 core tools (default) | 1,582 |
| MCP tool schemas, all 22 tools (`[mcp] tools = "all"`) | 2,401 |
| MCP server instructions | 77 |
| CLAUDE.md / AGENTS.md guide block | 144 |
| session brief, fresh project (cap 600) | 74 |
| **total per session, fresh project** | **1,877** |
| workflow guide, only when asked (plan / debug / review) | 240 / 181 / 182 |

Prompt recall adds at most 250 tokens per prompt and only when past sessions match; hints are capped at 12 per session.

Net: the 8 filtered commands above saved 50,621 tokens, so one session that runs them nets
**48,744** tokens. At the median saving per command, the fixed overhead is repaid after
4 filtered commands. A session that never runs a noisy command pays the 1,877 tokens and saves nothing;
these are per-command measurements, not totals from real sessions.
