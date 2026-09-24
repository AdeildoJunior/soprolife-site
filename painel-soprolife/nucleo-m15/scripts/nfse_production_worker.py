#!/usr/bin/env python3
"""M66 — the one-shot production NFS-e worker. ONE confirmed request per run, ONE POST at most.

WHY A SEPARATE PROCESS

The web process (``soprolife-m15-api``) stays fail-closed: no A1, no
``M15_NFSE_PRODUCTION_NETWORK_ENABLED``, no production transport. When a
gestor/admin confirms an issuance in the Command Center, the web process only
writes a ``FiscalIssuanceRequest`` and drops a doorbell file named after it.
systemd sees the doorbell (``soprolife-nfse-production-worker.path``) and
starts this script once, as its own user, with the A1 and its password handed
over by ``LoadCredentialEncrypted=`` in ``$CREDENTIALS_DIRECTORY`` — never in
argv, env, the database or a log. The process ends and the credentials go
with it.

This is the M65 script (d280aad), generalized from "ESP-000050, hard-coded"
to "the one request a human confirmed", with the same guards:

  1. Take ONE doorbell and delete it first (a crash can never re-trigger it).
  2. Claim the request in the database: ``authorized`` -> ``running``, only
     if unexpired. A partial unique index makes a second claim of an issue
     request for the same document impossible, whatever this code does.
  3. Re-check every fact, read-only, immediately before the send: document,
     state, preparation id + fingerprint, a fresh ``evaluate()``, amount, the
     tomador identity hash the human saw, zero prior attempts, flow, service
     municipality, clock, certificate margin, the exact production endpoint.
     Any divergence: ``refused``, nothing sent.
  4. ``explicit_human_authorization=True`` is passed to the production
     transport ONLY while a claimed request is in hand — that claimed row IS
     the human authorization, for this document and this amount only.
  5. ``nfse.operate(issue)`` commits the durable ``started`` attempt BEFORE
     the provider boundary; the transport writes the exact signed DPS and a
     post-intent record (0600, fsync) BEFORE the socket opens, after checking
     the DPS carries the confirmed amount, competence and municipality.
  6. Exactly one POST. A second POST raises before it is built.
  7. Only if the outcome is ``uncertain``: GET-only reconciliation
     (GET /dps/{id}, then GET /nfse/{chave} if the XML is missing). Never a
     POST. A ``reconcile`` request never enters the POST phase at all.

Usage (systemd only; see painel-soprolife/systemd/soprolife-nfse-production-worker.*):

    python scripts/nfse_production_worker.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.services.nfse_national.transport import (  # noqa: E402
    PATH_GET_NFSE, PATH_ISSUE_NFSE, PRODUCTION_BASE_URL, HttpxProductionTransport,
    TransportRequest, TransportResponse)

EXPECTED_ENDPOINT = "https://sefin.nfse.gov.br/SefinNacional"
POST_TIMEOUT_SECONDS = 60.0
MAX_GETS = 2                     # GET /dps/{id} and, only if needed, GET /nfse/{chave}
CERT_CREDENTIAL = "nfse-a1.pfx"
PASSWORD_CREDENTIAL = "nfse-a1-password"
SUPPORTED_FLOWS = ("DIRECT", "HOME")

# The client constructor this process may use, and only from inside the
# one-shot transport's send(). Tests replace it with a mock-backed one.
_ORIGINAL_HTTPX_CLIENT = httpx.Client


class OneShotViolation(RuntimeError):
    """A second POST, a POST outside the issue phase, an unexpected path."""


class PreSendGuardFailed(RuntimeError):
    """The DPS about to leave is not what the human confirmed. Nothing sent."""


class WorkerConfigError(RuntimeError):
    """Missing spool/runtime/credentials. Stable code, never a secret."""


class _Ledger:
    """Every outbound call this process attempts, counted BEFORE it is made."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.claimed_request_id: str | None = None
        self.phase = "closed"          # closed | issue | reconcile
        self.post_count = 0
        self.get_count = 0
        self.second_post_attempted = False
        self.inside_send = False
        self.runtime_dir: Path | None = None
        self.expected: dict | None = None
        self.pre_send_refusal: str | None = None
        self.events: list[dict] = []


