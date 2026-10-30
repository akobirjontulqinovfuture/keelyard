"""`keelyard init --global`: wire every agent at user level, auto-setup projects, verify with doctor --json."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("KEELYARD_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(h / ".local" / "share"))
    return h


def ky(*args, cwd=None, inp=None):
    env = {**os.environ}
    env.pop("KEELYARD_ROOT", None)
    return subprocess.run([sys.executable, "-m", "keelyard.cli", *args], cwd=cwd, input=inp, capture_output=True, text=True,
                          env=env, timeout=120)


def test_global_install_wires_all_agents_keeps_user_settings_and_backs_up(home, monkeypatch):
    monkeypatch.setenv("KEELYARD_OPENCODE_MCP", "1")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "say done"}]}]}}))
    (home / ".claude.json").write_text(json.dumps({"numStartups": 3, "mcpServers": {"other": {"command": "x"}}}))
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "o4"\n')
    for _ in range(2):  # idempotent
        r = ky("init", "--global", "--json", cwd=home)
        assert r.returncode == 0, r.stderr
    s = json.loads((home / ".claude" / "settings.json").read_text())
    assert s["theme"] == "dark"
    stop = [h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]]
    assert "say done" in stop and sum("keelyard" in c for c in stop) == 1
    cj = json.loads((home / ".claude.json").read_text())
    assert cj["numStartups"] == 3 and "other" in cj["mcpServers"] and cj["mcpServers"]["keelyard"]["args"] == ["mcp"]
    assert "keelyard" in (home / ".claude" / "CLAUDE.md").read_text()
    assert (home / ".claude" / "commands" / "keelyard" / "plan.md").exists()
    toml = (home / ".codex" / "config.toml").read_text()
    assert toml.startswith('model = "o4"') and toml.count("[mcp_servers.keelyard]") == 1
    assert "Stop" in json.loads((home / ".codex" / "hooks.json").read_text())["hooks"]
    oc = home / ".config" / "opencode"
    assert json.loads((oc / "opencode.json").read_text())["mcp"]["keelyard"]["type"] == "local"
    assert (oc / "plugins" / "keelyard.js").exists() and "keelyard" in (oc / "AGENTS.md").read_text()
    assert json.loads((home / ".claude" / "settings.json.keelyard-backup").read_text())["theme"] == "dark"
    assert (home / ".codex" / "config.toml.keelyard-backup").read_text() == 'model = "o4"\n'


def test_global_uninstall_removes_only_keelyard(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "o4"\n')
    assert ky("init", "--global", cwd=home).returncode == 0
    r = ky("uninstall", "--global", "--json", cwd=home)
    assert r.returncode == 0, r.stderr
    assert (home / ".codex" / "config.toml").read_text().strip() == 'model = "o4"'
    assert "keelyard" not in (home / ".claude" / "settings.json").read_text()
    assert "keelyard" not in json.dumps(json.loads((home / ".claude.json").read_text()))
    assert not (home / ".config" / "opencode" / "plugins" / "keelyard.js").exists()
    md = home / ".claude" / "CLAUDE.md"
    assert not md.exists() or "keelyard" not in md.read_text()


def test_first_session_sets_up_a_project_automatically(home, tmp_path):
    assert ky("init", "--global", cwd=home).returncode == 0
    proj = tmp_path / "newproj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    (proj / "a.py").write_text("x = 1\n")
    r = ky("hook", "claude", "SessionStart", cwd=proj, inp=json.dumps({"cwd": str(proj), "session_id": "s1", "source": "startup"}))
    assert r.returncode == 0, r.stderr
    assert (proj / ".keelyard" / "config.toml").exists()
    assert "keelyard is active" in r.stdout
    st = json.loads(ky("status", "--json", cwd=proj).stdout)
    assert st["timeline"]["steps"] >= 1 if isinstance(st.get("timeline"), dict) else True
    # a folder that is not a git repo is left alone
    loose = tmp_path / "loose"
    loose.mkdir()
    ky("hook", "claude", "SessionStart", cwd=loose, inp=json.dumps({"cwd": str(loose), "session_id": "s2"}))
    assert not (loose / ".keelyard").exists()


def test_doctor_json_reports_global_wiring(home):
    assert ky("init", "--global", cwd=home).returncode == 0
    d = json.loads(ky("doctor", "--json", cwd=home).stdout)
    g = {c["check"]: c for c in d["checks"] if c["section"] == "global"}
    assert set(g) == {"claude", "codex", "opencode"} and all(c["state"] == "ok" for c in g.values())


def test_scripted_agent_installs_from_readme_alone(home, tmp_path):
    """Follow only the README's agent install block, as an agent would. Network installs are replaced by the local tree."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- agent-install:start -->(.*?)<!-- agent-install:end -->", text, re.S)
    assert m, "README needs an agent install section"
    block = re.search(r"```(?:sh|bash)\n(.*?)```", m.group(1), re.S).group(1)
    cmds = [c.strip() for c in block.splitlines() if c.strip() and not c.strip().startswith("#")]
    assert any(re.search(r"\b(pipx|pip) install( --user)? keelyard\b", c) for c in cmds)
    ran = []
    for c in cmds:
        if c.split()[0] in ("pipx", "pip", "uv", "python", "py"):
            continue  # the network install; this test runs the source tree instead
        assert c.startswith("keelyard "), c
        r = ky(*c.split()[1:], cwd=home)
        ran.append(c)
        assert r.returncode == 0, (c, r.stdout, r.stderr)
    assert "keelyard init --global" in ran and any(c.startswith("keelyard doctor") for c in ran)
    assert (home / ".claude" / "settings.json").exists()


def test_json_flag_works_before_the_command(home):
    d = json.loads(ky("--json", "doctor", cwd=home).stdout)
    assert "checks" in d
