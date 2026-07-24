#!/usr/bin/env python3
"""M18 — inserção histórica única e idempotente de Hericsson.

Reconciliação bancária autorizada pelo operador: um exame de espirometria
extra-Pastore (15/07/2026, coworking) ocorrido após o snapshot imutável de
migração M15.8, com recebimento confirmado via Pix (print de banco
posterior). Reusa exatamente a mesma camada de serviço dos routers da API
(allocate_public_code, idempotent_create, audit, sincronização de
follow-up) — não é SQL cru nem um caminho de escrita paralelo.

Idempotente: pode ser executado múltiplas vezes com segurança. Falha
fechado (aborta sem escrever nada) se já existir qualquer pessoa, exame ou
lançamento que possa já representar este registro.

Uso:
  M15_DATABASE_URL=... .venv/bin/python scripts/insert_historical_hericsson.py --dry-run
  M15_DATABASE_URL=... .venv/bin/python scripts/insert_historical_hericsson.py --commit
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.audit import audit
from app.dates import parse_incomplete_date
from app.db import get_sessionmaker
from app.ids import allocate_public_code
from app.models import FinancialEntry, Person, SpirometryExam
from app.normalize import normalize_name
from app.routers.operations import _apply_date, _sync_exam_followup
from app.services.idempotency import idempotent_create

PATIENT_NAME = "Hericsson"
EXAM_DATE_RAW = "2026-07-15"
EXAM_DATE = date(2026, 7, 15)
LOCAL_ATENDIMENTO = "Coworking"
MODALIDADE = "cowork"
VALOR = Decimal("220.00")
LEGACY_SOURCE = "m18_reconciliacao_bancaria"
LEGACY_ID = "hericsson-2026-07-15"
IDEMPOTENCY_KEY_EXAM = "m18-historico-hericsson-2026-07-15-exame"
IDEMPOTENCY_KEY_FIN = "m18-historico-hericsson-2026-07-15-financeiro"


class AbortInsertion(Exception):
    pass


def _mask(name: str) -> str:
    return (name[:3] + "***") if name else "***"


def duplicate_check(db) -> None:
    """Falha fechado: aborta se qualquer evidência de duplicidade existir."""
    nome_norm = normalize_name(PATIENT_NAME)
    existing_person = db.execute(
        select(Person).where(Person.nome_normalizado == nome_norm)
    ).scalars().first()
    if existing_person:
        raise AbortInsertion(
            f"Já existe pessoa com nome normalizado igual "
            f"(public_code={existing_person.public_code}). Abortando — "
            f"resolver identidade manualmente, nunca fundir automaticamente."
        )

    existing_exam = db.execute(
        select(SpirometryExam).where(
            SpirometryExam.data_exame == EXAM_DATE,
            SpirometryExam.local_atendimento.ilike("%coworking%"),
        )
    ).scalars().first()
    if existing_exam:
        raise AbortInsertion(
            f"Já existe exame em {EXAM_DATE_RAW} com local 'coworking' "
            f"(public_code={existing_exam.public_code}). Abortando."
        )

    existing_key_exam = db.execute(
        select(SpirometryExam).where(SpirometryExam.idempotency_key == IDEMPOTENCY_KEY_EXAM)
    ).scalars().first()
    existing_key_fin = db.execute(
        select(FinancialEntry).where(FinancialEntry.idempotency_key == IDEMPOTENCY_KEY_FIN)
    ).scalars().first()
    if existing_key_exam or existing_key_fin:
        raise AbortInsertion(
            "Chave de idempotência do M18 já usada — este script já rodou "
            "antes com sucesso. Nada a fazer (idempotente)."
        )

    unlinked_220 = db.execute(
        select(FinancialEntry).where(
            FinancialEntry.valor == VALOR,
            FinancialEntry.spirometry_exam_id.is_(None),
        )
    ).scalars().first()
    if unlinked_220:
        raise AbortInsertion(
            f"Já existe um lançamento de R$220,00 sem exame vinculado "
            f"(public_code={unlinked_220.public_code}) que pode já "
            f"representar Hericsson. Abortando para resolução manual."
        )


def run(commit: bool, db=None) -> int:
    owns_session = db is None
    if owns_session:
        db = get_sessionmaker()()
    try:
        duplicate_check(db)

        # 1) pessoa
        person = Person(
            public_code=allocate_public_code(db, "people"),
            nome_completo=PATIENT_NAME,
            nome_normalizado=normalize_name(PATIENT_NAME),
            status="ativo",
            observacao=(
                "Registro histórico inserido via reconciliação bancária "
                "autorizada pelo operador (exame pós-snapshot M15.8)."
            ),
        )
        db.add(person)
        db.flush()
        audit(
            db, "pessoa.criada", "people", person.id, None, "script:m18-historico-hericsson",
            {"public_code": person.public_code, "marcador": "m18_historico_hericsson"},
        )

        # 2) exame de espirometria (idempotente)
        def exam_factory(key, fingerprint):
            exam = SpirometryExam(
                public_code=allocate_public_code(db, "spirometry_exams"),
                person_id=person.id,
                modalidade=MODALIDADE,
                local_atendimento=LOCAL_ATENDIMENTO,
                partner_id=None,
                partner_unit_id=None,
                status="Realizado",
                origem="Conciliação bancária — registro histórico pós-snapshot M15.8",
                idempotency_key=key,
                idempotency_fingerprint=fingerprint,
                legacy_source=LEGACY_SOURCE,
                legacy_id=LEGACY_ID,
            )
            _apply_date(exam, "data_exame", EXAM_DATE_RAW)
            db.add(exam)
            db.flush()
            return exam

        exam_payload = {
            "person_id": person.id,
            "data_exame": EXAM_DATE_RAW,
            "modalidade": MODALIDADE,
            "local_atendimento": LOCAL_ATENDIMENTO,
            "status": "Realizado",
            "marker": "m18_historico_hericsson",
        }
        exam, exam_existed = idempotent_create(
            db, SpirometryExam, IDEMPOTENCY_KEY_EXAM, exam_payload, exam_factory
        )
        if exam_existed:
            raise AbortInsertion("Exame já existia sob a mesma chave de idempotência.")

        fup_info = _sync_exam_followup(db, person, exam)
        audit(
            db, "espirometria.criada", "spirometry_exams", exam.id, None,
            "script:m18-historico-hericsson",
            {"public_code": exam.public_code, "status": exam.status, "followup": str(fup_info.get("motivo"))},
        )

        # 3) lançamento financeiro (idempotente, vinculado ao exame)
        def fin_factory(key, fingerprint):
            entry = FinancialEntry(
                public_code=allocate_public_code(db, "financial_entries"),
                tipo="receita",
                categoria="Espirometria",
                valor=VALOR,
                moeda="BRL",
                status="Recebido",
                forma_pagamento="Pix",
                origem_preco="Tabela",
                spirometry_exam_id=exam.id,
                idempotency_key=key,
                idempotency_fingerprint=fingerprint,
                legacy_source=LEGACY_SOURCE,
                legacy_id=LEGACY_ID,
            )
            nd = parse_incomplete_date(EXAM_DATE_RAW)
            entry.data_competencia = nd.value
            entry.data_competencia_original = nd.original or None
            entry.data_competencia_precisao = nd.precision
            entry.data_competencia_dia_assumido = nd.day_assumed
            db.add(entry)
            db.flush()
            return entry

        fin_payload = {
            "tipo": "receita",
            "categoria": "Espirometria",
            "valor": str(VALOR),
            "spirometry_exam_id": exam.id,
            "marker": "m18_historico_hericsson",
        }
        entry, fin_existed = idempotent_create(
            db, FinancialEntry, IDEMPOTENCY_KEY_FIN, fin_payload, fin_factory
        )
        if fin_existed:
            raise AbortInsertion("Lançamento já existia sob a mesma chave de idempotência.")

        audit(
            db, "lancamento.criado", "financial_entries", entry.id, None,
            "script:m18-historico-hericsson",
            {"public_code": entry.public_code, "tipo": entry.tipo, "status": entry.status},
        )

        print(f"OK (dry-run={not commit}): pessoa={person.public_code} "
              f"exame={exam.public_code} lancamento={entry.public_code} "
              f"valor={VALOR} data={EXAM_DATE_RAW} nome_mascarado={_mask(PATIENT_NAME)}")

        if commit:
            db.commit()
            print("COMMITADO.")
        else:
            db.rollback()
            print("DRY-RUN — nada foi persistido (rollback).")
        return 0
    except AbortInsertion as exc:
        db.rollback()
        print(f"ABORTADO: {exc}", file=sys.stderr)
        return 1
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Simula sem persistir (rollback no final).")
    group.add_argument("--commit", action="store_true", help="Persiste de fato (commit).")
    args = parser.parse_args()
    sys.exit(run(commit=args.commit))
