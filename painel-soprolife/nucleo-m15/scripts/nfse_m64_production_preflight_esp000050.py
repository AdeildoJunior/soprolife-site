#!/usr/bin/env python3
"""M64 — prepare ESP-000050 in PRODUCTION and run the signed preflight. Sends nothing.

WHAT THIS DOES

For exactly one exam, ESP-000050, and nothing else:

  1. re-checks, against the operational database, the facts this depends on:
     the exam's service municipality is 3304557, and all five manual NFS-e
     (ESP-000039/046/048/049/053) are protected;
  2. prepares the production fiscal document (state ``pending``);
  3. runs the production preflight: builds the tpAmb=1 DPS, signs it LOCALLY
     with the real A1, validates XSD and XMLDSig, checks the timezone, the
     namespace prefix count, the clock and the certificate margin, and stages
     the signed bytes in a private local directory.

WHAT IT CANNOT DO

It never calls ``nfse.operate()``, so no attempt row, no provider, no
transport. The production network gate stays False in the Settings it builds,
``explicit_human_production_authorization`` is never passed, and
``httpx.Client`` is replaced with something that raises before this script
touches the database — if anything below tried to open a connection to SEFIN,
the script would die instead.

THE ONE IRREVERSIBLE THING IT DOES

The preflight allocates this document's DPS number (series 00001) in the
durable, append-only numbering table. On a database that has never issued
through this system, that is number 1. It cannot be taken back. Before
running this, confirm on the DANFSe of one of the manual notes which DPS
series and number the Emissor Nacional used: if it also used series 00001,
number 1 is already taken at SEFIN and the first real POST would be refused
as a duplicate DPS. The ``--dps-series-checked`` flag is required and is the
operator saying they looked.

THE PASSWORD

Read only from the terminal. Never from an argument, the environment, a file
or a pipe — a redirected stdin is refused. Overwritten in memory after use.

Usage (in a REAL terminal, with M15_DATABASE_URL pointing at the operational
database through an SSH tunnel):

    python scripts/nfse_m64_production_preflight_esp000050.py --dps-series-checked
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TARGET = "ESP-000050"
EXPECTED_MUNICIPALITY = "3304557"
PROTECTED_MANUAL_NOTES = ("ESP-000039", "ESP-000046", "ESP-000048", "ESP-000049", "ESP-000053")
CERTIFICATE = Path("~/.local/share/soprolife/secrets/nfse/soprolife-nfse.pfx").expanduser()
ARTIFACTS = Path("~/.local/share/soprolife/nfse-production/artifacts").expanduser()
ACTOR_EMAIL_HINT = None   # the first user, as for every M60–M64 write


def _no_network():
    """Make any HTTP client construction fatal, before anything else runs."""
    import httpx

    def refuse(*args, **kwargs):
        raise RuntimeError("M64: tentativa de construir httpx.Client — rede proibida nesta etapa")
    httpx.Client = refuse
    httpx.AsyncClient = refuse


def _password() -> str:
    if not sys.stdin.isatty():
        print("ERRO: a senha só é aceite a partir de um terminal real.", file=sys.stderr)
        raise SystemExit(2)
    value = getpass.getpass("Senha do certificado A1 (não é exibida nem guardada): ")
    if not value:
        print("ERRO: senha vazia.", file=sys.stderr)
        raise SystemExit(2)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dps-series-checked", action="store_true", required=True,
                        help="confirma que a série/número de DPS do Emissor Nacional foi "
                             "conferido num DANFSe manual antes de alocar a DPS nº 1")
    args = parser.parse_args()

    if not os.environ.get("M15_DATABASE_URL"):
        print("ERRO: M15_DATABASE_URL não definido.", file=sys.stderr)
        return 2
    if not CERTIFICATE.is_file():
        print(f"ERRO: certificado não encontrado em {CERTIFICATE}", file=sys.stderr)
        return 2

    _no_network()

    from sqlalchemy import select

    from app.config import Settings
    from app.db import get_sessionmaker
    from app.models import FiscalAttempt, FiscalDocument, SpirometryExam, User
    from app.services import nfse
    from app.services.nfse_external_issuance import IMPORT_OPERATION
    from app.services.nfse_national.production_preflight import run_production_preflight

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    os.chmod(ARTIFACTS, 0o700)
    os.chmod(ARTIFACTS.parent, 0o700)

    password = _password()
    try:
        settings = Settings(
            nfse_enabled=True,
            nfse_environment="production",
            nfse_real_enabled=True,
            nfse_production_network_enabled=False,      # the gate stays shut
            nfse_restricted_certificate_path=CERTIFICATE,
            nfse_restricted_certificate_password=password,
            nfse_fiscal_artifacts_dir=ARTIFACTS,
        )
    finally:
        password = "\0" * len(password)
        del password
    assert settings.nfse_production_network_enabled is False

    report: dict = {"target": TARGET}
    with get_sessionmaker()() as db:
        exam = db.scalars(select(SpirometryExam).where(SpirometryExam.public_code == TARGET)).first()
        if exam is None:
            print("ERRO: exame não encontrado.", file=sys.stderr)
            return 1
        if exam.municipio_atendimento_ibge != EXPECTED_MUNICIPALITY:
            print(f"ERRO: município do {TARGET} é {exam.municipio_atendimento_ibge!r}, "
                  f"esperado {EXPECTED_MUNICIPALITY}. Parado antes de preparar.", file=sys.stderr)
            return 1

        for code in PROTECTED_MANUAL_NOTES:
            protected = db.scalars(
                select(FiscalAttempt).join(FiscalDocument,
                                           FiscalDocument.id == FiscalAttempt.document_id)
                .join(SpirometryExam, SpirometryExam.id == FiscalDocument.spirometry_exam_id)
                .where(SpirometryExam.public_code == code,
                       FiscalAttempt.operation == IMPORT_OPERATION)).first()
            if protected is None:
                print(f"ERRO: a nota manual de {code} não está protegida. Parado.", file=sys.stderr)
                return 1
        report["manual_notes_protected"] = list(PROTECTED_MANUAL_NOTES)

        actor = db.scalars(select(User).order_by(User.created_at).limit(1)).first()
        evaluation = nfse.evaluate(db, exam.id, "production")
        report["eligibility_blockers"] = evaluation["blocking_reasons"]
        if evaluation["blocking_reasons"]:
            db.rollback()
            print(json.dumps(report, indent=2, ensure_ascii=False))
            print("\nERRO: o exame tem blockers. Parado antes de preparar.", file=sys.stderr)
            return 1
        db.rollback()   # release the evaluate() locks before preparing

        document = nfse.prepare(db, exam.id, settings, actor.id)
        report["document_id"] = document.id
        report["document_state_after_prepare"] = document.state

        result = run_production_preflight(db, document.id, settings, actor.id)
        data = result.as_dict()
        report["preflight"] = {k: data[k] for k in (
            "environment_is_production", "production_endpoint_ok", "network_gate_enabled",
            "xsd_and_signature_ok", "dh_emi", "dh_emi_timezone_ok", "namespace_prefix_count",
            "fiscal_config_resolved", "request_fingerprint", "human_authorization_present",
            "technical_ready", "authorization_ready", "network_send_allowed", "blockers")}
        report["preflight"]["certificate"] = {k: data["certificate"].get(k) for k in (
            "valid", "reason", "days_remaining", "required_days")}
        report["preflight"]["clock"] = data["clock"]

        from app.models import DpsNumberAllocation
        allocation = db.get(DpsNumberAllocation, document.id)
        report["dps_number_allocated"] = allocation.dps_number if allocation else None

        document = nfse.get_document(db, document.id)
        attempts = db.scalars(select(FiscalAttempt).where(
            FiscalAttempt.document_id == document.id)).all()
        report["document_state_final"] = document.state
        report["fiscal_attempts"] = len(attempts)

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    # The state M64 must end in, stated as checks rather than hopes.
    failures = []
    if report["document_state_final"] != "pending":
        failures.append("document_not_pending")
    if report["fiscal_attempts"] != 0:
        failures.append("attempt_was_created")
    if report["preflight"]["network_send_allowed"] is not False:
        failures.append("network_send_allowed_is_not_false")
    if report["preflight"]["authorization_ready"] is not False:
        failures.append("authorization_ready_is_not_false")
    technical = [b for b in report["preflight"]["blockers"]
                 if b != "production_network_gate_disabled"]
    if technical:
        failures.append("technical_blockers:" + ",".join(technical))
    print("\nVERIFICAÇÃO FINAL:", "OK — pronto no limiar, nada enviado" if not failures
          else "FALHOU: " + "; ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
