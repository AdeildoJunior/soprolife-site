# Commands and deterministic gates

Run commands from the repository root unless the command starts with `cd`.
Choose only the gates relevant to the files touched.

## Worktree and diff gates

```bash
git branch --show-current
git status --short --branch
git worktree list
git diff --check
git diff --stat
git diff
```

Implementation belongs in an isolated feature worktree, never in the primary
branch worktree or another agent's active worktree. Read the complete final
diff before commit or handoff.

## Local M15 stack

Follow [the M15 README](../../../../painel-soprolife/nucleo-m15/README.md).
Start the API from `painel-soprolife/nucleo-m15`, then start the same-origin
proxy from the repository root:

```bash
python3 painel-soprolife/scripts/command-center-local-server.py
```

Do not use `python3 -m http.server` to validate M15: it does not implement the
proxy. Keep the versioned feature flag off unless the current local test
explicitly needs a temporary manual activation.

## Stable automated gates

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python -m pytest tests -q
cd ../..
python3 painel-soprolife/scripts/test_command_center_m15_proxy.py
node --check painel-soprolife/js/m15-nucleo.js
bash painel-soprolife/nucleo-m15/scripts/test-deploy-hardening.sh
bash -n painel-soprolife/nucleo-m15/scripts/deploy-producao-vps.sh
```

The PostgreSQL proof is heavier and should run when models, migrations, or
PostgreSQL-specific behavior changes:

```bash
cd painel-soprolife/nucleo-m15
bash scripts/test-postgres-efemero.sh
```

It must use only synthetic test data and an ephemeral isolated database.

## Migration boundary

The importer is dry-run by default. Use a private synthetic input while
developing:

```bash
cd painel-soprolife/nucleo-m15
.venv/bin/python -m app.cli importar --tipo leads --arquivo /absolute/private/path/leads.csv
```

Do not add `--execute` for real history without backup, dry-run, mapping report,
and explicit human approval. Never place real input or generated reports in
Git.

## Deployment, health, and backup boundary

Do not run production commands merely to validate a code change. When the user
explicitly authorizes a reviewed deployment, follow the versioned
[M15.2 runbook](../../../../painel-soprolife/docs/m15-2-proxy-seguro-deploy-vps.md)
and [M15.3B hardening addendum](../../../../painel-soprolife/docs/m15-3b-hardening-operacional.md)
exactly. Do not edit installed services or production files manually.

Health automation must use the versioned finite retry helper and accept only
HTTP 200 with JSON `status=ok`. Backups must use the versioned private backup
script and must be validated; never print secrets or put backups in Git.
