# Stable system boundaries through M15.3B

Read the section relevant to the task. These are mandatory unless a later
merged specification explicitly supersedes them.

## Architecture and activation

- Keep the legacy panel operational during gradual native migration.
- Treat M15 as the native PostgreSQL/FastAPI operational core. Google Sheets
  remains a historical migration, backup, verification, and rollback source
  during the transition; do not claim a cutover that has not been approved.
- Keep M15 behind the versioned feature flag. Do not activate it as a side
  effect of implementation, migration, deployment, or testing.
- Keep PostgreSQL on loopback and FastAPI on `127.0.0.1:8015`. Browsers reach
  M15 only through the approved same-origin panel proxy. Never expose the API
  or database directly.
- Preserve authentication, hierarchical RBAC, append-only audit, payload
  validation, request limits, safe logging, and service hardening. UI hiding is
  not authorization; the server remains authoritative.
- Do not silently replace or remove legacy modules, Apps Script flows, or
  historical sources.

Sources: [M15.1 architecture](../../../../painel-soprolife/docs/m15-1-nucleo-operacional-nativo.md),
[M15.2 proxy and deployment](../../../../painel-soprolife/docs/m15-2-proxy-seguro-deploy-vps.md),
[M15.3A administration](../../../../painel-soprolife/docs/m15-3a-administracao-operacao.md),
and [M15.3B hardening](../../../../painel-soprolife/docs/m15-3b-hardening-operacional.md).

## People, records, identity, and dates

- Model one person with many related exams and consultations; never collapse
  those records into one event.
- Use explicit internal, public, and legacy IDs when available. Never match or
  merge on name alone. A normalized phone is a matching candidate, not proof;
  ambiguous cases require a recorded human decision.
- Never use real patient data in development, fixtures, screenshots, demos, or
  tests. Never commit PII, credentials, tokens, API URLs, private identifiers,
  real imports, or migration reports containing sensitive mappings.
- Preserve date source text and precision. For month- or year-only values,
  retain the original value, precision, and assumed-day metadata. Never present
  an assumed day as an exact known date.
- Honor consent history and do-not-contact state. Generate a WhatsApp URL only
  for human review when policy permits it, and record an interaction only after
  explicit human confirmation.

## Migration safety

- Keep real source files and reports outside Git. Default migration tooling to
  dry-run and zero writes.
- Do not run a real historical migration without a private validated backup,
  dry-run, mapping report, and explicit human approval.
- Require deterministic IDs, idempotency, per-batch accounting, ambiguity
  reporting, and transactional rollback. Never repair production migration
  data with ad hoc row deletion or silent merging.
- Google Sheets and CSV history remain available for migration, verification,
  backup, and rollback throughout the controlled transition.

## Finance

- `Financeiro_Lancamentos` is the only monetary source of truth. CRM, clinical,
  partner, and referral modules are operational sources, not ledgers.
- Never recreate the deleted legacy `Financeiro` sheet.
- Keep names, phones, CPF, and other PII out of financial descriptions and
  summaries. Relate finance to clinical records only with technical IDs.
- Correct an immutable monetary event with a new auditable entry; do not hide
  history by overwriting amounts.

## Production readiness

- Require HTTPS before any real patient or clinical production use. An HTTP
  login page is not an acceptable boundary for entering real clinical data.
- Use only versioned deployment and hardening scripts. Keep health waits finite,
  require HTTP 200 plus JSON `status=ok`, and fail closed on exhaustion or an
  unexpected listener/process.
- Keep deployment, first-user creation, feature activation, seed/demo creation,
  and real import as separate explicitly approved actions.
- Store backups privately outside Git; validate each backup before relying on
  it, and restore only into an isolated target after a reviewed plan.
