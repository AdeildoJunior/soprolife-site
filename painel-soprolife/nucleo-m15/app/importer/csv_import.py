"""Importador seguro de CSVs legados (Sheets -> Núcleo M15).

Garantias (M15.1 + hardening M15.1A):
- dry-run padrão: NENHUMA escrita de dados sem execute=True;
- decoding ESTRITO (UTF-8/UTF-8-BOM apenas — encoding inválido falha fechado);
- limites duros de bytes, linhas, colunas e tamanho de campo;
- datas não vazias e inválidas são REJEITADAS (nunca viram NULL silencioso);
- células iniciadas por = + - @ (fora número/data) são rejeitadas
  (CSV injection) — fórmulas nunca são executadas nem reproduzidas;
- ordem segura: espirometrias/consultas com paciente_id exigem pacientes já
  importados (fail-closed); linha só com nome cria pessoa PLACEHOLDER marcada;
- paciente já importado é ENRIQUECIDO de forma auditável (nunca descartado);
- hash SHA-256 da fonte; unique constraint impede duas execuções do mesmo
  arquivo; advisory lock (PostgreSQL) serializa executes simultâneos;
- IDs antigos preservados em legacy_aliases (nunca renumerados);
- candidatos de identidade registrados — NENHUMA fusão silenciosa;
- transação por lote: erro -> rollback completo (o chamador comita);
- linhas/relatórios sem PII bruta: hash, origem, número da linha e motivo;
- linhas PCMSO rejeitadas (fora da operação ativa; histórico fica na fonte);
- dados reais nunca entram em fixtures nem no Git.
"""

import csv
import hashlib
import io
import json
import pathlib
import re
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..dates import parse_incomplete_date
from ..domain import pcmso_violation
from ..ids import allocate_public_code, new_uuid
from ..models import (
    Consultation,
    ImportBatch,
    ImportRow,
    Lead,
    LegacyAlias,
    Partner,
    PartnerContact,
    Person,
    PersonContact,
    SpirometryExam,
)
from ..normalize import normalize_name, normalize_phone
from ..services.followup import due_after_attendance, schedule_followup
from ..services.identity import find_person_candidates, register_candidates

IMPORT_TYPES = {
    "leads",
    "crm_pacientes",
    "crm_espirometria",
    "crm_consultas",
    "contatos_b2b",
}

# Ordem segura recomendada (documentada): pacientes -> espirometrias ->
# consultas -> leads -> parceiros. A dependência dura (fail-closed) é:
# espirometria/consulta com paciente_id exige o paciente já importado.

# Limites duros do importador (CLI e API).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLS = 100
MAX_FIELD_CHARS = 2_000

# Encodings permitidos: UTF-8 com ou sem BOM. Qualquer byte inválido falha.
ALLOWED_ENCODING = "utf-8-sig"

_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_DATELIKE_RE = re.compile(r"^[+-]?\d[\d/\-.]*$")

PLACEHOLDER_MARK = "[PLACEHOLDER-IMPORT]"


