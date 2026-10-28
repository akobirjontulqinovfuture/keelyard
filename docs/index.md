# keelyard

<p class="lede">Your coding agent has to show its work before it can say "done". Every edit is snapshotted, so any
step can be undone. Logs are trimmed before they eat the context window. keelyard works with Claude Code, Codex
and OpenCode.</p>

![keelyard in a terminal](img/hero.gif)

```bash
pipx install keelyard
cd your-project
keelyard init
```

That is the whole setup. `keelyard init` finds the agents on your machine and wires each one: hooks, the MCP
server, a short guide in `CLAUDE.md` or `AGENTS.md`, and a skill plus slash commands for Claude Code.
After that it takes a first snapshot.

## The pieces

| | What you get |
|---|---|
| [Proof gate](guide/proof.md) | The agent can't finish until there is a checklist, evidence for every item, and a passing test run after its last edit. You get a one-file HTML report. |
| [Rewind](guide/rewind.md) | A snapshot after every edit, in a separate git repo. Undo any step and fork from any step. |
| [Smaller context](guide/context.md) | Output filters that keep every failure, deduplicated reads, `project_map` / `outline` / `find_symbol`. |
| [Memory and usage](guide/memory.md) | Search across past sessions from all three agents. Tokens and cost from local logs. |
| [Swarm](guide/swarm.md) | Bulk reading and drafting on a cheap model, with every citation checked. |
| [Arena](guide/arena.md) | One task, several agents and models: pass rate, time and tokens side by side. |

## What it is not

keelyard is not a proxy and does not read your API traffic. It does not change your `.git`, and it does not send
anything over the network unless you configure a helper model for `swarm`. Every number in these docs was
measured. See [Benchmarks](BENCHMARKS.md), including the results that did not go our way.
