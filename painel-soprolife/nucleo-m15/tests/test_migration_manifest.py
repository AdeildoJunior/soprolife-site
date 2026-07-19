"""Manifesto de snapshot (M15.6A): validação fail-closed, confinamento de
caminho, rejeição de credenciais/URLs e identidade única de snapshot."""

import json

import pytest

from app.migration.manifest import (
    load_and_validate_manifest,
    validate_backup_evidence,
)
from app.migration.mapping import MAPPING_VERSION
from app.migration.service import MigrationError, register_snapshot

from tests._migration_fixtures import (
    LEADS_HEADERS,
    LEADS_ROWS,
    write_backup_evidence,
    write_snapshot_files,
)


def test_manifesto_valido(tmp_path):
    name = write_snapshot_files(tmp_path)
    v = load_and_validate_manifest(name, tmp_path)
    assert v.ok, v.erros
    assert v.file_sha256 and v.manifest_sha256
    assert v.as_dict()["mapping_version_atual"] == MAPPING_VERSION


def test_checksum_divergente(tmp_path):
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(sha256="a" * 64))
    v = load_and_validate_manifest(name, tmp_path)
    assert not v.ok
    assert "checksum_divergente" in v.erros


def test_manifesto_ausente(tmp_path):
    v = load_and_validate_manifest("nao-existe.manifest.json", tmp_path)
    assert not v.ok
    assert "manifesto_ausente" in v.erros


def test_schema_version_desconhecida(tmp_path):
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(schema_version="v99"))
    v = load_and_validate_manifest(name, tmp_path)
    assert v.erros == ["schema_version_desconhecida"]


def test_mapping_version_nao_revisada(tmp_path):
    name = write_snapshot_files(tmp_path, mapping_version="antiga-0.0")
    v = load_and_validate_manifest(name, tmp_path)
    assert "mapping_version_nao_revisada" in v.erros


def test_path_traversal_no_nome_do_manifesto(tmp_path):
    for evil in ("../fora.json", "/etc/passwd", "sub/arq.json", "..", ""):
        v = load_and_validate_manifest(evil, tmp_path)
        assert not v.ok
        assert v.erros[0] in ("caminho_de_manifesto_invalido", "manifesto_ausente")


def test_arquivo_fora_do_diretorio_aprovado(tmp_path):
    (tmp_path.parent / "fora.csv").write_bytes(b"nome\nX\n")
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(arquivo="../fora.csv"))
    v = load_and_validate_manifest(name, tmp_path)
    assert "arquivo_fora_do_diretorio_aprovado" in v.erros


def test_credencial_no_manifesto_rejeitada(tmp_path):
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(api_token="x"))
    v = load_and_validate_manifest(name, tmp_path)
    assert any(e.startswith("conteudo_proibido_no_manifesto") for e in v.erros)


def test_url_no_manifesto_rejeitada(tmp_path):
    name = write_snapshot_files(
        tmp_path,
        mutate_manifest=lambda m: m.update(
            workbook_alias="https://docs.google.com/spreadsheets/x"))
    v = load_and_validate_manifest(name, tmp_path)
    assert any(e.startswith("conteudo_proibido_no_manifesto") for e in v.erros)


def test_cabecalho_desconhecido_sem_mapeamento_explicito(tmp_path):
    headers = LEADS_HEADERS + ["coluna_surpresa"]
    rows = [r + ["x"] for r in LEADS_ROWS]
    name = write_snapshot_files(tmp_path, headers=headers, rows=rows)
    v = load_and_validate_manifest(name, tmp_path)
    assert "cabecalho_desconhecido:coluna_surpresa" in v.erros


def test_cabecalho_extra_aprovado_explicitamente(tmp_path):
    headers = LEADS_HEADERS + ["coluna_surpresa"]
    rows = [r + ["x"] for r in LEADS_ROWS]
    name = write_snapshot_files(
        tmp_path, headers=headers, rows=rows, extras=["coluna_surpresa"])
    v = load_and_validate_manifest(name, tmp_path)
    assert v.ok, v.erros


def test_cabecalho_obrigatorio_ausente(tmp_path):
    headers = ["id", "telefone"]
    rows = [["L1", "(21) 0000-9001"]]
    name = write_snapshot_files(tmp_path, headers=headers, rows=rows)
    v = load_and_validate_manifest(name, tmp_path)
    assert any(e.startswith("cabecalho_obrigatorio_ausente") for e in v.erros)