class ImportFormatError(ValueError):
    """Erro de formato/limite da fonte — aborta o lote inteiro."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pick(row: dict, *names: str) -> str:
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower().replace(" ", "_") == name:
                if value and str(value).strip():
                    return str(value).strip()
    return ""


def _formula_cell(value: str) -> bool:
    """Célula que um editor de planilha interpretaria como fórmula.

    '-' e '+' são tolerados quando o conteúdo é número ou data ("-5", "+55...").
    """
    if not value:
        return False
    first = value[0]
    if first in ("=", "@"):
        return True
    if first in ("+", "-"):
        stripped = value.replace(" ", "").replace("(", "").replace(")", "")
        return not (_NUMERIC_RE.match(stripped) or _DATELIKE_RE.match(stripped)
                    or normalize_phone(value))
    return False


def _row_injection(row: dict) -> str | None:
    for key, value in row.items():
        if value and _formula_cell(str(value).strip()):
            return key or "coluna"
    return None


def _is_pcmso_row(row: dict) -> bool:
    # concatena todos os valores textuais sob um campo inspecionado pela
    # regra central — qualquer coluna com marcador PCMSO rejeita a linha
    joined = " | ".join(str(v) for v in row.values() if v)
    return pcmso_violation({"observacao": joined}) is not None


def _read_rows(content: bytes) -> list[dict]:
    """Parse estrito com limites. Falha fechada em encoding/tamanho."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportFormatError(
            f"Arquivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        text_content = content.decode(ALLOWED_ENCODING, errors="strict")
    except UnicodeDecodeError as exc:
        raise ImportFormatError(
            "Encoding inválido: o importador aceita apenas UTF-8 (com ou sem BOM). "
            f"Byte problemático na posição {exc.start}."
        ) from exc
    if "\x00" in text_content:
        raise ImportFormatError("Arquivo contém byte NUL e não é um CSV textual válido.")
    sample = text_content[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)
    if not reader.fieldnames:
        raise ImportFormatError("CSV sem cabeçalho.")
    normalized_headers = [
        (name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames
    ]
    if any(not name for name in normalized_headers):
        raise ImportFormatError("CSV contém coluna sem nome no cabeçalho.")
    if len(set(normalized_headers)) != len(normalized_headers):
        raise ImportFormatError("CSV contém colunas duplicadas no cabeçalho.")
    if reader.fieldnames and len(reader.fieldnames) > MAX_COLS:
        raise ImportFormatError(f"Mais de {MAX_COLS} colunas.")
    rows: list[dict] = []
    for row in reader:
        if None in row:
            raise ImportFormatError(
                f"Linha {len(rows) + 2} possui mais campos que o cabeçalho."
            )
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        for key, value in row.items():
            if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
                raise ImportFormatError(
                    f"Campo com mais de {MAX_FIELD_CHARS} caracteres "
                    f"(coluna {key!r}, linha {len(rows) + 2})."
                )
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ImportFormatError(f"Mais de {MAX_ROWS} linhas.")
    return rows


def _parse_date_or_reject(raw: str):
    """Data vazia -> (None-normalizada, ok). Não vazia inválida -> rejeição."""
    nd = parse_incomplete_date(raw)
    if raw.strip() and nd.value is None:
        return nd, False
    return nd, True


def _find_alias(db: Session, entidade: str, source: str, legacy_id: str) -> str | None:
    row = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == entidade,
            LegacyAlias.legacy_source == source,
            LegacyAlias.legacy_id == legacy_id,
        )
    ).scalar_one_or_none()
    return row.entity_id if row else None


class _Ctx:
    """Estado de uma execução de importação."""

    def __init__(self, db: Session, source_type: str, batch: ImportBatch | None, execute: bool):
        self.db = db
        self.source_type = source_type
        self.batch = batch
        self.execute = execute
        self.seen_legacy_ids: set[str] = set()
        self.ambiguous = 0
        self.identity_candidates = 0
        self.followups_created = 0
        self.enriched = 0
        # simulação dry-run: aliases/telefones/nomes "criados" nesta passada
        self.simulated_aliases: dict[tuple[str, str, str], str] = {}
        self.simulated_phones: dict[str, str] = {}
        self.simulated_names: dict[str, str] = {}

    def alias_lookup(self, entidade: str, source: str, legacy_id: str) -> str | None:
        found = _find_alias(self.db, entidade, source, legacy_id)
        if found:
            return found
        return self.simulated_aliases.get((entidade, source, legacy_id))

    def register_alias(self, entidade: str, source: str, legacy_id: str, entity_id: str):
        if self.execute:
            self.db.add(
                LegacyAlias(
                    entidade=entidade,
                    legacy_source=source,
                    legacy_id=legacy_id,
                    entity_id=entity_id,
                    import_batch_id=self.batch.id if self.batch else None,
                )
            )
        else:
            self.simulated_aliases[(entidade, source, legacy_id)] = entity_id


