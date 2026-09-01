"""M26.2 — o caminho do dinheiro da espirometria, provado ponta a ponta.

Duas perguntas que a operação real fez e o código precisa responder sozinho:

1. o atendimento SoproLife direto cria UMA receita, e só uma, mesmo repetido;
2. o exame Pastore nunca vira receita antes de o gestor confirmar o
   recebimento — e, ainda assim, nunca fica órfão de fechamento.

Nada aqui infere preço. Onde não há valor decidido, o teste prova que o
sistema se recusa a inventar um.
"""

import importlib.util
import pathlib
import sys
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    FinancialEntry,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    SpirometryExam,
)

API = "/api/v1"


def _codigo(resp) -> str:
    """Código de domínio do erro, nas DUAS formas de envelope da API.

    Regra de negócio recusada no schema Pydantic sobe como código próprio;
    recusada no router sobe embrulhada em `http_<status>`. Um teste que só
    conhecesse uma das formas passaria a mentir na primeira vez que a regra
    mudasse de camada.
    """

    erro = resp.json()["erro"]
    mensagem = erro["mensagem"]
    if isinstance(mensagem, dict) and "codigo" in mensagem:
        return mensagem["codigo"]
    return erro["codigo"]

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "reconciliar_financeiro_espirometria.py"
)


def _carregar_script():
    """Importa o script versionado sem executá-lo pela linha de comando."""

    nucleo = str(pathlib.Path(__file__).resolve().parents[1])
    if nucleo not in sys.path:
        sys.path.insert(0, nucleo)
    spec = importlib.util.spec_from_file_location("reconciliar_m26_2", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def reconciliador():
    return _carregar_script()


@pytest.fixture()
def pastore(db):
    partner = Partner(
        public_code="CLI-M262A", nome="Pastore", tipo="clinica",
        status="ativa", arquivado=False,
    )
    db.add(partner)
    db.flush()
    unit = PartnerUnit(
        public_code="UNI-M262A", partner_id=partner.id,
        nome="Pastore Ipanema", ativo=True,
    )
    db.add(unit)
    db.commit()
    return partner, unit


def _domiciliar(client, auth, person, valor="230.00", chave=None, status="Realizado"):
    """O atendimento da Vanessa: SoproLife direto, domiciliar, valor digitado."""

    corpo = {
        "person_id": person["id"],
        "tipo": "espirometria_soprolife",
        "espirometria": {
            "data_exame": "2026-08-28",
            "status": status,
            "modalidade": "residencial",
        },
        "financeiro": {
            "espirometria": {
                "valor": valor,
                "status": "Pendente",
                "data_competencia": "2026-08-28",
            }
        },
    }
    if chave:
        corpo["idempotency_key"] = chave
    return client.post(f"{API}/atendimentos", json=corpo, headers=auth("operacional"))


def _exame_pastore(client, auth, person, partner, unit, data, status="Realizado"):
    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": data,
                "status": status,
                "partner_id": partner.id,
                "partner_unit_id": unit.id,
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["espirometria"]


# ------------------------------------------------- 1..2  SoproLife direto


def test_1_domiciliar_soprolife_cria_exatamente_uma_receita(client, auth, person, db):
    resp = _domiciliar(client, auth, person)
    assert resp.status_code == 201, resp.text
    corpo = resp.json()

    lancamentos = corpo["lancamentos"]
    assert len(lancamentos) == 1
    assert lancamentos[0]["componente"] == "espirometria"
    assert Decimal(lancamentos[0]["valor"]) == Decimal("230.00")

    exam_id = corpo["espirometria"]["id"]
    receitas = db.execute(
        select(FinancialEntry).where(
            FinancialEntry.spirometry_exam_id == exam_id,
            FinancialEntry.tipo == "receita",
        )
    ).scalars().all()
    assert len(receitas) == 1
    # O valor é o DIGITADO. Nenhuma tabela de preço foi consultada.
    assert receitas[0].valor == Decimal("230.00")


def test_2_repetir_o_mesmo_atendimento_nao_duplica_receita(client, auth, person, db):
    primeiro = _domiciliar(client, auth, person, chave="vanessa-m262")
    assert primeiro.status_code == 201

    repetido = _domiciliar(client, auth, person, chave="vanessa-m262")
    assert repetido.status_code == 409
    assert _codigo(repetido) == "atendimento_repetido"

    total = db.execute(
        select(FinancialEntry).where(FinancialEntry.tipo == "receita")
    ).scalars().all()
    assert len(total) == 1


# ------------------------------------------------------- 3..5  Pastore


def test_3_pastore_recusa_receita_no_exame_e_so_cria_no_recebimento(
    client, auth, person, pastore, db
):
    partner, unit = pastore

    # 3a. o exame Pastore não aceita pagamento direto — nem tentando.
    recusa = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_pastore",
            "espirometria": {
                "data_exame": "2026-08-29", "status": "Realizado",
                "partner_id": partner.id, "partner_unit_id": unit.id,
            },
            "financeiro": {"espirometria": {"valor": "109.50", "status": "Pendente"}},
        },
        headers=auth("operacional"),
    )
    assert recusa.status_code == 422
    assert _codigo(recusa) == "pagamento_direto_pastore_proibido"

    # 3b. exame limpo → fechamento nasce SEM valor, e sem lançamento.
    _exame_pastore(client, auth, person, partner, unit, "2026-08-29")
    criado = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-08"},
        headers=auth("gestor"),
    )
    assert criado.status_code == 201, criado.text
    fechamento = criado.json()
    assert fechamento["valor_total"] is None
    assert fechamento["recebimento"] is None
    assert not db.execute(
        select(FinancialEntry).where(FinancialEntry.partner_settlement_id.isnot(None))
    ).scalars().all()
    db.rollback()  # libera o lock do SQLite antes da próxima escrita da API

    # 3c. só o recebimento confirmado cria a receita — agregada, não por exame.
    recebido = client.post(
        f"{API}/pastore/fechamentos/{fechamento['id']}/receber",
        json={
            "valor_confirmado": "109.50",
            "data_recebimento": "2026-09-05",
            "forma_pagamento": "Pix",
            "idempotency_key": "receb-m262",
        },
        headers=auth("gestor"),
    )
    assert recebido.status_code == 201, recebido.text
    entry = db.execute(
        select(FinancialEntry).where(FinancialEntry.partner_settlement_id.isnot(None))
    ).scalar_one()
    assert entry.tipo == "receita"
    assert entry.categoria == "Recebimento de parceiro"
    assert entry.spirometry_exam_id is None  # nunca ligado ao paciente


