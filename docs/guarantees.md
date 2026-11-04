# What keelyard guarantees, and what it doesn't

keelyard is a local tool that runs as you. It makes an agent's claims checkable and its file edits undoable.
It is not a sandbox. This page lists exactly what holds and where the limits are.

## Proof

| keelyard guarantees | it does not guarantee |
|---|---|
| "proven" means a passing test run was recorded **for the current file contents**. Any change to a snapshotted file after that run (even one made without a hook firing) reopens the proof. | That your tests are good. A proof is only as strong as the test suite. |
| Evidence is shown at three levels, never merged: **test-verified** (keelyard ran the test command itself), **command-succeeded** (the agent ran tests in its shell and the agent harness reported the result), **agent-claimed** (the agent wrote it, e.g. a checklist tick or `accept-tests`). | That agent-claimed evidence is true. It is shown so you can judge it. |
| Proof bundles are signed with a key kept outside the project (`<data home>/keelyard/proof.key`, mode 600). A bundle edited by hand, or with its signature removed, is never reported as proven again in that session. | Protection against a process that deliberately reads that key. Anything running as your user can. Signing stops casual forging, not a determined attacker. |
| Weakened tests keep the proof open: deleted test files, fewer test functions or assertions, new `skip`/`xfail`/`only` markers, changed or added test config, fewer tests in the run than before. Compared against the files as they were before the session's first edit. | Catching every weakening. Changing an expected value (`== 81` to `== 90`), or a test that checks the wrong thing, still looks fine. The counts are pattern-based per language. |
| If no agent's Stop hook is wired, or a wired agent has never called keelyard, status and doctor say so in yellow and never show a green "proven". | That an agent obeys the gate. Claude Code and Codex let the Stop hook block; OpenCode cannot veto "idle", so keelyard sends the missing items as a new prompt. After `max_blocks` reminders the gate lets the agent stop and records that. |
| Parallel sessions and hooks write proof and timeline state under file locks; a torn write cannot produce a half bundle (atomic replace). | Consistency across machines or network file systems with broken locking. |

## Rewind

| keelyard guarantees | it does not guarantee |
|---|---|
| Every rewind first snapshots the current state, so it can be undone with the step number it prints. | Undoing anything outside the project folder. |
| Files a snapshot cannot hold (gitignored, secrets, larger than `max_file_mb`) but that a rewind would overwrite or delete are copied to `.keelyard/backup/<time>/` first. | Undoing **databases, migrations that ran, deployed services, sent emails, pushed commits, or package installs**. A snapshot holds files only. Before a destructive command (`rm -rf`, `git reset --hard`, `DROP TABLE`, migrations) keelyard snapshots the files and warns; the database itself is not restored. |
| An interrupted rewind (crash, Ctrl-C) leaves a journal. keelyard refuses to rewind again until `keelyard rewind recover` puts back the state from just before it. | Recovery if `.keelyard/` itself is deleted. |
| Your own `.git` is never touched (separate shadow repository, own index). | |

## Secrets

