"""M67 — prontidão para NFS-e no cadastro de "Novo atendimento".

O que estes testes provam, do lado do servidor:

1. as regras que a tela espelha (js/nfse-prontidao.js) dão, no backend, o
   mesmo resultado para os mesmos casos (arquivo de casos compartilhado);
2. o paciente já cadastrado chega à tela com `nfse_identidade_pendencias`
   calculado pelo próprio production_fact_blockers — só códigos, nunca o CPF;
3. um atendimento que a tela chama de "Dados fiscais completos" é, para
   nfse.evaluate(production), livre de qualquer bloqueio de CADASTRO (sobra
   só a política fiscal, que não é dado do cadastro);
4. cada lacuna que a tela aponta é também um bloqueio real do evaluate();
5. salvar incompleto continua permitido, e criar atendimento não cria
   nenhum documento, tentativa ou pedido fiscal.

Nenhum teste toca produção nem rede: banco SQLite efêmero das fixtures.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (FiscalAttempt, FiscalDocument, FiscalIssuanceRequest,
                        FiscalPreparation)
from app.services import nfse
from app.services.nfse_national.recipient_identity import (
    RecipientIdentityError, assert_production_cpf, assert_production_name)

API = "/api/v1"
CASOS = json.loads(
    (Path(__file__).parent / "data_m67_identidade_fiscal.json").read_text("utf-8"))
CPF_SINTETICO = "12345678909"
NOME_FICTICIO = "Marina Costa Ribeiro"
RIO = "3304557"
NITEROI = "3303302"
# O que NÃO é dado de cadastro: sem política fiscal vigente o evaluate()
# sempre bloqueia, e a tela não tem como (nem deve) resolver isso.
BLOQUEIOS_FORA_DO_CADASTRO = {"policy_missing"}


def _codigo(fn, valor):
    try:
        fn(valor)
    except RecipientIdentityError as exc:
        return exc.code
    return None


# ═════════════════════════════════════════ 1. paridade com o arquivo de casos

@pytest.mark.parametrize("nome, esperado", CASOS["nomes"])
def test_nome_fiscal_mesmo_resultado_que_a_tela(nome, esperado):
    assert _codigo(assert_production_name, nome) == esperado


@pytest.mark.parametrize("cpf, esperado", CASOS["cpfs"])
def test_cpf_fiscal_mesmo_resultado_que_a_tela(cpf, esperado):
    # A tela trata vazio como "ausente", exatamente como production_fact_blockers.
    if not cpf:
        assert esperado == "recipient_cpf_missing"
        return
    assert _codigo(assert_production_cpf, cpf) == esperado


# ═══════════════════════════════════ 2. paciente existente: julgado no servidor

def _criar_pessoa(client, auth, **campos):
    corpo = {"nome_completo": NOME_FICTICIO, "contatos": []}
    corpo.update(campos)
    resp = client.post(f"{API}/pessoas", json=corpo, headers=auth("operacional"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_pessoa_com_identidade_fiscal_completa_nao_tem_pendencia(client, auth):
    pessoa = _criar_pessoa(client, auth, cpf=CPF_SINTETICO)
    lida = client.get(f"{API}/pessoas/{pessoa['id']}", headers=auth("operacional")).json()
    assert lida["nfse_identidade_pendencias"] == []


def test_pessoa_sem_cpf_aponta_cpf_ausente(client, auth):
    pessoa = _criar_pessoa(client, auth)
    lida = client.get(f"{API}/pessoas/{pessoa['id']}", headers=auth("operacional")).json()
    assert lida["nfse_identidade_pendencias"] == ["recipient_cpf_missing"]


def test_pessoa_com_nome_placeholder_aponta_nome(client, auth):
    pessoa = _criar_pessoa(client, auth, nome_completo="Paciente Teste", cpf=CPF_SINTETICO)
    lida = client.get(f"{API}/pessoas/{pessoa['id']}", headers=auth("operacional")).json()
    assert lida["nfse_identidade_pendencias"] == ["recipient_name_looks_like_placeholder"]


def test_pendencias_fiscais_nunca_carregam_o_cpf(client, auth):
    pessoa = _criar_pessoa(client, auth, cpf=CPF_SINTETICO)
    busca = client.post(f"{API}/pessoas/busca", json={"q": "Marina", "tamanho": 5},
                        headers=auth("operacional")).json()
    item = next(i for i in busca["itens"] if i["id"] == pessoa["id"])
    # A busca é o que alimenta o cartão do paciente na Central.
    assert "nfse_identidade_pendencias" in item
    assert CPF_SINTETICO not in json.dumps(item["nfse_identidade_pendencias"])
    assert CPF_SINTETICO not in json.dumps(item)


def test_pendencias_so_carregam_codigos_de_identidade(client, auth, db):
    """Município é do exame, não da pessoa: nunca aparece aqui."""
    pessoa = _criar_pessoa(client, auth)
    lida = client.get(f"{API}/pessoas/{pessoa['id']}", headers=auth("operacional")).json()
    assert set(lida["nfse_identidade_pendencias"]) <= set(nfse.RECIPIENT_FISCAL_REASONS)


# ════════════════════ 3/4. "completo" na tela ⇔ sem bloqueio de cadastro no evaluate

def _atendimento(client, auth, *, tipo="espirometria_soprolife", pessoa=None,
                 espirometria=None, financeiro="padrao", consulta=None):
    esp = {"data_exame": "2026-09-01", "status": "Realizado", "broncodilatador": False,
           "municipio_atendimento_ibge": RIO, "modalidade": "residencial",
           "local_atendimento": "Domicílio do paciente"}
    esp.update(espirometria or {})
    esp = {k: v for k, v in esp.items() if v is not None}
    corpo = {
        "pessoa": pessoa or {"nome_completo": NOME_FICTICIO, "contatos": [],
                             "cpf": CPF_SINTETICO},
        "tipo": tipo,
        "espirometria": esp,
    }
    if financeiro == "padrao":
        financeiro = {"espirometria": {"valor": "220.00", "status": "Recebido",
                                       "data_recebimento": "2026-09-01"}}
    if financeiro is not None:
        corpo["financeiro"] = financeiro
    if consulta is not None:
        corpo["consulta"] = consulta
    resp = client.post(f"{API}/atendimentos/novo-paciente", json=corpo,
                       headers=auth("operacional"))
    return resp


def _bloqueios(db, exam_id):
    db.expire_all()
    avaliado = nfse.evaluate(db, exam_id, "production", for_update=False)
    return set(avaliado["blocking_reasons"]), avaliado


@pytest.mark.parametrize("municipio", [RIO, NITEROI])
@pytest.mark.parametrize("modalidade, fluxo, local", [
    ("residencial", "HOME", "Domicílio do paciente"),
    ("cowork", "DIRECT", "Coworking"),
])
def test_completo_na_tela_nao_tem_bloqueio_de_cadastro(client, auth, db, municipio,
                                                       modalidade, fluxo, local):
    resp = _atendimento(client, auth, espirometria={
        "municipio_atendimento_ibge": municipio, "modalidade": modalidade,
        "local_atendimento": local})
    assert resp.status_code == 201, resp.text
    bloqueios, avaliado = _bloqueios(db, resp.json()["espirometria"]["id"])
    assert bloqueios <= BLOQUEIOS_FORA_DO_CADASTRO, bloqueios
    assert avaliado["flow"] == fluxo


def test_espirometria_com_consulta_tambem_fica_completa(client, auth, db):
    resp = _atendimento(
        client, auth, tipo="espirometria_consulta_soprolife",
        consulta={"data_consulta": "2026-09-01", "status": "Realizada"},
        financeiro={
            "espirometria": {"valor": "220.00", "status": "Recebido",
                             "data_recebimento": "2026-09-01"},
            "consulta": {"valor_bruto": "300.00", "status": "Recebido",
                         "data_recebimento": "2026-09-01",
                         "repasse_medico_percentual": "100"},
        })
    assert resp.status_code == 201, resp.text
    bloqueios, _ = _bloqueios(db, resp.json()["espirometria"]["id"])
    # A receita da consulta e o repasse médico não contaminam a da espirometria.
    assert bloqueios <= BLOQUEIOS_FORA_DO_CADASTRO, bloqueios


def test_sem_data_de_recebimento_e_forma_nao_bloqueia_o_fiscal(client, auth, db):
    """A data de recebimento e a forma de pagamento não são requisito fiscal:
    evaluate() não as lê. Por isso a tela não as marca com o selo NFS-e."""
    resp = _atendimento(client, auth, financeiro={
        "espirometria": {"valor": "220.00", "status": "Recebido"}})
    assert resp.status_code == 201, resp.text
    bloqueios, _ = _bloqueios(db, resp.json()["espirometria"]["id"])
    assert bloqueios <= BLOQUEIOS_FORA_DO_CADASTRO, bloqueios


@pytest.mark.parametrize("ajuste, bloqueio", [
    ({"pessoa": {"nome_completo": NOME_FICTICIO, "contatos": []}}, "recipient_cpf_missing"),
    ({"pessoa": {"nome_completo": "Marina", "contatos": [], "cpf": CPF_SINTETICO}},
     "recipient_name_not_a_full_name"),
    ({"espirometria": {"municipio_atendimento_ibge": None}}, "service_location_missing"),
    ({"espirometria": {"modalidade": None, "local_atendimento": None}},
     "commercial_flow_unsupported"),
    ({"espirometria": {"data_exame": "09/2026"}}, "service_date_missing_or_imprecise"),
    ({"espirometria": {"data_exame": "2099-01-01"}}, "service_date_in_future"),
    ({"espirometria": {"status": "Aguardando"}}, "service_not_performed"),
    ({"financeiro": None}, "financial_entry_missing"),
    ({"financeiro": {"espirometria": {"valor": "220.00", "status": "Pendente"}}},
     "financial_revenue_not_received_or_invalid"),
])
def test_cada_lacuna_que_a_tela_aponta_e_bloqueio_real(client, auth, db, ajuste, bloqueio):
    resp = _atendimento(client, auth, **ajuste)
    # 15 — incompleto para a NFS-e continua SALVANDO.
    assert resp.status_code == 201, resp.text
    bloqueios, _ = _bloqueios(db, resp.json()["espirometria"]["id"])
    assert bloqueio in bloqueios


def test_broncodilatador_indefinido_salva_mas_nao_tem_descricao_fiscal(client, auth, db):
    """service_description.py exige booleano real. O evaluate() não lista
    isto como motivo — quem recusa é a confirmação/dispatch — e a tela
    antecipa o problema."""
    from app.models import SpirometryExam
    from app.services.nfse_national.service_description import (
        ServiceDescriptionUndetermined, spirometry_service_description)
    resp = _atendimento(client, auth, espirometria={"broncodilatador": None})
    assert resp.status_code == 201, resp.text
    exam = db.get(SpirometryExam, resp.json()["espirometria"]["id"])
    assert exam.broncodilatador is None
    with pytest.raises(ServiceDescriptionUndetermined):
        spirometry_service_description(exam.broncodilatador)


def test_pastore_continua_bloqueado_por_modelo_de_parceria(db):
    """A tela mostra só a explicação da parceria; o backend continua dizendo
    PASTORE / commercial_flow_unsupported. Nada aqui mudou."""
    import inspect
    fonte = inspect.getsource(nfse.evaluate)
    assert "'PASTORE' if partner_flow" in fonte
    assert "commercial_flow_unsupported" in fonte


# ═══════════════════════════════════ 5. salvar não cria nada fiscal

def test_criar_atendimento_nao_cria_nada_fiscal(client, auth, db):
    completo = _atendimento(client, auth)
    incompleto = _atendimento(client, auth, pessoa={"nome_completo": "Paciente Novo",
                                                    "contatos": []},
                              espirometria={"municipio_atendimento_ibge": None},
                              financeiro=None)
    assert completo.status_code == 201 and incompleto.status_code == 201
    for modelo in (FiscalDocument, FiscalPreparation, FiscalAttempt, FiscalIssuanceRequest):
        assert db.scalar(select(func.count()).select_from(modelo)) == 0, modelo.__name__
