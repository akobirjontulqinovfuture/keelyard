import json
import os

from keelyard.memory import Memory, format_hits


def write_fixtures(home):
    cc = home / "claude" / "projects" / "-work-shop"
    cc.mkdir(parents=True)
    lines = [
        {"type": "user", "sessionId": "cc1", "cwd": "/work/shop", "timestamp": "2026-09-01T10:00:00Z",
         "message": {"role": "user", "content": "the stripe webhook returns 400 signature mismatch"}},
        {"type": "assistant", "sessionId": "cc1", "cwd": "/work/shop", "timestamp": "2026-09-01T10:01:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Fixed: use the raw request body before json parsing when verifying the webhook signature."},
             {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "hooks.py"}}]}},
        {"type": "user", "sessionId": "cc1", "cwd": "/work/shop", "timestamp": "2026-09-01T10:02:00Z",
         "message": {"role": "user", "content": "here is my key STRIPE_SECRET=sk_live_abcdefghijklmnop"}},
        {"type": "summary", "summary": "ignored"},
    ]
    (cc / "cc1.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")

    cx = home / "codex" / "sessions" / "2026" / "09" / "02"
    cx.mkdir(parents=True)
    rows = [
        {"type": "session_meta", "payload": {"id": "cx1", "cwd": "/work/api"}},
        {"type": "response_item", "timestamp": "2026-09-02T08:00:00Z",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "alembic migration fails on sqlite"}]}},
        {"type": "response_item", "timestamp": "2026-09-02T08:01:00Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Use batch_alter_table for sqlite ALTER support."}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{\"cmd\":[\"alembic\",\"upgrade\"]}"}},
    ]
    (cx / "rollout-x.jsonl").write_text("\n".join(json.dumps(x) for x in rows))

    oc = home / "xdg" / "opencode" / "storage"
    (oc / "session" / "p1").mkdir(parents=True)
    (oc / "session" / "p1" / "ses1.json").write_text(json.dumps({"id": "ses1", "directory": "/work/web"}))
    (oc / "message" / "ses1").mkdir(parents=True)
    (oc / "message" / "ses1" / "msg1.json").write_text(json.dumps({"id": "msg1", "role": "assistant", "time": {"created": 1}}))
    (oc / "part" / "msg1").mkdir(parents=True)
    part = {"type": "text", "text": "vite hydration mismatch fixed by suppressHydrationWarning"}
    (oc / "part" / "msg1" / "prt1.json").write_text(json.dumps(part))
    return [("claude-code", home / "claude" / "projects"), ("codex", home / "codex" / "sessions"),
            ("opencode", oc)]


def test_ingest_search_redact(tmp_path):
    sources = write_fixtures(tmp_path)
    mem = Memory(tmp_path / "m.db")
    stats = mem.ingest(sources)
    assert stats["claude-code"] >= 3 and stats["codex"] >= 3 and stats["opencode"] == 1
    assert mem.ingest(sources)["claude-code"] == 0  # incremental: unchanged files skipped

    hits = mem.search("webhook signature")
    assert hits and hits[0]["agent"] == "claude-code"
    assert "raw request body" in format_hits(mem.search("verify webhook raw body"))
    assert mem.search("sqlite migration")[0]["agent"] == "codex"
    assert mem.search("hydration")[0]["agent"] == "opencode"
    assert mem.search("webhook", project="/work/api") == []

    everything = mem.db.execute("SELECT group_concat(text) FROM chunks").fetchone()[0]
    assert "sk_live_abcdefghijklmnop" not in everything
    assert sum(len(h["snippet"]) for h in mem.search("the", budget_chars=100)) <= 100
    if os.name != "nt":  # Windows has no POSIX modes
        assert (tmp_path / "m.db").stat().st_mode & 0o077 == 0


def test_codex_system_prompt_and_injected_context_are_not_memory(tmp_path):
    from keelyard.memory import read_codex

    f = tmp_path / "rollout.jsonl"
    msg = lambda role, text: {"type": "response_item", "payload": {  # noqa: E731
        "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}
    rows = [msg("developer", "You are a coding agent running in the Codex CLI"),
            msg("user", "# AGENTS.md instructions for /x\n<INSTRUCTIONS>be terse</INSTRUCTIONS>"),
            msg("user", "<hook_prompt hook_run_id=\"stop:1\">keelyard proof gate</hook_prompt>"),
            msg("user", "the cache key ignores the locale")]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    assert [t.text for t in read_codex(f)] == ["the cache key ignores the locale"]
