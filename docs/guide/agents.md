# For agents

keelyard is meant to be driven by agents. Everything a person can do from the CLI, an agent can do through
MCP, and both return JSON.

## What `init` installs

| agent | hooks | MCP | instructions |
|---|---|---|---|
| Claude Code | `.claude/settings.json` | `.mcp.json` | `CLAUDE.md` section, `.claude/skills/keelyard/SKILL.md`, `/keelyard:status`, `/keelyard:prove`, `/keelyard:undo` |
| Codex | `.codex/hooks.json` | snippet for `~/.codex/config.toml`, or `.codex/config.toml` with `--codex-project-mcp` | `AGENTS.md` section |
| OpenCode | `.opencode/plugins/keelyard.js` | `opencode.json` | `AGENTS.md` section |

The instruction sections sit between `<!-- keelyard:start -->` and `<!-- keelyard:end -->`. `init` replaces only
that block, and `uninstall` removes only that block. The rest of the file stays as you wrote it.

## The loop an agent follows

1. `proof_requirements(items=[...])`
2. `project_map`, `find_symbol`, `outline`, then `read_file`
3. edit (snapshots happen automatically)
4. `run_compact` for tests and builds
5. `proof_run_tests(phase="after")`, then `proof_check` for each item
6. `proof_status` → `{"proven": true}`

All 20 tools are listed, with their schemas, in the [MCP reference](../mcp.md).

## Self-test

`keelyard selftest` plays the agent. It sends the same hook JSON Claude Code sends and does everything else
through the MCP server over stdio. In a temporary project it records requirements, reads code, searches
memory, runs a swarm task against a local stub model, makes a bad edit, gets blocked by the gate, rewinds,
fixes the bug, proves it and is let through. It needs no network and no API key.

![selftest](../img/cli-selftest.png)

### Codex: checked version

Checked against **Codex CLI 0.165.0** (Oct 28, 2026) with `scripts/codex_e2e.py`, which runs the real Codex binary
against a local scripted model (no API key) and runs in CI on every push and weekly. All five hooks fire, the proof
gate blocks a "done" with red tests and opens after a passing shell test run, MCP tools and the AGENTS.md block reach
the model, and rewind restores an edit. Codex sends shell results to `PostToolUse` as a plain string with no exit
code; keelyard reads the test result from the output. See [Codex in 2 minutes](../codex.md).

### Codex MCP: project or user level

Codex reads MCP servers from `~/.codex/config.toml`, and from a project's `.codex/config.toml` only when you
trust the project. keelyard never writes either without asking:

- `keelyard init --codex-project-mcp` (or `keelyard install codex --codex-project-mcp`) adds the server to this
  project's `.codex/config.toml`, between `# keelyard:start` / `# keelyard:end`. Trust the project in Codex once.
- `keelyard install codex --global` adds it to `~/.codex/config.toml` for every project.

`keelyard doctor` warns if Codex hooks are wired but neither file has the server. `keelyard uninstall codex`
removes the block and deletes the file if nothing else was in it.

### Codex CLI

Checked on Codex CLI 0.165.0 (`@openai/codex`). `.codex/hooks.json` uses Codex's own shape: a top-level `hooks`
object, then the event, then a matcher group, then `{type: command}`. Hooks are on by default. Project hooks
load only after you trust the project, and each command must be approved once with `/hooks`.

On that version a real session did this:

- `SessionStart` added the keelyard brief as developer context.
- `PreToolUse` for `rm -rf build` warned that `rm -rf` is destructive and named the snapshot to rewind to.
  Codex also refuses `rm -f` style commands itself, before they run.
- A shell command that changed a file was snapshotted, and `keelyard status` listed that step.
- `Stop` with `decision: block` kept the turn open when proof was missing.

Shell output is a string (`tool_response`), not Claude's `{stdout, stderr}` object. Codex rejects
`updatedToolOutput` and reports `PostToolUse Failed`. keelyard therefore sends a filtered result as
`continue: false` and `stopReason`. Codex replaces the tool result the model sees and reports
`PostToolUse Stopped`. `apply_patch` carries the patch in `tool_input.command`.
