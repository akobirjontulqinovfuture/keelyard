# keelyard alternatives and comparisons

What other tools do, and when you might want them instead of, or next to, keelyard. Facts about other projects
come from their own READMEs; numbers are only ours where we say "measured". Full table: [Landscape](landscape.md).

## Built-in agent features

| need | built into the agent | what keelyard adds |
|---|---|---|
| Undo an edit | Claude Code has `/rewind` checkpoints and OpenCode has `/undo`, each for edits made in its own sessions | snapshots after **every** edit and shell command, across all three agents, in a separate git dir; rewind is itself undoable |
| Run tests | every agent can run tests when it decides to | a `Stop` hook that **blocks** finishing until a passing run after the last edit is recorded, and keeps the proof open if tests were weakened |
| Project rules | `CLAUDE.md` / `AGENTS.md` | a short block in those files plus hooks, so the rule is enforced, not only stated |

## Output and context tools

- **rtk**: per-command output filters. On our 8 test commands we measured 56,522 → 5,747 tokens for rtk and
  5,901 for keelyard. Use rtk if you only want output filtering.
- **repomix**, **code2prompt**: pack a repository into one prompt. Good for one-shot chats; keelyard instead gives
  the agent ranked slices (`find_symbol`, outlines) during a session.
- **serena**: symbol-level navigation and editing over LSP. Deeper code intelligence than keelyard's `find_symbol`.
- **ccusage**: usage reports from local logs. keelyard's `usage` does a similar job across Claude Code, Codex and OpenCode.

## Workflow packs

- **superpowers**, **spec-kit**: skills and spec-to-task workflows. keelyard ships small plan / review / debug
  commands, but its focus is enforcing proof, not planning.

## When keelyard is not the right tool

- You need a sandbox. keelyard runs as your user and is not a security boundary ([Guarantees](guarantees.md)).
- Your project has no tests. The proof gate can only check what your test suite checks.
- You need to undo database changes, deployments or pushed commits. Snapshots hold files only.
