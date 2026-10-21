"""M26.34 — vários exames numa NFS-e só, com um tomador escolhido.

O CASO QUE PEDIU ISSO

Dois exames domiciliares de um casal, no mesmo dia e município, quitados por
UM Pix pela filha. A nota é uma, do total, no CPF dela — que não é paciente de
nenhum dos dois. Pelo caminho `single` sairiam duas notas, cada uma no nome de
um paciente: não a operação que de fato aconteceu.

O QUE ESTE MÓDULO NÃO FAZ

Não reimplementa elegibilidade fiscal. Cada exame continua passando por
`nfse.evaluate()` — a mesma função, as mesmas regras, os mesmos códigos de
bloqueio do fluxo de sempre. O que existe aqui é EXCLUSIVAMENTE o que só faz
sentido quando há mais de um exame: a coerência entre eles, o tomador
escolhido e o total.

AS DUAS TROCAS DE IDENTIDADE FISCAL

Num documento agrupado a identidade que vai na nota é a do TOMADOR, não a do
paciente. Então, dos bloqueios que `evaluate()` devolve por exame, os de
identidade fiscal do paciente (`RECIPIENT_FISCAL_REASONS` — CPF ausente,
dígitos, nome) são descartados e reaplicados ao tomador. O paciente continua
tendo de existir e não estar arquivado: ele é o beneficiário do serviço, só
não é quem paga.

POR QUE PASTORE NÃO AGRUPA

No fluxo PASTORE o valor não é preço ao paciente: é o repasse da parceria
(o campo `valor_recebido_por_exame` dela, lido só por `partner_pricing`), e o
tomador é a clínica. Misturar
isso com uma nota a um terceiro pagador juntaria duas relações comerciais
diferentes no mesmo documento. Fica recusado explicitamente, não por omissão.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (FiscalDocument, FiscalDocumentItem, Person,
                      SpirometryExam)
from . import nfse
from .nfse_national.recipient_identity import (RecipientIdentityError,
                                               assert_production_recipient)
from .nfse_national.service_description import (
    ServiceDescriptionUndetermined, grouped_spirometry_service_description)

# A fonte do valor de uma preparação agrupada, declarada na própria linha
# (ver o CHECK `fiscal_snapshot_source` e a migration b5e2f7a91c38). Definida
# em `nfse` e reexportada aqui: um só literal, nos dois módulos.
GROUPED_AMOUNT_SOURCE = nfse.GROUPED_AMOUNT_SOURCE

MINIMUM_ITEMS = 2

# Por que um grupo não pode ser preparado. Códigos only — nunca nome, CPF ou
# qualquer identificação de pessoa.
GROUP_REASONS = (
    "group_requires_two_or_more_exams",
    "group_exam_duplicated",
    "group_exam_not_found",
    "group_exam_already_billed",
    "group_service_date_divergent",
    "group_service_location_divergent",
    "group_policy_divergent",
    "group_flow_divergent",
    "group_flow_unsupported",
    "group_bronchodilator_divergent",
    "group_amount_undetermined",
    "group_recipient_missing",
    "group_recipient_not_found",
    "group_recipient_archived",
)

# NÃO existe bloqueio para "o tomador é um dos pacientes do grupo", e isso é
# deliberado: um cônjuge que paga os dois exames é tomador legítimo da nota
# inteira, mesmo sendo beneficiário de um deles. Recusar seria inventar uma
# regra fiscal que ninguém pediu.

# Fluxos que admitem nota agrupada a um terceiro pagador.
GROUPABLE_FLOWS = {"DIRECT", "HOME"}


def billed_exam_ids(db: Session, exam_ids, environment: str) -> set[str]:
    """Quais destes exames já ocupam um documento fiscal neste ambiente.

    Lê `fiscal_document_items`, que desde a M26.34 é a trava universal de
    ocupação — vale para os documentos `single` históricos (que ganharam item
    no backfill) e para os agrupados.
    """
    if not exam_ids:
        return set()
    return set(db.scalars(select(FiscalDocumentItem.spirometry_exam_id).where(
        FiscalDocumentItem.spirometry_exam_id.in_(list(exam_ids)),
        FiscalDocumentItem.environment == environment,
    )).all())


def _canonical(exams):
    """Ordem canônica pelo código público do exame.

    Dois pedidos com os mesmos exames em ordem diferente têm de produzir o
    MESMO documento, a mesma chave de idempotência e o mesmo fingerprint —
    senão clicar na ordem inversa criaria um segundo documento para a mesma
    nota, e a trava de ocupação só avisaria depois.
    """
    return sorted(exams, key=lambda e: (e.public_code or "", e.id))


def canonical_exam_ids(db: Session, exam_ids) -> list[str]:
    """Os ids dados, na ordem canônica. Ids inexistentes caem fora."""
    if not exam_ids:
        return []
    exams = db.scalars(select(SpirometryExam).where(
        SpirometryExam.id.in_(list(dict.fromkeys(exam_ids))))).all()
    return [exam.id for exam in _canonical(exams)]


def _unique(values):
    """Valores distintos preservando a ordem, para comparar coerência."""
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def evaluate_group(db: Session, exam_ids: list[str], recipient_person_id: str | None,
                   environment: str, *, for_update: bool = True,
                   document_id: str | None = None) -> dict:
    """O que uma NFS-e agrupada destes exames, para este tomador, seria agora.

    Puramente de leitura. `document_id`, quando dado, é o documento que já
    detém estes exames: os itens dele não contam como "já faturado", senão
    reavaliar o próprio documento o bloquearia.
    """
    reasons: list[str] = []
    ordered = list(exam_ids or [])
    if len(set(ordered)) != len(ordered):
        reasons.append("group_exam_duplicated")
    unique_ids = _unique(ordered)
    if len(unique_ids) < MINIMUM_ITEMS:
        reasons.append("group_requires_two_or_more_exams")

    exams = {e.id: e for e in db.scalars(select(SpirometryExam).where(
        SpirometryExam.id.in_(unique_ids))).all()} if unique_ids else {}
    if len(exams) != len(unique_ids):
        reasons.append("group_exam_not_found")

    found = _canonical(exams.values())

    ocupados = billed_exam_ids(db, [e.id for e in found], environment)
    if document_id is not None:
        proprios = set(db.scalars(select(FiscalDocumentItem.spirometry_exam_id).where(
            FiscalDocumentItem.document_id == document_id)).all())
        ocupados -= proprios
    if ocupados:
        reasons.append("group_exam_already_billed")

    # A elegibilidade de cada exame vem da MESMA função do fluxo single, com
    # uma única diferença, declarada: a identidade fiscal do PACIENTE não
    # entra, porque quem vai na nota é o tomador escolhido. Sem esse flag o
    # valor do exame voltaria None junto com o bloqueio e o grupo seria
    # recusado por "sem valor" — com o valor ali, na receita.
    per_exam = []
    for exam in found:
        data = nfse.evaluate(db, exam.id, environment, for_update=for_update,
                             recipient_identity=False)
        per_exam.append((exam, data, data["blocking_reasons"]))
        reasons.extend(data["blocking_reasons"])

    if found:
        flows = _unique(d["flow"] for _, d, _ in per_exam)
        if len(flows) > 1:
            reasons.append("group_flow_divergent")
        elif flows[0] not in GROUPABLE_FLOWS:
            reasons.append("group_flow_unsupported")
        if len(_unique(d["service_date"] for _, d, _ in per_exam)) > 1:
            reasons.append("group_service_date_divergent")
        if len(_unique(d["service_municipio_ibge"] for _, d, _ in per_exam)) > 1:
            reasons.append("group_service_location_divergent")
        if len(_unique(d["policy_id"] for _, d, _ in per_exam)) > 1:
            reasons.append("group_policy_divergent")
        if len(_unique(e.broncodilatador for e, _, _ in per_exam)) > 1:
            reasons.append("group_bronchodilator_divergent")

    # ---- o tomador escolhido, e a identidade fiscal que vai na nota
    recipient = None
    if not recipient_person_id:
        reasons.append("group_recipient_missing")
    else:
        recipient = db.get(Person, recipient_person_id)
        if recipient is None:
            reasons.append("group_recipient_not_found")
        elif recipient.arquivado:
            reasons.append("group_recipient_archived")
        elif environment == "production":
            if not recipient.cpf:
                reasons.append("recipient_cpf_missing")
            try:
                assert_production_recipient(nome=recipient.nome_completo,
                                            cpf=recipient.cpf)
            except RecipientIdentityError as exc:
                if not (exc.code == "recipient_cpf_malformed" and not recipient.cpf):
                    reasons.append(exc.code)

    # ---- itens e total
    items = []
    for position, (exam, data, _own) in enumerate(per_exam, start=1):
        items.append({
            "position": position,
            "exam_id": exam.id,
            "exam_code": exam.public_code,
            "financial_entry_id": data["financial_entry_id"],
            "amount": data["amount_snapshot"],
        })
    if found and any(item["amount"] is None for item in items):
        # Sem valor determinado em algum exame não existe total: um documento
        # com um item "a definir" cobraria menos do que o serviço prestado.
        reasons.append("group_amount_undetermined")

    total = None
    if items and not reasons:
        total = sum((item["amount"] for item in items), Decimal("0.00"))

    service_date = per_exam[0][1]["service_date"] if per_exam else None
    municipio = per_exam[0][1]["service_municipio_ibge"] if per_exam else None
    policy_id = per_exam[0][1]["policy_id"] if per_exam else None
    flow = per_exam[0][1]["flow"] if per_exam else "UNSUPPORTED"

    description = None
    if len(items) >= MINIMUM_ITEMS:
        try:
            description = grouped_spirometry_service_description(
                len(items), per_exam[0][0].broncodilatador
                if len(_unique(e.broncodilatador for e, _, _ in per_exam)) == 1 else None)
        except ServiceDescriptionUndetermined:
            description = None

    return {
        "kind": "grouped",
        "recipient_person_id": recipient.id if recipient else None,
        "items": items,
        "total": total,
        "flow": flow,
        "policy_id": policy_id,
        "service_date": service_date,
        "competence": service_date,
        "service_municipio_ibge": municipio,
        "description": description,
        "amount_source": GROUPED_AMOUNT_SOURCE,
        "blocking_reasons": sorted(set(reasons)),
    }


def preparation_columns(data: dict) -> dict:
    """O subconjunto de `evaluate_group` que a tabela de preparação guarda.

    `financial_entry_id` e `partnership_id` ficam NULL de propósito: num
    documento agrupado o valor não vem de UMA receita nem de uma regra de
    parceria, e sim da soma dos itens — o que `amount_source` declara e o
    CHECK `fiscal_snapshot_source` exige. As receitas individuais não se
    perdem: cada uma está no seu `FiscalDocumentItem`.
    """
    return {
        "financial_entry_id": None,
        "policy_id": data["policy_id"],
        "recipient_person_id": data["recipient_person_id"],
        "flow": data["flow"],
        "service_date": data["service_date"],
        "competence": data["competence"],
        "service_municipio_ibge": data["service_municipio_ibge"],
        "amount_snapshot": data["total"],
        "description": data["description"],
        "blocking_reasons": data["blocking_reasons"],
        "amount_source": GROUPED_AMOUNT_SOURCE,
        "partnership_id": None,
        "partner_unit_id": None,
    }


def document_exam_ids(db: Session, document_id: str) -> list[str]:
    """Os exames de um documento agrupado, na ordem dos itens."""
    return list(db.scalars(select(FiscalDocumentItem.spirometry_exam_id).where(
        FiscalDocumentItem.document_id == document_id
    ).order_by(FiscalDocumentItem.position)).all())


def document_items(db: Session, document_id: str) -> list[FiscalDocumentItem]:
    return list(db.scalars(select(FiscalDocumentItem).where(
        FiscalDocumentItem.document_id == document_id
    ).order_by(FiscalDocumentItem.position)).all())


def _editable_or_fail(doc: FiscalDocument) -> None:
    if doc.kind != "grouped":
        nfse.fail("document_is_not_grouped", 422)
    # Mesmo portão do preparo: depois de uma tentativa, a composição é
    # evidência do que foi enviado e não se reescreve.
    if doc.state not in {"blocked", "pending", "failed"}:
        nfse.fail("document_frozen_after_attempt")


def _sync_items(db: Session, doc: FiscalDocument, data: dict) -> None:
    """Põe os itens do documento exatamente iguais ao avaliado agora.

    O item é ESTADO CORRENTE da composição, não evidência: a evidência
    imutável é a `FiscalPreparation` (append-only, com gatilho). Por isso o
    valor do item é reescrito aqui em vez de virar uma segunda história —
    duas versões do mesmo número é precisamente o defeito que a preparação
    append-only existe para evitar.
    """
    # Apaga TODOS e reinsere, em vez de casar item por item. Reposicionar no
    # lugar colide com `uq_fiscal_item_position`: trocar a composição muda a
    # ordem canônica, e atribuir a posição 1 a um item novo enquanto o antigo
    # ainda a ocupa estoura a unicidade no meio do flush. Apagar primeiro
    # libera posições E ocupações numa só vez, e não há evidência a preservar
    # aqui — a linha imutável é a `FiscalPreparation`.
    for item in document_items(db, doc.id):
        db.delete(item)
    db.flush()
    for wanted in data["items"]:
        db.add(FiscalDocumentItem(
            document_id=doc.id, environment=doc.environment,
            spirometry_exam_id=wanted["exam_id"],
            financial_entry_id=wanted["financial_entry_id"],
            amount=wanted["amount"], position=wanted["position"]))
    db.flush()


def prepare_group(db: Session, exam_ids: list[str], recipient_person_id: str,
                  settings, actor: str, request_id: str | None = None,
                  *, document_id: str | None = None) -> FiscalDocument:
    """Cria (ou reprepara) um documento fiscal agrupado. NÃO emite nada.

    POR QUE UM GRUPO BLOQUEADO NÃO É CRIADO

    O fluxo `single` cria o documento mesmo bloqueado, e isso é certo lá: o
    documento é a projeção de um fato que existe (o exame) e o bloqueio é
    informação útil na fila. Aqui, criar bloqueado teria um efeito colateral
    sério — os itens OCUPAM os exames (`uq_fiscal_item_exam_environment`), e
    um agrupamento inválido sequestraria exames que poderiam ser faturados de
    outra forma. Então um grupo que não passa é recusado com os códigos, sem
    escrever linha nenhuma.
    """
    if not settings.nfse_enabled:
        nfse.fail("nfse_disabled", 503)
    environment = settings.nfse_environment

    # A chave de idempotência sai da ordem CANÔNICA, calculada antes de
    # qualquer avaliação, por dois motivos: a ordem do clique não pode criar
    # um segundo documento, e um replay precisa encontrar o documento que já
    # existe ANTES de avaliar — senão os itens dele próprio apareceriam como
    # "exame já faturado" e o replay se bloquearia a si mesmo.
    identity = {"kind": "grouped", "environment": environment,
                "exam_ids": canonical_exam_ids(db, exam_ids),
                "recipient_person_id": recipient_person_id}
    key = nfse.payload_fingerprint(identity)

    doc = None
    if document_id is not None:
        doc = nfse.get_document(db, document_id, lock=True)
    else:
        existing = db.scalar(select(FiscalDocument).where(
            FiscalDocument.idempotency_key == key))
        if existing is not None:
            doc = nfse.get_document(db, existing.id, lock=True)
    if doc is not None:
        _editable_or_fail(doc)
        if doc.environment != environment:
            nfse.fail("document_environment_mismatch")

    data = evaluate_group(db, exam_ids, recipient_person_id, environment,
                          document_id=doc.id if doc else None)
    if data["blocking_reasons"]:
        raise nfse.HTTPException(409, detail={
            "codigo": "grouped_document_blocked",
            "motivos": data["blocking_reasons"],
        })

    if doc is None:
        def factory(key, fingerprint):
            obj = FiscalDocument(
                spirometry_exam_id=None, kind="grouped", environment=environment,
                recipient_person_id=data["recipient_person_id"], created_by=actor,
                idempotency_key=key, idempotency_fingerprint=fingerprint)
            db.add(obj)
            db.flush()
            return obj

        doc, _replay = nfse.idempotent_create(db, FiscalDocument, key, identity, factory)
        doc = nfse.get_document(db, doc.id, lock=True)
    else:
        doc.recipient_person_id = data["recipient_person_id"]

    _sync_items(db, doc, data)

    fingerprint = nfse.payload_fingerprint(data)
    latest = nfse.latest_preparation(db, doc.id)
    changed = latest is None or latest.fingerprint != fingerprint
    if changed:
        from ..models import FiscalPreparation
        db.add(FiscalPreparation(document_id=doc.id, created_by=actor,
                                 fingerprint=fingerprint,
                                 **preparation_columns(data)))
    doc.blocking_reasons = []
    doc.eligibility = "eligible"
    issued_before = db.scalar(select(nfse.FiscalAttempt.id).where(
        nfse.FiscalAttempt.document_id == doc.id,
        nfse.FiscalAttempt.operation == "issue",
        nfse.FiscalAttempt.phase == "started",
    ).limit(1))
    doc.state = "failed" if issued_before else "pending"
    if changed:
        # Códigos, contagem e total. Nunca nome ou CPF do tomador.
        nfse.record(db, "grouped_prepared", "fiscal_document", doc.id, actor,
                    request_id, status=doc.state, itens=len(data["items"]),
                    total=str(data["total"]))
    db.commit()
    return doc


def set_recipient(db: Session, document_id: str, recipient_person_id: str,
                  settings, actor: str, request_id: str | None = None) -> FiscalDocument:
    """Troca o tomador de um documento agrupado ainda não enviado.

    NÃO reprepara de propósito: a preparação anterior fica STALE, e
    `nfse.operate()` recusa emitir contra ela (`preparation_stale`). Trocar
    quem recebe a nota invalida a conferência humana que autorizou a anterior
    — repreparar aqui ressuscitaria aquela autorização em silêncio.
    """
    doc = nfse.get_document(db, document_id, lock=True)
    _editable_or_fail(doc)
    recipient = db.get(Person, recipient_person_id)
    if recipient is None:
        nfse.fail("group_recipient_not_found", 404)
    if recipient.arquivado:
        nfse.fail("group_recipient_archived")
    if doc.recipient_person_id != recipient.id:
        doc.recipient_person_id = recipient.id
        nfse.record(db, "grouped_recipient_changed", "fiscal_document", doc.id,
                    actor, request_id, status=doc.state)
        db.commit()
    return doc


# Rótulos legíveis dos bloqueios de grupo. Só vocabulário fechado — nenhum
# deles cita exame, paciente ou tomador por nome.
GROUP_REASON_LABELS = {
    "group_requires_two_or_more_exams": "Escolha ao menos dois exames.",
    "group_exam_duplicated": "O mesmo exame foi escolhido duas vezes.",
    "group_exam_not_found": "Algum exame escolhido não existe.",
    "group_exam_already_billed": "Algum exame já pertence a outro documento fiscal.",
    "group_service_date_divergent": "Os exames têm datas de serviço diferentes.",
    "group_service_location_divergent": "Os exames foram realizados em municípios diferentes.",
    "group_policy_divergent": "Os exames caem em políticas fiscais diferentes.",
    "group_flow_divergent": "Os exames têm fluxos comerciais diferentes.",
    "group_flow_unsupported": "Este fluxo comercial não admite nota agrupada.",
    "group_bronchodilator_divergent":
        "Os exames divergem quanto ao broncodilatador: a descrição do serviço "
        "não seria verdadeira para os dois.",
    "group_amount_undetermined": "Algum exame ainda não tem valor determinado.",
    "group_recipient_missing": "Escolha o tomador da nota.",
    "group_recipient_not_found": "O tomador escolhido não existe.",
    "group_recipient_archived": "O tomador escolhido está arquivado.",
    # Identidade fiscal do TOMADOR (os mesmos códigos de
    # `nfse_national.recipient_identity`, aqui em português do operador).
    "recipient_cpf_missing": "O tomador não tem CPF cadastrado.",
    "recipient_cpf_malformed": "O CPF do tomador não tem 11 dígitos.",
    "recipient_cpf_repeated_digits": "O CPF do tomador é uma sequência repetida.",
    "recipient_cpf_check_digits_invalid": "O CPF do tomador tem dígitos verificadores inválidos.",
    "recipient_name_missing": "O tomador não tem nome cadastrado.",
    "recipient_name_not_a_full_name": "O nome do tomador não parece um nome completo.",
    "recipient_name_looks_like_placeholder":
        "O nome do tomador parece um cadastro de teste.",
    "recipient_name_too_long": "O nome do tomador excede o limite fiscal.",
    # Bloqueios que vêm de cada exame, pela avaliação de sempre.
    "recipient_missing_or_archived": "Algum paciente do grupo está arquivado ou ausente.",
    "service_not_performed": "Algum exame não está marcado como realizado.",
    "service_date_missing_or_imprecise": "Algum exame não tem data exata de realização.",
    "service_date_in_future": "Algum exame tem data futura.",
    "financial_entry_missing": "Algum exame não tem receita lançada.",
    "financial_entry_ambiguous": "Algum exame tem mais de uma receita lançada.",
    "financial_link_incoherent": "Alguma receita está vinculada a outro componente.",
    "financial_revenue_not_received_or_invalid":
        "Alguma receita não está recebida, ou o valor/moeda é inválido.",
    "commercial_flow_unsupported": "Algum exame tem fluxo comercial não suportado.",
    "service_location_missing": "Algum exame não tem município de atendimento.",
    "service_location_unsupported": "O município de atendimento não é suportado.",
    "policy_missing": "Não há política fiscal cadastrada para este fluxo.",
    "policy_outside_validity": "A política fiscal não cobre a data do serviço.",
    "policy_incomplete": "A configuração tributária está incompleta.",
    "policy_ambiguous": "Há mais de uma política fiscal válida para a data.",
    "policy_invalid": "A política fiscal vigente é inválida.",
    "policy_amount_basis_mismatch":
        "A política fiscal não corresponde ao fluxo comercial do exame.",
}


def serialize_group_preview(db: Session, data: dict) -> dict:
    """Exatamente o que a tela mostra antes de preparar.

    O CPF do tomador sai MASCARADO, como já saía na confirmação de produção —
    quem confere precisa reconhecer a pessoa, não ler o documento inteiro.
    """
    from .nfse_production_issuance import mask_cpf
    from .nfse_national.service_location import SUPPORTED_SERVICE_MUNICIPALITIES

    recipient = (db.get(Person, data["recipient_person_id"])
                 if data["recipient_person_id"] else None)
    municipio = data["service_municipio_ibge"]
    return {
        "kind": "grouped",
        "recipient": {
            "person_id": recipient.id if recipient else None,
            "public_code": recipient.public_code if recipient else None,
            "nome": recipient.nome_completo if recipient else None,
            "cpf_masked": mask_cpf(recipient.cpf if recipient else None),
        },
        "itens": [{
            "position": item["position"],
            "spirometry_exam_id": item["exam_id"],
            "exam_code": item["exam_code"],
            "financial_entry_id": item["financial_entry_id"],
            "amount": f'{item["amount"]:.2f}' if item["amount"] is not None else None,
        } for item in data["items"]],
        "itens_count": len(data["items"]),
        "total": f'{data["total"]:.2f}' if data["total"] is not None else None,
        "flow": data["flow"],
        "service_date": data["service_date"],
        "competence": data["competence"],
        "municipio_ibge": municipio,
        "municipio_name": SUPPORTED_SERVICE_MUNICIPALITIES.get(municipio or ""),
        "service_description": data["description"],
        "amount_source": data["amount_source"],
        "blocking_reasons": data["blocking_reasons"],
        "blocking_labels": [GROUP_REASON_LABELS.get(r, r)
                            for r in data["blocking_reasons"]],
        "can_prepare": not data["blocking_reasons"],
    }
