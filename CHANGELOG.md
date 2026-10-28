# Changelog

## 1.0.1 (Oct 28, 2026)

- Install docs (README, AGENTS-install.md, llms.txt, docs) now use `pipx install keelyard` / `pip install keelyard` from PyPI
- README renders on PyPI: absolute links and image URL; Python versions badge from PyPI
- PyPI metadata: clearer description, Python 3.11 to 3.13 classifiers, more keywords, Documentation, Source, Issues and Changelog links
- MCP handshake reports keelyard's version (was empty)
- `keelyard --json <command>` works the same as `keelyard <command> --json`
- `keelyard proof test` defaults to the `after` phase
- `keelyard --help` lists `state`; `doctor` says "1 step", not "1 steps"

## 1.0.0 (Oct 28, 2026, first PyPI release)

First stable release.
- Published to PyPI as `keelyard` (`pip install keelyard`) by `.github/workflows/publish.yml`, using PyPI
  Trusted Publishing (no API token stored in the repo)
- Timeline browser: `g` / `G` jump to the first / last step (#9)
- New `mypy` filter: every diagnostic line is kept whole, so the trailing `[error-code]` is no longer clipped;
  real mypy and pyright output added to the filter corpus with a test (#1)
- README: PyPI install, "without vs with keelyard" table, per-agent wiring table; ROADMAP.md
- Version 1.0.0; no longer marked alpha (README badge, PyPI classifier `Production/Stable`)
- Install docs rewritten (README, `AGENTS-install.md`, `llms.txt`): human instructions first, then a labeled note
  for AI coding agents to install only when the user explicitly asked, show the commands and get confirmation.
  Removed the wording that told agents to run the install without the user typing it
- README: "What keelyard changes on your machine" + uninstall steps; SECURITY.md: reporting process and trust
  properties; `requirements-lock.txt` (pinned, pip-audit clean)
- Benchmark numbers unchanged and still small-sample (5 baseline vs 3 lean runs); see BENCHMARKS.md

## 0.1.0 (pre-release)

### Lean defaults made permanent (Oct 17, 2026)
- Every install path ends on the lean defaults the session bench measured (~80% saving): `init`, `install <agent>`
  (was balanced; now `--preset`, default lean), `init --global` (now adds OpenCode's lean build agent and Claude
  Code's lean env at user level, taken back by `uninstall --global`), global first-session setup (lean config)
- Upgrades: on SessionStart, a lean (or pre-preset) project whose config still has the old default lines gets the
  lean values; values set by hand are kept
- `keelyard doctor` warns when lean settings are overridden (project config or user-level agent config)
- `tests/test_lean_defaults.py`: fails on drift (prompt length, denied tools, terse rule, brief size, filter on,
  lean config, install/global/upgrade paths) - this is the per-PR static token budget check, run in CI
- Lean brief: rename hint (every file + one `perl -pi` command); edit-heavy now 78% saving, 100% correct
- TODO: scheduled weekly/manual session-bench workflow (2 reps/task, free model, fail <70% or correctness <
  baseline); per-session token recording (OpenCode db, Claude Code/Codex logs) with `usage`/`doctor` warning under
  target; Codex has no lean agent-config equivalent yet; docs page for these guarantees

### Ideas from popular agent tools, rebuilt from scratch (Oct 16, 2026)
- Terse-output rule in every preset (CLAUDE.md, AGENTS.md); `--no-terse` / `KEELYARD_TERSE=0` to opt out;
  `bench/terse.py` measures it on a local model. Rule shortened to ~12 tokens after a 3-model, 32-prompt x 2-seed
  comparison (fewer total tokens than the old line, more facts than the previous rule)
- Lossless JSON compaction in the output filters
- One hint when the same test failure returns after a code change (hooks, auto-test, `proof_run_tests`)
- Test: the session brief is byte-stable (prompt cache)
- README credits inspirations; survey of 57 repos in `docs/research/ideas.md`

### Token-saving mechanisms, each with a safeguard (Oct 16, 2026)
- Find code: ranked `find_symbol` (exact names and source first), `refs=true` call sites, `outline(related=true)`
  and `keelyard code related FILE` (imports, importers, tests); `keelyard code find --refs --offset --limit`
- Paged results: `find_symbol` and `memory_search` return `total`, `remaining` and `next_offset`; nothing is cut silently
- Re-reads: an "unchanged" notice is never sent when the file itself is smaller; diff exactness and `force=true` tested
- Log compaction: the `keelyard run` footer shows the exit code and how many lines were collapsed; `go test -v`
  failure messages (printed before `--- FAIL`) are now kept; the corpus test checks counts and stack frames
- Work state: `work_state` MCP tool and `keelyard state` (decisions, open questions, changed files, requirements);
  every decision needs a validated ref and stale refs are flagged; included in the brief after compaction
- `[mcp].tools = "lazy"`: 6 schemas up front plus `tool_search`/`tool_call`; falls back to `core` if not smaller
- `bench/mechanisms.py` measures all six on three fixture repos (see BENCHMARKS); `python -m keelyard` works

### Trust: proof and rewind (Oct 16, 2026)
- Proof is bound to a content hash of the worktree: any edit after the last passing test run reopens it,
  even one made without a hook firing
- Evidence levels shown separately: test-verified / command-succeeded / agent-claimed (`status`, `proof`, `--json`)
- Proof bundles are signed (HMAC, key in the user data dir, mode 600); a hand-edited bundle is never proven.
  Bundles written by older versions have no signature and count as edited: run `keelyard proof reset`
- Test weakening keeps the proof open: deleted test files, fewer tests or assertions, new skip/xfail/only,
  test config changed or added, fewer tests run. `keelyard proof accept-tests "<why>"` acknowledges a real
  refactor, recorded as agent-claimed and bound to those exact files
- The gate is never shown green when no Stop hook is wired or no agent ever called keelyard (`unhooked`/`unverified`);
  doctor shows it too
- Proof writes are locked (parallel hooks and sessions); tested with 12 processes
- Rewind copies files snapshots cannot hold (ignored, secret, too big) aside before overwriting or deleting them;
  an interrupted rewind is detected and `keelyard rewind recover` restores the state from just before it
- Secrets (`.env`, keys, credentials files, ...) are excluded from snapshots by default (`[rewind].include_secrets`,
  `[rewind].exclude`); a file that becomes excluded is dropped from the timeline index
- Filter corpus of real logs; pytest keeps the warnings summary, cargo keeps warning blocks
- Dependencies capped at the next major version; new CI job: clean venv + clean HOME install, init, doctor,
  selftest, uninstall leaves the project unchanged, on Linux, macOS and Windows
- Docs: "What keelyard guarantees and what it doesn't" with a per-agent capability table; shorter README;
  BENCHMARKS splits cached and uncached input and states the free-model and tools-off limits

### Audit fixes, round 2 (Oct 16, 2026)
- Codex: opt-in `--codex-project-mcp` on `init` and `install codex` writes the MCP server to `.codex/config.toml`
- `--json` works before or after the action in every group command (`keelyard proof --json status`)
- No trailing spaces in human output; error hints are not hard-wrapped
- `rewind` and the timeline browser name your files and count agent setup files (`a.py (+5 agent setup)`)
- memory and usage read OpenCode's `opencode.db` (OpenCode 1.x+/2.x keep sessions there, not in `storage/`);
  read-only, only the session/message/part tables; messages left over from the JSON-to-SQLite migration count once
- swarm: `swarm plan --json > tasks.json` feeds `swarm run tasks.json` (it failed before); an error file from
  `plan` is refused with its hint; a missing API key is reported before any call; HTTP 401/402/404/429 and
  unreachable servers get one-line explanations with a free-model hint; exit 3 when every call failed;
  `--model` is honored in cooperative mode (it was ignored)
- arena: per-contender summary and progress in the terminal; `files_changed` no longer counts the caches
  the check writes; no matplotlib font warnings
- timeline browser: tests drive it headless; a cut diff says so and how to see all of it

### Audit fixes (Oct 16, 2026)
- `keelyard uninstall <agent>` now restores config files byte for byte (formatting included), deletes the `{}` files
  and empty folders keelyard created, and leaves files alone if you edited them after install
- `keelyard run` never makes output bigger: when filtering saves less than its own footer, the raw output is returned
- pytest output drops frames from site-packages and the standard library and shortens `=====` banners
- A failing test run is the only gap the proof gate reports (no extra "add a checklist" line while tests are red)
- Bare `keelyard proof` shows the status (with `--json` too); usage errors name the real command (`keelyard code`)
- `keelyard doctor` warns when Codex hooks are wired but `~/.codex/config.toml` has no keelyard MCP server
- Windows: every file and stdin is read and written as UTF-8 (non-Latin text and emoji no longer depend on cp1252)

### OpenCode 2
- The OpenCode plugin supports OpenCode 2.x (`setup(ctx)`: context, compaction, prompt, tool and event hooks, MCP via
  `ctx.mcp.transform`) and 1.x (`server()`) from one file, now installed to `.opencode/plugins/keelyard.js`
- Fix: shell output from OpenCode (`bash`) and Codex (`shell`) was never filtered; only Claude's `Bash` was

### Cooperative swarm
- `keelyard.coop`: planner -> workers -> verifier with a shared blackboard, a optional peer critic, voting, cheapest-capable
  model routing (`[swarm].ladder`), and hard token budgets (`budget_tokens`, `task_budget_tokens`) enforced by
  reserving each call's worst case before it is sent. On by default (`cooperative = true`, `--basic` to opt out)
- Tested with 250 tasks and more than 200 calls in flight; token totals match exactly

### Smaller footprint, workflow pack
- MCP server exposes 13 core tools by default (`[mcp] tools = "core"`, or `"all"`, or a list); schemas
  drop pydantic titles. Measured: 1,582 vs 2,401 tokens per session (`bench/overhead.py`)
- Workflow pack: `workflow("plan"|"debug"|"review")` MCP tool, `/keelyard:plan|debug|review` for Claude Code,
  `keelyard-*` commands for OpenCode; installed by `init`, loaded only when asked for
- Compact guide block and brief rules; "be terse" rule
- Destructive-command check ignores text inside quotes (unless run via `bash -c`, `eval`, `ssh`) and only
  flags SQL when a SQL client is involved
- Snapshots skip keelyard's own wiring files and no longer force-add ignored files in untracked folders
- Screens and hero GIF show `~/` paths; release workflow with PyPI trusted publishing; gitleaks allowlist for test fixtures

### Always on and stronger helpers
- Brief at session start and after compaction, past-session recall per prompt, outline/diff on repeat reads
- Snapshot before destructive commands; rate-limited hints (no tests nearby, session budget); `[brain]` config
- Swarm: `swarm_plan`, context packs, JSON answers, one-definition rewrites, patches tested in a copy,
  best-of-N, retry with feedback, escalation, `subagent_tasks` for the host agent's subagents
- Windows CI; fixes for Windows paths, cp1252 output, CRLF diffs, racing state directory

### CLI overhaul (breaking renames)
- `x` → `run`, `compress` → `filter`, `timeline` → `rewind list -i`, `mem` → `memory`, `sym`/`map`/`outline` → `code find|map|outline`, `proof req` → `proof add`
- New `init` (detects Claude Code, Codex, OpenCode), `doctor`, `status`, `selftest`
- `--json` on every command, stable exit codes (0 ok, 1 error, 2 usage, 3 check failed), errors with hints
- Agent environment: 20 structured MCP tools, CLAUDE.md/AGENTS.md guide block, Claude Code skill and slash commands
- New design: Geist-based proof and arena reports, textual timeline, mkdocs-material docs site
- 35 bugs fixed from adversarial QA (see tests/test_adversarial.py)

### Initial

- Proof gate (Stop hook), HTML proof report
- Rewind: shadow-git snapshots, rewind/fork, curses timeline
- Helper swarm with citation and patch verification
- Saver: read dedupe, command-aware output filters (git, pytest, cargo/go/jest, grep, find, ruff/eslint/mypy),
  Claude Code PostToolUse output replacement, `recall`
- Structure-first reading: `outline`, `project_map`, `find_symbol`
- Unified memory (SQLite FTS5) and usage report across Claude Code, Codex, OpenCode
- Arena: one task across agents/models, tokens/cost/pass rate, HTML (+PNG) report
- Benchmarks: compression corpus, snapshot timing, head-to-head vs rtk with fact retention
