"""Plan/hash/apply de configuração e confirmação de exame existente; sem criar paciente/exame.

Executar do núcleo, com PYTHONPATH=. e ambiente privado carregado pelo runbook.
Sem --apply: transação revertida, apenas plano saneado. Não emite NFS-e.
"""
import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import (ExamPartnerShare, FinancialEntry, Partner, PartnerCommercialRule,
                        SpirometryExam, User, ProfitRevenueBasis)
from app.services.partner_share_setup import configure, approve_alergocare_direct
from app.services.partner_shares import create_share, position, record_movement
from app.routers.partner_shares import ShareMovementCreate


def perform(db, args, actor):
    if args.mode == 'approve-alergocare-direct':
        return approve_alergocare_direct(db, actor.id)
    if args.mode == 'configure':
        results=[]
        for code, when in [('maclin',args.maclin_from),('alergocare',args.alergocare_from)]:
            assert when is not None, 'Vigências explícitas obrigatórias'
            rule=configure(db,code,when,actor.id)
            results.append({'codigo':code,'vigencia':str(when),'parceria':rule.partnership_id,
                            'unidade':rule.partner_unit_id,'clinica':'30.00','soprolife':'70.00'})
        return {'configuracao':results}
    assert args.exam_code and args.entry_code and args.service_date, 'Informe exame, lançamento e data conferidos'
    exam=db.scalar(select(SpirometryExam).where(SpirometryExam.public_code==args.exam_code).with_for_update())
    assert exam is not None and exam.data_exame==args.service_date and not exam.data_exame_dia_assumido
    assert db.get(Partner,exam.partner_id).nome.strip().casefold()=='maclin'
    entry=db.scalar(select(FinancialEntry).where(FinancialEntry.public_code==args.entry_code).with_for_update())
    assert entry and entry.spirometry_exam_id==exam.id and entry.tipo=='receita'
    assert entry.valor==Decimal('100') and entry.status=='Recebido' and entry.forma_pagamento=='Dinheiro'
    candidates=[]
    for e in db.scalars(select(SpirometryExam).where(SpirometryExam.data_exame>=date(2026,11,1),SpirometryExam.data_exame<=date(2026,11,13))):
        p=db.get(Partner,e.partner_id) if e.partner_id else None
        if 'maclin' in ' '.join([e.origem or '',e.local_atendimento or '',p.nome if p else '']).casefold():
            candidates.append(e.id)
    assert candidates==[exam.id], 'Candidatura Maclin ambígua no intervalo informado'
    share=db.scalar(select(ExamPartnerShare).where(ExamPartnerShare.spirometry_exam_id==exam.id))
    if share is None:
        assert len(db.scalars(select(FinancialEntry).where(FinancialEntry.spirometry_exam_id==exam.id)).all())==1, 'Movimentos/lançamentos prévios exigem revisão'
        assert db.scalar(select(ProfitRevenueBasis).where(ProfitRevenueBasis.spirometry_exam_id==exam.id)) is None, 'Base prévia exige revisão'
        share=create_share(db,exam,entry,'maclin','dividido',actor.id,'admin-existing-confirmed',confirmed_existing=True)
    assert share.recebido_por=='dividido' and share.valor_bruto==Decimal('100')
    result=record_movement(db,share.id,ShareMovementCreate(valor='70.00',natureza='regularizacao',direcao='entrada',
        forma_pagamento='Pix',conta='C6 PJ SoproLife',origem_bancaria='Faustino',
        ocorrido_em=datetime.fromisoformat('2026-11-13T19:28:24-03:00'),
        idempotency_key='maclin-confirmed-20261113-192824'),actor.id,'admin-existing-confirmed')
    # Deterministic review plan: omit generated UUIDs and creation timestamps.
    return {k:result[k] for k in ('exame','bruto','parcela_parceiro','parcela_soprolife','recebido_por',
            'forma_pagamento','a_pagar','a_receber','status_acerto','entradas_em_conta')}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['configure','confirm-existing','approve-alergocare-direct'])
    p.add_argument('--actor',required=True)
    p.add_argument('--maclin-from',type=date.fromisoformat)
    p.add_argument('--alergocare-from',type=date.fromisoformat)
    p.add_argument('--exam-code');p.add_argument('--entry-code');p.add_argument('--service-date',type=date.fromisoformat)
    p.add_argument('--apply',action='store_true');p.add_argument('--plan-sha256')
    args=p.parse_args()
    with Session(get_engine()) as db:
        actor=db.get(User,args.actor)
        assert actor and actor.ativo and any(r.name=='admin' for r in actor.roles), 'Ator admin ativo obrigatório'
        # Serialize setup as well as case confirmation, without altering the actor.
        db.scalar(select(User).where(User.id==actor.id).with_for_update())
        result=perform(db,args,actor)
        encoded=json.dumps(result,sort_keys=True,ensure_ascii=False)
        # Configuration IDs are newly generated on dry-run: hash the explicit intended facts.
        facts={'mode':args.mode,'actor':args.actor,'maclin_from':str(args.maclin_from),
               'alergocare_from':str(args.alergocare_from),'exam':args.exam_code,'entry':args.entry_code,
               'service_date':str(args.service_date)}
        if args.mode in ('confirm-existing','approve-alergocare-direct'):facts['result']=result
        digest=hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()
        print(encoded);print('plan_sha256='+digest)
        if args.apply:
            assert args.plan_sha256==digest, 'Plano não confirmado ou mudou; executar novo dry-run'
            db.commit();print('APPLIED')
        else:
            db.rollback();print('DRY_RUN_ROLLED_BACK')


if __name__=='__main__': main()
