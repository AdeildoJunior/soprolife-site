"""Adapters explícitos dos layouts REAIS sanitizados (M15.6B).

Cada aba real inventariada tem um adapter fechado: conjunto EXATO de
cabeçalhos conhecidos, papéis explícitos por coluna e mapeamento canônico
com proveniência (cabeçalho original preservado). Regras duras:

- cabeçalho obrigatório ausente = rejeição da aba inteira (fail-closed);
- cabeçalho desconhecido = rejeição (nenhuma heurística de adivinhação);
- CRM Pacientes: primeiro_nome -> campo canônico nome_pessoa, SEM perder a
  proveniência do cabeçalho original;
- Financeiro_Lancamentos é a ÚNICA fonte monetária (papel valor_monetario);
- colunas monetárias das abas Pastore são REFERÊNCIA BLOQUEADA: jamais
  produzem verdade monetária nem proposta financeira;
- PCMSO é histórico EXCLUÍDO: adapter existe só para inventariar/excluir;
- nenhum valor real aparece aqui — apenas nomes de campos sanitizados.
"""

from dataclasses import dataclass

from .mapping import is_monetary_header, normalize_header

ADAPTERS_VERSION = "m15-6b.1"

# Papéis de coluna (contrato fechado).
PAPEL_ID = "id"                                # identificador explícito da fonte
PAPEL_REF_PESSOA = "ref_pessoa"                # referência explícita de pessoa
PAPEL_REF_TECNICA = "ref_tecnica"              # referência técnica entre entidades
PAPEL_NOME_PESSOA = "nome_pessoa"              # nome de pessoa (nunca vincula sozinho)
PAPEL_TELEFONE = "telefone"                    # candidato de identidade, nunca prova
PAPEL_EMAIL = "email"                          # candidato de identidade, nunca prova
PAPEL_DATA = "data"                            # data com precisão preservada
PAPEL_DATA_HORA = "data_hora"                  # datetime com precisão preservada
PAPEL_CONSENTIMENTO = "consentimento"          # consentimento/não-contatar
PAPEL_TEXTO = "texto"                          # texto operacional simples
PAPEL_NOTA_PRIVADA = "nota_privada"            # nunca exportada em resumo/log
PAPEL_VALOR_MONETARIO = "valor_monetario"      # SÓ Financeiro_Lancamentos
PAPEL_REF_MONETARIA_BLOQUEADA = "ref_monetaria_bloqueada"  # Pastore: nunca vira dinheiro
PAPEL_UNIDADE = "unidade"                      # unidade/parceiro (Pastore)

# Estágios da ordem de dependência multiaba (M15.6B, fixa e estável).
ESTAGIO_HISTORICO = 1
ESTAGIO_PARCEIROS = 2
ESTAGIO_CONTATOS_PARCEIRO = 3
ESTAGIO_PESSOAS = 4
ESTAGIO_CONSENTIMENTOS = 5
ESTAGIO_LEADS = 6
ESTAGIO_ESPIROMETRIA = 7
ESTAGIO_CONSULTAS = 8
ESTAGIO_FOLLOWUPS = 9
ESTAGIO_FINANCEIRO = 10

CATEGORIA_OPERACIONAL = "operacional"
CATEGORIA_FINANCEIRA = "financeira"
CATEGORIA_HISTORICA = "historica_excluida"


@dataclass(frozen=True)
class CampoReal:
    header: str                 # cabeçalho ORIGINAL da planilha real
    papel: str
    canonico: str | None = None  # campo canônico; None = só proveniência
    obrigatorio: bool = False
    data_ativa: bool = True      # data inválida entra na fila ativa de revisão?


@dataclass(frozen=True)
class AdapterReal:
    sheet_kind: str
    layout_version: str
    estagio: int
    alvo: str                    # entidade(s) alvo do staging
    categoria: str
    campos: tuple[CampoReal, ...]
    monetario: bool = False
    descricao: str = ""

    def por_header_normalizado(self) -> dict[str, CampoReal]:
        return {normalize_header(c.header): c for c in self.campos}


@dataclass(frozen=True)
class LinhaAdaptada:
    """Linha canônica: valores por campo canônico + proveniência do original."""

    campos: dict            # canonico -> valor (str, já com strip)
    proveniencia: dict      # canonico -> cabeçalho original
    papeis: dict            # canonico -> papel


def _c(header, papel, canonico=None, obrigatorio=False, data_ativa=True):
    return CampoReal(header, papel, canonico or normalize_header(header),
                     obrigatorio, data_ativa)


