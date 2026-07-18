"""Regras de domínio centrais da M15, compartilhadas por API, CLI, importador,
schemas e seeds.

PCMSO está FORA da operação ativa (decisão registrada do proprietário).
A regra vale para dados NOVOS da operação; arquivos históricos permanecem
arquivados nas fontes legadas e não são reescritos por esta regra.
"""

import unicodedata

from fastapi import HTTPException

PCMSO_MARKERS = (
    "pcmso",
    "ocupacional",
    "medicina do trabalho",
    "saude ocupacional",
    "aso ",
)

# Campos de texto de operação que a regra inspeciona quando presentes.
PCMSO_TEXT_FIELDS = (
    "modalidade",
    "origem",
    "local_atendimento",
    "canal_entrada",
    "servico_interesse",
    "servico_solicitado",
    "observacao",
    "observacao_operacional",
    "tipo",
    "categoria",
    "descricao",
    "nome",
)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def pcmso_violation(fields: dict[str, object]) -> tuple[str, str] | None:
    """Retorna (campo, marcador) se algum campo textual contiver PCMSO."""
    for field in PCMSO_TEXT_FIELDS:
        value = fields.get(field)
        if not isinstance(value, str) or not value:
            continue
        folded = _fold(value)
        for marker in PCMSO_MARKERS:
            if marker in folded:
                return field, marker.strip()
    return None


def ensure_sem_pcmso(fields: dict[str, object], contexto: str = "") -> None:
    """Levanta 422 estruturado se o payload contiver PCMSO em campo ativo.

    O chamador da API audita a rejeição (acao='pcmso.rejeitado').
    """
    violation = pcmso_violation(fields)
    if violation:
        campo, marcador = violation
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": "pcmso_fora_da_operacao",
                "mensagem": "PCMSO está fora da operação ativa da SoproLife.",
                "campo": campo,
                "marcador": marcador,
                "contexto": contexto or None,
            },
        )
