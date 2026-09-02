"""M26.4 — o acesso do paciente ao próprio resultado.

Um só lugar decide o que é um acesso válido, como o link nasce, quando ele
morre e quais dois arquivos ele entrega. O processo público e o painel
privado importam daqui; nenhum dos dois reimplementa a regra.

## O token

`token = base64url(HMAC-SHA256(chave, id_do_acesso || ":" || geração))`

Trinta e dois bytes — 256 bits de saída, sobre uma chave de pelo menos 256
bits. Não é sequencial, não é derivável de nada público e não é adivinhável
sem a chave.

O banco guarda apenas `sha256(token)`. Isso resolve os dois requisitos que
normalmente brigam entre si:

- **nunca armazenar o segredo** — o processo público compara hashes e não
  tem como reconstruir link nenhum, nem que leia a tabela inteira;
- **poder reenviar o mesmo link** — o painel privado re-deriva o token
  quando o operador pede, porque a chave vive só no EnvironmentFile dele.

Revogar/regenerar é incrementar `generation`: o hash muda, o link que estava
no WhatsApp para de funcionar no mesmo instante, e a linha continua inteira
para a auditoria.

O token nunca é escrito em `audit_logs` (a allowlist de `app/audit.py` não
tem chave para ele), nunca em log de acesso (uvicorn sobe com `access_log`
desligado) e nunca na querystring — ele viaja no FRAGMENTO da URL, que o
navegador não envia ao servidor.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..ids import new_uuid
from ..models import (
    ASSINADO_ACEITO,
    ASSINADO_ENTREGUE,
    ASSINADO_VALIDADO_EXTERNAMENTE,
    RESULTADO_ACESSADO,
    RESULTADO_COOLDOWN_MINUTOS,
    RESULTADO_DISPONIVEL,
    RESULTADO_ENVIADO,
    RESULTADO_MAX_TENTATIVAS,
    RESULTADO_REVOGADO,
    ExternalSignedDocument,
    PatientResultAccess,
    PatientResultSession,
    Person,
    PersonContact,
    ReportDocument,
    ReportDocumentVersion,
    SpirometryExam,
)

# Estados do documento assinado que autorizam entrega ao paciente. São os
# mesmos dois que a fila administrativa já considera "pronto para entrega":
# o aceite automático da M25.29H e a conferência externa histórica.
#
# `recebido_validacao_pendente` (legado), `em_conferencia` e `recusado` ficam
# de fora, e é essa lista — não o chamador — que garante isso.
ESTADOS_ENTREGAVEIS = (ASSINADO_ACEITO, ASSINADO_VALIDADO_EXTERNAMENTE)
# O caminho MANUAL ("Gerar acesso ao resultado", para laudos históricos)
# aceita também o que já foi entregue pelos canais antigos: o documento é o
# mesmo, assinado e íntegro, e negar o portal a ele seria punir o paciente
# por a entrega ter acontecido antes de o portal existir. O gatilho
# automático continua estrito.
ESTADOS_ENTREGAVEIS_MANUAL = (*ESTADOS_ENTREGAVEIS, ASSINADO_ENTREGUE)

MOTIVO_REGENERADO = "acesso_regenerado"


def agora() -> datetime:
    return datetime.now(timezone.utc)


def _como_utc(valor: datetime | None) -> datetime | None:
    """SQLite devolve datetime ingênuo; comparar sem tzinfo explode em prod."""

    if valor is None:
        return None
    return valor if valor.tzinfo is not None else valor.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------- o segredo


def derive_token(access_id: str, generation: int) -> str:
    """O link deste acesso, nesta geração. Determinístico e re-derivável."""

    chave = get_settings().resolved_portal_token_key().encode()
    bruto = hmac.new(
        chave, f"{access_id}:{generation}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(bruto).decode().rstrip("=")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_url(token: str) -> str:
    """O endereço que o paciente reconhece, com o segredo no FRAGMENTO.

    O fragmento (`#t=`) não sai do navegador: não entra em log de acesso, não
    vai no cabeçalho Referer e não atravessa proxy. Um token em querystring
    estaria hoje no log do nginx e amanhã no backup dele.
    """

    base = get_settings().portal_public_base_url
    if not base:
        raise ValueError(
            "M15_PORTAL_PUBLIC_BASE_URL não configurado — nenhum link é "
            "inventado (fail-closed)."
        )
    return f"{base}/#t={token}"


# ------------------------------------------------------- criar / reapontar


@dataclass(frozen=True)
class ResultadoAcesso:
    """O que o painel privado precisa saber, com o link já derivado."""

    acesso: PatientResultAccess
    token: str

    @property
    def url(self) -> str:
        return public_url(self.token)


def _versao_assinada(
    db: Session, assinado: ExternalSignedDocument
) -> ReportDocumentVersion | None:
    return db.get(ReportDocumentVersion, assinado.report_document_version_id)


def ensure_access(
    db: Session,
    assinado: ExternalSignedDocument,
    *,
    user_id: str | None = None,
    request_id: str | None = None,
    permitir_entregue: bool = False,
) -> PatientResultAccess | None:
    """O GATILHO. Chamado logo depois de o documento entrar em entregável.

    Devolve o acesso vigente, ou `None` quando não há acesso a criar. As
    recusas são silenciosas de propósito: este é um efeito colateral do
    fluxo clínico da médica, e uma exceção aqui derrubaria o recebimento de
    um PDF assinado que já foi aceito — trocaria um problema de entrega por
    um problema clínico.

    Nunca cria acesso para prévia, para PDF sem assinatura nem para
    documento recusado: as guardas documentais da M25.29H já decidiram isso
    ANTES, e o que chega aqui só passa se o status for entregável. A
    verificação é repetida mesmo assim — é barata, e um chamador novo com a
    ordem errada seria um vazamento.
    """

    settings = get_settings()
    if not settings.portal_enabled:
        return None
    permitidos = (
        ESTADOS_ENTREGAVEIS_MANUAL if permitir_entregue else ESTADOS_ENTREGAVEIS
    )
    if assinado.status not in permitidos:
        return None

    documento = db.get(ReportDocument, assinado.report_document_id)
    if documento is None:
        return None
    versao = _versao_assinada(db, assinado)
    if versao is None or versao.report_document_id != documento.id:
        return None
    exame = db.get(SpirometryExam, documento.spirometry_exam_id)
    if exame is None or exame.person_id is None:
        return None
    pessoa = db.get(Person, exame.person_id)
    if pessoa is None or pessoa.arquivado:
        return None

    existente = db.execute(
        select(PatientResultAccess)
        .where(PatientResultAccess.report_document_id == documento.id)
        .with_for_update()
    ).scalar_one_or_none()

    if existente is not None:
        if existente.status == RESULTADO_REVOGADO:
            # Uma revogação foi um ato deliberado de alguém. Um PDF novo não
            # a desfaz por conta própria — o administrador clica em "Gerar
            # novo acesso" quando quiser reabrir. O que este ramo garante é
            # que o fato fique registrado, e não que o link ressuscite.
            audit(
                db,
                "resultado_acesso_nao_recriado_revogado",
                entidade="patient_result_accesses",
                entidade_id=existente.id,
                user_id=user_id,
                request_id=request_id,
                detalhes={
                    "report_code": documento.public_code,
                    "status": existente.status,
                },
            )
            return existente
        if existente.report_document_version_id == versao.id:
            return existente
        # PDF assinado NOVO substituindo o anterior: o acesso passa a
        # apontar para a versão vigente, mantendo o mesmo link (que já pode
        # estar no WhatsApp do paciente) e toda a trilha de datas.
        existente.signed_document_id = assinado.id
        existente.report_document_version_id = versao.id
        existente.expires_at = agora() + timedelta(
            days=settings.portal_access_ttl_days
        )
        audit(
            db,
            "resultado_acesso_atualizado",
            entidade="patient_result_accesses",
            entidade_id=existente.id,
            user_id=user_id,
            request_id=request_id,
            detalhes={
                "report_code": documento.public_code,
                "exam_code": exame.public_code,
                "signed_document_id": assinado.id,
                "report_version_id": versao.id,
                "sha256": versao.sha256,
            },
        )
        return existente

    # O id é gerado AQUI, e não pelo default da coluna, porque o token
    # deriva dele: inserir com `token_sha256` vazio para preencher no flush
    # seguinte colidiria no índice único assim que dois acessos nascessem na
    # mesma janela.
    novo_id = new_uuid()
    acesso = PatientResultAccess(
        id=novo_id,
        person_id=pessoa.id,
        spirometry_exam_id=exame.id,
        report_document_id=documento.id,
        signed_document_id=assinado.id,
        report_document_version_id=versao.id,
        token_sha256=token_digest(derive_token(novo_id, 1)),
        generation=1,
        status=RESULTADO_DISPONIVEL,
        created_at=agora(),
        expires_at=agora() + timedelta(days=settings.portal_access_ttl_days),
        created_by_user_id=user_id,
    )
    db.add(acesso)
    db.flush()
    audit(
        db,
        "resultado_acesso_criado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        user_id=user_id,
        request_id=request_id,
        detalhes={
            "report_code": documento.public_code,
            "exam_code": exame.public_code,
            "signed_document_id": assinado.id,
            "report_version_id": versao.id,
            "sha256": versao.sha256,
            "modo": "automatico" if user_id is None else "manual",
        },
    )
    return acesso


def regenerate(
    db: Session,
    acesso: PatientResultAccess,
    *,
    user_id: str,
    request_id: str | None = None,
) -> PatientResultAccess:
    """Novo link, o anterior morto no mesmo instante. Nenhuma linha apagada."""

    settings = get_settings()
    acesso.generation += 1
    acesso.token_sha256 = token_digest(
        derive_token(acesso.id, acesso.generation)
    )
    acesso.status = RESULTADO_DISPONIVEL
    acesso.revoked_at = None
    acesso.revoked_motivo = None
    acesso.revoked_by_user_id = None
    acesso.sent_at = None
    acesso.failed_attempts = 0
    acesso.locked_until = None
    acesso.expires_at = agora() + timedelta(
        days=settings.portal_access_ttl_days
    )
    revoke_sessions(db, acesso)
    audit(
        db,
        "resultado_acesso_regenerado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        user_id=user_id,
        request_id=request_id,
        detalhes={"version": acesso.generation, "status": acesso.status},
    )
    return acesso


def revoke(
    db: Session,
    acesso: PatientResultAccess,
    *,
    user_id: str,
    motivo: str,
    request_id: str | None = None,
) -> PatientResultAccess:
    """Link morto, PDF fechado, sessão viva derrubada. Trilha preservada."""

    acesso.status = RESULTADO_REVOGADO
    acesso.revoked_at = agora()
    acesso.revoked_motivo = motivo[:120]
    acesso.revoked_by_user_id = user_id
    revoke_sessions(db, acesso)
    audit(
        db,
        "resultado_acesso_revogado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        user_id=user_id,
        request_id=request_id,
        detalhes={"motivo": motivo[:120], "status": acesso.status},
    )
    return acesso


def mark_sent(
    db: Session,
    acesso: PatientResultAccess,
    *,
    user_id: str,
    canal: str,
    request_id: str | None = None,
) -> PatientResultAccess:
    """Registra que o operador ABRIU o envio — nunca que o paciente recebeu.

    O sistema não tem como saber se a mensagem chegou, muito menos se foi
    lida. `sent_at` diz o que de fato aconteceu aqui dentro.
    """

    if acesso.sent_at is None:
        acesso.sent_at = agora()
    if acesso.status == RESULTADO_DISPONIVEL:
        acesso.status = RESULTADO_ENVIADO
    audit(
        db,
        "resultado_acesso_enviado",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        user_id=user_id,
        request_id=request_id,
        detalhes={"canal": canal, "status": acesso.status},
    )
    return acesso


# ----------------------------------------------------- validade e fatores


def is_expired(acesso: PatientResultAccess, *, referencia: datetime | None = None) -> bool:
    limite = _como_utc(acesso.expires_at)
    return limite is None or limite <= (referencia or agora())


def is_locked(acesso: PatientResultAccess, *, referencia: datetime | None = None) -> bool:
    ate = _como_utc(acesso.locked_until)
    return ate is not None and ate > (referencia or agora())


def find_by_token(db: Session, token: str) -> PatientResultAccess | None:
    """Localiza pelo HASH. O token bruto nunca chega ao banco."""

    if not token or len(token) > 200:
        return None
    return db.execute(
        select(PatientResultAccess).where(
            PatientResultAccess.token_sha256 == token_digest(token)
        )
    ).scalar_one_or_none()


def check_birthdate(pessoa: "PacientePortal", valor: date) -> bool:
    """Segundo fator. Comparação de datas, sem margem e sem aproximação.

    Cadastro SEM data de nascimento nunca autentica — o campo é opcional no
    domínio, e tratar ausência como "qualquer data serve" transformaria um
    cadastro incompleto em porta aberta.
    """

    if pessoa.data_nascimento is None:
        return False
    return pessoa.data_nascimento == valor


def register_failure(acesso: PatientResultAccess) -> None:
    """Conta a tentativa errada e aplica o resfriamento quando estoura.

    O contador vive no BANCO, e não em memória: reiniciar o processo público
    não pode ser a forma de zerar o bloqueio.
    """

    acesso.failed_attempts += 1
    if acesso.failed_attempts >= RESULTADO_MAX_TENTATIVAS:
        acesso.locked_until = agora() + timedelta(
            minutes=RESULTADO_COOLDOWN_MINUTOS
        )
        acesso.failed_attempts = 0


def register_success(acesso: PatientResultAccess) -> None:
    momento = agora()
    acesso.failed_attempts = 0
    acesso.locked_until = None
    if acesso.first_access_at is None:
        acesso.first_access_at = momento
    acesso.last_access_at = momento
    if acesso.status in (RESULTADO_DISPONIVEL, RESULTADO_ENVIADO):
        acesso.status = RESULTADO_ACESSADO


def register_download(acesso: PatientResultAccess) -> None:
    acesso.download_count += 1
    acesso.last_download_at = agora()


# ------------------------------------------------------------- sessões


def create_session(db: Session, acesso: PatientResultAccess) -> tuple[str, PatientResultSession]:
    """Devolve (segredo em claro, linha). O banco fica só com o hash."""

    settings = get_settings()
    segredo = secrets.token_urlsafe(32)
    sessao = PatientResultSession(
        access_id=acesso.id,
        token_hash=hashlib.sha256(segredo.encode()).hexdigest(),
        created_at=agora(),
        expires_at=agora()
        + timedelta(minutes=settings.portal_session_ttl_minutes),
    )
    db.add(sessao)
    db.flush()
    return segredo, sessao


def load_session(
    db: Session, sessao_id: str, segredo: str
) -> tuple[PatientResultSession, PatientResultAccess] | None:
    """Fail-closed em cada porta: sessão, acesso, revogação e prazo."""

    sessao = db.get(PatientResultSession, sessao_id)
    if sessao is None or sessao.revoked_at is not None:
        return None
    if not hmac.compare_digest(
        sessao.token_hash, hashlib.sha256(segredo.encode()).hexdigest()
    ):
        return None
    limite = _como_utc(sessao.expires_at)
    if limite is None or limite <= agora():
        return None
    acesso = db.get(PatientResultAccess, sessao.access_id)
    if acesso is None or acesso.status == RESULTADO_REVOGADO:
        return None
    if is_expired(acesso):
        return None
    return sessao, acesso


def revoke_sessions(db: Session, acesso: PatientResultAccess) -> int:
    vivas = db.execute(
        select(PatientResultSession).where(
            PatientResultSession.access_id == acesso.id,
            PatientResultSession.revoked_at.is_(None),
        )
    ).scalars().all()
    momento = agora()
    for sessao in vivas:
        sessao.revoked_at = momento
    return len(vivas)


# ------------------------------------------- leitura ESTREITA do portal
#
# O processo público lê o MÍNIMO de colunas de que precisa, em vez de
# carregar as entidades inteiras pelo ORM. Não é preciosismo: é o que
# permite dar ao papel de banco do portal um GRANT por COLUNA
# (scripts/sql/m26-4-portal-db-role.sql). Com `db.get(Person, ...)` o
# SELECT traria `cpf`, `observacao` e o resto do cadastro — e o GRANT teria
# de ser da tabela toda. Do jeito que está, o CPF de um paciente não é
# legível pelo processo que está na internet nem que alguém o peça.


@dataclass(frozen=True)
class PacientePortal:
    id: str
    nome_completo: str
    data_nascimento: date | None
    arquivado: bool


@dataclass(frozen=True)
class ExamePortal:
    id: str
    data_exame: date | None


@dataclass(frozen=True)
class VersaoPortal:
    id: str
    report_document_id: str
    kind: str
    version_number: int
    storage_path: str
    sha256: str
    size_bytes: int
    page_count: int


def load_patient(db: Session, person_id: str) -> PacientePortal | None:
    linha = db.execute(
        select(
            Person.id,
            Person.nome_completo,
            Person.data_nascimento,
            Person.arquivado,
        ).where(Person.id == person_id)
    ).first()
    return PacientePortal(*linha) if linha else None


def load_exam(db: Session, exam_id: str) -> ExamePortal | None:
    linha = db.execute(
        select(SpirometryExam.id, SpirometryExam.data_exame).where(
            SpirometryExam.id == exam_id
        )
    ).first()
    return ExamePortal(*linha) if linha else None


_COLUNAS_VERSAO = (
    ReportDocumentVersion.id,
    ReportDocumentVersion.report_document_id,
    ReportDocumentVersion.kind,
    ReportDocumentVersion.version_number,
    ReportDocumentVersion.storage_path,
    ReportDocumentVersion.sha256,
    ReportDocumentVersion.size_bytes,
    ReportDocumentVersion.page_count,
)


# -------------------------------------------------------- os documentos


def signed_version(
    db: Session, acesso: PatientResultAccess
) -> VersaoPortal | None:
    """EXATAMENTE a versão apontada pelo acesso. Nunca "o último PDF"."""

    linha = db.execute(
        select(*_COLUNAS_VERSAO).where(
            ReportDocumentVersion.id == acesso.report_document_version_id
        )
    ).first()
    if linha is None:
        return None
    versao = VersaoPortal(*linha)
    # Contenção redundante de propósito: a versão TEM de pertencer ao mesmo
    # laudo do acesso. Se um dia alguém apontar a coluna para outro
    # documento, o download morre aqui em vez de entregar o PDF errado.
    if versao.report_document_id != acesso.report_document_id:
        return None
    return versao


def technical_version(
    db: Session, acesso: PatientResultAccess
) -> VersaoPortal | None:
    """A MIR original do MESMO laudo — a versão `original` deste documento.

    O vínculo é pelo `report_document_id` do acesso, que por sua vez nasceu
    do exame daquela pessoa. Não há caminho aqui por onde o técnico de outro
    exame entre.
    """

    linha = db.execute(
        select(*_COLUNAS_VERSAO)
        .where(
            ReportDocumentVersion.report_document_id
            == acesso.report_document_id,
            ReportDocumentVersion.kind == "original",
        )
        .order_by(ReportDocumentVersion.version_number.desc())
    ).first()
    return VersaoPortal(*linha) if linha else None


# ------------------------------------------------------------- WhatsApp


def _primeiro_nome(nome: str) -> str:
    partes = [p for p in (nome or "").strip().split() if p]
    return partes[0] if partes else "paciente"


MENSAGEM_WHATSAPP = (
    "Olá, {primeiro_nome}.\n\n"
    "Seu resultado de espirometria realizado pela SoproLife já está "
    "disponível.\n\n"
    "Acesse com segurança pelo link:\n"
    "{link}\n\n"
    "Para visualizar os documentos, será necessário confirmar sua data de "
    "nascimento.\n\n"
    "SoproLife Diagnósticos e Soluções em Saúde"
)


def build_message(pessoa, link: str) -> str:
    """A mensagem pronta. Sem resultado clínico, sem CPF, sem código interno.

    O que ela contém: primeiro nome, o fato de existir um resultado de
    espirometria, o link e a instrução do segundo fator. Nada do laudo.
    """

    return MENSAGEM_WHATSAPP.format(
        primeiro_nome=_primeiro_nome(pessoa.nome_completo), link=link
    )


def patient_phone(db: Session, pessoa) -> PersonContact | None:
    """O telefone discável do paciente, se houver. Ausência não é erro."""

    contatos = db.execute(
        select(PersonContact).where(
            PersonContact.person_id == pessoa.id,
            PersonContact.ativo.is_(True),
            PersonContact.nao_discavel.is_(False),
            PersonContact.tipo.in_(["whatsapp", "telefone"]),
        )
    ).scalars().all()
    if not contatos:
        return None
    principais = [c for c in contatos if c.principal]
    whatsapp = [c for c in (principais or contatos) if c.tipo == "whatsapp"]
    return (whatsapp or principais or contatos)[0]


def whatsapp_url(telefone_normalizado: str, mensagem: str) -> str:
    """`wa.me` com a mensagem pronta — o operador só aperta ENVIAR.

    Não existe integração oficial Meta WhatsApp Cloud API neste projeto (a
    auditoria da M26.4 confirmou), e improvisar bot ou automação de WhatsApp
    Web seria trocar uma entrega auditável por uma que a Meta derruba e que
    ninguém consegue explicar. Quando a Cloud API entrar, ela vira outro
    CANAL do mesmo serviço — este arquivo não muda de forma.
    """

    return f"https://wa.me/{telefone_normalizado}?text={quote(mensagem)}"
