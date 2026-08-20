"""M25.29H — aceite automático do assinado, isolamento do download, paridade.

Três coisas, e a primeira explica as outras duas.

**A conferência administrativa manual foi eliminada porque não conferia.**
A auditoria da M25.29H encontrou um documento marcado `validado_externamente`
— conferido por uma pessoa identificada — que era byte a byte igual ao PDF
final e não continha estrutura de assinatura nenhuma. Um falso positivo. O
que substitui aquele clique não é confiança: são guardas documentais que
rodam sobre os bytes no momento do recebimento, e que teriam recusado aquele
arquivo em milissegundos.

**O download precisa provar isolamento, não alegá-lo.** Houve relato de um
"Baixar laudo assinado" que trouxe PDF de outra paciente. A auditoria em
produção mostrou isolamento preservado, mas relato de integridade não se
fecha com uma auditoria pontual: aqui dois pacientes sintéticos assinam
documentos diferentes, e o teste compara HASH do que entra com HASH do que
sai, nos dois sentidos.

**Administrar não é laudar.** O sócio ganha o conjunto administrativo inteiro
da conta principal e nada da autoria clínica. `admin` não implica `medico`, e
os testes provam os dois lados dessa fronteira.

Nada aqui usa dado real: pacientes, médicas, CRMs e PDFs são sintéticos.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import pathlib
import re

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from app.models import ExternalSignedDocument, User
from app.services.signature_acceptance import (
    MOTIVO_ASSOCIACAO_FRACA,
    MOTIVO_IDENTICO_AO_FINAL,
    MOTIVO_PREVIA,
    MOTIVO_SEM_ASSINATURA,
    GuardasDocumentais,
)

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()


def _carregar(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M29D = _carregar("test_m25_29d_fluxo_conclusao_assinatura.py")

_assinar_por_fora = _M29D._assinar_por_fora
_caso_em_elaboracao = _M29D._caso_em_elaboracao
_concluir = _M29D._concluir
reports_enabled = _M29D.reports_enabled  # fixture autouse


def _js_sem_comentarios() -> str:
    sem_bloco = re.sub(r"/\*.*?\*/", "", WORKFLOW_JS, flags=re.S)
    return re.sub(r"^\s*//.*$", "", sem_bloco, flags=re.M)


# ------------------------------------------------------------- utilidades


def _baixar_para_assinar(client, caso) -> bytes:
    resposta = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.content


def _enviar(client, caso, dados: bytes, nome="TESTE APAGAR - assinado.pdf"):
    return client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={"arquivos": (nome, dados, "application/pdf")},
        headers=caso["doctor_auth"],
    )


def _sem_metadados(pdf: bytes) -> bytes:
    """Reescreve o PDF perdendo o carimbo, preservando o que está impresso.

    É o que um assinador que reconstrói o arquivo faria. Sobra apenas o que
    a folha mostra — e o código de verificação está impresso nela.
    """

    escritor = PdfWriter()
    for pagina in PdfReader(io.BytesIO(pdf)).pages:
        escritor.add_page(pagina)
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def _pdf_apenas_com_o_codigo(report_code: str) -> bytes:
    """Um PDF que só traz o código LAU impresso. Nada mais o liga ao laudo."""

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(72, 760, f"Documento avulso {report_code}")
    c.save()
    return _assinar_por_fora(buffer.getvalue())


def _fila(client, auth):
    resposta = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


@pytest.fixture()
def caso_a(client, auth, db):
    return _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente A", suffix="02"
    )


@pytest.fixture()
def caso_b(client, auth, db):
    return _caso_em_elaboracao(
        client, auth, db, nome_paciente="TESTE APAGAR Paciente B", suffix="03"
    )


@pytest.fixture()
def aceito(client, caso_a):
    """Um laudo concluído, assinado por fora e ACEITO — sem clique nenhum."""

    _concluir(client, caso_a)
    final = _baixar_para_assinar(client, caso_a)
    assinado = _assinar_por_fora(final)
    resposta = _enviar(client, caso_a, assinado)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 1, corpo
    caso_a["assinado_bytes"] = assinado
    caso_a["final_bytes"] = final
    caso_a["signed_document_id"] = corpo["identificados"][0][
        "signed_document_id"
    ]
    return caso_a


# =====================================================================
# 1-2. O que passa no aceite automático
# =====================================================================


def test_assinado_com_carimbo_forte_fica_pronto_para_entrega(
    client, auth, aceito, db
):
    """Item 1 — assinar e devolver basta. Ninguém mais toca no documento."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "recebido_assinado"
    assert registro.confirmed_at is not None
    assert registro.validated_at is None
    assert registro.delivered_at is None

    alvo = [
        item for item in _fila(client, auth)["itens"]
        if item["document_id"] == aceito["document_id"]
    ]
    assert alvo, "o laudo precisa aparecer na fila administrativa"
    assert alvo[0]["estado"] == "pronto_para_entrega"


