"""Quanto a SoproLife RECEBE por exame feito numa parceria.

Direção do dinheiro é o ponto inteiro deste módulo. `Partnership.modelo_repasse`
/ `percentual_repasse` / `valor_repasse_fixo` descrevem dinheiro que SAI da
SoproLife para o parceiro e são somados em custo. O que se resolve aqui é o
oposto: receita que ENTRA. Os dois nunca se misturam, e este módulo é o único
lugar do sistema que lê `modelo_recebimento` / `valor_recebido_por_exame`.

Ser o único ponto de leitura é o que deixa a arquitetura preparada para o
override por unidade sem complicar a operação de hoje. A Pastore tem uma
unidade ativa; criar granularidade por unidade agora seria modelar uma exceção
que não existe. Quando ela existir, `resolve_valor_por_exame` já recebe a
unidade e é o único lugar a mudar — nem endpoint, nem snapshot, nem painel
sabem de onde o número veio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Partner, Partnership, PartnerUnit

MONEY_QUANT = Decimal("0.01")

MODELO_INDEFINIDO = "indefinido"
MODELO_VALOR_POR_EXAME = "valor_por_exame"

#: Estados de parceria cuja regra comercial vale para valer. Uma parceria
#: arquivada ou encerrada não deve continuar prevendo receita futura.
STATUS_VIGENTES = frozenset({"ativa", "em_negociacao", "piloto"})

REGRA_NAO_CADASTRADA = "Não inferido; exige confirmação do gestor."


def quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class RegraRecebimento:
    """A regra que vale para uma competência, e de onde ela veio."""

    modelo: str
    valor_por_exame: Decimal | None
    vigencia_inicio: date | None
    origem: str  # "partnership" | "unidade" | "nenhuma"
    partnership_id: str | None = None

    @property
    def cadastrada(self) -> bool:
        return self.modelo == MODELO_VALOR_POR_EXAME and self.valor_por_exame is not None

    @property
    def descricao(self) -> str:
        if not self.cadastrada:
            return REGRA_NAO_CADASTRADA
        return f"R$ {self.valor_por_exame:.2f} por exame (vigente desde {self.vigencia_inicio:%d/%m/%Y})"

    def previsto(self, quantidade: int) -> Decimal | None:
        """Valor previsto para N exames. `None` enquanto não houver regra.

        Nunca devolve 0,00 por falta de regra: zero é uma afirmação sobre
        dinheiro, e a ausência de regra não afirma nada.
        """
        if not self.cadastrada:
            return None
        return quantize(self.valor_por_exame * Decimal(quantidade))


SEM_REGRA = RegraRecebimento(
    modelo=MODELO_INDEFINIDO, valor_por_exame=None, vigencia_inicio=None, origem="nenhuma"
)


def _partnerships_do_parceiro(db: Session, partner: Partner) -> list[Partnership]:
    return db.execute(
        select(Partnership)
        .where(Partnership.partner_id == partner.id)
        .order_by(Partnership.vigencia_inicio, Partnership.public_code)
    ).scalars().all()


def resolve_valor_por_exame(
    db: Session,
    partner: Partner,
    unit: PartnerUnit | None = None,
    competencia: date | None = None,
) -> RegraRecebimento:
    """A regra de recebimento aplicável ao parceiro/unidade numa competência.

    Ordem de precedência — hoje só o segundo degrau existe:

    1. override da unidade (ainda não implementado; ponto de extensão único);
    2. regra da parceria com vigência já iniciada na competência.

    `competencia` é o primeiro dia do mês. Uma regra que começa DENTRO do mês
    ainda vale para aquele mês: o extrato do parceiro é mensal, e recusar o mês
    inteiro por causa do dia deixaria uma competência sem previsão nenhuma.
    """

    # Degrau 1 — override por unidade. Deliberadamente vazio: a Pastore tem
    # uma unidade ativa e uma exceção que não existe não deve virar caminho
    # principal. Quando existir, a leitura entra AQUI e nada mais muda.
    del unit

    candidatas = [
        p for p in _partnerships_do_parceiro(db, partner)
        if p.modelo_recebimento == MODELO_VALOR_POR_EXAME
        and p.valor_recebido_por_exame is not None
        and p.vigencia_inicio is not None
        and p.status in STATUS_VIGENTES
    ]
    if competencia is not None:
        candidatas = [p for p in candidatas if p.vigencia_inicio <= _fim_do_mes(competencia)]
    if not candidatas:
        return SEM_REGRA
    # A mais recente que já começou — regra nova substitui regra velha.
    vigente = max(candidatas, key=lambda p: (p.vigencia_inicio, p.public_code))
    return RegraRecebimento(
        modelo=vigente.modelo_recebimento,
        valor_por_exame=quantize(vigente.valor_recebido_por_exame),
        vigencia_inicio=vigente.vigencia_inicio,
        origem="partnership",
        partnership_id=vigente.id,
    )


def _fim_do_mes(primeiro: date) -> date:
    if primeiro.month == 12:
        proximo = date(primeiro.year + 1, 1, 1)
    else:
        proximo = date(primeiro.year, primeiro.month + 1, 1)
    return date.fromordinal(proximo.toordinal() - 1)