def _resolve_or_create_person(
    ctx: _Ctx,
    nome: str,
    telefone: str,
    email: str,
    legacy_source: str,
    legacy_id: str | None,
    import_row_id: str | None,
    data_nascimento: str = "",
    observacao: str | None = None,
    placeholder: bool = False,
) -> tuple[str | None, bool, int]:
    """Retorna (person_id, criado, n_candidatos). Nunca funde automaticamente."""
    db = ctx.db
    if legacy_id:
        existing = ctx.alias_lookup("people", legacy_source, legacy_id)
        if existing:
            return existing, False, 0

    nome_norm = normalize_name(nome)
    phone_norm = normalize_phone(telefone)
    candidates = find_person_candidates(db, nome_norm, [phone_norm] if phone_norm else [])
    n_candidates = len(candidates)

    if not ctx.execute:
        # candidatos também contra linhas anteriores do MESMO arquivo (simulado)
        if phone_norm and phone_norm in ctx.simulated_phones:
            n_candidates += 1
        elif nome_norm and nome_norm in ctx.simulated_names:
            n_candidates += 1
        person_id = new_uuid()  # simulado
        if phone_norm:
            ctx.simulated_phones.setdefault(phone_norm, person_id)
        if nome_norm:
            ctx.simulated_names.setdefault(nome_norm, person_id)
        if legacy_id:
            ctx.register_alias("people", legacy_source, legacy_id, person_id)
        ctx.identity_candidates += n_candidates
        return person_id, True, n_candidates

    nd_nasc, _nasc_ok = _parse_date_or_reject(data_nascimento)
    obs = observacao
    if placeholder:
        obs = f"{PLACEHOLDER_MARK} criado a partir de {legacy_source}; " \
              f"enriquecer com o cadastro completo. {observacao or ''}".strip()
    person = Person(
        public_code=allocate_public_code(db, "people"),
        nome_completo=nome,
        nome_normalizado=nome_norm,
        data_nascimento=nd_nasc.value,
        observacao=obs,
        legacy_source=legacy_source,
        legacy_id=legacy_id,
        import_batch_id=ctx.batch.id if ctx.batch else None,
    )
    db.add(person)
    db.flush()
    if telefone:
        db.add(
            PersonContact(
                person_id=person.id,
                tipo="whatsapp",
                valor=telefone,
                valor_normalizado=phone_norm,
                principal=True,
            )
        )
    if email:
        db.add(
            PersonContact(
                person_id=person.id,
                tipo="email",
                valor=email,
                valor_normalizado=email.lower(),
            )
        )
    if legacy_id:
        ctx.register_alias("people", legacy_source, legacy_id, person.id)
    registered = register_candidates(
        db, person, candidates, origem="import",
        batch_id=ctx.batch.id if ctx.batch else None,
        import_row_id=import_row_id,
    )
    ctx.identity_candidates += len(registered)
    db.flush()
    return person.id, True, n_candidates


def _enrich_existing_person(ctx: _Ctx, person_id: str, row: dict) -> bool:
    """Completa campos faltantes de pessoa já importada, de forma auditável.

    Nunca sobrescreve valor existente; só preenche lacunas.
    """
    if not ctx.execute:
        return False
    db = ctx.db
    person = db.get(Person, person_id)
    if person is None:
        return False
    changed = False
    telefone = _pick(row, "telefone", "whatsapp", "celular", "fone")
    if telefone:
        has_phone = db.execute(select(PersonContact).where(
            PersonContact.person_id == person_id,
            PersonContact.tipo.in_(["whatsapp", "telefone"]),
        )).scalars().first()
        if has_phone is None:
            db.add(PersonContact(
                person_id=person_id, tipo="whatsapp", valor=telefone,
                valor_normalizado=normalize_phone(telefone), principal=True,
            ))
            changed = True
    email = _pick(row, "email", "e-mail")
    if email:
        has_email = db.execute(select(PersonContact).where(
            PersonContact.person_id == person_id, PersonContact.tipo == "email",
        )).scalars().first()
        if has_email is None:
            db.add(PersonContact(
                person_id=person_id, tipo="email", valor=email,
                valor_normalizado=email.lower(),
            ))
            changed = True
    if person.data_nascimento is None:
        nd, ok = _parse_date_or_reject(_pick(row, "data_nascimento", "nascimento"))
        if ok and nd.value is not None:
            person.data_nascimento = nd.value
            changed = True
    if changed and person.observacao and PLACEHOLDER_MARK in person.observacao:
        person.observacao = person.observacao.replace(PLACEHOLDER_MARK, "").strip() or None
    if changed:
        db.flush()
        ctx.enriched += 1
    return changed


