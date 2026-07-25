"""Serialização das entidades para respostas JSON.

Timestamps: armazenados em UTC; expostos como *_utc e *_local
(America/Sao_Paulo, configurável).
"""

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from .config import get_settings
from .status_display import exam_status_display
from . import models as m

# Política de arredondamento monetário do núcleo: ROUND_HALF_UP, 2 casas.
MONEY_QUANT = Decimal("0.01")


def money(value) -> str | None:
    """Serializa Decimal como string monetária sem perda (ex.: "250.00")."""
    if value is None:
        return None
    return str(Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP))


def to_local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(get_settings().display_timezone)).isoformat()


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _legacy(obj) -> dict:
    return {
        "legacy_source": obj.legacy_source,
        "legacy_id": obj.legacy_id,
        "import_batch_id": obj.import_batch_id,
    }


def _stamps(obj) -> dict:
    return {
        "created_at_utc": iso(obj.created_at),
        "created_at_local": to_local(obj.created_at),
        "updated_at_utc": iso(getattr(obj, "updated_at", None)),
    }


def ser_contact(c: m.PersonContact) -> dict:
    return {
        "id": c.id,
        "tipo": c.tipo,
        "valor": c.valor,
        "valor_normalizado": c.valor_normalizado,
        "principal": c.principal,
        "ativo": c.ativo,
        "nao_discavel": c.nao_discavel,
    }


def ser_person(p: m.Person, with_contacts: bool = True) -> dict:
    data = {
        "id": p.id,
        "public_code": p.public_code,
        "nome_completo": p.nome_completo,
        "status": p.status,
        "nao_contatar": p.nao_contatar,
        "data_nascimento": d(p.data_nascimento),
        "observacao": p.observacao,
        **_legacy(p),
        **_stamps(p),
    }
    if with_contacts:
        data["contatos"] = [ser_contact(c) for c in p.contacts if c.ativo]
    return data


def ser_consent(c: m.Consent) -> dict:
    return {
        "id": c.id,
        "person_id": c.person_id,
        "canal": c.canal,
        "status": c.status,
        "origem": c.origem,
        "ts_utc": iso(c.ts_utc),
        "ts_local": to_local(c.ts_utc),
        "observacao": c.observacao,
    }


def ser_person_relationship(r: m.PersonRelationship) -> dict:
    return {
        "id": r.id,
        "minor_person_id": r.minor_person_id,
        "guardian_person_id": r.guardian_person_id,
        "relationship_type": r.relationship_type,
        "is_legal_guardian": r.is_legal_guardian,
        "active": r.active,
        **_stamps(r),
    }


def _date_meta(obj, prefix: str) -> dict:
    return {
        prefix: d(getattr(obj, prefix)),
        f"{prefix}_original": getattr(obj, f"{prefix}_original"),
        f"{prefix}_precisao": getattr(obj, f"{prefix}_precisao"),
        f"{prefix}_dia_assumido": getattr(obj, f"{prefix}_dia_assumido"),
    }


def ser_lead(l: m.Lead) -> dict:
    return {
        "id": l.id,
        "public_code": l.public_code,
        "person_id": l.person_id,
        "origem": l.origem,
        "canal_entrada": l.canal_entrada,
        "servico_interesse": l.servico_interesse,
        "modalidade": l.modalidade,
        "etapa": l.etapa,
        **_date_meta(l, "data_primeiro_contato"),
        "data_retomada_manual": d(l.data_retomada_manual),
        "responsavel": l.responsavel,
        "observacao": l.observacao,
        **_legacy(l),
        **_stamps(l),
    }


def ser_exam(e: m.SpirometryExam) -> dict:
    return {
        "id": e.id,
        "public_code": e.public_code,
        "person_id": e.person_id,
        **_date_meta(e, "data_exame"),
        "modalidade": e.modalidade,
        "local_atendimento": e.local_atendimento,
        "partner_id": e.partner_id,
        "partner_unit_id": e.partner_unit_id,
        "status": e.status,
        # Apresentação canônica (M20): o valor gravado nunca é reescrito.
        "status_exibicao": exam_status_display(e.status),
        "broncodilatador": e.broncodilatador,
        "origem": e.origem,
        "responsavel": e.responsavel,
        "idempotency_key": e.idempotency_key,
        "observacao": e.observacao,
        **_legacy(e),
        **_stamps(e),
    }


def ser_consultation(c: m.Consultation) -> dict:
    return {
        "id": c.id,
        "public_code": c.public_code,
        "person_id": c.person_id,
        **_date_meta(c, "data_consulta"),
        "modalidade": c.modalidade,
        "profissional": c.profissional,
        "status": c.status,
        "origem": c.origem,
        "responsavel": c.responsavel,
        "idempotency_key": c.idempotency_key,
        "observacao": c.observacao,
        **_legacy(c),
        **_stamps(c),
    }


