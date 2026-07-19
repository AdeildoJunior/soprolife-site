"""Registro de mapeamentos fonte -> módulo nativo, com versão revisável.

Regras centrais (contrato M15.6A):
- cada domínio de origem tem cabeçalhos obrigatórios e conhecidos;
- cabeçalho desconhecido só passa se estiver em colunas_extras_aprovadas
  do manifesto (mapeamento explícito) — mudança inesperada falha fechada;
- Financeiro_Lancamentos é a ÚNICA fonte monetária: coluna monetária em
  qualquer outro domínio é erro duro, mesmo aprovada como extra;
- PCMSO é categoria histórica EXCLUÍDA — nunca vira módulo operacional;
- nenhum alias aqui referencia ID de planilha, credencial ou hostname.
"""

import re
from dataclasses import dataclass

MAPPING_VERSION = "m15-6a.1"

# Execução: "nativa" = handler real em app.importer.csv_import;
# "preparada" = mapeamento/validação prontos, execução ainda bloqueada;
# "excluida_historica" = fonte arquivada (PCMSO) — jamais executável.
EXECUCAO_NATIVA = "nativa"
EXECUCAO_PREPARADA = "preparada"
EXECUCAO_EXCLUIDA = "excluida_historica"

CATEGORIA_OPERACIONAL = "operacional"
CATEGORIA_FINANCEIRA = "financeira"
CATEGORIA_HISTORICA = "historica_excluida"

# Colunas monetárias fora do financeiro são violação de fronteira (M14.2).
_MONETARY_HEADER_RE = re.compile(
    r"(^|_)(valor|valores|preco|precos|preço|montante|repasse|receita|despesa)(_|$)|r\$"
)


def normalize_header(name: str) -> str:
    """Mesma normalização de cabeçalho usada pelo importador CSV."""
    return (name or "").strip().lower().replace(" ", "_")


def is_monetary_header(header: str) -> bool:
    return bool(_MONETARY_HEADER_RE.search(normalize_header(header)))


@dataclass(frozen=True)
class MappingSpec:
    source_type: str
    target_module: str
    relacoes: tuple[str, ...]
    categoria: str
    execucao: str
    # cada grupo interno exige AO MENOS um dos cabeçalhos presentes
    required_any: tuple[tuple[str, ...], ...]
    known_headers: frozenset
    date_headers: tuple[str, ...] = ()
    monetario: bool = False
    descricao: str = ""


_PESSOA_CONTATO = ("telefone", "whatsapp", "celular", "fone", "email", "e-mail")

