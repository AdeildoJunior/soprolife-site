"""M25.18 — CPF do paciente: opcional, validado, nunca inventado.

A Resolução CFM 2.381/2024 pede a identificação do paciente por nome e CPF
*quando houver*. O núcleo não tinha o campo, e a M25.15 registrou isso como
pendência em vez de preencher com aproximação.

Três decisões moldam este módulo:

* **Opcional de verdade.** Existe paciente sem CPF aplicável (criança sem
  inscrição, estrangeiro em atendimento pontual). Um campo obrigatório
  produziria CPF inventado para destravar cadastro — exatamente o que não
  pode acontecer num documento médico.

* **Validado quando preenchido.** Guardar dígito que não fecha é pior que não
  guardar: o laudo sairia com um CPF que não é de ninguém. A checagem é a dos
  dois dígitos verificadores, mais a recusa das sequências repetidas
  (`111.111.111-11` e afins passam no cálculo e não são CPF de ninguém).

* **Armazenado só em dígitos.** Máscara é apresentação. Guardar
  `123.456.789-09` e `12345678909` como coisas diferentes tornaria a
  unicidade inútil.
"""

from __future__ import annotations

import re

_NAO_DIGITO = re.compile(r"\D")
CPF_LEN = 11


class CPFInvalidoError(ValueError):
    """CPF preenchido mas inválido — com `codigo` estável para a resposta."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


def somente_digitos(valor: str | None) -> str:
    return _NAO_DIGITO.sub("", valor or "")


def _digito_verificador(digitos: str, peso_inicial: int) -> int:
    soma = sum(
        int(d) * peso
        for d, peso in zip(digitos, range(peso_inicial, 1, -1))
    )
    resto = (soma * 10) % 11
    return 0 if resto == 10 else resto


def cpf_valido(valor: str | None) -> bool:
    """True somente para CPF com 11 dígitos e verificadores corretos."""

    digitos = somente_digitos(valor)
    if len(digitos) != CPF_LEN:
        return False
    # Sequência repetida fecha a conta mas não corresponde a ninguém.
    if digitos == digitos[0] * CPF_LEN:
        return False
    if _digito_verificador(digitos[:9], 10) != int(digitos[9]):
        return False
    return _digito_verificador(digitos[:10], 11) == int(digitos[10])


def normalizar_cpf(valor: str | None) -> str | None:
    """Digitos do CPF, ou `None` quando o campo veio vazio.

    Vazio é ausência legítima e devolve `None`; preenchido e inválido
    levanta, porque aceitar seria gravar um número que não é de ninguém.
    """

    if valor is None:
        return None
    bruto = str(valor).strip()
    if not bruto:
        return None
    digitos = somente_digitos(bruto)
    if len(digitos) != CPF_LEN:
        raise CPFInvalidoError(
            "cpf_formato_invalido",
            "O CPF precisa ter 11 dígitos. Deixe em branco se não houver.",
        )
    if not cpf_valido(digitos):
        raise CPFInvalidoError(
            "cpf_invalido",
            "O CPF informado não é válido. Confira os dígitos ou deixe em "
            "branco se não houver.",
        )
    return digitos


def formatar_cpf(digitos: str | None) -> str | None:
    """`12345678909` → `123.456.789-09`. Apresentação, nunca armazenamento."""

    limpo = somente_digitos(digitos)
    if len(limpo) != CPF_LEN:
        return None
    return f"{limpo[:3]}.{limpo[3:6]}.{limpo[6:9]}-{limpo[9:]}"


def mascarar_cpf(digitos: str | None) -> str | None:
    """`12345678909` → `***.456.789-**`.

    Para telas que precisam mostrar que EXISTE um CPF e permitir conferência
    parcial, sem imprimir o número inteiro. O laudo usa `formatar_cpf`; as
    telas de cadastro e listagem usam esta.
    """

    limpo = somente_digitos(digitos)
    if len(limpo) != CPF_LEN:
        return None
    return f"***.{limpo[3:6]}.{limpo[6:9]}-**"