def test_codigo_de_verificacao_sozinho_sustenta_o_aceite(
    client, auth, caso_a, db
):
    """Item 2 — sem carimbo, o código de verificação da FINAL basta.

    Ele não é o código LAU: a prévia imprime "—" no lugar dele, então um
    arquivo que o traz não pode ter vindo de uma prévia.
    """

    _concluir(client, caso_a)
    final = _baixar_para_assinar(client, caso_a)
    devolvido = _assinar_por_fora(_sem_metadados(final))

    resposta = _enviar(client, caso_a, devolvido)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 1, corpo

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "recebido_assinado"


# =====================================================================
# 3-5. O que NÃO passa
# =====================================================================


def test_previa_assinada_e_recusada(client, caso_a, db):
    """Item 3 — o incidente histórico do LAU-000014, agora impossível."""

    previa = client.get(
        f"/api/v1/laudos/{caso_a['document_id']}/versoes/"
        f"{caso_a['previa']['preview_version_id']}/conteudo?modo=download",
        headers=caso_a["doctor_auth"],
    ).content
    _concluir(client, caso_a)

    resposta = _enviar(client, caso_a, _assinar_por_fora(previa))
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 0, corpo
    assert not db.execute(select(ExternalSignedDocument)).scalars().all()


def test_final_devolvido_sem_assinar_e_recusado(client, caso_a, db):
    """Item 4 — o incidente do LAU-000010, 011 e 015."""

    _concluir(client, caso_a)
    final = _baixar_para_assinar(client, caso_a)

    resposta = _enviar(client, caso_a, final)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 0, corpo
    mensagens = " ".join(a["mensagem"] for a in corpo["arquivos"])
    assert "igual ao PDF final sem assinatura" in mensagens
    # Nem sequer vira linha no banco: não há fila para ninguém conferir.
    assert not db.execute(select(ExternalSignedDocument)).scalars().all()


def test_apenas_o_codigo_do_laudo_nao_autoaceita(client, caso_a, db):
    """Item 5 — o fallback fraco que causou o LAU-000014 não aceita nada.

    O código LAU está impresso na prévia e no final. Ele identifica o
    documento; não prova de qual folha o arquivo saiu.
    """

    _concluir(client, caso_a)
    avulso = _pdf_apenas_com_o_codigo(caso_a["report_code"])

    resposta = _enviar(client, caso_a, avulso)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 0, corpo
    assert not db.execute(select(ExternalSignedDocument)).scalars().all()


def test_guardas_recusam_cada_modo_de_falha_pelo_motivo_certo():
    """A ordem dos motivos é contrato: ela vira texto na tela da médica."""

    def guarda(**ajustes):
        base = dict(
            tem_versao_final=True,
            origem_e_a_versao_final=True,
            parece_previa=False,
            identico_ao_final=False,
            tem_estrutura_assinatura=True,
            contem_o_final=True,
            metadado_coerente=True,
            codigo_validacao_coerente=True,
        )
        base.update(ajustes)
        return GuardasDocumentais(**base)

    assert guarda().aceito
    assert guarda(parece_previa=True).motivo == MOTIVO_PREVIA
    assert guarda(tem_estrutura_assinatura=False).motivo == MOTIVO_SEM_ASSINATURA
    assert guarda(identico_ao_final=True).motivo == MOTIVO_IDENTICO_AO_FINAL
    # Só o código LAU: nenhuma das três associações fortes vale.
    fraco = guarda(
        contem_o_final=False,
        metadado_coerente=False,
        codigo_validacao_coerente=False,
    )
    assert not fraco.associacao_forte
    assert fraco.motivo == MOTIVO_ASSOCIACAO_FRACA
    assert not fraco.aceito


