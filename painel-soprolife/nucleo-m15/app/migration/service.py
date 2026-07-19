"""Orquestração da migração por snapshot: registro, dry-run, portões,
aprovação humana, execução explícita e reconciliação determinística.

Nada aqui comita: o chamador (CLI/rota) decide commit/rollback.
Dry-run NUNCA grava registro operacional — apenas o resumo do lote
(ImportBatch modo=dry_run) como estrutura de staging auditável, sem PII.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..dates import parse_incomplete_date
from ..domain import pcmso_violation
from ..importer.csv_import import _read_rows, _row_injection, run_import
from ..models import (
    Consent,
    Consultation,
    FinancialEntry,
    Followup,
    IdentityCandidate,
    ImportBatch,
    ImportRow,
    ImportSnapshot,
    Lead,
    LegacyAlias,
    MigrationDecision,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerUnit,
    Partnership,
    Person,
    PersonContact,
    SpirometryExam,
)
from ..normalize import contains_clinical_info, contains_pii_like
from . import mapping as mapping_registry
from .manifest import (
    ManifestError,
    load_and_validate_manifest,
    load_snapshot_content,
    validate_backup_evidence,
)
from .mapping import MAPPING_VERSION, get_mapping

CONFIRMATION_TEMPLATE = "EXECUTAR IMPORTACAO {snapshot_id}"

# Motivos que são EXCLUSÃO deliberada (não erro crítico a resolver).
EXCLUSION_REASONS = {"pcmso_fora_da_operacao", "fonte_historica_excluida"}

DECISION_APPROVAL = "aprovacao_execucao"
DECISION_REVOCATION = "revogacao_execucao"

_MONEY_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")

# Tabelas-alvo contadas antes/depois da execução (reconciliação).
_COUNT_TABLES = {
    "people": Person,
    "person_contacts": PersonContact,
    "consents": Consent,
    "leads": Lead,
    "spirometry_exams": SpirometryExam,
    "consultations": Consultation,
    "partners": Partner,
    "partner_units": PartnerUnit,
    "partner_contacts": PartnerContact,
    "partnerships": Partnership,
    "partner_referrals": PartnerReferral,
    "financial_entries": FinancialEntry,
    "legacy_aliases": LegacyAlias,
    "identity_candidates": IdentityCandidate,
    "followups": Followup,
    "import_rows": ImportRow,
}


class MigrationError(ValueError):
    """Falha de contrato de migração — carrega códigos sem PII."""

    def __init__(self, codigo: str, detalhes: dict | list | None = None):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalhes = detalhes

    def as_dict(self) -> dict:
        out = {"ok": False, "codigo": self.codigo}
        if self.detalhes is not None:
            out["detalhes"] = self.detalhes
        return out


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick_norm(row: dict, *names: str) -> str:
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower().replace(" ", "_") == name:
                if value and str(value).strip():
                    return str(value).strip()
    return ""


def confirmation_phrase(snapshot_id: str) -> str:
    return CONFIRMATION_TEMPLATE.format(snapshot_id=snapshot_id)


def table_counts(db: Session) -> dict[str, int]:
    return {
        name: int(db.execute(select(func.count()).select_from(model)).scalar_one())
        for name, model in _COUNT_TABLES.items()
    }


# ------------------------------------------------------------------ registro

def register_snapshot(
    db: Session,
    manifest_name: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Valida o manifesto e registra o snapshot (identidade única)."""
    validation = load_and_validate_manifest(manifest_name, base_dir)
    if not validation.ok:
        raise MigrationError("manifesto_invalido", validation.erros)
    m = validation.manifest

    duplicate = db.execute(
        select(ImportSnapshot).where(
            ImportSnapshot.workbook_alias == m["workbook_alias"],
            ImportSnapshot.sheet_alias == m["sheet_alias"],
            ImportSnapshot.sha256 == validation.file_sha256,
        )
    ).scalars().first()
    if duplicate is not None:
        raise MigrationError("snapshot_duplicado", {"snapshot_id": duplicate.id})

    snap = ImportSnapshot(
        source_type=m["source_type"],
        workbook_alias=m["workbook_alias"],
        sheet_alias=m["sheet_alias"],
        snapshot_ts_utc=datetime.fromisoformat(str(m["snapshot_ts_utc"])),
        arquivo=validation.arquivo,
        sha256=validation.file_sha256,
        manifest_sha256=validation.manifest_sha256,
        schema_version=m["schema_version"],
        mapping_version=m["mapping_version"],
        encoding=m["encoding"],
        delimiter=m["delimiter"],
        headers=[mapping_registry.normalize_header(h) for h in m["headers"]],
        row_count=m["row_count"],
        linha_inicial_dados=m["linha_inicial_dados"],
        status="registrado",
        registered_by=user_id,
    )
    db.add(snap)
    db.flush()
    audit(db, "migracao.snapshot_registrado", "import_snapshots", snap.id, user_id,
          detalhes={
              "source_type": snap.source_type,
              "workbook_alias": snap.workbook_alias,
              "sheet_alias": snap.sheet_alias,
              "sha256": snap.sha256,
              "row_count": snap.row_count,
              "mapping_version": snap.mapping_version,
          })
    return {
        "ok": True,
        "snapshot_id": snap.id,
        "source_type": snap.source_type,
        "workbook_alias": snap.workbook_alias,
        "sheet_alias": snap.sheet_alias,
        "sha256": snap.sha256,
        "row_count": snap.row_count,
        "mapping_version": snap.mapping_version,
        "status": snap.status,
        "avisos": validation.avisos,
    }


