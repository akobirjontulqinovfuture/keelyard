"""Shipped skills install as slash commands for Claude Code, Codex and OpenCode."""

import json

from keelyard.adapters import install as inst
from keelyard.skills import parse_skill, shipped_skills


def _names():
    return [s.name for s in shipped_skills()]


def test_shipped_skills_include_keesite_and_keepromo():
    skills = {s.name: s for s in shipped_skills()}
    assert "keesite" in skills and "keepromo" in skills
    keesite = skills["keesite"]
    assert "Lighthouse" in keesite.body and "GSAP" in keesite.body
    assert keesite.raw.startswith("---") and "name: keesite" in keesite.raw
    assert keesite.raw.count("\n") < 150
    keepromo = skills["keepromo"]
    assert keepromo.raw.startswith("---\nname: keepromo\n")
    assert "16:9" in keepromo.body and "9:16" in keepromo.body
    parse_skill(keesite.raw, folder="keesite")
    parse_skill(keepromo.raw, folder="keepromo")


def test_install_writes_slash_commands_for_each_agent(project, monkeypatch):
    monkeypatch.setenv("KEELYARD_OPENCODE_MCP", "1")
    dest = {
        "claude": project / ".claude" / "commands",
        "codex": project / ".codex" / "prompts",
        "opencode": project / ".opencode" / "command",
    }
    for agent in inst.AGENTS:
        inst.install(agent, project)
        for name in _names():
            path = dest[agent] / f"{name}.md"
            text = path.read_text(encoding="utf-8")
            assert f"name: {name}" in text
            assert text == next(s.raw for s in shipped_skills() if s.name == name)


def test_uninstall_removes_skills_and_restores_prior_file(project):
    prior = project / ".claude" / "commands" / "keesite.md"
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.write_text("user command\n", encoding="utf-8")
    inst.install("claude", project)
    assert "name: keesite" in prior.read_text(encoding="utf-8")
    inst.uninstall("claude", project)
    assert prior.read_text(encoding="utf-8") == "user command\n"
    assert not (project / ".claude" / "commands" / "keepromo.md").exists()
    assert not (project / ".codex" / "prompts" / "keesite.md").exists()


def test_uninstall_keeps_user_edits_to_a_skill(project):
    inst.install("codex", project)
    path = project / ".codex" / "prompts" / "keesite.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n# I edited this\n", encoding="utf-8")
    inst.uninstall("codex", project)
    assert path.exists() and "# I edited this" in path.read_text(encoding="utf-8")


def test_global_install_and_uninstall_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("KEELYARD_OPENCODE_MCP", "1")
    prior = home / ".codex" / "prompts" / "keepromo.md"
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.write_text("mine\n", encoding="utf-8")
    for agent in inst.AGENTS:
        inst.install_global(agent, home=home)
    assert (home / ".claude" / "commands" / "keesite.md").is_file()
    assert (home / ".codex" / "prompts" / "keesite.md").is_file()
    oc = home / ".config" / "opencode" / "command"
    assert (oc / "keepromo.md").is_file()
    assert "name: keepromo" in prior.read_text(encoding="utf-8")
    for agent in inst.AGENTS:
        inst.uninstall_global(agent, home=home)
    assert prior.read_text(encoding="utf-8") == "mine\n"
    assert not (home / ".claude" / "commands" / "keesite.md").exists()
    assert not (oc / "keesite.md").exists()
    assert not prior.with_name(prior.name + ".keelyard-backup").exists()


def test_doctor_reports_skills(project, capsys):
    from keelyard.cli import main

    inst.install("claude", project)
    assert main(["doctor", "--json"]) == 0
    checks = {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    for name in _names():
        row = checks[f"/{name}"]
        assert row["state"] == "ok"
        assert "claude" in row["detail"]
