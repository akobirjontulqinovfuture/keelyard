"""Rewind: a shadow git repository that snapshots the worktree after agent tool calls.

The shadow repo lives in .keelyard/shadow.git and never touches the user's own
.git (no commits, no stash, no index changes there). It honours .gitignore.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from .config import VENDOR_DIRS, Config
from .locking import file_lock


class SnapshotError(RuntimeError):
    pass


@dataclass
class Step:
    step: int
    sha: str
    tool: str
    session: str
    ts: float
    files: list[str]
    note: str = ""
    skipped: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


# keelyard's own wiring files are not your work: rewinding must never remove or revert them.
EXCLUDE = (".keelyard/\n.git/\nnode_modules/\n.venv/\n__pycache__/\n"
           ".claude/skills/keelyard/\n.claude/commands/keelyard/\n.opencode/plugin/keelyard.js\n.opencode/plugins/keelyard.js\n"
           ".opencode/command/keelyard-*.md\n")

def _skill_exclude() -> str:
    """Slash-command copies of shipped skills: rewind must not revert install wiring."""
    try:
        from .skills import shipped_skills
        lines = []
        for s in shipped_skills():
            lines += [f".claude/commands/{s.name}.md", f".codex/prompts/{s.name}.md",
                      f".opencode/command/{s.name}.md"]
        return "".join(f"{ln}\n" for ln in lines)
    except (OSError, ValueError):
        return ""




# Secrets never enter a snapshot (and so never a diff, report or rewind). Example files stay in.
SECRETS = (".env\n.env.*\n!.env.example\n!.env.sample\n!.env.template\n*.pem\n*.key\n*.p12\n*.pfx\n*.jks\n*.keystore\n"
           "id_rsa*\nid_dsa*\nid_ecdsa*\nid_ed25519*\n.npmrc\n.pypirc\n.netrc\n.git-credentials\n.dockercfg\n"
           "credentials.json\nservice-account*.json\n*.tfstate\n*.tfstate.*\nsecrets/\n.secrets/\n")


WIRING = (".claude/", ".codex/", ".opencode/", "AGENTS.md", "CLAUDE.md", "opencode.json", ".mcp.json")


def user_first(files: list[str]) -> list[str]:
    """Your own files first, agent wiring (.claude/, AGENTS.md, ...) last, for display."""
    return sorted(files, key=lambda f: f.startswith(WIRING))


class Shadow:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.root
        state = cfg.root / ".keelyard"  # not cfg.state: that creates the folder, even in $HOME
        self.gitdir = state / "shadow.git"
        self.log = state / "steps.jsonl"
        self.lock = state / "timeline.lock"

    # -- plumbing -----------------------------------------------------------
    def _git(self, *args: str, input: bytes | None = None, check: bool = True) -> bytes:
        env = {
            **os.environ,
            "GIT_DIR": str(self.gitdir),
            "GIT_WORK_TREE": str(self.root),
            "GIT_AUTHOR_NAME": "keelyard",
            "GIT_AUTHOR_EMAIL": "keelyard@localhost",
            "GIT_COMMITTER_NAME": "keelyard",
            "GIT_COMMITTER_EMAIL": "keelyard@localhost",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        env.pop("GIT_INDEX_FILE", None)
        try:
            p = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=self.root, env=env, input=input, capture_output=True)
        except FileNotFoundError:
            raise SnapshotError("git is not installed. Hint: install git (https://git-scm.com) - rewind needs it.") from None
        if check and p.returncode != 0:
            raise SnapshotError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
        return p.stdout

    def _names(self, *args: str) -> list[str]:
        return [f for f in self._git(*args, "-z").decode("utf-8", "surrogateescape").split("\0") if f]

    def excludes(self) -> str:
        """EXCLUDE + secrets (unless [rewind].include_secrets) + your own [rewind].exclude patterns."""
        opts = self.cfg.section("rewind")
        extra = "".join(f"{p}\n" for p in opts.get("exclude", []) or [])
        return EXCLUDE + _skill_exclude() + ("" if opts.get("include_secrets", False) else SECRETS) + extra

    def _ensure(self) -> None:
        self.cfg.require_project()
        if (self.gitdir / "HEAD").exists():
            excl = self.gitdir / "info" / "exclude"
            try:
                want = self.excludes()
                if excl.read_text(encoding="utf-8") != want:  # older timelines or a config change: new exclusions
                    excl.write_text(want, encoding="utf-8")
            except OSError:
                pass
            return
        self.cfg.state  # noqa: B018 - creates .keelyard with its .gitignore
        with file_lock(self.lock):
            if not (self.gitdir / "HEAD").exists():
                self._init_repo()

    def _init_repo(self) -> None:
        self.gitdir.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "core.autocrlf", "false")
        self._git("config", "gc.auto", "0")
        excl = self.gitdir / "info" / "exclude"
        excl.parent.mkdir(exist_ok=True)
        excl.write_text(self.excludes(), encoding="utf-8")

    def steps(self) -> list[Step]:
        if not self.log.exists():
            return []
        out = []
        known = {f.name for f in fields(Step)}
        for line in self.log.read_text(errors="replace", encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                out.append(Step(**{k: v for k, v in d.items() if k in known}))
            except (ValueError, TypeError):
                continue  # a torn or corrupted line must never break the timeline
        return out

    def get(self, n: int) -> Step:
        for s in self.steps():
            if s.step == n:
                return s
        last = self.steps()
        span = f"steps 1-{last[-1].step} exist" if last else "the timeline is empty"
        raise SnapshotError(f"no step {n} ({span}). Hint: run `keelyard rewind list`.")

    # -- operations ---------------------------------------------------------
    def snapshot(self, tool: str = "manual", session: str = "", note: str = "") -> Step | None:
        """Record the worktree. Returns None when nothing changed since the last step."""
        self._ensure()
        with file_lock(self.lock):
            return self._snapshot(tool, session, note)

    def _stage(self, parent: str | None) -> list[str]:
        """Stage the worktree into the shadow index; return paths skipped for size.

        Only paths that changed since the last snapshot are inspected, so a 7,000-file repo
        costs one `git add` plus a handful of stat calls per agent edit."""
        limit = float(self.cfg.section("rewind").get("max_file_mb", 20)) * 1024 * 1024
        self._git("add", "-A", "--ignore-errors", ".", check=False)
        # a file that became excluded (a secret, a new pattern) must leave the index, not linger from an older step
        stale = self._names("ls-files", "--cached", "--ignored", "--exclude-standard")
        if stale:
            self._git("rm", "--cached", "-q", "--ignore-unmatch", "--", *stale, check=False)
        nested = []
        skipped = []
        changed = (self._names("diff", "--cached", "--name-only", "--no-renames", parent) if parent
                   else self._names("ls-files", "--cached"))
        # whatever `git add` refused (empty nested repositories) is still untracked: usually nothing
        changed += self._names("ls-files", "--others", "--directory", "--exclude-standard")
        for rel in changed:
            path = self.root / rel
            if path.is_dir() and path.name not in VENDOR_DIRS:
                # `git add` skipped this dir: either it is (or holds) a nested repository, or all of it is ignored
                for dirpath, dirnames, _ in os.walk(path):
                    if (Path(dirpath) / ".git").exists():
                        nested.append(Path(dirpath).relative_to(self.root).as_posix())
                        dirnames[:] = []
                    else:
                        dirnames[:] = [d for d in dirnames if d not in VENDOR_DIRS]
                continue
            if path.is_dir():
                continue
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size > limit:
                    skipped.append(rel)
            except OSError:
                continue
        # nested repositories (monorepos, vendored checkouts) would become opaque gitlinks: add their files
        for sub in nested:
            if any(part in VENDOR_DIRS for part in Path(sub).parts):
                continue
            self._git("rm", "--cached", "-q", "--ignore-unmatch", "--", sub, check=False)
            entries = []
            for dirpath, dirnames, filenames in os.walk(self.root / sub):
                dirnames[:] = [d for d in dirnames if d not in VENDOR_DIRS]
                for name in filenames:
                    p = Path(dirpath) / name
                    rel = p.relative_to(self.root).as_posix()
                    try:
                        if p.is_symlink() or p.stat().st_size > limit:
                            skipped.append(rel) if not p.is_symlink() else None
                            continue
                    except OSError:
                        continue
                    entries.append(rel)
            if entries:  # honour .gitignore and keelyard's excludes inside nested repositories too
                out = self._git("check-ignore", "--no-index", "--stdin", "-z", input="\0".join(entries).encode(), check=False)
                ignored = set(out.decode("utf-8", "surrogateescape").split("\0"))
                entries = [e for e in entries if e not in ignored]
            if entries:
                shas = self._git("hash-object", "-w", "--stdin-paths", input="\n".join(entries).encode()).decode().split()
                info = "".join(f"100644 {sha}\t{rel}\n" for sha, rel in zip(shas, entries, strict=True))
                self._git("update-index", "--add", "--index-info", input=info.encode())
        if skipped:
            self._git("rm", "--cached", "-q", "--ignore-unmatch", "--", *skipped, check=False)
        return sorted(skipped)

    def _snapshot(self, tool: str, session: str, note: str) -> Step | None:
        prev = self.steps()
        parent = prev[-1].sha if prev else None
        skipped = self._stage(parent)
        tree = self._git("write-tree").decode().strip()
        if parent:
            prev_tree = self._git("rev-parse", f"{parent}^{{tree}}").decode().strip()
            if prev_tree == tree:
                return None
        args = ["commit-tree", tree, "-m", f"{tool} {note}".strip()]
        if parent:
            args[2:2] = ["-p", parent]
        sha = self._git(*args).decode().strip()
        self._git("update-ref", "refs/heads/timeline", sha)
        if parent:
            files = self._names("diff", "--name-only", parent, sha)
        else:
            files = self._names("ls-tree", "-r", "--name-only", sha)
        step = Step(prev[-1].step + 1 if prev else 1, sha, tool, session, time.time(), files, note, skipped)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(step.to_json() + "\n")
        return step

    def tree_hash(self) -> str:
        """Content hash of the worktree as keelyard sees it (gitignore and secret excludes applied).
        Two equal hashes mean no tracked byte changed in between."""
        self._ensure()
        with file_lock(self.lock):
            prev = self.steps()
            self._stage(prev[-1].sha if prev else None)
            return self._git("write-tree").decode().strip()

    def test_files_at(self, rev: str) -> dict[str, str]:
        """Test files and test config at a commit or tree, as {path: text}."""
        from .trust import is_tracked_name

        out = {}
        for rel in self._names("ls-tree", "-r", "--name-only", rev):
            if is_tracked_name(rel):
                out[rel] = self._git("cat-file", "blob", f"{rev}:{rel}", check=False).decode("utf-8", "replace")
        return out

    def diff(self, n: int) -> str:
        s = self.get(n)
        parent = self._git("rev-list", "--parents", "-n1", s.sha).decode().split()[1:]
        if not parent:
            return self._git("show", "--stat", "--format=", s.sha).decode()
        return self._git("diff", parent[0], s.sha).decode(errors="replace")

    def rewind(self, n: int) -> dict:
        """Restore the worktree to step n. A safety snapshot is taken first, so rewind is undoable."""
        target = self.get(n)
        self._ensure()
        with file_lock(self.lock):
            return self._rewind(n, target)

    @property
    def journal(self) -> Path:
        return self.root / ".keelyard" / "rewind.journal"

    def interrupted(self) -> dict | None:
        """A rewind that started and never finished (crash, Ctrl-C, power loss) leaves its journal behind."""
        try:
            return json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _rewind(self, n: int, target: Step, recovering: bool = False) -> dict:
        if not recovering and self.interrupted():
            raise SnapshotError("an earlier rewind did not finish. Hint: run `keelyard rewind recover` first.")
        safety = self._snapshot("pre-rewind", "", f"before rewind to {n}")
        cur_step = safety or self.steps()[-1]
        current = cur_step.sha
        added = self._names("diff", "--name-only", "--diff-filter=A", target.sha, current)
        # files the snapshot could not hold (ignored, secret, too big) but the restore would overwrite or delete:
        # copy them aside first, so a rewind never destroys work that exists nowhere else
        in_safety = set(self._names("ls-tree", "-r", "--name-only", current))
        touched = set(self._names("ls-tree", "-r", "--name-only", target.sha)) | set(added)
        backup = self.root / ".keelyard" / "backup" / time.strftime("%Y%m%d-%H%M%S")
        saved = []
        for rel in sorted(touched - in_safety):
            p = self.root / rel
            if p.is_file() and not p.is_symlink():
                dest = backup / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
                saved.append(rel)
        tmp = self.journal.with_suffix(".tmp")
        tmp.write_text(json.dumps({"target": n, "safety_step": cur_step.step, "safety_sha": current, "ts": time.time(),
                                   "backup": str(backup) if saved else ""}), encoding="utf-8")
        os.replace(tmp, self.journal)
        for rel in added:
            p = self.root / rel
            if p.is_file() or p.is_symlink():
                p.unlink()
        self._git("read-tree", target.sha)
        self._git("checkout-index", "-a", "-f")
        after = self._snapshot("rewind", "", f"restored step {n}")
        self.journal.unlink(missing_ok=True)
        return {
            "restored": n,
            "removed": added,
            "safety_step": cur_step.step,
            "new_step": after.step if after else None,
            "backed_up": saved,
            "backup_dir": str(backup) if saved else "",
        }

    def recover(self) -> dict:
        """Finish an interrupted rewind by putting back the state saved just before it started."""
        j = self.interrupted()
        if not j:
            raise SnapshotError("no interrupted rewind to recover.")
        self._ensure()
        with file_lock(self.lock):
            res = self._rewind(j["safety_step"], self.get(j["safety_step"]), recovering=True)
        res["recovered_from"] = j
        return res

    def fork(self, n: int, dest: Path) -> Path:
        """Materialise step n into a separate directory to try another approach."""
        target = self.get(n)
        dest = dest.resolve()
        if dest.exists() and any(dest.iterdir()):
            raise SnapshotError(f"{dest} is not empty")
        dest.mkdir(parents=True, exist_ok=True)
        data = self._git("archive", "--format=tar", target.sha)
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(dest, filter="data")
        return dest
