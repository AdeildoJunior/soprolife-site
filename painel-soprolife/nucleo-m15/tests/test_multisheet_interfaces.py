"""Paridade de interfaces e segurança da revisão multiaba M15.6C."""

import io
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.migration.adapters import ADAPTERS_VERSION
from app.migration.multisheet import (
    MultiSheetError,
    _decisions_by_token,
    multi_sheet_review_queue,
    run_multi_sheet_dry_run,
)
from app.migration.review_batch import (
    confirmation_phrase,
    load_private_review_batch,
    prepare_review_batch,
)
from app.models import AuditLog, ImportBatch, MigrationDecision
from tests._multisheet_fixtures import representative_sheets, write_raw_envelope


class _TTY(io.StringIO):
    def isatty(self):
        return True


def _review_document(db, batch_id, overrides=None):
    queue = multi_sheet_review_queue(db, batch_id)
    current = _decisions_by_token(db, batch_id)
    batch = db.get(ImportBatch, batch_id)
    items = []
    for item in queue["items"]:
        if item["status"] != "pendente":
            continue
        token = item["private_reference_token"]
        existing = (current.get(token) or {}).get("decision")
        desired = (overrides or {}).get(token, existing)
        items.append({
            "token": token,
            "category": item["category"],
            "decision": desired,
            "expected_current_decision": existing,
        })
    return {
        "schema_version": "m15.review-batch.1",
        "coverage_mode": "all_actionable",
        "batch_id": batch_id,
        "snapshot_sha256": batch.sha256,
        "mapping_version": queue["mapping_version"],
        "queue_fingerprint": queue["queue_fingerprint"],
        "items": items,
    }


def _write_review_file(path, document):
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _review_setup(db, users, tmp_path):
    name = write_raw_envelope(tmp_path, representative_sheets())
    result = run_multi_sheet_dry_run(
        db, name, base_dir=tmp_path, user_id=users["admin"].id
    )
    db.commit()
    document = _review_document(db, result["batch_id"])
    path = _write_review_file(tmp_path / "revisoes.json", document)
    return result["batch_id"], document, path


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
        "private_reference_token", "category", "category_label", "status",
        "mapping_version", "decision_state", "executable_decisions",
        "requires_executable_decision", "replacement_required",
    }
    # rótulo sanitizado do console: nunca ecoa token privado nem afirma que
    # a gravação oficial da revisão está bloqueada
    assert item["private_reference_token"] not in item["category_label"]
    assert "gravação oficial bloqueada" not in item["category_label"]
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


def test_cli_revisao_lote_preview_escreve_zero_e_nao_vaza_token_ou_pii(
    db, engine, users, tmp_path, monkeypatch, capsys,
):
    batch_id, document, path = _review_setup(db, users, tmp_path)
    candidate = next(
        item for item in document["items"]
        if item["category"] == "candidato_identidade_provavel"
    )
    candidate["decision"] = "vincular_candidato"
    _write_review_file(path, document)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    before_decisions = len(db.execute(select(MigrationDecision)).scalars().all())
    before_audit = len(db.execute(select(AuditLog)).scalars().all())

    assert cli.main([
        "migracao", "revisar-multiaba-em-lote", "--arquivo", path.name,
        "--email", users["admin"].email, "--somente-preview", "--json",
    ]) == 0
    output = capsys.readouterr()
    preview = json.loads(output.out)
    assert preview["mode"] == "preview"
    assert preview["counts"]["new"] == 1
    assert preview["would_write"] is True
    assert preview["operational_migration_executed"] is False
    assert len(db.execute(select(MigrationDecision)).scalars().all()) == before_decisions
    assert len(db.execute(select(AuditLog)).scalars().all()) == before_audit
    assert all(item["token"] not in output.out + output.err
               for item in document["items"])
    assert "Pessoa Sintetica" not in output.out + output.err
    assert batch_id in output.out


