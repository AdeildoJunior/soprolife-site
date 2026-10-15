"""M26.31 — o convite para seguir o Instagram, ao lado do envio do resultado.

A pergunta desta milestone não é "o botão aparece?". É o que o botão NÃO
faz: não deriva token, não mexe no estado da entrega do resultado, não
inventa uma segunda fonte para o endereço do perfil, não manda mensagem a
quem pediu para não ser contatado e não coloca telefone de paciente numa
resposta de lote.

Tudo sintético: pacientes "TESTE APAGAR", telefones no prefixo local 0000
(não atribuído no Brasil, nunca discável) e PDFs gerados na hora.
"""

from __future__ import annotations

import importlib.util
import pathlib
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, Consent, Person
from app.services import patient_results as prs

PANEL_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_JS = (PANEL_ROOT / "js" / "report-workflow.js").read_text()
WORKFLOW_CSS = (PANEL_ROOT / "css" / "report-workflow.css").read_text()


def _carregar(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M26_4 = _carregar("test_m26_4_portal_resultados.py")
_montar_caso = _M26_4._montar_caso
_acesso = _M26_4._acesso


# ------------------------------------------------------------- ambiente


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    """Laudos em piloto + portal ligado — o mesmo arranjo da M26.4.

    O portal fica ligado porque é o cenário REAL da tela: o cartão que tem
    "Link, QR e WhatsApp" é o que ganha o botão novo ao lado. Que o convite
    não dependa do portal tem teste próprio, que o desliga.
    """

    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_REPORTS_VALIDATION_BASE_URL",
        "https://painel-teste.soprolife.local/validar",
    )
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-31-painel-administrativo-secret-de-teste-0123456789"
    )
    monkeypatch.setenv("M15_PORTAL_ENABLED", "true")
    monkeypatch.setenv(
        "M15_PORTAL_TOKEN_KEY",
        "m26-31-chave-que-deriva-o-link-do-paciente-9876543210abcdef",
    )
    monkeypatch.setenv(
        "M15_PORTAL_SESSION_SECRET",
        "m26-31-segredo-do-cookie-do-portal-publico-fedcba9876543210",
    )
    monkeypatch.setenv(
        "M15_PORTAL_PUBLIC_BASE_URL", "https://soprolife.com.br/resultados"
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def caso(client, auth, db):
    return _montar_caso(
        client, auth, db, nome="TESTE APAGAR Paciente Convite", suffix="81"
    )


def _convite(client, auth, document_id, papel="operacional"):
    return client.get(
        f"/api/v1/laudos/{document_id}/convite-instagram", headers=auth(papel)
    )


# ======================================================================
# A mensagem
# ======================================================================


def test_convite_vai_pelo_mesmo_canal_com_a_mensagem_pronta(client, auth, caso):
    resposta = _convite(client, auth, caso["document_id"])
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    zap = corpo["whatsapp"]
    assert zap["disponivel"] is True
    partes = urlsplit(zap["url"])
    assert partes.scheme == "https" and partes.netloc == "wa.me"
    assert partes.path.strip("/").isdigit()
    # O MESMO canal do envio do resultado: `wa.me` com a mensagem pronta,
    # para a pessoa apertar ENVIAR.
    assert parse_qs(partes.query)["text"][0] == corpo["mensagem"]

    texto = corpo["mensagem"]
    assert texto.startswith("Olá, TESTE.")  # primeiro nome do cadastro
    # M26.32 reescreveu a frase do convite ("acompanhe diariamente"),
    # mas o contrato desta milestone é o mesmo: convite ao Instagram,
    # com um endereço só, no corpo da mensagem.
    assert "Acompanhe a SoproLife diariamente no Instagram" in texto
    assert "https://www.instagram.com/soprolife" in texto
    assert texto.rstrip().endswith("SoproLife Diagnósticos e Soluções em Saúde")


def test_o_link_do_instagram_tem_uma_fonte_so(client, auth, caso):
    """A URL do perfil é a constante do núcleo — não há cópia no navegador."""

    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["instagram_url"] == prs.INSTAGRAM_URL
    assert prs.INSTAGRAM_URL in corpo["mensagem"]
    # Nenhum pedaço do painel conhece o endereço: ele desce com a mensagem.
    assert "instagram.com" not in WORKFLOW_JS
    assert "instagram.com" not in WORKFLOW_CSS


def test_convite_nao_leva_nada_do_laudo(client, auth, db, caso):
    """Mensagem de relacionamento: sem resultado, sem link, sem token."""

    corpo = _convite(client, auth, caso["document_id"]).json()
    texto = corpo["mensagem"]
    acesso = _acesso(db, caso["document_id"])
    token = prs.derive_token(acesso.id, acesso.generation)
    assert token not in resposta_texto(corpo)
    assert "/resultados" not in texto
    proibidos = [
        "DVO", "obstru", "distúrbio", "broncodilatador", "CVF", "VEF",
        "normal", "alterado", "LAU-", "ESP-", "PES-", "CPF", "sha256",
        "resultado", "laudo", "nascimento",
    ]
    for termo in proibidos:
        assert termo.lower() not in texto.lower(), termo


def resposta_texto(corpo: dict) -> str:
    return "\n".join(str(v) for v in corpo.values())


# ======================================================================
# O que o convite NÃO toca
# ======================================================================


def test_convite_nao_altera_o_estado_da_entrega(client, auth, db, caso):
    """`Enviado` continua querendo dizer que o RESULTADO foi enviado."""

    antes = _acesso(db, caso["document_id"])
    status_antes, enviado_antes = antes.status, antes.sent_at
    assert _convite(client, auth, caso["document_id"]).status_code == 200
    depois = _acesso(db, caso["document_id"])
    assert depois.status == status_antes
    assert depois.sent_at == enviado_antes
    assert depois.generation == antes.generation


def test_convite_funciona_com_o_portal_desligado(
    client, auth, db, caso, monkeypatch
):
    """O convite não é entrega de resultado e não depende do portal."""

    monkeypatch.setenv("M15_PORTAL_ENABLED", "false")
    get_settings.cache_clear()
    assert client.get(
        f"/api/v1/laudos/{caso['document_id']}/acesso-resultado",
        headers=auth("operacional"),
    ).status_code == 503
    resposta = _convite(client, auth, caso["document_id"])
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["whatsapp"]["disponivel"] is True


def test_telefone_do_paciente_continua_fora_da_fila(client, auth, caso):
    """O número sai por clique, num laudo. Nunca em resposta de lote."""

    corpo = _convite(client, auth, caso["document_id"]).json()
    numero = urlsplit(corpo["whatsapp"]["url"]).path.strip("/")
    fila = client.get(
        "/api/v1/laudos/assinatura-externa/fila", headers=auth("operacional")
    )
    assert fila.status_code == 200
    assert numero not in fila.text
    assert "instagram" not in fila.text.lower()


# ======================================================================
# Quando o convite NÃO pode ir
# ======================================================================


def test_sem_telefone_o_convite_nao_quebra(client, auth, db):
    caso = _montar_caso(
        client, auth, db, nome="TESTE APAGAR Convite Sem Telefone",
        suffix="82", telefone=None,
    )
    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"] == {
        "disponivel": False, "motivo": "Telefone não cadastrado"
    }
    # A mensagem continua pronta: dá para copiar da tela.
    assert "Acompanhe a SoproLife diariamente" in corpo["mensagem"]


