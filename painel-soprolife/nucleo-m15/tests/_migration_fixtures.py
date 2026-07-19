"""Helpers de fixtures SINTÉTICAS para os testes de migração M15.6A.

Nunca contém dado real: nomes 'Teste', telefones não discáveis (prefixo
(21) 0000-xxxx) e hashes calculados na hora.
"""

import hashlib
import json
import pathlib

from app.migration.mapping import MAPPING_VERSION

SNAPSHOT_SCHEMA = "m15.snapshot.1"
EVIDENCE_SCHEMA = "m15.backup-evidencia.1"

LEADS_HEADERS = ["id", "nome", "telefone", "data_primeiro_contato",
                 "origem", "observacao"]
LEADS_ROWS = [
    ["L001", "Lead Teste 001", "(21) 0000-9101", "01/06/2026", "site", ""],
    ["L002", "Lead Teste 002", "(21) 0000-9102", "06/2026", "indicacao", ""],
    ["L003", "Lead Teste 003", "(21) 0000-9103", "2026", "anuncio", ""],
]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def csv_bytes(headers: list[str], rows: list[list[str]],
              delimiter: str = ",") -> bytes:
    lines = [delimiter.join(headers)]
    lines += [delimiter.join(str(c) for c in row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_snapshot_files(
    base: pathlib.Path,
    *,
    source_type: str = "leads",
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    delimiter: str = ",",
    workbook: str = "planilha-operacao-teste",
    sheet: str = "aba-teste",
    arquivo: str | None = None,
    manifesto: str | None = None,
    mapping_version: str = MAPPING_VERSION,
    extras: list[str] | None = None,
    mutate_manifest=None,
    mutate_content=None,
) -> str:
    """Escreve snapshot CSV + manifesto no diretório base. Retorna o nome
    do manifesto. mutate_manifest/mutate_content permitem corromper de
    propósito para os testes de rejeição."""
    headers = headers if headers is not None else list(LEADS_HEADERS)
    rows = rows if rows is not None else [list(r) for r in LEADS_ROWS]
    arquivo = arquivo or f"{source_type}-snap.csv"
    manifesto = manifesto or f"{arquivo}.manifest.json"
    content = csv_bytes(headers, rows, delimiter)
    if mutate_content:
        content = mutate_content(content)
    base.mkdir(parents=True, exist_ok=True)
    (base / arquivo).write_bytes(content)
    manifest = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source_type": source_type,
        "workbook_alias": workbook,
        "sheet_alias": sheet,
        "snapshot_ts_utc": "2026-07-01T00:00:00+00:00",
        "arquivo": arquivo,
        "sha256": sha256_bytes(content),
        "encoding": "utf-8",
        "delimiter": delimiter,
        "headers": headers,
        "row_count": len(rows),
        "linha_inicial_dados": 2,
        "mapping_version": mapping_version,
    }
    if extras is not None:
        manifest["colunas_extras_aprovadas"] = extras
    if mutate_manifest:
        mutate_manifest(manifest)
    (base / manifesto).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifesto


def write_backup_evidence(
    base: pathlib.Path,
    name: str = "backup-teste.evidencia.json",
    backup_name: str = "backup-teste.dump",
    corrupt_checksum: bool = False,
) -> str:
    base.mkdir(parents=True, exist_ok=True)
    payload = b"backup sintetico de teste - sem dados reais"
    (base / backup_name).write_bytes(payload)
    sha = sha256_bytes(payload)
    if corrupt_checksum:
        sha = "0" * 64
    (base / name).write_text(json.dumps({
        "schema_version": EVIDENCE_SCHEMA,
        "arquivo_backup": backup_name,
        "sha256": sha,
        "criado_em_utc": "2026-07-01T00:00:00+00:00",
    }), encoding="utf-8")
    return name
