"""M26.20 — agrupamento AUDITÁVEL das conclusões personalizadas.

Por que este módulo existe
--------------------------
O gráfico "Resultados registrados" jogava tudo que a médica escreveu à mão
dentro de uma única fatia chamada "Personalizado". Oito laudos, oito
redações diferentes, zero informação. Este módulo divide aquela fatia — e
só ela — em categorias que correspondem LITERALMENTE ao que foi escrito.

O que este módulo NÃO faz (limites aprovados em 07/10/2026)
-----------------------------------------------------------
* NÃO interpreta espirometria, NÃO calcula grau, NÃO sugere conclusão.
* NÃO nomeia distúrbio nem padrão. "Distúrbio obstrutivo" e "padrão
  sugestivo de restrição" têm código próprio no catálogo fechado
  (`report_conclusions.py`); deduzi-los de texto livre seria produzir um
  diagnóstico novo, que é exatamente o que o marco proíbe.
* NÃO transforma sugestão em diagnóstico: "sugiro complementar com volumes
  pulmonares" é conduta, não achado, e é ignorado na classificação.
* NÃO mistura resposta ao broncodilatador com tipo de achado. O complemento
  pós-BD tem código estruturado próprio (`bronchodilator_code_snapshot`) e
  seu próprio gráfico; aqui ele é ignorado.
* NÃO conta achados: uma categoria == um laudo. Um texto que menciona CVF e
  VEF1 cai em UMA categoria ("Redução de CVF e VEF1"), nunca em duas.

A regra, em uma frase: a categoria é o nome do que a médica escreveu, não
uma leitura do que aquilo significa. Tudo que não bate explicitamente fica
em "Não classificável com segurança" — o bucket nunca é esvaziado por
palpite.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

NOT_CLASSIFIABLE = "nao_classificavel"


@dataclass(frozen=True)
class CustomConclusionGroup:
    """Uma categoria do detalhamento.

    ``descricao`` explica O QUE ESTÁ SENDO CONTADO (vai para o tooltip);
    ``criterio`` descreve a correspondência textual exigida (vai para a
    tabela "Ver conclusões"). Nenhum dos dois afirma diagnóstico.
    """

    chave: str
    rotulo: str
    descricao: str
    criterio: str


# Termos de redução aceitos. Lista fechada e deliberadamente curta: só
# flexões do mesmo radical que a médica efetivamente usa. "alteração",
# "comprometimento" e afins NÃO entram — seriam leitura nossa.
_REDUCTION = re.compile(
    r"\b(reducao|reducoes|reduzido|reduzida|reduzidos|reduzidas|diminuicao)\b"
)
# Negação em qualquer ponto do texto derruba a classificação inteira. É mais
# conservador do que procurar a negação só antes do termo: um texto com
# "sem redução" em qualquer lugar vai para o bucket residual em vez de ser
# contado como redução por causa de outra frase.
_NEGATION = re.compile(
    r"\b(sem\s+reducao|sem\s+reducoes|sem\s+diminuicao"
    r"|ausencia\s+de\s+reducao|nao\s+houve\s+reducao)\b"
)
_CVF = re.compile(r"\bcvf\b")
_VEF1 = re.compile(r"\bvef\s*-?\s*1\b")

_COUNTED = (
    "Quantidade de laudos em que essa conclusão foi explicitamente "
    "registrada pela médica."
)

GROUPS: tuple[CustomConclusionGroup, ...] = (
    CustomConclusionGroup(
        "reducao_cvf_vef1",
        "Redução de CVF e VEF1",
        f"{_COUNTED} O texto cita redução e nomeia os dois parâmetros.",
        "Termo de redução + CVF + VEF1 no texto da conclusão.",
    ),
    CustomConclusionGroup(
        "reducao_cvf",
        "Redução isolada de CVF",
        f"{_COUNTED} O texto cita redução de CVF e não menciona VEF1.",
        "Termo de redução + CVF, sem VEF1 no texto da conclusão.",
    ),
    CustomConclusionGroup(
        "reducao_vef1",
        "Redução isolada de VEF1",
        f"{_COUNTED} O texto cita redução de VEF1 e não menciona CVF.",
        "Termo de redução + VEF1, sem CVF no texto da conclusão.",
    ),
    CustomConclusionGroup(
        NOT_CLASSIFIABLE,
        "Não classificável com segurança",
        "Laudos cujo texto não corresponde explicitamente a nenhuma "
        "categoria acima. Nenhuma classificação é atribuída a eles.",
        "Nenhuma correspondência explícita, ou presença de negação "
        "(\"sem redução\", \"ausência de redução\").",
    ),
)

GROUPS_BY_KEY = {group.chave: group for group in GROUPS}

# Trechos que descrevem conduta ou fase do exame. Ficam registrados aqui
# para deixar explícito que são ignorados de propósito — não participam de
# nenhum critério e não criam categoria nenhuma.
IGNORED_IN_CLASSIFICATION = (
    "volumes pulmonares / complementar — conduta sugerida, não achado",
    "prova broncodilatadora — tem código estruturado e gráfico próprios",
)


def normalize(text: str | None) -> str:
    """Minúsculas, sem acento, espaços colapsados.

    Só isso: nada é truncado, traduzido nem reescrito. A normalização existe
    para que "Redução de CVF e Vef1" e "reducao de cvf e vef1" caiam na
    mesma categoria — diferença de digitação não é diferença clínica.
    """

    if not text:
        return ""
    folded = unicodedata.normalize("NFD", text)
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", folded).strip().lower()


def classify(text: str | None) -> str:
    """Chave da categoria de um texto de conclusão personalizada.

    Retorna sempre uma chave de `GROUPS`; na dúvida, `NOT_CLASSIFIABLE`.
    """

    normalized = normalize(text)
    if not normalized:
        return NOT_CLASSIFIABLE
    if _NEGATION.search(normalized):
        return NOT_CLASSIFIABLE
    if not _REDUCTION.search(normalized):
        return NOT_CLASSIFIABLE
    has_cvf = bool(_CVF.search(normalized))
    has_vef1 = bool(_VEF1.search(normalized))
    if has_cvf and has_vef1:
        return "reducao_cvf_vef1"
    if has_cvf:
        return "reducao_cvf"
    if has_vef1:
        return "reducao_vef1"
    return NOT_CLASSIFIABLE


def payload(counter, *, total: int) -> dict:
    """Bloco `personalizados` da resposta de estatísticas.

    `total` é a contagem de laudos personalizados vigentes; as quantidades
    das categorias somam exatamente esse total, por construção. Nenhum texto
    escrito pela médica entra nesta resposta — só rótulo, critério e número.
    """

    return {
        "total": total,
        "categorias": [
            {
                "chave": group.chave,
                "rotulo": group.rotulo,
                "descricao": group.descricao,
                "criterio": group.criterio,
                "quantidade": counter[group.chave],
                "percentual": (
                    round(counter[group.chave] * 100 / total, 1) if total else 0.0
                ),
            }
            for group in GROUPS
        ],
        "ignorado_na_classificacao": list(IGNORED_IN_CLASSIFICATION),
    }