# =====================================================================
# 6-7. Um arquivo nunca encontra o laudo errado
# =====================================================================


def test_arquivo_de_outra_medica_nao_e_identificado(client, caso_a, caso_b):
    """Item 6 — o universo de pareamento é o da médica que envia."""

    _concluir(client, caso_a)
    assinado_de_a = _assinar_por_fora(_baixar_para_assinar(client, caso_a))
    _concluir(client, caso_b)

    resposta = _enviar(client, caso_b, assinado_de_a)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["aceitos"] == 0, corpo
    assert corpo["arquivos"][0]["resultado"] == "nao_identificado"


def test_arquivo_de_um_paciente_nunca_entra_no_laudo_do_outro(
    client, auth, caso_a, caso_b, db
):
    """Item 7 — dois laudos vivos ao mesmo tempo, e nenhum se confunde."""

    _concluir(client, caso_a)
    _concluir(client, caso_b)
    assinado_de_a = _assinar_por_fora(_baixar_para_assinar(client, caso_a))

    resposta = _enviar(client, caso_a, assinado_de_a)
    assert resposta.json()["aceitos"] == 1

    registros = db.execute(select(ExternalSignedDocument)).scalars().all()
    assert len(registros) == 1
    assert registros[0].report_document_id == caso_a["document_id"]
    assert registros[0].report_document_id != caso_b["document_id"]


# =====================================================================
# 8-9. O download entrega o arquivo daquele laudo, provado por hash
# =====================================================================


def test_download_do_assinado_entrega_o_hash_do_recebido(
    client, auth, aceito, db
):
    """Item 8 — byte a byte, o que sai é o que entrou."""

    resposta = client.get(
        f"/api/v1/laudos/{aceito['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"].startswith("application/pdf")

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    baixado = hashlib.sha256(resposta.content).hexdigest()
    assert baixado == registro.sha256
    assert baixado == hashlib.sha256(aceito["assinado_bytes"]).hexdigest()
    # E NÃO é o PDF final sem assinatura, que é o outro arquivo do caso.
    assert baixado != hashlib.sha256(aceito["final_bytes"]).hexdigest()


def test_dois_pacientes_sinteticos_nunca_cruzam_pdfs(
    client, auth, caso_a, caso_b, db
):
    """Item 9 — pedir A devolve A; pedir B devolve B. Sem exceção."""

    enviados = {}
    for caso in (caso_a, caso_b):
        _concluir(client, caso)
        assinado = _assinar_por_fora(_baixar_para_assinar(client, caso))
        assert _enviar(client, caso, assinado).json()["aceitos"] == 1
        enviados[caso["document_id"]] = hashlib.sha256(assinado).hexdigest()

    assert len(set(enviados.values())) == 2, "os dois PDFs têm de ser distintos"

    for document_id, esperado in enviados.items():
        resposta = client.get(
            f"/api/v1/laudos/{document_id}/assinado/conteudo",
            headers=auth("operacional"),
        )
        assert resposta.status_code == 200, resposta.text
        obtido = hashlib.sha256(resposta.content).hexdigest()
        assert obtido == esperado, f"documento {document_id} devolveu outro PDF"
        outros = [h for k, h in enviados.items() if k != document_id]
        assert obtido not in outros


