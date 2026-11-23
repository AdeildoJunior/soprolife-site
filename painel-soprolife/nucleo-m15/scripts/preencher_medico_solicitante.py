"""Preenche o médico solicitante em exames aguardando laudo — fail-closed.

Cada `--primeiro-nome` precisa resolver para EXATAMENTE um exame aguardando
laudo, e todos precisam ter um sobrenome em comum. Qualquer ambiguidade,
ausência ou valor divergente já gravado aborta sem escrever nada. Não lauda,
não assina e não toca status. Sem `--aplicar`, só mostra o que faria.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_engine
from app.models import Person, ReportDocument, SpirometryExam, User
from app.normalize import normalize_name
from app.schemas import MedicoSolicitante

AGUARDANDO_LAUDO = ("atribuido", "em_elaboracao")
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


def candidatos(db: Session, primeiro_nome: str):
    linhas = db.execute(
        select(ReportDocument, SpirometryExam, Person)
        .join(SpirometryExam, SpirometryExam.id == ReportDocument.spirometry_exam_id)
        .join(Person, Person.id == SpirometryExam.person_id)
        .where(
            ReportDocument.status.in_(AGUARDANDO_LAUDO),
            ReportDocument.devolucao_cancelada_em.is_(None),
            ReportDocument.superseded_by_id.is_(None),
            SpirometryExam.encerramento_motivo.is_(None),
            Person.arquivado.is_(False),
        )
        .with_for_update(of=(ReportDocument, SpirometryExam))
    ).all()
    alvo = normalize_name(primeiro_nome)
    return [
        linha for linha in linhas
        if normalize_name(linha[2].nome_completo).split()[:1] == [alvo]
    ]


def resolver(db: Session, nomes: list[str]):
    escolhidos, sobrenomes = [], None
    for nome in nomes:
        achados = candidatos(db, nome)
        if len(achados) != 1:
            for doc, exam, person in achados:
                print(f"  ? {exam.public_code} {doc.public_code} {person.nome_completo}")
            raise SystemExit(f"{nome}: {len(achados)} exames aguardando laudo; nada alterado.")
        escolhidos.append(achados[0])
        partes = set(normalize_name(achados[0][2].nome_completo).split()[1:]) - PARTICULAS
        sobrenomes = partes if sobrenomes is None else sobrenomes & partes
    if len({e.id for _, e, _ in escolhidos}) != len(nomes) or not sobrenomes:
        raise SystemExit("Sem sobrenome em comum entre os pacientes; nada alterado.")
    return escolhidos


def preencher(db: Session, nomes, medico: MedicoSolicitante, operador: User | None, aplicar: bool):
    escolhidos = resolver(db, nomes)
    novo = (medico.medico_solicitante_nome, medico.medico_solicitante_crm)
    for doc, exam, person in escolhidos:
        atual = (exam.medico_solicitante_nome, exam.medico_solicitante_crm)
        print(f"  {exam.public_code} {doc.public_code} [{doc.status}] "
              f"{exam.data_exame} {person.nome_completo} — atual: {atual[0] or 'vazio'}")
        if atual not in {(None, None), novo}:
            raise SystemExit(f"{exam.public_code} já tem outro solicitante; nada alterado.")
    if not aplicar:
        db.rollback()
        print("Simulação; nada gravado.")
        return
    for _, exam, _ in escolhidos:
        if (exam.medico_solicitante_nome, exam.medico_solicitante_crm) == novo:
            continue
        exam.medico_solicitante_nome, exam.medico_solicitante_crm = novo
        audit(db, "exame_medico_solicitante_atualizado", "spirometry_exams", exam.id,
              operador.id, None, {
                  "exam_code": exam.public_code, "modo": "script_operador",
                  "campos": ["medico_solicitante_nome", "medico_solicitante_crm"]})
    ids = [exam.id for _, exam, _ in escolhidos]
    status = {doc.id: doc.status for doc, _, _ in escolhidos}
    db.commit()
    for exam in db.scalars(select(SpirometryExam).where(SpirometryExam.id.in_(ids))):
        print(f"  OK {exam.public_code}: {exam.medico_solicitante_nome} — "
              f"CRM {exam.medico_solicitante_crm}")
    for doc_id, antes in status.items():
        print(f"  {db.get(ReportDocument, doc_id).public_code}: status {antes} preservado")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--primeiro-nome", action="append", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--crm", required=True)
    p.add_argument("--operador", help="e-mail do usuário ativo responsável")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args()
    medico = MedicoSolicitante(medico_solicitante_nome=a.nome, medico_solicitante_crm=a.crm)
    with Session(get_engine()) as db:
        operador = None
        if a.aplicar:
            operador = db.scalar(select(User).where(User.email == (a.operador or ""), User.ativo.is_(True)))
            if operador is None:
                raise SystemExit("--operador precisa ser um usuário ativo.")
        preencher(db, a.primeiro_nome, medico, operador, a.aplicar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