def _get_snapshot(db: Session, snapshot_id: str) -> ImportSnapshot:
    snap = db.get(ImportSnapshot, snapshot_id)
    if snap is None:
        raise MigrationError("snapshot_inexistente")
    return snap


# ------------------------------------------------------------------- dry-run

def _date_profile(rows: list[dict], date_headers: tuple[str, ...]) -> dict:
    """Perfil de precisão de datas por coluna — sem valores brutos no resumo."""
    profile: dict[str, dict] = {}
    warnings = 0
    for header in date_headers:
        counts = {"dia": 0, "mes": 0, "ano": 0, "desconhecida": 0,
                  "vazia": 0, "dia_assumido": 0}
        seen = False
        for row in rows:
            raw = _pick_norm(row, header)
            if not raw:
                # coluna pode nem existir nesta linha/arquivo
                if any(mapping_registry.normalize_header(k) == header
                       for k in row.keys()):
                    seen = True
                    counts["vazia"] += 1
                continue
            seen = True
            nd = parse_incomplete_date(raw)
            if nd.value is None:
                counts["desconhecida"] += 1
                warnings += 1
            else:
                counts[nd.precision] += 1
                if nd.day_assumed:
                    counts["dia_assumido"] += 1
        if seen:
            profile[header] = counts
    return {"colunas": profile, "avisos_parser": warnings,
            "locale_assumido": "pt-BR"}


