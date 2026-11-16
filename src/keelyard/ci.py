"""Markdown proof report for a PR, plus a test-inventory diff against a git base.

`keelyard ci` writes a markdown report from the local session (proof, audit, risk, spend).
`.keelyard` is gitignored, so a GitHub Actions runner only sees that state if you
upload it or run keelyard on the machine that had the agent session.

`keelyard ci --diff-only` compares today's test files with a git ref (the PR base, `main`,
or `--base`) and does not need a keelyard session. That is the check that belongs
in CI: an agent PR that deletes or skips tests is flagged even when no proof bundle
was uploaded.

`--comment` posts or updates a PR comment when GITHUB_TOKEN (or `gh`) is available.
The token is never printed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__, watch
from .config import VENDOR_DIRS, Config
from .trust import inventory, is_tracked_name, read_worktree, weakening

MARKER = "<!-- keelyard-ci -->"


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "git failed").strip())
    return r.stdout


def default_base(root: Path) -> str | None:
    env = os.environ.get("GITHUB_BASE_SHA") or os.environ.get("GITHUB_BASE_REF")
    if env:
        return env
    for ref in ("origin/main", "origin/master", "main", "master"):
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", ref],
                           capture_output=True, text=True)
        if r.returncode == 0:
            mb = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD", ref],
                                capture_output=True, text=True)
            return (mb.stdout.strip() or r.stdout.strip()) if mb.returncode == 0 else r.stdout.strip()
    return None


def files_at(root: Path, ref: str) -> dict[str, str]:
    names = [n for n in _git(root, "ls-tree", "-r", "--name-only", "--", ref).splitlines() if is_tracked_name(n)]
    out = {}
    for rel in names:
        show = subprocess.run(["git", "-C", str(root), "show", "--", f"{ref}:{rel}"], capture_output=True)
        if show.returncode == 0:
            out[rel] = show.stdout.decode("utf-8", "replace")
    return out


def inventory_diff(cfg: Config, base: str | None = None) -> dict:
    ref = base or default_base(cfg.root)
    if not ref:
        return {"base": None, "weakened": [], "ok": True, "why": "no git base ref found"}
    try:
        before = inventory(files_at(cfg.root, ref))
    except RuntimeError as e:
        # A requested base that cannot be read must not look like a clean pass.
        return {"base": ref, "weakened": [], "ok": False, "why": str(e)}
    now_names = set(before["tests"]) | set(before["configs"])
    skip = VENDOR_DIRS | {".keelyard", ".git"}
    for dirpath, dirnames, filenames in os.walk(cfg.root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            fp = Path(dirpath) / name
            if fp.is_symlink() or not fp.is_file():
                continue
            try:
                rel = fp.relative_to(cfg.root).as_posix()
            except ValueError:
                continue
            if is_tracked_name(rel):
                now_names.add(rel)
    now = inventory(read_worktree(cfg.root, sorted(now_names)))
    weakened = weakening(before, now)
    return {"base": ref, "weakened": weakened, "ok": not weakened}


def report_md(cfg: Config, base: str | None = None) -> tuple[str, dict]:
    d = watch.dashboard(cfg)
    diff = inventory_diff(cfg, base)
    state = d["proof"]["state"]
    title = {"proven": "Proven", "open": "Proof incomplete", "no_work": "No agent work recorded"}.get(state, state)
    lines = [MARKER, f"## keelyard report — {title}", ""]
    if d["proof"].get("task"):
        lines += [f"**Task:** {d['proof']['task'][:300]}", ""]
    lines.append(f"- Session `{d['session']}` · {d['edits']} edits · {d['steps']} snapshots")
    if d["proof"]["missing"]:
        lines.append("- Still open:")
        lines.extend(f"  - {g}" for g in d["proof"]["missing"])
    else:
        lines.append("- Proof: every recorded requirement is checked and tests passed after the last edit."
                     if state == "proven" else "- No files were changed in this session.")
    if d["audit"]["lies"]:
        lines.append("- **False claims** (what the agent said vs what was recorded):")
        lines.extend(f"  - Claimed “{lie['text']}”, but {lie['evidence']}" for lie in d["audit"]["lies"])
    b = d["budget"]
    if b.get("enabled") or b["tokens"]:
        cap = f" / {b['max_tokens']:,}" if b.get("max_tokens") else ""
        flag = " (at cap)" if b.get("over") else ""
        lines.append(f"- Spend: {b['tokens']:,}{cap} tokens{flag}")
    if diff["weakened"]:
        lines.append(f"- **Tests weakened** vs `{diff['base']}`:")
        lines.extend(f"  - {w}" for w in diff["weakened"])
    elif diff.get("base"):
        lines.append(f"- Tests vs `{diff['base']}`: no deletions, fewer assertions, or new skip/xfail markers.")
    lines += ["", f"*Local evidence, generated by keelyard {__version__}. Read it before you trust it.*"]
    body = "\n".join(lines) + "\n"
    data = {**d, "diff": diff, "markdown": body,
            "ok": state in ("proven", "no_work") and not d["audit"]["lies"] and diff["ok"]}
    # no_work + weakened tests in the PR should fail
    if diff["weakened"]:
        data["ok"] = False
    if state == "open":
        data["ok"] = False
    return body, data


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or ""


def _pr_number(explicit: int | None) -> int | None:
    if explicit:
        return explicit
    ev = os.environ.get("GITHUB_EVENT_PATH")
    if ev and Path(ev).is_file():
        try:
            j = json.loads(Path(ev).read_text(encoding="utf-8"))
            n = (j.get("pull_request") or {}).get("number") or j.get("number")
            if n:
                return int(n)
        except (OSError, ValueError):
            pass
    return None


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_API_HOST = "api.github.com"


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RuntimeError("refusing HTTP redirect when talking to GitHub")


def _open(req: Request, timeout: int = 30):
    return build_opener(_RefuseRedirect).open(req, timeout=timeout)


def post_comment(body: str, pr: int, repo: str, token: str) -> dict:
    """Create or update the keelyard-ci comment on a PR. Never logs the token."""
    if not _REPO.fullmatch(repo or ""):
        raise RuntimeError("repo must be in owner/name form, like acme/app.")
    if not isinstance(pr, int) or pr <= 0:
        raise RuntimeError("pull request number must be a positive integer.")
    base = f"https://{_API_HOST}/repos/{repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": f"keelyard/{__version__}", "X-GitHub-Api-Version": "2022-11-28"}

    def call(method: str, url: str, payload: dict | None = None) -> dict | list:
        host = urlparse(url).hostname
        if host != _API_HOST or not url.startswith("https://"):
            raise RuntimeError("refusing to call a host other than api.github.com")
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method=method)
        try:
            with _open(req, timeout=30) as r:  # noqa: S310 — https api.github.com only, redirects refused
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise RuntimeError(f"GitHub API {e.code} on {method} repos/{repo}{urlparse(url).path.split(repo, 1)[-1]}") from None

    raw = call("GET", f"{base}/issues/{pr}/comments?per_page=100")
    if not isinstance(raw, list):
        raise RuntimeError("GitHub API did not return a comment list")
    existing = next((c for c in raw if isinstance(c, dict) and MARKER in (c.get("body") or "")), None)
    if existing:
        cid = existing.get("id")
        if not isinstance(cid, int):
            raise RuntimeError("GitHub comment id was missing")
        out = call("PATCH", f"{base}/issues/comments/{cid}", {"body": body})
        url = out.get("html_url") if isinstance(out, dict) else None
        return {"action": "updated", "url": url}
    out = call("POST", f"{base}/issues/{pr}/comments", {"body": body})
    url = out.get("html_url") if isinstance(out, dict) else None
    return {"action": "created", "url": url}


def comment(body: str, pr: int | None = None, repo: str | None = None) -> dict:
    token, repo, pr = _token(), repo or _repo(), _pr_number(pr)
    if not token:
        raise RuntimeError("no GITHUB_TOKEN or GH_TOKEN in the environment. Hint: pass --comment only in CI or after `gh auth`.")
    if not repo or not pr:
        raise RuntimeError("no pull request to comment on. Hint: set --pr and --repo, or run in a GitHub Actions pull_request job.")
    return post_comment(body, pr, repo, token)
