"""M25.29G — conferência administrativa simples, e prévia assinada histórica.

Dois achados da operação, no mesmo lugar da tela.

**A janela errada.** Ao testar a fila administrativa apareceu um
`window.prompt()` pedindo para *digitar* a frase "Confirmo a conferência
externa". A exigência nasceu na M25.20 com uma intenção certa — um clique
distraído não pode registrar o testemunho de uma pessoa identificada — mas
na prática virou copiar e colar, e a dúvida sobre QUAL botão a disparava
custou tempo numa operação parada.

A intenção continua: a confirmação é deliberada, em dois passos, e o texto
diz exatamente o que está sendo afirmado. O que sai é a digitação.

**O contrato do backend não foi afrouxado.** A API continua exigindo a
frase; o que mudou é quem a digita. Nenhuma rota, nenhum estado e nenhuma
regra de autorização mudaram nesta etapa.

O que estes testes travam:

1. cada botão da fila dispara UMA ação, e só a sua;
2. os dois downloads não abrem confirmação nenhuma;
3. a conferência abre exatamente uma confirmação, com dois botões;
4. cancelar não grava nada;
5. nada de frase digitada, nada de `prompt()` nessa ação;
6. `qualified_signature` continua falso e nada vira "entregue" sozinho.

Somente dados sintéticos.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import re

import pytest
from sqlalchemy import select

from app.models import ExternalSignedDocument

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()


def _modulo_da_m25_29e():
    caminho = pathlib.Path(__file__).with_name(
        "test_m25_29e_pos_assinatura_downloads.py"
    )
    spec = importlib.util.spec_from_file_location("_m25_29e", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M29E = _modulo_da_m25_29e()
reports_enabled = _M29E.reports_enabled  # fixture autouse
caso = _M29E.caso
assinado = _M29E.assinado
_concluir = _M29E._concluir
_assinar_por_fora = _M29E._assinar_por_fora


# M25.29H — o aceite automático impede que um documento inválido vire linha
# no banco. Isso é o conserto, e é ótimo; mas o script de recusa da M25.29G
# existe justamente para as linhas que JÁ existem, criadas antes dele. Para
# testá-lo é preciso reproduzir aquele mundo — e reproduzir é exatamente o
# que este contexto faz, sem afrouxar nada no código de produção.
@contextlib.contextmanager
def _sem_guardas_documentais():
    from app.routers import reports
    from app.services.signature_acceptance import GuardasDocumentais

    permissivo = GuardasDocumentais(
        tem_versao_final=True,
        origem_e_a_versao_final=True,
        parece_previa=False,
        identico_ao_final=False,
        tem_estrutura_assinatura=True,
        contem_o_final=True,
        metadado_coerente=True,
        codigo_validacao_coerente=True,
    )
    original = reports.avaliar_guardas_documentais
    reports.avaliar_guardas_documentais = lambda *a, **k: permissivo
    try:
        yield
    finally:
        reports.avaliar_guardas_documentais = original


def _voltar_ao_estado_antigo(db, *, status):
    """Recoloca o assinado no estado em que a M25.29G o encontrava."""

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = status
    db.commit()
    return registro


def _js_sem_comentarios() -> str:
    sem_bloco = re.sub(r"/\*.*?\*/", "", WORKFLOW_JS, flags=re.S)
    return re.sub(r"^\s*//.*$", "", sem_bloco, flags=re.M)


def _bloco_da_funcao(nome: str) -> str:
    """O corpo de uma função do painel, delimitado por contagem de chaves."""

    codigo = _js_sem_comentarios()
    inicio = codigo.index(f"function {nome}(")
    abre = codigo.index("{", inicio)
    nivel = 0
    for pos in range(abre, len(codigo)):
        if codigo[pos] == "{":
            nivel += 1
        elif codigo[pos] == "}":
            nivel -= 1
            if nivel == 0:
                return codigo[abre:pos]
    raise AssertionError(f"função {nome} não fecha")


# =====================================================================
# 1-4. Cada botão dispara a sua ação, e só a sua
# =====================================================================


def test_os_tres_botoes_tem_atributos_distintos():
    """Sem atributo compartilhado não há como um clique cair no outro."""

    codigo = _js_sem_comentarios()
    for atributo in (
        "data-delivery-download-mir",
        "data-delivery-download-assinado",
    ):
        assert atributo in codigo, atributo

    # M25.29H — os três atributos da conferência saíram junto com a etapa.
    # O risco que este teste nasceu para pegar (um `matches()` colidindo com
    # o prefixo do outro) deixa de existir quando o prefixo não existe.
    for saiu in (
        "data-delivery-validate",
        "data-delivery-validate-cancel",
        "data-delivery-validate-confirm",
    ):
        assert saiu not in codigo, saiu


def test_download_do_exame_tecnico_nao_abre_confirmacao():
    """Item 1 e 2 — baixar é baixar."""

    corpo = _bloco_da_funcao("baixarDocumentoDaEntrega")
    assert "apiBlob" in corpo
    assert "saveBlob" in corpo
    assert "prompt" not in corpo
    assert "confirmConference" not in corpo


def test_download_do_assinado_usa_a_mesma_funcao_de_baixar():
    """Item 3 e 4 — os dois downloads são o mesmo caminho, sem popup."""

    codigo = _js_sem_comentarios()
    assert 'baixarDocumentoDaEntrega(\n        button.getAttribute("data-delivery-download-assinado"),\n        "assinado"\n      );' in codigo or (
        "data-delivery-download-assinado" in codigo
        and '"assinado"' in codigo
    )
    corpo = _bloco_da_funcao("baixarDocumentoDaEntrega")
    assert "window.prompt" not in corpo


# =====================================================================
# 5-8. A confirmação da conferência
# =====================================================================


def test_conferencia_abre_uma_confirmacao_na_tela():
    """M25.29H — não abre mais nada: a confirmação inteira saiu da tela.

    A M25.29G trocou uma frase digitada por dois botões. A M25.29H removeu
    os dois: o que eles confirmavam era um testemunho humano sobre um
    arquivo, e a auditoria mostrou que esse testemunho podia estar errado —
    um documento foi marcado como conferido sendo idêntico ao PDF final e
    sem estrutura de assinatura nenhuma.
    """

    codigo = _js_sem_comentarios()
    assert "renderConferenceConfirm" not in codigo
    assert "Confirmar conferência do PDF assinado?" not in codigo
    assert "Registrar conferência do PDF assinado" not in codigo


def test_nao_existe_frase_digitada_na_conferencia():
    """Item 8 — nada de `prompt()` e nada de frase para copiar.

    A M25.29G tirou a digitação; a M25.29H tirou também a função que a
    consumia. O que continua proibido é o mesmo: a fila administrativa não
    pede texto a ninguém.
    """

    codigo = _js_sem_comentarios()
    assert "registerExternalValidation" not in codigo
    assert "VALIDACAO_FRASE" not in codigo
    assert "Para confirmar, digite" not in codigo
    corpo = _bloco_da_funcao("renderDeliveryRow")
    assert "prompt" not in corpo


def test_o_contrato_do_backend_nao_foi_afrouxado():
    """A API legada continua exigindo a frase — ninguém a afrouxou.

    A rota de conferência externa não é mais alcançável pela tela, mas
    continua existindo para os documentos históricos. Afrouxar o contrato
    dela ao remover o botão seria trocar uma etapa por uma porta aberta.
    """

    from pydantic import ValidationError

    from app.schemas import ExternalValidationRequest

    with pytest.raises(ValidationError):
        ExternalValidationRequest(metodo="validar_iti", confirmacao="ok")
    aceito = ExternalValidationRequest(
        metodo="validar_iti", confirmacao="Confirmo a conferência externa"
    )
    assert aceito.metodo == "validar_iti"


def test_cancelar_nao_grava_nada(client, auth, assinado, db):
    """M25.29H — não há o que cancelar, e a fila continua sem gravar nada.

    O teste original provava que fechar a confirmação não mexia no banco.
    A garantia que sobrevive é mais forte: a tela administrativa inteira
    não tem nenhuma ação que altere o estado do documento assinado, além
    da entrega, que é explícita.
    """

    antes = db.execute(select(ExternalSignedDocument)).scalars().one()
    estado_antes = antes.status

    corpo = _bloco_da_funcao("renderDeliveryRow")
    assert "data-delivery-validate" not in corpo
    # Só três ações na linha: dois downloads e a entrega.
    assert corpo.count("data-delivery-download-mir") == 1
    assert corpo.count("data-delivery-download-assinado") == 1
    assert corpo.count("data-delivery-deliver") == 1

    depois = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert depois.status == estado_antes


def test_confirmar_grava_somente_a_conferencia(client, auth, assinado, db):
    """M25.29H — não há mais o que confirmar, e a rota legada diz isso.

    O documento já chegou pronto para entrega pelo aceite automático. Pedir
    conferência sobre ele agora devolve 409: não é erro do administrador, é
    uma etapa que não existe mais. O que continua garantido é o essencial —
    receber não entrega, e nada afirma assinatura qualificada.
    """

    signed_id = assinado["signed_document_id"]
    resposta = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": "Confirmo a conferência externa",
        },
        headers=auth("admin"),
    )
    assert resposta.status_code == 409, resposta.text

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "recebido_assinado"
    assert registro.validated_at is None
    # Receber NÃO entrega, e NÃO afirma assinatura qualificada.
    assert registro.delivered_at is None
    assert getattr(registro, "qualified_signature", False) is False


# =====================================================================
# 9. Celular e desktop
# =====================================================================


def test_confirmacao_tem_alvo_de_toque_e_empilha():
    assert ".report-delivery-confirm-buttons .m15-btn" in WORKFLOW_CSS
    assert "min-height: 48px" in WORKFLOW_CSS

    inicio = WORKFLOW_CSS.index(".report-delivery-confirm-buttons")
    trecho = WORKFLOW_CSS[inicio:inicio + 1800]
    assert "column-reverse" in trecho


def test_conferencia_exige_admin_nao_basta_operacional(client, auth, assinado):
    """Quem vê a fila não é necessariamente quem pode atestar a conferência.

    Vale registrar como contrato: o botão aparece na fila que `operacional`
    enxerga, mas só `admin` consegue registrar. Um usuário operacional que
    clicar recebe uma recusa clara da API, não um sucesso silencioso.
    """

    signed_id = assinado["signed_document_id"]
    resposta = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": "Confirmo a conferência externa",
        },
        headers=auth("operacional"),
    )
    assert resposta.status_code == 403, resposta.text


# =====================================================================
# O estado `recusado` — documento inválido preservado, mas sem valer
# =====================================================================


def _script_de_recusa():
    caminho = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "rejeitar_documento_assinado_invalido.py"
    )
    spec = importlib.util.spec_from_file_location("_rejeitar", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _lau_do_caso(client, auth, document_id) -> str:
    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert fila.status_code == 200, fila.text
    for item in fila.json()["itens"]:
        if item["document_id"] == document_id:
            return item["report_code"]
    raise AssertionError("laudo não encontrado na fila")


def test_status_recusado_existe_e_e_generico():
    """Um estado só, reutilizável — o motivo mora na auditoria."""

    from app.models import ASSINADO_RECUSADO, ASSINADO_STATUS_VALUES

    assert ASSINADO_RECUSADO == "recusado"
    assert ASSINADO_RECUSADO in ASSINADO_STATUS_VALUES
    # Nada de um status por modo de falha.
    assert not any(
        "previa" in valor for valor in ASSINADO_STATUS_VALUES
    ), ASSINADO_STATUS_VALUES


def test_recusado_nao_conta_como_assinado_atual(client, auth, assinado, db):
    """A função que escolhe o assinado vigente precisa ignorá-lo.

    É ela que decide, num lugar só, o estado da fila, o que o download
    administrativo entrega e se um novo arquivo pode entrar.
    """

    from app.models import ASSINADO_RECUSADO
    from app.routers.reports import _assinado_mais_recente

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert _assinado_mais_recente(db, registro.report_document_id) is not None

    registro.status = ASSINADO_RECUSADO
    db.commit()

    assert _assinado_mais_recente(db, registro.report_document_id) is None


def test_recusado_nao_pode_ser_conferido_nem_entregue(
    client, auth, assinado, db
):
    from app.models import ASSINADO_RECUSADO

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()
    signed_id = assinado["signed_document_id"]

    conferencia = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": "Confirmo a conferência externa",
        },
        headers=auth("admin"),
    )
    assert conferencia.status_code == 409, conferencia.text
    corpo = conferencia.json()["erro"]
    assert corpo["codigo"] == "documento_assinado_recusado"
    # A mensagem não pode mentir dizendo que já foi conferido.
    assert "já foi registrada" not in corpo["mensagem"]
    assert "assinado novamente" in corpo["mensagem"]

    entrega = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/entrega",
        json={"canal": "whatsapp"},
        headers=auth("operacional"),
    )
    assert entrega.status_code == 409, entrega.text
    assert entrega.json()["erro"]["codigo"] == "documento_assinado_recusado"


def test_recusado_nao_e_baixavel_como_assinado(client, auth, assinado, db):
    """O botão da administração para de entregar o arquivo inválido."""

    from app.models import ASSINADO_RECUSADO

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()

    resposta = client.get(
        f"/api/v1/laudos/{assinado['document_id']}/assinado/conteudo",
        headers=auth("operacional"),
    )
    assert resposta.status_code == 404
    assert resposta.headers["content-type"].startswith("application/json")
    assert resposta.content[:4] != b"%PDF"


def test_recusado_devolve_o_laudo_para_aguardando_assinatura(
    client, auth, assinado, db
):
    from app.models import ASSINADO_RECUSADO

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()

    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    alvo = [
        item for item in fila.json()["itens"]
        if item["document_id"] == assinado["document_id"]
    ]
    assert alvo, "o laudo tem de continuar visível na fila"
    assert alvo[0]["estado"] == "aguardando_assinatura"


def test_o_blob_e_o_historico_sobrevivem_a_recusa(client, auth, assinado, db):
    """Recusar não apaga: a evidência do incidente continua inteira."""

    from app.models import ASSINADO_RECUSADO, ReportDocumentVersion

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    sha_antes = registro.sha256
    versao_antes = registro.report_document_version_id

    registro.status = ASSINADO_RECUSADO
    db.commit()

    depois = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert depois.sha256 == sha_antes
    assert depois.report_document_version_id == versao_antes
    assert db.get(ReportDocumentVersion, versao_antes) is not None


def test_recusado_libera_nova_assinatura_da_versao_final(
    client, auth, assinado, db
):
    """A médica precisa conseguir baixar e devolver de novo."""

    from app.models import ASSINADO_RECUSADO

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()

    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [assinado["document_id"]]},
        headers=assinado["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text
    assert baixado.content[:4] == b"%PDF"

    # Assina de novo — bytes diferentes da primeira assinatura, como um
    # assinador real produz.
    reassinado = _assinar_por_fora(baixado.content) + (
        b"\n% segunda revisao (sintetica)\n"
    )
    enviado = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "TESTE APAGAR - reassinado.pdf",
                reassinado,
                "application/pdf",
            )
        },
        headers=assinado["doctor_auth"],
    )
    assert enviado.status_code == 200, enviado.text
    assert enviado.json()["resumo"]["identificados"] == 1, enviado.json()


def test_conclusao_clinica_permanece_apos_recusa(client, auth, assinado, db):
    from app.models import ASSINADO_RECUSADO, ReportDocument

    documento = db.get(ReportDocument, assinado["document_id"])
    versao_antes = documento.current_version_id
    codigo_antes = documento.validation_code
    status_antes = documento.status

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()

    documento = db.get(ReportDocument, assinado["document_id"])
    assert documento.current_version_id == versao_antes
    assert documento.validation_code == codigo_antes
    assert documento.status == status_antes


# =====================================================================
# O script de manutenção
# =====================================================================


def test_script_dry_run_nao_escreve(client, auth, assinado, db, capsys):
    modulo = _script_de_recusa()
    lau = _lau_do_caso(client, auth, assinado["document_id"])

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    estado_antes = registro.status

    codigo = modulo.rejeitar(
        db, lau=lau, motivo="documento_sem_assinatura_externa", aplicar=False
    )
    saida = capsys.readouterr().out

    # Este caso foi assinado de verdade: o hash difere do final, então a
    # evidência NÃO sustenta "sem assinatura externa" — e o script se recusa.
    assert codigo == 4, saida
    assert "NADA FOI ESCRITO" in saida

    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().one().status
        == estado_antes
    )


def test_script_exige_evidencia_para_o_motivo(client, auth, assinado, db, capsys):
    """Sem evidência objetiva, o script não age — nem com --apply."""

    modulo = _script_de_recusa()
    lau = _lau_do_caso(client, auth, assinado["document_id"])

    codigo = modulo.rejeitar(
        db, lau=lau, motivo="previa_assinada_antes_da_conclusao", aplicar=True
    )
    saida = capsys.readouterr().out
    assert codigo == 4, saida
    assert "NADA FOI ESCRITO" in saida

    db.expire_all()
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "recebido_assinado"


def test_script_aplica_e_e_idempotente(client, auth, caso, db, capsys):
    """Um documento devolvido IDÊNTICO ao final — o caso LAU-000010/011."""

    from app.models import ASSINADO_RECUSADO

    _concluir(client, caso)
    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [caso["document_id"]]},
        headers=caso["doctor_auth"],
    )
    # Devolve o MESMO arquivo, sem assinar nada. Hoje o envio recusaria isso
    # na hora — a linha abaixo reproduz o fluxo anterior, que é o único que
    # conseguia produzir a linha que este script conserta.
    with _sem_guardas_documentais():
        enviado = client.post(
            "/api/v1/laudos/assinatura-externa/enviar",
            files={
                "arquivos": (
                    "TESTE APAGAR - devolvido.pdf",
                    baixado.content,
                    "application/pdf",
                )
            },
            headers=caso["doctor_auth"],
        )
    revisao = enviado.json()
    assert revisao["resumo"]["identificados"] == 1, revisao
    _voltar_ao_estado_antigo(db, status="recebido_validacao_pendente")

    modulo = _script_de_recusa()
    lau = _lau_do_caso(client, auth, caso["document_id"])

    codigo = modulo.rejeitar(
        db, lau=lau, motivo="documento_sem_assinatura_externa", aplicar=True
    )
    saida = capsys.readouterr().out
    assert codigo == 0, saida
    assert "APLICADO" in saida
    assert "hash idêntico ao PDF final" in saida

    db.expire_all()
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == ASSINADO_RECUSADO

    # Segunda execução: nada duplica, nada corrompe.
    codigo = modulo.rejeitar(
        db, lau=lau, motivo="documento_sem_assinatura_externa", aplicar=True
    )
    saida = capsys.readouterr().out
    assert codigo == 0, saida
    assert "JÁ RECUSADO" in saida

    db.expire_all()
    assert (
        db.execute(select(ExternalSignedDocument)).scalars().one().status
        == ASSINADO_RECUSADO
    )


def test_script_para_diante_de_conferencia_ja_registrada(
    client, auth, assinado, db, capsys
):
    """Recusar depois da conferência apagaria um testemunho humano."""

    # Um documento conferido à mão só existe no fluxo anterior: hoje ele
    # chegaria pronto para entrega sem passar por conferência nenhuma.
    _voltar_ao_estado_antigo(db, status="recebido_validacao_pendente")
    signed_id = assinado["signed_document_id"]
    conferencia = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": "Confirmo a conferência externa",
        },
        headers=auth("admin"),
    )
    assert conferencia.status_code == 200, conferencia.text

    modulo = _script_de_recusa()
    lau = _lau_do_caso(client, auth, assinado["document_id"])
    codigo = modulo.rejeitar(
        db, lau=lau, motivo="documento_sem_assinatura_externa", aplicar=True
    )
    saida = capsys.readouterr().out
    assert codigo == 3, saida
    assert "PARE" in saida


def test_reenviar_o_mesmo_arquivo_recusado_nao_o_torna_valido(
    client, auth, assinado, db
):
    """A médica reenvia o arquivo recusado achando que resolve. Não resolve.

    A trava de idempotência por hash já impedia uma segunda versão, mas
    respondia "já havia sido recebido" — o que soa como sucesso. Para um
    arquivo recusado a resposta precisa dizer que ele não serve.
    """

    from app.models import ASSINADO_RECUSADO

    # Recusa primeiro: só então o laudo volta a ser baixável, e o arquivo
    # que sai é o mesmo PDF final de antes.
    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    registro.status = ASSINADO_RECUSADO
    db.commit()

    baixado = client.post(
        "/api/v1/laudos/assinatura-externa/baixar",
        json={"document_ids": [assinado["document_id"]]},
        headers=assinado["doctor_auth"],
    )
    assert baixado.status_code == 200, baixado.text
    # Reproduz exatamente os bytes que já estão no sistema.
    ja_no_sistema = _assinar_por_fora(baixado.content)

    enviado = client.post(
        "/api/v1/laudos/assinatura-externa/enviar",
        files={
            "arquivos": (
                "TESTE APAGAR - mesmo.pdf",
                ja_no_sistema,
                "application/pdf",
            )
        },
        headers=assinado["doctor_auth"],
    )
    assert enviado.status_code == 200, enviado.text
    corpo = enviado.json()
    assert corpo["resumo"]["identificados"] == 0
    (veredito,) = corpo["arquivos"]
    assert veredito["resultado"] == "recusado"
    assert "assinado novamente" in veredito["mensagem"]