def test_4_nenhum_preco_vem_de_partner_unit(client, auth, person, pastore, db):
    """Não existe override de preço por unidade — nem para ser lido."""

    _partner, unit = pastore
    assert not hasattr(unit, "valor_por_exame")
    assert not any(
        "valor" in c.name or "preco" in c.name for c in PartnerUnit.__table__.columns
    )
    # E o contrato do endpoint diz por escrito que não infere.
    listagem = client.get(f"{API}/pastore/fechamentos", headers=auth("gestor"))
    assert listagem.json()["regra_valor"] == "Não inferido; exige confirmação do gestor."


def test_5_nenhum_preco_vem_de_partnership(db):
    """Partnership só tem campos de REPASSE — dinheiro que sai, não que entra."""

    from app.models import Partnership

    monetarias = {
        c.name for c in Partnership.__table__.columns
        if "valor" in c.name or "percentual" in c.name
    }
    assert monetarias == {"percentual_repasse", "valor_repasse_fixo"}
    # Nenhum caminho FINANCEIRO consome esses campos para decidir receita.
    # (`routers/partners.py` os lê e escreve — é o CRUD do cadastro, e é o
    # único lugar onde eles têm efeito.)
    servidor = pathlib.Path(__file__).resolve().parents[1] / "app"
    financeiros = [
        servidor / "routers" / "finance.py",
        servidor / "routers" / "pastore.py",
        servidor / "routers" / "attendances.py",
        servidor / "services" / "pastore.py",
    ]
    for caminho in financeiros:
        texto = caminho.read_text(encoding="utf-8")
        assert "valor_repasse_fixo" not in texto
        assert "percentual_repasse" not in texto