def test_download_le_a_versao_recebida_e_nao_a_de_origem(
    client, auth, aceito, db
):
    """A rota resolve por `report_document_version_id`, e por mais nada.

    `source_version_id` é o PDF final SEM assinatura. Se a rota lesse dali,
    o administrador entregaria ao paciente um documento não assinado — que é
    a forma exata do relato que abriu esta missão.
    """

    resposta = client.get(
        f"/api/v1/laudos/{aceito['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 200, resposta.text
    obtido = hashlib.sha256(resposta.content).hexdigest()

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.report_document_version_id != registro.source_version_id
    assert obtido == registro.sha256
    assert obtido != registro.source_sha256


# =====================================================================
# 10-11. O que o sistema continua NÃO afirmando
# =====================================================================


def test_qualified_signature_continua_falso(client, auth, aceito, db):
    """Item 10 — aceitar não é validar cadeia ICP-Brasil."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert getattr(registro, "qualified_signature", False) is False

    alvo = [
        item for item in _fila(client, auth)["itens"]
        if item["document_id"] == aceito["document_id"]
    ][0]
    assert alvo["assinado"]["assinatura_verificada_criptograficamente"] is False


def test_aceite_automatico_nao_usa_validado_externamente(
    client, auth, aceito, db
):
    """Item 11 — ninguém validou externamente; o estado não pode dizer que sim."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status != "validado_externamente"
    assert registro.validated_by_user_id is None
    assert registro.validation_method is None

    from app.audit import AuditLog

    acoes = {
        linha.acao
        for linha in db.execute(select(AuditLog)).scalars().all()
    }
    assert "assinatura_conferida_externamente" not in acoes
    assert "laudo_assinado_aceito_automaticamente" in acoes


# =====================================================================
# 12-14. A tela
# =====================================================================


def test_nao_existe_registrar_conferencia_no_fluxo_novo():
    """Item 12 — o botão, a confirmação e a função saíram."""

    codigo = _js_sem_comentarios()
    assert "Registrar conferência do PDF assinado" not in codigo
    assert "renderConferenceConfirm" not in codigo
    assert "registerExternalValidation" not in codigo
    assert "data-delivery-validate" not in codigo


def test_medica_encerra_o_trabalho_no_upload(client, caso_a):
    """Item 13 — o envio responde com o fim do trabalho dela."""

    _concluir(client, caso_a)
    assinado = _assinar_por_fora(_baixar_para_assinar(client, caso_a))
    corpo = _enviar(client, caso_a, assinado).json()

    assert corpo["aceitos"] == 1
    assert corpo["estado"] == "recebido_assinado"
    assert "associado" in corpo["observacao"]

    codigo = _js_sem_comentarios()
    assert "Seu trabalho neste exame terminou." in codigo
    assert "confirmSignedBatch" not in codigo
    assert "data-signature-confirm" not in codigo


def test_admin_ve_pronto_para_entrega_e_pode_entregar(client, auth, aceito, db):
    """Item 14 — a administração baixa e entrega, sem etapa intermediária."""

    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{aceito['signed_document_id']}"
        "/entrega",
        json={"canal": "whatsapp"},
        headers=auth("operacional"),
    )
    assert entrega.status_code == 200, entrega.text
    assert entrega.json()["estado"] == "entregue"

    codigo = _js_sem_comentarios()
    assert "Documento assinado recebido e associado automaticamente" in codigo


# =====================================================================
# 15-18. Paridade administrativa, e o limite dela
# =====================================================================


def _promover(db, identificador: str, *, apply: bool):
    modulo = _carregar_script("promover_admin_soprolife.py")
    return modulo.promover(db, identificador=identificador, apply=apply)


