#!/usr/bin/env python3
"""M25.29D — auditoria READ-ONLY de um caso de laudo, sem imprimir PII.

Existe por causa de um incidente real: uma prévia foi assinada por fora e
foi preciso responder, antes de tocar em qualquer coisa, se aquele arquivo
tinha voltado ao sistema e como o sistema o havia classificado.

O que este script NÃO faz, por desenho:

* não escreve nada — nenhum INSERT, UPDATE ou DELETE;
* não imprime nome, CPF, data de nascimento, contato ou texto clínico. Sai
  identificador institucional, estado, hash e carimbo de tempo. Quem precisa
  do conteúdo clínico abre o laudo na bancada, autenticado;
* não decide nada. Ele descreve.

Uso:
    .venv/bin/python scripts/auditar_caso_laudo.py LAU-000013
    .venv/bin/python scripts/auditar_caso_laudo.py --exame ESP-000028
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import build_engine  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    ExternalSignatureBatch,
    ExternalSignedDocument,
    PhysicianProfile,
    ReportAddendum,
    ReportAssignment,
    ReportDocument,
    ReportDocumentVersion,
    ReportSignature,
    SpirometryExam,
)


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<34} {valor if valor is not None else '—'}")


def _titulo(texto: str) -> None:
    print(f"\n=== {texto} ===")


def auditar(db: Session, *, report_code: str | None, exam_code: str | None):
    documento = None
    exame = None

    if report_code:
        documento = db.execute(
            select(ReportDocument).where(
                ReportDocument.public_code == report_code.upper()
            )
        ).scalar_one_or_none()
        if documento is None:
            print(f"Nenhum laudo com código {report_code}.")
            return 1
        exame = db.get(SpirometryExam, documento.spirometry_exam_id)
    else:
        exame = db.execute(
            select(SpirometryExam).where(
                SpirometryExam.public_code == exam_code.upper()
            )
        ).scalar_one_or_none()
        if exame is None:
            print(f"Nenhum exame com código {exam_code}.")
            return 1
        documento = db.execute(
            select(ReportDocument).where(
                ReportDocument.spirometry_exam_id == exame.id
            )
        ).scalars().first()

    _titulo("EXAME")
    _linha("código", exame.public_code if exame else None)
    _linha("data do exame", exame.data_exame if exame else None)
    _linha("status", exame.status if exame else None)
    _linha("encerramento (motivo)", exame.encerramento_motivo if exame else None)
    # O paciente é referenciado pelo id interno; o NOME não é impresso.
    _linha("person_id (interno)", exame.person_id if exame else None)

    if documento is None:
        print("\nEste exame não possui laudo criado.")
        return 0

    _titulo("LAUDO")
    _linha("código", documento.public_code)
    _linha("status", documento.status)
    _linha("signature_status", documento.signature_status)
    _linha("código de verificação", documento.validation_code)
    _linha("current_version_id", documento.current_version_id)
    _linha("concluído em", documento.released_at)
    _linha("baixado p/ assinatura em", documento.signature_downloaded_at)
    _linha("corrige documento", documento.corrects_document_id)
    _linha("substituído por", documento.superseded_by_id)

    _titulo("MÉDICA ATRIBUÍDA")
    atribuicoes = db.execute(
        select(ReportAssignment).where(
            ReportAssignment.report_document_id == documento.id
        )
    ).scalars().all()
    for atribuicao in atribuicoes:
        perfil = db.get(PhysicianProfile, atribuicao.physician_profile_id)
        _linha(
            f"perfil {atribuicao.physician_profile_id[:8]}…",
            f"ativa={atribuicao.active} crm="
            f"{(perfil.crm_display or perfil.crm_number) if perfil else '—'}"
            f"/{perfil.crm_state if perfil else '—'}",
        )

    _titulo("VERSÕES (ordem de criação)")
    versoes = db.execute(
        select(ReportDocumentVersion)
        .where(ReportDocumentVersion.report_document_id == documento.id)
        .order_by(ReportDocumentVersion.version_number)
    ).scalars().all()
    for versao in versoes:
        marca = " <= CORRENTE" if versao.id == documento.current_version_id else ""
        print(
            f"  v{versao.version_number:<3} {versao.kind:<32}"
            f" {versao.sha256[:12]}… {versao.size_bytes:>8}B"
            f" {versao.created_at}{marca}"
        )
        if versao.validation_code_snapshot:
            _linha("   código no documento", versao.validation_code_snapshot)

    previas = [v for v in versoes if v.kind == "laudo_previa"]
    finais = [v for v in versoes if v.kind in ("laudo_liberado", "laudo_adendo")]
    _titulo("LEITURA")
    _linha("prévias existentes", len(previas))
    _linha("versões finais existentes", len(finais))
    _linha(
        "versão corrente é prévia?",
        any(v.id == documento.current_version_id for v in previas),
    )

    _titulo("ASSINATURA INSTITUCIONAL REGISTRADA")
    assinaturas = db.execute(
        select(ReportSignature).where(
            ReportSignature.report_document_version_id.in_(
                [v.id for v in versoes]
            )
        )
    ).scalars().all()
    for assinatura in assinaturas or []:
        _linha(
            assinatura.provider,
            f"{assinatura.status} em {assinatura.completed_at}",
        )
    if not assinaturas:
        print("  (nenhuma)")

    _titulo("PDF ASSINADO EXTERNAMENTE — CHEGOU AO SISTEMA?")
    assinados = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == documento.id
        )
    ).scalars().all()
    if not assinados:
        print("  NÃO. Nenhum arquivo assinado foi recebido para este laudo.")
    for assinado in assinados:
        origem = db.get(ReportDocumentVersion, assinado.source_version_id)
        lote = db.get(ExternalSignatureBatch, assinado.batch_id)
        print(
            f"  {assinado.id[:8]}… status={assinado.status}"
            f" recebido={assinado.received_at}"
        )
        _linha("   pareado por", assinado.match_method)
        _linha("   sha256 do arquivo", (assinado.sha256 or "")[:16] + "…")
        _linha(
            "   versão de origem",
            f"v{origem.version_number} {origem.kind}" if origem else "—",
        )
        _linha("   origem era PRÉVIA?", origem.kind == "laudo_previa" if origem else "?")
        _linha("   lote", lote.public_code if lote else None)

    _titulo("LOTES DE ASSINATURA EXTERNA (desta médica)")
    perfis = {a.physician_profile_id for a in atribuicoes}
    lotes = db.execute(
        select(ExternalSignatureBatch)
        .where(ExternalSignatureBatch.physician_profile_id.in_(perfis or {""}))
        .order_by(ExternalSignatureBatch.created_at)
    ).scalars().all()
    for lote in lotes:
        print(
            f"  {lote.public_code} {lote.direction:<8}"
            f" docs={lote.document_count} {lote.created_at}"
        )

    _titulo("ADENDOS")
    adendos = db.execute(
        select(ReportAddendum).where(
            ReportAddendum.report_document_id == documento.id
        )
    ).scalars().all()
    print(f"  {len(adendos)} adendo(s)")

    _titulo("AUDITORIA DO LAUDO (ordem cronológica)")
    registros = db.execute(
        select(AuditLog)
        .where(AuditLog.entidade_id == documento.id)
        .order_by(AuditLog.ts_utc)
    ).scalars().all()
    for registro in registros:
        print(f"  {registro.ts_utc}  {registro.acao}")
    if not registros:
        print("  (nenhum registro com este entidade_id)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("laudo", nargs="?", help="código LAU-XXXXXX")
    parser.add_argument("--exame", help="código ESP-XXXXXX")
    args = parser.parse_args()
    if not args.laudo and not args.exame:
        parser.error("informe um LAU ou --exame ESP")

    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return auditar(db, report_code=args.laudo, exam_code=args.exame)


if __name__ == "__main__":
    raise SystemExit(main())
