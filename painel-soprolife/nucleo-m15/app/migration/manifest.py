"""Manifesto imutável de snapshot privado + evidência de backup/rollback.

Contrato (M15.6A):
- o snapshot e o manifesto vivem SOMENTE no diretório privado aprovado
  (fora do Git); nomes são simples, sem separador de caminho — qualquer
  tentativa de path traversal falha fechada;
- o manifesto nunca contém credencial, ID de planilha ou URL; qualquer
  marcador desses é rejeição imediata (e nunca vai para log);
- checksum divergente entre manifesto e arquivo = rejeição;
- schema_version desconhecida = rejeição;
- cabeçalho fora do mapeamento só passa com mapeamento explícito
  (colunas_extras_aprovadas);
- nada aqui faz rede: validação é 100% local e determinística.
"""

import csv
import hashlib
import io
import json
import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from ..importer.csv_import import MAX_UPLOAD_BYTES
from . import mapping as mapping_registry
from .mapping import MAPPING_VERSION, check_headers, get_mapping, normalize_header

SCHEMA_VERSIONS = {"m15.snapshot.1"}
EVIDENCE_SCHEMA_VERSIONS = {"m15.backup-evidencia.1"}

ENV_PRIVATE_DIR = "M15_IMPORT_PRIVATE_DIR"
DEFAULT_PRIVATE_DIR = "data-private/import-snapshots"

ALLOWED_ENCODINGS = {"utf-8", "utf-8-sig"}
ALLOWED_DELIMITERS = {",", ";"}

# Marcadores de credencial/segredo: presença em CHAVE ou VALOR rejeita o
# manifesto inteiro. Inclui identificadores de planilha (nunca no Git/log).
_FORBIDDEN_TOKENS = (
    "senha", "password", "passwd", "token", "secret", "api_key", "apikey",
    "credencial", "credential", "authorization", "private_key", "bearer",
    "client_secret", "refresh_token", "spreadsheet_id", "spreadsheetid",
    "service_account",
)
# URLs/esquemas: manifesto não pode induzir busca em rede (fail-closed).
_FORBIDDEN_VALUE_MARKERS = ("://", "docs.google.com", "sheets.google")

_REQUIRED_FIELDS = (
    "schema_version", "source_type", "workbook_alias", "sheet_alias",
    "snapshot_ts_utc", "arquivo", "sha256", "encoding", "delimiter",
    "headers", "row_count", "linha_inicial_dados",
)


class ManifestError(ValueError):
    """Erro de validação de manifesto/evidência (mensagens sem PII)."""


def import_private_dir() -> pathlib.Path:
    """Diretório privado aprovado. Só configurável por variável de ambiente
    (M15_IMPORT_PRIVATE_DIR) — nunca por parâmetro de request/manifesto."""
    return pathlib.Path(os.environ.get(ENV_PRIVATE_DIR, DEFAULT_PRIVATE_DIR))


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_name(name: str) -> str | None:
    """Nome simples de arquivo dentro do diretório aprovado; None se inválido."""
    if not name or not isinstance(name, str):
        return None
    if name != name.strip() or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    if pathlib.PurePath(name).name != name or os.path.isabs(name):
        return None
    return name


def resolve_inside(base: pathlib.Path, name: str) -> pathlib.Path | None:
    """Resolve name dentro de base; None se escapar (symlink incluso)."""
    if safe_name(name) is None:
        return None
    base_resolved = base.resolve()
    candidate = (base_resolved / name).resolve()
    if candidate.parent != base_resolved:
        return None
    return candidate


