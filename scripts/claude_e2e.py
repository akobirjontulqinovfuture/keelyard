"""End-to-end check of keelyard with the real Claude Code CLI, no Anthropic key needed.

Claude Code is pointed at a local scripted model (a tiny Messages API server in this file, via
ANTHROPIC_BASE_URL), so every hook, the MCP server and CLAUDE.md go through the real `claude` binary.
The script plays an agent that breaks a function, tries to finish, gets stopped by the proof gate,
fixes it, runs the tests and finishes. Then it checks keelyard's record and rewinds.

    npm i -g @anthropic-ai/claude-code && pip install keelyard
    python scripts/claude_e2e.py          # exit 0 = every check passed

Needs: claude and keelyard on PATH, git, and pytest importable by the Python running this script.
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
    {"tool": {"command": "sed -i.bak 's/a + b/a - b/' m.py && rm m.py.bak", "description": "break add"}},
    {"msg": "Done."},
    {"tool": {"command": f"{PY} -m pytest -q", "description": "run tests"}},
    {"tool": {"command": "sed -i.bak 's/a - b/a + b/' m.py && rm m.py.bak", "description": "fix add"}},
    {"tool": {"command": f"{PY} -m pytest -q", "description": "run tests"}},
    {"msg": "Fixed; tests pass."},
]
requests: list[dict] = []


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def _json(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json(b'{"data":[]}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "count_tokens" in self.path:
            return self._json(b'{"input_tokens":10}')
        if body.get("tools"):           # the main agent loop; side calls (titles etc.) get plain text
            i = len(requests)
            requests.append(body)
            step = SCRIPT[i] if i < len(SCRIPT) else {"msg": "done"}
        else:
            i, step = -1, {"msg": "ok"}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def ev(kind: str, data: dict) -> None:
            data["type"] = kind
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())

        ev("message_start", {"message": {"id": f"m{i}", "type": "message", "role": "assistant",
                                         "model": body.get("model", "scripted"), "content": [],
                                         "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}})
        if "tool" in step:
            ev("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": f"tu{i}",
                                                                      "name": "Bash", "input": {}}})
            ev("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta",
                                                              "partial_json": json.dumps(step["tool"])}})
            stop = "tool_use"
        else:
            ev("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
            ev("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": step["msg"]}})
            stop = "end_turn"
        ev("content_block_stop", {"index": 0})
        ev("message_delta", {"delta": {"stop_reason": stop}, "usage": {"output_tokens": 1}})
        ev("message_stop", {})


def run(cmd: list[str], cwd: Path, env: dict) -> str:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    return p.stdout + p.stderr


def main() -> int:
    for tool in ("claude", "keelyard", "git"):
        if not shutil.which(tool):
            print(f"missing on PATH: {tool}")
            return 2
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    root = Path(tempfile.mkdtemp(prefix="keelyard-claude-"))
    proj, home = root / "proj", root / "home"
    (proj / "tests").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    # approve the project's .mcp.json without the interactive prompt
    (home / ".claude" / "settings.json").write_text('{"enableAllProjectMcpServers": true}\n')
    (proj / "m.py").write_text("def add(a, b):\n    return a + b\n")
    (proj / "tests" / "test_m.py").write_text("from m import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    (proj / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), ANTHROPIC_API_KEY="unused-local-model",
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{srv.server_port}")
    git = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.com"]
    run(["git", "init", "-q"], proj, env)
    run(git + ["add", "-A"], proj, env)
    run(git + ["commit", "-qm", "init"], proj, env)
    out_init = run(["keelyard", "init", "--agent", "claude"], proj, env)
    ver = run(["claude", "--version"], proj, env).strip().splitlines()[-1:] or ["claude ?"]
    mcp = run(["claude", "mcp", "list"], proj, env)
    raw = run(["claude", "-p", "Make add() correct and prove it", "--dangerously-skip-permissions",
               "--output-format", "stream-json", "--verbose", "--include-hook-events"], proj, env)
    events = []
    for ln in raw.splitlines():
        try:
            events.append(json.loads(ln))
        except ValueError:
            pass
    fired = {e.get("hook_event") for e in events if e.get("subtype") == "hook_response"}
    blocked = [e for e in events if e.get("subtype") == "hook_response" and e.get("hook_event") == "Stop"
               and "keelyard proof gate" in str(e.get("output", ""))]
    result = next((e for e in events if e.get("type") == "result"), {})
    status = run(["keelyard", "status"], proj, env)
    first = json.dumps(requests[0]) if requests else ""
    rew = run(["keelyard", "rewind", "to", "2"], proj, env)
    broken = "a - b" in (proj / "m.py").read_text()

    checks = [
        ("keelyard init --agent claude wrote .claude/settings.json hooks",
         "hooks" in json.loads((proj / ".claude" / "settings.json").read_text())),
        ("CLAUDE.md has the keelyard block", "keelyard:start" in (proj / "CLAUDE.md").read_text()),
        ("claude mcp list shows keelyard", "keelyard" in mcp),
        ("Claude Code sent keelyard's MCP tools to the model", "mcp__keelyard__" in json.dumps(requests)),  # MCP starts async
        ("Claude Code sent the CLAUDE.md block to the model", "Every edit is snapshotted" in first),
        ("SessionStart / UserPromptSubmit hooks ran", {"SessionStart", "UserPromptSubmit"} <= fired),
        ("PreToolUse / PostToolUse hooks ran", {"PreToolUse", "PostToolUse"} <= fired),
        ("proof gate blocked 'Done.' while tests were unrun", bool(blocked)),
        ("session finished after the passing test run", result.get("subtype") == "success"),
        ("keelyard status says proven", "proven" in status),
        ("rewind to step 2 brought back the broken edit", broken),
    ]
    print(f"claude {ver[0]} · {run(['keelyard', '--version'], proj, env).strip()}")
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    failed = [n for n, ok in checks if not ok]
    if failed and "-v" in sys.argv:
        print(out_init, mcp, raw[-4000:], status, rew, sep="\n----\n")
    srv.shutdown()
    shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
