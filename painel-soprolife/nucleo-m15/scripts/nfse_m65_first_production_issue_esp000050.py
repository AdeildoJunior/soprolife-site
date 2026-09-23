#!/usr/bin/env python3
"""M65 — the FIRST real production NFS-e: ESP-000050, R$ 220,00. ONE POST, NEVER TWO.

THE AUTHORIZATION

Given by the user, in the M65 mission text, for exactly this:

    "Autorizo emitir a NFS-e real de produção do ESP-000050, R$ 220."

document 7feee219-1bf9-4150-af40-10e7417f6baa, DPS série 00001 nº 1. It is
carried into the code in exactly one place — the constructor of
``M65OneShotProductionTransport`` below passes
``explicit_human_authorization=True`` — and nowhere else: not Settings, not
the environment, not the database. It dies with this process.

WHAT THIS DOES, IN ORDER

  1. Refuses unless ``M15_NFSE_PRODUCTION_NETWORK_ENABLED=true`` is set for
     THIS process only (the launcher sets it on the exec line; nothing
     persistent carries it).
  2. Refuses if the single-fire marker already exists. A second POST is
     structurally impossible, whoever runs this and however often.
  3. Re-checks every M65 guard against the operational database, read-only:
     the document, its state, the preparation snapshot, the DPS allocation,
     zero attempts, the five manual NFS-e, ESP-000051 untouched, clock,
     certificate, the exact production endpoint.
  4. Builds the DPS once OFFLINE, through the very provider resolution the
     real call will use but with a transport that refuses, and proves it is
     the M64 preflight DPS in everything but ``dhEmi``.
  5. Claims the single-fire marker, then calls ``nfse.operate(issue)``: the
     durable ``started`` attempt is committed BEFORE the provider boundary,
     and the transport writes the exact signed XML it is about to send to a
     private file, fsync'd, BEFORE the socket opens.
  6. Exactly one POST. No retry for any reason. A second POST raises before
     it is built.
  7. If — and only if — the outcome is ``uncertain``: one read-only
     reconciliation (GET /dps/{idDPS}); and, only if that yields the key
     without the NFS-e document, one GET /nfse/{chave}. No POST, ever.
  8. Reports the final state, ``fiscal_validity``, artifacts, and the call
     ledger (POST count, GET count, whether a second POST was attempted).

THE PASSWORD

Read only from the terminal, never echoed, never stored, never in argv or env.

Usage (in a REAL terminal, through the private launcher):

    run-m65-issue-esp000050.sh --confirm ESP-000050
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.services.nfse_national.transport import (  # noqa: E402
    PATH_GET_DPS, PATH_GET_NFSE, PATH_ISSUE_NFSE, PRODUCTION_BASE_URL,
    HttpxProductionTransport, TransportRequest, TransportResponse)

TARGET = "ESP-000050"
DOCUMENT_ID = "7feee219-1bf9-4150-af40-10e7417f6baa"
EXPECTED_AMOUNT = Decimal("220.00")
EXPECTED_COMPETENCE = date(2026, 9, 16)
EXPECTED_FLOW = "DIRECT"
EXPECTED_MUNICIPALITY = "3304557"
EXPECTED_DPS_NUMBER = 1
EXPECTED_SCOPE = "3304557:2:63544026000110:00001"
EXPECTED_ISSUER_CNPJ = "63544026000110"
EXPECTED_ENDPOINT = "https://sefin.nfse.gov.br/SefinNacional"
PREFLIGHT_SIGNED_SHA256 = "282c16af29216d70fc3bf149b1150c0900289a29aad8427c1c568820c6ae180c"
RESERVE = "ESP-000051"
PROTECTED_MANUAL_NOTES = {
    "ESP-000039": "33045572263544026000110000000000000426099130462655",
    "ESP-000046": "33045572263544026000110000000000000526092817158327",
    "ESP-000048": "33045572263544026000110000000000000626096136136469",
    "ESP-000049": "33045572263544026000110000000000000726091190747856",
    "ESP-000053": "33045572263544026000110000000000000826099736433520",
}
ISSUE_KEY = "m65-esp000050-first-production-issue"
RECONCILE_KEY = "m65-esp000050-readonly-reconcile-1"
REQUEST_ID = "m65-esp000050"
CERTIFICATE = Path("~/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx").expanduser()
ARTIFACTS = Path("~/.local/share/soprolife/nfse-production/artifacts").expanduser()
RUNTIME = Path("~/.local/share/soprolife/nfse-production/runtime/m65").expanduser()
FIRE_MARKER_NAME = "m65-esp000050-post-fired.marker"
POST_TIMEOUT_SECONDS = 60.0
MAX_GETS = 2   # GET /dps/{id} and, only if needed, GET /nfse/{chave}

# The client constructor this process is allowed to use, and only from inside
# the one-shot transport's send(). Tests replace it with a mock-backed one.
_ORIGINAL_HTTPX_CLIENT = httpx.Client


class OneShotViolation(RuntimeError):
    """A second POST, an unexpected method/path, or a GET before it is allowed."""


class PreSendGuardFailed(RuntimeError):
    """The DPS about to leave is not the one M64 preflighted. Nothing was sent."""


class _Ledger:
    """Every outbound call this process attempts, counted BEFORE it is made."""

    def __init__(self):
        self.post_count = 0
        self.get_count = 0
        self.gets_allowed = False
        self.second_post_attempted = False
        self.inside_send = False
        self.reference_dps: bytes | None = None
        self.events: list[dict] = []


LEDGER = _Ledger()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_private(path: Path, data: bytes) -> str:
    """0600, fsync'd, never overwriting. Returns the sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ DPS identity