# ------------------------------------------------------- handlers por fonte

def _handle_leads(ctx: _Ctx, row: dict, import_row_id: str | None):
    nome = _pick(row, "nome", "nome_completo", "lead", "paciente")
    if not nome:
        return "rejeitado", "nome_ausente", None, None
    if _is_pcmso_row(row):
        return "rejeitado", "pcmso_fora_da_operacao", None, None
    legacy_id = _pick(row, "id", "lead_id") or None
    if legacy_id and legacy_id in ctx.seen_legacy_ids:
        return "duplicado", "legacy_id_repetido_no_arquivo", None, None
    if legacy_id:
        ctx.seen_legacy_ids.add(legacy_id)
        if ctx.alias_lookup("leads", "leads_csv", legacy_id):
            return "ja_existente", "lead_ja_importado", None, None
    data_contato = _pick(row, "data_primeiro_contato", "data_contato", "data", "criado_em")
    nd, data_ok = _parse_date_or_reject(data_contato)
    if not data_ok:
        return "rejeitado", "data_invalida", None, None
    telefone = _pick(row, "telefone", "whatsapp", "celular", "fone")
    email = _pick(row, "email", "e-mail")
    person_id, _created, n_cand = _resolve_or_create_person(
        ctx, nome, telefone, email, "leads_csv",
        _pick(row, "paciente_id") or None, import_row_id,
    )
    if n_cand:
        ctx.ambiguous += 1
    if not ctx.execute:
        return ("ambiguo" if n_cand else "importado"), None, "leads", None
    modalidade_raw = _pick(row, "modalidade", "tipo_atendimento", "local")
    modalidade = None
    ml = modalidade_raw.lower()
    if "domicili" in ml or "residenc" in ml or "casa" in ml:
        modalidade = "residencial"
    elif "cowork" in ml:
        modalidade = "cowork"
    elif "clinica" in ml or "clínica" in ml or "parceir" in ml:
        modalidade = "clinica_parceira"
    lead = Lead(
        public_code=allocate_public_code(ctx.db, "leads"),
        person_id=person_id,
        origem=_pick(row, "origem", "fonte") or None,
        canal_entrada=_pick(row, "canal", "canal_entrada") or None,
        servico_interesse=None,
        modalidade=modalidade,
        etapa="novo",
        data_primeiro_contato=nd.value,
        data_primeiro_contato_original=nd.original or None,
        data_primeiro_contato_precisao=nd.precision,
        data_primeiro_contato_dia_assumido=nd.day_assumed,
        responsavel=_pick(row, "responsavel") or None,
        observacao=_pick(row, "observacao", "obs") or None,
        legacy_source="leads_csv",
        legacy_id=legacy_id,
        import_batch_id=ctx.batch.id if ctx.batch else None,
    )
    ctx.db.add(lead)
    ctx.db.flush()
    if legacy_id:
        ctx.register_alias("leads", "leads_csv", legacy_id, lead.id)
    person = ctx.db.get(Person, person_id)
    _fup, motivo = schedule_followup(
        ctx.db, person, "lead_sem_atendimento", "leads", lead.id, due=nd.value,
    )
    if motivo == "criado":
        ctx.followups_created += 1
    return ("ambiguo" if n_cand else "importado"), None, "leads", lead.id


