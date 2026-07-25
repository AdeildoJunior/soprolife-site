#!/usr/bin/env python3
"""
SoproLife M23 — guarda do modo canônico de fonte de dados.

Decisão arquitetural permanente: PostgreSQL/API do Núcleo M15 é a ÚNICA fonte
operacional. Google Sheets não é mais fonte de runtime, destino de escrita,
alvo de sincronização nem fallback para nenhum dado de negócio.

Este módulo é o ponto único que:
  - lê o contrato core/contracts/data-source-mode.json;
  - responde se o ambiente está em modo postgresql_only;
  - BLOQUEIA, fail-closed, qualquer utilitário legado de Google Sheets que
    seja invocado sem autorização humana explícita.

Search Console e GA4 continuam permitidos: são leitura de marketing por conta
de serviço dedicada, não são dado de negócio e estão declarados no contrato.

Uso em um leitor legado (primeira linha executável do main):

    import sys as _sys, pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    import data_source_mode
    data_source_mode.block_legacy_sheets("read-leads-sheets.py")

O bloqueio só é liberado com a variável de ambiente explícita
SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1, que NENHUMA unit systemd define.
Ela existe para execução humana pontual (migração/forense), nunca produção.

Uso como CLI:
    python3 painel-soprolife/scripts/data_source_mode.py --status
    python3 painel-soprolife/scripts/data_source_mode.py --check
    python3 painel-soprolife/scripts/data_source_mode.py --self-test
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MODE_POSTGRES_ONLY = "postgresql_only"
MODE_LEGACY_MIGRATION = "legacy_sheets_migration"

ESCAPE_ENV = "SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION"
ESCAPE_VALUE = "1"

# Código de saída dedicado: distingue "bloqueado pela arquitetura" de um erro
# comum do script (1) ou de uso incorreto (2).
EXIT_BLOCKED = 3

_CONTRACT_RELATIVE = Path("painel-soprolife") / "core" / "contracts" / "data-source-mode.json"

# Fallback embutido: se o contrato sumir do disco, o guarda continua FECHADO.
# Nunca abrir por ausência de arquivo — essa é a falha que o M23 elimina.
_FALLBACK = {
    "modo": MODE_POSTGRES_ONLY,
    "integracoes_permitidas": [{"id": "search_console"}, {"id": "ga4"}],
}


def contract_path() -> Path:
    """Caminho do contrato, resolvido a partir da raiz do repositório."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / _CONTRACT_RELATIVE


def load_contract() -> dict:
    path = contract_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_FALLBACK)
    if not isinstance(data, dict) or not data.get("modo"):
        return dict(_FALLBACK)
    return data


def current_mode() -> str:
    """Modo efetivo. O escape humano é a ÚNICA forma de sair de postgresql_only."""
    declared = load_contract().get("modo", MODE_POSTGRES_ONLY)
    if declared != MODE_POSTGRES_ONLY:
        # Contrato adulterado ou valor desconhecido: trata como fechado.
        declared = MODE_POSTGRES_ONLY
    if legacy_migration_authorized():
        return MODE_LEGACY_MIGRATION
    return declared


def legacy_migration_authorized() -> bool:
    return os.environ.get(ESCAPE_ENV, "").strip() == ESCAPE_VALUE


def is_postgres_only() -> bool:
    return current_mode() == MODE_POSTGRES_ONLY


def allowed_integrations() -> list[str]:
    contract = load_contract()
    out = []
    for item in contract.get("integracoes_permitidas", []):
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
    return out


def marketing_integrations_enabled() -> bool:
    """Search Console e GA4 seguem habilitados — o M23 não os descomissiona."""
    allowed = set(allowed_integrations())
    return {"search_console", "ga4"}.issubset(allowed)


def _blocked_message(tool: str) -> str:
    return (
        f"BLOQUEADO (M23): '{tool}' é um utilitário legado de Google Sheets e o\n"
        f"painel opera em modo {MODE_POSTGRES_ONLY}.\n"
        "\n"
        "PostgreSQL/API do Núcleo M15 é a única fonte operacional. Este script\n"
        "não pode rodar no pipeline automático nem no timer de produção.\n"
        "\n"
        "Para uso humano pontual de migração/forense, e somente com decisão\n"
        f"explícita, exporte {ESCAPE_ENV}={ESCAPE_VALUE} antes de executar.\n"
        "\n"
        "Contrato: painel-soprolife/core/contracts/data-source-mode.json"
    )


