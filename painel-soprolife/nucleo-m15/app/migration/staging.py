"""Staging multiaba EM MEMÓRIA do dry-run M15.6B.

Grafo de simulação compartilhado por todas as abas de UM snapshot:

- ordem de dependência FIXA e estável (histórico -> parceiros/clinicas ->
  contatos de parceiro -> pessoas/contatos -> consentimentos -> leads ->
  espirometrias -> consultas -> follow-ups -> financeiro técnico);
- IDs explícitos da fonte são autoritativos; aliases determinísticos entre
  abas resolvem relações; telefone/e-mail geram apenas CANDIDATOS; nome
  isolado NUNCA vincula;
- nenhuma linha é descartada em silêncio: todo estado é contado e a soma
  dos estados fecha com o total da fonte (auto-verificado);
- datas preservam precisão original (ano-mês nunca vira data exata);
  data ativa inválida gera item de revisão humana; PCMSO histórico não
  entra na fila ativa;
- dinheiro SÓ nasce de Financeiro_Lancamentos por relação técnica; CRM e
  Pastore jamais produzem verdade monetária;
- NADA aqui toca banco: o resultado é um plano sanitizado e determinístico
  (mesmo snapshot + mesmo mapping => mesmo plano).
"""

import re
from dataclasses import dataclass, field

from ..dates import parse_incomplete_date
from ..domain import pcmso_violation
from ..importer.csv_import import _formula_cell
from ..normalize import (
    contains_clinical_info,
    contains_pii_like,
    normalize_name,
    normalize_phone,
)
from .adapters import (
    ADAPTERS_VERSION,
    ESTAGIO_CONSENTIMENTOS,
    ESTAGIO_PESSOAS,
    PAPEL_CONSENTIMENTO,
    PAPEL_DATA,
    PAPEL_DATA_HORA,
    PAPEL_REF_MONETARIA_BLOQUEADA,
    AdapterReal,
    LinhaAdaptada,
    adapt_row,
    check_real_headers,
    get_adapter,
)
from .rawsnapshot import RawEnvelope, RawSheet

# Estados de linha (fechamento obrigatório: a soma == total da fonte).
ST_VALIDA = "valida"
ST_REJEITADA = "rejeitada"
ST_EXCLUIDA = "excluida"
ST_PENDENTE = "pendente_revisao"
ST_DUPLICADA = "duplicada"
ST_JA_EXISTENTE = "ja_existente"
ESTADOS = (ST_VALIDA, ST_REJEITADA, ST_EXCLUIDA, ST_PENDENTE,
           ST_DUPLICADA, ST_JA_EXISTENTE)

# Categorias da fila de revisão humana (contrato sanitizado M15.6B).
CAT_CANDIDATO_IDENTIDADE = "candidato_identidade_provavel"
CAT_EXAME_ORFAO = "exame_orfao"
CAT_CONSULTA_ORFA = "consulta_orfa"
CAT_REGISTRO_ORFAO = "registro_orfao"
CAT_LEAD_SEM_PESSOA = "lead_sem_pessoa"
CAT_ID_CONFLITANTE = "identificador_conflitante"
CAT_ID_NAO_ENCONTRADO = "identificador_nao_encontrado"
CAT_DATA_INVALIDA = "data_invalida_ativa"
CAT_FINANCEIRO_NAO_RESOLVIDO = "relacao_financeira_nao_resolvida"
CAT_FONTE_MONETARIA_BLOQUEADA = "fonte_monetaria_nao_autorizada"
CAT_EXCLUSAO_EXPLICITA = "exclusao_explicita"

CATEGORIAS_REVISAO = (
    CAT_CANDIDATO_IDENTIDADE, CAT_EXAME_ORFAO, CAT_CONSULTA_ORFA,
    CAT_REGISTRO_ORFAO, CAT_LEAD_SEM_PESSOA, CAT_ID_CONFLITANTE,
    CAT_ID_NAO_ENCONTRADO, CAT_DATA_INVALIDA, CAT_FINANCEIRO_NAO_RESOLVIDO,
    CAT_FONTE_MONETARIA_BLOQUEADA, CAT_EXCLUSAO_EXPLICITA,
)

_MONEY_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
_DATETIME_RE_BR = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{4})[ T]\d{1,2}:\d{2}(?::\d{2})?$")
_DATETIME_RE_ISO = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2})[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?$")


def parse_source_datetime_or_date(raw: str):
    """Data OU datetime da fonte. Retorna (NormalizedDate, eh_datetime).

    Datetime válido tem precisão de DIA (hora preservada só na fonte
    privada); nada aqui inventa dia para valores parciais.
    """
    texto = (raw or "").strip()
    m = _DATETIME_RE_BR.match(texto) or _DATETIME_RE_ISO.match(texto)
    if m:
        nd = parse_incomplete_date(m.group(1))
        if nd.value is not None:
            return nd, True
    return parse_incomplete_date(texto), False


@dataclass(frozen=True)
class ItemRevisao:
    """Item sanitizado da fila de revisão: categoria + token privado.

    O token (aba:linha[:campo]) resolve para o snapshot privado — nenhum
    valor pessoal é copiado para cá.
    """

    token: str
    categoria: str
    aba: str
    linha: int
    campo: str | None
    motivo: str
    status: str = "pendente"

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "categoria": self.categoria,
            "aba": self.aba,
            "linha": self.linha,
            "campo": self.campo,
            "motivo": self.motivo,
            "status": self.status,
        }


def _token(aba: str, linha: int, categoria: str, campo: str | None = None) -> str:
    base = f"{aba}:{linha}:{categoria}"
    return f"{base}:{campo}" if campo else base