def _walk_forbidden(obj, path: str, erros: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if any(tok in key_l for tok in _FORBIDDEN_TOKENS):
                erros.append(f"conteudo_proibido_no_manifesto:chave:{path}{key}")
            _walk_forbidden(value, f"{path}{key}.", erros)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_forbidden(item, f"{path}{i}.", erros)
    elif isinstance(obj, str):
        low = obj.lower()
        if any(tok in low for tok in _FORBIDDEN_TOKENS) or any(
            mark in low for mark in _FORBIDDEN_VALUE_MARKERS
        ):
            erros.append(f"conteudo_proibido_no_manifesto:valor:{path.rstrip('.')}")


def _detect_delimiter(text: str) -> str:
    sample = text[:2048]
    return ";" if sample.count(";") > sample.count(",") else ","


def _read_headers_and_count(content: bytes, encoding: str, delimiter: str):
    """Cabeçalhos normalizados + contagem de linhas de dados não vazias.

    Mesma semântica do importador (_read_rows): linha 100% vazia não conta.
    """
    text = content.decode("utf-8-sig" if encoding == "utf-8-sig" else "utf-8",
                          errors="strict")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    headers_raw = next(reader, None)
    if headers_raw is None:
        raise ManifestError("arquivo_sem_cabecalho")
    headers = [normalize_header(h) for h in headers_raw]
    count = 0
    for row in reader:
        if any((cell or "").strip() for cell in row):
            count += 1
    return headers, count


@dataclass
class ManifestValidation:
    ok: bool = False
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    manifest: dict | None = None
    manifest_sha256: str | None = None
    file_sha256: str | None = None
    arquivo: str | None = None

    def as_dict(self) -> dict:
        m = self.manifest or {}
        return {
            "ok": self.ok,
            "erros": self.erros,
            "avisos": self.avisos,
            "source_type": m.get("source_type"),
            "workbook_alias": m.get("workbook_alias"),
            "sheet_alias": m.get("sheet_alias"),
            "schema_version": m.get("schema_version"),
            "mapping_version": m.get("mapping_version"),
            "mapping_version_atual": MAPPING_VERSION,
            "arquivo": self.arquivo,
            "sha256": self.file_sha256,
            "manifest_sha256": self.manifest_sha256,
            "row_count": m.get("row_count"),
        }


def load_and_validate_manifest(
    manifest_name: str, base_dir: pathlib.Path | None = None
) -> ManifestValidation:
    """Valida manifesto + arquivo de snapshot. Nunca escreve nada."""
    result = ManifestValidation()
    base = base_dir or import_private_dir()

    manifest_path = resolve_inside(base, manifest_name)
    if manifest_path is None:
        result.erros.append("caminho_de_manifesto_invalido")
        return result
    if not manifest_path.is_file():
        result.erros.append("manifesto_ausente")
        return result

    raw = manifest_path.read_bytes()
    result.manifest_sha256 = sha256_bytes(raw)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result.erros.append("manifesto_json_invalido")
        return result
    if not isinstance(manifest, dict):
        result.erros.append("manifesto_json_invalido")
        return result
    result.manifest = manifest

    _walk_forbidden(manifest, "", result.erros)
    if result.erros:
        return result  # credencial/URL: rejeição imediata, sem tocar arquivo

    faltando = [f for f in _REQUIRED_FIELDS if f not in manifest]
    if faltando:
        result.erros.extend(f"campo_obrigatorio_ausente:{f}" for f in faltando)
        return result

    if manifest["schema_version"] not in SCHEMA_VERSIONS:
        result.erros.append("schema_version_desconhecida")
        return result

    spec = get_mapping(str(manifest["source_type"]))
    if spec is None:
        result.erros.append("source_type_desconhecido")
        return result

    mv = manifest.get("mapping_version")
    if mv != MAPPING_VERSION:
        result.erros.append("mapping_version_nao_revisada")

    try:
        datetime.fromisoformat(str(manifest["snapshot_ts_utc"]))
    except ValueError:
        result.erros.append("snapshot_ts_invalido")

    if manifest["encoding"] not in ALLOWED_ENCODINGS:
        result.erros.append("encoding_nao_permitido")
    if manifest["delimiter"] not in ALLOWED_DELIMITERS:
        result.erros.append("delimitador_nao_permitido")

    headers_decl = manifest["headers"]
    if not isinstance(headers_decl, list) or not headers_decl or not all(
        isinstance(h, str) and h.strip() for h in headers_decl
    ):
        result.erros.append("headers_invalidos")
        return result

    row_count = manifest["row_count"]
    linha_inicial = manifest["linha_inicial_dados"]
    if not isinstance(row_count, int) or row_count < 0:
        result.erros.append("row_count_invalido")
    if not isinstance(linha_inicial, int) or linha_inicial < 2:
        result.erros.append("linha_inicial_dados_invalida")

    sha_decl = str(manifest["sha256"]).lower()
    if len(sha_decl) != 64 or any(c not in "0123456789abcdef" for c in sha_decl):
        result.erros.append("sha256_invalido")

    extras = manifest.get("colunas_extras_aprovadas", [])
    if not isinstance(extras, list) or not all(isinstance(e, str) for e in extras):
        result.erros.append("colunas_extras_invalidas")
        extras = []

    if result.erros:
        return result

    arquivo = str(manifest["arquivo"])
    file_path = resolve_inside(base, arquivo)
    if file_path is None:
        result.erros.append("arquivo_fora_do_diretorio_aprovado")
        return result
    result.arquivo = arquivo
    if not file_path.is_file():
        result.erros.append("arquivo_de_snapshot_ausente")
        return result
    if file_path.stat().st_size > MAX_UPLOAD_BYTES:
        result.erros.append("arquivo_excede_limite")
        return result

    content = file_path.read_bytes()
    result.file_sha256 = sha256_bytes(content)
    if result.file_sha256 != sha_decl:
        result.erros.append("checksum_divergente")
        return result

    try:
        headers_arquivo, count_arquivo = _read_headers_and_count(
            content, manifest["encoding"], manifest["delimiter"]
        )
    except UnicodeDecodeError:
        result.erros.append("encoding_do_arquivo_invalido")
        return result
    except ManifestError as exc:
        result.erros.append(str(exc))
        return result

    headers_norm_decl = [normalize_header(h) for h in headers_decl]
    if headers_arquivo != headers_norm_decl:
        result.erros.append("cabecalhos_divergentes_do_arquivo")
    if count_arquivo != row_count:
        result.erros.append("row_count_divergente")

    detected = _detect_delimiter(content.decode(
        "utf-8-sig" if manifest["encoding"] == "utf-8-sig" else "utf-8",
        errors="replace",
    ))
    if detected != manifest["delimiter"]:
        result.erros.append("delimitador_incompativel_com_arquivo")

    result.erros.extend(check_headers(spec, headers_norm_decl, extras))
    if extras:
        result.avisos.append(f"colunas_extras_aprovadas:{len(extras)}")
    if spec.execucao == mapping_registry.EXECUCAO_PREPARADA:
        result.avisos.append("execucao_ainda_nao_disponivel_para_este_source_type")
    if spec.execucao == mapping_registry.EXECUCAO_EXCLUIDA:
        result.avisos.append("fonte_historica_excluida_somente_arquivamento")

    result.ok = not result.erros
    return result


def load_snapshot_content(
    arquivo: str, expected_sha256: str, base_dir: pathlib.Path | None = None
) -> bytes:
    """Relê o snapshot verificando confinamento e checksum (imutabilidade)."""
    base = base_dir or import_private_dir()
    path = resolve_inside(base, arquivo)
    if path is None:
        raise ManifestError("arquivo_fora_do_diretorio_aprovado")
    if not path.is_file():
        raise ManifestError("arquivo_de_snapshot_ausente")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ManifestError("arquivo_excede_limite")
    content = path.read_bytes()
    if sha256_bytes(content) != expected_sha256:
        raise ManifestError("checksum_alterado_apos_registro")
    return content


def validate_backup_evidence(
    evidence_name: str, base_dir: pathlib.Path | None = None
) -> dict:
    """Valida evidência de backup/rollback no diretório privado aprovado.

    Formato esperado (JSON, sem credenciais):
    {
      "schema_version": "m15.backup-evidencia.1",
      "arquivo_backup": "backup-....sql.gz",
      "sha256": "<sha256 do arquivo de backup>",
      "criado_em_utc": "2026-07-19T00:00:00+00:00"
    }
    """
    erros: list[str] = []
    base = base_dir or import_private_dir()
    path = resolve_inside(base, evidence_name)
    if path is None:
        return {"ok": False, "erros": ["caminho_de_evidencia_invalido"]}
    if not path.is_file():
        return {"ok": False, "erros": ["evidencia_de_backup_ausente"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "erros": ["evidencia_json_invalida"]}
    if not isinstance(data, dict):
        return {"ok": False, "erros": ["evidencia_json_invalida"]}
    _walk_forbidden(data, "", erros)
    if erros:
        return {"ok": False, "erros": erros}
    if data.get("schema_version") not in EVIDENCE_SCHEMA_VERSIONS:
        erros.append("schema_version_de_evidencia_desconhecida")
    backup_name = data.get("arquivo_backup")
    backup_path = resolve_inside(base, backup_name) if isinstance(
        backup_name, str) else None
    if backup_path is None:
        erros.append("arquivo_backup_fora_do_diretorio_aprovado")
    elif not backup_path.is_file():
        erros.append("arquivo_backup_ausente")
    else:
        sha = str(data.get("sha256", "")).lower()
        if sha256_bytes(backup_path.read_bytes()) != sha:
            erros.append("checksum_do_backup_divergente")
    try:
        datetime.fromisoformat(str(data.get("criado_em_utc", "")))
    except ValueError:
        erros.append("criado_em_utc_invalido")
    return {
        "ok": not erros,
        "erros": erros,
        "evidencia": evidence_name,
        "arquivo_backup": backup_name if not erros else None,
        "local_rollback": f"{base}/{backup_name}" if not erros else None,
    }