def test_cli_revisao_lote_exige_admin_ativo(
    db, engine, users, tmp_path, monkeypatch, capsys,
):
    _batch_id, _document, path = _review_setup(db, users, tmp_path)
    reviewer_email = users["gestor"].email
    db.rollback()
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    assert cli.main([
        "migracao", "revisar-multiaba-em-lote", "--arquivo", path.name,
        "--email", reviewer_email, "--somente-preview", "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().err)["codigo"] == (
        "papel admin obrigatório"
    )
    assert not db.execute(select(MigrationDecision)).scalars().all()


def test_cli_revisao_lote_uma_frase_aplica_e_replay_fica_noop(
    db, engine, users, tmp_path, monkeypatch, capsys,
):
    _batch_id, document, path = _review_setup(db, users, tmp_path)
    candidate = next(
        item for item in document["items"]
        if item["category"] == "candidato_identidade_provavel"
    )
    candidate["decision"] = "vincular_candidato"
    _write_review_file(path, document)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    loaded = load_private_review_batch(path.name)
    phrase = confirmation_phrase(
        loaded.document["batch_id"], loaded.fingerprint
    )
    reviewer_email = users["admin"].email
    db.rollback()  # libera a transação de leitura antes do commit da CLI
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())

    monkeypatch.setattr(cli.sys, "stdin", _TTY(phrase + "\n"))
    assert cli.main([
        "migracao", "revisar-multiaba-em-lote", "--arquivo", path.name,
        "--email", reviewer_email, "--json",
    ]) == 0
    captured = capsys.readouterr()
    assert '"mode": "preview"' in captured.err
    assert json.loads(captured.out)["status"] == "applied"
    decisions = len(db.execute(select(MigrationDecision)).scalars().all())
    audits = len(db.execute(select(AuditLog)).scalars().all())
    db.rollback()

    monkeypatch.setattr(cli.sys, "stdin", _TTY(phrase + "\n"))
    assert cli.main([
        "migracao", "revisar-multiaba-em-lote", "--arquivo", path.name,
        "--email", reviewer_email, "--json",
    ]) == 0
    replay_output = capsys.readouterr()
    assert '"replay": true' in replay_output.err
    assert json.loads(replay_output.out)["status"] == "no_op"
    assert len(db.execute(select(MigrationDecision)).scalars().all()) == decisions
    assert len(db.execute(select(AuditLog)).scalars().all()) == audits


@pytest.mark.parametrize(
    ("stdin", "error_code"),
    [
        (_TTY("frase incorreta\n"), "frase_de_confirmacao_incorreta"),
        (_TTY(""), "confirmacao_interativa_eof"),
        (io.StringIO("qualquer coisa\n"), "confirmacao_exige_tty_interativo"),
    ],
    ids=("frase-errada", "eof", "sem-tty"),
)
def test_cli_revisao_lote_confirmacao_invalida_escreve_zero(
    db, engine, users, tmp_path, monkeypatch, capsys, stdin, error_code,
):
    _batch_id, document, path = _review_setup(db, users, tmp_path)
    candidate = next(
        item for item in document["items"]
        if item["category"] == "candidato_identidade_provavel"
    )
    candidate["decision"] = "vincular_candidato"
    _write_review_file(path, document)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    before_decisions = len(db.execute(select(MigrationDecision)).scalars().all())
    before_audit = len(db.execute(select(AuditLog)).scalars().all())

    assert cli.main([
        "migracao", "revisar-multiaba-em-lote", "--arquivo", path.name,
        "--email", users["admin"].email, "--json",
    ]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err.splitlines()[-1])
    assert error["codigo"] == error_code
    assert len(db.execute(select(MigrationDecision)).scalars().all()) == before_decisions
    assert len(db.execute(select(AuditLog)).scalars().all()) == before_audit