def ser_partner(p: m.Partner) -> dict:
    return {
        "id": p.id,
        "public_code": p.public_code,
        "nome": p.nome,
        "tipo": p.tipo,
        "status": p.status,
        "cidade": p.cidade,
        "observacao": p.observacao,
        "arquivado": p.arquivado,
        "merged_into_partner_id": p.merged_into_partner_id,
        **_legacy(p),
        **_stamps(p),
    }


def ser_partner_unit(u: m.PartnerUnit) -> dict:
    return {
        "id": u.id,
        "public_code": u.public_code,
        "partner_id": u.partner_id,
        "nome": u.nome,
        "bairro": u.bairro,
        "cidade": u.cidade,
        "ativo": u.ativo,
        "observacao": u.observacao,
    }


def ser_partner_contact(c: m.PartnerContact) -> dict:
    return {
        "id": c.id,
        "public_code": c.public_code,
        "partner_id": c.partner_id,
        "partner_unit_id": c.partner_unit_id,
        "nome": c.nome,
        "cargo": c.cargo,
        "telefone": c.telefone,
        "email": c.email,
        "principal": c.principal,
        "ativo": c.ativo,
    }


def ser_partnership(p: m.Partnership) -> dict:
    return {
        "id": p.id,
        "public_code": p.public_code,
        "partner_id": p.partner_id,
        "status": p.status,
        **_date_meta(p, "data_inicio"),
        "modelo_repasse": p.modelo_repasse,
        "percentual_repasse": money(p.percentual_repasse),
        "valor_repasse_fixo": money(p.valor_repasse_fixo),
        "responsavel_soprolife": p.responsavel_soprolife,
        "responsavel_followup": p.responsavel_followup,
        "observacao": p.observacao,
    }


def _num(v) -> str | None:
    return money(v)


def ser_referral(r: m.PartnerReferral) -> dict:
    return {
        "id": r.id,
        "public_code": r.public_code,
        "person_id": r.person_id,
        "partner_id": r.partner_id,
        "partner_unit_id": r.partner_unit_id,
        "partner_contact_id": r.partner_contact_id,
        **_date_meta(r, "data_encaminhamento"),
        "servico_solicitado": r.servico_solicitado,
        "data_agendada": d(r.data_agendada),
        "data_realizacao": d(r.data_realizacao),
        "status": r.status,
        "spirometry_exam_id": r.spirometry_exam_id,
        "consultation_id": r.consultation_id,
        "financial_entry_id": r.financial_entry_id,
        "valor_cobrado": _num(r.valor_cobrado),
        "valor_recebido": _num(r.valor_recebido),
        "tipo_repasse": r.tipo_repasse,
        "valor_repasse": _num(r.valor_repasse),
        "percentual_repasse": _num(r.percentual_repasse),
        "status_repasse": r.status_repasse,
        "laudo_enviado": r.laudo_enviado,
        "data_envio_laudo": d(r.data_envio_laudo),
        "responsavel_soprolife": r.responsavel_soprolife,
        "observacao_operacional": r.observacao_operacional,
        "autorizacao_contato_soprolife": r.autorizacao_contato_soprolife,
        "responsavel_followup": r.responsavel_followup,
        "proximo_followup": d(r.proximo_followup),
        **_legacy(r),
        **_stamps(r),
    }


def ser_interaction(i: m.Interaction) -> dict:
    return {
        "id": i.id,
        "public_code": i.public_code,
        "person_id": i.person_id,
        "canal": i.canal,
        "direcao": i.direcao,
        "ts_utc": iso(i.ts_utc),
        "ts_local": to_local(i.ts_utc),
        "resumo": i.resumo,
        "resultado": i.resultado,
        "user_id": i.user_id,
        "followup_id": i.followup_id,
    }


def ser_followup(f: m.Followup, fila: str | None = None) -> dict:
    data = {
        "id": f.id,
        "public_code": f.public_code,
        "person_id": f.person_id,
        "patient_person_id": f.patient_person_id,
        "contact_person_id": f.contact_person_id or f.person_id,
        "tipo": f.tipo,
        "origem_entidade": f.origem_entidade,
        "origem_id": f.origem_id,
        "due_date": d(f.due_date),
        "due_date_manual": f.due_date_manual,
        "status": f.status,
        "controlado_por_parceiro": f.controlado_por_parceiro,
        "partner_id": f.partner_id,
        "responsavel": f.responsavel,
        "tentativas": f.tentativas,
        "concluido_em": iso(f.concluido_em),
        "resultado": f.resultado,
        "observacao": f.observacao,
        **_stamps(f),
    }
    if fila is not None:
        data["fila"] = fila
    return data