def _carregar_script(nome: str):
    caminho = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / nome
    )
    spec = importlib.util.spec_from_file_location(f"_s_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_promocao_dry_run_nao_escreve(client, auth, users, db, capsys):
    """Nada é gravado sem `--apply`. É a regra de toda manutenção daqui."""

    antes = sorted(p.name for p in users["gestor"].roles)
    assert _promover(db, "gestor@teste.local", apply=False) == 0
    saida = capsys.readouterr().out
    assert "DRY-RUN" in saida

    db.expire_all()
    usuario = db.execute(
        select(User).where(User.email == "gestor@teste.local")
    ).scalar_one()
    assert sorted(p.name for p in usuario.roles) == antes


def test_promocao_iguala_a_autoridade_administrativa(
    client, auth, users, tokens, db, capsys
):
    """Item 15 e 17 — o sócio passa a poder o que a conta principal pode."""

    cabecalho = {"Authorization": f"Bearer {tokens['gestor']}"}
    administrativo = "/api/v1/admin/usuarios"

    assert client.get(administrativo, headers=cabecalho).status_code == 403
    # A conta principal já podia, e continua podendo.
    assert client.get(administrativo, headers=auth("admin")).status_code == 200

    assert _promover(db, "gestor@teste.local", apply=True) == 0
    capsys.readouterr()

    assert client.get(administrativo, headers=cabecalho).status_code == 200
    assert client.get(administrativo, headers=auth("admin")).status_code == 200
    assert (
        client.get(
            "/api/v1/laudos/assinatura-externa/fila", headers=cabecalho
        ).status_code
        == 200
    )


def test_promocao_e_idempotente(client, auth, users, db, capsys):
    """Rodar de novo não duplica papel nem grava nada."""

    assert _promover(db, "gestor@teste.local", apply=True) == 0
    capsys.readouterr()
    assert _promover(db, "gestor@teste.local", apply=True) == 0
    assert "NADA A FAZER" in capsys.readouterr().out

    db.expire_all()
    usuario = db.execute(
        select(User).where(User.email == "gestor@teste.local")
    ).scalar_one()
    assert sorted(p.name for p in usuario.roles) == ["admin"]


def test_promovido_nao_recebe_autoria_medica(
    client, auth, users, tokens, db, capsys
):
    """Item 16 — administrar o sistema não é assinar laudo.

    `admin` não implica `medico` na hierarquia, e o script não concede papel
    clínico nem cria perfil profissional. A fronteira é o ponto inteiro
    desta promoção.
    """

    assert _promover(db, "gestor@teste.local", apply=True) == 0
    saida = capsys.readouterr().out
    assert "NÃO — nunca, por desenho" in saida

    db.expire_all()
    usuario = db.execute(
        select(User).where(User.email == "gestor@teste.local")
    ).scalar_one()
    assert "medico" not in {p.name for p in usuario.roles}

    cabecalho = {"Authorization": f"Bearer {tokens['gestor']}"}
    clinico = client.get("/api/v1/laudos/meus", headers=cabecalho)
    assert clinico.status_code == 403, clinico.text
    assert (
        clinico.json()["erro"]["codigo"] == "papel_medico_explicito_necessario"
    )


def test_medico_nao_ganha_funcoes_administrativas(client, caso_a):
    """Item 18 — a fronteira vale nos dois sentidos."""

    for rota in ("/api/v1/admin/usuarios", "/api/v1/laudos/assinatura-externa/fila"):
        resposta = client.get(rota, headers=caso_a["doctor_auth"])
        assert resposta.status_code == 403, (rota, resposta.text)


# =====================================================================
# 19. A manutenção que reconcilia a fila histórica
# =====================================================================


def test_reconciliacao_dry_run_nao_escreve(client, auth, aceito, db, capsys):
    modulo = _carregar_script("reconciliar_fila_assinada.py")
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = "recebido_validacao_pendente"
    db.commit()

    assert modulo.reconciliar(db, apply=False, laudos=[]) == 0
    saida = capsys.readouterr().out
    assert "DRY-RUN" in saida
    assert "PROMOVER" in saida

    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().one().status
        == "recebido_validacao_pendente"
    )


def test_reconciliacao_promove_o_que_passa_nas_guardas(
    client, auth, aceito, db, capsys
):
    """O caso LAU-000012 e LAU-000013: íntegros, só parados no estado antigo."""

    modulo = _carregar_script("reconciliar_fila_assinada.py")
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = "recebido_validacao_pendente"
    db.commit()

    assert modulo.reconciliar(db, apply=True, laudos=[]) == 0
    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().one().status
        == "recebido_assinado"
    )

    # Idempotente: a segunda passada não tem o que promover.
    capsys.readouterr()
    assert modulo.reconciliar(db, apply=True, laudos=[]) == 0
    assert "INTOCADO" in capsys.readouterr().out


def test_reconciliacao_nao_toca_no_que_ja_foi_entregue(
    client, auth, aceito, db, capsys
):
    """Reclassificar o que já saiu para o paciente não desfaz a entrega."""

    modulo = _carregar_script("reconciliar_fila_assinada.py")
    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{aceito['signed_document_id']}"
        "/entrega",
        json={"canal": "whatsapp"},
        headers=auth("operacional"),
    )
    assert entrega.status_code == 200, entrega.text

    capsys.readouterr()
    assert modulo.reconciliar(db, apply=True, laudos=[]) == 0
    assert "já entregue" in capsys.readouterr().out

    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().one().status
        == "entregue"
    )
