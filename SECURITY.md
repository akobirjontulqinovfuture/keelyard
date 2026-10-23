# Security

Please report vulnerabilities privately via GitHub Security Advisories on this repository.

Threat model notes:
- keelyard runs with your user's permissions, as part of your agent's hooks. It runs test commands you configure
  (`[proof].test_cmd`) without a shell.
- Memory stores redacted transcript text in `$XDG_DATA_HOME/keelyard/memory.db` (mode 600). Redaction is pattern based
  and cannot catch every secret. Do not paste secrets into agent chats.
- Swarm sends file contents (redacted) to the helper endpoint you configure. Point it at a local model if the code is
  sensitive.

## Reporting a vulnerability

Use "Report a vulnerability" under the repository's Security tab (GitHub private advisory). Please include the
version (`keelyard --version`), OS, and steps to reproduce. Expect a first reply within 7 days. Do not open a
public issue for security problems.

## Trust properties

- No telemetry, no install scripts, no post-install hooks, no obfuscated code.
- Network calls only to model endpoints you configure for `swarm`/`coop`.
- Agent config is written only by explicit `init`/`install` commands, with backups; `uninstall` reverses it.
- Snapshots use `.keelyard/shadow.git` via `GIT_DIR`; the project's `.git` is never touched.
- `requirements-lock.txt` pins a tested dependency set; `pip-audit -r requirements-lock.txt` reported no known
  vulnerabilities at 1.0.0.