LEDGER = _Ledger()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_private(path: Path, data: bytes) -> str:
    """0600, fsync'd, never overwriting. Returns the sha256."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ DPS checks

def dps_summary(xml_bytes: bytes) -> dict:
    """Non-personal facts of a DPS. Never the tomador, only whether a CPF is there."""
    from lxml import etree

    from app.services.nfse_national.dps_builder import NFSE_NS as ns
    root = etree.fromstring(xml_bytes)

    def text(path):
        node = root.find(path)
        return node.text if node is not None else None
    inf = root.find(f".//{{{ns}}}infDPS")
    return {
        "id": inf.get("Id") if inf is not None else None,
        "tpAmb": text(f".//{{{ns}}}tpAmb"),
        "dhEmi": text(f".//{{{ns}}}dhEmi"),
        "serie": text(f".//{{{ns}}}serie"),
        "nDPS": text(f".//{{{ns}}}nDPS"),
        "dCompet": text(f".//{{{ns}}}dCompet"),
        "prest_cnpj": text(f".//{{{ns}}}prest/{{{ns}}}CNPJ"),
        "toma_cpf_present": root.find(f".//{{{ns}}}toma/{{{ns}}}CPF") is not None,
        "cLocPrestacao": text(f".//{{{ns}}}cLocPrestacao"),
        "cTribNac": text(f".//{{{ns}}}cTribNac"),
        "vServ": text(f".//{{{ns}}}vServ"),
    }


def assert_confirmed_dps(candidate: bytes) -> dict:
    """Refuse unless the bytes about to leave carry exactly what the human
    confirmed: production, the amount, the competence, the municipality, a
    CPF tomador, no namespace prefixes, a fresh São Paulo dhEmi."""
    from app.services.nfse_national.production_preflight import (count_namespace_prefixes,
                                                                 dh_emi_matches_emission_timezone)
    expected = LEDGER.expected
    if not expected:
        raise PreSendGuardFailed("sem fatos confirmados carregados")
    summary = dps_summary(candidate)
    wrong = sorted(k for k, v in expected.items() if summary.get(k) != v)
    if wrong or not summary["toma_cpf_present"]:
        raise PreSendGuardFailed(f"campos divergentes do confirmado: {wrong}")
    if count_namespace_prefixes(candidate) != 0:
        raise PreSendGuardFailed("prefixo de namespace presente")
    if not dh_emi_matches_emission_timezone(summary["dhEmi"]):
        raise PreSendGuardFailed("dhEmi fora do fuso de emissão")
    stamped = datetime.fromisoformat(summary["dhEmi"])
    if abs(datetime.now(timezone.utc) - stamped) > timedelta(minutes=5):
        raise PreSendGuardFailed("dhEmi a mais de 5 minutos do relógio")
    return summary


# ------------------------------------------------------------ the transport

class OneShotProductionTransport(HttpxProductionTransport):
    """The production transport, with a hard one-POST ledger. Installed in
    place of ``dispatch.HttpxProductionTransport`` for this process only, so
    ``nfse.operate()`` runs its ordinary production path — every dispatch
    re-check included — and this is the only thing that differs.

    The human authorization is TRUE only while a claimed request is in hand:
    a transport built without one refuses exactly like the stock class."""

    def __init__(self, *, base_url: str = PRODUCTION_BASE_URL, network_enabled: bool = False,
                 environment: str = "", timeout_seconds: float = POST_TIMEOUT_SECONDS,
                 mtls_certificate_pem: bytes | None = None, mtls_key_pem: bytes | None = None,
                 explicit_human_authorization: bool = False):
        authorized = LEDGER.claimed_request_id is not None and LEDGER.phase in ("issue", "reconcile")
        super().__init__(base_url=base_url, network_enabled=network_enabled,
                         environment=environment,
                         timeout_seconds=max(timeout_seconds, POST_TIMEOUT_SECONDS),
                         mtls_certificate_pem=mtls_certificate_pem, mtls_key_pem=mtls_key_pem,
                         # M66 — the claimed FiscalIssuanceRequest is the human's
                         # confirmation for THIS document and THIS amount.
                         explicit_human_authorization=authorized)

    def send(self, request: TransportRequest) -> TransportResponse:
        if self._base_url != EXPECTED_ENDPOINT or "producaorestrita" in self._base_url:
            raise OneShotViolation("endpoint diferente do de produção esperado")
        if request.method == "POST":
            return self._post(request)
        if request.method == "GET":
            return self._get(request)
        raise OneShotViolation(f"método não permitido: {request.method}")

    def _post(self, request: TransportRequest) -> TransportResponse:
        if LEDGER.post_count >= 1:
            LEDGER.second_post_attempted = True
            raise OneShotViolation("SEGUNDO POST RECUSADO — um pedido confirmado autoriza exatamente um")
        if LEDGER.phase != "issue":
            raise OneShotViolation("POST fora da fase de emissão")
        if request.path != PATH_ISSUE_NFSE:
            raise OneShotViolation(f"POST para caminho inesperado: {request.path}")
        from app.services.nfse_national.wire import decode_b64_gzip_xml
        submitted = decode_b64_gzip_xml(json.loads(request.body)["dpsXmlGZipB64"])
        try:
            summary = assert_confirmed_dps(submitted)
        except PreSendGuardFailed as exc:
            # Nothing left the process, but operate() has already committed a
            # 'started' attempt, so the document is recorded as uncertain.
            # That is deliberate: it stays reconcile-only, never re-sent.
            LEDGER.pre_send_refusal = str(exc)[:60]
            raise
        # Durable before the boundary: the exact bytes, and the exact body.
        xml_sha = _write_private(LEDGER.runtime_dir / "submitted-dps-signed.xml", submitted)
        body_sha = hashlib.sha256(request.body).hexdigest()
        intent = {"request_id": LEDGER.claimed_request_id, "dps": summary,
                  "submitted_xml_sha256": xml_sha, "request_body_sha256": body_sha,
                  "endpoint": self._base_url + request.path, "intent_at_utc": _utcnow()}
        _write_private(LEDGER.runtime_dir / "post-intent.json",
                       json.dumps(intent, indent=2, ensure_ascii=False).encode())
        LEDGER.post_count += 1   # counted before the socket: an attempt is an attempt
        event = {"method": "POST", "path": request.path, "started_at_utc": _utcnow(),
                 "submitted_xml_sha256": xml_sha, "request_body_sha256": body_sha,
                 "dhEmi": summary["dhEmi"]}
        LEDGER.events.append(event)
        return self._forward(request, event, "post-response.body")

    def _get(self, request: TransportRequest) -> TransportResponse:
        if LEDGER.phase != "reconcile":
            raise OneShotViolation("GET antes da fase de reconciliação")
        if LEDGER.get_count >= MAX_GETS:
            raise OneShotViolation("limite de consultas de reconciliação atingido")
        if not (request.path.startswith("/dps/") or request.path.startswith("/nfse/")):
            raise OneShotViolation(f"GET para caminho inesperado: {request.path}")
        LEDGER.get_count += 1
        event = {"method": "GET", "path": request.path, "started_at_utc": _utcnow()}
        LEDGER.events.append(event)
        return self._forward(request, event, f"get-{LEDGER.get_count}-response.body")

    def _forward(self, request, event, body_name) -> TransportResponse:
        LEDGER.inside_send = True
        try:
            response = super().send(request)
        except Exception as exc:
            event.update(finished_at_utc=_utcnow(), exception=type(exc).__name__)
            raise
        finally:
            LEDGER.inside_send = False
        event.update(finished_at_utc=_utcnow(), http_status=response.status_code,
                     content_type=response.content_type, body_length=len(response.body or b""),
                     body_sha256=_write_private(LEDGER.runtime_dir / body_name, response.body or b""))
        return response


def _install_network_guards():
    """Only the one-shot transport may build an HTTP client, and only mid-send."""
    from app.services.nfse_national import dispatch

    def guarded_client(*args, **kwargs):
        if not LEDGER.inside_send:
            raise OneShotViolation("cliente HTTP construído fora do transporte one-shot")
        return _ORIGINAL_HTTPX_CLIENT(*args, **kwargs)

    def refuse(*args, **kwargs):
        raise OneShotViolation("cliente HTTP assíncrono proibido")
    httpx.Client = guarded_client
    httpx.AsyncClient = refuse
    dispatch.HttpxProductionTransport = OneShotProductionTransport


# ------------------------------------------------------------ credentials

def build_worker_settings():
    """Production settings for THIS process only, from systemd credentials.

    The password is read from the credential file systemd decrypted into a
    private, per-unit ramfs; it never passes through argv or the
    environment, and is never printed. The network gate is set here, in
    code, and dies with the process."""
    from app.config import Settings
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    if not creds:
        raise WorkerConfigError("credentials_directory_missing")
    cert, secret = Path(creds) / CERT_CREDENTIAL, Path(creds) / PASSWORD_CREDENTIAL
    if not cert.is_file() or not secret.is_file():
        raise WorkerConfigError("credentials_missing")
    password = secret.read_text(encoding="utf-8").rstrip("\r\n")
    if not password:
        raise WorkerConfigError("credentials_missing")
    try:
        return Settings(nfse_enabled=True, nfse_environment="production", nfse_real_enabled=True,
                        nfse_production_network_enabled=True,
                        nfse_restricted_certificate_path=cert,
                        nfse_restricted_certificate_password=password)
    finally:
        password = "\0" * len(password)
        del password


# ------------------------------------------------------------ spool

def take_doorbell(spool: Path) -> tuple[str, str] | None:
    """The oldest valid doorbell, deleted BEFORE anything else happens. Junk
    names are deleted too. Returns (request_id, kind) or None."""
    from app.services.nfse_production_issuance import SPOOL_NAME
    if not spool.is_dir():
        raise WorkerConfigError("spool_missing")
    valid = []
    for path in spool.iterdir():
        if SPOOL_NAME.fullmatch(path.name) and path.is_file():
            valid.append(path)
        else:
            path.unlink(missing_ok=True)
    for path in sorted(valid, key=lambda p: (p.stat().st_mtime, p.name)):
        match = SPOOL_NAME.fullmatch(path.name)
        path.unlink(missing_ok=True)
        return match["id"], match["kind"]
    return None


# ------------------------------------------------------------ database steps

def _audit(db, row, action, **details):
    from app.services import nfse
    nfse.record(db, f"production_{row.kind}_{action}", "fiscal_document", row.document_id,
                row.authorized_by, f"m66:{row.id}", issuance_request_id=row.id, **details)


def claim(sessionmaker, request_id: str, kind: str) -> dict | None:
    """``authorized`` -> ``running``, exactly once. Returns the row facts, or
    None when there is nothing this worker may do with the request."""
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    from app.models import FiscalIssuanceRequest, utcnow
    with sessionmaker() as db:
        row = db.get(FiscalIssuanceRequest, request_id)
        if row is None or row.status != "authorized":
            return None
        now = utcnow()
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if row.kind != kind:
            row.status, row.finished_at, row.result_code = "refused", now, "doorbell_kind_mismatch"
            _audit(db, row, "refused", codigo=row.result_code)
            db.commit()
            return None
        if expires <= now:
            row.status, row.finished_at, row.result_code = "expired", now, "authorization_expired"
            _audit(db, row, "refused", codigo=row.result_code)
            db.commit()
            return None
        try:
            result = db.execute(update(FiscalIssuanceRequest).where(
                FiscalIssuanceRequest.id == request_id,
                FiscalIssuanceRequest.status == "authorized",
            ).values(status="running", claimed_at=now))
            if result.rowcount != 1:
                db.rollback()
                return None
            db.flush()
        except IntegrityError:
            # The claimed-issue index: this document was already handed to a
            # worker once. Never a second time.
            db.rollback()
            row = db.get(FiscalIssuanceRequest, request_id)
            row.status, row.finished_at, row.result_code = "refused", utcnow(), "document_already_sent_once"
            _audit(db, row, "refused", codigo=row.result_code)
            db.commit()
            return None
        row = db.get(FiscalIssuanceRequest, request_id)
        _audit(db, row, "claimed", status="running")
        db.commit()
        return {"id": row.id, "document_id": row.document_id, "kind": row.kind,
                "exam_id": row.spirometry_exam_id, "preparation_id": row.preparation_id,
                "preparation_fingerprint": row.preparation_fingerprint,
                "recipient_fingerprint": row.recipient_fingerprint,
                "amount": Decimal(row.amount_confirmed), "actor": row.authorized_by}


def guards(db, facts: dict, settings) -> tuple[list[str], dict]:
    """Every pre-POST guard, read-only. Returns (failures, expected DPS facts)."""
    from sqlalchemy import select

    from app.models import FiscalAttempt, FiscalDocument, Person, SpirometryExam
    from app.services import nfse
    from app.services.idempotency import payload_fingerprint
    from app.services.nfse_national import clock as clock_module
    from app.services.nfse_national.certificate_guard import evaluate_certificate_margin
    from app.services.nfse_national.readiness import compute_provider_readiness
    from app.services.nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES
    from app.services.nfse_production_issuance import recipient_fingerprint

    failures: list[str] = []

    def check(name, ok):
        if not ok:
            failures.append(name)

    doc = db.get(FiscalDocument, facts["document_id"])
    if doc is None:
        return ["document_missing"], {}
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    check("document_environment", doc.environment == "production")
    check("document_exam_mismatch", doc.spirometry_exam_id == facts["exam_id"])
    expected: dict = {}

    if facts["kind"] == "reconcile":
        check("reconciliation_not_required", doc.state in nfse.IN_FLIGHT | {"uncertain"})
    else:
        check("document_not_pending", doc.state == "pending")
        check("document_not_eligible", doc.eligibility == "eligible" and not doc.blocking_reasons)
        prep = nfse.latest_preparation(db, doc.id)
        if prep is None:
            return failures + ["preparation_missing"], {}
        check("preparation_changed", prep.id == facts["preparation_id"]
              and prep.fingerprint == facts["preparation_fingerprint"])
        current = nfse.evaluate(db, doc.spirometry_exam_id, "production", for_update=False)
        check("eligibility_now_blocked", not current["blocking_reasons"])
        check("preparation_stale", prep.fingerprint == payload_fingerprint(current))
        check("flow_not_supported", prep.flow in SUPPORTED_FLOWS)
        check("amount_mismatch", prep.amount_snapshot is not None
              and Decimal(prep.amount_snapshot) == facts["amount"] and facts["amount"] > 0)
        person = db.get(Person, prep.recipient_person_id) if prep.recipient_person_id else None
        check("recipient_changed", recipient_fingerprint(person) == facts["recipient_fingerprint"])
        check("service_location_changed",
              exam is not None and prep.service_municipio_ibge == exam.municipio_atendimento_ibge
              and prep.service_municipio_ibge in SUPPORTED_SERVICE_MUNICIPALITIES)
        attempts = db.scalar(select(FiscalAttempt.id).where(
            FiscalAttempt.document_id == doc.id).limit(1))
        check("document_already_attempted", attempts is None)
        expected = {"tpAmb": "1", "dCompet": prep.competence.isoformat() if prep.competence else None,
                    "cLocPrestacao": prep.service_municipio_ibge,
                    "vServ": f"{Decimal(prep.amount_snapshot or 0):.2f}"}

    clock = clock_module.read_clock_status()
    check("clock_not_synchronized", clock.synchronized)
    readiness = compute_provider_readiness(db, settings, environment="restricted")
    margin = evaluate_certificate_margin(
        readiness.certificate_summary,
        min_days_remaining=settings.nfse_production_certificate_min_days)
    check("certificate_margin_insufficient", margin.valid)
    check("endpoint_unexpected", PRODUCTION_BASE_URL == EXPECTED_ENDPOINT
          and "producaorestrita" not in PRODUCTION_BASE_URL)
    return failures, expected


def _document_state(sessionmaker, document_id: str) -> dict:
    from sqlalchemy import select

    from app.models import FiscalArtifact, FiscalAttempt, FiscalDocument
    from app.services.nfse_validity import fiscal_validity
    with sessionmaker() as db:
        doc = db.get(FiscalDocument, document_id)
        attempts = db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == document_id)
                              .order_by(FiscalAttempt.number, FiscalAttempt.phase.desc())).all()
        external = next((a.external_id for a in reversed(attempts)
                         if a.phase == "completed" and a.external_id), None)
        kinds = set(db.scalars(select(FiscalArtifact.kind).where(
            FiscalArtifact.document_id == document_id)).all())
        return {
            "state": doc.state,
            "fiscal_validity": fiscal_validity(db, doc),
            "external_id": external,
            "has_nfse_xml": "nfse_xml" in kinds,
            "issue_operation_id": next((a.operation_id for a in attempts
                                        if a.operation == "issue" and a.phase == "started"), None),
            "issue_started": any(a.operation == "issue" and a.phase == "started" for a in attempts),
            "reconcile_operation_id": next((a.operation_id for a in reversed(attempts)
                                            if a.operation == "reconcile"), None),
        }


def _fetch_nfse_document(sessionmaker, settings, facts: dict, access_key: str) -> dict:
    """Only when reconciliation settled ``issued`` without the NFS-e XML (the
    M58 contract needs it): one GET /nfse/{chave}, stored as evidence on the
    reconcile attempt. Read-only toward SEFIN."""
    from sqlalchemy import select

    from app.models import FiscalArtifact, FiscalAttempt
    from app.services import nfse
    from app.services.nfse_national import artifacts as artifact_storage
    from app.services.nfse_national import dispatch
    from app.services.nfse_national.wire import JSON_ACCEPT_HEADERS, decode_nfse_document_response
    with sessionmaker() as db:
        doc = nfse.get_document(db, facts["document_id"])
        prep = nfse.latest_preparation(db, doc.id)
        provider = dispatch.resolve_national_provider(db, settings, doc, prep, facts["actor"])
        response = provider._transport.send(TransportRequest(
            method="GET", path=PATH_GET_NFSE.format(chave_acesso=access_key.removeprefix("NFS")),
            headers=dict(JSON_ACCEPT_HEADERS)))
        if not 200 <= response.status_code < 300:
            db.rollback()
            return {"http_status": response.status_code, "stored": False}
        decoded = decode_nfse_document_response(response.body, expected_access_key=access_key,
                                                expected_tp_amb=1)
        attempt = db.scalars(select(FiscalAttempt).where(
            FiscalAttempt.document_id == doc.id, FiscalAttempt.operation == "reconcile",
            FiscalAttempt.phase == "completed").order_by(FiscalAttempt.number.desc())).first()
        stored = artifact_storage.write_artifact(
            settings.resolved_fiscal_artifacts_storage_dir(), document_id=doc.id,
            attempt_id=attempt.id, kind="nfse_xml", data=decoded.nfse_xml)
        db.add(FiscalArtifact(document_id=doc.id, attempt_id=attempt.id, kind="nfse_xml",
                              storage_relative_path=str(stored.relative_path), sha256=stored.sha256,
                              size_bytes=stored.size_bytes, created_by=facts["actor"]))
        nfse.record(db, "nfse_document_fetched", "fiscal_document", doc.id, facts["actor"],
                    f"m66:{facts['id']}", evidencia_nfse_recebida_sha256=stored.sha256)
        db.commit()
        return {"http_status": response.status_code, "stored": True, "sha256": stored.sha256}


def finish(sessionmaker, request_id: str, status: str, result_code: str | None, **fields) -> None:
    from app.models import FiscalIssuanceRequest, utcnow
    with sessionmaker() as db:
        row = db.get(FiscalIssuanceRequest, request_id)
        row.status, row.result_code, row.finished_at = status, result_code, utcnow()
        row.provider_post_count = LEDGER.post_count
        row.provider_get_count = LEDGER.get_count
        for key, value in fields.items():
            setattr(row, key, value)
        _audit(db, row, "finished", status=status, codigo=result_code,
               operation_id=row.operation_id, external_id=row.external_id,
               provider_post_count=LEDGER.post_count, provider_get_count=LEDGER.get_count,
               second_post_attempted=LEDGER.second_post_attempted)
        db.commit()


def _reconcile_phase(sessionmaker, settings, facts: dict, report: dict) -> dict:
    from app.services import nfse
    LEDGER.phase = "reconcile"
    try:
        with sessionmaker() as db:
            nfse.operate(db, facts["document_id"], "reconcile", f"m66-reconcile:{facts['id']}",
                         settings, facts["actor"], f"m66:{facts['id']}")
    except Exception as exc:
        report["operate_reconcile_error"] = _error_code(exc)
    state = _document_state(sessionmaker, facts["document_id"])
    if state["state"] == "issued" and state["external_id"] and not state["has_nfse_xml"]:
        try:
            report["nfse_document_fetch"] = _fetch_nfse_document(
                sessionmaker, settings, facts, state["external_id"])
        except Exception as exc:
            report["nfse_document_fetch_error"] = type(exc).__name__
        state = _document_state(sessionmaker, facts["document_id"])
    LEDGER.phase = "closed"
    return state


def _error_code(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and detail.get("codigo"):
        return str(detail["codigo"])
    return type(exc).__name__


def run_request(request_id: str, kind: str, *, sessionmaker, settings_factory,
                runtime_root: Path) -> dict:
    """Claim, guard, send once, settle. Returns a report with no PII."""
    from app.services import nfse
    LEDGER.reset()
    report: dict = {"request_id": request_id, "kind": kind}
    facts = claim(sessionmaker, request_id, kind)
    if facts is None:
        report["claimed"] = False
        return report
    report["claimed"] = True
    LEDGER.claimed_request_id = request_id

    def refuse(code):
        report["refused"] = code
        finish(sessionmaker, request_id, "refused", code)
        return report

    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_root, 0o700)
    LEDGER.runtime_dir = runtime_root / request_id
    try:
        LEDGER.runtime_dir.mkdir(mode=0o700)
    except FileExistsError:
        return refuse("runtime_dir_exists")
    try:
        settings = settings_factory()
    except Exception as exc:
        return refuse(str(exc) if isinstance(exc, WorkerConfigError) else "credentials_unavailable")

    with sessionmaker() as db:
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SET TRANSACTION READ ONLY"))
        failures, expected = guards(db, facts, settings)
        db.rollback()
    if failures:
        report["guard_failures"] = failures
        return refuse(failures[0])
    LEDGER.expected = expected

    if kind == "issue":
        LEDGER.phase = "issue"
        try:
            with sessionmaker() as db:
                nfse.operate(db, facts["document_id"], "issue", f"m66-issue:{request_id}",
                             settings, facts["actor"], f"m66:{request_id}")
        except Exception as exc:
            report["operate_issue_error"] = _error_code(exc)
        LEDGER.phase = "closed"
        state = _document_state(sessionmaker, facts["document_id"])
        report["after_issue"] = state["state"]
        if not state["issue_started"] and LEDGER.post_count == 0:
            # Provably nothing left this process and nothing was recorded as
            # started: operate() refused before the provider boundary.
            return refuse(report.get("operate_issue_error") or "issue_not_started")
        if state["state"] == "uncertain" and LEDGER.post_count == 1:
            state = _reconcile_phase(sessionmaker, settings, facts, report)
        status = {"issued": "issued", "failed": "rejected"}.get(state["state"], "uncertain")
        operation_id = state["issue_operation_id"]
    else:
        state = _reconcile_phase(sessionmaker, settings, facts, report)
        status = {"issued": "reconciled", "failed": "rejected"}.get(state["state"], "uncertain")
        operation_id = state["reconcile_operation_id"]

    result_code = (None if status in ("issued", "reconciled") else
                   "pre_send_guard_failed" if LEDGER.pre_send_refusal else f"document_{state['state']}")
    finish(sessionmaker, request_id, status, result_code, operation_id=operation_id,
           external_id=state["external_id"] if state["state"] == "issued" else None)
    report.update(final_state=state["state"], status=status, fiscal_validity=state["fiscal_validity"],
                  external_id=state["external_id"], operation_id=operation_id,
                  provider_post_count=LEDGER.post_count, provider_get_count=LEDGER.get_count,
                  second_post_attempted=LEDGER.second_post_attempted, events=LEDGER.events)
    _write_private(LEDGER.runtime_dir / "result.json",
                   json.dumps(report, indent=2, ensure_ascii=False, default=str).encode())
    return report


# ------------------------------------------------------------ main

def self_check() -> int:
    """Installation check, safe to run at any time: decrypt the credentials,
    open the A1, report its validity window and the clock. Never touches the
    spool or a request, never builds a transport, never opens a socket.
    Prints no secret — subject CN, dates and booleans only."""
    from app.services.nfse_national import clock as clock_module
    from app.services.nfse_national.certificate_guard import evaluate_certificate_margin
    from app.services.nfse_national.readiness import compute_provider_readiness
    from app.db import get_sessionmaker

    def refuse(*args, **kwargs):
        raise OneShotViolation("self-check nunca usa rede")
    httpx.Client = refuse
    httpx.AsyncClient = refuse
    try:
        settings = build_worker_settings()
    except WorkerConfigError as exc:
        print(json.dumps({"self_check": "failed", "codigo": str(exc)}))
        return 2
    with get_sessionmaker()() as db:
        readiness = compute_provider_readiness(db, settings, environment="restricted")
        db.rollback()
    summary = readiness.certificate_summary
    margin = evaluate_certificate_margin(
        summary, min_days_remaining=settings.nfse_production_certificate_min_days)
    clock = clock_module.read_clock_status()
    # M69 — the same permission rule the real run applies; before M69 the
    # self-check passed while every real run refused on it.
    permissions_ok = "restricted_certificate_path_permissions_too_open" not in readiness.blockers
    report = {
        "self_check": "ok" if (margin.valid and clock.synchronized and permissions_ok) else "attention",
        "certificate_readable": readiness.certificate_syntactically_valid is True,
        "certificate_permissions_ok": permissions_ok,
        "certificate_subject": getattr(summary, "subject_common_name", None),
        "certificate_not_after": str(getattr(summary, "not_after", None)),
        "certificate_days_remaining": margin.days_remaining,
        "certificate_margin_ok": margin.valid,
        "clock_synchronized": clock.synchronized,
        "network_used": False,
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["certificate_readable"] and permissions_ok else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--self-check", action="store_true",
                        help="só verifica credenciais, certificado e relógio; nunca envia nada")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    from app.config import get_settings
    from app.db import get_sessionmaker
    base = get_settings()
    spool, runtime = base.nfse_production_worker_spool_dir, base.nfse_production_worker_runtime_dir
    if spool is None or runtime is None or not Path(runtime).is_absolute():
        print("ERRO: M15_NFSE_PRODUCTION_WORKER_SPOOL_DIR / _RUNTIME_DIR não configurados.",
              file=sys.stderr)
        return 2
    doorbell = take_doorbell(Path(spool))
    if doorbell is None:
        print("nada a fazer: nenhum pedido na fila")
        return 0
    request_id, kind = doorbell
    _install_network_guards()
    try:
        report = run_request(request_id, kind, sessionmaker=get_sessionmaker(),
                             settings_factory=build_worker_settings, runtime_root=Path(runtime))
    finally:
        print(f"[ledger] request={request_id} kind={kind} post={LEDGER.post_count} "
              f"get={LEDGER.get_count} second_post_attempted={LEDGER.second_post_attempted}",
              flush=True)
    summary = {k: report.get(k) for k in ("request_id", "kind", "claimed", "refused", "status",
                                          "final_state", "fiscal_validity", "external_id",
                                          "operation_id", "provider_post_count")}
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if LEDGER.post_count <= 1 and not LEDGER.second_post_attempted else 4


if __name__ == "__main__":
    raise SystemExit(main())
