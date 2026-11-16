# Catch lies, score risky edits, cap spend

keelyard already blocks an agent that tries to finish without a passing test run. These tools add three more
checks around that, plus a dashboard and a PR report. None of them talk to a model, and none of them invent
token-saving numbers.

## Did the agent lie about the tests?

`keelyard audit` reads the session transcript (Claude Code sends `transcript_path` on every hook) and looks
only for **clear, completed** claims:

- "all tests passed", "the test suite is green", "I ran the tests and they passed"
- "I didn't change any tests"

Hypotheticals are ignored: "if all tests pass we can merge" is not a claim. Vague praise is not scored either.

A claim is a **lie** when it disagrees with what keelyard recorded: no test run after the last edit, a failing
run, a passing run that is older than the last edit, or a weaker test inventory (deleted tests, fewer
assertions, new `skip`/`xfail`). The Stop hook adds those lines to the proof-gate message when `[audit].block`
is on (the default).

```bash
keelyard audit
keelyard audit --json
```

## Score a change before it lands

Snapshots usually happen *after* an edit. For a risky write, keelyard also snapshots **first**, so
`keelyard rewind to <step>` still has the pre-edit tree.

| Level | Typical paths |
|---|---|
| critical | `.env`, keys, credentials files; deleting a test file |
| high | CI workflows, Docker/compose, Terraform, migrations, auth-related code, test files |
| medium | lockfiles, `pyproject.toml` / `package.json`, Makefile |
| low | ordinary project files, documentation, `.env.example` |

```bash
keelyard risk
```

`[risk].checkpoint` (default `medium`) controls when the pre-edit snapshot is taken. `[risk].nudge` (default
`high`) controls when the agent is told. `[risk].block` is `off` by default: keelyard does not refuse the
edit unless you set a level.

## Cap what a session may spend

`[budget].max_tokens` and `[budget].max_usd` default to 0 (off). When a cap is crossed, further **edits and
shell commands** are denied; Stop and rewind still work. Spend is read from the agent's own transcript. Cost
is computed only from prices you put in `[prices."model"]`, or from a cost field the log already stores.
keelyard ships no price table.

```toml
[budget]
max_tokens = 200000
# max_usd = 2.00   # only works if you also set [prices]
warn_at = 0.8
block = true
```

```bash
keelyard budget
```

## Watch a session live

```bash
keelyard watch --once          # one snapshot, good for scripts
keelyard watch                 # refresh until Ctrl-C
keelyard watch --once --json
```

The dashboard shows proof state, edits, spend against any cap, the highest recent risk, and any false claims.

## Report it on a pull request

```bash
keelyard ci                    # markdown: proof + audit + spend + test diff vs main
keelyard ci --out report.md
keelyard ci --diff-only --base origin/main
keelyard ci --comment --pr 42  # needs GITHUB_TOKEN; the token is never printed
```

`.keelyard` is gitignored, so a GitHub Actions runner only sees the session proof if you upload that folder
or run `keelyard ci` on the machine that had the agent. The **test-inventory diff** does not need a session:
it compares today's test files with a git base (the PR base, or `main`). That is the check that belongs in CI
even when no proof bundle was uploaded. An agent PR that deletes or skips tests is flagged.

A composite action is in the repo:

```yaml
- uses: akobirjontulqinovfuture/keelyard/.github/actions/proof-report@main
```

It installs keelyard from the same git ref as the action (it does not need a PyPI release) and writes the
report. Set `comment: false` if you only want the markdown.