ADAPTERS: dict[str, AdapterReal] = {
    a.sheet_kind: a
    for a in (
        AdapterReal(
            sheet_kind="crm_pacientes",
            layout_version="m15-6b.crm_pacientes.1",
            estagio=ESTAGIO_PESSOAS,
            alvo="people",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("paciente_id", PAPEL_ID, obrigatorio=True),
                _c("data_cadastro", PAPEL_DATA),
                # Exemplo obrigatório do contrato: primeiro_nome -> nome_pessoa
                _c("primeiro_nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa",
                   obrigatorio=True),
                _c("telefone", PAPEL_TELEFONE),
                _c("ultimo_servico", PAPEL_TEXTO),
                _c("status_relacionamento", PAPEL_TEXTO),
                _c("proximo_contato", PAPEL_DATA),
                _c("motivo_proximo_contato", PAPEL_TEXTO),
                _c("canal", PAPEL_TEXTO),
                _c("responsavel", PAPEL_TEXTO),
                _c("consentimento_whatsapp", PAPEL_CONSENTIMENTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
                _c("ultima_consulta_data", PAPEL_DATA),
                _c("ultima_consulta_tipo", PAPEL_TEXTO),
                _c("ultima_consulta_status", PAPEL_TEXTO),
                _c("proxima_consulta_data", PAPEL_DATA),
                _c("proxima_consulta_tipo", PAPEL_TEXTO),
                _c("status_consulta", PAPEL_TEXTO),
            ),
            descricao="CRM Pacientes -> pessoas, contatos, consentimento e aliases",
        ),
        AdapterReal(
            sheet_kind="crm_espirometria",
            layout_version="m15-6b.crm_espirometria.1",
            estagio=ESTAGIO_ESPIROMETRIA,
            alvo="spirometry_exams",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("exame_id", PAPEL_ID, obrigatorio=True),
                _c("data_entrada", PAPEL_DATA),
                _c("primeiro_nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa"),
                _c("telefone", PAPEL_TELEFONE),
                _c("servico", PAPEL_TEXTO),
                _c("origem", PAPEL_TEXTO),
                _c("status_exame", PAPEL_TEXTO),
                _c("data_exame", PAPEL_DATA),
                _c("proximo_contato", PAPEL_DATA),
                _c("motivo_proximo_contato", PAPEL_TEXTO),
                _c("canal", PAPEL_TEXTO),
                _c("responsavel", PAPEL_TEXTO),
                _c("consentimento_whatsapp", PAPEL_CONSENTIMENTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
            ),
            descricao="CRM Espirometria -> exames + candidato de pessoa",
        ),
        AdapterReal(
            sheet_kind="crm_consultas",
            layout_version="m15-6b.crm_consultas.1",
            estagio=ESTAGIO_CONSULTAS,
            alvo="consultations",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("consulta_id", PAPEL_ID, obrigatorio=True),
                _c("data_entrada", PAPEL_DATA),
                _c("primeiro_nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa"),
                _c("telefone", PAPEL_TELEFONE),
                _c("origem", PAPEL_TEXTO),
                _c("tipo_consulta", PAPEL_TEXTO),
                _c("status", PAPEL_TEXTO),
                _c("medica", PAPEL_TEXTO),
                _c("data_consulta", PAPEL_DATA),
                _c("proximo_contato", PAPEL_DATA),
                _c("motivo_proximo_contato", PAPEL_TEXTO),
                _c("canal", PAPEL_TEXTO),
                _c("responsavel", PAPEL_TEXTO),
                _c("consentimento_whatsapp", PAPEL_CONSENTIMENTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
                # alias determinístico entre abas: pessoa explícita da fonte
                _c("paciente_id", PAPEL_REF_PESSOA),
                _c("tipo_evento", PAPEL_TEXTO),
                _c("consulta_referencia_id", PAPEL_REF_TECNICA),
                _c("retorno_recomendado", PAPEL_TEXTO),
                _c("retorno_previsto_data", PAPEL_DATA),
                _c("retorno_agendado_data", PAPEL_DATA),
                _c("status_retorno", PAPEL_TEXTO),
            ),
            descricao="CRM Consultas -> consultas + vínculo explícito de pessoa",
        ),
        AdapterReal(
            sheet_kind="leads",
            layout_version="m15-6b.leads.1",
            estagio=ESTAGIO_LEADS,
            alvo="leads",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("lead_id", PAPEL_ID, obrigatorio=True),
                _c("data_contato", PAPEL_DATA),
                _c("nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa"),
                _c("telefone_whatsapp", PAPEL_TELEFONE),
                _c("servico_interesse", PAPEL_TEXTO),
                _c("origem", PAPEL_TEXTO),
                _c("etapa", PAPEL_TEXTO),
                _c("responsavel", PAPEL_TEXTO),
                _c("proxima_acao", PAPEL_TEXTO),
                _c("data_proxima_acao", PAPEL_DATA),
                _c("observacao", PAPEL_TEXTO),
                _c("tipo_lead", PAPEL_TEXTO),
                _c("entidade_destino", PAPEL_TEXTO),
                _c("entidade_id", PAPEL_REF_TECNICA),
                _c("data_conversao", PAPEL_DATA),
                _c("status_operacional", PAPEL_TEXTO),
            ),
            descricao="Leads -> leads com vínculo de pessoa OPCIONAL explícito",
        ),
        AdapterReal(
            sheet_kind="crm_clinicas",
            layout_version="m15-6b.crm_clinicas.1",
            estagio=ESTAGIO_PARCEIROS,
            alvo="partners",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("clinica_id", PAPEL_ID, obrigatorio=True),
                _c("nome_clinica", PAPEL_TEXTO, obrigatorio=True),
                _c("bairro", PAPEL_TEXTO),
                _c("regiao", PAPEL_TEXTO),
                _c("tipo_clinica", PAPEL_TEXTO),
                _c("etapa", PAPEL_TEXTO),
                _c("ultima_interacao", PAPEL_DATA_HORA),
                _c("proxima_acao", PAPEL_TEXTO),
                _c("data_proxima_acao", PAPEL_DATA),
                _c("responsavel", PAPEL_TEXTO),
                _c("prioridade", PAPEL_TEXTO),
                _c("observacao", PAPEL_TEXTO),
            ),
            descricao="CRM Clinicas -> parceiros/unidades/vínculos (ANTES dos "
                      "contatos B2B)",
        ),
        AdapterReal(
            sheet_kind="contatos_b2b",
            layout_version="m15-6b.contatos_b2b.1",
            estagio=ESTAGIO_CONTATOS_PARCEIRO,
            alvo="partner_contacts",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("contato_id", PAPEL_ID, obrigatorio=True),
                _c("clinica_id", PAPEL_REF_TECNICA, obrigatorio=True),
                _c("nome_contato", PAPEL_TEXTO, obrigatorio=True),
                _c("cargo", PAPEL_TEXTO),
                _c("telefone_whatsapp", PAPEL_TELEFONE),
                _c("email", PAPEL_EMAIL),
                _c("papel", PAPEL_TEXTO),
                _c("status_relacionamento", PAPEL_TEXTO),
                _c("ultima_interacao", PAPEL_DATA_HORA),
                _c("proxima_acao", PAPEL_TEXTO),
                _c("data_proxima_acao", PAPEL_DATA),
                _c("responsavel", PAPEL_TEXTO),
                _c("observacao", PAPEL_TEXTO),
            ),
            descricao="CRM Contatos B2B -> contatos de parceiro (clínica já "
                      "processada no estágio anterior)",
        ),
        AdapterReal(
            sheet_kind="followup_whatsapp",
            layout_version="m15-6b.followup_whatsapp.1",
            estagio=ESTAGIO_FOLLOWUPS,
            alvo="followups",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("followup_id", PAPEL_ID, obrigatorio=True),
                _c("data_criacao", PAPEL_DATA),
                _c("primeiro_nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa"),
                _c("telefone", PAPEL_TELEFONE),
                _c("tipo_mensagem", PAPEL_TEXTO),
                _c("data_prevista", PAPEL_DATA),
                _c("status", PAPEL_TEXTO),
                _c("canal", PAPEL_TEXTO),
                _c("responsavel", PAPEL_TEXTO),
                _c("template_usado", PAPEL_TEXTO),
                _c("consentimento", PAPEL_CONSENTIMENTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
            ),
            descricao="Follow-up WhatsApp -> followups + candidato de pessoa",
        ),
        AdapterReal(
            sheet_kind="financeiro_lancamentos",
            layout_version="m15-6b.financeiro_lancamentos.1",
            estagio=ESTAGIO_FINANCEIRO,
            alvo="financial_entries",
            categoria=CATEGORIA_FINANCEIRA,
            monetario=True,
            campos=(
                _c("id_lancamento", PAPEL_ID, obrigatorio=True),
                # ÚNICO caminho de vínculo: referência técnica, nunca nome
                _c("id_atendimento", PAPEL_REF_TECNICA),
                _c("criado_em", PAPEL_DATA_HORA),
                _c("data_exame", PAPEL_DATA),
                _c("tipo_movimento", PAPEL_TEXTO, obrigatorio=True),
                _c("servico", PAPEL_TEXTO),
                _c("local_atendimento", PAPEL_TEXTO),
                _c("valor_tabela", PAPEL_VALOR_MONETARIO),
                _c("valor_cobrado", PAPEL_VALOR_MONETARIO),
                _c("valor_recebido", PAPEL_VALOR_MONETARIO),
                _c("desconto", PAPEL_VALOR_MONETARIO),
                _c("status_exame", PAPEL_TEXTO),
                _c("status_pagamento", PAPEL_TEXTO),
                _c("forma_pagamento", PAPEL_TEXTO),
                _c("origem_preco", PAPEL_TEXTO),
                _c("observacao_financeira", PAPEL_NOTA_PRIVADA),
                _c("fonte", PAPEL_TEXTO),
            ),
            descricao="Financeiro_Lancamentos — ÚNICA fonte monetária de verdade",
        ),
        AdapterReal(
            sheet_kind="pastore_config",
            layout_version="m15-6b.pastore_config.1",
            estagio=ESTAGIO_PARCEIROS,
            alvo="partner_units",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("unidade", PAPEL_UNIDADE, obrigatorio=True),
                _c("status", PAPEL_TEXTO),
                _c("dia_semana", PAPEL_TEXTO),
                _c("horario_inicio", PAPEL_TEXTO),
                _c("horario_fim", PAPEL_TEXTO),
                _c("capacidade_estimada_por_turno", PAPEL_TEXTO),
                # Config Pastore NUNCA cria verdade monetária (fonte única M14.2)
                _c("valor_exame_sem_bd", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("valor_exame_com_bd", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("repasse_pastore_tipo", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("repasse_pastore_valor", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_insumo_padrao", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_deslocamento_padrao", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_profissional_padrao", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("observacao", PAPEL_TEXTO),
            ),
            descricao="Parceria Pastore - Config -> parceiro ativo + unidades "
                      "suportadas (sem verdade monetária)",
        ),
        AdapterReal(
            sheet_kind="pastore_atendimentos",
            layout_version="m15-6b.pastore_atendimentos.1",
            estagio=ESTAGIO_ESPIROMETRIA,
            alvo="spirometry_attendance_candidates",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("data_atendimento", PAPEL_DATA, obrigatorio=True),
                _c("unidade", PAPEL_UNIDADE, obrigatorio=True),
                _c("dia_semana", PAPEL_TEXTO),
                _c("horario_inicio", PAPEL_TEXTO),
                _c("horario_fim", PAPEL_TEXTO),
                _c("origem", PAPEL_TEXTO),
                _c("paciente_nome", PAPEL_NOME_PESSOA, canonico="nome_pessoa"),
                _c("paciente_whatsapp", PAPEL_TELEFONE),
                _c("tipo_exame", PAPEL_TEXTO),
                _c("broncodilatador", PAPEL_TEXTO),
                # CRM Pastore NUNCA cria verdade monetária
                _c("valor_cobrado", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("forma_pagamento", PAPEL_TEXTO),
                _c("recebido_por", PAPEL_TEXTO),
                _c("repasse_pastore", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_insumo", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_deslocamento", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_profissional", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("outros_custos", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("receita_bruta", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("custo_total", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("resultado_liquido", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("status", PAPEL_TEXTO),
                _c("followup_status", PAPEL_TEXTO),
                _c("consentimento_contato_futuro", PAPEL_CONSENTIMENTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
            ),
            descricao="Parceria Pastore - Atendimentos -> candidatos de "
                      "atendimento de espirometria (sem verdade monetária)",
        ),
        AdapterReal(
            sheet_kind="pastore_custos",
            layout_version="m15-6b.pastore_custos.1",
            estagio=ESTAGIO_FINANCEIRO,
            alvo="",
            categoria=CATEGORIA_OPERACIONAL,
            campos=(
                _c("data", PAPEL_DATA, obrigatorio=True),
                _c("unidade", PAPEL_UNIDADE, obrigatorio=True),
                _c("tipo_custo", PAPEL_TEXTO),
                _c("descricao", PAPEL_TEXTO),
                _c("valor", PAPEL_REF_MONETARIA_BLOQUEADA),
                _c("pago_por", PAPEL_TEXTO),
                _c("recorrente", PAPEL_TEXTO),
                _c("observacao_privada_minima", PAPEL_NOTA_PRIVADA),
            ),
            descricao="Parceria Pastore - Custos: fonte monetária NÃO "
                      "autorizada — linhas exigem decisão humana; nunca viram "
                      "lançamento financeiro",
        ),
        AdapterReal(
            sheet_kind="pcmso_historico",
            layout_version="m15-6b.pcmso_historico.1",
            estagio=ESTAGIO_HISTORICO,
            alvo="",
            categoria=CATEGORIA_HISTORICA,
            campos=(
                _c("Nome da clínica/empresa", PAPEL_TEXTO,
                   canonico="nome_clinica_empresa"),
                _c("Tipo", PAPEL_TEXTO),
                _c("Bairro/Região", PAPEL_TEXTO, canonico="bairro_regiao"),
                _c("Telefone/WhatsApp", PAPEL_TELEFONE,
                   canonico="telefone_whatsapp"),
                _c("Pessoa de contato", PAPEL_TEXTO, canonico="pessoa_contato"),
                _c("Origem da lista", PAPEL_TEXTO, canonico="origem_lista"),
                _c("Status", PAPEL_TEXTO),
                _c("Interesse", PAPEL_TEXTO),
                _c("Próximo passo", PAPEL_TEXTO, canonico="proximo_passo"),
                # histórica: data inválida NÃO entra na fila ativa de revisão
                _c("Data do próximo passo", PAPEL_DATA,
                   canonico="data_proximo_passo", data_ativa=False),
                _c("Observações", PAPEL_TEXTO, canonico="observacoes"),
                _c("Etapa", PAPEL_TEXTO),
            ),
            descricao="Base Prospecção B2B PCMSO — histórico preservado e "
                      "EXCLUÍDO da migração ativa",
        ),
    )
}

KNOWN_SHEET_KINDS = frozenset(ADAPTERS)


def _validar_fronteira_monetaria() -> None:
    """Auto-checagem no import: dinheiro canônico só no financeiro; cabeçalho
    monetário fora dele precisa estar EXPLICITAMENTE bloqueado."""
    for adapter in ADAPTERS.values():
        for campo in adapter.campos:
            if campo.papel == PAPEL_VALOR_MONETARIO and not adapter.monetario:
                raise AssertionError(
                    f"valor monetário fora do financeiro: {adapter.sheet_kind}"
                )
            if (not adapter.monetario
                    and is_monetary_header(campo.header)
                    and campo.papel != PAPEL_REF_MONETARIA_BLOQUEADA):
                raise AssertionError(
                    f"cabeçalho monetário sem bloqueio explícito: "
                    f"{adapter.sheet_kind}.{campo.header}"
                )


_validar_fronteira_monetaria()


def get_adapter(sheet_kind: str) -> AdapterReal | None:
    return ADAPTERS.get(sheet_kind)


def check_real_headers(adapter: AdapterReal, headers: list[str] | tuple) -> list[str]:
    """Valida os cabeçalhos REAIS da aba contra o layout fechado do adapter.

    Retorna lista de erros (vazia = ok). Cabeçalho crítico desconhecido e
    obrigatório ausente falham fechado; nada é adivinhado.
    """
    erros: list[str] = []
    conhecidos = adapter.por_header_normalizado()
    normalizados = [normalize_header(h) for h in headers]
    presentes = {h for h in normalizados if h}
    vistos: set[str] = set()
    for norm in normalizados:
        if norm and norm in vistos:
            erros.append(f"cabecalho_duplicado:{norm}")
        vistos.add(norm)
    for campo in adapter.campos:
        if campo.obrigatorio and normalize_header(campo.header) not in presentes:
            erros.append(f"cabecalho_obrigatorio_ausente:{normalize_header(campo.header)}")
    for h in headers:
        norm = normalize_header(h)
        if not norm:
            erros.append("cabecalho_vazio")
            continue
        if norm not in conhecidos:
            erros.append(f"cabecalho_desconhecido:{norm}")
    return erros


def adapt_row(adapter: AdapterReal, headers: tuple, valores: tuple) -> LinhaAdaptada:
    """Converte uma linha bruta em campos canônicos com proveniência.

    Pressupõe cabeçalhos já validados por check_real_headers.
    """
    conhecidos = adapter.por_header_normalizado()
    campos: dict = {}
    proveniencia: dict = {}
    papeis: dict = {}
    for header, valor in zip(headers, valores):
        campo = conhecidos.get(normalize_header(header))
        if campo is None:
            continue  # inalcançável após check_real_headers; nunca adivinha
        campos[campo.canonico] = (valor or "").strip()
        proveniencia[campo.canonico] = header
        papeis[campo.canonico] = campo.papel
    return LinhaAdaptada(campos=campos, proveniencia=proveniencia, papeis=papeis)


def campos_por_papel(adapter: AdapterReal, *papeis: str) -> list[CampoReal]:
    return [c for c in adapter.campos if c.papel in papeis]
