"""Pessoas: cadastro canônico único, contatos, consentimentos, aliases legados."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import allocate_public_code
from ..models import (
    Consent,
    LegacyAlias,
    Person,
    PersonContact,
    PersonRelationship,
    User,
)
from ..normalize import normalize_name, normalize_phone
from ..services.cpf import CPFInvalidoError, normalizar_cpf
from ..pagination import PageParams, paginate
from ..schemas import (
    ConsentIn,
    ContactIn,
    DuplicateCheck,
    IdentificacaoAssistidaIn,
    PersonCpfLookup,
    PersonCreate,
    PersonRelationshipCreate,
    PersonRelationshipDeactivate,
    PersonSearch,
    PersonUpdate,
    PatientRegistrationUpdate,
)
from ..security import (
    ROLE_LEITURA,
    ROLE_OPERACIONAL,
    require_patient_registration_editor,
    require_role,
)
from ..serializers import ser_consent, ser_person, ser_person_relationship
from ..config import get_settings
from ..services.identificacao_assistida import (
    avaliar_comprovante,
    cache_consultas,
    chave_do_par,
    emitir_comprovante,
    get_consulta_cpf_provider,
    limite_consultas,
    pessoa_minima,
    pessoa_por_cpf,
)
from ..services.identity import find_person_candidates, register_candidates
from ..services.serpro_cpf import SerproCpfClient
from ..services.person_registration import build_person
from ..services.relationships import RelationshipError, create_relationship

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


def _registration_contact(person: Person, kinds: tuple[str, ...]) -> PersonContact | None:
    active = [c for c in person.contacts if c.ativo and c.tipo in kinds]
    active.sort(key=lambda c: (not c.principal, c.created_at, c.id))
    return active[0] if active else None


def _audit_registration_change(
    db: Session,
    request: Request,
    person: Person,
    user: User,
    field: str,
    before,
    after,
) -> None:
    """Uma linha por campo: usuário/horário vêm das colunas do AuditLog."""

    def serial(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    audit(
        db,
        "pessoa.cadastro_campo_alterado",
        "people",
        person.id,
        user.id,
        request.state.request_id,
        {
            "campo": field,
            "valor_anterior": serial(before),
            "valor_novo": serial(after),
        },
    )


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
        phone = normalize_phone(payload.q)
        digits_in_q = sum(c.isdigit() for c in payload.q)
        if payload.q.upper().startswith("PES-"):
            stmt = stmt.where(Person.public_code == payload.q.upper())
        elif phone and digits_in_q >= 8 and digits_in_q >= len(payload.q.strip()) // 2:
            # Termo majoritariamente numérico: busca por telefone normalizado
            # nos contatos (whatsapp/telefone) — candidato, nunca prova.
            stmt = stmt.join(
                PersonContact, PersonContact.person_id == Person.id
            ).where(PersonContact.valor_normalizado == phone).distinct()
        else:
            stmt = stmt.where(Person.nome_normalizado.like(f"%{term}%"))
    if payload.status:
        stmt = stmt.where(Person.status == payload.status)
    if payload.nao_contatar is not None:
        stmt = stmt.where(Person.nao_contatar == payload.nao_contatar)
    params = PageParams(pagina=payload.pagina, tamanho=payload.tamanho)
    return paginate(db, stmt, params, ser_person)


@router.post("/verificar-duplicados")
def check_duplicates(
    payload: DuplicateCheck,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Pré-checagem de duplicados sem efeito colateral: nada é criado nem
    registrado — apenas devolve candidatos para aviso na UI antes do cadastro."""
    phones = [p for p in (normalize_phone(t) for t in payload.telefones) if p]
    candidates = find_person_candidates(
        db, normalize_name(payload.nome_completo), phones
    )
    return {
        "total": len(candidates),
        "candidatos": [
            {
                "id": person.id,
                "public_code": person.public_code,
                "nome_completo": person.nome_completo,
                "data_nascimento": person.data_nascimento.isoformat()
                if person.data_nascimento else None,
                "motivo": motivo,
            }
            for person, motivo in candidates
        ],
    }


