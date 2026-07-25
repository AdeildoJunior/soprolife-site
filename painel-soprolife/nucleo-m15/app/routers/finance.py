"""Financeiro: fonte canônica, sem PII, Decimal, vinculado só por IDs técnicos.

Hardening M15.1A: idempotência atômica também em repasses; coerência
parceiro/parceria/encaminhamento/acerto validada na transação; transições
de status de repasse controladas; papel gestor obrigatório para mutações.
"""

from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..dates import parse_incomplete_date
from ..db import get_db
from ..domain import ensure_sem_pcmso
from ..ids import allocate_public_code
from ..models import (
    Consultation,
    FinancialEntry,
    Partner,
    PartnerReferral,
    PartnerSettlement,
    PartnerTransfer,
    PartnerUnit,
    Person,
    SpirometryExam,
    User,
)
from ..normalize import normalize_name
from ..pagination import PageParams, paginate
from ..schemas import (
    ConciliacaoLoteRequest,
    FinanceSearch,
    FinancialEntryCreate,
    FinancialEntryUpdate,
    TransferCreate,
)
from ..security import ROLE_GESTOR, ROLE_LEITURA, require_role
from ..serializers import money as _num_str
from ..serializers import ser_financial_entry, ser_transfer
from ..services.idempotency import idempotent_create
from ..services.integrity import (
    ensure_partner_exists,
    ensure_entry_for_referral,
    ensure_financial_links_coherent,
    ensure_partnership_of_partner,
    ensure_referral_of_partner,
    ensure_settlement_of_partner,
)

router = APIRouter(tags=["financeiro"])

MONEY_QUANT = Decimal("0.01")


def _q(value: Decimal | None) -> Decimal | None:
    """Quantização monetária canônica: 2 casas, ROUND_HALF_UP."""
    if value is None:
        return None
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _check_pcmso(db: Session, request: Request, user: User, payload, contexto: str) -> None:
    try:
        ensure_sem_pcmso(payload.model_dump(exclude_none=True), contexto)
    except HTTPException as exc:
        audit(
            db,
            "pcmso.rejeitado",
            user_id=user.id,
            request_id=request.state.request_id,
            detalhes=exc.detail if isinstance(exc.detail, dict) else None,
        )
        db.commit()
        raise


def _entry_context(db: Session, entry: FinancialEntry) -> dict:
    """Contexto derivado SOMENTE por relação técnica (LAN ↔ ESP/CON/ENC).

    O nome do paciente nunca é armazenado no lançamento — é lido da pessoa
    vinculada no momento da exibição, preservando a separação PII/financeiro.
    """
    ctx: dict = {}

    def _person_ctx(person_id: str | None) -> dict | None:
        if not person_id:
            return None
        person = db.get(Person, person_id)
        if not person:
            return None
        return {"public_code": person.public_code, "nome_completo": person.nome_completo}

    if entry.spirometry_exam_id:
        exam = db.get(SpirometryExam, entry.spirometry_exam_id)
        if exam:
            ctx["exame"] = {
                "public_code": exam.public_code,
                "data_exame": exam.data_exame.isoformat() if exam.data_exame else None,
                "status": exam.status,
                "pessoa": _person_ctx(exam.person_id),
            }
    if entry.consultation_id:
        con = db.get(Consultation, entry.consultation_id)
        if con:
            ctx["consulta"] = {
                "public_code": con.public_code,
                "data_consulta": con.data_consulta.isoformat() if con.data_consulta else None,
                "status": con.status,
                "pessoa": _person_ctx(con.person_id),
            }
    if entry.partner_referral_id:
        ref = db.get(PartnerReferral, entry.partner_referral_id)
        if ref:
            ctx["encaminhamento"] = {
                "public_code": ref.public_code,
                "status": ref.status,
                "pessoa": _person_ctx(ref.person_id),
            }
    if entry.partner_settlement_id:
        settlement = db.get(PartnerSettlement, entry.partner_settlement_id)
        if settlement:
            partner = db.get(Partner, settlement.partner_id)
            unit = db.get(PartnerUnit, settlement.partner_unit_id)
            ctx["fechamento_parceiro"] = {
                "partner_public_code": partner.public_code if partner else None,
                "pagador": partner.nome if partner else None,
                "unidade_public_code": unit.public_code if unit else None,
                "competencia": (
                    settlement.competencia.strftime("%Y-%m")
                    if settlement.competencia else None
                ),
            }
    return ctx


