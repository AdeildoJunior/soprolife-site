"""M23 — geração dos snapshots do painel a partir do PostgreSQL.

Substitui, na esteira automática, todos os leitores de Google Sheets. A partir
do M23 os arquivos ``painel-soprolife/data/*.local.json`` nascem exclusivamente
do banco canônico, pela mesma camada de modelos que a API usa.

Regras inegociáveis:

* **Nenhuma PII sai daqui.** Só agregados e campos institucionais. Nome de
  paciente, telefone, CPF, observação clínica e endereço nunca são lidos.
  Nome de clínica/parceiro é dado institucional da empresa e continua
  permitido, como já era no fluxo anterior.
* **Nada é inventado.** Quando o banco não tem o dado, o campo é ``None`` e a
  UI mostra "—". Nunca zero fabricado, nunca valor de exemplo.
* **Escrita atômica.** Cada arquivo é escrito em temporário e renomeado, para
  que uma falha no meio nunca deixe um snapshot truncado no lugar de um válido.

Marketing/SEO NÃO é gerado aqui: Search Console e GA4 continuam vindo do
conector dedicado com conta de serviço somente leitura (decisão explícita do
M23 de não descomissionar essa integração).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    Consultation,
    FinancialEntry,
    Followup,
    Lead,
    Partner,
    PartnerContact,
    PartnerSettlement,
    PartnerSettlementItem,
    PartnerUnit,
    PartnerUnitConfig,
    Role,
    SpirometryExam,
    UserRole,
)
from .services.followup import today_local
from .services.partner_pricing import resolve_valor_por_exame

GENERATOR = "nucleo-m15/app/snapshots.py"
SOURCE_TYPE = "postgresql_nucleo_m15"

#: Nome de arquivo → função construtora. Ordem estável para saída legível.
SNAPSHOT_FILES = (
    "resumo-dashboard.local.json",
    "leads-summary.local.json",
    "crm-clinicas.local.json",
    "financeiro-summary.local.json",
    "followup-pacientes-summary.local.json",
    "followup-clinicas-summary.local.json",
    "crm-contatos-b2b-summary.local.json",
    "auditoria-summary.local.json",
    "parcerias-pastore-summary.local.json",
)

_ULTIMOS_EVENTOS = 30


# --------------------------------------------------------------------------- util

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source(subtype: str, nota: str | None = None) -> dict:
    payload = {
        "type": f"{SOURCE_TYPE}_{subtype}",
        "official_source": "PostgreSQL (Núcleo M15)",
        "generator": GENERATOR,
        "safeToDisplay": True,
        "containsPersonalData": False,
        "containsHealthData": False,
        "generatedAt": _now_iso(),
    }
    if nota:
        payload["nota"] = nota
    return payload


def _brl(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _br_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _clean(record: dict) -> dict:
    """Remove chaves vazias — o painel trata ausência como 'não informado'."""
    return {k: v for k, v in record.items() if v not in (None, "", [], {})}


def _bucket(due: date | None, today: date) -> str:
    if due is None:
        return "semData"
    delta = (due - today).days
    if delta < 0:
        return "atrasados"
    if delta == 0:
        return "hoje"
    if delta <= 7:
        return "proximos7dias"
    return "futuro"


def _empty_buckets() -> dict:
    return {"total": 0, "hoje": 0, "atrasados": 0,
            "proximos7dias": 0, "futuro": 0, "semData": 0}


# --------------------------------------------------------------------- construtores

def build_leads_summary(db: Session) -> dict:
    """Leads sem PII: código público, etapa, origem, canal, serviço."""
    leads = db.scalars(select(Lead).order_by(Lead.created_at.desc())).all()
    itens = []
    for lead in leads:
        itens.append(_clean({
            "lead_id": lead.public_code,
            "data_contato": _br_date(lead.data_primeiro_contato),
            "servico_interesse": lead.servico_interesse,
            "origem": lead.origem,
            "canal": lead.canal_entrada,
            "etapa": lead.etapa,
            "responsavel": lead.responsavel,
            "modalidade": lead.modalidade,
            # Texto livre nunca sai; só o booleano derivado.
            "tem_proxima_acao": bool(lead.data_retomada_manual),
        }))
    return {
        "source": _source(
            "leads_summary",
            "Leads do PostgreSQL. Nome e telefone vivem só no banco e na API "
            "autenticada — nunca neste arquivo.",
        ),
        "leads": itens,
    }


def build_crm_clinicas(db: Session) -> dict:
    """Clínicas/parceiros ativos. Nome de empresa é dado institucional."""
    parceiros = db.scalars(
        select(Partner)
        .where(Partner.arquivado.is_(False))
        .order_by(Partner.nome)
    ).all()

    clinicas = []
    for p in parceiros:
        unidades = db.scalars(
            select(PartnerUnit).where(PartnerUnit.partner_id == p.id)
        ).all()
        bairro = next((u.bairro for u in unidades if u.bairro), None)
        # Nomes de campo seguem a allowlist FECHADA validada por
        # check-access.sh. O contrato do resumo seguro não é ampliado aqui:
        # um campo a mais no arquivo servido é um risco, não uma conveniência.
        clinicas.append(_clean({
            "clinica_id": p.public_code,
            "nome_clinica": p.nome,
            "bairro": bairro or p.cidade,
            "tipo_clinica": p.tipo,
            "etapa": p.status,
        }))

    return {
        "source": _source(
            "crm_clinicas",
            "Parceiros/clínicas do PostgreSQL. Contato pessoal (nome, telefone, "
            "e-mail) não é exportado.",
        ),
        "clinicas": clinicas,
    }


MESES_PT = (
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez",
)

ORIGEM_PROPRIA = "SoproLife"


def _competencia_label(chave: str) -> str:
    ano, mes = chave.split("-")
    return f"{MESES_PT[int(mes) - 1].capitalize()}/{ano}"


def build_financeiro_summary(db: Session) -> dict:
    """Financeiro derivado exclusivamente de FinancialEntry + fechamentos.

    Duas grandezas que o painel antigo confundia e a M26.3 separa:

    * **recebido** — só o que tem lançamento com status `Recebido`. Dinheiro
      que alguém conferiu que entrou.
    * **a receber** — lançamento pendente MAIS o que os fechamentos de
      parceria já devem (valor conferido quando existe, previsto pela regra
      vigente enquanto não existe). Exame realizado vira dívida do parceiro no
      instante em que acontece; ele só migra para "recebido" quando o recibo
      do fechamento nasce.

    Nada é inventado: parceria sem regra cadastrada contribui `None`, não zero.
    """
    entradas = db.scalars(select(FinancialEntry)).all()
    settlements = db.scalars(select(PartnerSettlement)).all()

    # Fechamento → parceiro, para rotular a origem da receita sem PII.
    partners = {p.id: p for p in db.scalars(select(Partner)).all()}
    settlement_por_id = {s.id: s for s in settlements}

    def _origem(entry: FinancialEntry) -> str:
        if entry.partner_settlement_id:
            s = settlement_por_id.get(entry.partner_settlement_id)
            parceiro = partners.get(s.partner_id) if s else None
            if parceiro:
                return parceiro.nome
        return ORIGEM_PROPRIA

    receita_recebida = Decimal("0")
    receita_pendente = Decimal("0")
    por_status: dict[str, dict] = defaultdict(lambda: {"quantidade": 0, "valor": Decimal("0")})
    por_competencia: dict[str, Decimal] = defaultdict(Decimal)
    por_categoria: dict[str, Decimal] = defaultdict(Decimal)
    por_origem: dict[str, Decimal] = defaultdict(Decimal)
    cancelados = 0
    exames_pagos = 0
    lancamentos_recebidos = 0
    receita_sem_competencia = Decimal("0")
    lancamentos_sem_competencia = 0

    # A competência é do fuso da operação, não do UTC. Entre 21h e meia-noite
    # em Brasília o UTC já virou o dia (e, no fim do mês, o MÊS) seguinte: o
    # card "Receita da competência atual" mostraria o mês que vem zerado, que é
    # exatamente o tipo de número inútil que esta etapa foi feita para remover.
    hoje = today_local()
    competencia_atual = hoje.strftime("%Y-%m")
    total_mes_atual = Decimal("0")

    for e in entradas:
        status = e.status or "Pendente"
        por_status[status]["quantidade"] += 1
        por_status[status]["valor"] += e.valor

        if e.tipo == "receita":
            if status == "Recebido":
                receita_recebida += e.valor
                lancamentos_recebidos += 1
                por_origem[_origem(e)] += e.valor
                if e.spirometry_exam_id:
                    exames_pagos += 1
            elif status in ("Pendente", "Parcial"):
                receita_pendente += e.valor
        if status == "Cancelado":
            cancelados += 1

        # A régua do gráfico mensal é a COMPETÊNCIA, não a data do crédito: um
        # recibo Pastore de julho pago em agosto pertence a julho, que é o mês
        # em que aqueles exames foram feitos. Usar a data de recebimento
        # empilharia toda a regularização histórica num mês só e apagaria a
        # produção real dos meses anteriores.
        #
        # Só a receita efetivamente recebida entra: um mês não "faturou" o que
        # ainda não entrou.
        ref = e.data_competencia or e.data_recebimento
        if e.tipo == "receita" and status == "Recebido":
            if ref:
                chave = ref.strftime("%Y-%m")
                por_competencia[chave] += e.valor
                if chave == competencia_atual:
                    total_mes_atual += e.valor
            else:
                # Receita real sem data nenhuma existe (lote de conciliação
                # histórica M18). Somar no gráfico seria inventar um mês;
                # ignorar em silêncio faria a soma das barras não bater com
                # "Receita recebida" e ninguém entenderia por quê. Fica
                # explícito, para o painel poder dizer.
                receita_sem_competencia += e.valor
                lancamentos_sem_competencia += 1
        if e.categoria:
            por_categoria[e.categoria] += e.valor

    # --- parceria: o que já é devido e ainda não foi recebido ---------------
    a_receber_parceria = Decimal("0")
    exames_em_fechamento_aberto = 0
    parcerias_sem_regra: list[str] = []
    for s in settlements:
        if s.status in ("recebido", "cancelado"):
            continue
        parceiro = partners.get(s.partner_id)
        if parceiro is None:
            continue
        itens = db.scalar(
            select(func.count()).select_from(PartnerSettlementItem)
            .where(PartnerSettlementItem.settlement_id == s.id)
        ) or 0
        unidade = db.get(PartnerUnit, s.partner_unit_id) if s.partner_unit_id else None
        regra = resolve_valor_por_exame(db, parceiro, unidade, s.competencia)
        devido = s.valor_total if s.valor_total is not None else regra.previsto(itens)
        if devido is None:
            # Sem regra e sem valor conferido não há o que somar. Zero seria
            # uma afirmação; ausência é a verdade.
            if parceiro.nome not in parcerias_sem_regra:
                parcerias_sem_regra.append(parceiro.nome)
            continue
        a_receber_parceria += Decimal(devido)
        exames_em_fechamento_aberto += itens

    # Exames de parceria que já viraram receita recebida (via recibo do
    # fechamento). Contam para a média por exame — sem eles a média dividiria
    # receita de 34 exames pelo número de exames próprios e mentiria.
    exames_parceria_recebidos = 0
    for s in settlements:
        if s.status != "recebido":
            continue
        exames_parceria_recebidos += db.scalar(
            select(func.count()).select_from(PartnerSettlementItem)
            .where(PartnerSettlementItem.settlement_id == s.id)
        ) or 0

    exames_reconhecidos = exames_pagos + exames_parceria_recebidos
    # M26.3 — a métrica antiga dividia receita por LANÇAMENTO. Um recibo
    # Pastore é um lançamento só que cobre 14 exames, então aquele número
    # crescia sozinho a cada fechamento e não descrevia nada. A média por
    # EXAME descreve.
    receita_media_por_exame = (
        round(float(receita_recebida) / exames_reconhecidos, 2)
        if exames_reconhecidos else None
    )

    # --- últimos movimentos -------------------------------------------------
    recentes = sorted(
        entradas,
        key=lambda e: (e.data_recebimento or e.data_competencia or date.min, e.public_code),
        reverse=True,
    )[:10]
    def _referencia(e: FinancialEntry) -> str | None:
        """O código público do que o lançamento paga — nunca texto livre."""
        if e.spirometry_exam_id:
            exame = db.get(SpirometryExam, e.spirometry_exam_id)
            return exame.public_code if exame else None
        if e.consultation_id:
            consulta = db.get(Consultation, e.consultation_id)
            return consulta.public_code if consulta else None
        if e.partner_settlement_id:
            s = settlement_por_id.get(e.partner_settlement_id)
            unidade = db.get(PartnerUnit, s.partner_unit_id) if s and s.partner_unit_id else None
            return unidade.public_code if unidade else None
        return None

    # `FinancialEntry.descricao` é texto livre que o operador pode digitar.
    # O validador de escrita recusa PII ali, mas um snapshot marcado como
    # seguro não deve depender de um detector para continuar seguro: o que
    # sai daqui é vocabulário fechado (categoria), código público
    # (referência) e competência. O painel compõe a frase na tela.
    lancamentos_recentes = [
        {
            "public_code": e.public_code,
            "data": _br_date(e.data_recebimento or e.data_competencia),
            "data_iso": (e.data_recebimento or e.data_competencia).isoformat()
            if (e.data_recebimento or e.data_competencia) else None,
            "categoria": e.categoria,
            "referencia": _referencia(e),
            "competencia": (
                e.data_competencia.strftime("%Y-%m")
                if e.partner_settlement_id and e.data_competencia else None
            ),
            "origem": _origem(e),
            "valor": _brl(e.valor),
            "tipo": e.tipo,
            "status": e.status,
        }
        for e in recentes
    ]

    total_origens = sum(por_origem.values())
    origens = [
        {
            "origem": nome,
            "valor": _brl(valor),
            "percentual": (
                round(float(valor) / float(total_origens) * 100, 1)
                if total_origens else None
            ),
        }
        for nome, valor in sorted(por_origem.items(), key=lambda kv: -kv[1])
    ]

    a_receber_total = receita_pendente + a_receber_parceria

    return {
        "source": _source(
            "financeiro_summary",
            "Fonte financeira única: tabela financial_entries do PostgreSQL, "
            "mais o que os fechamentos de parceria já devem. Nenhum valor é "
            "derivado de planilha, média ou estimativa.",
        ),
        "periodo": competencia_atual,
        "competencia_atual": competencia_atual,
        "competencia_atual_label": _competencia_label(competencia_atual),
        "totais": {
            "receita_recebida": _brl(receita_recebida),
            "receita_pendente": _brl(receita_pendente),
            "a_receber_parceria": _brl(a_receber_parceria),
            "a_receber_total": _brl(a_receber_total),
            "cancelados": cancelados,
            # Quantos lançamentos SUSTENTAM a receita recebida — não quantos
            # existem. O card diz "confirmado(s)"; contar pendente e cancelado
            # ali faria o rótulo mentir.
            "lancamentos_recebidos": lancamentos_recebidos,
            "lancamentos_validos": len(entradas),
        },
        "exames_pagos": exames_pagos,
        "exames_parceria_recebidos": exames_parceria_recebidos,
        "exames_reconhecidos": exames_reconhecidos,
        "exames_em_fechamento_aberto": exames_em_fechamento_aberto,
        "receita_media_por_exame": receita_media_por_exame,
        # Métrica antiga aposentada: dividia receita por lançamento e um
        # recibo de fechamento cobre dezenas de exames. Explicitamente None
        # para que nenhum consumidor herde o número errado em silêncio.
        "ticket_medio_real": None,
        # Não há tabela de preço canônica no banco: não inventar.
        "valor_base_exame": None,
        "parcerias_sem_regra": parcerias_sem_regra,
        "por_status": [
            {"status": s, "quantidade": v["quantidade"], "valor": _brl(v["valor"])}
            for s, v in sorted(por_status.items())
        ],
        "por_competencia": [
            {"competencia": m, "label": _competencia_label(m), "valor": _brl(v)}
            for m, v in sorted(por_competencia.items())
        ],
        "por_origem": origens,
        "receita_sem_competencia": _brl(receita_sem_competencia),
        "lancamentos_sem_competencia": lancamentos_sem_competencia,
        "por_categoria": [
            {"categoria": c, "valor": _brl(v)}
            for c, v in sorted(por_categoria.items(), key=lambda kv: -kv[1])
        ],
        "lancamentos_recentes": lancamentos_recentes,
        # Chaves de compatibilidade consumidas por operational-brain.js e
        # generate-ultimos-lancamentos.py — mesmos nomes de antes.
        "por_mes": [{"mes": m, "valor": _brl(v)} for m, v in sorted(por_competencia.items())],
        "receita_exames": _brl(receita_recebida),
        "espirometrias_pagas": exames_pagos,
        "total_lancamentos": len(entradas),
        "total_entradas_mes_atual": _brl(total_mes_atual),
        # Saldo bancário não existe no banco operacional — nunca fabricar 0,00.
        "saldo_operacional": None,
    }


def build_followup_pacientes_summary(db: Session) -> dict:
    """Contagens de follow-up por origem clínica. Nenhum paciente é nomeado."""
    hoje = datetime.now(timezone.utc).date()
    espi = _empty_buckets()
    cons = _empty_buckets()

    pendentes = db.scalars(
        select(Followup).where(Followup.status == "pendente")
    ).all()

    for f in pendentes:
        alvo = espi if f.tipo == "pos_exame" else cons if f.tipo == "pos_consulta" else None
        if alvo is None:
            continue
        alvo["total"] += 1
        alvo[_bucket(f.due_date, hoje)] += 1

    return {
        "geradoEm": _now_iso(),
        "referencia": hoje.isoformat(),
        "safeToDisplay": True,
        "containsPersonalData": False,
        "containsHealthData": False,
        "source": _source("followup_pacientes_summary"),
        "espirometria": espi,
        "consultas": cons,
    }


def build_followup_clinicas_summary(db: Session) -> dict:
    """Follow-up comercial B2B agregado por etapa do parceiro."""
    hoje = datetime.now(timezone.utc).date()
    buckets = _empty_buckets()

    pendentes = db.scalars(
        select(Followup).where(
            Followup.status == "pendente",
            Followup.tipo == "encaminhamento_parceiro",
        )
    ).all()
    for f in pendentes:
        buckets["total"] += 1
        buckets[_bucket(f.due_date, hoje)] += 1

    por_etapa = Counter(
        p.status for p in db.scalars(
            select(Partner).where(Partner.arquivado.is_(False))
        ).all()
    )

    return {
        "geradoEm": _now_iso(),
        "safeToDisplay": True,
        "containsPersonalData": False,
        "source": _source("followup_clinicas_summary"),
        "clinicas": buckets,
        "por_etapa": dict(sorted(por_etapa.items())),
    }


def build_contatos_b2b_summary(db: Session) -> dict:
    """Contatos B2B SEM identificação: só vínculo e contagem por parceiro."""
    contatos = db.scalars(
        select(PartnerContact).where(PartnerContact.ativo.is_(True))
    ).all()
    codigos = {
        p.id: p.public_code
        for p in db.scalars(select(Partner)).all()
    }

    itens = [
        _clean({
            "contato_id": c.public_code,
            "clinica_id": codigos.get(c.partner_id),
            "principal": c.principal,
            # Quantos canais o contato tem cadastrados — nunca QUAIS, nunca o
            # valor. Nomes de chave com "telefone"/"email" são proibidos em
            # summary seguro mesmo carregando só um booleano.
            "canais_registrados": sum(1 for v in (c.telefone, c.email) if v),
        })
        for c in contatos
    ]

    return {
        "source": _source(
            "contatos_b2b_summary",
            "Somente vínculos e códigos públicos. Nome, cargo, telefone e "
            "e-mail do contato ficam apenas no banco e na API autenticada.",
        ),
        "contatos": itens,
    }


#: Ordem de prioridade institucional para escolher UM papel por usuário
#: quando há mais de um. Reflete a hierarquia real de app.security.ROLE_IMPLIES.
_ROLE_PRIORIDADE = ("admin", "gestor", "operacional", "leitura")

#: Fragmentos que, quando presentes no nome real da ação (dado do banco),
#: sinalizam falha/rejeição/bloqueio. Não é inferência sobre o negócio —
#: é o próprio texto de `acao` já gravado pela API no momento do evento.
_ACAO_FALHA_FRAGMENTOS = ("falha", "erro", "rejeit", "bloqueado")


def _resultado_from_acao(acao: str | None) -> str:
    """Deriva 'resultado' do texto real de 'acao' — nunca fabrica sucesso.

    O modelo AuditLog não tem uma coluna de resultado explícita. Se o nome
    da ação não sinaliza falha/rejeição/bloqueio, o evento só existe na
    trilha porque a transação correspondente foi commitada — dizer 'ok'
    aqui é ler um fato do banco (a ação gravada), não inventar um veredito
    que o banco não prova.
    """
    termo = (acao or "").lower()
    if any(frag in termo for frag in _ACAO_FALHA_FRAGMENTOS):
        return "falha"
    return "ok"


def build_auditoria_summary(db: Session) -> dict:
    """Trilha de auditoria agregada, no contrato de segurança REAL validado
    por scripts/check-access.sh (ALLOWED_EVENT_KEYS em
    scripts/audit_summary_contract.py): cada evento carrega só
    ``timestamp``, ``acao``, ``entidade_tipo``, ``operador`` e
    ``resultado``.

    NENHUM identificador de registro sai daqui. ``detalhes``, o UUID de
    usuário e o ``entidade_id`` da linha auditada ficam no banco:
    'operador' é sempre um papel institucional (admin/gestor/operacional/
    leitura) e 'entidade_tipo' é o nome da tabela — ambos de domínio
    fechado. O que o painel precisa de granularidade está em ``stats``,
    como contagem, não como ponteiro para um registro.
    """
    total = db.scalar(select(func.count()).select_from(AuditLog)) or 0

    # user_id (UUID interno, nunca exportado) -> papel institucional mais
    # alto do usuário. Usuário sem papel ou não encontrado fica de fora do
    # mapa: 'operador' vira None nesse evento — sem fonte confiável, não se
    # inventa um valor.
    papel_por_usuario: dict[str, str] = {}
    for user_id, role_name in db.execute(
        select(UserRole.user_id, Role.name).join(Role, Role.id == UserRole.role_id)
    ).all():
        atual = papel_por_usuario.get(user_id)
        if atual is None or (
            role_name in _ROLE_PRIORIDADE
            and (atual not in _ROLE_PRIORIDADE
                 or _ROLE_PRIORIDADE.index(role_name) < _ROLE_PRIORIDADE.index(atual))
        ):
            papel_por_usuario[user_id] = role_name

    por_acao = Counter()
    por_dia = Counter()
    por_operador = Counter()
    por_entidade = Counter()
    por_resultado = Counter()
    erros = 0
    for acao, entidade, ts, user_id in db.execute(
        select(AuditLog.acao, AuditLog.entidade, AuditLog.ts_utc, AuditLog.user_id)
    ).all():
        por_acao[acao] += 1
        if ts:
            por_dia[ts.date().isoformat()] += 1
        resultado = _resultado_from_acao(acao)
        por_resultado[resultado] += 1
        if resultado == "falha":
            erros += 1
        if entidade:
            por_entidade[entidade] += 1
        operador = papel_por_usuario.get(user_id)
        if operador:
            por_operador[operador] += 1

    # AuditLog.entidade_id NÃO entra na projeção: o que não é lido do banco
    # não pode escapar por descuido em uma edição futura.
    ultimos = [
        _clean({
            "timestamp": row.ts_utc.isoformat() if row.ts_utc else None,
            "acao": row.acao,
            "entidade_tipo": row.entidade,
            "operador": papel_por_usuario.get(row.user_id),
            "resultado": _resultado_from_acao(row.acao),
        })
        for row in db.execute(
            select(AuditLog.acao, AuditLog.entidade,
                   AuditLog.user_id, AuditLog.ts_utc)
            .order_by(AuditLog.ts_utc.desc())
            .limit(_ULTIMOS_EVENTOS)
        ).all()
    ]

    return {
        "source": _source(
            "auditoria_summary",
            "Trilha append-only do PostgreSQL. Nenhum identificador de "
            "registro é exportado: 'detalhes', o identificador de usuário e "
            "o identificador da entidade auditada ficam no banco. 'operador' "
            "é o papel institucional (admin/gestor/operacional/leitura) do "
            "autor do evento e 'entidade_tipo' é o nome da tabela — nunca "
            "nome pessoal, UUID ou ponteiro para um registro.",
        ),
        "stats": {
            "total_eventos": total,
            "erros": erros,
            "por_acao": dict(sorted(por_acao.items(), key=lambda kv: -kv[1])),
            "por_operador": dict(sorted(por_operador.items(), key=lambda kv: -kv[1])),
            "por_entidade": dict(sorted(por_entidade.items(), key=lambda kv: -kv[1])),
            "por_resultado": dict(sorted(por_resultado.items(), key=lambda kv: -kv[1])),
        },
        "eventos_por_dia": [
            {"dia": d, "eventos": n} for d, n in sorted(por_dia.items())
        ],
        "ultimos_eventos": ultimos,
    }


def build_pastore_summary(db: Session) -> dict:
    """Parceria Pastore. Valores comerciais seguem indefinidos por decisão."""
    partner = db.scalars(
        select(Partner).where(
            Partner.arquivado.is_(False),
            func.lower(Partner.nome) == "pastore",
        )
    ).first()

    if partner is None:
        return {
            "source": _source(
                "pastore_summary",
                "Nenhum parceiro canônico 'Pastore' ativo no banco.",
            ),
            "parceria": None,
            "disponivel": False,
        }

    unidades = db.scalars(
        select(PartnerUnit).where(
            PartnerUnit.partner_id == partner.id,
            PartnerUnit.ativo.is_(True),
        )
    ).all()
    unidade_ids = [u.id for u in unidades]

    exames = db.scalars(
        select(SpirometryExam).where(SpirometryExam.partner_id == partner.id)
    ).all()

    producao = Counter()
    com_bd = sem_bd = 0
    for e in exames:
        if e.data_exame:
            producao[e.data_exame.isoformat()] += 1
        if e.broncodilatador is True:
            com_bd += 1
        elif e.broncodilatador is False:
            sem_bd += 1

    agenda = []
    for u in unidades:
        for cfg in db.scalars(
            select(PartnerUnitConfig).where(PartnerUnitConfig.partner_unit_id == u.id)
        ).all():
            horario = None
            if cfg.horario_inicio and cfg.horario_fim:
                horario = f"{cfg.horario_inicio}–{cfg.horario_fim}"
            agenda.append(_clean({
                "unidade": u.nome,
                "dia_semana": cfg.dia_semana,
                "horario": horario,
                "status": cfg.status,
                "capacidade_estimada_por_turno": cfg.capacidade_estimada_por_turno,
            }))

    fechamentos = db.scalars(
        select(PartnerSettlement).where(PartnerSettlement.partner_id == partner.id)
    ).all()
    por_status = Counter(f.status for f in fechamentos)
    recebido = sum(
        (f.valor_total or Decimal("0"))
        for f in fechamentos
        if f.status == "recebido" and f.valor_total is not None
    )

    labels = sorted(producao)
    return {
        "source": _source(
            "pastore_summary",
            "Agregados do PostgreSQL. Valor, preço e percentual de repasse "
            "continuam indefinidos por decisão de negócio (M22) — não são "
            "inferidos aqui.",
        ),
        "disponivel": True,
        "parceria": {
            "nome": partner.nome,
            "unidade": unidades[0].nome if len(unidades) == 1 else None,
            "servico": "Espirometria",
            "status": partner.status,
        },
        "kpis": {
            "exames_realizados": len(exames),
            # Só fechamento recebido vira receita — exame Pastore não gera
            # recebível individual.
            "receita_confirmada": _brl(recebido) if fechamentos else None,
            "fechamentos": dict(sorted(por_status.items())),
        },
        "producao_por_data": {
            "labels": labels,
            "exames": [producao[d] for d in labels],
        },
        "agenda": agenda,
        "pacientes_pastore": {
            "total_atendidos": len({e.person_id for e in exames}),
            "distribuicao_tipo_exame": {
                "sem_broncodilatador": sem_bd,
                "com_broncodilatador": com_bd,
            },
        },
        "financeiro_parametros": {
            "valor_exame_sem_broncodilatador": None,
            "valor_exame_com_broncodilatador": None,
            "repasse_percentual_pastore": None,
            "nota": "Valores comerciais não definidos; o sistema não infere.",
        },
    }


def build_resumo_dashboard(db: Session) -> dict:
    """Cards do Painel Geral, contados diretamente no banco.

    As chaves seguem a allowlist fechada validada por check-access.sh — o
    contrato do resumo seguro não é ampliado aqui. Cards trazem apenas
    ``key``/``label``/``value``: não existe série histórica no banco, então
    nenhuma variação percentual é exibida (inventá-la seria estimativa).
    """
    hoje = datetime.now(timezone.utc).date()
    inicio_mes = hoje.replace(day=1)

    leads_total = db.scalar(select(func.count()).select_from(Lead)) or 0
    leads_mes = db.scalar(
        select(func.count()).select_from(Lead)
        .where(Lead.data_primeiro_contato >= inicio_mes)
    ) or 0
    exames_total = db.scalar(select(func.count()).select_from(SpirometryExam)) or 0
    parceiros_total = db.scalar(
        select(func.count()).select_from(Partner).where(Partner.arquivado.is_(False))
    ) or 0
    followups_pendentes = db.scalar(
        select(func.count()).select_from(Followup)
        .where(Followup.status == "pendente")
    ) or 0
    # Só teleconsultas: a chave allowlistada tem esse significado exato e não
    # seria honesto usá-la para o total de consultas de todas as modalidades.
    teleconsultas = db.scalar(
        select(func.count()).select_from(Consultation)
        .where(Consultation.modalidade == "teleconsulta")
    ) or 0
    receita = db.scalar(
        select(func.coalesce(func.sum(FinancialEntry.valor), 0)).where(
            FinancialEntry.tipo == "receita",
            FinancialEntry.status == "Recebido",
        )
    ) or Decimal("0")

    def card(key, label, value):
        return {"key": key, "label": label, "value": value}

    receita_br = f"{float(receita):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

    return {
        "source": _source(
            "resumo_dashboard",
            "Contagens diretas do PostgreSQL. Sem variação percentual "
            "histórica: o banco não guarda a série anterior e nada é estimado.",
        ),
        "empresa": "SoproLife",
        "periodo": hoje.strftime("%Y-%m"),
        "cards": [
            card("totalLeads", "Total de leads", leads_total),
            card("leadsNovos", "Leads no mês", leads_mes),
            card("clinicasCadastradas", "Parceiros ativos", parceiros_total),
            card("examesEspirometriaRealizados", "Espirometrias", exames_total),
            card("teleconsultasRealizadas", "Teleconsultas", teleconsultas),
            card("followupsPendentes", "Follow-ups pendentes", followups_pendentes),
            card("receitaRecebida", "Receita recebida", f"R$ {receita_br}"),
        ],
    }


BUILDERS = {
    "resumo-dashboard.local.json": build_resumo_dashboard,
    "leads-summary.local.json": build_leads_summary,
    "crm-clinicas.local.json": build_crm_clinicas,
    "financeiro-summary.local.json": build_financeiro_summary,
    "followup-pacientes-summary.local.json": build_followup_pacientes_summary,
    "followup-clinicas-summary.local.json": build_followup_clinicas_summary,
    "crm-contatos-b2b-summary.local.json": build_contatos_b2b_summary,
    "auditoria-summary.local.json": build_auditoria_summary,
    "parcerias-pastore-summary.local.json": build_pastore_summary,
}


# ------------------------------------------------------------------------ escrita

# Chaves que jamais podem aparecer num snapshot seguro. Rede final: os
# construtores já não leem esses campos, mas um erro futuro de edição não pode
# passar silenciosamente para o disco.
_FORBIDDEN_KEYS = frozenset({
    "nome", "nome_completo", "telefone", "telefone_whatsapp", "celular",
    "cpf", "email", "e_mail", "endereco", "observacao", "observação",
    "laudo", "diagnostico", "diagnóstico", "pedido_medico", "detalhes",
})

# Exceções institucionais: nome de EMPRESA parceira e do próprio negócio.
_ALLOWED_NAME_PATHS = frozenset({"clinicas.nome_clinica", "parceria.nome",
                                 "agenda.unidade"})

#: Ruleset registrado em scripts/pii_guard.py para estes snapshots.
PII_RULESET = "m23-snapshots"

# Caminho da guarda de PII compartilhada do painel. Ela vive em
# painel-soprolife/scripts/ e é carregada por caminho explícito porque não faz
# parte do pacote da API.
_PII_GUARD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pii_guard.py"


def _load_pii_guard():
    """Carrega a guarda compartilhada. Ausência é ERRO, nunca permissão."""
    import importlib.util

    if not _PII_GUARD_PATH.is_file():
        raise RuntimeError(f"Guarda de PII não encontrada em {_PII_GUARD_PATH}")
    spec = importlib.util.spec_from_file_location("_m23_pii_guard", _PII_GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Guarda de PII ilegível em {_PII_GUARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Caminho do contrato real de auditoria (mesma allowlist usada por
# scripts/check-access.sh). Carregado do mesmo jeito que a guarda de PII:
# check-access.sh vive fora do pacote da API.
_AUDIT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "audit_summary_contract.py"
)


def _load_audit_contract():
    """Carrega o contrato de auditoria compartilhado. Ausência é ERRO."""
    import importlib.util

    if not _AUDIT_CONTRACT_PATH.is_file():
        raise RuntimeError(f"Contrato de auditoria não encontrado em {_AUDIT_CONTRACT_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_m23_audit_contract", _AUDIT_CONTRACT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Contrato de auditoria ilegível em {_AUDIT_CONTRACT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guard_rules(guard) -> dict:
    return guard._FILE_RULESETS[PII_RULESET]


def validate_snapshot(name: str, payload: dict) -> list[str]:
    """Verifica ausência de chaves de PII antes de gravar."""
    problemas: list[str] = []

    def walk(node, path: str):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else key
                leaf = here.split(".")[-1]
                parent = ".".join(here.split(".")[-2:])
                if leaf in _FORBIDDEN_KEYS and parent not in _ALLOWED_NAME_PATHS:
                    problemas.append(f"{name}: chave proibida '{here}'")
                walk(value, here)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(payload, "")
    return problemas


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
        path.chmod(0o644)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def export_snapshots(db: Session, out_dir: Path, *, write: bool = False) -> dict:
    """Gera todos os snapshots. Em dry-run nada é escrito.

    Levanta ``ValueError`` se qualquer payload contiver chave de PII: um
    snapshot inválido nunca substitui um snapshot válido no disco.
    """
    resultados = []
    problemas: list[str] = []
    payloads: dict[str, dict] = {}

    guard = _load_pii_guard()
    rules = _guard_rules(guard)
    audit_contract = _load_audit_contract()

    for name in SNAPSHOT_FILES:
        payload = BUILDERS[name](db)
        payloads[name] = payload
        # Camadas de validação antes de qualquer escrita: chaves proibidas
        # (estrutural, aqui), a guarda de PII compartilhada do painel
        # (conteúdo: telefone, CPF, e-mail, token, termo clínico, URL de
        # planilha) e, para a auditoria especificamente, o MESMO contrato
        # real que scripts/check-access.sh usa — a divergência entre essas
        # duas pontas foi exatamente o que quebrou o primeiro deploy do M23.
        problemas.extend(validate_snapshot(name, payload))
        problemas.extend(guard.validate_summary(payload, rules, context=name))
        if name == "auditoria-summary.local.json":
            problemas.extend(
                f"{name}: {msg}"
                for msg in audit_contract.validate_auditoria_payload(payload)
            )
        resultados.append({"arquivo": name, "chaves": sorted(payload.keys())})

    if problemas:
        raise ValueError("Snapshot inseguro — nada foi gravado:\n  " + "\n  ".join(problemas))

    if write:
        for name, payload in payloads.items():
            _write_atomic(out_dir / name, payload)

    return {
        "modo": "write" if write else "dry-run",
        "destino": str(out_dir),
        "gerados": resultados,
        "fonte": SOURCE_TYPE,
        "generatedAt": _now_iso(),
    }
