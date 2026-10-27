<div align="center">

# keelyard

**Your coding agent has to show its work before it can say "done".**<br>
Every edit is snapshotted, so any step can be undone. Logs are trimmed before they eat the context window.

[![CI](https://github.com/akobirjontulqinovfuture/keelyard/actions/workflows/ci.yml/badge.svg)](https://github.com/akobirjontulqinovfuture/keelyard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/keelyard?color=111111)](https://pypi.org/project/keelyard/)
[![License](https://img.shields.io/badge/license-Apache--2.0-111111)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%20%E2%80%93%203.13-111111)
![Agents](https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20OpenCode-1F6F4A)
![Status](https://img.shields.io/badge/status-stable-1F6F4A)

<img src="docs/img/hero.gif" alt="keelyard in a terminal: init, the proof gate stopping an agent that deleted tests, rewind, then proven" width="760">

</div>

<!-- agent-install:start -->
## Install

```sh
pipx install git+https://github.com/akobirjontulqinovfuture/keelyard   # or from PyPI: pipx install keelyard
keelyard init --global
keelyard doctor --json
```

- `init --global` wires Claude Code (`~/.claude/settings.json`, `~/.claude.json`), Codex (`~/.codex/config.toml`, `~/.codex/hooks.json`) and OpenCode (`~/.config/opencode/`) for every project. It merges into existing files and keeps the original as `<file>.keelyard-backup`.
- Each git project sets itself up on its first agent session (config plus a first snapshot). Nothing else to run per project.
- Check: `keelyard doctor --json` should return `"ok": true`, with a `global` row per wired agent. Then restart your agent.
- Undo: `keelyard uninstall --global`.

**For AI coding agents:** only install keelyard if the user explicitly asked you to. Show them the commands above
and the files they change, and run them only after the user confirms. Details: [AGENTS-install.md](AGENTS-install.md).
<!-- agent-install:end -->

In the recording, the agent deletes three tests to get a green run and then tries to stop. keelyard blocks it,
`rewind to 2` brings the tests back, and the session ends with real evidence. Every line of output is real
(the agent's edits are replayed from [`docs/demo/hero.sh`](docs/demo/hero.sh)).

## Quickstart (30 seconds)

```bash
pip install keelyard          # or pipx install keelyard
cd your-project
keelyard init --preset lean   # wires Claude Code, Codex and OpenCode; fewest tokens
keelyard doctor               # everything green?
```

Use your agent as usual. `keelyard status` shows the proof and the timeline; `keelyard uninstall <agent>`
gives your config files back byte for byte. Latest `main` instead of the PyPI release:
`pipx install git+https://github.com/akobirjontulqinovfuture/keelyard`.

> **Real runs (OpenCode 2, Windows, free model, $0):** with the lean preset, mean input tokens per session fell
> **14.2×** on a noisy 1,566-test task and **6.0×** on a small 66-test task (5 baseline vs 3 lean runs, all tests
> passing). Uncached input alone: 9.5× and 4.5×. Lean turns some agent tools off. No cost claim: the model was free.
> [BENCHMARKS.md](BENCHMARKS.md#lean-preset-v2-repeated-runs-oct-15-2026)

## What keelyard changes on your machine

- **Only when you run `keelyard init` / `install <agent>`** (project) or **`keelyard init --global`** (user level).
  Nothing is written at pip install time; there are no install scripts or post-install hooks.
- Agent config it edits: hooks and MCP entries (`.claude/settings.json`, `.mcp.json`, `.codex/hooks.json`,
  `opencode.json`, or the `~/` equivalents with `--global`) and a block between `<!-- keelyard:start -->` and
  `<!-- keelyard:end -->` in `CLAUDE.md` / `AGENTS.md`. Originals are kept as `<file>.keelyard-backup`.
- Project state lives in `.keelyard/` (config, `shadow.git` snapshots). Snapshots use a separate git dir;
  your own `.git` is never read or written by rewind.
- Memory: `$XDG_DATA_HOME/keelyard/memory.db` (mode 600, redacted).
- Network: none by default and no telemetry. Only `swarm`/`coop` call a model endpoint, and only the one you configure.
- **Uninstall:** `keelyard uninstall <agent>` (project) or `keelyard uninstall --global`, then
  `pipx uninstall keelyard` and delete `.keelyard/` if you want the snapshots gone.

## What it does

- **Proof gate.** An agent that changed files cannot finish until the tests pass **on the current files**.
  Evidence is shown as test-verified, command-succeeded or agent-claimed. Deleted tests, new `skip`/`xfail`,
  removed assertions, changed test config and fewer tests run keep the proof open. Bundles are signed.
- **Rewind.** Every edit is snapshotted into `.keelyard/shadow.git` (your `.git` is never touched).
  `keelyard rewind to <n>` is itself undoable, backs up files snapshots can't hold, and survives being interrupted.
  Secrets (`.env`, keys) are never snapshotted.
- **Smaller context.** Filters for test runners, git, linters and search keep every error, warning and `file:line`
  ([tested on real logs](tests/corpus/)). JSON output is compacted losslessly. Re-reading an unchanged file costs one line.
  The agent is told to answer with results only (opt out: `--no-terse`).
- **Memory, usage, swarm, arena.** Search past sessions of all three agents, see token use from local logs,
  send small checked tasks to a free/cheap model, compare agents on one task.

### Without vs with keelyard

| | Without | With keelyard |
|---|---|---|
| Agent says "done" | You take its word, then run the tests yourself | It can't stop until tests pass on the current files; the proof is shown |
| Agent deletes or skips a failing test | Easy to miss in a long diff | Fewer tests, new `skip`/`xfail` or removed asserts keep the proof open |
| A step breaks something | `git stash`/manual undo, if you committed in time | `keelyard rewind to <n>`, and the rewind itself can be undone |
| 3,000 lines of passing test output | All of it goes into the context window | Errors, warnings and `file:line` kept, the rest collapsed |

### How it plugs into each agent

| Agent | Hooks (gate, snapshots, filters) | MCP server |
|---|---|---|
| Claude Code | `.claude/settings.json` | `.mcp.json` |
| Codex | `.codex/hooks.json` (not yet checked on a live Codex CLI, see [#7](https://github.com/akobirjontulqinovfuture/keelyard/issues/7)) | `~/.codex/config.toml` (or `.codex/config.toml` with `--codex-project-mcp`) |
| OpenCode | `.opencode/plugins/keelyard.js` | `opencode.json` |

`keelyard init` writes these for you; `keelyard doctor` checks them.

**Read [what keelyard guarantees and what it doesn't](docs/guarantees.md)** before you rely on it. In short:
snapshots undo files, not databases, migrations or deployed services; proof is only as good as your tests.

## For agents and scripts

An MCP server (typed tools with examples), a short `CLAUDE.md`/`AGENTS.md` section, and for Claude Code a skill
and slash commands. Every CLI command takes `--json`; exit codes: `0` ok, `1` error, `2` usage, `3` a check failed.

## Docs

[Documentation](docs/index.md): one page per module, CLI and MCP reference, config, troubleshooting,
[benchmarks](BENCHMARKS.md), [per-agent capabilities](docs/guarantees.md#per-agent).

## Inspirations

Ideas, not code: everything in keelyard is written from scratch. Thanks to
[rtk](https://github.com/rtk-ai/rtk) (filtered command output),
[caveman](https://github.com/JuliusBrussee/caveman) and SuperClaude (terse output),
[headroom](https://github.com/headroomlabs-ai/headroom) (content-aware compression, cache-stable context),
[projectmem](https://github.com/riponcm/projectmem) (warn before repeating a failed attempt),
[planning-with-files](https://github.com/OthmanAdi/planning-with-files) and
[context-mode](https://github.com/mksglu/context-mode) (state that survives compaction),
[aider](https://github.com/Aider-AI/aider) (repo map), [claude-mem](https://github.com/thedotmack/claude-mem)
(session memory), [ccusage](https://github.com/ccusage/ccusage) (usage from local logs) and
[cline](https://github.com/cline/cline) (checkpoints). Survey: [docs/research/ideas.md](docs/research/ideas.md).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](ROADMAP.md). Issues labeled
[good first issue](https://github.com/akobirjontulqinovfuture/keelyard/labels/good%20first%20issue) are a good start. Real command logs for the filter corpus are especially welcome.
Security issues: [SECURITY.md](SECURITY.md). Apache-2.0; third-party notices in [NOTICE](NOTICE).
