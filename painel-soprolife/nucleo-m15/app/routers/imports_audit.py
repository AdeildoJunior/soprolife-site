"""Importações (dry-run via API), decisão de identidade e auditoria consultável."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import get_db
from ..importer.csv_import import IMPORT_TYPES, MAX_UPLOAD_BYTES, run_import
from ..migration.service import (
    MigrationError,
    approve_snapshot,
    preflight,
    snapshot_status,
)
from ..models import (
    AuditLog,
    IdentityCandidate,
    ImportBatch,
    ImportRow,
    ImportSnapshot,
    MigrationDecision,
    User,
)
from ..pagination import PageParams, paginate
from ..schemas import IdentityDecision, MigrationApproval
from ..security import ROLE_ADMIN, ROLE_GESTOR, require_role
from ..serializers import (
    ser_audit,
    ser_identity_candidate,
    ser_import_batch,
    ser_import_row,
    ser_import_snapshot,
)

router = APIRouter(tags=["migracao"])

_UPLOAD_CHUNK = 256 * 1024


async def _read_limited(file: UploadFile, limit: int) -> bytes:
    """Lê o upload em streaming e falha ANTES de carregar além do limite."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Arquivo excede o limite de {limit // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/importacoes/dry-run")
async def import_dry_run(
    source_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Dry-run SEMPRE: a API nunca executa escrita de importação.

    A execução real só existe pela CLI com --execute, sob decisão humana.
    """
    if source_type not in IMPORT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"source_type inválido. Aceitos: {sorted(IMPORT_TYPES)}",
        )
    content = await _read_limited(file, MAX_UPLOAD_BYTES)
    report = run_import(
        db,
        source_type=source_type,
        source_name=file.filename or "upload.csv",
        content=content,
        execute=False,
        user_id=user.id,
    )
    db.rollback()  # garantia extra: dry-run via API não persiste NADA
    return report


@router.get("/importacoes")
def list_batches(
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc())
    return paginate(db, stmt, params, ser_import_batch)


@router.get("/importacoes/{batch_id}/linhas")
def list_batch_rows(
    batch_id: str,
    status: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    stmt = (
        select(ImportRow)
        .where(ImportRow.batch_id == batch_id)
        .order_by(ImportRow.row_number)
    )
    if status:
        stmt = stmt.where(ImportRow.status == status)
    return paginate(db, stmt, params, ser_import_row)


# --------------------------------------------- snapshots privados (M15.6A)

@router.get("/migracao/snapshots")
def list_snapshots(
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Snapshots registrados — aliases, hashes e contagens; nunca PII."""
    stmt = select(ImportSnapshot).order_by(ImportSnapshot.created_at.desc())
    return paginate(db, stmt, params, ser_import_snapshot)


@router.get("/migracao/snapshots/{snapshot_id}")
def snapshot_detail(
    snapshot_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Detalhe + portões de execução. A evidência de backup só é validável
    pela CLI local (arquivo privado); aqui os portões de backup aparecem
    pendentes — a execução NUNCA acontece pela API."""
    try:
        status = snapshot_status(db, snapshot_id)
    except MigrationError:
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")
    gates = preflight(db, snapshot_id)
    return {
        "snapshot": status,
        "gates": gates["gates"],
        "portoes_ok": gates["ok"],
        "execucao_pela_api": False,
        "aviso": "Execução real somente pela CLI local, com todos os portões "
                 "verdes e frase exata.",
    }


@router.post("/migracao/snapshots/{snapshot_id}/aprovacao")
def approve_snapshot_api(
    snapshot_id: str,
    payload: MigrationApproval,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_ADMIN)),
):
    """Aprovação humana explícita (papel admin). Idempotente: repetir a
    requisição (refresh) NÃO duplica aprovação — retorna 409."""
    try:
        result = approve_snapshot(
            db, snapshot_id, payload.sha256, payload.mapping_version,
            payload.dry_run_batch_id, user_id=user.id,
            observacao=payload.observacao,
        )
    except MigrationError as exc:
        if exc.codigo == "snapshot_inexistente":
            raise HTTPException(status_code=404, detail="Snapshot não encontrado.")
        status_code = 409 if exc.codigo == "ja_aprovado" else 422
        raise HTTPException(status_code=status_code, detail=exc.as_dict())
    audit(db, "migracao.aprovacao_api", "import_snapshots", snapshot_id,
          user.id, request.state.request_id)
    db.commit()
    return result


# ------------------------------------------------------ decisão de identidade

DECISION_STATUS = {
    "pessoas_diferentes": "pessoas_diferentes",
    "possivel_mesma_pessoa": "possivel_mesma_pessoa",
    "adiar": "adiado",
}


@router.get("/identidade/candidatos")
def list_identity_candidates(
    status: str | None = "pendente",
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    stmt = select(IdentityCandidate).order_by(IdentityCandidate.created_at.desc())
    if status:
        stmt = stmt.where(IdentityCandidate.status == status)
    return paginate(db, stmt, params, ser_identity_candidate)


@router.post("/identidade/candidatos/{candidate_id}/decisao")
def decide_identity_candidate(
    candidate_id: str,
    payload: IdentityDecision,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Decisão humana registrada append-only. NUNCA funde registros —
    fusão destrutiva não existe nesta fase."""
    candidate = db.get(IdentityCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidato não encontrado.")
    if candidate.status not in ("pendente", "adiado"):
        raise HTTPException(
            status_code=409,
            detail={"codigo": "candidato_ja_decidido",
                    "mensagem": f"Candidato já está '{candidate.status}'."},
        )
    novo_status = DECISION_STATUS[payload.decisao]
    candidate.status = novo_status
    candidate.decidido_por = user.id
    candidate.decidido_em = datetime.now(timezone.utc)
    db.add(MigrationDecision(
        identity_candidate_id=candidate.id,
        tipo="identidade",
        decisao=payload.decisao,
        decidido_por=user.id,
        detalhes={"observacao": payload.observacao} if payload.observacao else None,
    ))
    audit(db, "identidade.decisao", "identity_candidates", candidate.id, user.id,
          request.state.request_id, {"decisao": payload.decisao})
    db.commit()
    return ser_identity_candidate(candidate)


@router.get("/auditoria")
def list_audit(
    user_id: str | None = None,
    entidade: str | None = None,
    acao: str | None = None,
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(ROLE_GESTOR)),
):
    """Trilha append-only — apenas leitura, sem update/delete em nenhuma rota."""
    stmt = select(AuditLog).order_by(AuditLog.ts_utc.desc())
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if entidade:
        stmt = stmt.where(AuditLog.entidade == entidade)
    if acao:
        stmt = stmt.where(AuditLog.acao == acao)
    return paginate(db, stmt, params, ser_audit)