def _ns():
    from app.services.nfse_national.dps_builder import NFSE_NS
    return NFSE_NS


def dps_identity(xml_bytes: bytes) -> bytes:
    """Canonical infDPS without ``dhEmi`` and ``verAplic``: everything that must
    NOT change between the preflight and the send (série, nDPS, valor,
    competência, tomador, município, serviço, prestador, Id).

    ``dhEmi`` is stamped at send time by design. ``verAplic`` is the software
    label, not a fiscal fact: the offline preflight stamps ``sl-preflight-0.1``
    and the provider ``soprolife-m29-0.1``."""
    from lxml import etree
    ns = _ns()
    root = etree.fromstring(xml_bytes)
    inf = root.find(f".//{{{ns}}}infDPS")
    if inf is None:
        raise PreSendGuardFailed("infDPS ausente")
    inf = etree.fromstring(etree.tostring(inf))
    for tag in ("dhEmi", "verAplic"):
        for node in inf.findall(f".//{{{ns}}}{tag}"):
            node.getparent().remove(node)
    return etree.tostring(inf, method="c14n")


def dps_summary(xml_bytes: bytes) -> dict:
    """Non-personal facts of a DPS, for the report. Never the tomador."""
    from lxml import etree
    ns = _ns()
    root = etree.fromstring(xml_bytes)

    def text(path):
        node = root.find(path)
        return node.text if node is not None else None
    inf = root.find(f".//{{{ns}}}infDPS")
    return {
        "id": inf.get("Id") if inf is not None else None,
        "tpAmb": text(f".//{{{ns}}}tpAmb"),
        "dhEmi": text(f".//{{{ns}}}dhEmi"),
        "verAplic": text(f".//{{{ns}}}verAplic"),
        "serie": text(f".//{{{ns}}}serie"),
        "nDPS": text(f".//{{{ns}}}nDPS"),
        "dCompet": text(f".//{{{ns}}}dCompet"),
        "prest_cnpj": text(f".//{{{ns}}}prest/{{{ns}}}CNPJ"),
        "toma_cpf_present": root.find(f".//{{{ns}}}toma/{{{ns}}}CPF") is not None,
        "cLocPrestacao": text(f".//{{{ns}}}cLocPrestacao"),
        "cTribNac": text(f".//{{{ns}}}cTribNac"),
        "vServ": text(f".//{{{ns}}}vServ"),
    }


