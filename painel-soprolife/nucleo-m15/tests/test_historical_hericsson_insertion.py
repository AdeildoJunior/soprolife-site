"""M18 — inserção histórica idempotente de Hericsson via script dedicado.

Cobre os itens 16-19 da bateria M18: idempotência, vínculo financeiro
correto, exclusão de Pastore do alvo, e ausência de valores fabricados.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import insert_historical_hericsson as script  # noqa: E402
from app.models import AuditLog, FinancialEntry, Followup, Person, SpirometryExam


def test_insercao_cria_pessoa_exame_lancamento_vinculados(db):
    rc = script.run(commit=True, db=db)
    assert rc == 0

    person = db.query(Person).filter(Person.nome_normalizado == "hericsson").one()
    exam = db.query(SpirometryExam).filter(SpirometryExam.person_id == person.id).one()
    entry = db.query(FinancialEntry).filter(FinancialEntry.spirometry_exam_id == exam.id).one()

    assert exam.data_exame == date(2026, 7, 15)
    assert exam.local_atendimento == "Coworking"
    assert exam.partner_id is None  # nunca Pastore
    assert entry.valor == Decimal("220.00")
    assert entry.status == "Recebido"
    assert entry.forma_pagamento == "Pix"
    assert entry.spirometry_exam_id == exam.id


def test_insercao_cria_followup_seis_meses(db):
    script.run(commit=True, db=db)
    exam = db.query(SpirometryExam).filter(
        SpirometryExam.legacy_id == script.LEGACY_ID
    ).one()
    fup = db.query(Followup).filter(Followup.origem_id == exam.id).one()
    assert fup.due_date == date(2027, 1, 15)
    assert fup.status == "pendente"


def test_insercao_e_idempotente_segunda_execucao_aborta(db):
    rc1 = script.run(commit=True, db=db)
    assert rc1 == 0
    rc2 = script.run(commit=True, db=db)
    assert rc2 == 1  # aborta, não cria duplicata

    assert db.query(Person).filter(Person.nome_normalizado == "hericsson").count() == 1
    assert db.query(SpirometryExam).filter(
        SpirometryExam.legacy_id == script.LEGACY_ID
    ).count() == 1
    assert db.query(FinancialEntry).filter(
        FinancialEntry.legacy_id == script.LEGACY_ID
    ).count() == 1


def test_dry_run_nao_persiste_nada(db):
    rc = script.run(commit=False, db=db)
    assert rc == 0
    assert db.query(Person).filter(Person.nome_normalizado == "hericsson").count() == 0
    assert db.query(SpirometryExam).count() == 0
    assert db.query(FinancialEntry).count() == 0


def test_aborta_se_pessoa_ja_existe(db):
    from app.ids import allocate_public_code
    from app.normalize import normalize_name

    existing = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo="Hericsson",
        nome_normalizado=normalize_name("Hericsson"),
        status="ativo",
    )
    db.add(existing)
    db.commit()

    rc = script.run(commit=True, db=db)
    assert rc == 1
    assert db.query(Person).filter(Person.nome_normalizado == "hericsson").count() == 1


def test_aborta_se_ja_existe_lancamento_220_sem_exame(db):
    from app.ids import allocate_public_code

    orphan = FinancialEntry(
        public_code=allocate_public_code(db, "financial_entries"),
        tipo="receita",
        categoria="Espirometria",
        valor=Decimal("220.00"),
        moeda="BRL",
        status="Recebido",
    )
    db.add(orphan)
    db.commit()

    rc = script.run(commit=True, db=db)
    assert rc == 1
    assert db.query(FinancialEntry).count() == 1  # só o órfão, nada novo criado


def test_auditoria_registrada_sem_pii(db):
    script.run(commit=True, db=db)
    rows = db.query(AuditLog).all()
    acoes = {r.acao for r in rows}
    assert {"pessoa.criada", "espirometria.criada", "lancamento.criado"} <= acoes
    for row in rows:
        if row.detalhes:
            # só chaves permitidas sobrevivem (allowlist em app/audit.py)
            assert "nome_completo" not in row.detalhes
            assert "telefone" not in row.detalhes
            assert "valor" not in row.detalhes or isinstance(row.detalhes.get("valor"), str)


def test_valor_nao_e_fabricado_bate_com_fato_operador(db):
    """R$220,00 é o único valor evidenciado (print de banco) — não deve
    haver rateio/média aplicada a este registro."""
    script.run(commit=True, db=db)
    entry = db.query(FinancialEntry).filter(
        FinancialEntry.legacy_id == script.LEGACY_ID
    ).one()
    assert entry.valor == Decimal("220.00")
    assert entry.valor != Decimal("236.80")  # valor médio antigo, nunca usado aqui
