"""Leitor versionado do envelope de snapshot bruto multiaba (M15.6B).

Contrato do envelope privado imutável (produzido pelo processo governado de
snapshot das planilhas, SEMPRE fora do Git):

- o envelope JSON e os arquivos de aba vivem SOMENTE no diretório privado
  aprovado (M15_IMPORT_PRIVATE_DIR); nomes simples, sem separador de caminho;
- somente arquivos referenciados pelo envelope validado são lidos;
- checksum SHA-256 conferido ANTES de qualquer parse do arquivo de aba;
- nenhum ID de workbook, credencial, URL ou hostname pode aparecer — os
  marcadores proibidos do manifesto M15.6A valem aqui também;
- valores são SEMPRE texto formatado: nenhuma fórmula é executada nem
  reproduzida (célula não textual = rejeição fechada);
- nenhuma busca em rede: leitura 100% local e determinística;
- ordem de linhas determinística e preservada: número original da linha
  estritamente crescente, senão rejeição;
- schema_version explícita do formato bruto; versão desconhecida = rejeição.
"""

import json
import os
import pathlib
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime

from ..importer.csv_import import MAX_COLS, MAX_FIELD_CHARS, MAX_ROWS, MAX_UPLOAD_BYTES
from .manifest import (
    ManifestError,
    _walk_forbidden,
    import_private_dir,
    resolve_inside,
    safe_name,
    sha256_bytes,
)

RAW_ENVELOPE_SCHEMA_VERSIONS = {"m15.raw-envelope.1"}
RAW_SHEET_SCHEMA_VERSIONS = {"m15.raw-sheet.1"}