@dataclass
class _AbaResumo:
    kind: str
    estagio: int
    layout_version: str
    total: int = 0
    estados: dict = field(default_factory=lambda: {s: 0 for s in ESTADOS})
    motivos: dict = field(default_factory=dict)
    rejeicoes_amostra: list = field(default_factory=list)

    def registrar(self, linha: int, status: str, motivo: str | None):
        self.estados[status] += 1
        if motivo:
            self.motivos[motivo] = self.motivos.get(motivo, 0) + 1
            if status in (ST_REJEITADA, ST_DUPLICADA) and \
                    len(self.rejeicoes_amostra) < 50:
                self.rejeicoes_amostra.append({"linha": linha, "motivo": motivo})


class StagingError(ValueError):
    """Erro de contrato do staging multiaba (código sem PII)."""


class _Contexto:
    """Estado compartilhado do grafo entre todas as abas do snapshot."""

    def __init__(self, envelope: RawEnvelope, existentes: set | None):
        self.envelope = envelope
        # aliases já existentes no banco: {(entidade, fonte, legacy_id)}
        self.existentes = existentes or set()
        # aliases simulados nesta passada: (entidade, fonte, legacy_id) -> chave
        self.aliases: dict[tuple, str] = {}
        # nome normalizado guardado por chave simulada (detecção de conflito)
        self.nomes_por_chave: dict[str, str] = {}
        # candidatos: telefone/e-mail normalizado -> [chaves de pessoa]
        self.pessoas_por_telefone: dict[str, list[str]] = {}
        self.pessoas_por_email: dict[str, list[str]] = {}
        self.nomes_pessoas: dict[str, list[str]] = {}
        self.unidades_pastore: dict[str, str] = {}
        self.parceiro_pastore_criado = False
        self.insercoes: dict[str, int] = {}
        self.relacionamentos: dict[str, int] = {}
        self.consentimentos_pendentes: list[tuple] = []  # (aba, linha, chave, valor)
        self.propostas_consentimento = 0
        self.restricoes_nao_contatar = 0
        self.refs_monetarias_bloqueadas = 0
        self.avisos_parser = 0
        self.consentimentos_sem_pessoa = 0
        self.fila: list[ItemRevisao] = []
        self.perfil_datas: dict[str, dict] = {}
        self.registros_data_privados: list[dict] = []
        self.identidade = {
            "pessoas_explicitas": 0,
            "vinculos_deterministicos": 0,
            "candidatos_por_telefone": 0,
            "candidatos_por_email": 0,
            "candidatos_multiplos": 0,
            "correspondencias_somente_nome_ignoradas": 0,
            "exames_orfaos": 0,
            "consultas_orfas": 0,
            "leads_sem_pessoa": 0,
            "followups_sem_pessoa": 0,
            "identificadores_conflitantes": 0,
        }
        self.financeiro = {
            "total": 0,
            "vinculadas_tecnicamente": 0,
            "nao_resolvidas": 0,
            "bloqueadas_fonte_nao_autorizada": 0,
        }

    # ------------------------------------------------------------- aliases

    def alias(self, entidade: str, fonte: str, legacy_id: str) -> str | None:
        key = (entidade, fonte, legacy_id)
        simulated = self.aliases.get(key)
        if simulated is not None:
            return simulated
        if key in self.existentes:
            return f"existing:{entidade}:{fonte}:{legacy_id}"
        return None

    def ja_existente(self, entidade: str, fonte: str, legacy_id: str) -> bool:
        return (entidade, fonte, legacy_id) in self.existentes

    def registrar_alias(self, entidade: str, fonte: str, legacy_id: str,
                        chave: str) -> None:
        self.aliases[(entidade, fonte, legacy_id)] = chave

    def propor(self, entidade: str, n: int = 1) -> None:
        self.insercoes[entidade] = self.insercoes.get(entidade, 0) + n

    def relacionar(self, tipo: str, n: int = 1) -> None:
        self.relacionamentos[tipo] = self.relacionamentos.get(tipo, 0) + n

    def revisar(self, item: ItemRevisao) -> None:
        self.fila.append(item)

    # ------------------------------------------------------------ identidade

    def indexar_pessoa(self, chave: str, nome: str, telefone: str,
                       email: str = "") -> None:
        nome_norm = normalize_name(nome)
        fone_norm = normalize_phone(telefone)
        if nome_norm:
            self.nomes_pessoas.setdefault(nome_norm, []).append(chave)
            self.nomes_por_chave[chave] = nome_norm
        if fone_norm:
            self.pessoas_por_telefone.setdefault(fone_norm, []).append(chave)
        if email:
            self.pessoas_por_email.setdefault(email.lower(), []).append(chave)

    def candidatos_pessoa(self, telefone: str, email: str = "") -> tuple[list[str], str]:
        """Candidatos por telefone/e-mail — NUNCA por nome. Retorna
        (chaves, origem_do_candidato)."""
        fone_norm = normalize_phone(telefone)
        if fone_norm and fone_norm in self.pessoas_por_telefone:
            return list(self.pessoas_por_telefone[fone_norm]), "telefone"
        if email and email.lower() in self.pessoas_por_email:
            return list(self.pessoas_por_email[email.lower()]), "email"
        return [], ""


# ------------------------------------------------------------------ por linha


def _linha_vazia(valores: tuple) -> bool:
    return not any((v or "").strip() for v in valores)


def _coluna_injecao(headers: tuple, valores: tuple) -> str | None:
    for header, valor in zip(headers, valores):
        if valor and _formula_cell(str(valor).strip()):
            return header
    return None