def test_quem_pediu_para_nao_ser_contatado_nao_recebe(client, auth, db, caso):
    pessoa = db.get(Person, caso["pessoa"]["id"])
    pessoa.nao_contatar = True
    db.commit()
    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"] == {
        "disponivel": False, "motivo": "Paciente pediu para não ser contatado"
    }


def test_consentimento_revogado_barra_o_convite(client, auth, db, caso):
    db.add(
        Consent(
            person_id=caso["pessoa"]["id"],
            canal="whatsapp",
            status="revogado",
            origem="teste_m26_31",
        )
    )
    db.commit()
    corpo = _convite(client, auth, caso["document_id"]).json()
    assert corpo["whatsapp"] == {
        "disponivel": False, "motivo": "Consentimento de WhatsApp revogado"
    }


def test_apenas_papel_operacional_pede_o_convite(client, auth, caso):
    assert _convite(client, auth, caso["document_id"], "leitura").status_code == 403
    assert client.get(
        f"/api/v1/laudos/{caso['document_id']}/convite-instagram"
    ).status_code == 401


def test_laudo_inexistente_responde_404(client, auth):
    assert _convite(client, auth, "nao-existe").status_code == 404


# ======================================================================
# Trilha
# ======================================================================


def test_cada_convite_exibido_fica_na_trilha(client, auth, db, caso):
    assert _convite(client, auth, caso["document_id"]).status_code == 200
    db.commit()
    linhas = db.execute(
        select(AuditLog).where(AuditLog.acao == "convite_instagram_exibido")
    ).scalars().all()
    assert len(linhas) == 1
    detalhes = linhas[0].detalhes
    assert detalhes["report_code"] == caso["report_code"]
    assert detalhes["canal"] == "whatsapp_manual"
    assert detalhes["status"] == "disponivel"
    # Nenhum telefone, nenhum nome de paciente na trilha.
    assert "telefone" not in detalhes
    assert "TESTE APAGAR" not in str(detalhes)


# ======================================================================
# A tela
# ======================================================================


def test_botao_fica_ao_lado_de_link_qr_e_whatsapp():
    """O botão novo nasce no MESMO grupo de ações do cartão da fila."""

    trecho = WORKFLOW_JS.split("function renderResultAccessActions", 1)[1]
    trecho = trecho.split("function renderInstagramInviteAction", 1)[0]
    assert "Link, QR e WhatsApp" in trecho
    assert "renderInstagramInviteAction(item)" in trecho
    assert "data-instagram-open" in WORKFLOW_JS
    assert "data-instagram-send" in WORKFLOW_JS
    assert "data-instagram-close" in WORKFLOW_JS
    # O botão antigo continua intacto: mesmo rótulo, mesmo atributo, mesma
    # rota de acesso ao resultado.
    assert "data-result-open" in WORKFLOW_JS
    assert "data-result-whatsapp" in WORKFLOW_JS
    assert "/acesso-resultado" in WORKFLOW_JS


def test_a_tela_abre_o_whatsapp_sem_buscar_nada_no_meio():
    """`window.open` no gesto do clique — senão o navegador bloqueia."""

    trecho = WORKFLOW_JS.split('data-instagram-send]")', 1)[1].split("return;", 1)[0]
    assert "window.open" in trecho
    assert "await" not in trecho
    assert "client()" not in trecho


def test_o_convite_usa_o_acabamento_da_fila():
    assert ".report-instagram-message" in WORKFLOW_CSS
    assert "report-instagram-panel" in WORKFLOW_JS
    # Reaproveita o painel e a linha de ações que já existem no cartão —
    # nenhuma grade nova para desalinhar no celular.
    assert "report-result-panel report-instagram-panel" in WORKFLOW_JS
    assert "report-result-actions" in WORKFLOW_JS
