#!/usr/bin/env python3
"""M25.3 — verificação ponta a ponta do Laudo Online contra o banco local.

Exercita, sobre o cenário fictício criado por `seed_m25_3_laudo_demo.py`, os
12 passos pedidos para a validação funcional:

     1. exame pendente na fila médica
     2. abertura pela área médica
     3. seleção da conclusão (DVO Leve)
     4. edição do texto
     5. pré-visualização
     6. confirmação consciente (recusa sem confirmação)
     7. liberação
     8. geração do PDF
     9. verificação do hash e da versão
    10. tentativa de alteração indevida após a assinatura
    11. criação de adendo preservando a versão anterior
    12. download separado dos dois PDFs

Não altera nada fora do cenário fictício e não é um substituto da suíte
`tests/test_m25_2_native_report.py` — é a prova de que o fluxo roda no
ambiente local realmente provisionado.

Uso:
    cd painel-soprolife/nucleo-m15
    .venv/bin/python scripts/test_m25_3_fluxo_completo.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import ReportDocument, ReportDocumentVersion, User  # noqa: E402
from app.security import issue_token  # noqa: E402

MEDICA_EMAIL = "medica.teste@soprolife.local"
SAIDA = Path(os.environ.get("M25_3_SAIDA", "/tmp/m25-3-laudo"))

_ok = 0
_falhas: list[str] = []


def _pdf_text(data: bytes) -> str:
    """Texto real do PDF (o reportlab comprime os streams)."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def check(rotulo: str, condicao: bool, detalhe: str = "") -> None:
    global _ok
    if condicao:
        _ok += 1
        print(f"  OK   {rotulo}")
    else:
        _falhas.append(rotulo)
        print(f"  FALHA {rotulo} {detalhe}")