- Never snapshotted by default: `.env`, `.env.*` (except `.env.example`/`.sample`/`.template`), `*.pem`, `*.key`,
  `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `id_rsa*`/`id_ed25519*`/..., `.npmrc`, `.pypirc`, `.netrc`,
  `.git-credentials`, `credentials.json`, `service-account*.json`, `*.tfstate`, `secrets/`. Change it with
  `[rewind].include_secrets = true` or add patterns with `[rewind].exclude`.
- Memory, proof bundles and reports pass text through a redactor (API keys, tokens, private keys, JWTs,
  `PASSWORD=...` style assignments). It is pattern-based: an unusual secret format can slip through.
- keelyard never sends anything anywhere. The only network calls are the ones you configure (swarm helper models).

## Tokens

- keelyard's own additions (brief, hints, footers) are budgeted. `keelyard run` returns the raw output when
  filtering would not save more than its own footer costs.
- Filters never drop an error or warning line: tested against real logs from pytest, cargo, go, node, eslint,
  ruff, npm and git (`tests/corpus/`), also with thousands of lines of padding.
- Six mechanisms, each with a safeguard (measured in [BENCHMARKS](BENCHMARKS.md), $0, no model):

| mechanism | what it saves | the safeguard | limit |
|---|---|---|---|
| find code (`find_symbol`, `outline`, `related`, `code find/refs/related`) | reading whole files to locate one function | ranked results never cut silently; `related` lists imports, importers and tests; `refs=true` lists call sites | regex-based, not a type checker: dynamic dispatch and re-exports can be missed |
| re-reads (`read_file`) | resending a file already in context | unchanged = short notice, changed = diff, `force=true` = full text; a notice is never sent when the file is smaller | relies on the agent still holding the earlier copy; reset on compaction |
| log compaction (`keelyard run`, `run_compact`) | passing lines and banners | errors, warnings, stack frames, test counts and the exit code are kept (corpus test); the footer says how many lines were collapsed and `keelyard recall <id>` returns the raw output | pattern-based per tool; unknown tools get the generic compressor |
| paged results (`find_symbol`, `memory_search`) | long result lists | best first, `remaining` and `next_offset` on every page; walking all pages returns everything | memory search counts at most 500 matches |
| work state (`work_state`, `keelyard state`) | re-deriving decisions after a compaction | every decision needs a ref (`path:line`, `cmd: ...`, `#proof item`), validated when added and re-checked on every render; stale refs are marked STALE | it records what the agent writes down; it does not infer decisions |
| tools per task (`[mcp].tools = "lazy"`) | tool schemas sent every request | `tool_search` finds every hidden tool, `tool_call` runs it; lazy falls back to `core` if it is not smaller | each on-demand use costs about 309 tokens; past about 4 distinct hidden tools a session is cheaper with `core`, so `core` is the default |

- Every preset adds a ~12-token terse-output rule to CLAUDE.md/AGENTS.md ("Be terse: no preamble or recap. Keep
  code exact."). Opt out: `keelyard init --no-terse` or `KEELYARD_TERSE=0`. On three local 0.5-1.8B models it used
  2.6% fewer total tokens than the old "Be terse" line and kept more required facts (155/192 vs 142): not a
  guarantee for other models or for every model (`BENCHMARKS.md`).
- JSON output is compacted only losslessly (minified, same-key lists as columns/rows) and only when smaller.
- A failing test run identical to an earlier failure on different code adds one ~40-token hint; nothing otherwise.
- The lean preset saves tokens partly by **turning tools off** (9 OpenCode built-ins, keelyard's MCP list).
- Benchmarks used a free model, so there is no cost claim. See [BENCHMARKS](BENCHMARKS.md) for the split
  between cached and uncached input.

## Per agent

| capability | Claude Code | Codex | OpenCode |
|---|---|---|---|
| proof gate at the end of a turn | Stop hook blocks | Stop hook blocks (approve hooks once with `/hooks`) | cannot block "idle": missing items sent as a new prompt |
| evidence from shell test runs | command-succeeded | command-succeeded | command-succeeded |
| `proof test` / `proof_run_tests` | test-verified | test-verified | test-verified (MCP off in lean: use the CLI) |
| snapshot after each edit | yes | yes | yes |
| shell output filtered in place | yes (`updatedToolOutput`) | yes (`continue: false` / `stopReason`; Codex 0.165.0 rejects `updatedToolOutput`) | yes (plugin rewrites the output) |
| `[mcp].tools = "lazy"` (tool_search/tool_call) | yes | yes | only with MCP on |
| work state in the brief after compaction | yes (SessionStart `compact`) | at session start; re-sent after compaction not confirmed | yes (plugin compaction hook) |
| MCP tools | `.mcp.json` | `~/.codex/config.toml` or `--codex-project-mcp` | off by default (`KEELYARD_OPENCODE_MCP=1`) |
| checked how | scripted hook payloads in CI (`keelyard selftest`) | Codex CLI 0.165.0 plus scripted payloads | real sessions, OpenCode 2.0.22 on Windows 11 |

Real Claude Code and Codex sessions have not been benchmarked yet.
