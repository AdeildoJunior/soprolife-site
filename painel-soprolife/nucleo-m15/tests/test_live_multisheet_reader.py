"""M15.7 live-reader: leitor multiaba ao vivo (mock da API Sheets, sem rede).

Cobre o contrato do script read-multisheet-snapshot-adc.py: mapeamento
fechado das 12 abas aprovadas, validação de cabeçalho fail-closed, exclusão
de abas fora do escopo (incluindo Resumo Dashboard), imutabilidade do
snapshot e recibo sanitizado. Nenhum dado real de planilha é usado —
somente fixtures sintéticas.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.migration.adapters import (
    ADAPTERS,
    ADAPTERS_VERSION,
    PAPEL_CONSENTIMENTO,
    PAPEL_DATA,
    PAPEL_DATA_HORA,
    PAPEL_EMAIL,
    PAPEL_ID,
    PAPEL_NOME_PESSOA,
    PAPEL_REF_MONETARIA_BLOQUEADA,
    PAPEL_REF_PESSOA,
    PAPEL_REF_TECNICA,
    PAPEL_TELEFONE,
    PAPEL_UNIDADE,
    PAPEL_VALOR_MONETARIO,
)

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts" / "read-multisheet-snapshot-adc.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "read_multisheet_snapshot_adc", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _autorizar_utilitario_legado(monkeypatch):
    """M23 — este módulo testa um utilitário LEGADO de Google Sheets.

    Desde o M23 o painel opera em modo postgresql_only e o leitor multiaba
    recusa executar (exit 3) sem decisão humana explícita. O teste declara
    essa autorização, exatamente como um humano faria numa migração ou
    perícia — e é justamente esse gesto explícito que a esteira automática
    nunca executa, porque nenhuma unit systemd define esta variável.
    """
    monkeypatch.setenv("SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION", "1")


@pytest.fixture()
def mod():
    return _load_module()


def test_leitor_legado_e_bloqueado_sem_autorizacao_humana(mod, monkeypatch):
    """Sem o escape explícito, o leitor multiaba não roda — nem em teste."""
    monkeypatch.delenv("SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION", raising=False)
    monkeypatch.setattr(sys, "argv", ["read-multisheet-snapshot-adc.py", "--write"])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 3


class _FixedDateTime:
    _fixed = datetime(2026, 7, 21, 16, 30, 9, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


class _Exec:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._payload


class _ValuesResource:
    def __init__(self, service):
        self._service = service

    def get(self, spreadsheetId=None, range=None, valueRenderOption=None):
        return self._service._values_get(range)


class _SpreadsheetsResource:
    def __init__(self, service):
        self._service = service

    def get(self, spreadsheetId=None, fields=None):
        return self._service._meta_get()

    def values(self):
        return _ValuesResource(self._service)


class FakeSheetsService:
    """Dublê da API Sheets: metadados + valores, nunca rede real."""

    def __init__(self, tabs_meta, headers, data, meta_exc=None, values_exc_titles=None):
        self.tabs_meta = tabs_meta          # title -> {"rowCount", "columnCount"}
        self.headers = headers              # title -> list[str]
        self.data = data                    # title -> list[list[str]]
        self.meta_exc = meta_exc
        self.values_exc_titles = values_exc_titles or set()
        self.values_calls: list[str] = []   # espião: toda range pedida

    def spreadsheets(self):
        return _SpreadsheetsResource(self)

    def _meta_get(self):
        if self.meta_exc is not None:
            return _Exec(exc=self.meta_exc)
        sheets = [
            {"properties": {"title": t, "gridProperties": m}}
            for t, m in self.tabs_meta.items()
        ]
        return _Exec({"sheets": sheets})

    def _values_get(self, range_str):
        self.values_calls.append(range_str)
        title = range_str.split("'", 2)[1]
        if title in self.values_exc_titles:
            return _Exec(exc=RuntimeError("simulated values error"))
        is_header = range_str.endswith("!1:1")
        if is_header:
            rows = [self.headers[title]] if title in self.headers else []
        else:
            rows = self.data.get(title, [])
        return _Exec({"values": rows} if rows else {})


def _headers_for(sheet_kind: str) -> list[str]:
    return [c.header for c in ADAPTERS[sheet_kind].campos]


def _synthetic_row(sheet_kind: str, i: int) -> list[str]:
    adapter = ADAPTERS[sheet_kind]
    valores = []
    for c in adapter.campos:
        if c.papel in (PAPEL_ID, PAPEL_REF_TECNICA, PAPEL_REF_PESSOA):
            valores.append(f"SINT-{sheet_kind[:4].upper()}-{i:04d}")
        elif c.papel == PAPEL_DATA:
            valores.append("2026-01-15")
        elif c.papel == PAPEL_DATA_HORA:
            valores.append("2026-01-15 10:00:00")
        elif c.papel == PAPEL_TELEFONE:
            valores.append("(21) 0000-9001")  # prefixo sintético não discável
        elif c.papel == PAPEL_EMAIL:
            valores.append("sintetico@example.invalid")
        elif c.papel == PAPEL_NOME_PESSOA:
            valores.append("Pessoa Sintetica")
        elif c.papel in (PAPEL_VALOR_MONETARIO, PAPEL_REF_MONETARIA_BLOQUEADA):
            valores.append("100,00")
        elif c.papel == PAPEL_CONSENTIMENTO:
            valores.append("sim")
        elif c.papel == PAPEL_UNIDADE:
            valores.append("unidade-sintetica")
        else:
            valores.append("valor-sintetico")
    return valores


def _happy_path_service(mod):
    tabs_meta = {
        title: {"rowCount": 100, "columnCount": len(_headers_for(kind))}
        for title, kind in mod.APPROVED_TABS.items()
    }
    headers = {title: _headers_for(kind) for title, kind in mod.APPROVED_TABS.items()}
    data = {
        title: [_synthetic_row(kind, 1), _synthetic_row(kind, 2)]
        for title, kind in mod.APPROVED_TABS.items()
    }
    return FakeSheetsService(tabs_meta, headers, data)


def _run(mod, monkeypatch, tmp_path, service, argv):
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_authenticate", lambda build, auth: service)
    monkeypatch.setattr(mod, "_load_config", lambda: "TEST-SPREADSHEET-ID-NOT-REAL")
    monkeypatch.setattr(mod, "datetime", _FixedDateTime)
    monkeypatch.setattr(sys, "argv", ["read-multisheet-snapshot-adc.py", *argv])
    return mod.main()


# ---------------------------------------------------------------------------
# 1. Fechamento do escopo aprovado
# ---------------------------------------------------------------------------

def test_approved_tabs_match_known_adapters_exactly(mod):
    assert len(mod.APPROVED_TABS) == 12
    for sheet_kind in mod.APPROVED_TABS.values():
        assert sheet_kind in ADAPTERS
    assert "Resumo Dashboard" not in mod.APPROVED_TABS
    assert "Resumo" not in mod.APPROVED_TABS


def test_col_letter_conversion(mod):
    assert mod._col_letter(1) == "A"
    assert mod._col_letter(26) == "Z"
    assert mod._col_letter(27) == "AA"
    assert mod._col_letter(28) == "AB"


# ---------------------------------------------------------------------------
# 2. Caminho feliz: dry-run e write
# ---------------------------------------------------------------------------

def test_dry_run_validates_all_12_and_writes_nothing(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    rc = _run(mod, monkeypatch, tmp_path, service, ["--dry-run"])
    assert rc == 0
    assert list(tmp_path.iterdir()) == []


def test_write_creates_immutable_snapshot_with_sanitized_receipt(
    mod, monkeypatch, tmp_path, capsys
):
    service = _happy_path_service(mod)
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 0

    files = sorted(tmp_path.iterdir())
    # 12 arquivos de aba + 1 envelope
    assert len(files) == 13
    envelope_files = [f for f in files if "envelope" in f.name]
    assert len(envelope_files) == 1

    for f in files:
        assert oct(f.stat().st_mode)[-3:] == "600"

    envelope = json.loads(envelope_files[0].read_text())
    assert envelope["mapping_version"] == ADAPTERS_VERSION
    assert envelope["mapping_version"] != "m15-6b.1"  # incompatibilidade resolvida
    assert len(envelope["sheets"]) == 12

    out = capsys.readouterr().out
    assert "TEST-SPREADSHEET-ID-NOT-REAL" not in out
    assert "SINT-" not in out  # nenhum valor de linha impresso
    assert "envelope_sha256" in out


def test_write_is_immutable_never_overwrites(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    rc1 = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc1 == 0
    files_before = {f: f.read_bytes() for f in tmp_path.iterdir()}

    service2 = _happy_path_service(mod)
    rc2 = _run(mod, monkeypatch, tmp_path, service2, ["--write"])
    assert rc2 == 1  # mesmo snapshot_id (datetime fixo) -> já existe

    for f, content in files_before.items():
        assert f.read_bytes() == content  # nada foi alterado


def test_show_structure_never_reads_any_cell_value(mod, monkeypatch, tmp_path, capsys):
    service = _happy_path_service(mod)
    rc = _run(mod, monkeypatch, tmp_path, service, ["--show-structure"])
    assert rc == 0
    assert service.values_calls == []  # nenhuma chamada de valores, só metadados
    out = capsys.readouterr().out
    assert "SINT-" not in out


# ---------------------------------------------------------------------------
# 3. Abas fora do escopo nunca são lidas
# ---------------------------------------------------------------------------

def test_unmapped_and_dashboard_tabs_never_fetch_values(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.tabs_meta["Resumo Dashboard"] = {"rowCount": 50, "columnCount": 3}
    service.tabs_meta["Log Auditoria"] = {"rowCount": 50, "columnCount": 5}
    service.tabs_meta["_Backup_Leads_Demo_20260623_0000"] = {"rowCount": 10, "columnCount": 5}

    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 0
    fetched_titles = {c.split("'", 2)[1] for c in service.values_calls}
    assert "Resumo Dashboard" not in fetched_titles
    assert "Log Auditoria" not in fetched_titles
    assert "_Backup_Leads_Demo_20260623_0000" not in fetched_titles


# ---------------------------------------------------------------------------
# 4. Fail-closed
# ---------------------------------------------------------------------------

def test_write_fails_closed_when_approved_tab_missing(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    del service.tabs_meta["Leads"]
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_unknown_header(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.headers["Leads"] = service.headers["Leads"] + ["coluna_nao_mapeada"]
    for row in service.data["Leads"]:
        row.append("valor-extra")
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []  # nada gravado mesmo com 11 abas válidas


def test_write_fails_closed_on_missing_required_header(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    headers = service.headers["CRM Pacientes"]
    required = next(
        c.header for c in ADAPTERS["crm_pacientes"].campos if c.obrigatorio
    )
    service.headers["CRM Pacientes"] = [h for h in headers if h != required]
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_duplicate_header(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    headers = service.headers["CRM Clinicas"]
    service.headers["CRM Clinicas"] = headers + [headers[0]]
    for row in service.data["CRM Clinicas"]:
        row.append(row[0])
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_empty_required_sheet(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.data["Financeiro_Lancamentos"] = []
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_non_text_cell(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.data["CRM Espirometria"][0][0] = 12345  # não-textual (defesa em profundidade)
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_api_error(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.values_exc_titles = {"CRM Consultas"}
    rc = _run(mod, monkeypatch, tmp_path, service, ["--write"])
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_write_fails_closed_on_metadata_error(mod, monkeypatch, tmp_path):
    service = _happy_path_service(mod)
    service.meta_exc = RuntimeError("simulated 403")
    monkeypatch.setenv("M15_IMPORT_PRIVATE_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_authenticate", lambda build, auth: service)
    monkeypatch.setattr(mod, "_load_config", lambda: "TEST-SPREADSHEET-ID-NOT-REAL")
    monkeypatch.setattr(sys, "argv", ["read-multisheet-snapshot-adc.py", "--write"])
    with pytest.raises(SystemExit):
        mod.main()
    assert list(tmp_path.iterdir()) == []