def _cpf_ou_422(valor: str | None) -> str | None:
    """CPF normalizado, ou 422 explicando o que corrigir.

    M25.18 — o valor entra no sistema SÓ por aqui. Vazio é ausência
    legítima; preenchido e inválido é recusado, porque um CPF que não fecha
    impresso num laudo é pior que nenhum CPF.
    """

    try:
        return normalizar_cpf(valor)
    except CPFInvalidoError as exc:
        raise HTTPException(
            status_code=422,
            detail={"codigo": exc.codigo, "mensagem": exc.mensagem},
        ) from None


def cpf_obrigatorio_ou_422(valor: str | None) -> str:
    """Como `_cpf_ou_422`, mas vazio também é recusado: aqui o CPF é a chave."""

    cpf = _cpf_ou_422(valor)
    if cpf is None:
        raise HTTPException(
            status_code=422,
            detail={"codigo": "cpf_obrigatorio", "mensagem": "Informe o CPF."},
        )
    return cpf


def recusar_cpf_ja_cadastrado(db: Session, cpf_bruto: str | None) -> None:
    """M68 — CPF igual não é "possível duplicado", é o MESMO paciente.

    Não há confirmação que destrave: o 409 devolve o cadastro existente (só o
    mínimo) para a tela oferecer "Usar este paciente". A unicidade da coluna
    já impediria a gravação; isto transforma o conflito genérico numa
    resposta que a tela sabe explicar.
    """

    cpf = _cpf_ou_422(cpf_bruto)
    if cpf is None:
        return
    existente = pessoa_por_cpf(db, cpf)
    if existente is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "codigo": "cpf_ja_cadastrado",
                "mensagem": "Paciente já cadastrado com este CPF. Use o cadastro existente.",
                "pessoa": pessoa_minima(existente),
            },
        )


def registrar_identificacao_oficial(
    db: Session, request: Request, user: User, person: Person, dados
) -> None:
    """Registra SE o nome veio confirmado pelo SERPRO e SE foi editado.

    Só um código de vocabulário fechado vai para a auditoria — nunca CPF,
    nascimento ou nome. Sem comprovante, nada é registrado.
    """

    codigo = avaliar_comprovante(
        getattr(dados, "identificacao_oficial", None),
        person.cpf, person.data_nascimento, person.nome_completo,
    )
    if codigo is None:
        return
    audit(db, "pessoa.identificacao_oficial", "people", person.id, user.id,
          request.state.request_id, {"identificacao_oficial": codigo})


