"""Testes do workspace canônico de CRM (M19).

Cobrem os critérios de aceite: dados reais do banco (nunca sintéticos de
demonstração), telefone mascarado, contato do responsável quando o paciente
é menor, WhatsApp que nunca conclui sozinho, resultado de contato que gera
exatamente uma interação auditável, bloqueio de follow-up duplicado,
agendamento de 6 meses de calendário exato, linha do tempo combinada,
indicadores com dado real, RBAC do bloco financeiro e resolução de aliases
históricos sem renumerar código canônico.
"""

from datetime import date, timedelta

import pytest

from app.ids import PREFIXES, code_dictionary
from app.models import Followup, LegacyAlias, Person
from app.services import crm as csvc
from app.services.followup import today_local

from .conftest import SYNTH_PHONE

# Telefone sintético DISCÁVEL para os testes de máscara/WhatsApp: o dígito
# local não é 0000, então passa pela trava de "não discável", mas continua
# sendo um número de teste — nenhum dado real de paciente entra aqui.
TEST_PHONE = "(21) 98888-7766"
TEST_PHONE_NORM = "5521988887766"


# ----------------------------------------------------------------- fixtures

def _criar_pessoa(client, auth, nome, telefone=TEST_PHONE, consentimento="concedido"):
    resp = client.post(
        "/api/v1/pessoas",
        json={
            "nome_completo": nome,
            "contatos": [{"tipo": "whatsapp", "valor": telefone, "principal": True}],
            "consentimento_whatsapp": consentimento,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _criar_pessoa_sem_contato(client, auth, nome):
    resp = client.post(
        "/api/v1/pessoas",
        json={"nome_completo": nome, "contatos": []},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _criar_exame(client, auth, person_id, data_exame, status="Realizado"):
    resp = client.post(
        "/api/v1/espirometrias",
        json={
            "person_id": person_id,
            "data_exame": data_exame.isoformat(),
            "modalidade": "residencial",
            "status": status,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _criar_consulta(client, auth, person_id, data_consulta, status="Realizada"):
    resp = client.post(
        "/api/v1/consultas",
        json={
            "person_id": person_id,
            "data_consulta": data_consulta.isoformat(),
            "modalidade": "teleconsulta",
            "status": status,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def paciente(client, auth):
    """Paciente com exame realizado — gera follow-up de 6 meses automático."""
    pessoa = _criar_pessoa(client, auth, "Paciente Teste M19")
    exame = _criar_exame(client, auth, pessoa["id"], date.today() - timedelta(days=200))
    return {"pessoa": pessoa, "exame": exame}


# --------------------------------------------------------------- fundamentos

def test_kpis_usam_dados_reais_do_banco(client, auth, paciente):
    resp = client.get("/api/v1/crm/kpis", headers=auth("leitura"))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Todos os KPIs exigidos existem e são numéricos — sem valor de demonstração.
    for chave in (
        "total_pacientes", "contatos_hoje", "contatos_atrasados", "proximos_7",
        "proximos_30", "sem_telefone", "followups_concluidos_mes",
        "pacientes_reativados", "exames_mes", "consultas_mes",
    ):
        assert isinstance(data[chave], int), chave
    assert data["total_pacientes"] == 1


def test_kpis_vazios_quando_nao_ha_paciente(client, auth):
    data = client.get("/api/v1/crm/kpis", headers=auth("leitura")).json()
    assert data["total_pacientes"] == 0
    assert data["contatos_atrasados"] == 0


def test_lista_de_pacientes_nunca_devolve_telefone_completo(client, auth, paciente):
    resp = client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth("operacional")
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["itens"][0]
    mascara = item["contato"]["telefone_mascarado"]
    assert item["contato"]["telefone_utilizavel"] is True
    assert mascara is not None
    assert TEST_PHONE_NORM not in mascara
    assert mascara.endswith(TEST_PHONE_NORM[-4:])
    assert mascara.count("•") == len(TEST_PHONE_NORM) - 4
    # o corpo inteiro não pode conter o número completo em lugar nenhum
    assert TEST_PHONE_NORM not in resp.text


def test_lista_de_pacientes_traz_codigo_canonico_e_ultimos_atendimentos(
    client, auth, paciente
):
    _criar_consulta(client, auth, paciente["pessoa"]["id"], date.today() - timedelta(days=10))
    item = client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth("operacional")
    ).json()["itens"][0]
    assert item["public_code"] == paciente["pessoa"]["public_code"]
    assert item["public_code"].startswith("PES-")
    assert item["ultimo_exame"]["public_code"] == paciente["exame"]["public_code"]
    assert item["ultima_consulta"]["public_code"].startswith("CON-")
    assert item["proximo_contato"]["public_code"].startswith("FUP-")


def test_busca_por_nome_vai_no_corpo_e_filtra(client, auth, paciente):
    _criar_pessoa(client, auth, "Outra Pessoa Distinta")
    _criar_exame(
        client, auth,
        client.post(
            "/api/v1/pessoas/busca", json={"q": "Outra Pessoa"}, headers=auth("leitura")
        ).json()["itens"][0]["id"],
        date.today() - timedelta(days=30),
    )
    resp = client.post(
        "/api/v1/crm/pacientes/busca",
        json={"q": "Paciente Teste"},
        headers=auth("leitura"),
    )
    itens = resp.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["nome_completo"] == "Paciente Teste M19"


def test_financeiro_do_paciente_so_para_gestor(client, auth, paciente):
    leitura = client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth("leitura")
    ).json()
    assert leitura["com_financeiro"] is False
    assert "financeiro" not in leitura["itens"][0]

    gestor = client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth("gestor")
    ).json()
    assert gestor["com_financeiro"] is True
    assert gestor["itens"][0]["financeiro"]["status"] == "sem_lancamento"


# ------------------------------------------------------- responsável / menor

def test_contato_usa_responsavel_legal_do_menor(client, auth, db):
    menor = _criar_pessoa_sem_contato(client, auth, "Menor Teste M19")
    responsavel = _criar_pessoa(client, auth, "Responsavel Teste M19")
    resp = client.post(
        f"/api/v1/pessoas/{menor['id']}/responsaveis",
        json={
            "guardian_person_id": responsavel["id"],
            "relationship_type": "mother",
            "is_legal_guardian": True,
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    _criar_exame(client, auth, menor["id"], date.today() - timedelta(days=190))

    itens = client.post(
        "/api/v1/crm/pacientes/busca", json={"q": "Menor Teste"},
        headers=auth("operacional"),
    ).json()["itens"]
    assert len(itens) == 1
    contato = itens[0]["contato"]
    # o paciente continua sendo o menor; quem é contatado é a responsável
    assert itens[0]["public_code"] == menor["public_code"]
    assert contato["public_code"] == responsavel["public_code"]
    assert contato["eh_responsavel"] is True
    assert contato["telefone_utilizavel"] is True


# ------------------------------------------------------------- filas / prazos

def test_fila_atrasados_e_seis_meses_exatos(client, auth, db, paciente):
    exame_data = date.fromisoformat(paciente["exame"]["data_exame"])
    fup = _followup_row(db, paciente["pessoa"]["id"])
    # 6 meses de CALENDÁRIO — mesmo dia, seis meses adiante.
    esperado = exame_data.replace(
        year=exame_data.year + (exame_data.month + 6 - 1) // 12,
        month=(exame_data.month + 6 - 1) % 12 + 1,
    )
    assert fup["due_date"] == esperado

    resp = client.get(
        "/api/v1/crm/contatos-a-realizar?fila=atrasados", headers=auth("leitura")
    )
    assert resp.status_code == 200
    itens = resp.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["status_apresentacao"] == "atrasado"
    assert itens[0]["origem"]["public_code"] == paciente["exame"]["public_code"]
    assert itens[0]["motivo"] == "pos_exame"


def test_todas_as_filas_exigidas_existem(client, auth, paciente):
    totais = client.get(
        "/api/v1/crm/contatos-a-realizar", headers=auth("leitura")
    ).json()["totais"]
    assert set(totais) == {
        "hoje", "atrasados", "proximos_7", "proximos_30", "sem_telefone",
        "reagendados", "nao_responderam", "nao_contatar",
    }


def test_fila_sem_telefone_pega_paciente_sem_numero_discavel(client, auth):
    pessoa = _criar_pessoa_sem_contato(client, auth, "Sem Telefone M19")
    _criar_exame(client, auth, pessoa["id"], date.today() - timedelta(days=200))
    itens = client.get(
        "/api/v1/crm/contatos-a-realizar?fila=sem_telefone", headers=auth("leitura")
    ).json()["itens"]
    assert [i["paciente"]["public_code"] for i in itens] == [pessoa["public_code"]]
    assert itens[0]["contato"]["telefone_utilizavel"] is False
    assert itens[0]["contato"]["telefone_mascarado"] is None


def test_fila_ignora_followup_cancelado(client, auth, db, paciente):
    db.rollback()
    fup = (
        db.query(Followup)
        .filter(Followup.person_id == paciente["pessoa"]["id"])
        .one()
    )
    fup.status = "cancelado"
    db.commit()
    itens = client.get(
        "/api/v1/crm/contatos-a-realizar", headers=auth("leitura")
    ).json()["itens"]
    assert itens == []


# --------------------------------------------------------------- WhatsApp

def test_whatsapp_de_paciente_exige_modelo_valido(client, auth, paciente):
    resp = client.get(
        f"/api/v1/crm/pacientes/{paciente['pessoa']['id']}/whatsapp-url?template=inexistente",
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "template", ["pos_exame", "resultado_exame", "pos_consulta", "reativacao", "geral"]
)
def test_todos_os_modelos_de_mensagem_geram_previa(client, auth, paciente, template):
    resp = client.get(
        f"/api/v1/crm/pacientes/{paciente['pessoa']['id']}/whatsapp-url?template={template}",
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["envio_automatico"] is False
    assert data["mensagem_sugerida"].startswith("Olá Paciente")
    assert data["url"].startswith("https://wa.me/")
    assert data["contato"]["telefone_mascarado"].endswith(TEST_PHONE_NORM[-4:])


def test_whatsapp_bloqueado_sem_consentimento(client, auth):
    pessoa = _criar_pessoa(client, auth, "Sem Consentimento M19", consentimento="revogado")
    _criar_exame(client, auth, pessoa["id"], date.today() - timedelta(days=200))
    resp = client.get(
        f"/api/v1/crm/pacientes/{pessoa['id']}/whatsapp-url",
        headers=auth("operacional"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "sem_consentimento"


def test_abrir_whatsapp_nao_conclui_followup(client, auth, db, paciente):
    client.get(
        f"/api/v1/crm/pacientes/{paciente['pessoa']['id']}/whatsapp-url",
        headers=auth("operacional"),
    )
    fup = _followup_row(db, paciente["pessoa"]["id"])
    assert fup["status"] == "pendente"
    assert fup["tentativas"] == 0


# ------------------------------------------------------- resultado do contato

def _followup_row(db, person_id, tipo="pos_exame"):
    """Lê o follow-up como valores puros e libera a transação do SQLite.

    O cliente da API escreve por outra conexão; manter uma transação de
    leitura aberta na sessão de teste travaria o banco de arquivo.
    """
    db.rollback()
    fup = (
        db.query(Followup)
        .filter(Followup.person_id == person_id, Followup.tipo == tipo)
        .one()
    )
    row = {
        "id": fup.id,
        "status": fup.status,
        "tentativas": fup.tentativas,
        "due_date": fup.due_date,
        "due_date_manual": fup.due_date_manual,
    }
    db.rollback()
    return row


def _followup_id(db, person_id, tipo="pos_exame"):
    return _followup_row(db, person_id, tipo)["id"]


def _pessoa_nao_contatar(db, person_id):
    db.rollback()
    valor = db.get(Person, person_id).nao_contatar
    db.rollback()
    return valor


def _contar_followups_pendentes(db, **filtros):
    db.rollback()
    q = db.query(Followup).filter(Followup.status == "pendente")
    for campo, valor in filtros.items():
        q = q.filter(getattr(Followup, campo) == valor)
    total = q.count()
    db.rollback()
    return total


def test_contato_realizado_cria_uma_interacao_e_conclui(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    resp = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "contato_realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["efeito"] == "followup_concluido"

    interacoes = client.get(
        f"/api/v1/interacoes?followup_id={fid}", headers=auth("leitura")
    ).json()
    assert interacoes["total"] == 1
    assert interacoes["itens"][0]["public_code"].startswith("INT-")

    assert _followup_row(db, paciente["pessoa"]["id"])["status"] == "concluido"


def test_nao_respondeu_mantem_pendente_e_entra_na_fila(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    resp = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "nao_respondeu"},
        headers=auth("operacional"),
    )
    assert resp.json()["efeito"] == "tentativa_registrada"
    fup = _followup_row(db, paciente["pessoa"]["id"])
    assert fup["status"] == "pendente"
    assert fup["tentativas"] == 1

    itens = client.get(
        "/api/v1/crm/contatos-a-realizar?fila=nao_responderam", headers=auth("leitura")
    ).json()["itens"]
    assert len(itens) == 1


def test_reagendar_exige_data_e_muda_vencimento(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    sem_data = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "reagendar"},
        headers=auth("operacional"),
    )
    assert sem_data.status_code == 422

    nova = date.today() + timedelta(days=15)
    resp = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "reagendar", "nova_data": nova.isoformat()},
        headers=auth("operacional"),
    )
    assert resp.json()["efeito"] == "followup_reagendado"
    fup = _followup_row(db, paciente["pessoa"]["id"])
    assert fup["due_date"] == nova
    assert fup["due_date_manual"] is True

    itens = client.get(
        "/api/v1/crm/contatos-a-realizar?fila=reagendados", headers=auth("leitura")
    ).json()["itens"]
    assert len(itens) == 1
    assert itens[0]["status_apresentacao"] == "reagendado"


def test_nao_deseja_contato_marca_pessoa_e_tira_da_fila(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "nao_deseja_contato"},
        headers=auth("operacional"),
    )
    assert _pessoa_nao_contatar(db, paciente["pessoa"]["id"]) is True
    # nunca mais aparece em fila acionável; some das filas de prazo
    totais = client.get(
        "/api/v1/crm/contatos-a-realizar", headers=auth("leitura")
    ).json()["totais"]
    assert totais["atrasados"] == 0
    assert totais["nao_contatar"] == 0  # follow-up foi cancelado junto


def test_telefone_invalido_marca_contato_nao_discavel(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    resp = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "telefone_invalido"},
        headers=auth("operacional"),
    )
    assert resp.json()["efeito"] == "telefone_marcado_invalido"
    itens = client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth("operacional")
    ).json()["itens"]
    assert itens[0]["contato"]["telefone_utilizavel"] is False


def test_observacao_com_pii_e_recusada(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    resp = client.post(
        "/api/v1/crm/contatos",
        json={
            "followup_id": fid,
            "resultado": "nao_respondeu",
            "observacao": "retornar no 21 98888-7766",
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422


def test_leitura_nao_registra_contato(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    resp = client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "nao_respondeu"},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


# ------------------------------------------------------------- duplicidade

def test_followup_duplicado_e_bloqueado(client, auth, db, paciente):
    """Nem a API nem a sincronização de origem criam um segundo pendente."""
    pessoa_id = paciente["pessoa"]["id"]
    exame_id = paciente["exame"]["id"]

    # 1) o follow-up da origem (exame) já existe e é único
    assert _contar_followups_pendentes(db, person_id=pessoa_id, origem_id=exame_id) == 1

    # 2) editar o exame ressincroniza o MESMO follow-up, nunca cria outro
    patch = client.patch(
        f"/api/v1/espirometrias/{exame_id}",
        json={"observacao": "reprocessado no M19"},
        headers=auth("operacional"),
    )
    assert patch.status_code == 200, patch.text
    assert _contar_followups_pendentes(db, person_id=pessoa_id, origem_id=exame_id) == 1

    # 3) criar manualmente duas vezes devolve o existente na segunda
    primeiro = client.post(
        "/api/v1/followups",
        json={
            "patient_person_id": pessoa_id,
            "tipo": "manual",
            "due_date": (date.today() + timedelta(days=5)).isoformat(),
        },
        headers=auth("operacional"),
    )
    assert primeiro.status_code == 201, primeiro.text
    assert primeiro.json()["motivo"] == "criado"

    segundo = client.post(
        "/api/v1/followups",
        json={"patient_person_id": pessoa_id, "tipo": "manual"},
        headers=auth("operacional"),
    )
    assert segundo.status_code == 201, segundo.text
    assert segundo.json()["motivo"] == "ja_existente"
    assert segundo.json()["public_code"] == primeiro.json()["public_code"]
    assert _contar_followups_pendentes(db, person_id=pessoa_id, tipo="manual") == 1


# ------------------------------------------------------------- linha do tempo

def test_timeline_combina_todas_as_entidades(client, auth, db, paciente):
    pessoa_id = paciente["pessoa"]["id"]
    _criar_consulta(client, auth, pessoa_id, date.today() - timedelta(days=20))
    client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": _followup_id(db, pessoa_id), "resultado": "nao_respondeu"},
        headers=auth("operacional"),
    )
    resp = client.get(
        f"/api/v1/crm/pacientes/{pessoa_id}/timeline", headers=auth("gestor")
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    tipos = {e["tipo"] for e in data["eventos"]}
    assert {"cadastro", "espirometria", "consulta", "followup", "interacao"} <= tipos
    datas = [e["data"] or "" for e in data["eventos"]]
    assert datas == sorted(datas)
    assert data["paciente"]["rotulo_codigo"] == "Pessoa"


def test_timeline_esconde_financeiro_de_leitura(client, auth, paciente):
    data = client.get(
        f"/api/v1/crm/pacientes/{paciente['pessoa']['id']}/timeline",
        headers=auth("leitura"),
    ).json()
    assert data["com_financeiro"] is False
    assert all(e["tipo"] != "financeiro" for e in data["eventos"])


def test_timeline_404_para_pessoa_inexistente(client, auth):
    resp = client.get(
        "/api/v1/crm/pacientes/nao-existe/timeline", headers=auth("leitura")
    )
    assert resp.status_code == 404


# ------------------------------------------------------------- indicadores

def test_indicadores_usam_agregados_reais(client, auth, db, paciente):
    pessoa_id = paciente["pessoa"]["id"]
    _criar_consulta(client, auth, pessoa_id, date.today() - timedelta(days=20))
    client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": _followup_id(db, pessoa_id), "resultado": "nao_respondeu"},
        headers=auth("operacional"),
    )
    data = client.get("/api/v1/crm/indicadores?meses=24", headers=auth("leitura")).json()
    assert sum(p["valor"] for p in data["exames_por_mes"]) == 1
    assert sum(p["valor"] for p in data["consultas_por_mes"]) == 1
    assert data["contatos_atrasados"] == 1
    assert data["resultados_de_contato"] == [
        {"resultado": "nao_respondeu", "rotulo": "Não respondeu", "valor": 1}
    ]
    assert data["pacientes_por_origem"][0]["valor"] == 1


def test_indicadores_vazios_tem_estado_vazio_correto(client, auth):
    data = client.get("/api/v1/crm/indicadores", headers=auth("leitura")).json()
    assert data["exames_por_mes"] == []
    assert data["consultas_por_mes"] == []
    assert data["resultados_de_contato"] == []
    assert data["contatos_atrasados"] == 0


def test_paciente_reativado_exige_intervalo_longo_real(client, auth, db):
    pessoa = _criar_pessoa(client, auth, "Reativado Teste M19")
    _criar_exame(client, auth, pessoa["id"], date.today() - timedelta(days=400))
    _criar_exame(client, auth, pessoa["id"], date.today() - timedelta(days=5))
    db.rollback()
    reativados = csvc.reactivated_patients(db, None, None)
    assert len(reativados) == 1
    assert reativados[0]["dias_sem_atendimento"] == 395
    db.rollback()

    outra = _criar_pessoa(client, auth, "Nao Reativado M19")
    _criar_exame(client, auth, outra["id"], date.today() - timedelta(days=40))
    _criar_exame(client, auth, outra["id"], date.today() - timedelta(days=5))
    db.rollback()
    assert len(csvc.reactivated_patients(db, None, None)) == 1
    db.rollback()


# ------------------------------------------------------------- histórico

def test_historico_de_contatos_filtra_e_identifica_operador(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "nao_respondeu"},
        headers=auth("operacional"),
    )
    data = client.get("/api/v1/crm/historico-contatos", headers=auth("leitura")).json()
    assert data["total"] == 1
    item = data["itens"][0]
    assert item["resultado_rotulo"] == "Não respondeu"
    assert item["operador"]["nome"] == "Teste operacional"
    assert item["followup"]["public_code"].startswith("FUP-")
    assert item["paciente"]["public_code"] == paciente["pessoa"]["public_code"]

    filtrado = client.get(
        "/api/v1/crm/historico-contatos?resultado=contato_realizado",
        headers=auth("leitura"),
    ).json()
    assert filtrado["total"] == 0

    por_codigo = client.get(
        "/api/v1/crm/historico-contatos"
        f"?person_public_code={paciente['pessoa']['public_code']}",
        headers=auth("leitura"),
    ).json()
    assert por_codigo["total"] == 1


def test_historico_nunca_expoe_telefone(client, auth, db, paciente):
    fid = _followup_id(db, paciente["pessoa"]["id"])
    client.post(
        "/api/v1/crm/contatos",
        json={"followup_id": fid, "resultado": "nao_respondeu"},
        headers=auth("operacional"),
    )
    resp = client.get("/api/v1/crm/historico-contatos", headers=auth("leitura"))
    assert TEST_PHONE_NORM not in resp.text


# ------------------------------------------------------------- códigos / alias

def test_dicionario_de_prefixos_cobre_todas_as_tabelas():
    dicionario = code_dictionary()
    assert {d["prefixo"] for d in dicionario} == set(PREFIXES.values())
    assert all(d["formato"].endswith("-000000") for d in dicionario)


def test_resolver_codigo_canonico(client, auth, paciente):
    codigo = paciente["pessoa"]["public_code"]
    data = client.get(
        f"/api/v1/crm/codigos/resolver?codigo={codigo}", headers=auth("leitura")
    ).json()
    assert data["encontrado"] is True
    assert data["resultados"][0]["entidade"] == "people"
    assert data["resultados"][0]["rotulo"] == "Pessoa"
    assert data["resultados"][0]["public_code"] == codigo


def test_alias_historico_resolve_sem_renumerar(client, auth, db, paciente):
    """FIN-0004 -> LAN-000001 é este caso: alias aponta, código não muda."""
    exame_id = paciente["exame"]["id"]
    resp = client.post(
        "/api/v1/lancamentos",
        json={
            "tipo": "receita",
            "categoria": "Espirometria",
            "valor": "250.00",
            "status": "Recebido",
            "spirometry_exam_id": exame_id,
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 201, resp.text
    lancamento = resp.json()
    assert lancamento["public_code"] == "LAN-000001"

    db.rollback()
    db.add(LegacyAlias(
        entidade="financial_entries",
        entity_id=lancamento["id"],
        legacy_source="planilha_financeiro",
        legacy_id="FIN-0004",
    ))
    db.commit()

    data = client.get(
        "/api/v1/crm/codigos/resolver?codigo=FIN-0004", headers=auth("leitura")
    ).json()
    assert data["encontrado"] is True
    alias = [r for r in data["resultados"] if r["tipo"] == "alias_historico"][0]
    assert alias["public_code"] == "LAN-000001"
    assert alias["rotulo"] == "Lançamento"
    # nada foi renumerado nem duplicado
    assert client.get(
        "/api/v1/lancamentos", headers=auth("gestor")
    ).json()["total"] == 1


def test_resolver_codigo_desconhecido_nao_quebra(client, auth):
    data = client.get(
        "/api/v1/crm/codigos/resolver?codigo=XXX-999999", headers=auth("leitura")
    ).json()
    assert data["encontrado"] is False
    assert data["resultados"] == []


# ------------------------------------------------------------- config / RBAC

def test_config_traz_vocabulario_completo(client, auth):
    data = client.get("/api/v1/crm/config", headers=auth("leitura")).json()
    assert data["meses_followup"] == 6
    assert len(data["resultados_contato"]) == 5
    assert len(data["templates_whatsapp"]) == 5
    assert len(data["filas"]) == 8
    assert data["pode_ver_financeiro"] is False


def test_config_libera_financeiro_para_gestor(client, auth):
    data = client.get("/api/v1/crm/config", headers=auth("gestor")).json()
    assert data["pode_ver_financeiro"] is True


@pytest.mark.parametrize("papel", ["leitura", "operacional", "gestor", "admin"])
def test_todos_os_papeis_acessam_o_crm(client, auth, paciente, papel):
    """Nenhum usuário válido pode ficar trancado fora do CRM canônico."""
    assert client.get("/api/v1/crm/kpis", headers=auth(papel)).status_code == 200
    assert client.post(
        "/api/v1/crm/pacientes/busca", json={}, headers=auth(papel)
    ).status_code == 200
    assert client.get(
        "/api/v1/crm/contatos-a-realizar", headers=auth(papel)
    ).status_code == 200


def test_crm_exige_autenticacao(client):
    assert client.get("/api/v1/crm/kpis").status_code == 401
    assert client.post("/api/v1/crm/pacientes/busca", json={}).status_code == 401


# --------------------------------------------------------- status derivados

def test_status_de_apresentacao_cobre_o_vocabulario_exigido(db):
    hoje = today_local()
    pessoa = Person(
        public_code="PES-999999", nome_completo="X", nome_normalizado="x"
    )
    casos = {
        "cancelado": Followup(status="cancelado", tentativas=0, due_date_manual=False),
        "concluido": Followup(status="concluido", tentativas=0, due_date_manual=False),
        "atrasado": Followup(
            status="pendente", tentativas=0, due_date_manual=False,
            due_date=hoje - timedelta(days=1),
        ),
        "hoje": Followup(
            status="pendente", tentativas=0, due_date_manual=False, due_date=hoje
        ),
        "proximo": Followup(
            status="pendente", tentativas=0, due_date_manual=False,
            due_date=hoje + timedelta(days=3),
        ),
        "futuro": Followup(
            status="pendente", tentativas=0, due_date_manual=False,
            due_date=hoje + timedelta(days=90),
        ),
        "reagendado": Followup(
            status="pendente", tentativas=1, due_date_manual=True,
            due_date=hoje + timedelta(days=10),
        ),
    }
    for esperado, fup in casos.items():
        assert csvc.presentation_status(fup, pessoa, hoje) == esperado

    pessoa.nao_contatar = True
    assert csvc.presentation_status(
        Followup(status="pendente", tentativas=0, due_date_manual=False, due_date=hoje),
        pessoa, hoje,
    ) == "nao_contatar"
    assert set(csvc.STATUS_LABELS) == {
        "futuro", "proximo", "hoje", "atrasado", "concluido", "reagendado",
        "nao_contatar", "cancelado",
    }


def test_mascara_de_telefone_nunca_vaza_digitos_do_meio():
    assert csvc.mask_phone("5521988887766") == "•••••••••7766"
    assert csvc.mask_phone(None) is None
    assert csvc.mask_phone("12") == "••"
