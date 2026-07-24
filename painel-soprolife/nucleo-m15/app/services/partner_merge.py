"""Consolidação de parceiro duplicado (M20) — resolução e mesclagem.

Princípios:
- NADA é apagado. A duplicata é arquivada e passa a apontar para o parceiro
  canônico; o código público antigo vira alias legado e continua resolvendo.
- Relacionamentos são MIGRADOS antes do arquivamento, na mesma transação.
- Contatos duplicados NÃO são recriados: a chave natural (nome normalizado +
  telefone/e-mail normalizados) decide se é o mesmo contato.
- Fail-closed: qualquer ambiguidade levanta erro e a transação não conclui.
- Auditoria é preservada e ampliada; nenhuma linha de audit_logs é tocada.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..models import (
    LegacyAlias,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerSettlement,
    PartnerTransfer,
    PartnerUnit,
    Partnership,
    Followup,
    SpirometryExam,
)

# Alias legado registrado para o código público da duplicata arquivada.
LEGACY_SOURCE_MERGE = "m20_merge_partner_code"

_MAX_MERGE_DEPTH = 10


class PartnerMergeError(RuntimeError):
    """Ambiguidade ou pré-condição violada — a consolidação não prossegue."""


def _fold(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _digits(value: str | None) -> str:
    return "".join(c for c in (value or "") if c.isdigit())


def _contact_key(contact: PartnerContact) -> tuple[str, str, str]:
    """Chave natural de um contato — nunca o id técnico."""
    return (_fold(contact.nome), _digits(contact.telefone), _fold(contact.email))


# ------------------------------------------------------------------ resolução

def resolve_partner(db: Session, referencia: str) -> tuple[Partner | None, Partner | None]:
    """Resolve id, código público ou id legado até o parceiro canônico.

    Retorna (canonico, encontrado_original). Quando o registro encontrado
    está arquivado com merged_into, o canônico é o destino da cadeia; caso
    contrário canônico == encontrado.
    """
    found = db.get(Partner, referencia)
    if found is None:
        found = db.execute(
            select(Partner).where(Partner.public_code == referencia)
        ).scalars().first()
    if found is None:
        alias = db.execute(
            select(LegacyAlias).where(
                LegacyAlias.entidade == "partners",
                LegacyAlias.legacy_id == referencia,
            )
        ).scalars().first()
        if alias is not None:
            found = db.get(Partner, alias.entity_id)
    if found is None:
        return None, None

    canonical = found
    seen = {canonical.id}
    depth = 0
    while canonical.merged_into_partner_id:
        depth += 1
        if depth > _MAX_MERGE_DEPTH:
            raise PartnerMergeError("Cadeia de consolidação de parceiro longa demais.")
        nxt = db.get(Partner, canonical.merged_into_partner_id)
        if nxt is None:
            raise PartnerMergeError("Parceiro canônico da cadeia não existe.")
        if nxt.id in seen:
            raise PartnerMergeError("Ciclo na cadeia de consolidação de parceiro.")
        seen.add(nxt.id)
        canonical = nxt
    return canonical, found


def active_partner_ids(db: Session) -> set[str]:
    return {
        p.id
        for p in db.execute(select(Partner).where(Partner.arquivado.is_(False)))
        .scalars()
        .all()
    }


# ------------------------------------------------------------------ mesclagem

def merge_preview(db: Session, duplicate: Partner, canonical: Partner) -> dict:
    """Mapa "antes" sanitizado: apenas contagens e códigos técnicos."""

    def counts(partner_id: str) -> dict:
        unidades = db.execute(
            select(PartnerUnit).where(PartnerUnit.partner_id == partner_id)
        ).scalars().all()
        unit_ids = [u.id for u in unidades]
        agendas = 0
        if unit_ids:
            from ..models import PartnerUnitConfig

            agendas = len(
                db.execute(
                    select(PartnerUnitConfig).where(
                        PartnerUnitConfig.partner_unit_id.in_(unit_ids)
                    )
                ).scalars().all()
            )
        return {
            "public_code": db.get(Partner, partner_id).public_code,
            "unidades": [u.public_code for u in unidades],
            "agendas_de_unidade": agendas,
            "contatos": [
                c.public_code
                for c in db.execute(
                    select(PartnerContact).where(PartnerContact.partner_id == partner_id)
                ).scalars().all()
            ],
            "parcerias": [
                p.public_code
                for p in db.execute(
                    select(Partnership).where(Partnership.partner_id == partner_id)
                ).scalars().all()
            ],
            "exames": [
                e.public_code
                for e in db.execute(
                    select(SpirometryExam).where(SpirometryExam.partner_id == partner_id)
                ).scalars().all()
            ],
            "encaminhamentos": len(
                db.execute(
                    select(PartnerReferral).where(PartnerReferral.partner_id == partner_id)
                ).scalars().all()
            ),
            "acertos": len(
                db.execute(
                    select(PartnerSettlement).where(
                        PartnerSettlement.partner_id == partner_id
                    )
                ).scalars().all()
            ),
            "repasses": len(
                db.execute(
                    select(PartnerTransfer).where(PartnerTransfer.partner_id == partner_id)
                ).scalars().all()
            ),
            "followups": len(
                db.execute(
                    select(Followup).where(Followup.partner_id == partner_id)
                ).scalars().all()
            ),
        }

    return {
        "duplicata": counts(duplicate.id),
        "canonico": counts(canonical.id),
    }


def merge_partner(
    db: Session,
    duplicate: Partner,
    canonical: Partner,
    *,
    user_id: str | None = None,
    request_id: str | None = None,
    arquivar_unidades: bool = True,
) -> dict:
    """Migra tudo da duplicata para o canônico e arquiva a duplicata.

    Executa dentro da transação corrente do chamador — quem chama decide o
    commit. Levanta PartnerMergeError em qualquer pré-condição violada.
    """
    if duplicate.id == canonical.id:
        raise PartnerMergeError("Duplicata e canônico são o mesmo parceiro.")
    if canonical.arquivado or canonical.merged_into_partner_id:
        raise PartnerMergeError("O parceiro canônico não pode estar arquivado.")
    if duplicate.merged_into_partner_id:
        raise PartnerMergeError("A duplicata já foi consolidada anteriormente.")

    resultado: dict = {
        "duplicata_public_code": duplicate.public_code,
        "canonico_public_code": canonical.public_code,
        "contatos_migrados": [],
        "contatos_duplicados_ignorados": [],
        "parcerias_migradas": [],
        "unidades_migradas": [],
        "unidades_arquivadas": [],
        "encaminhamentos_migrados": 0,
        "acertos_migrados": 0,
        "repasses_migrados": 0,
        "followups_migrados": 0,
        "exames_migrados": 0,
        "metadados_de_unidade": [],
    }

    # 1. Contatos — dedupe por chave natural, nunca por id.
    existentes = {
        _contact_key(c): c
        for c in db.execute(
            select(PartnerContact).where(PartnerContact.partner_id == canonical.id)
        ).scalars().all()
    }
    for contato in db.execute(
        select(PartnerContact).where(PartnerContact.partner_id == duplicate.id)
    ).scalars().all():
        chave = _contact_key(contato)
        gemeo = existentes.get(chave)
        if gemeo is not None:
            # Mesmo contato já presente no canônico: não duplicar. O registro
            # da duplicata é desativado, mas permanece para auditoria.
            contato.ativo = False
            resultado["contatos_duplicados_ignorados"].append(contato.public_code)
            continue
        contato.partner_id = canonical.id
        # Uma unidade da duplicata pode ter sido arquivada abaixo; o vínculo
        # de unidade só sobrevive se a unidade também migrar.
        existentes[chave] = contato
        resultado["contatos_migrados"].append(contato.public_code)

    # 2. Parcerias.
    for parceria in db.execute(
        select(Partnership).where(Partnership.partner_id == duplicate.id)
    ).scalars().all():
        parceria.partner_id = canonical.id
        resultado["parcerias_migradas"].append(parceria.public_code)

    # 3. Unidades. Unidade COM relacionamento real migra; unidade vazia é
    #    arquivada (ativo=False) e seus metadados úteis preenchem lacunas da
    #    unidade canônica — sem sobrescrever nada já informado.
    unidades_canonicas = db.execute(
        select(PartnerUnit).where(PartnerUnit.partner_id == canonical.id)
    ).scalars().all()
    alvo = unidades_canonicas[0] if len(unidades_canonicas) == 1 else None
    for unidade in db.execute(
        select(PartnerUnit).where(PartnerUnit.partner_id == duplicate.id)
    ).scalars().all():
        usada = _unit_in_use(db, unidade.id)
        if usada or not arquivar_unidades:
            unidade.partner_id = canonical.id
            resultado["unidades_migradas"].append(unidade.public_code)
            continue
        if alvo is not None:
            for campo in ("bairro", "cidade"):
                origem = getattr(unidade, campo)
                if origem and not getattr(alvo, campo):
                    setattr(alvo, campo, origem)
                    resultado["metadados_de_unidade"].append(f"{campo}<-{unidade.public_code}")
        unidade.ativo = False
        unidade.partner_id = canonical.id
        db.add(
            LegacyAlias(
                entidade="partner_units",
                entity_id=(alvo.id if alvo is not None else unidade.id),
                legacy_source=LEGACY_SOURCE_MERGE,
                legacy_id=unidade.public_code,
            )
        )
        resultado["unidades_arquivadas"].append(unidade.public_code)

    # 4. Demais vínculos técnicos.
    for model, chave in (
        (PartnerReferral, "encaminhamentos_migrados"),
        (PartnerSettlement, "acertos_migrados"),
        (PartnerTransfer, "repasses_migrados"),
        (Followup, "followups_migrados"),
        (SpirometryExam, "exames_migrados"),
    ):
        linhas = db.execute(
            select(model).where(model.partner_id == duplicate.id)
        ).scalars().all()
        for linha in linhas:
            linha.partner_id = canonical.id
        resultado[chave] = len(linhas)

    # 5. Alias legado: o código público antigo continua resolvendo, e o
    #    legacy_source/legacy_id de origem da duplicata é preservado.
    _ensure_alias(db, canonical.id, LEGACY_SOURCE_MERGE, duplicate.public_code)
    if duplicate.legacy_source and duplicate.legacy_id:
        _ensure_alias(db, canonical.id, duplicate.legacy_source, duplicate.legacy_id)

    # 6. Arquivamento — sem apagar, sem renumerar.
    duplicate.arquivado = True
    duplicate.merged_into_partner_id = canonical.id
    duplicate.status = "encerrada"

    db.flush()

    audit(db, "parceiro.consolidado", "partners", duplicate.id, user_id, request_id,
          {"public_code": duplicate.public_code,
           "codigo": canonical.public_code,
           "campos": sorted(k for k, v in resultado.items() if v)})
    audit(db, "parceiro.recebeu_consolidacao", "partners", canonical.id, user_id,
          request_id,
          {"public_code": canonical.public_code, "codigo": duplicate.public_code})
    return resultado


def _ensure_alias(db: Session, entity_id: str, source: str, legacy_id: str) -> None:
    ja = db.execute(
        select(LegacyAlias).where(
            LegacyAlias.entidade == "partners",
            LegacyAlias.legacy_source == source,
            LegacyAlias.legacy_id == legacy_id,
        )
    ).scalars().first()
    if ja is not None:
        ja.entity_id = entity_id
        return
    db.add(
        LegacyAlias(
            entidade="partners",
            entity_id=entity_id,
            legacy_source=source,
            legacy_id=legacy_id,
        )
    )


def _unit_in_use(db: Session, unit_id: str) -> bool:
    """Unidade "em uso" = tem exame, encaminhamento ou agenda operacional."""
    from ..models import PartnerUnitConfig

    for model, coluna in (
        (SpirometryExam, SpirometryExam.partner_unit_id),
        (PartnerReferral, PartnerReferral.partner_unit_id),
        (PartnerUnitConfig, PartnerUnitConfig.partner_unit_id),
    ):
        if db.execute(select(model).where(coluna == unit_id).limit(1)).scalars().first():
            return True
    return False
