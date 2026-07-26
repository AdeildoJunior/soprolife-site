"""Contrato canônico de categoria financeira — M23.1.

Uma ÚNICA fonte de normalização compartilhada por quem cria (POST
/lancamentos e /atendimentos), quem atualiza (PATCH /lancamentos) e quem
detecta duplicidade. A revisão crítica do M23.1 provou o custo de não ter
isso: o bloqueio de receita duplicada comparava a categoria com `==` puro,
então "espirometria", "ESPIROMETRIA", " Espirometria " e a categoria ausente
passavam direto e criavam uma segunda receita idêntica para o mesmo exame.

Regras:

- A CHAVE de comparação (`chave_categoria`) usa NFKC, remoção de caracteres
  invisíveis (formato/controle), colapso de espaços e casefold Unicode. Duas
  grafias que só diferem por caixa, espaço, forma Unicode compatível ou
  caractere invisível são a MESMA categoria. Acentos não são descartados:
  remover informação linguística faria categorias potencialmente distintas
  colidirem.
- A forma PERSISTIDA (`canonizar_categoria`) é a grafia canônica exata
  quando a chave é conhecida ("Espirometria", "Consulta", "Repasse ao
  médico"); qualquer outra categoria é preservada como o operador digitou,
  apenas limpa de espaços/invisíveis. Categorias legítimas e não
  equivalentes ("Ajuste", "Outro", "Repasse ao médico") continuam distintas.
- Nada aqui conhece HTTP, banco ou PII: são funções puras, testáveis
  isoladamente, sem dependência do resto do núcleo.
"""

import unicodedata

# Categorias da receita PRÓPRIA de um exame/consulta (o lançamento que o
# atendimento cria sozinho) e do repasse médico. Definidas aqui para que
# attendances.py, finance.py, o guarda de duplicidade e a migração de
# unicidade nunca divirjam por um literal digitado diferente.
CATEGORIA_ESPIROMETRIA = "Espirometria"
CATEGORIA_CONSULTA = "Consulta"
CATEGORIA_REPASSE_MEDICO = "Repasse ao médico"

CATEGORIAS_CANONICAS = (
    CATEGORIA_ESPIROMETRIA,
    CATEGORIA_CONSULTA,
    CATEGORIA_REPASSE_MEDICO,
)

# Pontos de código com a propriedade Unicode Default_Ignorable que NÃO são
# Cf (os Cf já são removidos abaixo). Inclui CGJ, fillers, vogais Khmer
# inerentes, seletores de variação e o bloco suplementar reservado para tags/
# seletores. Sem isso, "Espirometria\ufe0f" ou "Espi\u034frometria" parecem
# idênticas na tela, mas virariam uma categoria livre e contornariam o guarda.
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

# Rótulo humano do componente, usado nas mensagens de conflito.
ROTULO_EXAME = "exame"
ROTULO_CONSULTA = "consulta"


def _e_default_ignorable(caractere: str) -> bool:
    ponto = ord(caractere)
    return any(inicio <= ponto <= fim for inicio, fim in _DEFAULT_IGNORABLE_RANGES)


def _sem_invisiveis(texto: str) -> str:
    """Remove caracteres de formato (Cf: ZWSP, ZWNJ, BOM…) e transforma
    controle/separadores em espaço.

    Sem isto, "Espirometria​" seria uma categoria "diferente" e
    contornaria o bloqueio de duplicidade sem qualquer diferença visível
    para o operador.
    """
    saida = []
    for caractere in texto:
        categoria_unicode = unicodedata.category(caractere)
        if categoria_unicode == "Cf" or _e_default_ignorable(caractere):
            continue
        if categoria_unicode in ("Cc", "Zs", "Zl", "Zp"):
            saida.append(" ")
            continue
        saida.append(caractere)
    return "".join(saida)


def limpar_categoria(valor: str | None) -> str | None:
    """Forma limpa e comparável visualmente: NFKC, sem invisíveis, espaços
    colapsados. Devolve None para vazio/ausente."""
    if valor is None:
        return None
    if not isinstance(valor, str):  # pragma: no cover - contrato do schema
        raise TypeError("categoria precisa ser texto ou None")
    limpo = " ".join(_sem_invisiveis(unicodedata.normalize("NFKC", valor)).split())
    return limpo or None


def chave_categoria(valor: str | None) -> str | None:
    """Chave de equivalência Unicode: caixa, espaço e invisível não contam."""
    limpo = limpar_categoria(valor)
    if limpo is None:
        return None
    return limpo.casefold()


