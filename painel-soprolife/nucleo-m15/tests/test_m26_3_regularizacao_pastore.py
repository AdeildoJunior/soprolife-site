"""M26.3 — o script de regularização, provado contra a FORMA de produção.

A réplica reproduz a topologia auditada em 31/08/2026 e nada mais: 15 receitas
próprias somando R$ 3.494,79, três fechamentos Pastore (2, 3 e 14 exames), os
dois primeiros já valorados e nenhum recibo. Nenhum dado real, nenhum nome.

O que estes testes cobram, além do resultado: que o script **se recuse** a
escrever quando a realidade não é a que o gestor descreveu.
"""

import importlib.util
import pathlib
import sys
from datetime import date
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
    Partnership,
    Person,
    SpirometryExam,
)
from app.security import ensure_roles_exist, get_role, hash_password
from app.models import User
from app.snapshots import build_financeiro_summary

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "regularizar_recebimentos_pastore.py"
)


def _carregar():
    nucleo = str(pathlib.Path(__file__).resolve().parents[1])
    if nucleo not in sys.path:
        sys.path.insert(0, nucleo)
    spec = importlib.util.spec_from_file_location("regularizar_m26_3", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def script():
    return _carregar()


@pytest.fixture()
def gestor(db):
    ensure_roles_exist(db)
    user = User(email="gestor-m263@teste.local", nome="Gestor Teste",
                password_hash=hash_password("senha-teste-123"))
    user.roles.append(get_role(db, "gestor"))
    db.add(user)
    db.commit()
    return user


@pytest.fixture()
def producao(db):
    """A FORMA de produção em 31/08/2026, antes da regularização."""
    pessoa = Person(public_code="PES-M263", nome_completo="Paciente 001",
                    nome_normalizado="paciente 001")
    db.add(pessoa)
    db.flush()
    parceiro = Partner(public_code="CLI-M263P", nome="Pastore", tipo="clinica",
                       status="ativa", arquivado=False)
    db.add(parceiro)
    db.flush()
    unidade = PartnerUnit(public_code="UNI-M263P", partner_id=parceiro.id,
                          nome="Pastore Ipanema", ativo=True)
    db.add(unidade)
    db.add(Partnership(public_code="PAR-M263P", partner_id=parceiro.id,
                       status="em_negociacao", modelo_repasse="indefinido"))
    db.flush()

    contador = {"n": 0}

    def exame(dia, parceria=False):
        contador["n"] += 1
        e = SpirometryExam(
            public_code=f"ESP-M{contador['n']:05d}", person_id=pessoa.id,
            status="Realizado", data_exame=dia,
            modalidade="clinica_parceira" if parceria else "residencial",
            partner_id=parceiro.id if parceria else None,
            partner_unit_id=unidade.id if parceria else None,
        )
        db.add(e)
        db.flush()
        return e

    # 15 receitas próprias — os mesmos valores e datas auditados.
    proprios = (
        [("220.00", date(2026, 7, 10)), ("219.00", date(2026, 7, 2)),
         ("220.00", date(2026, 7, 15))]
        + [("238.58", None)] * 9 + [("238.57", None)]
        + [("220.00", date(2026, 8, 26)), ("230.00", date(2026, 8, 28))]
    )
    for i, (valor, competencia) in enumerate(proprios, start=1):
        db.add(FinancialEntry(
            public_code=f"LAN-M{i:05d}", tipo="receita", categoria="Espirometria",
            descricao="Espirometria", valor=Decimal(valor), status="Recebido",
            data_competencia=competencia,
            spirometry_exam_id=exame(competencia or date(2026, 6, 1)).id,
        ))

    def fechamento(competencia, sequencia, quantidade, valor):
        s = PartnerSettlement(
            partner_id=parceiro.id, partner_unit_id=unidade.id,
            competencia=competencia, periodo_inicio=competencia,
            valor_total=Decimal(valor) if valor else None,
            status="a_receber" if valor else "incluido",
            sequencia=sequencia,
        )
        db.add(s)
        db.flush()
        for _ in range(quantidade):
            db.add(PartnerSettlementItem(
                settlement_id=s.id,
                spirometry_exam_id=exame(competencia, parceria=True).id,
            ))
        db.flush()
        return s

    fechamento(date(2026, 7, 1), 1, 2, "219.00")
    fechamento(date(2026, 8, 1), 1, 3, "328.50")
    fechamento(date(2026, 8, 1), 2, 14, None)
    db.commit()
    return parceiro, unidade


def test_1_dry_run_confere_tudo_e_nao_escreve(db, script, producao):
    dados = script.coletar(db)
    assert dados["receita_propria"] == Decimal("3494.79")
    assert dados["receita_parceria"] == Decimal("0.00")
    assert script.conferir(db, dados) == []

    plano = script.planejar(db, dados)
    assert plano["cadastrar_regra"] is True
    assert [r["valor"] for r in plano["recibos_a_criar"]] == [
        Decimal("219.00"), Decimal("328.50"), Decimal("1533.00")
    ]
    # Dry-run não tocou em nada.
    assert db.execute(select(FinancialEntry).where(
        FinancialEntry.partner_settlement_id.isnot(None))).scalars().all() == []
    assert db.execute(select(Partnership)).scalar_one().modelo_recebimento == "indefinido"


def test_2_apply_produz_exatamente_os_valores_esperados(db, script, producao, gestor):
    dados = script.coletar(db)
    feito = script.aplicar(db, dados, script.planejar(db, dados), gestor)

    assert feito["regra"]["valor_por_exame"] == "109.50"
    assert [r["valor"] for r in feito["recibos"]] == ["219.00", "328.50", "1533.00"]

    parceria = db.execute(select(Partnership)).scalar_one()
    assert parceria.modelo_recebimento == "valor_por_exame"
    assert parceria.valor_recebido_por_exame == Decimal("109.50")
    assert parceria.vigencia_inicio == date(2026, 7, 1)
    # O repasse (dinheiro que SAI) continua intocado.
    assert parceria.valor_repasse_fixo is None
    assert parceria.percentual_repasse is None

    recibos = db.execute(select(FinancialEntry).where(
        FinancialEntry.partner_settlement_id.isnot(None))).scalars().all()
    assert len(recibos) == 3
    assert sum(r.valor for r in recibos) == Decimal("2080.50")
    for r in recibos:
        assert r.status == "Recebido"
        assert r.forma_pagamento == "Outro"      # nunca "Pix" inventado
        assert r.data_recebimento == date(2026, 8, 31)
        assert r.spirometry_exam_id is None
        assert r.origem_preco == "Parceria"

    for s in db.execute(select(PartnerSettlement)).scalars().all():
        assert s.status == "recebido"
        assert "data bancária original não informada" in s.observacao


def test_3_totais_finais_no_painel(db, script, producao, gestor):
    dados = script.coletar(db)
    script.aplicar(db, dados, script.planejar(db, dados), gestor)

    resumo = build_financeiro_summary(db)
    assert resumo["totais"]["receita_recebida"] == 5575.29
    assert resumo["totais"]["a_receber_total"] == 0
    origens = {o["origem"]: o["valor"] for o in resumo["por_origem"]}
    assert origens == {"SoproLife": 3494.79, "Pastore": 2080.50}
    assert resumo["exames_reconhecidos"] == 34


def test_4_receitas_proprias_ficam_byte_a_byte_iguais(db, script, producao, gestor):
    antes = {
        e.public_code: (e.valor, e.status, e.categoria, e.data_competencia,
                        e.spirometry_exam_id, e.forma_pagamento)
        for e in db.execute(select(FinancialEntry)).scalars().all()
    }
    dados = script.coletar(db)
    script.aplicar(db, dados, script.planejar(db, dados), gestor)

    depois = {
        e.public_code: (e.valor, e.status, e.categoria, e.data_competencia,
                        e.spirometry_exam_id, e.forma_pagamento)
        for e in db.execute(select(FinancialEntry)).scalars().all()
        if e.partner_settlement_id is None
    }
    assert depois == antes
    # A de R$ 230,00, que o pedido nomeia, entre elas.
    assert any(v[0] == Decimal("230.00") for v in depois.values())


def test_5_segunda_execucao_nao_duplica_nada(db, script, producao, gestor):
    dados = script.coletar(db)
    script.aplicar(db, dados, script.planejar(db, dados), gestor)

    dados2 = script.coletar(db)
    assert script.conferir(db, dados2) == []
    plano2 = script.planejar(db, dados2)
    assert plano2["cadastrar_regra"] is False
    assert plano2["recibos_a_criar"] == []

    feito2 = script.aplicar(db, dados2, plano2, gestor)
    assert feito2 == {"regra": None, "recibos": []}
    assert len(db.execute(select(FinancialEntry).where(
        FinancialEntry.partner_settlement_id.isnot(None))).scalars().all()) == 3
    assert build_financeiro_summary(db)["totais"]["receita_recebida"] == 5575.29


def test_6_trilha_registra_o_porque_sem_afirmar_credito_bancario(
    db, script, producao, gestor
):
    dados = script.coletar(db)
    script.aplicar(db, dados, script.planejar(db, dados), gestor)

    recebidos = db.execute(select(AuditLog).where(
        AuditLog.acao == "pastore.fechamento_recebido")).scalars().all()
    assert len(recebidos) == 3
    for log in recebidos:
        assert log.user_id == gestor.id
        # `audit()` descarta em silêncio qualquer chave fora de ALLOWED_KEYS.
        # Estas asserções leem o log DEPOIS da sanitização de propósito: uma
        # trilha que some é pior do que uma que nunca foi escrita.
        assert log.detalhes["motivo"].startswith("regularizacao_pastore_m26_3")
        assert "confirmado pelo gestor em 31/08/2026" in log.detalhes["motivo"]
        assert "data bancária original não informada" in log.detalhes["motivo"]
        assert "109.50" in log.detalhes["decisao"]
        assert "forma_pagamento=Outro" in log.detalhes["decisao"]
        assert "data_recebimento=2026-08-31" in log.detalhes["decisao"]
    assert sorted(l.detalhes["total"] for l in recebidos) == [2, 3, 14]

    regra = db.execute(select(AuditLog).where(
        AuditLog.acao == "parceria.regra_recebimento_cadastrada")).scalar_one()
    assert "109.50 por exame" in regra.detalhes["decisao"]
    assert "2026-07-01" in regra.detalhes["decisao"]
    assert regra.detalhes["campos"] == [
        "modelo_recebimento", "valor_recebido_por_exame", "vigencia_inicio"
    ]


# ─────────────────────────────── fail-closed: divergiu, não escreve


def test_7_contagem_de_exames_diferente_bloqueia(db, script, producao):
    """Um exame a mais no complementar muda o valor — e o script para."""
    parceiro, unidade = producao
    complementar = db.execute(select(PartnerSettlement).where(
        PartnerSettlement.sequencia == 2)).scalar_one()
    extra = SpirometryExam(
        public_code="ESP-M99999",
        person_id=db.execute(select(Person)).scalars().first().id,
        status="Realizado", data_exame=date(2026, 8, 27),
        modalidade="clinica_parceira",
        partner_id=parceiro.id, partner_unit_id=unidade.id,
    )
    db.add(extra)
    db.flush()
    db.add(PartnerSettlementItem(settlement_id=complementar.id,
                                 spirometry_exam_id=extra.id))
    db.commit()

    problemas = script.conferir(db, script.coletar(db))
    assert any("15 exame(s), esperava 14" in p for p in problemas)


def test_8_receita_propria_diferente_bloqueia(db, script, producao):
    entrada = db.execute(select(FinancialEntry).where(
        FinancialEntry.public_code == "LAN-M00015")).scalar_one()
    entrada.valor = Decimal("999.00")
    db.commit()

    problemas = script.conferir(db, script.coletar(db))
    assert any("receita SoproLife direta" in p for p in problemas)
    assert any("total somaria" in p for p in problemas)


def test_9_valor_ja_gravado_divergente_bloqueia(db, script, producao):
    julho = db.execute(select(PartnerSettlement).where(
        PartnerSettlement.competencia == date(2026, 7, 1))).scalar_one()
    julho.valor_total = Decimal("200.00")
    db.commit()

    problemas = script.conferir(db, script.coletar(db))
    assert any("diverge do confirmado" in p for p in problemas)


def test_10_fechamento_inesperado_bloqueia(db, script, producao):
    parceiro, unidade = producao
    db.add(PartnerSettlement(
        partner_id=parceiro.id, partner_unit_id=unidade.id,
        competencia=date(2026, 9, 1), periodo_inicio=date(2026, 9, 1),
        status="incluido", sequencia=1,
    ))
    db.commit()

    problemas = script.conferir(db, script.coletar(db))
    assert any("fora do combinado" in p and "2026-09 #1" in p for p in problemas)