def assert_same_dps(candidate: bytes, *, label: str) -> dict:
    """Refuse unless ``candidate`` is the preflight DPS but for ``dhEmi``, and
    carries the M65 facts literally. Returns its non-personal summary."""
    from app.services.nfse_national.production_preflight import (count_namespace_prefixes,
                                                                 dh_emi_matches_emission_timezone)
    if LEDGER.reference_dps is None:
        raise PreSendGuardFailed(f"{label}: DPS de referência do preflight não carregada")
    if dps_identity(candidate) != dps_identity(LEDGER.reference_dps):
        raise PreSendGuardFailed(f"{label}: a DPS difere da do preflight M64 "
                                 "(além de dhEmi/verAplic)")
    summary = dps_summary(candidate)
    expected = {"tpAmb": "1", "serie": "1", "nDPS": str(EXPECTED_DPS_NUMBER),
                "dCompet": EXPECTED_COMPETENCE.isoformat(), "prest_cnpj": EXPECTED_ISSUER_CNPJ,
                "cLocPrestacao": EXPECTED_MUNICIPALITY, "vServ": f"{EXPECTED_AMOUNT:.2f}"}
    wrong = {k: summary[k] for k, v in expected.items() if summary[k] != v}
    if wrong or not summary["toma_cpf_present"]:
        raise PreSendGuardFailed(f"{label}: campos divergentes {sorted(wrong)}")
    if count_namespace_prefixes(candidate) != 0:
        raise PreSendGuardFailed(f"{label}: prefixo de namespace presente")
    if not dh_emi_matches_emission_timezone(summary["dhEmi"]):
        raise PreSendGuardFailed(f"{label}: dhEmi fora do fuso de emissão")
    stamped = datetime.fromisoformat(summary["dhEmi"])
    if abs(datetime.now(timezone.utc) - stamped) > timedelta(minutes=5):
        raise PreSendGuardFailed(f"{label}: dhEmi a mais de 5 minutos do relógio")
    return summary


# ------------------------------------------------------------ the transport

class M65OneShotProductionTransport(HttpxProductionTransport):
    """The production transport, with the M65 human authorization and a hard
    one-POST ledger. Installed in place of ``dispatch.HttpxProductionTransport``
    for this process only, so ``nfse.operate()`` runs its ordinary production
    path — every dispatch re-check included — and this is the only thing
    that differs."""

    def __init__(self, *, base_url: str = PRODUCTION_BASE_URL, network_enabled: bool = False,
                 environment: str = "", timeout_seconds: float = POST_TIMEOUT_SECONDS,
                 mtls_certificate_pem: bytes | None = None, mtls_key_pem: bytes | None = None,
                 explicit_human_authorization: bool = False):
        super().__init__(base_url=base_url, network_enabled=network_enabled,
                         environment=environment,
                         timeout_seconds=max(timeout_seconds, POST_TIMEOUT_SECONDS),
                         mtls_certificate_pem=mtls_certificate_pem, mtls_key_pem=mtls_key_pem,
                         # M65 — the user's explicit authorization for ESP-000050,
                         # R$ 220,00, DPS 00001 nº 1. In code, in this process only.
                         explicit_human_authorization=True)

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
            raise OneShotViolation("SEGUNDO POST RECUSADO — a M65 autoriza exatamente um")
        if request.path != PATH_ISSUE_NFSE:
            raise OneShotViolation(f"POST para caminho inesperado: {request.path}")
        from app.services.nfse_national.wire import decode_b64_gzip_xml
        submitted = decode_b64_gzip_xml(json.loads(request.body)["dpsXmlGZipB64"])
        summary = assert_same_dps(submitted, label="pré-envio")
        # Durable before the boundary: the exact bytes, and the exact body.
        xml_sha = _write_private(RUNTIME / "submitted-dps-signed.xml", submitted)
        body_sha = hashlib.sha256(request.body).hexdigest()
        intent = {"document_id": DOCUMENT_ID, "target": TARGET, "dps": summary,
                  "submitted_xml_sha256": xml_sha, "request_body_sha256": body_sha,
                  "endpoint": self._base_url + request.path, "intent_at_utc": _utcnow()}
        _write_private(RUNTIME / "post-intent.json",
                       json.dumps(intent, indent=2, ensure_ascii=False).encode())
        LEDGER.post_count += 1   # counted before the socket: an attempt is an attempt
        event = {"method": "POST", "path": request.path, "started_at_utc": _utcnow(),
                 "submitted_xml_sha256": xml_sha, "request_body_sha256": body_sha,
                 "dhEmi": summary["dhEmi"]}
        LEDGER.events.append(event)
        return self._forward(request, event, "post-response.body")

    def _get(self, request: TransportRequest) -> TransportResponse:
        if not LEDGER.gets_allowed:
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
                     body_sha256=_write_private(RUNTIME / body_name, response.body or b""))
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
    dispatch.HttpxProductionTransport = M65OneShotProductionTransport


