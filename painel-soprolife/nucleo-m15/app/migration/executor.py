"""Execução real, controlada e final do plano multiaba (M15.6C).

Caminho ÚNICO de escrita do formato multiaba — somente CLI, somente admin,
somente com TODOS os portões verdes e frase exata digitada interativamente.
Não existe endpoint de API nem botão de navegador para este fluxo.

Contrato central:

- o plano de execução é DERIVADO (nunca digitado): envelope privado íntegro
  + staging determinístico M15.6B + decisões humanas aprovadas. Mesmo
  envelope + mesmo mapping + mesmas decisões => mesmo plano, mesmo hash;
- identidade: só ID explícito da fonte ou decisão humana aprovada vincula;
  telefone só vincula com decisão explícita (vincular_candidato) e com
  candidato ÚNICO; nome isolado NUNCA vincula; nenhuma fusão silenciosa;
- dinheiro: Financeiro_Lancamentos é a única fonte monetária; vínculo só
  por referência técnica aprovada; Pastore/CRM jamais criam lançamento;
- PCMSO permanece excluído e não cria NENHUMA linha operacional;
- transação única: o chamador (CLI) comita no fim; qualquer falha deixa o
  lote inteiro para rollback (fail-closed);
- toda linha criada ganha proveniência (lote, domínio, fingerprint
  irreversível, mapping, chave de idempotência determinística única);
- reexecutar o mesmo lote não cria nenhuma linha nova;
- rollback seletivo usa SOMENTE IDs criados pelo lote, em ordem reversa,
  nunca toca linha preexistente e falha fechado com proveniência incompleta.
"""

import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, inspect as sa_inspect, select, text
from sqlalchemy.orm import Session

from ..audit import audit
from ..db import Base
from ..ids import allocate_public_code
from ..importer.csv_import import _acquire_execute_lock
from ..models import (
    Consent,
    Consultation,
    FinancialEntry,
    Followup,
    ImportBatch,
    ImportRow,
    Interaction,
    Lead,
    LegacyAlias,
    MigrationDecision,
    MigrationProvenance,
    Partner,
    PartnerContact,
    PartnerReferral,
    PartnerSettlement,
    PartnerTransfer,
    PartnerUnit,
    PartnerUnitConfig,
    Partnership,
    PaymentAllocation,
    Person,
    PersonContact,
    Role,
    SpirometryExam,
    User,
    UserRole,
)
from ..normalize import normalize_name, normalize_phone
from .adapters import ADAPTERS, ADAPTERS_VERSION
from .manifest import ManifestError, validate_backup_evidence
from .multisheet import (
    MULTI_SHEET_SOURCE_TYPE,
    MultiSheetError,
    _decisions_by_token,
    _get_multi_batch,
    _private_token,
    sanitize_multi_sheet_plan,
)
from .rawsnapshot import load_raw_envelope
from .staging import (
    CAT_CANDIDATO_IDENTIDADE,
    CAT_CONSULTA_ORFA,
    CAT_DATA_INVALIDA,
    CAT_DUPLICATA_EXECUCAO,
    CAT_EXAME_ORFAO,
    CAT_FINANCEIRO_NAO_RESOLVIDO,
    CAT_FONTE_MONETARIA_BLOQUEADA,
    CAT_ID_CONFLITANTE,
    CAT_ID_NAO_ENCONTRADO,
    CAT_REGISTRO_ORFAO,
    ST_DUPLICADA,
    ST_EXCLUIDA,
    ST_JA_EXISTENTE,
    ST_PENDENTE,
    ST_REJEITADA,
    ST_VALIDA,
    StagingError,
    parse_source_datetime_or_date,
    stage_multi_sheet,
)

EXEC_PLAN_VERSION = "m15.multi-sheet-execute.1"
CONFIRMATION_TEMPLATE_MULTI = "EXECUTAR MIGRACAO MULTIABA {batch_id}"
ROLLBACK_TEMPLATE_MULTI = "REVERTER MIGRACAO MULTIABA {batch_id}"

STATUS_EXECUTADO = "executado_aguardando_reconciliacao"
STATUS_CONCLUIDO = "concluido"
STATUS_DIVERGENTE = "executado_com_divergencia"
STATUS_REVERTIDO = "revertido"

# Decisões humanas com efeito executável, por categoria da fila (fechado).
# "resolvido"/"adiado" NUNCA executam nada: item pendente sem decisão
# executável mantém a execução bloqueada (fail-closed).
DECISOES_EXECUTAVEIS_POR_CATEGORIA = {
    CAT_CANDIDATO_IDENTIDADE: frozenset(
        ("vincular_candidato", "criar_pessoa", "excluido")),
    CAT_EXAME_ORFAO: frozenset(("criar_pessoa", "excluido")),
    CAT_CONSULTA_ORFA: frozenset(("criar_pessoa", "excluido")),
    CAT_REGISTRO_ORFAO: frozenset(("criar_pessoa", "excluido")),
    CAT_ID_CONFLITANTE: frozenset(("excluido",)),
    CAT_ID_NAO_ENCONTRADO: frozenset(("excluido",)),
    CAT_DATA_INVALIDA: frozenset(("excluido",)),
    CAT_FINANCEIRO_NAO_RESOLVIDO: frozenset(("excluido",)),
    CAT_FONTE_MONETARIA_BLOQUEADA: frozenset(("excluido",)),
    CAT_DUPLICATA_EXECUCAO: frozenset(
        ("manter_primeira", "manter_segunda", "manter_ambas")),
}

# Ordem FINAL de inserção por entidade (contrato M15.6C, fixa e estável).
ORDEM_ENTIDADES = {
    "partners": 1,
    "partner_units": 2,
    "partner_unit_configs": 3,
    "partner_contacts": 4,
    "partnerships": 5,
    "people": 6,
    "person_contacts": 7,
    "consents": 8,
    "leads": 9,
    "spirometry_exams": 10,
    "consultations": 11,
    "followups": 12,
    "financial_entries": 13,
    "legacy_aliases": 14,
}

_MODELOS = {
    "partners": Partner,
    "partner_units": PartnerUnit,
    "partner_unit_configs": PartnerUnitConfig,
    "partner_contacts": PartnerContact,
    "partnerships": Partnership,
    "people": Person,
    "person_contacts": PersonContact,
    "consents": Consent,
    "leads": Lead,
    "spirometry_exams": SpirometryExam,
    "consultations": Consultation,
    "followups": Followup,
    "financial_entries": FinancialEntry,
    "legacy_aliases": LegacyAlias,
}

_TIPOS_MOVIMENTO = {"receita", "despesa", "repasse"}


def confirmation_phrase_multiaba(batch_id: str) -> str:
    return CONFIRMATION_TEMPLATE_MULTI.format(batch_id=batch_id)


def rollback_phrase_multiaba(batch_id: str) -> str:
    return ROLLBACK_TEMPLATE_MULTI.format(batch_id=batch_id)


