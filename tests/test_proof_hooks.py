import json
import subprocess
import sys

from keelyard import hooks
from keelyard.proof import Proof


def claude(event, **kw):
    return {"session_id": "s1", "hook_event_name": event, "cwd": ".", **kw}


def test_gate_lifecycle(cfg, project):
    assert hooks.handle("SessionStart", claude("SessionStart", source="startup"), cfg)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    hooks.handle("UserPromptSubmit", claude("UserPromptSubmit", prompt="make add() support 3 args"), cfg)
    # pure Q&A turn: nothing changed -> no gate
    assert hooks.handle("Stop", claude("Stop", stop_hook_active=False), cfg) is None

    (project / "app.py").write_text("def add(a, b, c=0):\n    return a + b + c\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Edit", tool_input={"file_path": "app.py"}), cfg)
    out = hooks.handle("Stop", claude("Stop", stop_hook_active=False), cfg)
    assert out["decision"] == "block"
    assert "requirement checklist" in out["reason"] and "No test run" in out["reason"]

    pf = Proof(cfg, "s1")
    pf.add_requirements(["add() accepts an optional third argument"])
    pf.check(1, "app.py:1 signature is add(a, b, c=0); test_add passes")
    run = pf.run_tests("after", f"{sys.executable} -m pytest -q")
    assert run["exit"] == 0
    assert hooks.handle("Stop", claude("Stop", stop_hook_active=True), cfg) is None
    assert pf.load().task.startswith("make add()")
    assert (pf.dir / "report.html").read_text().count("pill ok\">Proven<") == 1


def test_gate_gives_up_after_max_blocks(cfg, project):
    hooks.handle("SessionStart", claude("SessionStart"), cfg)
    (project / "app.py").write_text("x = 2\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Write", tool_input={}), cfg)
    blocks = [hooks.handle("Stop", claude("Stop"), cfg) for _ in range(4)]
    assert [b.get("decision") for b in blocks[:3]] == ["block"] * 3
    assert "decision" not in blocks[3] and "proof incomplete" in blocks[3]["systemMessage"]


def test_failing_tests_keep_gate_closed(cfg, project):
    hooks.handle("SessionStart", claude("SessionStart"), cfg)
    (project / "app.py").write_text("def add(a, b):\n    return 0\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Bash", tool_input={"command": "sed ..."}), cfg)
    pf = Proof(cfg, "s1")
    pf.add_requirements(["x"])
    pf.check(1, "looked at it")
    assert pf.run_tests("after", f"{sys.executable} -m pytest -q")["exit"] != 0
    assert any("failed" in g for g in pf.missing())


def test_hook_cli_claude_format_end_to_end(project):
    """Run the real `keelyard hook claude ...` binary the way Claude Code does: JSON on stdin, JSON on stdout."""
    def run(event, payload):
        p = subprocess.run([sys.executable, "-m", "keelyard.cli", "hook", "claude", event],
                           input=json.dumps(payload), capture_output=True, text=True, cwd=project)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout) if p.stdout.strip() else None

    base = {"session_id": "abc-123", "transcript_path": "/tmp/x.jsonl", "cwd": str(project)}
    out = run("SessionStart", {**base, "hook_event_name": "SessionStart", "source": "startup"})
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    (project / "app.py").write_text("print('changed')\n")
    run("PostToolUse", {**base, "hook_event_name": "PostToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": str(project / "app.py"), "content": "..."}, "tool_response": {}})
    out = run("Stop", {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    assert out["decision"] == "block"
    # malformed input never breaks the agent
    p = subprocess.run([sys.executable, "-m", "keelyard.cli", "hook", "claude", "Stop"], input="not json",
                       capture_output=True, text=True, cwd=project)
    assert p.returncode == 0


def test_red_test_is_the_only_gap_shown(cfg, project):
    """A failing run already says what to do; asking for a checklist too just costs tokens."""
    hooks.handle("SessionStart", claude("SessionStart"), cfg)
    (project / "app.py").write_text("def add(a, b):\n    return 0\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Bash", tool_input={"command": "sed ..."}), cfg)
    pf = Proof(cfg, "s1")
    assert pf.run_tests("after", f"{sys.executable} -m pytest -q")["exit"] != 0
    gaps = pf.missing()
    assert len(gaps) == 1 and "failed" in gaps[0]


def test_codex_shell_test_run_opens_the_gate(cfg, project):
    """Codex (0.165) sends a Bash tool_response as a plain string with no exit code."""
    hooks.handle("SessionStart", claude("SessionStart"), cfg)
    hooks.handle("UserPromptSubmit", claude("UserPromptSubmit", prompt="fix add"), cfg)
    (project / "app.py").write_text("def add(a, b):\n    return a + b\n")
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Edit", tool_input={"file_path": "app.py"}), cfg)
    hooks.handle("PostToolUse", claude("PostToolUse", tool_name="Bash", tool_input={"command": "python -m pytest -q"},
                                       tool_response="..                                    [100%]\n2 passed in 0.01s\n"), cfg)
    runs = [r for r in Proof(cfg, "s1").load().test_runs if r["source"] == "shell"]
    assert [r["exit"] for r in runs] == [0]
    assert hooks.handle("Stop", claude("Stop", stop_hook_active=True), cfg) is None
