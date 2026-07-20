"""Orquestração pública e sanitizada do dry-run multiaba M15.6B.

O grafo detalhado fica apenas em memória. Somente um resumo sem valores de
origem, nomes de abas ou números de linha pode ser persistido e exposto.
Esta camada nunca executa: a gravação oficial de decisões de revisão é
suportada (revisar-multiaba, revisor autenticado) e a execução real é
exclusiva da CLI local admin (M15.6C), com portões completos e frase exata.
"""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..models import ImportBatch, LegacyAlias, MigrationDecision
from .adapters import ADAPTERS, ADAPTERS_VERSION
from .manifest import ManifestError
from .rawsnapshot import load_raw_envelope
from .staging import ESTADOS, StagingError, stage_multi_sheet

MULTI_SHEET_SOURCE_TYPE = "snapshot_multiaba"
MULTI_SHEET_REPORT_VERSION = "m15.multi-sheet-dry-run.1"
MULTI_SHEET_REVIEW_DECISION = "revisao_multiaba"
# Decisões genéricas (M15.6B) + decisões com efeito executável (M15.6C).
# "resolvido"/"adiado" nunca liberam execução; o vocabulário executável por
# categoria é validado aqui e re-aplicado fail-closed pelo executor.
EXEC_REVIEW_DECISIONS = frozenset((
    "vincular_candidato", "criar_pessoa",
    "manter_primeira", "manter_segunda", "manter_ambas",
))
REVIEW_DECISIONS = frozenset(
    ("resolvido", "excluido", "adiado")) | EXEC_REVIEW_DECISIONS
EXEC_DECISION_CATEGORIES = {
    "vincular_candidato": frozenset(("candidato_identidade_provavel",)),
    "criar_pessoa": frozenset((
        "candidato_identidade_provavel", "exame_orfao", "consulta_orfa",
        "registro_orfao",
    )),
    "manter_primeira": frozenset(("duplicata_execucao",)),
    "manter_segunda": frozenset(("duplicata_execucao",)),
    "manter_ambas": frozenset(("duplicata_execucao",)),
}
# Rótulo sanitizado por categoria para qualquer console de revisão humana.
# Linguagem precisa: a gravação oficial da decisão É suportada; o que segue
# bloqueado é somente a execução fora da CLI local admin. Nunca inclui token
# privado nem valor de origem.
REVIEW_CATEGORY_LABELS = {
    "duplicata_execucao": (
        "Duplicata bloqueante de execução — pendente de revisão humana; "
        "decisão oficial gravável via revisar-multiaba"
    ),
}


class MultiSheetError(ValueError):
    """Erro fail-closed com código sanitizado e opcionalmente detalhes."""

    def __init__(self, codigo: str, detalhes=None):
        super().__init__(codigo)
        self.codigo = codigo
        self.detalhes = detalhes

    def as_dict(self) -> dict:
        result = {"ok": False, "codigo": self.codigo}
        if self.detalhes is not None:
            result["detalhes"] = self.detalhes
        return result


def _private_token(envelope_sha256: str, raw_token: str) -> str:
    digest = hashlib.sha256(
        f"{envelope_sha256}:{raw_token}".encode("utf-8")
    ).hexdigest()
    return f"priv-{digest[:32]}"


def _aggregate_sources(plan: dict) -> dict:
    sources: dict[str, dict] = {}
    for sheet in plan["abas"].values():
        kind = sheet["kind"]
        dest = sources.setdefault(
            kind,
            {
                "source_total": 0,
                **{state: 0 for state in ESTADOS},
            },
        )
        dest["source_total"] += sheet["total"]
        for state in ESTADOS:
            dest[state] += sheet[state]
    return {kind: sources[kind] for kind in sorted(sources)}


