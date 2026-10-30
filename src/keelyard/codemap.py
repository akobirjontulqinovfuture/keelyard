"""Structure-first reading: outlines, a project map and symbol lookup.

Same goal as repomix --compress (MIT) and the "signatures only" modes in rtk and
claude-context-optimizer: let the agent see the shape of code before paying for
the bodies. Independent implementation: Python uses the stdlib ast module, other
languages use line-anchored definition patterns. Every entry carries path:line so
the agent can read exactly the range it needs.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path

from .config import VENDOR_DIRS
from .saver import approx_tokens

DEF_PATTERNS = {
    ".js": r"^\s*(export\s+)?(default\s+)?(async\s+)?(function\*?\s+\w+|class\s+\w+|(const|let)\s+\w+\s*=\s*(async\s*)?(\(|function))",
    ".ts": r"^\s*(export\s+)?(default\s+)?(abstract\s+)?(async\s+)?(function\*?\s+\w+|class\s+\w+|interface\s+\w+|type\s+\w+\s*=|enum\s+\w+|"
           r"(const|let)\s+\w+\s*(:\s*[^=]+)?=\s*(async\s*)?(\(|function))",
    ".go": r"^(func|type)\s",
    ".rs": r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(fn|struct|enum|trait|impl|mod|type)\b",
    ".java": r"^\s*(public|private|protected|static|final|abstract|\s)*(class|interface|enum|record|[\w<>\[\], ]+\s+\w+\s*\()",
    ".rb": r"^\s*(def|class|module)\s",
    ".php": r"^\s*(abstract\s+|final\s+)?(public\s+|private\s+|protected\s+)?(static\s+)?(function|class|interface|trait)\s",
    ".c": r"^[A-Za-z_][\w \*]*\s+\**\w+\s*\([^;]*$",
    ".swift": r"^\s*(public\s+|private\s+|internal\s+)?(func|class|struct|enum|protocol|extension)\s",
    ".kt": r"^\s*(fun|class|interface|object|data class|enum class)\s",
}
ALIASES = {".jsx": ".js", ".mjs": ".js", ".tsx": ".ts", ".h": ".c", ".cc": ".c", ".cpp": ".c", ".hpp": ".c"}


def _py_outline(src: str) -> list[str]:
    tree = ast.parse(src)
    out: list[str] = []

    def sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        ret = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
        pre = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
        return f"{pre} {fn.name}({ast.unparse(fn.args)}){ret}"

    def doc(node) -> str:
        d = ast.get_docstring(node)
        return f"  # {d.strip().splitlines()[0][:90]}" if d else ""

    def walk(body, indent: str) -> None:
        for n in body:
            if isinstance(n, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in n.bases)
                out.append(f"{n.lineno:>5} {indent}class {n.name}" + (f"({bases})" if bases else "") + doc(n))
                walk(n.body, indent + "    ")
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                deco = "".join(f"@{ast.unparse(d)} " for d in n.decorator_list)
                out.append(f"{n.lineno:>5} {indent}{deco}{sig(n)}{doc(n)}")
            elif not indent and isinstance(n, (ast.Assign, ast.AnnAssign)) and len(ast.unparse(n)) < 100:
                tgt = ast.unparse(n.targets[0] if isinstance(n, ast.Assign) else n.target)
                if tgt.isupper():
                    out.append(f"{n.lineno:>5} {ast.unparse(n)}")

    walk(tree.body, "")
    return out


MAX_OUTLINE = 400


def outline(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return f"{path.name}: binary file ({len(data):,} bytes), no outline"
    src = data.decode(errors="replace")
    ext = ALIASES.get(path.suffix, path.suffix)
    lines: list[str]
    if ext == ".py":
        try:
            lines = _py_outline(src)
        except (SyntaxError, ValueError, RecursionError):  # py2 code, null bytes, absurd nesting
            rx = re.compile(r"^\s*(async\s+def|def|class)\s+\w+")
            lines = [f"{i:>5} {ln.rstrip()[:160]}" for i, ln in enumerate(src.splitlines(), 1) if rx.search(ln)]
    else:
        pat = DEF_PATTERNS.get(ext)
        if not pat:
            return f"(no outline rules for {path.suffix}; {len(src.splitlines())} lines)"
        rx = re.compile(pat)
        lines = [f"{i:>5} {ln.rstrip()[:160]}" for i, ln in enumerate(src.splitlines(), 1) if rx.search(ln)]
    if len(lines) > MAX_OUTLINE:
        lines = lines[:MAX_OUTLINE] + [f"  ... {len(lines) - MAX_OUTLINE} more definitions (use find_symbol)"]
    head = f"{path.name}: {len(src.splitlines())} lines, ~{approx_tokens(src)} tokens; outline ~{approx_tokens(chr(10).join(lines))}"
    return head + "\n" + "\n".join(lines)


def tracked_files(root: Path) -> list[str]:
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard", "-z"],
                           cwd=root, capture_output=True)
        ok = p.returncode == 0
    except FileNotFoundError:  # git not installed
        ok = False
    if ok:
        files = [f for f in p.stdout.decode("utf-8", "surrogateescape").split("\0") if f]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in VENDOR_DIRS and not d.startswith("."))
            rel = Path(dirpath).relative_to(root)
            files += [(rel / n).as_posix() for n in sorted(filenames)]
    return [f for f in files if not f.startswith(".keelyard/") and not f.endswith("/")]


AGENT_FILES = {".opencode", ".claude", ".codex", ".keelyard", "AGENTS.md", "CLAUDE.md", "opencode.json", ".mcp.json"}


def project_map(root: Path, max_dirs: int = 60) -> str:
    dirs: dict[str, list[tuple[str, int]]] = OrderedDict()
    total = 0
    for rel in sorted(tracked_files(root)):
        if rel.split("/")[0] in AGENT_FILES:
            continue
        p = root / rel
        try:
            size = p.stat().st_size
        except OSError:
            continue
        total += size
        d, _, name = rel.rpartition("/")
        dirs.setdefault(d or ".", []).append((name, size))
    out = [f"{sum(len(v) for v in dirs.values())} files, ~{total // 4} tokens if read in full"]
    for d, files in list(dirs.items())[:max_dirs]:
        files.sort(key=lambda x: -x[1])
        big = ", ".join(f"{n} ~{s // 4}t" for n, s in files[:6])
        out.append(f"{d}/ ({len(files)}): {big}" + (" …" if len(files) > 6 else ""))
    if len(dirs) > max_dirs:
        out.append(f"… {len(dirs) - max_dirs} more dirs")
    return "\n".join(out)


def find_symbol(root: Path, name: str, limit: int = 20) -> str:
    if not re.fullmatch(r"[A-Za-z_][\w.]*", name):
        return "symbol must be an identifier"
    short = name.split(".")[-1]
    defs = re.compile(rf"^\s*(export\s+)?(default\s+)?(pub(\([^)]*\))?\s+)?(async\s+)?"
                      rf"(def|class|function\*?|fn|func|struct|enum|trait|interface|type|const|let|var|module)\s+(\([^)]*\)\s*)?{re.escape(short)}\b")
    hits = []
    for rel in tracked_files(root):
        p = root / rel
        if p.suffix not in (".py", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".java", ".c", ".h", ".cpp") and \
                ALIASES.get(p.suffix, p.suffix) not in (".js", ".ts"):
            continue
        try:
            for i, ln in enumerate(p.read_text(errors="replace", encoding="utf-8").splitlines(), 1):
                if short in ln and defs.search(ln):
                    hits.append(f"{rel}:{i}: {ln.strip()[:160]}")
                    if len(hits) >= limit:
                        return "\n".join(hits)
        except OSError:
            continue
    return "\n".join(hits) or f"no definition of {name} found"


# --------------------------------------------------------------------------- navigation with paging
CODE_SUFFIXES = (".py", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".java", ".c", ".h", ".cpp", ".js", ".ts",
                 ".jsx", ".tsx", ".mjs", ".cjs")
TESTISH = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")


def _code_files(root: Path) -> list[str]:
    return [f for f in tracked_files(root) if f.endswith(CODE_SUFFIXES)]


def _lines(root: Path, rel: str) -> list[str]:
    try:
        return (root / rel).read_text(errors="replace", encoding="utf-8").splitlines()
    except OSError:
        return []


def page(items: list[str], offset: int, size: int) -> dict:
    """One page of ranked results, and how to get the next one. Nothing is dropped silently."""
    offset = max(0, offset)
    chunk = items[offset:offset + size]
    rest = max(0, len(items) - offset - len(chunk))
    out = {"total": len(items), "offset": offset, "items": chunk, "remaining": rest}
    if rest:
        out["next_offset"] = offset + len(chunk)
    return out


def find_symbol_ranked(root: Path, name: str) -> list[str]:
    """Every definition of `name`, best first: exact name in source files, then tests, then partial names."""
    if not re.fullmatch(r"[A-Za-z_][\w.]*", name):
        return []
    short = name.split(".")[-1]
    defs = re.compile(rf"^\s*(export\s+)?(default\s+)?(pub(\([^)]*\))?\s+)?(async\s+)?"
                      rf"(def|class|function\*?|fn|func|struct|enum|trait|interface|type|const|let|var|module)\s+"
                      rf"(\([^)]*\)\s*)?(\w*{re.escape(short)}\w*)\b")
    scored = []
    for rel in _code_files(root):
        for i, ln in enumerate(_lines(root, rel), 1):
            if short not in ln:
                continue
            m = defs.search(ln)
            if not m:
                continue
            exact = m.group(8) == short
            score = (0 if exact else 2) + (1 if TESTISH.search(rel) else 0)
            scored.append((score, rel, i, f"{rel}:{i}: {ln.strip()[:160]}"))
    return [s[3] for s in sorted(scored, key=lambda s: (s[0], s[1], s[2]))]


def references(root: Path, name: str) -> list[str]:
    """Call sites and uses of `name` (definitions excluded), source files before tests."""
    short = name.split(".")[-1]
    if not re.fullmatch(r"\w+", short):
        return []
    word = re.compile(rf"(?<![\w.]){re.escape(short)}\b|\.{re.escape(short)}\b")
    defn = re.compile(rf"^\s*(export\s+)?(async\s+)?(def|class|function|fn|func|struct|interface|type)\s+{re.escape(short)}\b")
    src: list[str] = []
    tests: list[str] = []
    for rel in _code_files(root):
        for i, ln in enumerate(_lines(root, rel), 1):
            if short in ln and word.search(ln) and not defn.search(ln) and not ln.lstrip().startswith(("#", "//")):
                (tests if TESTISH.search(rel) else src).append(f"{rel}:{i}: {ln.strip()[:140]}")
    return src + tests


_PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w, *()]+)|import\s+([\w., ]+))")
_JS_IMPORT = re.compile(r"""(?:from\s+|require\(\s*|import\s*\(\s*|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""")


def _module_candidates(rel: str, spec: str) -> list[str]:
    base = Path(rel).parent
    if spec.startswith("."):  # python relative import
        dots = len(spec) - len(spec.lstrip("."))
        for _ in range(dots - 1):
            base = base.parent
        mod = spec.lstrip(".").replace(".", "/")
        stem = (base / mod).as_posix() if mod else base.as_posix()
    else:
        stem = spec.replace(".", "/")
    stem = stem.lstrip("./") if stem not in (".", "") else ""
    return [f"{stem}.py", f"{stem}/__init__.py", f"src/{stem}.py", f"src/{stem}/__init__.py"]


def imports_of(root: Path, rel: str, files: set[str] | None = None) -> list[str]:
    """Project files that `rel` imports (stdlib and packages left out)."""
    files = files if files is not None else set(tracked_files(root))
    found = []
    for ln in _lines(root, rel):
        m = _PY_IMPORT.match(ln) if rel.endswith(".py") else None
        if m:
            specs = [m.group(1)] if m.group(1) is not None else [s.strip().split(" ")[0] for s in (m.group(3) or "").split(",")]
            if m.group(1) is not None and m.group(1).strip(".") == "" and m.group(2):  # from . import a, b
                specs = [m.group(1) + n.strip(" ()") for n in m.group(2).split(",") if n.strip(" ()") not in ("", "*")]
            for spec in specs:
                for c in _module_candidates(rel, spec):
                    if c in files:
                        found.append(c)
                        break
        for js in _JS_IMPORT.findall(ln):
            stem = os.path.normpath((Path(rel).parent / js).as_posix()).replace("\\", "/")
            for c in (stem, *(stem + e for e in (".js", ".ts", ".jsx", ".tsx", ".mjs")), stem + "/index.js", stem + "/index.ts"):
                if c in files:
                    found.append(c)
                    break
    return list(dict.fromkeys(found))


def related(root: Path, rel: str) -> dict:
    """What to look at next for one file: what it imports, who imports it, and the tests that cover it."""
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    all_files = tracked_files(root)
    files = set(all_files)
    if rel not in files:
        raise FileNotFoundError(f"{rel}: not a tracked file. Hint: keelyard code map")
    code = [f for f in all_files if f.endswith(CODE_SUFFIXES)]
    importers = [f for f in code if f != rel and rel in imports_of(root, f, files)]
    stem = Path(rel).stem
    name_tests = {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec", f"{stem}Test", f"{stem}_spec"}
    tests = [f for f in code if TESTISH.search(f) and (Path(f).stem in name_tests or f in importers)]
    return {"file": rel, "imports": imports_of(root, rel, files), "imported_by": [f for f in importers if f not in tests],
            "tests": tests}