def test_arquivo_revisao_recusa_fora_symlink_e_permissoes_inseguras(
    db, users, tmp_path, monkeypatch,
):
    _batch_id, document, valid = _review_setup(db, users, tmp_path)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    outside_dir = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_dir.mkdir(mode=0o700)
    outside = _write_review_file(outside_dir / "fora.json", document)
    with pytest.raises(MultiSheetError) as exc:
        load_private_review_batch(str(outside))
    assert exc.value.codigo == "arquivo_revisao_fora_diretorio_privado"

    link = tmp_path / "link.json"
    link.symlink_to(outside)
    with pytest.raises(MultiSheetError) as exc:
        load_private_review_batch(link.name)
    assert exc.value.codigo == "arquivo_revisao_symlink_recusado"

    valid.chmod(0o644)
    with pytest.raises(MultiSheetError) as exc:
        load_private_review_batch(valid.name)
    assert exc.value.codigo == "arquivo_revisao_permissoes_inseguras"


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        ("snapshot_sha256", "0" * 64, "snapshot_sha256_nao_confere"),
        ("mapping_version", "m15-divergente", "mapping_version_nao_confere"),
        ("queue_fingerprint", "0" * 64, "fingerprint_fila_divergente"),
    ],
)
def test_revisao_lote_recusa_identidade_ou_fingerprint_divergente(
    db, users, tmp_path, monkeypatch, field, value, error_code,
):
    _batch_id, document, path = _review_setup(db, users, tmp_path)
    document[field] = value
    _write_review_file(path, document)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    loaded = load_private_review_batch(path.name)
    with pytest.raises(MultiSheetError) as exc:
        prepare_review_batch(db, loaded, users["admin"].id)
    assert exc.value.codigo == error_code
    db.rollback()


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("duplicate", "arquivo_revisao_token_repetido"),
        ("missing", "arquivo_revisao_token_ausente"),
        ("extra", "arquivo_revisao_token_extra"),
        ("unknown", "arquivo_revisao_token_desconhecido"),
        ("category", "arquivo_revisao_categoria_modificada"),
        ("decision", "decisao_incompativel_com_categoria"),
    ],
)
def test_revisao_lote_recusa_cobertura_token_categoria_e_decisao_invalidos(
    db, users, tmp_path, monkeypatch, mutation, error_code,
):
    batch_id, document, path = _review_setup(db, users, tmp_path)
    queue = multi_sheet_review_queue(db, batch_id)
    if mutation == "duplicate":
        document["items"].append(dict(document["items"][0]))
    elif mutation == "missing":
        document["items"].pop()
    elif mutation == "extra":
        registered = next(item for item in queue["items"]
                          if item["status"] != "pendente")
        document["items"].append({
            "token": registered["private_reference_token"],
            "category": registered["category"],
            "decision": None,
            "expected_current_decision": None,
        })
    elif mutation == "unknown":
        document["items"][0]["token"] = "priv-" + "0" * 32
    elif mutation == "category":
        document["items"][0]["category"] = "categoria-modificada"
    elif mutation == "decision":
        candidate = next(item for item in document["items"]
                         if item["category"] ==
                         "candidato_identidade_provavel")
        candidate["decision"] = "manter_ambas"
    _write_review_file(path, document)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    with pytest.raises(MultiSheetError) as exc:
        loaded = load_private_review_batch(path.name)
        prepare_review_batch(db, loaded, users["admin"].id)
    assert exc.value.codigo == error_code
    db.rollback()


def test_revisao_lote_schema_version_e_campos_sao_fechados(
    db, users, tmp_path, monkeypatch,
):
    _batch_id, document, path = _review_setup(db, users, tmp_path)
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    document["schema_version"] = "m15.review-batch.2"
    _write_review_file(path, document)
    with pytest.raises(MultiSheetError) as exc:
        load_private_review_batch(path.name)
    assert exc.value.codigo == "arquivo_revisao_schema_version_invalida"
    document["schema_version"] = "m15.review-batch.1"
    document["campo_extra"] = True
    _write_review_file(path, document)
    with pytest.raises(MultiSheetError) as exc:
        load_private_review_batch(path.name)
    assert exc.value.codigo == "arquivo_revisao_schema_fechado_invalido"
