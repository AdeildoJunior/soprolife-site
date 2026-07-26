"""m23.1 — unicidade da receita própria de exame e consulta

Adiciona somente dois índices parciais únicos. Antes de qualquer DDL, lê os
lançamentos vinculados e aborta se encontrar receitas próprias equivalentes
em conflito. Nenhuma linha financeira é alterada, mesclada ou excluída.

Revision ID: c9d5f7a31b42
Revises: b8c4e6d21a90
Create Date: 2026-07-26 12:30:00.000000
"""

import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "c9d5f7a31b42"
down_revision = "b8c4e6d21a90"
branch_labels = None
depends_on = None

INDEX_EXAME = "uq_financial_entries_receita_espirometria"
INDEX_CONSULTA = "uq_financial_entries_receita_consulta"

_SQLITE_CATEGORY_TRIM = (
    "trim(categoria, ' ' || char(9) || char(10) || char(11) "
    "|| char(12) || char(13))"
)
_POSTGRES_CATEGORY_TRIM = (
    "btrim(categoria, ' ' || chr(9) || chr(10) || chr(11) "
    "|| chr(12) || chr(13))"
)
PREDICATE_EXAME_SQLITE = (
    "tipo = 'receita' AND spirometry_exam_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_SQLITE_CATEGORY_TRIM} = '' "
    f"OR lower({_SQLITE_CATEGORY_TRIM}) = 'espirometria')"
)
PREDICATE_EXAME_POSTGRES = (
    "tipo = 'receita' AND spirometry_exam_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_POSTGRES_CATEGORY_TRIM} = '' "
    f"OR lower({_POSTGRES_CATEGORY_TRIM}) = 'espirometria')"
)
PREDICATE_CONSULTA_SQLITE = (
    "tipo = 'receita' AND consultation_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_SQLITE_CATEGORY_TRIM} = '' "
    f"OR lower({_SQLITE_CATEGORY_TRIM}) = 'consulta')"
)
PREDICATE_CONSULTA_POSTGRES = (
    "tipo = 'receita' AND consultation_id IS NOT NULL AND "
    f"(categoria IS NULL OR {_POSTGRES_CATEGORY_TRIM} = '' "
    f"OR lower({_POSTGRES_CATEGORY_TRIM}) = 'consulta')"
)

_ASCII_WHITESPACE = " \t\n\v\f\r"
_DEFAULT_IGNORABLE_RANGES = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0xE0000, 0xE0FFF),
)


def _e_default_ignorable(caractere: str) -> bool:
    ponto = ord(caractere)
    return any(inicio <= ponto <= fim for inicio, fim in _DEFAULT_IGNORABLE_RANGES)


def _sem_invisiveis(texto: str) -> str:
    saida = []
    for caractere in texto:
        categoria = unicodedata.category(caractere)
        if categoria == "Cf" or _e_default_ignorable(caractere):
            continue
        if categoria in ("Cc", "Zs", "Zl", "Zp"):
            saida.append(" ")
        else:
            saida.append(caractere)
    return "".join(saida)


def _chave_categoria(valor: str | None) -> str | None:
    if valor is None:
        return None
    limpo = " ".join(
        _sem_invisiveis(unicodedata.normalize("NFKC", valor)).split()
    )
    return limpo.casefold() if limpo else None


def _abrangida_pelo_indice(valor: str | None, chave: str) -> bool:
    """O predicado SQL cobre NULL e whitespace ASCII de borda.

    Formas Unicode mais exóticas são aceitas e canonizadas pela aplicação,
    mas não têm uma normalização NFKC portável em índices de PostgreSQL e
    SQLite. Se uma forma dessas já existir historicamente, o upgrade aborta
    em vez de criar um backstop que fingiria cobri-la.
    """
    if valor is None:
        return True
    limpo_sql = valor.strip(_ASCII_WHITESPACE)
    return limpo_sql == "" or limpo_sql.lower() == chave


def _detectar_conflitos_historicos() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, public_code, categoria,
                   spirometry_exam_id, consultation_id
              FROM financial_entries
             WHERE tipo = 'receita'
               AND (
                    spirometry_exam_id IS NOT NULL
                    OR consultation_id IS NOT NULL
               )
          ORDER BY public_code, id
            """
        )
    ).mappings().all()

    grupos: dict[tuple[str, str], list[str]] = {}
    nao_abrangidas: list[str] = []
    for row in rows:
        chave = _chave_categoria(row["categoria"])
        codigo = row["public_code"] or row["id"]
        classificacoes: list[tuple[str, str, str]] = []

        # Categoria ausente/vazia num vínculo técnico é a receita própria.
        # Com os dois vínculos, ela ocupa ambos de modo fail-closed; a API nova
        # rejeita essa ambiguidade antes de persistir.
        if chave is None:
            if row["spirometry_exam_id"]:
                classificacoes.append(
                    ("exame", row["spirometry_exam_id"], "espirometria")
                )
            if row["consultation_id"]:
                classificacoes.append(
                    ("consulta", row["consultation_id"], "consulta")
                )
        elif chave == "espirometria" and row["spirometry_exam_id"]:
            classificacoes.append(
                ("exame", row["spirometry_exam_id"], "espirometria")
            )
        elif chave == "consulta" and row["consultation_id"]:
            classificacoes.append(
                ("consulta", row["consultation_id"], "consulta")
            )

        for componente, vinculo_id, chave_canonica in classificacoes:
            grupos.setdefault((componente, vinculo_id), []).append(codigo)
            if not _abrangida_pelo_indice(row["categoria"], chave_canonica):
                nao_abrangidas.append(f"{componente}:{codigo}")

    conflitos = [
        f"{componente}=[{','.join(codigos)}]"
        for (componente, _vinculo_id), codigos in sorted(grupos.items())
        if len(codigos) > 1
    ]
    if conflitos:
        raise RuntimeError(
            "M23.1: receitas próprias históricas conflitantes; upgrade "
            "abortado sem alterar linhas: " + "; ".join(conflitos)
        )
    if nao_abrangidas:
        raise RuntimeError(
            "M23.1: categoria histórica equivalente não é representável "
            "com segurança no índice portátil; upgrade abortado sem alterar "
            "linhas: " + ", ".join(sorted(nao_abrangidas))
        )


def upgrade() -> None:
    _detectar_conflitos_historicos()
    op.create_index(
        INDEX_EXAME,
        "financial_entries",
        ["spirometry_exam_id"],
        unique=True,
        sqlite_where=sa.text(PREDICATE_EXAME_SQLITE),
        postgresql_where=sa.text(PREDICATE_EXAME_POSTGRES),
    )
    op.create_index(
        INDEX_CONSULTA,
        "financial_entries",
        ["consultation_id"],
        unique=True,
        sqlite_where=sa.text(PREDICATE_CONSULTA_SQLITE),
        postgresql_where=sa.text(PREDICATE_CONSULTA_POSTGRES),
    )


def downgrade() -> None:
    op.drop_index(INDEX_CONSULTA, table_name="financial_entries")
    op.drop_index(INDEX_EXAME, table_name="financial_entries")
