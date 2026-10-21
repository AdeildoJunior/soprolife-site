"""Structured spirometry service-description mapping for the national DPS
``xDescServ`` field (``TCCServ``/``cServ``).

The variant (with/without bronchodilator) comes ONLY from
``SpirometryExam.broncodilatador`` — a structured, tri-state boolean column
already used as the sole source of truth for this distinction elsewhere in
the domain (see ``app/services/nfse.py::evaluate()``). Never inferred from
free text (``observacao``, ``indicacao_clinica`` or any other field). When the
attribute is ``None`` ("não informado" — legacy records, per the column's own
docstring), this fails closed by raising rather than guessing a variant.

The two exact sentences below are the real, accountant/company-supplied
SoproLife service descriptions for the national DPS (M29 mission, section B)
— not paraphrased or shortened.
"""
from __future__ import annotations

ISSUER_LEGAL_NAME = "SoproLife Diagnósticos e Soluções em Saúde LTDA"

_DESCRIPTIONS: dict[bool, str] = {
    True: (
        "Espirometria com broncodilatador, com emissão de laudo médico, "
        f"realizada pela {ISSUER_LEGAL_NAME}."
    ),
    False: (
        "Espirometria sem broncodilatador, com emissão de laudo médico, "
        f"realizada pela {ISSUER_LEGAL_NAME}."
    ),
}


class ServiceDescriptionUndetermined(ValueError):
    """Raised instead of guessing a variant when the structured attribute is
    absent/undetermined. Never carries any patient-identifying data."""


def spirometry_service_description(broncodilatador: bool | None) -> str:
    """Return the exact national-DPS service description for this exam.

    ``broncodilatador`` must be a definite ``True``/``False``. Any other
    value (``None``, or anything not already a ``bool``) fails closed —
    this is a fiscal document description, never a best-effort guess.
    """
    if not isinstance(broncodilatador, bool):
        raise ServiceDescriptionUndetermined(
            "exam.broncodilatador não é um booleano definido; a descrição de "
            "serviço da DPS nacional não pode ser inferida (fail closed)."
        )
    return _DESCRIPTIONS[broncodilatador]


_GROUPED_DESCRIPTIONS: dict[bool, str] = {
    True: (
        "{count} espirometrias com broncodilatador, com emissão de laudo "
        f"médico, realizadas pela {ISSUER_LEGAL_NAME}."
    ),
    False: (
        "{count} espirometrias sem broncodilatador, com emissão de laudo "
        f"médico, realizadas pela {ISSUER_LEGAL_NAME}."
    ),
}


def grouped_spirometry_service_description(
    count: int, broncodilatador: bool | None
) -> str:
    """M26.34 — a mesma sentença oficial, no plural, para um documento que
    cobre vários exames.

    Deriva das DUAS sentenças acima palavra por palavra: só o número e a
    concordância mudam. Nada de novo é redigido aqui, porque a descrição do
    serviço de uma NFS-e é texto fornecido pela contabilidade, não redação do
    sistema.

    `broncodilatador` precisa ser um único True/False — o valor que TODOS os
    exames do grupo compartilham. Um grupo misto não tem descrição correta:
    "com broncodilatador" mentiria sobre um dos exames e "sem" mentiria sobre
    o outro. Quem agrupa é responsável por recusar a mistura antes de chegar
    aqui (ver `services/nfse_grouped.py`), e isto falha fechado se não o fez.
    """
    if not isinstance(broncodilatador, bool):
        raise ServiceDescriptionUndetermined(
            "o grupo não tem um valor único de broncodilatador; a descrição de "
            "serviço da DPS nacional não pode ser inferida (fail closed)."
        )
    if not isinstance(count, int) or count < 2:
        raise ServiceDescriptionUndetermined(
            "um documento agrupado descreve dois ou mais exames."
        )
    return _GROUPED_DESCRIPTIONS[broncodilatador].format(count=count)