def _eh_pcmso(campos: dict) -> bool:
    joined = " | ".join(str(v) for v in campos.values() if v)
    return pcmso_violation({"observacao": joined}) is not None


def _perfil_data(ctx: _Contexto, aba: str, adapter: AdapterReal,
                 row: LinhaAdaptada, linha: int, status_base: str) -> bool:
    """Perfila TODAS as colunas de data da linha; retorna True se alguma data
    ATIVA inválida exigir revisão humana. Texto original nunca sai daqui —
    fica recuperável pela referência privada (aba/linha/campo)."""
    requer_revisao = False
    perfil_aba = ctx.perfil_datas.setdefault(aba, {})
    for campo in adapter.campos:
        if campo.papel not in (PAPEL_DATA, PAPEL_DATA_HORA):
            continue
        canonico = campo.canonico
        contagem = perfil_aba.setdefault(canonico, {
            "dia": 0, "mes": 0, "ano": 0, "datetime": 0, "vazia": 0,
            "invalida": 0, "dia_assumido": 0,
        })
        bruto = row.campos.get(canonico, "")
        if not bruto:
            contagem["vazia"] += 1
            continue
        nd, eh_datetime = parse_source_datetime_or_date(bruto)
        ctx.registros_data_privados.append({
            "private_source_reference": _token(
                aba, linha, "date_precision", canonico
            ),
            "original_text": bruto,
            "normalized_date": nd.value.isoformat() if nd.value else None,
            "precision": ("dia" if eh_datetime else nd.precision),
            "assumed_day": nd.day_assumed,
            "locale": ctx.envelope.locale,
            "parser_warning": nd.value is None,
        })
        if nd.value is None:
            contagem["invalida"] += 1
            ctx.avisos_parser += 1
            if campo.data_ativa and status_base not in (
                    ST_REJEITADA, ST_EXCLUIDA, ST_DUPLICADA):
                # data ativa inválida => decisão humana (nunca adivinhada)
                ctx.revisar(ItemRevisao(
                    token=_token(aba, linha, CAT_DATA_INVALIDA, canonico),
                    categoria=CAT_DATA_INVALIDA, aba=aba, linha=linha,
                    campo=canonico, motivo="data_ativa_invalida",
                ))
                requer_revisao = True
        elif eh_datetime:
            contagem["datetime"] += 1
        else:
            contagem[nd.precision] += 1
            if nd.day_assumed:
                # parcial nunca vira exata: o metadado acompanha a proposta
                contagem["dia_assumido"] += 1
    return requer_revisao


def _contar_refs_monetarias(ctx: _Contexto, row: LinhaAdaptada) -> None:
    for canonico, papel in row.papeis.items():
        if papel == PAPEL_REF_MONETARIA_BLOQUEADA and row.campos.get(canonico):
            ctx.refs_monetarias_bloqueadas += 1


def _consentimento(ctx: _Contexto, aba: str, linha: int, row: LinhaAdaptada,
                   chave_pessoa: str | None) -> None:
    """Registra consentimento/não-contatar quando a linha tem pessoa
    deterministicamente resolvida; senão apenas conta (sem perder linha)."""
    for canonico, papel in row.papeis.items():
        if papel != PAPEL_CONSENTIMENTO:
            continue
        valor = row.campos.get(canonico, "")
        if not valor:
            continue
        if chave_pessoa:
            ctx.consentimentos_pendentes.append((aba, linha, chave_pessoa, valor))
        else:
            ctx.consentimentos_sem_pessoa += 1


def _normalizar_consentimento(valor: str) -> tuple[str, bool]:
    """(status, nao_contatar). Vocabulário fechado; resto = desconhecido."""
    texto = normalize_name(valor)
    nao_contatar = "nao contatar" in texto or "nao_contatar" in texto
    if texto in ("sim", "true", "concedido", "autorizado", "aceito"):
        return "concedido", False
    if nao_contatar or texto in ("nao", "false", "revogado", "recusado"):
        return "revogado", nao_contatar
    return "desconhecido", False


def _processar_consentimentos(ctx: _Contexto) -> None:
    """Estágio 5 real: roda uma única vez, antes de leads e dependentes."""
    for _aba, _linha, _chave, valor in sorted(ctx.consentimentos_pendentes):
        status_consent, nao_contatar = _normalizar_consentimento(valor)
        ctx.propor("consents")
        ctx.relacionar("consent->person")
        ctx.propostas_consentimento += 1
        if nao_contatar or status_consent == "revogado":
            ctx.restricoes_nao_contatar += 1


def _tratar_id_explicito(
    ctx: _Contexto, aba: str, linha: int, entidade: str, fonte: str,
    legacy_id: str, nome_norm: str, resumo: _AbaResumo,
) -> tuple[str | None, str | None]:
    """Contrato comum de identificador explícito autoritativo.

    Retorna (status_final, motivo) quando a linha NÃO segue adiante
    (duplicada/conflito/já existente); (None, None) quando pode continuar.
    """
    chave_previa = ctx.aliases.get((entidade, fonte, legacy_id))
    if chave_previa is not None:
        nome_previo = ctx.nomes_por_chave.get(chave_previa, "")
        if nome_norm and nome_previo and nome_norm != nome_previo:
            ctx.identidade["identificadores_conflitantes"] += 1
            ctx.revisar(ItemRevisao(
                token=_token(aba, linha, CAT_ID_CONFLITANTE),
                categoria=CAT_ID_CONFLITANTE, aba=aba, linha=linha,
                campo=None, motivo="mesmo_identificador_conteudo_divergente",
            ))
            return ST_PENDENTE, "identificador_conflitante"
        return ST_DUPLICADA, "legacy_id_repetido_no_snapshot"
    if ctx.ja_existente(entidade, fonte, legacy_id):
        return ST_JA_EXISTENTE, "registro_ja_importado"
    return None, None