def deterministic_plan_hash(report: dict) -> str:
    """Hash canônico do relatório sanitizado do dry-run (determinístico)."""
    canonical = json.dumps(
        report, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trunc(valor: str | None, n: int) -> str | None:
    if not valor:
        return None
    return str(valor)[:n]


def _fingerprint(envelope_sha256: str, aba: str, linha: int) -> str:
    """Fingerprint IRREVERSÍVEL da referência privada de origem."""
    return hashlib.sha256(
        f"{envelope_sha256}:{aba}:{linha}".encode("utf-8")
    ).hexdigest()


def _idem_legacy(entidade: str, fonte: str, legacy_id: str) -> str:
    return hashlib.sha256(
        f"m15-multiaba:{entidade}:{fonte}:{legacy_id}".encode("utf-8")
    ).hexdigest()


def _idem_linha(entidade: str, envelope_sha256: str, aba: str, linha: int,
                subtipo: str) -> str:
    return hashlib.sha256(
        f"m15-multiaba:{entidade}:{envelope_sha256}:{aba}:{linha}:{subtipo}"
        .encode("utf-8")
    ).hexdigest()


def resolve_admin(db: Session, email: str | None) -> User:
    """Identidade administrativa EXATA: usuário existente, ativo e admin."""
    if not email:
        raise MultiSheetError("administrador_obrigatorio")
    user = db.execute(
        select(User).where(User.email == email.lower())
    ).scalar_one_or_none()
    if user is None:
        raise MultiSheetError("administrador_inexistente")
    if not user.ativo:
        raise MultiSheetError("administrador_inativo")
    if "admin" not in {role.name for role in user.roles}:
        raise MultiSheetError("papel_admin_obrigatorio")
    return user


def _normalizar_consentimento(valor: str) -> tuple[str, bool]:
    texto = normalize_name(valor)
    nao_contatar = "nao contatar" in texto or "nao_contatar" in texto
    if texto in ("sim", "true", "concedido", "autorizado", "aceito"):
        return "concedido", False
    if nao_contatar or texto in ("nao", "false", "revogado", "recusado"):
        return "revogado", nao_contatar
    return "desconhecido", False


# ------------------------------------------------------------------- plano


@dataclass
class OperacaoPlanejada:
    """Uma inserção planejada — payload privado NUNCA vai para log/params."""

    entidade: str
    chave: str
    fonte: str            # sheet_kind de origem
    aba: str
    linha: int
    campos: dict
    refs: dict            # campo FK -> chave do plano OU ("existing", ent, fonte, id)
    idempotency_key: str
    source_fingerprint: str
    legacy: tuple | None = None    # (entidade, legacy_source, legacy_id)
    decisao: str | None = None
    seq: int = 0


@dataclass
class PlanoExecucao:
    envelope_sha256: str
    mapping_version: str
    plan_hash: str
    decisoes_fingerprint: str
    ops: list = field(default_factory=list)
    linhas: list = field(default_factory=list)   # estado FINAL por linha
    bloqueios: list = field(default_factory=list)
    revisoes_pendentes_sem_decisao: int = 0
    financeiro_nao_resolvido: int = 0
    conflitos_nao_resolvidos: int = 0
    linhas_rejeitadas: int = 0
    pcmso_operacoes: int = 0
    relatorio_fresco: dict | None = None

    def manifesto(self) -> dict:
        contagem: dict[str, int] = {}
        for op in self.ops:
            contagem[op.entidade] = contagem.get(op.entidade, 0) + 1
        return dict(sorted(contagem.items()))

    def relacionamentos(self) -> dict:
        contagem: dict[str, int] = {}
        for op in self.ops:
            for campo in sorted(op.refs):
                tipo = f"{op.entidade}.{campo}"
                contagem[tipo] = contagem.get(tipo, 0) + 1
        return dict(sorted(contagem.items()))

    def estados_linhas(self) -> dict:
        contagem: dict[str, int] = {}
        for registro in self.linhas:
            estado = registro["status_final"]
            contagem[estado] = contagem.get(estado, 0) + 1
        return dict(sorted(contagem.items()))

    def rollback_planejado(self) -> list[dict]:
        manifesto = self.manifesto()
        ordem = sorted(
            manifesto, key=lambda ent: ORDEM_ENTIDADES[ent], reverse=True
        )
        return [
            {"entidade": entidade, "linhas_a_remover": manifesto[entidade]}
            for entidade in ordem
        ]

    def resumo_sanitizado(self) -> dict:
        """Resumo SEM nenhum valor de origem (só contagens e hashes)."""
        return {
            "plan_version": EXEC_PLAN_VERSION,
            "plan_hash": self.plan_hash,
            "mapping_version": self.mapping_version,
            "decisoes_fingerprint": self.decisoes_fingerprint,
            "operacoes_planejadas": len(self.ops),
            "manifesto_insercoes": self.manifesto(),
            "relacionamentos_planejados": self.relacionamentos(),
            "estados_finais_linhas": self.estados_linhas(),
            "rollback_planejado": self.rollback_planejado(),
            "bloqueios": sorted(self.bloqueios),
            "revisoes_pendentes_sem_decisao":
                self.revisoes_pendentes_sem_decisao,
            "financeiro_nao_resolvido": self.financeiro_nao_resolvido,
            "conflitos_nao_resolvidos": self.conflitos_nao_resolvidos,
            "linhas_rejeitadas": self.linhas_rejeitadas,
        }


class _PlanBuilder:
    """Transforma registros privados por linha + decisões em operações."""

    def __init__(self, envelope_sha256: str, existentes: set,
                 decisoes: dict, plano: PlanoExecucao):
        self.sha = envelope_sha256
        self.existentes = existentes
        self.decisoes = decisoes          # raw token -> decisão executável
        self.plano = plano
        self.chaves: set[str] = set()
        self.pessoas_por_telefone: dict[str, list[str]] = {}
        self.excluidas_por_decisao: set[str] = set()   # chaves de alias excluídas
        self.parceiro_pastore = False
        self.unidades_pastore: dict[str, str] = {}
        self._seq = 0

    # ------------------------------------------------------------- básicos

    def bloquear(self, codigo: str) -> None:
        if codigo not in self.plano.bloqueios:
            self.plano.bloqueios.append(codigo)

    def op(self, entidade: str, chave: str, fonte: str, aba: str, linha: int,
           campos: dict, refs: dict | None = None,
           legacy: tuple | None = None, subtipo: str = "",
           decisao: str | None = None) -> str:
        self._seq += 1
        if legacy:
            idem = _idem_legacy(entidade, legacy[1], legacy[2])
        else:
            idem = _idem_linha(entidade, self.sha, aba, linha,
                               subtipo or entidade)
        operacao = OperacaoPlanejada(
            entidade=entidade, chave=chave, fonte=fonte, aba=aba, linha=linha,
            campos=campos, refs=refs or {}, idempotency_key=idem,
            source_fingerprint=_fingerprint(self.sha, aba, linha),
            legacy=legacy, decisao=decisao, seq=self._seq,
        )
        self.plano.ops.append(operacao)
        self.chaves.add(chave)
        return chave

    def ref_alias(self, entidade: str, fonte: str, legacy_id: str):
        """Resolve referência técnica: chave do plano, alias já no banco ou
        None (inexistente/excluída)."""
        chave = f"{entidade}:{fonte}:{legacy_id}"
        if chave in self.excluidas_por_decisao:
            return "excluida"
        if chave in self.chaves:
            return chave
        if (entidade, fonte, legacy_id) in self.existentes:
            return ("existing", entidade, fonte, legacy_id)
        return None

    def decisao_da_linha(self, aba: str, linha: int,
                         itens_da_linha: list[dict]) -> tuple[str | None, str | None]:
        """Decisão executável da linha pendente. Retorna (decisao, bloqueio)."""
        pendentes = [i for i in itens_da_linha if i["status"] == "pendente"]
        if not pendentes:
            return None, None
        decisoes = set()
        for item in pendentes:
            executaveis = DECISOES_EXECUTAVEIS_POR_CATEGORIA.get(
                item["categoria"], frozenset())
            registrada = self.decisoes.get(item["token"])
            if registrada is None or registrada not in executaveis:
                self.plano.revisoes_pendentes_sem_decisao += 1
                if item["categoria"] == CAT_FINANCEIRO_NAO_RESOLVIDO:
                    self.plano.financeiro_nao_resolvido += 1
                if item["categoria"] == CAT_ID_CONFLITANTE:
                    self.plano.conflitos_nao_resolvidos += 1
                if item["categoria"] == CAT_DUPLICATA_EXECUCAO:
                    self.plano.conflitos_nao_resolvidos += 1
                return None, "revisao_pendente_sem_decisao_executavel"
            decisoes.add(registrada)
        if len(decisoes) > 1:
            return None, "decisoes_conflitantes_na_mesma_linha"
        return decisoes.pop(), None

    # --------------------------------------------------- pessoas auxiliares

    def indexar_pessoa(self, chave: str, telefone: str) -> None:
        fone = normalize_phone(telefone)
        if fone:
            self.pessoas_por_telefone.setdefault(fone, []).append(chave)

    def criar_pessoa_op(self, kind: str, aba: str, linha: int, campos: dict,
                        chave: str, subtipo: str, decisao: str | None,
                        legacy: tuple | None = None,
                        placeholder_nome: str | None = None) -> str | None:
        nome = (campos.get("nome_pessoa") or "").strip() or placeholder_nome
        if not nome:
            self.bloquear("criar_pessoa_sem_nome_na_origem")
            return None
        telefone = (campos.get("telefone")
                    or campos.get("telefone_whatsapp")
                    or campos.get("paciente_whatsapp") or "")
        pessoa_campos = {"nome_completo": nome}
        if placeholder_nome is not None:
            pessoa_campos["placeholder"] = True
        self.op("people", chave, kind, aba, linha, pessoa_campos,
                legacy=legacy, subtipo=subtipo, decisao=decisao)
        if telefone.strip():
            self.op(
                "person_contacts", f"{chave}:contato", kind, aba, linha,
                {"tipo": "whatsapp", "valor": telefone.strip()},
                refs={"person_id": chave}, subtipo=f"{subtipo}:contato",
                decisao=decisao,
            )
        return chave

    def consentimento_op(self, kind: str, aba: str, linha: int, campos: dict,
                         ref_pessoa) -> None:
        """Consentimento SÓ para pessoa deterministicamente resolvida na
        própria linha (mesma regra do staging M15.6B)."""
        valor = (campos.get("consentimento_whatsapp") or "").strip()
        if not valor:
            return
        status, nao_contatar = _normalizar_consentimento(valor)
        self.op(
            "consents", f"consents:{kind}:{aba}:{linha}", kind, aba, linha,
            {"canal": "whatsapp", "status": status,
             "nao_contatar": nao_contatar, "origem": kind},
            refs={"person_id": ref_pessoa}, subtipo="consent",
        )


def _itens_por_linha(plan: dict) -> dict[tuple, list[dict]]:
    porlinha: dict[tuple, list[dict]] = {}
    for item in plan["fila_revisao"]:
        porlinha.setdefault((item["aba"], item["linha"]), []).append(item)
    return porlinha


def _decisoes_executaveis(db: Session, batch_id: str,
                          envelope_sha256: str, plan: dict) -> dict:
    """Mapa raw_token -> decisão registrada (última por token privado)."""
    privadas = _decisions_by_token(db, batch_id)
    resultado: dict[str, str] = {}
    for item in plan["fila_revisao"]:
        token_privado = _private_token(envelope_sha256, item["token"])
        registrada = privadas.get(token_privado)
        if registrada:
            resultado[item["token"]] = registrada["decision"]
    return resultado


def _decisoes_fingerprint(decisoes: dict) -> str:
    canonical = json.dumps(sorted(decisoes.items()), ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_execution_plan(
    db: Session,
    envelope_name: str,
    batch_id: str,
    base_dir=None,
) -> PlanoExecucao:
    """Deriva o plano de execução decidido, determinístico e fail-closed.

    Nunca escreve nada. Todo desvio (revisão sem decisão executável,
    financeiro não resolvido, conflito, linha rejeitada, referência a linha
    excluída, vínculo ambíguo) vira bloqueio explícito.
    """
    batch = _get_multi_batch(db, batch_id)
    stored = batch.params["multi_sheet_report"]
    try:
        envelope = load_raw_envelope(
            envelope_name, base_dir=base_dir,
            known_kinds=frozenset(ADAPTERS),
        )
    except ManifestError as exc:
        raise MultiSheetError(str(exc).partition(":")[0]) from exc
    if envelope.envelope_sha256 != batch.sha256:
        raise MultiSheetError("envelope_diferente_do_lote_dry_run")
    if envelope.mapping_version != ADAPTERS_VERSION:
        raise MultiSheetError("mapping_version_nao_exata")

    existentes = {
        (alias.entidade, alias.legacy_source, alias.legacy_id)
        for alias in db.execute(select(LegacyAlias)).scalars().all()
    }
    try:
        bare = stage_multi_sheet(envelope, existentes=existentes)
    except StagingError as exc:
        raise MultiSheetError(str(exc).partition(":")[0]) from exc
    fresco = sanitize_multi_sheet_plan(bare, envelope.envelope_sha256)
    hash_fresco = deterministic_plan_hash(fresco)
    hash_armazenado = deterministic_plan_hash(stored)

    decisoes = _decisoes_executaveis(db, batch.id, envelope.envelope_sha256, bare)
    plano = PlanoExecucao(
        envelope_sha256=envelope.envelope_sha256,
        mapping_version=envelope.mapping_version,
        plan_hash=hash_fresco,
        decisoes_fingerprint=_decisoes_fingerprint(decisoes),
        relatorio_fresco=fresco,
    )
    if hash_fresco != hash_armazenado:
        plano.bloqueios.append("plano_deterministico_divergente_do_dry_run")

    builder = _PlanBuilder(envelope.envelope_sha256, existentes, decisoes, plano)
    itens = _itens_por_linha(bare)

    for registro in bare["registros_linhas_privados"]:
        aba, linha = registro["aba"], registro["linha"]
        kind = registro["kind"]
        status, motivo = registro["status"], registro["motivo"]
        campos = registro["campos"] or {}
        final = {"aba": aba, "linha": linha, "kind": kind,
                 "campos": campos, "decisao": None,
                 "fingerprint": _fingerprint(envelope.envelope_sha256, aba, linha)}

        if status == ST_REJEITADA:
            plano.linhas_rejeitadas += 1
            builder.bloquear("linhas_rejeitadas_bloqueiam_execucao")
            final.update(status_final="rejeitado", motivo=motivo)
        elif status == ST_EXCLUIDA:
            final.update(status_final="excluido", motivo=motivo)
        elif status == ST_DUPLICADA:
            _resolver_linha_duplicada(
                builder, kind, aba, linha, campos, motivo,
                itens.get((aba, linha), []), final)
        elif status == ST_JA_EXISTENTE:
            final.update(status_final="ja_existente", motivo=motivo)
        elif status == ST_VALIDA:
            _construir_linha_valida(builder, kind, aba, linha, campos, final)
        elif status == ST_PENDENTE:
            decisao, bloqueio = builder.decisao_da_linha(
                aba, linha, itens.get((aba, linha), []))
            if bloqueio:
                builder.bloquear(bloqueio)
                final.update(status_final="pendente", motivo=bloqueio)
            elif decisao == "excluido":
                _registrar_exclusao_decidida(builder, kind, campos)
                final.update(status_final="excluido",
                             motivo="excluido_por_decisao_humana",
                             decisao="excluido")
            else:
                _construir_linha_decidida(
                    builder, kind, aba, linha, campos, decisao, final)
        else:  # inalcançável: estados do staging são fechados
            raise MultiSheetError("estado_de_linha_desconhecido")
        plano.linhas.append(final)

    if plano.pcmso_operacoes:
        builder.bloquear("pcmso_gerou_operacao_proibida")

    ordem_final = {op.chave: op for op in plano.ops}
    if len(ordem_final) != len(plano.ops):
        raise MultiSheetError("chave_de_plano_duplicada")
    plano.ops.sort(key=lambda op: (ORDEM_ENTIDADES[op.entidade], op.seq))
    _validar_referencias(plano, builder)
    return plano


def _identidade_tecnica_duplicata(kind: str, campos: dict):
    """Identidade fechada usada para decidir duplicatas sem heurística."""
    if kind == "pastore_config":
        unidade = normalize_name(campos.get("unidade", ""))
        return (kind, unidade) if unidade else None
    return None


def _linha_ativa_duplicada(plano: PlanoExecucao, kind: str, campos: dict):
    identidade = _identidade_tecnica_duplicata(kind, campos)
    if identidade is None:
        return None
    for anterior in reversed(plano.linhas):
        if (
            anterior["kind"] == kind
            and anterior.get("status_final") == "importado"
            and _identidade_tecnica_duplicata(kind, anterior["campos"])
            == identidade
        ):
            return anterior
    return None


def _resolver_linha_duplicada(
    builder: _PlanBuilder,
    kind: str,
    aba: str,
    linha: int,
    campos: dict,
    motivo: str | None,
    itens_da_linha: list[dict],
    final: dict,
) -> None:
    """Aplica somente decisões explícitas sobre uma duplicata bloqueante."""
    decisao, bloqueio = builder.decisao_da_linha(
        aba, linha, itens_da_linha)
    if bloqueio:
        final.update(status_final="duplicado", motivo=bloqueio)
        return

    if decisao == "manter_primeira":
        final.update(
            status_final="excluido",
            motivo="duplicata_excluida_por_decisao_humana",
            decisao=decisao,
        )
        return

    anterior = _linha_ativa_duplicada(builder.plano, kind, campos)
    if anterior is None:
        builder.plano.conflitos_nao_resolvidos += 1
        builder.bloquear("duplicata_sem_identidade_tecnica_anterior")
        final.update(
            status_final="duplicado",
            motivo="duplicata_sem_identidade_tecnica_anterior",
            decisao=decisao,
        )
        return

    identidade_atual = _identidade_tecnica_duplicata(kind, campos)
    identidade_anterior = _identidade_tecnica_duplicata(
        kind, anterior["campos"])

    if decisao == "manter_ambas":
        if identidade_atual == identidade_anterior:
            builder.plano.conflitos_nao_resolvidos += 1
            builder.bloquear("duplicatas_com_identidade_tecnica_igual")
            final.update(
                status_final="duplicado",
                motivo="duplicatas_com_identidade_tecnica_igual",
                decisao=decisao,
            )
            return
        _construir_linha_valida(builder, kind, aba, linha, campos, final)
        final["decisao"] = decisao
        return

    if decisao == "manter_segunda" and kind == "pastore_config":
        # A configuração Pastore vem antes dos atendimentos no plano. Neste
        # ponto nenhuma operação posterior pode depender da primeira linha.
        origem = (anterior["aba"], anterior["linha"])
        builder.plano.ops = [
            op for op in builder.plano.ops
            if not (op.fonte == kind and (op.aba, op.linha) == origem)
        ]
        builder.chaves = {op.chave for op in builder.plano.ops}
        builder.parceiro_pastore = any(
            op.chave == "partners:pastore:ativo" for op in builder.plano.ops)
        unidade = normalize_name(campos.get("unidade", ""))
        builder.unidades_pastore.pop(unidade, None)
        anterior.update(
            status_final="excluido",
            motivo="primeira_duplicata_excluida_por_decisao_humana",
            decisao=decisao,
        )
        _construir_linha_valida(builder, kind, aba, linha, campos, final)
        final["decisao"] = decisao
        return

    builder.plano.conflitos_nao_resolvidos += 1
    builder.bloquear("decisao_de_duplicata_sem_aplicacao_segura")
    final.update(
        status_final="duplicado",
        motivo="decisao_de_duplicata_sem_aplicacao_segura",
        decisao=decisao,
    )


def _registrar_exclusao_decidida(builder: _PlanBuilder, kind: str,
                                 campos: dict) -> None:
    """Linha excluída por decisão: propaga a exclusão para o alias técnico
    para que referências a ela falhem fechado."""
    id_campos = {
        "crm_espirometria": ("spirometry_exams", "crm_espirometria", "exame_id"),
        "crm_consultas": ("consultations", "crm_consultas", "consulta_id"),
        "crm_pacientes": ("people", "crm_pacientes", "paciente_id"),
        "leads": ("leads", "leads", "lead_id"),
        "followup_whatsapp": ("followups", "followup_whatsapp", "followup_id"),
        "financeiro_lancamentos":
            ("financial_entries", "financeiro_lancamentos", "id_lancamento"),
    }
    info = id_campos.get(kind)
    if info:
        entidade, fonte, campo = info
        legacy_id = (campos.get(campo) or "").strip()
        if legacy_id:
            builder.excluidas_por_decisao.add(f"{entidade}:{fonte}:{legacy_id}")


def _data_campos(campos: dict, campo: str) -> dict:
    """Campos de data com precisão preservada (nunca inventa dia)."""
    bruto = (campos.get(campo) or "").strip()
    if not bruto:
        return {}
    nd, _dt = parse_source_datetime_or_date(bruto)
    if nd.value is None:
        return {"original": bruto, "precisao": "desconhecida"}
    return {
        "valor": nd.value,
        "original": bruto,
        "precisao": nd.precision,
        "dia_assumido": nd.day_assumed,
    }


def _construir_linha_valida(builder: _PlanBuilder, kind: str, aba: str,
                            linha: int, campos: dict, final: dict) -> None:
    plano = builder.plano
    if kind == "pcmso_historico":
        # inalcançável: PCMSO nunca é válida no staging; contado por defesa
        plano.pcmso_operacoes += 1
        final.update(status_final="excluido", motivo="pcmso_preservado")
        return

    if kind == "crm_clinicas":
        clinica_id = campos["clinica_id"].strip()
        chave = builder.op(  # parceiro + unidade principal + vínculo
            "partners", f"partners:crm_clinicas:{clinica_id}", kind, aba,
            linha,
            {"nome": campos["nome_clinica"].strip(),
             "tipo": _trunc(campos.get("tipo_clinica"), 30) or "clinica",
             "status": _trunc(campos.get("etapa"), 30) or "prospecto"},
            legacy=("partners", "crm_clinicas", clinica_id),
        )
        builder.op(
            "partner_units", f"partner_units:crm_clinicas:{clinica_id}", kind,
            aba, linha,
            {"nome": campos["nome_clinica"].strip(),
             "bairro": _trunc(campos.get("bairro"), 120),
             "cidade": _trunc(campos.get("regiao"), 120)},
            refs={"partner_id": chave}, subtipo="unidade",
        )
        builder.op(
            "partnerships", f"partnerships:crm_clinicas:{clinica_id}", kind,
            aba, linha,
            {"responsavel_soprolife": _trunc(campos.get("responsavel"), 120)},
            refs={"partner_id": chave}, subtipo="parceria",
        )
        final.update(status_final="importado", motivo=None,
                     entidade="partners", chave=chave)
        return

    if kind == "pastore_config":
        unidade_norm = normalize_name(campos["unidade"])
        if not builder.parceiro_pastore:
            builder.op(
                "partners", "partners:pastore:ativo", kind, aba, linha,
                {"nome": "Pastore", "tipo": "clinica", "status": "ativa"},
                legacy=("partners", "pastore", "ativo"), subtipo="pastore",
            )
            builder.parceiro_pastore = True
        chave_unidade = builder.op(
            "partner_units", f"partner_units:pastore_config:{unidade_norm}",
            kind, aba, linha,
            {"nome": campos["unidade"].strip()},
            refs={"partner_id": "partners:pastore:ativo"}, subtipo="unidade",
        )
        builder.unidades_pastore[unidade_norm] = chave_unidade
        builder.op(
            "partner_unit_configs",
            f"partner_unit_configs:pastore_config:{unidade_norm}", kind, aba,
            linha,
            {"status": _trunc(campos.get("status"), 40),
             "dia_semana": _trunc(campos.get("dia_semana"), 40),
             "horario_inicio": _trunc(campos.get("horario_inicio"), 20),
             "horario_fim": _trunc(campos.get("horario_fim"), 20),
             "capacidade_estimada_por_turno":
                 _trunc(campos.get("capacidade_estimada_por_turno"), 40)},
            refs={"partner_unit_id": chave_unidade}, subtipo="config",
        )
        final.update(status_final="importado", motivo=None,
                     entidade="partner_units", chave=chave_unidade)
        return

    if kind == "contatos_b2b":
        contato_id = (campos.get("contato_id") or "").strip()
        clinica_id = campos["clinica_id"].strip()
        parceiro = builder.ref_alias("partners", "crm_clinicas", clinica_id)
        if parceiro in (None, "excluida"):
            builder.bloquear("contato_b2b_sem_clinica_resolvida")
            final.update(status_final="pendente",
                         motivo="contato_b2b_sem_clinica_resolvida")
            return
        chave = builder.op(
            "partner_contacts",
            f"partner_contacts:contatos_b2b:{contato_id or f'{aba}:{linha}'}",
            kind, aba, linha,
            {"nome": campos["nome_contato"].strip(),
             "cargo": _trunc(campos.get("cargo"), 120),
             "telefone": _trunc(campos.get("telefone_whatsapp"), 40),
             "email": _trunc(campos.get("email"), 200)},
            refs={"partner_id": parceiro},
            legacy=(("partner_contacts", "contatos_b2b", contato_id)
                    if contato_id else None),
        )
        final.update(status_final="importado", motivo=None,
                     entidade="partner_contacts", chave=chave)
        return

    if kind == "crm_pacientes":
        paciente_id = campos["paciente_id"].strip()
        chave = f"people:crm_pacientes:{paciente_id}"
        builder.criar_pessoa_op(
            kind, aba, linha, campos, chave, subtipo="pessoa", decisao=None,
            legacy=("people", "crm_pacientes", paciente_id),
        )
        builder.indexar_pessoa(chave, campos.get("telefone", ""))
        builder.consentimento_op(kind, aba, linha, campos, chave)
        final.update(status_final="importado", motivo=None,
                     entidade="people", chave=chave)
        return

    if kind == "leads":
        lead_id = campos["lead_id"].strip()
        entidade_id = (campos.get("entidade_id") or "").strip()
        if entidade_id:
            pessoa = builder.ref_alias("people", "crm_pacientes", entidade_id)
            if pessoa in (None, "excluida"):
                builder.bloquear("lead_referencia_pessoa_nao_disponivel")
                final.update(status_final="pendente",
                             motivo="lead_referencia_pessoa_nao_disponivel")
                return
        else:
            # política explícita M15.6B mantida: lead sem pessoa vira pessoa
            # PLACEHOLDER determinística (uma por lead), nunca vínculo por nome
            pessoa = builder.criar_pessoa_op(
                kind, aba, linha, campos,
                f"people:leads_placeholder:{lead_id}",
                subtipo="pessoa_placeholder", decisao=None,
                placeholder_nome=f"Lead {lead_id} (sem pessoa vinculada)",
            )
            if pessoa is None:
                final.update(status_final="pendente",
                             motivo="criar_pessoa_sem_nome_na_origem")
                return
        chave = builder.op(
            "leads", f"leads:leads:{lead_id}", kind, aba, linha,
            {"origem": _trunc(campos.get("origem"), 120),
             "servico_interesse": _trunc(campos.get("servico_interesse"), 60),
             "etapa": _trunc(campos.get("etapa"), 30) or "novo",
             "responsavel": _trunc(campos.get("responsavel"), 120),
             "data_primeiro_contato": _data_campos(campos, "data_contato")},
            refs={"person_id": pessoa},
            legacy=("leads", "leads", lead_id),
        )
        final.update(status_final="importado", motivo=None,
                     entidade="leads", chave=chave)
        return

    if kind == "crm_consultas":
        consulta_id = campos["consulta_id"].strip()
        paciente_id = (campos.get("paciente_id") or "").strip()
        pessoa = builder.ref_alias("people", "crm_pacientes", paciente_id)
        if pessoa in (None, "excluida"):
            builder.bloquear("consulta_referencia_pessoa_nao_disponivel")
            final.update(status_final="pendente",
                         motivo="consulta_referencia_pessoa_nao_disponivel")
            return
        chave = _op_consulta(builder, kind, aba, linha, campos, consulta_id,
                             pessoa, decisao=None)
        builder.consentimento_op(kind, aba, linha, campos, pessoa)
        final.update(status_final="importado", motivo=None,
                     entidade="consultations", chave=chave)
        return

    if kind == "financeiro_lancamentos":
        _op_financeiro(builder, kind, aba, linha, campos, final)
        return

    # inalcançável para os kinds restantes (sempre pendentes/excluídos)
    builder.bloquear(f"linha_valida_sem_construtor:{kind}")
    final.update(status_final="pendente",
                 motivo="linha_valida_sem_construtor")


def _op_consulta(builder: _PlanBuilder, kind: str, aba: str, linha: int,
                 campos: dict, consulta_id: str, pessoa, decisao) -> str:
    return builder.op(
        "consultations", f"consultations:crm_consultas:{consulta_id}", kind,
        aba, linha,
        {"data_consulta": _data_campos(campos, "data_consulta"),
         "modalidade": _trunc(campos.get("tipo_consulta"), 30),
         "profissional": _trunc(campos.get("medica"), 200),
         "status": _trunc(campos.get("status"), 20) or "Agendada",
         "origem": _trunc(campos.get("origem"), 120),
         "responsavel": _trunc(campos.get("responsavel"), 120)},
        refs={"person_id": pessoa},
        legacy=("consultations", "crm_consultas", consulta_id),
        decisao=decisao,
    )


def _op_exame(builder: _PlanBuilder, kind: str, aba: str, linha: int,
              campos: dict, chave: str, pessoa, decisao,
              legacy: tuple | None, refs_extra: dict | None = None) -> str:
    refs = {"person_id": pessoa}
    refs.update(refs_extra or {})
    campo_data = ("data_exame" if kind == "crm_espirometria"
                  else "data_atendimento")
    return builder.op(
        "spirometry_exams", chave, kind, aba, linha,
        {"data_exame": _data_campos(campos, campo_data),
         "status": _trunc(campos.get("status_exame")
                          or campos.get("status"), 20) or "Aguardando",
         "origem": _trunc(campos.get("origem"), 120),
         "responsavel": _trunc(campos.get("responsavel"), 120),
         "modalidade": ("clinica_parceira"
                        if kind == "pastore_atendimentos" else None)},
        refs=refs, legacy=legacy, decisao=decisao, subtipo="exame",
    )


def _op_financeiro(builder: _PlanBuilder, kind: str, aba: str, linha: int,
                   campos: dict, final: dict) -> None:
    """ÚNICA origem de lançamento financeiro: referência técnica aprovada."""
    lancamento_id = campos["id_lancamento"].strip()
    id_atendimento = (campos.get("id_atendimento") or "").strip()
    exame = builder.ref_alias(
        "spirometry_exams", "crm_espirometria", id_atendimento)
    if exame == "excluida":
        builder.bloquear("financeiro_referencia_linha_excluida")
        final.update(status_final="pendente",
                     motivo="financeiro_referencia_linha_excluida")
        return
    if exame is None:
        builder.bloquear("financeiro_sem_referencia_tecnica_resolvida")
        builder.plano.financeiro_nao_resolvido += 1
        final.update(status_final="pendente",
                     motivo="financeiro_sem_referencia_tecnica_resolvida")
        return
    tipo = normalize_name(campos.get("tipo_movimento", ""))
    if tipo not in _TIPOS_MOVIMENTO:
        builder.bloquear("tipo_movimento_nao_suportado")
        final.update(status_final="pendente",
                     motivo="tipo_movimento_nao_suportado")
        return
    valor = ""
    for campo in ("valor_cobrado", "valor_recebido", "valor_tabela"):
        bruto = (campos.get(campo) or "").replace(" ", "")
        if bruto:
            valor = bruto.replace(",", ".")
            break
    if not valor or float(valor) <= 0:
        builder.bloquear("valor_monetario_ausente_ou_nao_positivo")
        final.update(status_final="pendente",
                     motivo="valor_monetario_ausente_ou_nao_positivo")
        return
    chave = builder.op(
        "financial_entries", f"financial_entries:financeiro:{lancamento_id}",
        kind, aba, linha,
        {"tipo": tipo,
         "categoria": _trunc(campos.get("servico"), 60),
         "valor": valor,
         "data_competencia": _data_campos(campos, "data_exame"),
         "status": _trunc(campos.get("status_pagamento"), 20) or "Pendente",
         "forma_pagamento": _trunc(campos.get("forma_pagamento"), 20),
         "origem_preco": _trunc(campos.get("origem_preco"), 20)},
        refs={"spirometry_exam_id": exame},
        legacy=("financial_entries", "financeiro_lancamentos", lancamento_id),
    )
    final.update(status_final="importado", motivo=None,
                 entidade="financial_entries", chave=chave)


def _construir_linha_decidida(builder: _PlanBuilder, kind: str, aba: str,
                              linha: int, campos: dict, decisao: str,
                              final: dict) -> None:
    """Linha pendente com decisão executável aprovada (nunca automática)."""
    final["decisao"] = decisao
    plano = builder.plano

    if decisao == "vincular_candidato":
        telefone = (campos.get("telefone")
                    or campos.get("telefone_whatsapp")
                    or campos.get("paciente_whatsapp") or "")
        candidatos = builder.pessoas_por_telefone.get(
            normalize_phone(telefone), [])
        if len(candidatos) != 1:
            builder.bloquear("vinculo_aprovado_sem_candidato_unico")
            final.update(status_final="pendente",
                         motivo="vinculo_aprovado_sem_candidato_unico")
            return
        pessoa = candidatos[0]
    elif decisao == "criar_pessoa":
        chave_pessoa = f"people:decisao:{aba}:{linha}"
        pessoa = builder.criar_pessoa_op(
            kind, aba, linha, campos, chave_pessoa,
            subtipo="pessoa_decisao", decisao=decisao)
        if pessoa is None:
            final.update(status_final="pendente",
                         motivo="criar_pessoa_sem_nome_na_origem")
            return
    else:  # inalcançável: vocabulário fechado por categoria
        builder.bloquear("decisao_sem_construtor")
        final.update(status_final="pendente", motivo="decisao_sem_construtor")
        return

    if kind == "crm_espirometria":
        exame_id = campos["exame_id"].strip()
        chave = _op_exame(
            builder, kind, aba, linha, campos,
            f"spirometry_exams:crm_espirometria:{exame_id}", pessoa, decisao,
            legacy=("spirometry_exams", "crm_espirometria", exame_id))
        final.update(status_final="importado", motivo=None,
                     entidade="spirometry_exams", chave=chave)
        return
    if kind == "pastore_atendimentos":
        unidade_norm = normalize_name(campos.get("unidade", ""))
        chave_unidade = builder.unidades_pastore.get(unidade_norm)
        if chave_unidade is None:
            builder.bloquear("atendimento_pastore_sem_unidade_configurada")
            final.update(status_final="pendente",
                         motivo="atendimento_pastore_sem_unidade_configurada")
            return
        chave = _op_exame(
            builder, kind, aba, linha, campos,
            f"spirometry_exams:pastore:{aba}:{linha}", pessoa, decisao,
            legacy=None,
            refs_extra={"partner_id": "partners:pastore:ativo",
                        "partner_unit_id": chave_unidade})
        final.update(status_final="importado", motivo=None,
                     entidade="spirometry_exams", chave=chave)
        return
    if kind == "crm_consultas":
        consulta_id = campos["consulta_id"].strip()
        chave = _op_consulta(builder, kind, aba, linha, campos, consulta_id,
                             pessoa, decisao)
        final.update(status_final="importado", motivo=None,
                     entidade="consultations", chave=chave)
        return
    if kind == "followup_whatsapp":
        followup_id = campos["followup_id"].strip()
        data = _data_campos(campos, "data_prevista")
        due = data.get("valor") if data.get("precisao") == "dia" else None
        chave = builder.op(
            "followups", f"followups:followup_whatsapp:{followup_id}", kind,
            aba, linha,
            {"tipo": "manual",
             "due_date": due,
             "status": ("concluido"
                        if normalize_name(campos.get("status", "")) ==
                        "concluido" else "pendente"),
             "responsavel": _trunc(campos.get("responsavel"), 120)},
            refs={"person_id": pessoa},
            legacy=("followups", "followup_whatsapp", followup_id),
            decisao=decisao,
        )
        final.update(status_final="importado", motivo=None,
                     entidade="followups", chave=chave)
        return
    if kind == "leads":
        lead_id = campos["lead_id"].strip()
        chave = builder.op(
            "leads", f"leads:leads:{lead_id}", kind, aba, linha,
            {"origem": _trunc(campos.get("origem"), 120),
             "servico_interesse": _trunc(campos.get("servico_interesse"), 60),
             "etapa": _trunc(campos.get("etapa"), 30) or "novo",
             "responsavel": _trunc(campos.get("responsavel"), 120),
             "data_primeiro_contato": _data_campos(campos, "data_contato")},
            refs={"person_id": pessoa},
            legacy=("leads", "leads", lead_id), decisao=decisao,
        )
        final.update(status_final="importado", motivo=None,
                     entidade="leads", chave=chave)
        return
    builder.bloquear(f"decisao_sem_alvo_para_kind:{kind}")
    final.update(status_final="pendente",
                 motivo=f"decisao_sem_alvo_para_kind:{kind}")


def _validar_referencias(plano: PlanoExecucao, builder: _PlanBuilder) -> None:
    """Toda referência do plano precisa resolver para operação ou alias
    existente no banco — nunca para o vazio (fail-closed)."""
    chaves = {op.chave for op in plano.ops}
    for op in plano.ops:
        for campo, alvo in op.refs.items():
            if isinstance(alvo, tuple) and alvo and alvo[0] == "existing":
                continue
            if alvo not in chaves:
                plano.bloqueios.append(
                    f"referencia_sem_operacao:{op.entidade}.{campo}")


# ------------------------------------------------------------------ legacy
# aliases planejados são derivados das operações com legacy explícito.


def _ops_com_aliases(plano: PlanoExecucao) -> list[OperacaoPlanejada]:
    """Materializa operações de legacy_aliases a partir das entidades."""
    aliases: list[OperacaoPlanejada] = []
    seq = max((op.seq for op in plano.ops), default=0)
    for op in plano.ops:
        if not op.legacy or op.entidade == "legacy_aliases":
            continue
        entidade_alias, fonte, legacy_id = op.legacy
        seq += 1
        aliases.append(OperacaoPlanejada(
            entidade="legacy_aliases",
            chave=f"legacy_aliases:{entidade_alias}:{fonte}:{legacy_id}",
            fonte=op.fonte, aba=op.aba, linha=op.linha,
            campos={"entidade": entidade_alias, "legacy_source": fonte,
                    "legacy_id": legacy_id},
            refs={"entity_id": op.chave},
            idempotency_key=_idem_legacy("legacy_aliases", fonte,
                                         f"{entidade_alias}:{legacy_id}"),
            source_fingerprint=op.source_fingerprint,
            legacy=None, decisao=op.decisao, seq=seq,
        ))
    return aliases


def finalize_plan(plano: PlanoExecucao) -> PlanoExecucao:
    """Anexa aliases derivados e reordena — chamada única após o build."""
    plano.ops.extend(_ops_com_aliases(plano))
    plano.ops.sort(key=lambda op: (ORDEM_ENTIDADES[op.entidade], op.seq))
    return plano


def build_final_plan(db: Session, envelope_name: str, batch_id: str,
                     base_dir=None) -> PlanoExecucao:
    return finalize_plan(
        build_execution_plan(db, envelope_name, batch_id, base_dir))


# ------------------------------------------------------------------ portões


def _gate(ok: bool, detalhe: str) -> dict:
    return {"ok": bool(ok), "detalhe": detalhe}


def _schema_head_gate(db: Session) -> dict:
    """Esquema na cabeça: todas as tabelas do metadata presentes e, quando o
    banco é gerido por Alembic, versão corrente == head dos scripts."""
    bind = db.get_bind()
    inspector = sa_inspect(bind)
    tabelas = set(inspector.get_table_names())
    faltando = sorted(t for t in Base.metadata.tables if t not in tabelas)
    if faltando:
        return _gate(False, "tabelas_ausentes:" + ",".join(faltando))
    if "alembic_version" not in tabelas:
        return _gate(True, "esquema_completo_sem_alembic_version")
    atual = db.execute(
        text("SELECT version_num FROM alembic_version")).scalar()
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        raiz = pathlib.Path(__file__).resolve().parents[2]
        cfg = Config(str(raiz / "alembic.ini"))
        cfg.set_main_option("script_location", str(raiz / "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001 — fail-closed sem vazar caminho
        return _gate(False, "scripts_de_migracao_indisponiveis")
    return _gate(atual == head, f"{atual} vs head {head}")


def _security_fingerprint(db: Session) -> str:
    """Digest irreversível de usuários, papéis e credenciais (hashes)."""
    users = db.execute(
        select(User.id, User.email, User.ativo, User.password_hash)
        .order_by(User.id)
    ).all()
    roles = db.execute(select(Role.id, Role.name).order_by(Role.id)).all()
    vinculos = db.execute(
        select(UserRole.user_id, UserRole.role_id)
        .order_by(UserRole.user_id, UserRole.role_id)
    ).all()
    blob = json.dumps(
        [[list(map(str, r)) for r in users],
         [list(map(str, r)) for r in roles],
         [list(map(str, r)) for r in vinculos]],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tabelas_contadas(db: Session) -> dict[str, int]:
    contagens = {
        nome: int(db.execute(
            select(func.count()).select_from(modelo)).scalar_one())
        for nome, modelo in _MODELOS.items()
    }
    contagens["import_rows"] = int(db.execute(
        select(func.count()).select_from(ImportRow)).scalar_one())
    contagens["migration_provenance"] = int(db.execute(
        select(func.count()).select_from(MigrationProvenance)).scalar_one())
    contagens["users"] = int(db.execute(
        select(func.count()).select_from(User)).scalar_one())
    contagens["roles"] = int(db.execute(
        select(func.count()).select_from(Role)).scalar_one())
    return contagens


def _execucao_previa(db: Session, sha256: str) -> ImportBatch | None:
    return db.execute(
        select(ImportBatch).where(
            ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE,
            ImportBatch.sha256 == sha256,
            ImportBatch.modo == "executado",
        )
    ).scalars().first()


def preflight_multi_sheet_execution(
    db: Session,
    envelope_name: str,
    batch_id: str,
    backup_evidence: str | None = None,
    admin_email: str | None = None,
    base_dir=None,
) -> dict:
    """Avalia TODOS os portões de execução multiaba. Nunca escreve nada."""
    gates: dict[str, dict] = {}
    plano: PlanoExecucao | None = None

    try:
        batch = _get_multi_batch(db, batch_id)
        gates["lote_dry_run_valido"] = _gate(True, batch.id)
    except MultiSheetError as exc:
        gates["lote_dry_run_valido"] = _gate(False, exc.codigo)
        return {"ok": False, "gates": gates, "frase_confirmacao": None,
                "plan_hash": None, "resumo_plano": None}

    try:
        plano = build_final_plan(db, envelope_name, batch_id, base_dir)
        gates["envelope_privado_integro"] = _gate(
            True, f"sha256:{plano.envelope_sha256[:12]}…")
        gates["mapping_version_exata"] = _gate(True, plano.mapping_version)
    except MultiSheetError as exc:
        codigo = exc.codigo
        gates["envelope_privado_integro"] = _gate(
            codigo not in ("checksum_divergente_da_aba", "arquivo_ausente",
                           "arquivo_de_aba_ausente",
                           "conteudo_proibido_no_envelope"),
            codigo)
        gates["mapping_version_exata"] = _gate(
            codigo != "mapping_version_nao_exata", codigo)
        gates["plano_derivavel"] = _gate(False, codigo)
        return {"ok": False, "gates": gates, "frase_confirmacao": None,
                "plan_hash": None, "resumo_plano": None}

    gates["plano_hash_identico"] = _gate(
        "plano_deterministico_divergente_do_dry_run" not in plano.bloqueios,
        plano.plan_hash)
    gates["revisoes_resolvidas"] = _gate(
        plano.revisoes_pendentes_sem_decisao == 0,
        f"pendentes_sem_decisao={plano.revisoes_pendentes_sem_decisao}")
    gates["financeiro_resolvido"] = _gate(
        plano.financeiro_nao_resolvido == 0,
        f"nao_resolvidas={plano.financeiro_nao_resolvido}")
    gates["duplicatas_conflitos_resolvidos"] = _gate(
        plano.conflitos_nao_resolvidos == 0,
        f"conflitos={plano.conflitos_nao_resolvidos}")
    gates["sem_linhas_rejeitadas"] = _gate(
        plano.linhas_rejeitadas == 0,
        f"rejeitadas={plano.linhas_rejeitadas}")
    gates["pcmso_preservado_excluido"] = _gate(
        plano.pcmso_operacoes == 0, "nenhuma_operacao_pcmso"
        if plano.pcmso_operacoes == 0 else "pcmso_gerou_operacao")
    outros = [b for b in plano.bloqueios if b not in (
        "plano_deterministico_divergente_do_dry_run",
        "linhas_rejeitadas_bloqueiam_execucao",
        "revisao_pendente_sem_decisao_executavel",
    )]
    gates["plano_sem_bloqueios"] = _gate(
        not outros, ";".join(sorted(outros)) or "sem_bloqueios")

    if backup_evidence:
        evidencia = validate_backup_evidence(backup_evidence, base_dir)
        gates["backup_validado"] = _gate(
            evidencia["ok"],
            "backup_integro" if evidencia["ok"]
            else ";".join(evidencia["erros"]))
        gates["evidencia_rollback_disponivel"] = _gate(
            evidencia["ok"] and bool(evidencia.get("local_rollback")),
            evidencia.get("local_rollback") or "indisponivel")
    else:
        evidencia = None
        gates["backup_validado"] = _gate(False, "evidencia_nao_informada")
        gates["evidencia_rollback_disponivel"] = _gate(
            False, "evidencia_nao_informada")

    armazenado = (batch.params or {}).get("plano_execucao_multiaba") or {}
    gates["plano_rollback_gerado"] = _gate(
        armazenado.get("plan_hash") == plano.plan_hash
        and armazenado.get("decisoes_fingerprint")
        == plano.decisoes_fingerprint,
        "gerado_e_atual" if armazenado else "nao_gerado")

    gates["esquema_na_cabeca"] = _schema_head_gate(db)

    executando = db.execute(
        select(ImportBatch).where(
            ImportBatch.source_type == MULTI_SHEET_SOURCE_TYPE,
            ImportBatch.status == "executando",
        )
    ).scalars().first()
    gates["sem_execucao_concorrente"] = _gate(
        executando is None,
        "livre" if executando is None else f"em_execucao:{executando.id}")

    previa = _execucao_previa(db, plano.envelope_sha256)
    gates["sem_execucao_previa"] = _gate(
        previa is None,
        "inedito" if previa is None else f"ja_executado_no_lote_{previa.id}")

    try:
        admin = resolve_admin(db, admin_email)
        gates["administrador_exato"] = _gate(True, admin.email)
    except MultiSheetError as exc:
        gates["administrador_exato"] = _gate(False, exc.codigo)

    ok = all(g["ok"] for g in gates.values())
    return {
        "ok": ok,
        "batch_id": batch.id,
        "gates": gates,
        "plan_hash": plano.plan_hash,
        "resumo_plano": plano.resumo_sanitizado(),
        "frase_confirmacao": (
            confirmation_phrase_multiaba(batch.id) if ok else None),
        "evidencia": evidencia,
    }


# ------------------------------------------------------- plano de rollback


def generate_rollback_plan(
    db: Session,
    envelope_name: str,
    batch_id: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Gera e persiste (no lote de dry-run) o manifesto planejado + plano de
    rollback ANTES de qualquer escrita operacional. Obrigatório antes do
    execute (portão plano_rollback_gerado)."""
    batch = _get_multi_batch(db, batch_id)
    plano = build_final_plan(db, envelope_name, batch_id, base_dir)
    resumo = plano.resumo_sanitizado()
    registro = {
        **resumo,
        "envelope_name": envelope_name,
        "deltas_esperados": plano.manifesto(),
        "gerado_em_utc": _utcnow_iso(),
        "gerado_por": user_id,
    }
    batch.params = {**(batch.params or {}),
                    "plano_execucao_multiaba": registro}
    audit(db, "migracao.multiaba_plano_execucao", "import_batches", batch.id,
          user_id, detalhes={
              "plan_hash": plano.plan_hash,
              "operacoes_planejadas": len(plano.ops),
              "bloqueios": len(plano.bloqueios),
          })
    db.flush()
    return {"ok": True, "batch_id": batch.id, **registro}


# ---------------------------------------------------------------- execução


def _resolver_ref(db: Session, ids_por_chave: dict, alvo) -> str:
    if isinstance(alvo, tuple) and alvo and alvo[0] == "existing":
        _mark, entidade, fonte, legacy_id = alvo
        alias = db.execute(
            select(LegacyAlias).where(
                LegacyAlias.entidade == entidade,
                LegacyAlias.legacy_source == fonte,
                LegacyAlias.legacy_id == legacy_id,
            )
        ).scalar_one_or_none()
        if alias is None:
            raise MultiSheetError("alias_existente_desapareceu")
        return alias.entity_id
    if alvo not in ids_por_chave:
        raise MultiSheetError("referencia_de_plano_nao_materializada")
    return ids_por_chave[alvo]


def _instanciar(db: Session, op: OperacaoPlanejada, refs: dict,
                batch_id: str):
    """Constrói o modelo da operação (payload privado só vive aqui)."""
    campos = op.campos
    if op.entidade == "partners":
        return Partner(
            public_code=allocate_public_code(db, "partners"),
            nome=campos["nome"], tipo=campos.get("tipo") or "clinica",
            status=campos.get("status") or "prospecto",
            legacy_source=op.legacy[1] if op.legacy else None,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "partner_units":
        return PartnerUnit(
            public_code=allocate_public_code(db, "partner_units"),
            partner_id=refs["partner_id"], nome=campos["nome"],
            bairro=campos.get("bairro"), cidade=campos.get("cidade"),
        )
    if op.entidade == "partner_unit_configs":
        return PartnerUnitConfig(
            partner_unit_id=refs["partner_unit_id"],
            status=campos.get("status"), dia_semana=campos.get("dia_semana"),
            horario_inicio=campos.get("horario_inicio"),
            horario_fim=campos.get("horario_fim"),
            capacidade_estimada_por_turno=campos.get(
                "capacidade_estimada_por_turno"),
        )
    if op.entidade == "partner_contacts":
        return PartnerContact(
            public_code=allocate_public_code(db, "partner_contacts"),
            partner_id=refs["partner_id"], nome=campos["nome"],
            cargo=campos.get("cargo"), telefone=campos.get("telefone"),
            email=campos.get("email"),
        )
    if op.entidade == "partnerships":
        return Partnership(
            public_code=allocate_public_code(db, "partnerships"),
            partner_id=refs["partner_id"],
            responsavel_soprolife=campos.get("responsavel_soprolife"),
        )
    if op.entidade == "people":
        nome = campos["nome_completo"]
        observacao = ("[PLACEHOLDER M15.6C] lead sem pessoa vinculada"
                      if campos.get("placeholder") else None)
        return Person(
            public_code=allocate_public_code(db, "people"),
            nome_completo=nome, nome_normalizado=normalize_name(nome),
            observacao=observacao,
            legacy_source=op.legacy[1] if op.legacy else op.fonte,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "person_contacts":
        valor = campos["valor"]
        return PersonContact(
            person_id=refs["person_id"], tipo=campos.get("tipo", "whatsapp"),
            valor=valor, valor_normalizado=normalize_phone(valor) or valor,
            principal=True,
        )
    if op.entidade == "consents":
        return Consent(
            person_id=refs["person_id"], canal=campos["canal"],
            status=campos["status"], origem=campos.get("origem"),
        )
    if op.entidade == "leads":
        data = campos.get("data_primeiro_contato") or {}
        return Lead(
            public_code=allocate_public_code(db, "leads"),
            person_id=refs["person_id"], origem=campos.get("origem"),
            servico_interesse=campos.get("servico_interesse"),
            etapa=campos.get("etapa") or "novo",
            responsavel=campos.get("responsavel"),
            data_primeiro_contato=data.get("valor"),
            data_primeiro_contato_original=_trunc(data.get("original"), 40),
            data_primeiro_contato_precisao=_trunc(data.get("precisao"), 15),
            data_primeiro_contato_dia_assumido=bool(data.get("dia_assumido")),
            legacy_source=op.legacy[1] if op.legacy else None,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "spirometry_exams":
        data = campos.get("data_exame") or {}
        return SpirometryExam(
            public_code=allocate_public_code(db, "spirometry_exams"),
            person_id=refs["person_id"],
            partner_id=refs.get("partner_id"),
            partner_unit_id=refs.get("partner_unit_id"),
            data_exame=data.get("valor"),
            data_exame_original=_trunc(data.get("original"), 40),
            data_exame_precisao=_trunc(data.get("precisao"), 15),
            data_exame_dia_assumido=bool(data.get("dia_assumido")),
            status=campos.get("status") or "Aguardando",
            origem=campos.get("origem"), responsavel=campos.get("responsavel"),
            modalidade=campos.get("modalidade"),
            idempotency_key=op.idempotency_key,
            legacy_source=op.legacy[1] if op.legacy else op.fonte,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "consultations":
        data = campos.get("data_consulta") or {}
        return Consultation(
            public_code=allocate_public_code(db, "consultations"),
            person_id=refs["person_id"],
            data_consulta=data.get("valor"),
            data_consulta_original=_trunc(data.get("original"), 40),
            data_consulta_precisao=_trunc(data.get("precisao"), 15),
            data_consulta_dia_assumido=bool(data.get("dia_assumido")),
            modalidade=campos.get("modalidade"),
            profissional=campos.get("profissional"),
            status=campos.get("status") or "Agendada",
            origem=campos.get("origem"), responsavel=campos.get("responsavel"),
            idempotency_key=op.idempotency_key,
            legacy_source=op.legacy[1] if op.legacy else None,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "followups":
        return Followup(
            public_code=allocate_public_code(db, "followups"),
            person_id=refs["person_id"], tipo=campos.get("tipo", "manual"),
            due_date=campos.get("due_date"),
            status=campos.get("status") or "pendente",
            responsavel=campos.get("responsavel"),
            origem_entidade="importacao_multiaba",
        )
    if op.entidade == "financial_entries":
        data = campos.get("data_competencia") or {}
        return FinancialEntry(
            public_code=allocate_public_code(db, "financial_entries"),
            tipo=campos["tipo"], categoria=campos.get("categoria"),
            valor=campos["valor"],
            data_competencia=data.get("valor"),
            data_competencia_original=_trunc(data.get("original"), 40),
            data_competencia_precisao=_trunc(data.get("precisao"), 15),
            data_competencia_dia_assumido=bool(data.get("dia_assumido")),
            status=campos.get("status") or "Pendente",
            forma_pagamento=campos.get("forma_pagamento"),
            origem_preco=campos.get("origem_preco"),
            spirometry_exam_id=refs.get("spirometry_exam_id"),
            idempotency_key=op.idempotency_key,
            legacy_source=op.legacy[1] if op.legacy else None,
            legacy_id=op.legacy[2] if op.legacy else None,
            import_batch_id=batch_id,
        )
    if op.entidade == "legacy_aliases":
        return LegacyAlias(
            entidade=campos["entidade"],
            legacy_source=campos["legacy_source"],
            legacy_id=campos["legacy_id"],
            entity_id=refs["entity_id"],
            import_batch_id=batch_id,
        )
    raise MultiSheetError(f"entidade_sem_construtor:{op.entidade}")


def execute_multi_sheet(
    db: Session,
    envelope_name: str,
    batch_id: str,
    confirmation: str,
    backup_evidence: str,
    admin_email: str,
    base_dir=None,
) -> dict:
    """Execução real do lote multiaba — transação única do chamador.

    Ordem interna: preflight completo -> frase exata -> lock -> manifesto e
    contagens pré-escrita persistidos -> inserções em ordem de dependência
    com proveniência por linha -> deltas conferidos AINDA na transação.
    Qualquer divergência levanta exceção e nada é gravado.
    """
    dry_batch_pre = _get_multi_batch(db, batch_id)
    previa = _execucao_previa(db, dry_batch_pre.sha256)
    if previa is not None:
        # reexecução do MESMO lote: idempotente — zero linhas novas
        return {
            "ok": True, "status": "ja_executado",
            "batch_execucao_id": previa.id, "novas_linhas": 0,
            "mensagem": "Lote com este envelope já executado. Nada gravado.",
        }

    pf = preflight_multi_sheet_execution(
        db, envelope_name, batch_id, backup_evidence, admin_email, base_dir)
    if not pf["ok"]:
        reprovados = sorted(n for n, g in pf["gates"].items() if not g["ok"])
        raise MultiSheetError("portoes_reprovados", reprovados)
    admin = resolve_admin(db, admin_email)
    if confirmation != confirmation_phrase_multiaba(batch_id):
        raise MultiSheetError("frase_de_confirmacao_incorreta")

    plano = build_final_plan(db, envelope_name, batch_id, base_dir)
    if plano.bloqueios:
        raise MultiSheetError("plano_com_bloqueios", sorted(plano.bloqueios))

    _acquire_execute_lock(db, MULTI_SHEET_SOURCE_TYPE, plano.envelope_sha256)
    if _execucao_previa(db, plano.envelope_sha256) is not None:
        # corrida perdida para outra execução concorrente: nada gravado
        raise MultiSheetError("execucao_concorrente_detectada")

    antes = _tabelas_contadas(db)
    seguranca_antes = _security_fingerprint(db)
    dry_batch = _get_multi_batch(db, batch_id)
    stored_report = dry_batch.params["multi_sheet_report"]
    manifesto = plano.manifesto()

    exec_batch = ImportBatch(
        source_type=MULTI_SHEET_SOURCE_TYPE,
        source_name="private-versioned-envelope",
        sha256=plano.envelope_sha256,
        modo="executado",
        status="executando",
        total_rows=stored_report["totals"]["source_total"],
        created_by=admin.id,
    )
    db.add(exec_batch)
    db.flush()

    # Manifesto, deltas esperados, rollback planejado e contagens PRÉ-escrita
    # ficam registrados antes de qualquer linha operacional.
    exec_batch.params = {
        "envelope_name": envelope_name,
        "dry_run_batch_id": dry_batch.id,
        "plan_hash": plano.plan_hash,
        "decisoes_fingerprint": plano.decisoes_fingerprint,
        "mapping_version": plano.mapping_version,
        "manifesto_planejado": manifesto,
        "relacionamentos_planejados": plano.relacionamentos(),
        "estados_finais_planejados": plano.estados_linhas(),
        "deltas_esperados": manifesto,
        "rollback_planejado": plano.rollback_planejado(),
        "tabelas_antes": antes,
        "seguranca_fingerprint_antes": seguranca_antes,
        "evidencia_rollback": {
            "arquivo": backup_evidence,
            "arquivo_backup": (pf["evidencia"] or {}).get("arquivo_backup"),
            "local": (pf["evidencia"] or {}).get("local_rollback"),
        },
    }
    db.flush()

    ids_por_chave: dict[str, str] = {}
    ordem = 0
    criadas_por_entidade: dict[str, int] = {}
    for op in plano.ops:
        replay = db.execute(
            select(MigrationProvenance).where(
                MigrationProvenance.idempotency_key == op.idempotency_key)
        ).scalar_one_or_none()
        if replay is not None:
            # linha já criada por execução anterior — zero novas linhas
            ids_por_chave[op.chave] = replay.entity_id
            continue
        refs = {campo: _resolver_ref(db, ids_por_chave, alvo)
                for campo, alvo in op.refs.items()}
        instancia = _instanciar(db, op, refs, exec_batch.id)
        db.add(instancia)
        db.flush()
        ids_por_chave[op.chave] = instancia.id
        ordem += 1
        db.add(MigrationProvenance(
            batch_id=exec_batch.id, ordem=ordem, entidade=op.entidade,
            entity_id=instancia.id, source_domain=op.fonte,
            source_fingerprint=op.source_fingerprint,
            mapping_version=plano.mapping_version,
            idempotency_key=op.idempotency_key,
        ))
        criadas_por_entidade[op.entidade] = (
            criadas_por_entidade.get(op.entidade, 0) + 1)
        if (op.entidade == "consents" and op.campos.get("nao_contatar")):
            pessoa = db.get(Person, refs["person_id"])
            if pessoa is not None and pessoa.id in set(ids_por_chave.values()):
                pessoa.nao_contatar = True

    contagem_estados: dict[str, int] = {}
    for registro in plano.linhas:
        estado = registro["status_final"]
        contagem_estados[estado] = contagem_estados.get(estado, 0) + 1
        entidade_final = registro.get("entidade")
        chave_final = registro.get("chave")
        db.add(ImportRow(
            batch_id=exec_batch.id,
            row_number=registro["linha"],
            legacy_id=None,
            status=estado,
            motivo=registro.get("motivo"),
            entity_type=entidade_final,
            entity_id=ids_por_chave.get(chave_final) if chave_final else None,
            row_hash=registro["fingerprint"],
        ))
    db.flush()

    depois = _tabelas_contadas(db)
    seguranca_depois = _security_fingerprint(db)
    deltas = {t: depois[t] - antes[t] for t in antes}
    esperados = dict(manifesto)
    esperados["import_rows"] = len(plano.linhas)
    esperados["migration_provenance"] = sum(criadas_por_entidade.values())
    divergencias = {
        t: {"esperado": esperados.get(t, 0), "observado": deltas.get(t, 0)}
        for t in set(esperados) | set(deltas)
        if deltas.get(t, 0) != esperados.get(t, 0)
    }
    if divergencias or seguranca_antes != seguranca_depois:
        # fail-closed AINDA dentro da transação: nada será comitado
        raise MultiSheetError(
            "deltas_divergentes_do_manifesto",
            sorted(divergencias) + (
                ["seguranca_alterada"]
                if seguranca_antes != seguranca_depois else []),
        )

    rollback_real = _rollback_manifest_real(db, exec_batch.id)
    exec_batch.status = STATUS_EXECUTADO
    exec_batch.valid_rows = contagem_estados.get("importado", 0)
    exec_batch.rejected_rows = contagem_estados.get("duplicado", 0)
    exec_batch.ambiguous_rows = 0
    exec_batch.params = {
        **exec_batch.params,
        "tabelas_depois": depois,
        "seguranca_fingerprint_depois": seguranca_depois,
        "estados_finais_observados": dict(sorted(contagem_estados.items())),
        "rollback_manifesto_real": rollback_real,
        "executado_em_utc": _utcnow_iso(),
    }
    db.add(MigrationDecision(
        tipo="execucao_multiaba",
        decisao="executado",
        decidido_por=admin.id,
        detalhes={"batch_id": exec_batch.id, "dry_run_batch_id": dry_batch.id,
                  "plan_hash": plano.plan_hash,
                  "sha256": plano.envelope_sha256},
    ))
    audit(db, "migracao.multiaba_execucao", "import_batches", exec_batch.id,
          admin.id, detalhes={
              "plan_hash": plano.plan_hash,
              "linhas_fonte": len(plano.linhas),
              "linhas_criadas": sum(criadas_por_entidade.values()),
              "manifesto": manifesto,
          })
    db.flush()
    return {
        "ok": True,
        "status": STATUS_EXECUTADO,
        "batch_execucao_id": exec_batch.id,
        "dry_run_batch_id": dry_batch.id,
        "plan_hash": plano.plan_hash,
        "manifesto": manifesto,
        "estados_finais": dict(sorted(contagem_estados.items())),
        "tabelas_antes": antes,
        "tabelas_depois": depois,
        "evidencia_rollback": exec_batch.params["evidencia_rollback"],
        "aviso": "Lote pendente de reconciliação: rode reconciliar-multiaba.",
    }


def _rollback_manifest_real(db: Session, exec_batch_id: str) -> list[dict]:
    linhas = db.execute(
        select(MigrationProvenance)
        .where(MigrationProvenance.batch_id == exec_batch_id)
        .order_by(MigrationProvenance.ordem.desc())
    ).scalars().all()
    return [
        {"ordem": p.ordem, "entidade": p.entidade, "entity_id": p.entity_id}
        for p in linhas
    ]


# ----------------------------------------------------------- reconciliação


def _get_exec_batch(db: Session, exec_batch_id: str) -> ImportBatch:
    batch = db.get(ImportBatch, exec_batch_id)
    if (batch is None or batch.source_type != MULTI_SHEET_SOURCE_TYPE
            or batch.modo != "executado"):
        raise MultiSheetError("lote_de_execucao_inexistente")
    return batch


# Relações persistidas por entidade criada (campo FK -> obrigatória?).
_CAMPOS_RELACAO = {
    "partner_units": (("partner_id", True),),
    "partner_unit_configs": (("partner_unit_id", True),),
    "partner_contacts": (("partner_id", True),),
    "partnerships": (("partner_id", True),),
    "person_contacts": (("person_id", True),),
    "consents": (("person_id", True),),
    "leads": (("person_id", True),),
    "spirometry_exams": (("person_id", True), ("partner_id", False),
                         ("partner_unit_id", False)),
    "consultations": (("person_id", True),),
    "followups": (("person_id", True),),
    "financial_entries": (("spirometry_exam_id", False),),
    "legacy_aliases": (("entity_id", True),),
}


def reconcile_multi_sheet_execution(
    db: Session,
    exec_batch_id: str,
    base_dir=None,
    user_id: str | None = None,
) -> dict:
    """Fechamento exato pós-execução. O lote SÓ vira `concluido` se TODAS as
    verificações fecharem; qualquer discrepância deixa o lote divergente."""
    batch = _get_exec_batch(db, exec_batch_id)
    params = batch.params or {}
    if batch.status == STATUS_REVERTIDO:
        raise MultiSheetError("lote_revertido_nao_reconciliavel")

    problemas: list[str] = []
    checks: dict[str, dict] = {}

    def check(nome: str, ok: bool, detalhe) -> None:
        checks[nome] = {"ok": bool(ok), "detalhe": detalhe}
        if not ok:
            problemas.append(nome)

    # 1. fechamento da fonte: toda linha da fonte tem UM estado final
    estados = {
        status: int(n)
        for status, n in db.execute(
            select(ImportRow.status, func.count())
            .where(ImportRow.batch_id == batch.id)
            .group_by(ImportRow.status)
        ).all()
    }
    total_registrado = sum(estados.values())
    check("fechamento_da_fonte", total_registrado == batch.total_rows,
          {"linhas_fonte": batch.total_rows,
           "linhas_com_estado": total_registrado, "estados": estados})
    check("estados_iguais_ao_plano",
          estados == params.get("estados_finais_planejados"),
          {"planejado": params.get("estados_finais_planejados"),
           "observado": estados})

    # 2. inserções planejadas x reais (proveniência)
    reais = {
        entidade: int(n)
        for entidade, n in db.execute(
            select(MigrationProvenance.entidade, func.count())
            .where(MigrationProvenance.batch_id == batch.id)
            .group_by(MigrationProvenance.entidade)
        ).all()
    }
    manifesto = params.get("manifesto_planejado") or {}
    check("insercoes_planejadas_x_reais", reais == manifesto,
          {"planejado": manifesto, "observado": reais})

    # 3. entidades criadas ainda existem (nenhuma linha inesperada/perdida)
    proveniencia = db.execute(
        select(MigrationProvenance)
        .where(MigrationProvenance.batch_id == batch.id)
        .order_by(MigrationProvenance.ordem)
    ).scalars().all()
    ausentes = 0
    relacoes_reais: dict[str, int] = {}
    ids_do_lote = {p.entity_id for p in proveniencia}
    for prov in proveniencia:
        modelo = _MODELOS.get(prov.entidade)
        registro = db.get(modelo, prov.entity_id) if modelo else None
        if registro is None:
            ausentes += 1
            continue
        for campo, _obrig in _CAMPOS_RELACAO.get(prov.entidade, ()):  # noqa: B007
            if getattr(registro, campo, None):
                tipo = f"{prov.entidade}.{campo}"
                relacoes_reais[tipo] = relacoes_reais.get(tipo, 0) + 1
    check("linhas_criadas_presentes", ausentes == 0,
          {"ausentes": ausentes, "total": len(proveniencia)})
    check("relacionamentos_planejados_x_reais",
          dict(sorted(relacoes_reais.items()))
          == (params.get("relacionamentos_planejados") or {}),
          {"planejado": params.get("relacionamentos_planejados"),
           "observado": dict(sorted(relacoes_reais.items()))})

    # 4. deltas de contagem por tabela
    antes = params.get("tabelas_antes") or {}
    depois = params.get("tabelas_depois") or {}
    deltas = {t: depois.get(t, 0) - antes.get(t, 0) for t in antes}
    esperados = dict(manifesto)
    esperados["import_rows"] = batch.total_rows
    esperados["migration_provenance"] = sum(manifesto.values())
    delta_ok = all(
        deltas.get(t, 0) == esperados.get(t, 0)
        for t in set(deltas) | set(esperados)
        if t not in ("users", "roles")
    )
    check("deltas_de_tabelas", delta_ok,
          {"esperado": esperados,
           "observado": {t: n for t, n in sorted(deltas.items()) if n}})

    # 5. usuários, papéis e credenciais intactos
    check("usuarios_papeis_credenciais_intactos",
          params.get("seguranca_fingerprint_antes")
          == params.get("seguranca_fingerprint_depois")
          and deltas.get("users", 0) == 0 and deltas.get("roles", 0) == 0,
          "fingerprint_e_contagens")

    # 6. replay de idempotência: o plano re-derivado AGORA (com o banco já
    # migrado) não pode conter NENHUMA chave nova — repetir o lote cria zero
    # linhas. O plan hash não é comparado aqui: pós-execução os aliases
    # existem e o staging re-marca as linhas como já existentes (esperado).
    envelope_name = params.get("envelope_name")
    try:
        plano = build_final_plan(
            db, envelope_name, params.get("dry_run_batch_id"), base_dir)
        chaves_replay = {op.idempotency_key for op in plano.ops}
        conhecidas = {
            chave for (chave,) in db.execute(
                select(MigrationProvenance.idempotency_key).where(
                    MigrationProvenance.idempotency_key.in_(chaves_replay))
            ).all()
        } if chaves_replay else set()
        novas = sorted(chaves_replay - conhecidas)
        check("replay_idempotente", not novas,
              {"operacoes_re_derivadas": len(chaves_replay),
               "chaves_novas": len(novas)})
    except MultiSheetError as exc:
        check("replay_idempotente", False, exc.codigo)

    # 7. nenhuma proveniência aponta para fora do manifesto
    entidades_fora = sorted(set(reais) - set(manifesto))
    check("sem_linhas_inesperadas", not entidades_fora and ausentes == 0,
          {"entidades_fora_do_manifesto": entidades_fora})
    _ = ids_do_lote

    ok = not problemas
    report = {
        "ok": ok,
        "batch_execucao_id": batch.id,
        "status_final": STATUS_CONCLUIDO if ok else STATUS_DIVERGENTE,
        "checks": checks,
        "problemas": problemas,
        "reconciliado_em_utc": _utcnow_iso(),
    }
    batch.status = STATUS_CONCLUIDO if ok else STATUS_DIVERGENTE
    batch.params = {**params, "reconciliacao_execucao": report}
    audit(db, "migracao.multiaba_reconciliacao", "import_batches", batch.id,
          user_id, detalhes={"ok": ok, "problemas": problemas})
    db.flush()
    return report


# --------------------------------------------------------------- rollback


# Referências EXTERNAS que impedem rollback seletivo se apontarem para uma
# linha criada pelo lote a partir de registro que NÃO é do lote.
_REFERENCIAS_EXTERNAS = {
    "people": (
        (Lead, "person_id"), (SpirometryExam, "person_id"),
        (Consultation, "person_id"), (Followup, "person_id"),
        (Consent, "person_id"), (PersonContact, "person_id"),
        (PartnerReferral, "person_id"), (Interaction, "person_id"),
    ),
    "partners": (
        (PartnerUnit, "partner_id"), (PartnerContact, "partner_id"),
        (Partnership, "partner_id"), (SpirometryExam, "partner_id"),
        (Followup, "partner_id"), (PartnerReferral, "partner_id"),
        (PartnerSettlement, "partner_id"), (PartnerTransfer, "partner_id"),
    ),
    "partner_units": (
        (PartnerUnitConfig, "partner_unit_id"),
        (PartnerContact, "partner_unit_id"),
        (SpirometryExam, "partner_unit_id"),
        (PartnerReferral, "partner_unit_id"),
    ),
    "partner_contacts": ((PartnerReferral, "partner_contact_id"),),
    "partnerships": (
        (PartnerSettlement, "partnership_id"),
        (PartnerTransfer, "partnership_id"),
    ),
    "spirometry_exams": (
        (FinancialEntry, "spirometry_exam_id"),
        (PartnerReferral, "spirometry_exam_id"),
    ),
    "consultations": (
        (FinancialEntry, "consultation_id"),
        (PartnerReferral, "consultation_id"),
    ),
    "followups": ((Interaction, "followup_id"),),
    "financial_entries": (
        (PaymentAllocation, "financial_entry_id"),
        (PartnerTransfer, "financial_entry_id"),
        (PartnerReferral, "financial_entry_id"),
    ),
}


def rollback_plan_report(db: Session, exec_batch_id: str) -> dict:
    """Manifesto real de rollback (IDs criados pelo lote, ordem reversa)."""
    batch = _get_exec_batch(db, exec_batch_id)
    manifesto = _rollback_manifest_real(db, batch.id)
    por_entidade: dict[str, int] = {}
    for item in manifesto:
        por_entidade[item["entidade"]] = por_entidade.get(
            item["entidade"], 0) + 1
    return {
        "ok": True,
        "batch_execucao_id": batch.id,
        "status_lote": batch.status,
        "linhas_a_remover": len(manifesto),
        "por_entidade": dict(sorted(por_entidade.items())),
        "ordem_reversa": manifesto,
        "frase_confirmacao": rollback_phrase_multiaba(batch.id),
        "politica": "somente_ids_criados_pelo_lote;ordem_reversa;"
                    "nunca_toca_linha_preexistente;fail_closed",
    }


def _provar_rollback_seguro(db: Session, batch: ImportBatch) -> list:
    """Prova de segurança ANTES de qualquer delete. Lista de impedimentos."""
    proveniencia = db.execute(
        select(MigrationProvenance)
        .where(MigrationProvenance.batch_id == batch.id)
        .order_by(MigrationProvenance.ordem.desc())
    ).scalars().all()
    impedimentos: list[str] = []
    if not proveniencia:
        impedimentos.append("proveniencia_ausente")
        return impedimentos
    ids_do_lote = {p.entity_id for p in proveniencia}
    esperado = ((batch.params or {}).get("manifesto_planejado") or {})
    reais: dict[str, int] = {}
    for prov in proveniencia:
        reais[prov.entidade] = reais.get(prov.entidade, 0) + 1
        modelo = _MODELOS.get(prov.entidade)
        if modelo is None:
            impedimentos.append(f"entidade_desconhecida:{prov.entidade}")
            continue
        if db.get(modelo, prov.entity_id) is None:
            impedimentos.append("linha_criada_ausente")
            continue
        for ref_model, campo in _REFERENCIAS_EXTERNAS.get(prov.entidade, ()):
            dependentes = db.execute(
                select(ref_model.id).where(
                    getattr(ref_model, campo) == prov.entity_id)
            ).scalars().all()
            for dep in dependentes:
                if dep not in ids_do_lote:
                    impedimentos.append(
                        f"dependencia_externa:{prov.entidade}")
                    break
    if reais != esperado:
        impedimentos.append("proveniencia_incompleta_x_manifesto")
    return sorted(set(impedimentos))


def rollback_multi_sheet_execution(
    db: Session,
    exec_batch_id: str,
    confirmation: str,
    admin_email: str,
) -> dict:
    """Rollback seletivo — só executa quando provadamente seguro."""
    batch = _get_exec_batch(db, exec_batch_id)
    if batch.status == STATUS_REVERTIDO:
        raise MultiSheetError("lote_ja_revertido")
    admin = resolve_admin(db, admin_email)
    if confirmation != rollback_phrase_multiaba(batch.id):
        raise MultiSheetError("frase_de_confirmacao_incorreta")

    impedimentos = _provar_rollback_seguro(db, batch)
    if impedimentos:
        raise MultiSheetError("rollback_nao_provado_seguro", impedimentos)

    antes = _tabelas_contadas(db)
    proveniencia = db.execute(
        select(MigrationProvenance)
        .where(MigrationProvenance.batch_id == batch.id)
        .order_by(MigrationProvenance.ordem.desc())
    ).scalars().all()
    removidas: dict[str, int] = {}
    for prov in proveniencia:
        registro = db.get(_MODELOS[prov.entidade], prov.entity_id)
        if registro is None:  # pré-provado; defesa extra fail-closed
            raise MultiSheetError("linha_criada_ausente_durante_rollback")
        db.delete(registro)
        removidas[prov.entidade] = removidas.get(prov.entidade, 0) + 1
    db.flush()
    depois = _tabelas_contadas(db)

    evidencia = {
        "linhas_removidas": dict(sorted(removidas.items())),
        "total_removido": sum(removidas.values()),
        "tabelas_antes_rollback": antes,
        "tabelas_depois_rollback": depois,
        "revertido_em_utc": _utcnow_iso(),
        "revertido_por": admin.id,
    }
    batch.status = STATUS_REVERTIDO
    batch.params = {**(batch.params or {}), "rollback_execucao": evidencia}
    db.add(MigrationDecision(
        tipo="rollback_multiaba",
        decisao="revertido",
        decidido_por=admin.id,
        detalhes={"batch_id": batch.id,
                  "total_removido": evidencia["total_removido"]},
    ))
    audit(db, "migracao.multiaba_rollback", "import_batches", batch.id,
          admin.id, detalhes={
              "total_removido": evidencia["total_removido"],
              "linhas_removidas": evidencia["linhas_removidas"],
          })
    db.flush()
    return {"ok": True, "batch_execucao_id": batch.id,
            "status": STATUS_REVERTIDO, **evidencia}
