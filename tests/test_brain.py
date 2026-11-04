"""keelyard in the agent's working memory: brief, recall, nudges (hooks level, agent-neutral)."""

import json

import pytest

from keelyard import brain, config, hooks
from keelyard.brain import Nudger, destructive, has_tests_nearby
from keelyard.memory import Memory, Turn
from keelyard.proof import Proof


def ctx(out):
    return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


def test_session_start_injects_brief_with_open_items_and_map(cfg):
    Proof(cfg, "s").add_requirements(["SAVE10 takes 10% off"])
    out = hooks.handle("SessionStart", {"session_id": "s", "source": "startup"}, cfg)
    text = ctx(out)
    assert "keelyard brief" in text and "rewind" in text
    assert "SAVE10 takes 10% off" in text and "Project map" in text
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_brief_is_reinjected_after_compaction_and_respects_budget(cfg):
    out = hooks.handle("SessionStart", {"session_id": "s", "source": "compact"}, cfg)
    assert "re-sent after compaction" in ctx(out)
    for budget in (80, 200, 600):
        assert brain.approx_tokens(brain.brief(cfg, "s", budget)) <= budget + 2


def test_brief_can_be_turned_off(project):
    (project / ".keelyard").mkdir(exist_ok=True)
    (project / ".keelyard" / "config.toml").write_text("[brain]\nbrief = false\n")
    assert hooks.handle("SessionStart", {"session_id": "s"}, config.load(project)) is None


def _memory(tmp_path, monkeypatch, project):
    db = tmp_path / "mem.db"
    monkeypatch.setenv("KEELYARD_MEMORY_DB", str(db))
    m = Memory(db)
    m._add(Turn("claude-code", "old", str(project), "2026-09-01", "assistant",
                "Coupon rounding bug: always round money with round(x, 2) after the discount."))
    m._add(Turn("claude-code", "other", "/elsewhere", "2026-09-01", "assistant", "coupon rounding elsewhere secret"))
    m.db.commit()


def test_prompt_recall_injects_relevant_past_snippets_only(cfg, project, tmp_path, monkeypatch):
    _memory(tmp_path, monkeypatch, project)
    out = hooks.handle("UserPromptSubmit", {"session_id": "new", "prompt": "fix the coupon rounding please"}, cfg)
    text = ctx(out)
    assert "round(x, 2)" in text and "elsewhere" not in text
    assert brain.approx_tokens(text) <= cfg.section("brain")["recall_tokens"] + 2
    assert hooks.handle("UserPromptSubmit", {"session_id": "new", "prompt": "unrelated zebra topic"}, cfg) is None


def test_prompt_recall_skips_the_current_session(cfg, project, tmp_path, monkeypatch):
    _memory(tmp_path, monkeypatch, project)
    assert hooks.handle("UserPromptSubmit", {"session_id": "old", "prompt": "coupon rounding"}, cfg) is None


@pytest.mark.parametrize("cmd,kind", [
    ("rm -rf build/", "rm -rf"), ("rm -fr x", "rm -rf"), ("git reset --hard HEAD~1", "git reset --hard"),
    ("git clean -fdx", "git clean -f"), ("git checkout -- .", "git checkout ."), ("git push -f origin main", "git push --force"),
    ("psql -c 'DROP TABLE users'", "DROP/TRUNCATE"), ("python manage.py migrate", "database migration"),
    ("npx prisma migrate reset", "database migration"), ("alembic downgrade -1", "database migration"),
    ("Remove-Item -Recurse -Force dist", "Remove-Item -Recurse"),
    ("bash -c 'rm -rf build'", "rm -rf"), ("sqlite3 app.db \"DELETE FROM users;\"", "DELETE without WHERE"),
    ("psql -f drop_all.sql", None),
])
def test_destructive_commands_are_recognised(cmd, kind):
    assert destructive(cmd) == kind


@pytest.mark.parametrize("cmd", ["rm file.txt", "git reset HEAD a.py", "ls -rf", "git checkout main", "pytest -q",
                                 "git push origin main", "echo 'rm -rf /'", 'git commit -m "drop table users; git reset --hard"',
                                 "grep -n 'DROP TABLE' schema.py", 'rg "rm -rf" docs'])
def test_safe_commands_pass(cmd):
    assert destructive(cmd) is None


