"""Paridade CLI/API/UI-contract do dry-run multiaba M15.6B."""

import json

from sqlalchemy.orm import sessionmaker

from app import cli
from app.migration.adapters import ADAPTERS_VERSION
from tests._multisheet_fixtures import representative_sheets, write_raw_envelope


def test_api_multiaba_autorizacao_saida_sanitizada_e_sem_execute(
    client, auth, tmp_path, monkeypatch,
):
    name = write_raw_envelope(tmp_path, representative_sheets())
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    url = "/api/v1/migracao/multiaba/dry-run"
    assert client.post(url, json={"envelope": name}).status_code == 401
    assert client.post(
        url, json={"envelope": name}, headers=auth("gestor")
    ).status_code == 403
    response = client.post(
        url, json={"envelope": name}, headers=auth("admin")
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["execution_allowed"] is False
    assert report["mapping_version"] == ADAPTERS_VERSION
    assert report["reconciliation_preview"]["fechamento_ok"] is True
    assert client.post(
        "/api/v1/migracao/multiaba/executar",
        json={}, headers=auth("admin"),
    ).status_code in (404, 405)

    listing = client.get(
        "/api/v1/migracao/multiaba", headers=auth("gestor")
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["execution_allowed"] is False


def test_api_fila_e_decisao_humana_por_token_privado(
    client, auth, tmp_path, monkeypatch,
):
    name = write_raw_envelope(tmp_path, representative_sheets())
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    run = client.post(
        "/api/v1/migracao/multiaba/dry-run",
        json={"envelope": name}, headers=auth("admin"),
    ).json()
    batch = run["batch_id"]
    queue_url = f"/api/v1/migracao/multiaba/{batch}/revisoes"
    assert client.get(queue_url).status_code == 401
    queue = client.get(queue_url, headers=auth("gestor"))
    assert queue.status_code == 200
    item = next(i for i in queue.json()["items"] if i["status"] == "pendente")
    assert set(item) == {
        "private_reference_token", "category", "status", "mapping_version",
        "decision_state",
    }
    decision_url = (
        f"{queue_url}/{item['private_reference_token']}/decisao"
    )
    decided = client.post(
        decision_url,
        json={"decisao": "resolvido", "mapping_version": ADAPTERS_VERSION},
        headers=auth("gestor"),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decision_state"] == "resolvido"


def test_cli_api_paridade_do_relatorio_multiaba(
    client, auth, engine, tmp_path, monkeypatch, capsys,
):
    name = write_raw_envelope(tmp_path, representative_sheets())
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())

    assert cli.main([
        "migracao", "dry-run-multiaba", "--envelope", name, "--json"
    ]) == 0
    cli_report = json.loads(capsys.readouterr().out)
    api_report = client.get(
        f"/api/v1/migracao/multiaba/{cli_report['batch_id']}",
        headers=auth("gestor"),
    ).json()
    for key in (
        "mapping_version", "totals", "identity_profile",
        "financial_relationship_coverage", "reconciliation_preview", "blockers",
    ):
        assert cli_report[key] == api_report[key]

    assert cli.main([
        "migracao", "status-multiaba", "--batch", cli_report["batch_id"],
        "--json",
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["execution_allowed"] is False


def test_cli_revisao_exige_usuario_existente(
    engine, users, tmp_path, monkeypatch, capsys,
):
    name = write_raw_envelope(tmp_path, representative_sheets())
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    assert cli.main([
        "migracao", "dry-run-multiaba", "--envelope", name, "--json"
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    token = next(
        item["private_reference_token"] for item in report["review_queue"]
        if item["status"] == "pendente"
    )
    assert cli.main([
        "migracao", "revisar-multiaba", "--batch", report["batch_id"],
        "--referencia", token, "--decisao", "resolvido",
        "--mapping-version", ADAPTERS_VERSION, "--json",
    ]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["codigo"] == "revisor_autenticado_obrigatorio"

    assert cli.main([
        "migracao", "revisar-multiaba", "--batch", report["batch_id"],
        "--referencia", token, "--decisao", "resolvido",
        "--mapping-version", ADAPTERS_VERSION,
        "--email", users["gestor"].email, "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["decision_state"] == "resolvido"
