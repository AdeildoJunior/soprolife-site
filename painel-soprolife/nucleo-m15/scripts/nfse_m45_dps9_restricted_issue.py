"""M45 — the SINGLE authorized restricted-SEFIN POST for DPS #9.

Run only after the local waiter has independently confirmed the gate-open
API (started by run-m45-dps9-gate-open.sh) is the correct process: exact
worktree, exact HEAD, correct isolated DB, correct environment, correct
Sefin base, network gate true. This script itself never touches the PFX
password — that lives only inside the already-running server process.

Sequence, exactly once, no retry:
  1. Mint a local Bearer token (DB-only, same mechanism as every prior
     read-only check this mission).
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

from app.db import get_sessionmaker
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


def main():
    db = get_sessionmaker()()
    try:
        user = db.get(User, LAB_ACTOR_USER_ID)
        assert user is not None
        token = issue_token(user.id, user.password_hash, ttl_minutes=10)

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

        # Full M44-decoded audit detail for this exact attempt, read straight
        # from the append-only audit trail (same DB, direct read) — this
        # works regardless of whether the HTTP layer above is healthy.
        audit_rows = db.scalars(
            select(AuditLog).where(
                AuditLog.entidade == "fiscal_document",
                AuditLog.acao == "fiscal.issue_completed",
            ).order_by(AuditLog.ts_utc.desc()).limit(20)
        ).all()
        matching_audit = next(
            (r for r in audit_rows if r.entidade_id == DOCUMENT_ID), None)

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
            "audit_detail": (matching_audit.detalhes if matching_audit else None),
            "audit_ts_utc": (matching_audit.ts_utc.isoformat() if matching_audit else None),
        }
        print("== FINAL RESULT (full) ==")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
