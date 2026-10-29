import json
import sys
import textwrap
from pathlib import Path

from keelyard import arena, codemap, usage


def test_outline_python_and_ts(project):
    (project / "m.py").write_text(textwrap.dedent('''
        MAX = 3
        class A(Base):
            """Holds things."""
            def f(self, x: int = 1) -> str:
                return "x"
        async def g(*args, **kw):
            pass
    '''))
    out = codemap.outline(project / "m.py")
    assert "class A(Base)  # Holds things." in out and "def f(self, x: int=1) -> str" in out and "async def g(*args, **kw)" in out
    assert "MAX = 3" in out and 'return "x"' not in out
    (project / "w.ts").write_text("export async function load(id: string) {\n  return 1\n}\nexport const h = (a) => a\ninterface P { a: 1 }\n")
    ts = codemap.outline(project / "w.ts")
    assert "load" in ts and "const h" in ts and "interface P" in ts and "return 1" not in ts


def test_find_symbol_and_map(project):
    assert "app.py:1: def add(a, b):" in codemap.find_symbol(project, "add")
    assert codemap.find_symbol(project, "nope_xyz").startswith("no definition")
    assert codemap.find_symbol(project, "a;rm") == "symbol must be an identifier"
    assert "app.py" in codemap.project_map(project)


def test_project_map_skips_bytecode_and_caches(project):
    (project / "m.py").write_text("def f():\n    return 1\n")
    cache = project / "__pycache__"
    cache.mkdir()
    (cache / "m.cpython-313.pyc").write_bytes(b"\x00\x01")
    (project / "stale.pyc").write_bytes(b"\x00\x01")
    (project / ".pytest_cache").mkdir()
    (project / ".pytest_cache" / "CACHEDIR.TAG").write_text("x")
    out = codemap.project_map(project)
    assert "m.py" in out and "app.py" in out
    assert "__pycache__" not in out and ".pyc" not in out and ".pytest_cache" not in out


def test_usage_three_agents(tmp_path):
    cc = tmp_path / "c" / "p"
    cc.mkdir(parents=True)
    line = {"type": "assistant", "timestamp": "2026-10-01T00:00:00Z", "requestId": "r1",
            "message": {"id": "m1", "model": "model-a", "usage": {"input_tokens": 10, "output_tokens": 5,
                                                                    "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}}}
    (cc / "s.jsonl").write_text(json.dumps(line) + "\n" + json.dumps(line) + "\n")  # streamed duplicate
    cx = tmp_path / "x"
    cx.mkdir()
    (cx / "r.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"type": "turn_context", "payload": {"model": "model-b"}},
        {"type": "event_msg", "timestamp": "2026-10-01T01:00:00Z", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": 50, "cached_input_tokens": 30, "output_tokens": 7}}}}]))
    oc = tmp_path / "o" / "message" / "ses"
    oc.mkdir(parents=True)
    (oc / "m.json").write_text(json.dumps({"role": "assistant", "modelID": "model-c", "cost": 0.5, "time": {"created": 1790000000000},
                                           "tokens": {"input": 1, "output": 2, "reasoning": 3, "cache": {"read": 4, "write": 5}}}))
    rows = usage.collect({"model-a": {"input": 1e6, "output": 0}}, [("claude-code", tmp_path / "c"), ("codex", cx),
                                                                       ("opencode", tmp_path / "o")], by="model")
    a, b, c = rows["claude-code model-a"], rows["codex model-b"], rows["opencode model-c"]
    assert (a.input, a.output, a.cache_read, a.cache_write, a.messages) == (10, 5, 100, 20, 1)
    assert a.cost == 10 and a.priced
    assert (b.input, b.cache_read, b.output) == (20, 30, 7) and not b.priced
    assert c.cost == 0.5 and c.output == 5
    assert "*" in usage.table(rows)


def test_parse_usage_formats():
    claude = json.dumps({"type": "result", "total_cost_usd": 0.12, "usage": {"input_tokens": 3, "cache_creation_input_tokens": 7,
                                                                              "output_tokens": 9, "cache_read_input_tokens": 40}})
    assert arena.parse_usage(claude) == {"input": 10, "output": 9, "cache_read": 40, "cost": 0.12}
    codex = "\n".join(json.dumps(x) for x in [{"type": "thread.started"}, {"type": "turn.completed", "usage": {
        "input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 8}}])
    assert arena.parse_usage(codex) == {"input": 40, "output": 8, "cache_read": 60, "cost": None}
    assert arena.parse_usage("plain text") == {"input": None, "output": None, "cache_read": None, "cost": None}


def test_arena_end_to_end_with_fake_agents(tmp_path):
    repo = tmp_path / "task" / "repo"
    repo.mkdir(parents=True)
    (repo / "cart.py").write_text("def total(p):\n    return sum(p)\n")
    (repo / "test_cart.py").write_text("from cart import total\ndef test_d():\n    assert total([10]) == 9\n")
    good = tmp_path / "task" / "good_agent.py"
    good.write_text(
        "import json\nopen('cart.py','w').write('def total(p):\\n    return sum(p) * 0.9\\n')\n"
        "print(json.dumps({'type':'result','total_cost_usd':0.01,'usage':{'input_tokens':100,'output_tokens':20}}))\n")
    lazy = tmp_path / "task" / "lazy_agent.py"
    lazy.write_text("print('I fixed it!')\n")
    spec = tmp_path / "task" / "task.toml"
    spec.write_text(f'''
[task]
name = "discount"
repo = "repo"
prompt = "apply a 10% discount"
check = "{Path(sys.executable).as_posix()} -m pytest -q"
runs = 2
[[agent]]
name = "good"
model = "m1"
cmd = ["{Path(sys.executable).as_posix()}", "{{here}}/good_agent.py", "{{prompt}}"]
[[agent]]
name = "lazy"
cmd = ["{Path(sys.executable).as_posix()}", "{{here}}/lazy_agent.py"]
[[agent]]
name = "missing"
cmd = ["definitely-not-installed-agent"]
''')
    out = tmp_path / "out"
    runs = arena.run_task(spec, out)
    good_runs = [r for r in runs if r.agent == "good"]
    assert len(good_runs) == 2 and all(r.passed and r.input_tokens == 100 and r.cost_usd == 0.01 for r in good_runs)
    assert all(not r.passed and r.input_tokens is None for r in runs if r.agent == "lazy")
    assert all(r.files_changed == 0 for r in runs if r.agent == "lazy")  # the check's .pytest_cache is not an edit
    assert all(r.files_changed == 1 for r in good_runs)
    assert any(r.agent == "missing" and "not installed" in r.note for r in runs)
    html = (out / "report.html").read_text()
    assert "100%" in html and "n/a" in html and "discount" in html
    assert (repo / "cart.py").read_text().endswith("sum(p)\n")  # template untouched
