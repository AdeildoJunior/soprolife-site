"""Read-only partnership projections. IDs/rules are canonical; no new ledger/facts."""
from datetime import date
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from ..models import (ExamPartnerShare, FinancialEntry, PartnerSettlement,
    PartnerSettlementItem, Person, SpirometryExam)
from . import partner_shares, pastore, profit_projection
from .funnel import PERFORMED
from .followup import today_local

CODES = ('pastore', 'maclin', 'alergocare')
LABELS = {'pastore': 'Pastore', 'maclin': 'Maclin', 'alergocare': 'Alergocare'}
MONEY = ('bruto', 'soprolife', 'parceiro', 'a_receber', 'a_pagar')


def catalog(db):
    """Resolve existing canonical identities, never match an exam by free text."""
    result = []
    for code in CODES:
        row = {'codigo': code, 'nome': LABELS[code], 'estado': 'ready'}
        try:
            if code == 'pastore':
                partner = pastore.canonical_pastore(db)
                units = pastore.active_pastore_units(db, partner)
                row['unidades'] = [{'id': u.id, 'nome': u.nome} for u in units]
            else:
                rule, partner, unit = partner_shares.resolve_rule(db, code, today_local())
                row.update(unidades=[{'id': unit.id, 'nome': unit.nome}],
                    regra={'soprolife': str(rule.percentual_soprolife),
                           'parceiro': str(rule.percentual_parceiro), 'fiscal': rule.fiscal_flow})
            row['partner_id'] = partner.id
        except HTTPException:
            row.update(estado='unavailable', partner_id=None, unidades=[])
        result.append(row)
    return result


def code_for_partner(db, partner_id):
    return next((r['codigo'] for r in catalog(db) if r['partner_id'] == partner_id), None)


def _sum(values):
    values = list(values)
    return None if any(v is None for v in values) else f'{sum((Decimal(v) for v in values), Decimal(0)):.2f}'


def _period(start, end):
    if start is None and end is None:
        end = today_local(); start = end.replace(day=1)
    if start is None or end is None or start > end or (end-start).days > 366:
        raise HTTPException(422, 'Informe início/fim válidos, em intervalo de até 366 dias.')
    return start, end


def _in_period(exam, start, end):
    if not exam.data_exame or not start <= exam.data_exame <= end:
        return False
    if exam.data_exame_precisao == 'mes':
        first = exam.data_exame.replace(day=1)
        return start <= first and end >= pastore.month_end(first)
    return exam.data_exame_precisao == 'dia' and not exam.data_exame_dia_assumido


def _pastore_values(db, exams, start, end):
    """Only confirmed complete settlements; never allocate a batch into a subset."""
    from ..routers.pastore import _serialize_settlement
    ids = {e.id for e in exams}
    covered = set(); own = []; receivable = []; pending = 0
    settlements = db.scalars(select(PartnerSettlement).where(
        PartnerSettlement.partner_id == exams[0].partner_id,
        PartnerSettlement.competencia.between(start.replace(day=1), end))).all() if exams else []
    for settlement in settlements:
        if settlement.status == 'cancelado':
            continue
        item_ids = set(db.scalars(select(PartnerSettlementItem.spirometry_exam_id).where(
            PartnerSettlementItem.settlement_id == settlement.id)))
        if not item_ids or not item_ids <= ids:
            continue
        data = _serialize_settlement(db, settlement)
        receipt = db.scalar(select(FinancialEntry).where(FinancialEntry.partner_settlement_id == settlement.id))
        valid_receipt = receipt is not None and receipt.status != 'Cancelado'
        amount = str(receipt.valor) if valid_receipt else data['valor_total']
        own.append(amount); covered.update(item_ids)
        if settlement.status in ('incluido', 'enviado', 'a_receber'):
            pending += 1; receivable.append(amount)
        elif settlement.status == 'recebido' and valid_receipt and receipt.status == 'Recebido':
            receivable.append('0.00')
        else:
            receivable.append(None)
    complete = covered == ids
    return {'bruto': None if exams else '0.00', 'parceiro': None if exams else '0.00',
            'soprolife': _sum(own) if complete else None,
            'a_receber': _sum(receivable) if complete else None,
            'a_pagar': None if exams else '0.00',
            'acertos_pendentes': pending if complete else None,
            'cobertura_acertos': len(covered)}