# --------------------------------------------------------- handlers por aba


def _h_pcmso(ctx, aba, linha, row, resumo):
    ctx.revisar(ItemRevisao(
        token=_token(aba, linha, CAT_EXCLUSAO_EXPLICITA),
        categoria=CAT_EXCLUSAO_EXPLICITA, aba=aba, linha=linha, campo=None,
        motivo="pcmso_historico_excluido", status="registrada",
    ))
    return ST_EXCLUIDA, "pcmso_historico_excluido"


def _h_crm_clinicas(ctx, aba, linha, row, resumo):
    clinica_id = row.campos.get("clinica_id", "")
    nome = row.campos.get("nome_clinica", "")
    if not nome:
        return ST_REJEITADA, "clinica_ausente"
    if not clinica_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="clinica_id", motivo="clinica_sem_identificador",
        ))
        return ST_PENDENTE, "clinica_sem_identificador"
    nome_norm = normalize_name(nome)
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "partners", "crm_clinicas", clinica_id, nome_norm, resumo)
    if status:
        return status, motivo
    chave = f"parceiro:{clinica_id}"
    ctx.registrar_alias("partners", "crm_clinicas", clinica_id, chave)
    ctx.nomes_por_chave[chave] = nome_norm
    ctx.propor("partners")
    ctx.propor("partner_units")
    ctx.propor("partnerships")
    ctx.relacionar("partner_unit->partner")
    ctx.relacionar("partnership->partner")
    return ST_VALIDA, None


def _h_pastore_config(ctx, aba, linha, row, resumo):
    unidade = row.campos.get("unidade", "")
    if not unidade:
        return ST_REJEITADA, "unidade_ausente"
    unidade_norm = normalize_name(unidade)
    if unidade_norm in ctx.unidades_pastore:
        return ST_DUPLICADA, "unidade_repetida_no_snapshot"
    if not ctx.parceiro_pastore_criado:
        # parceiro ativo ÚNICO e determinístico; nada monetário nasce daqui
        ctx.registrar_alias("partners", "pastore", "ativo", "parceiro:pastore")
        ctx.propor("partners")
        ctx.parceiro_pastore_criado = True
    chave = f"unidade:pastore:{unidade_norm}"
    ctx.unidades_pastore[unidade_norm] = chave
    ctx.registrar_alias("partner_units", "pastore_config", unidade_norm, chave)
    ctx.propor("partner_units")
    ctx.propor("partner_unit_configs")
    ctx.relacionar("partner_unit->partner")
    ctx.relacionar("partner_unit_config->partner_unit")
    return ST_VALIDA, None


def _h_contatos_b2b(ctx, aba, linha, row, resumo):
    contato_id = row.campos.get("contato_id", "")
    clinica_id = row.campos.get("clinica_id", "")
    nome_contato = row.campos.get("nome_contato", "")
    if not nome_contato:
        return ST_REJEITADA, "contato_sem_nome"
    if contato_id:
        status, motivo = _tratar_id_explicito(
            ctx, aba, linha, "partner_contacts", "contatos_b2b", contato_id,
            normalize_name(nome_contato), resumo)
        if status:
            return status, motivo
    if not clinica_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="clinica_id", motivo="contato_sem_clinica",
        ))
        return ST_PENDENTE, "contato_sem_clinica"
    parceiro = ctx.alias("partners", "crm_clinicas", clinica_id)
    if parceiro is None:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="clinica_id", motivo="clinica_nao_encontrada",
        ))
        return ST_PENDENTE, "clinica_nao_encontrada"
    if contato_id:
        chave = f"contato_parceiro:{contato_id}"
        ctx.registrar_alias("partner_contacts", "contatos_b2b", contato_id, chave)
        ctx.nomes_por_chave[chave] = normalize_name(nome_contato)
    ctx.propor("partner_contacts")
    ctx.relacionar("partner_contact->partner")
    return ST_VALIDA, None


def _h_crm_pacientes(ctx, aba, linha, row, resumo):
    paciente_id = row.campos.get("paciente_id", "")
    nome = row.campos.get("nome_pessoa", "")
    if not nome:
        return ST_REJEITADA, "nome_ausente"
    if not paciente_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="paciente_id", motivo="pessoa_sem_identificador",
        ))
        return ST_PENDENTE, "pessoa_sem_identificador"
    nome_norm = normalize_name(nome)
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "people", "crm_pacientes", paciente_id, nome_norm, resumo)
    if status:
        return status, motivo
    chave = f"pessoa:{paciente_id}"
    ctx.registrar_alias("people", "crm_pacientes", paciente_id, chave)
    ctx.indexar_pessoa(chave, nome, row.campos.get("telefone", ""))
    ctx.identidade["pessoas_explicitas"] += 1
    ctx.propor("people")
    ctx.propor("legacy_aliases")
    if row.campos.get("telefone"):
        ctx.propor("person_contacts")
        ctx.relacionar("person_contact->person")
    _consentimento(ctx, aba, linha, row, chave)
    return ST_VALIDA, None


