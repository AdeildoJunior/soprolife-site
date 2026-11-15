"""Deployment evidence: read-only SQL and GETs only. Never print PII/credentials."""
import argparse
import hashlib
import json
from urllib.request import Request, urlopen
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db import get_engine
from app.models import User
from app.security import issue_token


def fingerprint():
    with get_engine().connect() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        output={}
        for table in sorted(inspect(db).get_table_names()):
            if table=='alembic_version' or table.startswith('whatsapp_'):
                continue
            hashes=db.scalars(text(f'SELECT md5(row_to_json(t)::text) FROM "{table}" t ORDER BY 1')).all()
            output[table]={'count':len(hashes),'sha256':hashlib.sha256(''.join(hashes).encode()).hexdigest()}
        print(json.dumps(output,sort_keys=True))


def smoke():
    settings=get_settings()
    assert settings.whatsapp_integration_enabled is False, 'WhatsApp must remain disabled'
    assert not any((settings.whatsapp_app_secret,settings.whatsapp_verify_token,settings.whatsapp_identity_key)), 'Do not install WhatsApp secrets for preparation'
    with Session(get_engine()) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        user=db.scalar(select(User).where(User.ativo.is_(True),User.roles.any(name='admin')).order_by(User.id))
        assert user is not None, 'No active admin for authenticated read-only smoke'
        token=issue_token(user.id,user.password_hash,ttl_minutes=5)
        assert all(db.scalar(text('SELECT count(*) FROM '+t))==0 for t in ('whatsapp_peers','whatsapp_intakes','whatsapp_events')), 'Preparation must contain zero operational WhatsApp rows'
    paths={'whatsapp':'/integrations/whatsapp/status','funil':'/funil/config',
        'pastore':'/pastore/configuracao-atendimento','financeiro':'/lancamentos?tamanho=1',
        'nfse':'/fiscal/status','eeglife':'/eeg/modalidades','marketing':'/marketing/refresh-status'}
    result={}
    for label,base in [('api','http://127.0.0.1:8015/api/v1'),('proxy','http://127.0.0.1:8765/painel-soprolife/api/m15')]:
        for module,path in paths.items():
            request=Request(base+path,headers={'Authorization':'Bearer '+token})
            with urlopen(request,timeout=10) as response:
                assert response.status==200
                data=json.load(response)
                if module=='whatsapp':
                    assert data['enabled'] is False and data['processing_enabled'] is False
                    assert data['last_valid_event_at'] is None
                    assert data['configuration_ready'] is False
                result[label+'.'+module]=response.status
    print(json.dumps({'read_only_smoke':result,'whatsapp_disabled':True,'whatsapp_rows':0},sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('fingerprint','smoke'))
    args=parser.parse_args()
    (fingerprint if args.mode=='fingerprint' else smoke)()