class _RefusingTransport:
    def send(self, request):
        raise OneShotViolation("transporte do build offline nunca envia")


# ------------------------------------------------------------ helpers

def _password() -> str:
    if not sys.stdin.isatty():
        print("ERRO: a senha só é aceite a partir de um terminal real.", file=sys.stderr)
        raise SystemExit(2)
    value = getpass.getpass("Senha do certificado A1 (não é exibida nem guardada): ")
    if not value:
        print("ERRO: senha vazia.", file=sys.stderr)
        raise SystemExit(2)
    return value


def _claim_single_fire():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    os.chmod(RUNTIME, 0o700)
    try:
        fd = os.open(RUNTIME / FIRE_MARKER_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise OneShotViolation("marcador de disparo já existe")
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps({"claimed_at_utc": _utcnow(), "document_id": DOCUMENT_ID,
                                 "target": TARGET, "dps_number": EXPECTED_DPS_NUMBER,
                                 "pid": os.getpid()}, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _guards(db, settings) -> tuple[list[str], dict, object]:
    """Every M65 pre-POST guard. Returns (failures, facts, actor)."""
    from sqlalchemy import func, select

    from app.models import (DpsNumberAllocation, FiscalArtifact, FiscalAttempt, FiscalDocument,
                            SpirometryExam, User)
    from app.services import nfse
    from app.services.idempotency import payload_fingerprint
    from app.services.nfse_external_issuance import IMPORT_OPERATION
    from app.services.nfse_national import artifacts as artifact_storage
    from app.services.nfse_national import clock as clock_module
    from app.services.nfse_national.certificate_guard import evaluate_certificate_margin
    from app.services.nfse_national.readiness import compute_provider_readiness

    failures: list[str] = []
    facts: dict = {}

    def check(name, ok, value=None):
        facts[name] = value if value is not None else ok
        if not ok:
            failures.append(name)

    doc = db.get(FiscalDocument, DOCUMENT_ID)
    if doc is None:
        return ["document_missing"], facts, None
    exam = db.get(SpirometryExam, doc.spirometry_exam_id)
    check("exam", exam is not None and exam.public_code == TARGET, exam.public_code if exam else None)
    check("environment", doc.environment == "production", doc.environment)
    check("state", doc.state == "pending", doc.state)
    check("eligibility", doc.eligibility == "eligible", doc.eligibility)
    check("document_blocking_reasons", not doc.blocking_reasons, doc.blocking_reasons)

    prep = nfse.latest_preparation(db, doc.id)
    check("preparation", prep is not None, prep.id if prep else None)
    if prep is None:
        return failures, facts, None
    check("flow", prep.flow == EXPECTED_FLOW, prep.flow)
    check("competence", prep.competence == EXPECTED_COMPETENCE, str(prep.competence))
    check("service_date", prep.service_date == EXPECTED_COMPETENCE, str(prep.service_date))
    check("amount", Decimal(prep.amount_snapshot) == EXPECTED_AMOUNT, str(prep.amount_snapshot))
    check("municipio_preparation", prep.service_municipio_ibge == EXPECTED_MUNICIPALITY,
          prep.service_municipio_ibge)
    check("municipio_exam", exam.municipio_atendimento_ibge == EXPECTED_MUNICIPALITY,
          exam.municipio_atendimento_ibge)
    check("broncodilatador", exam.broncodilatador is True, exam.broncodilatador)

    current = nfse.evaluate(db, exam.id, "production", for_update=False)
    check("evaluate_blockers", not current["blocking_reasons"], current["blocking_reasons"])
    check("preparation_not_stale", prep.fingerprint == payload_fingerprint(current))

    attempts = db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == doc.id)).all()
    check("document_attempts", len(attempts) == 0, len(attempts))
    issue_n = db.scalar(select(func.count()).select_from(FiscalAttempt)
                        .where(FiscalAttempt.operation == "issue"))
    reconcile_n = db.scalar(select(func.count()).select_from(FiscalAttempt)
                            .where(FiscalAttempt.operation == "reconcile"))
    check("fiscal_attempt_issue_total", issue_n == 0, issue_n)
    check("fiscal_attempt_reconcile_total", reconcile_n == 0, reconcile_n)
    check("reconciliation_required", not any(a.reconciliation_required for a in attempts), False)

    allocation = db.get(DpsNumberAllocation, doc.id)
    check("dps_number", allocation is not None and allocation.dps_number == EXPECTED_DPS_NUMBER,
          allocation.dps_number if allocation else None)
    check("dps_scope", allocation is not None and allocation.scope_key == EXPECTED_SCOPE,
          allocation.scope_key if allocation else None)
    allocations = db.scalar(select(func.count()).select_from(DpsNumberAllocation))
    check("dps_allocations_total", allocations == 1, allocations)

    notes = {}
    for code, key in PROTECTED_MANUAL_NOTES.items():
        row = db.execute(
            select(FiscalDocument.state, FiscalDocument.environment, FiscalAttempt.provider,
                   FiscalAttempt.external_id)
            .join(FiscalAttempt, FiscalAttempt.document_id == FiscalDocument.id)
            .join(SpirometryExam, SpirometryExam.id == FiscalDocument.spirometry_exam_id)
            .where(SpirometryExam.public_code == code,
                   FiscalAttempt.operation == IMPORT_OPERATION)).first()
        ok = (row is not None and row.state == "issued" and row.environment == "production"
              and row.provider == "external_manual" and row.external_id == "NFS" + key)
        notes[code] = "production/issued import/external_manual" if ok else "DIVERGENTE"
        if not ok:
            failures.append(f"manual_note:{code}")
    facts["manual_notes"] = notes

    reserve_docs = db.scalar(
        select(func.count()).select_from(FiscalDocument)
        .join(SpirometryExam, SpirometryExam.id == FiscalDocument.spirometry_exam_id)
        .where(SpirometryExam.public_code == RESERVE))
    check("esp000051_fiscal_documents", reserve_docs == 0, reserve_docs)

    clock = clock_module.read_clock_status()
    check("clock_synchronized", clock.synchronized, clock.as_dict())
    readiness = compute_provider_readiness(db, settings, environment="restricted")
    margin = evaluate_certificate_margin(
        readiness.certificate_summary,
        min_days_remaining=settings.nfse_production_certificate_min_days)
    check("certificate", margin.valid, {"valid": margin.valid, "reason": margin.reason,
                                        "days_remaining": margin.days_remaining,
                                        "required_days": margin.required_days})
    check("endpoint", PRODUCTION_BASE_URL == EXPECTED_ENDPOINT
          and "producaorestrita" not in PRODUCTION_BASE_URL, PRODUCTION_BASE_URL)

    # The M64 preflight DPS, read back and checked against its recorded hash.
    reference = None
    for artifact in db.scalars(select(FiscalArtifact).where(
            FiscalArtifact.document_id == doc.id, FiscalArtifact.kind == "dps_signed_xml",
            FiscalArtifact.attempt_id.is_(None))).all():
        if artifact.sha256 == PREFLIGHT_SIGNED_SHA256:
            data = artifact_storage.read_artifact(settings.resolved_fiscal_artifacts_storage_dir(),
                                                  Path(artifact.storage_relative_path))
            if hashlib.sha256(data).hexdigest() == PREFLIGHT_SIGNED_SHA256:
                reference = data
    check("preflight_dps_intact", reference is not None, PREFLIGHT_SIGNED_SHA256[:16])
    LEDGER.reference_dps = reference

    actor = db.scalars(select(User).order_by(User.created_at).limit(1)).first()
    return failures, facts, actor


