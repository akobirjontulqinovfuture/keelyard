"""Secret redaction applied before anything is stored or reported."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("telegram-bot", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}\b")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("url-cred", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    (
        "assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_KEY)[A-Z0-9_]*)\s*[=:]\s*['\"]?([^\s'\"]{8,})"
        ),
    ),
]


def redact(text: str) -> str:
    if not text:
        return text
    for name, pat in _PATTERNS:
        if name == "assignment":
            text = pat.sub(lambda m: f"{m.group(1)}=[REDACTED:assignment]", text)
        else:
            text = pat.sub(f"[REDACTED:{name}]", text)
    return text
