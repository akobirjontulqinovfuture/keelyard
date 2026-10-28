# AGENTS.md: working on keelyard with Codex (or any coding agent)

keelyard is a Python 3.11+ CLI and MCP server that wires hooks into Claude Code, Codex and OpenCode.
This file tells an agent how to change it safely. Humans: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup and checks

```bash
pip install -e ".[dev]"
make test          # ruff + mypy + pytest, must be green before you finish
make codex-e2e     # real Codex CLI against a local scripted model, needs `codex` on PATH, no key
make claude-e2e    # real Claude Code CLI against a local scripted model, needs `claude` on PATH, no key
make demo          # replays a whole session in a temp project
```

Unset `CODEX_HOME` and `OPENAI_API_KEY` before `pytest`; a stray `CODEX_HOME` changes `doctor` output.

## Layout

| Path | What lives there |
|---|---|
| `src/keelyard/cli.py` | argument parsing and command dispatch |
| `src/keelyard/hooks.py` | hook entry point for every agent (`keelyard hook <agent> <event>`) |
| `src/keelyard/proof.py` | the proof gate: requirements, test runs, signed evidence |
| `src/keelyard/snapshot.py` | snapshots in a separate git dir (`.keelyard/`), `rewind` |
| `src/keelyard/adapters/install.py` | `init` / `uninstall` for each agent, project and `--global` |
| `src/keelyard/mcp_server.py` | MCP tools (`proof_*`, `memory_search`, `find_symbol`, ...) |
| `scripts/*_e2e.py` | end-to-end checks with the real agent CLIs |
| `tests/` | pytest, behaviour-level tests |

## Rules

- Never touch the user's `.git`; snapshots go to `.keelyard/` only.
- `uninstall` must restore every file it changed byte for byte. Add a test when you add a file to `init`.
- Hook handlers must never crash the agent: on any internal error, exit 0 and log.
- Agent payloads differ (Codex sends Bash results as a plain string). Test new shapes in `tests/test_proof_hooks.py`.
- The README's `agent-install` block is executed by `tests/test_global.py`; keep it plain commands only.
- No invented numbers in docs: every benchmark figure must come from a committed log in `bench/`.
- Add a CHANGELOG line under the next version for user-visible changes.
