"""M26.17 — a médica é livre para escrever onde quiser, e "Meus laudos" não
pode mostrar um laudo já superado como se ainda precisasse de ação.

Caso real que motivou a missão: uma médica escreveu a conclusão inteira na
caixa grande ("Texto final do laudo") com a conclusão "Personalizado"
selecionada, e deixou a caixa pequena ("Conclusão personalizada") vazia — o
servidor recusou com "texto_personalizado_ausente", mesmo com o laudo cheio
de texto dela. Separadamente, LAU-000035/036 de Claudia (superados pela
corretiva LAU-000038) continuavam em "Meus laudos" como "Concluído —
aguardando assinatura qualificada".

Reaproveita os helpers já provados em `test_m25_2_native_report.py` e
`test_m26_13_laudos_efetivos_producao.py` via import dinâmico — dirige o
fluxo pela API real.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest


def _load(nome: str):
    caminho = pathlib.Path(__file__).with_name(nome)
    spec = importlib.util.spec_from_file_location(f"_{nome[:-3]}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_M25_2 = _load("test_m25_2_native_report.py")
_preview = _M25_2._preview

_M26_13 = _load("test_m26_13_laudos_efetivos_producao.py")
_liberar = _M26_13._liberar
_abrir_corretiva = _M26_13._abrir_corretiva


@pytest.fixture(autouse=True)
def reports_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("M15_REPORTS_ENABLED", "true")
    monkeypatch.setenv("M15_REPORTS_MODE", "pilot")
    monkeypatch.setenv("M15_REPORTS_STORAGE_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv(
        "M15_AUTH_SECRET", "m26-17-secret-de-teste-0123456789abcdef0123"
    )
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def case(client, auth, db, person):
    return _M25_2._make_case(client, auth, db, person, com_bd=True)


# ------------------------------------------- liberdade no texto do laudo


def test_texto_escrito_so_na_caixa_grande_nao_e_bloqueado(client, case):
    """A médica escreveu tudo em "Texto final do laudo" e deixou a caixa
    pequena de conclusão personalizada vazia — isso não pode mais bloquear."""

    texto_da_medica = (
        "Redução de CVF e VEF1 isolados.\n"
        "Sem resposta significativa ao broncodilatador.\n"
        "Sugerido complementar com volumes pulmonares."
    )
    resposta = _preview(
        client,
        case,
        conclusion_code="PERSONALIZADO",
        final_text=texto_da_medica,
    )
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["final_text"] == texto_da_medica


def test_ambas_as_caixas_vazias_continua_recusado(client, case):
    """Sem texto em lugar nenhum, continua sem ter o que assinar — isso não
    é o "bloqueio aleatório" reportado, é o mínimo clínico razoável."""

    resposta = _preview(client, case, conclusion_code="PERSONALIZADO")
    assert resposta.status_code == 422
    assert resposta.json()["erro"]["codigo"] == "texto_personalizado_ausente"


def test_caixa_pequena_preenchida_continua_funcionando_sem_texto_final(client, case):
    """Fluxo antigo intacto: preencher só a caixa pequena, sem tocar no
    texto final (o servidor compõe), continua funcionando como antes."""

    resposta = _preview(
        client,
        case,
        conclusion_code="PERSONALIZADO",
        conclusion_custom_text="TESTE APAGAR: conclusão sintética personalizada.",
        bronchodilator_code="RBD_NEGATIVO",
    )
    assert resposta.status_code == 200, resposta.text
    assert "conclusão sintética personalizada" in resposta.json()["final_text"]


def test_caixa_pequena_com_texto_proprio_muito_longo_continua_recusada(client, case):
    """O limite da caixa pequena, quando a médica REALMENTE a usa, continua
    valendo — a liberdade é sobre onde escrever, não um limite sem limite.

    O schema (`ReportNativeDraft.conclusion_custom_text`, max_length=2000)
    já barra isso na borda, antes mesmo de `resolve_conclusion_text` rodar
    — por isso o código de erro aqui é o genérico de validação de schema,
    não `texto_personalizado_longo` (que seguiria valendo se algum dia o
    limite do schema ficasse maior que `MAX_CUSTOM_CONCLUSION_CHARS`)."""

    resposta = _preview(
        client,
        case,
        conclusion_code="PERSONALIZADO",
        conclusion_custom_text="x" * 2001,
    )
    assert resposta.status_code == 422


# ------------------------------------------- "Meus laudos" sem superados


def _minha_fila(client, doctor_auth, **params):
    resposta = client.get(
        "/api/v1/laudos/meus", params=params, headers=doctor_auth
    )
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def test_laudo_superado_some_de_meus_laudos_por_padrao(client, case):
    document_id = case["document"]["id"]
    _liberar(client, case)

    antes = _minha_fila(client, case["doctor_auth"])
    assert any(item["document_id"] == document_id for item in antes), (
        "antes de corrigir, o laudo liberado precisa estar na fila ativa da médica"
    )

    _abrir_corretiva(client, case)

    ativa = _minha_fila(client, case["doctor_auth"])
    assert not any(item["document_id"] == document_id for item in ativa), (
        "um laudo já superado por corretiva não pode continuar em 'Meus "
        "laudos' como se ainda precisasse de assinatura"
    )

    historico = _minha_fila(client, case["doctor_auth"], somente_superados="true")
    alvo = next(item for item in historico if item["document_id"] == document_id)
    assert alvo["has_corrective_successor"] is True
    assert alvo["is_delivered"] is False

    completa = _minha_fila(client, case["doctor_auth"], incluir_superados="true")
    assert any(item["document_id"] == document_id for item in completa)
