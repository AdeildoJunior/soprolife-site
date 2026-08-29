"""M25.30 — importação administrativa de PDFs assinados fora do fluxo.

Três laudos foram assinados de verdade enquanto a recepção estava quebrada.
Os bytes assinados existem; o sistema nunca os recebeu. Regularizá-los é uma
manutenção histórica, e a única forma honesta de fazê-la é submeter os
arquivos às MESMAS guardas documentais da M25.29H — trocando apenas quem
executa o registro, nunca o que é verificado.

O que estes testes provam, e por quê:

* **o caminho feliz existe e é estreito** — o arquivo certo, no laudo certo,
  com o SHA do manifesto, entra e sai da fila de "aguardando assinatura";
* **cada guarda recusa sozinha** — código de verificação trocado, versão
  final diferente da esperada, prévia assinada, PDF sem assinatura nenhuma e
  arquivo de outro laudo. São exatamente os cinco enganos que produziram o
  incidente histórico, e nenhum deles pode passar por ser "administrativo";
* **nada histórico se perde** — um `ExternalSignedDocument` recusado
  continua byte a byte onde estava depois da regularização;
* **rodar duas vezes não duplica** — a segunda execução não escreve;
* **o download entrega o que entrou** — o SHA que a administração baixa é o
  SHA do arquivo assinado importado, não o do PDF final.

Nada aqui usa dado real: pacientes, médicas, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys

import pytest
from sqlalchemy import select

from app.models import (
    ASSINADO_ACEITO,
    ASSINADO_RECUSADO,
    ExternalSignedDocument,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
)

NUCLEO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _carregar(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _carregar_script():
    """Carrega o script de manutenção como módulo, sem instalá-lo."""

    caminho = NUCLEO_ROOT / "scripts" / "importar_assinados_historicos.py"
    spec = importlib.util.spec_from_file_location(
        "_importar_assinados_historicos", caminho
    )
    modulo = importlib.util.module_from_spec(spec)
    # Registrar em `sys.modules` ANTES de executar: `@dataclass` resolve as
    # anotações pelo módulo declarante, e um módulo não registrado deixa
    # `cls.__module__` apontando para o nada.
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


_M29D = _carregar("test_m25_29d_fluxo_conclusao_assinatura.py")
IMPORTADOR = _carregar_script()

_assinar_por_fora = _M29D._assinar_por_fora
_caso_em_elaboracao = _M29D._caso_em_elaboracao
_concluir = _M29D._concluir
_minimal_pdf = _M29D._minimal_pdf
reports_enabled = _M29D.reports_enabled  # fixture autouse


# ------------------------------------------------------------- utilidades


def _baixar_para_assinar(client, caso) -> bytes:
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.content


def _baixar_previa_assinavel(client, caso) -> bytes:
    """Os bytes da PRÉVIA — a folha errada do incidente histórico."""

    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}"
        f"/versoes/{caso['previa']['preview_version_id']}/conteudo"
        "?modo=download",
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.content


def _versao_final(db, document_id) -> ReportDocumentVersion:
    document = db.get(ReportDocument, document_id)
    return db.get(ReportDocumentVersion, document.current_version_id)


def _manifesto_do_caso(db, caso, dados: bytes) -> dict:
    """O manifesto CORRETO deste laudo — a base que cada teste distorce."""

    db.expire_all()
    document = db.get(ReportDocument, caso["document_id"])
    exame = db.get(SpirometryExam, document.spirometry_exam_id)
    final = _versao_final(db, document.id)
    return {
        "lau": document.public_code,
        "esp": exame.public_code,
        "validation_code": document.validation_code,
        "versao_final": final.version_number,
        "sha256": hashlib.sha256(dados).hexdigest(),
    }


def _escrever(tmp_path, nome: str, dados: bytes) -> pathlib.Path:
    caminho = tmp_path / nome
    caminho.write_bytes(dados)
    return caminho


def _casos(manifestos: list[dict], tmp_path) -> tuple:
    arquivo = tmp_path / "manifesto.json"
    arquivo.write_text(json.dumps(manifestos), encoding="utf-8")
    return IMPORTADOR.carregar_manifesto(arquivo)


def _executar(db, *, caminhos, manifestos, ator, tmp_path, aplicar=False):
    return IMPORTADOR.executar(
        db,
        caminhos=list(caminhos),
        casos=_casos(manifestos, tmp_path),
        ator_id=ator,
        aplicar=aplicar,
    )


@pytest.fixture()
def caso_assinado(client, auth, db):
    """Um laudo concluído + os bytes que a médica assinou por fora."""

    caso = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente M2530", suffix="30"
    )
    _concluir(client, caso)
    final = _baixar_para_assinar(client, caso)
    return caso, final, _assinar_por_fora(final)


# ------------------------------------------------------- 1. o caminho feliz


def test_lau_correto_e_regularizado_e_sai_da_fila(
    client, auth, db, caso_assinado, tmp_path, users
):
    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "PACIENTE SINTETICO - Assinado.pdf", assinado)

    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    antes = {i["report_code"]: i["estado"] for i in fila["itens"]}
    assert antes[manifesto["lau"]] == "aguardando_assinatura"

    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 0
    )

    db.expire_all()
    document = db.get(ReportDocument, caso["document_id"])
    registro = db.execute(
        select(ExternalSignedDocument).where(
            ExternalSignedDocument.report_document_id == document.id
        )
    ).scalar_one()

    assert registro.status == ASSINADO_ACEITO
    assert registro.sha256 == manifesto["sha256"]
    assert registro.received_filename == "PACIENTE SINTETICO - Assinado.pdf"
    assert registro.source_version_id == document.current_version_id
    assert registro.source_sha256 == _versao_final(db, document.id).sha256

    # A médica continua sendo a responsável clínica; quem EXECUTOU foi a
    # conta administrativa. Confundir os dois é o erro que o script evita.
    assert registro.physician_profile_id == caso["profile"]["id"]
    assert registro.uploader_user_id == users["admin"].id

    # Ninguém conferiu assinatura fora daqui, e nada foi entregue.
    assert registro.validated_by_user_id is None
    assert registro.validated_at is None
    assert registro.validation_method is None
    assert registro.delivered_at is None

    nova = db.get(ReportDocumentVersion, registro.report_document_version_id)
    assert nova.kind == "laudo_assinado_externo_recebido"
    assert nova.sha256 == manifesto["sha256"]
    # A versão CORRENTE do laudo não muda: o assinado é uma versão a mais,
    # nunca a substituição do documento final.
    assert document.current_version_id != nova.id

    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    ).json()
    depois = {i["report_code"]: i["estado"] for i in fila["itens"]}
    assert depois[manifesto["lau"]] == "pronto_para_entrega"

    aguardando = next(
        e for e in fila["estados"] if e["chave"] == "aguardando_assinatura"
    )
    assert aguardando["total"] == 0

    assinado_serializado = next(
        i for i in fila["itens"] if i["report_code"] == manifesto["lau"]
    )["assinado"]
    assert assinado_serializado["assinatura_verificada_criptograficamente"] is False


def test_auditoria_registra_manutencao_administrativa(
    client, auth, db, caso_assinado, tmp_path, users
):
    """A trilha precisa dizer que foi manutenção, e por quem."""

    from app.models import AuditLog

    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    _executar(
        db,
        caminhos=[arquivo],
        manifestos=[manifesto],
        ator=users["admin"].email,
        tmp_path=tmp_path,
        aplicar=True,
    )

    registro = db.execute(
        select(AuditLog).where(
            AuditLog.acao == "laudo_assinado_regularizado_historicamente"
        )
    ).scalar_one()
    assert registro.user_id == users["admin"].id
    assert registro.entidade_id == caso["document_id"]
    assert registro.detalhes["contexto"] == "manutencao_administrativa_historica"
    assert registro.detalhes["report_code"] == manifesto["lau"]
    assert registro.detalhes["exam_code"] == manifesto["esp"]
    assert registro.detalhes["sha256"] == manifesto["sha256"]
    # Dito em todo registro, porque a ausência é o que se esquece.
    assert registro.detalhes["qualified_signature"] is False
    # Nenhum nome de paciente entra na trilha.
    assert "TESTE APAGAR" not in json.dumps(registro.detalhes)


# --------------------------------------------------- 2. cada guarda recusa


def _recusa(db, *, caminhos, manifestos, ator, tmp_path) -> int:
    """Roda com --apply e exige que NADA tenha sido escrito."""

    codigo = _executar(
        db,
        caminhos=caminhos,
        manifestos=manifestos,
        ator=ator,
        tmp_path=tmp_path,
        aplicar=True,
    )
    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().all() == []
    ), "recusa não pode escrever nada"
    return codigo


def test_validation_code_errado_recusa(db, caso_assinado, tmp_path, users):
    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    manifesto["validation_code"] = "ZZZZZZZZZZZZ"
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_versao_final_esperada_errada_recusa(db, caso_assinado, tmp_path, users):
    """O manifesto aponta outra versão final. O arquivo pode ser o certo."""

    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    manifesto["versao_final"] = manifesto["versao_final"] + 1
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_previa_assinada_recusa(client, db, caso_assinado, tmp_path, users):
    """O incidente histórico: a folha errada, assinada com certificado."""

    caso, _final, _assinado = caso_assinado
    previa_assinada = _assinar_por_fora(_baixar_previa_assinavel(client, caso))
    manifesto = _manifesto_do_caso(db, caso, previa_assinada)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", previa_assinada)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_pdf_sem_assinatura_recusa(db, caso_assinado, tmp_path, users):
    """O PDF final devolvido como se fosse o assinado."""

    caso, final, _assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, final)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", final)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_arquivo_de_outro_laudo_recusa(client, auth, db, tmp_path, users):
    """O assinado da paciente A oferecido para regularizar o laudo de B."""

    caso_a = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente A", suffix="31"
    )
    _concluir(client, caso_a)
    assinado_a = _assinar_por_fora(_baixar_para_assinar(client, caso_a))

    caso_b = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente B", suffix="32"
    )
    _concluir(client, caso_b)
    assinado_b = _assinar_por_fora(_baixar_para_assinar(client, caso_b))

    # O manifesto é o de B; os bytes (e o conteúdo) são de A. Nem o SHA do
    # manifesto ajuda: a identificação vem do CONTEÚDO, e ele diz A.
    manifesto_b = _manifesto_do_caso(db, caso_b, assinado_a)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado_a)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto_b],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )

    # E o contrário do erro: com o manifesto de B e o arquivo de B, passa.
    manifesto_b_certo = _manifesto_do_caso(db, caso_b, assinado_b)
    arquivo_b = _escrever(tmp_path, "SINTETICO B - Assinado.pdf", assinado_b)
    assert (
        _executar(
            db,
            caminhos=[arquivo_b],
            manifestos=[manifesto_b_certo],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 0
    )


def test_nome_do_arquivo_nao_identifica_o_laudo(db, caso_assinado, tmp_path, users):
    """Renomear o arquivo não muda a que laudo ele pertence.

    A identificação é pelos bytes. O nome só vira `received_filename` — que
    é registro do que chegou, nunca critério de pareamento.
    """

    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "LAU-999999 nome enganoso.pdf", assinado)

    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 0
    )
    db.expire_all()
    registro = db.execute(select(ExternalSignedDocument)).scalar_one()
    assert registro.report_document_id == caso["document_id"]
    assert registro.received_filename == "LAU-999999 nome enganoso.pdf"


def test_ator_medico_e_recusado(db, caso_assinado, tmp_path, client, auth):
    """A conta da médica não pode ser o ator da manutenção.

    O ponto inteiro do script é não registrar como dela um upload que ela
    não executou. Aceitar a conta dela como ator anularia o script.
    """

    from app.models import User

    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    medica = db.execute(
        select(User).where(User.email.like("medica-m25-29d-30@%"))
    ).scalar_one()

    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=medica.email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 2
    )
    db.expire_all()
    assert db.execute(select(ExternalSignedDocument)).scalars().all() == []


# ------------------------------------------------------- 3. idempotência


def test_segunda_execucao_nao_duplica(db, caso_assinado, tmp_path, users):
    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    for _ in range(2):
        assert (
            _executar(
                db,
                caminhos=[arquivo],
                manifestos=[manifesto],
                ator=users["admin"].email,
                tmp_path=tmp_path,
                aplicar=True,
            )
            == 0
        )

    db.expire_all()
    registros = (
        db.execute(
            select(ExternalSignedDocument).where(
                ExternalSignedDocument.report_document_id == caso["document_id"]
            )
        )
        .scalars()
        .all()
    )
    assert len(registros) == 1

    versoes_assinadas = (
        db.execute(
            select(ReportDocumentVersion).where(
                ReportDocumentVersion.report_document_id == caso["document_id"],
                ReportDocumentVersion.kind == "laudo_assinado_externo_recebido",
            )
        )
        .scalars()
        .all()
    )
    assert len(versoes_assinadas) == 1


# ----------------------------------------- 4. o histórico recusado sobrevive


def test_historico_recusado_e_preservado(db, caso_assinado, tmp_path, users):
    """O registro da recusa continua sendo evidência do que aconteceu.

    Ele não pode ser apagado, reaproveitado nem reescrito — nem os bytes da
    versão dele. É a única prova de que uma folha errada circulou.
    """

    from app.models import (
        BATCH_DIRECAO_UPLOAD,
        PAREAMENTO_METADADO,
        ExternalSignatureBatch,
    )
    from app.ids import allocate_public_code

    caso, final, assinado = caso_assinado
    document = db.get(ReportDocument, caso["document_id"])
    versao_final = _versao_final(db, document.id)

    # Um recusado histórico: a versão final devolvida sem assinatura, como
    # aconteceu de verdade antes da M25.29H.
    lote = ExternalSignatureBatch(
        public_code=allocate_public_code(db, "external_signature_batches"),
        direction=BATCH_DIRECAO_UPLOAD,
        physician_profile_id=caso["profile"]["id"],
        created_by_user_id=users["admin"].id,
        document_count=1,
    )
    db.add(lote)
    db.flush()
    historico = ExternalSignedDocument(
        report_document_id=document.id,
        report_document_version_id=versao_final.id,
        source_version_id=versao_final.id,
        source_sha256=versao_final.sha256,
        batch_id=lote.id,
        physician_profile_id=caso["profile"]["id"],
        uploader_user_id=users["admin"].id,
        sha256=hashlib.sha256(final).hexdigest(),
        size_bytes=len(final),
        received_filename="devolvido sem assinatura.pdf",
        match_method=PAREAMENTO_METADADO,
        status=ASSINADO_RECUSADO,
    )
    db.add(historico)
    db.commit()
    antes = (
        historico.id,
        historico.status,
        historico.sha256,
        historico.report_document_version_id,
        historico.received_filename,
    )

    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)
    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 0
    )

    db.expire_all()
    ainda = db.get(ExternalSignedDocument, antes[0])
    assert ainda is not None
    assert (
        ainda.id,
        ainda.status,
        ainda.sha256,
        ainda.report_document_version_id,
        ainda.received_filename,
    ) == antes

    # E o novo registro é OUTRO, não uma reescrita do recusado.
    vigentes = (
        db.execute(
            select(ExternalSignedDocument).where(
                ExternalSignedDocument.report_document_id == document.id,
                ExternalSignedDocument.status == ASSINADO_ACEITO,
            )
        )
        .scalars()
        .all()
    )
    assert len(vigentes) == 1
    assert vigentes[0].id != historico.id


def test_reimportar_bytes_ja_recusados_para(db, caso_assinado, tmp_path, users):
    """Regularizar o que foi recusado contradiria a recusa. O script para."""

    from app.models import (
        BATCH_DIRECAO_UPLOAD,
        PAREAMENTO_METADADO,
        ExternalSignatureBatch,
    )
    from app.ids import allocate_public_code

    caso, _final, assinado = caso_assinado
    document = db.get(ReportDocument, caso["document_id"])
    versao_final = _versao_final(db, document.id)

    lote = ExternalSignatureBatch(
        public_code=allocate_public_code(db, "external_signature_batches"),
        direction=BATCH_DIRECAO_UPLOAD,
        physician_profile_id=caso["profile"]["id"],
        created_by_user_id=users["admin"].id,
        document_count=1,
    )
    db.add(lote)
    db.flush()
    db.add(
        ExternalSignedDocument(
            report_document_id=document.id,
            report_document_version_id=versao_final.id,
            source_version_id=versao_final.id,
            source_sha256=versao_final.sha256,
            batch_id=lote.id,
            physician_profile_id=caso["profile"]["id"],
            uploader_user_id=users["admin"].id,
            sha256=hashlib.sha256(assinado).hexdigest(),
            size_bytes=len(assinado),
            received_filename="recusado por decisao humana.pdf",
            match_method=PAREAMENTO_METADADO,
            status=ASSINADO_RECUSADO,
        )
    )
    db.commit()

    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)
    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=True,
        )
        == 4
    )
    db.expire_all()
    assert (
        db.execute(
            select(ExternalSignedDocument).where(
                ExternalSignedDocument.status == ASSINADO_ACEITO
            )
        )
        .scalars()
        .all()
        == []
    )


# ------------------------------------------- 5. o download entrega o que entrou


def test_download_administrativo_devolve_o_sha_importado(
    client, auth, db, caso_assinado, tmp_path, users
):
    caso, final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    _executar(
        db,
        caminhos=[arquivo],
        manifestos=[manifesto],
        ator=users["admin"].email,
        tmp_path=tmp_path,
        aplicar=True,
    )

    resposta = client.get(
        f"/api/v1/laudos/{caso['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    baixado = hashlib.sha256(resposta.content).hexdigest()
    assert baixado == manifesto["sha256"]
    # E não é o PDF final sem assinatura, que é o engano que se quer evitar.
    assert baixado != hashlib.sha256(final).hexdigest()


# ------------------------------------------------------ 6. tudo ou nada


def test_um_divergente_impede_a_gravacao_de_todos(
    client, auth, db, tmp_path, users
):
    """Um caso ruim no lote não deixa o caso bom entrar sozinho.

    Regularização histórica é um ato só. Aplicar metade deixaria a operação
    sem saber qual metade, e é justamente o estado que a missão veio
    encerrar.
    """

    caso_a = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente C", suffix="33"
    )
    _concluir(client, caso_a)
    assinado_a = _assinar_por_fora(_baixar_para_assinar(client, caso_a))

    caso_b = _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente D", suffix="34"
    )
    _concluir(client, caso_b)
    final_b = _baixar_para_assinar(client, caso_b)

    bom = _escrever(tmp_path, "BOM - Assinado.pdf", assinado_a)
    ruim = _escrever(tmp_path, "RUIM - Assinado.pdf", final_b)

    assert (
        _recusa(
            db,
            caminhos=[bom, ruim],
            manifestos=[
                _manifesto_do_caso(db, caso_a, assinado_a),
                _manifesto_do_caso(db, caso_b, final_b),
            ],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_manifesto_sem_arquivo_correspondente_para(
    db, caso_assinado, tmp_path, users
):
    """Faltar um dos três é motivo de parada, não de aplicação parcial."""

    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    ausente = dict(manifesto, lau="LAU-999998", esp="ESP-999998")
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    assert (
        _recusa(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto, ausente],
            ator=users["admin"].email,
            tmp_path=tmp_path,
        )
        == 4
    )


def test_dry_run_nao_escreve(db, caso_assinado, tmp_path, users):
    caso, _final, assinado = caso_assinado
    manifesto = _manifesto_do_caso(db, caso, assinado)
    arquivo = _escrever(tmp_path, "SINTETICO - Assinado.pdf", assinado)

    assert (
        _executar(
            db,
            caminhos=[arquivo],
            manifestos=[manifesto],
            ator=users["admin"].email,
            tmp_path=tmp_path,
            aplicar=False,
        )
        == 0
    )
    db.expire_all()
    assert db.execute(select(ExternalSignedDocument)).scalars().all() == []
    assert (
        db.execute(
            select(ReportDocumentVersion).where(
                ReportDocumentVersion.kind == "laudo_assinado_externo_recebido"
            )
        )
        .scalars()
        .all()
        == []
    )


# ---------------------------------------- 7. o manifesto versionado da missão


def test_manifesto_versionado_tem_os_tres_casos_da_missao():
    """O manifesto é a memória da missão — e não carrega nome de paciente."""

    casos = {c.lau: c for c in IMPORTADOR.CASOS_M25_30}
    assert set(casos) == {"LAU-000010", "LAU-000014", "LAU-000015"}
    assert casos["LAU-000010"].esp == "ESP-000029"
    assert casos["LAU-000014"].esp == "ESP-000025"
    assert casos["LAU-000015"].esp == "ESP-000030"
    assert casos["LAU-000010"].versao_final == 3
    assert casos["LAU-000014"].versao_final == 3
    assert casos["LAU-000015"].versao_final == 4
    for caso in casos.values():
        assert len(caso.sha256) == 64
        assert caso.sha256 == caso.sha256.lower()
        assert len(caso.validation_code) == 12


def test_identificacao_ignora_o_nome_e_le_o_conteudo(caso_assinado):
    """A função de identificação não recebe o nome do arquivo, e é o ponto."""

    _caso, final, assinado = caso_assinado
    achado = IMPORTADOR.identificar_pelo_conteudo(assinado)
    assert achado.lau is not None
    assert achado.match_method is not None
    # Um PDF qualquer, sem carimbo nem código impresso, não é identificável.
    vazio = IMPORTADOR.identificar_pelo_conteudo(_minimal_pdf())
    assert vazio.lau is None
    assert vazio.match_method is None
    assert IMPORTADOR.identificar_pelo_conteudo(final).lau == achado.lau
