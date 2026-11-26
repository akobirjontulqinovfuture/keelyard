"""Fail if the lean defaults the session bench measured (~80% saving) drift. Budgets here are the static token budget."""
import json
import subprocess

from keelyard import config
from keelyard.adapters import install as inst

PROMPT_MAX = 600       # chars of the lean OpenCode build prompt
TERSE_MAX = 80         # chars of the terse rule
BRIEF_TOKENS = 600     # [brain].brief_tokens default
DENIED = {"webfetch", "websearch", "subagent", "skill", "question", "todowrite", "execute", "glob", "grep"}


def _git(p):
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)


def test_static_token_budget():
    assert len(inst.LEAN_OPENCODE_BUILD["system"]) <= PROMPT_MAX
    assert "sed/perl" in inst.LEAN_OPENCODE_BUILD["system"]
    assert {x["action"] for x in inst.LEAN_OPENCODE_BUILD["permissions"]} == DENIED
    terse = inst._guide("terse.md").strip()
    assert 0 < len(terse) <= TERSE_MAX and "terse" in terse.lower()
    assert inst.terse_default()


def test_new_config_is_lean(tmp_path):
    (tmp_path / "tests").mkdir()
    text = config.init(tmp_path).read_text(encoding="utf-8")
    assert config.lean_drift(text) == []
    assert "filter_bash = true" in text and f"brief_tokens = {BRIEF_TOKENS}" in text
    assert config.lean_drift(config.DEFAULT_CONFIG)  # the raw template is not lean; init must apply it


def test_install_defaults_to_lean(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "_opencode_major", lambda: 2)
    _git(tmp_path)
    config.init(tmp_path)
    inst.install("opencode", tmp_path)
    assert json.loads((tmp_path / ".keelyard" / "preset.json").read_text())["preset"] == "lean"
    assert not (tmp_path / "opencode.json").exists()  # lean never rewrites the user's OpenCode config
    inst.install("opencode", tmp_path, trim=True)
    assert json.loads((tmp_path / "opencode.json").read_text())["agents"]["build"] == inst.LEAN_OPENCODE_BUILD


def test_upgrade_migrates_old_defaults_but_keeps_user_values(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / ".keelyard").mkdir()
    cfgp = tmp_path / ".keelyard" / "config.toml"
    cfgp.write_text(config.DEFAULT_CONFIG.replace("auto_test_after_edit = false", "auto_test_after_edit = false\n"), encoding="utf-8")
    assert config.migrate_lean(config.load(tmp_path))
    assert config.lean_drift(cfgp.read_text()) == []
    cfgp.write_text(config.apply_lean(config.DEFAULT_CONFIG).replace("inline_files_tokens = 1500", "inline_files_tokens = 300"))
    assert not config.migrate_lean(config.load(tmp_path))
    assert config.lean_drift(cfgp.read_text())  # user override is reported, not overwritten


def test_global_install_is_lean_and_uninstall_restores(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(inst, "_opencode_major", lambda: 2)
    home = tmp_path / "home"
    for a in ("claude", "opencode"):
        inst.install_global(a, home=home, trim=True)
    assert inst.global_lean_state(home) == {"claude": [], "opencode": []}
    for a in ("claude", "opencode"):
        inst.uninstall_global(a, home=home)
    p = inst.global_paths(home)
    assert "env" not in json.loads(p["claude_settings"].read_text())
    assert "agents" not in json.loads(p["opencode_json"].read_text())


def test_no_test_suite_means_no_test_gate(tmp_path):
    """No tests in the project: the after-edit test line stays off, and doctor does not call that drift."""
    text = config.init(tmp_path).read_text(encoding="utf-8")
    assert "auto_test_after_edit = false" in text and "inline_files_tokens = 1500" in text
    assert config.lean_drift(text, config.has_tests(tmp_path, text)) == []
    (tmp_path / "tests").mkdir()
    assert config.has_tests(tmp_path)
    assert config.migrate_lean(config.load(tmp_path))  # a test suite appeared: lean turns the line on


def test_plain_global_install_leaves_user_agent_config_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    home = tmp_path / "home"
    oc = inst.global_paths(home)["opencode_json"]
    oc.parent.mkdir(parents=True)
    oc.write_text('{"theme": "dark"}')
    inst.install_global("opencode", home=home)
    assert json.loads(oc.read_text()) == {"theme": "dark"}
    inst.install_global("claude", home=home)
    assert "env" not in json.loads(inst.global_paths(home)["claude_settings"].read_text())


def test_stop_gate_does_not_demand_tests_when_project_has_none(tmp_path, monkeypatch):
    from keelyard.proof import Proof
    from keelyard.snapshot import Shadow

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    _git(tmp_path)
    (tmp_path / "notes.txt").write_text("a\n")
    config.init(tmp_path)
    cfg = config.load(tmp_path)
    Shadow(cfg).snapshot(tool="edit", session="s1")
    (tmp_path / "notes.txt").write_text("b\n")
    Shadow(cfg).snapshot(tool="edit", session="s1")
    assert Proof(cfg, "s1").missing() == []  # nothing to run: no "No test run after the last edit" gap
    (tmp_path / "tests").mkdir()
    assert any("No test run" in g for g in Proof(cfg, "s1").missing())
