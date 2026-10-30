# keelyard + Claude Code in 2 minutes

keelyard plugs into [Claude Code](https://docs.anthropic.com/en/docs/claude-code) through three things Claude Code
already supports: lifecycle **hooks** (`.claude/settings.json`), an **MCP server** (`.mcp.json`), and **CLAUDE.md**.
You keep using Claude Code as usual.

**Checked with Claude Code 2.1.197** (Oct 29, 2026) by [`scripts/claude_e2e.py`](https://github.com/akobirjontulqinovfuture/keelyard/blob/main/scripts/claude_e2e.py):
the real `claude` binary in `-p` mode against a local scripted model (`ANTHROPIC_BASE_URL`), so no Anthropic key is
used. CI runs it on every push and weekly.

## 1. Install

```bash
pipx install keelyard                       # or: pip install keelyard
npm i -g @anthropic-ai/claude-code          # if you don't have Claude Code yet
```

## 2. Wire up your project

```bash
cd your-project
keelyard init --agent claude
keelyard doctor
```

This writes:

| File | What it holds |
|---|---|
| `.claude/settings.json` | hooks `SessionStart`, `UserPromptSubmit`, `PreCompact`, `PreToolUse`, `PostToolUse`, `Stop` → `keelyard hook claude <event>`; lean preset also sets `env` (capped tool output, no extra model calls) |
| `.mcp.json` | `mcpServers.keelyard` → `keelyard mcp` (project scope) |
| `CLAUDE.md` | a short block between `<!-- keelyard:start -->` and `<!-- keelyard:end -->` |
| `.claude/skills/keelyard/` | a keelyard skill |
| `.claude/commands/keelyard/` | `/keelyard:prove`, `review`, `status`, `debug`, `plan`, `undo` |
| `.keelyard/` | config and snapshots (a separate git dir; your `.git` is never touched) |

Originals are kept, and `keelyard uninstall claude` restores them. For every project at once, use
`keelyard init --global` (writes `~/.claude/settings.json`, `~/.claude.json` and `~/.claude/CLAUDE.md`).

## 3. Approve the MCP server once

Claude Code asks before it starts servers from a project's `.mcp.json`. Until then:

```text
$ claude mcp list
keelyard: keelyard mcp - ⏸ Pending approval (run `claude` to approve)
```

Start `claude` in the project and accept the keelyard server. Prefer it for every project? Run
`claude mcp add --scope user keelyard -- keelyard mcp` instead. The hooks need no approval.

## 4. What you see

- **Session start:** a brief with the project's test command and a baseline test result.
- **Every edit** (Edit/Write or a Bash command that changes files) is snapshotted:
  `keelyard rewind list`, `keelyard rewind to 3`.
- **When Claude says "done" with red or unrun tests,** the `Stop` hook sends it back:

  ```text
  Stop hook feedback:
  keelyard proof gate: you changed files but have not proven the work yet.
  ```

  A test run in Claude's own Bash tool counts as proof. The gate gives up after three blocks, so it never loops.
- **Tools:** `mcp__keelyard__proof_check`, `proof_run_tests`, `memory_search`, `find_symbol`, … are available to the model.
- **Afterwards:** `keelyard status` shows `proof proven`, the evidence and the timeline.

## What was checked (Claude Code 2.1.197)

| Check | Result |
|---|---|
| `claude mcp list` shows keelyard from `.mcp.json` | yes (pending approval until accepted) |
| keelyard MCP tools and the CLAUDE.md block reach the model | yes |
| hooks fire: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop | yes (`PreCompact` needs a long session) |
| gate blocks a "Done." after a breaking edit with no test run | yes |
| gate opens after a passing `pytest` in Claude's Bash | yes, `keelyard status` → proven |
| `keelyard rewind to 2` brings back the broken edit | yes |

Not checked: token savings with a real Claude model, and `PreCompact` (needs a long session).

## Verify it yourself

```bash
git clone https://github.com/akobirjontulqinovfuture/keelyard && cd keelyard
pip install -e . pytest && npm i -g @anthropic-ai/claude-code
python scripts/claude_e2e.py
```

Expected output: 11 `PASS` lines.
