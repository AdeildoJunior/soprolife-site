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
    FinancialEntry, FiscalDocument, FiscalDocumentItem,
    OperationalSession, OperationalSessionExam, Partner, PartnerReferral, PartnerSettlement,
    PartnerSettlementItem, PartnerTransfer, PartnerUnit, PhysicianTransfer,
    ProfitCostRule, ProfitRevenueBasis, ReportDocument, SpirometryExam, Person,
)
from .exam_profit import allocate, calculate_exam, cents, money, percent, session_cost
from .partner_pricing import resolve_regra_fiscal_por_exame, FISCAL_OK
from .nfse_validity import fiscal_validity
from .maclin import split_gross

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
        received = None  # parcial: valor pago não registrado
    if form in {"cartao", "credito", "debito", "cartao de credito", "cartao de debito"}:
        return True, received, cents(entry.valor)
    if form in {"pix", "dinheiro", "transferencia", "transferencia bancaria"}:
        return True, received, 0
    return False, received, None


def build_projection(db: Session, start: date, end: date) -> dict:
    """Todos os exames do intervalo; rateio usa também pares fora do filtro."""
    all_exams = db.scalars(select(SpirometryExam).join(Person, Person.id == SpirometryExam.person_id).where(
        Person.arquivado.is_not(True),
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
    issued_exam_ids = {d.spirometry_exam_id for d in fiscal_docs.values() if d.spirometry_exam_id}
    for item in db.scalars(select(FiscalDocumentItem).where(FiscalDocumentItem.environment == "production")).all():
        if item.document_id in fiscal_docs:
            issued_exam_ids.add(item.spirometry_exam_id)

    links = {link.spirometry_exam_id: link.session_id for link in db.scalars(select(OperationalSessionExam)).all()}
    sessions = {s.id: s for s in db.scalars(select(OperationalSession)).all()}
    explicit_days = {(s.place, s.service_date) for s in sessions.values()}
    session_sources = {}
    session_hours = {}
    groups = defaultdict(list)
    for exam in all_exams:
        if exam.id in links:
            groups["session", links[exam.id]].append(exam.id)
        elif not exam.data_exame_dia_assumido:
            place = places[exam.id]
            if place == "home":
                groups["home_visit", exam.id].append(exam.id)
            elif place in PLACES and (place, exam.data_exame) not in explicit_days:
                identity = place if place in {"downtown", "nova_america", "pastore"} else (place, exam.partner_unit_id or _plain(exam.local_atendimento))
                groups["place_day", (identity, exam.data_exame)].append(exam.id)
    allocations = {}
    for (kind, key), exam_ids in groups.items():
        if kind == "session":
            sess = sessions.get(key)
            if not sess or sess.status != "confirmado":
                continue
            place, when = sess.place, sess.service_date
            if place not in PLACES or any(places[eid] != place or by_id[eid].data_exame != when or by_id[eid].data_exame_dia_assumido for eid in exam_ids):
                continue
            hours = sess.billable_hours
        else:
            sess = None
            place = places[exam_ids[0]]
            when = by_id[exam_ids[0]].data_exame
            hours = rules_at(rules, when).get(f"{place}.minimum_hours") if place in ("downtown", "nova_america") else None
        try:
            components = session_cost(
                place, hours, rules_at(rules, when), sess.room_override if sess else None,
                parking_override=sess.parking_override if sess else None,
                fuel_override=sess.fuel_override if sess else None, partial=True,
            )
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
            session_sources[eid] = sess.source if sess else "domiciliar_individual" if place == "home" else "local_mesma_data"
            session_hours[eid] = str(hours) if hours is not None else None
            allocations[eid] = {name: shares[eid] for name, shares in pieces.items()}

    rows = []
    for exam in all_exams:
        if exam.data_exame < start:
            continue
        place = places[exam.id]
        exam_rules = rules_at(rules, exam.data_exame) if not exam.data_exame_dia_assumido else {}
        direct = [e for e in entries[exam.id] if e.tipo == "receita" and e.status != "Cancelado"]
        entry = direct[0] if len(direct) == 1 else None
        payment_entry = entry
        settlement = None
        revenue = cents(entry.valor) if entry else None
        gross = revenue if entry and not exam.partner_id else None
        known, received, card_paid = _payment(entry)
        net_partner = False
        basis = None
        partner_cost = 0 if not exam.partner_id and place not in ("maclin", "alergolife", "pastore") else None
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
                if receipt and settlement and receipt.status != "Cancelado" and settlement.status != "cancelado" and len(settlement_exam_ids[item.settlement_id]) * revenue == cents(receipt.valor):
                    payment_entry = receipt
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
            if basis and basis.basis == "liquido" and (place == "maclin" or (not linked and not separate)):
                net_partner = True
                partner_cost = 0
                gross = cents(basis.gross_amount) if basis.gross_amount is not None else None
                partner_source = "receita_liquida_confirmada"
                if place == "maclin" and card_paid:
                    # O caixa do histórico guarda só os 70%; a maquininha
                    # processou o bruto. Ausência do bruto só bloqueia cartão.
                    card_paid = gross
            elif basis and basis.basis == "bruto" and place == "maclin":
                gross = revenue
                if exam_rules.get("maclin.partner_percent") == Decimal("30.00"):
                    net, _ = split_gross(entry.valor)
                    revenue = cents(net)
                    received = revenue if received == gross else received
                    net_partner = True
                    partner_cost = 0
                    partner_source = "maclin_liquido_do_bruto"
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
        # Fiscal permanece independente do imposto gerencial autorizado.
        base = revenue
        result = calculate_exam(
            revenue=revenue, received=received, gross=gross, card_paid=card_paid,
            payment_known=known, tax_base=base, partner_transfer=partner_cost,
            physician_transfer=physician_cost, shared=allocations.get(exam.id),
            rules=exam_rules, partner_revenue_is_net=net_partner,
            extra_direct=direct_other + allocations.get(exam.id, {}).get("other", 0),
            card_fee_override=card_fee_recorded, tax_amount_override=tax_recorded,
            contractual_partner=place == "pastore" and net_partner,
        )
        if place in ("maclin", "alergolife", "pastore") and not exam.partner_id:
            result["pendencias"].append("parceiro_nao_definido")
            result.update(status="incompleto", lucro=None, margem=None)
        if place == "maclin" and basis and basis.basis == "liquido" and known and card_paid is None:
            result["pendencias"] = ["valor_bruto_cartao_nao_comprovado" if p == "forma_de_pagamento_nao_classificada" else p
                                    for p in result["pendencias"]]
        if exam.data_exame_dia_assumido:
            result["pendencias"].append("data_exame_imprecisa")
            result.update(status="incompleto", lucro=None, margem=None)
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
        if result["status"] != "completo":
            result["custo_total"] = None
            result["lucro_competencia"] = result["lucro_realizado"] = None
        session = sessions.get(links.get(exam.id)) if exam.id in links else None
        maclin_split = None
        if place == "maclin" and basis and net_partner:
            maclin_split = {"modelo": "historico_liquido" if basis.basis == "liquido" else "novo_bruto",
                            "parcela_soprolife": money(revenue),
                            "parcela_maclin": money(gross - revenue) if gross is not None else None}
        rows.append({"id": exam.id, "session_id": links.get(exam.id),
                     "partilha_maclin": maclin_split,
                     "fonte_sessao": session_sources.get(exam.id),
                     "horas_faturaveis": session_hours.get(exam.id),
                     "fonte_horas": "sessao_registrada" if session else "minimo_operacional_aprovado" if session_hours.get(exam.id) else None,
                     "exame": exam.public_code, "data": exam.data_exame.isoformat(),
                     "local": place or "não classificado", "parceiro": partners.get(exam.partner_id).nome if exam.partner_id in partners else None,
                     "forma_pagamento": payment_entry.forma_pagamento if payment_entry else None,
                     "data_recebimento": payment_entry.data_recebimento.isoformat() if payment_entry and payment_entry.data_recebimento else None,
                     "status_financeiro": payment_entry.status if payment_entry else settlement.status if settlement else "sem_lancamento_individual",
                     "fonte_receita": "financial_entry" if entry else "partnership" if net_partner else None,
                     "fonte_repasse_parceiro": partner_source,
                     "fonte_base_imposto": "receita_reconhecida" if base is not None else None,
                     "status_fiscal": "EMITIDA" if exam.id in issued_exam_ids else "NÃO EMITIDA",
                     **result})
    return summarize(rows)


def summarize(rows: list[dict]) -> dict:
    def known_sum(source, key):
        values = [cents(r[key]) for r in source if r.get(key) is not None]
        return money(sum(values)) if values else None

    def metrics(source):
        complete = [r for r in source if r["status"] == "completo"]
        revenue = known_sum(complete, "receita")
        profit = known_sum(complete, "lucro")
        cost = known_sum(complete, "custo_total")
        counts = {key: sum(r.get(key) is not None for r in source)
                  for key in ("receita", "recebido", "pendente", "custos_comprovados")}
        return {
            "receita": known_sum(source, "receita"),
            "recebido": known_sum(source, "recebido"),
            "a_receber": known_sum(source, "pendente"),
            "custos": known_sum(source, "custos_comprovados"),
            "receita_calculada": revenue, "custo_total_calculado": cost,
            "lucro": profit, "lucro_competencia": profit,
            "margem": str((Decimal(profit) * 100 / Decimal(revenue)).quantize(Decimal("0.1"))) if profit is not None and revenue is not None and Decimal(revenue) else None,
            "lucro_medio": money(cents(Decimal(profit) / len(complete))) if complete else None,
            "custo_medio": money(cents(Decimal(cost) / len(complete))) if complete else None,
            "exames": len(complete), "incompletos": len(source) - len(complete),
            "total_exames": len(source), "cobertura": counts,
            "informacoes_faltantes": sum(len(r["pendencias"]) for r in source),
            "lucro_realizado": known_sum([r for r in complete if r["realizado"]], "lucro"),
            "exames_caixa_comprovado": sum(r["realizado"] is True for r in complete),
            "exames_caixa_pendente": sum(r["realizado"] is not True for r in complete),
            "lucro_previsto": known_sum([r for r in complete if not r["realizado"]], "lucro"),
        }

    missing = Counter(p for row in rows for p in row["pendencias"])
    by_place, by_month, by_day = (defaultdict(list) for _ in range(3))
    composition = Counter()
    for row in rows:
        by_place[row["local"]].append(row)
        by_month[row["data"][:7]].append(row)
        by_day[row["data"]].append(row)
        composition.update({k: cents(v) for k, v in row.get("custos", {}).items() if v is not None})
    def series(source):
        return [{"chave": key, **metrics(group)} for key, group in sorted(source.items())]
    return {"kpis": metrics(rows),
            "por_local": series(by_place), "por_mes": series(by_month),
            "por_dia": series(by_day),
            "composicao": {k: money(v) for k, v in composition.items()},
            "composicao_parcial": any(r["status"] != "completo" for r in rows),
            "pendencias": dict(missing), "exames": rows}
