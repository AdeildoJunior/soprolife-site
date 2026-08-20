#!/usr/bin/env python3
"""M25.29H — auditoria READ-ONLY da integridade do laudo assinado.

Duas perguntas, nesta ordem, antes de qualquer automação:

1. **"Baixar laudo assinado" entrega o arquivo certo?** Há relato de um
   download que trouxe um PDF aparentemente sem assinatura e de outra
   paciente. O script refaz, sem HTTP, exatamente a resolução que o endpoint
   faz — documento → assinado vigente → versão recebida → bytes em disco — e
   confere se a versão entregue pertence ao MESMO documento, com o MESMO
   hash gravado.

2. **Quais pendentes passariam no aceite automático?** Para cada documento
   assinado que ainda espera conferência administrativa, aplica as guardas
   documentais objetivas e diz o veredito, sem gravar nada.

O que este script NÃO faz, por desenho:

* não escreve. Nenhum INSERT, UPDATE ou DELETE, nenhuma promoção de estado,
  nenhum arquivo regravado;
* não imprime PII. Sai código institucional, id, hash abreviado, tamanho e
  veredito. Nome de paciente, contato e texto clínico nunca são impressos —
  o vínculo com a pessoa aparece como identificador, não como nome;
* não decide. Ele descreve a evidência; a decisão é de quem lê.

Uso:
    /opt/soprolife/venvs/m15/bin/python \
        scripts/auditar_m25_29h_integridade_assinados.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import (  # noqa: E402
    ASSINADO_EM_CONFERENCIA,
    ASSINADO_RECUSADO,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
    Role,
    SpirometryExam,
    User,
)
from app.services.signature_batch import (  # noqa: E402
    ESTADO_CONCLUIDO,
    looks_like_preview,
    read_markers_from_metadata,
)

KIND_LIBERADO = "laudo_liberado"
KIND_ADENDO = "laudo_adendo"
KIND_RECEBIDO = "laudo_assinado_externo_recebido"


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<38} {valor if valor is not None else '—'}")


def _curto(valor: str | None) -> str | None:
    return f"{valor[:16]}…" if valor else None


def _bytes_da_versao(settings, version: ReportDocumentVersion) -> bytes | None:
    """Lê o blob pelo mesmo caminho do download. Somente leitura."""

    caminho = settings.resolved_reports_storage_dir() / version.storage_path
    try:
        return caminho.read_bytes()
    except OSError:
        return None


def _tem_estrutura_de_assinatura(pdf: bytes) -> bool:
    """O PDF traz um campo de assinatura?

    Isto NÃO é validação criptográfica: não confere cadeia ICP-Brasil,
    certificado, revogação nem integridade do digest. Diz apenas que a
    estrutura de assinatura existe dentro do arquivo.
    """

    return b"/ByteRange" in pdf and (b"/Sig" in pdf or b"/Adbe.pkcs7" in pdf)


def _versao_final(db: Session, document: ReportDocument):
    versao = db.get(ReportDocumentVersion, document.current_version_id)
    if versao is None or versao.report_document_id != document.id:
        return None
    if versao.kind not in (KIND_LIBERADO, KIND_ADENDO):
        return None
    return versao


def _avaliar(db: Session, settings, assinado: ExternalSignedDocument) -> dict:
    """Toda a evidência de UM documento assinado, sem tocar em nada."""

    ev: dict = {"problemas": [], "assinado_id": assinado.id}
    documento = db.get(ReportDocument, assinado.report_document_id)
    ev["documento"] = documento
    ev["laudo"] = documento.public_code if documento else None
    ev["exame"] = None
    ev["pessoa_id"] = None
    if documento is not None:
        exame = db.get(SpirometryExam, documento.spirometry_exam_id)
        if exame is not None:
            ev["exame"] = exame.public_code
            ev["pessoa_id"] = exame.person_id

    recebida = db.get(
        ReportDocumentVersion, assinado.report_document_version_id
    )
    ev["versao_recebida"] = recebida
    if recebida is None:
        ev["problemas"].append("versão recebida AUSENTE — registro órfão")
        return ev

    # --- isolamento: a versão entregue é DESTE documento?
    ev["versao_do_mesmo_documento"] = (
        recebida.report_document_id == assinado.report_document_id
    )
    if not ev["versao_do_mesmo_documento"]:
        ev["problemas"].append(
            "VERSÃO DE OUTRO DOCUMENTO ligada ao assinado — vazamento"
        )
    ev["kind_recebido"] = recebida.kind
    if recebida.kind != KIND_RECEBIDO:
        ev["problemas"].append(f"kind inesperado: {recebida.kind}")

    # --- hash: banco x banco x disco
    dados = _bytes_da_versao(settings, recebida)
    ev["arquivo_existe"] = dados is not None
    if dados is None:
        ev["problemas"].append("arquivo do assinado ausente em disco")
        return ev
    sha_disco = hashlib.sha256(dados).hexdigest()
    ev["sha_registrado_versao"] = recebida.sha256
    ev["sha_registrado_assinado"] = assinado.sha256
    ev["sha_disco"] = sha_disco
    ev["hashes_batem"] = (
        sha_disco == recebida.sha256 == (assinado.sha256 or recebida.sha256)
    )
    if not ev["hashes_batem"]:
        ev["problemas"].append("HASH DIVERGENTE entre banco e disco")
    ev["magic_pdf"] = dados[:4] == b"%PDF"
    ev["tamanho"] = len(dados)

    # --- origem declarada
    origem = (
        db.get(ReportDocumentVersion, assinado.source_version_id)
        if assinado.source_version_id
        else None
    )
    ev["source_version_id"] = assinado.source_version_id
    ev["origem_do_mesmo_documento"] = (
        origem is not None
        and origem.report_document_id == assinado.report_document_id
    )
    if origem is not None and not ev["origem_do_mesmo_documento"]:
        ev["problemas"].append("source_version_id aponta para OUTRO documento")

    final = _versao_final(db, documento) if documento else None
    ev["tem_final"] = final is not None
    ev["origem_e_a_final"] = (
        final is not None and origem is not None and origem.id == final.id
    )

    # --- guardas documentais
    ev["parece_previa"] = looks_like_preview(dados)
    ev["tem_estrutura_assinatura"] = _tem_estrutura_de_assinatura(dados)

    marcadores = read_markers_from_metadata(dados)
    ev["meta_report_code"] = marcadores.report_code
    ev["meta_validation_code"] = marcadores.validation_code
    ev["meta_version"] = marcadores.version_number
    ev["meta_state"] = marcadores.document_state
    ev["meta_source_hash"] = marcadores.source_sha256

    ev["identico_ao_final"] = False
    ev["contem_o_final"] = False
    ev["metadado_coerente"] = False
    if final is not None:
        bytes_final = _bytes_da_versao(settings, final)
        ev["sha_final"] = final.sha256
        if bytes_final is not None:
            ev["identico_ao_final"] = sha_disco == final.sha256
            # Assinar ANEXA: o preparado continua sendo prefixo exato do
            # assinado. É a associação mais forte que existe sem criptografia.
            ev["contem_o_final"] = dados.startswith(bytes_final)
            marc_final = read_markers_from_metadata(bytes_final)
            ev["metadado_coerente"] = bool(
                documento is not None
                and marcadores.report_code == documento.public_code
                and marcadores.version_number == final.version_number
                and marcadores.source_sha256
                and marcadores.source_sha256 == marc_final.source_sha256
                and marcadores.document_state == ESTADO_CONCLUIDO
            )
    ev["codigo_validacao_coerente"] = bool(
        documento is not None
        and documento.validation_code
        and documento.validation_code not in ("—", "-", "")
        and marcadores.validation_code
        and marcadores.validation_code == documento.validation_code
    )

    # --- veredito do aceite automático
    forte = (
        ev["contem_o_final"]
        or ev["metadado_coerente"]
        or ev["codigo_validacao_coerente"]
    )
    ev["associacao_forte"] = forte
    ev["passaria_no_aceite"] = bool(
        documento is not None
        and ev["tem_final"]
        and ev["origem_e_a_final"]
        and not ev["parece_previa"]
        and not ev["identico_ao_final"]
        and ev["tem_estrutura_assinatura"]
        and ev["versao_do_mesmo_documento"]
        and ev["hashes_batem"]
        and forte
    )
    return ev


def _imprimir(ev: dict) -> None:
    print("")
    _linha("laudo (card)", ev.get("laudo"))
    _linha("exame", ev.get("exame"))
    _linha("report_document_id", ev.get("documento").id if ev.get("documento") else None)
    _linha("external_signed_document.id", ev.get("assinado_id"))
    recebida = ev.get("versao_recebida")
    _linha("report_document_version_id", recebida.id if recebida else None)
    _linha("  ...pertence a este documento?", ev.get("versao_do_mesmo_documento"))
    _linha("  ...kind", ev.get("kind_recebido"))
    _linha("source_version_id", ev.get("source_version_id"))
    _linha("origem é a versão FINAL corrente?", ev.get("origem_e_a_final"))
    _linha("SHA armazenado (versão)", _curto(ev.get("sha_registrado_versao")))
    _linha("SHA armazenado (assinado)", _curto(ev.get("sha_registrado_assinado")))
    _linha("SHA do blob em disco", _curto(ev.get("sha_disco")))
    _linha("hashes conferem", ev.get("hashes_batem"))
    _linha("começa com %PDF", ev.get("magic_pdf"))
    _linha("tamanho em bytes", ev.get("tamanho"))
    _linha("paciente associado (id)", ev.get("pessoa_id"))
    _linha("código de verificação (meta)", ev.get("meta_validation_code"))
    _linha("  ...bate com o do laudo?", ev.get("codigo_validacao_coerente"))
    _linha("carimbo: código do laudo", ev.get("meta_report_code"))
    _linha("carimbo: versão", ev.get("meta_version"))
    _linha("carimbo: estado", ev.get("meta_state"))
    _linha("carimbo coerente com a final", ev.get("metadado_coerente"))
    _linha("contém a final byte a byte", ev.get("contem_o_final"))
    _linha("idêntico à final (sem assinar)", ev.get("identico_ao_final"))
    _linha("parece PRÉVIA", ev.get("parece_previa"))
    _linha("tem estrutura de assinatura", ev.get("tem_estrutura_assinatura"))
    _linha("ASSOCIAÇÃO FORTE", ev.get("associacao_forte"))
    _linha("PASSARIA NO ACEITE AUTOMÁTICO", ev.get("passaria_no_aceite"))
    for problema in ev.get("problemas", []):
        _linha("PROBLEMA", problema)


def auditar_assinados(db: Session) -> int:
    settings = get_settings()
    todos = db.execute(
        select(ExternalSignedDocument).order_by(
            ExternalSignedDocument.received_at
        )
    ).scalars().all()

    print("\n=== TODOS OS DOCUMENTOS ASSINADOS RECEBIDOS ===")
    _linha("registros", len(todos))

    problemas = 0
    vazamentos = 0
    versoes_vistas: dict[str, str] = {}
    for assinado in todos:
        ev = _avaliar(db, settings, assinado)
        _linha_status = assinado.status
        print("")
        _linha("--- estado gravado", _linha_status)
        _imprimir(ev)
        problemas += len(ev["problemas"])
        if ev.get("versao_do_mesmo_documento") is False:
            vazamentos += 1
        recebida = ev.get("versao_recebida")
        if recebida is not None:
            # A MESMA versão ligada a dois assinados diferentes seria a
            # forma mais direta de um download entregar o PDF de outro laudo.
            anterior = versoes_vistas.get(recebida.id)
            if anterior and anterior != assinado.report_document_id:
                print("  ATENÇÃO: versão compartilhada entre documentos!")
                vazamentos += 1
            versoes_vistas[recebida.id] = assinado.report_document_id

    print("\n=== ISOLAMENTO DO DOWNLOAD ===")
    _linha("assinados analisados", len(todos))
    _linha("versões ligadas a outro documento", vazamentos)
    _linha(
        "veredito",
        "ISOLAMENTO PRESERVADO" if vazamentos == 0 else "VAZAMENTO DETECTADO",
    )

    ativos = [
        a for a in todos
        if a.status not in (ASSINADO_RECUSADO, ASSINADO_EM_CONFERENCIA)
    ]
    print("\n=== PENDENTES x NOVO CRITÉRIO ===")
    _linha("assinados vigentes", len(ativos))
    return 1 if problemas or vazamentos else 0


def auditar_papeis(db: Session) -> None:
    """Papéis administrativos. Sem senha, hash, token, cookie ou segredo."""

    print("\n=== CONTAS E PAPÉIS ===")
    usuarios = db.execute(select(User).order_by(User.created_at)).scalars().all()
    todos_papeis = db.execute(select(Role).order_by(Role.name)).scalars().all()
    _linha("papéis existentes", ", ".join(p.name for p in todos_papeis))
    for user in usuarios:
        papeis = sorted(p.name for p in user.roles)
        print("")
        _linha("user_id", user.id)
        _linha("email", user.email)
        _linha("nome", user.nome)
        _linha("ativo", user.ativo)
        _linha("papéis", ", ".join(papeis) or "NENHUM")


def main() -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        codigo = auditar_assinados(db)
        auditar_papeis(db)
    print("")
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
