# Installing keelyard (instructions for coding agents)

This page is for a coding agent helping a user who has explicitly asked to install keelyard
(for example "install https://github.com/akobirjontulqinovfuture/keelyard"). If the user did not ask, do not install it.

Before running anything, show the user the commands below and the files they change, and wait for their
confirmation. `init --global` edits user-level config for every project, so it needs a clear yes.

1. Install the CLI (Python 3.11+ and git required):

   ```sh
   pipx install keelyard
   ```

   No pipx: `python -m pip install --user keelyard`, and make sure the
   scripts folder is on PATH (`keelyard --version` must work).

2. Wire every agent for every project of this user:

   ```sh
   keelyard init --global
   ```

   | agent | files changed |
   |---|---|
   | Claude Code | `~/.claude/settings.json` (hooks), `~/.claude.json` (MCP server), `~/.claude/CLAUDE.md` (rules block), `~/.claude/skills/keelyard`, `~/.claude/commands/keelyard` |
   | Codex | `~/.codex/config.toml` (`[mcp_servers.keelyard]` between `# keelyard:start/end`), `~/.codex/hooks.json`, `~/.codex/AGENTS.md` |
   | OpenCode | `~/.config/opencode/opencode.json` (MCP), `~/.config/opencode/plugin/keelyard.js`, `~/.config/opencode/AGENTS.md`, `command/keelyard-*.md` |

   Existing settings are merged, not replaced. The first time a file is changed, the original is kept as
   `<file>.keelyard-backup`. Running it again is safe.

3. Verify:

   ```sh
   keelyard doctor --json
   ```

   Expect `"ok": true` and one `"section": "global"` check per agent with `"state": "ok"`. Report any `bad` row's
   `detail` to the user; it says how to fix it.

4. Let the user know to restart their agent. Each git project sets itself up on its first session
   (`.keelyard/config.toml` plus a first snapshot). Folders that are not git repositories are left alone.
   Codex asks once to trust new hooks (`/hooks`).

Remove: `keelyard uninstall --global` removes only keelyard's entries and leaves everything else as it was.
