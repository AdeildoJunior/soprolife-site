"""Motor de follow-up semiautomático — ciclo de vida completo.

Regras (decisões de negócio M15 + hardening M15.1A):
- exame/consulta realizados -> data do atendimento + 6 meses;
- lead sem atendimento -> retomada manual (precedência) ou primeiro contato;
- pessoa "não contatar" NUNCA entra (nem volta) para a fila;
- consentimento de WhatsApp é FAIL-CLOSED: sem "concedido", nada de URL;
- follow-up controlado pelo parceiro não expõe contato da SoproLife;
- criação atômica (SAVEPOINT + índice único parcial) — sem duplicatas,
  inclusive quando origem_id é NULL;
- transições de status validadas; alteração da origem sincroniza o
  follow-up NA MESMA transação; nova tentativa limpa a conclusão anterior.
"""

from datetime import date, datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..dates import add_months
from ..ids import allocate_public_code
from ..models import Consent, Followup, Interaction, Person

FOLLOWUP_MONTHS = 6

FILA_ATRASADO = "atrasado"
FILA_HOJE = "retomar_hoje"
FILA_SEMANA = "retomar_semana"
FILA_AGUARDANDO = "aguardando_data"
FILA_CONCLUIDO = "concluido"
FILA_NAO_CONTATAR = "nao_contatar"

# Transições válidas de status do follow-up.
STATUS_TRANSITIONS = {
    "pendente": {"concluido", "cancelado"},
    "concluido": {"pendente"},   # nova tentativa
    "cancelado": {"pendente"},   # reagendamento explícito
}


def ensure_transition(fup: Followup, novo_status: str) -> None:
    if novo_status == fup.status:
        return
    allowed = STATUS_TRANSITIONS.get(fup.status, set())
    if novo_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "codigo": "transicao_invalida",
                "mensagem": f"Transição de follow-up '{fup.status}' -> '{novo_status}' não é permitida.",
            },
        )


def today_local() -> date:
    tz = ZoneInfo(get_settings().display_timezone)
    return datetime.now(timezone.utc).astimezone(tz).date()


def due_after_attendance(base: date) -> date:
    return add_months(base, FOLLOWUP_MONTHS)


def find_pending(
    db: Session, person_id: str, tipo: str, origem_id: str | None
) -> Followup | None:
    stmt = select(Followup).where(
        Followup.person_id == person_id,
        Followup.tipo == tipo,
        Followup.status == "pendente",
    )
    if origem_id is None:
        stmt = stmt.where(Followup.origem_id.is_(None))
    else:
        stmt = stmt.where(Followup.origem_id == origem_id)
    return db.execute(stmt).scalars().first()


def schedule_followup(
    db: Session,
    person: Person,
    tipo: str,
    origem_entidade: str | None = None,
    origem_id: str | None = None,
    due: date | None = None,
    due_manual: bool = False,
    responsavel: str | None = None,
    controlado_por_parceiro: bool = False,
    partner_id: str | None = None,
    observacao: str | None = None,
) -> tuple[Followup | None, str]:
    """Cria follow-up de forma atômica, respeitando 'não contatar' e dedupe.

    Retorna (followup|None, motivo). O índice parcial único garante a
    unicidade sob corrida; perder a corrida devolve o registro vencedor.
    """
    if person.nao_contatar:
        return None, "pessoa_nao_contatar"
    existing = find_pending(db, person.id, tipo, origem_id)
    if existing is not None:
        return existing, "ja_existente"
    try:
        with db.begin_nested():
            fup = Followup(
                public_code=allocate_public_code(db, "followups"),
                person_id=person.id,
                tipo=tipo,
                origem_entidade=origem_entidade,
                origem_id=origem_id,
                due_date=due,
                due_date_manual=due_manual,
                responsavel=responsavel,
                controlado_por_parceiro=controlado_por_parceiro,
                partner_id=partner_id,
                observacao=observacao,
            )
            db.add(fup)
        return fup, "criado"
    except IntegrityError:
        existing = find_pending(db, person.id, tipo, origem_id)
        if existing is not None:
            return existing, "ja_existente"
        raise


