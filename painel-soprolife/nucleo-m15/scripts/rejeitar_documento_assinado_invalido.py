#!/usr/bin/env python3
"""M25.29G — recusa um documento assinado inválido, preservando tudo.

Existe por causa de dois achados reais na fila de conferência:

* um PDF devolvido "assinado" que era, na verdade, a **prévia** assinada por
  fora antes da conclusão do laudo;
* PDFs devolvidos **byte a byte idênticos** ao laudo final — ou seja, o mesmo
  arquivo que a médica baixou, sem assinatura nenhuma acrescentada.

O que este script faz, e só isso:

    external_signed_documents.status  ->  "recusado"
    + uma linha de auditoria com o motivo

O que ele NÃO faz, por desenho:

* não apaga nada — nem blob, nem versão, nem hash, nem trilha;
* não toca no exame, no laudo, nas versões, no código de verificação, no
  `current_version_id`, na conclusão clínica nem no MIR;
* não marca conferência nem entrega;
* não decide sozinho: sem evidência objetiva, ele recusa a AGIR.

Por padrão roda em **dry-run**. Escrever exige `--apply` explícito.

É idempotente: rodar duas vezes não duplica nada e a segunda execução
informa que não há o que fazer.

Uso:
    python scripts/rejeitar_documento_assinado_invalido.py \\
        --lau LAU-000014 --motivo previa_assinada_antes_da_conclusao
    python scripts/rejeitar_documento_assinado_invalido.py \\
        --lau LAU-000014 --motivo previa_assinada_antes_da_conclusao --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.audit import audit  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import (  # noqa: E402
    ASSINADO_ENTREGUE,
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ASSINADO_RECUSADO,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
)

# Os motivos são fechados de propósito: o estado é genérico, mas a razão
# precisa ser comparável entre casos, e texto livre não é comparável.
MOTIVOS = {
    "previa_assinada_antes_da_conclusao": (
        "O arquivo devolvido é a PRÉVIA assinada por fora, antes de o laudo "
        "ser concluído."
    ),
    "documento_sem_assinatura_externa": (
        "O arquivo devolvido é idêntico ao PDF final: nenhuma assinatura foi "
        "acrescentada."
    ),
    "documento_nao_corresponde_a_versao_final": (
        "O arquivo devolvido não corresponde à versão final do laudo."
    ),
}


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<40} {valor if valor is not None else '—'}")


def _versao_final(db: Session, document: ReportDocument):
    return db.get(ReportDocumentVersion, document.current_version_id)


def _tem_estrutura_de_assinatura(dados: bytes) -> bool:
    """Procura estrutura de assinatura no PDF.

    **Isto NÃO é validação ICP-Brasil.** Não confere cadeia, não confere
    certificado, não confere integridade criptográfica. Serve para uma
    pergunta muito mais simples: este arquivo chegou a receber um campo de
    assinatura, ou é o mesmo PDF que saiu daqui?
    """

    return any(
        marca in dados
        for marca in (b"/ByteRange", b"/SubFilter", b"/Sig", b"/DigitalSignature")
    )


def _evidencia(db: Session, settings, document, assinado, versao_recebida):
    """Reúne os fatos objetivos. Não julga — descreve."""

    from app.services.report_storage import read_and_validate_stored_pdf
    from app.services.signature_batch import looks_like_preview

    final = _versao_final(db, document)
    raiz = settings.resolved_reports_storage_dir()

    dados = None
    caminho = raiz / versao_recebida.storage_path
    if caminho.exists():
        dados = caminho.read_bytes()

    fatos = {
        "sha_final": (final.sha256 or "") if final else "",
        "sha_recebido": versao_recebida.sha256 or "",
        "tamanho_final": final.size_bytes if final else None,
        "tamanho_recebido": versao_recebida.size_bytes,
        "match_method": assinado.match_method,
        "arquivo_existe": dados is not None,
        "parece_previa": looks_like_preview(dados) if dados else None,
        "tem_estrutura_assinatura": (
            _tem_estrutura_de_assinatura(dados) if dados else None
        ),
    }
    fatos["identico_ao_final"] = bool(
        fatos["sha_final"] and fatos["sha_final"] == fatos["sha_recebido"]
    )
    fatos["menor_que_o_final"] = bool(
        fatos["tamanho_final"]
        and fatos["tamanho_recebido"] < fatos["tamanho_final"]
    )
    return fatos


def _evidencia_sustenta(motivo: str, fatos: dict) -> tuple[bool, str]:
    """A evidência sustenta ESTE motivo? Sem isso, não se escreve."""

    if motivo == "documento_sem_assinatura_externa":
        if fatos["identico_ao_final"]:
            return True, "hash idêntico ao PDF final — nada foi acrescentado"
        return False, (
            "o hash difere do final: houve alguma modificação, então não se "
            "pode afirmar que não foi assinado"
        )

    if motivo == "previa_assinada_antes_da_conclusao":
        if fatos["parece_previa"]:
            return True, "o conteúdo do PDF traz as marcas de prévia"
        if fatos["parece_previa"] is None:
            return False, "arquivo ausente em disco — não dá para afirmar nada"
        if fatos["menor_que_o_final"]:
            return True, (
                "menor que o PDF final, e assinatura só acrescenta bytes"
            )
        return False, (
            "o conteúdo não traz marcas de prévia e o arquivo não é menor "
            "que o final"
        )

    if motivo == "documento_nao_corresponde_a_versao_final":
        if fatos["identico_ao_final"]:
            return False, "o arquivo é idêntico ao final — o motivo não cabe"
        return True, "hash diverge da versão final corrente"

    return False, "motivo desconhecido"


def rejeitar(db: Session, *, lau: str, motivo: str, aplicar: bool) -> int:
    settings = get_settings()

    document = db.execute(
        select(ReportDocument).where(ReportDocument.public_code == lau)
    ).scalar_one_or_none()
    if document is None:
        print(f"\nERRO: laudo {lau} não encontrado.\n")
        return 2

    assinado = db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.report_document_id == document.id)
        .order_by(ExternalSignedDocument.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    print(f"\n=== {lau} ===")
    _linha("status do laudo", document.status)
    _linha("versão corrente", document.current_version_id)
    _linha("código de verificação", document.validation_code)

    if assinado is None:
        print("\n  Nenhum documento assinado registrado. Nada a fazer.\n")
        return 0

    _linha("documento assinado", assinado.id)
    _linha("status atual", assinado.status)

    # ---- idempotência ---------------------------------------------------
    if assinado.status == ASSINADO_RECUSADO:
        print("\n  JÁ RECUSADO. Nada a fazer — execução idempotente.\n")
        return 0

    # ---- travas de segurança -------------------------------------------
    if assinado.status == ASSINADO_ENTREGUE:
        print(
            "\n  PARE: este documento consta como ENTREGUE. Recusá-lo aqui "
            "não desfaz a entrega que já aconteceu fora do sistema.\n"
            "  Leve o caso a uma decisão humana.\n"
        )
        return 3
    if assinado.status == ASSINADO_VALIDADO_EXTERNAMENTE:
        print(
            "\n  PARE: a conferência externa já foi registrada por alguém.\n"
            "  Recusar apagaria o valor desse testemunho sem explicá-lo.\n"
            "  Leve o caso a uma decisão humana.\n"
        )
        return 3
    if assinado.status != ASSINADO_RECEBIDO_VALIDACAO_PENDENTE:
        print(f"\n  PARE: estado inesperado ({assinado.status}).\n")
        return 3

    versao = db.get(ReportDocumentVersion, assinado.report_document_version_id)
    if versao is None:
        print("\n  PARE: versão ligada ao documento assinado não existe.\n")
        return 3

    # ---- evidência ------------------------------------------------------
    fatos = _evidencia(db, settings, document, assinado, versao)
    print("\n  --- evidência objetiva ---")
    _linha("pareado por", fatos["match_method"])
    _linha("sha do PDF final", (fatos["sha_final"] or "")[:16] + "…")
    _linha("sha do recebido", (fatos["sha_recebido"] or "")[:16] + "…")
    _linha("idêntico ao final?", fatos["identico_ao_final"])
    _linha("tamanho final", fatos["tamanho_final"])
    _linha("tamanho recebido", fatos["tamanho_recebido"])
    _linha("menor que o final?", fatos["menor_que_o_final"])
    _linha("arquivo existe em disco", fatos["arquivo_existe"])
    _linha("conteúdo parece prévia?", fatos["parece_previa"])
    _linha("tem estrutura de assinatura?", fatos["tem_estrutura_assinatura"])
    print("      (estrutura de assinatura NÃO é validação ICP-Brasil)")

    sustenta, porque = _evidencia_sustenta(motivo, fatos)
    print(f"\n  motivo pedido..: {motivo}")
    print(f"  a evidência sustenta? {sustenta} — {porque}")

    if not sustenta:
        print("\n  NADA FOI ESCRITO. A evidência não sustenta o motivo.\n")
        return 4

    # ---- o que muda -----------------------------------------------------
    print("\n  --- o que MUDA ---")
    print(f"  external_signed_documents[{assinado.id}].status")
    print(f"      '{assinado.status}'  ->  '{ASSINADO_RECUSADO}'")
    print("  + 1 linha em audit_logs com o motivo")
    print("\n  --- o que NÃO muda ---")
    print("  exame, laudo, versões (todas), blob, hashes, código de")
    print("  verificação, current_version_id, conclusão clínica, MIR, lotes")

    if not aplicar:
        print("\n  DRY-RUN — nada foi escrito. Use --apply para aplicar.\n")
        return 0

    assinado.status = ASSINADO_RECUSADO
    audit(
        db,
        "assinado_externo_recusado",
        entidade="report_documents",
        entidade_id=document.id,
        user_id=None,
        request_id=None,
        detalhes={
            "signed_document_id": assinado.id,
            "motivo": motivo,
            "match_method": fatos["match_method"],
            "identico_ao_final": fatos["identico_ao_final"],
            "parece_previa": fatos["parece_previa"],
        },
    )
    db.commit()
    print("\n  APLICADO. O laudo volta a aguardar a assinatura da versão")
    print("  final; nada foi apagado.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lau", required=True, help="código LAU-XXXXXX")
    parser.add_argument("--motivo", required=True, choices=sorted(MOTIVOS))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="escreve de verdade; sem isto roda em dry-run",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return rejeitar(
            db, lau=args.lau, motivo=args.motivo, aplicar=args.apply
        )


if __name__ == "__main__":
    raise SystemExit(main())