@router.post("/busca-cpf")
def search_person_by_cpf(
    payload: PersonCpfLookup,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """M68 — busca EXATA por CPF no corpo POST: nunca parcial, nunca na URL.

    Não reaproveita a heurística de telefone de `/pessoas/busca` (11 dígitos
    parecem celular). Devolve só o mínimo para "Paciente já cadastrado".
    """

    cpf = cpf_obrigatorio_ou_422(payload.cpf)
    existente = pessoa_por_cpf(db, cpf)
    return {
        "encontrada": existente is not None,
        "pessoa": pessoa_minima(existente) if existente is not None else None,
    }


_MENSAGENS_IDENTIFICACAO = {
    "confere": "Nome confirmado na Receita Federal via SERPRO.",
    "nao_confere": "CPF e data de nascimento não conferem no cadastro oficial.",
    "dados_recusados": (
        "O cadastro oficial recusou a consulta. Confira CPF e nascimento, "
        "ou preencha o nome manualmente."
    ),
    "protegido": (
        "O cadastro oficial não disponibiliza os dados deste CPF. "
        "Preencha o nome manualmente."
    ),
    "indisponivel": (
        "Não foi possível consultar o cadastro oficial agora. Você pode "
        "preencher o nome manualmente e continuar."
    ),
    "nao_configurada": "Consulta oficial indisponível — preencha o nome manualmente.",
}


@router.post("/identificacao-assistida")
def assisted_identification(
    payload: IdentificacaoAssistidaIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
    provider: SerproCpfClient | None = Depends(get_consulta_cpf_provider),
):
    """M68 — CPF + nascimento → nome oficial (Consulta CPF v3 / SERPRO).

    Assistência de digitação, nunca autoridade: toda falha devolve 200 com um
    `resultado` que a tela traduz em "preencha manualmente". Nada é gravado
    além de um código de resultado na auditoria quando HÁ consulta externa.
    """

    cpf = cpf_obrigatorio_ou_422(payload.cpf)
    nascimento = payload.data_nascimento
    if nascimento.year < 1900 or nascimento > date.today():
        raise HTTPException(
            status_code=422,
            detail={"codigo": "nascimento_invalido",
                    "mensagem": "Informe uma data de nascimento válida."},
        )

    existente = pessoa_por_cpf(db, cpf)
    if existente is not None:
        return {
            "resultado": "cpf_ja_cadastrado",
            "mensagem": "Paciente já cadastrado.",
            "pessoa": pessoa_minima(existente),
        }

    if provider is None:
        return {"resultado": "nao_configurada",
                "mensagem": _MENSAGENS_IDENTIFICACAO["nao_configurada"]}

    chave = chave_do_par(cpf, nascimento)
    consulta = cache_consultas.obter(chave)
    if consulta is None:
        settings = get_settings()
        if not limite_consultas.permitir(
            user.id,
            settings.serpro_cpf_max_consultas_por_usuario,
            settings.serpro_cpf_janela_minutos * 60,
        ):
            raise HTTPException(
                status_code=429,
                detail={"codigo": "limite_consultas",
                        "mensagem": "Muitas consultas ao cadastro oficial em pouco "
                                    "tempo. Preencha o nome manualmente."},
            )
        consulta = provider.consultar(cpf, nascimento)
        cache_consultas.guardar(chave, consulta)
        audit(db, "pessoa.identificacao_assistida", "people", None, user.id,
              request.state.request_id,
              {"consulta_serpro_realizada": True, "resultado": consulta.resultado})
        db.commit()

    resposta = {
        "resultado": consulta.resultado,
        "mensagem": _MENSAGENS_IDENTIFICACAO[consulta.resultado],
    }
    if consulta.resultado == "confere":
        resposta.update({
            "nome_oficial": consulta.nome,
            "nome_social": consulta.nome_social,
            "situacao": {"codigo": consulta.situacao_codigo,
                         "descricao": consulta.situacao_descricao},
            "parcial": consulta.parcial,
            "comprovante": emitir_comprovante(cpf, nascimento, consulta.nome),
        })
    return resposta


@router.post("", status_code=201)
def create_person(
    payload: PersonCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    # M25.26 — a construção mora em `services/person_registration` para ser a
    # MESMA usada pelo fluxo atômico pessoa+atendimento. Duas cópias divergem:
    # uma ganharia o campo novo e a outra criaria pessoa sem ele.
    _cpf_ou_422(payload.cpf)  # recusa cedo, com mensagem de campo
    recusar_cpf_ja_cadastrado(db, payload.cpf)
    person = build_person(
        db,
        nome_completo=payload.nome_completo,
        cpf=payload.cpf,
        data_nascimento=payload.data_nascimento,
        sexo=payload.sexo,
        observacao=payload.observacao,
        contatos=payload.contatos,
        consentimento_whatsapp=payload.consentimento_whatsapp,
        registrado_por=user.id,
    )
    # candidatos de identidade (aviso, nunca fusão automática)
    phones = [
        normalize_phone(c.valor) for c in payload.contatos if c.tipo in ("whatsapp", "telefone")
    ]
    candidates = find_person_candidates(
        db, person.nome_normalizado, [p for p in phones if p], exclude_person_id=person.id
    )
    registered = register_candidates(db, person, candidates, origem="api")
    registrar_identificacao_oficial(db, request, user, person, payload)
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


@router.patch("/{person_id}/cadastro")
def update_patient_registration(
    person_id: str,
    payload: PatientRegistrationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_patient_registration_editor),
):
    """Corrige cadastro sem oferecer qualquer porta para laudo/exame/IDs.

    Cada mudança ganha sua própria linha antes/depois. PDFs já persistidos
    continuam byte a byte intactos; o gerador só consultará estes valores ao
    compor uma versão futura.
    """

    person = _get_person(db, person_id)
    supplied = payload.model_fields_set
    changes: list[tuple[str, object, object]] = []

    if "nome_completo" in supplied and payload.nome_completo is None:
        raise HTTPException(status_code=422, detail="Nome completo não pode ser removido.")
    if "nome_completo" in supplied and payload.nome_completo != person.nome_completo:
        changes.append(("nome_completo", person.nome_completo, payload.nome_completo))
        person.nome_completo = payload.nome_completo
        person.nome_normalizado = normalize_name(payload.nome_completo)

    if "data_nascimento" in supplied and payload.data_nascimento != person.data_nascimento:
        changes.append(("data_nascimento", person.data_nascimento, payload.data_nascimento))
        person.data_nascimento = payload.data_nascimento

    if "sexo" in supplied and payload.sexo != person.sexo:
        changes.append(("sexo", person.sexo, payload.sexo))
        person.sexo = payload.sexo

    contact_specs = (
        ("telefone", ("whatsapp", "telefone"), payload.telefone, "whatsapp"),
        ("email", ("email",), payload.email, "email"),
    )
    for field, kinds, new_value, default_kind in contact_specs:
        if field not in supplied:
            continue
        current = _registration_contact(person, kinds)
        before = current.valor if current else None
        after = (new_value or "").strip() or None
        if after == before:
            continue
        if field == "telefone" and after is not None:
            normalized = normalize_phone(after)
            if len(normalized) < 10:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "codigo": "telefone_invalido",
                        "mensagem": "Informe um telefone com DDD.",
                    },
                )
        else:
            normalized = after.lower() if after else ""
        if current:
            if after is None:
                current.ativo = False
                current.principal = False
            else:
                current.valor = after
                current.valor_normalizado = normalized
                current.principal = True
        elif after is not None:
            db.add(
                PersonContact(
                    person_id=person.id,
                    tipo=default_kind,
                    valor=after,
                    valor_normalizado=normalized,
                    principal=True,
                    ativo=True,
                )
            )
        changes.append((field, before, after))

    for field, before, after in changes:
        _audit_registration_change(db, request, person, user, field, before, after)
    db.commit()
    return ser_person(person)


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
    if payload.cpf is not None:
        # String vazia DESVINCULA; ausente significa "não mexa". Sem essa
        # distinção não haveria como corrigir um CPF cadastrado por engano.
        person.cpf = _cpf_ou_422(payload.cpf)
        changed.append("cpf")
    if payload.nome_completo is not None:
        person.nome_completo = payload.nome_completo
        person.nome_normalizado = normalize_name(payload.nome_completo)
        changed.append("nome_completo")
    if payload.data_nascimento is not None:
        person.data_nascimento = payload.data_nascimento
        changed.append("data_nascimento")
    if payload.sexo is not None:
        person.sexo = payload.sexo
        changed.append("sexo")
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