def project(db, financial, start=None, end=None, selected='todas'):
    start, end = _period(start, end)
    if selected not in ('todas', *CODES):
        raise HTTPException(422, 'Parceria inválida.')
    cards = [c for c in catalog(db) if selected == 'todas' or c['codigo'] == selected]
    partner_ids = [c['partner_id'] for c in cards if c['partner_id']]
    candidates = db.scalars(select(SpirometryExam).join(Person).where(
        SpirometryExam.partner_id.in_(partner_ids), Person.arquivado.is_not(True),
        SpirometryExam.data_exame.between(start, end)).order_by(
        SpirometryExam.data_exame.desc(), SpirometryExam.public_code.desc())).all()
    shares = {r.spirometry_exam_id:r for r in db.scalars(select(ExamPartnerShare).where(
        ExamPartnerShare.spirometry_exam_id.in_([e.id for e in candidates])))} if financial else {}
    legacy_ids = {e.id for e in candidates if e.partner_id in {
        c['partner_id'] for c in cards if c['codigo'] != 'pastore'} and e.id not in shares
        and _in_period(e,start,end) and e.status in PERFORMED}
    projection = {r['id']: r for r in profit_projection.build_projection(db, start, end)['exames']
                  if r['id'] in legacy_ids} if financial and legacy_ids else {}
    all_rows = {}
    for card in cards:
        code = card['codigo']
        exams = [e for e in candidates if e.partner_id == card['partner_id'] and _in_period(e,start,end)
            and (pastore.is_completed_pastore_exam(e) if code == 'pastore' else e.status in PERFORMED)]
        card['datas_imprecisas'] = sum(e.partner_id == card['partner_id'] and not _in_period(e,start,end) for e in candidates)
        card.update(exames=len(exams) if card['partner_id'] else None,
                    ultimo_atendimento={'data': exams[0].data_exame.isoformat(), 'precisao': exams[0].data_exame_precisao,
                        'exame': exams[0].public_code} if exams else None)
        rows = []
        for exam in exams:
            row = {'id': exam.id, 'codigo': exam.public_code, 'data': exam.data_exame.isoformat(),
                   'precisao': exam.data_exame_precisao, 'status': exam.status, 'partner_unit_id': exam.partner_unit_id}
            if financial:
                source = projection.get(exam.id, {})
                revenue, cost = source.get('receita'), source.get('custos', {}).get('parceiro')
                own = Decimal(revenue)-Decimal(cost) if revenue is not None and cost is not None else None
                row.update(bruto=source.get('bruto'), soprolife=str(own) if own is not None else None,
                    parceiro=str(Decimal(source['bruto'])-own) if source.get('bruto') is not None and own is not None else None,
                    a_receber=None, a_pagar=None, status_acerto=None, recebido_por=None, share_id=None)
                share = shares.get(exam.id) if code != 'pastore' else None
                if share and share.partner_id == card['partner_id']:
                    pos = partner_shares.position(db, share)
                    if pos['status_pagamento_paciente'] != 'Cancelado':
                        row.update(bruto=pos['bruto'], soprolife=pos['parcela_soprolife'], parceiro=pos['parcela_parceiro'],
                            **{k:pos[k] for k in ('a_receber','a_pagar','status_acerto','recebido_por')}, share_id=share.id)
                if code == 'pastore':
                    # Batch amounts are presented only on the card/legacy screen.
                    for k in MONEY: row[k] = None
            rows.append(row)
        if financial:
            if not card['partner_id']:
                card['financeiro'] = {**{k:None for k in MONEY}, 'acertos_pendentes':None, 'cobertura_acertos':0}
            elif code == 'pastore':
                card['financeiro'] = _pastore_values(db, exams, start, end)
            else:
                card['financeiro'] = {**{k:_sum(r[k] for r in rows) for k in MONEY},
                    'acertos_pendentes': sum(r['status_acerto'] in ('pendente','parcial') for r in rows) if all(r['status_acerto'] is not None for r in rows) else None,
                    'cobertura_acertos': sum(r['status_acerto'] is not None for r in rows)}
        all_rows[code] = rows
    totals = {'exames': sum(c['exames'] for c in cards) if all(c['exames'] is not None for c in cards) else None}
    if financial:
        totals.update({k:_sum(c['financeiro'][k] for c in cards) for k in MONEY})
        vals = [c['financeiro']['acertos_pendentes'] for c in cards]
        totals['acertos_pendentes'] = sum(vals) if all(v is not None for v in vals) else None
    return {'inicio': start.isoformat(), 'fim': end.isoformat(), 'parceiro':selected,
        'com_financeiro':financial, 'consolidado':totals, 'parcerias':cards,
        'nota':'Valores desconhecidos permanecem em aberto. Saldos atuais correspondem aos exames/fechamentos do período; acertos não são receita.'}, all_rows