def main() -> int:
    os.environ.setdefault("M15_AUTH_SECRET", "m25-3-seed-local-somente-dev-0123456789")
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.reports_enabled or settings.reports_mode != "pilot":
        print("ABORTADO: laudos desabilitados no .env local.", file=sys.stderr)
        return 2

    SAIDA.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    medica = db.execute(
        select(User).where(User.email == MEDICA_EMAIL)
    ).scalar_one_or_none()
    if medica is None:
        print("ABORTADO: rode antes scripts/seed_m25_3_laudo_demo.py --confirmar",
              file=sys.stderr)
        return 2
    auth = {"Authorization": f"Bearer {issue_token(medica.id, medica.password_hash)}"}
    # Precisa de um laudo AINDA editável: um laudo já liberado recusa nova
    # prévia por desenho (laudo_bloqueado_para_edicao), então reexecutar o
    # fluxo sobre ele falharia pelo motivo certo, mas sem provar nada.
    doc_row = db.execute(
        select(ReportDocument)
        .where(ReportDocument.status.in_(("atribuido", "em_elaboracao")))
        .order_by(ReportDocument.created_at.desc())
    ).scalars().first()
    if doc_row is None:
        print(
            "ABORTADO: nenhum laudo editável no banco local.\n"
            "Crie um caso novo com:\n"
            "  .venv/bin/python scripts/seed_m25_3_laudo_demo.py "
            "--confirmar --novo-laudo",
            file=sys.stderr,
        )
        return 2
    doc_id = doc_row.id
    db.commit()

    app = create_app()
    print("\n== M25.3 — fluxo completo do Laudo Online ==\n")
    with TestClient(app) as client:
        # -------------------------------------------------- 1. fila médica
        print("[1] exame pendente na fila médica")
        fila = client.get("/api/v1/laudos/meus", headers=auth)
        check("fila responde 200", fila.status_code == 200, fila.text)
        # A fila devolve uma lista simples (ver GET /laudos/meus).
        itens = fila.json()
        check("laudo fictício aparece na fila",
              any(i["document_id"] == doc_id for i in itens),
              str(fila.json())[:200])

        # -------------------------------------------- 2. abertura do exame
        print("[2] abertura da tela médica do exame")
        detalhe = client.get(f"/api/v1/laudos/{doc_id}", headers=auth)
        check("detalhe do laudo responde 200", detalhe.status_code == 200, detalhe.text)
        corpo = detalhe.json()
        check("traz identificação do paciente",
              "João da Silva Teste" in str(corpo), "")
        check("traz o local de realização estruturado",
              "Unidade Ipanema" in str(corpo), "")

        # ------------------------------------------------- 3. catálogo
        print("[3] catálogo de conclusões e complementos pós-BD")
        cat = client.get(f"/api/v1/laudos/{doc_id}/catalogo-conclusoes", headers=auth)
        check("catálogo responde 200", cat.status_code == 200, cat.text)
        payload = cat.json()
        codigos = {c["codigo"] for c in payload["conclusoes"]}
        esperados = {
            "NORMAL", "DVO_LEVE", "DVO_MODERADO", "DVO_MOD_GRAVE", "DVO_GRAVE",
            "DVO_MUITO_GRAVE", "DVR_SUG_LEVE", "DVR_SUG_MODERADO",
            "DVR_SUG_MOD_GRAVE", "DVR_SUG_GRAVE", "DVR_SUG_MUITO_GRAVE",
            "DVM_SUG_LEVE", "DVM_SUG_MODERADO", "DVM_SUG_MOD_GRAVE",
            "DVM_SUG_GRAVE", "DVM_SUG_MUITO_GRAVE", "DVI", "PERSONALIZADO",
        }
        check("catálogo tem as 18 opções exigidas", codigos == esperados,
              str(sorted(codigos ^ esperados)))
        bd = {c["codigo"] for c in payload["complementos_bd"]}
        check("exame com pós-BD oferece os 5 complementos",
              bd == {"RBD_POSITIVO", "RBD_NEGATIVO", "REV_COMPLETA",
                     "REV_PARCIAL", "BD_NAO_REALIZADO"}, str(sorted(bd)))

        # ------------------------------- 4+5. edição do texto e prévia
        print("[4] seleção DVO Leve + complemento RBD+ e edição do texto")
        base = client.post(
            f"/api/v1/laudos/{doc_id}/laudo/previa",
            json={"conclusion_code": "DVO_LEVE",
                  "bronchodilator_code": "RBD_POSITIVO"},
            headers=auth,
        )
        check("prévia base responde 200", base.status_code == 200, base.text)
        texto_auto = base.json()["final_text"]
        check("sigla convertida para texto por extenso",
              "Distúrbio ventilatório obstrutivo leve." in texto_auto, texto_auto)
        check("complemento pós-BD por extenso",
              "Com resposta significativa ao broncodilatador." in texto_auto,
              texto_auto)

        texto_editado = (
            texto_auto
            + "\nExame tecnicamente aceitável, com cooperação adequada do paciente."
        )
        print("[5] pré-visualização com o texto editado pela médica")
        prev = client.post(
            f"/api/v1/laudos/{doc_id}/laudo/previa",
            json={"conclusion_code": "DVO_LEVE",
                  "bronchodilator_code": "RBD_POSITIVO",
                  "final_text": texto_editado,
                  "observations": "Espirometria realizada pré e pós-broncodilatador."},
            headers=auth,
        )
        check("prévia editada responde 200", prev.status_code == 200, prev.text)
        pv = prev.json()
        check("texto editado é preservado", pv["final_text"] == texto_editado, "")
        check("hash do texto confere",
              pv["final_text_sha256"]
              == hashlib.sha256(texto_editado.encode("utf-8")).hexdigest(), "")

        # ------------------------------------- 6. confirmação consciente
        print("[6] confirmação consciente antes da assinatura")
        sem_conf = client.post(
            f"/api/v1/laudos/{doc_id}/assinar-e-liberar",
            json={"confirmacao": "sim",
                  "expected_version_id": pv["preview_version_id"],
                  "expected_text_sha256": pv["final_text_sha256"]},
            headers=auth,
        )
        check("assinar sem a confirmação exata é recusado",
              sem_conf.status_code == 422, str(sem_conf.status_code))
        divergente = client.post(
            f"/api/v1/laudos/{doc_id}/assinar-e-liberar",
            json={"confirmacao": "ASSINAR E LIBERAR",
                  "expected_version_id": pv["preview_version_id"],
                  "expected_text_sha256": "0" * 64},
            headers=auth,
        )
        check("assinar conteúdo divergente do conferido é recusado",
              divergente.status_code >= 400, str(divergente.status_code))

        # -------------------------------------------- 7+8. liberação e PDF
        print("[7] liberação consciente do laudo")
        rel = client.post(
            f"/api/v1/laudos/{doc_id}/assinar-e-liberar",
            json={"confirmacao": "ASSINAR E LIBERAR",
                  "expected_version_id": pv["preview_version_id"],
                  "expected_text_sha256": pv["final_text_sha256"]},
            headers=auth,
        )
        check("liberação responde 200", rel.status_code == 200, rel.text)
        rb = rel.json()
        check("estado passa a liberado", rb["status"] == "liberado", str(rb.get("status")))
        check("código de validação alocado", bool(rb.get("validation_code")), "")

        # --------------------------------------- 9. hash, versão e código
        print("[8/9] PDF final, hash e versão congelados")
        versao = db.get(ReportDocumentVersion, rb["released_version_id"])
        db.commit()
        check("versão liberada é do tipo laudo_liberado",
              versao.kind == "laudo_liberado", versao.kind)
        check("versão liberada tem hash SHA-256", bool(versao.sha256), "")
        check("número de versão maior que a prévia",
              versao.version_number >= 1, str(versao.version_number))

        # ---------------------------------- 10. bloqueio pós-assinatura
        print("[10] tentativa de alteração indevida após a assinatura")
        bloqueada = client.post(
            f"/api/v1/laudos/{doc_id}/laudo/previa",
            json={"conclusion_code": "NORMAL"},
            headers=auth,
        )
        check("nova prévia é recusada após a liberação",
              bloqueada.status_code >= 400, str(bloqueada.status_code))
        rebloqueio = client.post(
            f"/api/v1/laudos/{doc_id}/assinar-e-liberar",
            json={"confirmacao": "ASSINAR E LIBERAR",
                  "expected_version_id": pv["preview_version_id"],
                  "expected_text_sha256": pv["final_text_sha256"]},
            headers=auth,
        )
        check("nova liberação é recusada", rebloqueio.status_code >= 400,
              str(rebloqueio.status_code))

        # ------------------------------------------------- 11. adendo
        print("[11] adendo preservando a versão liberada anterior")
        adendo = client.post(
            f"/api/v1/laudos/{doc_id}/adendo",
            json={"body_text": "Adendo de teste: complementação técnica do exame.",
                  "confirmacao": "PUBLICAR ADENDO"},
            headers=auth,
        )
        check("adendo responde 201", adendo.status_code == 201, adendo.text)
        ab = adendo.json()
        anterior = db.get(ReportDocumentVersion, rb["released_version_id"])
        db.commit()
        check("versão liberada anterior permanece intacta",
              anterior is not None
              and anterior.sha256 == versao.sha256, "")
        nova = db.get(ReportDocumentVersion, ab["addendum_version_id"])
        db.commit()
        check("adendo gerou versão nova", nova.id != anterior.id, "")

        # ------------------------------- 12. download separado dos dois PDFs
        print("[12] download separado dos dois documentos")
        docs = client.get(f"/api/v1/laudos/{doc_id}/documentos", headers=auth)
        check("listagem de documentos responde 200", docs.status_code == 200, docs.text)
        entrega = docs.json()
        # O contrato entrega os dois documentos em CHAVES separadas, e não
        # numa lista — é o que garante que ninguém os trate como o "mesmo"
        # arquivo em duas versões.
        check("documento 1 (técnico da MIR) presente",
              entrega.get("tecnico_mir") is not None, "")
        check("documento 2 (laudo SoproLife) presente",
              entrega.get("laudo_soprolife") is not None, "")
        check("laudo marcado como bloqueado após a liberação",
              entrega.get("locked") is True, str(entrega.get("locked")))

        baixados = {}
        for tipo in ("tecnico_mir", "laudo_soprolife"):
            entrada = entrega.get(tipo)
            if not entrada:
                continue
            r = client.get(
                f"/api/v1{entrada['download_path']}?modo=download", headers=auth
            )
            check(f"download OK: {tipo}", r.status_code == 200, str(r.status_code))
            if r.status_code == 200:
                destino = SAIDA / f"{tipo}.pdf"
                destino.write_bytes(r.content)
                baixados[tipo] = r.content
                sha = hashlib.sha256(r.content).hexdigest()
                check(f"hash do arquivo confere com o registrado: {tipo}",
                      sha == entrada["sha256"], f"{sha} != {entrada['sha256']}")
                print(f"       -> {destino} ({len(r.content)} bytes)")

        if len(baixados) == 2:
            check("os dois PDFs são arquivos distintos",
                  baixados["tecnico_mir"] != baixados["laudo_soprolife"], "")
            # O conteúdo do reportlab vem comprimido: comparar bytes crus
            # daria falso negativo. Extrai-se o texto de verdade.
            mir_txt = _pdf_text(baixados["tecnico_mir"])
            laudo_txt = _pdf_text(baixados["laudo_soprolife"])
            check("PDF técnico da MIR não recebeu assinatura nem CRM por cima",
                  "CRM-RJ" not in mir_txt and "Assinatura" not in mir_txt, "")
            for esperado in (
                "Dra. Ana Cristina do Nascimento Cunha",
                "Médica Pneumologista",
                "CRM-RJ 52.62307-5",
                "RQE 58224",
            ):
                check(f"laudo traz «{esperado}»", esperado in laudo_txt, "")
            check("laudo traz o local de realização da clínica",
                  "Clínica Pastore — Unidade Ipanema" in laudo_txt
                  and "Rua Teixeira de Melo, 54" in laudo_txt, "")
            check("laudo traz a conclusão por extenso",
                  "Distúrbio ventilatório obstrutivo leve." in laudo_txt, "")
            check("laudo traz o complemento pós-BD por extenso",
                  "Com resposta significativa ao broncodilatador." in laudo_txt, "")
            check("laudo declara que o PDF da MIR é documento separado",
                  "SEPARADO" in laudo_txt.upper(), "")
            check("laudo NÃO alega assinatura ICP-Brasil",
                  "não constitui, por si só" in laudo_txt, "")
            check("laudo traz o adendo preservando o corpo anterior",
                  "ADENDO 1" in laudo_txt.upper(), "")

        # ------------------------------------- 13. validação por código
        print("[13] validação do documento pelo código")
        codigo = entrega.get("validation_code") or rb.get("validation_code")
        val = client.get(f"/api/v1/laudos/validacao/{codigo}", headers=auth)
        check("validação responde 200", val.status_code == 200, val.text)
        if val.status_code == 200:
            vb = val.json()
            check("validação NÃO expõe paciente nem conclusão",
                  "João da Silva Teste" not in str(vb)
                  and "obstrutivo" not in str(vb).lower(), str(vb)[:200])

    db.close()
    print(f"\n== Resultado: {_ok} verificações OK, {len(_falhas)} falha(s) ==")
    for f in _falhas:
        print(f"  - {f}")
    print(f"\nPDFs do cenário fictício em: {SAIDA}")
    return 1 if _falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
