---
title: How to reduce Claude Code token usage
---

# How to reduce Claude Code token usage

**Short answer:** most tokens in a coding session go to tool output (test logs, installs, `git` progress) and to
re-reading files. Cap and filter that output, avoid repeat reads, and give the agent a small map of the project
instead of letting it explore. keelyard's `lean` preset does this for Claude Code, Codex and OpenCode:

```bash
pipx install keelyard
cd your-project
keelyard init --preset lean
```

For Claude Code, `lean` sets `BASH_MAX_OUTPUT_LENGTH=12000`, `MAX_MCP_OUTPUT_TOKENS=8000` and
`DISABLE_NON_ESSENTIAL_MODEL_CALLS=1` in `.claude/settings.json` (your own values win), adds a shell-output filter
that keeps every failure and `file:line` but drops noise, and puts a file map in the session brief.
`keelyard uninstall claude` removes only what it added.

## How much does it save?

Honest answer: **for Claude Code, not measured yet.** What is measured:

- **Output filtering (any agent):** on the fixed log corpus shipped in the package, 8,887 → 4,253 approx tokens
  (−52%). Example: verbose failing `pytest` 1,811 → 658, `git clone` progress 2,101 → 220. Short logs stay the same.
- **Whole sessions on OpenCode:** with `lean` v2, input tokens (cached + uncached) fell **14.2×** on a task with a
  1,566-test noisy suite (111,783 → 7,857 mean) and **6.0×** on a small 66-test task (49,425 → 8,269), with all
  tests passing in every counted run.

Conditions and limits of the OpenCode numbers: one agent (OpenCode 2), one free model (`opencode/big-pickle`),
5 baseline and 3 lean runs per task, Oct 15, 2026. `lean` also turns off nine OpenCode built-in tools, which is a
big part of the saving and means the agent can do less. Uncached input fell less (4.5× and 9.5×), and that is what
paid models mostly bill. Full data: [Benchmarks](../BENCHMARKS.md).

## How do I reproduce the numbers?

```bash
git clone https://github.com/akobirjontulqinovfuture/keelyard && cd keelyard
pip install -e ".[dev]"
make bench    # offline, a few minutes: output compression and snapshot speed on your machine,
              # plus the session table recomputed from the committed raw logs
```

`make bench-sessions` reruns the real OpenCode sessions (free model, slow). If you measure Claude Code or Codex,
please [share your results](https://github.com/akobirjontulqinovfuture/keelyard/issues/new?template=results.yml),
good or bad.

## What else cuts Claude Code tokens without any tool?

- Keep `CLAUDE.md` short; it is sent with every request.
- Ask for quiet test output (`pytest -q`, `--silent` installs).
- `/compact` long sessions, or start fresh sessions per task.
- See [Claude Code's own cost guide](https://code.claude.com/docs/en/costs).
