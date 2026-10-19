"""Catálogo de modalidades de EEG — fonte única do painel.

PROVENIÊNCIA
------------
Os sete códigos e nomes vêm do conteúdo REAL já publicado pela EEGLife, em
`~/eeglife-site/src/content/modalidades.mjs` (lido apenas; aquele repositório
não foi alterado). Os `codigo` são os mesmos `slug` de lá, de propósito: já são
estáveis, já foram revisados e evitam inventar um segundo vocabulário para a
mesma coisa.

Duas modalidades do escopo de laudo contratado ficam DE FORA, pela mesma razão
registrada no site: EEG à beira-leito e EEG para morte encefálica são exames
hospitalares, e oferecê-los num serviço ambulatorial sugeriria capacidade que a
EEGLife não tem.

O QUE ESTE CATÁLOGO NÃO AFIRMA
------------------------------
Que a EEGLife realiza estas modalidades hoje. O site mantém o interruptor
`disponibilidadeAnunciada = false` justamente porque equipamento, equipe e
local seguem pendentes. Aqui o catálogo serve para CLASSIFICAR um exame
registrado no painel, não para anunciar serviço.

`duracao_minutos_aproximada` só existe onde a duração está declarada em
números no conteúdo de origem. Onde o texto diz "pouco maior" ou "conforme o
pedido médico", o campo fica `None` — estimar viraria número inventado.

Nenhum preço, repasse ou prazo aparece aqui: regra comercial do EEG é fase
posterior.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModalidadeEeg:
    codigo: str
    nome: str
    tambem_chamado: str | None
    duracao_minutos_aproximada: int | None
    preparo: str | None
    ativo: bool


# Ordem: do registro padrão para os mais longos, e só depois a diferença de
# método de análise — a mesma do conteúdo de origem.
MODALIDADES: tuple[ModalidadeEeg, ...] = (
    ModalidadeEeg(
        codigo="vigilia",
        nome="EEG de vigília",
        tambem_chamado="EEG de rotina, EEG basal, EEG convencional",
        duracao_minutos_aproximada=None,
        preparo="Cabelo limpo e seco, sem creme ou gel. Alimentação normal.",
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="fotoestimulacao",
        nome="EEG com fotoestimulação",
        tambem_chamado="EEG com estímulo luminoso intermitente (ELI)",
        duracao_minutos_aproximada=None,
        preparo=(
            "Mesmo preparo do EEG de vigília. Avisar antes se houver "
            "fotossensibilidade conhecida ou crises desencadeadas por luz."
        ),
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="prolongado-30-60",
        nome="EEG prolongado de 30 minutos a 1 hora",
        tambem_chamado=None,
        duracao_minutos_aproximada=None,
        preparo="Conforme o pedido médico. Privação de sono somente com orientação médica expressa.",
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="prolongado-2h",
        nome="EEG prolongado de 2 horas",
        tambem_chamado=None,
        duracao_minutos_aproximada=120,
        preparo="Conforme o pedido médico. Privação de sono somente com orientação médica expressa.",
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="digital-por-hora",
        nome="EEG digital prolongado por hora",
        tambem_chamado=None,
        duracao_minutos_aproximada=None,
        preparo="Conforme o pedido médico.",
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="mapeamento",
        nome="EEG com mapeamento cerebral",
        tambem_chamado="qEEG",
        duracao_minutos_aproximada=None,
        preparo="Mesmo preparo do registro de base.",
        ativo=True,
    ),
    ModalidadeEeg(
        codigo="mapeamento-fotoestimulacao",
        nome="EEG com mapeamento cerebral e fotoestimulação",
        tambem_chamado=None,
        duracao_minutos_aproximada=None,
        preparo=(
            "Mesmo preparo do registro de base. Avisar antes se houver "
            "fotossensibilidade conhecida."
        ),
        ativo=True,
    ),
)

_POR_CODIGO = {m.codigo: m for m in MODALIDADES}

CODIGOS_VALIDOS = frozenset(_POR_CODIGO)


def obter(codigo: str | None) -> ModalidadeEeg | None:
    """Modalidade pelo código interno, ou None se desconhecida."""
    if not codigo:
        return None
    return _POR_CODIGO.get(codigo)


def modalidade_ativa(codigo: str | None) -> bool:
    """Só aceita código conhecido E ativo — default-fechado."""
    modalidade = obter(codigo)
    return bool(modalidade and modalidade.ativo)


def listar_ativas() -> list[ModalidadeEeg]:
    return [m for m in MODALIDADES if m.ativo]
