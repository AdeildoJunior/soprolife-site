#!/usr/bin/env python3
"""M25.29H — reconcilia a fila assinada com as guardas documentais.

Quando o aceite automático entrou, a fila já tinha documentos parados em
estados do fluxo antigo. Este script reaplica a eles EXATAMENTE as mesmas
guardas que um arquivo enviado hoje enfrenta, e ajusta o estado ao veredito.

Ele existe por causa de um achado concreto. A auditoria da M25.29H encontrou
um documento marcado `validado_externamente` — conferido por uma pessoa —
que era byte a byte igual ao PDF final e não continha estrutura de assinatura
nenhuma. Um falso positivo da conferência humana. O script de recusa da
M25.29G se recusa a tocar em documentos já conferidos, e com razão: aquele
script não sabia reavaliar evidência. Este sabe, e por isso pode.

O que ele faz:

* PROMOVE para `recebido_assinado` o documento que passa em todas as guardas
  e ainda está num estado do fluxo antigo;
* RECUSA o documento que não passa, gravando o motivo objetivo na auditoria;
* NÃO TOCA em documento já entregue, já recusado, ou já
  `validado_externamente` que passe nas guardas — nesses casos não há nada a
  corrigir, e mexer só produziria ruído na trilha.

O que ele NUNCA faz:

* nenhum DELETE, em nenhuma tabela;
* não apaga nem reescreve auditoria anterior — a conferência humana
  invalidada continua registrada, e o evento novo diz que ela foi invalidada
  e por qual evidência;
* não toca em conclusão clínica, texto, paciente, exame, versão ou blob;
* não afirma validação criptográfica: `qualified_signature` continua falso.

Uso:
    # dry-run (padrão) — não escreve nada
    python scripts/reconciliar_fila_assinada.py
    python scripts/reconciliar_fila_assinada.py --lau LAU-000015

    # escrita, explícita
    python scripts/reconciliar_fila_assinada.py --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit import audit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import (  # noqa: E402
    ASSINADO_ACEITO,
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_ENTREGUE,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_RECUSADO,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
)
from app.services.signature_acceptance import avaliar  # noqa: E402

# Estados que este script pode reavaliar. `entregue` fica de fora de
# propósito: o documento já saiu para o paciente, e reclassificar o que já
# foi entregue não desfaz a entrega — só apaga o rastro do que aconteceu.
REAVALIAVEIS = (
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_VALIDADO_EXTERNAMENTE,
)

PROMOVIVEIS = (ASSINADO_EM_CONFERENCIA, ASSINADO_RECEBIDO_VALIDACAO_PENDENTE)


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<40} {valor if valor is not None else '—'}")


def _bytes_de(settings, versao: ReportDocumentVersion | None) -> bytes | None:
    if versao is None:
        return None
    caminho = settings.resolved_reports_storage_dir() / versao.storage_path
    try:
        return caminho.read_bytes()
    except OSError:
        return None


def _avaliar(db: Session, settings, assinado: ExternalSignedDocument):
    documento = db.get(ReportDocument, assinado.report_document_id)
    recebida = db.get(
        ReportDocumentVersion, assinado.report_document_version_id
    )
    origem = db.get(ReportDocumentVersion, assinado.source_version_id)
    recebidos = _bytes_de(settings, recebida)
    finais = _bytes_de(settings, origem)
    if documento is None or recebidos is None:
        return documento, None
    return documento, avaliar(
        recebidos,
        final=finais,
        document_code=documento.public_code,
        validation_code=documento.validation_code,
        final_version_number=origem.version_number if origem else None,
        origem_e_a_versao_final=bool(
            origem is not None and documento.current_version_id == origem.id
        ),
    )


def reconciliar(db: Session, *, apply: bool, laudos: list[str]) -> int:
    settings = get_settings()
    consulta = select(ExternalSignedDocument).order_by(
        ExternalSignedDocument.received_at
    )
    assinados = db.execute(consulta).scalars().all()

    alvo = {codigo.strip().upper() for codigo in laudos}
    promovidos = recusados = intocados = 0

    print("\n=== RECONCILIAÇÃO DA FILA ASSINADA ===")
    _linha("modo", "APLICANDO ESCRITA" if apply else "DRY-RUN (não escreve)")
    _linha("filtro", ", ".join(sorted(alvo)) if alvo else "todos")

    for assinado in assinados:
        documento, guardas = _avaliar(db, settings, assinado)
        codigo = documento.public_code if documento else "?"
        if alvo and codigo.upper() not in alvo:
            continue

        print("")
        _linha("laudo", codigo)
        _linha("signed_document_id", assinado.id)
        _linha("estado atual", assinado.status)

        if assinado.status == ASSINADO_ENTREGUE:
            _linha("decisão", "INTOCADO — já entregue ao paciente")
            intocados += 1
            continue
        if assinado.status == ASSINADO_RECUSADO:
            _linha("decisão", "INTOCADO — já recusado (idempotente)")
            intocados += 1
            continue
        if assinado.status not in REAVALIAVEIS:
            _linha("decisão", "INTOCADO — estado fora do escopo")
            intocados += 1
            continue
        if guardas is None:
            _linha("decisão", "INTOCADO — evidência indisponível em disco")
            intocados += 1
            continue

        _linha("passa nas guardas", guardas.aceito)
        if not guardas.aceito:
            _linha("motivo", guardas.motivo)
        _linha("evidência", guardas.para_auditoria())

        if guardas.aceito:
            if assinado.status == ASSINADO_VALIDADO_EXTERNAMENTE:
                # Já está pronto para entrega e a conferência humana estava
                # certa. Trocar o estado só embaralharia a trilha.
                _linha("decisão", "INTOCADO — já pronto para entrega")
                intocados += 1
                continue
            _linha("decisão", f"PROMOVER {assinado.status} → {ASSINADO_ACEITO}")
            promovidos += 1
            if apply:
                anterior = assinado.status
                assinado.status = ASSINADO_ACEITO
                if assinado.confirmed_at is None:
                    assinado.confirmed_at = datetime.now(timezone.utc)
                audit(
                    db,
                    "laudo_assinado_aceito_automaticamente",
                    entidade="report_documents",
                    entidade_id=assinado.report_document_id,
                    user_id=None,
                    detalhes={
                        "signed_document_id": assinado.id,
                        "sha256": assinado.sha256,
                        "match_method": assinado.match_method,
                        "status_anterior": anterior,
                        "status": ASSINADO_ACEITO,
                        "aceito": True,
                        **guardas.para_auditoria(),
                    },
                )
            continue

        _linha("decisão", f"RECUSAR {assinado.status} → {ASSINADO_RECUSADO}")
        recusados += 1
        if apply:
            anterior = assinado.status
            assinado.status = ASSINADO_RECUSADO
            if anterior == ASSINADO_VALIDADO_EXTERNAMENTE:
                # O falso positivo tem evento PRÓPRIO. A conferência anterior
                # continua gravada, com quem a fez e quando; o que este
                # evento acrescenta é que ela foi invalidada, e por qual
                # evidência documental — não por opinião de ninguém.
                audit(
                    db,
                    "conferencia_externa_invalidada_por_evidencia",
                    entidade="report_documents",
                    entidade_id=assinado.report_document_id,
                    user_id=None,
                    detalhes={
                        "signed_document_id": assinado.id,
                        "sha256": assinado.sha256,
                        "status_anterior": anterior,
                        "motivo": guardas.motivo,
                        **guardas.para_auditoria(),
                    },
                )
            audit(
                db,
                "assinado_externo_recusado",
                entidade="report_documents",
                entidade_id=assinado.report_document_id,
                user_id=None,
                detalhes={
                    "signed_document_id": assinado.id,
                    "sha256": assinado.sha256,
                    "match_method": assinado.match_method,
                    "status_anterior": anterior,
                    "status": ASSINADO_RECUSADO,
                    "motivo": guardas.motivo,
                    "aceito": False,
                    **guardas.para_auditoria(),
                },
            )

    if apply:
        db.commit()

    print("\n=== RESUMO ===")
    _linha("promovidos a pronto para entrega", promovidos)
    _linha("recusados", recusados)
    _linha("intocados", intocados)
    if not apply:
        print("\n  DRY-RUN: nada foi gravado. Repita com --apply para aplicar.")
    print("")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reaplica as guardas documentais à fila assinada."
    )
    parser.add_argument(
        "--lau",
        action="append",
        default=[],
        help="Código do laudo a reconciliar. Repetível. Padrão: todos.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava as mudanças. Sem esta opção, nada é escrito.",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return reconciliar(db, apply=args.apply, laudos=args.lau)


if __name__ == "__main__":
    raise SystemExit(main())