def block_legacy_sheets(tool: str, *, stream=None) -> None:
    """Encerra o processo se um utilitário legado rodar em modo postgresql_only.

    Chamar como primeira instrução executável do main() do utilitário legado.
    """
    if not is_postgres_only():
        return
    print(_blocked_message(tool), file=stream or sys.stderr)
    raise SystemExit(EXIT_BLOCKED)


def assert_no_sheets_runtime(tool: str) -> None:
    """Variante para bibliotecas: levanta RuntimeError em vez de sair."""
    if is_postgres_only():
        raise RuntimeError(_blocked_message(tool))


def status_payload() -> dict:
    contract = load_contract()
    return {
        "modo": current_mode(),
        "modo_declarado": contract.get("modo", MODE_POSTGRES_ONLY),
        "marco": contract.get("marco", "M23"),
        "escape_humano_ativo": legacy_migration_authorized(),
        "integracoes_permitidas": allowed_integrations(),
        "marketing_habilitado": marketing_integrations_enabled(),
        "contrato": str(_CONTRACT_RELATIVE),
    }


# ---------------------------------------------------------------------------
# Self-test — não depende de rede, credencial nem banco.
# ---------------------------------------------------------------------------

def _self_test() -> int:
    falhas = 0
    original = os.environ.get(ESCAPE_ENV)

    def check(nome, cond):
        nonlocal falhas
        if cond:
            print(f"  OK   {nome}")
        else:
            print(f"  FALHA {nome}")
            falhas += 1

    try:
        os.environ.pop(ESCAPE_ENV, None)
        check("modo padrão é postgresql_only", current_mode() == MODE_POSTGRES_ONLY)
        check("is_postgres_only() verdadeiro sem escape", is_postgres_only() is True)
        check("Search Console e GA4 continuam permitidos",
              marketing_integrations_enabled() is True)

        bloqueou = False
        try:
            block_legacy_sheets("teste.py", stream=open(os.devnull, "w"))
        except SystemExit as exc:
            bloqueou = exc.code == EXIT_BLOCKED
        check("block_legacy_sheets bloqueia sem escape", bloqueou)

        os.environ[ESCAPE_ENV] = "0"
        check("escape com valor inválido não libera", is_postgres_only() is True)

        os.environ[ESCAPE_ENV] = ESCAPE_VALUE
        check("escape explícito libera o modo migração",
              current_mode() == MODE_LEGACY_MIGRATION)
        liberou = True
        try:
            block_legacy_sheets("teste.py", stream=open(os.devnull, "w"))
        except SystemExit:
            liberou = False
        check("block_legacy_sheets não bloqueia com escape", liberou)

        os.environ.pop(ESCAPE_ENV, None)
        check("contrato existe no disco", contract_path().exists())
        contrato = load_contract()
        check("contrato declara postgresql_only",
              contrato.get("modo") == MODE_POSTGRES_ONLY)
        check("contrato proíbe leitura de Sheets em runtime",
              contrato.get("proibido_em_runtime", {}).get("google_sheets_leitura") is True)
        check("contrato proíbe fallback de exemplo",
              contrato.get("proibido_em_runtime", {})
              .get("fallback_de_exemplo_para_dado_operacional") is True)
    finally:
        if original is None:
            os.environ.pop(ESCAPE_ENV, None)
        else:
            os.environ[ESCAPE_ENV] = original

    print()
    if falhas:
        print(f"SELF-TEST FALHOU: {falhas} verificação(ões).")
        return 1
    print("SELF-TEST OK.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "--status":
        print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
        return 0
    if args[0] == "--check":
        if is_postgres_only():
            print(f"OK: modo {MODE_POSTGRES_ONLY} ativo; nenhum leitor de planilha pode rodar.")
            return 0
        print(f"ATENÇÃO: escape humano ativo ({ESCAPE_ENV}={ESCAPE_VALUE}). "
              "Utilitários legados de Sheets estão liberados nesta sessão.")
        return 10
    if args[0] == "--self-test":
        return _self_test()
    print(f"Uso: {sys.argv[0]} [--status|--check|--self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