def _candidato_ou_orfao(ctx, aba, linha, row, categoria_orfa, contador_orfao):
    """Resolução NÃO determinística de pessoa: telefone/e-mail = candidato;
    nome isolado NUNCA vincula; sem candidato = órfão pendente."""
    telefone = row.campos.get("telefone", "") or row.campos.get(
        "telefone_whatsapp", "") or row.campos.get("paciente_whatsapp", "")
    email = row.campos.get("email", "")
    nome = row.campos.get("nome_pessoa", "")
    candidatos, origem = ctx.candidatos_pessoa(telefone, email)
    if candidatos:
        if len(candidatos) == 1:
            ctx.identidade[f"candidatos_por_{origem}"] += 1
            motivo = f"{origem}_candidato_unico_sem_auto_vinculo"
        else:
            ctx.identidade["candidatos_multiplos"] += 1
            motivo = f"{origem}_candidatos_multiplos"
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_CANDIDATO_IDENTIDADE),
            categoria=CAT_CANDIDATO_IDENTIDADE, aba=aba, linha=linha,
            campo=None, motivo=motivo,
        ))
        return ST_PENDENTE, motivo
    if nome and normalize_name(nome) in ctx.nomes_pessoas:
        # correspondência somente por nome: contada e IGNORADA como vínculo
        ctx.identidade["correspondencias_somente_nome_ignoradas"] += 1
    ctx.identidade[contador_orfao] += 1
    ctx.revisar(ItemRevisao(
        token=_token(aba, linha, categoria_orfa),
        categoria=categoria_orfa, aba=aba, linha=linha, campo=None,
        motivo="sem_pessoa_deterministica",
    ))
    return ST_PENDENTE, "sem_pessoa_deterministica"


def _h_leads(ctx, aba, linha, row, resumo):
    lead_id = row.campos.get("lead_id", "")
    nome = row.campos.get("nome_pessoa", "")
    if not lead_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="lead_id", motivo="lead_sem_identificador",
        ))
        return ST_PENDENTE, "lead_sem_identificador"
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "leads", "leads", lead_id, normalize_name(nome), resumo)
    if status:
        return status, motivo
    chave = f"lead:{lead_id}"
    ctx.registrar_alias("leads", "leads", lead_id, chave)
    ctx.nomes_por_chave[chave] = normalize_name(nome)

    entidade_id = row.campos.get("entidade_id", "")
    if entidade_id:
        pessoa = ctx.alias("people", "crm_pacientes", entidade_id)
        if pessoa:
            ctx.identidade["vinculos_deterministicos"] += 1
            ctx.relacionar("lead->person")
        else:
            ctx.revisar(ItemRevisao(
                token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
                categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
                campo="entidade_id", motivo="entidade_destino_nao_encontrada",
            ))
            return ST_PENDENTE, "entidade_destino_nao_encontrada"
        ctx.propor("leads")
        ctx.propor("legacy_aliases")
        return ST_VALIDA, None

    telefone = row.campos.get("telefone_whatsapp", "")
    candidatos, origem = ctx.candidatos_pessoa(telefone)
    if candidatos:
        if len(candidatos) == 1:
            ctx.identidade[f"candidatos_por_{origem}"] += 1
            motivo = f"{origem}_candidato_unico_sem_auto_vinculo"
        else:
            ctx.identidade["candidatos_multiplos"] += 1
            motivo = f"{origem}_candidatos_multiplos"
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_CANDIDATO_IDENTIDADE),
            categoria=CAT_CANDIDATO_IDENTIDADE, aba=aba, linha=linha,
            campo=None, motivo=motivo,
        ))
        return ST_PENDENTE, motivo
    if nome and normalize_name(nome) in ctx.nomes_pessoas:
        ctx.identidade["correspondencias_somente_nome_ignoradas"] += 1
    # política explícita: lead PODE ficar sem pessoa (contado e revisável)
    ctx.identidade["leads_sem_pessoa"] += 1
    ctx.revisar(ItemRevisao(
        token=_token(aba, linha, CAT_LEAD_SEM_PESSOA),
        categoria=CAT_LEAD_SEM_PESSOA, aba=aba, linha=linha, campo=None,
        motivo="vinculo_opcional_nao_estabelecido", status="registrada",
    ))
    ctx.propor("leads")
    ctx.propor("legacy_aliases")
    return ST_VALIDA, None


def _h_crm_espirometria(ctx, aba, linha, row, resumo):
    exame_id = row.campos.get("exame_id", "")
    if not exame_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="exame_id", motivo="exame_sem_identificador",
        ))
        return ST_PENDENTE, "exame_sem_identificador"
    nome_norm = normalize_name(row.campos.get("nome_pessoa", ""))
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "spirometry_exams", "crm_espirometria", exame_id,
        nome_norm, resumo)
    if status:
        return status, motivo
    chave = f"exame:{exame_id}"
    # alias registrado MESMO pendente: o financeiro resolve por referência
    # técnica, independentemente da revisão de identidade do exame
    ctx.registrar_alias("spirometry_exams", "crm_espirometria", exame_id, chave)
    ctx.nomes_por_chave[chave] = nome_norm
    status, motivo = _candidato_ou_orfao(
        ctx, aba, linha, row, CAT_EXAME_ORFAO, "exames_orfaos")
    _consentimento(ctx, aba, linha, row, None)
    return status, motivo


def _h_pastore_atendimentos(ctx, aba, linha, row, resumo):
    unidade = row.campos.get("unidade", "")
    if not row.campos.get("data_atendimento"):
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_DATA_INVALIDA, "data_atendimento"),
            categoria=CAT_DATA_INVALIDA, aba=aba, linha=linha,
            campo="data_atendimento", motivo="data_obrigatoria_ausente",
        ))
        return ST_PENDENTE, "data_obrigatoria_ausente"
    if not unidade or normalize_name(unidade) not in ctx.unidades_pastore:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="unidade", motivo="unidade_pastore_nao_configurada",
        ))
        return ST_PENDENTE, "unidade_pastore_nao_configurada"
    status, motivo = _candidato_ou_orfao(
        ctx, aba, linha, row, CAT_EXAME_ORFAO, "exames_orfaos")
    _consentimento(ctx, aba, linha, row, None)
    return status, motivo