def _handle_pacientes(ctx: _Ctx, row: dict, import_row_id: str | None):
    nome = _pick(row, "nome", "nome_completo", "paciente")
    if not nome:
        return "rejeitado", "nome_ausente", None, None
    nd_nasc, nasc_ok = _parse_date_or_reject(_pick(row, "data_nascimento", "nascimento"))
    if not nasc_ok:
        return "rejeitado", "data_invalida", None, None
    legacy_id = _pick(row, "paciente_id", "id") or None
    if legacy_id and legacy_id in ctx.seen_legacy_ids:
        return "duplicado", "legacy_id_repetido_no_arquivo", None, None
    if legacy_id:
        ctx.seen_legacy_ids.add(legacy_id)
        existing_id = ctx.alias_lookup("people", "crm_pacientes", legacy_id)
        if existing_id:
            # NUNCA descartar silenciosamente: enriquece lacunas auditavelmente
            enriched = _enrich_existing_person(ctx, existing_id, row)
            motivo = "paciente_enriquecido" if enriched else "paciente_ja_importado"
            return "ja_existente", motivo, "people", existing_id if ctx.execute else None
    person_id, _created, n_cand = _resolve_or_create_person(
        ctx, nome,
        _pick(row, "telefone", "whatsapp", "celular"),
        _pick(row, "email", "e-mail"),
        "crm_pacientes", legacy_id, import_row_id,
        data_nascimento=_pick(row, "data_nascimento", "nascimento"),
        observacao=_pick(row, "observacao", "obs") or None,
    )
    if n_cand:
        ctx.ambiguous += 1
    return ("ambiguo" if n_cand else "importado"), None, "people", (
        person_id if ctx.execute else None
    )


def _resolve_attendance_person(ctx: _Ctx, row: dict, import_row_id: str | None,
                               fonte: str):
    """Resolve a pessoa de exame/consulta com ordem segura (fail-closed).

    - paciente_id presente SEM alias -> (None, 'ordem') — importar pacientes antes;
    - só nome -> pessoa PLACEHOLDER explicitamente marcada;
    - nada -> (None, 'sem_paciente').
    """
    paciente_legacy = _pick(row, "paciente_id", "id_paciente") or None
    if paciente_legacy:
        person_id = ctx.alias_lookup("people", "crm_pacientes", paciente_legacy)
        if person_id:
            return person_id, 0, None
        return None, 0, "requer_importacao_previa_de_pacientes"
    nome = _pick(row, "nome", "paciente", "nome_paciente")
    if not nome:
        return None, 0, "paciente_nao_identificado"
    person_id, _created, n_cand = _resolve_or_create_person(
        ctx, nome,
        _pick(row, "telefone", "whatsapp"),
        "",
        fonte, None, import_row_id,
        placeholder=True,
    )
    return person_id, n_cand, None


def _handle_espirometria(ctx: _Ctx, row: dict, import_row_id: str | None):
    legacy_id = _pick(row, "exame_id", "id_atendimento", "id") or None
    if legacy_id and legacy_id in ctx.seen_legacy_ids:
        return "duplicado", "legacy_id_repetido_no_arquivo", None, None
    if legacy_id:
        ctx.seen_legacy_ids.add(legacy_id)
        if ctx.alias_lookup("spirometry_exams", "crm_espirometria", legacy_id):
            return "ja_existente", "exame_ja_importado", None, None
    if _is_pcmso_row(row):
        return "rejeitado", "pcmso_fora_da_operacao", None, None
    nd, data_ok = _parse_date_or_reject(_pick(row, "data_exame", "data", "data_atendimento"))
    if not data_ok:
        return "rejeitado", "data_invalida", None, None
    person_id, n_cand, erro = _resolve_attendance_person(
        ctx, row, import_row_id, "crm_espirometria")
    if erro:
        return "rejeitado", erro, None, None
    if n_cand:
        ctx.ambiguous += 1
    status = _pick(row, "status", "status_exame") or "Realizado"
    if status.lower() in ("realizado", "exame realizado", "realizada"):
        status = "Realizado"
    if not ctx.execute:
        return ("ambiguo" if n_cand else "importado"), None, "spirometry_exams", None
    exam = SpirometryExam(
        public_code=allocate_public_code(ctx.db, "spirometry_exams"),
        person_id=person_id,
        data_exame=nd.value,
        data_exame_original=nd.original or None,
        data_exame_precisao=nd.precision,
        data_exame_dia_assumido=nd.day_assumed,
        local_atendimento=_pick(row, "local", "local_atendimento") or None,
        status=status if status in ("Aguardando", "Realizado", "Cancelado", "Remarcado") else "Realizado",
        origem=_pick(row, "origem") or "importacao_crm",
        responsavel=_pick(row, "responsavel") or None,
        observacao=_pick(row, "observacao", "obs") or None,
        legacy_source="crm_espirometria",
        legacy_id=legacy_id,
        import_batch_id=ctx.batch.id if ctx.batch else None,
    )
    ctx.db.add(exam)
    ctx.db.flush()
    if legacy_id:
        ctx.register_alias("spirometry_exams", "crm_espirometria", legacy_id, exam.id)
    if exam.status == "Realizado" and exam.data_exame:
        person = ctx.db.get(Person, person_id)
        _fup, motivo = schedule_followup(
            ctx.db, person, "pos_exame", "spirometry_exams", exam.id,
            due=due_after_attendance(exam.data_exame),
        )
        if motivo == "criado":
            ctx.followups_created += 1
    return ("ambiguo" if n_cand else "importado"), None, "spirometry_exams", exam.id