def test_coluna_monetaria_fora_do_financeiro_e_erro_duro(tmp_path):
    headers = LEADS_HEADERS + ["valor"]
    rows = [r + ["100"] for r in LEADS_ROWS]
    # nem colunas_extras_aprovadas salva a fronteira monetária
    name = write_snapshot_files(
        tmp_path, headers=headers, rows=rows, extras=["valor"])
    v = load_and_validate_manifest(name, tmp_path)
    assert "coluna_monetaria_fora_do_financeiro:valor" in v.erros


def test_financeiro_aceita_valor_como_coluna_propria(tmp_path):
    headers = ["lancamento_id", "tipo", "categoria", "descricao", "valor",
               "data_competencia", "exame_id"]
    rows = [["F001", "receita", "espirometria", "atendimento avulso",
             "180.00", "01/06/2026", "E001"]]
    name = write_snapshot_files(
        tmp_path, source_type="financeiro_lancamentos",
        headers=headers, rows=rows)
    v = load_and_validate_manifest(name, tmp_path)
    assert v.ok, v.erros


def test_row_count_divergente(tmp_path):
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(row_count=99))
    v = load_and_validate_manifest(name, tmp_path)
    assert "row_count_divergente" in v.erros


def test_cabecalhos_divergentes_do_arquivo(tmp_path):
    name = write_snapshot_files(
        tmp_path,
        mutate_manifest=lambda m: m.update(
            headers=["id", "nome", "telefone", "data_primeiro_contato",
                     "origem", "outra"],
        ),
    )
    v = load_and_validate_manifest(name, tmp_path)
    assert "cabecalhos_divergentes_do_arquivo" in v.erros


def test_delimitador_incompativel(tmp_path):
    # arquivo com vírgula, manifesto declarando ponto-e-vírgula
    def declara_pv(m):
        m["delimiter"] = ";"
        # cabeçalhos declarados seguem iguais; o arquivo continua com vírgula

    name = write_snapshot_files(tmp_path, mutate_manifest=declara_pv)
    v = load_and_validate_manifest(name, tmp_path)
    assert not v.ok  # cabeçalho não bate e/ou delimitador incompatível


def test_pcmso_historico_e_registravel_como_arquivo(tmp_path):
    name = write_snapshot_files(
        tmp_path, source_type="pcmso_historico",
        headers=["qualquer", "coluna"], rows=[["a", "b"]])
    v = load_and_validate_manifest(name, tmp_path)
    assert v.ok, v.erros
    assert "fonte_historica_excluida_somente_arquivamento" in v.avisos


def test_snapshot_duplicado_rejeitado(db, tmp_path):
    name = write_snapshot_files(tmp_path)
    register_snapshot(db, name, tmp_path)
    db.commit()
    with pytest.raises(MigrationError) as exc:
        register_snapshot(db, name, tmp_path)
    assert exc.value.codigo == "snapshot_duplicado"


def test_manifesto_invalido_bloqueia_registro(db, tmp_path):
    name = write_snapshot_files(
        tmp_path, mutate_manifest=lambda m: m.update(sha256="b" * 64))
    with pytest.raises(MigrationError) as exc:
        register_snapshot(db, name, tmp_path)
    assert exc.value.codigo == "manifesto_invalido"
    assert "checksum_divergente" in exc.value.detalhes


def test_evidencia_de_backup_valida_e_corrompida(tmp_path):
    ok_name = write_backup_evidence(tmp_path)
    result = validate_backup_evidence(ok_name, tmp_path)
    assert result["ok"], result["erros"]
    assert result["local_rollback"].endswith("backup-teste.dump")

    bad_name = write_backup_evidence(
        tmp_path, name="backup-ruim.evidencia.json",
        backup_name="backup-ruim.dump", corrupt_checksum=True)
    result = validate_backup_evidence(bad_name, tmp_path)
    assert not result["ok"]
    assert "checksum_do_backup_divergente" in result["erros"]

    assert not validate_backup_evidence("inexistente.json", tmp_path)["ok"]
    assert not validate_backup_evidence("../fora.json", tmp_path)["ok"]


def test_evidencia_nao_aceita_credencial(tmp_path):
    name = "evidencia-credencial.json"
    (tmp_path / name).write_text(json.dumps({
        "schema_version": "m15.backup-evidencia.1",
        "arquivo_backup": "b.dump",
        "sha256": "0" * 64,
        "criado_em_utc": "2026-07-01T00:00:00+00:00",
        "database_password": "x",
    }), encoding="utf-8")
    result = validate_backup_evidence(name, tmp_path)
    assert not result["ok"]
