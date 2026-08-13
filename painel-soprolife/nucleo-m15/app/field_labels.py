"""M25.26 — tradução de caminho de payload para o rótulo que o operador vê.

Existe por causa de um defeito observado em uso real: a Dra. Ana e o operador
tentaram lançar uma Espirometria SoproLife, o servidor recusou com 422 e a
tela mostrou literalmente ``Payload inválido.``. A informação de QUAL campo
faltava existia na resposta (``erro.campos``), mas em formato de caminho
técnico (``body.espirometria.data_exame``) e sem rótulo — nada que se possa
mostrar a quem opera.

Duas decisões deliberadas:

* **O rótulo é o MESMO texto do formulário.** "Data do exame" aqui é a
  mesma string que a Central de Cadastros imprime no ``<label>``. Um
  sinônimo ("data de realização") obrigaria o operador a traduzir o erro
  antes de consertar, que é exatamente o trabalho que esta missão remove.

* **Nenhum valor digitado entra no texto do erro.** O motivo é derivado do
  TIPO do erro do Pydantic, nunca do ``input``. É o que impede um CPF ou um
  telefone de vazar para um log de acesso ou para uma captura de tela.
"""

from __future__ import annotations

import re

# Caminho do payload (já sem o prefixo "body") -> rótulo do formulário.
#
# A busca é feita do caminho mais específico para o mais genérico, então
# ``financeiro.espirometria.status`` não é confundido com ``espirometria.status``.
CAMPO_ROTULOS: dict[str, str] = {
    # ------------------------------------------------ atendimento (raiz)
    "person_id": "Paciente",
    "tipo": "Tipo de atendimento",
    "idempotency_key": "Chave de idempotência",
    # ------------------------------------------------------ espirometria
    "espirometria.data_exame": "Data do exame",
    "espirometria.status": "Status do exame",
    "espirometria.broncodilatador": "Broncodilatador",
    "espirometria.modalidade": "Modalidade",
    "espirometria.local_atendimento": "Local / unidade de atendimento",
    "espirometria.partner_id": "Parceiro",
    "espirometria.partner_unit_id": "Unidade operacional",
    "espirometria.origem": "Origem",
    "espirometria.responsavel": "Técnico / responsável",
    "espirometria.observacao": "Observações do exame",
    "espirometria.proximo_followup": "Próximo acompanhamento",
    # ----------------------------------------------------------- consulta
    "consulta.data_consulta": "Data da consulta",
    "consulta.status": "Status da consulta",
    "consulta.modalidade": "Modalidade da consulta",
    "consulta.profissional": "Médico / profissional",
    "consulta.origem": "Origem",
    "consulta.responsavel": "Responsável",
    "consulta.observacao": "Observações da consulta",
    "consulta.retorno": "Retorno",
    "consulta.retorno_data": "Data do retorno",
    "consulta.retorno_intervalo_meses": "Intervalo de retorno (meses)",
    # -------------------------------------------------------- financeiro
    "financeiro.espirometria.valor": "Valor da espirometria",
    "financeiro.espirometria.status": "Status do pagamento",
    "financeiro.espirometria.data_competencia": "Competência do lançamento",
    "financeiro.espirometria.data_recebimento": "Data de recebimento",
    "financeiro.espirometria.forma_pagamento": "Forma de pagamento",
    "financeiro.espirometria.origem_preco": "Origem do preço",
    "financeiro.consulta.valor_bruto": "Receita bruta da consulta",
    "financeiro.consulta.status": "Status do pagamento da consulta",
    "financeiro.consulta.data_competencia": "Competência do lançamento",
    "financeiro.consulta.data_recebimento": "Data de recebimento",
    "financeiro.consulta.forma_pagamento": "Forma de pagamento",
    "financeiro.consulta.origem_preco": "Origem do preço",
    "financeiro.consulta.repasse_medico_percentual": "Percentual do repasse",
    "financeiro.consulta.repasse_medico_valor": "Valor do repasse",
    "financeiro.consulta.repasse_medico_status": "Status do repasse",
    # ------------------------------------------------------------- pessoa
    "nome_completo": "Nome completo",
    "data_nascimento": "Data de nascimento",
    "cpf": "CPF",
    "sexo": "Sexo",
    "observacao": "Observações",
    "consentimento_whatsapp": "Consentimento WhatsApp",
    "contatos": "Contatos",
    "contatos.valor": "Contato (telefone ou e-mail)",
    "contatos.tipo": "Tipo de contato",
    "pessoa.nome_completo": "Nome completo",
    "pessoa.data_nascimento": "Data de nascimento",
    "pessoa.cpf": "CPF",
    "pessoa.sexo": "Sexo",
    "pessoa.contatos": "Contatos",
    "pessoa.consentimento_whatsapp": "Consentimento WhatsApp",
}