def _handle_consultas(ctx: _Ctx, row: dict, import_row_id: str | None):
    legacy_id = _pick(row, "consulta_id", "id") or None
    if legacy_id and legacy_id in ctx.seen_legacy_ids:
        return "duplicado", "legacy_id_repetido_no_arquivo", None, None
    if legacy_id:
        ctx.seen_legacy_ids.add(legacy_id)
        if ctx.alias_lookup("consultations", "crm_consultas", legacy_id):
            return "ja_existente", "consulta_ja_importada", None, None
    nd, data_ok = _parse_date_or_reject(_pick(row, "data_consulta", "data"))
    if not data_ok:
        return "rejeitado", "data_invalida", None, None
    person_id, n_cand, erro = _resolve_attendance_person(
        ctx, row, import_row_id, "crm_consultas")
    if erro:
        return "rejeitado", erro, None, None
    if n_cand:
        ctx.ambiguous += 1
    status = _pick(row, "status") or "Realizada"
    if not ctx.execute:
        return ("ambiguo" if n_cand else "importado"), None, "consultations", None
    consultation = Consultation(
        public_code=allocate_public_code(ctx.db, "consultations"),
        person_id=person_id,
        data_consulta=nd.value,
        data_consulta_original=nd.original or None,
        data_consulta_precisao=nd.precision,
        data_consulta_dia_assumido=nd.day_assumed,
        modalidade=None,
        profissional=_pick(row, "profissional", "medico") or None,
        status=status if status in (
            "Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"
        ) else "Realizada",
        origem="importacao_crm",
        observacao=_pick(row, "observacao", "obs") or None,
        legacy_source="crm_consultas",
        legacy_id=legacy_id,
        import_batch_id=ctx.batch.id if ctx.batch else None,
    )
    ctx.db.add(consultation)
    ctx.db.flush()
    if legacy_id:
        ctx.register_alias("consultations", "crm_consultas", legacy_id, consultation.id)
    if consultation.status == "Realizada" and consultation.data_consulta:
        person = ctx.db.get(Person, person_id)
        _fup, motivo = schedule_followup(
            ctx.db, person, "pos_consulta", "consultations", consultation.id,
            due=due_after_attendance(consultation.data_consulta),
        )
        if motivo == "criado":
            ctx.followups_created += 1
    return ("ambiguo" if n_cand else "importado"), None, "consultations", consultation.id


def _handle_contatos_b2b(ctx: _Ctx, row: dict, import_row_id: str | None):
    clinica = _pick(row, "clinica", "empresa", "nome_clinica", "nome")
    if not clinica:
        return "rejeitado", "clinica_ausente", None, None
    if _is_pcmso_row(row):
        return "rejeitado", "pcmso_fora_da_operacao", None, None
    legacy_id = _pick(row, "id", "contato_id") or None
    if legacy_id and legacy_id in ctx.seen_legacy_ids:
        return "duplicado", "legacy_id_repetido_no_arquivo", None, None
    if legacy_id:
        ctx.seen_legacy_ids.add(legacy_id)
        if ctx.alias_lookup("partners", "contatos_b2b", legacy_id):
            return "ja_existente", "parceiro_ja_importado", None, None
    if not ctx.execute:
        return "importado", None, "partners", None
    partner = ctx.db.execute(
        select(Partner).where(Partner.nome == clinica)
    ).scalar_one_or_none()
    if partner is None:
        partner = Partner(
            public_code=allocate_public_code(ctx.db, "partners"),
            nome=clinica,
            tipo="clinica",
            status="prospecto",
            cidade=_pick(row, "cidade") or None,
            observacao=_pick(row, "observacao", "obs") or None,
            legacy_source="contatos_b2b",
            legacy_id=legacy_id,
            import_batch_id=ctx.batch.id if ctx.batch else None,
        )
        ctx.db.add(partner)
        ctx.db.flush()
    if legacy_id:
        ctx.register_alias("partners", "contatos_b2b", legacy_id, partner.id)
    contato_nome = _pick(row, "contato", "contato_nome", "responsavel")
    if contato_nome:
        contact = PartnerContact(
            public_code=allocate_public_code(ctx.db, "partner_contacts"),
            partner_id=partner.id,
            nome=contato_nome,
            cargo=_pick(row, "cargo") or None,
            telefone=_pick(row, "telefone", "whatsapp") or None,
            email=_pick(row, "email", "e-mail") or None,
        )
        ctx.db.add(contact)
        ctx.db.flush()
    return "importado", None, "partners", partner.id