def test_destructive_bash_snapshots_first_and_warns_once(cfg, project):
    from keelyard.snapshot import Shadow

    (project / "app.py").write_text("def add(a, b):\n    return a - b\n")
    ev = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "git reset --hard"}}
    out = hooks.handle("PreToolUse", ev, cfg)
    steps = Shadow(cfg).steps()
    assert steps[-1].tool == "pre-destructive" and "app.py" in steps[-1].files
    assert f"keelyard rewind to {steps[-1].step}" in ctx(out) and out["systemMessage"]
    assert "permissionDecision" not in out["hookSpecificOutput"]  # warn, never decide for the user
    assert hooks.handle("PreToolUse", ev, cfg) is None  # rate-limited
    assert hooks.handle("PreToolUse", {**ev, "tool_input": {"command": "ls"}}, cfg) is None


def test_edit_without_tests_nudges_once_per_file(cfg, project):
    (project / "billing.py").write_text("x = 1\n")
    ev = {"session_id": "s", "tool_name": "Edit", "tool_input": {"file_path": "billing.py"}}
    assert "no tests found for billing.py" in ctx(hooks.handle("PreToolUse", ev, cfg))
    assert hooks.handle("PreToolUse", ev, cfg) is None
    assert hooks.handle("PreToolUse", {**ev, "tool_input": {"file_path": "app.py"}}, cfg) is None  # tests/test_app.py


def test_codex_apply_patch_files_are_checked(cfg, project):
    (project / "pay.py").write_text("x = 1\n")
    patch = "*** Begin Patch\n*** Update File: pay.py\n@@\n-x = 1\n+x = 2\n*** End Patch\n"
    # Claude-style `input`, and Codex CLI which puts the same patch in `tool_input.command`.
    for key in ("input", "command"):
        out = hooks.handle("PreToolUse", {"session_id": key, "tool_name": "apply_patch", "tool_input": {key: patch}}, cfg)
        assert "pay.py" in ctx(out)


@pytest.mark.parametrize("rel,files,expected", [
    ("app.py", ["tests/test_app.py"], True), ("src/cart.ts", ["src/cart.test.ts"], True), ("lib/x.py", [], False),
    ("README.md", [], True), ("test_thing.py", [], True), ("pkg/handler.go", ["pkg/handler_test.go"], True),
])
def test_tests_nearby(tmp_path, rel, files, expected):
    for f in [rel, *files]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x\n")
    assert has_tests_nearby(tmp_path, rel) is expected


def test_nudges_respect_cap_cooldown_and_switch(project):
    (project / ".keelyard").mkdir(exist_ok=True)
    (project / ".keelyard" / "config.toml").write_text("[brain]\nmax_nudges_per_session = 2\n")
    n = Nudger(config.load(project), "s")
    assert n.allow("a") and not n.allow("a") and n.allow("b") and not n.allow("c")
    assert Nudger(config.load(project), "s2").allow("a")  # new session, new allowance
    (project / ".keelyard" / "config.toml").write_text("[brain]\nnudges = false\n")
    assert not Nudger(config.load(project), "s3").allow("a")


def test_budget_nudge_when_session_is_large(project, tmp_path):
    (project / ".keelyard").mkdir(exist_ok=True)
    (project / ".keelyard" / "config.toml").write_text("[brain]\nplan_tokens = 1000\n")
    c = config.load(project)
    t = tmp_path / "t.jsonl"
    t.write_text("x" * 12 * 900)  # ~900 tokens
    out = hooks.handle("UserPromptSubmit", {"session_id": "s", "prompt": "go", "transcript_path": str(t)}, c)
    assert "swarm" in ctx(out) and "1,000" in ctx(out)
    t.write_text("x" * 100)
    assert hooks.handle("UserPromptSubmit", {"session_id": "s2", "prompt": "go", "transcript_path": str(t)}, c) is None


def test_repeat_read_returns_outline_or_diff(cfg, project):
    big = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(80))
    (project / "big.py").write_text(big)
    ev = {"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": str(project / "big.py")}}
    hooks.handle("PostToolUse", ev, cfg)
    reason = hooks.handle("PreToolUse", ev, cfg)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "unchanged" in reason and "def f3()" in reason
    (project / "big.py").write_text(big.replace("return 40\n", "return 41\n"))
    reason = hooks.handle("PreToolUse", ev, cfg)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "diff only" in reason and "+    return 41" in reason
    assert hooks.handle("PreToolUse", ev, cfg)["hookSpecificOutput"]["permissionDecision"] == "deny"  # now cached


def test_installed_hooks_cover_the_new_events(project):
    from keelyard.adapters.install import install

    install("claude", project)
    s = json.loads((project / ".claude" / "settings.json").read_text())["hooks"]
    assert "Bash" in s["PreToolUse"][0]["matcher"] and "Edit" in s["PreToolUse"][0]["matcher"]
    install("codex", project)
    c = json.loads((project / ".codex" / "hooks.json").read_text())["hooks"]
    assert "PreToolUse" in c and "SessionStart" in c
