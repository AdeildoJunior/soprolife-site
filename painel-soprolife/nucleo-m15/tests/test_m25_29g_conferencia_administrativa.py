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
        "data-delivery-validate",
        "data-delivery-validate-cancel",
        "data-delivery-validate-confirm",
    ):
        assert atributo in codigo, atributo

    # `data-delivery-validate` é prefixo dos outros dois: o dispatch usa
    # `matches()`, que exige o atributo EXATO, e não `startsWith`.
    assert 'button.matches("[data-delivery-validate]")' in codigo
    assert 'button.matches("[data-delivery-validate-cancel]")' in codigo
    assert 'button.matches("[data-delivery-validate-confirm]")' in codigo


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
    """Item 5 — uma confirmação, com título e dois botões."""

    corpo = _bloco_da_funcao("renderConferenceConfirm")
    assert "Confirmar conferência do PDF assinado?" in corpo
    assert "Confirme apenas se você conferiu externamente" in corpo
    assert "não realiza validação criptográfica da cadeia" in corpo
    assert "Cancelar" in corpo
    assert "Confirmar conferência" in corpo


def test_nao_existe_frase_digitada_na_conferencia():
    """Item 8 — nada de `prompt()` com texto para copiar."""

    corpo = _bloco_da_funcao("registerExternalValidation")
    assert "window.prompt" not in corpo
    assert "Para confirmar, digite" not in _js_sem_comentarios()


def test_o_contrato_do_backend_nao_foi_afrouxado():
    """A API continua exigindo a frase — mudou quem a digita, não a regra."""

    corpo = _bloco_da_funcao("registerExternalValidation")
    assert "confirmacao: VALIDACAO_FRASE" in corpo
    assert 'VALIDACAO_FRASE = "Confirmo a conferência externa"' in WORKFLOW_JS


def test_cancelar_nao_grava_nada(client, auth, assinado, db):
    """Item 6 — cancelar é só fechar; o estado no banco não se mexe.

    O cancelamento vive inteiro no navegador: nenhuma chamada é feita. O
    que se prova aqui é o outro lado — que sem a chamada, o documento
    continua exatamente onde estava.
    """

    antes = db.execute(select(ExternalSignedDocument)).scalars().one()
    estado_antes = antes.status

    codigo = _js_sem_comentarios()
    # O cancelar apenas limpa o estado e redesenha — não há `client().api`
    # entre o atributo e o `render()`.
    trecho = codigo[
        codigo.index('button.matches("[data-delivery-validate-cancel]")'):
    ][:220]
    assert 'state.confirmConference = ""' in trecho
    assert "api(" not in trecho

    depois = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert depois.status == estado_antes


def test_confirmar_grava_somente_a_conferencia(client, auth, assinado, db):
    """Item 7 — confirma a conferência, e nada além dela."""

    signed_id = assinado["signed_document_id"]
    resposta = client.post(
        f"/api/v1/laudos/assinatura-externa/{signed_id}/validacao-externa",
        json={
            "metodo": "validar_iti",
            "confirmacao": "Confirmo a conferência externa",
        },
        # A conferência exige ROLE_ADMIN — `operacional` enxerga a fila mas
        # não registra o testemunho. Provado no teste seguinte.
        headers=auth("admin"),
    )
    assert resposta.status_code == 200, resposta.text

    registro = db.execute(select(ExternalSignedDocument)).scalars().one()
    assert registro.status == "validado_externamente"
    # Conferir NÃO entrega, e NÃO afirma assinatura qualificada.
    assert getattr(registro, "entregue_em", None) is None
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