HANDLERS = {
    "leads": _handle_leads,
    "crm_pacientes": _handle_pacientes,
    "crm_espirometria": _handle_espirometria,
    "crm_consultas": _handle_consultas,
    "contatos_b2b": _handle_contatos_b2b,
}


def _acquire_execute_lock(db: Session, source_type: str, digest: str) -> None:
    """Serializa executes concorrentes do mesmo arquivo no PostgreSQL
    (advisory lock transacional). Em SQLite o unique parcial + o modelo de
    escrita única do arquivo cumprem o papel."""
    if db.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(
            hashlib.sha256(f"{source_type}:{digest}".encode()).digest()[:8],
            "big", signed=True,
        )
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def run_import(
    db: Session,
    source_type: str,
    source_name: str,
    content: bytes,
    execute: bool = False,
    user_id: str | None = None,
) -> dict:
    """Executa uma importação. dry-run (execute=False) não escreve NADA no banco.

    Em execute=True tudo roda na transação corrente — o chamador decide
    commit/rollback; qualquer exceção deixa o lote inteiro para rollback.
    """
    if source_type not in IMPORT_TYPES:
        raise ValueError(f"source_type inválido: {source_type}")
    source_name = pathlib.PurePath(source_name).name  # nunca caminho completo
    digest = sha256_bytes(content)

    # reexecução idempotente: mesmo arquivo já executado -> não importa de novo
    if execute:
        _acquire_execute_lock(db, source_type, digest)
        previous = db.execute(
            select(ImportBatch).where(
                ImportBatch.source_type == source_type,
                ImportBatch.sha256 == digest,
                ImportBatch.modo == "executado",
            )
        ).scalars().first()
        if previous:
            return {
                "status": "ja_importado",
                "batch_id": previous.id,
                "source_type": source_type,
                "source_name": source_name,
                "sha256": digest,
                "executado_em_utc": datetime.now(timezone.utc).isoformat(),
                "mensagem": "Arquivo com este SHA-256 já foi executado. Nada gravado.",
            }

    rows = _read_rows(content)
    batch: ImportBatch | None = None
    if execute:
        try:
            with db.begin_nested():
                batch = ImportBatch(
                    source_type=source_type,
                    source_name=source_name,
                    sha256=digest,
                    modo="executado",
                    status="executando",
                    total_rows=len(rows),
                    created_by=user_id,
                )
                db.add(batch)
        except IntegrityError:
            # corrida com outro execute do mesmo arquivo — idempotente
            previous = db.execute(
                select(ImportBatch).where(
                    ImportBatch.source_type == source_type,
                    ImportBatch.sha256 == digest,
                    ImportBatch.modo == "executado",
                )
            ).scalars().first()
            return {
                "status": "ja_importado",
                "batch_id": previous.id if previous else None,
                "source_type": source_type,
                "source_name": source_name,
                "sha256": digest,
                "executado_em_utc": datetime.now(timezone.utc).isoformat(),
                "mensagem": "Execução concorrente detectada. Nada gravado.",
            }

    ctx = _Ctx(db, source_type, batch, execute)
    handler = HANDLERS[source_type]
    counts = {"importado": 0, "rejeitado": 0, "duplicado": 0, "ja_existente": 0, "ambiguo": 0}
    rejections: list[dict] = []

    for index, row in enumerate(rows, start=2):  # linha 1 = cabeçalho
        row_hash = sha256_bytes(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
        )
        import_row_id = new_uuid()
        import_row = None
        if execute:
            # criada ANTES do handler: identity_candidates referencia esta linha
            import_row = ImportRow(
                id=import_row_id,
                batch_id=batch.id,
                row_number=index,
                legacy_id=_pick(row, "id", "lead_id", "paciente_id", "exame_id",
                                "consulta_id", "contato_id") or None,
                status="processando",
                row_hash=row_hash,
            )
            db.add(import_row)
            db.flush()
        coluna_injecao = _row_injection(row)
        if coluna_injecao is not None:
            status, motivo, entity_type, entity_id = (
                "rejeitado", f"csv_injection_coluna_{coluna_injecao}", None, None,
            )
        else:
            status, motivo, entity_type, entity_id = handler(ctx, row, import_row_id)
        counts[status] = counts.get(status, 0) + 1
        if status in ("rejeitado", "duplicado") and len(rejections) < 50:
            rejections.append({"linha": index, "motivo": motivo})
        if execute:
            import_row.status = status
            import_row.motivo = motivo
            import_row.entity_type = entity_type
            import_row.entity_id = entity_id

    valid = counts["importado"] + counts["ambiguo"]
    rejected = counts["rejeitado"] + counts["duplicado"]
    if execute:
        batch.status = "executado"
        batch.valid_rows = valid
        batch.rejected_rows = rejected
        batch.ambiguous_rows = counts["ambiguo"]
        db.flush()

    return {
        "status": "executado" if execute else "dry_run",
        "batch_id": batch.id if batch else None,
        "source_type": source_type,
        "source_name": source_name,
        "sha256": digest,
        "total": len(rows),
        "validas": valid,
        "rejeitadas": rejected,
        "ja_existentes": counts["ja_existente"],
        "enriquecidos": ctx.enriched,
        "ambiguas": counts["ambiguo"],
        "candidatos_identidade": ctx.identity_candidates,
        "followups_criados": ctx.followups_created,
        "rejeicoes_amostra": rejections,
        "executado_em_utc": datetime.now(timezone.utc).isoformat(),
        "aviso": None if execute else "DRY-RUN: nada foi gravado no banco.",
    }


