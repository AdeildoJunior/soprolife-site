"""M20 — consolidação Pastore, status de exame e fluxo único de atendimento.

Provas obrigatórias desta etapa. Nenhum teste toca produção: todos rodam
contra o banco isolado por teste (SQLite/PostgreSQL efêmero das fixtures).
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Consultation,
    FinancialEntry,
    Followup,
    Partner,
    PartnerContact,
    PartnerUnit,
    PartnerUnitConfig,
    Partnership,
    Person,
    SpirometryExam,
)
from app.services.partner_merge import merge_partner, resolve_partner
from app.status_display import (
    EXAM_PERFORMED_DISPLAY,
    exam_status_display,
    exam_status_filter_values,
)

API = "/api/v1"


# ------------------------------------------------- 2. status de espirometria

@pytest.mark.parametrize(
    "armazenado",
    ["Realizado", "realizado", "Exame realizado", "exame realizado"],
)
def test_status_realizado_exibe_espirometria_realizada(armazenado):
    assert exam_status_display(armazenado) == EXAM_PERFORMED_DISPLAY
    assert EXAM_PERFORMED_DISPLAY == "Espirometria realizada"


@pytest.mark.parametrize("armazenado", ["Liberado", "liberado"])
def test_liberado_permanece_exatamente_liberado(armazenado):
    """FORA DE ESCOPO: 'Liberado' não é renomeado, migrado nem remapeado."""
    assert exam_status_display(armazenado) == armazenado


@pytest.mark.parametrize(
    "armazenado",
    ["Aguardando", "Laudo Liberado", "Cancelado", "Remarcado", "Agendada",
     "Consulta realizada", "Não compareceu", ""],
)
def test_nenhum_outro_status_e_alterado(armazenado):
    assert exam_status_display(armazenado) == armazenado


def test_status_none_continua_none():
    assert exam_status_display(None) is None


def test_filtro_realizado_casa_todos_os_sinonimos():
    valores = exam_status_filter_values(EXAM_PERFORMED_DISPLAY)
    assert set(valores) == {"Realizado", "realizado", "Exame realizado", "exame realizado"}
    assert exam_status_filter_values("Realizado") == valores


def test_filtro_liberado_e_igualdade_exata():
    assert exam_status_filter_values("Liberado") == ["Liberado"]
    assert exam_status_filter_values("Laudo Liberado") == ["Laudo Liberado"]


def test_serializer_expoe_status_exibicao_sem_reescrever_o_gravado(client, auth, person, db):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "data_exame": "2026-07-01", "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "Realizado"
    assert resp.json()["status_exibicao"] == EXAM_PERFORMED_DISPLAY

    # valor histórico fora do vocabulário atual continua intacto no banco
    exam = db.get(SpirometryExam, resp.json()["id"])
    exam.status = "Liberado"
    db.commit()
    lista = client.get(f"{API}/espirometrias", headers=auth("leitura")).json()
    linha = [e for e in lista["itens"] if e["id"] == exam.id][0]
    assert linha["status"] == "Liberado"
    assert linha["status_exibicao"] == "Liberado"


def test_filtro_da_api_casa_sinonimos_e_preserva_liberado(client, auth, person, db):
    for status in ("Realizado", "Aguardando"):
        client.post(
            f"{API}/espirometrias",
            json={"person_id": person["id"], "data_exame": "2026-07-02", "status": status},
            headers=auth("operacional"),
        )
    # grava um sinônimo histórico e um "Liberado" direto no banco
    for status in ("Exame realizado", "Liberado"):
        db.add(SpirometryExam(
            public_code=f"ESP-TST{status[:3]}", person_id=person["id"],
            status=status,
        ))
    db.commit()

    realizados = client.get(
        f"{API}/espirometrias", params={"status": EXAM_PERFORMED_DISPLAY},
        headers=auth("leitura"),
    ).json()
    assert {e["status"] for e in realizados["itens"]} == {"Realizado", "Exame realizado"}

    liberados = client.get(
        f"{API}/espirometrias", params={"status": "Liberado"}, headers=auth("leitura"),
    ).json()
    assert [e["status"] for e in liberados["itens"]] == ["Liberado"]


def test_timeline_do_paciente_usa_o_rotulo_canonico(client, auth, person, db):
    resp = client.post(
        f"{API}/espirometrias",
        json={"person_id": person["id"], "data_exame": "2026-07-03", "status": "Realizado"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    timeline = client.get(
        f"{API}/crm/pacientes/{person['id']}/timeline", headers=auth("gestor")
    ).json()
    exames = [e for e in timeline["eventos"] if e["tipo"] == "espirometria"]
    assert exames and exames[0]["detalhe"] == EXAM_PERFORMED_DISPLAY


# ----------------------------------------------- 1. consolidação de parceiro

@pytest.fixture()
def pastore(db):
    """Reproduz a topologia real: canônico com unidade/exames/agendas e uma
    duplicata do CRM antigo com contato e parceria."""
    canonical = Partner(
        public_code="CLI-000002", nome="Pastore", tipo="clinica", status="ativa",
        legacy_source="pastore", legacy_id="ativo",
    )
    duplicate = Partner(
        public_code="CLI-000001", nome="Pastore", tipo="consultorio",
        status="ativa", legacy_source="crm_clinicas", legacy_id="CLIN-006",
    )
    db.add_all([canonical, duplicate])
    db.flush()

    unidade_boa = PartnerUnit(
        public_code="UNI-000002", partner_id=canonical.id, nome="Pastore Ipanema",
    )
    unidade_vazia = PartnerUnit(
        public_code="UNI-000001", partner_id=duplicate.id, nome="Pastore",
        bairro="Ipanema", cidade="Zona Sul",
    )
    db.add_all([unidade_boa, unidade_vazia])
    db.flush()

    db.add_all([
        PartnerUnitConfig(partner_unit_id=unidade_boa.id, status="Planejada",
                          dia_semana="Terça-feira", horario_inicio="08:00",
                          horario_fim="12:00"),
        PartnerUnitConfig(partner_unit_id=unidade_boa.id, status="Planejada",
                          dia_semana="Sábado", horario_inicio="08:00",
                          horario_fim="12:00"),
        PartnerContact(public_code="CTT-000001", partner_id=duplicate.id,
                       nome="Contato Institucional 001", cargo="Diretor médico",
                       telefone="(21) 0000-9002"),
        Partnership(public_code="PAR-000001", partner_id=duplicate.id,
                    status="em_negociacao", modelo_repasse="indefinido"),
    ])

    pessoa = Person(public_code="PES-T001", nome_completo="Paciente Teste 001",
                    nome_normalizado="paciente teste 001")
    db.add(pessoa)
    db.flush()
    exames = [
        SpirometryExam(public_code="ESP-000013", person_id=pessoa.id,
                       status="Liberado", partner_id=canonical.id,
                       partner_unit_id=unidade_boa.id, modalidade="clinica_parceira"),
        SpirometryExam(public_code="ESP-000014", person_id=pessoa.id,
                       status="Liberado", partner_id=canonical.id,
                       partner_unit_id=unidade_boa.id, modalidade="clinica_parceira"),
    ]
    db.add_all(exames)
    db.commit()
    return {"canonical": canonical, "duplicate": duplicate,
            "unidade_boa": unidade_boa, "unidade_vazia": unidade_vazia}


def test_consolidacao_deixa_um_parceiro_e_uma_unidade_selecionaveis(
    client, auth, db, pastore
):
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()

    parceiros = client.get(f"{API}/parceiros", headers=auth("leitura")).json()
    assert [p["public_code"] for p in parceiros["itens"]] == ["CLI-000002"]

    unidades = client.get(f"{API}/unidades", headers=auth("leitura")).json()
    assert [u["nome"] for u in unidades["itens"]] == ["Pastore Ipanema"]

    # nada foi apagado: a duplicata continua acessível para inspeção técnica
    todos = client.get(
        f"{API}/parceiros", params={"incluir_arquivados": True}, headers=auth("leitura"),
    ).json()
    assert {p["public_code"] for p in todos["itens"]} == {"CLI-000001", "CLI-000002"}


def test_consolidacao_preserva_exames_unidade_e_agendas(db, pastore):
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()

    exames = db.execute(
        select(SpirometryExam).where(SpirometryExam.partner_id == pastore["canonical"].id)
    ).scalars().all()
    assert {e.public_code for e in exames} == {"ESP-000013", "ESP-000014"}
    assert all(e.partner_unit_id == pastore["unidade_boa"].id for e in exames)
    assert all(e.status == "Liberado" for e in exames)

    agendas = db.execute(
        select(PartnerUnitConfig).where(
            PartnerUnitConfig.partner_unit_id == pastore["unidade_boa"].id
        )
    ).scalars().all()
    assert sorted(c.dia_semana for c in agendas) == ["Sábado", "Terça-feira"]


def test_codigo_obsoleto_resolve_para_o_canonico(client, auth, db, pastore):
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()

    canonical, found = resolve_partner(db, "CLI-000001")
    assert canonical.id == pastore["canonical"].id
    assert found.public_code == "CLI-000001"
    # alias legado da fonte antiga também resolve
    assert resolve_partner(db, "CLIN-006")[0].id == pastore["canonical"].id

    resp = client.get(f"{API}/parceiros/CLI-000001", headers=auth("leitura"))
    assert resp.status_code == 200
    assert resp.json()["public_code"] == "CLI-000002"
    assert resp.json()["resolvido_de"]["public_code"] == "CLI-000001"


def test_nenhum_contato_ou_relacionamento_util_e_perdido(db, pastore):
    resultado = merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()

    contatos = db.execute(
        select(PartnerContact).where(PartnerContact.partner_id == pastore["canonical"].id)
    ).scalars().all()
    assert [c.public_code for c in contatos] == ["CTT-000001"]
    assert resultado["contatos_migrados"] == ["CTT-000001"]

    parcerias = db.execute(
        select(Partnership).where(Partnership.partner_id == pastore["canonical"].id)
    ).scalars().all()
    assert [p.public_code for p in parcerias] == ["PAR-000001"]

    # metadados da unidade vazia foram aproveitados sem sobrescrever nada
    assert pastore["unidade_boa"].bairro == "Ipanema"
    assert pastore["unidade_vazia"].ativo is False


def test_consolidacao_nao_duplica_contato_ja_existente(db, pastore):
    db.add(PartnerContact(
        public_code="CTT-000002", partner_id=pastore["canonical"].id,
        nome="Contato Institucional 001", cargo="Diretor médico",
        telefone="(21) 0000-9002",
    ))
    db.commit()
    resultado = merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()

    ativos = db.execute(
        select(PartnerContact).where(
            PartnerContact.partner_id == pastore["canonical"].id,
            PartnerContact.ativo.is_(True),
        )
    ).scalars().all()
    assert len(ativos) == 1
    assert resultado["contatos_duplicados_ignorados"] == ["CTT-000001"]


def test_parceiro_arquivado_recusa_escrita(client, auth, db, pastore):
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()
    resp = client.post(
        f"{API}/unidades",
        json={"partner_id": pastore["duplicate"].id, "nome": "Unidade Nova"},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "parceiro_arquivado"


# ------------------------------------------------- 4. fluxo Novo atendimento

def _pessoa(client, auth, nome="Paciente Fluxo 001"):
    resp = client.post(
        f"{API}/pessoas", json={"nome_completo": nome}, headers=auth("operacional")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_atendimento_espirometria_soprolife(client, auth):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {"data_exame": "2026-07-20", "status": "Realizado",
                             "broncodilatador": True},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["consulta"] is None
    assert body["espirometria"]["status_exibicao"] == EXAM_PERFORMED_DISPLAY
    assert body["espirometria"]["partner_id"] is None
    assert body["lancamentos"] == []


def test_atendimento_soprolife_recusa_parceiro(client, auth, db, pastore):
    pessoa = _pessoa(client, auth)
    for tipo in ("espirometria_soprolife", "espirometria_consulta_soprolife"):
        payload = {
            "person_id": pessoa["id"],
            "tipo": tipo,
            "espirometria": {"data_exame": "2026-07-20",
                             "partner_id": pastore["canonical"].id,
                             "partner_unit_id": pastore["unidade_boa"].id},
        }
        if tipo == "espirometria_consulta_soprolife":
            payload["consulta"] = {"data_consulta": "2026-07-20"}
        resp = client.post(f"{API}/atendimentos", json=payload, headers=auth("operacional"))
        assert resp.status_code == 422, resp.text


def test_espirometria_pastore_exige_unidade_operacional(client, auth, db, pastore):
    pessoa = _pessoa(client, auth)
    sem_unidade = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "espirometria_pastore",
              "espirometria": {"data_exame": "2026-07-21",
                               "partner_id": pastore["canonical"].id}},
        headers=auth("operacional"),
    )
    assert sem_unidade.status_code == 422

    # M22: a resolução é fail-closed até a duplicata M20 estar consolidada.
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    db.commit()
    com_unidade = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "espirometria_pastore",
              "espirometria": {"data_exame": "2026-07-21",
                               "partner_id": pastore["canonical"].id,
                               "partner_unit_id": pastore["unidade_boa"].id}},
        headers=auth("operacional"),
    )
    assert com_unidade.status_code == 201, com_unidade.text
    assert com_unidade.json()["espirometria"]["partner_unit_id"] == pastore["unidade_boa"].id


def test_espirometria_pastore_recusa_unidade_inativa(client, auth, db, pastore):
    merge_partner(db, pastore["duplicate"], pastore["canonical"])
    pastore["unidade_boa"].ativo = False
    db.commit()
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "espirometria_pastore",
              "espirometria": {"data_exame": "2026-07-21",
                               "partner_id": pastore["canonical"].id,
                               "partner_unit_id": pastore["unidade_boa"].id}},
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == "unidade_pastore_invalida"


def test_espirometria_pastore_falha_fechada_com_duas_canonicas(
    client, auth, db, pastore
):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "espirometria_pastore",
              "espirometria": {"data_exame": "2026-07-21",
                               "partner_id": pastore["canonical"].id,
                               "partner_unit_id": pastore["unidade_boa"].id}},
        headers=auth("operacional"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pastore_canonica_ambigua"


def test_consulta_soprolife_receita_bruta_pertence_a_soprolife(client, auth, db):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "consulta_soprolife",
            "consulta": {"data_consulta": "2026-07-22", "status": "Realizada",
                         "profissional": "Profissional 001", "retorno": "sem_retorno"},
            "financeiro": {"consulta": {
                "valor_bruto": "300.00", "status": "Recebido",
                "data_recebimento": "2026-07-22",
                "repasse_medico_percentual": "100",
            }},
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    lancamentos = resp.json()["lancamentos"]
    assert len(lancamentos) == 2

    bruto = [entry for entry in lancamentos if entry["componente"] == "consulta_bruto"][0]
    repasse = [entry for entry in lancamentos
               if entry["componente"] == "consulta_repasse_medico"][0]
    # repasse de 100% NÃO apaga a receita bruta: são linhas separadas
    assert bruto["tipo"] == "receita" and Decimal(bruto["valor"]) == Decimal("300.00")
    assert repasse["tipo"] == "repasse" and Decimal(repasse["valor"]) == Decimal("300.00")

    linhas = db.execute(select(FinancialEntry)).scalars().all()
    assert {linha.tipo for linha in linhas} == {"receita", "repasse"}
    assert all(linha.consultation_id is not None for linha in linhas)


def test_consulta_sem_valor_nao_cria_lancamento(client, auth, db):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "consulta_soprolife",
              "consulta": {"data_consulta": "2026-07-22", "retorno": "sem_retorno"}},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["lancamentos"] == []
    assert db.execute(select(FinancialEntry)).scalars().all() == []


def test_consulta_nao_assume_retorno_de_seis_meses(client, auth, db):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "consulta_soprolife",
              "consulta": {"data_consulta": "2026-07-22", "status": "Realizada",
                           "retorno": "sem_retorno"}},
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["consulta"]["followup"]["id"] is None
    assert db.execute(
        select(Followup).where(Followup.tipo == "pos_consulta")
    ).scalars().all() == []
    db.rollback()

    outra = _pessoa(client, auth, "Paciente Fluxo 002")
    escolhido = client.post(
        f"{API}/atendimentos",
        json={"person_id": outra["id"], "tipo": "consulta_soprolife",
              "consulta": {"data_consulta": "2026-07-22", "status": "Realizada",
                           "retorno": "intervalo_meses", "retorno_intervalo_meses": 3}},
        headers=auth("operacional"),
    ).json()
    fup = db.get(Followup, escolhido["consulta"]["followup"]["id"])
    assert fup.due_date.isoformat() == "2026-10-22"


def test_atendimento_combinado_cria_um_de_cada_com_componentes_separados(
    client, auth, db
):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_consulta_soprolife",
            "espirometria": {"data_exame": "2026-07-23", "status": "Realizado"},
            "consulta": {"data_consulta": "2026-07-23", "status": "Realizada",
                         "retorno": "sem_retorno"},
            "financeiro": {
                "espirometria": {"valor": "220.00", "status": "Recebido",
                                 "data_recebimento": "2026-07-23"},
                "consulta": {"valor_bruto": "300.00", "status": "Recebido",
                             "data_recebimento": "2026-07-23",
                             "repasse_medico_percentual": "100"},
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert len(db.execute(select(Person)).scalars().all()) >= 1
    exames = db.execute(select(SpirometryExam)).scalars().all()
    consultas = db.execute(select(Consultation)).scalars().all()
    assert len(exames) == 1 and len(consultas) == 1
    assert exames[0].person_id == consultas[0].person_id == pessoa["id"]
    # combinado NUNCA é Pastore
    assert exames[0].partner_id is None

    componentes = {entry["componente"]: entry for entry in body["lancamentos"]}
    assert set(componentes) == {"espirometria", "consulta_bruto", "consulta_repasse_medico"}
    entradas = {entry.public_code: entry for entry in
                db.execute(select(FinancialEntry)).scalars().all()}
    esp_entry = entradas[componentes["espirometria"]["public_code"]]
    rep_entry = entradas[componentes["consulta_repasse_medico"]["public_code"]]
    # o repasse ao médico NÃO se aplica ao componente de espirometria
    assert esp_entry.spirometry_exam_id == exames[0].id
    assert esp_entry.consultation_id is None
    assert rep_entry.consultation_id == consultas[0].id
    assert rep_entry.spirometry_exam_id is None


def test_combinado_que_falha_nao_deixa_nada_parcial(client, auth, db):
    """A falha acontece DEPOIS de o exame já ter sido criado na transação."""
    pessoa = _pessoa(client, auth)
    # 1) consulta isolada consome a chave "<k>:con"
    chave = "chave-idem-m20-0001"
    primeira = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "consulta_soprolife",
              "idempotency_key": chave,
              "consulta": {"data_consulta": "2026-07-24", "retorno": "sem_retorno"}},
        headers=auth("operacional"),
    )
    assert primeira.status_code == 201, primeira.text

    exames_antes = len(db.execute(select(SpirometryExam)).scalars().all())
    consultas_antes = len(db.execute(select(Consultation)).scalars().all())
    lancamentos_antes = len(db.execute(select(FinancialEntry)).scalars().all())
    pessoas_antes = len(db.execute(select(Person)).scalars().all())
    db.rollback()

    # 2) combinado com a MESMA chave: o exame nasce, a consulta colide e
    #    o atendimento inteiro precisa ser desfeito.
    quebrado = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": pessoa["id"],
            "tipo": "espirometria_consulta_soprolife",
            "idempotency_key": chave,
            "espirometria": {"data_exame": "2026-07-25", "status": "Realizado"},
            "consulta": {"data_consulta": "2026-07-25", "status": "Realizada",
                         "retorno": "data", "retorno_data": "2027-01-25"},
            "financeiro": {"espirometria": {"valor": "220.00"},
                           "consulta": {"valor_bruto": "300.00"}},
        },
        headers=auth("operacional"),
    )
    assert quebrado.status_code == 409, quebrado.text

    assert len(db.execute(select(SpirometryExam)).scalars().all()) == exames_antes
    assert len(db.execute(select(Consultation)).scalars().all()) == consultas_antes
    assert len(db.execute(select(FinancialEntry)).scalars().all()) == lancamentos_antes
    assert len(db.execute(select(Person)).scalars().all()) == pessoas_antes


def test_selecionar_paciente_existente_nao_duplica_identidade(client, auth, db):
    pessoa = _pessoa(client, auth)
    for data in ("2026-07-20", "2026-07-27"):
        resp = client.post(
            f"{API}/atendimentos",
            json={"person_id": pessoa["id"], "tipo": "espirometria_soprolife",
                  "espirometria": {"data_exame": data, "status": "Realizado"}},
            headers=auth("operacional"),
        )
        assert resp.status_code == 201, resp.text
    pessoas = db.execute(
        select(Person).where(Person.id == pessoa["id"])
    ).scalars().all()
    assert len(pessoas) == 1
    exames = db.execute(
        select(SpirometryExam).where(SpirometryExam.person_id == pessoa["id"])
    ).scalars().all()
    assert len(exames) == 2


def test_rbac_leitura_nao_cria_atendimento(client, auth):
    pessoa = _pessoa(client, auth)
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": pessoa["id"], "tipo": "espirometria_soprolife",
              "espirometria": {"data_exame": "2026-07-20"}},
        headers=auth("leitura"),
    )
    assert resp.status_code == 403


def test_atendimento_sem_token_e_recusado(client):
    resp = client.post(
        f"{API}/atendimentos",
        json={"person_id": "x", "tipo": "espirometria_soprolife",
              "espirometria": {"data_exame": "2026-07-20"}},
    )
    assert resp.status_code == 401
