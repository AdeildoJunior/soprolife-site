"""Migrations EEG preservam história e recusam apagar evidência operacional."""
from pathlib import Path
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect


def test_migration_aditiva_reversivel_sem_backfill_e_sem_apagar_evidencia(tmp_path, monkeypatch):
    monkeypatch.delenv("M15_DATABASE_URL", raising=False)
    url=f"sqlite:///{tmp_path}/isolado.db"
    root=Path(__file__).parents[1]
    cfg=Config(str(root/'alembic.ini'))
    cfg.set_main_option('script_location',str(root/'migrations'))
    cfg.set_main_option('sqlalchemy.url',url)
    command.upgrade(cfg,'b70c4a2e9d15')
    engine=create_engine(url)
    ident=str(uuid4())
    from sqlalchemy.orm import Session
    from app.models import Person
    with Session(engine) as db:
        db.add(Person(id=ident, public_code="PES-900001", nome_completo="Pessoa Historica Sintetica", nome_normalizado="pessoa historica sintetica"))
        db.commit()
    before={t: [x['name'] for x in inspect(engine).get_columns(t)] for t in inspect(engine).get_table_names()}
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(cfg)
    # M26.33 (autorização de entrega de resultado) soma uma migration
    # aditiva no topo; a cadeia EEG abaixo dela segue intacta.
    # M26.34 (documento fiscal agrupado) soma uma migration no topo; a
    # cadeia abaixo dela segue intacta.
    # M26.36 (reteste de controle sem cobrança) soma outra no topo, aditiva
    # e reversível, sem backfill — a cadeia abaixo dela segue intacta.
    assert script.get_heads() == ["f1e4a2b72029"]
    assert script.get_revision("d7b2c4e86a91").down_revision == "c3f8a1d7e529"
    assert script.get_revision("c3f8a1d7e529").down_revision == "b5e2f7a91c38"
    assert script.get_revision("b5e2f7a91c38").down_revision == "a1c7e5d9b204"
    assert script.get_revision("a1c7e5d9b204").down_revision == "e2a0b6c719af"
    assert script.get_revision("e2a0b6c719af").down_revision == "c3f0a7b12e45"
    assert script.get_revision("c3f0a7b12e45").down_revision == "b70c4a2e9d15"
    # Sobe até a Fase 2A, não até `head`: o que se prova aqui é que AS
    # MIGRATIONS EEG não alteram tabela preexistente. A M26.34, bem depois
    # delas, altera `fiscal_documents` de propósito (documento agrupado) e tem
    # a sua própria prova de aditividade — medir as duas juntas diria que a
    # Fase 2A mexeu no que ela nunca tocou.
    command.upgrade(cfg,'e2a0b6c719af')
    after={t: [x['name'] for x in inspect(engine).get_columns(t)] for t in before}
    assert before==after
    command.upgrade(cfg,'head')
    command.check(cfg)
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM eeg_enrollments')).scalar()==0
        assert c.execute(text('SELECT id FROM people')).scalar()==ident
    command.downgrade(cfg,'b70c4a2e9d15')
    command.upgrade(cfg,'head')
    command.check(cfg)
    with engine.begin() as c:
        c.execute(text("INSERT INTO eeg_enrollments (id,person_id,status,created_at,updated_at) VALUES (:e,:p,'ativo',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),{'e':str(uuid4()),'p':ident})
    with pytest.raises(RuntimeError,match='Downgrade recusado'):
        command.downgrade(cfg,'b70c4a2e9d15')
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM eeg_enrollments')).scalar()==1
    engine.dispose()
