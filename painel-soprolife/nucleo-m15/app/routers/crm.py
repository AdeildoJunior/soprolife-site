"""Workspace canônico de CRM de pacientes e acompanhamento (M19).

Somente LEITURA agregada + UM ponto de escrita: o registro do resultado da
tentativa de contato. Nenhum cadastro novo nasce aqui — criação de pessoa,
exame, consulta, lead, parceiro ou lançamento continua exclusivamente na
Central de Cadastros, pelos endpoints já existentes.

Privacidade:
- telefone só sai mascarado nas listagens; o número completo aparece apenas
  dentro da URL de WhatsApp montada para revisão humana (contrato herdado);
- busca por nome é POST (corpo), nunca query string;
- o financeiro do paciente só é anexado para papel gestor ou acima.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..ids import (
    ENTITY_LABELS,
    PREFIX_TO_ENTITY,
    allocate_public_code,
    code_dictionary,
)
from ..models import (
    Consultation,
    FinancialEntry,
    Followup,
    Interaction,
    LegacyAlias,
    Lead,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerUnit,
    Partnership,
    Person,
    PersonContact,
    SpirometryExam,
    User,
)
from ..normalize import normalize_name
from ..schemas import CrmContatoRegistro, CrmPacienteBusca
from ..security import (
    ROLE_GESTOR,
    ROLE_LEITURA,
    ROLE_OPERACIONAL,
    require_role,
    user_effective_roles,
)
from ..serializers import ser_interaction
from ..services import crm as csvc
from ..services import followup as fsvc

router = APIRouter(prefix="/crm", tags=["crm"])

# Tabelas que possuem código público, na ordem de resolução de busca.
CODE_MODELS = {
    "people": Person,
    "leads": Lead,
    "spirometry_exams": SpirometryExam,
    "consultations": Consultation,
    "partners": Partner,
    "partner_units": PartnerUnit,
    "partner_contacts": PartnerContact,
    "partnerships": Partnership,
    "partner_referrals": PartnerReferral,
    "interactions": Interaction,
    "followups": Followup,
    "financial_entries": FinancialEntry,
}


def _pode_ver_financeiro(user: User) -> bool:
    return ROLE_GESTOR in user_effective_roles(user)


# ------------------------------------------------------------------ visão geral

@router.get("/kpis")
def kpis(
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    return csvc.build_kpis(db)


@router.get("/config")
def config(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Rótulos e vocabulário do workspace — evita duplicar strings no cliente."""
    return {
        "filas": [{"chave": k, "rotulo": csvc.FILA_LABELS[k]} for k in csvc.FILAS],
        "status_acompanhamento": [
            {"chave": k, "rotulo": v} for k, v in csvc.STATUS_LABELS.items()
        ],
        "resultados_contato": [
            {"chave": k, "rotulo": csvc.RESULTADO_LABELS[k]} for k in csvc.RESULTADOS
        ],
        "templates_whatsapp": [
            {"chave": k, "rotulo": v} for k, v in csvc.TEMPLATES.items()
        ],
        "dicionario_codigos": code_dictionary(),
        "pode_ver_financeiro": _pode_ver_financeiro(user),
        "meses_followup": fsvc.FOLLOWUP_MONTHS,
    }


# ------------------------------------------------------------------ pacientes

@router.post("/pacientes/busca")
def buscar_pacientes(
    payload: CrmPacienteBusca,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_LEITURA)),
):
    com_financeiro = _pode_ver_financeiro(user)
    rows = csvc.build_patient_rows(db, com_financeiro=com_financeiro)

    if payload.q:
        alvo = normalize_name(payload.q)
        rows = [
            r for r in rows
            if alvo in normalize_name(r["nome_completo"])
            or alvo in r["public_code"].casefold()
        ]
    if payload.origem and payload.origem != "todas":
        rows = [r for r in rows if r["origem"] == payload.origem]
    if payload.status_acompanhamento:
        rows = [
            r for r in rows
            if r["status_acompanhamento"] == payload.status_acompanhamento
        ]
    if payload.somente_sem_telefone:
        rows = [r for r in rows if not r["contato"]["telefone_utilizavel"]]
    if payload.fila:
        if payload.fila not in csvc.FILAS:
            raise HTTPException(status_code=422, detail="Fila inválida.")
        queue = csvc.build_queue_rows(db)
        na_fila = {
            item["paciente"]["person_id"]
            for item in queue if payload.fila in item["filas"]
        }
        rows = [r for r in rows if r["person_id"] in na_fila]

    total = len(rows)
    offset = (payload.pagina - 1) * payload.tamanho
    return {
        "itens": rows[offset:offset + payload.tamanho],
        "total": total,
        "pagina": payload.pagina,
        "tamanho": payload.tamanho,
        "com_financeiro": com_financeiro,
    }


