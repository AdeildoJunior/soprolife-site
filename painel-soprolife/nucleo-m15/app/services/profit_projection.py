"""Leitura analítica dos dados reais, sem lançar receita ou despesa nova."""
from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    FinancialEntry, FiscalAttempt, FiscalDocument, FiscalDocumentItem, FiscalPreparation,
    OperationalSession, OperationalSessionExam, Partner, PartnerReferral, PartnerSettlement,
    PartnerSettlementItem, PartnerTransfer, PartnerUnit, PhysicianTransfer,
    ProfitCostRule, ProfitRevenueBasis, ReportDocument, SpirometryExam,
)
from .exam_profit import DEFAULT_RULES, allocate, calculate_exam, cents, money, percent, session_cost
from .partner_pricing import resolve_regra_fiscal_por_exame, FISCAL_OK
from .nfse_validity import fiscal_validity

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
PLACES = {"downtown", "nova_america", "pastore", "maclin", "alergolife", "home"}


def _plain(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", (value or "").lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def classify_place(exam: SpirometryExam, partner: Partner | None,
                   unit: PartnerUnit | None) -> str | None:
    label = _plain(" ".join(filter(None, [exam.local_atendimento,
                                             partner.nome if partner else None,
                                             unit.nome if unit else None,
                                             unit.bairro if unit else None])))
    if "pastore" in label:
        return "pastore" if "ipanema" in label else None
    if "maclin" in label:
        return "maclin"
    if "alergolife" in label or "alergo life" in label:
        return "alergolife"
    if "downtown" in label:
        return "downtown"
    if "nova america" in label:
        return "nova_america"
    if exam.modalidade == "residencial" or "domic" in label or "residenc" in label:
        return "home"
    return None


def rules_at(rule_rows: list[ProfitCostRule], when: date) -> dict[str, Decimal]:
    rules = {}
    latest = {}
    for row in rule_rows:
        if row.effective_from <= when and (row.key not in latest or row.effective_from >= latest[row.key]):
            rules[row.key] = Decimal(row.value)
            latest[row.key] = row.effective_from
    return rules


def _payment(entry: FinancialEntry | None) -> tuple[bool, int | None, int | None]:
    if entry is None:
        return False, None, None
    form = _plain(entry.forma_pagamento)
    if entry.status == "Recebido":
        received = cents(entry.valor)
    elif entry.status == "Pendente":
        received = 0
    else:
        return False, None, None  # parcial: valor pago não registrado
    if "cart" in form or "credito" in form or "debito" in form:
        return True, received, received
    if "pix" in form or "dinheiro" in form or "transferencia" in form:
        return True, received, 0
    return False, received, None