_CANONICA_POR_CHAVE = {chave_categoria(c): c for c in CATEGORIAS_CANONICAS}


def canonizar_categoria(valor: str | None) -> str | None:
    """Forma que vai PARA O BANCO.

    Grafia canônica exata quando a chave é conhecida; caso contrário a
    categoria limpa do operador (categorias livres seguem livres).
    """
    limpo = limpar_categoria(valor)
    if limpo is None:
        return None
    return _CANONICA_POR_CHAVE.get(chave_categoria(limpo), limpo)


def mesma_categoria(a: str | None, b: str | None) -> bool:
    """True quando as duas grafias são a MESMA categoria (chave igual).

    Ausência nunca é igual a nada: quem não tem categoria é resolvido por
    `categoria_propria`, não por comparação.
    """
    chave_a = chave_categoria(a)
    if chave_a is None:
        return False
    return chave_a == chave_categoria(b)


def categoria_propria(
    *, spirometry_exam_id: str | None, consultation_id: str | None
) -> tuple[str | None, str | None]:
    """(categoria canônica, rótulo) da receita PRÓPRIA do componente vinculado.

    Devolve (None, None) quando o lançamento não tem exatamente um vínculo de
    atendimento: sem vínculo não existe "receita própria", e com exame E
    consulta ao mesmo tempo a inferência seria ambígua — nunca escolhemos por
    conta própria.
    """
    if spirometry_exam_id and consultation_id:
        return None, None
    if spirometry_exam_id:
        return CATEGORIA_ESPIROMETRIA, ROTULO_EXAME
    if consultation_id:
        return CATEGORIA_CONSULTA, ROTULO_CONSULTA
    return None, None


def categoria_efetiva_de_receita(
    *,
    tipo: str,
    categoria: str | None,
    spirometry_exam_id: str | None,
    consultation_id: str | None,
) -> str | None:
    """Categoria efetiva de uma receita, já canonizada.

    Decisão documentada do M23.1 sobre categoria AUSENTE: uma receita ligada
    a exatamente um componente e sem categoria É a receita própria daquele
    componente, então é canonizada para ela. `categoria` sempre foi opcional
    no contrato da API (schemas.FinancialEntryCreate) e a UI envia o preset;
    recusar quebraria clientes e importadores existentes, enquanto canonizar
    torna a omissão indistinguível da grafia canônica — e portanto sujeita ao
    mesmo bloqueio de duplicidade, que era exatamente o contorno explorado na
    revisão crítica.

    A ambiguidade (exame E consulta no mesmo lançamento sem categoria) NÃO é
    resolvida aqui: devolve None e quem chama recusa com 422.
    """
    canonica = canonizar_categoria(categoria)
    if canonica is not None:
        return canonica
    if tipo != "receita":
        return None
    propria, _rotulo = categoria_propria(
        spirometry_exam_id=spirometry_exam_id, consultation_id=consultation_id
    )
    return propria


def e_receita_propria_do_componente(
    *,
    tipo: str,
    categoria: str | None,
    spirometry_exam_id: str | None,
    consultation_id: str | None,
) -> tuple[str | None, str | None]:
    """(categoria canônica, rótulo) quando o lançamento É a receita própria do
    componente vinculado; (None, None) caso contrário.

    É o predicado único do bloqueio de duplicidade — usado igual no POST, no
    PATCH e ao classificar as linhas já existentes no banco.
    """
    if tipo != "receita":
        return None, None
    efetiva = categoria_efetiva_de_receita(
        tipo=tipo,
        categoria=categoria,
        spirometry_exam_id=spirometry_exam_id,
        consultation_id=consultation_id,
    )
    # Categoria explícita elimina a ambiguidade mesmo quando o lançamento
    # carrega os dois vínculos técnicos de um atendimento combinado. Sem
    # isto, acrescentar consultation_id a uma segunda receita de exame (ou o
    # inverso) contornaria tanto o guarda do POST quanto o do PATCH.
    if spirometry_exam_id and mesma_categoria(efetiva, CATEGORIA_ESPIROMETRIA):
        return CATEGORIA_ESPIROMETRIA, ROTULO_EXAME
    if consultation_id and mesma_categoria(efetiva, CATEGORIA_CONSULTA):
        return CATEGORIA_CONSULTA, ROTULO_CONSULTA
    return None, None