_ENVELOPE_REQUIRED = (
    "schema_version", "workbook_alias", "snapshot_ts_utc", "locale",
    "timezone", "mapping_version", "sheets",
)
_SHEET_REF_REQUIRED = ("sheet_alias", "sheet_kind", "arquivo", "sha256")
_SHEET_FILE_REQUIRED = (
    "schema_version", "sheet_alias", "headers", "linha_inicial_dados", "rows",
)
_ROW_KEYS = {"linha", "valores"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


@dataclass(frozen=True)
class RawRow:
    """Linha bruta preservada: número ORIGINAL da linha + valores textuais."""

    linha: int
    valores: tuple[str, ...]


@dataclass(frozen=True)
class RawSheet:
    sheet_alias: str
    sheet_kind: str
    arquivo: str
    sha256: str
    headers: tuple[str, ...]      # cabeçalhos ORIGINAIS, preservados verbatim
    linha_inicial_dados: int
    rows: tuple[RawRow, ...]


@dataclass
class RawEnvelope:
    workbook_alias: str
    snapshot_ts_utc: str
    locale: str
    timezone: str
    mapping_version: str
    schema_version: str
    envelope_sha256: str
    sheets: list[RawSheet] = field(default_factory=list)


@dataclass(frozen=True)
class RawSnapshotWriteResult:
    """Recibo sanitizado; nunca inclui células nem identificador de origem."""

    envelope_name: str
    envelope_sha256: str
    snapshot_ts_utc: str
    sheet_count: int


def _fail(codigo: str) -> ManifestError:
    return ManifestError(codigo)


def _canonical_json(document: dict) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_exclusive_private(path: pathlib.Path, content: bytes) -> None:
    """Cria arquivo regular 0600 uma única vez, sem seguir links."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise _fail("arquivo_excede_limite")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise _fail("snapshot_imutavel_ja_existe") from exc
    try:
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise _fail("falha_ao_gravar_snapshot")
            offset += written
        os.fsync(fd)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
        ):
            raise _fail("arquivo_privado_inseguro")
    except Exception:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)


def write_immutable_raw_snapshot(
    *,
    snapshot_id: str,
    workbook_alias: str,
    snapshot_ts_utc: str,
    locale: str,
    timezone: str,
    mapping_version: str,
    sheets: list[dict],
    known_kinds: frozenset | None = None,
) -> RawSnapshotWriteResult:
    """Gera snapshot multiaba privado e imutável a partir de valores textuais.

    O chamador de integração lê a fonte oficial e entrega somente aliases e
    células formatadas. IDs da planilha, URLs e credenciais não fazem parte do
    contrato. A publicação local é exclusiva e validada integralmente antes
    de devolver o recibo; nunca há escrita operacional no banco.
    """
    if (
        not isinstance(snapshot_id, str)
        or not _SNAPSHOT_ID_RE.fullmatch(snapshot_id)
        or safe_name(snapshot_id) is None
    ):
        raise _fail("snapshot_id_invalido")
    if not isinstance(sheets, list) or not sheets:
        raise _fail("envelope_sem_abas")
    if len(sheets) > MAX_COLS:
        raise _fail("envelope_excede_limite_de_abas")

    base = import_private_dir()
    base.mkdir(parents=True, exist_ok=True)
    if base.is_symlink() or not base.is_dir():
        raise _fail("diretorio_privado_inseguro")
    base = base.resolve(strict=True)

    refs: list[dict] = []
    payloads: list[tuple[str, bytes]] = []
    seen_aliases: set[str] = set()
    for index, spec in enumerate(sheets, start=1):
        if not isinstance(spec, dict) or set(spec) != {
            "sheet_alias", "sheet_kind", "headers", "linha_inicial_dados", "rows"
        }:
            raise _fail("estrutura_de_aba_desconhecida")
        alias = spec["sheet_alias"]
        kind = spec["sheet_kind"]
        if (
            not isinstance(alias, str)
            or not alias.strip()
            or alias in seen_aliases
            or not isinstance(kind, str)
            or not kind.strip()
            or (known_kinds is not None and kind not in known_kinds)
        ):
            raise _fail("referencia_de_aba_invalida")
        seen_aliases.add(alias)
        filename = f"m15-raw-{snapshot_id}-sheet-{index:02d}.json"
        sheet_document = {
            "schema_version": "m15.raw-sheet.1",
            "sheet_alias": alias,
            "headers": spec["headers"],
            "linha_inicial_dados": spec["linha_inicial_dados"],
            "rows": spec["rows"],
        }
        errors: list[str] = []
        _walk_forbidden(sheet_document, "", errors)
        if errors:
            raise _fail("conteudo_proibido_na_aba")
        content = _canonical_json(sheet_document)
        ref = {
            "sheet_alias": alias,
            "sheet_kind": kind,
            "arquivo": filename,
            "sha256": sha256_bytes(content),
        }
        _validate_sheet_file(sheet_document, ref)
        refs.append(ref)
        payloads.append((filename, content))

    envelope_name = f"m15-raw-{snapshot_id}-envelope.json"
    envelope = {
        "schema_version": "m15.raw-envelope.1",
        "workbook_alias": workbook_alias,
        "snapshot_ts_utc": snapshot_ts_utc,
        "locale": locale,
        "timezone": timezone,
        "mapping_version": mapping_version,
        "sheets": refs,
    }
    errors = []
    _walk_forbidden(envelope, "", errors)
    if errors:
        raise _fail("conteudo_proibido_no_envelope")
    envelope_content = _canonical_json(envelope)

    created: list[pathlib.Path] = []
    try:
        for filename, content in payloads:
            target = base / filename
            _write_exclusive_private(target, content)
            created.append(target)
        envelope_path = base / envelope_name
        _write_exclusive_private(envelope_path, envelope_content)
        created.append(envelope_path)
        loaded = load_raw_envelope(envelope_name, base, known_kinds)
    except Exception:
        # Remove apenas arquivos que esta tentativa acabou de criar; um
        # snapshot pré-existente jamais é tocado ou substituído.
        for created_path in reversed(created):
            created_path.unlink(missing_ok=True)
        raise
    return RawSnapshotWriteResult(
        envelope_name=envelope_name,
        envelope_sha256=loaded.envelope_sha256,
        snapshot_ts_utc=loaded.snapshot_ts_utc,
        sheet_count=len(loaded.sheets),
    )


def _load_json_confined(base: pathlib.Path, name: str, limite: int) -> tuple[dict, bytes]:
    """Lê JSON confinado ao diretório aprovado. Nunca segue path traversal."""
    path = resolve_inside(base, name)
    if path is None:
        raise _fail("caminho_fora_do_diretorio_aprovado")
    unresolved = base.resolve() / name
    if unresolved.is_symlink():
        raise _fail("link_simbolico_nao_permitido")
    if not path.is_file():
        raise _fail("arquivo_ausente")
    if path.stat().st_size > limite:
        raise _fail("arquivo_excede_limite")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _fail("json_invalido") from None
    if not isinstance(data, dict):
        raise _fail("json_invalido")
    return data, raw


def _validate_sheet_file(data: dict, ref: dict) -> RawSheet:
    if set(data) != set(_SHEET_FILE_REQUIRED):
        raise _fail("estrutura_de_aba_desconhecida")
    faltando = [f for f in _SHEET_FILE_REQUIRED if f not in data]
    if faltando:
        raise _fail("campo_obrigatorio_ausente_na_aba:" + ",".join(faltando))
    if (not isinstance(data["schema_version"], str)
            or data["schema_version"] not in RAW_SHEET_SCHEMA_VERSIONS):
        raise _fail("schema_version_de_aba_desconhecida")
    if data["sheet_alias"] != ref["sheet_alias"]:
        raise _fail("sheet_alias_divergente_do_envelope")

    headers = data["headers"]
    if (not isinstance(headers, list) or not headers
            or not all(isinstance(h, str) for h in headers)):
        raise _fail("cabecalhos_invalidos")
    if len(headers) > MAX_COLS:
        raise _fail("mais_colunas_que_o_limite")
    if any(len(h) > MAX_FIELD_CHARS for h in headers):
        raise _fail("cabecalho_excede_limite")

    linha_inicial = data["linha_inicial_dados"]
    if type(linha_inicial) is not int or linha_inicial < 2:
        raise _fail("linha_inicial_dados_invalida")

    rows_decl = data["rows"]
    if not isinstance(rows_decl, list):
        raise _fail("linhas_invalidas")
    if len(rows_decl) > MAX_ROWS:
        raise _fail("mais_linhas_que_o_limite")

    rows: list[RawRow] = []
    anterior = linha_inicial - 1
    for item in rows_decl:
        if not isinstance(item, dict) or set(item.keys()) != _ROW_KEYS:
            # fail-closed: linha só carrega número original + valores textuais
            raise _fail("estrutura_de_linha_desconhecida")
        linha = item["linha"]
        if type(linha) is not int or linha <= anterior:
            # ordem determinística obrigatória (estritamente crescente)
            raise _fail("ordem_de_linhas_nao_deterministica")
        anterior = linha
        valores = item["valores"]
        if not isinstance(valores, list) or len(valores) != len(headers):
            raise _fail("largura_de_linha_divergente_do_cabecalho")
        for valor in valores:
            # nenhuma fórmula/objeto é aceito nem executado: só texto formatado
            if not isinstance(valor, str):
                raise _fail("celula_nao_textual")
            if len(valor) > MAX_FIELD_CHARS:
                raise _fail("campo_excede_limite")
        rows.append(RawRow(linha=linha, valores=tuple(valores)))

    return RawSheet(
        sheet_alias=str(ref["sheet_alias"]),
        sheet_kind=str(ref["sheet_kind"]),
        arquivo=str(ref["arquivo"]),
        sha256=str(ref["sha256"]).lower(),
        headers=tuple(headers),
        linha_inicial_dados=linha_inicial,
        rows=tuple(rows),
    )


def load_raw_envelope(
    envelope_name: str,
    base_dir: pathlib.Path | None = None,
    known_kinds: frozenset | None = None,
) -> RawEnvelope:
    """Valida e carrega o envelope multiaba completo. Nunca escreve nada.

    known_kinds: conjunto de sheet_kind aceitos (registro de adapters);
    kind fora do registro é rejeição fechada — nada é adivinhado.
    """
    base = base_dir or import_private_dir()
    envelope, raw = _load_json_confined(base, envelope_name, MAX_UPLOAD_BYTES)

    erros: list[str] = []
    _walk_forbidden(envelope, "", erros)
    if erros:
        # credencial/ID de planilha/URL: rejeição imediata, sem tocar abas
        raise _fail("conteudo_proibido_no_envelope")

    faltando = [f for f in _ENVELOPE_REQUIRED if f not in envelope]
    if faltando:
        raise _fail("campo_obrigatorio_ausente:" + ",".join(faltando))
    if (not isinstance(envelope["schema_version"], str)
            or envelope["schema_version"] not in RAW_ENVELOPE_SCHEMA_VERSIONS):
        raise _fail("schema_version_de_envelope_desconhecida")
    if set(envelope) != set(_ENVELOPE_REQUIRED):
        raise _fail("estrutura_de_envelope_desconhecida")
    for campo in (
        "workbook_alias", "snapshot_ts_utc", "locale", "timezone",
        "mapping_version",
    ):
        if not isinstance(envelope[campo], str) or not envelope[campo].strip():
            raise _fail(f"campo_de_envelope_invalido:{campo}")
    try:
        instante = datetime.fromisoformat(
            envelope["snapshot_ts_utc"].replace("Z", "+00:00")
        )
    except ValueError:
        raise _fail("snapshot_ts_utc_invalido") from None
    if instante.tzinfo is None:
        raise _fail("snapshot_ts_utc_sem_timezone")

    refs = envelope["sheets"]
    if not isinstance(refs, list) or not refs:
        raise _fail("envelope_sem_abas")
    if len(refs) > MAX_COLS:
        raise _fail("envelope_excede_limite_de_abas")

    vistos_alias: set[str] = set()
    vistos_arquivo: set[str] = set()
    sheets: list[RawSheet] = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise _fail("referencia_de_aba_invalida")
        if any(f not in ref for f in _SHEET_REF_REQUIRED):
            raise _fail("referencia_de_aba_incompleta")
        if set(ref) != set(_SHEET_REF_REQUIRED):
            raise _fail("estrutura_de_referencia_desconhecida")
        if not all(isinstance(ref[f], str) and ref[f].strip()
                   for f in _SHEET_REF_REQUIRED):
            raise _fail("referencia_de_aba_invalida")
        if not _SHA256_RE.fullmatch(ref["sha256"].lower()):
            raise _fail("sha256_de_aba_invalido")
        alias = ref["sheet_alias"]
        arquivo = ref["arquivo"]
        if alias in vistos_alias:
            raise _fail("sheet_alias_duplicado_no_envelope")
        if arquivo in vistos_arquivo:
            raise _fail("arquivo_duplicado_no_envelope")
        vistos_alias.add(alias)
        vistos_arquivo.add(arquivo)
        if known_kinds is not None and str(ref["sheet_kind"]) not in known_kinds:
            raise _fail(f"sheet_kind_desconhecido:{ref['sheet_kind']}")

        path = resolve_inside(base, arquivo)
        if path is None:
            raise _fail("arquivo_fora_do_diretorio_aprovado")
        if (base.resolve() / arquivo).is_symlink():
            raise _fail("link_simbolico_nao_permitido")
        if not path.is_file():
            raise _fail("arquivo_de_aba_ausente")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise _fail("arquivo_excede_limite")
        conteudo = path.read_bytes()
        # checksum SEMPRE antes do parse — envelope divergente = rejeição
        if sha256_bytes(conteudo) != str(ref["sha256"]).lower():
            raise _fail("checksum_divergente_da_aba")
        try:
            data = json.loads(conteudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _fail("json_de_aba_invalido") from None
        if not isinstance(data, dict):
            raise _fail("json_de_aba_invalido")
        sheets.append(_validate_sheet_file(data, ref))

    return RawEnvelope(
        workbook_alias=str(envelope["workbook_alias"]),
        snapshot_ts_utc=str(envelope["snapshot_ts_utc"]),
        locale=str(envelope["locale"]),
        timezone=str(envelope["timezone"]),
        mapping_version=str(envelope["mapping_version"]),
        schema_version=str(envelope["schema_version"]),
        envelope_sha256=sha256_bytes(raw),
        sheets=sheets,
    )
