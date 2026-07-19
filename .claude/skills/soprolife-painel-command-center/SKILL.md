---
name: soprolife-painel-command-center
description: Operate and change the SoproLife Command Center safely across its legacy panel and native M15 PostgreSQL/FastAPI core. Use for repository workflow, backend/API work, data models or migration, identity matching, finance, authentication/RBAC/audit, proxy or loopback architecture, deployment, backups, and production-readiness work. Covers the stable project state through M15.3B; do not use unmerged M15.4A implementation details as established behavior.
---

# SoproLife Painel Command Center

Use this skill as the operational authority for architecture, data, security,
Git workflow, backend, migration, and deployment boundaries. For presentation,
forms, responsive layout, accessibility, and date/calendar UX, use
[soprolife-ux-premium](../soprolife-ux-premium/SKILL.md).

## Read only what the task needs

- Read [stable system boundaries](references/stable-system-boundaries.md) before
  changing data behavior, identity, finance, authentication, API/proxy behavior,
  migration, deployment, or production controls.
- Read [commands and gates](references/commands-and-gates.md) before running the
  M15 stack, tests, migrations, health checks, backup, or deployment tooling.
- Treat the versioned M15.1–M15.3B documents linked by those references as the
  stable source. Inspect code before acting; ignore unmerged M15.4A behavior.

## Mandatory workflow

1. Work from the main repository `~/soprolife-site`, whose primary branch is
   `painel-soprolife-v01`. Implement only in a clean isolated worktree on a
   dedicated feature branch. Never edit another agent's active worktree or the
   primary-branch worktree.
2. Before editing, confirm the worktree, branch, base, status, and relevant
   versioned documentation. Keep the change inside the requested scope.
3. Preserve coexistence: legacy modules remain available while M15 migrates
   gradually behind its versioned feature flag. Never silently replace, remove,
   or redirect a legacy module.
4. Use synthetic data only. Keep reports, prompts, secrets, private exports,
   migration inputs, and backups outside Git. Never commit PII, credentials,
   tokens, API URLs, or private identifiers.
5. Run deterministic gates for the touched area. Prefer automated checks over
   chains of AI audits; request at most one meaningful final review when risk
   warrants it. Read the complete final diff before delivery.
6. Do not deploy an unreviewed feature branch. When deployment is explicitly
   authorized, use the versioned deployment/hardening path; never improvise
   production edits.

## Non-negotiable engineering boundaries

- Keep PostgreSQL and FastAPI loopback-only. Remote panel access must use the
  approved proxy architecture. Do not weaken authentication, RBAC, append-only
  audit, request validation, or deployment hardening.
- Keep M15 activation controlled by `painel-soprolife/data/m15-config.json`.
  Activation, real-data migration, first-user creation, and deployment are
  separate human-controlled actions.
- Preserve deterministic identity and date semantics. Explicit IDs are
  authoritative; a name alone never links people; a phone is only a candidate.
  Preserve original date text, precision, and assumed-day metadata.
- Keep `Financeiro_Lancamentos` as the sole monetary source of truth. Clinical
  and CRM records may reference finance only through technical IDs and must not
  carry financial PII.
- Follow-up must honor consent and do-not-contact state. A WhatsApp interaction
  is recorded only after human confirmation; never imply automatic sending.
- Real clinical use requires HTTPS. Do not enter real clinical data through an
  HTTP-only login flow. Backups must be private, validated, and excluded from
  Git.

## Communication

- Write prompts intended for external coding agents in English.
- Give user-facing terminal instructions as explicit, copy-pasteable commands
  with the required directory and placeholders identified.
- State assumptions and failed gates plainly. Never hide a failure or broaden
  a task into deployment, migration, or production work.

## Recommendations

- Prefer small additive changes, idempotent operations, fail-closed validation,
  and rollback paths that preserve the legacy panel and existing data.
- Prefer a dry-run plus a mapping/report artifact for any migration work.
- Link to versioned project documentation instead of restating large schemas or
  runbooks in prompts and reports.
