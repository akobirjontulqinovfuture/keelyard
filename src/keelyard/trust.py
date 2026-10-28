"""What makes a proof trustworthy: signed bundles, and spotting tests that were weakened to get green.

Signing: every proof bundle carries an HMAC made with a key kept outside the project
(<data home>/keelyard/proof.key, readable only by you). An agent that edits bundle.json by hand
produces a bundle that no longer verifies, and keelyard then never reports it as proven. This stops
casual forging. It is not a sandbox: a process running as you can read the key too (see docs/guarantees.md).

Test weakening: the test files and test config as they were before the session's first edit are compared
with the current ones. Deleted test files, fewer test functions or assertions, new skip/xfail/only markers,
changed test config, and fewer tests in the run all keep the proof open until fixed or acknowledged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from .config import data_home

# -- signing -------------------------------------------------------------------


def _key() -> bytes:
    p = data_home() / "proof.key"
    if not p.exists():
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, os.urandom(32).hex().encode())
        finally:
            os.close(fd)
    return p.read_bytes().strip()


def sign(d: dict) -> str:
    body = json.dumps({k: v for k, v in d.items() if k != "sig"}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


def verify(d: dict) -> bool:
    sig = d.get("sig")
    return isinstance(sig, str) and hmac.compare_digest(sig, sign(d))


# -- test inventory ---------------------------------------------------------------

TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/.+\.(py|[cm]?[jt]sx?|go|rs|rb|php|java|kt|cs)$|(^|/)test_[^/]*\.py$|"
                       r"_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|_spec\.rb$|Test\.(java|kt|cs)$")
CONFIG_FILE = re.compile(r"(^|/)(pytest\.ini|tox\.ini|setup\.cfg|conftest\.py|noxfile\.py|jest\.config\.[\w.]+|"
                         r"vitest\.config\.[\w.]+|\.mocharc[\w.]*|karma\.conf\.[\w.]+|phpunit\.xml(\.dist)?|\.rspec)$")
PARTIAL_CONFIG = {"pyproject.toml": re.compile(r"^\[tool\.(pytest|coverage)[^\]]*\][\s\S]*?(?=^\[|\Z)", re.M),
                  "package.json": None, "Cargo.toml": re.compile(r"^\[\[?test[^\]]*\]\]?[\s\S]*?(?=^\[|\Z)", re.M)}
TEST_DEF = re.compile(r"^\s*(async\s+)?def test\w*\s*\(|^\s*(it|test)(\.each\([^)]*\))?\s*\(|^\s*func Test\w+\s*\(|"
                      r"#\[(tokio::)?test\]|^\s*@Test\b|^\s*\[(Fact|Test|TestMethod)\]|^\s*public function test\w+", re.M)
ASSERT = re.compile(r"\bassert\w*\b|\bexpect\s*\(|\.should\b|\bt\.(Error|Errorf|Fatal|Fatalf)\(|\bassert(_eq|_ne)?!\(|"
                    r"\bself\.assert\w+\(|\bAssert\.\w+\(|\brequire\.\w+\(")
SKIP = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail|importorskip)\(|@unittest\.(skip\w*|expectedFailure)|"
                  r"\b(it|test|describe|context)\.(skip|only|todo|failing)\b|\b[xf](it|describe|test)\s*\(|\bt\.Skip(Now|f)?\(|"
                  r"#\[ignore\]|@(Disabled|Ignore)\b|\bmarkTestSkipped\(")
RAN = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)\b|\bTests:\s+.*?(\d+) total|"
                 r"test result: \w+\. (\d+) passed; (\d+) failed|^ok\s+\S+|^--- (PASS|FAIL)", re.M)


def _config_part(rel: str, text: str) -> str | None:
    name = rel.rsplit("/", 1)[-1]
    if CONFIG_FILE.search(rel):
        return text
    if name in PARTIAL_CONFIG and "/" not in rel:
        if name == "package.json":
            try:
                pkg = json.loads(text)
            except ValueError:
                return text
            keep = {k: pkg.get(k) for k in ("jest", "mocha", "vitest") if k in pkg}
            keep["scripts"] = {k: v for k, v in (pkg.get("scripts") or {}).items() if "test" in k}
            return json.dumps(keep, sort_keys=True)
        pat = PARTIAL_CONFIG[name]
        if pat is None:
            return text
        return "\n".join(m.group(0) for m in pat.finditer(text))
    return None


def is_tracked_name(rel: str) -> bool:
    return bool(TEST_FILE.search(rel) or CONFIG_FILE.search(rel) or rel in PARTIAL_CONFIG)


def inventory(files: dict[str, str]) -> dict:
    """{path: text} of test-related files -> counts per test file and a digest per test-config file."""
    tests, configs = {}, {}
    for rel, text in files.items():
        part = _config_part(rel, text)
        if part is not None:
            configs[rel] = hashlib.sha256(part.encode("utf-8", "replace")).hexdigest()[:16]
        if TEST_FILE.search(rel) and not rel.endswith("conftest.py"):
            tests[rel] = {"tests": len(TEST_DEF.findall(text)), "asserts": len(ASSERT.findall(text)),
                          "skips": len(SKIP.findall(text))}
    return {"tests": tests, "configs": configs}


def digest(inv: dict) -> str:
    return hashlib.sha256(json.dumps(inv, sort_keys=True).encode()).hexdigest()[:16]


def tests_ran(output: str) -> int | None:
    """How many tests a run executed, from its summary line; None when the format is unknown."""
    total = None
    for m in RAN.finditer(output or ""):
        if m.group(1):
            total = (total or 0) + int(m.group(1)) if m.group(2) != "skipped" else (total or 0)
        elif m.group(3):
            total = int(m.group(3))
        elif m.group(4):
            total = int(m.group(4)) + int(m.group(5))
    return total


def weakening(before: dict, after: dict, ran_before: int | None = None, ran_after: int | None = None) -> list[str]:
    out = []
    for rel, b in before["tests"].items():
        a = after["tests"].get(rel)
        if a is None:
            out.append(f"test file deleted: {rel}")
            continue
        if a["tests"] < b["tests"]:
            out.append(f"tests removed in {rel}: {b['tests']} -> {a['tests']}")
        if a["asserts"] < b["asserts"]:
            out.append(f"assertions removed in {rel}: {b['asserts']} -> {a['asserts']}")
        if a["skips"] > b["skips"]:
            out.append(f"skip/xfail/only added in {rel}: {b['skips']} -> {a['skips']}")
    for rel, a in after["tests"].items():
        if rel not in before["tests"] and a["skips"]:
            out.append(f"skip/xfail/only in new test file {rel}")
    for rel, h in before["configs"].items():
        if after["configs"].get(rel) != h:
            out.append(f"test config {'deleted' if rel not in after['configs'] else 'changed'}: {rel}")
    for rel in after["configs"]:
        if rel not in before["configs"]:
            out.append(f"test config added: {rel}")
    if ran_before and ran_after is not None and ran_after < ran_before:
        out.append(f"fewer tests ran: {ran_before} -> {ran_after}")
    return out


def read_worktree(root: Path, names: list[str]) -> dict[str, str]:
    out = {}
    for rel in names:
        if is_tracked_name(rel):
            try:
                out[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out
