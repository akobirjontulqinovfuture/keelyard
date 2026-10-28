# keelyard + Codex in 2 minutes

keelyard plugs into the [Codex CLI](https://github.com/openai/codex) through three things Codex already supports:
lifecycle **hooks** (`.codex/hooks.json`), an **MCP server**, and **AGENTS.md**. You keep using Codex as usual.

**Checked with Codex CLI 0.165.0** (Oct 28, 2026) by [`scripts/codex_e2e.py`](https://github.com/akobirjontulqinovfuture/keelyard/blob/main/scripts/codex_e2e.py),
which runs the real Codex binary against a local scripted model, so it needs no OpenAI key. CI runs it on every push.

## 1. Install

```bash
pipx install keelyard        # or: pip install keelyard
npm i -g @openai/codex       # if you don't have Codex yet
```

## 2. Wire up your project

```bash
cd your-project
keelyard init --agent codex --codex-project-mcp
keelyard doctor
```

This writes:

| File | What it holds |
|---|---|
| `.codex/hooks.json` | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop` → `keelyard hook codex <event>` |
| `.codex/config.toml` | `[mcp_servers.keelyard]` (only with `--codex-project-mcp`) |
| `AGENTS.md` | a short block between `<!-- keelyard:start -->` and `<!-- keelyard:end -->` |
| `.keelyard/` | config and snapshots (a separate git dir; your `.git` is never touched) |

Originals are kept, and `keelyard uninstall codex` restores them byte for byte.

Prefer the MCP server for every project? Put it in `~/.codex/config.toml` instead (or run
`codex mcp add keelyard -- keelyard mcp`):

```toml
[mcp_servers.keelyard]
command = "keelyard"
args = ["mcp"]
```

`codex mcp list` should now show `keelyard  …  enabled`.

The AGENTS.md block keelyard adds (lean preset) is three lines:

```markdown
## keelyard

Every edit is snapshotted (undo: `keelyard rewind list`, `keelyard rewind to N`). Your shell test runs are the proof:
run the project's tests after your last edit before you finish.
Be terse: no preamble or recap. Keep code exact.
```

## 3. Trust the hooks once

Codex only runs hooks you have reviewed. Start `codex` in the project, type **`/hooks`**, and trust the five keelyard
hooks. Codex also needs the project itself to be trusted to load `.codex/` (it asks the first time).
For one-off automation where you vetted the hooks yourself, `codex exec --dangerously-bypass-hook-trust` skips the
review for that run.

## 4. What you see

- **Session start:** a brief with the project's test command and a baseline test result.
- **Every edit** (`apply_patch` or a shell command that changes files) is snapshotted:
  `keelyard rewind list`, `keelyard rewind to 3`.
- **When Codex says "done" with red or unrun tests,** the `Stop` hook sends it back with what is missing:

  ```text
  keelyard proof gate: you changed files but have not proven the work yet.
  - No test run after the last edit (proof_run_tests phase='after', or `keelyard proof test after`).
  Run the project's tests in your shell (that counts as proof), or use the keelyard proof tools, then finish.
  ```

  A test run in Codex's own shell counts as proof; Codex does not need to call keelyard. The gate gives up after
  three blocks so a session can never loop forever.
- **Afterwards:** `keelyard status` shows `proof proven`, the evidence and the timeline.

## Verify it yourself

```bash
git clone https://github.com/akobirjontulqinovfuture/keelyard && cd keelyard
pip install -e . pytest && npm i -g @openai/codex
python scripts/codex_e2e.py
```

Expected output: 11 `PASS` lines (hooks file, AGENTS.md, `codex mcp list`, MCP tools and AGENTS.md reaching the
model, each hook event firing, the gate blocking a red "Done.", the gate opening after a passing run,
`keelyard status` proven, rewind restoring the broken edit).

## Known limits with Codex

- **Output filtering does not apply.** Codex hooks can't replace a shell result yet, so keelyard's log compression
  works through the MCP `run_compact` tool only, not on Codex's own shell calls.
- **No lean switch for Codex tools.** The lean preset can turn some Claude Code / OpenCode tools off; Codex has no
  config for that, so on Codex lean means the shorter brief and AGENTS.md block.
- **Token savings on Codex are not measured yet.** The e2e check uses a scripted model, which proves the wiring,
  not savings. Real-model numbers are on the [roadmap](https://github.com/akobirjontulqinovfuture/keelyard/blob/main/ROADMAP.md).
- Codex's own sandbox rejects some commands (for example `rm -rf`) before keelyard's `PreToolUse` sees them.