def _structural_row_checks(spec, row: dict) -> tuple[str, str | None]:
    """Classificação de linha para source_types preparados (sem handler nativo).

    Retorna (status, motivo): valida | rejeitada | excluida.
    """
    coluna = _row_injection(row)
    if coluna is not None:
        return "rejeitada", f"csv_injection_coluna_{coluna}"

    joined = " | ".join(str(v) for v in row.values() if v)
    if pcmso_violation({"observacao": joined}) is not None:
        # PCMSO é categoria histórica excluída em TODOS os módulos ativos
        return "excluida", "pcmso_fora_da_operacao"

    if spec.source_type == "financeiro_lancamentos":
        tipo = _pick_norm(row, "tipo").lower()
        if tipo not in ("receita", "despesa", "repasse"):
            return "rejeitada", "tipo_financeiro_invalido"
        valor = _pick_norm(row, "valor").replace(" ", "")
        if not _MONEY_RE.match(valor) or float(valor.replace(",", ".")) <= 0:
            return "rejeitada", "valor_invalido"
        for campo in ("descricao", "categoria"):
            texto = _pick_norm(row, campo)
            if texto and (contains_pii_like(texto) or contains_clinical_info(texto)):
                return "rejeitada", f"pii_em_{campo}_financeira"
        for campo in ("data_competencia", "data_recebimento"):
            raw = _pick_norm(row, campo)
            if raw and parse_incomplete_date(raw).value is None:
                return "rejeitada", "data_invalida"
        return "valida", None

    if spec.source_type == "consentimentos":
        if not _pick_norm(row, "paciente_id"):
            return "rejeitada", "paciente_id_ausente"
        canal = _pick_norm(row, "canal").lower()
        if canal not in ("whatsapp", "telefone", "email"):
            return "rejeitada", "canal_invalido"
        status = _pick_norm(row, "status").lower()
        if status not in ("concedido", "revogado", "desconhecido"):
            return "rejeitada", "status_de_consentimento_invalido"
        raw = _pick_norm(row, "data")
        if raw and parse_incomplete_date(raw).value is None:
            return "rejeitada", "data_invalida"
        return "valida", None

    if spec.source_type == "parcerias_encaminhamentos":
        if not _pick_norm(row, "paciente_id"):
            return "rejeitada", "paciente_id_ausente"
        if not _pick_norm(row, "parceiro_id", "clinica_id", "clinica"):
            return "rejeitada", "parceiro_nao_identificado"
        for campo in spec.date_headers:
            raw = _pick_norm(row, campo)
            if raw and parse_incomplete_date(raw).value is None:
                return "rejeitada", "data_invalida"
        return "valida", None

    if spec.source_type == "crm_clinicas":
        if not _pick_norm(row, "nome", "clinica", "nome_clinica"):
            return "rejeitada", "clinica_ausente"
        return "valida", None

    return "valida", None


_ID_HEADERS_BY_TYPE = {
    "financeiro_lancamentos": ("lancamento_id", "id"),
    "consentimentos": (),
    "parcerias_encaminhamentos": ("encaminhamento_id", "id"),
    "crm_clinicas": ("clinica_id", "id"),
}


def _structural_dry_run(spec, rows: list[dict]) -> dict:
    counts = {"valida": 0, "rejeitada": 0, "excluida": 0, "duplicada": 0}
    motivos: dict[str, int] = {}
    amostra: list[dict] = []
    seen_ids: set[str] = set()
    id_headers = _ID_HEADERS_BY_TYPE.get(spec.source_type, ())
    for index, row in enumerate(rows, start=2):
        legacy_id = _pick_norm(row, *id_headers) if id_headers else ""
        if legacy_id and legacy_id in seen_ids:
            status, motivo = "duplicada", "legacy_id_repetido_no_arquivo"
        else:
            if legacy_id:
                seen_ids.add(legacy_id)
            status, motivo = _structural_row_checks(spec, row)
        counts[status] += 1
        if motivo:
            motivos[motivo] = motivos.get(motivo, 0) + 1
            if status in ("rejeitada", "duplicada") and len(amostra) < 50:
                amostra.append({"linha": index, "motivo": motivo})
    return {"counts": counts, "motivos": motivos, "rejeicoes_amostra": amostra}