def sanitize_multi_sheet_plan(plan: dict, envelope_sha256: str) -> dict:
    """Remove toda referência resolvível sem acesso ao snapshot privado."""
    queue = [
        {
            "private_reference_token": _private_token(
                envelope_sha256, item["token"]
            ),
            "category": item["categoria"],
            "status": item["status"],
            "mapping_version": plan["mapping_version"],
        }
        for item in plan["fila_revisao"]
    ]
    queue.sort(
        key=lambda item: (
            item["category"], item["private_reference_token"], item["status"]
        )
    )
    review_summary: dict[str, dict] = {}
    for item in queue:
        category = item["category"]
        summary = review_summary.setdefault(
            category, {"count": 0, "pending": 0, "registered": 0}
        )
        summary["count"] += 1
        if item["status"] == "pendente":
            summary["pending"] += 1
        else:
            summary["registered"] += 1

    totals = plan["totais"]
    report = {
        "ok": True,
        "report_version": MULTI_SHEET_REPORT_VERSION,
        "mode": "dry_run",
        "status": "blocked_for_execution",
        "execution_allowed": False,
        "private_snapshot_token": f"snapshot-{envelope_sha256[:24]}",
        "snapshot_sha256": envelope_sha256,
        "raw_schema_version": plan["schema_version"],
        "mapping_version": plan["mapping_version"],
        "adapters_version": plan["adapters_version"],
        "supported_adapters": sorted(ADAPTERS),
        "processing_order": [
            {"kind": item["kind"], "stage": item["estagio"]}
            for item in plan["ordem_processamento"]
        ],
        "source_totals": _aggregate_sources(plan),
        "totals": {
            "source_total": totals["fonte_total"],
            "valid": totals["valida"],
            "rejected": totals["rejeitada"],
            "excluded": totals["excluida"],
            "pending_review": totals["pendente_revisao"],
            "duplicates": totals["duplicada"],
            "already_existing": totals["ja_existente"],
        },
        "proposed_inserts": plan["propostas"]["insercoes"],
        "proposed_relationships": plan["propostas"]["relacionamentos"],
        "consent_preview": {
            "proposed": plan["propostas"]["consentimentos"],
            "do_not_contact": plan["propostas"]["restricoes_nao_contatar"],
            "without_resolved_person": plan["propostas"][
                "consentimentos_sem_pessoa_resolvida"
            ],
        },
        "date_precision_profile": plan["perfil_datas"]["precisao_agregada"],
        "date_parser_warnings": plan["perfil_datas"]["avisos_parser"],
        "locale": plan["perfil_datas"]["locale_assumido"],
        "identity_profile": plan["perfil_identidade"],
        "financial_relationship_coverage": plan["cobertura_financeira"],
        "blocked_monetary_references": plan["propostas"][
            "referencias_monetarias_bloqueadas"
        ],
        "review_queue": queue,
        "review_summary": {
            key: review_summary[key] for key in sorted(review_summary)
        },
        "reconciliation_preview": plan["reconciliacao_preview"],
        "blockers": list(plan["bloqueios"]),
        "policies": plan["politicas"],
    }
    return report


def _safe_error_code(exc: Exception) -> str:
    """Mantém somente o código estável; nunca ecoa valor/cabeçalho privado."""
    return str(exc).partition(":")[0] or "snapshot_multiaba_invalido"


def build_multi_sheet_report(
    envelope_name: str, base_dir=None, existentes: set | None = None
) -> dict:
    """Lê, verifica e simula o snapshot sem banco, disco de saída ou rede."""
    try:
        envelope = load_raw_envelope(
            envelope_name, base_dir=base_dir,
            known_kinds=frozenset(ADAPTERS),
        )
        plan = stage_multi_sheet(envelope, existentes=existentes)
    except (ManifestError, StagingError) as exc:
        raise MultiSheetError(_safe_error_code(exc)) from exc
    return sanitize_multi_sheet_plan(plan, envelope.envelope_sha256)


def _get_multi_batch(db: Session, batch_id: str) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.source_type != MULTI_SHEET_SOURCE_TYPE:
        raise MultiSheetError("lote_multiaba_inexistente")
    if not (batch.params or {}).get("multi_sheet_report"):
        raise MultiSheetError("lote_multiaba_sem_relatorio")
    return batch


