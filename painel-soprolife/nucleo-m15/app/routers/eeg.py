"""EEGLife: People corporativo, atendimento e fluxo clínico próprio.

RBAC EEG por capacidade no servidor; médico respiratório não herda EEG.
Busca de identidade usa corpo POST. Razão e motor fiscal são corporativos.
Importação privada, bancada e gestão complementam estas rotas em eeg_operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
# Erro de domínio da casa: produz {"erro": {"codigo", "mensagem"}}. O nome é
# herdado do M24A, mas o contrato é geral — é o envelope que o painel já lê.
from ..errors import ReportDomainError as DomainError
from ..ids import allocate_public_code
from ..models import (
    EegEnrollment,
    EegExam,
    EegReportDispatch,
    EegReportDocument,
    EegResultAccess,
    EegTechnicalFile,
    PartnerUnit,
    Person, PersonContact, FinancialEntry,
    User,
)
from ..normalize import normalize_name, normalize_phone
from ..pagination import PageParams, paginate
from ..schemas import (
    EegPessoaBusca, EegAtendimentoCreate, EegPagamentoUpdate,
    EegArquivoTecnicoCreate,
    EegEnvioLaudoCreate,
    EegExameCreate,
    EegExameStatusUpdate,
    EegPessoaCreate,
    EegVinculoCreate,
)
from ..security import (require_eeg_access, require_eeg_operator, require_eeg_technician, require_eeg_finance, user_effective_roles, require_eeg_roles)
from ..serializers import ser_person
from ..services import eeg_flow
from ..services.eeg_workflow import case_for, serialize as serialize_case, require_exam
from ..models import EegCase, EegProfessional
from ..services.cpf import CPFInvalidoError, normalizar_cpf
from ..services.eeg_modalidades import MODALIDADES, modalidade_ativa, obter
from ..services.eeg_storage import eeg_report_storage_path, atomic_write_new_file
from ..services.report_storage import read_and_validate_stored_pdf, ReportStorageError
from ..services import eeg_results, patient_results
from ..services.identity import find_person_candidates
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from ..services.person_registration import add_contact
from ..services.idempotency import idempotent_create, payload_fingerprint

router = APIRouter(prefix="/eeg", tags=["eeg"])

ORIGENS_VALIDAS = {"direto", "parceiro"}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


# ── Serialização ──────────────────────────────────────────────────────────

def _ser_modalidade(m) -> dict:
    return {
        "codigo": m.codigo,
        "nome": m.nome,
        "tambem_chamado": m.tambem_chamado,
        "duracao_minutos_aproximada": m.duracao_minutos_aproximada,
        "preparo": None,
        "preparo_status": "consultar_aprovacao_por_modalidade",
        "ativo": m.ativo,
        "ativa_operacionalmente": False,
        "situacao_tecnica": "validacao_tecnica_pendente" if m.ativo else "inativa",
        "validacao_tecnica": "pendente",
        "aviso_tecnico": "Pendente de validação técnica no Nano/iCelera; cadastro não confirma capacidade.",
    }


def _ser_vinculo(db: Session, enrollment: EegEnrollment) -> dict:
    person = db.get(Person, enrollment.person_id)
    total = db.execute(
        select(func.count()).select_from(EegExam)
        .where(EegExam.enrollment_id == enrollment.id)
    ).scalar_one()
    return {
        "id": enrollment.id,
        "status": enrollment.status,
        "origem": enrollment.origem,
        "observacao": enrollment.observacao,
        "consentimento_whatsapp": enrollment.consentimento_whatsapp,
        "exames": total,
        # A pessoa vem do serializador CORPORATIVO: mesma identidade, mesmo
        # mascaramento de CPF, mesmas pendências de cadastro.
        "pessoa": ser_person(person) if person else None,
    }


def _ser_exame(db: Session, exam: EegExam) -> dict:
    enrollment = db.get(EegEnrollment, exam.enrollment_id)
    person = db.get(Person, enrollment.person_id) if enrollment else None
    unidade = db.get(PartnerUnit, exam.partner_unit_id) if exam.partner_unit_id else None
    modalidade = obter(exam.modalidade_codigo)
    tecnico = db.execute(
        select(EegTechnicalFile).where(EegTechnicalFile.exam_id == exam.id)
        .order_by(EegTechnicalFile.criado_em.desc())
    ).scalars().first()
    envio = db.execute(
        select(EegReportDispatch)
        .where(EegReportDispatch.exam_id == exam.id,
               EegReportDispatch.status == "enviado")
        .order_by(EegReportDispatch.enviado_em.desc())
    ).scalars().first()
    laudo = db.execute(
        select(EegReportDocument)
        .where(EegReportDocument.exam_id == exam.id,
               EegReportDocument.status != "substituido")
        .order_by(EegReportDocument.versao.desc())
    ).scalars().first()
    financial_status = db.scalar(select(FinancialEntry.status).where(FinancialEntry.eeg_exam_id == exam.id))
    return {
        "situacao_financeira": financial_status or "Sem receita vinculada",
        "fluxo": serialize_case(case_for(db, exam.id)),
        "id": exam.id,
        "public_code": exam.public_code,
        "enrollment_id": exam.enrollment_id,
        "paciente": {
            "id": person.id, "nome_completo": person.nome_completo,
            "public_code": person.public_code,
        } if person else None,
        "modalidade_codigo": exam.modalidade_codigo,
        "modalidade_nome": modalidade.nome if modalidade else None,
        "origem_tipo": exam.origem_tipo,
        "partner_unit_id": exam.partner_unit_id,
        "local_nome": exam.local_atendimento or (unidade.nome if unidade else ("Direto EEGLife" if exam.origem_tipo == "direto" else None)),
        "municipio_atendimento_ibge": exam.municipio_atendimento_ibge,
        "origem_canal": exam.origem_canal,
        "status": exam.status,
        "agendado_para": _iso(exam.agendado_para),
        "realizado_em": _iso(exam.realizado_em),
        "enviado_para_laudo_em": _iso(exam.enviado_para_laudo_em),
        "laudo_recebido_em": _iso(exam.laudo_recebido_em),
        "disponibilizado_em": _iso(exam.disponibilizado_em),
        "cancelado_em": _iso(exam.cancelado_em),
        "tecnico_responsavel": exam.tecnico_responsavel,
        "observacao": exam.observacao,
        # Situação do arquivo técnico: nunca o arquivo, nunca um caminho.
        "arquivo_tecnico": {
            "id": tecnico.id,
            "privado": bool(tecnico.object_key),
            "registrado": True,
            "armazenamento_origem": tecnico.armazenamento_origem,
            "formato": tecnico.formato,
            "referencia_externa": tecnico.referencia_externa,
            "tamanho_bytes": tecnico.tamanho_bytes,
        } if tecnico else {"registrado": False},
        "envio_laudo": {
            "destino_tipo": envio.destino_tipo,
            "destino_nome": envio.destino_nome,
            "metodo": envio.metodo,
            "protocolo_externo": envio.protocolo_externo,
            "enviado_em": _iso(envio.enviado_em),
        } if envio else None,
        "laudo": {
            "id": laudo.id,
            "public_code": laudo.public_code,
            "versao": laudo.versao,
            "status": laudo.status,
            "medico_laudador": laudo.medico_laudador,
            "recebido_em": _iso(laudo.recebido_em),
            "disponibilizado_em": _iso(laudo.disponibilizado_em),
        } if laudo else None,
    }


# ── Catálogo ──────────────────────────────────────────────────────────────

@router.get("/modalidades")
def listar_modalidades(_user: User = Depends(require_eeg_access)):
    """Catálogo real da EEGLife. Não afirma disponibilidade de serviço."""
    return {"itens": [_ser_modalidade(m) for m in MODALIDADES]}


@router.get("/locais")
def listar_locais(
    db: Session = Depends(get_db),
    _user: User = Depends(require_eeg_operator),
):
    """Unidades parceiras CORPORATIVAS, reaproveitadas sem duplicar empresa.

    Pastore, coworkings e demais parceiros já existem no cadastro corporativo.
    A EEGLife aponta para a mesma unidade; o vínculo comercial por marca é
    assunto de outra fase e não é tocado aqui.
    """
    unidades = db.execute(
        select(PartnerUnit).where(PartnerUnit.ativo.is_(True))
        .order_by(PartnerUnit.nome)
    ).scalars().all()
    return {"itens": [
        {"id": u.id, "public_code": u.public_code, "nome": u.nome,
         "cidade": u.cidade, "bairro": u.bairro}
        for u in unidades
    ]}


def _candidatos(db, payload):
    candidatos = find_person_candidates(
        db, normalize_name(payload.nome_completo),
        [normalize_phone(payload.telefone)] if payload.telefone else [],
    )
    if payload.cpf:
        try:
            cpf = normalizar_cpf(payload.cpf)
        except CPFInvalidoError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        pessoa = db.execute(select(Person).where(Person.cpf == cpf)).scalar_one_or_none()
        if pessoa and all(p.id != pessoa.id for p, _ in candidatos):
            candidatos.insert(0, (pessoa, "cpf"))
    return candidatos


# ── Central de Cadastros (escopo EEG) ─────────────────────────────────────

@router.get("/pessoas")
def listar_pessoas_eeg(
    busca: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    params: PageParams = Depends(),
    _user: User = Depends(require_eeg_operator),
):
    """Somente quem tem vínculo operacional com a EEGLife.

    A consulta parte de `eeg_enrollments` e só então junta `people`. Pessoa da
    SoproLife sem vínculo EEG não aparece aqui por construção — não por
    filtro que alguém possa esquecer de aplicar.
    """
    stmt = (
        select(EegEnrollment)
        .join(Person, Person.id == EegEnrollment.person_id)
        .where(EegEnrollment.status == "ativo")
    )
    if busca:
        alvo = f"%{normalize_name(busca)}%"
        stmt = stmt.where(Person.nome_normalizado.like(alvo))
    stmt = stmt.order_by(Person.nome_completo)
    return paginate(db, stmt, params, lambda e: _ser_vinculo(db, e))


@router.post("/pessoas/busca")
def buscar_pessoas_eeg(payload: EegPessoaBusca, db: Session = Depends(get_db),
                       _user: User = Depends(require_eeg_operator)):
    """Pesquisa explícita no cadastro corporativo; PII só no corpo, nunca URL."""
    stmt = select(Person).where(Person.arquivado.is_(False))
    if not payload.corporativo:
        stmt = stmt.join(EegEnrollment).where(EegEnrollment.status == "ativo")
    q = (payload.q or "").strip()
    if payload.corporativo and len(q) < 3:
        raise DomainError(422, "busca_insuficiente", "Informe pelo menos três caracteres para procurar pessoa existente.")
    if q:
        digits = "".join(c for c in q if c.isdigit())
        if q.upper().startswith("PES-"):
            stmt = stmt.where(Person.public_code == q.upper())
        elif len(digits) >= 8:
            phone = normalize_phone(q)
            contacts = select(PersonContact.person_id).where(
                PersonContact.ativo.is_(True), PersonContact.tipo.in_(["telefone", "whatsapp"]),
                PersonContact.valor_normalizado == phone)
            stmt = stmt.where(or_(Person.cpf == digits, Person.id.in_(contacts)))
        else:
            term = normalize_name(q).replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Person.nome_normalizado.like(f"%{term}%", escape="\\"))
    def serialize(person):
        vinculo = db.scalar(select(EegEnrollment).where(EegEnrollment.person_id == person.id))
        return {"pessoa": ser_person(person), "id": vinculo.id if vinculo else None,
                "ja_vinculada": bool(vinculo and vinculo.status == "ativo"),
                "exames": db.scalar(select(func.count()).select_from(EegExam).where(
                    EegExam.enrollment_id == vinculo.id)) if vinculo else 0}
    return paginate(db, stmt.order_by(Person.nome_completo, Person.id),
                    PageParams(pagina=payload.pagina, tamanho=payload.tamanho), serialize)


@router.post("/pessoas/vincular", status_code=201)
def vincular_pessoa(
    payload: EegVinculoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_operator),
):
    """Vincula pessoa existente à EEGLife — SEM duplicar identidade.

    É o caminho de quem já fez espirometria e agora fará EEG. O cadastro da
    pessoa não é copiado nem alterado: só passa a existir um vínculo.
    """
    person = db.get(Person, payload.person_id)
    if not person or person.arquivado:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada ou arquivada.")
    existente = db.execute(
        select(EegEnrollment).where(EegEnrollment.person_id == person.id)
    ).scalars().first()
    if existente:
        if existente.status != "ativo":
            existente.status = "ativo"
            audit(db, "eeg.vinculo_reativado", "eeg_enrollments", existente.id, user_id=user.id,
                  request_id=request.state.request_id, detalhes={"status": "ativo"})
            db.commit()
        return _ser_vinculo(db, existente)

    enrollment = EegEnrollment(
        person_id=person.id, origem=payload.origem,
        observacao=payload.observacao, criado_por=user.id,
    )
    db.add(enrollment)
    db.flush()
    audit(db, "eeg.vinculo_criado", "eeg_enrollments", enrollment.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"origem": payload.origem, "reaproveitou_identidade": True})
    db.commit()
    db.refresh(enrollment)
    return _ser_vinculo(db, enrollment)


@router.post("/pessoas/verificar")
def verificar_duplicados_eeg(
    payload: EegPessoaCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(require_eeg_operator),
):
    """Candidatos de identidade ANTES de cadastrar — sem efeito colateral.

    Mesma regra de deduplicação do cadastro corporativo. Serve para a tela
    oferecer "vincular esta pessoa" em vez de criar uma segunda.
    """
    telefone = normalize_phone(payload.telefone) if payload.telefone else None
    candidatos = _candidatos(db, payload)
    return {"itens": [
        {"person_id": p.id, "public_code": p.public_code,
         "nome_completo": p.nome_completo, "motivo": motivo,
         # Já vinculada? A tela precisa saber se oferece vincular ou abrir.
         "ja_vinculada": db.execute(
             select(EegEnrollment).where(EegEnrollment.person_id == p.id)
         ).scalars().first() is not None}
        for p, motivo in candidatos
    ]}


@router.post("/pessoas", status_code=201)
def criar_pessoa_eeg(
    payload: EegPessoaCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_operator),
):
    """Cadastra pessoa NOVA já vinculada à EEGLife.

    Antes de criar, procura correspondência pela MESMA regra de deduplicação
    do painel (`find_person_candidates`). Havendo candidato, recusa com 409 e
    devolve a lista: reaproveitar a identidade existente é decisão do
    operador, e é assim que a pessoa não vira duas por ter mudado de marca.
    """
    nome_normalizado = normalize_name(payload.nome_completo)
    candidatos = _candidatos(db, payload)
    if payload.cpf:
        from .people import recusar_cpf_ja_cadastrado
        recusar_cpf_ja_cadastrado(db, payload.cpf)
    if candidatos and not payload.confirmar_nova_pessoa:
        raise DomainError(
            409, "pessoa_possivelmente_existente",
            "Há pessoa com dados semelhantes no cadastro corporativo. "
            "Consulte /eeg/pessoas/verificar, vincule a existente ou confirme "
            "que é outra pessoa.",
        )

    cpf_normalizado = None
    if payload.cpf:
        try:
            cpf_normalizado = normalizar_cpf(payload.cpf)
        except CPFInvalidoError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    person = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo=payload.nome_completo,
        nome_normalizado=nome_normalizado,
        cpf=cpf_normalizado,
        data_nascimento=payload.data_nascimento,
        sexo=payload.sexo,
    )
    db.add(person)
    db.flush()
    if payload.telefone:
        add_contact(db, person, "whatsapp", payload.telefone, True)
    if payload.email:
        add_contact(db, person, "email", payload.email, True)
    audit(db, "pessoa.criada", "people", person.id, user_id=user.id,
          request_id=request.state.request_id, detalhes={"contexto": "eeglife",
          "candidatos_identidade": [p.id for p, _ in candidatos],
          "decisao": "nova_identidade_confirmada" if candidatos else "nova_identidade"})

    enrollment = EegEnrollment(
        person_id=person.id, origem=payload.origem,
        consentimento_whatsapp=payload.consentimento_whatsapp or "desconhecido",
        observacao=payload.observacao, criado_por=user.id,
    )
    db.add(enrollment)
    db.flush()
    audit(db, "eeg.consentimento_registrado", "eeg_enrollments", enrollment.id,
          user_id=user.id, request_id=request.state.request_id,
          detalhes={"contexto": "eeglife", "canal": "whatsapp", "status": enrollment.consentimento_whatsapp})
    audit(db, "eeg.vinculo_criado", "eeg_enrollments", enrollment.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"origem": payload.origem, "pessoa_nova": True})
    db.commit()
    db.refresh(enrollment)
    return _ser_vinculo(db, enrollment)


# ── Exames e traçados ─────────────────────────────────────────────────────

@router.get("/exames")
def listar_exames(
    status: str | None = Query(default=None, max_length=30),
    db: Session = Depends(get_db),
    params: PageParams = Depends(),
    _user: User = Depends(require_eeg_access),
):
    stmt = select(EegExam)
    roles = user_effective_roles(_user)
    if not roles & {'admin', 'eeg_operador', 'eeg_tecnico'}:
        if 'medical_eeg' not in roles: raise HTTPException(403, 'Papel clínico/operacional EEG necessário.')
        stmt = stmt.join(EegCase, EegCase.exam_id == EegExam.id).join(EegProfessional, EegProfessional.id == EegCase.professional_id).where(EegProfessional.user_id == _user.id, EegProfessional.ativo.is_(True))
    if status:
        stmt = stmt.where(EegExam.status == status)
    stmt = stmt.order_by(EegExam.created_at.desc())
    return paginate(db, stmt, params, lambda e: _ser_exame(db, e))


@router.post("/exames", status_code=201)
def criar_exame(
    payload: EegExameCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_operator),
):
    enrollment = db.get(EegEnrollment, payload.enrollment_id)
    if not enrollment or enrollment.status != "ativo":
        raise HTTPException(status_code=404, detail="Vínculo EEGLife não encontrado.")
    person = db.get(Person, enrollment.person_id)
    if not person or person.arquivado:
        raise DomainError(409, "pessoa_arquivada", "Pessoa arquivada não pode receber atendimento.")
    if not modalidade_ativa(payload.modalidade_codigo):
        raise DomainError(422, "modalidade_invalida",
                          "Modalidade de EEG desconhecida ou inativa.")
    if payload.origem_tipo not in ORIGENS_VALIDAS:
        raise HTTPException(status_code=422, detail="Origem inválida.")
    if payload.origem_tipo == "parceiro":
        if not payload.partner_unit_id:
            raise HTTPException(
                status_code=422,
                detail="Origem de parceiro exige a unidade parceira.")
        unit = db.get(PartnerUnit, payload.partner_unit_id)
        if not unit or not unit.ativo:
            raise HTTPException(status_code=404, detail="Unidade parceira não encontrada.")
    elif payload.partner_unit_id:
        raise HTTPException(
            status_code=422, detail="Origem direta não aceita unidade parceira.")

    exam = EegExam(
        public_code=allocate_public_code(db, "eeg_exams"),
        enrollment_id=enrollment.id,
        modalidade_codigo=payload.modalidade_codigo,
        origem_tipo=payload.origem_tipo,
        partner_unit_id=payload.partner_unit_id,
        status=eeg_flow.AGENDADO if payload.agendado_para else eeg_flow.CADASTRO,
        agendado_para=payload.agendado_para,
        tecnico_responsavel=payload.tecnico_responsavel,
        observacao=payload.observacao,
        criado_por=user.id,
    )
    db.add(exam)
    db.flush()
    audit(db, "eeg.exame_criado", "eeg_exams", exam.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"codigo": exam.modalidade_codigo, "status": exam.status,
                    "origem_tipo": exam.origem_tipo})
    if not isinstance(payload, EegAtendimentoCreate):
        db.commit()
        db.refresh(exam)
        return _ser_exame(db, exam)
    return exam


@router.post("/atendimentos", status_code=201)
def novo_atendimento(payload: EegAtendimentoCreate, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_eeg_operator)):
    identity = payload.model_dump(mode="json", exclude={"idempotency_key"})
    def factory(key, fingerprint):
        exam = criar_exame(payload, request, db, user)
        exam.local_atendimento = payload.local_atendimento
        exam.municipio_atendimento_ibge = payload.municipio_atendimento_ibge
        exam.origem_canal = payload.origem_canal
        exam.idempotency_key, exam.idempotency_fingerprint = key, fingerprint
        from ..services.nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES
        if payload.municipio_atendimento_ibge not in SUPPORTED_SERVICE_MUNICIPALITIES:
            raise DomainError(422,'municipio_invalido','Selecione município do catálogo IBGE operacional.')
        if payload.reteste_sem_cobranca:
            db.add(EegCase(exam_id=exam.id, reteste_sem_cobranca=True))
        else:
            db.add(FinancialEntry(public_code=allocate_public_code(db, "financial_entries"),
            tipo="receita", categoria="EEG", descricao="Atendimento EEG " + exam.public_code,
            valor=payload.valor, moeda="BRL", status=payload.pagamento_status, forma_pagamento=payload.forma_pagamento, data_recebimento=payload.data_recebimento,
            eeg_exam_id=exam.id, data_competencia=payload.agendado_para.astimezone(ZoneInfo("America/Sao_Paulo")).date()))
        db.flush()
        audit(db, "eeg.atendimento_criado", "eeg_exams", exam.id, user_id=user.id,
              request_id=request.state.request_id, detalhes={"contexto": "eeglife", "status": exam.status})
        return exam
    exam, _ = idempotent_create(db, EegExam, payload.idempotency_key, identity, factory)
    db.commit()
    return _ser_exame(db, exam)


@router.get("/financeiro")
def financeiro_eeg(db: Session = Depends(get_db), params: PageParams = Depends(),
                   _user: User = Depends(require_eeg_finance)):
    from ..serializers import ser_financial_entry
    def financial_row(entry):
        e = db.get(EegExam, entry.eeg_exam_id)
        unit = db.get(PartnerUnit,e.partner_unit_id) if e and e.partner_unit_id else None
        actor = db.get(User,e.criado_por) if e and e.criado_por else None
        return {**ser_financial_entry(entry), 'eeg_exam_id':entry.eeg_exam_id,
            'marca':'eeglife', 'exame':e.public_code if e else None,
            'origem':e.origem_canal if e else None, 'parceiro':unit.nome if unit else None,
            'responsavel':actor.nome if actor else None}
    return paginate(db, select(FinancialEntry).where(FinancialEntry.eeg_exam_id.is_not(None))
        .order_by(FinancialEntry.created_at.desc()), params, financial_row)



@router.patch("/financeiro/{entry_id}/pagamento")
def atualizar_pagamento(entry_id: str, payload: EegPagamentoUpdate, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(require_eeg_finance)):
    from ..models import FiscalDocument, FiscalAttempt, FiscalIssuanceRequest
    entry = db.scalar(select(FinancialEntry).where(FinancialEntry.id == entry_id,
        FinancialEntry.eeg_exam_id.is_not(None)).with_for_update())
    if not entry:
        raise HTTPException(404, "Receita EEG não encontrada.")
    doc = db.scalar(select(FiscalDocument).where(FiscalDocument.eeg_exam_id == entry.eeg_exam_id,
        FiscalDocument.environment == "production"))
    if doc and (doc.state not in {"blocked", "pending"} or db.scalar(select(FiscalAttempt.id).where(
            FiscalAttempt.document_id == doc.id).limit(1)) or db.scalar(select(FiscalIssuanceRequest.id).where(
            FiscalIssuanceRequest.document_id == doc.id, FiscalIssuanceRequest.status.in_(["authorized", "running"])).limit(1))):
        raise DomainError(409, "receita_fiscal_congelada", "Receita vinculada a emissão fiscal; requer revisão administrativa.")
    if payload.status == "Recebido" and not payload.data_recebimento:
        raise DomainError(422, "data_recebimento_obrigatoria", "Informe a data real do recebimento.")
    if payload.data_recebimento and payload.data_recebimento > _agora().date():
        raise DomainError(422, "recebimento_futuro", "Recebimento não pode estar no futuro.")
    anterior = entry.status
    entry.status = payload.status
    entry.data_recebimento = payload.data_recebimento if payload.status == "Recebido" else None
    audit(db, "eeg.pagamento_atualizado", "financial_entries", entry.id, user_id=user.id,
          request_id=request.state.request_id, detalhes={"contexto": "eeglife", "status": entry.status,
          "status_anterior": anterior})
    db.commit()
    return {"id": entry.id, "status": entry.status, "marca": "eeglife"}


@router.patch("/exames/{exam_id}/status")
def mudar_status(
    exam_id: str,
    payload: EegExameStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_roles("eeg_operador", "eeg_tecnico")),
):
    """Transição conferida no servidor. Pulo de etapa é recusado, não corrigido."""
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
    if not user_effective_roles(user) & {'admin','eeg_operador'} and payload.status != eeg_flow.REALIZADO:
        raise HTTPException(403, 'Técnico pode registrar realização; agenda e entrega exigem operador EEG.')
    anterior = exam.status
    try:
        eeg_flow.garantir_transicao(anterior, payload.status)
    except eeg_flow.TransicaoInvalidaError as exc:
        raise DomainError(409, "transicao_invalida", str(exc)) from exc

    if payload.status in {eeg_flow.ENVIADO, eeg_flow.LAUDO_RECEBIDO}:
        raise DomainError(409, "evento_exige_registro",
                          "Use o registro de envio ou o recebimento do PDF para esta etapa.")

    # `laudo_disponivel` exige laudo de verdade: não se entrega ao paciente o
    # que ainda não chegou do laudador.
    if payload.status == eeg_flow.LAUDO_DISPONIVEL:
        from .eeg_operations import action
        from ..schemas_eeg import CaseAction
        action(exam.id, CaseAction(acao='publicar', confirmacao_codigo=exam.public_code),request,db,user)
        return _ser_exame(db,exam)

    exam.status = payload.status
    carimbo = eeg_flow.CARIMBO.get(payload.status)
    if carimbo:
        setattr(exam, carimbo, payload.ocorrido_em or _agora())
    if payload.observacao:
        exam.observacao = payload.observacao
    audit(db, "eeg.exame_status", "eeg_exams", exam.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"status_anterior": anterior, "status": payload.status})
    db.commit()
    db.refresh(exam)
    return _ser_exame(db, exam)


@router.post("/exames/{exam_id}/arquivo-tecnico", status_code=201)
def registrar_arquivo_tecnico(
    exam_id: str,
    payload: EegArquivoTecnicoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_technician),
):
    """Registra que o traçado EXISTE — e onde. Nunca recebe o traçado.

    Não há upload aqui, de propósito: um EEG digital tem dezenas a centenas de
    MB e monitorização prolongada chega a GB. Fingir que o arquivo chegou ao
    Centro de Comando seria pior do que dizer onde ele está.
    """
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
    case = case_for(db,exam.id)
    if exam.status in {eeg_flow.LAUDO_RECEBIDO,eeg_flow.LAUDO_DISPONIVEL} or (case and case.current_file_id):
        raise DomainError(409,'referencia_congelada','Use as versões privadas antes da conclusão; referência não substitui arquivo recebido.')
    registro = EegTechnicalFile(
        exam_id=exam.id,
        armazenamento_origem=payload.armazenamento_origem,
        referencia_externa=payload.referencia_externa,
        nome_logico=payload.nome_logico,
        formato=payload.formato,
        mime_type=payload.mime_type,
        tamanho_bytes=payload.tamanho_bytes,
        sha256=payload.sha256,
        duracao_segundos=payload.duracao_segundos,
        canais=payload.canais,
        frequencia_hz=payload.frequencia_hz,
        equipamento=payload.equipamento,
        observacao=payload.observacao,
        criado_por=user.id,
    )
    if exam.status not in eeg_flow.EXAME_JA_REALIZADO:
        raise DomainError(409, "exame_nao_realizado", "Registre a realização antes da referência técnica.")
    db.add(registro)
    db.flush()
    audit(db, "eeg.arquivo_tecnico_registrado", "eeg_technical_files", registro.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"exam_code": exam.public_code,
                    "armazenamento_origem": registro.armazenamento_origem})
    db.commit()
    db.refresh(exam)
    return _ser_exame(db, exam)


# ── Envio ao laudador ─────────────────────────────────────────────────────

@router.post("/exames/{exam_id}/envio-laudo", status_code=201)
def registrar_envio(
    exam_id: str,
    payload: EegEnvioLaudoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_operator),
):
    """Registra o envio ao laudador — empresa de telelaudo ou médico direto.

    Agnóstico de provedor e manual: nenhuma API de terceiro é chamada nem
    presumida. O que fica gravado é quem enviou, para onde, por qual meio e
    com qual protocolo externo, se houver.
    """
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
    if exam.status not in {eeg_flow.REALIZADO, eeg_flow.AGUARDANDO_ENVIO,
                           eeg_flow.ENVIADO}:
        raise DomainError(
            409, "exame_nao_realizado",
            f"Só um exame realizado pode ser enviado para laudo "
            f"(status atual: {exam.status}).")
    if payload.destino_tipo not in {"empresa", "medico"}:
        raise HTTPException(status_code=422, detail="Destino inválido.")
    if payload.metodo not in {"plataforma_externa", "upload_manual_externo", "outro"}:
        raise HTTPException(status_code=422, detail="Método de envio inválido.")

    # Reenvio: o envio anterior vira `substituido` em vez de sumir.
    anteriores = db.execute(
        select(EegReportDispatch).where(
            EegReportDispatch.exam_id == exam.id,
            EegReportDispatch.status == "enviado")
    ).scalars().all()
    for anterior in anteriores:
        anterior.status = "substituido"

    envio = EegReportDispatch(
        exam_id=exam.id,
        destino_tipo=payload.destino_tipo,
        destino_nome=payload.destino_nome,
        metodo=payload.metodo,
        protocolo_externo=payload.protocolo_externo,
        observacao=payload.observacao,
        enviado_em=payload.enviado_em or _agora(),
        enviado_por=user.id,
    )
    db.add(envio)
    exam.status = eeg_flow.ENVIADO
    exam.enviado_para_laudo_em = envio.enviado_em
    db.flush()
    audit(db, "eeg.enviado_para_laudo", "eeg_report_dispatches", envio.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"exam_code": exam.public_code, "tipo": envio.destino_tipo,
                    "canal": envio.metodo, "reenvio": bool(anteriores)})
    db.commit()
    db.refresh(exam)
    return _ser_exame(db, exam)


# ── Laudos de EEG ─────────────────────────────────────────────────────────

@router.get("/laudos")
def listar_laudos(
    db: Session = Depends(get_db),
    params: PageParams = Depends(),
    _user: User = Depends(require_eeg_access),
):
    """Fila PRÓPRIA da EEGLife.

    Não consulta `report_documents`: laudo de espirometria não entra aqui, e
    laudo de EEG não entra lá. São exames, autorias e documentos diferentes.
    """
    stmt = select(EegExam).where(EegExam.status.in_(tuple(eeg_flow.EXAME_JA_REALIZADO))).order_by(EegExam.realizado_em.desc())
    roles = user_effective_roles(_user)
    if not roles & {'admin','eeg_operador','eeg_tecnico'}:
        if 'medical_eeg' not in roles: raise HTTPException(403, 'Papel clínico/operacional EEG necessário.')
        stmt = stmt.join(EegCase, EegCase.exam_id == EegExam.id).join(EegProfessional, EegProfessional.id == EegCase.professional_id).where(EegProfessional.user_id == _user.id, EegProfessional.ativo.is_(True))
    return paginate(db, stmt, params, lambda e: _ser_exame(db, e))


@router.post("/exames/{exam_id}/laudo", status_code=201)
def receber_laudo(
    exam_id: str,
    request: Request,
    arquivo: UploadFile = File(...),
    medico_laudador: str | None = Form(default=None),
    observacao: str | None = Form(default=None),
    assinatura_recebida: bool = Form(default=False),
    confirmacao_codigo: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_operator),
):
    """Recebe o PDF que voltou do laudador.

    O PDF é validado como qualquer outro do sistema (estrutura, tamanho,
    páginas, sem conteúdo ativo) e gravado no filesystem privado, sob
    `eeg/laudos/` — separado do acervo da espirometria. O caminho interno
    nunca sai numa resposta.

    Nenhuma validação criptográfica é afirmada: este endpoint não verifica
    assinatura digital, e dizer que verifica seria falso.
    """
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
    if exam.status not in {eeg_flow.ENVIADO, eeg_flow.EM_LAUDO,
                           eeg_flow.LAUDO_RECEBIDO, eeg_flow.LAUDO_DISPONIVEL}:
        raise DomainError(
            409, "exame_sem_envio",
            f"O exame precisa ter sido enviado para laudo "
            f"(status atual: {exam.status}).")

    case = case_for(db, exam.id, create=True)
    if not case.professional_id or case.status not in {'atribuido','em_elaboracao','aguardando_assinatura','recebido_assinado','pronto_entrega','entregue'}:
        raise DomainError(409,'atribuicao_ausente','Confira o traçado e atribua um laudador antes de receber o PDF.')
    if assinatura_recebida and confirmacao_codigo != exam.public_code:
        raise DomainError(422,'confirmacao_divergente','Confira o paciente e confirme o código EEG do PDF assinado.')
    if not assinatura_recebida and case.status in {'recebido_assinado','pronto_entrega','entregue'}:
        raise DomainError(409,'assinatura_exige_revisao','Substituição do resultado publicado exige novo PDF assinado conferido.')
    professional = db.get(EegProfessional,case.professional_id)
    if professional and professional.user_id and not case.final_document_id:
        raise DomainError(409,'conclusao_medica_ausente','Médico interno deve concluir o laudo antes do retorno assinado.')
    medico_laudador = (case.professional_snapshot or {}).get('nome')
    settings = get_settings()
    data = arquivo.file.read(settings.reports_max_upload_bytes + 1)
    try:
        validado = validate_pdf_bytes(
            data,
            max_size_bytes=settings.reports_max_upload_bytes,
            declared_content_type=arquivo.content_type,
            allow_signature_form=True,
        )
    except InvalidPdfError as exc:
        raise DomainError(422, "pdf_invalido", str(exc)) from exc

    anteriores = db.execute(
        select(EegReportDocument).where(EegReportDocument.exam_id == exam.id)
    ).scalars().all()
    for anterior in anteriores:
        anterior.status = "substituido"
        access = db.execute(select(EegResultAccess).where(
            EegResultAccess.document_id == anterior.id)).scalar_one_or_none()
        if access and access.status != "revogado":
            patient_results.revoke(db, access, user_id=user.id, motivo="laudo_substituido")
    versao = max((d.versao for d in anteriores), default=0) + 1

    documento = EegReportDocument(
        public_code=allocate_public_code(db, "eeg_report_documents"),
        exam_id=exam.id,
        versao=versao,
        storage_path="",
        sha256=validado.sha256,
        size_bytes=len(data),
        page_count=validado.page_count,
        medico_laudador=medico_laudador,
        observacao=observacao,
        recebido_em=_agora(),
        recebido_por=user.id,
    )
    db.add(documento)
    db.flush()

    root = settings.resolved_reports_storage_dir()
    destino = eeg_report_storage_path(root, exam_id=exam.id, document_id=documento.id)
    atomic_write_new_file(destino, data, root=root)
    documento.storage_path = str(destino.relative_to(root))

    if assinatura_recebida:
        case.assinado_document_id = documento.id
        case.assinado_em = _agora()
        case.status = 'recebido_assinado'
    else:
        case.final_document_id = documento.id
        case.concluido_em = _agora()
        case.status = 'aguardando_assinatura'
    exam.status = eeg_flow.LAUDO_RECEBIDO
    exam.laudo_recebido_em = documento.recebido_em
    audit(db, "eeg.laudo_recebido", "eeg_report_documents", documento.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None),
          detalhes={"exam_code": exam.public_code, "version": versao,
                    "substituiu": bool(anteriores)})
    db.commit()
    db.refresh(exam)
    return _ser_exame(db, exam)


@router.get("/laudos/{documento_id}/arquivo")
def baixar_laudo(
    documento_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_access),
):
    """Download do PDF — admin-only, como todo o resto desta operação."""
    documento = db.get(EegReportDocument, documento_id)
    if not documento:
        raise HTTPException(status_code=404, detail="Laudo não encontrado.")
    require_exam(db, user, documento.exam_id)
    settings = get_settings()
    root = settings.resolved_reports_storage_dir()
    caminho = root / documento.storage_path
    try:
        data = read_and_validate_stored_pdf(
            caminho, root=root, expected_sha256=documento.sha256,
            expected_size_bytes=documento.size_bytes,
            expected_page_count=documento.page_count,
            max_size_bytes=settings.reports_max_upload_bytes,
            allow_signature_form=True,
        ).data
    except (OSError, ReportStorageError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Arquivo indisponível.") from exc
    audit(db, "eeg.laudo_baixado", "eeg_report_documents", documento.id,
          user_id=user.id, request_id=getattr(request.state, "request_id", None))
    db.commit()
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="laudo-eeg-{documento.public_code}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/laudos/{documento_id}/acesso-resultado")
def gerar_acesso_resultado(
    documento_id: str, request: Request, renovar: bool = False,
    db: Session = Depends(get_db), user: User = Depends(require_eeg_operator),
):
    acesso = eeg_results.issue(db, documento_id, user.id, regenerate=renovar)
    link = eeg_results.public_url(acesso)
    db.commit()
    return {"url": link, "status": acesso.status,
            "expira_em": acesso.expires_at.isoformat()}


@router.post("/laudos/{documento_id}/acesso-resultado/revogar")
def revogar_acesso_resultado(
    documento_id: str, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_eeg_operator),
):
    acesso = db.execute(select(EegResultAccess).where(
        EegResultAccess.document_id == documento_id)).scalar_one_or_none()
    if not acesso:
        raise HTTPException(status_code=404, detail="Acesso não encontrado.")
    patient_results.revoke(db, acesso, user_id=user.id, motivo="revogado_admin")
    db.commit()
    return {"status": acesso.status}
