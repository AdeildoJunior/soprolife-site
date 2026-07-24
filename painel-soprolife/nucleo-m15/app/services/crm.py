"""Agregações do workspace canônico de CRM (M19).

Camada de LEITURA sobre as tabelas operacionais já existentes: pessoas,
espirometrias, consultas, follow-ups, interações, parceiros e lançamentos.
Nada aqui cria tabela paralela nem copia dado para renderizar tela — todas
as visões (lista de pacientes, filas de contato, histórico, linha do tempo,
indicadores) são derivadas das relações reais.

Contrato de privacidade:
- telefone NUNCA sai inteiro daqui; só a máscara e o sinal de "utilizável".
  O número completo continua saindo apenas dentro da URL do WhatsApp, pelo
  endpoint dedicado que já existia (revisão humana obrigatória);
- nome e telefone nunca entram em rota (querystring/path) — a busca por nome
  é POST com corpo;
- nada é escrito em log a partir destas funções.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Consultation,
    FinancialEntry,
    Followup,
    Interaction,
    Lead,
    Partner,
    Person,
    PersonContact,
    PersonRelationship,
    SpirometryExam,
)
from .followup import today_local

# Janela usada para classificar "paciente reativado": um atendimento novo
# depois de um intervalo longo sem qualquer atendimento.
REATIVACAO_DIAS = 180

# Estados de apresentação do acompanhamento (M19 §10). Derivados do estado
# real (status + due_date + tentativas + nao_contatar) — sem coluna nova.
ST_FUTURO = "futuro"
ST_PROXIMO = "proximo"
ST_HOJE = "hoje"
ST_ATRASADO = "atrasado"
ST_CONCLUIDO = "concluido"
ST_REAGENDADO = "reagendado"
ST_NAO_CONTATAR = "nao_contatar"
ST_CANCELADO = "cancelado"

STATUS_LABELS = {
    ST_FUTURO: "Futuro",
    ST_PROXIMO: "Próximo",
    ST_HOJE: "Hoje",
    ST_ATRASADO: "Atrasado",
    ST_CONCLUIDO: "Concluído",
    ST_REAGENDADO: "Reagendado",
    ST_NAO_CONTATAR: "Não contatar",
    ST_CANCELADO: "Cancelado",
}

# Filas operacionais da tela "Contatos a realizar" (M19 §8).
FILA_HOJE = "hoje"
FILA_ATRASADOS = "atrasados"
FILA_7 = "proximos_7"
FILA_30 = "proximos_30"
FILA_SEM_TELEFONE = "sem_telefone"
FILA_REAGENDADOS = "reagendados"
FILA_NAO_RESPONDERAM = "nao_responderam"
FILA_NAO_CONTATAR = "nao_contatar"

FILAS = [
    FILA_HOJE,
    FILA_ATRASADOS,
    FILA_7,
    FILA_30,
    FILA_SEM_TELEFONE,
    FILA_REAGENDADOS,
    FILA_NAO_RESPONDERAM,
    FILA_NAO_CONTATAR,
]

FILA_LABELS = {
    FILA_HOJE: "Hoje",
    FILA_ATRASADOS: "Atrasados",
    FILA_7: "Próximos 7 dias",
    FILA_30: "Próximos 30 dias",
    FILA_SEM_TELEFONE: "Sem telefone",
    FILA_REAGENDADOS: "Reagendados",
    FILA_NAO_RESPONDERAM: "Não responderam",
    FILA_NAO_CONTATAR: "Não contatar",
}

# Resultados de contato aceitos (M19 §9) -> efeito colateral no follow-up.
RESULTADO_REALIZADO = "contato_realizado"
RESULTADO_NAO_RESPONDEU = "nao_respondeu"
RESULTADO_REAGENDAR = "reagendar"
RESULTADO_NAO_DESEJA = "nao_deseja_contato"
RESULTADO_TELEFONE_INVALIDO = "telefone_invalido"

RESULTADOS = [
    RESULTADO_REALIZADO,
    RESULTADO_NAO_RESPONDEU,
    RESULTADO_REAGENDAR,
    RESULTADO_NAO_DESEJA,
    RESULTADO_TELEFONE_INVALIDO,
]

RESULTADO_LABELS = {
    RESULTADO_REALIZADO: "Contato realizado",
    RESULTADO_NAO_RESPONDEU: "Não respondeu",
    RESULTADO_REAGENDAR: "Reagendar",
    RESULTADO_NAO_DESEJA: "Não deseja contato",
    RESULTADO_TELEFONE_INVALIDO: "Telefone inválido",
}

# Modelos de mensagem do WhatsApp (M19 §9).
TEMPLATES = {
    "pos_exame": "Acompanhamento de espirometria (6 meses)",
    "resultado_exame": "Retorno de resultado de exame",
    "pos_consulta": "Acompanhamento de consulta",
    "reativacao": "Reativação de paciente",
    "geral": "Contato operacional geral",
}


# --------------------------------------------------------------- utilitários

def mask_phone(valor_normalizado: str | None) -> str | None:
    """Máscara estável: só os 4 últimos dígitos ficam visíveis.

    O número completo NUNCA é devolvido por estas visões; ele existe apenas
    dentro da URL do WhatsApp montada sob confirmação humana.
    """
    if not valor_normalizado:
        return None
    digits = "".join(ch for ch in str(valor_normalizado) if ch.isdigit())
    if len(digits) <= 4:
        return "•" * len(digits)
    return "•" * (len(digits) - 4) + digits[-4:]


def dialable_contact(db: Session, person_id: str) -> PersonContact | None:
    """Mesmo critério do WhatsApp assistido: ativo, discável, whatsapp/telefone."""
    return db.execute(
        select(PersonContact)
        .where(
            PersonContact.person_id == person_id,
            PersonContact.tipo.in_(["whatsapp", "telefone"]),
            PersonContact.ativo.is_(True),
            PersonContact.nao_discavel.is_(False),
        )
        .order_by(PersonContact.principal.desc())
    ).scalars().first()


def guardian_of(db: Session, minor_person_id: str) -> Person | None:
    rel = db.execute(
        select(PersonRelationship).where(
            PersonRelationship.minor_person_id == minor_person_id,
            PersonRelationship.is_legal_guardian.is_(True),
            PersonRelationship.active.is_(True),
        )
    ).scalars().first()
    return db.get(Person, rel.guardian_person_id) if rel else None


def presentation_status(fup: Followup, patient: Person, today: date) -> str:
    """Estado exibido do acompanhamento — derivado, nunca persistido."""
    if fup.status == "cancelado":
        return ST_CANCELADO
    if fup.status == "concluido":
        return ST_CONCLUIDO
    if patient.nao_contatar:
        return ST_NAO_CONTATAR
    if fup.tentativas > 0 and fup.due_date_manual:
        return ST_REAGENDADO
    if fup.due_date is None:
        return ST_FUTURO
    if fup.due_date < today:
        return ST_ATRASADO
    if fup.due_date == today:
        return ST_HOJE
    if fup.due_date <= today + timedelta(days=7):
        return ST_PROXIMO
    return ST_FUTURO


def month_key(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m")


# ------------------------------------------------------------------ pacientes

def patient_person_ids(db: Session) -> set[str]:
    """Pacientes = pessoas com exame, consulta ou acompanhamento reais.

    Leads puros (sem atendimento e sem follow-up) continuam no módulo de
    Leads; o CRM de pacientes não os duplica.
    """
    ids: set[str] = set()
    for stmt in (
        select(SpirometryExam.person_id),
        select(Consultation.person_id),
        select(Followup.person_id),
    ):
        ids.update(row for row in db.execute(stmt).scalars().all() if row)
    return ids


def _origin_label(
    db: Session,
    person_id: str,
    exames: list[SpirometryExam],
    consultas: list[Consultation],
    leads: list[Lead],
    partner_names: dict[str, str],
) -> str:
    for exam in exames:
        if exam.partner_id and exam.partner_id in partner_names:
            return partner_names[exam.partner_id]
    for exam in exames:
        if exam.origem:
            return exam.origem
    for consulta in consultas:
        if consulta.origem:
            return consulta.origem
    for lead in leads:
        if lead.origem:
            return lead.origem
    return "Não informado"


def _index_by_person(rows) -> dict[str, list]:
    out: dict[str, list] = {}
    for row in rows:
        out.setdefault(row.person_id, []).append(row)
    return out


def build_patient_rows(db: Session, *, com_financeiro: bool) -> list[dict]:
    """Uma linha canônica por paciente, com tudo o que a lista do CRM mostra."""
    today = today_local()
    ids = patient_person_ids(db)
    if not ids:
        return []

    people = {
        p.id: p
        for p in db.execute(select(Person).where(Person.id.in_(ids))).scalars().all()
    }
    exames = _index_by_person(
        db.execute(
            select(SpirometryExam).where(SpirometryExam.person_id.in_(ids))
        ).scalars().all()
    )
    consultas = _index_by_person(
        db.execute(
            select(Consultation).where(Consultation.person_id.in_(ids))
        ).scalars().all()
    )
    leads = _index_by_person(
        db.execute(select(Lead).where(Lead.person_id.in_(ids))).scalars().all()
    )
    followups = _index_by_person(
        db.execute(select(Followup).where(Followup.person_id.in_(ids))).scalars().all()
    )
    partner_names = {
        p.id: p.nome for p in db.execute(select(Partner)).scalars().all()
    }

    entries_by_exam: dict[str, FinancialEntry] = {}
    if com_financeiro:
        for entry in db.execute(
            select(FinancialEntry).where(FinancialEntry.spirometry_exam_id.is_not(None))
        ).scalars().all():
            entries_by_exam[entry.spirometry_exam_id] = entry

    rows: list[dict] = []
    for person_id, person in people.items():
        p_exames = sorted(
            exames.get(person_id, []),
            key=lambda e: (e.data_exame or date.min),
        )
        p_consultas = sorted(
            consultas.get(person_id, []),
            key=lambda c: (c.data_consulta or date.min),
        )
        p_leads = leads.get(person_id, [])
        p_followups = followups.get(person_id, [])

        pendentes = [f for f in p_followups if f.status == "pendente"]
        pendentes.sort(key=lambda f: (f.due_date or date.max))
        proximo = pendentes[0] if pendentes else None

        # Quem realmente recebe o contato: responsável legal quando existir
        # vínculo ativo; nunca o pagador, nunca uma inferência por nome.
        contato_pessoa = person
        via_responsavel = False
        if proximo is not None and proximo.contact_person_id:
            alt = db.get(Person, proximo.contact_person_id)
            if alt is not None and alt.id != person.id:
                contato_pessoa = alt
                via_responsavel = True
        else:
            guardian = guardian_of(db, person_id)
            if guardian is not None:
                contato_pessoa = guardian
                via_responsavel = True

        contact = dialable_contact(db, contato_pessoa.id)
        ultimo_exame = p_exames[-1] if p_exames else None
        ultima_consulta = p_consultas[-1] if p_consultas else None

        row = {
            "person_id": person.id,
            "public_code": person.public_code,
            "nome_completo": person.nome_completo,
            "status_pessoa": person.status,
            "nao_contatar": person.nao_contatar,
            "contato": {
                "person_id": contato_pessoa.id,
                "public_code": contato_pessoa.public_code,
                "nome_completo": contato_pessoa.nome_completo,
                "eh_responsavel": via_responsavel,
                "telefone_mascarado": mask_phone(contact.valor_normalizado if contact else None),
                "telefone_utilizavel": contact is not None,
            },
            "ultimo_exame": {
                "public_code": ultimo_exame.public_code,
                "data": ultimo_exame.data_exame.isoformat() if ultimo_exame.data_exame else None,
                "status": ultimo_exame.status,
            } if ultimo_exame else None,
            "ultima_consulta": {
                "public_code": ultima_consulta.public_code,
                "data": (
                    ultima_consulta.data_consulta.isoformat()
                    if ultima_consulta.data_consulta else None
                ),
                "status": ultima_consulta.status,
            } if ultima_consulta else None,
            "proximo_contato": {
                "followup_id": proximo.id,
                "public_code": proximo.public_code,
                "due_date": proximo.due_date.isoformat() if proximo.due_date else None,
                "tipo": proximo.tipo,
                "tentativas": proximo.tentativas,
                "controlado_por_parceiro": proximo.controlado_por_parceiro,
                "status_apresentacao": presentation_status(proximo, person, today),
            } if proximo else None,
            "status_acompanhamento": (
                presentation_status(proximo, person, today) if proximo
                else (ST_NAO_CONTATAR if person.nao_contatar else None)
            ),
            "origem": _origin_label(
                db, person_id, p_exames, p_consultas, p_leads, partner_names
            ),
            "total_exames": len(p_exames),
            "total_consultas": len(p_consultas),
            "total_followups": len(p_followups),
        }

        if com_financeiro:
            vinculados = sum(1 for e in p_exames if e.id in entries_by_exam)
            row["financeiro"] = {
                "exames_com_lancamento": vinculados,
                "exames_sem_lancamento": len(p_exames) - vinculados,
                "status": (
                    "conciliado" if p_exames and vinculados == len(p_exames)
                    else "parcial" if vinculados
                    else "sem_lancamento" if p_exames
                    else "nao_aplicavel"
                ),
                "lancamentos": sorted(
                    entries_by_exam[e.id].public_code
                    for e in p_exames if e.id in entries_by_exam
                ),
            }
        rows.append(row)

    rows.sort(key=lambda r: (
        r["proximo_contato"]["due_date"] if r["proximo_contato"] and
        r["proximo_contato"]["due_date"] else "9999-99-99",
        r["nome_completo"].casefold(),
    ))
    return rows


# ------------------------------------------------------ filas / contatos a fazer

def build_queue_rows(db: Session) -> list[dict]:
    """Linhas de "contatos a realizar" — uma por follow-up relevante."""
    today = today_local()
    rows = db.execute(
        select(Followup, Person)
        .join(Person, Person.id == Followup.person_id)
        .order_by(Followup.due_date.asc().nulls_last())
    ).all()

    # Follow-ups que já receberam uma tentativa sem resposta continuam
    # pendentes; a fila "Não responderam" é derivada das interações reais.
    sem_resposta = {
        row.followup_id
        for row in db.execute(
            select(Interaction).where(
                Interaction.followup_id.is_not(None),
                Interaction.resultado == RESULTADO_NAO_RESPONDEU,
            )
        ).scalars().all()
        if row.followup_id
    }

    out: list[dict] = []
    for fup, person in rows:
        if fup.status == "cancelado":
            continue
        contato_pessoa = person
        via_responsavel = False
        if fup.contact_person_id and fup.contact_person_id != person.id:
            alt = db.get(Person, fup.contact_person_id)
            if alt is not None:
                contato_pessoa = alt
                via_responsavel = True
        contact = dialable_contact(db, contato_pessoa.id)
        status = presentation_status(fup, person, today)

        filas: list[str] = []
        bloqueado = person.nao_contatar or contato_pessoa.nao_contatar
        if bloqueado:
            filas.append(FILA_NAO_CONTATAR)
        elif fup.status == "pendente":
            if status == ST_ATRASADO:
                filas.append(FILA_ATRASADOS)
            elif status == ST_HOJE:
                filas.append(FILA_HOJE)
            if fup.due_date is not None and today < fup.due_date <= today + timedelta(days=7):
                filas.append(FILA_7)
            if fup.due_date is not None and today < fup.due_date <= today + timedelta(days=30):
                filas.append(FILA_30)
            if contact is None:
                filas.append(FILA_SEM_TELEFONE)
            if fup.tentativas > 0 and fup.due_date_manual:
                filas.append(FILA_REAGENDADOS)
            if fup.id in sem_resposta:
                filas.append(FILA_NAO_RESPONDERAM)
        if not filas:
            continue

        origem_ref = None
        if fup.origem_entidade == "spirometry_exams" and fup.origem_id:
            exam = db.get(SpirometryExam, fup.origem_id)
            if exam:
                origem_ref = {
                    "entidade": "spirometry_exams",
                    "public_code": exam.public_code,
                    "data": exam.data_exame.isoformat() if exam.data_exame else None,
                }
        elif fup.origem_entidade == "consultations" and fup.origem_id:
            consulta = db.get(Consultation, fup.origem_id)
            if consulta:
                origem_ref = {
                    "entidade": "consultations",
                    "public_code": consulta.public_code,
                    "data": (
                        consulta.data_consulta.isoformat()
                        if consulta.data_consulta else None
                    ),
                }

        out.append({
            "followup_id": fup.id,
            "followup_public_code": fup.public_code,
            "filas": filas,
            "status_apresentacao": status,
            "paciente": {
                "person_id": person.id,
                "public_code": person.public_code,
                "nome_completo": person.nome_completo,
            },
            "contato": {
                "person_id": contato_pessoa.id,
                "public_code": contato_pessoa.public_code,
                "nome_completo": contato_pessoa.nome_completo,
                "eh_responsavel": via_responsavel,
                "telefone_mascarado": mask_phone(contact.valor_normalizado if contact else None),
                "telefone_utilizavel": contact is not None,
            },
            "motivo": fup.tipo,
            "origem": origem_ref,
            "due_date": fup.due_date.isoformat() if fup.due_date else None,
            "tentativas": fup.tentativas,
            "controlado_por_parceiro": fup.controlado_por_parceiro,
            "status_followup": fup.status,
        })
    return out


# ------------------------------------------------------------------ linha do tempo

def build_timeline(db: Session, person: Person, *, com_financeiro: bool) -> list[dict]:
    """Linha do tempo real do paciente — sem tabela paralela, sem cópia."""
    eventos: list[dict] = []

    eventos.append({
        "tipo": "cadastro",
        "data": (person.created_at.date().isoformat() if person.created_at else None),
        "ts": person.created_at.isoformat() if person.created_at else None,
        "titulo": "Cadastro do paciente",
        "public_code": person.public_code,
        "entidade": "people",
        "detalhe": f"Status {person.status}",
    })

    partner_names = {p.id: p.nome for p in db.execute(select(Partner)).scalars().all()}

    for exam in db.execute(
        select(SpirometryExam).where(SpirometryExam.person_id == person.id)
    ).scalars().all():
        eventos.append({
            "tipo": "espirometria",
            "data": exam.data_exame.isoformat() if exam.data_exame else None,
            "ts": exam.created_at.isoformat() if exam.created_at else None,
            "titulo": "Espirometria",
            "public_code": exam.public_code,
            "entidade": "spirometry_exams",
            "entidade_id": exam.id,
            "detalhe": exam.status,
            "parceiro": partner_names.get(exam.partner_id) if exam.partner_id else None,
            "local": exam.local_atendimento,
        })

    for consulta in db.execute(
        select(Consultation).where(Consultation.person_id == person.id)
    ).scalars().all():
        eventos.append({
            "tipo": "consulta",
            "data": consulta.data_consulta.isoformat() if consulta.data_consulta else None,
            "ts": consulta.created_at.isoformat() if consulta.created_at else None,
            "titulo": "Consulta",
            "public_code": consulta.public_code,
            "entidade": "consultations",
            "entidade_id": consulta.id,
            "detalhe": consulta.status,
            "modalidade": consulta.modalidade,
        })

    today = today_local()
    for fup in db.execute(
        select(Followup).where(Followup.person_id == person.id)
    ).scalars().all():
        eventos.append({
            "tipo": "followup",
            "data": fup.due_date.isoformat() if fup.due_date else None,
            "ts": fup.created_at.isoformat() if fup.created_at else None,
            "titulo": "Acompanhamento",
            "public_code": fup.public_code,
            "entidade": "followups",
            "entidade_id": fup.id,
            "detalhe": STATUS_LABELS.get(presentation_status(fup, person, today), fup.status),
            "motivo": fup.tipo,
        })

    contact_ids = {person.id}
    guardian = guardian_of(db, person.id)
    if guardian is not None:
        contact_ids.add(guardian.id)
    for inter in db.execute(
        select(Interaction).where(Interaction.person_id.in_(contact_ids))
    ).scalars().all():
        eventos.append({
            "tipo": "interacao",
            "data": inter.ts_utc.date().isoformat() if inter.ts_utc else None,
            "ts": inter.ts_utc.isoformat() if inter.ts_utc else None,
            "titulo": "Tentativa de contato",
            "public_code": inter.public_code,
            "entidade": "interactions",
            "entidade_id": inter.id,
            "detalhe": RESULTADO_LABELS.get(inter.resultado, inter.resultado or inter.canal),
            "canal": inter.canal,
        })

    for lead in db.execute(
        select(Lead).where(Lead.person_id == person.id)
    ).scalars().all():
        eventos.append({
            "tipo": "lead",
            "data": (
                lead.data_primeiro_contato.isoformat()
                if lead.data_primeiro_contato else None
            ),
            "ts": lead.created_at.isoformat() if lead.created_at else None,
            "titulo": "Lead / primeiro contato",
            "public_code": lead.public_code,
            "entidade": "leads",
            "entidade_id": lead.id,
            "detalhe": lead.etapa,
            "origem": lead.origem,
        })

    if com_financeiro:
        exam_ids = [
            e.id for e in db.execute(
                select(SpirometryExam).where(SpirometryExam.person_id == person.id)
            ).scalars().all()
        ]
        consulta_ids = [
            c.id for c in db.execute(
                select(Consultation).where(Consultation.person_id == person.id)
            ).scalars().all()
        ]
        if exam_ids or consulta_ids:
            entries = db.execute(
                select(FinancialEntry).where(
                    FinancialEntry.spirometry_exam_id.in_(exam_ids or ["-"])
                    | FinancialEntry.consultation_id.in_(consulta_ids or ["-"])
                )
            ).scalars().all()
            for entry in entries:
                eventos.append({
                    "tipo": "financeiro",
                    "data": (
                        entry.data_recebimento.isoformat()
                        if entry.data_recebimento
                        else (
                            entry.data_competencia.isoformat()
                            if entry.data_competencia else None
                        )
                    ),
                    "ts": entry.created_at.isoformat() if entry.created_at else None,
                    "titulo": "Lançamento financeiro",
                    "public_code": entry.public_code,
                    "entidade": "financial_entries",
                    "entidade_id": entry.id,
                    "detalhe": f"{entry.tipo} · {entry.status}",
                    "valor": str(entry.valor) if entry.valor is not None else None,
                })

    eventos.sort(key=lambda e: (e["data"] or "0000-00-00", e["ts"] or ""))
    return eventos


# ------------------------------------------------------------------ indicadores

def _in_period(value: date | None, inicio: date | None, fim: date | None) -> bool:
    if value is None:
        return False
    if inicio and value < inicio:
        return False
    if fim and value > fim:
        return False
    return True


def reactivated_patients(db: Session, inicio: date | None, fim: date | None) -> list[dict]:
    """Paciente reativado: atendimento novo após >= 180 dias sem atendimento.

    Definição determinística e auditável a partir das datas reais de exame e
    consulta — não usa heurística de nome, telefone nem valor.
    """
    atendimentos: dict[str, list[date]] = {}
    for exam in db.execute(select(SpirometryExam)).scalars().all():
        if exam.data_exame:
            atendimentos.setdefault(exam.person_id, []).append(exam.data_exame)
    for consulta in db.execute(select(Consultation)).scalars().all():
        if consulta.data_consulta:
            atendimentos.setdefault(consulta.person_id, []).append(consulta.data_consulta)

    out: list[dict] = []
    for person_id, datas in atendimentos.items():
        datas = sorted(datas)
        for anterior, atual in zip(datas, datas[1:]):
            if (atual - anterior).days < REATIVACAO_DIAS:
                continue
            if not _in_period(atual, inicio, fim):
                continue
            person = db.get(Person, person_id)
            out.append({
                "person_id": person_id,
                "public_code": person.public_code if person else None,
                "nome_completo": person.nome_completo if person else None,
                "data_reativacao": atual.isoformat(),
                "atendimento_anterior": anterior.isoformat(),
                "dias_sem_atendimento": (atual - anterior).days,
            })
    out.sort(key=lambda r: r["data_reativacao"])
    return out


def build_kpis(db: Session) -> dict:
    today = today_local()
    mes = today.strftime("%Y-%m")
    inicio_mes = today.replace(day=1)

    pacientes = patient_person_ids(db)
    queue = build_queue_rows(db)

    def in_fila(nome: str) -> int:
        return sum(1 for row in queue if nome in row["filas"])

    followups = db.execute(select(Followup)).scalars().all()
    concluidos_mes = sum(
        1 for f in followups
        if f.status == "concluido" and f.concluido_em
        and f.concluido_em.astimezone(timezone.utc).strftime("%Y-%m") == mes
    )
    exames_mes = sum(
        1 for e in db.execute(select(SpirometryExam)).scalars().all()
        if e.data_exame and month_key(e.data_exame) == mes
    )
    consultas_mes = sum(
        1 for c in db.execute(select(Consultation)).scalars().all()
        if c.data_consulta and month_key(c.data_consulta) == mes
    )
    reativados = reactivated_patients(db, inicio_mes, today)

    return {
        "data_referencia": today.isoformat(),
        "mes_referencia": mes,
        "total_pacientes": len(pacientes),
        "contatos_hoje": in_fila(FILA_HOJE),
        "contatos_atrasados": in_fila(FILA_ATRASADOS),
        "proximos_7": in_fila(FILA_7),
        "proximos_30": in_fila(FILA_30),
        "sem_telefone": in_fila(FILA_SEM_TELEFONE),
        "followups_concluidos_mes": concluidos_mes,
        "pacientes_reativados": len({r["person_id"] for r in reativados}),
        "exames_mes": exames_mes,
        "consultas_mes": consultas_mes,
    }


def build_indicators(
    db: Session, *, inicio: date | None, fim: date | None, origem: str | None
) -> dict:
    today = today_local()
    rows = build_patient_rows(db, com_financeiro=False)
    if origem and origem != "todas":
        permitidos = {r["person_id"] for r in rows if r["origem"] == origem}
    else:
        permitidos = {r["person_id"] for r in rows}

    def keep(person_id: str) -> bool:
        return person_id in permitidos

    contatos_por_mes: dict[str, int] = {}
    concluidos_por_mes: dict[str, int] = {}
    atrasados = 0
    for fup in db.execute(select(Followup)).scalars().all():
        if not keep(fup.person_id):
            continue
        if fup.due_date and _in_period(fup.due_date, inicio, fim):
            key = month_key(fup.due_date)
            contatos_por_mes[key] = contatos_por_mes.get(key, 0) + 1
        if (
            fup.status == "pendente"
            and fup.due_date is not None
            and fup.due_date < today
        ):
            atrasados += 1
        if fup.status == "concluido" and fup.concluido_em:
            concluido_data = fup.concluido_em.astimezone(timezone.utc).date()
            if _in_period(concluido_data, inicio, fim):
                key = month_key(concluido_data)
                concluidos_por_mes[key] = concluidos_por_mes.get(key, 0) + 1

    resultados: dict[str, int] = {}
    for inter in db.execute(select(Interaction)).scalars().all():
        if not keep(inter.person_id):
            continue
        data = inter.ts_utc.astimezone(timezone.utc).date() if inter.ts_utc else None
        if not _in_period(data, inicio, fim):
            continue
        key = inter.resultado or "sem_resultado"
        resultados[key] = resultados.get(key, 0) + 1

    por_origem: dict[str, int] = {}
    for row in rows:
        if not keep(row["person_id"]):
            continue
        por_origem[row["origem"]] = por_origem.get(row["origem"], 0) + 1

    exames_por_mes: dict[str, int] = {}
    for exam in db.execute(select(SpirometryExam)).scalars().all():
        if not keep(exam.person_id) or not _in_period(exam.data_exame, inicio, fim):
            continue
        key = month_key(exam.data_exame)
        exames_por_mes[key] = exames_por_mes.get(key, 0) + 1

    consultas_por_mes: dict[str, int] = {}
    for consulta in db.execute(select(Consultation)).scalars().all():
        if not keep(consulta.person_id) or not _in_period(consulta.data_consulta, inicio, fim):
            continue
        key = month_key(consulta.data_consulta)
        consultas_por_mes[key] = consultas_por_mes.get(key, 0) + 1

    reativados_por_mes: dict[str, int] = {}
    for item in reactivated_patients(db, inicio, fim):
        if not keep(item["person_id"]):
            continue
        key = item["data_reativacao"][:7]
        reativados_por_mes[key] = reativados_por_mes.get(key, 0) + 1

    def series(d: dict[str, int]) -> list[dict]:
        return [{"periodo": k, "valor": v} for k, v in sorted(d.items())]

    return {
        "data_referencia": today.isoformat(),
        "periodo": {
            "inicio": inicio.isoformat() if inicio else None,
            "fim": fim.isoformat() if fim else None,
        },
        "origem": origem or "todas",
        "origens_disponiveis": sorted({r["origem"] for r in rows}),
        "contatos_por_periodo": series(contatos_por_mes),
        "contatos_atrasados": atrasados,
        "followups_concluidos_por_mes": series(concluidos_por_mes),
        "resultados_de_contato": [
            {"resultado": k, "rotulo": RESULTADO_LABELS.get(k, k), "valor": v}
            for k, v in sorted(resultados.items())
        ],
        "pacientes_por_origem": [
            {"origem": k, "valor": v} for k, v in sorted(por_origem.items())
        ],
        "exames_por_mes": series(exames_por_mes),
        "consultas_por_mes": series(consultas_por_mes),
        "pacientes_reativados_por_mes": series(reativados_por_mes),
    }


# ------------------------------------------------------------------ mensagens

def template_message(nome: str, template: str) -> str:
    primeiro = (nome or "").split(" ")[0]
    base = f"Olá {primeiro}, aqui é da SoproLife."
    if template == "pos_exame":
        return (
            f"{base} Faz cerca de 6 meses desde a sua espirometria. "
            "Podemos conversar sobre o acompanhamento?"
        )
    if template == "resultado_exame":
        return (
            f"{base} Estamos com o resultado do seu exame disponível. "
            "Podemos combinar o melhor momento para conversar sobre ele?"
        )
    if template == "pos_consulta":
        return (
            f"{base} Faz cerca de 6 meses desde a sua consulta. "
            "Que tal agendarmos o seu retorno?"
        )
    if template == "reativacao":
        return (
            f"{base} Faz um tempo desde o seu último atendimento conosco. "
            "Podemos retomar o seu acompanhamento respiratório?"
        )
    return f"{base} Podemos falar sobre o seu atendimento?"


def money_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
