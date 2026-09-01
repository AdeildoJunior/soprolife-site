"""Operação Pastore: configuração de entrada e fechamento mensal.

O exame não é um recebimento. O único lançamento financeiro possível neste
domínio é o recibo agregado do fechamento, criado depois que gestor confirma
valor, data e forma do pagamento efetivamente recebido.

M26 — um mês pode fechar mais de uma vez. Exames realizados DEPOIS de o mês já
ter sido fechado deixaram de ficar órfãos: se o fechamento daquela competência
ainda está aberto (`incluido`, sem valor conferido), os exames novos entram
nele; se ele já tem valor, sai um fechamento COMPLEMENTAR, com sequência,
valor e recibo próprios. Nenhum preço continua sendo inferido em lugar nenhum.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import allocate_public_code
from ..models import (
    FinancialEntry,
    Partner,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    SpirometryExam,
    User,
)
from ..schemas import (
    PastoreSettlementCreate,
    PastoreSettlementReceive,
    PastoreSettlementUpdate,
)
from ..security import ROLE_GESTOR, ROLE_LEITURA, require_role
from ..serializers import money, ser_partner, ser_partner_unit
from ..services.idempotency import idempotent_create, payload_fingerprint
from ..services.partner_pricing import resolve_valor_por_exame
from ..services.pastore import (
    active_pastore_units,
    attach_eligible_exams,
    canonical_pastore,
    competency_month,
    is_completed_pastore_exam,
    pastore_unit,
    planned_action,
)

router = APIRouter(tags=["pastore"])

STATUS_LABELS = {
    "incluido": "Incluído no fechamento",
    "enviado": "Fechamento enviado",
    "a_receber": "A receber da Pastore",
    "recebido": "Recebido da Pastore",
    "cancelado": "Fechamento cancelado",
}
AGUARDANDO_LABEL = "Aguardando fechamento mensal"
MONEY_QUANT = Decimal("0.01")


def _erro(codigo: str, mensagem: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"codigo": codigo, "mensagem": mensagem})


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _settlement_or_404(
    db: Session, settlement_id: str, partner: Partner | None = None
) -> PartnerSettlement:
    settlement = db.get(PartnerSettlement, settlement_id)
    if settlement is None:
        raise HTTPException(status_code=404, detail="Fechamento não encontrado.")
    partner = partner or canonical_pastore(db)
    if settlement.partner_id != partner.id:
        raise _erro(
            "fechamento_nao_pastore",
            "O fechamento informado não pertence à Pastore canônica.",
            409,
        )
    return settlement


def _receipt(db: Session, settlement_id: str) -> FinancialEntry | None:
    return db.execute(
        select(FinancialEntry).where(
            FinancialEntry.partner_settlement_id == settlement_id
        )
    ).scalar_one_or_none()


def _items(db: Session, settlement_id: str) -> list[PartnerSettlementItem]:
    return db.execute(
        select(PartnerSettlementItem)
        .where(PartnerSettlementItem.settlement_id == settlement_id)
        .order_by(PartnerSettlementItem.created_at, PartnerSettlementItem.id)
    ).scalars().all()


def _serialize_settlement(
    db: Session,
    settlement: PartnerSettlement,
    partner: Partner | None = None,
    unit: PartnerUnit | None = None,
) -> dict:
    partner = partner or db.get(Partner, settlement.partner_id)
    unit = unit or db.get(PartnerUnit, settlement.partner_unit_id)
    item_rows = _items(db, settlement.id)
    exam_codes = []
    for item in item_rows:
        exam = db.get(SpirometryExam, item.spirometry_exam_id)
        exam_codes.append(exam.public_code if exam else None)
    receipt = _receipt(db, settlement.id)
    # M26.3 — o previsto é DERIVADO, nunca gravado. Uma coluna guardaria o
    # número do dia em que alguém a escreveu e envelheceria em silêncio a cada
    # exame novo; derivar do par (quantidade de itens × regra vigente) faz o
    # recálculo acontecer sozinho, sem job e sem clique.
    #
    # `valor_total` continua significando só uma coisa: o valor CONFERIDO
    # contra o extrato do parceiro. Previsto e conferido nunca se sobrescrevem.
    regra = resolve_valor_por_exame(db, partner, unit, settlement.competencia)
    previsto = regra.previsto(len(item_rows))
    return {
        "id": settlement.id,
        "partner": {
            "id": partner.id,
            "public_code": partner.public_code,
            "nome": partner.nome,
        } if partner else None,
        "unidade": {
            "id": unit.id,
            "public_code": unit.public_code,
            "nome": unit.nome,
        } if unit else None,
        "competencia": (
            settlement.competencia.strftime("%Y-%m")
            if settlement.competencia else None
        ),
        "sequencia": settlement.sequencia,
        "complementar": settlement.sequencia > 1,
        "titulo": (
            f"Fechamento {settlement.competencia:%Y-%m}"
            if settlement.competencia else "Fechamento"
        ) + (
            f" — complementar {settlement.sequencia}"
            if settlement.sequencia > 1 else ""
        ),
        "periodo_inicio": (
            settlement.periodo_inicio.isoformat() if settlement.periodo_inicio else None
        ),
        "periodo_fim": (
            settlement.periodo_fim.isoformat() if settlement.periodo_fim else None
        ),
        "valor_total": money(settlement.valor_total),
        "valor_previsto": money(previsto) if previsto is not None else None,
        "valor_por_exame": money(regra.valor_por_exame) if regra.cadastrada else None,
        "regra_recebimento": regra.descricao,
        # O que a Pastore deve por este fechamento agora: o conferido quando
        # existe, o previsto enquanto não existe. É este número que o painel
        # soma em "A receber" — e ele some assim que o recibo nasce.
        "valor_em_aberto": (
            None if settlement.status in {"recebido", "cancelado"}
            else money(settlement.valor_total if settlement.valor_total is not None else previsto)
            if (settlement.valor_total is not None or previsto is not None) else None
        ),
        "status": settlement.status,
        "status_label": STATUS_LABELS.get(settlement.status, settlement.status),
        "data_envio": settlement.data_envio.isoformat() if settlement.data_envio else None,
        "observacao": settlement.observacao,
        "itens": {
            "total": len(item_rows),
            "exames_public_codes": [code for code in exam_codes if code],
        },
        "recebimento": {
            "financial_entry_id": receipt.id,
            "public_code": receipt.public_code,
            "valor": money(receipt.valor),
            "data_recebimento": (
                receipt.data_recebimento.isoformat() if receipt.data_recebimento else None
            ),
            "forma_pagamento": receipt.forma_pagamento,
        } if receipt else None,
    }


@router.get("/pastore/configuracao-atendimento")
def attendance_configuration(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    partner = canonical_pastore(db)
    units = active_pastore_units(db, partner)
    return {
        "partner": ser_partner(partner),
        "unidades": [ser_partner_unit(unit) for unit in units],
        "modalidade": {"valor": "clinica_parceira", "rotulo": "Clínica parceira"},
        "origem": partner.nome,
        "unidade_unica": len(units) == 1,
    }


@router.get("/pastore/fechamentos")
def list_monthly_settlements(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    partner = canonical_pastore(db)
    units = active_pastore_units(db, partner)
    settlements = db.execute(
        select(PartnerSettlement)
        .where(PartnerSettlement.partner_id == partner.id)
        .order_by(
            PartnerSettlement.competencia.desc(),
            PartnerSettlement.created_at.desc(),
        )
    ).scalars().all()
    linked_exam_ids = set(
        db.execute(select(PartnerSettlementItem.spirometry_exam_id)).scalars().all()
    )
    awaiting = []
    groups: dict[tuple[str, str], dict] = {}
    for unit in units:
        exams = db.execute(
            select(SpirometryExam)
            .where(
                SpirometryExam.partner_id == partner.id,
                SpirometryExam.partner_unit_id == unit.id,
            )
            .order_by(SpirometryExam.data_exame, SpirometryExam.public_code)
        ).scalars().all()
        for exam in exams:
            if not is_completed_pastore_exam(exam) or exam.id in linked_exam_ids:
                continue
            competencia = exam.data_exame.strftime("%Y-%m")
            awaiting.append({
                "id": exam.id,
                "public_code": exam.public_code,
                "data_exame": exam.data_exame.isoformat(),
                "status_exame": exam.status,
                "estado_fechamento": AGUARDANDO_LABEL,
                "partner_unit_id": unit.id,
                "unidade": unit.nome,
                "competencia": competencia,
            })
            key = (unit.id, competencia)
            group = groups.setdefault(key, {
                "partner_unit_id": unit.id,
                "unidade": unit.nome,
                "competencia": competencia,
                "quantidade": 0,
            })
            group["quantidade"] += 1
    # M26 — o painel precisa dizer ANTES do clique o que vai acontecer. Um mês
    # que já fechou e já tem valor conferido não reabre: os exames que
    # sobraram viram um complementar, e o operador merece saber disso na tela
    # em vez de descobrir pelo resultado.
    for group in groups.values():
        unit_id, competencia = group["partner_unit_id"], group["competencia"]
        da_competencia = [
            row for row in settlements
            if row.partner_unit_id == unit_id
            and row.competencia is not None
            and row.competencia.strftime("%Y-%m") == competencia
        ]
        group["fechamentos_existentes"] = len(da_competencia)
        group["acao_prevista"] = planned_action(da_competencia)
        group["acao_rotulo"] = {
            "criar": "Criar fechamento",
            "incorporar": "Incluir no fechamento aberto",
            "complementar": (
                f"Criar fechamento complementar {len(da_competencia) + 1}"
            ),
        }[group["acao_prevista"]]

    serialized = [_serialize_settlement(db, row, partner) for row in settlements]
    # M26.3 — "a receber" deixou de significar só o que já foi conferido. O
    # exame realizado gera dívida do parceiro no instante em que acontece, e
    # o painel precisa mostrar esse dinheiro antes do extrato chegar. O que
    # NÃO muda: nada disso entra em "recebido" sem confirmação do gestor.
    em_aberto = sum(
        (Decimal(row["valor_em_aberto"]) for row in serialized
         if row["valor_em_aberto"] is not None),
        Decimal("0"),
    )
    regra_atual = resolve_valor_por_exame(db, partner, units[0] if units else None)
    indicators = {
        "aguardando_fechamento": len(awaiting),
        "fechamento_em_aberto": sum(
            row.status in {"incluido", "enviado"} for row in settlements
        ),
        "a_receber": sum(row.status == "a_receber" for row in settlements),
        "recebido": sum(row.status == "recebido" for row in settlements),
        "valor_a_receber_confirmado": money(sum(
            (row.valor_total or Decimal("0"))
            for row in settlements if row.status == "a_receber"
        )),
        "valor_recebido": money(sum(
            (row.valor_total or Decimal("0"))
            for row in settlements if row.status == "recebido"
        )),
        # Conferido quando existe, previsto enquanto não existe — o total que
        # a Pastore deve hoje, sem recibo nenhum.
        "valor_em_aberto": money(em_aberto),
        "exames_em_fechamento": sum(row["itens"]["total"] for row in serialized),
    }
    return {
        "partner": ser_partner(partner),
        "unidades_ativas": [ser_partner_unit(unit) for unit in units],
        "indicadores": indicators,
        "elegiveis": awaiting,
        "grupos_elegiveis": list(groups.values()),
        "fechamentos": serialized,
        "regra_valor": regra_atual.descricao,
        "regra_cadastrada": regra_atual.cadastrada,
        "valor_por_exame": (
            money(regra_atual.valor_por_exame) if regra_atual.cadastrada else None
        ),
    }


@router.post("/pastore/fechamentos", status_code=201)
def create_monthly_settlement(
    payload: PastoreSettlementCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    partner = canonical_pastore(db)
    unit = pastore_unit(db, payload.partner_unit_id, partner)
    competencia = competency_month(payload.competencia)
    try:
        attachment = attach_eligible_exams(
            db, partner, unit, competencia, payload.observacao
        )
    except ValueError:
        raise _erro(
            "fechamento_sem_exames_elegiveis",
            "Não há exames Pastore concluídos elegíveis nesta unidade e competência.",
        ) from None
    except IntegrityError:
        db.rollback()
        raise _erro(
            "conflito_fechamento_mensal",
            "O fechamento ou um de seus exames foi incluído concorrentemente.",
            409,
        ) from None
    settlement, exams, acao = attachment.settlement, attachment.exams, attachment.acao
    audit(
        db,
        "pastore.fechamento_criado" if acao == "criado"
        else "pastore.fechamento_itens_incorporados",
        "partner_settlements", settlement.id,
        user.id, request.state.request_id,
        {
            "status": settlement.status,
            "total": len(exams),
            "sequencia": settlement.sequencia,
        },
    )
    db.commit()
    data = _serialize_settlement(db, settlement, partner, unit)
    data["acao"] = acao
    data["exames_adicionados"] = len(exams)
    return data


@router.patch("/pastore/fechamentos/{settlement_id}")
def update_monthly_settlement(
    settlement_id: str,
    payload: PastoreSettlementUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    partner = canonical_pastore(db)
    settlement = _settlement_or_404(db, settlement_id, partner)
    if settlement.status == "recebido":
        raise _erro(
            "fechamento_ja_recebido",
            "Fechamento recebido é imutável; corrija por lançamento auditável separado.",
            409,
        )
    updates = payload.model_dump(exclude_unset=True)
    status_final = updates.get("status", settlement.status)
    valor_final = updates.get("valor_total", settlement.valor_total)
    data_envio_final = updates.get("data_envio", settlement.data_envio)
    if status_final == "a_receber" and valor_final is None:
        raise _erro(
            "a_receber_sem_valor_confirmado",
            "O estado 'A receber da Pastore' exige valor mensal confirmado.",
        )
    if status_final == "enviado" and data_envio_final is None:
        raise _erro(
            "fechamento_enviado_sem_data",
            "Fechamento enviado exige data_envio.",
        )
    if "valor_total" in updates and updates["valor_total"] is not None:
        updates["valor_total"] = _q(updates["valor_total"])
    for field, value in updates.items():
        setattr(settlement, field, value)
    audit(
        db, "pastore.fechamento_atualizado", "partner_settlements", settlement.id,
        user.id, request.state.request_id,
        {"status": settlement.status, "campos": list(updates)},
    )
    db.commit()
    return _serialize_settlement(db, settlement, partner)


@router.post("/pastore/fechamentos/{settlement_id}/receber", status_code=201)
def receive_monthly_settlement(
    settlement_id: str,
    payload: PastoreSettlementReceive,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    partner = canonical_pastore(db)
    settlement = _settlement_or_404(db, settlement_id, partner)
    existing = _receipt(db, settlement.id)
    if existing is not None:
        if existing.idempotency_key == payload.idempotency_key:
            if existing.idempotency_fingerprint != payload_fingerprint(
                payload.model_dump(mode="json")
            ):
                raise _erro(
                    "idempotencia_payload_divergente",
                    "A chave de idempotência já foi usada com outro recebimento.",
                    409,
                )
            data = _serialize_settlement(db, settlement, partner)
            data["idempotente"] = True
            return data
        raise _erro(
            "recibo_fechamento_duplicado",
            "Este fechamento já possui um recibo financeiro.",
            409,
        )
    if settlement.status == "cancelado":
        raise _erro(
            "fechamento_cancelado",
            "Fechamento cancelado não pode ser recebido.",
            409,
        )
    confirmed = _q(payload.valor_confirmado)
    if settlement.valor_total is not None and _q(settlement.valor_total) != confirmed:
        raise _erro(
            "valor_recebido_diverge_fechamento",
            "O valor recebido diverge do valor mensal confirmado.",
            409,
        )
    unit = db.get(PartnerUnit, settlement.partner_unit_id)

    def factory(key, fingerprint):
        entry = FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo="receita",
            categoria="Recebimento de parceiro",
            descricao=(
                f"Fechamento Pastore {settlement.competencia:%Y-%m}"
                f" — {unit.public_code if unit else 'unidade'}"
            ),
            valor=confirmed,
            moeda="BRL",
            data_competencia=settlement.competencia,
            data_competencia_original=settlement.competencia.isoformat(),
            data_competencia_precisao="dia",
            data_competencia_dia_assumido=False,
            data_recebimento=payload.data_recebimento,
            status="Recebido",
            forma_pagamento=payload.forma_pagamento,
            origem_preco="Parceria",
            partner_settlement_id=settlement.id,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        db.add(entry)
        db.flush()
        return entry

    entry, existed_by_key = idempotent_create(
        db,
        FinancialEntry,
        payload.idempotency_key,
        payload.model_dump(mode="json"),
        factory,
    )
    if existed_by_key and entry.partner_settlement_id != settlement.id:
        db.rollback()
        raise _erro(
            "idempotencia_outro_fechamento",
            "A chave de idempotência já pertence a outro lançamento.",
            409,
        )
    settlement.valor_total = confirmed
    settlement.status = "recebido"
    audit(
        db, "pastore.fechamento_recebido", "partner_settlements", settlement.id,
        user.id, request.state.request_id,
        {"status": settlement.status, "public_code": entry.public_code},
    )
    db.commit()
    return _serialize_settlement(db, settlement, partner, unit)
