# Security

Please report vulnerabilities privately via GitHub Security Advisories on this repository.

Threat model notes:
- keelyard runs with your user's permissions, as part of your agent's hooks. It runs test commands you configure
  (`[proof].test_cmd`) without a shell.
- Memory stores redacted transcript text in `$XDG_DATA_HOME/keelyard/memory.db` (mode 600). Redaction is pattern based
  and cannot catch every secret. Do not paste secrets into agent chats.
- Swarm sends file contents (redacted) to the helper endpoint you configure. Point it at a local model if the code is
  sensitive.

## Supported versions

| Version | Supported |
|---|---|
| latest 1.x release | yes, fixes ship as a new patch release |
| older releases | no, please upgrade (`pipx upgrade keelyard`) |

## Reporting a vulnerability

Use [Report a vulnerability](https://github.com/akobirjontulqinovfuture/keelyard/security/advisories/new) under the
repository's Security tab (private GitHub advisory). Please include the version (`keelyard --version`), OS, and steps
to reproduce. Do not open a public issue for security problems.

What happens next:
- first reply within 7 days, with whether the report is confirmed;
- a fix or mitigation plan within 30 days for confirmed issues;
- a GitHub Security Advisory (and CVE when it applies) once a fixed release is on PyPI, crediting you unless you
  ask not to be named.

## Trust properties

- No telemetry, no install scripts, no post-install hooks, no obfuscated code.
- Network calls only to model endpoints you configure for `swarm`/`coop`.
- Agent config is written only by explicit `init`/`install` commands, with backups; `uninstall` reverses it.
- Snapshots use `.keelyard/shadow.git` via `GIT_DIR`; the project's `.git` is never touched.
- Release files on PyPI are built in GitHub Actions with Trusted Publishing and carry build provenance
  attestations (`gh attestation verify <file> -R akobirjontulqinovfuture/keelyard`).
- `requirements-lock.txt` pins a tested dependency set; `pip-audit -r requirements-lock.txt` reported no known
  vulnerabilities at 1.0.0.