def dry_run_snapshot(
    db: Session,
    snapshot_id: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Dry-run governado por snapshot. Não grava NENHUM registro operacional;
    persiste apenas o resumo do lote (staging sem PII) para revisão na UI."""
    snap = _get_snapshot(db, snapshot_id)
    spec = get_mapping(snap.source_type)
    if spec is None:
        raise MigrationError("source_type_desconhecido")
    try:
        content = load_snapshot_content(snap.arquivo, snap.sha256, base_dir)
    except ManifestError as exc:
        raise MigrationError(str(exc)) from exc
    rows = _read_rows(content)

    if spec.execucao == mapping_registry.EXECUCAO_EXCLUIDA:
        total = len(rows)
        resumo = {
            "total": total, "validas": 0, "rejeitadas": 0, "ja_existentes": 0,
            "ambiguas": 0, "excluidas": total, "duplicadas_no_arquivo": 0,
            "criticos": 0, "candidatos_identidade": 0,
            "motivos": {"fonte_historica_excluida": total} if total else {},
            "rejeicoes_amostra": [],
            "motivo_exclusao": "pcmso_categoria_historica_excluida",
        }
    elif spec.execucao == mapping_registry.EXECUCAO_NATIVA:
        report = run_import(
            db, snap.source_type, snap.arquivo, content,
            execute=False, user_id=user_id,
        )
        db.rollback()  # garantia extra de dry-run (nada operacional persiste)
        snap = _get_snapshot(db, snapshot_id)
        motivos = dict(report.get("motivos", {}))
        excluidas = sum(motivos.get(m, 0) for m in EXCLUSION_REASONS)
        duplicadas = motivos.get("legacy_id_repetido_no_arquivo", 0)
        resumo = {
            "total": report["total"],
            "validas": report["validas"],
            "rejeitadas": report["rejeitadas"],
            "ja_existentes": report["ja_existentes"],
            "ambiguas": report["ambiguas"],
            "excluidas": excluidas,
            "duplicadas_no_arquivo": duplicadas,
            "criticos": report["rejeitadas"] - excluidas,
            "candidatos_identidade": report["candidatos_identidade"],
            "motivos": motivos,
            "rejeicoes_amostra": report["rejeicoes_amostra"],
        }
    else:  # preparada: validação estrutural, sem handler de escrita
        st = _structural_dry_run(spec, rows)
        c = st["counts"]
        resumo = {
            "total": len(rows),
            "validas": c["valida"],
            "rejeitadas": c["rejeitada"] + c["duplicada"],
            "ja_existentes": 0,
            "ambiguas": 0,
            "excluidas": c["excluida"],
            "duplicadas_no_arquivo": c["duplicada"],
            "criticos": c["rejeitada"] + c["duplicada"],
            "candidatos_identidade": 0,
            "motivos": st["motivos"],
            "rejeicoes_amostra": st["rejeicoes_amostra"],
        }

    perfil = _date_profile(rows, spec.date_headers)
    resumo["perfil_datas"] = perfil
    resumo["avisos"] = perfil["avisos_parser"]

    batch = ImportBatch(
        source_type=snap.source_type,
        source_name=snap.arquivo,
        sha256=snap.sha256,
        modo="dry_run",
        status="dry_run_ok" if resumo["criticos"] == 0 else "dry_run_com_erros",
        total_rows=resumo["total"],
        valid_rows=resumo["validas"],
        rejected_rows=resumo["rejeitadas"],
        ambiguous_rows=resumo["ambiguas"],
        params={
            "snapshot_id": snap.id,
            "mapping_version": snap.mapping_version,
            "resumo": resumo,
        },
        created_by=user_id,
    )
    db.add(batch)
    db.flush()
    snap.dry_run_batch_id = batch.id
    snap.status = "dry_run_ok" if resumo["criticos"] == 0 else "dry_run_com_erros"
    audit(db, "migracao.dry_run", "import_batches", batch.id, user_id,
          detalhes={
              "snapshot_id": snap.id,
              "source_type": snap.source_type,
              "criticos": resumo["criticos"],
              "total": resumo["total"],
          })
    return {
        "ok": True,
        "modo": "dry_run",
        "batch_id": batch.id,
        "snapshot_id": snap.id,
        "source_type": snap.source_type,
        "proposta_entidade": spec.target_module or None,
        "relacoes_propostas": list(spec.relacoes),
        "execucao": spec.execucao,
        "status_snapshot": snap.status,
        **resumo,
        "aviso": "DRY-RUN: nenhum registro operacional foi gravado.",
        "executado_em_utc": _utcnow_iso(),
    }


# -------------------------------------------------------------------- portões

def _latest_approval_decision(db: Session, snapshot_id: str):
    decisions = db.execute(
        select(MigrationDecision)
        .where(MigrationDecision.tipo.in_([DECISION_APPROVAL, DECISION_REVOCATION]))
        .order_by(MigrationDecision.ts_utc.desc(), MigrationDecision.id.desc())
    ).scalars().all()
    for d in decisions:
        det = d.detalhes or {}
        if det.get("snapshot_id") == snapshot_id:
            return d
    return None


def _gate(ok: bool, detalhe: str) -> dict:
    return {"ok": bool(ok), "detalhe": detalhe}


def preflight(
    db: Session,
    snapshot_id: str,
    backup_evidence: str | None = None,
    base_dir=None,
) -> dict:
    """Avalia TODOS os portões de execução. Nunca escreve nada."""
    gates: dict[str, dict] = {}
    evidencia = None
    try:
        snap = _get_snapshot(db, snapshot_id)
        gates["snapshot_registrado"] = _gate(True, snap.status)
    except MigrationError:
        gates["snapshot_registrado"] = _gate(False, "snapshot_inexistente")
        return {"ok": False, "gates": gates, "frase_confirmacao": None,
                "evidencia": None}

    spec = get_mapping(snap.source_type)
    try:
        load_snapshot_content(snap.arquivo, snap.sha256, base_dir)
        gates["checksum_inalterado"] = _gate(True, snap.sha256)
    except Exception as exc:  # noqa: BLE001 — motivo sanitizado, sem caminho
        gates["checksum_inalterado"] = _gate(False, str(exc))

    batch = db.get(ImportBatch, snap.dry_run_batch_id) if snap.dry_run_batch_id else None
    dry_ok = (
        batch is not None
        and batch.modo == "dry_run"
        and batch.sha256 == snap.sha256
    )
    gates["dry_run_realizado"] = _gate(
        dry_ok, batch.id if batch else "dry_run_ausente")

    criticos = None
    if dry_ok:
        criticos = ((batch.params or {}).get("resumo") or {}).get("criticos")
    gates["sem_erros_criticos"] = _gate(
        criticos == 0, f"criticos={criticos}" if criticos is not None
        else "sem_dry_run_valido")

    gates["mapping_version_revisada"] = _gate(
        snap.mapping_version == MAPPING_VERSION,
        f"{snap.mapping_version} vs atual {MAPPING_VERSION}")

    exec_ok = spec is not None and spec.execucao == mapping_registry.EXECUCAO_NATIVA
    gates["execucao_disponivel"] = _gate(
        exec_ok, spec.execucao if spec else "source_type_desconhecido")

    if backup_evidence:
        evidencia = validate_backup_evidence(backup_evidence, base_dir)
        gates["backup_validado"] = _gate(
            evidencia["ok"],
            "backup_integro" if evidencia["ok"] else ";".join(evidencia["erros"]))
        gates["evidencia_rollback_disponivel"] = _gate(
            evidencia["ok"] and bool(evidencia.get("local_rollback")),
            evidencia.get("local_rollback") or "indisponivel")
    else:
        gates["backup_validado"] = _gate(False, "evidencia_nao_informada")
        gates["evidencia_rollback_disponivel"] = _gate(
            False, "evidencia_nao_informada")

    decision = _latest_approval_decision(db, snapshot_id)
    approval_ok = False
    detalhe_aprovacao = "sem_aprovacao"
    if decision is not None and decision.tipo == DECISION_APPROVAL:
        det = decision.detalhes or {}
        approval_ok = (
            det.get("sha256") == snap.sha256
            and det.get("mapping_version") == snap.mapping_version
            and det.get("dry_run_batch_id") == snap.dry_run_batch_id
        )
        detalhe_aprovacao = (
            "aprovado" if approval_ok else "aprovacao_desatualizada")
    elif decision is not None:
        detalhe_aprovacao = "aprovacao_revogada"
    gates["aprovacao_humana"] = _gate(approval_ok, detalhe_aprovacao)

    executed = db.execute(
        select(ImportBatch).where(
            ImportBatch.source_type == snap.source_type,
            ImportBatch.sha256 == snap.sha256,
            ImportBatch.modo == "executado",
        )
    ).scalars().first()
    gates["idempotencia"] = _gate(
        executed is None,
        "sem_execucao_previa" if executed is None
        else f"ja_executado_no_lote_{executed.id}")

    ok = all(g["ok"] for g in gates.values())
    return {
        "ok": ok,
        "snapshot_id": snap.id,
        "gates": gates,
        "frase_confirmacao": confirmation_phrase(snap.id) if ok else None,
        "evidencia": evidencia,
    }


# ------------------------------------------------------------------ aprovação

def approve_snapshot(
    db: Session,
    snapshot_id: str,
    sha256: str,
    mapping_version: str,
    dry_run_batch_id: str,
    user_id: str | None = None,
    observacao: str | None = None,
) -> dict:
    """Aprovação humana explícita. Todos os identificadores devem ser digitados
    EXATAMENTE — nenhum valor é assumido; nada aqui é automático."""
    snap = _get_snapshot(db, snapshot_id)
    if sha256 != snap.sha256:
        raise MigrationError("sha256_nao_confere")
    if mapping_version != snap.mapping_version or mapping_version != MAPPING_VERSION:
        raise MigrationError("mapping_version_nao_confere")
    if not snap.dry_run_batch_id or dry_run_batch_id != snap.dry_run_batch_id:
        raise MigrationError("dry_run_batch_nao_confere")
    batch = db.get(ImportBatch, snap.dry_run_batch_id)
    criticos = ((batch.params or {}).get("resumo") or {}).get("criticos")
    if criticos != 0:
        raise MigrationError("erros_criticos_nao_resolvidos",
                             {"criticos": criticos})
    spec = get_mapping(snap.source_type)
    if spec is None or spec.execucao != mapping_registry.EXECUCAO_NATIVA:
        raise MigrationError("execucao_indisponivel_para_source_type")

    decision = _latest_approval_decision(db, snapshot_id)
    if decision is not None and decision.tipo == DECISION_APPROVAL:
        det = decision.detalhes or {}
        if (det.get("sha256") == snap.sha256
                and det.get("dry_run_batch_id") == snap.dry_run_batch_id):
            raise MigrationError("ja_aprovado", {"decisao_id": decision.id})

    detalhes = {
        "snapshot_id": snap.id,
        "sha256": snap.sha256,
        "mapping_version": snap.mapping_version,
        "dry_run_batch_id": snap.dry_run_batch_id,
    }
    if observacao:
        detalhes["observacao"] = observacao
    db.add(MigrationDecision(
        tipo=DECISION_APPROVAL,
        decisao="aprovado",
        decidido_por=user_id,
        detalhes=detalhes,
    ))
    snap.status = "aprovado"
    audit(db, "migracao.aprovacao", "import_snapshots", snap.id, user_id,
          detalhes={"sha256": snap.sha256,
                    "dry_run_batch_id": snap.dry_run_batch_id})
    db.flush()
    return {"ok": True, "snapshot_id": snap.id, "status": snap.status,
            "frase_confirmacao": confirmation_phrase(snap.id)}


def revoke_approval(
    db: Session,
    snapshot_id: str,
    user_id: str | None = None,
    observacao: str | None = None,
) -> dict:
    snap = _get_snapshot(db, snapshot_id)
    decision = _latest_approval_decision(db, snapshot_id)
    if decision is None or decision.tipo != DECISION_APPROVAL:
        raise MigrationError("sem_aprovacao_ativa")
    detalhes = {"snapshot_id": snap.id, "aprovacao_revogada_id": decision.id}
    if observacao:
        detalhes["observacao"] = observacao
    db.add(MigrationDecision(
        tipo=DECISION_REVOCATION,
        decisao="revogado",
        decidido_por=user_id,
        detalhes=detalhes,
    ))
    if snap.status == "aprovado":
        snap.status = "dry_run_ok"
    audit(db, "migracao.aprovacao_revogada", "import_snapshots", snap.id, user_id)
    db.flush()
    return {"ok": True, "snapshot_id": snap.id, "status": snap.status}


# ------------------------------------------------------------------- execução

def execute_snapshot(
    db: Session,
    snapshot_id: str,
    dry_run_batch_id: str,
    confirmation: str,
    backup_evidence: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Execução real — só passa com TODOS os portões verdes, lote exato e
    frase exata. Reexecução é bloqueada pelo portão de idempotência e pelo
    índice único (source_type, sha256, modo=executado)."""
    pf = preflight(db, snapshot_id, backup_evidence, base_dir)
    if not pf["ok"]:
        reprovados = sorted(
            nome for nome, g in pf["gates"].items() if not g["ok"])
        raise MigrationError("portoes_reprovados", reprovados)
    snap = _get_snapshot(db, snapshot_id)
    if dry_run_batch_id != snap.dry_run_batch_id:
        raise MigrationError("batch_id_nao_confere")
    if confirmation != confirmation_phrase(snap.id):
        raise MigrationError("frase_de_confirmacao_incorreta")

    try:
        content = load_snapshot_content(snap.arquivo, snap.sha256, base_dir)
    except ManifestError as exc:
        raise MigrationError(str(exc)) from exc
    antes = table_counts(db)
    report = run_import(
        db, snap.source_type, snap.arquivo, content,
        execute=True, user_id=user_id,
    )
    if report.get("status") == "ja_importado":
        # corrida com outra execução: idempotente, nada gravado por esta via
        return {**report, "ok": True, "snapshot_id": snap.id}
    depois = table_counts(db)

    batch = db.get(ImportBatch, report["batch_id"])
    batch.params = {
        "snapshot_id": snap.id,
        "mapping_version": snap.mapping_version,
        "dry_run_batch_id": snap.dry_run_batch_id,
        "evidencia_rollback": {
            "arquivo": backup_evidence,
            "arquivo_backup": (pf["evidencia"] or {}).get("arquivo_backup"),
            "local": (pf["evidencia"] or {}).get("local_rollback"),
        },
        "tabelas_antes": antes,
        "tabelas_depois": depois,
        "resumo_execucao": {
            "validas": report["validas"],
            "rejeitadas": report["rejeitadas"],
            "ja_existentes": report["ja_existentes"],
            "enriquecidos": report["enriquecidos"],
            "ambiguas": report["ambiguas"],
            "candidatos_identidade": report["candidatos_identidade"],
            "followups_criados": report["followups_criados"],
            "motivos": report.get("motivos", {}),
        },
    }
    snap.execute_batch_id = batch.id
    snap.status = "executado"
    db.add(MigrationDecision(
        tipo="execucao",
        decisao="executado",
        decidido_por=user_id,
        detalhes={"snapshot_id": snap.id, "batch_id": batch.id,
                  "sha256": snap.sha256},
    ))
    audit(db, "migracao.execucao", "import_batches", batch.id, user_id,
          detalhes={"snapshot_id": snap.id, "sha256": snap.sha256,
                    "validas": report["validas"],
                    "rejeitadas": report["rejeitadas"]})
    db.flush()
    return {**report, "ok": True, "snapshot_id": snap.id,
            "evidencia_rollback": batch.params["evidencia_rollback"]}


# -------------------------------------------------------------- reconciliação

def reconcile_snapshot(
    db: Session,
    snapshot_id: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Reconciliação determinística pós-execução (releitura sempre igual)."""
    snap = _get_snapshot(db, snapshot_id)
    if not snap.execute_batch_id:
        raise MigrationError("snapshot_nao_executado")
    batch = db.get(ImportBatch, snap.execute_batch_id)
    params = batch.params or {}

    rows = db.execute(
        select(ImportRow.status, func.count())
        .where(ImportRow.batch_id == batch.id)
        .group_by(ImportRow.status)
    ).all()
    by_status = {status: int(n) for status, n in rows}

    inserted = db.execute(
        select(ImportRow.entity_type, func.count())
        .where(
            ImportRow.batch_id == batch.id,
            ImportRow.status.in_(["importado", "ambiguo"]),
            ImportRow.entity_id.is_not(None),
        )
        .group_by(ImportRow.entity_type)
    ).all()
    inseridos = {etype: int(n) for etype, n in inserted if etype}

    unresolved = int(db.execute(
        select(func.count()).select_from(IdentityCandidate).where(
            IdentityCandidate.batch_id == batch.id,
            IdentityCandidate.status == "pendente",
        )
    ).scalar_one())

    aliases = db.execute(
        select(LegacyAlias.entidade, func.count())
        .where(LegacyAlias.import_batch_id == batch.id)
        .group_by(LegacyAlias.entidade)
    ).all()
    relacoes = {entidade: int(n) for entidade, n in aliases}

    try:
        load_snapshot_content(snap.arquivo, snap.sha256, base_dir)
        checksum = {"confere": True, "sha256": snap.sha256}
    except Exception as exc:  # noqa: BLE001
        checksum = {"confere": False, "motivo": str(exc)}

    resumo_exec = params.get("resumo_execucao", {})
    report = {
        "ok": True,
        "snapshot_id": snap.id,
        "batch_id": batch.id,
        "fonte_aceitas": by_status.get("importado", 0) + by_status.get("ambiguo", 0),
        "fonte_rejeitadas": by_status.get("rejeitado", 0) + by_status.get("duplicado", 0),
        "fonte_ja_existentes": by_status.get("ja_existente", 0),
        "alvo_inseridos": inseridos,
        "alvo_atualizados": resumo_exec.get("enriquecidos", 0),
        "alvo_inalterados": by_status.get("ja_existente", 0),
        "identidades_nao_resolvidas": unresolved,
        "relacoes_alvo": relacoes,
        "checksum": checksum,
        "tabelas_antes": params.get("tabelas_antes", {}),
        "tabelas_depois": params.get("tabelas_depois", {}),
        "evidencia_rollback": params.get("evidencia_rollback"),
    }
    batch.params = {**params, "reconciliacao": report}
    snap.status = "reconciliado"
    audit(db, "migracao.reconciliacao", "import_batches", batch.id, user_id,
          detalhes={"snapshot_id": snap.id,
                    "fonte_aceitas": report["fonte_aceitas"],
                    "fonte_rejeitadas": report["fonte_rejeitadas"],
                    "identidades_nao_resolvidas": unresolved})
    db.flush()
    return report


# ---------------------------------------------------------------------- status

def snapshot_status(db: Session, snapshot_id: str) -> dict:
    snap = _get_snapshot(db, snapshot_id)
    spec = get_mapping(snap.source_type)
    dry = db.get(ImportBatch, snap.dry_run_batch_id) if snap.dry_run_batch_id else None
    execb = db.get(ImportBatch, snap.execute_batch_id) if snap.execute_batch_id else None
    decision = _latest_approval_decision(db, snapshot_id)
    aprovacao = None
    if decision is not None:
        aprovacao = {
            "tipo": decision.tipo,
            "decisao": decision.decisao,
            "ts_utc": decision.ts_utc.isoformat() if decision.ts_utc else None,
        }
    return {
        "snapshot_id": snap.id,
        "source_type": snap.source_type,
        "workbook_alias": snap.workbook_alias,
        "sheet_alias": snap.sheet_alias,
        "sha256": snap.sha256,
        "row_count": snap.row_count,
        "mapping_version": snap.mapping_version,
        "mapping_version_atual": MAPPING_VERSION,
        "execucao": spec.execucao if spec else None,
        "status": snap.status,
        "dry_run": {
            "batch_id": dry.id,
            "status": dry.status,
            "resumo": (dry.params or {}).get("resumo"),
        } if dry else None,
        "execucao_batch": {
            "batch_id": execb.id,
            "status": execb.status,
            "evidencia_rollback": (execb.params or {}).get("evidencia_rollback"),
            "reconciliacao": (execb.params or {}).get("reconciliacao"),
        } if execb else None,
        "aprovacao": aprovacao,
    }


def list_snapshot_status(db: Session) -> list[dict]:
    snaps = db.execute(
        select(ImportSnapshot).order_by(ImportSnapshot.created_at.desc())
    ).scalars().all()
    return [snapshot_status(db, s.id) for s in snaps]
