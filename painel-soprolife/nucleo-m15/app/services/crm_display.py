"""M25.15 — apresentação canônica do CRM.

O número gravado (`crm_number`) é a identidade rastreável e NUNCA é
reescrito por este módulo: aqui só se decide como ele é DESENHADO na tela e
no PDF. Separar as duas coisas é o que permite corrigir a aparência sem
tocar em `verification_status`, `verification_reference` ou na trilha de
verificação no conselho.

A única máscara conhecida é a do CRM-RJ, que a SoproLife usa hoje e cujo
formato foi conferido com o registro real: oito dígitos desenhados como
``NN.NNNNN-N`` (dois, ponto, cinco, hífen, dígito verificador).

Fora desse caso exato nada é mascarado. Inventar um agrupamento para uma UF
cujo padrão não foi conferido produziria um número com aparência oficial e
separadores errados — pior que não formatar, porque parece correto. O
fallback é sempre o que já existe: `crm_display` cadastrado ou, na falta
dele, o número puro.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d")

# UFs cuja máscara foi verificada contra um registro real. Crescer esta
# tabela exige a mesma prova: um número conferido, não uma suposição.
_MASKED_STATES = frozenset({"RJ"})
_RJ_DIGIT_COUNT = 8


def crm_digits(crm_number: str | None) -> str:
    """Somente os dígitos do número gravado."""

    return "".join(_DIGITS.findall(crm_number or ""))


def format_crm_number(
    crm_number: str | None,
    crm_state: str | None,
    *,
    crm_display: str | None = None,
) -> str:
    """Número do CRM como deve ser lido por uma pessoa.

    Devolve string vazia apenas quando não há nada cadastrado — nunca um
    placeholder que pareça um número.
    """

    state = (crm_state or "").strip().upper()
    digits = crm_digits(crm_number)
    if state in _MASKED_STATES and len(digits) == _RJ_DIGIT_COUNT:
        return f"{digits[:2]}.{digits[2:7]}-{digits[7]}"
    stored = (crm_display or "").strip()
    if stored:
        return stored
    return (crm_number or "").strip()


def format_crm_full(
    crm_number: str | None,
    crm_state: str | None,
    *,
    crm_display: str | None = None,
) -> str:
    """Registro completo com a UF, como impresso no laudo: ``CRM-RJ 52.62307-5``."""

    number = format_crm_number(
        crm_number, crm_state, crm_display=crm_display
    )
    state = (crm_state or "").strip().upper()
    if not number:
        return f"CRM-{state}" if state else ""
    if not state:
        return number
    return f"CRM-{state} {number}"


def format_physician_credentials(
    professional_name: str,
    *,
    crm_number: str | None,
    crm_state: str | None,
    crm_display: str | None = None,
    rqe: str | None = None,
    especialidade: str | None = None,
) -> str:
    """Rótulo humano do médico para seletores e cabeçalhos.

    ``Dra. Ana … • Pneumologista • CRM-RJ 52.62307-5 • RQE 58224``. Partes
    ausentes somem em vez de virar "não informado": este rótulo é escolha de
    interface, não declaração documental.
    """

    partes = [professional_name.strip()]
    if especialidade and especialidade.strip():
        partes.append(especialidade.strip())
    registro = format_crm_full(
        crm_number, crm_state, crm_display=crm_display
    )
    if registro:
        partes.append(registro)
    if rqe and str(rqe).strip():
        partes.append(f"RQE {str(rqe).strip()}")
    return " • ".join(partes)