def run_multi_sheet_dry_run(
    db: Session,
    envelope_name: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Executa a simulação e persiste somente seu resumo sanitizado.

    Replay do mesmo envelope+mapping reutiliza o lote anterior; nenhum novo
    efeito, entidade operacional ou fila com PII é criado.
    """
    existing_aliases = {
        (alias.entidade, alias.legacy_source, alias.legacy_id)
        for alias in db.execute(select(LegacyAlias)).scalars().all()
    }
    report = build_multi_sheet_report(
        envelope_name, base_dir, existentes=existing_aliases
    )
    previous = db.execute(
        select(ImportBatch)
        .where(
            ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE,
            ImportBatch.modo == "dry_run",
            ImportBatch.sha256 == report["snapshot_sha256"],
        )
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
    ).scalars().all()
    for batch in previous:
        stored = (batch.params or {}).get("multi_sheet_report") or {}
        if (
            stored.get("private_snapshot_token")
            == report["private_snapshot_token"]
            and stored.get("mapping_version") == report["mapping_version"]
            and stored == report
        ):
            return {**report, "batch_id": batch.id, "replay": True}

    totals = report["totals"]
    batch = ImportBatch(
        source_type=MULTI_SHEET_SOURCE_TYPE,
        source_name="private-versioned-envelope",
        sha256=report["snapshot_sha256"],
        modo="dry_run",
        status="dry_run_bloqueado",
        total_rows=totals["source_total"],
        valid_rows=totals["valid"],
        rejected_rows=totals["rejected"] + totals["duplicates"],
        ambiguous_rows=totals["pending_review"],
        params={"multi_sheet_report": report},
        created_by=user_id,
    )
    db.add(batch)
    db.flush()
    audit(
        db, "migracao.multiaba_dry_run", "import_batches", batch.id, user_id,
        detalhes={
            "mapping_version": report["mapping_version"],
            "source_total": totals["source_total"],
            "pending_review": totals["pending_review"],
            "execution_allowed": False,
        },
    )
    return {**report, "batch_id": batch.id, "replay": False}


def _decisions_by_token(db: Session, batch_id: str) -> dict[str, dict]:
    decisions = db.execute(
        select(MigrationDecision)
        .where(MigrationDecision.tipo == MULTI_SHEET_REVIEW_DECISION)
        .order_by(MigrationDecision.ts_utc.desc(), MigrationDecision.id.desc())
    ).scalars().all()
    latest: dict[str, dict] = {}
    for decision in decisions:
        details = decision.detalhes or {}
        token = details.get("private_reference_token")
        if details.get("batch_id") != batch_id or not token or token in latest:
            continue
        latest[token] = {
            "decision": decision.decisao,
            "decision_state": decision.decisao,
        }
    return latest


def multi_sheet_review_queue(db: Session, batch_id: str) -> dict:
    batch = _get_multi_batch(db, batch_id)
    report = batch.params["multi_sheet_report"]
    decisions = _decisions_by_token(db, batch.id)
    items = []
    for item in report["review_queue"]:
        decision = decisions.get(item["private_reference_token"])
        items.append({
            **item,
            "category_label": REVIEW_CATEGORY_LABELS.get(
                item["category"], item["category"]
            ),
            "decision_state": (
                decision["decision_state"] if decision else item["status"]
            ),
        })
    pending = sum(
        1 for item in items
        if item["status"] == "pendente" and item["decision_state"] not in (
            "resolvido", "excluido", "vincular_candidato", "criar_pessoa",
            "manter_primeira", "manter_segunda", "manter_ambas",
        )
    )
    return {
        "batch_id": batch.id,
        "mapping_version": report["mapping_version"],
        "items": items,
        "total": len(items),
        "pending_decisions": pending,
        "execution_allowed": False,
    }


def decide_multi_sheet_review(
    db: Session,
    batch_id: str,
    private_reference_token: str,
    decision: str,
    mapping_version: str,
    user_id: str | None,
) -> dict:
    if user_id is None:
        raise MultiSheetError("revisor_autenticado_obrigatorio")
    if decision not in REVIEW_DECISIONS:
        raise MultiSheetError("decisao_de_revisao_invalida")
    batch = _get_multi_batch(db, batch_id)
    report = batch.params["multi_sheet_report"]
    if mapping_version != report["mapping_version"]:
        raise MultiSheetError("mapping_version_nao_confere")
    item = next(
        (
            candidate for candidate in report["review_queue"]
            if candidate["private_reference_token"] == private_reference_token
        ),
        None,
    )
    if item is None:
        raise MultiSheetError("referencia_privada_inexistente")
    categorias = EXEC_DECISION_CATEGORIES.get(decision)
    if categorias is not None and item["category"] not in categorias:
        raise MultiSheetError("decisao_incompativel_com_categoria")
    current = _decisions_by_token(db, batch.id).get(private_reference_token)
    if current and current["decision"] == decision:
        raise MultiSheetError("decisao_de_revisao_ja_registrada")

    db.add(MigrationDecision(
        tipo=MULTI_SHEET_REVIEW_DECISION,
        decisao=decision,
        decidido_por=user_id,
        detalhes={
            "batch_id": batch.id,
            "private_reference_token": private_reference_token,
            "mapping_version": mapping_version,
            "category": item["category"],
            "rule": "human_review_multi_sheet_dry_run",
        },
    ))
    audit(
        db, "migracao.multiaba_revisao", "import_batches", batch.id, user_id,
        detalhes={
            "private_reference_token": private_reference_token,
            "mapping_version": mapping_version,
            "category": item["category"],
            "decision": decision,
        },
    )
    db.flush()
    return {
        "ok": True,
        "batch_id": batch.id,
        "private_reference_token": private_reference_token,
        "mapping_version": mapping_version,
        "decision_state": decision,
        "execution_allowed": False,
    }


def _execution_status(db: Session, batch: ImportBatch,
                      pending_decisions: int) -> dict:
    """Status SANITIZADO de execução (M15.6C) — somente leitura, sem PII.

    A API nunca executa: aqui só aparecem prontidão, hash do plano, estado
    de reconciliação e evidência de rollback do lote executado (se houver).
    """
    exec_batch = db.execute(
        select(ImportBatch).where(
            ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE,
            ImportBatch.sha256 == batch.sha256,
            ImportBatch.modo == "executado",
        )
    ).scalars().first()
    plano = (batch.params or {}).get("plano_execucao_multiaba") or {}
    info = {
        "caminho_execucao": "somente_cli_local_admin",
        "revisoes_pendentes": pending_decisions,
        "plano_rollback_gerado": bool(plano),
        "plan_hash": plano.get("plan_hash"),
        "backup": "validacao_somente_pela_cli_local",
        "pronto_para_preflight": pending_decisions == 0 and bool(plano),
        "executado": exec_batch is not None,
    }
    if exec_batch is not None:
        params = exec_batch.params or {}
        recon = params.get("reconciliacao_execucao") or {}
        info.update({
            "batch_execucao_id": exec_batch.id,
            "status_execucao": exec_batch.status,
            "plan_hash": params.get("plan_hash") or info["plan_hash"],
            "reconciliacao_ok": recon.get("ok"),
            "reconciliacao_status": recon.get("status_final"),
            "evidencia_rollback_registrada":
                bool(params.get("evidencia_rollback")),
            "rollback_realizado": bool(params.get("rollback_execucao")),
        })
    return info


def multi_sheet_status(db: Session, batch_id: str) -> dict:
    batch = _get_multi_batch(db, batch_id)
    report = batch.params["multi_sheet_report"]
    queue = multi_sheet_review_queue(db, batch.id)
    decision_summary: dict[str, dict[str, int]] = {}
    for item in queue["items"]:
        category = item["category"]
        state = item["decision_state"]
        counts = decision_summary.setdefault(category, {})
        counts[state] = counts.get(state, 0) + 1
    return {
        **report,
        "batch_id": batch.id,
        "review_queue": queue["items"],
        "pending_decisions": queue["pending_decisions"],
        "decision_summary": {
            key: decision_summary[key] for key in sorted(decision_summary)
        },
        "execution": _execution_status(db, batch, queue["pending_decisions"]),
        "execution_allowed": False,
    }


def list_multi_sheet_status(db: Session, limit: int = 25) -> dict:
    total = int(db.execute(
        select(func.count()).select_from(ImportBatch).where(
            ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE
        )
    ).scalar_one())
    batches = db.execute(
        select(ImportBatch)
        .where(ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE)
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .limit(limit)
    ).scalars().all()
    items = []
    for batch in batches:
        report = (batch.params or {}).get("multi_sheet_report") or {}
        queue = multi_sheet_review_queue(db, batch.id)
        items.append({
            "batch_id": batch.id,
            "status": report.get("status"),
            "mapping_version": report.get("mapping_version"),
            "adapters_version": report.get("adapters_version"),
            "totals": report.get("totals"),
            "review_summary": report.get("review_summary"),
            "pending_decisions": queue["pending_decisions"],
            "financial_relationship_coverage": report.get(
                "financial_relationship_coverage"
            ),
            "reconciliation_preview": report.get("reconciliation_preview"),
            "blockers": report.get("blockers", []),
            "execution": _execution_status(
                db, batch, queue["pending_decisions"]),
            "execution_allowed": False,
        })
    return {
        "items": items,
        "total": total,
        "mapping_version": ADAPTERS_VERSION,
        "execution_allowed": False,
        "global_blocker": "execucao_real_somente_via_cli_admin_com_portoes",
    }
