"""Regras canônicas da operação Pastore.

Pastore identifica o parceiro do atendimento, nunca a pessoa. Toda resolução
é fail-closed: exatamente um parceiro canônico não arquivado e uma unidade
ativa pertencente a ele.
"""

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    SpirometryExam,
)


def _erro(codigo: str, mensagem: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"codigo": codigo, "mensagem": mensagem})


def canonical_pastore(db: Session) -> Partner:
    rows = db.execute(
        select(Partner).where(
            Partner.arquivado.is_(False),
            func.lower(func.trim(Partner.nome)) == "pastore",
        )
    ).scalars().all()
    if len(rows) != 1:
        raise _erro(
            "pastore_canonica_ambigua",
            "É necessário existir exatamente um parceiro Pastore canônico não arquivado.",
            409,
        )
    return rows[0]


def active_pastore_units(db: Session, partner: Partner | None = None) -> list[PartnerUnit]:
    partner = partner or canonical_pastore(db)
    return db.execute(
        select(PartnerUnit)
        .where(PartnerUnit.partner_id == partner.id, PartnerUnit.ativo.is_(True))
        .order_by(PartnerUnit.nome, PartnerUnit.public_code)
    ).scalars().all()


def pastore_unit(db: Session, unit_id: str, partner: Partner | None = None) -> PartnerUnit:
    partner = partner or canonical_pastore(db)
    unit = db.get(PartnerUnit, unit_id)
    if unit is None or unit.partner_id != partner.id or not unit.ativo:
        raise _erro(
            "unidade_pastore_invalida",
            "A unidade precisa ser uma unidade Pastore ativa.",
        )
    return unit


def is_completed_pastore_exam(exam: SpirometryExam) -> bool:
    if exam.data_exame is None:
        return False
    normalized = (exam.status or "").strip().casefold()
    return normalized not in {
        "",
        "aguardando",
        "agendada",
        "cancelado",
        "cancelada",
        "remarcado",
        "remarcada",
        "não compareceu",
        "nao compareceu",
    }


def competency_month(raw: str) -> date:
    year, month = (int(part) for part in raw.split("-", 1))
    return date(year, month, 1)


def month_end(first: date) -> date:
    if first.month == 12:
        next_month = date(first.year + 1, 1, 1)
    else:
        next_month = date(first.year, first.month + 1, 1)
    return date.fromordinal(next_month.toordinal() - 1)


# --------------------------------------------------------------- fechamento
#
# M26.2 — a regra de "quais exames ainda podem fechar e onde eles entram" era
# privada do router. O script de reconciliação precisa da MESMA regra, e uma
# segunda implementação seria uma segunda verdade: bastaria uma divergir para
# o script criar vínculo que o painel considera impossível, ou vice-versa.
# Endpoint e script passam a chamar estas funções.


def eligible_exams(
    db: Session, partner: Partner, unit: PartnerUnit, competencia: date
) -> list[SpirometryExam]:
    """Exames concluídos do mês que ainda não pertencem a fechamento algum.

    O filtro por vínculo é feito AQUI, na consulta, não depois. Antes da M26 a
    seleção trazia o mês inteiro e o endpoint rejeitava o conjunto todo assim
    que encontrava um exame já vinculado; bastava um fechamento parcial para
    que nenhum exame posterior daquele mês pudesse ser fechado nunca mais.
    """

    candidates = db.execute(
        select(SpirometryExam)
        .where(
            SpirometryExam.partner_id == partner.id,
            SpirometryExam.partner_unit_id == unit.id,
            SpirometryExam.data_exame >= competencia,
            SpirometryExam.data_exame <= month_end(competencia),
            SpirometryExam.id.not_in(
                select(PartnerSettlementItem.spirometry_exam_id)
            ),
        )
        .order_by(SpirometryExam.data_exame, SpirometryExam.public_code)
    ).scalars().all()
    return [exam for exam in candidates if is_completed_pastore_exam(exam)]


def settlements_of_competency(
    db: Session, partner: Partner, unit: PartnerUnit, competencia: date
) -> list[PartnerSettlement]:
    return db.execute(
        select(PartnerSettlement)
        .where(
            PartnerSettlement.partner_id == partner.id,
            PartnerSettlement.partner_unit_id == unit.id,
            PartnerSettlement.competencia == competencia,
        )
        .order_by(PartnerSettlement.sequencia)
    ).scalars().all()


def open_settlement(
    settlements: list[PartnerSettlement],
) -> PartnerSettlement | None:
    """O fechamento da competência que ainda pode receber exames.

    "Aberto" exige as DUAS coisas: estado `incluido` e nenhum valor conferido.
    Um fechamento que já declara valor cobre um conjunto conhecido de exames —
    aquele número foi conferido contra o extrato do parceiro. Acrescentar
    exames a ele transformaria um valor verificado em afirmação falsa sobre um
    conjunto maior. Nesse caso o certo é um complementar.
    """

    abertos = [
        row for row in settlements
        if row.status == "incluido" and row.valor_total is None
    ]
    return abertos[-1] if abertos else None


def planned_action(settlements: list[PartnerSettlement]) -> str:
    """`criar` | `incorporar` | `complementar` — o que um fechamento faria agora."""

    if not settlements:
        return "criar"
    if open_settlement(settlements) is not None:
        return "incorporar"
    return "complementar"