def ser_financial_entry(e: m.FinancialEntry) -> dict:
    return {
        "id": e.id,
        "public_code": e.public_code,
        "tipo": e.tipo,
        "categoria": e.categoria,
        "descricao": e.descricao,
        "valor": _num(e.valor),
        "moeda": e.moeda,
        **_date_meta(e, "data_competencia"),
        "data_recebimento": d(e.data_recebimento),
        "status": e.status,
        "forma_pagamento": e.forma_pagamento,
        "origem_preco": e.origem_preco,
        "spirometry_exam_id": e.spirometry_exam_id,
        "consultation_id": e.consultation_id,
        "partner_referral_id": e.partner_referral_id,
        "partner_settlement_id": e.partner_settlement_id,
        "idempotency_key": e.idempotency_key,
        **_legacy(e),
        **_stamps(e),
    }


def ser_transfer(t: m.PartnerTransfer) -> dict:
    return {
        "id": t.id,
        "partner_id": t.partner_id,
        "partnership_id": t.partnership_id,
        "partner_referral_id": t.partner_referral_id,
        "settlement_id": t.settlement_id,
        "financial_entry_id": t.financial_entry_id,
        "valor": _num(t.valor),
        "status": t.status,
        "data_prevista": d(t.data_prevista),
        "data_pagamento": d(t.data_pagamento),
        **_stamps(t),
    }


def ser_import_snapshot(s: m.ImportSnapshot) -> dict:
    """Metadados do snapshot privado — aliases e hashes, nunca PII."""
    return {
        "id": s.id,
        "source_type": s.source_type,
        "workbook_alias": s.workbook_alias,
        "sheet_alias": s.sheet_alias,
        "snapshot_ts_utc": iso(s.snapshot_ts_utc),
        "arquivo": s.arquivo,
        "sha256": s.sha256,
        "manifest_sha256": s.manifest_sha256,
        "schema_version": s.schema_version,
        "mapping_version": s.mapping_version,
        "encoding": s.encoding,
        "delimiter": s.delimiter,
        "row_count": s.row_count,
        "linha_inicial_dados": s.linha_inicial_dados,
        "status": s.status,
        "dry_run_batch_id": s.dry_run_batch_id,
        "execute_batch_id": s.execute_batch_id,
        "created_at_utc": iso(s.created_at),
        "created_at_local": to_local(s.created_at),
    }


def ser_import_batch(b: m.ImportBatch) -> dict:
    return {
        "id": b.id,
        "source_type": b.source_type,
        "source_name": b.source_name,
        "sha256": b.sha256,
        "modo": b.modo,
        "status": b.status,
        "total_rows": b.total_rows,
        "valid_rows": b.valid_rows,
        "rejected_rows": b.rejected_rows,
        "ambiguous_rows": b.ambiguous_rows,
        "created_at_utc": iso(b.created_at),
        "created_at_local": to_local(b.created_at),
    }


def ser_import_row(r: m.ImportRow) -> dict:
    return {
        "id": r.id,
        "batch_id": r.batch_id,
        "row_number": r.row_number,
        "legacy_id": r.legacy_id,
        "status": r.status,
        "motivo": r.motivo,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "row_hash": r.row_hash,
    }


def ser_identity_candidate(c: m.IdentityCandidate) -> dict:
    return {
        "id": c.id,
        "origem": c.origem,
        "batch_id": c.batch_id,
        "person_id": c.person_id,
        "candidate_person_id": c.candidate_person_id,
        "motivo": c.motivo,
        "detalhes": c.detalhes,
        "status": c.status,
        "created_at_utc": iso(c.created_at),
    }


def ser_audit(a: m.AuditLog) -> dict:
    return {
        "id": a.id,
        "ts_utc": iso(a.ts_utc),
        "ts_local": to_local(a.ts_utc),
        "request_id": a.request_id,
        "user_id": a.user_id,
        "acao": a.acao,
        "entidade": a.entidade,
        "entidade_id": a.entidade_id,
        "detalhes": a.detalhes,
    }


def ser_user(u: m.User) -> dict:
    """Usuário interno para a administração — NUNCA inclui hash de senha."""
    return {
        "id": u.id,
        "email": u.email,
        "nome": u.nome,
        "ativo": u.ativo,
        "papeis": sorted(r.name for r in u.roles),
        **_stamps(u),
    }
