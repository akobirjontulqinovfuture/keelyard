"""Shipped agent skills (`keelyard/skills/<name>/SKILL.md`), installed as slash commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    raw: str


def parse_skill(text: str, folder: str | None = None) -> Skill:
    m = _FRONT.match(text)
    if not m:
        raise ValueError("SKILL.md must start with YAML frontmatter that has name and description.")
    meta, body = m.group(1), m.group(2)
    fields: dict[str, str] = {}
    key, acc = None, []
    for line in meta.splitlines():
        if ":" in line and re.match(r"^[A-Za-z0-9_-]+\s*:", line):
            if key:
                fields[key] = _scalar("\n".join(acc))
            name, _, rest = line.partition(":")
            key, acc = name.strip(), [rest]
        elif key:
            acc.append(line)
    if key:
        fields[key] = _scalar("\n".join(acc))
    name = fields.get("name") or ""
    description = fields.get("description") or ""
    if not _NAME.fullmatch(name):
        raise ValueError(f"invalid skill name {name!r}. Hint: start with a letter, then letters, digits, _ or -.")
    if folder and folder != name:
        raise ValueError(f"skill folder {folder!r} must match frontmatter name {name!r}.")
    if not description:
        raise ValueError(f"skill {name!r} needs a description in its frontmatter.")
    return Skill(name=name, description=description, body=body, raw=text)


def _scalar(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def shipped_skills() -> list[Skill]:
    """Every valid `SKILL.md` shipped in the keelyard package, sorted by name."""
    root = resources.files("keelyard.skills")
    out: list[Skill] = []
    for entry in root.iterdir():
        if getattr(entry, "name", "").startswith("_") or not entry.is_dir():
            continue
        path = entry / "SKILL.md"
        if not path.is_file():
            continue
        out.append(parse_skill(path.read_text(encoding="utf-8"), folder=entry.name))
    out.sort(key=lambda s: s.name)
    return out


def project_dest(agent: str, root: Path, name: str) -> Path:
    if agent == "claude":
        return root / ".claude" / "commands" / f"{name}.md"
    if agent == "codex":
        return root / ".codex" / "prompts" / f"{name}.md"
    if agent == "opencode":
        return root / ".opencode" / "command" / f"{name}.md"
    raise ValueError(f"unknown agent {agent!r}")


def global_dest(agent: str, paths: dict[str, Path], name: str) -> Path:
    if agent == "claude":
        return paths["claude_dir"] / "commands" / f"{name}.md"
    if agent == "codex":
        return paths["codex_dir"] / "prompts" / f"{name}.md"
    if agent == "opencode":
        return paths["opencode_dir"] / "command" / f"{name}.md"
    raise ValueError(f"unknown agent {agent!r}")


def rel_for(agent: str, name: str) -> str:
    return project_dest(agent, Path("."), name).as_posix()