def _offline_build(db, settings, actor_id) -> dict:
    """Build + sign the DPS once through the real provider resolution, with a
    transport that cannot send, and prove it is the preflight DPS."""
    from sqlalchemy import func, select

    from app.models import DpsNumberAllocation
    from app.services import nfse
    from app.services.nfse_national import dispatch
    before = db.scalar(select(func.count()).select_from(DpsNumberAllocation))
    doc = nfse.get_document(db, DOCUMENT_ID)
    prep = nfse.latest_preparation(db, doc.id)
    provider = dispatch.resolve_national_provider(db, settings, doc, prep, actor_id,
                                                  transport=_RefusingTransport())
    if provider.expected_tp_amb != 1 or provider.environment != "production":
        raise PreSendGuardFailed("provider não está ligado a produção/tpAmb=1")
    description = dispatch.resolve_service_description(db, doc)
    signed = provider._build_signed_dps(nfse._request(doc, prep, "m65-offline-build",
                                                      description=description))
    summary = assert_same_dps(signed, label="build offline")
    db.rollback()
    after = db.scalar(select(func.count()).select_from(DpsNumberAllocation))
    if before != after:
        raise PreSendGuardFailed("o build offline alterou as alocações de DPS")
    return summary


def _final_state(db, settings) -> dict:
    from sqlalchemy import func, select

    from app.models import DpsNumberAllocation, FiscalArtifact, FiscalAttempt, FiscalDocument
    from app.services.nfse_validity import fiscal_validity
    doc = db.get(FiscalDocument, DOCUMENT_ID)
    attempts = db.scalars(select(FiscalAttempt).where(FiscalAttempt.document_id == DOCUMENT_ID)
                          .order_by(FiscalAttempt.number, FiscalAttempt.phase.desc())).all()
    artifacts = db.scalars(select(FiscalArtifact).where(
        FiscalArtifact.document_id == DOCUMENT_ID).order_by(FiscalArtifact.created_at)).all()
    external = next((a.external_id for a in reversed(attempts)
                     if a.phase == "completed" and a.external_id), None)
    return {
        "state": doc.state,
        "reconciliation_required": bool(attempts and attempts[-1].reconciliation_required),
        "external_id": external,
        "fiscal_validity": fiscal_validity(db, doc),
        "attempts": [{"operation": a.operation, "phase": a.phase, "number": a.number,
                      "operation_id": a.operation_id, "outcome": a.outcome,
                      "error_code": a.error_code, "provider": a.provider,
                      "environment": a.environment,
                      "reconciliation_required": a.reconciliation_required,
                      "started_at": str(a.started_at), "completed_at": str(a.completed_at)}
                     for a in attempts],
        "artifacts": [{"kind": a.kind, "attempt": a.attempt_id, "sha256": a.sha256,
                       "size": a.size_bytes} for a in artifacts],
        "dps_allocations_total": db.scalar(select(func.count()).select_from(DpsNumberAllocation)),
        "dps_number_max": db.scalar(select(func.max(DpsNumberAllocation.dps_number))),
    }


