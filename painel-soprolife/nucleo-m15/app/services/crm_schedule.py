"""Projeção de acompanhamento do CRM; nunca altera objetos ORM ou o histórico."""
from datetime import date, timedelta
from types import SimpleNamespace

from ..dates import add_months


def next_contact_date(base: date) -> date:
    six_months = add_months(base, 6)
    return six_months + timedelta(days=(-six_months.weekday()) % 7)


def relevant_exam(exam, today):
    return (
        exam.data_exame is not None and exam.data_exame <= today
        and exam.status in {"Realizado", "Laudo Liberado", "Liberado"}
        and not exam.data_exame_dia_assumido
        and exam.data_exame_precisao in (None, "dia")
    )


def project_followups(followups, exams, today):
    """Datas manuais/importadas ficam intactas; só o automático tem nova regra.

    Um reteste realizado é atividade relevante mesmo sem cobrança. O CRM não
    lê preço, laudo ou nota fiscal para decidir a data de relacionamento.
    A origem antiga permanece rastreável, mas um automático superado não cria
    uma segunda tarefa concorrente com o exame mais recente.
    """
    by_id = {exam.id: exam for exam in exams}
    latest = {}
    for exam in exams:
        if relevant_exam(exam, today):
            previous = latest.get(exam.person_id)
            if previous is None or (exam.data_exame, exam.public_code) > (
                previous.data_exame, previous.public_code
            ):
                latest[exam.person_id] = exam
    candidates = {}
    for fup in followups:
        source = by_id.get(fup.origem_id) if fup.origem_entidade == "spirometry_exams" else None
        if (fup.status == "pendente" and fup.tipo == "pos_exame"
                and source is not None and source.person_id == fup.person_id):
            candidates.setdefault(fup.person_id, []).append(fup)
    chosen = {}
    for person_id, rows in candidates.items():
        base = latest.get(person_id)
        if base:
            chosen[person_id] = max(rows, key=lambda f: (
                f.origem_id == base.id,
                by_id[f.origem_id].data_exame or date.min,
                f.created_at.isoformat() if f.created_at else "",
                f.id,
            )).id
    manual_pending = {
        f.person_id for f in followups
        if f.status == "pendente" and f.due_date_manual and f.due_date
    }
    out = []
    for fup in followups:
        # Cópia de valores simples: atribuições abaixo jamais entram na sessão.
        view = SimpleNamespace(**{c.key: getattr(fup, c.key) for c in fup.__table__.columns})
        view.stored_due_date = fup.due_date
        view.date_source = (
            "manual" if fup.due_date_manual else
            "herdada" if fup.origem_entidade == "importacao_multiaba" else
            "automatica" if fup.due_date and fup.origem_entidade in ("spirometry_exams", "consultations") else "sem_fonte_identificavel"
        )
        view.base_date = None
        base = latest.get(fup.person_id)
        if (fup.status == "pendente" and not fup.due_date_manual
                and fup.person_id in chosen and fup.tipo == "pos_exame"
                and fup.origem_entidade == "spirometry_exams"):
            if fup.person_id in manual_pending or fup.id != chosen[fup.person_id]:
                continue
            view.due_date = next_contact_date(base.data_exame)
            view.base_date = base.data_exame
            view.date_source = "derivada_exame"
        out.append(view)
    return out


def contact_situation(due, today, *, do_not_contact=False):
    if do_not_contact:
        return "nao_contatar"
    if due is None:
        return "sem_data"
    if due < today:
        return "atrasado"
    if due == today:
        return "hoje"
    return "proximo" if due <= today + timedelta(days=7) else "futuro"