def write_reports(report: dict, out_dir: str | pathlib.Path) -> tuple[str, str]:
    """Grava relatório JSON e Markdown (fora do Git — diretório var/).

    Relatórios carregam apenas hash, contagens, número de linha e motivo
    sanitizado — nunca conteúdo bruto/PII.
    """
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"import-{report['source_type']}-{report['status']}-{stamp}"
    json_path = out / f"{base}.json"
    md_path = out / f"{base}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Importação {report['source_type']} — {report['status']}",
        "",
        f"- Arquivo: `{report['source_name']}`",
        f"- SHA-256: `{report['sha256']}`",
        f"- Total de linhas: {report.get('total', '—')}",
        f"- Válidas: {report.get('validas', '—')}",
        f"- Rejeitadas: {report.get('rejeitadas', '—')}",
        f"- Já existentes (idempotência): {report.get('ja_existentes', '—')}",
        f"- Enriquecidos (lacunas preenchidas): {report.get('enriquecidos', '—')}",
        f"- Ambíguas (candidato de identidade, sem fusão): {report.get('ambiguas', '—')}",
        f"- Candidatos de identidade registrados: {report.get('candidatos_identidade', '—')}",
        f"- Follow-ups criados: {report.get('followups_criados', '—')}",
        f"- Executado em (UTC): {report['executado_em_utc']}",
        "",
    ]
    if report.get("rejeicoes_amostra"):
        lines.append("## Rejeições (amostra)")
        lines.append("")
        for r in report["rejeicoes_amostra"]:
            lines.append(f"- linha {r['linha']}: {r['motivo']}")
        lines.append("")
    if report.get("aviso"):
        lines.append(f"> {report['aviso']}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(json_path), str(md_path)