def _fetch_nfse_document(db, settings, actor_id, access_key: str) -> dict:
    """Only when reconciliation settled ``issued`` without the NFS-e XML (the
    M58 contract needs it): one GET /nfse/{chave}, stored as evidence on the
    reconcile attempt. Read-only toward SEFIN."""
    from sqlalchemy import select

    from app.models import FiscalArtifact, FiscalAttempt
    from app.services import nfse
    from app.services.nfse_national import artifacts as artifact_storage
    from app.services.nfse_national import dispatch
    from app.services.nfse_national.wire import JSON_ACCEPT_HEADERS, decode_nfse_document_response
    doc = nfse.get_document(db, DOCUMENT_ID)
    prep = nfse.latest_preparation(db, doc.id)
    provider = dispatch.resolve_national_provider(db, settings, doc, prep, actor_id)
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
        FiscalAttempt.phase == "completed")).first()
    stored = artifact_storage.write_artifact(
        settings.resolved_fiscal_artifacts_storage_dir(), document_id=doc.id,
        attempt_id=attempt.id, kind="nfse_xml", data=decoded.nfse_xml)
    db.add(FiscalArtifact(document_id=doc.id, attempt_id=attempt.id, kind="nfse_xml",
                          storage_relative_path=str(stored.relative_path), sha256=stored.sha256,
                          size_bytes=stored.size_bytes, created_by=actor_id))
    nfse.record(db, "nfse_document_fetched", "fiscal_document", doc.id, actor_id, REQUEST_ID,
                evidencia_nfse_recebida_sha256=stored.sha256)
    db.commit()
    return {"http_status": response.status_code, "stored": True, "sha256": stored.sha256}


