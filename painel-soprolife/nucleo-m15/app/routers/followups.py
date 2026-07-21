"""Follow-ups, fila semiautomática, WhatsApp assistido e interações.

Hardening M15.1A:
- fila com paginação e limites;
- consentimento de WhatsApp fail-closed: sem "concedido", sem URL;
- follow-up controlado pelo parceiro não expõe botão/URL da SoproLife;
- registros sintéticos (seed_demo) e contatos não discáveis nunca geram URL;
- transições de status validadas; nova tentativa limpa conclusão anterior.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import allocate_public_code
from ..models import Followup, Interaction, Person, PersonContact, User
from ..pagination import PageParams, paginate
from ..schemas import (
    FollowupComplete,
    FollowupCreate,
    FollowupRetry,
    InteractionCreate,
    WhatsAppConfirm,
)
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_followup, ser_interaction
from ..services import followup as fsvc
from ..services.relationships import legal_contact_relationship

router = APIRouter(tags=["followup"])

QUEUE_SCAN_LIMIT = 1000  # teto duro de linhas varridas por chamada da fila


def _followup_or_404(db: Session, followup_id: str) -> Followup:
    fup = db.get(Followup, followup_id)
    if not fup:
        raise HTTPException(status_code=404, detail="Follow-up não encontrado.")
    return fup


def _is_synthetic(person: Person) -> bool:
    return person.legacy_source == "seed_demo"


def _contact_person(db: Session, fup: Followup, patient: Person) -> Person:
    if not fup.contact_person_id:
        return patient
    contact = db.get(Person, fup.contact_person_id)
    if contact is None:  # FK deveria tornar isto inalcançável; falha fechada.
        raise HTTPException(status_code=409, detail="Pessoa de contato inválida.")
    if contact.id == patient.id:
        return patient
    if legal_contact_relationship(db, patient.id, contact.id) is None:
        raise HTTPException(
            status_code=409,
            detail={"codigo": "active_legal_guardian_required"},
        )
    return contact


@router.get("/followups")
def list_followups(
    status: str | None = None,
    tipo: str | None = None,
    person_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Followup).order_by(Followup.due_date.asc().nulls_last())
    if status:
        stmt = stmt.where(Followup.status == status)
    if tipo:
        stmt = stmt.where(Followup.tipo == tipo)
    if person_id:
        stmt = stmt.where(Followup.person_id == person_id)
    return paginate(db, stmt, params, ser_followup)


def _queue_item(db: Session, fup: Followup, person: Person, queue: str) -> dict:
    contact = _contact_person(db, fup, person)
    consent = fsvc.whatsapp_consent_status(db, contact.id)
    sintetico = _is_synthetic(person) or _is_synthetic(contact)
    item = ser_followup(fup, fila=queue)
    item["pessoa"] = {
        "id": person.id,
        "public_code": person.public_code,
        "nome_completo": person.nome_completo,
    }
    item["paciente"] = item["pessoa"]
    item["pessoa_contato"] = {
        "id": contact.id,
        "public_code": contact.public_code,
        "nome_completo": contact.nome_completo,
    }
    item["consentimento_whatsapp"] = consent
    item["aviso_consentimento"] = consent != "concedido"
    item["sintetico"] = sintetico
    # botão/URL de WhatsApp só quando TODAS as condições fecham (fail-closed)
    item["whatsapp_permitido"] = (
        fup.status == "pendente"
        and consent == "concedido"
        and not fup.controlado_por_parceiro
        and not sintetico
        and not contact.nao_contatar
    )
    return item


@router.get("/followups/fila")
def followup_queue(
    fila: str | None = Query(
        None, description="atrasado|retomar_hoje|retomar_semana|aguardando_data|concluido"
    ),
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Fila semiautomática. Pessoas 'não contatar' NUNCA aparecem.

    Sem `fila`: retorna totais + primeira página de cada fila.
    Com `fila`: retorna a fila escolhida paginada (pagina/tamanho).
    """
    today = fsvc.today_local()
    rows = db.execute(
        select(Followup, Person)
        .join(Person, Person.id == Followup.person_id)
        .where(Followup.status.in_(["pendente", "concluido"]))
        .order_by(Followup.due_date.asc().nulls_last())
        .limit(QUEUE_SCAN_LIMIT)
    ).all()
    filas: dict[str, list] = {
        fsvc.FILA_ATRASADO: [],
        fsvc.FILA_HOJE: [],
        fsvc.FILA_SEMANA: [],
        fsvc.FILA_AGUARDANDO: [],
        fsvc.FILA_CONCLUIDO: [],
    }
    excluidos_nao_contatar = 0
    for fup, person in rows:
        if _contact_person(db, fup, person).nao_contatar:
            excluidos_nao_contatar += 1
            continue
        queue = fsvc.classify_queue(fup, person, today)
        if queue == fsvc.FILA_NAO_CONTATAR:
            excluidos_nao_contatar += 1
            continue
        filas[queue].append((fup, person))
    totais = {k: len(v) for k, v in filas.items()}
    if fila:
        if fila not in filas:
            raise HTTPException(status_code=422, detail="Fila inválida.")
        page = filas[fila][params.offset:params.offset + params.tamanho]
        return {
            "data_referencia": today.isoformat(),
            "fila": fila,
            "itens": [_queue_item(db, f, p, fila) for f, p in page],
            "pagina": params.pagina,
            "tamanho": params.tamanho,
            "total": totais[fila],
            "excluidos_nao_contatar": excluidos_nao_contatar,
        }
    primeira_pagina = {
        key: [_queue_item(db, f, p, key) for f, p in items[:params.tamanho]]
        for key, items in filas.items()
    }
    return {
        "data_referencia": today.isoformat(),
        "filas": primeira_pagina,
        "totais": totais,
        "tamanho_pagina": params.tamanho,
        "excluidos_nao_contatar": excluidos_nao_contatar,
    }


