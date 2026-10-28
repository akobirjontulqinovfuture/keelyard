# FAQ

Short answers to the questions people ask about keelyard. Every claim links to the page with the details.

## What is keelyard?

keelyard is an open-source (Apache-2.0) command-line tool and MCP server that makes AI coding agents
(Claude Code, Codex CLI and OpenCode) prove their work with a passing test run before they can finish, snapshots
every edit so any step can be undone, and trims noisy tool output. It runs locally, with no telemetry.

## How do I make Claude Code or Codex prove the tests pass before it says "done"?

Install keelyard and wire it into the project:

```bash
pipx install keelyard
keelyard init --agent claude     # or: --agent codex
```

This adds a `Stop` hook. When the agent changed files and tries to finish without a passing test run after its
last edit, the hook blocks it and says what is missing. A test run in the agent's own shell counts as proof.
See [Proof gate](guide/proof.md), [Claude Code in 2 minutes](claude-code.md) and [Codex in 2 minutes](codex.md).

## Can the agent cheat by deleting or weakening tests?

keelyard keeps the proof open when tests are deleted, when there are fewer test functions or assertions, when new
`skip`/`xfail`/`only` markers appear, or when fewer tests run than before. It does **not** catch every weakening:
changing an expected value still looks fine. Details: [Guarantees and limits](guarantees.md).

## How do I roll back a bad step an AI coding agent made?

keelyard snapshots the project after every edit and shell command, in a separate git directory
(`.keelyard/shadow.git`), so your own `.git` is never touched.

```bash
keelyard rewind list
keelyard rewind to 3
```

A rewind first snapshots the current state, so it can be undone too. Snapshots hold files only: databases,
pushed commits and package installs are not restored. See [Rewind](guide/rewind.md).

## How do I reduce the tokens a coding agent uses?

keelyard's `lean` preset (the default for `init`) caps tool output, filters noisy logs (test runners, git, package
managers) and gives the agent smaller code views. On our own benchmark of six tasks run in OpenCode
(3–8 runs per setup), whole-session tokens went from 267,525 to 52,745 (−80%) with equal or better correctness.
That is one benchmark on one agent with small samples; savings on Claude Code and Codex are not measured yet.
See [Benchmarks](BENCHMARKS.md) and [Presets](guide/presets.md).

## Does it need an API key or send my code anywhere?

No. keelyard runs on your machine and makes no network calls, except to model endpoints you configure yourself
for the optional `swarm` and `coop` commands. The end-to-end checks in CI use a local scripted model, so they
need no key either.

## Which agents and platforms are supported?

Claude Code, Codex CLI and OpenCode, on Linux, macOS and Windows, with Python 3.11+. Claude Code and Codex are
checked end to end in CI on every push with the real CLIs. See [For agents](guide/agents.md).

## How is keelyard different from other tools?

Most related tools do one thing: shrink output, pack the repo, or track usage. keelyard's main job is the proof
gate plus undo, with output trimming as a side benefit. See [Alternatives](alternatives.md).

## How do I remove it?

`keelyard uninstall claude` (or `codex`, `opencode`) restores the files `init` changed. `keelyard uninstall
--global` does the same for user-level config.
