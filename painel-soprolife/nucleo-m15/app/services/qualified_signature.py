"""M25.7 — orquestração da assinatura qualificada ICP-Brasil do laudo.

Este é o único lugar que conhece a máquina de estados completa. As regras
que ele existe para sustentar, e que nenhuma otimização futura pode afrouxar:

- ninguém assina no lugar da médica atribuída — nem admin, nem outra médica;
- nenhuma assinatura acontece sem ação consciente dela no VIDaaS;
- o callback é de USO ÚNICO e amarrado a state + nonce + credencial;
- o laudo só é marcado como assinado depois da validação criptográfica do
  PDF final; falhou a validação, nada é liberado;
- os DOIS hashes são gravados separadamente (preparado e final);
- o PDF técnico da MIR nunca entra aqui, nunca é preparado e nunca é
  enviado à autoridade certificadora.

A liberação institucional (assinatura eletrônica interna) segue existindo em
paralelo e não depende de nada deste módulo.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    QSR_AGUARDANDO_AUTENTICACAO,
    QSR_AGUARDANDO_AUTORIZACAO,
    QSR_ASSINADO_LIBERADO,
    QSR_ASSINATURA_RECEBIDA,
    QSR_EXPIRADO,
    QSR_FALHA_DEFINITIVA,
    QSR_FALHA_RECUPERAVEL,
    QSR_RECUSADO,
    QSR_VALIDANDO,
    QUALIFIED_SIGNATURE_ACTIVE_STATUSES,
    QualifiedSignatureRequest,
    ReportDocument,
    ReportDocumentVersion,
)
from .integraicp_client import (
    IntegraICPClient,
    IntegraICPError,
    generate_pkce,
)
from .report_pades import (
    PADES_LEVEL_ACHIEVED,
    PadesError,
    PreparedPdf,
    inject_cms,
    prepare_pades,
    validate_pades,
)
from .transient_secrets import (
    TransientSecretError,
    open_transient_secret,
    seal_transient_secret,
)


class QualifiedSignatureError(ValueError):
    """Erro de fluxo com `codigo` estável e HTTP sugerido."""

    def __init__(self, http_status: int, codigo: str, mensagem: str):
        self.http_status = http_status
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


@dataclass(frozen=True)
class StartedRequest:
    request: QualifiedSignatureRequest
    authorization_url: str
    prepared: PreparedPdf


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_client(settings: Settings, *, transport=None) -> IntegraICPClient:
    """Cliente configurado. Levanta se a integração não estiver pronta."""

    if not settings.integraicp_ready():
        raise QualifiedSignatureError(
            503,
            "integracao_indisponivel",
            "A assinatura com VIDaaS ainda não está configurada neste "
            "ambiente.",
        )
    return IntegraICPClient(
        base_url=settings.integraicp_base_url or "",
        channel_id=settings.integraicp_channel_id or "",
        callback_url=settings.integraicp_callback_url or "",
        signature_policy=settings.integraicp_signature_policy,
        timeout_seconds=settings.integraicp_request_timeout_seconds,
        transport=transport,
    )


def active_request(
    db: Session, *, document_id: str
) -> QualifiedSignatureRequest | None:
    """Solicitação viva do laudo, se houver — base da idempotência."""

    return db.execute(
        select(QualifiedSignatureRequest)
        .where(
            QualifiedSignatureRequest.report_document_id == document_id,
            QualifiedSignatureRequest.status.in_(
                QUALIFIED_SIGNATURE_ACTIVE_STATUSES
            ),
        )
        .order_by(QualifiedSignatureRequest.created_at.desc())
    ).scalars().first()


def expire_if_due(
    db: Session, request: QualifiedSignatureRequest
) -> QualifiedSignatureRequest:
    """Move para `expirado` quando a janela venceu. Nunca o contrário."""

    if request.status not in QUALIFIED_SIGNATURE_ACTIVE_STATUSES:
        return request
    deadline = request.clearance_expires_at
    if deadline is not None and _now() > deadline:
        request.status = QSR_EXPIRADO
        request.error_code = "clearance_expirado"
        request.error_message = (
            "A janela de autorização no VIDaaS expirou antes da confirmação."
        )
        request.pkce_verifier_encrypted = None
        request.completed_at = _now()
        db.flush()
    return request


def start(
    db: Session,
    *,
    settings: Settings,
    document: ReportDocument,
    version: ReportDocumentVersion,
    prepared_pdf: bytes,
    physician_profile_id: str,
    requested_by_user_id: str,
    reason: str,
    location: str,
    transport=None,
) -> StartedRequest:
    """Abre a solicitação e devolve o endereço para a médica autorizar.

    `prepared_pdf` são os bytes do laudo JÁ congelado (o documento nativo da
    SoproLife). O PDF técnico da MIR nunca deve ser passado aqui.
    """

    client = build_client(settings, transport=transport)

    existing = active_request(db, document_id=document.id)
    if existing is not None:
        existing = expire_if_due(db, existing)
        if existing.status in QUALIFIED_SIGNATURE_ACTIVE_STATUSES:
            # Idempotência: atualizar a página ou clicar de novo não pode
            # abrir uma segunda solicitação nem invalidar a primeira.
            raise QualifiedSignatureError(
                409,
                "solicitacao_em_andamento",
                "Já existe uma solicitação de assinatura em andamento para "
                "este laudo.",
            )

    try:
        prepared = prepare_pades(
            prepared_pdf, reason=reason, location=location
        )
    except PadesError as exc:
        raise QualifiedSignatureError(422, exc.codigo, exc.mensagem) from None

    pkce = generate_pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    now = _now()

    request = QualifiedSignatureRequest(
        report_document_id=document.id,
        report_document_version_id=version.id,
        physician_profile_id=physician_profile_id,
        requested_by_user_id=requested_by_user_id,
        status=QSR_AGUARDANDO_AUTENTICACAO,
        provider="integraicp",
        state_hash=_hash(state),
        nonce_hash=_hash(nonce),
        pkce_verifier_encrypted=seal_transient_secret(
            pkce.verifier, auth_secret=settings.resolved_auth_secret()
        ),
        prepared_sha256=prepared.prepared_sha256,
        signed_digest_sha256=prepared.signed_digest_sha256,
        clearance_expires_at=now
        + timedelta(seconds=settings.integraicp_clearance_lifetime_seconds),
        credential_expires_at=now
        + timedelta(seconds=settings.integraicp_credential_lifetime_seconds),
        attempts=0,
    )
    db.add(request)
    db.flush()

    url = client.build_authentication_url(pkce=pkce, state=state, nonce=nonce)
    request.status = QSR_AGUARDANDO_AUTORIZACAO
    db.flush()
    return StartedRequest(
        request=request, authorization_url=url, prepared=prepared
    )


def resolve_callback(
    db: Session,
    *,
    settings: Settings,
    state: str,
    nonce: str,
    credential_id: str,
    acting_user_id: str,
) -> QualifiedSignatureRequest:
    """Consome o retorno do VIDaaS — uma única vez, e só pela dona do laudo.

    Só encontra a solicitação, confere as amarras e marca o consumo. A
    obtenção da assinatura em si acontece em `complete`, para que uma falha
    de rede não gaste o callback.
    """

    request = db.execute(
        select(QualifiedSignatureRequest)
        .where(QualifiedSignatureRequest.state_hash == _hash(state))
        .with_for_update()
    ).scalar_one_or_none()
    if request is None:
        # Mensagem deliberadamente igual à de state inválido: não confirmar
        # a existência de uma solicitação para quem chuta valores.
        raise QualifiedSignatureError(
            404, "retorno_invalido", "Retorno de assinatura inválido."
        )
    if request.requested_by_user_id != acting_user_id:
        raise QualifiedSignatureError(
            403,
            "retorno_de_outro_usuario",
            "Este retorno de assinatura pertence a outra sessão.",
        )
    if request.callback_consumed_at is not None:
        raise QualifiedSignatureError(
            409,
            "retorno_ja_utilizado",
            "Este retorno de assinatura já foi utilizado.",
        )
    if request.nonce_hash != _hash(nonce):
        raise QualifiedSignatureError(
            400, "retorno_invalido", "Retorno de assinatura inválido."
        )
    request = expire_if_due(db, request)
    if request.status == QSR_EXPIRADO:
        raise QualifiedSignatureError(
            410,
            "solicitacao_expirada",
            "A janela de autorização expirou. Solicite novamente.",
        )
    if request.status not in (
        QSR_AGUARDANDO_AUTORIZACAO,
        QSR_AGUARDANDO_AUTENTICACAO,
    ):
        raise QualifiedSignatureError(
            409,
            "estado_invalido_para_retorno",
            "Esta solicitação não está aguardando autorização.",
        )
    if not credential_id:
        raise QualifiedSignatureError(
            400, "credencial_ausente", "O VIDaaS não devolveu credencial."
        )

    request.callback_consumed_at = _now()
    request.credential_id_hash = _hash(credential_id)
    request.status = QSR_ASSINATURA_RECEBIDA
    db.flush()
    return request


def complete(
    db: Session,
    *,
    settings: Settings,
    request: QualifiedSignatureRequest,
    prepared: PreparedPdf,
    credential_id: str,
    transport=None,
) -> bytes:
    """Obtém o CMS, injeta e VALIDA. Só devolve bytes de PDF já conferido.

    Qualquer falha muda o estado da solicitação e levanta — nunca devolve um
    PDF "provavelmente ok". É este método que decide se o laudo pode ser
    marcado como assinado.
    """

    if prepared.signed_digest_sha256 != request.signed_digest_sha256:
        # Os bytes preparados agora não são os que geraram o digest enviado.
        _fail(db, request, "digest_divergente",
              "O conteúdo do laudo mudou depois do início da assinatura.",
              definitiva=True)
        raise QualifiedSignatureError(
            409,
            "digest_divergente",
            "O conteúdo do laudo mudou depois do início da assinatura.",
        )

    try:
        verifier = open_transient_secret(
            request.pkce_verifier_encrypted,
            auth_secret=settings.resolved_auth_secret(),
        )
    except TransientSecretError:
        _fail(db, request, "verificador_indisponivel",
              "O verificador desta solicitação não está mais disponível.",
              definitiva=True)
        raise QualifiedSignatureError(
            409,
            "verificador_indisponivel",
            "Esta solicitação não pode mais ser concluída. Solicite "
            "novamente.",
        ) from None

    request.attempts += 1
    request.status = QSR_VALIDANDO
    db.flush()

    client = build_client(settings, transport=transport)
    try:
        credential = client.fetch_credential(
            credential_id=credential_id, pkce_verifier=verifier
        )
        result = client.request_signature(
            credential_id=credential_id,
            digest_hex=request.signed_digest_sha256,
            pkce_verifier=verifier,
        )
    except IntegraICPError as exc:
        if exc.codigo == "autorizacao_recusada":
            _fail(db, request, exc.codigo, exc.mensagem,
                  definitiva=True, recusa=True)
        else:
            _fail(db, request, exc.codigo, exc.mensagem,
                  definitiva=not exc.recuperavel)
        raise QualifiedSignatureError(502, exc.codigo, exc.mensagem) from None

    try:
        final_pdf = inject_cms(prepared, result.cms_der)
        validation = validate_pades(
            final_pdf,
            expected_signed_digest_sha256=request.signed_digest_sha256,
        )
    except PadesError as exc:
        _fail(db, request, exc.codigo, exc.mensagem, definitiva=True)
        raise QualifiedSignatureError(422, exc.codigo, exc.mensagem) from None

    # Só aqui a solicitação vira evidência: depois da validação passar.
    request.final_sha256 = validation.final_sha256
    request.external_reference = result.external_reference
    request.signer_subject = validation.signer_subject[:300]
    request.signer_issuer = validation.signer_issuer[:300]
    request.signer_serial = validation.signer_serial[:80]
    request.certificate_not_before = validation.not_valid_before
    request.certificate_not_after = validation.not_valid_after
    # Nível REALMENTE obtido — não o desejado.
    request.pades_level = PADES_LEVEL_ACHIEVED
    request.status = QSR_ASSINADO_LIBERADO
    request.completed_at = _now()
    # Segredo transitório cumpriu o papel: some do banco.
    request.pkce_verifier_encrypted = None
    if credential.subject_name and not request.signer_subject:
        request.signer_subject = credential.subject_name[:300]
    db.flush()
    return final_pdf


def cancel(
    db: Session, request: QualifiedSignatureRequest, *, motivo: str = "cancelado"
) -> QualifiedSignatureRequest:
    """Cancelamento consciente pela médica. Nunca reabre o que já fechou."""

    if request.status not in QUALIFIED_SIGNATURE_ACTIVE_STATUSES:
        raise QualifiedSignatureError(
            409,
            "solicitacao_nao_cancelavel",
            "Esta solicitação não está em andamento.",
        )
    request.status = QSR_RECUSADO
    request.error_code = motivo
    request.error_message = "Solicitação cancelada pela médica."
    request.pkce_verifier_encrypted = None
    request.completed_at = _now()
    db.flush()
    return request


def _fail(
    db: Session,
    request: QualifiedSignatureRequest,
    codigo: str,
    mensagem: str,
    *,
    definitiva: bool,
    recusa: bool = False,
) -> None:
    if recusa:
        request.status = QSR_RECUSADO
    else:
        request.status = (
            QSR_FALHA_DEFINITIVA if definitiva else QSR_FALHA_RECUPERAVEL
        )
    request.error_code = codigo[:60]
    request.error_message = mensagem[:300]
    if request.status != QSR_FALHA_RECUPERAVEL:
        # Falha recuperável mantém o verificador: a médica pode tentar de
        # novo com a MESMA solicitação. Falha definitiva descarta o segredo.
        request.pkce_verifier_encrypted = None
        request.completed_at = _now()
    db.flush()


def diagnostics(settings: Settings) -> dict:
    """Diagnóstico para a tela de administração — apenas booleanos.

    Nunca devolve o valor de nenhum segredo, endpoint, canal ou callback:
    só se estão presentes. Um diagnóstico que vaza a configuração seria
    pior que não ter diagnóstico.
    """

    return {
        "provedor_selecionado": settings.report_signature_provider,
        "integracao_habilitada": bool(settings.integraicp_enabled),
        "integracao_pronta": settings.integraicp_ready(),
        "base_url_configurada": bool(settings.integraicp_base_url),
        "canal_configurado": bool(settings.integraicp_channel_id),
        "callback_configurado": bool(settings.integraicp_callback_url),
        "politica_configurada": bool(settings.integraicp_signature_policy),
        "timeout_segundos": settings.integraicp_request_timeout_seconds,
        "nivel_pades_suportado": PADES_LEVEL_ACHIEVED,
        "mensagem": (
            "Integração pronta para uso."
            if settings.integraicp_ready()
            else "Integração aguardando credencial da Valid."
        ),
    }


def rebuild_prepared(data: bytes) -> PreparedPdf:
    """Reexporta a reconstrução do preparado, para o router não precisar
    conhecer o módulo de PAdES diretamente."""

    from .report_pades import rebuild_prepared as _rebuild

    return _rebuild(data)
