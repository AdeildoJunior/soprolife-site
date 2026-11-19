"""Partilhas e acertos sobre FinancialEntry, sem segunda receita ou alteração do legado."""
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from ..audit import audit
from ..ids import allocate_public_code
from ..models import (ExamPartnerShare, FinancialEntry, Partner, PartnerCommercialRule,
                      PartnerShareMovement, PartnerUnit, Partnership, SpirometryExam)
from .maclin import record_basis

HISTORICAL_NET_CODES = frozenset(("ESP-000059", "ESP-000069", "ESP-000072"))


def operational_origin(codigo, partner):
    # O nome institucional pode conter o responsável; a origem é da operação.
    return {'maclin': 'Maclin', 'alergocare': 'Alergocare'}.get(codigo, partner.nome)


def _utc(when):
    return when.astimezone(ZoneInfo("UTC")) if when.tzinfo else when.replace(tzinfo=ZoneInfo("UTC"))


def rule_version_key(rule):
    return rule.vigencia_inicio, _utc(rule.created_at)


def split(gross, partner_percent):
    partner = (gross * partner_percent / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return gross - partner, partner


def resolve_rule(db, codigo, when):
    rows = db.scalars(select(PartnerCommercialRule).where(
        PartnerCommercialRule.codigo == codigo,
        PartnerCommercialRule.vigencia_inicio <= when,
    ).order_by(PartnerCommercialRule.vigencia_inicio.desc(), PartnerCommercialRule.created_at.desc())).all()
    if not rows or (rows[0].vigencia_fim and rows[0].vigencia_fim < when):
        raise HTTPException(422, "Regra comercial ausente para a data do exame.")
    rule = rows[0]
    agreement = db.get(Partnership, rule.partnership_id)
    partner = db.get(Partner, agreement.partner_id)
    unit = db.get(PartnerUnit, rule.partner_unit_id)
    if (partner.arquivado or agreement.status != "ativa" or not unit.ativo
            or unit.partner_id != partner.id):
        raise HTTPException(422, "Parceiro, parceria ou unidade comercial indisponível/incoerente.")
    return rule, partner, unit


def catalog(db):
    rows = []
    for rule in db.scalars(select(PartnerCommercialRule).order_by(PartnerCommercialRule.codigo, PartnerCommercialRule.vigencia_inicio.desc(), PartnerCommercialRule.created_at.desc())):
        agreement = db.get(Partnership, rule.partnership_id)
        partner = db.get(Partner, agreement.partner_id)
        unit = db.get(PartnerUnit, rule.partner_unit_id)
        if partner.arquivado or agreement.status != "ativa" or not unit.ativo:
            continue
        rows.append({"codigo": rule.codigo, "rotulo": partner.nome, "partner_id": partner.id,
                     "origem_operacional": operational_origin(rule.codigo, partner),
                     "partner_unit_id": unit.id, "unidade": unit.nome, "modalidade": rule.modalidade,
                     "percentual_parceiro": str(rule.percentual_parceiro),
                     "percentual_soprolife": str(rule.percentual_soprolife), "fiscal_flow": rule.fiscal_flow,
                     "vigencia_inicio": rule.vigencia_inicio.isoformat(),
                     "vigencia_fim": rule.vigencia_fim.isoformat() if rule.vigencia_fim else None})
    return rows


def create_share(db, exam, entry, codigo, recebido_por, user_id, request_id, *, confirmed_existing=False):
    if exam.public_code in HISTORICAL_NET_CODES:
        raise HTTPException(409, "Histórico líquido consolidado: não admite nova partilha.")
    rule, partner, unit = resolve_rule(db, codigo, exam.data_exame)
    if exam.partner_id != partner.id or (not confirmed_existing and (exam.partner_unit_id, exam.modalidade) != (unit.id, rule.modalidade)):
        raise HTTPException(422, "Exame incompatível com a regra comercial.")
    net, partner_amount = split(entry.valor, rule.percentual_parceiro)
    share = ExamPartnerShare(rule_id=rule.id, spirometry_exam_id=exam.id, financial_entry_id=entry.id,
        partner_id=partner.id, partner_unit_id=unit.id, modalidade=rule.modalidade,
        vigencia_inicio=rule.vigencia_inicio, percentual_parceiro=rule.percentual_parceiro,
        percentual_soprolife=rule.percentual_soprolife, valor_bruto=entry.valor,
        parcela_parceiro=partner_amount, parcela_soprolife=net, recebido_por=recebido_por,
        created_by_user_id=user_id)
    db.add(share)
    db.flush()
    record_basis(db, exam, entry, basis="bruto", gross_amount=entry.valor,
                 evidence_note="Bruto confirmado e partilha pela vigência comercial; sem alteração do lançamento.",
                 user_id=user_id, request_id=request_id)
    audit(db, "parceria.partilha_criada", "exam_partner_shares", share.id, user_id, request_id,
          {"exam_code": exam.public_code, "tipo": recebido_por})
    return share


def position(db, share):
    entry = db.get(FinancialEntry, share.financial_entry_id)
    movements = db.execute(select(PartnerShareMovement, FinancialEntry).join(
        FinancialEntry, FinancialEntry.id == PartnerShareMovement.financial_entry_id).where(
        PartnerShareMovement.share_id == share.id).order_by(PartnerShareMovement.ocorrido_em)).all()
    # Somente recebimento integral confirmado cria obrigação entre as partes.
    confirmed = entry.status == "Recebido"
    payable = share.parcela_parceiro if confirmed and share.recebido_por == "soprolife" else Decimal(0)
    receivable = share.parcela_soprolife if confirmed and share.recebido_por == "parceiro" else Decimal(0)
    settled = Decimal(0)
    cash_in = cash_out = Decimal(0)
    for movement, cash in movements:
        if cash.status == "Cancelado":
            continue
        if movement.natureza == "acerto":
            settled += cash.valor
        if movement.direcao == "entrada":
            cash_in += cash.valor
        else:
            cash_out += cash.valor
    payable = max(Decimal(0), payable - settled)
    receivable = max(Decimal(0), receivable - settled)
    status = ("aguardando_pagamento" if not confirmed else "pendente" if not settled and (payable or receivable)
              else "parcial" if payable or receivable else "acertado" if settled else "sem_repasse")
    settlements = [m for m, cash in movements if m.natureza == "acerto" and cash.status != "Cancelado"]
    local = ZoneInfo("America/Sao_Paulo")
    return {"id": share.id, "exam_id": share.spirometry_exam_id,
        "exame": db.get(SpirometryExam, share.spirometry_exam_id).public_code,
        "parceiro": db.get(Partner, share.partner_id).nome,
        "unidade": db.get(PartnerUnit, share.partner_unit_id).nome,
        "modalidade": share.modalidade, "vigencia_inicio": share.vigencia_inicio.isoformat(),
        "percentual_parceiro": str(share.percentual_parceiro), "percentual_soprolife": str(share.percentual_soprolife),
        "bruto": f"{share.valor_bruto:.2f}", "parcela_parceiro": f"{share.parcela_parceiro:.2f}",
        "parcela_soprolife": f"{share.parcela_soprolife:.2f}", "recebido_por": share.recebido_por,
        "forma_pagamento": entry.forma_pagamento, "status_pagamento_paciente": entry.status,
        "a_pagar": f"{payable:.2f}", "a_receber": f"{receivable:.2f}", "status_acerto": status,
        "data_acerto": _utc(settlements[-1].ocorrido_em).astimezone(local).isoformat() if status == "acertado" else None,
        "entradas_em_conta": f"{cash_in:.2f}", "saidas_de_conta": f"{cash_out:.2f}",
        "movimentos": [{"id": m.id, "natureza": m.natureza, "direcao": m.direcao, "valor": str(f.valor),
                        "conta": m.conta, "origem_bancaria": m.origem_bancaria, "ocorrido_em": _utc(m.ocorrido_em).astimezone(local).isoformat()}
                       for m, f in movements]}


def record_movement(db, share_id, payload, user_id, request_id):
    share = db.scalar(select(ExamPartnerShare).where(ExamPartnerShare.id == share_id).with_for_update())
    if not share:
        raise HTTPException(404, "Partilha não encontrada.")
    previous = db.scalar(select(PartnerShareMovement).where(PartnerShareMovement.idempotency_key == payload.idempotency_key))
    if previous:
        entry = db.get(FinancialEntry, previous.financial_entry_id)
        facts = (previous.share_id, previous.natureza, previous.direcao, previous.conta, previous.origem_bancaria,
                 _utc(previous.ocorrido_em).replace(tzinfo=None), entry.valor, entry.forma_pagamento)
        incoming = (share.id, payload.natureza, payload.direcao, payload.conta, payload.origem_bancaria,
                    payload.ocorrido_em.astimezone(ZoneInfo("UTC")).replace(tzinfo=None), payload.valor, payload.forma_pagamento)
        if facts != incoming:
            raise HTTPException(409, "Chave de idempotência já utilizada com outros dados.")
        return position(db, share)
    revenue = db.get(FinancialEntry, share.financial_entry_id)
    if revenue.status != "Recebido":
        raise HTTPException(409, "Confirme o pagamento do paciente antes de registrar o acerto.")
    pos = position(db, share)
    if payload.natureza == "acerto":
        expected_direction = "entrada" if share.recebido_por == "parceiro" else "saida"
        limit = Decimal(pos["a_receber"] if expected_direction == "entrada" else pos["a_pagar"])
        if payload.direcao != expected_direction or payload.valor > limit:
            raise HTTPException(422, "Acerto incompatível com a direção ou o saldo da obrigação.")
    else:
        # Transferência da parcela já pertencente à SoproLife, sem criar dívida.
        regularized = sum((Decimal(m["valor"]) for m in pos["movimentos"] if m["natureza"] == "regularizacao"), Decimal(0))
        if share.recebido_por == "parceiro" or payload.direcao != "entrada" or regularized + payload.valor > share.parcela_soprolife:
            raise HTTPException(422, "Regularização incompatível com a parcela própria já recebida.")
    when = payload.ocorrido_em.astimezone(ZoneInfo("America/Sao_Paulo")).date()
    entry = FinancialEntry(public_code=allocate_public_code(db, "financial_entries"), tipo="movimentacao",
        categoria="Acerto de parceria", descricao=f"{payload.natureza} {pos['exame']}", valor=payload.valor,
        status="Recebido", data_competencia=when, data_competencia_original=when.isoformat(),
        data_competencia_precisao="dia", data_competencia_dia_assumido=False,
        data_recebimento=when, forma_pagamento=payload.forma_pagamento, spirometry_exam_id=share.spirometry_exam_id)
    db.add(entry)
    db.flush()
    movement = PartnerShareMovement(share_id=share.id, financial_entry_id=entry.id,
        natureza=payload.natureza, direcao=payload.direcao, conta=payload.conta,
        origem_bancaria=payload.origem_bancaria, ocorrido_em=payload.ocorrido_em.astimezone(ZoneInfo("UTC")),
        idempotency_key=payload.idempotency_key, created_by_user_id=user_id)
    db.add(movement)
    db.flush()
    audit(db, "parceria.movimento_registrado", "partner_share_movements", movement.id, user_id, request_id,
          {"exam_code": pos["exame"], "tipo": payload.natureza, "valor_pago": str(entry.valor)})
    return position(db, share)
