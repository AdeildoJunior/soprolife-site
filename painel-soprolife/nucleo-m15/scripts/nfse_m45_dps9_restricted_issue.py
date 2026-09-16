"""M45 — the SINGLE authorized restricted-SEFIN POST for DPS #9.

Run only after the local waiter has independently confirmed the gate-open
API (started by run-m45-dps9-gate-open.sh) is the correct process: exact
worktree, exact HEAD, correct isolated DB, correct environment, correct
Sefin base, network gate true. This script itself never touches the PFX
password — that lives only inside the already-running server process.

M47 — CRITICAL ORDERING RULE (this is what caused every local 500 so far):
this script must hold NO open SQLite session/transaction of its own while
the API is servicing the POST. SQLite is a single-file database: a session
left open here keeps a SHARED lock on the very file the API must escalate
to EXCLUSIVE for its durable-intent `db.commit()`. Because this process is
simultaneously blocked waiting for that POST's HTTP reply, neither side can
proceed — a circular wait that no `busy_timeout` can win (proven: the API
waits the full timeout, then fails with `database is locked` at
`nfse.py`'s durable commit, which is exactly the DPS #9 failure signature).
Every local DB access below is therefore wrapped in its own short
`with`-scoped session that is fully CLOSED before any HTTP call is made.

Sequence, exactly once, no retry:
  1. Mint a local Bearer token (DB-only, same mechanism as every prior
     read-only check this mission) — inside a session that is CLOSED
     again before step 2, so no lock is held across the HTTP calls.
  2. GET the document: must still be pending/eligible/0 attempts.
  3. Exactly ONE POST /api/v1/fiscal/documentos/{id}/emitir-mock with a
     fresh idempotency key (this is the real code path: with
     M15_NFSE_ENVIRONMENT=restricted and the network gate true on the
     server process, `nfse.operate(..., 'issue', ...)` resolves the real
     RestrictedNfseProvider and sends the DPS to Sefin Nacional).
  4. Read back: document state/eligibility, the completed FiscalAttempt
     row (outcome, error_code, external_id), and the full M44-decoded
     sefin_erro_* / sefin_resposta_* audit detail if the attempt was not
     a clean success.
  5. Print one JSON object with everything the mission report needs.

No retry logic anywhere in this file. If the POST raises, that is
reported as-is; nothing here re-sends it.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import get_engine, get_sessionmaker
from app.models import AuditLog, User
from app.security import issue_token

LAB_ACTOR_USER_ID = "a7dc2c74-009b-40bc-a95e-93a0bd797672"
DOCUMENT_ID = "9be02a64-d41d-44f0-a2d4-483a79ef288a"
API_BASE = "http://127.0.0.1:8120"


def _get(url, token):
    # M45 — must never raise: an earlier run crashed here on a follow-up
    # GET after the POST itself had already returned a local 500, turning
    # a single clean diagnostic into an uncaught traceback that hid the
    # real signal (the POST's own body). Same graceful-decode shape as
    # `_post()` below.
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        try:
            parsed = json.loads(body_bytes)
        except Exception:
            parsed = {"raw": body_bytes.decode(errors="replace")}
        return exc.code, parsed


def _post(url, token, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        try:
            parsed = json.loads(body_bytes)
        except Exception:
            parsed = {"raw": body_bytes.decode(errors="replace")}
        return exc.code, parsed


def _assert_no_open_local_transaction(label):
    """M47 — fail loudly if this process still holds ANY SQLite transaction.

    A single leftover session here is enough to deadlock the API's durable
    commit (see the module docstring), and the symptom it produces —
    `database is locked` inside the API — points at the API, not at the
    caller that actually holds the lock. Checking it explicitly turns that
    silent, misattributed failure into an immediate, honest error here.
    """
    engine = get_engine()
    checked_out = engine.pool.checkedout()
    assert checked_out == 0, (
        f"{label}: this process still has {checked_out} pooled SQLite "
        f"connection(s) checked out — an open transaction here will block "
        f"the API's durable-intent commit and produce a spurious "
        f"'database is locked'. Close every local session before any HTTP call."
    )


def _mint_token():
    """Open a local session, mint the Bearer token, and CLOSE it fully.

    Nothing about the token depends on the session staying alive:
    `issue_token` is a pure HMAC over (user_id, exp, password fingerprint)
    and performs no SQL of its own — the only DB access here is reading the
    lab actor row.
    """
    with get_sessionmaker()() as db:
        user = db.get(User, LAB_ACTOR_USER_ID)
        assert user is not None
        token = issue_token(user.id, user.password_hash, ttl_minutes=10)
    # `with` closed the session; the SHARED lock taken by the read above is
    # released here, BEFORE the first HTTP call below.
    _assert_no_open_local_transaction("after minting the token")
    return token


def _read_audit_detail():
    """Post-POST audit read, in its OWN short session opened only now.

    Deliberately not reusing the token-minting session: that one had to die
    before the POST, and reopening here costs one cheap connection while
    keeping the "no local lock during the POST" rule structurally true.
    """
    with get_sessionmaker()() as db:
        audit_rows = db.scalars(
            select(AuditLog).where(
                AuditLog.entidade == "fiscal_document",
                AuditLog.acao == "fiscal.issue_completed",
            ).order_by(AuditLog.ts_utc.desc()).limit(20)
        ).all()
        matching = next(
            (r for r in audit_rows if r.entidade_id == DOCUMENT_ID), None)
        if matching is None:
            return None, None
        # Materialise before the session closes.
        return matching.detalhes, matching.ts_utc.isoformat()


def main():
    # Phase 1 — LOCAL DB ONLY. Opened and fully closed before any HTTP call.
    token = _mint_token()

    # Phase 2 — HTTP ONLY. This process holds no SQLite transaction here, so
    # the API is free to escalate its own durable-intent commit to EXCLUSIVE.
    status, before = _get(f"{API_BASE}/api/v1/fiscal/documentos/{DOCUMENT_ID}", token)
    assert status == 200, before
    assert before["state"] == "pending", before["state"]
    assert before["eligibility"] == "eligible", before["eligibility"]
    assert before["blocking_reasons"] == [], before["blocking_reasons"]

    status, attempts_before = _get(
        f"{API_BASE}/api/v1/fiscal/documentos/{DOCUMENT_ID}/tentativas?page_size=50", token)
    assert status == 200
    assert attempts_before.get("total", len(attempts_before.get("itens", []))) == 0, attempts_before

    idempotency_key = (
        "m45-dps9-single-authorized-post-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    # M47 — last line of defence before the one authorized POST: if anything
    # above accidentally left a session open, stop HERE rather than letting
    # the API fail with a misattributed `database is locked`.
    _assert_no_open_local_transaction("immediately before the single POST")
    print("== SINGLE OUTBOUND POST /nfse — SENDING NOW ==", flush=True)
    post_status, post_body = _post(
        f"{API_BASE}/api/v1/fiscal/documentos/{DOCUMENT_ID}/emitir-mock", token,
        {"idempotency_key": idempotency_key},
    )
    print("== LOCAL API RESPONSE (this endpoint's own status/body, not Sefin's raw HTTP status) ==")
    print(json.dumps({"local_http_status": post_status, "body": post_body}, indent=2, ensure_ascii=False))

    # M45 — every call from here on is best-effort/diagnostic only: if
    # the POST itself already failed at the local API, a follow-up
    # call failing TOO (e.g. the same process also 500s on a plain
    # GET) is important signal, not a script crash to hide it behind.
    after_status, after = _get(f"{API_BASE}/api/v1/fiscal/documentos/{DOCUMENT_ID}", token)
    attempts_status, attempts_after = _get(
        f"{API_BASE}/api/v1/fiscal/documentos/{DOCUMENT_ID}/tentativas?page_size=50", token)
    items = attempts_after.get("itens", []) if attempts_status == 200 else []
    completed = [a for a in items if a.get("phase") == "completed"]

    # Phase 3 — LOCAL DB again, only now that every HTTP call is finished.
    # Full M44-decoded audit detail for this exact attempt, read straight
    # from the append-only audit trail (same DB, direct read) — this
    # works regardless of whether the HTTP layer above is healthy.
    audit_detail, audit_ts_utc = _read_audit_detail()

    result = {
        "document_before": before,
        "attempts_before_count": attempts_before.get("total", len(attempts_before.get("itens", []))),
        "idempotency_key_used": idempotency_key,
        "local_http_status": post_status,
        "post_body": post_body,
        "follow_up_get_document_status": after_status,
        "follow_up_get_document_also_failed": after_status >= 400,
        "document_after": after,
        "follow_up_get_attempts_status": attempts_status,
        "attempts_after_count": len(items),
        "completed_attempts": completed,
        "audit_detail": audit_detail,
        "audit_ts_utc": audit_ts_utc,
    }
    print("== FINAL RESULT (full) ==")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
