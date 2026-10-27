# Roadmap

Where keelyard is going after 1.0. Nothing here has a date; items move when someone (often a contributor) picks
them up. Each item links to an issue where one exists. Ideas welcome in
[issues](https://github.com/akobirjontulqinovfuture/keelyard/issues).

## 1.0: release and polish

- PyPI release with Trusted Publishing, so `pip install keelyard` works everywhere
- Timeline browser: `g` / `G` to jump to the first and last step ([#9](https://github.com/akobirjontulqinovfuture/keelyard/issues/9), done in 1.0.0)
- mypy and pyright output keeps every `file:line` and error code ([#1](https://github.com/akobirjontulqinovfuture/keelyard/issues/1), done in 1.0.0)
- Monorepo example for `.keelyard/config.toml` ([#8](https://github.com/akobirjontulqinovfuture/keelyard/issues/8), PR #10 under review)

## 1.1: verified on every agent

- Run the Codex hooks against a real Codex CLI and document the checked version ([#4](https://github.com/akobirjontulqinovfuture/keelyard/issues/4), [#7](https://github.com/akobirjontulqinovfuture/keelyard/issues/7))
- Gemini CLI adapter: hooks + MCP ([#3](https://github.com/akobirjontulqinovfuture/keelyard/issues/3))
- Codex equivalent of the lean preset (Codex has no agent-config switch for it yet)

## 1.2: numbers anyone can reproduce

- A scheduled, public session benchmark: same tasks with and without keelyard, pass rate, tokens and cost per task,
  scripts and raw logs in the repo
- Bigger samples than the current 5 baseline vs 3 lean runs in [BENCHMARKS.md](BENCHMARKS.md)
- Community numbers from real transcripts ([#5](https://github.com/akobirjontulqinovfuture/keelyard/issues/5))
- `go test` and `jest` in the filter benchmark ([#6](https://github.com/akobirjontulqinovfuture/keelyard/issues/6))

## Later

- Per-session token recording for every agent, with a `doctor` warning when savings drop
- More filters from real logs (Maven/Gradle, dotnet test, PHPUnit): send yours, see CONTRIBUTING.md

## Not planned

- Telemetry of any kind
- A hosted service: keelyard stays a local tool
