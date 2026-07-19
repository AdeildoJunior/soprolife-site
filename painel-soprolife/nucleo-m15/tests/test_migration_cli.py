"""CLI de migração M15.6A: determinística, --json, exit != 0 em falha,
frase final interativa e paridade com a API."""

import json
import pathlib

import pytest
from sqlalchemy.orm import sessionmaker

from app import cli
from app.migration.mapping import MAPPING_VERSION

from tests._migration_fixtures import (
    LEADS_HEADERS,
    LEADS_ROWS,
    csv_bytes,
    write_backup_evidence,
    write_snapshot_files,
)


@pytest.fixture()
def cli_env(engine, tmp_path, monkeypatch):
    """CLI ligada ao banco de teste e ao diretório privado temporário."""
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "_session", lambda: SessionLocal())
    private = tmp_path / "privado"
    private.mkdir()
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(private))
    return private


def _json_out(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    return json.loads(out[-1])


def test_help_de_todos_os_subcomandos(capsys):
    for argv in (
        ["migracao", "--help"],
        ["migracao", "validar-manifesto", "--help"],
        ["migracao", "registrar-snapshot", "--help"],
        ["migracao", "dry-run", "--help"],
        ["migracao", "relatorio", "--help"],
        ["migracao", "preflight", "--help"],
        ["migracao", "aprovar", "--help"],
        ["migracao", "revogar-aprovacao", "--help"],
        ["migracao", "executar", "--help"],
        ["migracao", "reconciliar", "--help"],
        ["migracao", "status", "--help"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == 0
        capsys.readouterr()


def test_validar_manifesto_ok_e_falha(cli_env, capsys):
    name = write_snapshot_files(cli_env)
    assert cli.main(["migracao", "validar-manifesto",
                     "--manifesto", name, "--json"]) == 0
    assert _json_out(capsys)["ok"] is True

    ruim = write_snapshot_files(
        cli_env, manifesto="ruim.manifest.json",
        mutate_manifest=lambda m: m.update(sha256="c" * 64))
    assert cli.main(["migracao", "validar-manifesto",
                     "--manifesto", ruim, "--json"]) == 1
    saida = _json_out(capsys)
    assert "checksum_divergente" in saida["erros"]


def test_fluxo_completo_pela_cli(cli_env, capsys, monkeypatch):
    name = write_snapshot_files(cli_env)
    assert cli.main(["migracao", "registrar-snapshot",
                     "--manifesto", name, "--json"]) == 0
    reg = _json_out(capsys)
    sid = reg["snapshot_id"]

    # duplicado: exit 1, sem segundo registro
    assert cli.main(["migracao", "registrar-snapshot",
                     "--manifesto", name, "--json"]) == 1
    capsys.readouterr()

    assert cli.main(["migracao", "dry-run", "--snapshot", sid, "--json"]) == 0
    dry = _json_out(capsys)
    assert dry["criticos"] == 0

    # preflight ainda reprova (sem aprovação/backup): exit 1
    assert cli.main(["migracao", "preflight", "--snapshot", sid, "--json"]) == 1
    capsys.readouterr()

    ev = write_backup_evidence(cli_env)
    assert cli.main(["migracao", "aprovar", "--snapshot", sid,
                     "--sha256", reg["sha256"],
                     "--mapping-version", MAPPING_VERSION,
                     "--batch", dry["batch_id"], "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["migracao", "preflight", "--snapshot", sid,
                     "--backup-evidencia", ev, "--json"]) == 0
    pf = _json_out(capsys)
    assert pf["ok"] is True

    # frase errada: exit 1 e nada gravado
    monkeypatch.setattr("builtins.input", lambda *_: "frase errada")
    assert cli.main(["migracao", "executar", "--snapshot", sid,
                     "--batch", dry["batch_id"],
                     "--backup-evidencia", ev, "--json"]) == 1
    capsys.readouterr()

    # frase exata digitada: executa
    monkeypatch.setattr("builtins.input",
                        lambda *_: f"EXECUTAR IMPORTACAO {sid}")
    assert cli.main(["migracao", "executar", "--snapshot", sid,
                     "--batch", dry["batch_id"],
                     "--backup-evidencia", ev, "--json"]) == 0
    execucao = _json_out(capsys)
    assert execucao["status"] == "executado"

    # reexecução bloqueada pelo portão de idempotência
    assert cli.main(["migracao", "executar", "--snapshot", sid,
                     "--batch", dry["batch_id"],
                     "--backup-evidencia", ev, "--json"]) == 1
    capsys.readouterr()

    assert cli.main(["migracao", "reconciliar", "--snapshot", sid,
                     "--json"]) == 0
    recon = _json_out(capsys)
    assert recon["fonte_aceitas"] == 3
    assert recon["checksum"]["confere"] is True

    assert cli.main(["migracao", "status", "--snapshot", sid, "--json"]) == 0
    status = _json_out(capsys)
    assert status["status"] == "reconciliado"

    assert cli.main(["migracao", "status", "--json"]) == 0
    todos = _json_out(capsys)
    assert len(todos["snapshots"]) == 1


def test_relatorio_sanitizado_e_neutralizado(cli_env, tmp_path, capsys):
    name = write_snapshot_files(cli_env)
    assert cli.main(["migracao", "registrar-snapshot",
                     "--manifesto", name, "--json"]) == 0
    sid = _json_out(capsys)["snapshot_id"]
    assert cli.main(["migracao", "dry-run", "--snapshot", sid, "--json"]) == 0
    capsys.readouterr()
    saida = tmp_path / "relatorios"
    assert cli.main(["migracao", "relatorio", "--snapshot", sid,
                     "--saida", str(saida), "--json"]) == 0
    paths = _json_out(capsys)
    for chave in ("json", "md", "csv"):
        assert pathlib.Path(paths[chave]).is_file()
    conteudo_csv = pathlib.Path(paths["csv"]).read_text(encoding="utf-8")
    # nenhum PII no relatório: nomes/telefones sintéticos não aparecem
    assert "Lead Teste" not in conteudo_csv
    assert "0000-9101" not in conteudo_csv


def test_status_snapshot_inexistente_exit_1(cli_env, capsys):
    assert cli.main(["migracao", "status", "--snapshot", "nao-existe",
                     "--json"]) == 1


def test_paridade_cli_api_no_dry_run(cli_env, client, auth, capsys):
    """CLI (snapshot governado) e API (upload dry-run) classificam IGUAL."""
    rows = [list(r) for r in LEADS_ROWS] + [
        ["L004", "Lead Teste 004", "(21) 0000-9104", "data-invalida", "x", ""],
    ]
    name = write_snapshot_files(cli_env, rows=rows)
    assert cli.main(["migracao", "registrar-snapshot",
                     "--manifesto", name, "--json"]) == 0
    sid = _json_out(capsys)["snapshot_id"]
    assert cli.main(["migracao", "dry-run", "--snapshot", sid, "--json"]) == 0
    via_cli = _json_out(capsys)

    content = csv_bytes(LEADS_HEADERS, rows)
    resp = client.post(
        "/api/v1/importacoes/dry-run",
        data={"source_type": "leads"},
        files={"file": ("leads.csv", content, "text/csv")},
        headers=auth("admin"),
    )
    assert resp.status_code == 200
    via_api = resp.json()
    assert via_cli["total"] == via_api["total"]
    assert via_cli["validas"] == via_api["validas"]
    assert via_cli["rejeitadas"] == via_api["rejeitadas"]
    assert via_cli["ambiguas"] == via_api["ambiguas"]
    assert via_cli["motivos"] == via_api["motivos"]
