"""Leads, espirometrias e consultas — idempotência atômica, PCMSO bloqueado,
integridade cruzada e follow-up sincronizado na mesma transação."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..dates import parse_incomplete_date
from ..db import get_db
from ..domain import ensure_sem_pcmso
from ..ids import allocate_public_code
from ..models import (
    Consultation,
    Lead,
    PartnerUnit,
    Person,
    SpirometryExam,
    User,
)
from ..pagination import PageParams, paginate
from ..schemas import (
    ConsultationCreate,
    ConsultationUpdate,
    ExamCreate,
    ExamUpdate,
    LeadCreate,
    LeadUpdate,
)
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_consultation, ser_exam, ser_lead
from ..status_display import exam_status_filter_values
from ..services.followup import (
    due_after_attendance,
    schedule_followup,
    sync_followup_for_origin,
)
from ..services.idempotency import idempotent_create
from ..services.integrity import ensure_unit_of_partner

router = APIRouter(tags=["operacao"])


def _person_or_404(db: Session, person_id: str) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    return person


def _apply_date(obj, prefix: str, raw: str | None) -> None:
    nd = parse_incomplete_date(raw)
    setattr(obj, prefix, nd.value)
    setattr(obj, f"{prefix}_original", nd.original or None)
    setattr(obj, f"{prefix}_precisao", nd.precision)
    setattr(obj, f"{prefix}_dia_assumido", nd.day_assumed)


def _audit_pcmso_and_raise(db, request, user, exc: HTTPException):
    audit(db, "pcmso.rejeitado", user_id=user.id, request_id=request.state.request_id,
          detalhes=exc.detail if isinstance(exc.detail, dict) else None)
    db.commit()
    raise exc


def _check_pcmso(db, request, user, payload, contexto: str) -> None:
    try:
        ensure_sem_pcmso(payload.model_dump(exclude_none=True), contexto)
    except HTTPException as exc:
        _audit_pcmso_and_raise(db, request, user, exc)


# ------------------------------------------------------------------ leads

@router.get("/leads")
def list_leads(
    etapa: str | None = None,
    modalidade: str | None = None,
    person_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Lead).order_by(Lead.created_at.desc())
    if etapa:
        stmt = stmt.where(Lead.etapa == etapa)
    if modalidade:
        stmt = stmt.where(Lead.modalidade == modalidade)
    if person_id:
        stmt = stmt.where(Lead.person_id == person_id)
    return paginate(db, stmt, params, ser_lead)


@router.post("/leads", status_code=201)
def create_lead(
    payload: LeadCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _check_pcmso(db, request, user, payload, "leads.create")
    person = _person_or_404(db, payload.person_id)
    lead = Lead(
        public_code=allocate_public_code(db, "leads"),
        person_id=person.id,
        origem=payload.origem,
        canal_entrada=payload.canal_entrada,
        servico_interesse=payload.servico_interesse,
        modalidade=payload.modalidade,
        etapa=payload.etapa,
        data_retomada_manual=payload.data_retomada_manual,
        responsavel=payload.responsavel,
        observacao=payload.observacao,
    )
    _apply_date(lead, "data_primeiro_contato", payload.data_primeiro_contato)
    db.add(lead)
    db.flush()
    # lead sem atendimento: fila usa retomada manual OU data do primeiro contato
    due = payload.data_retomada_manual or lead.data_primeiro_contato
    fup, motivo = schedule_followup(
        db, person, "lead_sem_atendimento", "leads", lead.id,
        due=due, due_manual=payload.data_retomada_manual is not None,
        responsavel=payload.responsavel,
    )
    audit(db, "lead.criado", "leads", lead.id, user.id, request.state.request_id,
          {"public_code": lead.public_code, "followup": motivo})
    db.commit()
    data = ser_lead(lead)
    data["followup"] = {"motivo": motivo, "id": fup.id if fup else None}
    return data


@router.patch("/leads/{lead_id}")
def update_lead(
    lead_id: str,
    payload: LeadUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    _check_pcmso(db, request, user, payload, "leads.update")
    changed = []
    for field in ("etapa", "modalidade", "data_retomada_manual", "responsavel", "observacao"):
        value = getattr(payload, field)
        if value is not None:
            setattr(lead, field, value)
            changed.append(field)
    # sincroniza a fila NA MESMA transação: retomada manual muda vencimento;
    # lead convertido/perdido cancela o follow-up de captação
    person = db.get(Person, lead.person_id)
    lead_ativo = lead.etapa not in ("convertido", "perdido")
    fup, fup_motivo = sync_followup_for_origin(
        db, person, "lead_sem_atendimento", "leads", lead.id,
        due=lead.data_retomada_manual or lead.data_primeiro_contato,
        active=lead_ativo,
        responsavel=lead.responsavel,
    )
    if "data_retomada_manual" in changed and fup is not None and lead_ativo:
        # retomada manual definida pelo humano tem precedência absoluta
        fup.due_date = lead.data_retomada_manual
        fup.due_date_manual = True
        db.flush()
    audit(db, "lead.atualizado", "leads", lead.id, user.id,
          request.state.request_id, {"campos": changed, "followup": fup_motivo})
    db.commit()
    data = ser_lead(lead)
    data["followup"] = {"motivo": fup_motivo, "id": fup.id if fup else None}
    return data


# ------------------------------------------------------------ espirometrias

@router.get("/espirometrias")
def list_exams(
    status: str | None = None,
    modalidade: str | None = None,
    public_code: str | None = None,
    person_id: str | None = None,
    partner_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(SpirometryExam).order_by(SpirometryExam.created_at.desc())
    if status:
        # Filtrar por "Espirometria realizada" (ou por qualquer sinônimo em
        # escopo) casa TODOS os valores armazenados que significam realizado.
        # Qualquer outro status — inclusive "Liberado" — segue igualdade exata.
        stmt = stmt.where(SpirometryExam.status.in_(exam_status_filter_values(status)))
    if modalidade:
        stmt = stmt.where(SpirometryExam.modalidade == modalidade)
    if public_code:
        # Código institucional, sem nome/telefone na query. A UI de laudos
        # usa busca exata para localizar exames além da lista recente sem
        # colocar identificador de paciente em URL ou log.
        stmt = stmt.where(SpirometryExam.public_code == public_code.strip().upper())
    if person_id:
        stmt = stmt.where(SpirometryExam.person_id == person_id)
    if partner_id:
        stmt = stmt.where(SpirometryExam.partner_id == partner_id)
    return paginate(db, stmt, params, ser_exam)


@router.post("/espirometrias", status_code=201)
def create_exam(
    payload: ExamCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _check_pcmso(db, request, user, payload, "espirometrias.create")
    person = _person_or_404(db, payload.person_id)
    if payload.partner_id:
        from ..services.integrity import ensure_partner_exists

        ensure_partner_exists(db, payload.partner_id)
        ensure_unit_of_partner(db, payload.partner_unit_id, payload.partner_id)
    elif payload.partner_unit_id:
        raise HTTPException(
            status_code=422,
            detail={"codigo": "unidade_sem_parceiro",
                    "mensagem": "partner_unit_id exige partner_id."},
        )

    def factory(key, fingerprint):
        exam = SpirometryExam(
            public_code=allocate_public_code(db, "spirometry_exams"),
            person_id=person.id,
            modalidade=payload.modalidade,
            local_atendimento=payload.local_atendimento,
            partner_id=payload.partner_id,
            partner_unit_id=payload.partner_unit_id,
            status=payload.status,
            broncodilatador=payload.broncodilatador,
            origem=payload.origem,
            responsavel=payload.responsavel,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
            observacao=payload.observacao,
        )
        _apply_date(exam, "data_exame", payload.data_exame)
        db.add(exam)
        db.flush()
        return exam

    exam, ja_existia = idempotent_create(
        db, SpirometryExam, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_exam(exam)
        data["idempotente"] = True
        return data
    fup_info = _sync_exam_followup(db, person, exam)
    audit(db, "espirometria.criada", "spirometry_exams", exam.id, user.id,
          request.state.request_id, {"public_code": exam.public_code, "status": exam.status})
    db.commit()
    data = ser_exam(exam)
    data["followup"] = fup_info
    return data


def _sync_exam_followup(db: Session, person: Person, exam: SpirometryExam) -> dict:
    # "Laudo Liberado" pressupõe exame realizado — mantém o follow-up ativo.
    active = exam.status in ("Realizado", "Laudo Liberado") and exam.data_exame is not None
    fup, motivo = sync_followup_for_origin(
        db, person, "pos_exame", "spirometry_exams", exam.id,
        due=due_after_attendance(exam.data_exame) if active else None,
        active=active,
        responsavel=exam.responsavel,
        partner_id=exam.partner_id,
    )
    return {"motivo": motivo, "id": fup.id if fup else None}


@router.patch("/espirometrias/{exam_id}")
def update_exam(
    exam_id: str,
    payload: ExamUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    exam = db.get(SpirometryExam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
    _check_pcmso(db, request, user, payload, "espirometrias.update")
    changed = []
    if payload.status is not None:
        exam.status = payload.status
        changed.append("status")
    if payload.data_exame is not None:
        _apply_date(exam, "data_exame", payload.data_exame)
        changed.append("data_exame")
    if payload.broncodilatador is not None:
        exam.broncodilatador = payload.broncodilatador
        changed.append("broncodilatador")
    if payload.responsavel is not None:
        exam.responsavel = payload.responsavel
        changed.append("responsavel")
    if payload.observacao is not None:
        exam.observacao = payload.observacao
        changed.append("observacao")
    # M25.17 — local de realização. É o cadastro que o laudo passou a ler
    # para derivar origem e unidade, então a coerência é validada AQUI, na
    # escrita, e não só na hora de emitir: descobrir a contradição no
    # momento de anexar o PDF foi justamente a falha do primeiro uso real.
    if payload.modalidade is not None:
        exam.modalidade = payload.modalidade
        changed.append("modalidade")
    if payload.partner_id is not None:
        novo = payload.partner_id.strip() or None
        if novo:
            ensure_partner_exists(db, novo)
        exam.partner_id = novo
        changed.append("partner_id")
    if payload.partner_unit_id is not None:
        nova_unidade = payload.partner_unit_id.strip() or None
        if nova_unidade:
            unidade = db.get(PartnerUnit, nova_unidade)
            if unidade is None or not unidade.ativo:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "codigo": "unidade_invalida",
                        "mensagem": (
                            "A unidade parceira informada não existe ou "
                            "não está ativa."
                        ),
                    },
                )
            if exam.partner_id is None:
                # Escolher "Pastore Ipanema" já diz que o parceiro é Pastore.
                # Preencher sozinho evita obrigar o operador a informar duas
                # vezes a mesma coisa — e a errar em uma delas.
                exam.partner_id = unidade.partner_id
                changed.append("partner_id")
            elif unidade.partner_id != exam.partner_id:
                # A unidade precisa pertencer ao parceiro do exame; sem isso
                # o laudo imprimiria o endereço de uma clínica e o
                # financeiro creditaria outra.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "codigo": "unidade_de_outro_parceiro",
                        "mensagem": (
                            "A unidade informada pertence a outro parceiro."
                        ),
                    },
                )
        exam.partner_unit_id = nova_unidade
        changed.append("partner_unit_id")
    if {"modalidade", "partner_id", "partner_unit_id"} & set(changed):
        if exam.partner_unit_id and exam.modalidade != "clinica_parceira":
            raise HTTPException(
                status_code=422,
                detail={
                    "codigo": "unidade_incompativel_com_modalidade",
                    "mensagem": (
                        "Unidade parceira só se aplica a exame de clínica "
                        "parceira. Ajuste a modalidade ou remova a unidade."
                    ),
                },
            )
    # recalcula/cancela o follow-up na MESMA transação da mudança de origem
    person = db.get(Person, exam.person_id)
    fup_info = _sync_exam_followup(db, person, exam)
    audit(db, "espirometria.atualizada", "spirometry_exams", exam.id, user.id,
          request.state.request_id, {"campos": changed, "followup": fup_info["motivo"]})
    db.commit()
    data = ser_exam(exam)
    data["followup"] = fup_info
    return data


# --------------------------------------------------------------- consultas

@router.get("/consultas")
def list_consultations(
    status: str | None = None,
    modalidade: str | None = None,
    person_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Consultation).order_by(Consultation.created_at.desc())
    if status:
        stmt = stmt.where(Consultation.status == status)
    if modalidade:
        stmt = stmt.where(Consultation.modalidade == modalidade)
    if person_id:
        stmt = stmt.where(Consultation.person_id == person_id)
    return paginate(db, stmt, params, ser_consultation)


def _sync_consultation_followup(db: Session, person: Person, consultation: Consultation) -> dict:
    active = consultation.status == "Realizada" and consultation.data_consulta is not None
    fup, motivo = sync_followup_for_origin(
        db, person, "pos_consulta", "consultations", consultation.id,
        due=due_after_attendance(consultation.data_consulta) if active else None,
        active=active,
        responsavel=consultation.responsavel,
    )
    return {"motivo": motivo, "id": fup.id if fup else None}


@router.post("/consultas", status_code=201)
def create_consultation(
    payload: ConsultationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _check_pcmso(db, request, user, payload, "consultas.create")
    person = _person_or_404(db, payload.person_id)

    def factory(key, fingerprint):
        consultation = Consultation(
            public_code=allocate_public_code(db, "consultations"),
            person_id=person.id,
            modalidade=payload.modalidade,
            profissional=payload.profissional,
            status=payload.status,
            origem=payload.origem,
            responsavel=payload.responsavel,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
            observacao=payload.observacao,
        )
        _apply_date(consultation, "data_consulta", payload.data_consulta)
        db.add(consultation)
        db.flush()
        return consultation

    consultation, ja_existia = idempotent_create(
        db, Consultation, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_consultation(consultation)
        data["idempotente"] = True
        return data
    fup_info = _sync_consultation_followup(db, person, consultation)
    audit(db, "consulta.criada", "consultations", consultation.id, user.id,
          request.state.request_id, {"public_code": consultation.public_code})
    db.commit()
    data = ser_consultation(consultation)
    data["followup"] = fup_info
    return data


@router.patch("/consultas/{consultation_id}")
def update_consultation(
    consultation_id: str,
    payload: ConsultationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    consultation = db.get(Consultation, consultation_id)
    if not consultation:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    _check_pcmso(db, request, user, payload, "consultas.update")
    changed = []
    if payload.status is not None:
        consultation.status = payload.status
        changed.append("status")
    if payload.data_consulta is not None:
        _apply_date(consultation, "data_consulta", payload.data_consulta)
        changed.append("data_consulta")
    if payload.profissional is not None:
        consultation.profissional = payload.profissional
        changed.append("profissional")
    if payload.observacao is not None:
        consultation.observacao = payload.observacao
        changed.append("observacao")
    person = db.get(Person, consultation.person_id)
    fup_info = _sync_consultation_followup(db, person, consultation)
    audit(db, "consulta.atualizada", "consultations", consultation.id, user.id,
          request.state.request_id, {"campos": changed, "followup": fup_info["motivo"]})
    db.commit()
    data = ser_consultation(consultation)
    data["followup"] = fup_info
    return data