@dataclass(frozen=True)
class SettlementAttachment:
    settlement: PartnerSettlement
    exams: list[SpirometryExam]
    acao: str  # "criado" | "incorporado"
    sequencia: int


def attach_eligible_exams(
    db: Session,
    partner: Partner,
    unit: PartnerUnit,
    competencia: date,
    observacao: str | None = None,
) -> SettlementAttachment:
    """Vincula os exames órfãos da competência a um fechamento — SEM valor.

    Não decide preço, não cria lançamento e não toca em `valor_total`. O
    fechamento nasce (ou continua) sem valor: quem confirma quanto a Pastore
    pagou é o gestor, contra o extrato.

    Idempotente por construção: numa segunda passada `eligible_exams` volta
    vazio e a função levanta `ValueError`, sem escrever nada. A unicidade de
    `PartnerSettlementItem.spirometry_exam_id` é o backstop de corrida.

    O chamador é responsável pela trilha de auditoria e pelo commit.
    """

    exams = eligible_exams(db, partner, unit, competencia)
    if not exams:
        raise ValueError("Não há exames Pastore concluídos elegíveis nesta competência.")
    existentes = settlements_of_competency(db, partner, unit, competencia)
    aberto = open_settlement(existentes)

    if aberto is not None:
        # Incorporação: o mês ainda não declarou valor nenhum, então os exames
        # que faltavam pertencem ao fechamento que já está montado. Criar um
        # segundo aqui fragmentaria o mês sem motivo.
        settlement = aberto
        acao = "incorporado"
        if observacao:
            settlement.observacao = observacao
    else:
        settlement = PartnerSettlement(
            partner_id=partner.id,
            partner_unit_id=unit.id,
            competencia=competencia,
            periodo_inicio=competencia,
            periodo_fim=month_end(competencia),
            valor_total=None,
            status="incluido",
            observacao=observacao,
            sequencia=(
                max(row.sequencia for row in existentes) + 1 if existentes else 1
            ),
        )
        db.add(settlement)
        acao = "criado"

    db.flush()
    db.add_all([
        PartnerSettlementItem(
            settlement_id=settlement.id,
            spirometry_exam_id=exam.id,
        )
        for exam in exams
    ])
    db.flush()
    return SettlementAttachment(
        settlement=settlement, exams=exams, acao=acao, sequencia=settlement.sequencia
    )


# ------------------------------------------------- fechamento automático M26.3
#
# O buraco que a M26.2 mediu: 14 exames feitos entre 15 e 29/08 ficaram fora do
# Financeiro porque a criação do fechamento era um clique, e ninguém clicou. A
# correção não é automatizar dinheiro — é automatizar o VÍNCULO. O exame entra
# no fechamento da sua competência assim que existe; quanto a Pastore pagou
# continua sendo o gestor quem confirma contra o extrato.


OBSERVACAO_AUTOMATICA = (
    "Fechamento aberto automaticamente ao registrar exame elegível da "
    "competência. Valor previsto é derivado da regra vigente da parceria; "
    "o valor recebido continua exigindo confirmação do gestor."
)


def settlement_of_exam(db: Session, exam: SpirometryExam) -> PartnerSettlement | None:
    item = db.execute(
        select(PartnerSettlementItem).where(
            PartnerSettlementItem.spirometry_exam_id == exam.id
        )
    ).scalar_one_or_none()
    return db.get(PartnerSettlement, item.settlement_id) if item else None


def ensure_settlement_for_exam(
    db: Session, exam: SpirometryExam
) -> SettlementAttachment | None:
    """Garante que um exame de parceria elegível pertença a um fechamento.

    Devolve `None` — sem escrever nada — quando não há o que fazer: exame sem
    parceiro/unidade, exame ainda não concluído, ou exame que já pertence a um
    fechamento. Esse `None` é o que torna a função segura de chamar em todo
    caminho de escrita de exame, quantas vezes for.

    Idempotência em três camadas: a checagem de vínculo aqui, o `NOT IN` de
    `eligible_exams`, e a unicidade de `PartnerSettlementItem.spirometry_exam_id`
    como backstop de corrida. Um exame nunca entra em dois fechamentos.

    Não decide preço e não cria lançamento. O chamador audita e commita.
    """

    if exam.partner_id is None or exam.partner_unit_id is None:
        return None
    if not is_completed_pastore_exam(exam):
        return None
    if settlement_of_exam(db, exam) is not None:
        return None

    partner = db.get(Partner, exam.partner_id)
    unit = db.get(PartnerUnit, exam.partner_unit_id)
    if partner is None or unit is None or partner.arquivado:
        return None
    # Unidade desativada não deve abrir competência nova sozinha: o exame fica
    # visível como pendente e um humano decide onde ele entra.
    if not unit.ativo:
        return None

    competencia = exam.data_exame.replace(day=1)
    try:
        attachment = attach_eligible_exams(db, partner, unit, competencia)
    except ValueError:
        # Corrida: outro caminho vinculou o exame entre a checagem e agora.
        return None
    if attachment.acao == "criado" and attachment.settlement.observacao is None:
        attachment.settlement.observacao = OBSERVACAO_AUTOMATICA
    return attachment
