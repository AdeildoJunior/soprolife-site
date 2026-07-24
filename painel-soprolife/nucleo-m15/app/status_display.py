"""Apresentação canônica de status de atendimento (M20).

Escopo DELIBERADAMENTE mínimo: normaliza para exibição apenas os valores
que significam "a espirometria foi realizada". O valor gravado no banco
NUNCA é reescrito — isto é camada de apresentação.

Em escopo (todos exibem "Espirometria realizada"):
    "Realizado", "realizado", "Exame realizado", "exame realizado"

FORA DE ESCOPO, por decisão explícita do operador:
    "Liberado" / "liberado" — permanecem exatamente como estão, tanto no
    armazenamento quanto na exibição e no comportamento. Nenhum outro
    status é renomeado, migrado, reinterpretado ou remapeado aqui.
"""

EXAM_PERFORMED_DISPLAY = "Espirometria realizada"

# Chaves comparadas em minúsculas, sem espaços nas pontas. A lista é
# fechada: qualquer outro valor sai inalterado.
EXAM_PERFORMED_STORED = ("Realizado", "realizado", "Exame realizado", "exame realizado")

_EXAM_PERFORMED_KEYS = frozenset(v.lower() for v in EXAM_PERFORMED_STORED)


def is_exam_performed_status(status: str | None) -> bool:
    """True somente para os valores que significam exame realizado."""
    if not isinstance(status, str):
        return False
    return status.strip().lower() in _EXAM_PERFORMED_KEYS


def exam_status_display(status: str | None) -> str | None:
    """Rótulo de exibição do status da espirometria.

    Devolve "Espirometria realizada" para os valores em escopo e o próprio
    valor (intacto) para qualquer outro — inclusive "Liberado".
    """
    if status is None:
        return None
    if is_exam_performed_status(status):
        return EXAM_PERFORMED_DISPLAY
    return status


def exam_status_filter_values(status: str | None) -> list[str] | None:
    """Valores armazenados que um filtro de status deve casar.

    Filtrar por "Espirometria realizada" (ou por qualquer sinônimo em
    escopo) casa TODOS os sinônimos armazenados; qualquer outro filtro
    continua sendo igualdade exata com o valor gravado.
    """
    if status is None:
        return None
    if is_exam_performed_status(status) or status.strip() == EXAM_PERFORMED_DISPLAY:
        return list(EXAM_PERFORMED_STORED)
    return [status]