def _verdict(final: dict) -> str:
    if LEDGER.post_count == 0:
        return "STOPPED — NO POST WAS SENT"
    if final["state"] == "issued" and final["fiscal_validity"]:
        return "FIRST REAL PRODUCTION NFSE ISSUED SUCCESSFULLY"
    if final["state"] == "failed":
        return "FIRST REAL PRODUCTION NFSE REJECTED — NO RETRY PERFORMED"
    return "FIRST REAL PRODUCTION NFSE UNCERTAIN — DO NOT RETRY POST"


# ------------------------------------------------------------ main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", required=True,
                        help="tem de ser exatamente ESP-000050 (o único exame autorizado)")
    args = parser.parse_args()
    if args.confirm != TARGET:
        print(f"ERRO: --confirm tem de ser {TARGET}.", file=sys.stderr)
        return 2
    if not os.environ.get("M15_DATABASE_URL"):
        print("ERRO: M15_DATABASE_URL não definido.", file=sys.stderr)
        return 2
    if os.environ.get("M15_NFSE_PRODUCTION_NETWORK_ENABLED") != "true":
        print("ERRO: o gate de produção tem de vir ligado SÓ neste processo "
              "(M15_NFSE_PRODUCTION_NETWORK_ENABLED=true na linha do exec).", file=sys.stderr)
        return 2
    if (RUNTIME / FIRE_MARKER_NAME).exists():
        print(f"RECUSADO: {RUNTIME / FIRE_MARKER_NAME} já existe. A única tentativa autorizada "
              "já foi feita. NENHUM POST será enviado.", file=sys.stderr)
        return 3
    if not CERTIFICATE.is_file():
        print(f"ERRO: certificado não encontrado em {CERTIFICATE}", file=sys.stderr)
        return 2

    _install_network_guards()

    from app.config import Settings
    from app.db import get_sessionmaker
    from app.services import nfse

    password = _password()
    try:
        settings = Settings(
            nfse_enabled=True,
            nfse_environment="production",
            nfse_real_enabled=True,
            nfse_production_network_enabled=True,     # this process only
            nfse_restricted_certificate_path=CERTIFICATE,
            nfse_restricted_certificate_password=password,
            nfse_fiscal_artifacts_dir=ARTIFACTS,
        )
    finally:
        password = "\0" * len(password)
        del password
    assert settings.nfse_production_network_enabled is True
    assert settings.nfse_environment == "production"

    report: dict = {"target": TARGET, "document_id": DOCUMENT_ID,
                    "git_head": os.environ.get("M65_GIT_HEAD")}
    sessionmaker = get_sessionmaker()

    # ---- FASE 1: guards, read-only, session closed before anything else
    with sessionmaker() as db:
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SET TRANSACTION READ ONLY"))
        failures, facts, actor = _guards(db, settings)
        actor_id = actor.id if actor is not None else None
        db.rollback()
    report["guards"] = facts
    if failures or actor_id is None:
        report["guard_failures"] = failures
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print("\nPARADO SEM POST: guard divergente —", ", ".join(failures), file=sys.stderr)
        return 1

    try:
        with sessionmaker() as db:
            report["offline_build"] = _offline_build(db, settings, actor_id)
    except Exception as exc:
        report["offline_build_error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        print("\nPARADO SEM POST: o build offline não confere com o preflight.", file=sys.stderr)
        return 1
    report["ledger_before_post"] = {"post_count": LEDGER.post_count, "get_count": LEDGER.get_count}
    if LEDGER.post_count or LEDGER.get_count:
        print("PARADO: houve chamada antes da fase de envio.", file=sys.stderr)
        return 1

    # ---- FASE 4/5: durable intent (inside operate) + the single POST
    _claim_single_fire()
    try:
        return _issue_and_settle(report, settings, sessionmaker, actor_id)
    finally:
        # Whatever happened above — even a lost database connection — the
        # call ledger reaches the terminal.
        print(f"\n[ledger] provider_post_count = {LEDGER.post_count} | "
              f"provider_get_count = {LEDGER.get_count} | "
              f"second_post_attempted = {LEDGER.second_post_attempted}", flush=True)


def _issue_and_settle(report, settings, sessionmaker, actor_id) -> int:
    from app.services import nfse
    print(f"== {TARGET}: POST ÚNICO para {EXPECTED_ENDPOINT}/nfse — a enviar agora ==", flush=True)
    try:
        with sessionmaker() as db:
            nfse.operate(db, DOCUMENT_ID, "issue", ISSUE_KEY, settings, actor_id, REQUEST_ID)
    except Exception as exc:
        report["operate_issue_error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"

    with sessionmaker() as db:
        after_issue = _final_state(db, settings)
    report["after_issue"] = after_issue

    # ---- FASE 7: read-only reconciliation, only if the result is inconclusive
    if after_issue["state"] == "uncertain" and LEDGER.post_count == 1:
        LEDGER.gets_allowed = True
        try:
            with sessionmaker() as db:
                nfse.operate(db, DOCUMENT_ID, "reconcile", RECONCILE_KEY, settings, actor_id,
                             REQUEST_ID)
        except Exception as exc:
            report["operate_reconcile_error"] = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
        with sessionmaker() as db:
            reconciled = _final_state(db, settings)
        report["after_reconcile"] = reconciled
        if (reconciled["state"] == "issued" and reconciled["external_id"]
                and not any(a["kind"] == "nfse_xml" for a in reconciled["artifacts"])):
            try:
                with sessionmaker() as db:
                    report["nfse_document_fetch"] = _fetch_nfse_document(
                        db, settings, actor_id, reconciled["external_id"])
            except Exception as exc:
                report["nfse_document_fetch_error"] = type(exc).__name__
        LEDGER.gets_allowed = False

    with sessionmaker() as db:
        final = _final_state(db, settings)
    report["final"] = final
    report["ledger"] = {"provider_post_count": LEDGER.post_count,
                        "provider_get_count": LEDGER.get_count,
                        "second_post_attempted": LEDGER.second_post_attempted,
                        "events": LEDGER.events}
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    _write_private(RUNTIME / f"m65-result-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json",
                   json.dumps(report, indent=2, ensure_ascii=False, default=str).encode())
    verdict = _verdict(final)
    print(f"\nprovider_post_count = {LEDGER.post_count}")
    print(f"RESULTADO: {verdict}")
    return 0 if LEDGER.post_count <= 1 and not LEDGER.second_post_attempted else 4


if __name__ == "__main__":
    raise SystemExit(main())
