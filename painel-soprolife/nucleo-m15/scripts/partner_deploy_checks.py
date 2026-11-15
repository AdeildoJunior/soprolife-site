"""Read-only production evidence: only hashes, counts and authenticated GETs."""
import argparse
from collections import Counter
import json
from pathlib import Path
from urllib.request import Request, urlopen
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from app.db import get_engine
from app.models import User
from app.security import issue_token

NEW_TABLES={'partner_commercial_rules','exam_partner_shares','partner_share_movements'}
APPEND_TABLES={'partner_units','partnerships','financial_entries','profit_revenue_basis','audit_logs'}


def fingerprint():
    result={}
    with get_engine().connect() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        for table in sorted(inspect(db).get_table_names()):
            if table=='alembic_version':continue
            result[table]=db.scalars(text(f'SELECT md5(row_to_json(t)::text) FROM "{table}" t ORDER BY 1')).all()
        result['_sequences']=[list(r) for r in db.execute(text('SELECT prefix,next_value FROM code_sequences ORDER BY prefix'))]
    print(json.dumps(result,sort_keys=True))


def compare(before,after,allow_append=False):
    a=json.loads(Path(before).read_text());b=json.loads(Path(after).read_text())
    for table,hashes in a.items():
        if table=='_sequences':continue
        if allow_append and table=='code_sequences':continue
        if allow_append and table in (APPEND_TABLES | NEW_TABLES):
            assert not (Counter(hashes)-Counter(b[table])), f'Historical rows changed: {table}'
        else:assert hashes==b[table],f'Protected table changed: {table}'
    if allow_append:
        seq_a=dict(a['_sequences']);seq_b=dict(b['_sequences'])
        assert {k for k in seq_a if seq_a[k]!=seq_b[k]}<= {'UNI','PAR','LAN'}, 'Unexpected sequence changed'
    else:assert a['_sequences']==b['_sequences']
    extra=set(b)-set(a)
    assert extra<=NEW_TABLES
    print(json.dumps({'protected_tables':len(a)-1,'all_preexisting_rows_preserved':True,
                      'appended':{t:len(b[t])-len(a.get(t,[])) for t in sorted(set(b)-{'_sequences','code_sequences'}) if len(b[t])!=len(a.get(t,[]))}},sort_keys=True))


def smoke():
    from whatsapp_deploy_checks import smoke as old_smoke
    old_smoke()
    with Session(get_engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        user=db.scalar(select(User).where(User.ativo.is_(True),User.roles.any(name='admin')).order_by(User.id))
        token=issue_token(user.id,user.password_hash,ttl_minutes=5)
    result={}
    for base,label in [('http://127.0.0.1:8015/api/v1','api'),('http://127.0.0.1:8765/painel-soprolife/api/m15','proxy')]:
        for path in ('/atendimentos/configuracao','/financeiro/partilhas'):
            with urlopen(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=15) as response:
                data=json.load(response);assert response.status==200;result[label+path]=response.status
                if path.endswith('configuracao'):
                    for r in data['espirometria_soprolife']['parcerias_comerciais']:
                        assert r['codigo'] in ('maclin','alergocare')
                        assert r['percentual_parceiro']=='30.00' and r['percentual_soprolife']=='70.00'
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['fingerprint','compare','smoke'])
    p.add_argument('--before');p.add_argument('--after');p.add_argument('--allow-append',action='store_true');a=p.parse_args()
    if a.mode=='fingerprint':fingerprint()
    elif a.mode=='smoke':smoke()
    else:compare(a.before,a.after,a.allow_append)
