#!/usr/bin/env bash
# Clean-environment check: a fresh venv, a fresh HOME, a non-editable install, then the full user journey:
# init -> doctor -> selftest -> uninstall, ending with a project byte-identical to where it started.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
export HOME="$WORK/home" USERPROFILE="$WORK/home" XDG_DATA_HOME="$WORK/home/.local/share" XDG_CONFIG_HOME="$WORK/home/.config"
mkdir -p "$HOME"
PY="${PYTHON:-python}"
"$PY" -m venv "$WORK/venv"
if [ -x "$WORK/venv/Scripts/python.exe" ]; then BIN="$WORK/venv/Scripts"; else BIN="$WORK/venv/bin"; fi
"$BIN/python" -m pip install -q "$SRC"
K="$BIN/keelyard"
"$K" --version

P="$WORK/project"
mkdir -p "$P" && cd "$P"
git init -q
git -c user.name=ci -c user.email=ci@ci commit -q --allow-empty -m init
printf 'def add(a, b):\n    return a + b\n' > app.py
printf 'from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n' > test_app.py
printf '# my notes\n' > CLAUDE.md
git add -A && git -c user.name=ci -c user.email=ci@ci commit -qm project

"$K" init --yes --preset lean --agent claude --agent codex --agent opencode --codex-project-mcp
"$K" doctor --json > "$WORK/doctor.json"
"$BIN/python" - "$WORK/doctor.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["ok"] is True, d
PY
"$K" selftest
"$K" status
for a in claude codex opencode; do "$K" uninstall "$a"; done
LEFT="$(git status --porcelain)"
if [ -n "$LEFT" ]; then echo "uninstall left changes behind:"; echo "$LEFT"; exit 1; fi
git diff --exit-code
echo "clean-env journey OK"