@router.get("/{person_id}/responsaveis")
def list_guardians(
    person_id: str,
    active: bool | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    _get_person(db, person_id)
    stmt = (
        select(PersonRelationship)
        .where(PersonRelationship.minor_person_id == person_id)
        .order_by(PersonRelationship.created_at, PersonRelationship.id)
    )
    if active is not None:
        stmt = stmt.where(PersonRelationship.active == active)
    return {
        "items": [
            ser_person_relationship(item)
            for item in db.execute(stmt).scalars().all()
        ]
    }


@router.post("/{person_id}/responsaveis", status_code=201)
def add_guardian(
    person_id: str,
    payload: PersonRelationshipCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    try:
        relationship, replay = create_relationship(
            db,
            minor_person_id=person_id,
            guardian_person_id=payload.guardian_person_id,
            relationship_type=payload.relationship_type,
            is_legal_guardian=payload.is_legal_guardian,
            active=payload.active,
        )
    except RelationshipError as exc:
        status = 404 if exc.code.endswith("_not_found") else 409
        raise HTTPException(status_code=status, detail={"codigo": exc.code}) from exc
    if not replay:
        audit(
            db,
            "pessoa.relacionamento_criado",
            "person_relationships",
            relationship.id,
            user.id,
            request.state.request_id,
            {
                "relationship_type": relationship.relationship_type,
                "is_legal_guardian": relationship.is_legal_guardian,
            },
        )
    db.commit()
    return {**ser_person_relationship(relationship), "replay": replay}


@router.post("/{person_id}/responsaveis/{relationship_id}/desativar")
def deactivate_guardian(
    person_id: str,
    relationship_id: str,
    payload: PersonRelationshipDeactivate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    relationship = db.get(PersonRelationship, relationship_id)
    if relationship is None or relationship.minor_person_id != person_id:
        raise HTTPException(status_code=404, detail="Relacionamento não encontrado.")
    if relationship.active:
        relationship.active = False
        audit(
            db,
            "pessoa.relacionamento_desativado",
            "person_relationships",
            relationship.id,
            user.id,
            request.state.request_id,
            {"reason_code": payload.reason_code},
        )
        db.commit()
    return ser_person_relationship(relationship)
