"""End-to-end check of keelyard with the real Codex CLI, no OpenAI key needed.

Codex is pointed at a local scripted model (a tiny Responses API server in this file), so every
hook, the MCP server and AGENTS.md go through the real Codex binary. The script plays an agent that
breaks a function, tries to finish, gets stopped by the proof gate, fixes it, runs the tests and
finishes. Then it checks keelyard's record and rewinds.

    npm i -g @openai/codex && pip install keelyard
    python scripts/codex_e2e.py          # exit 0 = every check passed

Needs: codex and keelyard on PATH, git, and pytest importable by the Python running this script.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PY = sys.executable
SCRIPT = [
    {"call": {"cmd": "sed -i.bak 's/a + b/a - b/' m.py && rm m.py.bak"}},   # break add()
    {"msg": "Done."},                                                     # claim done with red tests
    {"call": {"cmd": f"{PY} -m pytest -q"}},                             # gate made it look
    {"call": {"cmd": "sed -i.bak 's/a - b/a + b/' m.py && rm m.py.bak"}},   # fix
    {"call": {"cmd": f"{PY} -m pytest -q"}},                             # prove
    {"msg": "Fixed; tests pass."},
]
requests: list[dict] = []


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[],"models":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        i = len(requests)
        requests.append(body)
        step = SCRIPT[i] if i < len(SCRIPT) else {"msg": "done"}
        if "call" in step:
            item = {"type": "function_call", "id": f"fc{i}", "call_id": f"c{i}", "name": "exec_command",
                    "arguments": json.dumps(step["call"])}
        else:
            item = {"type": "message", "role": "assistant", "id": f"m{i}",
                    "content": [{"type": "output_text", "text": step["msg"]}]}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for ev in ({"type": "response.created", "response": {"id": f"r{i}"}},
                   {"type": "response.output_item.done", "item": item},
                   {"type": "response.completed", "response": {"id": f"r{i}", "usage": {
                       "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}):
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())


def run(cmd: list[str], cwd: Path, env: dict) -> str:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    return p.stdout + p.stderr


def main() -> int:
    for tool in ("codex", "keelyard", "git"):
        if not shutil.which(tool):
            print(f"missing on PATH: {tool}")
            return 2
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    root = Path(tempfile.mkdtemp(prefix="keelyard-codex-"))
    proj, home = root / "proj", root / "codex-home"
    (proj / "tests").mkdir(parents=True)
    home.mkdir()
    (proj / "m.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "tests" / "test_m.py").write_text("from m import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    (proj / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    env = {**os.environ, "CODEX_HOME": str(home), "OPENAI_API_KEY": "unused-local-model"}
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.com"]
    run(["git", "init", "-q"], proj, env)
    run(git + ["add", "-A"], proj, env)
    run(git + ["commit", "-qm", "init"], proj, env)
    out_init = run(["keelyard", "init", "--agent", "codex"], proj, env)
    keelyard = shutil.which("keelyard")
    (home / "config.toml").write_text(
        'model = "scripted"\nmodel_provider = "local"\n\n'
        f'[model_providers.local]\nname = "local"\nbase_url = "http://127.0.0.1:{srv.server_port}/v1"\n'
        'wire_api = "responses"\nsupports_websockets = false\n\n'
        f'[mcp_servers.keelyard]\ncommand = {json.dumps(keelyard)}\nargs = ["mcp"]\n\n'
        f'[projects.{json.dumps(str(proj))}]\ntrust_level = "trusted"\n')
    ver = next((ln for ln in run(["codex", "--version"], proj, env).splitlines() if "codex" in ln), "codex ?")
    mcp = run(["codex", "mcp", "list"], proj, env)
    log = run(["codex", "exec", "--skip-git-repo-check", "--dangerously-bypass-hook-trust", "-s", "workspace-write",
               "Make add() correct and prove it"], proj, env)
    status = run(["keelyard", "status"], proj, env)
    first = json.dumps(requests[0]) if requests else ""
    gate_msgs = [r for r in requests if "keelyard proof gate" in json.dumps(r.get("input", [])[-1:])]
    rew = run(["keelyard", "rewind", "to", "2"], proj, env)
    broken = "a - b" in (proj / "m.py").read_text()

    checks = [
        ("keelyard init --agent codex wrote .codex/hooks.json", (proj / ".codex" / "hooks.json").is_file()),
        ("AGENTS.md has the keelyard block", "keelyard:start" in (proj / "AGENTS.md").read_text()),
        ("codex mcp list shows keelyard", "keelyard" in mcp),
        ("Codex sent keelyard's MCP tools to the model", "mcp__keelyard" in first),
        ("Codex sent the AGENTS.md block to the model", "keelyard:start" in first),
        ("SessionStart hook ran", "hook: SessionStart Completed" in log),
        ("PreToolUse / PostToolUse hooks ran", "hook: PreToolUse Completed" in log and "hook: PostToolUse Completed" in log),
        ("proof gate blocked 'Done.' while tests were red", "hook: Stop Blocked" in log and bool(gate_msgs)),
        ("Stop allowed after the passing test run", log.rstrip().count("hook: Stop Completed") >= 1),
        ("keelyard status says proven", "proven" in status),
        ("rewind to step 2 brought back the broken edit", broken),
    ]
    print(f"{ver} · {run(['keelyard', '--version'], proj, env).strip()}")
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    failed = [n for n, ok in checks if not ok]
    if failed and "-v" in sys.argv:
        print(out_init, log, status, rew, sep="\n----\n")
    srv.shutdown()
    shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
