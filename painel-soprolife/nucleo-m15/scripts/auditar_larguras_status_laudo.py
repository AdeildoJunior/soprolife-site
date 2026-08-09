#!/usr/bin/env python3
"""M25.14 — as colunas de status do domínio de laudos comportam seus valores?

O incidente da M25.13: `report_documents.signature_status` era `VARCHAR(20)`
enquanto a CHECK constraint da própria tabela **exigia** gravar ali
`'liberada_institucional'`, com 22 caracteres. Em SQLite passa (o limite é
ignorado); em PostgreSQL a liberação sempre falhava com 500.

Este script cruza, para as tabelas do domínio de laudos, os literais que
aparecem nas CHECK constraints com o limite declarado de cada coluna. É
verificação DIRECIONADA — não é auditoria geral do schema.

Uso:
    .venv/bin/python scripts/auditar_larguras_status_laudo.py
Sai com código 1 se algum valor exigido não couber na sua coluna.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import CheckConstraint, String  # noqa: E402

from app.models import Base  # noqa: E402

# Domínio desta verificação: laudos, assinatura e o perfil médico que os
# alimenta. Nada além disso.
PREFIXOS = ("report_", "qualified_signature_", "physician_")

LITERAL = re.compile(r"'([^']*)'")
# Nome de coluna que aparece perto do literal, do lado esquerdo de um
# operador de comparação ou dentro de IN/ANY.
COLUNA = re.compile(r"\b([a-z_][a-z0-9_]*)\b\s*(?:::text)?\s*(?:=|<>|IN|!=)", re.I)


def tabelas_do_dominio():
    for tabela in Base.metadata.sorted_tables:
        if tabela.name.startswith(PREFIXOS):
            yield tabela


def limites_de_texto(tabela) -> dict[str, int]:
    limites = {}
    for coluna in tabela.columns:
        tipo = coluna.type
        if isinstance(tipo, String) and tipo.length:
            limites[coluna.name] = tipo.length
    return limites


def main() -> int:
    problemas: list[str] = []
    verificados = 0

    for tabela in tabelas_do_dominio():
        limites = limites_de_texto(tabela)
        if not limites:
            continue
        for constraint in tabela.constraints:
            if not isinstance(constraint, CheckConstraint):
                continue
            texto = str(constraint.sqltext)
            # Para cada literal da constraint, descobre a qual coluna ele se
            # refere: a última coluna citada antes do literal.
            for achado in LITERAL.finditer(texto):
                valor = achado.group(1)
                if not valor:
                    continue
                antes = texto[: achado.start()]
                citadas = COLUNA.findall(antes)
                alvo = None
                for nome in reversed(citadas):
                    if nome in limites:
                        alvo = nome
                        break
                if alvo is None:
                    continue
                verificados += 1
                limite = limites[alvo]
                if len(valor) > limite:
                    problemas.append(
                        f"{tabela.name}.{alvo}: VARCHAR({limite}) não comporta "
                        f"{valor!r} ({len(valor)} caracteres) — exigido por "
                        f"{constraint.name or 'check sem nome'}"
                    )

    print(f"pares (coluna, valor exigido) verificados: {verificados}")
    if problemas:
        print("\nINCOMPATIBILIDADES ENCONTRADAS:")
        for p in sorted(set(problemas)):
            print(f"  - {p}")
        return 1
    print("nenhuma incompatibilidade: todo valor exigido cabe na sua coluna.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