def sync_followup_for_origin(
    db: Session,
    person: Person,
    tipo: str,
    origem_entidade: str,
    origem_id: str,
    due: date | None,
    active: bool,
    responsavel: str | None = None,
    controlado_por_parceiro: bool | None = None,
    partner_id: str | None = None,
) -> tuple[Followup | None, str]:
    """Sincroniza o follow-up com o estado da origem NA MESMA transação.

    - active=True: cria ou atualiza (vencimento não-manual, responsável,
      controle de parceiro) o pendente da origem;
    - active=False: cancela o pendente da origem, se existir.
    """
    existing = find_pending(db, person.id, tipo, origem_id)
    if not active or person.nao_contatar:
        if existing is not None:
            existing.status = "cancelado"
            db.flush()
            return existing, "cancelado"
        return None, "nao_aplicavel"
    if existing is not None:
        changed = False
        if due is not None and not existing.due_date_manual and existing.due_date != due:
            existing.due_date = due
            changed = True
        if responsavel is not None and existing.responsavel != responsavel:
            existing.responsavel = responsavel
            changed = True
        if (controlado_por_parceiro is not None
                and existing.controlado_por_parceiro != controlado_por_parceiro):
            existing.controlado_por_parceiro = controlado_por_parceiro
            changed = True
        if changed:
            db.flush()
        return existing, "atualizado" if changed else "ja_existente"
    return schedule_followup(
        db, person, tipo, origem_entidade, origem_id, due=due,
        responsavel=responsavel,
        controlado_por_parceiro=bool(controlado_por_parceiro),
        partner_id=partner_id,
    )


def classify_queue(fup: Followup, person: Person, today: date | None = None) -> str:
    if person.nao_contatar:
        return FILA_NAO_CONTATAR
    if fup.status == "concluido":
        return FILA_CONCLUIDO
    today = today or today_local()
    if fup.due_date is None:
        return FILA_AGUARDANDO
    if fup.due_date < today:
        return FILA_ATRASADO
    if fup.due_date == today:
        return FILA_HOJE
    # dentro da semana corrente (segunda a domingo)
    week_end = add_days(today, 6 - today.weekday())
    if fup.due_date <= week_end:
        return FILA_SEMANA
    return FILA_AGUARDANDO


def add_days(base: date, days: int) -> date:
    from datetime import timedelta

    return base + timedelta(days=days)


def whatsapp_consent_status(db: Session, person_id: str) -> str:
    row = db.execute(
        select(Consent)
        .where(Consent.person_id == person_id, Consent.canal == "whatsapp")
        .order_by(Consent.ts_utc.desc())
        .limit(1)
    ).scalars().first()
    return row.status if row else "desconhecido"


def build_whatsapp_url(phone_normalized: str, message: str) -> str:
    """Monta a URL wa.me. NUNCA dispara envio — abre para revisão humana."""
    return f"https://wa.me/{phone_normalized}?text={quote(message)}"


def default_message(person_nome: str, contexto: str | None = None) -> str:
    primeiro_nome = person_nome.split(" ")[0] if person_nome else ""
    base = f"Olá {primeiro_nome}, aqui é da SoproLife."
    if contexto == "pos_exame":
        return (
            f"{base} Faz cerca de 6 meses desde a sua espirometria. "
            "Podemos conversar sobre o acompanhamento?"
        )
    if contexto == "pos_consulta":
        return (
            f"{base} Faz cerca de 6 meses desde a sua consulta. "
            "Que tal agendarmos o seu retorno?"
        )
    return f"{base} Podemos falar sobre o seu atendimento?"


def complete_followup(
    db: Session,
    fup: Followup,
    user_id: str | None,
    resultado: str | None = None,
    observacao: str | None = None,
) -> Followup:
    ensure_transition(fup, "concluido")
    fup.status = "concluido"
    fup.concluido_em = datetime.now(timezone.utc)
    fup.concluido_por = user_id
    if resultado:
        fup.resultado = resultado
    if observacao:
        fup.observacao = observacao
    db.flush()
    return fup


def retry_followup(
    db: Session, fup: Followup, nova_data: date, observacao: str | None = None
) -> Followup:
    """Nova tentativa: reabre o registro limpando a conclusão anterior."""
    ensure_transition(fup, "pendente")
    fup.tentativas += 1
    fup.due_date = nova_data
    fup.due_date_manual = True
    fup.status = "pendente"
    fup.concluido_em = None
    fup.concluido_por = None
    fup.resultado = None
    if observacao:
        fup.observacao = observacao
    db.flush()
    return fup


def record_confirmed_interaction(
    db: Session,
    fup: Followup,
    user_id: str | None,
    resumo: str | None,
    resultado: str,
) -> Interaction:
    """Registra a interação SOMENTE após confirmação humana do envio."""
    interaction = Interaction(
        public_code=allocate_public_code(db, "interactions"),
        person_id=fup.person_id,
        canal="whatsapp",
        direcao="enviado",
        resumo=resumo,
        resultado=resultado,
        user_id=user_id,
        followup_id=fup.id,
    )
    fup.tentativas += 1
    db.add(interaction)
    db.flush()
    return interaction