def _h_crm_consultas(ctx, aba, linha, row, resumo):
    consulta_id = row.campos.get("consulta_id", "")
    if not consulta_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="consulta_id", motivo="consulta_sem_identificador",
        ))
        return ST_PENDENTE, "consulta_sem_identificador"
    nome_norm = normalize_name(row.campos.get("nome_pessoa", ""))
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "consultations", "crm_consultas", consulta_id,
        nome_norm, resumo)
    if status:
        return status, motivo
    chave = f"consulta:{consulta_id}"
    ctx.registrar_alias("consultations", "crm_consultas", consulta_id, chave)
    ctx.nomes_por_chave[chave] = nome_norm

    referencia = row.campos.get("consulta_referencia_id", "")
    if referencia and ctx.alias("consultations", "crm_consultas", referencia) is None:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="consulta_referencia_id",
            motivo="consulta_referencia_nao_encontrada",
        ))
        return ST_PENDENTE, "consulta_referencia_nao_encontrada"

    paciente_id = row.campos.get("paciente_id", "")
    if paciente_id:
        pessoa = ctx.alias("people", "crm_pacientes", paciente_id)
        if pessoa is None:
            ctx.revisar(ItemRevisao(
                token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
                categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
                campo="paciente_id", motivo="paciente_id_nao_encontrado",
            ))
            return ST_PENDENTE, "paciente_id_nao_encontrado"
        # alias explícito entre abas: vínculo determinístico autoritativo
        ctx.identidade["vinculos_deterministicos"] += 1
        ctx.propor("consultations")
        ctx.propor("legacy_aliases")
        ctx.relacionar("consultation->person")
        if referencia:
            ctx.relacionar("consultation->consultation_referencia")
        _consentimento(ctx, aba, linha, row, pessoa)
        return ST_VALIDA, None
    status, motivo = _candidato_ou_orfao(
        ctx, aba, linha, row, CAT_CONSULTA_ORFA, "consultas_orfas")
    _consentimento(ctx, aba, linha, row, None)
    return status, motivo


def _h_followup(ctx, aba, linha, row, resumo):
    followup_id = row.campos.get("followup_id", "")
    if not followup_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="followup_id", motivo="followup_sem_identificador",
        ))
        return ST_PENDENTE, "followup_sem_identificador"
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "followups", "followup_whatsapp", followup_id,
        normalize_name(row.campos.get("nome_pessoa", "")), resumo)
    if status:
        return status, motivo
    ctx.registrar_alias("followups", "followup_whatsapp", followup_id,
                        f"followup:{followup_id}")
    status, motivo = _candidato_ou_orfao(
        ctx, aba, linha, row, CAT_REGISTRO_ORFAO, "followups_sem_pessoa")
    _consentimento(ctx, aba, linha, row, None)
    return status, motivo


def _h_financeiro(ctx, aba, linha, row, resumo):
    lancamento_id = row.campos.get("id_lancamento", "")
    if not lancamento_id:
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_ID_NAO_ENCONTRADO),
            categoria=CAT_ID_NAO_ENCONTRADO, aba=aba, linha=linha,
            campo="id_lancamento", motivo="lancamento_sem_identificador",
        ))
        return ST_PENDENTE, "lancamento_sem_identificador"
    status, motivo = _tratar_id_explicito(
        ctx, aba, linha, "financial_entries", "financeiro_lancamentos",
        lancamento_id, "", resumo)
    if status:
        return status, motivo
    ctx.registrar_alias("financial_entries", "financeiro_lancamentos",
                        lancamento_id, f"lancamento:{lancamento_id}")

    if not row.campos.get("tipo_movimento"):
        return ST_REJEITADA, "tipo_movimento_ausente"
    for campo in ("valor_tabela", "valor_cobrado", "valor_recebido", "desconto"):
        bruto = row.campos.get(campo, "").replace(" ", "")
        if bruto and not _MONEY_RE.match(bruto):
            return ST_REJEITADA, "valor_invalido"
    observacao = row.campos.get("observacao_financeira", "")
    if observacao and (contains_pii_like(observacao)
                       or contains_clinical_info(observacao)):
        # fail-closed: descrição financeira jamais carrega PII/clínica
        return ST_REJEITADA, "pii_em_observacao_financeira"

    # ÚNICO caminho de vínculo: referência técnica id_atendimento.
    # Nome, telefone ou descrição NUNCA participam da resolução.
    id_atendimento = row.campos.get("id_atendimento", "")
    exame = (ctx.alias("spirometry_exams", "crm_espirometria", id_atendimento)
             if id_atendimento else None)
    if exame is None:
        ctx.financeiro["nao_resolvidas"] += 1
        ctx.revisar(ItemRevisao(
            token=_token(aba, linha, CAT_FINANCEIRO_NAO_RESOLVIDO),
            categoria=CAT_FINANCEIRO_NAO_RESOLVIDO, aba=aba, linha=linha,
            campo="id_atendimento",
            motivo=("sem_referencia_tecnica" if not id_atendimento
                    else "referencia_tecnica_nao_encontrada"),
        ))
        return ST_PENDENTE, "relacao_financeira_nao_resolvida"
    ctx.financeiro["vinculadas_tecnicamente"] += 1
    ctx.propor("financial_entries")
    ctx.relacionar("financial_entry->spirometry_exam")
    return ST_VALIDA, None


