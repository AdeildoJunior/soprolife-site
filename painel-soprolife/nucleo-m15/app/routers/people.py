"""Pessoas: cadastro canônico único, contatos, consentimentos, aliases legados."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import allocate_public_code
from ..models import Consent, LegacyAlias, Person, PersonContact, User
from ..normalize import normalize_name, normalize_phone
from ..pagination import PageParams, paginate
from ..schemas import ConsentIn, ContactIn, PersonCreate, PersonSearch, PersonUpdate
from ..security import ROLE_LEITURA, ROLE_OPERACIONAL, require_role
from ..serializers import ser_consent, ser_person
from ..services.identity import find_person_candidates, register_candidates

router = APIRouter(prefix="/pessoas", tags=["pessoas"])


def _get_person(db: Session, person_id: str) -> Person:
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    return person


def _add_contact(db: Session, person: Person, contato: ContactIn) -> PersonContact:
    normalized = (
        normalize_phone(contato.valor)
        if contato.tipo in ("whatsapp", "telefone")
        else contato.valor.strip().lower()
    )
    contact = PersonContact(
        person_id=person.id,
        tipo=contato.tipo,
        valor=contato.valor,
        valor_normalizado=normalized,
        principal=contato.principal,
    )
    db.add(contact)
    db.flush()
    return contact


@router.get("")
def list_people(
    status: str | None = Query(None, max_length=20),
    nao_contatar: bool | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Listagem sem busca textual — nome NUNCA trafega em query string
    (access logs não podem conter PII). Busca por nome: POST /pessoas/busca."""
    stmt = select(Person).order_by(Person.created_at.desc())
    if status:
        stmt = stmt.where(Person.status == status)
    if nao_contatar is not None:
        stmt = stmt.where(Person.nao_contatar == nao_contatar)
    return paginate(db, stmt, params, ser_person)


@router.post("/busca")
def search_people(
    payload: PersonSearch,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Busca autenticada via corpo POST — o termo nunca aparece na URL."""
    stmt = select(Person).order_by(Person.created_at.desc())
    if payload.q:
        term = normalize_name(payload.q)
        if payload.q.upper().startswith("PES-"):
            stmt = stmt.where(Person.public_code == payload.q.upper())
        else:
            stmt = stmt.where(Person.nome_normalizado.like(f"%{term}%"))
    if payload.status:
        stmt = stmt.where(Person.status == payload.status)
    if payload.nao_contatar is not None:
        stmt = stmt.where(Person.nao_contatar == payload.nao_contatar)
    params = PageParams(pagina=payload.pagina, tamanho=payload.tamanho)
    return paginate(db, stmt, params, ser_person)


@router.post("", status_code=201)
def create_person(
    payload: PersonCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    person = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo=payload.nome_completo,
        nome_normalizado=normalize_name(payload.nome_completo),
        data_nascimento=payload.data_nascimento,
        observacao=payload.observacao,
    )
    db.add(person)
    db.flush()
    for contato in payload.contatos:
        _add_contact(db, person, contato)
    if payload.consentimento_whatsapp:
        db.add(
            Consent(
                person_id=person.id,
                canal="whatsapp",
                status=payload.consentimento_whatsapp,
                origem="cadastro",
                registrado_por=user.id,
            )
        )
    # candidatos de identidade (aviso, nunca fusão automática)
    phones = [
        normalize_phone(c.valor) for c in payload.contatos if c.tipo in ("whatsapp", "telefone")
    ]
    candidates = find_person_candidates(
        db, person.nome_normalizado, [p for p in phones if p], exclude_person_id=person.id
    )
    registered = register_candidates(db, person, candidates, origem="api")
    audit(db, "pessoa.criada", "people", person.id, user.id, request.state.request_id,
          {"public_code": person.public_code, "candidatos_identidade": len(registered)})
    db.commit()
    data = ser_person(person)
    data["candidatos_identidade"] = len(registered)
    return data


@router.get("/{person_id}")
def get_person(
    person_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    person = _get_person(db, person_id)
    data = ser_person(person)
    aliases = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == "people", LegacyAlias.entity_id == person.id
        )
    ).scalars().all()
    data["aliases_legados"] = [
        {"legacy_source": a.legacy_source, "legacy_id": a.legacy_id} for a in aliases
    ]
    return data


@router.patch("/{person_id}")
def update_person(
    person_id: str,
    payload: PersonUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    person = _get_person(db, person_id)
    changed = []
    if payload.status is not None:
        person.status = payload.status
        changed.append("status")
    if payload.nao_contatar is not None:
        person.nao_contatar = payload.nao_contatar
        changed.append("nao_contatar")
    if payload.observacao is not None:
        person.observacao = payload.observacao
        changed.append("observacao")
    audit(db, "pessoa.atualizada", "people", person.id, user.id,
          request.state.request_id, {"campos": changed})
    db.commit()
    return ser_person(person)


@router.post("/{person_id}/contatos", status_code=201)
def add_contact(
    person_id: str,
    payload: ContactIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    person = _get_person(db, person_id)
    contact = _add_contact(db, person, payload)
    audit(db, "pessoa.contato_adicionado", "people", person.id, user.id,
          request.state.request_id, {"tipo": payload.tipo})
    db.commit()
    return {"id": contact.id, "tipo": contact.tipo, "valor": contact.valor}


@router.get("/{person_id}/consentimentos")
def list_consents(
    person_id: str,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    _get_person(db, person_id)
    stmt = (
        select(Consent).where(Consent.person_id == person_id).order_by(Consent.ts_utc.desc())
    )
    return paginate(db, stmt, params, ser_consent)


@router.post("/{person_id}/consentimentos", status_code=201)
def add_consent(
    person_id: str,
    payload: ConsentIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    _get_person(db, person_id)
    consent = Consent(
        person_id=person_id,
        canal=payload.canal,
        status=payload.status,
        origem=payload.origem,
        observacao=payload.observacao,
        registrado_por=user.id,
    )
    db.add(consent)
    audit(db, "pessoa.consentimento_registrado", "people", person_id, user.id,
          request.state.request_id, {"canal": payload.canal, "status": payload.status})
    db.commit()
    return ser_consent(consent)
