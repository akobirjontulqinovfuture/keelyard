"""Audit, risk, budget, watch and ci: new 1.3.0 session-trust features."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from keelyard import audit, budget, hooks, risk, watch
from keelyard.ci import inventory_diff, post_comment, report_md
from keelyard.cli import main
from keelyard.proof import Proof
from keelyard.snapshot import Shadow


def claude(event, **kw):
    return {"session_id": "s1", "hook_event_name": event, "cwd": ".", **kw}


def write_claude_transcript(path: Path, texts: list[str], usage=None):
    lines = []
    for i, text in enumerate(texts):
        msg = {"role": "assistant", "content": text, "id": f"m{i}", "model": "m1"}
        if usage:
            msg["usage"] = usage
        lines.append(json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": f"2026-11-16T12:00:{i:02d}Z",
                                 "message": msg, "requestId": f"r{i}"}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_audit_flags_false_pass_claim_and_ignores_hypotheticals(cfg, project, tmp_path):
    log = tmp_path / "s.jsonl"
    write_claude_transcript(log, [
        "If all tests pass we can merge.",
        "I will run the tests next.",
        "All tests passed and the suite is green.",
    ])
    hooks.handle("SessionStart", claude("SessionStart", source="startup", transcript_path=str(log)), cfg)
    (project / "app.py").write_text("def add(a, b):\n    return 0\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Edit", tool_input={"file_path": "app.py"},
                                       transcript_path=str(log)), cfg)
    pf = Proof(cfg, "s1")
    pf.add_requirements(["x"])
    pf.check(1, "looked")
    pf.run_tests("after", f"{sys.executable} -m pytest -q")
    result = audit.evaluate(cfg, "s1")
    kinds = {c["kind"] for c in result["claims"]}
    assert "tests_passed" in kinds
    assert result["lies"] and "failed" in result["lies"][0]["evidence"]
    assert result["ok"] is False
    # hypotheticals were not scored as claims of success on their own
    assert all("If all tests" not in c["text"] and "will run" not in c["text"] for c in result["claims"])


def test_audit_flags_unchanged_claim_when_tests_were_deleted(cfg, project, tmp_path):
    log = tmp_path / "s.jsonl"
    write_claude_transcript(log, ["I didn't change any tests, only app.py."])
    hooks.handle("SessionStart", claude("SessionStart", source="startup", transcript_path=str(log)), cfg)
    Shadow(cfg).snapshot("baseline", session="s1")
    (project / "tests" / "test_app.py").write_text("# gone\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Write",
                                       tool_input={"file_path": str(project / "tests" / "test_app.py")},
                                       transcript_path=str(log)), cfg)
    result = audit.evaluate(cfg, "s1")
    assert result["weakened"]
    assert any(c["kind"] == "tests_unchanged" for c in result["claims"])
    assert any("test file deleted" in lie["evidence"] or "tests removed" in lie["evidence"] for lie in result["lies"])


def test_audit_ok_when_claim_matches_passing_run(cfg, project, tmp_path):
    log = tmp_path / "s.jsonl"
    write_claude_transcript(log, ["All tests passed."])
    hooks.handle("SessionStart", claude("SessionStart", source="startup", transcript_path=str(log)), cfg)
    (project / "app.py").write_text("def add(a, b):\n    return a + b\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Edit", tool_input={"file_path": "app.py"},
                                       transcript_path=str(log)), cfg)
    pf = Proof(cfg, "s1")
    pf.run_tests("after", f"{sys.executable} -m pytest -q")
    result = audit.evaluate(cfg, "s1")
    assert result["claims"] and result["ok"] and not result["lies"]


def test_stop_hook_mentions_the_false_claim(cfg, project, tmp_path):
    log = tmp_path / "s.jsonl"
    write_claude_transcript(log, ["All tests passed."])
    hooks.handle("SessionStart", claude("SessionStart", source="startup", transcript_path=str(log)), cfg)
    (project / "app.py").write_text("x = 1\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Write", tool_input={"file_path": "app.py"},
                                       transcript_path=str(log)), cfg)
    out = hooks.handle("Stop", claude("Stop", transcript_path=str(log)), cfg)
    assert out and out.get("decision") == "block"
    assert "All tests passed" in out["reason"] or "claimed" in out["reason"].lower()


def test_risk_scores_and_pre_checkpoints(cfg, project):
    assert risk.score_path(".env")[0] == "critical"
    assert risk.score_path(".env.example")[0] == "low"
    assert risk.score_path("docs/auth.md")[0] == "low"
    assert risk.score_path("tests/test_app.py", deleting=True)[0] == "critical"
    assert risk.score_path("src/auth.py")[0] == "high"
    assert risk.score_path("package-lock.json")[0] == "medium"
    hooks.handle("SessionStart", claude("SessionStart", source="startup"), cfg)
    hooks.handle("PreToolUse", claude("PreToolUse", tool_name="Write",
                                      tool_input={"file_path": str(project / "tests" / "test_app.py"),
                                                  "content": "def test_add():\n    assert True\n"}), cfg)
    ev = risk.recent(cfg)
    steps = Shadow(cfg).steps()
    assert ev and ev[-1]["level"] in ("high", "critical")
    assert ev[-1]["step"] == steps[-1].step


def test_budget_blocks_writes_when_cap_crossed(cfg, project, tmp_path):
    log = tmp_path / "s.jsonl"
    write_claude_transcript(log, ["working"], usage={"input_tokens": 80, "output_tokens": 20,
                                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
    cfg_path = project / ".keelyard" / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    prev = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg_path.write_text(prev + "\n[budget]\nmax_tokens = 50\nblock = true\n", encoding="utf-8")
    from keelyard import config as cfgmod
    cfg = cfgmod.load(project)
    hooks.handle("SessionStart", claude("SessionStart", source="startup", transcript_path=str(log)), cfg)
    st = budget.status(cfg, "s1")
    assert st["tokens"] == 100 and st["over"] and st["block"]
    out = hooks.handle("PreToolUse", claude("PreToolUse", tool_name="Write",
                                            tool_input={"file_path": "app.py", "content": "x=1"},
                                            transcript_path=str(log)), cfg)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "budget" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()
    # Reads are still allowed
    out = hooks.handle("PreToolUse", claude("PreToolUse", tool_name="Read",
                                            tool_input={"file_path": "app.py"}, transcript_path=str(log)), cfg)
    assert not (out and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny")


def test_watch_and_cli_json(cfg, project, capsys):
    hooks.handle("SessionStart", claude("SessionStart", source="startup"), cfg)
    d = watch.dashboard(cfg, "s1")
    assert d["session"] == "s1" and d["proof"]["state"] == "no_work"
    text = watch.render(d)
    assert "session" in text and "proof" in text
    assert main(["watch", "--once", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["session"] == "s1"


def test_ci_report_and_diff(cfg, project, capsys):
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=project, check=True)
    # create main if needed
    subprocess.run(["git", "branch", "-M", "main"], cwd=project, check=True)
    (project / "tests" / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
        "    import pytest\n    pytest.skip('later')\n")
    diff = inventory_diff(cfg, "HEAD")
    # worktree has a new skip vs HEAD (committed file had no skip)
    # HEAD is the last commit, so yes
    assert any("skip" in w or "xfail" in w or "tests removed" in w or "assertions" in w for w in diff["weakened"]) or diff["weakened"]
    body, data = report_md(cfg, "HEAD")
    assert "keelyard report" in body and "<!-- keelyard-ci -->" in body
    assert data["ok"] is False  # weakened tests
    out = project / "report.md"
    assert main(["ci", "--out", str(out), "--base", "HEAD"]) == 3
    assert out.exists() and "Tests weakened" in out.read_text(encoding="utf-8")
    assert main(["ci", "--diff-only", "--base", "HEAD", "--json"]) == 3
    assert out.read_text(encoding="utf-8").count("keelyard report") == 1


def test_ci_comment_requires_token(cfg, project, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    from keelyard import ci
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        ci.comment("hello", pr=1, repo="a/b")


def test_ci_post_comment_update(cfg, project, monkeypatch):
    """post_comment creates, then updates, the marked comment. Token never appears in errors."""
    calls = []

    class FakeResp:
        def __init__(self, body):
            self._body = body.encode()
        def read(self):
            return self._body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=30):
        calls.append((req.get_method(), req.full_url, req.data))
        if req.get_method() == "GET":
            return FakeResp(json.dumps([{"id": 1, "body": "old <!-- keelyard-ci -->",
                                      "url": "https://api.github.com/x", "html_url": "https://example.com/c1"}]))
        return FakeResp(json.dumps({"html_url": "https://github.com/a/b/pull/1#c1"}))

    monkeypatch.setattr("keelyard.ci.urlopen", fake_urlopen)
    out = post_comment("<!-- keelyard-ci -->\nnew", 1, "a/b", "secret-token-do-not-print")
    assert out["action"] == "updated" and out["url"].endswith("c1")
    assert any(c[0] == "PATCH" for c in calls)
    # the token is in the request header, not in our return value
    assert "secret-token" not in json.dumps(out)


def test_cli_audit_risk_budget_help(project, capsys):
    for cmd in ("audit", "risk", "budget", "ci", "watch"):
        assert main([cmd, "--help"]) == 0
        assert cmd in capsys.readouterr().out.lower()
