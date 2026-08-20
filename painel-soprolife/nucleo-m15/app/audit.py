"""Trilha de auditoria append-only, sem PII nos detalhes.

- Sanitização RECURSIVA por allowlist: só chaves aprovadas sobrevivem, em
  qualquer nível; valores são escalares curtos ou listas pequenas deles.
- Append-only imposto em duas camadas: trigger no PostgreSQL (migration) e
  guarda de sessão SQLAlchemy (vale também para SQLite de teste).
"""

from sqlalchemy import event
from sqlalchemy.orm import Session

from .models import AuditLog

# Chaves permitidas nos detalhes — tudo fora daqui é descartado.
ALLOWED_KEYS = {
    "public_code", "campos", "status", "tipo", "canal", "motivo", "followup",
    "candidatos_identidade", "nova_data", "resultado", "modo", "source_type",
    "total", "validas", "rejeitadas", "ambiguas", "sha256", "batch_id",
    "decisao", "contexto", "campo", "marcador", "codigo", "fila", "papel",
    "ativo", "report_version_id", "delivery_mode", "institutional_status",
    "reason_code",
    # M24C — exclusivamente identificadores técnicos e valores fechados.
    "report_code", "exam_code", "origin_type", "physician_profile_id",
    "assignment_id", "previous_assignment_id", "target_user_id", "fields",
    "physician_role", "active", "verification_status", "template_id",
    "template_version", "page_number", "placement", "signature_status",
    "provider", "predecessor_document_id", "supersedes_template_id",
    "clinically_approved", "version",
    # M25.2 — laudo nativo. Somente identificadores técnicos, códigos de
    # catálogo fechado, hashes e valores booleanos. Nenhuma destas chaves
    # carrega texto clínico, identidade de paciente, nome de arquivo,
    # caminho absoluto ou byte de documento.
    "version_number", "conclusion_code", "bronchodilator_code",
    "location_source", "document_sha256", "signed_text_sha256",
    "validation_code", "handwritten_signature_applied",
    "qualified_signature", "addendum_sequence", "addendum_sha256",
    "supersedes_version_id", "size_bytes", "image_width", "image_height",
    "revoked_previous_asset_id",
    # M25.17 — de qual campo estruturado do exame o local do laudo foi
    # derivado, e qual unidade acabou gravada. São id e rótulo fechado de
    # cadastro institucional (a clínica), nunca do paciente. Sem eles, um
    # laudo impresso com o endereço errado não teria como ser rastreado até
    # a decisão que escolheu aquele endereço.
    "origin_source", "origin_partner_unit_id",
    # Arquivamento de cadastro interno de teste: por que saiu da operação e
    # o que saiu junto. `exames` e `laudos` são listas de código público.
    "exames", "laudos",
    # M25.24 — encerramento operacional do exame. `exam_code` e `reason_code`
    # já estavam na allowlist; o que entra aqui é a competência do
    # fechamento Pastore e o total documentado, ambos institucionais.
    "competencia", "valor_documentado", "itens",
    # M25.29D — quantos arquivos de um envio foram recusados por virem de uma
    # PRÉVIA. É uma contagem inteira, no mesmo espírito de "rejeitadas": o
    # que a auditoria precisa saber é que o incidente aconteceu e com que
    # frequência, nunca qual arquivo era.
    "recusadas_por_previa",
    # M25.29G — recusa de documento assinado inválido. Identificador técnico,
    # motivo de catálogo fechado e dois booleanos derivados de hash/conteúdo.
    "signed_document_id", "match_method", "identico_ao_final", "parece_previa",
    # M25.29H — a evidência que sustenta o aceite automático. São todos
    # booleanos derivados dos bytes do arquivo, mais a contagem de recusas e
    # o estado anterior numa reclassificação. Nenhum deles carrega conteúdo
    # do documento: dizem SE uma propriedade vale, nunca qual é o valor.
    "origem_e_a_versao_final", "tem_estrutura_assinatura", "contem_o_final",
    "metadado_coerente", "codigo_validacao_coerente", "recusadas_por_guarda",
    "status_anterior", "aceito",
}

_MAX_STR = 120
_MAX_LIST = 20


def _sanitize_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, (list, tuple)):
        clean = [_sanitize_value(v) for v in value[:_MAX_LIST]
                 if isinstance(v, (str, int, float, bool)) or v is None]
        return clean
    if isinstance(value, dict):
        return sanitize_details(value)
    return None


def sanitize_details(details: dict | None) -> dict | None:
    if details is None:
        return None
    clean: dict = {}
    for key, value in details.items():
        if key not in ALLOWED_KEYS:
            continue
        clean[key] = _sanitize_value(value)
    return clean


def audit(
    db: Session,
    acao: str,
    entidade: str | None = None,
    entidade_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
    detalhes: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            user_id=user_id,
            request_id=request_id,
            detalhes=sanitize_details(detalhes),
        )
    )


class AuditAppendOnlyError(RuntimeError):
    pass


@event.listens_for(Session, "before_flush")
def _audit_append_only_guard(session, _flush_context, _instances):
    """Nenhuma sessão da aplicação pode alterar ou apagar audit_logs."""
    for obj in session.dirty:
        if isinstance(obj, AuditLog) and session.is_modified(obj):
            raise AuditAppendOnlyError("audit_logs é append-only (update bloqueado).")
    for obj in session.deleted:
        if isinstance(obj, AuditLog):
            raise AuditAppendOnlyError("audit_logs é append-only (delete bloqueado).")