@router.get("/pacientes/{person_id}/timeline")
def timeline(
    person_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_LEITURA)),
):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    com_financeiro = _pode_ver_financeiro(user)
    contato = csvc.dialable_contact(db, person.id)
    guardian = csvc.guardian_of(db, person.id)
    return {
        "paciente": {
            "person_id": person.id,
            "public_code": person.public_code,
            "rotulo_codigo": ENTITY_LABELS["people"],
            "nome_completo": person.nome_completo,
            "status": person.status,
            "nao_contatar": person.nao_contatar,
            "telefone_mascarado": csvc.mask_phone(
                contato.valor_normalizado if contato else None
            ),
            "telefone_utilizavel": contato is not None,
        },
        "responsavel": {
            "person_id": guardian.id,
            "public_code": guardian.public_code,
            "nome_completo": guardian.nome_completo,
        } if guardian else None,
        "eventos": csvc.build_timeline(db, person, com_financeiro=com_financeiro),
        "com_financeiro": com_financeiro,
    }


@router.get("/pacientes/{person_id}/whatsapp-url")
def whatsapp_url_paciente(
    person_id: str,
    template: str = Query("geral"),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Prévia de mensagem para contato sem follow-up vinculado.

    Mesmas travas fail-closed do fluxo de follow-up: consentimento concedido,
    pessoa contactável, registro real e telefone discável. Abrir a URL NUNCA
    conclui nada — o resultado é registrado em /crm/contatos.
    """
    if template not in csvc.TEMPLATES:
        raise HTTPException(status_code=422, detail="Modelo de mensagem inválido.")
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    contato_pessoa = csvc.guardian_of(db, person.id) or person
    if person.nao_contatar or contato_pessoa.nao_contatar:
        raise HTTPException(
            status_code=409,
            detail="Pessoa marcada como 'não contatar' — contato bloqueado.",
        )
    if person.legacy_source == "seed_demo" or contato_pessoa.legacy_source == "seed_demo":
        raise HTTPException(
            status_code=409,
            detail={"codigo": "registro_sintetico",
                    "mensagem": "Registro sintético de demonstração — WhatsApp desabilitado."},
        )
    consent = fsvc.whatsapp_consent_status(db, contato_pessoa.id)
    if consent != "concedido":
        raise HTTPException(
            status_code=409,
            detail={"codigo": "sem_consentimento",
                    "mensagem": "Consentimento de WhatsApp não concedido — contato "
                                "bloqueado (fail-closed).",
                    "consentimento": consent},
        )
    contact = csvc.dialable_contact(db, contato_pessoa.id)
    if not contact or not contact.valor_normalizado:
        raise HTTPException(status_code=422, detail="Pessoa sem telefone discável cadastrado.")
    mensagem = csvc.template_message(contato_pessoa.nome_completo, template)
    return {
        "person_id": person.id,
        "template": template,
        "url": fsvc.build_whatsapp_url(contact.valor_normalizado, mensagem),
        "mensagem_sugerida": mensagem,
        "contato": {
            "person_id": contato_pessoa.id,
            "public_code": contato_pessoa.public_code,
            "nome_completo": contato_pessoa.nome_completo,
            "eh_responsavel": contato_pessoa.id != person.id,
            "telefone_mascarado": csvc.mask_phone(contact.valor_normalizado),
        },
        "envio_automatico": False,
        "instrucao": "Revise a mensagem, envie manualmente e registre o resultado.",
    }


# ------------------------------------------------------- contatos a realizar

@router.get("/contatos-a-realizar")
def contatos_a_realizar(
    fila: str | None = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    rows = csvc.build_queue_rows(db)
    totais = {f: sum(1 for r in rows if f in r["filas"]) for f in csvc.FILAS}
    if fila:
        if fila not in csvc.FILAS:
            raise HTTPException(status_code=422, detail="Fila inválida.")
        rows = [r for r in rows if fila in r["filas"]]
    return {
        "data_referencia": fsvc.today_local().isoformat(),
        "fila": fila,
        "itens": rows,
        "totais": totais,
        "rotulos": csvc.FILA_LABELS,
    }


@router.post("/contatos", status_code=201)
def registrar_contato(
    payload: CrmContatoRegistro,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_OPERACIONAL)),
):
    """Registra UM resultado de tentativa de contato e aplica o efeito.

    Abrir o WhatsApp nunca conclui nada; só esta chamada muda o estado do
    acompanhamento, e sempre criando exatamente uma interação auditável.
    """
    fup = None
    if payload.followup_id:
        fup = db.get(Followup, payload.followup_id)
        if not fup:
            raise HTTPException(status_code=404, detail="Follow-up não encontrado.")
        person = db.get(Person, fup.person_id)
    else:
        person = db.get(Person, payload.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")

    contato_pessoa = person
    if fup is not None and fup.contact_person_id:
        alt = db.get(Person, fup.contact_person_id)
        if alt is not None:
            contato_pessoa = alt

    interaction = Interaction(
        public_code=allocate_public_code(db, "interactions"),
        person_id=contato_pessoa.id,
        canal=payload.canal,
        direcao="enviado",
        resumo=payload.observacao,
        resultado=payload.resultado,
        user_id=user.id,
        followup_id=fup.id if fup is not None else None,
    )
    db.add(interaction)
    db.flush()

    efeito = "nenhum"
    if fup is not None:
        if payload.resultado == csvc.RESULTADO_REALIZADO:
            if fup.status == "pendente":
                fsvc.complete_followup(
                    db, fup, user.id, "contato_realizado", payload.observacao
                )
                efeito = "followup_concluido"
        elif payload.resultado == csvc.RESULTADO_NAO_RESPONDEU:
            fup.tentativas += 1
            db.flush()
            efeito = "tentativa_registrada"
        elif payload.resultado == csvc.RESULTADO_REAGENDAR:
            fsvc.retry_followup(db, fup, payload.nova_data, payload.observacao)
            efeito = "followup_reagendado"
        elif payload.resultado == csvc.RESULTADO_NAO_DESEJA:
            if fup.status == "pendente":
                fup.status = "cancelado"
                db.flush()
            efeito = "followup_cancelado"
        elif payload.resultado == csvc.RESULTADO_TELEFONE_INVALIDO:
            fup.tentativas += 1
            db.flush()
            efeito = "tentativa_registrada"

    if payload.resultado == csvc.RESULTADO_NAO_DESEJA:
        # Preferência do titular vale para a pessoa contatada e para o
        # paciente: nenhuma das duas volta para a fila.
        contato_pessoa.nao_contatar = True
        person.nao_contatar = True
        db.flush()
        efeito = "nao_contatar_marcado" if efeito == "nenhum" else efeito
    elif payload.resultado == csvc.RESULTADO_TELEFONE_INVALIDO:
        contact = csvc.dialable_contact(db, contato_pessoa.id)
        if contact is not None:
            contact.nao_discavel = True
            db.flush()
        efeito = "telefone_marcado_invalido"

    audit(db, "crm.contato_registrado", "interactions", interaction.id, user.id,
          request.state.request_id,
          {"resultado": payload.resultado, "canal": payload.canal,
           "followup_id": fup.id if fup is not None else None, "efeito": efeito})
    db.commit()
    return {"interacao": ser_interaction(interaction), "efeito": efeito}


# ------------------------------------------------------------ histórico

@router.get("/historico-contatos")
def historico_contatos(
    inicio: date | None = Query(None),
    fim: date | None = Query(None),
    resultado: str | None = Query(None),
    canal: str | None = Query(None),
    operador: str | None = Query(None),
    person_public_code: str | None = Query(None),
    origem: str | None = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Histórico auditável de tentativas de contato.

    Filtros por período, operador, resultado, canal, origem e CÓDIGO do
    paciente (nunca nome/telefone na rota).
    """
    stmt = select(Interaction).order_by(Interaction.ts_utc.desc())
    rows = db.execute(stmt).scalars().all()

    origem_por_pessoa = {
        r["person_id"]: r["origem"]
        for r in csvc.build_patient_rows(db, com_financeiro=False)
    }
    users = {u.id: u.nome for u in db.execute(select(User)).scalars().all()}

    itens: list[dict] = []
    for inter in rows:
        data = inter.ts_utc.date() if inter.ts_utc else None
        if inicio and (data is None or data < inicio):
            continue
        if fim and (data is None or data > fim):
            continue
        if resultado and inter.resultado != resultado:
            continue
        if canal and inter.canal != canal:
            continue
        if operador and inter.user_id != operador:
            continue
        contato = db.get(Person, inter.person_id)
        if person_public_code and (
            contato is None
            or contato.public_code.casefold() != person_public_code.casefold()
        ):
            continue
        if origem and origem != "todas":
            if origem_por_pessoa.get(inter.person_id) != origem:
                continue

        fup = db.get(Followup, inter.followup_id) if inter.followup_id else None
        paciente = db.get(Person, fup.person_id) if fup else contato
        itens.append({
            "public_code": inter.public_code,
            "ts_utc": inter.ts_utc.isoformat() if inter.ts_utc else None,
            "canal": inter.canal,
            "direcao": inter.direcao,
            "resultado": inter.resultado,
            "resultado_rotulo": csvc.RESULTADO_LABELS.get(
                inter.resultado, inter.resultado
            ),
            "observacao": inter.resumo,
            "paciente": {
                "person_id": paciente.id,
                "public_code": paciente.public_code,
                "nome_completo": paciente.nome_completo,
            } if paciente else None,
            "contatado": {
                "person_id": contato.id,
                "public_code": contato.public_code,
                "nome_completo": contato.nome_completo,
                "diferente_do_paciente": bool(paciente and contato.id != paciente.id),
            } if contato else None,
            "followup": {
                "id": fup.id,
                "public_code": fup.public_code,
                "tipo": fup.tipo,
                "status": fup.status,
                "due_date": fup.due_date.isoformat() if fup.due_date else None,
            } if fup else None,
            "operador": {"id": inter.user_id, "nome": users.get(inter.user_id)},
            "origem": origem_por_pessoa.get(inter.person_id),
        })

    return {
        "itens": itens,
        "total": len(itens),
        "operadores": [{"id": uid, "nome": nome} for uid, nome in sorted(
            users.items(), key=lambda kv: (kv[1] or "")
        )],
        "resultados": [
            {"chave": k, "rotulo": csvc.RESULTADO_LABELS[k]} for k in csvc.RESULTADOS
        ],
    }


# ------------------------------------------------------------ indicadores

@router.get("/indicadores")
def indicadores(
    meses: int = Query(12, ge=1, le=60),
    origem: str | None = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    hoje = fsvc.today_local()
    inicio = (hoje.replace(day=1) - timedelta(days=31 * (meses - 1))).replace(day=1)
    fim = hoje + timedelta(days=365)  # inclui vencimentos futuros já agendados
    return csvc.build_indicators(db, inicio=inicio, fim=fim, origem=origem)


# ------------------------------------------------------------ códigos / aliases

@router.get("/codigos/dicionario")
def dicionario_codigos(
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    return {"prefixos": code_dictionary()}


@router.get("/codigos/resolver")
def resolver_codigo(
    codigo: str = Query(min_length=2, max_length=64),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_LEITURA)),
):
    """Resolve código público OU alias histórico para a entidade canônica.

    Aliases legados (ex.: FIN-0004) continuam pesquisáveis e apontam para o
    código canônico já emitido (ex.: LAN-000001) — nada é renumerado e
    nenhuma entidade duplicada é criada para satisfazer um alias.
    """
    alvo = codigo.strip()
    resultados: list[dict] = []

    prefixo = alvo.split("-")[0].upper() if "-" in alvo else ""
    tabela = PREFIX_TO_ENTITY.get(prefixo)
    if tabela:
        model = CODE_MODELS[tabela]
        obj = db.execute(
            select(model).where(model.public_code == alvo.upper())
        ).scalars().first()
        if obj is not None:
            resultados.append({
                "tipo": "codigo_canonico",
                "entidade": tabela,
                "rotulo": ENTITY_LABELS[tabela],
                "public_code": obj.public_code,
                "id": obj.id,
            })

    for alias in db.execute(
        select(LegacyAlias).where(LegacyAlias.legacy_id == alvo)
    ).scalars().all():
        tabela = alias.entidade
        model = CODE_MODELS.get(tabela)
        obj = db.get(model, alias.entity_id) if model else None
        resultados.append({
            "tipo": "alias_historico",
            "entidade": tabela,
            "rotulo": ENTITY_LABELS.get(tabela, tabela),
            "legacy_id": alias.legacy_id,
            "legacy_source": alias.legacy_source,
            "public_code": getattr(obj, "public_code", None),
            "id": alias.entity_id,
        })

    return {
        "consulta": alvo,
        "encontrado": bool(resultados),
        "resultados": resultados,
    }
