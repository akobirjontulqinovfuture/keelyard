"""keelyard init --preset lean|balanced|max-quality: lowest-token agent settings, fully reversible."""

import json

import pytest

from keelyard.adapters.install import PRESETS, InstallError, install, uninstall


def test_presets_known():
    assert set(PRESETS) == {"lean", "balanced", "max-quality"}


def test_trim_opencode_trims_agent_and_uninstall_restores(project, monkeypatch):
    monkeypatch.setattr("keelyard.adapters.install._opencode_major", lambda: 2)
    (project / "opencode.json").write_text(json.dumps({"theme": "dark"}))
    install("opencode", project, preset="lean", trim=True)
    oc = json.loads((project / "opencode.json").read_text())
    build = oc["agents"]["build"]
    assert len(build["system"]) < 600
    denied = {r["action"] for r in build["permissions"] if r["effect"] == "deny"}
    assert {"webfetch", "websearch", "subagent", "todowrite"} <= denied
    assert {"glob", "grep"} <= denied
    assert "shell" not in denied and "edit" not in denied and "read" not in denied
    assert "keelyard" not in oc.get("mcp", {})
    uninstall("opencode", project)
    assert json.loads((project / "opencode.json").read_text()) == {"theme": "dark"}


def test_trim_opencode_v1_config_and_restore(project, monkeypatch):
    """OpenCode 1.x rejects V2 `agents`/`permissions`; --trim writes the V1 shape there and uninstall removes it."""
    monkeypatch.setattr("keelyard.adapters.install._opencode_major", lambda: 1)
    (project / "opencode.json").write_text(json.dumps({"theme": "dark"}))
    install("opencode", project, preset="lean", trim=True)
    oc = json.loads((project / "opencode.json").read_text())
    assert "agents" not in oc
    build = oc["agent"]["build"]
    assert len(build["prompt"]) < 600
    assert {"webfetch", "websearch", "task", "glob", "grep"} <= {k for k, v in build["permission"].items() if v == "deny"}
    assert "bash" not in build["permission"] and "edit" not in build["permission"]
    assert build["tools"] == {"todowrite": False, "todoread": False}
    uninstall("opencode", project)
    assert json.loads((project / "opencode.json").read_text()) == {"theme": "dark"}


def test_trim_keeps_user_agent_settings(project, monkeypatch):
    monkeypatch.setattr("keelyard.adapters.install._opencode_major", lambda: 2)
    (project / "opencode.json").write_text(json.dumps({"agents": {"build": {"system": "mine"}}}))
    install("opencode", project, preset="lean", trim=True)
    assert json.loads((project / "opencode.json").read_text())["agents"]["build"] == {"system": "mine"}


def test_trim_claude_env_and_restore(project):
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(json.dumps({"env": {"BASH_MAX_OUTPUT_LENGTH": "99"}}))
    install("claude", project, preset="lean", trim=True)
    env = json.loads((project / ".claude" / "settings.json").read_text())["env"]
    assert env["BASH_MAX_OUTPUT_LENGTH"] == "99"  # the user's value wins
    assert env["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] == "1"
    uninstall("claude", project)
    data = json.loads((project / ".claude" / "settings.json").read_text())
    assert data == {"env": {"BASH_MAX_OUTPUT_LENGTH": "99"}}


def test_max_quality_opencode_adds_mcp(project):
    install("opencode", project, preset="max-quality")
    assert json.loads((project / "opencode.json").read_text())["mcp"]["keelyard"]["type"] == "local"
    assert "const MCP = true;" in (project / ".opencode" / "plugins" / "keelyard.js").read_text()


def test_unknown_preset(project):
    with pytest.raises(InstallError):
        install("opencode", project, preset="turbo")


def test_lean_sets_keelyard_config(project):
    from keelyard import config

    config.init(project)
    install("opencode", project, preset="lean")
    c = config.load(project)
    assert c.section("brain")["inline_files_tokens"] == 1500
    assert c.section("proof")["auto_test_after_edit"] is True


def test_lean_alone_never_touches_agent_config(project):
    """The reported bug: plain lean rewrote opencode.json and Claude env. It must leave both alone."""
    (project / "opencode.json").write_text(json.dumps({"theme": "dark"}))
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text(json.dumps({"env": {"A": "1"}}))
    install("opencode", project, preset="lean")
    install("claude", project, preset="lean")
    assert json.loads((project / "opencode.json").read_text()) == {"theme": "dark"}
    assert json.loads((project / ".claude" / "settings.json").read_text())["env"] == {"A": "1"}


def test_rerun_without_trim_removes_earlier_trim(project, monkeypatch):
    monkeypatch.setattr("keelyard.adapters.install._opencode_major", lambda: 2)
    (project / "opencode.json").write_text(json.dumps({"theme": "dark"}))
    install("opencode", project, preset="lean", trim=True)
    assert "agents" in json.loads((project / "opencode.json").read_text())
    install("opencode", project, preset="lean")
    assert json.loads((project / "opencode.json").read_text()) == {"theme": "dark"}


def test_trim_keeps_core_tools_documented_list():
    from keelyard.adapters.install import LEAN_OPENCODE_BUILD

    assert LEAN_OPENCODE_BUILD["permissions"]  # only reachable through --trim (opt-in)