# Tipo de erro do Pydantic -> motivo em português, sem eco do valor digitado.
MOTIVOS: dict[str, str] = {
    "missing": "não preenchido",
    "string_too_short": "curto demais",
    "string_too_long": "longo demais",
    "string_pattern_mismatch": "formato inválido",
    "literal_error": "opção não permitida",
    "enum": "opção não permitida",
    "extra_forbidden": "não é aceito neste formulário",
    "date_parsing": "data inválida",
    "date_from_datetime_parsing": "data inválida",
    "int_parsing": "precisa ser um número inteiro",
    "decimal_parsing": "valor numérico inválido",
    "float_parsing": "valor numérico inválido",
    "greater_than": "fora do intervalo permitido",
    "greater_than_equal": "fora do intervalo permitido",
    "less_than": "fora do intervalo permitido",
    "less_than_equal": "fora do intervalo permitido",
    "bool_parsing": "precisa ser sim ou não",
    "model_type": "formato inválido",
}

MOTIVO_PADRAO = "inválido"

# Índices de lista viram um caminho estável: contatos.0.valor -> contatos.valor.
_INDICE = re.compile(r"\.\d+(?=\.|$)")

# Guarda de PII para a mensagem de domínio (ver `mensagem_de_dominio`).
_SEQUENCIA_LONGA_DE_DIGITOS = re.compile(r"\d{6,}")


def caminho_normalizado(loc: tuple | list) -> str:
    """Caminho do erro sem o prefixo ``body`` e sem índices de lista."""

    partes = [str(p) for p in loc]
    if partes and partes[0] in ("body", "query", "path"):
        partes = partes[1:]
    return _INDICE.sub("", ".".join(partes))


def rotulo_do_campo(caminho: str) -> str:
    """Rótulo do formulário para um caminho, do específico ao genérico.

    Sem entrada no registro, devolve o último segmento com a primeira letra
    maiúscula — pior que um rótulo curado, melhor que um caminho técnico.
    """

    if not caminho:
        return "Formulário"
    if caminho in CAMPO_ROTULOS:
        return CAMPO_ROTULOS[caminho]
    partes = caminho.split(".")
    # ``a.b.c`` -> tenta ``b.c``, depois ``c``.
    for inicio in range(1, len(partes)):
        sufixo = ".".join(partes[inicio:])
        if sufixo in CAMPO_ROTULOS:
            return CAMPO_ROTULOS[sufixo]
    return partes[-1].replace("_", " ").capitalize()


def motivo_do_tipo(tipo: str) -> str:
    return MOTIVOS.get(tipo, MOTIVO_PADRAO)


def mensagem_de_dominio(erro: dict) -> str | None:
    """Texto do ``ValueError`` levantado pelos validadores deste projeto.

    Só é aproveitado para ``value_error``, que no núcleo M15 vem sempre de um
    ``raise ValueError(...)` escrito à mão em ``schemas.py`` — texto de
    domínio, com enums fechados, nunca com valor digitado pelo operador.

    Ainda assim a saída passa por uma guarda: mensagem com sequência longa de
    dígitos ou com ``@`` é descartada. Se algum validador futuro interpolar a
    entrada, o pior desfecho é o operador ver o motivo genérico — nunca um
    CPF, telefone ou e-mail impresso numa tela ou capturado num print.
    """

    if erro.get("type") != "value_error":
        return None
    bruto = (erro.get("ctx") or {}).get("error")
    texto = str(bruto).strip() if bruto is not None else ""
    if not texto:
        texto = str(erro.get("msg") or "").strip()
        # Pydantic prefixa a mensagem do validador; o prefixo não é para humanos.
        if texto.startswith("Value error, "):
            texto = texto[len("Value error, "):]
    if not texto:
        return None
    if "@" in texto or _SEQUENCIA_LONGA_DE_DIGITOS.search(texto):
        return None
    return texto