# ------------------------------------------------------- 6..7  regras do exame


def test_6_broncodilatador_nao_altera_valor_nenhum(client, auth, person, db):
    """Com e sem BD produzem a MESMA receita: o valor é o digitado."""

    for bd in (True, False):
        resp = client.post(
            f"{API}/atendimentos",
            json={
                "person_id": person["id"],
                "tipo": "espirometria_soprolife",
                "espirometria": {
                    "data_exame": "2026-08-28", "status": "Realizado",
                    "modalidade": "residencial", "broncodilatador": bd,
                },
                "financeiro": {"espirometria": {
                    "valor": "230.00", "status": "Pendente",
                    "data_competencia": "2026-08-28",
                }},
            },
            headers=auth("operacional"),
        )
        assert resp.status_code == 201, resp.text
        assert Decimal(resp.json()["lancamentos"][0]["valor"]) == Decimal("230.00")


def test_7_exame_cancelado_nao_entra_em_fechamento(client, auth, person, pastore, db):
    partner, unit = pastore
    _exame_pastore(client, auth, person, partner, unit, "2026-08-10", status="Cancelado")
    resp = client.post(
        f"{API}/pastore/fechamentos",
        json={"partner_unit_id": unit.id, "competencia": "2026-08"},
        headers=auth("gestor"),
    )
    assert resp.status_code == 422
    assert _codigo(resp) == "fechamento_sem_exames_elegiveis"
    assert not db.execute(select(PartnerSettlementItem)).scalars().all()


# --------------------------------------------- 8..9  independência do laudo


def test_8_financeiro_nao_depende_do_laudo(client, auth, person, db):
    """A receita nasce com o atendimento; nenhum laudo existe ainda."""

    from app.models import ReportDocument

    resp = _domiciliar(client, auth, person)
    assert resp.status_code == 201
    exam_id = resp.json()["espirometria"]["id"]
    assert db.execute(
        select(FinancialEntry).where(FinancialEntry.spirometry_exam_id == exam_id)
    ).scalars().all()
    assert not db.execute(select(ReportDocument)).scalars().all()


def test_9_financeiro_nao_depende_da_assinatura_digital(client, auth, person, db):
    """Nenhum caminho financeiro lê estado de assinatura."""

    servidor = pathlib.Path(__file__).resolve().parents[1] / "app"
    financeiro = [
        servidor / "routers" / "finance.py",
        servidor / "routers" / "pastore.py",
        servidor / "routers" / "attendances.py",
        servidor / "services" / "pastore.py",
    ]
    for caminho in financeiro:
        texto = caminho.read_text(encoding="utf-8")
        for proibido in ("signature", "assinatura", "signed_"):
            assert proibido not in texto.lower(), (
                f"{caminho.name} passou a depender de assinatura ({proibido})"
            )


# ------------------------------------------- 10..11  backfill e histórico


def test_10_backfill_executado_duas_vezes_nao_cria_nada_na_segunda(
    client, auth, person, pastore, db, reconciliador, users
):
    partner, unit = pastore
    for data in ("2026-08-15", "2026-08-18", "2026-08-22"):
        _exame_pastore(client, auth, person, partner, unit, data)

    dados = reconciliador.coletar(db)
    resultado = reconciliador.classificar(db, dados)
    assert len(resultado["c_pastore_aguardando"]) == 3
    assert resultado["acoes_possiveis"][0]["acao_prevista"] == "criar"
    assert resultado["acoes_possiveis"][0]["valor"] is None  # nunca infere preço

    # 1ª passada
    aplicados = reconciliador.aplicar(db, dados, resultado, users["gestor"])
    assert len(aplicados) == 1
    assert aplicados[0]["acao"] == "criado"
    assert len(aplicados[0]["exames"]) == 3
    assert aplicados[0]["valor_total"] is None

    settlements = db.execute(select(PartnerSettlement)).scalars().all()
    itens = db.execute(select(PartnerSettlementItem)).scalars().all()
    assert len(settlements) == 1 and len(itens) == 3
    # o vínculo é NÃO monetário: nenhum lançamento nasceu
    assert not db.execute(select(FinancialEntry)).scalars().all()

    # 2ª passada — idempotência
    dados2 = reconciliador.coletar(db)
    resultado2 = reconciliador.classificar(db, dados2)
    assert resultado2["c_pastore_aguardando"] == []
    assert reconciliador.aplicar(db, dados2, resultado2, users["gestor"]) == []
    assert len(db.execute(select(PartnerSettlement)).scalars().all()) == 1
    assert len(db.execute(select(PartnerSettlementItem)).scalars().all()) == 3


