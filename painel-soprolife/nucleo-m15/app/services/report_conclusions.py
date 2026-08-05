"""M25.2 — catálogo fechado de conclusões de espirometria.

O painel mostra a abreviação curta (``short_label``); o PDF do laudo recebe
sempre o texto por extenso (``full_text``). A conversão é feita aqui, nunca
no navegador: o texto que vai para o documento é decidido no servidor a
partir de um código de catálogo fechado.

Este módulo NÃO interpreta valores de espirometria, NÃO calcula grau e NÃO
sugere conclusão. A escolha do código é integralmente da médica (requisito
explícito do marco: "os graus não devem ser calculados nem selecionados
automaticamente"). Aqui só existe a tradução código -> texto e a montagem
determinística do texto final.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

CONCLUSION_CUSTOM_CODE = "PERSONALIZADO"

BD_NOT_PERFORMED_CODE = "BD_NAO_REALIZADO"


@dataclass(frozen=True)
class ConclusionOption:
    """Uma entrada do catálogo. ``full_text`` é o que entra no PDF."""

    code: str
    short_label: str
    full_text: str
    group: str


@dataclass(frozen=True)
class BronchodilatorOption:
    """Complemento pós-broncodilatador.

    ``full_text`` vazio significa "não acrescentar frase" — usado apenas por
    ``BD_NAO_REALIZADO``.
    """

    code: str
    short_label: str
    full_text: str


# Ordem preservada: é a ordem exibida nos botões curtos do workspace médico.
CONCLUSION_OPTIONS: tuple[ConclusionOption, ...] = (
    ConclusionOption(
        "NORMAL",
        "Normal",
        "Espirometria dentro dos limites da normalidade.",
        "normal",
    ),
    ConclusionOption(
        "DVO_LEVE",
        "DVO Leve",
        "Distúrbio ventilatório obstrutivo leve.",
        "obstrutivo",
    ),
    ConclusionOption(
        "DVO_MODERADO",
        "DVO Moderado",
        "Distúrbio ventilatório obstrutivo moderado.",
        "obstrutivo",
    ),
    ConclusionOption(
        "DVO_MOD_GRAVE",
        "DVO Mod. grave",
        "Distúrbio ventilatório obstrutivo moderadamente grave.",
        "obstrutivo",
    ),
    ConclusionOption(
        "DVO_GRAVE",
        "DVO Grave",
        "Distúrbio ventilatório obstrutivo grave.",
        "obstrutivo",
    ),
    ConclusionOption(
        "DVO_MUITO_GRAVE",
        "DVO Muito grave",
        "Distúrbio ventilatório obstrutivo muito grave.",
        "obstrutivo",
    ),
    ConclusionOption(
        "DVR_SUG_LEVE",
        "DVR sug. Leve",
        "Padrão sugestivo de distúrbio ventilatório restritivo leve.",
        "restritivo",
    ),
    ConclusionOption(
        "DVR_SUG_MODERADO",
        "DVR sug. Moderado",
        "Padrão sugestivo de distúrbio ventilatório restritivo moderado.",
        "restritivo",
    ),
    ConclusionOption(
        "DVR_SUG_MOD_GRAVE",
        "DVR sug. Mod. grave",
        (
            "Padrão sugestivo de distúrbio ventilatório restritivo "
            "moderadamente grave."
        ),
        "restritivo",
    ),
    ConclusionOption(
        "DVR_SUG_GRAVE",
        "DVR sug. Grave",
        "Padrão sugestivo de distúrbio ventilatório restritivo grave.",
        "restritivo",
    ),
    ConclusionOption(
        "DVR_SUG_MUITO_GRAVE",
        "DVR sug. Muito grave",
        "Padrão sugestivo de distúrbio ventilatório restritivo muito grave.",
        "restritivo",
    ),
    ConclusionOption(
        "DVM_SUG_LEVE",
        "DVM sug. Leve",
        "Padrão sugestivo de distúrbio ventilatório misto leve.",
        "misto",
    ),
    ConclusionOption(
        "DVM_SUG_MODERADO",
        "DVM sug. Moderado",
        "Padrão sugestivo de distúrbio ventilatório misto moderado.",
        "misto",
    ),
    ConclusionOption(
        "DVM_SUG_MOD_GRAVE",
        "DVM sug. Mod. grave",
        "Padrão sugestivo de distúrbio ventilatório misto moderadamente grave.",
        "misto",
    ),
    ConclusionOption(
        "DVM_SUG_GRAVE",
        "DVM sug. Grave",
        "Padrão sugestivo de distúrbio ventilatório misto grave.",
        "misto",
    ),
    ConclusionOption(
        "DVM_SUG_MUITO_GRAVE",
        "DVM sug. Muito grave",
        "Padrão sugestivo de distúrbio ventilatório misto muito grave.",
        "misto",
    ),
    ConclusionOption(
        "DVI",
        "DVI",
        "Padrão sugestivo de distúrbio ventilatório inespecífico.",
        "inespecifico",
    ),
    ConclusionOption(
        CONCLUSION_CUSTOM_CODE,
        "Personalizado",
        "",
        "personalizado",
    ),
)

BRONCHODILATOR_OPTIONS: tuple[BronchodilatorOption, ...] = (
    BronchodilatorOption(
        "RBD_POSITIVO",
        "RBD+",
        "Com resposta significativa ao broncodilatador.",
    ),
    BronchodilatorOption(
        "RBD_NEGATIVO",
        "RBD−",
        "Sem resposta significativa ao broncodilatador.",
    ),
    BronchodilatorOption(
        "REV_COMPLETA",
        "REV completa",
        "Reversibilidade completa após broncodilatador.",
    ),
    BronchodilatorOption(
        "REV_PARCIAL",
        "REV parcial",
        "Reversibilidade parcial após broncodilatador.",
    ),
    BronchodilatorOption(BD_NOT_PERFORMED_CODE, "BD não realizado", ""),
)

CONCLUSIONS_BY_CODE: dict[str, ConclusionOption] = {
    option.code: option for option in CONCLUSION_OPTIONS
}
BRONCHODILATOR_BY_CODE: dict[str, BronchodilatorOption] = {
    option.code: option for option in BRONCHODILATOR_OPTIONS
}

CONCLUSION_CODES = frozenset(CONCLUSIONS_BY_CODE)
BRONCHODILATOR_CODES = frozenset(BRONCHODILATOR_BY_CODE)

# Complementos que só fazem sentido quando o exame realmente possui fase
# pós-broncodilatador. `BD_NAO_REALIZADO` é o único aceito quando não há
# fase pós-BD — e ele não acrescenta frase nenhuma.
POST_BD_ONLY_CODES = frozenset(
    code for code in BRONCHODILATOR_CODES if code != BD_NOT_PERFORMED_CODE
)

MAX_CUSTOM_CONCLUSION_CHARS = 2000
MAX_OBSERVATIONS_CHARS = 2000
MAX_FINAL_TEXT_CHARS = 6000


class ConclusionCatalogError(ValueError):
    """Erro de catálogo com `codigo` estável para a resposta 422."""

    def __init__(self, codigo: str, mensagem: str):
        self.codigo = codigo
        self.mensagem = mensagem
        super().__init__(mensagem)


def available_bronchodilator_options(
    *, has_post_bd: bool
) -> tuple[BronchodilatorOption, ...]:
    """Opções compatíveis com o exame.

    Sem fase pós-broncodilatador o catálogo devolve apenas
    ``BD_NAO_REALIZADO`` — o requisito proíbe exibir opções incompatíveis.
    """

    if has_post_bd:
        return BRONCHODILATOR_OPTIONS
    return tuple(
        option
        for option in BRONCHODILATOR_OPTIONS
        if option.code == BD_NOT_PERFORMED_CODE
    )


def _normalize_text(value: str) -> str:
    """Normaliza quebras de linha e espaços sem descartar caracteres.

    O texto é conteúdo clínico assinado: nada é truncado nem reescrito além
    de normalização Unicode NFC, remoção de caracteres de controle não
    imprimíveis e colapso de linhas em branco excedentes.
    """

    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = "".join(
            char
            for char in raw_line
            if char == "\t" or unicodedata.category(char) != "Cc"
        )
        cleaned_lines.append(line.rstrip())
    # Colapsa 3+ quebras consecutivas em no máximo uma linha em branco.
    collapsed: list[str] = []
    blank_run = 0
    for line in cleaned_lines:
        if line.strip():
            blank_run = 0
            collapsed.append(line)
            continue
        blank_run += 1
        if blank_run <= 1:
            collapsed.append("")
    return "\n".join(collapsed).strip()


def resolve_conclusion_text(
    *, conclusion_code: str, custom_text: str | None
) -> str:
    """Texto por extenso da conclusão principal escolhida."""

    option = CONCLUSIONS_BY_CODE.get(conclusion_code)
    if option is None:
        raise ConclusionCatalogError(
            "conclusao_desconhecida",
            "Conclusão fora do catálogo aprovado.",
        )
    if option.code != CONCLUSION_CUSTOM_CODE:
        if custom_text is not None and _normalize_text(custom_text):
            raise ConclusionCatalogError(
                "texto_personalizado_inesperado",
                "Texto personalizado só é aceito na conclusão Personalizado.",
            )
        return option.full_text
    normalized = _normalize_text(custom_text or "")
    if len(normalized) < 3:
        raise ConclusionCatalogError(
            "texto_personalizado_ausente",
            "A conclusão personalizada exige texto escrito pela médica.",
        )
    if len(normalized) > MAX_CUSTOM_CONCLUSION_CHARS:
        raise ConclusionCatalogError(
            "texto_personalizado_longo",
            "A conclusão personalizada excede o tamanho permitido.",
        )
    return normalized


def resolve_bronchodilator_text(
    *, bronchodilator_code: str | None, has_post_bd: bool
) -> str:
    """Frase complementar pós-BD, já validada contra a fase do exame."""

    if bronchodilator_code is None:
        if has_post_bd:
            raise ConclusionCatalogError(
                "complemento_bd_obrigatorio",
                "Exame com fase pós-broncodilatador exige um complemento.",
            )
        return ""
    option = BRONCHODILATOR_BY_CODE.get(bronchodilator_code)
    if option is None:
        raise ConclusionCatalogError(
            "complemento_bd_desconhecido",
            "Complemento pós-broncodilatador fora do catálogo aprovado.",
        )
    if not has_post_bd and option.code != BD_NOT_PERFORMED_CODE:
        raise ConclusionCatalogError(
            "complemento_bd_incompativel",
            "Este exame não possui fase pós-broncodilatador.",
        )
    return option.full_text


def compose_default_conclusion_text(
    *,
    conclusion_code: str,
    custom_text: str | None,
    bronchodilator_code: str | None,
    has_post_bd: bool,
) -> str:
    """Sugestão inicial montada pelo servidor a partir dos códigos.

    A médica pode editar livremente o resultado antes de assinar; este texto
    é apenas o ponto de partida determinístico do editor.
    """

    conclusion = resolve_conclusion_text(
        conclusion_code=conclusion_code, custom_text=custom_text
    )
    complement = resolve_bronchodilator_text(
        bronchodilator_code=bronchodilator_code, has_post_bd=has_post_bd
    )
    if not complement:
        return conclusion
    return f"{conclusion}\n{complement}"


def normalize_final_text(value: str) -> str:
    """Texto efetivamente assinado, validado em tamanho e conteúdo."""

    normalized = _normalize_text(value)
    if len(normalized) < 3:
        raise ConclusionCatalogError(
            "texto_final_ausente",
            "O texto do laudo não pode ficar vazio.",
        )
    if len(normalized) > MAX_FINAL_TEXT_CHARS:
        raise ConclusionCatalogError(
            "texto_final_longo",
            "O texto do laudo excede o tamanho permitido.",
        )
    return normalized


def normalize_observations(value: str | None) -> str | None:
    """Observações complementares — opcionais, nunca inventadas."""

    if value is None:
        return None
    normalized = _normalize_text(value)
    if not normalized:
        return None
    if len(normalized) > MAX_OBSERVATIONS_CHARS:
        raise ConclusionCatalogError(
            "observacoes_longas",
            "As observações complementares excedem o tamanho permitido.",
        )
    return normalized


def catalog_payload(*, has_post_bd: bool) -> dict:
    """Payload do catálogo para o workspace médico.

    Devolve rótulo curto (botão) e texto por extenso (tooltip/prévia). Não
    contém dado de paciente nem qualquer inferência automática.
    """

    return {
        "conclusoes": [
            {
                "codigo": option.code,
                "rotulo": option.short_label,
                "texto": option.full_text,
                "grupo": option.group,
                "personalizado": option.code == CONCLUSION_CUSTOM_CODE,
            }
            for option in CONCLUSION_OPTIONS
        ],
        "complementos_bd": [
            {
                "codigo": option.code,
                "rotulo": option.short_label,
                "texto": option.full_text,
                "acrescenta_frase": bool(option.full_text),
            }
            for option in available_bronchodilator_options(
                has_post_bd=has_post_bd
            )
        ],
        "exame_com_pos_bd": has_post_bd,
        "limites": {
            "conclusao_personalizada": MAX_CUSTOM_CONCLUSION_CHARS,
            "observacoes": MAX_OBSERVATIONS_CHARS,
            "texto_final": MAX_FINAL_TEXT_CHARS,
        },
    }