def _h_pastore_custos(ctx, aba, linha, row, resumo):
    # Custos Pastore NÃO são fonte monetária (M14.2): nenhuma proposta
    # financeira; linha preservada e bloqueada para decisão humana.
    ctx.financeiro["bloqueadas_fonte_nao_autorizada"] += 1
    ctx.revisar(ItemRevisao(
        token=_token(aba, linha, CAT_FONTE_MONETARIA_BLOQUEADA),
        categoria=CAT_FONTE_MONETARIA_BLOQUEADA, aba=aba, linha=linha,
        campo=None, motivo="custo_pastore_fora_da_fonte_monetaria_unica",
    ))
    return ST_PENDENTE, "custo_pastore_fora_da_fonte_monetaria_unica"


_HANDLERS = {
    "pcmso_historico": _h_pcmso,
    "crm_clinicas": _h_crm_clinicas,
    "pastore_config": _h_pastore_config,
    "contatos_b2b": _h_contatos_b2b,
    "crm_pacientes": _h_crm_pacientes,
    "leads": _h_leads,
    "crm_espirometria": _h_crm_espirometria,
    "pastore_atendimentos": _h_pastore_atendimentos,
    "crm_consultas": _h_crm_consultas,
    "followup_whatsapp": _h_followup,
    "financeiro_lancamentos": _h_financeiro,
    "pastore_custos": _h_pastore_custos,
}


# ------------------------------------------------------------------- staging


def ordem_de_processamento(envelope: RawEnvelope) -> list[RawSheet]:
    """Ordem determinística e estável: estágio de dependência, depois kind,
    depois alias. Clinica SEMPRE antes de contato B2B; pessoa antes de
    exame/consulta; financeiro por último."""
    def chave(sheet: RawSheet):
        adapter = get_adapter(sheet.sheet_kind)
        estagio = adapter.estagio if adapter else 99
        return (estagio, sheet.sheet_kind, sheet.sheet_alias)

    return sorted(envelope.sheets, key=chave)


