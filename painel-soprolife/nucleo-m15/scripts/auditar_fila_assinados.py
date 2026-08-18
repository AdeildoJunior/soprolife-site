#!/usr/bin/env python3
"""M25.29E — auditoria READ-ONLY da fila de assinados, sem imprimir PII.

Responde uma pergunta operacional antes de qualquer escrita: os documentos
parados em ``recebido_validacao_pendente`` estão íntegros? O arquivo existe
em disco, o tamanho e o hash batem com o registrado, e o backend consegue
reler o PDF pelo mesmo caminho que o download usa?

O que este script NÃO faz, por desenho:

* não escreve nada — nenhum INSERT, UPDATE ou DELETE. Em particular, não
  marca nada como validado nem como entregue, e não regrava PDF;
* não imprime nome de paciente, contato nem texto clínico. Sai identificador
  institucional, estado, tamanho, hash abreviado e veredito de leitura;
* não decide nada. Se um arquivo estiver ausente ou corrompido, ele diz — e
  para por aí, porque a decisão é humana.

Uso:
    .venv/bin/python scripts/auditar_fila_assinados.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import (  # noqa: E402
    ASSINADO_RECEBIDO_VALIDACAO_PENDENTE,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
)


def _linha(rotulo: str, valor) -> None:
    print(f"  {rotulo:.<34} {valor if valor is not None else '—'}")


def _storage_root(settings) -> Path:
    """A mesma raiz que o download usa — nada de adivinhar caminho."""

    return settings.resolved_reports_storage_dir()


def _conferir_arquivo(settings, version: ReportDocumentVersion) -> dict:
    """Lê o PDF pelo MESMO caminho do download, sem alterar byte nenhum."""

    from app.services.report_storage import read_and_validate_stored_pdf

    raiz = _storage_root(settings)
    caminho = raiz / version.storage_path
    resultado = {
        "existe": caminho.exists(),
        "tamanho_em_disco": caminho.stat().st_size if caminho.exists() else None,
        "backend_le": False,
        "erro": None,
        "magic_pdf": None,
    }
    if not resultado["existe"]:
        resultado["erro"] = "arquivo ausente em disco"
        return resultado

    try:
        with caminho.open("rb") as fh:
            resultado["magic_pdf"] = fh.read(4) == b"%PDF"
    except OSError as erro:  # pragma: no cover - depende do disco real
        resultado["erro"] = f"não foi possível abrir: {erro.__class__.__name__}"
        return resultado

    try:
        read_and_validate_stored_pdf(
            caminho,
            root=raiz,
            expected_sha256=version.sha256,
            expected_size_bytes=version.size_bytes,
            expected_page_count=version.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=True,
        )
        resultado["backend_le"] = True
    except Exception as erro:  # noqa: BLE001 - o veredito é o que importa
        resultado["erro"] = f"{erro.__class__.__name__}: {erro}"
    return resultado


def auditar(db: Session) -> int:
    settings = get_settings()
    pendentes = db.execute(
        select(ExternalSignedDocument)
        .where(
            ExternalSignedDocument.status
            == ASSINADO_RECEBIDO_VALIDACAO_PENDENTE
        )
        .order_by(ExternalSignedDocument.received_at)
    ).scalars().all()

    print(f"\n=== FILA: {ASSINADO_RECEBIDO_VALIDACAO_PENDENTE} ===")
    print(f"  documentos nesse estado.......... {len(pendentes)}")

    problemas = 0
    for assinado in pendentes:
        documento = db.get(ReportDocument, assinado.report_document_id)
        exame = (
            db.get(SpirometryExam, documento.spirometry_exam_id)
            if documento is not None
            else None
        )
        versao = db.get(
            ReportDocumentVersion, assinado.report_document_version_id
        )

        print("")
        _linha("laudo", documento.public_code if documento else "?")
        _linha("exame", exame.public_code if exame else "?")
        _linha("status do laudo", documento.status if documento else "?")
        _linha("recebido em", assinado.received_at)
        _linha("pareado por", assinado.match_method)
        _linha(
            "assinatura qualificada?",
            getattr(assinado, "qualified_signature", False),
        )

        if versao is None:
            _linha("versão ligada", "AUSENTE — registro órfão")
            problemas += 1
            continue

        _linha("versão ligada", f"{versao.kind}")
        _linha("sha256 registrado", (versao.sha256 or "")[:16] + "…")
        _linha("tamanho registrado", f"{versao.size_bytes}B")

        conferencia = _conferir_arquivo(settings, versao)
        _linha("arquivo existe", conferencia["existe"])
        _linha("tamanho em disco", conferencia["tamanho_em_disco"])
        _linha("começa com %PDF", conferencia["magic_pdf"])
        _linha("backend relê o PDF", conferencia["backend_le"])
        if conferencia["erro"]:
            _linha("PROBLEMA", conferencia["erro"])
        if not conferencia["backend_le"]:
            problemas += 1

    print("\n=== VEREDITO ===")
    if not pendentes:
        _linha("resultado", "fila vazia — nada a conferir")
    elif problemas:
        _linha("resultado", f"{problemas} documento(s) COM PROBLEMA")
        print("\n  PARE. Não escreva nada. Leve o diagnóstico acima a uma")
        print("  decisão humana antes de qualquer correção.")
    else:
        _linha("resultado", "todos íntegros e legíveis pelo backend")
    print("")
    return 1 if problemas else 0


def main() -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as db:
        return auditar(db)


if __name__ == "__main__":
    raise SystemExit(main())
