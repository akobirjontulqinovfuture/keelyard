---
title: How to roll back Claude Code or Codex changes
---

# How to roll back Claude Code or Codex changes

**Short answer:** in Claude Code, `/rewind` (or `Esc` twice) restores files Claude changed with its edit tools.
Changes made only by shell commands are not tracked, and Codex CLI has no built-in undo since its experimental
`/undo` was removed. To undo *any* step, including shell-made changes and untracked files, keelyard snapshots the
project after every edit and shell command into a separate git directory, so you can run `keelyard rewind to <step>`.

```bash
pipx install keelyard
cd your-project
keelyard init                 # wires Claude Code, Codex and OpenCode hooks; takes a first snapshot
# ... let the agent work ...
keelyard rewind list          # one step per edit or shell command
keelyard rewind show 12       # the diff step 12 made
keelyard rewind to 9          # files back to how they were after step 9
```

## What are the options for undoing an AI coding agent's changes?

| Option | What it restores | Gaps |
|---|---|---|
| Claude Code `/rewind` (checkpoints) | Files Claude edited with its file tools, per prompt; also the conversation | Files changed only by Bash are not tracked; most subagent edits are not restored; Claude Code only ([docs](https://code.claude.com/docs/en/checkpointing)) |
| Codex CLI `/undo` | Nothing today | Removed in 2026; Codex reports the feature as unavailable ([openai/codex#9203](https://github.com/openai/codex/issues/9203)) |
| `git stash` / `git checkout` / `git reset` | The last commit | Work since the last commit is lost; untracked files need extra flags; easy to lose your own edits too |
| Commit after every step | Anything committed | Noisy history, and agents do not reliably commit |
| **keelyard rewind** | Project files after every edit or shell command, for Claude Code, Codex and OpenCode | Files only: databases, pushed commits, package installs and anything outside the project are not restored; files over 20 MB are skipped |

Use Claude Code checkpoints for quick in-session undo, and git for real history. keelyard fills the gap between them.

## Does keelyard touch my git repository?

No. Snapshots go to `.keelyard/shadow.git`, a separate repository. Your `.git`, index, stash and branches are never
touched. Snapshots follow `.gitignore`.

## Can I undo a rewind?

Yes. `keelyard rewind to` snapshots the current state first and prints its step number; rewind to that number to go
back. Files created after the target step are removed, and files deleted since then are restored.

## Can I try a different approach from an earlier step?

`keelyard rewind fork 9 ../attempt-b` copies step 9 into another folder, so you can start a second agent there.

## How fast is it?

On a 7,083-file Django checkout, a snapshot after an edit took 88 ms (median); the first snapshot took 2.3 s
because it hashes every file. See [Rewind](../guide/rewind.md).