def test_11_reconciliacao_nunca_toca_receita_propria_existente(
    client, auth, person, pastore, db, reconciliador, users
):
    """O caso LAN-000017: exame próprio já lançado sai intacto do backfill."""

    partner, unit = pastore
    vanessa = _domiciliar(client, auth, person)
    assert vanessa.status_code == 201
    lan = vanessa.json()["lancamentos"][0]["public_code"]
    exam_id = vanessa.json()["espirometria"]["id"]
    _exame_pastore(client, auth, person, partner, unit, "2026-08-29")

    antes = db.execute(
        select(FinancialEntry).where(FinancialEntry.public_code == lan)
    ).scalar_one()
    assinatura_antes = (antes.valor, antes.status, antes.spirometry_exam_id,
                        antes.categoria, antes.data_competencia)

    dados = reconciliador.coletar(db)
    resultado = reconciliador.classificar(db, dados)
    # o exame próprio aparece como CORRETO, não como faltante
    assert any(l["esp"] == antes.descricao.split()[-1] for l in resultado["a_com_financeiro"])
    assert resultado["b_sem_financeiro"] == []
    assert resultado["e_duplicidades"] == []
    assert resultado["f_lan_sem_esp"] == []

    reconciliador.aplicar(db, dados, resultado, users["gestor"])
    db.expire_all()

    depois = db.execute(
        select(FinancialEntry).where(FinancialEntry.public_code == lan)
    ).scalar_one()
    assert (depois.valor, depois.status, depois.spirometry_exam_id,
            depois.categoria, depois.data_competencia) == assinatura_antes
    # e o exame próprio nunca entrou num fechamento de parceiro
    assert not db.execute(
        select(PartnerSettlementItem).where(
            PartnerSettlementItem.spirometry_exam_id == exam_id
        )
    ).scalars().all()


def test_12_backfill_deixa_trilha_de_auditoria(
    client, auth, person, pastore, db, reconciliador, users
):
    partner, unit = pastore
    _exame_pastore(client, auth, person, partner, unit, "2026-08-15")
    dados = reconciliador.coletar(db)
    resultado = reconciliador.classificar(db, dados)
    reconciliador.aplicar(db, dados, resultado, users["gestor"])

    log = db.execute(
        select(AuditLog).where(AuditLog.acao == "pastore.fechamento_criado")
    ).scalar_one()
    assert log.user_id == users["gestor"].id
    assert log.detalhes["motivo"] == "reconciliacao_financeiro_espirometria_m26_2"
    assert log.detalhes["sequencia"] == 1
    assert log.detalhes["total"] == 1


def test_13_exame_proprio_sem_receita_e_reportado_nunca_lancado(
    client, auth, person, db, reconciliador, users
):
    """Sem preço cadastrado, o script se recusa a inventar um."""

    resp = client.post(
        f"{API}/atendimentos",
        json={
            "person_id": person["id"],
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": "2026-08-28", "status": "Realizado",
                "modalidade": "residencial",
            },
        },
        headers=auth("operacional"),
    )
    assert resp.status_code == 201
    assert resp.json()["lancamentos"] == []

    dados = reconciliador.coletar(db)
    resultado = reconciliador.classificar(db, dados)
    assert len(resultado["b_sem_financeiro"]) == 1
    assert resultado["acoes_possiveis"] == []

    reconciliador.aplicar(db, dados, resultado, users["gestor"])
    assert not db.execute(select(FinancialEntry)).scalars().all()