@router.get("/lancamentos")
def list_entries(
    tipo: str | None = None,
    status: str | None = None,
    incluir_contexto: bool = False,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(FinancialEntry).order_by(FinancialEntry.created_at.desc())
    if tipo:
        stmt = stmt.where(FinancialEntry.tipo == tipo)
    if status:
        stmt = stmt.where(FinancialEntry.status == status)
    if not incluir_contexto:
        return paginate(db, stmt, params, ser_financial_entry)

    def _ser(entry: FinancialEntry) -> dict:
        data = ser_financial_entry(entry)
        data["contexto"] = _entry_context(db, entry)
        return data

    return paginate(db, stmt, params, _ser)


@router.post("/lancamentos/busca")
def search_entries(
    payload: FinanceSearch,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Busca por corpo POST (nome/código nunca em URL/log): casa código do
    lançamento (LAN-…), código do vínculo (ESP-/CON-/ENC-…) ou nome do
    paciente derivado da relação técnica."""
    q = (payload.q or "").strip()
    stmt = select(FinancialEntry).order_by(FinancialEntry.created_at.desc())
    upper = q.upper()
    if upper.startswith("LAN-"):
        stmt = stmt.where(FinancialEntry.public_code == upper)
    elif upper.startswith("ESP-"):
        stmt = stmt.join(
            SpirometryExam, FinancialEntry.spirometry_exam_id == SpirometryExam.id
        ).where(SpirometryExam.public_code == upper)
    elif upper.startswith("CON-"):
        stmt = stmt.join(
            Consultation, FinancialEntry.consultation_id == Consultation.id
        ).where(Consultation.public_code == upper)
    elif upper.startswith("ENC-"):
        stmt = stmt.join(
            PartnerReferral, FinancialEntry.partner_referral_id == PartnerReferral.id
        ).where(PartnerReferral.public_code == upper)
    elif q:
        term = f"%{normalize_name(q)}%"
        exam_person = (
            select(FinancialEntry.id)
            .join(SpirometryExam, FinancialEntry.spirometry_exam_id == SpirometryExam.id)
            .join(Person, SpirometryExam.person_id == Person.id)
            .where(Person.nome_normalizado.like(term))
        )
        con_person = (
            select(FinancialEntry.id)
            .join(Consultation, FinancialEntry.consultation_id == Consultation.id)
            .join(Person, Consultation.person_id == Person.id)
            .where(Person.nome_normalizado.like(term))
        )
        ref_person = (
            select(FinancialEntry.id)
            .join(PartnerReferral, FinancialEntry.partner_referral_id == PartnerReferral.id)
            .join(Person, PartnerReferral.person_id == Person.id)
            .where(Person.nome_normalizado.like(term))
        )
        stmt = stmt.where(
            FinancialEntry.id.in_(exam_person)
            | FinancialEntry.id.in_(con_person)
            | FinancialEntry.id.in_(ref_person)
        )

    def _ser(entry: FinancialEntry) -> dict:
        data = ser_financial_entry(entry)
        data["contexto"] = _entry_context(db, entry)
        return data

    params = PageParams(pagina=payload.pagina, tamanho=payload.tamanho)
    return paginate(db, stmt, params, _ser)


@router.post("/lancamentos", status_code=201)
def create_entry(
    payload: FinancialEntryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    _check_pcmso(db, request, user, payload, "lancamentos.create")
    ensure_financial_links_coherent(
        db,
        payload.spirometry_exam_id,
        payload.consultation_id,
        payload.partner_referral_id,
    )
    if payload.spirometry_exam_id:
        exam = db.get(SpirometryExam, payload.spirometry_exam_id)
        partner = db.get(Partner, exam.partner_id) if exam and exam.partner_id else None
        if partner and partner.nome.strip().casefold() == "pastore":
            raise HTTPException(status_code=422, detail={
                "codigo": "pagamento_direto_pastore_proibido",
                "mensagem": (
                    "Exame Pastore não aceita lançamento financeiro direto; "
                    "use o fechamento mensal da parceria."
                ),
            })

    def factory(key, fingerprint):
        entry = FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo=payload.tipo,
            categoria=payload.categoria,
            descricao=payload.descricao,
            valor=_q(payload.valor),
            moeda=payload.moeda,
            data_recebimento=payload.data_recebimento,
            status=payload.status,
            forma_pagamento=payload.forma_pagamento,
            origem_preco=payload.origem_preco,
            spirometry_exam_id=payload.spirometry_exam_id,
            consultation_id=payload.consultation_id,
            partner_referral_id=payload.partner_referral_id,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        nd = parse_incomplete_date(payload.data_competencia)
        entry.data_competencia = nd.value
        entry.data_competencia_original = nd.original or None
        entry.data_competencia_precisao = nd.precision
        entry.data_competencia_dia_assumido = nd.day_assumed
        db.add(entry)
        db.flush()
        return entry

    entry, ja_existia = idempotent_create(
        db, FinancialEntry, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_financial_entry(entry)
        data["idempotente"] = True
        return data
    audit(db, "lancamento.criado", "financial_entries", entry.id, user.id,
          request.state.request_id,
          {"public_code": entry.public_code, "tipo": entry.tipo, "status": entry.status})
    db.commit()
    return ser_financial_entry(entry)


# ---------------------------------------------------- conciliação extra-Pastore
#
# M18 — alvo histórico de conciliação bancária: R$ 3.044,79 recebidos em 13
# exames de espirometria próprios da SoproLife fora da Pastore. Este total é
# um controle de fechamento histórico (não um saldo bancário corrente) —
# nunca substitui o lançamento financeiro como fonte de verdade monetária.
TOTAL_ALVO_EXTRA_PASTORE = Decimal("3044.79")


def _exames_extra_pastore(db: Session):
    return db.execute(
        select(SpirometryExam)
        .where(SpirometryExam.partner_id.is_(None))
        .order_by(SpirometryExam.data_exame.asc().nulls_last())
    ).scalars().all()


@router.get("/financeiro/conciliacao/extra-pastore")
def conciliacao_extra_pastore(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    exames = _exames_extra_pastore(db)
    entries_by_exam: dict[str, FinancialEntry] = {}
    if exames:
        exam_ids = [e.id for e in exames]
        rows = db.execute(
            select(FinancialEntry).where(FinancialEntry.spirometry_exam_id.in_(exam_ids))
        ).scalars().all()
        for row in rows:
            entries_by_exam[row.spirometry_exam_id] = row

    conciliados = []
    pendentes = []
    total_vinculado = Decimal("0.00")
    for exam in exames:
        entry = entries_by_exam.get(exam.id)
        person = db.get(Person, exam.person_id)
        item = {
            "exam_id": exam.id,
            "exam_public_code": exam.public_code,
            "person_public_code": person.public_code if person else None,
            "person_nome": person.nome_completo if person else None,
            "data_exame": exam.data_exame.isoformat() if exam.data_exame else None,
            "local_atendimento": exam.local_atendimento,
        }
        if entry:
            total_vinculado += entry.valor
            item.update({
                "status": "conciliado",
                "financial_public_code": entry.public_code,
                "valor": _num_str(entry.valor),
            })
            conciliados.append(item)
        else:
            item.update({"status": "sem_lancamento", "financial_public_code": None, "valor": None})
            pendentes.append(item)

    total_vinculado = total_vinculado.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    total_pendente = (TOTAL_ALVO_EXTRA_PASTORE - total_vinculado).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    return {
        "total_alvo": _num_str(TOTAL_ALVO_EXTRA_PASTORE),
        "total_vinculado": _num_str(total_vinculado),
        "total_pendente": _num_str(total_pendente),
        "total_exames": len(exames),
        "exames_conciliados": len(conciliados),
        "exames_pendentes": len(pendentes),
        "conciliados": conciliados,
        "pendentes": pendentes,
        "rotulo": "Total histórico informado — exames próprios fora da Pastore",
    }


@router.post("/financeiro/conciliacao/extra-pastore/lote", status_code=201)
def conciliar_lote_extra_pastore(
    payload: ConciliacaoLoteRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Concilia N exames extra-Pastore numa única transação atômica.

    Bloqueia o envio se a soma dos itens não bater com o pendente
    recalculado NO SERVIDOR (nunca confia no total exibido no cliente) e
    nunca preenche valores por média — cada item precisa vir com o valor
    exato evidenciado.
    """
    exames = _exames_extra_pastore(db)
    exam_ids = {e.id for e in exames}
    ja_vinculados = {
        row.spirometry_exam_id
        for row in db.execute(
            select(FinancialEntry).where(FinancialEntry.spirometry_exam_id.in_(exam_ids))
        ).scalars().all()
    }

    seen_in_payload: set[str] = set()
    soma = Decimal("0.00")
    for item in payload.itens:
        if item.spirometry_exam_id not in exam_ids:
            raise HTTPException(422, detail={
                "codigo": "exame_fora_do_escopo",
                "mensagem": "spirometry_exam_id não é um exame extra-Pastore conhecido.",
            })
        if item.spirometry_exam_id in ja_vinculados:
            raise HTTPException(409, detail={
                "codigo": "exame_ja_conciliado",
                "mensagem": "Este exame já tem lançamento financeiro vinculado.",
            })
        if item.spirometry_exam_id in seen_in_payload:
            raise HTTPException(422, detail={
                "codigo": "exame_duplicado_no_lote",
                "mensagem": "O mesmo exame aparece mais de uma vez no lote.",
            })
        seen_in_payload.add(item.spirometry_exam_id)
        soma += _q(item.valor)

    soma = soma.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    total_vinculado_atual = Decimal("0.00")
    for row in db.execute(
        select(FinancialEntry).where(FinancialEntry.spirometry_exam_id.in_(exam_ids))
    ).scalars().all():
        total_vinculado_atual += row.valor
    pendente_atual = (TOTAL_ALVO_EXTRA_PASTORE - total_vinculado_atual).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )

    if payload.total_esperado.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP) != pendente_atual:
        raise HTTPException(409, detail={
            "codigo": "total_esperado_desatualizado",
            "mensagem": "O pendente mudou no servidor — recarregue antes de enviar.",
            "pendente_atual": _num_str(pendente_atual),
        })
    if soma != pendente_atual:
        raise HTTPException(422, detail={
            "codigo": "soma_nao_bate",
            "mensagem": "A soma dos itens não bate com o valor pendente.",
            "soma_informada": _num_str(soma),
            "pendente_atual": _num_str(pendente_atual),
        })

    criados = []
    for item in payload.itens:
        entry = FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo="receita",
            categoria="Espirometria",
            descricao=item.descricao,
            valor=_q(item.valor),
            moeda="BRL",
            status=item.status,
            data_recebimento=item.data_recebimento,
            forma_pagamento=item.forma_pagamento,
            origem_preco=item.origem_preco,
            spirometry_exam_id=item.spirometry_exam_id,
        )
        db.add(entry)
        db.flush()
        audit(db, "lancamento.criado", "financial_entries", entry.id, user.id,
              request.state.request_id,
              {"public_code": entry.public_code, "tipo": entry.tipo, "status": entry.status,
               "marcador": "m18_conciliacao_lote"})
        criados.append(ser_financial_entry(entry))

    db.commit()
    return {"criados": criados, "total_conciliado_no_lote": _num_str(soma)}


