"""API de migração M15.6A: papéis, portões, aprovação idempotente.

A API NUNCA executa importação — execução real é exclusiva da CLI local.
"""

from sqlalchemy.orm import sessionmaker

from app.migration.mapping import MAPPING_VERSION
from app.migration.service import dry_run_snapshot, register_snapshot

from tests._migration_fixtures import csv_bytes, LEADS_HEADERS, LEADS_ROWS, write_snapshot_files


def _prepara_snapshot(engine, tmp_path, monkeypatch):
    """Registra snapshot + dry-run direto no banco do client."""
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        name = write_snapshot_files(tmp_path)
        reg = register_snapshot(db, name, tmp_path)
        db.commit()
        dr = dry_run_snapshot(db, reg["snapshot_id"], tmp_path)
        db.commit()
        return reg, dr
    finally:
        db.close()


def test_listagem_exige_gestor(client, auth, engine, tmp_path, monkeypatch):
    _prepara_snapshot(engine, tmp_path, monkeypatch)
    assert client.get("/api/v1/migracao/snapshots",
                      headers=auth("operacional")).status_code == 403
    assert client.get("/api/v1/migracao/snapshots",
                      headers=auth("leitura")).status_code == 403
    resp = client.get("/api/v1/migracao/snapshots", headers=auth("gestor"))
    assert resp.status_code == 200
    itens = resp.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["source_type"] == "leads"
    assert itens[0]["mapping_version"] == MAPPING_VERSION
    # metadados sem PII: aliases e hash, nunca nome/telefone
    assert "nome" not in itens[0]
    assert itens[0]["sha256"]


def test_detalhe_traz_portoes_e_nunca_executa(client, auth, engine, tmp_path,
                                              monkeypatch):
    reg, _dr = _prepara_snapshot(engine, tmp_path, monkeypatch)
    resp = client.get(f"/api/v1/migracao/snapshots/{reg['snapshot_id']}",
                      headers=auth("gestor"))
    assert resp.status_code == 200
    det = resp.json()
    assert det["execucao_pela_api"] is False
    gates = det["gates"]
    assert gates["dry_run_realizado"]["ok"] is True
    assert gates["aprovacao_humana"]["ok"] is False
    # evidência de backup só é validável localmente pela CLI
    assert gates["backup_validado"]["ok"] is False
    assert det["portoes_ok"] is False
    assert det["snapshot"]["dry_run"]["resumo"]["criticos"] == 0

    assert client.get("/api/v1/migracao/snapshots/id-inexistente",
                      headers=auth("gestor")).status_code == 404


def test_aprovacao_exige_admin_e_nao_duplica(client, auth, engine, tmp_path,
                                             monkeypatch):
    reg, dr = _prepara_snapshot(engine, tmp_path, monkeypatch)
    payload = {
        "sha256": reg["sha256"],
        "mapping_version": MAPPING_VERSION,
        "dry_run_batch_id": dr["batch_id"],
    }
    url = f"/api/v1/migracao/snapshots/{reg['snapshot_id']}/aprovacao"
    assert client.post(url, json=payload,
                       headers=auth("gestor")).status_code == 403
    assert client.post(url, json=payload,
                       headers=auth("operacional")).status_code == 403

    errado = dict(payload, sha256="f" * 64)
    resp = client.post(url, json=errado, headers=auth("admin"))
    assert resp.status_code == 422

    resp = client.post(url, json=payload, headers=auth("admin"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "aprovado"

    # refresh/repetição: idempotente, sem segunda aprovação
    resp = client.post(url, json=payload, headers=auth("admin"))
    assert resp.status_code == 409


def test_dry_run_via_api_exige_admin_e_nao_grava(client, auth):
    content = csv_bytes(LEADS_HEADERS, [list(r) for r in LEADS_ROWS])
    files = {"file": ("leads.csv", content, "text/csv")}
    resp = client.post("/api/v1/importacoes/dry-run",
                       data={"source_type": "leads"}, files=files,
                       headers=auth("operacional"))
    assert resp.status_code == 403  # papel não autorizado

    resp = client.post("/api/v1/importacoes/dry-run",
                       data={"source_type": "leads"}, files=files,
                       headers=auth("admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "dry_run"
    assert body["validas"] == 3
    assert body["motivos"] == {}
    resp = client.get("/api/v1/pessoas?tamanho=5", headers=auth("gestor"))
    assert resp.json()["total"] == 0  # nada gravado
