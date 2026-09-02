"""M26.4 — o lado ADMINISTRATIVO do portal de resultados.

Fica no Command Center, atrás do Tailscale e do papel operacional, e é onde
o link, o QR Code e a mensagem de WhatsApp aparecem. Nada disto existe na
superfície pública: o processo do portal não sabe gerar link nenhum, porque
não tem a chave que os deriva.

Quatro ações, todas sobre UM laudo:

    GET    …/acesso-resultado           o que existe hoje (+ link, QR, mensagem)
    POST   …/acesso-resultado           gerar — ou regerar, matando o anterior
    POST   …/acesso-resultado/revogar   matar o link e derrubar sessões
    POST   …/acesso-resultado/enviado   registrar que o operador abriu o envio

O link e o QR **não** entram na listagem da fila. Um token por linha, em
resposta de lote, ficaria na memória do navegador de todo mundo que abre a
tela — e a tela abre sozinha. Aqui ele só sai quando alguém pede aquele
laudo, e a saída é auditada.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db
from ..errors import ReportDomainError, resolve_request_id
from ..models import (
    RESULTADO_ACESSADO,
    RESULTADO_DISPONIVEL,
    RESULTADO_ENVIADO,
    RESULTADO_REVOGADO,
    ExternalSignedDocument,
    PatientResultAccess,
    Person,
    ReportDocument,
    User,
)
from ..security import ROLE_OPERACIONAL, require_role
from ..services import patient_results as prs
from ..services.qrcode_svg import to_svg

router = APIRouter(prefix="/laudos", tags=["resultados-paciente"])

STATUS_ROTULOS = {
    RESULTADO_DISPONIVEL: "Disponível",
    RESULTADO_ENVIADO: "Enviado",
    RESULTADO_ACESSADO: "Acessado",
    RESULTADO_REVOGADO: "Revogado",
}

MOTIVO_SEM_TELEFONE = "Telefone não cadastrado"


class PedidoDeGeracao(BaseModel):
    # Regerar é destrutivo para o link ANTERIOR, então é explícito. Sem esta
    # flag a chamada é idempotente e devolve o que já existe.
    regenerar: bool = False


class PedidoDeRevogacao(BaseModel):
    motivo: str = Field(min_length=3, max_length=120)


class PedidoDeEnvio(BaseModel):
    canal: str = Field(default="whatsapp_manual", max_length=40)


def _exigir_portal() -> None:
    if not get_settings().portal_enabled:
        raise ReportDomainError(
            503,
            "portal_desabilitado",
            "O portal de resultados não está habilitado nesta instalação.",
        )


def _documento(db: Session, document_id: str) -> ReportDocument:
    documento = db.get(ReportDocument, document_id)
    if documento is None:
        raise ReportDomainError(404, "laudo_nao_encontrado", "Laudo não encontrado.")
    return documento


def _acesso(db: Session, document_id: str) -> PatientResultAccess | None:
    return db.execute(
        select(PatientResultAccess).where(
            PatientResultAccess.report_document_id == document_id
        )
    ).scalar_one_or_none()


def _iso(valor) -> str | None:
    return valor.isoformat() if valor else None


def resumo_do_acesso(acesso: PatientResultAccess | None) -> dict:
    """O que a FILA mostra: estado e datas. Sem link, sem QR, sem token.

    É esta função que o `list_delivery_queue` usa. Ela existe separada de
    `_detalhe` justamente para que seja impossível o token cair numa
    resposta de lote por descuido de quem editar a fila depois.
    """

    if acesso is None:
        return {"existe": False}
    return {
        "existe": True,
        "status": acesso.status,
        "status_rotulo": STATUS_ROTULOS.get(acesso.status, acesso.status),
        "criado_em": _iso(acesso.created_at),
        "enviado_em": _iso(acesso.sent_at),
        "primeiro_acesso_em": _iso(acesso.first_access_at),
        "ultimo_acesso_em": _iso(acesso.last_access_at),
        "revogado_em": _iso(acesso.revoked_at),
        "expira_em": _iso(acesso.expires_at),
        "expirado": prs.is_expired(acesso),
        "downloads": acesso.download_count,
    }


def _detalhe(db: Session, acesso: PatientResultAccess) -> dict:
    """Resumo + o material de entrega: link, QR e mensagem pronta."""

    token = prs.derive_token(acesso.id, acesso.generation)
    link = prs.public_url(token)
    pessoa = db.get(Person, acesso.person_id)
    corpo = resumo_do_acesso(acesso)
    corpo["link"] = link
    # QR sempre para a PÁGINA, jamais para o PDF. Um QR que abrisse o
    # arquivo direto seria um documento médico sem segundo fator, colado
    # numa folha que qualquer um fotografa.
    corpo["qr_svg"] = to_svg(link)
    mensagem = prs.build_message(pessoa, link) if pessoa else ""
    corpo["mensagem"] = mensagem
    contato = prs.patient_phone(db, pessoa) if pessoa else None
    if contato is None:
        corpo["whatsapp"] = {
            "disponivel": False,
            "motivo": MOTIVO_SEM_TELEFONE,
        }
    else:
        corpo["whatsapp"] = {
            "disponivel": True,
            "telefone": contato.valor,
            "url": prs.whatsapp_url(contato.valor_normalizado, mensagem),
        }
    return corpo


@router.get("/{document_id}/acesso-resultado")
def ver_acesso(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    operador: User = Depends(require_role(ROLE_OPERACIONAL)),
) -> dict:
    """Mostra o acesso deste laudo. Só aqui o link é derivado e auditado."""

    _exigir_portal()
    _documento(db, document_id)
    acesso = _acesso(db, document_id)
    if acesso is None:
        return {"existe": False}
    audit(
        db,
        "resultado_acesso_link_exibido",
        entidade="patient_result_accesses",
        entidade_id=acesso.id,
        user_id=operador.id,
        request_id=resolve_request_id(request.headers.get("X-Request-ID")),
        detalhes={"status": acesso.status},
    )
    corpo = _detalhe(db, acesso)
    db.commit()
    return corpo


@router.post("/{document_id}/acesso-resultado")
def gerar_acesso(
    document_id: str,
    payload: PedidoDeGeracao,
    request: Request,
    db: Session = Depends(get_db),
    operador: User = Depends(require_role(ROLE_OPERACIONAL)),
) -> dict:
    """Gera o acesso de um laudo histórico, ou troca o link por um novo.

    Existe porque a automação vale **daqui para frente**: nenhum resultado
    antigo é enviado a ninguém sozinho. Para os laudos que já estavam
    assinados quando a M26.4 subiu, alguém clica — um por um, com nome do
    paciente na tela.
    """

    _exigir_portal()
    documento = _documento(db, document_id)
    request_id = resolve_request_id(request.headers.get("X-Request-ID"))
    acesso = _acesso(db, document_id)

    if acesso is not None and not payload.regenerar:
        return _detalhe(db, acesso)

    if acesso is not None:
        prs.regenerate(db, acesso, user_id=operador.id, request_id=request_id)
        db.commit()
        return _detalhe(db, acesso)

    assinado = db.execute(
        select(ExternalSignedDocument)
        .where(ExternalSignedDocument.report_document_id == documento.id)
        .where(ExternalSignedDocument.status.in_(prs.ESTADOS_ENTREGAVEIS_MANUAL))
        .order_by(ExternalSignedDocument.received_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if assinado is None:
        raise ReportDomainError(
            409,
            "sem_laudo_assinado",
            "Este laudo ainda não tem um PDF assinado pronto para entrega.",
        )
    criado = prs.ensure_access(
        db,
        assinado,
        user_id=operador.id,
        request_id=request_id,
        permitir_entregue=True,
    )
    if criado is None:
        raise ReportDomainError(
            409,
            "acesso_nao_pode_ser_criado",
            "Não foi possível criar o acesso deste laudo.",
        )
    db.commit()
    return _detalhe(db, criado)


@router.post("/{document_id}/acesso-resultado/revogar")
def revogar_acesso(
    document_id: str,
    payload: PedidoDeRevogacao,
    request: Request,
    db: Session = Depends(get_db),
    operador: User = Depends(require_role(ROLE_OPERACIONAL)),
) -> dict:
    """Link morto na hora, PDF fechado, sessão aberta derrubada junto."""

    _exigir_portal()
    _documento(db, document_id)
    acesso = _acesso(db, document_id)
    if acesso is None:
        raise ReportDomainError(
            404, "acesso_nao_encontrado", "Este laudo não tem acesso gerado."
        )
    prs.revoke(
        db,
        acesso,
        user_id=operador.id,
        motivo=payload.motivo,
        request_id=resolve_request_id(request.headers.get("X-Request-ID")),
    )
    db.commit()
    return resumo_do_acesso(acesso)


@router.post("/{document_id}/acesso-resultado/enviado")
def marcar_enviado(
    document_id: str,
    payload: PedidoDeEnvio,
    request: Request,
    db: Session = Depends(get_db),
    operador: User = Depends(require_role(ROLE_OPERACIONAL)),
) -> dict:
    """Registra que o operador ABRIU o envio — não que o paciente recebeu.

    Chamado quando ele clica em "Enviar pelo WhatsApp" ou copia o link. O
    sistema não tem como saber se a mensagem chegou; dizer "entregue" aqui
    seria inventar um fato.
    """

    _exigir_portal()
    _documento(db, document_id)
    acesso = _acesso(db, document_id)
    if acesso is None:
        raise ReportDomainError(
            404, "acesso_nao_encontrado", "Este laudo não tem acesso gerado."
        )
    if acesso.status == RESULTADO_REVOGADO:
        raise ReportDomainError(
            409, "acesso_revogado", "Este acesso foi revogado."
        )
    prs.mark_sent(
        db,
        acesso,
        user_id=operador.id,
        canal=payload.canal[:40],
        request_id=resolve_request_id(request.headers.get("X-Request-ID")),
    )
    db.commit()
    return resumo_do_acesso(acesso)
