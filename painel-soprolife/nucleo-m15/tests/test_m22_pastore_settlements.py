"""M22 — exame Pastore não monetário e fechamento mensal agregado.

Todos os testes usam o SQLite isolado das fixtures; nenhuma produção ou
integração Google é acessada.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    FinancialEntry,
    Followup,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    SpirometryExam,
)

API = "/api/v1"


@pytest.fixture()
def pastore(db):
    partner = Partner(
        public_code="CLI-M22001",
        nome="Pastore",
        tipo="clinica",
        status="ativa",
        arquivado=False,
    )
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M22001",
        partner_id=partner.id,
        nome="Pastore Ipanema",
        ativo=True,
    )
    db.add(unit)
    db.commit()
    return partner, unit


def _attendance(client, auth, person, partner, unit, *, exam_date="2026-07-14",
                status="Realizado", extra=None):
    exam = {
        "data_exame": exam_date,
        "status": status,
        "partner_id": partner.id,
        "partner_unit_id": unit.id,
    }
    exam.update(extra or {})
    return client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": exam,
        },
        headers=auth("operacional"),
    )


def test_configuracao_resolve_parceiro_e_unidade_unica(client, auth, pastore):
    partner, unit = pastore
    resp = client.get(
        f"{API}/pastore/configuracao-atendimento",
        headers=auth("operacional"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["partner"]["id"] == partner.id
    assert body["partner"]["nome"] == "Pastore"
    assert [(u["id"], u["nome"]) for u in body["unidades"]] == [
        (unit.id, "Pastore Ipanema")
    ]
    assert body["unidade_unica"] is True
    assert body["modalidade"] == {
        "valor": "clinica_parceira",
        "rotulo": "Clínica parceira",
    }
    assert body["origem"] == "Pastore"


def test_configuracao_multiplas_unidades_filtra_so_ativas(
    client, auth, db, pastore
):
    partner, _unit = pastore
    active = PartnerUnit(
        public_code="UNI-M22002", partner_id=partner.id,
        nome="Pastore Leblon", ativo=True,
    )
    inactive = PartnerUnit(
        public_code="UNI-M22003", partner_id=partner.id,
        nome="Pastore Legado", ativo=False,
    )
    other = Partner(
        public_code="CLI-M22002", nome="Clínica Sintética",
        tipo="clinica", status="ativa",
    )
    db.add_all([active, inactive, other])
    db.flush()
    db.add(PartnerUnit(
        public_code="UNI-M22004", partner_id=other.id,
        nome="Outra unidade", ativo=True,
    ))
    db.commit()

    body = client.get(
        f"{API}/pastore/configuracao-atendimento",
        headers=auth("leitura"),
    ).json()
    assert body["unidade_unica"] is False
    assert {u["nome"] for u in body["unidades"]} == {
        "Pastore Ipanema", "Pastore Leblon"
    }


@pytest.mark.parametrize("count", [0, 2])
def test_configuracao_pastore_falha_fechada_sem_canonica_unica(
    client, auth, db, count
):
    for idx in range(count):
        db.add(Partner(
            public_code=f"CLI-AMB{idx:03d}", nome="Pastore",
            tipo="clinica", status="ativa", arquivado=False,
        ))
    db.commit()
    resp = client.get(
        f"{API}/pastore/configuracao-atendimento",
        headers=auth("leitura"),
    )
    assert resp.status_code == 409
    assert resp.json()["erro"]["mensagem"]["codigo"] == "pastore_canonica_ambigua"


def test_exame_deriva_modalidade_local_origem_e_cria_followup_sem_financeiro(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    resp = _attendance(
        client, auth, person, partner, unit,
        extra={
            "modalidade": "residencial",
            "local_atendimento": "Outro",
            "origem": "Paciente",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["lancamentos"] == []
    exam = db.get(SpirometryExam, body["espirometria"]["id"])
    assert exam.partner_id == partner.id
    assert exam.partner_unit_id == unit.id
    assert exam.modalidade == "clinica_parceira"
    assert exam.local_atendimento == "Pastore Ipanema"
    assert exam.origem == "Pastore"
    assert db.execute(select(FinancialEntry)).scalars().all() == []
    followup = db.get(Followup, body["espirometria"]["followup"]["id"])
    assert followup.partner_id == partner.id


def test_atendimento_pastore_rejeita_bloco_financeiro_e_nao_cria_nada(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "2026-07-14",
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
            "financeiro": {
                "espirometria": {
                    "valor": "250.00",
                    "status": "Recebido",
                    "data_recebimento": "2026-07-14",
                    "forma_pagamento": "Pix",
                }
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "pagamento_direto_pastore_proibido"
    assert "não aceita pagamento direto" in resp.json()["erro"]["mensagem"]
    assert db.execute(select(SpirometryExam)).scalars().all() == []
    assert db.execute(select(FinancialEntry)).scalars().all() == []


def test_lancamento_generico_recusa_vinculo_direto_com_exame_pastore(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    created = _attendance(client, auth, person, partner, unit)
    assert created.status_code == 201
    exam_id = created.json()["espirometria"]["id"]
    resp = client.post(
        f"{API}/lancamentos",
        json={
            "tipo": "receita",
            "valor": "250.00",
            "status": "Recebido",
            "data_recebimento": "2026-07-14",
            "forma_pagamento": "Pix",
            "spirometry_exam_id": exam_id,
        },
        headers=auth("gestor"),
    )
    assert resp.status_code == 422
    assert resp.json()["erro"]["mensagem"]["codigo"] == (
        "pagamento_direto_pastore_proibido"
    )
    assert db.execute(select(FinancialEntry)).scalars().all() == []


def test_elegibilidade_agrupa_por_parceiro_unidade_competencia(
    client, auth, person, pastore
):
    partner, unit = pastore
    for exam_date in ("2026-07-14", "2026-07-18"):
        assert _attendance(
            client, auth, person, partner, unit, exam_date=exam_date
        ).status_code == 201
    assert _attendance(
        client, auth, person, partner, unit,
        exam_date="2026-08-02", status="Aguardando",
    ).status_code == 201

    body = client.get(
        f"{API}/pastore/fechamentos", headers=auth("leitura")
    ).json()
    assert body["indicadores"]["aguardando_fechamento"] == 2
    assert body["regra_valor"] == "Não inferido; exige confirmação do gestor."
    assert body["grupos_elegiveis"] == [{
        "partner_unit_id": unit.id,
        "unidade": "Pastore Ipanema",
        "competencia": "2026-07",
        "quantidade": 2,
    }]
    assert {e["estado_fechamento"] for e in body["elegiveis"]} == {
        "Aguardando fechamento mensal"
    }


def test_fechamento_inclui_itens_sem_inferir_valor_e_impede_duplicata(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    exams = [
        _attendance(client, auth, person, partner, unit, exam_date=exam_date)
        .json()["espirometria"]["id"]
        for exam_date in ("2026-07-14", "2026-07-18")
    ]
    created = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-07"},
        headers=auth("gestor"),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "incluido"
    assert body["status_label"] == "Incluído no fechamento"
    assert body["valor_total"] is None
    assert body["itens"]["total"] == 2
    items = db.execute(select(PartnerSettlementItem)).scalars().all()
    assert {item.spirometry_exam_id for item in items} == set(exams)
    assert db.execute(select(FinancialEntry)).scalars().all() == []

    duplicate = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-07"},
        headers=auth("gestor"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["erro"]["mensagem"]["codigo"] == (
        "fechamento_mensal_duplicado"
    )


def test_mesmo_exame_nao_pode_entrar_em_dois_fechamentos(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    exam_id = _attendance(
        client, auth, person, partner, unit
    ).json()["espirometria"]["id"]
    first = PartnerSettlement(
        partner_id=partner.id, partner_unit_id=unit.id,
        competencia=date(2026, 7, 1), status="incluido",
    )
    second = PartnerSettlement(
        partner_id=partner.id, partner_unit_id=unit.id,
        competencia=date(2026, 8, 1), status="incluido",
    )
    db.add_all([first, second])
    db.flush()
    db.add(PartnerSettlementItem(settlement_id=first.id, spirometry_exam_id=exam_id))
    db.flush()
    db.add(PartnerSettlementItem(settlement_id=second.id, spirometry_exam_id=exam_id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_receber_exige_valor_e_recebimento_cria_um_recibo_agregado(
    client, auth, db, person, pastore
):
    partner, unit = pastore
    for exam_date in ("2026-07-14", "2026-07-18"):
        assert _attendance(
            client, auth, person, partner, unit, exam_date=exam_date
        ).status_code == 201
    settlement = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-07"},
        headers=auth("gestor"),
    ).json()

    invalid = client.patch(
        f"{API}/pastore/fechamentos/{settlement['id']}",
        json={"status": "a_receber"},
        headers=auth("gestor"),
    )
    assert invalid.status_code == 422
    assert invalid.json()["erro"]["mensagem"]["codigo"] == (
        "a_receber_sem_valor_confirmado"
    )
    confirmed = client.patch(
        f"{API}/pastore/fechamentos/{settlement['id']}",
        json={"status": "a_receber", "valor_total": "500.00"},
        headers=auth("gestor"),
    )
    assert confirmed.status_code == 200, confirmed.text

    received = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "500.00",
            "data_recebimento": "2026-08-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-receipt-2026-07",
        },
        headers=auth("gestor"),
    )
    assert received.status_code == 201, received.text
    body = received.json()
    assert body["status"] == "recebido"
    assert body["status_label"] == "Recebido da Pastore"
    assert body["recebimento"]["valor"] == "500.00"
    assert body["recebimento"]["data_recebimento"] == "2026-08-05"
    assert body["recebimento"]["forma_pagamento"] == "Pix"

    entries = db.execute(select(FinancialEntry)).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.partner_settlement_id == settlement["id"]
    assert entry.spirometry_exam_id is None
    assert entry.consultation_id is None
    assert entry.partner_referral_id is None
    assert entry.categoria == "Recebimento de parceiro"
    assert entry.valor == Decimal("500.00")
    assert entry.status == "Recebido"
    assert entry.data_competencia == date(2026, 7, 1)

    replay = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "500.00",
            "data_recebimento": "2026-08-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-receipt-2026-07",
        },
        headers=auth("gestor"),
    )
    assert replay.status_code == 201
    assert replay.json()["idempotente"] is True
    assert len(db.execute(select(FinancialEntry)).scalars().all()) == 1

    divergent_replay = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "500.00",
            "data_recebimento": "2026-08-06",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-receipt-2026-07",
        },
        headers=auth("gestor"),
    )
    assert divergent_replay.status_code == 409
    assert divergent_replay.json()["erro"]["mensagem"]["codigo"] == (
        "idempotencia_payload_divergente"
    )

    duplicate = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "500.00",
            "data_recebimento": "2026-08-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-other-key",
        },
        headers=auth("gestor"),
    )
    assert duplicate.status_code == 409


def test_recebimento_exige_gestor_valor_data_forma_e_valor_coerente(
    client, auth, person, pastore
):
    partner, unit = pastore
    assert _attendance(client, auth, person, partner, unit).status_code == 201
    forbidden = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-07"},
        headers=auth("operacional"),
    )
    assert forbidden.status_code == 403
    settlement = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-07"},
        headers=auth("gestor"),
    ).json()
    assert client.patch(
        f"{API}/pastore/fechamentos/{settlement['id']}",
        json={"status": "a_receber", "valor_total": "500.00"},
        headers=auth("gestor"),
    ).status_code == 200

    missing = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={"valor_confirmado": "500.00", "idempotency_key": "m22-missing"},
        headers=auth("gestor"),
    )
    assert missing.status_code == 422
    wrong = client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "499.99",
            "data_recebimento": "2026-08-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-wrong",
        },
        headers=auth("gestor"),
    )
    assert wrong.status_code == 409
    assert client.post(
        f"{API}/pastore/fechamentos/{settlement['id']}/receber",
        json={
            "valor_confirmado": "500.00",
            "data_recebimento": "2026-08-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "m22-oper",
        },
        headers=auth("operacional"),
    ).status_code == 403
