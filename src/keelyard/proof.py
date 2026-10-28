"""Proof-of-work: the evidence an agent must collect before it may say "done"."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import trust
from .config import Config
from .locking import file_lock
from .proc import split_cmd
from .redact import redact
from .snapshot import Shadow

TAIL = 4000
NOT_EDITS = ("pre-rewind", "baseline", "stop")

TEST_CMD = re.compile(r"(?<![\w-])(pytest|py\.test|unittest|tox|nox|(npm|pnpm|yarn|bun)( run)? test|jest|vitest|mocha|"
                      r"go test|cargo test|dotnet test|mvn test|gradle\w* test|make test|rspec|phpunit)(?![\w-])")
_FAIL = re.compile(r"\b[1-9]\d* (failed|errors?|failures?)\b|^(FAIL|FAILED|--- FAIL)\b|panicked at|test result: FAILED",
                   re.M | re.I)
_PASS = re.compile(r"\b\d+ passed\b|^ok\b|^OK\b|test result: ok|^PASS\b|\b\d+ tests? passed", re.M)


def is_test_cmd(cmd: str) -> bool:
    return bool(TEST_CMD.search(cmd or ""))


_SIG_LINE = re.compile(r"^(FAILED|ERROR) \S+|^E\s+\S|^--- FAIL: \S+|panicked at|^\s*●\s|\b\w+(Error|Exception):|^FAIL\s+\S+")


def failure_signature(text: str) -> str:
    """What failed, without volatile details (numbers, addresses, timings), to spot the same failure twice."""
    keep = set()
    for ln in (text or "").splitlines():
        if _SIG_LINE.search(ln):
            keep.add(re.sub(r"0x[0-9a-fA-F]+|\d+(\.\d+)?", "N", ln.strip())[:200])
    return "\n".join(sorted(keep))


def parse_test_exit(output: str) -> int | None:
    """Exit status of a test run judged from its output: 0 passed, 1 failed, None unknown."""
    if _FAIL.search(output or ""):
        return 1
    if _PASS.search(output or ""):
        return 0
    return None


def summary_line(output: str) -> str:
    lines = [ln.strip(" =") for ln in (output or "").splitlines() if _PASS.search(ln) or _FAIL.search(ln)]
    return lines[-1][:160] if lines else ""


def detect_test_cmd(root: Path) -> str:
    if (root / "package.json").exists():
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            if "test" in pkg.get("scripts", {}):
                return "npm test --silent"
        except json.JSONDecodeError:
            pass
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    markers = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py")
    if ((root / "tests").is_dir() or (root / "test").is_dir() or any((root / m).exists() for m in markers)
            or any(root.glob("test_*.py")) or any(root.glob("*_test.py"))):
        py = "python" if shutil.which("python") else "python3"
        return f"{py} -m pytest -q"
    return ""


@dataclass
class Bundle:
    session: str
    task: str = ""
    started: float = field(default_factory=time.time)
    requirements: list[dict] = field(default_factory=list)
    test_runs: list[dict] = field(default_factory=list)
    screenshots: list[dict] = field(default_factory=list)
    blocks: int = 0
    gate: list[dict] = field(default_factory=list)  # every Stop decision: {"ts", "blocked", "open"}
    tampered: bool = False  # bundle.json failed its signature once: never proven again in this session
    test_acks: list[dict] = field(default_factory=list)  # agent-claimed reasons for changed tests, bound to a digest
    sig: str = ""

# Evidence levels, weakest first. Shown separately; only test-verified is something keelyard saw itself.
CLAIMED, COMMAND, VERIFIED = "agent-claimed", "command-succeeded", "test-verified"


class Proof:
    def __init__(self, cfg: Config, session: str):
        self.cfg = cfg
        self.session = session
        self.dir = cfg.state / "proof" / session
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "bundle.json"
        self.opts = cfg.section("proof")

    # -- persistence --------------------------------------------------------
    @contextmanager
    def _locked(self):
        """Hooks of parallel sessions and tool calls can fire at once: every read-modify-write holds this."""
        with file_lock(self.dir / "bundle.lock"):
            yield

    def load(self) -> Bundle:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                known = Bundle.__dataclass_fields__
                b = Bundle(**{k: v for k, v in d.items() if k in known})
                if not trust.verify(d):
                    b.tampered = True  # edited outside keelyard (or by an old version): evidence can't be trusted
                return b
            except (ValueError, TypeError):
                self.path.replace(self.path.with_suffix(".corrupt.json"))  # keep it for inspection, start clean
                b = Bundle(session=self.session)
                b.tampered = True
                return b
        return Bundle(session=self.session)

    def save(self, b: Bundle) -> None:
        d = {k: v for k, v in b.__dict__.items() if k != "sig"}
        d["sig"] = trust.sign(d)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _update(self, fn):
        with self._locked():
            b = self.load()
            out = fn(b)
            self.save(b)
            return out

    # -- evidence -----------------------------------------------------------
    def set_task(self, task: str) -> None:
        def fn(b):
            if not b.task:
                b.task = redact(task)[:4000]
            b.blocks = 0  # a new user prompt resets the gate's reminder budget
        self._update(fn)

    def add_requirements(self, items: list[str]) -> list[dict]:
        def fn(b):
            for text in items:
                text = text.strip()
                if text and not any(r["text"] == text for r in b.requirements):
                    b.requirements.append({"id": len(b.requirements) + 1, "text": redact(text), "done": False, "evidence": ""})
            return b.requirements
        return self._update(fn)

    def check(self, req_id: int, evidence: str) -> dict:
        if not evidence.strip():
            raise ValueError("evidence is required: say what you ran or observed")
        def fn(b):
            for r in b.requirements:
                if r["id"] == req_id:
                    r["done"], r["evidence"], r["ts"], r["level"] = True, redact(evidence)[:2000], time.time(), CLAIMED
                    return r
            raise ValueError(f"no requirement {req_id}")
        return self._update(fn)

    def run_tests(self, phase: str, cmd: str | None = None, timeout: int = 900) -> dict:
        if phase not in ("before", "after"):
            raise ValueError("phase must be 'before' or 'after'")
        cmd = cmd or self.opts.get("test_cmd") or detect_test_cmd(self.cfg.root)
        if not cmd:
            raise ValueError("no test command found for this project. "
                             "Hint: pass --cmd \"npm test\" (MCP: command=...) or set [proof].test_cmd in .keelyard/config.toml")
        t0 = time.time()
        try:
            p = subprocess.run(split_cmd(cmd), cwd=self.cfg.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)  # noqa: E501
            code, out = p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired as e:
            code, out = 124, f"timeout after {timeout}s\n{_text(e.stdout)}"
        except FileNotFoundError:
            exe = shlex.split(cmd)[0]
            code, out = 127, f"{exe}: command not found. Hint: install it, or set [proof].test_cmd in .keelyard/config.toml"
        except ValueError as e:  # unbalanced quotes in the command
            code, out = 2, f"could not parse test command {cmd!r}: {e}"
        run = {"phase": phase, "cmd": cmd, "exit": code, "ts": time.time(), "seconds": round(time.time() - t0, 2),
               "tail": redact(out[-TAIL:]), "level": VERIFIED, "tree": self._tree(), "ran": trust.tests_ran(out)}
        self._update(lambda b: b.test_runs.append(run))
        return run

    def repeat_hint(self, run: dict | None) -> str | None:
        """Only when needed: a failing run whose failure is identical to an earlier one made on different code.
        The agent's last change did not move the result; saying so once beats another blind edit."""
        if not run or run.get("exit") == 0:
            return None
        sig = failure_signature(run.get("tail", ""))
        if not sig:
            return None
        runs = self.load().test_runs
        same = [t for t in runs if t is not run and t.get("ts", 0) < run.get("ts", 0) and t.get("exit") not in (0, None)
                and t.get("tree") != run.get("tree") and failure_signature(t.get("tail", "")) == sig]
        if not same:
            return None
        return (f"keelyard: same failure as {len(same)} earlier attempt(s) on different code; your last change did not "
                "affect it. Re-read the error before editing again (undo attempts: keelyard rewind list).")

    def _tree(self) -> str | None:
        try:
            return Shadow(self.cfg).tree_hash()
        except Exception:  # noqa: BLE001 - no git: fall back to timestamps
            return None

    def record_shell_run(self, cmd: str, output: str, exit_code: int | None = None) -> dict | None:
        """A test command the agent ran in its own shell counts as a test run: no extra tool call needed."""
        if not is_test_cmd(cmd):
            return None
        code = exit_code if isinstance(exit_code, int) else parse_test_exit(output)
        if code is None:
            return None
        run = {"phase": "after" if self.last_edit_ts() is not None else "before", "cmd": cmd, "exit": code,
               "ts": time.time(), "seconds": 0, "tail": redact((output or "")[-TAIL:]), "source": "shell",
               "level": COMMAND, "tree": self._tree(), "ran": trust.tests_ran(output)}
        self._update(lambda b: b.test_runs.append(run))
        return run

    def auto_complete(self) -> bool:
        """With no written checklist, a passing test run after the last edit proves the task itself."""
        if not self.opts.get("auto_requirements", True):
            return False
        last = self.last_edit_ts()

        def fn(b):
            if b.requirements:
                return False
            after = [t for t in b.test_runs if t["phase"] == "after" and (last is None or t["ts"] >= last)]
            if not after or after[-1]["exit"] != 0:
                return False
            t = after[-1]
            b.requirements.append({"id": 1, "text": (b.task or "the task").strip().strip('"')[:200], "done": True,
                                   "evidence": f"`{t['cmd']}` exit {t['exit']}: {summary_line(t['tail'])}".strip(),
                                   "ts": time.time(), "auto": True, "level": t.get("level", COMMAND)})
            return True
        return self._update(fn)

    def screenshot(self, url: str | None = None) -> dict:
        url = url or self.opts.get("app_url")
        if not url:
            raise ValueError("no url given and [proof].app_url not set")
        out = self.dir / f"shot-{int(time.time() * 1000)}.png"
        err = _screenshot(url, out)
        if err:
            raise RuntimeError(err)
        import hashlib

        shot = {"url": url, "path": str(out), "ts": time.time(), "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
        self._update(lambda b: b.screenshots.append(shot))
        return shot

    def accept_tests(self, reason: str) -> dict:
        """Acknowledge changed tests (a real refactor). Recorded as agent-claimed and bound to the current test
        files: any further change to them needs a new acknowledgement. Shown in status and the report."""
        if len(reason.strip()) < 10:
            raise ValueError("say why the tests changed (at least a sentence)")
        inv = self.test_inventory()[1]
        ack = {"reason": redact(reason.strip())[:1000], "digest": trust.digest(inv), "ts": time.time(), "level": CLAIMED}
        self._update(lambda b: b.test_acks.append(ack))
        return ack

    def test_inventory(self) -> tuple[dict | None, dict]:
        """(test files before this session's first edit, test files now). before is None without a timeline."""
        sh = Shadow(self.cfg)
        steps = sh.steps()
        first = next((i for i, s in enumerate(steps) if s.session == self.session and s.tool not in NOT_EDITS), None)
        try:
            now = trust.inventory(sh.test_files_at(sh.tree_hash()))
        except Exception:  # noqa: BLE001
            return None, trust.inventory(trust.read_worktree(self.cfg.root, []))
        if first is None or first == 0:
            return None, now
        return trust.inventory(sh.test_files_at(steps[first - 1].sha)), now

    def weakened(self) -> list[str]:
        b = self.load()
        before, now = self.test_inventory()
        if before is None:
            return []
        runs = [t for t in b.test_runs if t.get("ran")]
        ran_before = next((t["ran"] for t in runs if t["phase"] == "before"), None)
        ran_after = next((t["ran"] for t in reversed(runs) if t["phase"] == "after"), None)
        found = trust.weakening(before, now, ran_before, ran_after)
        if found and any(a["digest"] == trust.digest(now) for a in b.test_acks):
            return []
        return found

    def levels(self) -> dict[str, int]:
        """How much of the evidence is at each level, for status and the report."""
        b = self.load()
        out = {CLAIMED: 0, COMMAND: 0, VERIFIED: 0}
        for r in b.requirements:
            if r.get("done"):
                out[r.get("level", CLAIMED)] += 1
        for t in b.test_runs:
            if t.get("exit") == 0:
                out[t.get("level", COMMAND)] += 1
        out[CLAIMED] += len(b.test_acks)
        return out

    # -- the gate -----------------------------------------------------------
    def last_edit_ts(self) -> float | None:
        edits = [s.ts for s in Shadow(self.cfg).steps() if s.session == self.session and s.tool not in NOT_EDITS]
        return max(edits) if edits else None

    def state(self) -> str:
        """'no_work' (nothing changed, no checklist), 'open' (gaps left) or 'proven'."""
        gaps = self.missing()
        if gaps:
            return "open"
        if self.last_edit_ts() is None and not self.load().requirements:
            return "no_work"
        return "proven"

    def missing(self) -> list[str]:
        """Return what still has to be proven. Empty list = proof complete."""
        b = self.load()
        last_edit = self.last_edit_ts()
        if b.tampered:
            return ["The proof file was changed outside keelyard (signature check failed), so none of its evidence counts. "
                    "Hint: `keelyard proof reset`, then run the tests again."]
        if last_edit is None:
            # nothing changed in this session: a pure Q&A turn needs no proof, but a checklist that
            # was written down still has to be ticked off
            return [f"Requirement #{r['id']} not verified: {r['text']}" for r in b.requirements if not r["done"]]
        gaps = []
        after = [t for t in b.test_runs if t["phase"] == "after" and t["ts"] >= last_edit]
        auto_ok = self.opts.get("auto_requirements", True) and after and after[-1]["exit"] == 0
        failing = bool(after) and after[-1]["exit"] != 0
        if not b.requirements and not auto_ok and not failing:  # a red test is the one thing to fix first
            gaps.append("No requirement checklist yet (proof_requirements, or `keelyard proof add`).")
        for r in b.requirements:
            if not r["done"]:
                gaps.append(f"Requirement #{r['id']} not verified: {r['text']}")
        if not after:
            gaps.append("No test run after the last edit (proof_run_tests phase='after', or `keelyard proof test after`).")
        elif after[-1]["exit"] != 0:
            gaps.append(f"The last test run failed with exit {after[-1]['exit']}. Fix it, then run the tests again.")
        else:
            tested = after[-1].get("tree")
            if tested:
                now = self._tree()
                if now and now != tested:
                    gaps.append(f"Files changed after the last passing test run (tested {tested[:8]}, now {now[:8]}). "
                                "Run the tests again.")
        gaps += [f"Tests weakened: {w}. Restore it, or if this is a real refactor: "
                 "`keelyard proof accept-tests \"<why>\"` (shown to the user as agent-claimed)." for w in self.weakened()]
        if self.opts.get("app_url"):
            shots = [s for s in b.screenshots if s["ts"] >= last_edit]
            if not shots:
                gaps.append("No screenshot of the running app since the last edit (proof_screenshot, or `keelyard proof shot`).")
        return gaps


def gate_status(cfg: Config) -> dict:
    """Is anything actually stopping the agent? 'hooked' only when a wired agent's Stop hook has fired.
    Anything else is shown as a warning, never as a green 'proven'."""
    from .adapters.install import AGENTS, global_agents, installed

    if not cfg.section("proof").get("gate", True):
        return {"state": "off", "why": "[proof].gate = false in .keelyard/config.toml"}
    wired = [a for a in AGENTS if installed(a, cfg.root)] + [a for a in (global_agents() or []) if a in AGENTS]
    if not wired:
        return {"state": "unhooked", "why": "no agent is wired, so nothing stops it from finishing. Hint: keelyard init"}
    wired = [a for a in dict.fromkeys(wired) if _stop_wired(a, cfg.root)]
    if not wired:
        return {"state": "unhooked", "why": "the Stop hook was removed from the agent config, so the gate never runs. "
                                            "Hint: keelyard install <agent>"}
    try:
        seen = json.loads((cfg.root / ".keelyard" / "hooks_seen.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    fired = [a for a in wired if seen.get(a)]  # any event from the same hook config proves the agent runs it
    if not fired:
        return {"state": "unverified", "wired": sorted(set(wired)),
                "why": "hooks are wired but no agent has called the gate yet (Codex: approve hooks with /hooks)"}
    return {"state": "hooked", "agents": fired}


def _stop_wired(agent: str, root: Path) -> bool:
    from .adapters.install import global_paths

    g = global_paths()
    files = {"claude": [root / ".claude" / "settings.json", root / ".claude" / "settings.local.json", g["claude_settings"]],
             "codex": [root / ".codex" / "hooks.json", g["codex_hooks"]],
             "opencode": [root / ".opencode" / "plugins" / "keelyard.js", g["opencode_plugin"]]}[agent]
    needle = "Stop" if agent == "opencode" else f"hook {agent} Stop"
    for f in files:
        try:
            if needle in f.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _screenshot(url: str, out: Path) -> str | None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=str(out), full_page=True)
            browser.close()
        return None
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 - surface any browser failure to the agent
        return f"screenshot failed: {e}"
    if shutil.which("npx"):
        p = subprocess.run(["npx", "--yes", "playwright", "screenshot", "--full-page", url, str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return None if p.returncode == 0 and out.exists() else f"npx playwright failed: {p.stderr[-500:]}"
    return "playwright not available: pip install 'keelyard[web]' && playwright install chromium"


def _text(data: str | bytes | None) -> str:
    """TimeoutExpired.stdout is bytes even when the process ran with text=True."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data or ""