MAPPINGS: dict[str, MappingSpec] = {
    spec.source_type: spec
    for spec in (
        MappingSpec(
            source_type="crm_pacientes",
            target_module="people",
            relacoes=("person_contacts", "legacy_aliases", "identity_candidates"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_NATIVA,
            required_any=(("nome", "nome_completo", "paciente"),),
            known_headers=frozenset(
                ("paciente_id", "id", "nome", "nome_completo", "paciente",
                 "data_nascimento", "nascimento", "observacao", "obs")
                + _PESSOA_CONTATO
            ),
            date_headers=("data_nascimento", "nascimento"),
            descricao="Pessoas e contatos (CRM Pacientes)",
        ),
        MappingSpec(
            source_type="leads",
            target_module="leads",
            relacoes=("people", "person_contacts", "followups", "legacy_aliases"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_NATIVA,
            required_any=(("nome", "nome_completo", "lead", "paciente"),),
            known_headers=frozenset(
                ("id", "lead_id", "paciente_id", "nome", "nome_completo", "lead",
                 "paciente", "origem", "fonte", "canal", "canal_entrada",
                 "modalidade", "tipo_atendimento", "local",
                 "data_primeiro_contato", "data_contato", "data", "criado_em",
                 "responsavel", "observacao", "obs")
                + _PESSOA_CONTATO
            ),
            date_headers=("data_primeiro_contato", "data_contato", "data", "criado_em"),
            descricao="Leads comerciais",
        ),
        MappingSpec(
            source_type="crm_espirometria",
            target_module="spirometry_exams",
            relacoes=("people", "followups", "legacy_aliases", "identity_candidates"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_NATIVA,
            required_any=(
                ("paciente_id", "id_paciente", "nome", "paciente", "nome_paciente"),
            ),
            known_headers=frozenset(
                ("exame_id", "id_atendimento", "id", "paciente_id", "id_paciente",
                 "nome", "paciente", "nome_paciente", "telefone", "whatsapp",
                 "data_exame", "data", "data_atendimento", "status", "status_exame",
                 "local", "local_atendimento", "origem", "responsavel",
                 "observacao", "obs")
            ),
            date_headers=("data_exame", "data", "data_atendimento"),
            descricao="Exames de espirometria (CRM Espirometria)",
        ),
        MappingSpec(
            source_type="crm_consultas",
            target_module="consultations",
            relacoes=("people", "followups", "legacy_aliases", "identity_candidates"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_NATIVA,
            required_any=(
                ("paciente_id", "id_paciente", "nome", "paciente", "nome_paciente"),
            ),
            known_headers=frozenset(
                ("consulta_id", "id", "paciente_id", "id_paciente", "nome",
                 "paciente", "nome_paciente", "telefone", "whatsapp",
                 "data_consulta", "data", "status", "profissional", "medico",
                 "observacao", "obs")
            ),
            date_headers=("data_consulta", "data"),
            descricao="Consultas médicas (CRM Consultas)",
        ),
        MappingSpec(
            source_type="contatos_b2b",
            target_module="partners",
            relacoes=("partner_contacts", "legacy_aliases"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_NATIVA,
            required_any=(("clinica", "empresa", "nome_clinica", "nome"),),
            known_headers=frozenset(
                ("id", "contato_id", "clinica", "empresa", "nome_clinica", "nome",
                 "cidade", "contato", "contato_nome", "responsavel", "cargo",
                 "telefone", "whatsapp", "email", "e-mail", "observacao", "obs")
            ),
            descricao="Contatos B2B (prospecção de clínicas/empresas)",
        ),
        MappingSpec(
            source_type="crm_clinicas",
            target_module="partners",
            relacoes=("partner_units", "partner_contacts", "legacy_aliases"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_PREPARADA,
            required_any=(("nome", "clinica", "nome_clinica"),),
            known_headers=frozenset(
                ("clinica_id", "id", "nome", "clinica", "nome_clinica", "tipo",
                 "status", "cidade", "bairro", "unidade", "telefone", "email",
                 "e-mail", "contato", "cargo", "observacao", "obs")
            ),
            descricao="Clínicas parceiras e unidades (CRM Clinicas)",
        ),
        MappingSpec(
            source_type="consentimentos",
            target_module="consents",
            relacoes=("people",),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_PREPARADA,
            required_any=(("paciente_id",), ("canal",), ("status",)),
            known_headers=frozenset(
                ("paciente_id", "canal", "status", "origem", "data",
                 "nao_contatar", "observacao", "obs")
            ),
            date_headers=("data",),
            descricao="Consentimento e não-contatar por canal",
        ),
        MappingSpec(
            source_type="parcerias_encaminhamentos",
            target_module="partner_referrals",
            relacoes=("partnerships", "partners", "partner_units", "people",
                      "legacy_aliases"),
            categoria=CATEGORIA_OPERACIONAL,
            execucao=EXECUCAO_PREPARADA,
            required_any=(
                ("paciente_id",),
                ("parceiro_id", "clinica_id", "clinica"),
            ),
            known_headers=frozenset(
                ("encaminhamento_id", "id", "paciente_id", "parceiro_id",
                 "clinica_id", "clinica", "unidade", "data_encaminhamento",
                 "data_agendada", "data_realizacao", "servico",
                 "servico_solicitado", "status", "laudo_enviado",
                 "data_envio_laudo", "autorizacao_contato", "responsavel",
                 "observacao", "obs")
            ),
            date_headers=("data_encaminhamento", "data_agendada",
                          "data_realizacao", "data_envio_laudo"),
            descricao="Parcerias e encaminhamentos (inclui planilhas de "
                      "parceiro tipo Pastore — SEM colunas monetárias; valores "
                      "vivem só em Financeiro_Lancamentos)",
        ),
        MappingSpec(
            source_type="financeiro_lancamentos",
            target_module="financial_entries",
            relacoes=("spirometry_exams", "consultations", "partner_referrals"),
            categoria=CATEGORIA_FINANCEIRA,
            execucao=EXECUCAO_PREPARADA,
            required_any=(("tipo",), ("valor",)),
            known_headers=frozenset(
                ("lancamento_id", "id", "tipo", "categoria", "descricao",
                 "valor", "moeda", "data_competencia", "data_recebimento",
                 "status", "forma_pagamento", "origem_preco", "exame_id",
                 "consulta_id", "encaminhamento_id")
            ),
            date_headers=("data_competencia", "data_recebimento"),
            monetario=True,
            descricao="Financeiro_Lancamentos — ÚNICA fonte monetária; "
                      "vínculos clínicos apenas por IDs técnicos, sem PII",
        ),
        MappingSpec(
            source_type="pcmso_historico",
            target_module="",
            relacoes=(),
            categoria=CATEGORIA_HISTORICA,
            execucao=EXECUCAO_EXCLUIDA,
            required_any=(),
            known_headers=frozenset(),
            descricao="PCMSO: categoria histórica excluída — registrável "
                      "como snapshot arquivado, nunca importável",
        ),
    )
}


def get_mapping(source_type: str) -> MappingSpec | None:
    return MAPPINGS.get(source_type)


def check_headers(
    spec: MappingSpec,
    headers: list[str],
    extras_aprovadas: list[str] | None = None,
) -> list[str]:
    """Valida cabeçalhos do snapshot contra o mapeamento. Retorna erros.

    - fronteira monetária é erro DURO (extra aprovada não salva);
    - grupo obrigatório ausente falha;
    - cabeçalho desconhecido falha, salvo mapeamento explícito no manifesto.
    """
    erros: list[str] = []
    norm = [normalize_header(h) for h in headers]
    extras = {normalize_header(e) for e in (extras_aprovadas or [])}

    if spec.execucao != EXECUCAO_EXCLUIDA and not spec.monetario:
        for h in norm:
            if is_monetary_header(h):
                erros.append(f"coluna_monetaria_fora_do_financeiro:{h}")

    if spec.execucao == EXECUCAO_EXCLUIDA:
        return erros  # fonte arquivada: sem contrato de colunas ativo

    presentes = set(norm)
    for grupo in spec.required_any:
        if not presentes.intersection(grupo):
            erros.append("cabecalho_obrigatorio_ausente:" + "|".join(grupo))
    for h in norm:
        if h not in spec.known_headers and h not in extras and not (
            spec.monetario is False and is_monetary_header(h)
        ):
            erros.append(f"cabecalho_desconhecido:{h}")
    return erros
