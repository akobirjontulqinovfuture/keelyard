# Configuration

`keelyard init` writes `.keelyard/config.toml`. Every field is optional. This is the default file:

```toml
# keelyard configuration. Every field is optional.

[proof]
# Block the agent from finishing until a proof bundle exists.
gate = true
# Command used for before/after test runs. Empty = auto-detect.
test_cmd = ""
# URL of the running app; when set, a screenshot is required for proof.
app_url = ""
# Stop the gate from looping forever: give up after this many blocks per session.
max_blocks = 3

[rewind]
enabled = true
# Files larger than this are left out of snapshots and listed as skipped.
max_file_mb = 20
# Tools that trigger a snapshot after they run.
snapshot_tools = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "apply_patch", "write", "edit", "bash", "patch"]

[swarm]
# Max concurrent helper calls.
concurrency = 16
# Hard cap on tasks per swarm call.
max_tasks = 256
# Which [models.*] entry helpers use.
helper_model = "cheap"
# Samples per task (best-of-N), retries with verifier feedback, and the model tried once when all fail
# ("" = no escalation). Draft patches are also run against the tests in a temporary copy.
best_of = 2
retries = 1
escalate_model = "strong"
test_patches = true
# Cooperative mode (default; `keelyard swarm run --basic` turns it off for one run): models tried cheapest first
# ([] = helper_model then escalate_model), concurrent workers, a peer critic, and hard token caps
# (0 = no cap). Calls are refused before they are made once a cap would be crossed.
cooperative = true
ladder = []
workers = 64
critic = false
budget_tokens = 200000
task_budget_tokens = 20000

[mcp]
# Tools the MCP server offers. Every schema costs tokens in every session, so the default is the 13 the
# workflow needs. "all" adds outline, project_map, rewind_diff, usage_report, route_task, swarm_plan,
# swarm_verify, status (and proof_screenshot, which "core" adds by itself when [proof].app_url is set).
tools = "core"

[saver]
# Deny a repeat Read of an unchanged file (reset on context compaction).
dedupe_reads = true
# Filter large Bash results before the model reads them (raw output kept for `keelyard recall`).
filter_bash = true
filter_min_chars = 1200
# Max characters returned by compress_output before head/tail trimming.
max_output_chars = 6000

# Optional prices for `keelyard usage`, USD per million tokens. keelyard ships none.
# [prices."your-model-name"]
# input = 0.0
# output = 0.0
# cache_read = 0.0
# cache_write = 0.0

# Models are plain names you choose; keelyard never assumes a vendor's lineup.
# provider = "openai" (any OpenAI-compatible /chat/completions) or "anthropic".
[models.cheap]
provider = "openai"
base_url = "http://localhost:11434/v1"
model = "qwen2.5-coder:7b"
api_key_env = ""

[models.strong]
provider = "anthropic"
base_url = "https://api.anthropic.com"
model = "set-me"
api_key_env = "ANTHROPIC_API_KEY"
```

`.keelyard/` also holds snapshots (`shadow.git`, `steps.jsonl`), proof bundles, shortened outputs (`raw/`)
and the hook error log (`hook.log`). The folder contains a `.gitignore` with `*`, so it is never committed.

## Monorepo example

In a monorepo containing multiple packages (for example, a Python API backend alongside a JavaScript/TypeScript frontend), you can configure `.keelyard/config.toml` at the repository root to coordinate proof checks and token budgets across packages:

```toml
# .keelyard/config.toml for a monorepo (Python backend + TypeScript frontend)

[proof]
gate = true
# Chain test commands across packages so proof bundles verify the entire workspace:
test_cmd = "pytest -q backend && npm --prefix frontend test"
# Require visual proof if the frontend UI is running locally:
app_url = "http://localhost:3000"
# Prevent infinite gate loops: give up after 3 blocked attempts in a session:
max_blocks = 3

[mcp]
# MCP tool set offered to the agent:
# "core" (default): 13 workflow tools to keep tool schemas compact in context.
# "all": adds outline, project_map, rewind_diff, usage_report, route_task,
#        swarm_plan, swarm_verify, and status for extended agent introspection.
tools = "core"

[brain]
# Session brief: inject a compact architectural overview at start and post-compaction:
brief = true
# Brief cap: limit brief size to avoid taking up too much context window:
brief_tokens = 800

# Past-session recall: inject relevant memories matching the current prompt:
recall = true
# Recall cap: limit retrieved memory snippets to stay within budget:
recall_tokens = 350

# Work progress nudges:
nudges = true
nudge_cooldown_s = 300
max_nudges_per_session = 12

# Optional session budget warning threshold:
plan_tokens = 150000
budget_warn_at = 0.6

[rewind]
enabled = true
max_file_mb = 20

[saver]
# Deduplicate repeated reads of unchanged files across packages:
dedupe_reads = true
filter_bash = true
filter_min_chars = 1200
max_output_chars = 6000

[swarm]
# Helper swarm settings for parallel subtasks across packages:
concurrency = 16
max_tasks = 128
helper_model = "cheap"
escalate_model = "strong"
test_patches = true
cooperative = true
workers = 32
budget_tokens = 200000
task_budget_tokens = 20000

[models.cheap]
provider = "openai"
base_url = "http://localhost:11434/v1"
model = "qwen2.5-coder:7b"
api_key_env = ""

[models.strong]
provider = "anthropic"
base_url = "https://api.anthropic.com"
model = "claude-3-5-sonnet-20241022"
api_key_env = "ANTHROPIC_API_KEY"
```

Environment variables:

| variable | effect |
|---|---|
| `KEELYARD_ROOT` | use this folder as the project root instead of searching upward for `.keelyard` or `.git` |
| `XDG_DATA_HOME` | where the memory index lives (default `~/.local/share/keelyard`) |
| `NO_COLOR`, `FORCE_COLOR=0` | plain terminal output |