@router.patch("/lancamentos/{entry_id}")
def update_entry(
    entry_id: str,
    payload: FinancialEntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Atualização operacional do lançamento (status/recebimento) — gestor/admin.

    Valor e tipo são imutáveis: correção monetária é um NOVO lançamento,
    preservando a trilha da fonte financeira canônica.
    """
    entry = db.get(FinancialEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado.")
    _check_pcmso(db, request, user, payload, "lancamentos.update")
    updates = payload.model_dump(exclude_none=True)
    status_final = updates.get("status", entry.status)
    data_receb_final = updates.get("data_recebimento", entry.data_recebimento)
    forma_final = updates.get("forma_pagamento", entry.forma_pagamento)
    if status_final == "Recebido" and data_receb_final is None:
        raise HTTPException(status_code=422, detail={
            "codigo": "recebido_sem_data",
            "mensagem": "Status 'Recebido' exige data_recebimento."})
    if entry.partner_settlement_id and (
        status_final != "Recebido"
        or data_receb_final is None
        or forma_final is None
    ):
        raise HTTPException(status_code=422, detail={
            "codigo": "recibo_fechamento_invalido",
            "mensagem": (
                "Recibo de fechamento deve permanecer Recebido e conservar "
                "data e forma de pagamento."
            ),
        })
    changed = []
    for field, value in updates.items():
        setattr(entry, field, value)
        changed.append(field)
    audit(db, "lancamento.atualizado", "financial_entries", entry.id, user.id,
          request.state.request_id, {"campos": changed, "status": entry.status})
    db.commit()
    return ser_financial_entry(entry)


@router.get("/repasses")
def list_transfers(
    partner_id: str | None = None,
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    stmt = select(PartnerTransfer).order_by(PartnerTransfer.created_at.desc())
    if partner_id:
        stmt = stmt.where(PartnerTransfer.partner_id == partner_id)
    if status:
        stmt = stmt.where(PartnerTransfer.status == status)
    return paginate(db, stmt, params, ser_transfer)


@router.post("/repasses", status_code=201)
def create_transfer(
    payload: TransferCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    ensure_partner_exists(db, payload.partner_id)
    # coerência cruzada: parceria/encaminhamento/acerto do MESMO parceiro
    partnership = ensure_partnership_of_partner(
        db, payload.partnership_id, payload.partner_id
    )
    referral = ensure_referral_of_partner(db, payload.partner_referral_id, payload.partner_id)
    settlement = ensure_settlement_of_partner(db, payload.settlement_id, payload.partner_id)
    if (
        partnership
        and settlement
        and settlement.partnership_id not in (None, partnership.id)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "codigo": "acerto_parceria_incoerente",
                "mensagem": "O acerto pertence a outra parceria do mesmo parceiro.",
            },
        )
    if payload.financial_entry_id:
        entry = db.get(FinancialEntry, payload.financial_entry_id)
        if entry is None:
            raise HTTPException(status_code=422, detail={
                "codigo": "vinculo_inexistente", "mensagem": "Lançamento não encontrado."})
        if referral:
            ensure_entry_for_referral(db, entry.id, referral)
    if payload.status == "pago" and payload.data_pagamento is None:
        raise HTTPException(status_code=422, detail={
            "codigo": "pagamento_sem_data",
            "mensagem": "Repasse pago exige data_pagamento."})

    def factory(key, fingerprint):
        transfer = PartnerTransfer(
            partner_id=payload.partner_id,
            partnership_id=payload.partnership_id,
            partner_referral_id=payload.partner_referral_id,
            settlement_id=payload.settlement_id,
            financial_entry_id=payload.financial_entry_id,
            valor=_q(payload.valor),
            status=payload.status,
            data_prevista=payload.data_prevista,
            data_pagamento=payload.data_pagamento,
            idempotency_key=key,
            idempotency_fingerprint=fingerprint,
        )
        db.add(transfer)
        db.flush()
        return transfer

    transfer, ja_existia = idempotent_create(
        db, PartnerTransfer, payload.idempotency_key,
        payload.model_dump(mode="json"), factory,
    )
    if ja_existia:
        data = ser_transfer(transfer)
        data["idempotente"] = True
        return data
    audit(db, "repasse.criado", "partner_transfers", transfer.id, user.id,
          request.state.request_id, {"status": transfer.status})
    db.commit()
    return ser_transfer(transfer)
