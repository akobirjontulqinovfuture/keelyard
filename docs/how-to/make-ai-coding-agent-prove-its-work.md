---
title: How to make an AI coding agent prove its work before it says "done"
---

# How to make an AI coding agent prove its work before it says "done"

**Short answer:** don't rely on the prompt; add a check the agent cannot skip. keelyard installs a `Stop` hook in
Claude Code, Codex CLI and OpenCode. If the agent changed files and there is no passing test run *after its last
edit*, the hook blocks the finish and tells the agent what is missing. A test run in the agent's own shell counts.

```bash
pipx install keelyard
cd your-project
keelyard init --agent claude      # or: --agent codex, --agent opencode, or plain `keelyard init` for all found
keelyard status                   # what is proven and what is missing
```

## Why do AI coding agents say "done" when the tests fail?

The agent decides when to stop. Instructions like "always run the tests" in `CLAUDE.md` or `AGENTS.md` are advice,
and they get skipped on long tasks or after compaction. A hook runs outside the model, so the finish condition is
checked every time.

## How do I stop coding agents from skipping or deleting tests?

keelyard keeps the proof open when tests are deleted, when the number of test functions or assertions drops, when new
`skip`/`xfail`/`only` markers appear, or when fewer tests run than before. It does **not** catch every weakening:
changing an expected value to match wrong output still passes. See [Guarantees and limits](../guarantees.md).

## What is the best proof gate for AI coding agents?

It depends on what you need to prove. Agent-native options:

| Option | What it checks | Limits |
|---|---|---|
| Rules in `CLAUDE.md` / `AGENTS.md` | Nothing; it is a request | Easy for the model to skip |
| Your own `Stop` hook script | Whatever you write | You maintain it per agent, and must handle "tests ran *before* the last edit" |
| CI on the pull request | The full suite, after the fact | The agent already said "done"; feedback arrives late |
| **keelyard proof gate** | A passing test run after the last edit, a requirements checklist with evidence, signs of test weakening | Only as good as your tests; checks three agents (Claude Code, Codex, OpenCode) |

keelyard is not a replacement for CI or code review. It moves the "did the tests pass after the last change?"
check into the agent's own loop.

## Can the agent record what it verified?

Yes. Over MCP (`keelyard mcp`) the agent can store the request as a checklist (`proof_requirements`), attach
evidence per item (`proof_check`), run tests (`proof_run_tests`) and ask `proof_status` what is missing. keelyard
writes a one-file HTML report. See [Proof gate](../guide/proof.md) and the [MCP reference](../mcp.md).

## Is this checked against the real agents?

Claude Code and Codex CLI are run end to end in CI on every push, against a local scripted model (no API key):
the gate blocks a finish without proof and opens after a passing run.
