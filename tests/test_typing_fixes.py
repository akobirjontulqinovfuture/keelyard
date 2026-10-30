"""Bugs found while making the code pass mypy."""

import json
import subprocess
import sys
from pathlib import Path

from keelyard import saver
from keelyard.adapters.install import InstallError, _merge_json


def test_doctor_survives_an_invalid_config(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".keelyard").mkdir()
    (tmp_path / ".keelyard" / "config.toml").write_text("[proof\nbroken", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "keelyard.cli", "doctor", "--json"], cwd=tmp_path, capture_output=True, text=True)
    d = json.loads(r.stdout)
    assert any(c["check"] == "config" and c["state"] == "bad" for c in d["checks"]), d
    assert "Traceback" not in r.stderr


def test_timeout_output_is_text_not_bytes_repr():
    assert saver._text(b"partial \xe2\x9c\x93") == "partial \u2713"
    assert saver._text(None) == "" and saver._text("x") == "x"


def test_non_utf8_json_config_is_a_clear_install_error(tmp_path: Path):
    bad = tmp_path / "opencode.json"
    bad.write_bytes(b"\xff\xfe{")
    try:
        _merge_json(bad, "mcp", "keelyard", {})
    except InstallError as e:
        assert "UTF-8" in str(e)
    else:
        raise AssertionError("expected InstallError")
