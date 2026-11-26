"""CLI rough edges found in the 0.1 audit: bare group commands, usage lines, codex MCP check."""

import json
import subprocess

from test_global import home, ky  # noqa: F401  (fixture re-export)


def _proj(tmp_path):
    p = tmp_path / "p"
    p.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)
    return p


def test_bare_proof_shows_status_and_supports_json(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    assert ky("init", "--no-agents", cwd=p).returncode == 0
    r = ky("proof", cwd=p)
    assert r.returncode == 0 and "no work yet" in r.stdout
    r = ky("proof", "--json", cwd=p)
    assert r.returncode == 0 and json.loads(r.stdout)["state"] == "no_work"


def test_usage_errors_name_the_real_command(home, tmp_path):  # noqa: F811
    r = ky("code", cwd=_proj(tmp_path))
    assert r.returncode == 2
    assert "usage: keelyard code" in r.stderr and "<command> [options] code" not in r.stderr


def test_doctor_warns_when_codex_mcp_is_not_registered(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    assert ky("install", "codex", cwd=p).returncode == 0
    checks = json.loads(ky("doctor", "--json", cwd=p).stdout)["checks"]
    assert any(c["check"] == "codex mcp" and c["state"] == "warn" for c in checks)
    assert ky("install", "codex", "--global", cwd=p).returncode == 0
    checks = json.loads(ky("doctor", "--json", cwd=p).stdout)["checks"]
    assert not any(c["check"] == "codex mcp" for c in checks)


def test_every_text_file_access_is_utf8():
    """Windows defaults to cp1252: an Uzbek/Cyrillic comment or an emoji in an agent log would crash keelyard."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "keelyard"
    bad = []
    for f in src.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\.(read_text|write_text)\(", text):
            depth, j = 1, m.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            if "encoding" not in text[m.end():j]:
                bad.append(f"{f.name}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not bad, bad


def test_hook_reads_non_ascii_payload(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    (p / "app.py").write_text("# Qarshi shahri — привет ✓\n", encoding="utf-8")
    assert ky("init", "--agent", "claude", "--json", cwd=p).returncode == 0
    payload = json.dumps({"session_id": "s", "cwd": str(p), "hook_event_name": "UserPromptSubmit",
                          "prompt": "funksiyani tuzat — исправь ✓"}, ensure_ascii=False)
    import os
    import sys
    env = {k: v for k, v in os.environ.items() if k != "KEELYARD_ROOT"}
    r = subprocess.run([sys.executable, "-m", "keelyard.cli", "hook", "claude", "UserPromptSubmit"], cwd=p, env=env,
                       input=payload.encode("utf-8"), capture_output=True, timeout=120)  # agents send UTF-8 bytes
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8")  # keelyard must answer in UTF-8 too, whatever the console code page
    json.loads(out) if out.strip() else None


def test_codex_project_mcp_opt_in_and_clean_uninstall(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    assert ky("install", "codex", cwd=p).returncode == 0
    assert not (p / ".codex" / "config.toml").exists()  # never written without the flag
    assert ky("install", "codex", "--codex-project-mcp", cwd=p).returncode == 0
    toml = (p / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert toml.count("[mcp_servers.keelyard]") == 1
    checks = json.loads(ky("doctor", "--json", cwd=p).stdout)["checks"]
    assert not any(c["check"] == "codex mcp" for c in checks)
    assert ky("uninstall", "codex", cwd=p).returncode == 0
    assert not (p / ".codex").exists()


def test_codex_project_mcp_keeps_user_toml(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    (p / ".codex").mkdir()
    (p / ".codex" / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    assert ky("init", "--agent", "codex", "--codex-project-mcp", cwd=p).returncode == 1  # your file: needs consent
    assert (p / ".codex" / "config.toml").read_text(encoding="utf-8") == 'model = "x"\n'
    assert ky("init", "--agent", "codex", "--codex-project-mcp", "--yes", cwd=p).returncode == 0
    assert ky("uninstall", "codex", cwd=p).returncode == 0
    assert (p / ".codex" / "config.toml").read_text(encoding="utf-8").strip() == 'model = "x"'


def test_group_json_flag_works_before_or_after_the_action(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    assert ky("init", "--no-agents", cwd=p).returncode == 0
    for args in (["proof", "--json", "status"], ["proof", "status", "--json"], ["code", "--json", "map"],
                 ["rewind", "--json", "list"], ["memory", "stats", "--json"]):
        r = ky(*args, cwd=p)
        assert r.returncode == 0, (args, r.stderr)
        json.loads(r.stdout)


def test_human_output_has_no_trailing_spaces(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    (p / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert ky("init", cwd=p).returncode == 0
    for args in (["status"], ["doctor"], ["rewind"], ["proof"], ["uninstall", "claude"]):
        out = ky(*args, cwd=p).stdout
        assert not [ln for ln in out.splitlines() if ln != ln.rstrip()], (args, out)


def test_baseline_names_your_files_not_agent_setup(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    (p / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert ky("init", "--agent", "claude", cwd=p).returncode == 0
    out = ky("rewind", cwd=p).stdout
    line = next(ln for ln in out.splitlines() if "#1" in ln)
    assert "a.py" in line and "agent setup" in line and "CLAUDE.md" not in line


def test_existing_agent_config_needs_consent_dry_run_shows_diff(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    (p / ".claude").mkdir()
    mine = '{"permissions": {"allow": ["Bash(ls)"]}}\n'
    (p / ".claude" / "settings.json").write_text(mine, encoding="utf-8")
    r = ky("init", "--agent", "claude", "--dry-run", cwd=p)
    assert r.returncode == 0 and "would change your file" in r.stdout and "+++ b/.claude/settings.json" in r.stdout
    r = ky("init", "--agent", "claude", "--dry-run", "--json", cwd=p)
    assert any(c["path"] == ".claude/settings.json" and c["kind"] == "modify" for c in json.loads(r.stdout)["diff"])
    r = ky("install", "claude", cwd=p)  # no tty, no --yes: refuse, change nothing
    assert r.returncode == 1 and "--yes" in r.stdout + r.stderr
    assert (p / ".claude" / "settings.json").read_text(encoding="utf-8") == mine
    assert not (p / "CLAUDE.md").exists() and not (p / ".mcp.json").exists()
    assert ky("install", "claude", "--yes", cwd=p).returncode == 0
    assert "keelyard" in (p / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert (p / ".keelyard" / "originals" / ".claude__settings.json.orig").read_text(encoding="utf-8") == mine
    assert ky("uninstall", "claude", cwd=p).returncode == 0
    assert (p / ".claude" / "settings.json").read_text(encoding="utf-8") == mine  # byte-for-byte restore


def test_new_files_only_need_no_consent(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    assert ky("init", "--agent", "claude", cwd=p).returncode == 0


def test_opencode_json_untouched_by_default_trim_is_opt_in(home, tmp_path):  # noqa: F811
    p = _proj(tmp_path)
    mine = '{"theme": "dark"}\n'
    (p / "opencode.json").write_text(mine, encoding="utf-8")
    assert ky("init", "--agent", "opencode", cwd=p).returncode == 0  # nothing of yours changes: no prompt
    assert (p / "opencode.json").read_text(encoding="utf-8") == mine
    assert ky("init", "--agent", "opencode", cwd=p, inp="").returncode == 0
    r = ky("install", "opencode", "--trim", cwd=p)
    assert r.returncode == 1  # --trim edits opencode.json: consent needed
    assert ky("install", "opencode", "--trim", "--yes", cwd=p).returncode == 0
    assert "webfetch" in (p / "opencode.json").read_text(encoding="utf-8")
    assert ky("uninstall", "opencode", cwd=p).returncode == 0
    assert (p / "opencode.json").read_text(encoding="utf-8") == mine