def build_projection(db: Session, start: date, end: date) -> dict:
    """Todos os exames do intervalo; rateio usa também pares fora do filtro."""
    all_exams = db.scalars(select(SpirometryExam).where(
        SpirometryExam.data_exame.is_not(None),
        SpirometryExam.data_exame <= end,
        SpirometryExam.status.in_(("Realizado", "Exame realizado", "Laudo Liberado", "Liberado")),
        SpirometryExam.reteste_sem_cobranca.is_not(True),
    )).all()
    by_id = {e.id: e for e in all_exams}
    partners = {p.id: p for p in db.scalars(select(Partner)).all()}
    units = {u.id: u for u in db.scalars(select(PartnerUnit)).all()}
    places = {e.id: classify_place(e, partners.get(e.partner_id), units.get(e.partner_unit_id))
              for e in all_exams}
    rules = db.scalars(select(ProfitCostRule).order_by(ProfitCostRule.created_at, ProfitCostRule.id)).all()
    basis_by_exam = {}
    for basis in db.scalars(select(ProfitRevenueBasis).order_by(ProfitRevenueBasis.created_at, ProfitRevenueBasis.id)).all():
        basis_by_exam[basis.spirometry_exam_id] = basis
    entries = defaultdict(list)
    receipts = {}
    for row in db.scalars(select(FinancialEntry).where(FinancialEntry.eeg_exam_id.is_(None))).all():
        if row.spirometry_exam_id:
            entries[row.spirometry_exam_id].append(row)
        if row.partner_settlement_id and row.tipo == "receita":
            receipts[row.partner_settlement_id] = row
    settlement_items = {item.spirometry_exam_id: item for item in db.scalars(select(PartnerSettlementItem)).all()}
    settlements = {s.id: s for s in db.scalars(select(PartnerSettlement)).all()}
    settlement_exam_ids = defaultdict(list)
    for item in settlement_items.values():
        settlement_exam_ids[item.settlement_id].append(item.spirometry_exam_id)
    transfers_by_entry = defaultdict(list)
    transfers_by_referral = defaultdict(list)
    for t in db.scalars(select(PartnerTransfer)).all():
        if t.financial_entry_id:
            transfers_by_entry[t.financial_entry_id].append(t)
        if t.partner_referral_id:
            transfers_by_referral[t.partner_referral_id].append(t)
    referrals_by_exam = defaultdict(list)
    for referral in db.scalars(select(PartnerReferral).where(PartnerReferral.spirometry_exam_id.is_not(None))).all():
        referrals_by_exam[referral.spirometry_exam_id].append(referral)
    medical = {(m.physician_profile_id, m.competencia): m
               for m in db.scalars(select(PhysicianTransfer)).all()}
    roots = defaultdict(list)
    for doc in db.scalars(select(ReportDocument).where(
        ReportDocument.corrects_document_id.is_(None), ReportDocument.released_at.is_not(None))).all():
        roots[doc.spirometry_exam_id].append(doc)
    fiscal_docs = {d.id: d for d in db.scalars(select(FiscalDocument).where(
        FiscalDocument.environment == "production", FiscalDocument.state == "issued")).all()
                   if fiscal_validity(db, d)}
    fiscal_base = {}
    issued_attempts = db.scalars(select(FiscalAttempt).where(
        FiscalAttempt.document_id.in_(list(fiscal_docs)),
        FiscalAttempt.phase == "completed", FiscalAttempt.outcome == "issued",
        FiscalAttempt.provider == "production",
    )).all() if fiscal_docs else []
    issued_prep_ids = {a.preparation_id for a in issued_attempts if a.preparation_id}
    preparations = db.scalars(select(FiscalPreparation).where(
        FiscalPreparation.id.in_(issued_prep_ids))).all() if issued_prep_ids else []
    for prep in preparations:
        doc = fiscal_docs.get(prep.document_id)
        if doc and doc.spirometry_exam_id and prep.amount_snapshot is not None:
            fiscal_base[doc.spirometry_exam_id] = cents(prep.amount_snapshot)
    for item in db.scalars(select(FiscalDocumentItem).where(FiscalDocumentItem.environment == "production")).all():
        if item.document_id in fiscal_docs and item.amount is not None:
            fiscal_base[item.spirometry_exam_id] = cents(item.amount)

    links = {link.spirometry_exam_id: link.session_id for link in db.scalars(select(OperationalSessionExam)).all()}
    sessions = {s.id: s for s in db.scalars(select(OperationalSession)).all()}
    pastore_overrides = {s.service_date for s in sessions.values() if s.place == "pastore"}
    groups = defaultdict(list)
    for exam in all_exams:
        if exam.id in links:
            groups["session", links[exam.id]].append(exam.id)
        elif places[exam.id] == "pastore" and exam.data_exame not in pastore_overrides:
            groups["pastore_day", exam.data_exame].append(exam.id)
    allocations = {}
    for (kind, key), exam_ids in groups.items():
        if kind == "session":
            sess = sessions.get(key)
            if not sess or sess.status != "confirmado":
                continue
            place, when = sess.place, sess.service_date
            if place not in PLACES or any(places[eid] != place or by_id[eid].data_exame != when for eid in exam_ids):
                continue
            hours = sess.billable_hours
        else:
            sess = None
            place, when, hours = "pastore", key, None
        try:
            components = session_cost(place, hours, rules_at(rules, when), sess.room_override if sess else None)
        except (ValueError, KeyError):
            continue
        if sess:
            for name, attr in (("parking", "parking_override"), ("fuel", "fuel_override"), ("room", "room_override")):
                override = getattr(sess, attr)
                if override is not None:
                    components[name] = cents(override)
            if sess.other_shared is not None:
                components["other"] = cents(sess.other_shared)
        pieces = {name: allocate(amount, exam_ids) for name, amount in components.items()}
        for eid in exam_ids:
            allocations[eid] = {name: shares[eid] for name, shares in pieces.items()}

    rows = []
    for exam in all_exams:
        if exam.data_exame < start:
            continue
        place = places[exam.id]
        exam_rules = rules_at(rules, exam.data_exame)
        direct = [e for e in entries[exam.id] if e.tipo == "receita" and e.status != "Cancelado"]
        entry = direct[0] if len(direct) == 1 else None
        revenue = cents(entry.valor) if entry else None
        gross = revenue if entry and not exam.partner_id else None
        known, received, card_paid = _payment(entry)
        net_partner = False
        basis = None
        partner_cost = 0 if not exam.partner_id else None
        partner_source = "não aplicável" if not exam.partner_id else None
        direct_other = 0
        card_fee_recorded = None
        tax_recorded = None
        unclassified_fee = False
        for expense in entries[exam.id]:
            if expense.tipo != "despesa" or expense.status == "Cancelado":
                continue
            category = _plain(expense.categoria)
            if "cartao" in category or "cartão" in category:
                card_fee_recorded = (card_fee_recorded or 0) + cents(expense.valor)
            elif "imposto" in category or "simples" in category:
                tax_recorded = (tax_recorded or 0) + cents(expense.valor)
            elif "taxa" in category:
                unclassified_fee = True
            else:
                direct_other += cents(expense.valor)
        if place == "pastore" and not direct:
            partner = partners.get(exam.partner_id)
            rule = resolve_regra_fiscal_por_exame(db, partner, exam.data_exame) if partner else None
            if rule and rule.situacao == FISCAL_OK:
                revenue = cents(rule.regra.valor_por_exame)
                net_partner = True
                partner_cost = 0
                partner_source = "receita líquida da parceria; sem dedução adicional"
                item = settlement_items.get(exam.id)
                receipt = receipts.get(item.settlement_id) if item else None
                settlement = settlements.get(item.settlement_id) if item else None
                if receipt and settlement and len(settlement_exam_ids[item.settlement_id]) * revenue == cents(receipt.valor):
                    known, received, card_paid = _payment(receipt)
                    # Forma do pagamento do parceiro vale para sua parcela igual.
                    received = allocate(received, settlement_exam_ids[item.settlement_id])[exam.id] if received is not None else None
                    card_paid = allocate(card_paid, settlement_exam_ids[item.settlement_id])[exam.id] if card_paid is not None else None
        elif entry and exam.partner_id:
            basis = basis_by_exam.get(exam.id)
            if not basis or basis.financial_entry_id != entry.id or cents(basis.entry_amount_snapshot) != revenue:
                basis = None
            linked = {t.id: t for t in transfers_by_entry.get(entry.id, [])}
            for referral in referrals_by_exam[exam.id]:
                for transfer in transfers_by_referral[referral.id]:
                    linked[transfer.id] = transfer
            separate = [e for e in entries[exam.id] if e.tipo == "repasse" and e.status != "Cancelado"]
            if basis and basis.basis == "liquido" and not linked and not separate:
                net_partner = True
                partner_cost = 0
                gross = cents(basis.gross_amount) if basis.gross_amount is not None else None
                partner_source = "receita_liquida_confirmada"
            elif basis and basis.basis == "bruto" and len(linked) == 1:
                partner_cost = cents(next(iter(linked.values())).valor)
                gross = revenue
                partner_source = "partner_transfers"
            elif basis and basis.basis == "bruto" and not linked:
                if len(separate) == 1:
                    partner_cost = cents(separate[0].valor)
                    gross = revenue
                    partner_source = "financial_entries.repasse"
                elif not separate and place in ("maclin", "alergolife") and revenue is not None:
                    # Base bruta confirmada por admin e nenhum repasse
                    # explícito: custo analítico, nunca segundo lançamento.
                    partner_cost = percent(revenue, exam_rules[f"{place}.partner_percent"]) if f"{place}.partner_percent" in exam_rules else None
                    gross = revenue
                    partner_source = "regra_percentual" if partner_cost is not None else None
        physician_cost = 0 if exam.reteste_sem_cobranca else None
        if roots[exam.id]:
            root = roots[exam.id][0]
            competence = root.released_at.astimezone(LOCAL_TZ).date().replace(day=1)
            transfer = medical.get((root.released_physician_profile_id, competence))
            if len(roots[exam.id]) == 1 and transfer:
                physician_cost = cents(transfer.unit_amount)
        base = fiscal_base.get(exam.id, revenue)
        result = calculate_exam(
            revenue=revenue, received=received, gross=gross, card_paid=card_paid,
            payment_known=known, tax_base=base, partner_transfer=partner_cost,
            physician_transfer=physician_cost, shared=allocations.get(exam.id),
            rules=exam_rules, partner_revenue_is_net=net_partner,
            extra_direct=direct_other + allocations.get(exam.id, {}).get("other", 0),
            card_fee_override=card_fee_recorded, tax_amount_override=tax_recorded,
        )
        if entry and exam.partner_id and place != "pastore" and not basis:
            result["pendencias"].append("base_receita_parceiro_nao_confirmada")
            result["status"] = "incompleto"
            result["lucro"] = None
            result["margem"] = None
        if unclassified_fee:
            result["status"] = "incompleto"
            result["pendencias"].append("taxa_existente_sem_classificacao")
            result["lucro"] = None
            result["margem"] = None
        if place is None:
            result["status"] = "incompleto"
            result["pendencias"].append("local_nao_classificado")
            result["lucro"] = None
            result["margem"] = None
        session = sessions.get(links.get(exam.id)) if exam.id in links else None
        rows.append({"id": exam.id, "session_id": links.get(exam.id),
                     "fonte_sessao": session.source if session else "pastore_mesmo_dia" if place == "pastore" and exam.id in allocations else None,
                     "horas_faturaveis": str(session.billable_hours) if session and session.billable_hours is not None else None,
                     "exame": exam.public_code, "data": exam.data_exame.isoformat(),
                     "local": place or "não classificado", "parceiro": partners.get(exam.partner_id).nome if exam.partner_id in partners else None,
                     "forma_pagamento": entry.forma_pagamento if entry else None,
                     "status_financeiro": entry.status if entry else "sem_lancamento_individual",
                     "fonte_receita": "financial_entry" if entry else "partnership" if net_partner else None,
                     "fonte_repasse_parceiro": partner_source,
                     **result})
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
    complete = [r for r in rows if r["status"] == "completo"]
    revenue = sum(cents(r["receita"]) for r in complete)
    cost = sum(cents(r["custo_total"]) for r in complete)
    realized = sum(cents(r["lucro"]) for r in complete if r["realizado"])
    forecast = sum(cents(r["lucro"]) for r in complete if not r["realizado"])
    missing = Counter(p for row in rows for p in row["pendencias"])
    by_place = defaultdict(lambda: {"receita": 0, "custo": 0, "exames": 0})
    by_month = defaultdict(lambda: {"receita": 0, "custo": 0, "exames": 0})
    by_day = defaultdict(lambda: {"receita": 0, "custo": 0, "exames": 0})
    composition = Counter()
    for row in complete:
        for group in (by_place[row["local"]], by_month[row["data"][:7]], by_day[row["data"]]):
            group["receita"] += cents(row["receita"])
            group["custo"] += cents(row["custo_total"])
            group["exames"] += 1
        composition.update({k: cents(v) for k, v in row["custos"].items()})
    def series(source):
        return [{"chave": key, "receita": money(v["receita"]), "custos": money(v["custo"]),
                 "lucro": money(v["receita"] - v["custo"]), "exames": v["exames"],
                 "margem": str((Decimal(v["receita"] - v["custo"]) * 100 / v["receita"]).quantize(Decimal("0.1"))) if v["receita"] else None}
                for key, v in sorted(source.items())]
    return {"kpis": {"receita": money(revenue), "custos": money(cost),
                     "lucro": money(revenue - cost), "margem": str((Decimal(revenue - cost) * 100 / revenue).quantize(Decimal("0.1"))) if revenue else None,
                     "lucro_medio": money((revenue - cost) // len(complete)) if complete else None,
                     "custo_medio": money(cost // len(complete)) if complete else None,
                     "exames": len(complete), "incompletos": len(rows) - len(complete),
                     "lucro_realizado": money(realized), "lucro_previsto": money(forecast)},
            "por_local": series(by_place), "por_mes": series(by_month),
            "por_dia": series(by_day),
            "composicao": {k: money(v) for k, v in composition.items()},
            "pendencias": dict(missing), "exames": rows}