@router.post("/followups", status_code=201)
def create_followup(
    payload: FollowupCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    patient_id = payload.resolved_patient_person_id
    person = db.get(Person, patient_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    contact_id = payload.contact_person_id or patient_id
    contact = db.get(Person, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Pessoa de contato não encontrada.")
    if (
        contact_id != patient_id
        and legal_contact_relationship(db, patient_id, contact_id) is None
    ):
        raise HTTPException(
            status_code=409,
            detail={"codigo": "active_legal_guardian_required"},
        )
    fup, motivo = fsvc.schedule_followup(
        db, person, payload.tipo,
        due=payload.due_date, due_manual=payload.due_date is not None,
        responsavel=payload.responsavel, observacao=payload.observacao,
        contact_person_id=(contact_id if contact_id != patient_id else None),
    )
    if fup is None:
        raise HTTPException(
            status_code=409,
            detail="Pessoa marcada como 'não contatar' — follow-up bloqueado.",
        )
    audit(db, "followup.criado", "followups", fup.id, user.id,
          request.state.request_id, {"motivo": motivo, "tipo": payload.tipo})
    db.commit()
    data = ser_followup(fup)
    data["motivo"] = motivo
    return data


@router.post("/followups/{followup_id}/concluir")
def complete(
    followup_id: str,
    payload: FollowupComplete,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    fup = _followup_or_404(db, followup_id)
    if fup.status == "concluido":
        return ser_followup(fup)
    fsvc.complete_followup(db, fup, user.id, payload.resultado, payload.observacao)
    audit(db, "followup.concluido", "followups", fup.id, user.id,
          request.state.request_id, {"resultado": payload.resultado})
    db.commit()
    return ser_followup(fup)


@router.post("/followups/{followup_id}/nova-tentativa")
def retry(
    followup_id: str,
    payload: FollowupRetry,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    fup = _followup_or_404(db, followup_id)
    person = db.get(Person, fup.person_id)
    contact_person = _contact_person(db, fup, person)
    if person.nao_contatar or contact_person.nao_contatar:
        raise HTTPException(
            status_code=409,
            detail="Pessoa marcada como 'não contatar' — nova tentativa bloqueada.",
        )
    status_anterior = fup.status
    fsvc.retry_followup(db, fup, payload.nova_data, payload.observacao)
    audit(db, "followup.nova_tentativa", "followups", fup.id, user.id,
          request.state.request_id,
          {"nova_data": payload.nova_data.isoformat(), "status": status_anterior})
    db.commit()
    return ser_followup(fup)


@router.get("/followups/{followup_id}/whatsapp-url")
def whatsapp_url(
    followup_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Monta a URL do WhatsApp para REVISÃO HUMANA. Nunca dispara envio.

    Fail-closed: exige pessoa contactável, consentimento CONCEDIDO, follow-up
    da SoproLife (não do parceiro), registro real (não sintético) e telefone
    discável. A interação só é registrada via POST .../whatsapp-confirmacao.
    """
    fup = _followup_or_404(db, followup_id)
    if fup.status != "pendente":
        raise HTTPException(status_code=409, detail="Follow-up não está pendente.")
    person = db.get(Person, fup.person_id)
    contact_person = _contact_person(db, fup, person)
    if person.nao_contatar or contact_person.nao_contatar:
        raise HTTPException(
            status_code=409,
            detail="Pessoa marcada como 'não contatar' — contato bloqueado.",
        )
    if fup.controlado_por_parceiro:
        raise HTTPException(
            status_code=409,
            detail={"codigo": "followup_do_parceiro",
                    "mensagem": "Este follow-up é controlado pela clínica parceira — "
                                "a SoproLife não contata diretamente."},
        )
    if _is_synthetic(person) or _is_synthetic(contact_person):
        raise HTTPException(
            status_code=409,
            detail={"codigo": "registro_sintetico",
                    "mensagem": "Registro sintético de demonstração — WhatsApp desabilitado."},
        )
    consent = fsvc.whatsapp_consent_status(db, contact_person.id)
    if consent != "concedido":
        raise HTTPException(
            status_code=409,
            detail={"codigo": "sem_consentimento",
                    "mensagem": "Consentimento de WhatsApp não concedido — contato "
                                "bloqueado (fail-closed).",
                    "consentimento": consent},
        )
    contact = db.execute(
        select(PersonContact).where(
            PersonContact.person_id == contact_person.id,
            PersonContact.tipo.in_(["whatsapp", "telefone"]),
            PersonContact.ativo == True,  # noqa: E712
            PersonContact.nao_discavel == False,  # noqa: E712
        ).order_by(PersonContact.principal.desc())
    ).scalars().first()
    if not contact or not contact.valor_normalizado:
        raise HTTPException(status_code=422, detail="Pessoa sem telefone discável cadastrado.")
    message = fsvc.default_message(contact_person.nome_completo, fup.tipo)
    return {
        "followup_id": fup.id,
        "url": fsvc.build_whatsapp_url(contact.valor_normalizado, message),
        "mensagem_sugerida": message,
        "consentimento_whatsapp": consent,
        "aviso_consentimento": False,
        "envio_automatico": False,
        "instrucao": "Revise a mensagem, envie manualmente e confirme para registrar.",
    }


@router.post("/followups/{followup_id}/whatsapp-confirmacao", status_code=201)
def whatsapp_confirm(
    followup_id: str,
    payload: WhatsAppConfirm,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Registra a interação SOMENTE após confirmação humana do envio manual."""
    fup = _followup_or_404(db, followup_id)
    if fup.status != "pendente":
        raise HTTPException(status_code=409, detail="Follow-up não está pendente.")
    person = db.get(Person, fup.person_id)
    contact_person = _contact_person(db, fup, person)
    if person.nao_contatar or contact_person.nao_contatar:
        raise HTTPException(status_code=409, detail="Pessoa 'não contatar'.")
    if fup.controlado_por_parceiro:
        raise HTTPException(status_code=409, detail="Follow-up controlado pela clínica.")
    if _is_synthetic(person) or _is_synthetic(contact_person):
        raise HTTPException(status_code=409, detail="Registro sintético de demonstração.")
    if fsvc.whatsapp_consent_status(db, contact_person.id) != "concedido":
        raise HTTPException(
            status_code=409,
            detail="Consentimento de WhatsApp não concedido — confirmação bloqueada.",
        )
    interaction = fsvc.record_confirmed_interaction(
        db, fup, user.id, payload.resumo, payload.resultado
    )
    audit(db, "followup.whatsapp_confirmado", "followups", fup.id, user.id,
          request.state.request_id, {"resultado": payload.resultado})
    db.commit()
    return ser_interaction(interaction)


# ---------------------------------------------------------------- interações

@router.get("/interacoes")
def list_interactions(
    person_id: str | None = None,
    followup_id: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(Interaction).order_by(Interaction.ts_utc.desc())
    if person_id:
        stmt = stmt.where(Interaction.person_id == person_id)
    if followup_id:
        stmt = stmt.where(Interaction.followup_id == followup_id)
    return paginate(db, stmt, params, ser_interaction)


@router.post("/interacoes", status_code=201)
def create_interaction(
    payload: InteractionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    person = db.get(Person, payload.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    interaction = Interaction(
        public_code=allocate_public_code(db, "interactions"),
        person_id=payload.person_id,
        canal=payload.canal,
        direcao=payload.direcao,
        resumo=payload.resumo,
        resultado=payload.resultado,
        user_id=user.id,
        followup_id=payload.followup_id,
    )
    db.add(interaction)
    db.flush()
    audit(db, "interacao.criada", "interactions", interaction.id, user.id,
          request.state.request_id, {"canal": payload.canal})
    db.commit()
    return ser_interaction(interaction)
