#!/usr/bin/env python3
"""M25.12 — espirometria FICTÍCIA ainda sem laudo, para exercitar a recepção.

O cenário da M25.3 (`seed_m25_3_laudo_demo.py`) já entrega o laudo pronto na
fila da médica. O que faltava era um exame **sem laudo nenhum**: é ele que
permite exercitar, de ponta a ponta e pela interface real, o passo que
falhou em produção —

    localizar o exame pelo código institucional → anexar o PDF técnico →
    atribuir à médica

Reaproveita o paciente, a clínica e a unidade já criados pelo seed da M25.3;
se eles não existirem, mande rodar aquele script antes.

Segurança
---------
- Fail-closed: só roda com `M15_ENV=dev`, banco local SQLite e `--confirmar`.
- Todos os dados são fictícios. Nenhum dado real de paciente.
- Idempotente: reexecutar devolve o exame sem laudo que já existir.

Uso:
    cd painel-soprolife/nucleo-m15
    .venv/bin/python scripts/seed_m25_12_exame_sem_laudo.py --confirmar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    Person,
    ReportDocument,
    SpirometryExam,
    User,
)
from app.security import issue_token  # noqa: E402

PACIENTE_NOME = "João da Silva Teste"
OPERACIONAL_EMAIL = "operacional.teste@soprolife.local"
UNIDADE_NOME = "Unidade Ipanema"
DATA_EXAME = "2026-08-08"


def _guarda_ambiente(settings) -> None:
    problemas = []
    if settings.env != "dev":
        problemas.append(f"M15_ENV={settings.env!r} (exigido 'dev')")
    if not str(settings.database_url).startswith("sqlite"):
        problemas.append("banco não é SQLite local")
    if problemas:
        raise SystemExit(
            "Ambiente recusado para seed:\n  - " + "\n  - ".join(problemas)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="confirma que este é o ambiente LOCAL de teste",
    )
    args = parser.parse_args()

    settings = get_settings()
    _guarda_ambiente(settings)
    if not args.confirmar:
        raise SystemExit("Rode novamente com --confirmar.")

    Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    print("\n== M25.12 — espirometria fictícia SEM laudo ==\n")

    with Session() as db:
        person = db.execute(
            select(Person).where(Person.nome_completo == PACIENTE_NOME)
        ).scalar_one_or_none()
        if person is None:
            raise SystemExit(
                "Paciente fictício ausente. Rode antes:\n"
                "  .venv/bin/python scripts/seed_m25_3_laudo_demo.py --confirmar"
            )
        operador = db.execute(
            select(User).where(User.email == OPERACIONAL_EMAIL)
        ).scalar_one_or_none()
        if operador is None:
            raise SystemExit(
                "Usuário operacional de teste ausente. Rode antes o seed da M25.3."
            )

        # Exame já existente sem laudo? Então não cria outro.
        com_laudo = {
            row for row in db.execute(
                select(ReportDocument.spirometry_exam_id)
            ).scalars()
        }
        livre = db.execute(
            select(SpirometryExam)
            .where(SpirometryExam.person_id == person.id)
            .order_by(SpirometryExam.created_at.desc())
        ).scalars()
        for exame in livre:
            if exame.id not in com_laudo:
                print(f"  = exame sem laudo reaproveitado: {exame.public_code}")
                print(f"\n  Use este código na recepção: {exame.public_code}\n")
                return 0

        person_id = person.id
        token = issue_token(operador.id, operador.password_hash)
        db.commit()

    client = TestClient(create_app())
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post(
        "/api/v1/atendimentos",
        json={
            "person_id": person_id,
            "tipo": "espirometria_soprolife",
            "espirometria": {
                "data_exame": DATA_EXAME,
                "status": "Realizado",
                # Pré E pós-broncodilatador: é o que habilita os cinco
                # complementos (RBD+, RBD−, REV completa/parcial, BD não
                # realizado) na tela da médica.
                "broncodilatador": True,
                "modalidade": "clinica_parceira",
                "local_atendimento": UNIDADE_NOME,
            },
        },
        headers=headers,
    )
    if resp.status_code != 201:
        raise SystemExit(f"falha ao criar a espirometria fictícia: {resp.text}")

    with Session() as db:
        exame = db.execute(
            select(SpirometryExam)
            .where(SpirometryExam.person_id == person_id)
            .order_by(SpirometryExam.created_at.desc())
        ).scalars().first()
        codigo = exame.public_code

    print(f"  + espirometria fictícia criada: {codigo} (com pós-BD, sem laudo)")
    print(f"\n  Use este código na recepção: {codigo}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