def stage_multi_sheet(
    envelope: RawEnvelope,
    existentes: set | None = None,
) -> dict:
    """Executa o staging multiaba completo em memória e devolve o plano
    SANITIZADO e determinístico. Nunca toca banco, disco ou rede."""
    if envelope.mapping_version != ADAPTERS_VERSION:
        raise StagingError(
            "mapping_version_nao_suportada:"
            f"{envelope.mapping_version}:esperada:{ADAPTERS_VERSION}"
        )
    ctx = _Contexto(envelope, existentes)
    abas: dict[str, _AbaResumo] = {}
    ordem = ordem_de_processamento(envelope)

    erros_cabecalho: dict[str, list[str]] = {}
    for sheet in ordem:
        adapter = get_adapter(sheet.sheet_kind)
        if adapter is None:
            raise StagingError(f"sheet_kind_desconhecido:{sheet.sheet_kind}")
        erros = check_real_headers(adapter, list(sheet.headers))
        if erros:
            erros_cabecalho[sheet.sheet_alias] = sorted(erros)
    if erros_cabecalho:
        # fail-closed: layout divergente em QUALQUER aba aborta o snapshot
        raise StagingError(
            "cabecalhos_incompativeis:" + ";".join(
                f"{aba}({','.join(erros)})"
                for aba, erros in sorted(erros_cabecalho.items())
            )
        )

    consentimentos_processados = False
    for sheet in ordem:
        adapter = get_adapter(sheet.sheet_kind)
        if adapter.estagio > ESTAGIO_PESSOAS and not consentimentos_processados:
            _processar_consentimentos(ctx)
            consentimentos_processados = True
        resumo = _AbaResumo(
            kind=sheet.sheet_kind, estagio=adapter.estagio,
            layout_version=adapter.layout_version, total=len(sheet.rows),
        )
        abas[sheet.sheet_alias] = resumo
        handler = _HANDLERS[sheet.sheet_kind]
        for raw_row in sheet.rows:
            linha = raw_row.linha
            if _linha_vazia(raw_row.valores):
                resumo.registrar(linha, ST_EXCLUIDA, "linha_vazia")
                continue
            coluna = _coluna_injecao(sheet.headers, raw_row.valores)
            if coluna is not None:
                # fórmula nunca é executada nem reproduzida: rejeição
                resumo.registrar(
                    linha, ST_REJEITADA,
                    f"injecao_formula_coluna_{normalize_name(coluna).replace(' ', '_')}",
                )
                continue
            row = adapt_row(adapter, sheet.headers, raw_row.valores)
            if adapter.sheet_kind != "pcmso_historico" and _eh_pcmso(row.campos):
                resumo.registrar(linha, ST_EXCLUIDA, "pcmso_fora_da_operacao")
                continue
            _contar_refs_monetarias(ctx, row)
            if adapter.sheet_kind == "financeiro_lancamentos":
                ctx.financeiro["total"] += 1
            data_pendente = _perfil_data(
                ctx, sheet.sheet_alias, adapter, row, linha,
                ST_EXCLUIDA if adapter.sheet_kind == "pcmso_historico"
                else ST_VALIDA,
            )
            if data_pendente:
                status, motivo = ST_PENDENTE, "data_ativa_invalida"
            else:
                status, motivo = handler(
                    ctx, sheet.sheet_alias, linha, row, resumo
                )
            resumo.registrar(linha, status, motivo)

    if not consentimentos_processados:
        _processar_consentimentos(ctx)

    # fechamento de contagens: NENHUMA linha some em silêncio
    totais = {s: 0 for s in ESTADOS}
    total_geral = 0
    for alias, resumo in abas.items():
        soma = sum(resumo.estados.values())
        if soma != resumo.total:
            raise StagingError(f"fechamento_de_contagens_violado:{alias}")
        total_geral += resumo.total
        for estado, n in resumo.estados.items():
            totais[estado] += n

    fila_ordenada = sorted(
        ctx.fila, key=lambda i: (i.aba, i.linha, i.categoria, i.campo or ""))
    resumo_fila: dict[str, int] = {}
    pendentes_revisao_itens = 0
    for item in fila_ordenada:
        resumo_fila[item.categoria] = resumo_fila.get(item.categoria, 0) + 1
        if item.status == "pendente":
            pendentes_revisao_itens += 1

    bloqueios = ["execucao_real_indisponivel_para_multiaba_m15_6b"]
    if pendentes_revisao_itens:
        bloqueios.append(f"itens_de_revisao_pendentes:{pendentes_revisao_itens}")
    if ctx.financeiro["nao_resolvidas"]:
        bloqueios.append(
            f"relacoes_financeiras_nao_resolvidas:{ctx.financeiro['nao_resolvidas']}")
    if ctx.financeiro["bloqueadas_fonte_nao_autorizada"]:
        bloqueios.append(
            "fonte_monetaria_nao_autorizada:"
            f"{ctx.financeiro['bloqueadas_fonte_nao_autorizada']}")
    if ctx.identidade["identificadores_conflitantes"]:
        bloqueios.append(
            f"identificadores_conflitantes:{ctx.identidade['identificadores_conflitantes']}")
    if totais[ST_REJEITADA] or totais[ST_DUPLICADA]:
        bloqueios.append(
            f"linhas_rejeitadas_ou_duplicadas:{totais[ST_REJEITADA] + totais[ST_DUPLICADA]}")

    perfil_datas_agregado = {
        "dia": 0, "mes": 0, "ano": 0, "datetime": 0, "vazia": 0,
        "invalida": 0, "dia_assumido": 0,
    }
    for colunas in ctx.perfil_datas.values():
        for contagem in colunas.values():
            for precisao in perfil_datas_agregado:
                perfil_datas_agregado[precisao] += contagem.get(precisao, 0)

    contabilizadas = sum(totais.values())
    reconciliacao_preview = {
        "fonte_total": total_geral,
        "linhas_contabilizadas": contabilizadas,
        "fechamento_ok": contabilizadas == total_geral,
        "diferenca": total_geral - contabilizadas,
        "insercoes_propostas": sum(ctx.insercoes.values()),
        "relacionamentos_propostos": sum(ctx.relacionamentos.values()),
        "insercoes_efetivas": 0,
        "relacionamentos_efetivos": 0,
        "registros_operacionais_gravados": 0,
    }

    plano = {
        "workbook_alias": envelope.workbook_alias,
        "schema_version": envelope.schema_version,
        "mapping_version": envelope.mapping_version,
        "adapters_version": ADAPTERS_VERSION,
        "locale": envelope.locale,
        "timezone": envelope.timezone,
        "ordem_processamento": [
            {"aba": s.sheet_alias, "kind": s.sheet_kind,
             "estagio": get_adapter(s.sheet_kind).estagio}
            for s in ordem
        ],
        "estagio_consentimentos": ESTAGIO_CONSENTIMENTOS,
        "abas": {
            alias: {
                "kind": r.kind,
                "estagio": r.estagio,
                "layout_version": r.layout_version,
                "total": r.total,
                **r.estados,
                "motivos": dict(sorted(r.motivos.items())),
                "rejeicoes_amostra": r.rejeicoes_amostra,
                "fechamento_ok": True,
            }
            for alias, r in sorted(abas.items())
        },
        "totais": {"fonte_total": total_geral, **totais},
        "propostas": {
            "insercoes": dict(sorted(ctx.insercoes.items())),
            "relacionamentos": dict(sorted(ctx.relacionamentos.items())),
            "consentimentos": ctx.propostas_consentimento,
            "restricoes_nao_contatar": ctx.restricoes_nao_contatar,
            "consentimentos_sem_pessoa_resolvida": ctx.consentimentos_sem_pessoa,
            "referencias_monetarias_bloqueadas": ctx.refs_monetarias_bloqueadas,
        },
        "perfil_datas": {
            "abas": {a: dict(sorted(c.items()))
                     for a, c in sorted(ctx.perfil_datas.items())},
            "avisos_parser": ctx.avisos_parser,
            "locale_assumido": envelope.locale,
            "precisao_agregada": perfil_datas_agregado,
        },
        # Contrato PRIVADO em memória: preserva texto, normalização, precisão
        # e referência de origem. A camada pública remove este bloco inteiro.
        "registros_data_privados": ctx.registros_data_privados,
        "perfil_identidade": dict(sorted(ctx.identidade.items())),
        "cobertura_financeira": dict(sorted(ctx.financeiro.items())),
        "fila_revisao": [item.as_dict() for item in fila_ordenada],
        "fila_revisao_resumo": dict(sorted(resumo_fila.items())),
        "itens_revisao_pendentes": pendentes_revisao_itens,
        "bloqueios": bloqueios,
        "reconciliacao_preview": reconciliacao_preview,
        "politicas": {
            "identidade": "id_explicito_autoritativo;telefone_email_apenas_candidato;"
                          "nome_isolado_nunca_vincula",
            "lead_sem_pessoa": "permitido_explicito_registrado",
            "fonte_monetaria_unica": "financeiro_lancamentos",
            "pastore": "config_e_crm_nunca_criam_verdade_monetaria",
            "pcmso": "historico_excluido_da_operacao_ativa",
            "datas_parciais": "precisao_preservada_dia_assumido_marcado",
        },
    }
    return plano
