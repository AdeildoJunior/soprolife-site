"""EEGLife — operação (Fase 2A).

AUTORIZAÇÃO
-----------
TODA rota deste arquivo depende de `require_eeg_access`. A casca do Centro de
Comando esconde a marca de quem não pode vê-la, mas quem RECUSA é isto aqui:
o seletor, o localStorage e o DOM não participam da decisão. Um médico da
SoproLife recebe 403 em qualquer rota abaixo, inclusive no download do PDF.

ESCOPO DA CONSULTA
------------------
A Central da EEGLife lista pessoas pelo VÍNCULO (`eeg_enrollments`), nunca por
`people`. Quem nunca teve exame de EEG não aparece — e é isso que impede a
base da SoproLife de vazar para dentro da EEGLife.

O QUE NÃO EXISTE AQUI
---------------------
Preço, repasse, cobrança, NFS-e e prazo. Nada disso é desta fase, e inventar
qualquer um seria pior do que a ausência. Também não há integração com
plataforma de laudo: o envio é registrado como ato manual.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import func, select
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
    Person,
    User,
)
from ..normalize import normalize_name, normalize_phone
from ..pagination import PageParams, paginate
from ..schemas import (
    EegArquivoTecnicoCreate,
    EegEnvioLaudoCreate,
    EegExameCreate,
    EegExameStatusUpdate,
    EegPessoaCreate,
    EegVinculoCreate,
)
from ..security import require_eeg_access
from ..serializers import ser_person
from ..services import eeg_flow
from ..services.cpf import CPFInvalidoError, normalizar_cpf
from ..services.eeg_modalidades import MODALIDADES, modalidade_ativa, obter
from ..services.eeg_storage import eeg_report_storage_path, atomic_write_new_file
from ..services.report_storage import read_and_validate_stored_pdf, ReportStorageError
from ..services import eeg_results, patient_results
from ..services.identity import find_person_candidates
from ..services.pdf_validation import InvalidPdfError, validate_pdf_bytes
from ..services.person_registration import add_contact

router = APIRouter(prefix="/eeg", tags=["eeg"])

ORIGENS_VALIDAS = {"direto", "parceiro"}


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ── Serialização ──────────────────────────────────────────────────────────

def _ser_modalidade(m) -> dict:
    return {
        "codigo": m.codigo,
        "nome": m.nome,
        "tambem_chamado": m.tambem_chamado,
        "duracao_minutos_aproximada": m.duracao_minutos_aproximada,
        "preparo": m.preparo,
        "ativo": m.ativo,
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
    return {
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
        "local_nome": unidade.nome if unidade else ("Direto EEGLife" if exam.origem_tipo == "direto" else None),
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
    _user: User = Depends(require_eeg_access),
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
    _user: User = Depends(require_eeg_access),
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


@router.post("/pessoas/vincular", status_code=201)
def vincular_pessoa(
    payload: EegVinculoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_access),
):
    """Vincula pessoa existente à EEGLife — SEM duplicar identidade.

    É o caminho de quem já fez espirometria e agora fará EEG. O cadastro da
    pessoa não é copiado nem alterado: só passa a existir um vínculo.
    """
    person = db.get(Person, payload.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    existente = db.execute(
        select(EegEnrollment).where(EegEnrollment.person_id == person.id)
    ).scalars().first()
    if existente:
        if existente.status != "ativo":
            existente.status = "ativo"
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
    _user: User = Depends(require_eeg_access),
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
    user: User = Depends(require_eeg_access),
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

    enrollment = EegEnrollment(
        person_id=person.id, origem=payload.origem,
        observacao=payload.observacao, criado_por=user.id,
    )
    db.add(enrollment)
    db.flush()
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
    if status:
        stmt = stmt.where(EegExam.status == status)
    stmt = stmt.order_by(EegExam.created_at.desc())
    return paginate(db, stmt, params, lambda e: _ser_exame(db, e))


@router.post("/exames", status_code=201)
def criar_exame(
    payload: EegExameCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_access),
):
    enrollment = db.get(EegEnrollment, payload.enrollment_id)
    if not enrollment or enrollment.status != "ativo":
        raise HTTPException(status_code=404, detail="Vínculo EEGLife não encontrado.")
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
        if not db.get(PartnerUnit, payload.partner_unit_id):
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
    db.commit()
    db.refresh(exam)
    return _ser_exame(db, exam)


@router.patch("/exames/{exam_id}/status")
def mudar_status(
    exam_id: str,
    payload: EegExameStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_access),
):
    """Transição conferida no servidor. Pulo de etapa é recusado, não corrigido."""
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
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
        laudo = db.execute(
            select(EegReportDocument).where(
                EegReportDocument.exam_id == exam.id,
                EegReportDocument.status != "substituido")
        ).scalars().first()
        if not laudo:
            raise DomainError(409, "laudo_ausente",
                              "Não há laudo recebido para disponibilizar.")
        laudo.status = "vigente"
        laudo.disponibilizado_em = payload.ocorrido_em or _agora()

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
    user: User = Depends(require_eeg_access),
):
    """Registra que o traçado EXISTE — e onde. Nunca recebe o traçado.

    Não há upload aqui, de propósito: um EEG digital tem dezenas a centenas de
    MB e monitorização prolongada chega a GB. Fingir que o arquivo chegou ao
    Centro de Comando seria pior do que dizer onde ele está.
    """
    exam = db.get(EegExam, exam_id, with_for_update=True)
    if not exam:
        raise HTTPException(status_code=404, detail="Exame não encontrado.")
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
    user: User = Depends(require_eeg_access),
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
    stmt = (
        select(EegExam)
        .where(EegExam.status.in_(tuple(eeg_flow.EXAME_JA_REALIZADO)))
        .order_by(EegExam.realizado_em.desc())
    )
    return paginate(db, stmt, params, lambda e: _ser_exame(db, e))


@router.post("/exames/{exam_id}/laudo", status_code=201)
def receber_laudo(
    exam_id: str,
    request: Request,
    arquivo: UploadFile = File(...),
    medico_laudador: str | None = Form(default=None),
    observacao: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_eeg_access),
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
    db: Session = Depends(get_db), user: User = Depends(require_eeg_access),
):
    acesso = eeg_results.issue(db, documento_id, user.id, regenerate=renovar)
    link = eeg_results.public_url(acesso)
    db.commit()
    return {"url": link, "status": acesso.status,
            "expira_em": acesso.expires_at.isoformat()}


@router.post("/laudos/{documento_id}/acesso-resultado/revogar")
def revogar_acesso_resultado(
    documento_id: str, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_eeg_access),
):
    acesso = db.execute(select(EegResultAccess).where(
        EegResultAccess.document_id == documento_id)).scalar_one_or_none()
    if not acesso:
        raise HTTPException(status_code=404, detail="Acesso não encontrado.")
    patient_results.revoke(db, acesso, user_id=user.id, motivo="revogado_admin")
    db.commit()
    return {"status": acesso.status}
