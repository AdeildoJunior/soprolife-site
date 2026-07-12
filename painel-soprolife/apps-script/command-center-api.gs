// SoproLife Command Center — Apps Script Web App API
// Versão: 1.0  |  Fase 3 do Centro de Comando
//
// INSTALAÇÃO:
//   1. Abra a planilha Google Sheets da SoproLife.
//   2. Extensões → Apps Script → cole este arquivo no editor.
//   3. Arquivo → Propriedades do projeto → Propriedades do script:
//      Adicione a propriedade  API_TOKEN  com um valor secreto de sua escolha.
//      Ex.: API_TOKEN = aX9mQ2rT8uV5wZ3n  (gere algo aleatório, min. 16 chars)
//   4. Publicar → Implantar como app da web:
//      Executar como: Eu (minha conta)
//      Quem pode acessar: Qualquer pessoa
//   5. Copie a URL gerada e salve em:
//      painel-soprolife/data-private/command-center-config.local.json
//
// SEGURANÇA:
//   - O token só existe nas Propriedades do Script (nunca no código).
//   - Toda requisição exige o token no corpo do POST (não na URL).
//   - Telefone e observação privada NUNCA são registrados no log.
//   - O log registra apenas: timestamp, ação, status, aba, id, e-mail do executor, resumo.

// ── Constantes ────────────────────────────────────────────────────────────────

var _SHEETS = {
  LEADS:             "Leads",
  // Abas técnicas já criadas por converter-lead-em-paciente.gs (ocultas na planilha).
  // Reaproveitadas aqui, não recriadas — mesmos nomes exatos de _CV_ABA_CONVERTIDOS / _CV_ABA_LOG.
  LEADS_CONVERTIDOS: "Leads Convertidos",
  LOG_CONVERSOES:    "Log Conversões Leads",
  PACIENTES:         "CRM Pacientes",
  ESPIROMETRIA:      "CRM Espirometria",
  CONSULTAS:         "CRM Consultas",
  B2B_PCMSO:         "Base Prospecção B2B PCMSO",
  CRM_CLINICAS:      "CRM Clinicas",
  CRM_CONTATOS_B2B:  "CRM Contatos B2B",
  FOLLOWUP_WA:       "Follow-up WhatsApp",
  LOG:               "Log Centro Comando",
  // Trilha de auditoria append-only das escritas (M1) — ver _logAudit.
  LOG_AUDITORIA:     "Log Auditoria",
  // Parceria Pastore — ver painel-soprolife/docs/parceria-pastore-planilha.md
  PARCERIA_PASTORE_ATENDIMENTOS: "Parceria Pastore - Atendimentos",
  // M11.2A — lançamentos financeiros seguros da Nova Espirometria (Command
  // Center). Ver _registrarEspirometriaFinanceiro.
  FINANCEIRO_LANCAMENTOS: "Financeiro_Lancamentos",
};

// Cabeçalho da aba "Financeiro_Lancamentos" — só campos financeiros seguros,
// nunca nome/telefone/CPF/e-mail/endereço/observação clínica do paciente
// (ver _EF_CAMPOS_PROIBIDOS). Mesmo shape do payload de
// buildEspirometriaFinanceiroPayload (js/espirometria-financeiro.js) + os
// dois IDs de gravação (id_lancamento, id_atendimento).
var _FINANCEIRO_LANCAMENTOS_CABECALHO = [
  "id_lancamento", "id_atendimento", "criado_em", "data_exame", "tipo_movimento",
  "servico", "local_atendimento", "valor_tabela", "valor_cobrado", "valor_recebido",
  "desconto", "status_exame", "status_pagamento", "forma_pagamento", "origem_preco",
  "observacao_financeira", "fonte",
];

// Mesmos enums fechados de js/espirometria-financeiro.js — duplicados de
// propósito (cada arquivo é instalado/editado independentemente; ver nota
// equivalente em _CRM_ETAPAS_OFICIAIS). O servidor NUNCA confia apenas na
// validação do front-end.
var _EF_STATUS_EXAME      = ["Aguardando", "Realizado", "Cancelado", "Remarcado"];
var _EF_STATUS_PAGAMENTO  = ["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"];
var _EF_FORMA_PAGAMENTO   = ["Pix", "Dinheiro", "Cartão", "Outro"];
var _EF_ORIGEM_PRECO      = ["Tabela", "Promoção", "Parceria", "Negociação", "PCMSO", "Cortesia"];
var _EF_LOCAL_ATENDIMENTO = ["Domiciliar", "Clínica", "Empresa / PCMSO", "Parceiro", "Outro"];
var _EF_VALOR_TABELA_PADRAO = 250;

// Allowlist NEGATIVA (default-aberta ao contrário de _AUDIT_CAMPOS_PERMITIDOS):
// nenhum destes campos pode aparecer no payload de registrarEspirometriaFinanceiro,
// mesmo vazio — o lançamento financeiro é público internamente (planilha
// financeira) e nunca deve carregar dado de identificação do paciente.
var _EF_CAMPOS_PROIBIDOS = [
  "nome", "primeiro_nome", "paciente", "responsavel", "telefone", "whatsapp",
  "cpf", "email", "endereco", "token", "secret",
];


// Etapas que disparam a conversão automática do lead para os CRMs de
// atendimento (mesma lista usada pelo gatilho onEdit em converter-lead-em-paciente.gs).
var _LEAD_ETAPAS_CONVERSAO = [
  "Realizou consulta",
  "Realizou espirometria",
  "Realizou consulta e espirometria",
];

// Etapas oficiais aceitas pelo painel e pelo dropdown da aba Leads.
// A função updateLeadStage garante que a célula permita a etapa antes de gravar.
var _LEAD_ETAPAS_OFICIAIS = [
  "Novo contato",
  "Em conversa",
  "Aguardando retorno",
  "Agendado",
  "Realizou consulta",
  "Realizou espirometria",
  "Realizou consulta e espirometria",
  "Concluído",
  "Não respondeu",
  "Desistiu",
  "Perdido",
  // Etapas B2B / parcerias (leads de clínica, PCMSO, empresas) — ver vincularLeadAClinica.
  "Reunião marcada",
  "Proposta enviada",
  "Parceria fechada",
  "Parceiro ativo",
  "Convertido em clínica/parceiro",
  "Sem resposta",
];

// Etapas B2B terminais aceitas por vincularLeadAClinica — marcam o lead como
// convertido/arquivado e vinculado a uma entidade em CRM Clínicas.
// Propositalmente separadas de _LEAD_ETAPAS_CONVERSAO (fluxo de paciente,
// que aciona _cvConverterLeadCore) para não disparar a conversão em CRM Pacientes.
var _LEAD_ETAPAS_B2B_TERMINAL = [
  "Convertido em clínica/parceiro",
  "Parceiro ativo",
];

// Etapas oficiais do CRM Clínicas / Base Prospecção B2B PCMSO — mesmo
// vocabulário canônico usado em app.js (CRM_ETAPAS_ATIVAS + CRM_ETAPAS_TERMINAIS)
// e nos scripts Python (generate-followup-clinicas.py, promote-pcmso-to-crm.py).
// Duplicado de propósito: cada arquivo é instalado/editado independentemente.
// updateCrmClinicaEtapa recusa qualquer valor fora desta lista.
var _CRM_ETAPAS_OFICIAIS = [
  "Não abordada",
  "Abordada",
  "Em conversa",
  "Pediu apresentação",
  "Aguardando retorno",
  "Proposta enviada",
  "Parceiro ativo",
  "Sem interesse",
  "Não contatar / bloqueou",
  "Sem canal válido",
  "Arquivada",
];

// Colunas novas de suporte ao modelo B2B na aba Leads — criadas sob demanda
// (a primeira vez que vincularLeadAClinica rodar) sem remover/reordenar as
// colunas antigas: lead_id, data_contato, nome, telefone_whatsapp,
// servico_interesse, origem, etapa, responsavel, proxima_acao,
// data_proxima_acao, observacao.
var _LEAD_COLUNAS_EXTRA_B2B = [
  "tipo_lead",
  "entidade_destino",
  "entidade_id",
  "data_conversao",
  "status_operacional",
];

// Cabeçalho da aba "CRM Contatos B2B" — nova aba, criada apenas quando um
// contato é vinculado pela primeira vez (vincularLeadAClinica → _upsertContatoB2B).
var _CRM_CONTATOS_B2B_CABECALHO = [
  "contato_id",
  "clinica_id",
  "nome_contato",
  "cargo",
  "telefone_whatsapp",
  "email",
  "papel",
  "status_relacionamento",
  "ultima_interacao",
  "proxima_acao",
  "data_proxima_acao",
  "responsavel",
  "observacao",
];

// Cabeçalhos de fallback para "Leads Convertidos" e "Log Conversões Leads" —
// usados apenas se essas abas ainda não existirem (não deveria acontecer, pois
// já são criadas por converter-lead-em-paciente.gs). Espelham exatamente
// _CV_LEADS_CABECALHO e _CV_HEADERS_LOG daquele arquivo.
var _B2B_LEADS_CONVERTIDOS_CABECALHO_FALLBACK = [
  "lead_id", "data_contato", "nome", "telefone_whatsapp", "servico_interesse",
  "origem", "etapa", "responsavel", "proxima_acao", "data_proxima_acao", "observacao",
];
var _B2B_LOG_CONVERSOES_CABECALHO_FALLBACK = [
  "data_hora", "lead_id", "nome", "etapa", "destinos_criados", "resultado", "followup_status",
];

// ── Organização visual da planilha (organizarAbasCommandCenter) ───────────────

// Abas operacionais — devem ficar visíveis, com cabeçalho destacado, linha 1
// congelada e filtro básico. Nomes literais (não fazem parte de _SHEETS
// porque não são lidas/escritas por este arquivo, só organizadas visualmente).
var _ABAS_OPERACIONAIS = [
  _SHEETS.LEADS,
  _SHEETS.CRM_CLINICAS,
  _SHEETS.CRM_CONTATOS_B2B,
  _SHEETS.PACIENTES,
  _SHEETS.CONSULTAS,
  _SHEETS.ESPIROMETRIA,
  _SHEETS.FOLLOWUP_WA,
  "Tarefas",
  // M14.2: a antiga aba "Financeiro" foi removida da planilha — a aba
  // financeira oficial é Financeiro_Lancamentos (fonte financeira única).
  _SHEETS.FINANCEIRO_LANCAMENTOS,
  "Marketing Conteúdo",
];

// Abas técnicas — podem ficar ocultas depois de estáveis (auditoria/consolidação,
// não uso operacional do dia a dia). organizarAbasCommandCenter só oculta se
// dryRun=false, e sempre relata a ação no retorno.
var _ABAS_TECNICAS_OCULTAVEIS = [
  _SHEETS.LOG_CONVERSOES,
  _SHEETS.LEADS_CONVERTIDOS,
  "Resumo Dashboard",
];

// ── Entry point ───────────────────────────────────────────────────────────────

function doPost(e) {
  try {
    var token = PropertiesService.getScriptProperties().getProperty("API_TOKEN");
    if (!token) {
      return _err("API_TOKEN não configurado nas Propriedades do Script.", 500);
    }

    var body;
    try {
      body = JSON.parse(e.postData.contents);
    } catch (_) {
      return _err("Corpo da requisição inválido — JSON malformado.", 400);
    }

    if (!body.token || body.token !== token) {
      _logEntry({ acao: "autenticacao", status: "NEGADO", aba: "", id: "", resumo: "Token inválido." });
      return _err("Token inválido.", 401);
    }

    var action = String(body.action || "").trim();
    var data   = body.data || {};

    switch (action) {
      case "createLead":                  return _createLead(data);
      case "createPaciente":              return _createPaciente(data);
      case "createEspirometria":          return _createEspirometria(data);
      case "createConsulta":              return _createConsulta(data);
      case "createClinicaB2B":            return _createClinicaB2B(data);
      case "registrarInteracaoClinica":   return _registrarInteracaoClinica(data);
      case "registrarInteracaoPaciente":  return _registrarInteracaoPaciente(data);
      case "updateLeadStage":             return _updateLeadStage(data);
      case "updateCrmClinicaEtapa":       return _updateCrmClinicaEtapa(data);
      case "vincularLeadAClinica":        return _vincularLeadAClinica(data);
      case "dedupLeadsConvertidos":       return _dedupLeadsConvertidos(data);
      case "organizarAbasCommandCenter":  return _organizarAbasCommandCenter(data);
      case "registrarAtendimentoPastore":
        // Writer de STAGING isolado em pastore-staging.gs (M14.3A) — a aba
        // Pastore não é base canônica de pessoas nem de valores. Sem o
        // arquivo instalado, a ação falha explicitamente (fail-closed).
        if (typeof _registrarAtendimentoPastore !== "function") {
          return _err("pastore-staging.gs não está instalado neste projeto — ação indisponível.", 500);
        }
        return _registrarAtendimentoPastore(data);
      case "registrarEspirometriaFinanceiro": return _registrarEspirometriaFinanceiro(data);
      default:
        return _err("Ação desconhecida: " + action, 400);
    }
  } catch (ex) {
    _logEntry({ acao: "erro-interno", status: "ERRO", aba: "", id: "", resumo: ex.message });
    return _err("Erro interno: " + ex.message, 500);
  }
}

// ── Ações de escrita ──────────────────────────────────────────────────────────

function _createLead(data) {
  _required(data, ["nome", "responsavel"]);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var sheet = _getOrCreateSheet(_SHEETS.LEADS);
  // M14.3A — ID emitido pelo servidor (UUID opaco); nunca lastRow/contador.
  var id    = ctNovoIdServidor("LEAD", Utilities.getUuid());

  try {
    _appendRowSemValidacao(sheet, _buildRow(sheet, {
      lead_id:           id,
      data_contato:      _nowBr(),
      nome:              data.nome              || "",
      telefone_whatsapp: data.telefone_whatsapp || "",
      servico_interesse: data.servico_interesse || "",
      origem:            data.origem            || "",
      etapa:             data.etapa             || "Novo contato",
      responsavel:       data.responsavel       || "",
      proxima_acao:      data.proxima_acao      || "",
      data_proxima_acao: data.data_proxima_acao || "",
      observacao:        data.observacao        || "",
    }));
  } catch (e) {
    _auditAcao(auditRequestId, auditT0, "create_lead", "lead", id, data, "ERROR: falha na gravacao");
    throw e;
  }

  // telefone_whatsapp e observacao nunca são registrados no log
  _logEntry({ acao: "createLead", status: "OK", aba: _SHEETS.LEADS, id: id, resumo: "Lead registrado." });
  _auditAcao(auditRequestId, auditT0, "create_lead", "lead", id, data, "ok");
  return _ok({ id: id, message: "Lead registrado com sucesso." });
}

function _createPaciente(data) {
  _required(data, ["primeiro_nome", "responsavel"]);
  if (!_ctDisponivel()) return _err(_ERRO_CONTRATOS_AUSENTES, 500);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var sheet   = _getOrCreateSheet(_SHEETS.PACIENTES);
  // M14.3A — CRM Pacientes é MESTRE PERSISTENTE: esta ação só ADICIONA uma
  // linha; nunca reescreve, deduplica ou funde cadastros (nome nunca vincula;
  // telefone é no máximo candidato — decisões de fusão são humanas, fora
  // desta API). ID emitido pelo servidor (UUID opaco).
  var id      = ctNovoIdServidor("PAC", Utilities.getUuid());
  var consent = _normalizeConsent(data.consentimento_whatsapp);

  var campos = {
    paciente_id:            id,
    data_cadastro:          _nowBr(),
    primeiro_nome:          data.primeiro_nome              || "",
    telefone:               data.telefone                   || "",
    // M14.3 — aceita ultimo_servico explícito; mantém o fallback histórico
    // (origem) para não mudar o comportamento de chamadas antigas.
    ultimo_servico:         data.ultimo_servico || data.origem || "",
    status_relacionamento:  data.status_relacionamento      || "Ativo",
    proximo_contato:        data.proximo_contato            || "",
    motivo_proximo_contato: "",
    canal:                  data.canal                      || "",
    responsavel:            data.responsavel                || "",
    consentimento_whatsapp: consent,
  };
  if (data.id_legado) campos.id_legado = String(data.id_legado).trim();
  if (data.data_cadastro_precisao) campos.data_cadastro_precisao = String(data.data_cadastro_precisao).trim();

  var solicitados = _camposSolicitados(data, CT_REGISTROS.crm_pacientes.canonicos_opcionais);
  var headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
  var plano = ctPlanejarUpsert("crm_pacientes", headers, campos, solicitados, null);
  if (!plano.ok) {
    _auditAcao(auditRequestId, auditT0, "create_paciente", "paciente", id, data, "ERROR: contrato/cabecalho");
    return _err(plano.erro, 422);
  }

  try {
    _aplicarPlanoUpsert(sheet, plano, -1);
  } catch (e) {
    _auditAcao(auditRequestId, auditT0, "create_paciente", "paciente", id, data, "ERROR: falha na gravacao");
    throw e;
  }

  _logEntry({ acao: "createPaciente", status: "OK", aba: _SHEETS.PACIENTES, id: id, resumo: "Paciente cadastrado." });
  _auditAcao(auditRequestId, auditT0, "create_paciente", "paciente", id, data, "ok");
  return _ok({
    id:                 id,
    campos_persistidos: plano.persistidos,
    campos_ignorados:   plano.ignorados,
    contrato_versao:    plano.contrato_versao,
    versao_cabecalho:   plano.versao_cabecalho,
    message:            "Paciente criado com sucesso.",
  });
}

// ── Writers fail-closed sobre os contratos executáveis (M14.3A) ─────────────
// As funções ct* vêm de contratos-canonicos.gs (mesmo projeto Apps Script).
// Sem ele instalado, TODA gravação falha explicitamente — nunca sucesso
// silencioso com contrato ausente.

function _ctDisponivel() {
  return typeof ctPlanejarUpsert === "function" &&
         typeof ctValidarDataComPrecisao === "function" &&
         typeof ctNovoIdServidor === "function";
}

var _ERRO_CONTRATOS_AUSENTES =
  "contratos-canonicos.gs não está instalado neste projeto Apps Script — " +
  "gravação bloqueada (fail-closed). Instale o arquivo e tente de novo.";

// Campos canônicos que o CLIENTE pediu com valor não vazio — se a coluna não
// existir na aba, a gravação falha com a lista das colunas ausentes.
function _camposSolicitados(data, canonicos) {
  return canonicos.filter(function(c) {
    return data[c] !== undefined && data[c] !== null && String(data[c]).trim() !== "";
  });
}

// Aplica um plano de ctPlanejarUpsert: insert = linha nova (escrita agrupada
// única via setValues); patch = escreve as células planejadas (colunas fora
// do payload, fórmulas e colunas desconhecidas ficam intocadas).
//
// AVISO HONESTO DE ATOMICIDADE (M14.3A, correção final): o INSERT é uma única
// chamada setValues — na prática atômico (ou a linha inteira entra, ou a
// chamada falha e nada é gravado). O PATCH não é: o Google Sheets não oferece
// transação nativa entre células/colunas não contíguas, e reescrever a linha
// inteira apagaria fórmulas de colunas vizinhas não solicitadas pelo payload
// — por isso cada célula do patch é gravada e verificada (lida de volta) uma
// a uma. Se uma célula no meio da lista falhar (ex.: validação de dropdown da
// planilha), as anteriores JÁ ficam persistidas: não existe rollback
// automático nesta fase. TODA validação de contrato/cabeçalho já aconteceu
// antes desta função (ver ctPlanejarUpsert) — o que resta é o risco residual,
// documentado aqui, de a própria planilha rejeitar uma célula específica no
// meio da sequência. O erro nunca é silencioso: relata exatamente o que
// persistiu (camposGravadosParcialmente) e o que falhou (camposComFalha),
// para compensação manual ou automatizada na M14.3B.
function _aplicarPlanoUpsert(sheet, plano, rowIdxExistente) {
  if (plano.modo === "insert") {
    _appendRowSemValidacao(sheet, plano.linha);
    return;
  }
  var gravadas = [];
  var failures = [];
  plano.celulas.forEach(function(c) {
    if (_writeCellVerified(sheet, rowIdxExistente, c.indice + 1, c.valor)) {
      gravadas.push(c.coluna);
    } else {
      failures.push(c.coluna);
    }
  });
  if (failures.length > 0) {
    var erro = new Error(
      "Falha ao gravar campos: " + failures.join(", ") +
      (gravadas.length ? " | ATENÇÃO — já persistidos nesta tentativa (revisar linha " +
        rowIdxExistente + "): " + gravadas.join(", ") : "") +
      " | sem rollback automático nesta fase (M14.3A) — compensação manual ou M14.3B.");
    erro.camposGravadosParcialmente = gravadas;
    erro.camposComFalha = failures;
    throw erro;
  }
}

// M14.3A (2ª rodada) — helpers dos writers de eventos clínicos.
//
// IDENTIDADE × IDEMPOTÊNCIA: o ID canônico (exame_id/consulta_id) é SEMPRE
// UUID emitido pelo servidor. A chave de idempotência do cliente (quando
// enviada) vai para a coluna técnica idempotency_key (criada sob demanda) e
// serve apenas para replay/conflito:
//   mesma chave + mesmo payload   → replay (mesmo resultado, nada regravado);
//   mesma chave + payload difere  → conflito 409 (nada gravado);
//   sem chave                     → insert simples.
// Não existe caminho de PATCH nesses writers: corrigir um evento já gravado
// será uma ação explícita futura (M14.3B), nunca um reenvio de formulário.
//
// LOCK: busca + validação + escrita rodam sob LockService — duas requisições
// simultâneas com a mesma chave convergem para UMA linha.

function _lockScriptOuErro() {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(15000);
    return lock;
  } catch (e) {
    return null;
  }
}

// Insere um evento clínico (espirometria/consulta) com semântica de
// idempotência. Retorna o objeto de resposta (_ok/_err) pronto.
function _inserirEventoClinicoIdempotente(opts) {
  var contrato = CT_REGISTROS[opts.contrato];

  var chaveRes = ctChaveIdempotenciaAcao(opts.chaveBruta, contrato.prefixo);
  if (!chaveRes.ok) return _err(chaveRes.erro, 400);
  var chave = chaveRes.chave;

  var lock = _lockScriptOuErro();
  if (!lock) return _err("Sistema ocupado (lock não obtido) — tente novamente.", 503);

  try {
    // M14.3A (correção final) — operação diária NUNCA cria aba nem cabeçalho.
    // Aba ausente é erro fail-closed (nada foi gravado); instalação de
    // estrutura é ação explícita separada (fora deste writer).
    var sheet = _getSheetSemCriar(opts.aba);
    if (!sheet) {
      return _err(
        "Aba '" + opts.aba + "' não existe. A operação diária não cria abas " +
        "automaticamente (fail-closed) — use o instalador explícito para " +
        "configurar a estrutura antes de registrar " + opts.rotulo.toLowerCase() +
        ". Nada foi gravado.", 422);
    }

    var headers = sheet.getLastRow() === 0
      ? []
      : sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
    var cab = ctValidarCabecalho(opts.contrato, headers);
    if (!cab.ok) return _err(cab.erro, 422);

    // Fail-closed TOTAL: qualquer campo presente no payload (v1 ou v2) sem
    // coluna correspondente → erro ANTES de qualquer escrita (inclusive antes
    // da criação das colunas técnicas de idempotência).
    var presentes = ctCamposPresentes(opts.contrato, opts.payloadOriginal);
    var fingerprint = ctFingerprintPayload(opts.camposNegocio);

    var preCheck = ctPlanejarEscrita(opts.contrato, headers, opts.camposNegocio, presentes);
    if (!preCheck.ok) return _err(preCheck.erro, 422);

    if (chave) {
      // Replay/conflito: procura pela coluna técnica e, para linhas gravadas
      // antes da M14.3A (chave era o próprio id), pela coluna de id.
      var existente = _findRowByIdColumn(sheet, "idempotency_key", chave) ||
                      _findRowByIdColumn(sheet, opts.idCol, chave);
      if (existente) {
        var fpIdx = existente.headers.indexOf("idempotency_fingerprint");
        var fpArmazenado = fpIdx >= 0 ? String(existente.rowData[fpIdx] || "").trim() : "";
        var fpLinha = fpArmazenado ||
          ctFingerprintDaLinha(existente.headers, existente.rowData, Object.keys(opts.camposNegocio).sort());
        if (fpLinha !== fingerprint) {
          return _err(
            "Conflito de idempotência (409): esta chave já foi usada com um payload DIFERENTE. " +
            "Nada foi gravado. Para registrar outro evento, gere uma nova chave; para corrigir um " +
            "registro existente, use a ação de edição explícita (M14.3B).", 409);
        }
        var idIdx = existente.headers.indexOf(opts.idCol);
        var idExistente = idIdx >= 0 ? String(existente.rowData[idIdx] || "").trim() : "";
        return _ok({
          id: idExistente, replayed: true, created: false, updated: false,
          campos_persistidos: [], campos_ignorados: [],
          contrato_versao: CT_CONTRATOS_VERSAO, versao_cabecalho: cab.versao,
          message: opts.rotulo + " já registrada com esta chave — reenvio idempotente, nada regravado.",
        });
      }
      // Colunas técnicas criadas sob demanda SÓ depois de validar o payload
      // (nenhuma mutação antes da validação completa).
      headers = _ensureExtraColumns(sheet, contrato.colunas_tecnicas);
    }

    var id = ctNovoIdServidor(contrato.prefixo, Utilities.getUuid());
    var campos = {};
    Object.keys(opts.camposNegocio).forEach(function(k) { campos[k] = opts.camposNegocio[k]; });
    campos[opts.idCol] = id;
    campos.data_entrada = _nowBr();
    if (chave) {
      campos.idempotency_key = chave;
      campos.idempotency_fingerprint = fingerprint;
    }

    var solicitados = presentes.concat(chave ? contrato.colunas_tecnicas : []);
    var plano = ctPlanejarUpsert(opts.contrato, headers, campos, solicitados, null);
    if (!plano.ok) return _err(plano.erro, 422);

    _aplicarPlanoUpsert(sheet, plano, -1);

    return _ok({
      id: id, created: true, updated: false, replayed: false,
      campos_persistidos: plano.persistidos, campos_ignorados: plano.ignorados,
      contrato_versao: plano.contrato_versao, versao_cabecalho: plano.versao_cabecalho,
      message: opts.rotulo + " registrada com sucesso.",
    });
  } finally {
    lock.releaseLock();
  }
}

function _createEspirometria(data) {
  _required(data, ["primeiro_nome", "responsavel"]);
  if (!_ctDisponivel()) return _err(_ERRO_CONTRATOS_AUSENTES, 500);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  // Validação COMPLETA do payload antes de qualquer acesso/mutação de aba.
  // Datas: valor + precisão juntos — mês/ano nunca vira dia; vazio nunca
  // vira hoje; inválido/incoerente é erro explícito.
  var dataExame = ctValidarDataComPrecisao(data.data_exame, data.data_exame_precisao);
  if (!dataExame.ok) return _err("data_exame: " + dataExame.erro, 400);

  if (Object.prototype.hasOwnProperty.call(data, "local_atendimento") &&
      String(data.local_atendimento || "").trim() &&
      _EF_LOCAL_ATENDIMENTO.indexOf(String(data.local_atendimento).trim()) < 0) {
    return _err("local_atendimento inválido: " + String(data.local_atendimento).trim(), 400);
  }

  // Campos de negócio: SÓ o que o payload realmente enviou (own property).
  // Defaults de INSERT ficam explícitos e mínimos: servico/status quando o
  // cliente não mandou (registro novo precisa deles); nada é inventado em
  // cima de dado existente porque este writer nunca faz patch.
  var camposNegocio = {};
  ["primeiro_nome", "telefone", "servico", "origem", "status_exame",
   "proximo_contato", "motivo_proximo_contato", "canal", "responsavel",
   "paciente_id", "id_legado", "local_atendimento", "parceiro", "unidade",
   "modalidade"].forEach(function(c) {
    if (Object.prototype.hasOwnProperty.call(data, c)) {
      camposNegocio[c] = String(data[c] === null || data[c] === undefined ? "" : data[c]).trim();
    }
  });
  if (Object.prototype.hasOwnProperty.call(data, "data_exame")) {
    camposNegocio.data_exame = dataExame.valor;
    if (dataExame.precisao !== "desconhecida") camposNegocio.data_exame_precisao = dataExame.precisao;
  }
  if (Object.prototype.hasOwnProperty.call(data, "consentimento_whatsapp")) {
    camposNegocio.consentimento_whatsapp = _normalizeConsent(data.consentimento_whatsapp);
  }
  // M14.3A (correção final) — default de INSERT só quando o campo NÃO foi
  // enviado (própria ausência em camposNegocio, refletindo hasOwnProperty em
  // data); status_exame:"" explícito nunca vira "Aguardando" silenciosamente.
  if (!Object.prototype.hasOwnProperty.call(camposNegocio, "servico")) camposNegocio.servico = "Espirometria";
  if (!Object.prototype.hasOwnProperty.call(camposNegocio, "status_exame")) camposNegocio.status_exame = "Aguardando";

  var resposta = _inserirEventoClinicoIdempotente({
    contrato: "crm_espirometria",
    aba: _SHEETS.ESPIROMETRIA,
    idCol: "exame_id",
    rotulo: "Espirometria",
    chaveBruta: data.idempotency_key || data.id_atendimento,
    payloadOriginal: data,
    camposNegocio: camposNegocio,
  });

  var corpo = JSON.parse(resposta.getContent());
  _logEntry({
    acao:   "createEspirometria",
    status: corpo.ok ? "OK" : "ERRO",
    aba:    _SHEETS.ESPIROMETRIA,
    id:     corpo.id || "",
    resumo: corpo.ok
      ? (corpo.replayed ? "Reenvio idempotente — nada regravado." : "Espirometria registrada.")
      : ("Recusado: " + String(corpo.error || "").slice(0, 120)),
  });
  _auditAcao(auditRequestId, auditT0, "create_espirometria", "espirometria",
             corpo.id || "", data,
             corpo.ok ? (corpo.replayed ? "ok: replay" : "ok: insert") : ("ERROR: " + (corpo.code || "")));
  return resposta;
}

function _createConsulta(data) {
  _required(data, ["primeiro_nome", "responsavel"]);
  if (!_ctDisponivel()) return _err(_ERRO_CONTRATOS_AUSENTES, 500);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var dataConsulta = ctValidarDataComPrecisao(data.data_consulta, data.data_consulta_precisao);
  if (!dataConsulta.ok) return _err("data_consulta: " + dataConsulta.erro, 400);

  var camposNegocio = {};
  ["primeiro_nome", "telefone", "origem", "tipo_consulta", "status", "medica",
   "proximo_contato", "motivo_proximo_contato", "canal", "responsavel",
   "paciente_id", "id_legado", "modalidade"].forEach(function(c) {
    if (Object.prototype.hasOwnProperty.call(data, c)) {
      camposNegocio[c] = String(data[c] === null || data[c] === undefined ? "" : data[c]).trim();
    }
  });
  if (Object.prototype.hasOwnProperty.call(data, "data_consulta")) {
    camposNegocio.data_consulta = dataConsulta.valor;
    if (dataConsulta.precisao !== "desconhecida") camposNegocio.data_consulta_precisao = dataConsulta.precisao;
  }
  if (Object.prototype.hasOwnProperty.call(data, "consentimento_whatsapp")) {
    camposNegocio.consentimento_whatsapp = _normalizeConsent(data.consentimento_whatsapp);
  }
  // M14.3A (correção final) — default de INSERT só quando o campo NÃO foi
  // enviado; status:"" explícito nunca vira "Agendada" silenciosamente.
  if (!Object.prototype.hasOwnProperty.call(camposNegocio, "status")) camposNegocio.status = "Agendada";

  var resposta = _inserirEventoClinicoIdempotente({
    contrato: "crm_consultas",
    aba: _SHEETS.CONSULTAS,
    idCol: "consulta_id",
    rotulo: "Consulta",
    chaveBruta: data.idempotency_key || data.id_atendimento,
    payloadOriginal: data,
    camposNegocio: camposNegocio,
  });

  var corpo = JSON.parse(resposta.getContent());
  _logEntry({
    acao:   "createConsulta",
    status: corpo.ok ? "OK" : "ERRO",
    aba:    _SHEETS.CONSULTAS,
    id:     corpo.id || "",
    resumo: corpo.ok
      ? (corpo.replayed ? "Reenvio idempotente — nada regravado." : "Consulta registrada.")
      : ("Recusado: " + String(corpo.error || "").slice(0, 120)),
  });
  _auditAcao(auditRequestId, auditT0, "create_consulta", "consulta",
             corpo.id || "", data,
             corpo.ok ? (corpo.replayed ? "ok: replay" : "ok: insert") : ("ERROR: " + (corpo.code || "")));
  return resposta;
}

function _createClinicaB2B(data) {
  _required(data, ["nome_clinica", "status"]);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var pcmsoSheet = _getOrCreateSheet(_SHEETS.B2B_PCMSO);
  var crmSheet   = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var crmId      = ctNovoIdServidor("CLI", Utilities.getUuid()); // M14.3A: servidor emite; nunca lastRow

  // Uma ação de negócio (cadastro da clínica), duas abas escritas —
  // um único evento de auditoria, identificado pelo clinica_id do CRM.
  try {
    // Escreve na aba PCMSO (prospecção)
    _appendRowSemValidacao(pcmsoSheet, _buildRow(pcmsoSheet, {
      "Nome da clínica/empresa": data.nome_clinica       || "",
      "Tipo":                    data.tipo_clinica        || "",
      "Bairro/Região":           _joinNotEmpty(data.bairro, data.regiao),
      "Telefone/WhatsApp":       data.telefone_whatsapp   || "",
      "Pessoa de contato":       "",
      "Origem da lista":         data.origem              || "",
      "Status":                  data.status              || "",
      "Interesse":               data.interesse           || "",
      "Próximo passo":           data.proximo_passo       || "",
      "Data do próximo passo":   data.data_proximo_passo  || "",
      "Observações":             "",
    }));

    // Escreve também no CRM Clinicas
    _appendRowSemValidacao(crmSheet, _buildRow(crmSheet, {
      clinica_id:        crmId,
      nome_clinica:      data.nome_clinica      || "",
      bairro:            data.bairro            || "",
      regiao:            data.regiao            || "",
      tipo_clinica:      data.tipo_clinica      || "",
      etapa:             data.status            || "Não abordado",
      ultima_interacao:  _nowBr(),
      proxima_acao:      data.proximo_passo     || "",
      data_proxima_acao: data.data_proximo_passo || "",
      responsavel:       data.responsavel       || "",
      prioridade:        data.prioridade        || "Normal",
      observacao:        "",
    }));
  } catch (e) {
    _auditAcao(auditRequestId, auditT0, "create_clinica_b2b", "clinica", crmId, data, "ERROR: falha na gravacao");
    throw e;
  }

  _logEntry({ acao: "createClinicaB2B", status: "OK", aba: _SHEETS.B2B_PCMSO + " + " + _SHEETS.CRM_CLINICAS, id: crmId, resumo: "Clínica B2B cadastrada." });
  _auditAcao(auditRequestId, auditT0, "create_clinica_b2b", "clinica", crmId, data, "ok");
  return _ok({ id: crmId, message: "Clínica cadastrada com sucesso." });
}

function _registrarInteracaoClinica(data) {
  _required(data, ["nome_clinica", "etapa", "responsavel"]);
  if (!_ctDisponivel()) return _err(_ERRO_CONTRATOS_AUSENTES, 500);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var sheet   = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var lastRow = sheet.getLastRow();
  if (lastRow < 1) throw new Error("Aba " + _SHEETS.CRM_CLINICAS + " sem cabeçalho.");
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });
  var nomeIdx = headers.indexOf("nome_clinica");
  if (nomeIdx < 0) throw new Error("Coluna nome_clinica não encontrada.");

  var keyTarget = _keyNorm(data.nome_clinica);
  var rowIdx = -1;
  if (lastRow >= 2) {
    var allData = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
    for (var i = 0; i < allData.length; i++) {
      if (_keyNorm(String(allData[i][nomeIdx] || "")) === keyTarget) { rowIdx = i + 2; break; }
    }
  }

  // M14.3A (correção final) — contrato executável "crm_clinicas": fail-closed
  // TOTAL (qualquer campo suportado e enviado sem coluna correspondente gera
  // erro ANTES de qualquer setValue, nunca sucesso parcial) + patch POR PRESENÇA
  // REAL da propriedade (vazio explícito ≠ ausência). etapa e ultima_interacao
  // são a regra formal desta ação (sempre aplicadas); demais campos entram
  // somente se o cliente realmente os enviou.
  var campos = { etapa: String(data.etapa || "").trim(), ultima_interacao: _nowBr() };
  var solicitados = ["etapa", "ultima_interacao"];
  ["proxima_acao", "data_proxima_acao", "prioridade", "responsavel"].forEach(function(c) {
    if (Object.prototype.hasOwnProperty.call(data, c)) {
      campos[c] = String(data[c] === null || data[c] === undefined ? "" : data[c]).trim();
      solicitados.push(c);
    }
  });

  var linhaExistente = rowIdx > 0 ? sheet.getRange(rowIdx, 1, 1, lastCol).getValues()[0] : null;
  var clinicaIdAudit = "";
  var newId = "";
  if (!linhaExistente) {
    newId = ctNovoIdServidor("CLI", Utilities.getUuid());
    campos.clinica_id   = newId;
    campos.nome_clinica = String(data.nome_clinica || "").trim();
    solicitados = solicitados.concat(["clinica_id", "nome_clinica"]);
  } else {
    var idIdx = headers.indexOf("clinica_id");
    clinicaIdAudit = idIdx >= 0 ? String(linhaExistente[idIdx] || "").trim() : "";
  }

  // Validação COMPLETA antes de qualquer escrita: campo solicitado sem coluna
  // correspondente retorna erro aqui — nenhuma célula foi tocada ainda.
  var plano = ctPlanejarUpsert("crm_clinicas", headers, campos, solicitados, linhaExistente);
  if (!plano.ok) {
    _auditAcao(auditRequestId, auditT0, "registrar_interacao_clinica", "clinica", clinicaIdAudit, data, "ERROR: " + plano.erro);
    return _err(plano.erro, 422);
  }

  try {
    _aplicarPlanoUpsert(sheet, plano, rowIdx > 0 ? rowIdx : -1);
  } catch (e) {
    _logEntry({ acao: "registrarInteracaoClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.CRM_CLINICAS, id: clinicaIdAudit || newId, resumo: e.message });
    _auditAcao(auditRequestId, auditT0, "registrar_interacao_clinica", "clinica", clinicaIdAudit || newId, data, "ERROR: falha na gravacao");
    // Escrita parcial residual (M14.3A): nunca escondida — relata exatamente
    // o que persistiu e o que falhou (ver _aplicarPlanoUpsert).
    return _err(e.message, 500, {
      campos_gravados_parcialmente: e.camposGravadosParcialmente || [],
      campos_com_falha: e.camposComFalha || [],
    });
  }

  if (linhaExistente) {
    _logEntry({ acao: "registrarInteracaoClinica", status: "OK", aba: _SHEETS.CRM_CLINICAS, id: clinicaIdAudit, resumo: "Interação atualizada." });
    _auditAcao(auditRequestId, auditT0, "registrar_interacao_clinica", "clinica", clinicaIdAudit, data, "ok: update");
    return _ok({
      message: "Interação registrada com sucesso.", updated: true,
      campos_persistidos: plano.persistidos, campos_ignorados: plano.ignorados,
      contrato_versao: plano.contrato_versao,
    });
  }

  _logEntry({ acao: "registrarInteracaoClinica", status: "OK-NOVO", aba: _SHEETS.CRM_CLINICAS, id: newId, resumo: "Nova clínica criada via interação." });
  _auditAcao(auditRequestId, auditT0, "registrar_interacao_clinica", "clinica", newId, data, "ok: insert");
  return _ok({
    id: newId, message: "Clínica não encontrada — nova entrada criada.", created: true,
    campos_persistidos: plano.persistidos, campos_ignorados: plano.ignorados,
    contrato_versao: plano.contrato_versao,
  });
}

function _registrarInteracaoPaciente(data) {
  _required(data, ["primeiro_nome", "tipo_mensagem", "responsavel"]);

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var sheet   = _getOrCreateSheet(_SHEETS.FOLLOWUP_WA);
  var id      = ctNovoIdServidor("FUP", Utilities.getUuid());
  var consent = _normalizeConsent(data.consentimento);

  try {
    _appendRowSemValidacao(sheet, _buildRow(sheet, {
      followup_id:    id,
      data_criacao:   _nowBr(),
      primeiro_nome:  data.primeiro_nome  || "",
      telefone:       data.telefone       || "",
      tipo_mensagem:  data.tipo_mensagem  || "",
      data_prevista:  data.data_prevista  || "",
      status:         "Pendente",
      canal:          data.canal          || "WhatsApp",
      responsavel:    data.responsavel    || "",
      template_usado: data.template_usado || "",
      consentimento:  consent,
    }));
  } catch (e) {
    _auditAcao(auditRequestId, auditT0, "registrar_interacao_paciente", "followup", id, data, "ERROR: falha na gravacao");
    throw e;
  }

  _logEntry({ acao: "registrarInteracaoPaciente", status: "OK", aba: _SHEETS.FOLLOWUP_WA, id: id, resumo: "Follow-up registrado." });
  _auditAcao(auditRequestId, auditT0, "registrar_interacao_paciente", "followup", id, data, "ok");
  return _ok({ id: id, message: "Interação registrada." });
}


// Valor monetário: vazio -> fallback (ou erro se obrigatório); qualquer coisa
// que não vire número finito >= 0 -> erro. Mesma regra de
// js/espirometria-financeiro.js (_efNum) — o servidor não confia no cliente.
function _efServerNum(raw, opts) {
  opts = opts || {};
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    if (opts.obrigatorio) return { erro: true };
    return { valor: opts.fallback !== undefined ? opts.fallback : null };
  }
  var n = Number(String(raw).replace(",", "."));
  if (!isFinite(n) || n < 0) return { erro: true };
  return { valor: Math.round(n * 100) / 100 };
}

/**
 * Registra (ou atualiza, via upsert por id_atendimento) um lançamento
 * financeiro seguro da Nova Espirometria na aba "Financeiro_Lancamentos".
 * Usado pelo botão "Salvar lançamento financeiro" do painel, depois que
 * buildEspirometriaFinanceiroPayload (front-end) já validou o payload.
 *
 * Nunca confia apenas na validação do front-end: revalida aqui todos os
 * campos obrigatórios e os enums fechados, e RECALCULA (nunca só aceita)
 * desconto e valor_recebido a partir das mesmas regras de negócio de
 * js/espirometria-financeiro.js — um payload adulterado no meio do caminho
 * não pode fazer um exame Cancelado/Pendente/Cortesia virar receita.
 *
 * Rejeita de forma fechada (default-fechado) qualquer payload que contenha
 * um dos campos de _EF_CAMPOS_PROIBIDOS, mesmo vazio — este lançamento é
 * financeiro-only, nunca carrega identificação do paciente.
 *
 * data esperado: mesmo shape do payload de buildEspirometriaFinanceiroPayload
 * (data_exame, status_exame, status_pagamento, valor_tabela, valor_cobrado,
 * valor_recebido, forma_pagamento, origem_preco, local_atendimento,
 * observacao_financeira, id_atendimento opcional) + servico/fonte fixos.
 */
function _registrarEspirometriaFinanceiro(data) {
  data = data || {};
  if (!_ctDisponivel()) return _err(_ERRO_CONTRATOS_AUSENTES, 500);

  var camposProibidosPresentes = _EF_CAMPOS_PROIBIDOS.filter(function(campo) {
    return Object.prototype.hasOwnProperty.call(data, campo);
  });
  if (camposProibidosPresentes.length > 0) {
    return _err("Campo(s) não permitido(s) em lançamento financeiro: " + camposProibidosPresentes.join(", "), 400);
  }

  _required(data, ["data_exame", "status_exame", "status_pagamento", "valor_cobrado", "local_atendimento"]);

  // M14.3A (2ª rodada) — id_atendimento é OBRIGATÓRIO e validado por ação:
  // nenhum lançamento novo pode nascer órfão pela própria API. É chave de
  // NEGÓCIO (vínculo com o exame), não idempotency key técnica: atualizar o
  // pagamento do mesmo atendimento é o fluxo desenhado (patch por presença).
  var chaveRes = ctChaveIdempotenciaAcao(data.id_atendimento, "ESP");
  if (!chaveRes.ok) return _err("id_atendimento: " + chaveRes.erro, 400);
  var idAtendimento = chaveRes.chave;
  if (!idAtendimento) {
    return _err("id_atendimento é obrigatório — lançamento financeiro sem vínculo com o atendimento não pode ser criado.", 400);
  }

  // Datas: valor + precisão validados juntos — inválido é erro explícito;
  // mês/ano nunca vira dia factual; vazio nunca vira hoje.
  var dataExameFin = ctValidarDataComPrecisao(data.data_exame, data.data_exame_precisao);
  if (!dataExameFin.ok) return _err("data_exame: " + dataExameFin.erro, 400);

  var statusExame = String(data.status_exame).trim();
  if (_EF_STATUS_EXAME.indexOf(statusExame) < 0) {
    return _err("status_exame inválido: " + statusExame, 400);
  }

  var statusPagamento = String(data.status_pagamento).trim();
  if (_EF_STATUS_PAGAMENTO.indexOf(statusPagamento) < 0) {
    return _err("status_pagamento inválido: " + statusPagamento, 400);
  }

  var localAtendimento = String(data.local_atendimento).trim();
  if (_EF_LOCAL_ATENDIMENTO.indexOf(localAtendimento) < 0) {
    return _err("local_atendimento inválido: " + localAtendimento, 400);
  }

  var formaPagamento = data.forma_pagamento ? String(data.forma_pagamento).trim() : "";
  if (formaPagamento && _EF_FORMA_PAGAMENTO.indexOf(formaPagamento) < 0) {
    return _err("forma_pagamento inválida: " + formaPagamento, 400);
  }

  // AUSÊNCIA PERMANECE AUSENTE (M14.3A, 2ª rodada): nenhum default monetário
  // é inventado — valor_tabela ausente NÃO vira 250; origem_preco ausente NÃO
  // vira "Tabela"; valor_recebido ausente NÃO vira 0 silencioso.
  var origemPreco = "";
  if (Object.prototype.hasOwnProperty.call(data, "origem_preco") && String(data.origem_preco || "").trim()) {
    origemPreco = String(data.origem_preco).trim();
    if (_EF_ORIGEM_PRECO.indexOf(origemPreco) < 0) {
      return _err("origem_preco inválida: " + origemPreco, 400);
    }
  }

  var valorTabela = { valor: null };
  if (Object.prototype.hasOwnProperty.call(data, "valor_tabela") && String(data.valor_tabela === null ? "" : data.valor_tabela).trim() !== "") {
    valorTabela = _efServerNum(data.valor_tabela, { obrigatorio: true });
    if (valorTabela.erro) return _err("valor_tabela inválido.", 400);
  }

  var valorCobrado = _efServerNum(data.valor_cobrado, { obrigatorio: true });
  if (valorCobrado.erro) return _err("valor_cobrado inválido.", 400);

  var temValorRecebido = Object.prototype.hasOwnProperty.call(data, "valor_recebido") &&
                         String(data.valor_recebido === null ? "" : data.valor_recebido).trim() !== "";
  var valorRecebidoInformado = { valor: null };
  if (temValorRecebido) {
    valorRecebidoInformado = _efServerNum(data.valor_recebido, { obrigatorio: true });
    if (valorRecebidoInformado.erro) return _err("valor_recebido inválido.", 400);
  }

  // Regras cruzadas por status de pagamento — mesma lógica de
  // buildEspirometriaFinanceiroPayload (M11.1.1). Cancelado (por exame OU
  // pagamento) pula as regras de Recebido/Parcial: o exame não aconteceu.
  var cancelado = statusExame === "Cancelado" || statusPagamento === "Cancelado";
  if (!cancelado) {
    if (statusPagamento === "Recebido") {
      if (!formaPagamento) return _err("forma_pagamento é obrigatória quando status_pagamento é Recebido.", 400);
      if (!temValorRecebido) return _err("valor_recebido é obrigatório quando status_pagamento é Recebido.", 400);
      if (valorRecebidoInformado.valor !== valorCobrado.valor) {
        return _err("valor_recebido deve ser igual a valor_cobrado quando status_pagamento é Recebido.", 400);
      }
    } else if (statusPagamento === "Parcial") {
      if (!formaPagamento) return _err("forma_pagamento é obrigatória quando status_pagamento é Parcial.", 400);
      if (!temValorRecebido) return _err("valor_recebido é obrigatório quando status_pagamento é Parcial.", 400);
      if (!(valorRecebidoInformado.valor > 0 && valorRecebidoInformado.valor < valorCobrado.valor)) {
        return _err("valor_recebido deve ser maior que zero e menor que valor_cobrado quando status_pagamento é Parcial.", 400);
      }
    }
  }

  // REGRA FORMAL DE ZERO (registrada aqui e coberta por teste): Pendente,
  // Cortesia e Cancelado (exame ou pagamento) têm, por definição do fluxo
  // M11.1.1, recebimento igual a 0 — este é o ÚNICO caso em que 0 é gravado
  // sem ter sido digitado. Nos demais status, valor_recebido é o informado
  // (obrigatório). Nada além disso é derivado.
  var semRecebimento = cancelado || statusPagamento === "Cortesia" || statusPagamento === "Pendente";
  var valorRecebido  = semRecebimento ? 0 : valorRecebidoInformado.valor;

  // Desconto: SÓ derivável quando valor_tabela foi realmente enviado.
  var desconto = null;
  if (valorTabela.valor !== null) {
    desconto = Math.max(0, Math.round((valorTabela.valor - valorCobrado.valor) * 100) / 100);
  }

  var observacaoFinanceira = data.observacao_financeira ? String(data.observacao_financeira) : "";
  // M14.3A (correção final) — criado_em é autoridade EXCLUSIVA do servidor no
  // insert; um valor enviado pelo cliente é sempre ignorado (nunca aceito),
  // e o update preserva o criado_em já gravado (ver ramo "existente" abaixo:
  // imutável, nunca entra em campos). Não há caminho por onde o cliente
  // consiga fixar/forjar este timestamp técnico.
  var criadoEmClienteIgnorado = Object.prototype.hasOwnProperty.call(data, "criado_em");
  var criadoEm = new Date().toISOString();

  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var lock = _lockScriptOuErro();
  if (!lock) return _err("Sistema ocupado (lock não obtido) — tente novamente.", 503);

  var plano, idLancamento = "";
  try {
    var sheet = _getOrCreateSheet(_SHEETS.FINANCEIRO_LANCAMENTOS);
    _ensureSheetHeader(sheet, _FINANCEIRO_LANCAMENTOS_CABECALHO);

    // Upsert de NEGÓCIO por id_atendimento: retry/atualização de pagamento
    // vira PATCH POR PRESENÇA — só as colunas presentes/derivadas entram;
    // colunas extras/fórmulas/valores não enviados são preservados.
    var existente = _findRowByIdColumn(sheet, "id_atendimento", idAtendimento);

    var campos = {
      data_exame:       dataExameFin.valor,
      local_atendimento: localAtendimento,
      valor_cobrado:    valorCobrado.valor,
      valor_recebido:   valorRecebido,
      status_exame:     statusExame,
      status_pagamento: statusPagamento,
    };
    if (formaPagamento)            campos.forma_pagamento = formaPagamento;
    if (origemPreco)               campos.origem_preco = origemPreco;
    if (valorTabela.valor !== null) campos.valor_tabela = valorTabela.valor;
    if (desconto !== null)         campos.desconto = desconto;
    if (Object.prototype.hasOwnProperty.call(data, "observacao_financeira")) {
      campos.observacao_financeira = observacaoFinanceira;
    }
    if (dataExameFin.precisao !== "desconhecida") campos.data_exame_precisao = dataExameFin.precisao;
    if (Object.prototype.hasOwnProperty.call(data, "servico") && String(data.servico || "").trim()) {
      campos.servico = String(data.servico).trim();
    }

    if (existente) {
      var idColIdx = existente.headers.indexOf("id_lancamento");
      idLancamento = idColIdx >= 0 ? String(existente.rowData[idColIdx] || "").trim() : "";
      if (!idLancamento) {
        idLancamento = ctNovoIdServidor("FIN", Utilities.getUuid());
        campos.id_lancamento = idLancamento; // linha antiga sem id: completa
      }
      // id_atendimento/criado_em/tipo_movimento/fonte: imutáveis — preservados.
    } else {
      idLancamento          = ctNovoIdServidor("FIN", Utilities.getUuid());
      campos.id_lancamento  = idLancamento;
      campos.id_atendimento = idAtendimento;
      campos.criado_em      = criadoEm;
      campos.tipo_movimento = "receita";
      campos.fonte          = "nova_espirometria";
      if (!campos.servico)  campos.servico = "Espirometria";
    }

    var solicitadosFin = ctCamposPresentes("financeiro_lancamentos", data);
    var headersFin = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
    plano = ctPlanejarUpsert("financeiro_lancamentos", headersFin, campos, solicitadosFin,
                             existente ? existente.rowData : null);
    if (!plano.ok) {
      _auditAcao(auditRequestId, auditT0, "registrar_espirometria_financeiro", "financeiro", idLancamento, data, "ERROR: contrato/cabecalho");
      return _err(plano.erro, 422);
    }

    try {
      _aplicarPlanoUpsert(sheet, plano, existente ? existente.rowIdx : -1);
    } catch (e) {
      _auditAcao(auditRequestId, auditT0, "registrar_espirometria_financeiro", "financeiro", idLancamento, data, "ERROR: falha na gravacao");
      // Escrita parcial residual (M14.3A): nunca escondida — relata
      // exatamente o que persistiu e o que falhou (ver _aplicarPlanoUpsert).
      return _err("Erro ao gravar lançamento financeiro: " + e.message, 500, {
        campos_gravados_parcialmente: e.camposGravadosParcialmente || [],
        campos_com_falha: e.camposComFalha || [],
      });
    }
  } finally {
    lock.releaseLock();
  }

  // Auditoria mínima (Etapa 7): ação, status, id_lancamento, id_atendimento,
  // valor_recebido, status_pagamento, timestamp — nunca observacao_financeira
  // nem qualquer outro texto livre.
  _logAudit({
    requestId:    auditRequestId,
    acao:         "registrar_espirometria_financeiro",
    entidadeTipo: "financeiro",
    entidadeId:   idLancamento,
    origem:       data.audit_origem,
    operador:     data.audit_operador,
    trigger:      "web_app",
    resultado:    "ok | id_atendimento=" + idAtendimento +
                  " | valor_recebido=" + valorRecebido + " | status_pagamento=" + statusPagamento,
    durationMs:   Date.now() - auditT0,
  });

  _logEntry({
    acao:   "registrarEspirometriaFinanceiro",
    status: "OK",
    aba:    _SHEETS.FINANCEIRO_LANCAMENTOS,
    id:     idLancamento,
    resumo: "Lançamento financeiro da espirometria registrado (" + statusPagamento + ")." +
            (criadoEmClienteIgnorado ? " Aviso: criado_em enviado pelo cliente foi ignorado — o servidor sempre emite o próprio timestamp." : ""),
  });

  return _ok({
    id:                 idLancamento,
    campos_persistidos: plano.persistidos,
    campos_ignorados:   plano.ignorados,
    contrato_versao:    plano.contrato_versao,
    versao_cabecalho:   plano.versao_cabecalho,
    aviso_criado_em_ignorado: criadoEmClienteIgnorado || undefined,
    message:            "Lançamento financeiro salvo no Google Sheets.",
  });
}


/**
 * Atualiza a etapa de um lead já existente na aba Leads, identificado por lead_id.
 * Usado pelo botão "Mudar etapa" do painel (Leads e Agendamentos).
 *
 * Não cria uma linha nova se o lead_id não for encontrado — retorna erro,
 * já que uma atualização de etapa pressupõe um lead já cadastrado.
 *
 * Quando a nova etapa é uma das etapas de conversão ("Realizou consulta",
 * "Realizou espirometria", "Realizou consulta e espirometria"), reaproveita
 * o mesmo núcleo de conversão usado pelo gatilho onEdit manual
 * (_cvConverterLeadCore, definido em converter-lead-em-paciente.gs), para que
 * o resultado seja idêntico a editar a célula 'etapa' diretamente na planilha:
 * cria CRM Pacientes/Consultas/Espirometria e move o lead para 'Leads Convertidos'.
 * Se esse arquivo não estiver instalado no projeto, apenas atualiza a célula
 * de etapa (sem interromper a ação com erro).
 */
function _updateLeadStage(data) {
  _required(data, ["lead_id", "etapa"]);

  // request_id único por ação — correlaciona todas as linhas de auditoria
  // desta chamada (principal + derivadas) na aba Log Auditoria.
  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var novaEtapa = String(data.etapa).trim();
  var targetId  = String(data.lead_id).trim();

  var sheet = _getOrCreateSheet(_SHEETS.LEADS);
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) {
    return _err("Nenhum lead cadastrado na aba " + _SHEETS.LEADS + ".", 404);
  }

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });

  var idIdx = headers.indexOf("lead_id");
  if (idIdx < 0) return _err("Coluna lead_id não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var etapaIdx = headers.indexOf("etapa");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var rowIdx = -1; // 1-based, índice de linha na planilha
  for (var i = 1; i < allData.length; i++) {
    if (String(allData[i][idIdx] || "").trim() === targetId) {
      rowIdx = i + 1;
      break;
    }
  }

  if (rowIdx < 0) {
    _logEntry({ acao: "updateLeadStage", status: "NAO-ENCONTRADO", aba: _SHEETS.LEADS, id: targetId, resumo: "Lead não encontrado para atualização de etapa." });
    return _err("Lead não encontrado: " + targetId, 404);
  }

  // Valor anterior lido ANTES da escrita — allData foi carregado acima.
  var etapaAnterior = String(allData[rowIdx - 1][etapaIdx] || "").trim();

  // Atualiza a etapa e verifica se a gravação realmente persistiu.
  // Alguns dropdowns/validações do Google Sheets podem impedir a alteração sem erro claro
  // (ex.: "Selecione uma opção da lista."). _writeEtapaVerified trata isso de forma segura.
  if (!_writeEtapaVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa)) {
    var etapaLida = String(sheet.getRange(rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({
      acao:   "updateLeadStage",
      status: "ERRO-GRAVACAO",
      aba:    _SHEETS.LEADS,
      id:     targetId,
      resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida,
    });
    _logAudit({
      requestId:     auditRequestId,
      acao:          "update_lead_stage",
      entidadeTipo:  "lead",
      entidadeId:    targetId,
      campo:         "etapa",
      valorAnterior: etapaAnterior,
      valorNovo:     novaEtapa,
      origem:        data.audit_origem,
      operador:      data.audit_operador,
      trigger:       "web_app",
      resultado:     "ERROR: falha na gravacao da etapa",
      durationMs:    Date.now() - auditT0,
    });
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // Auditoria da transição de etapa — logo após a escrita confirmada.
  // Falha aqui nunca interrompe a operação (garantido dentro de _logAudit).
  _logAudit({
    requestId:     auditRequestId,
    acao:          "update_lead_stage",
    entidadeTipo:  "lead",
    entidadeId:    targetId,
    campo:         "etapa",
    valorAnterior: etapaAnterior,
    valorNovo:     novaEtapa,
    origem:        data.audit_origem,
    operador:      data.audit_operador,
    trigger:       "web_app",
    resultado:     "ok",
    durationMs:    Date.now() - auditT0,
  });

  // Nota curta de histórico na coluna observacao, se existir — sem dados sensíveis
  var obsIdx = headers.indexOf("observacao");
  if (obsIdx >= 0) {
    var obsAtual = String(allData[rowIdx - 1][obsIdx] || "").trim();
    var nota     = "[" + _nowBr() + "] Etapa alterada para \"" + novaEtapa + "\" via painel.";
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(sheet, rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: targetId, resumo: "Falha ao gravar nota em observacao." });
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  // Responsável pela mudança — só sobrescreve se enviado explicitamente
  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    if (!_writeCellVerified(sheet, rowIdx, respIdx + 1, String(data.responsavel).trim())) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: targetId, resumo: "Falha ao gravar responsavel." });
      return _err("Etapa gravada, mas falha ao gravar responsavel.", 500);
    }
  }

  var converted = false;
  if (_LEAD_ETAPAS_CONVERSAO.indexOf(novaEtapa) >= 0 && typeof _cvConverterLeadCore === "function") {
    var lock = LockService.getScriptLock();
    try {
      lock.waitLock(15000);
      _cvConverterLeadCore(_getWorkbook(), sheet, rowIdx, novaEtapa);
      converted = true;
    } catch (convErr) {
      _logEntry({ acao: "updateLeadStage", status: "ERRO-CONVERSAO", aba: _SHEETS.LEADS, id: targetId, resumo: convErr.message });
      return _err("Etapa atualizada, mas a conversão automática para CRM falhou: " + convErr.message, 500);
    } finally {
      lock.releaseLock();
    }
  }

  _logEntry({
    acao:   "updateLeadStage",
    status: "OK",
    aba:    _SHEETS.LEADS,
    id:     targetId,
    resumo: "Etapa alterada para " + novaEtapa + (converted ? " (convertido para CRM)" : ""),
  });

  return _ok({
    id: targetId,
    message: converted
      ? "Etapa atualizada e lead convertido para os CRMs de atendimento."
      : "Etapa atualizada com sucesso.",
    converted: converted,
  });
}

/**
 * Atualiza a etapa comercial de uma clínica/parceiro já existente em
 * "CRM Clinicas", identificada por clinica_id (nunca por telefone/nome
 * exposto no frontend — o painel já carrega clinica_id junto com cada linha
 * da tabela CRM Clínicas). Usado pelo botão "Mudar etapa" do painel
 * (CRM → Clínicas e Parceiros), espelhando updateLeadStage.
 *
 * Regra do funil (ver skill soprolife-b2b-pcmso-crm): Etapa é a única fonte
 * de verdade da fase comercial — Status/Próximo passo/Observação nunca
 * substituem a Etapa, e a cor da linha na planilha não é considerada aqui.
 *
 * Não cria uma linha nova se clinica_id não for encontrado (mesma regra de
 * updateLeadStage: atualização de etapa pressupõe uma clínica já cadastrada).
 * Nunca apaga nem move linhas.
 *
 * Efeito colateral best-effort: se a aba "Base Prospecção B2B PCMSO" existir
 * e tiver (ou puder ganhar, de forma idempotente) uma coluna "Etapa", e uma
 * linha com nome de clínica compatível for encontrada lá, a mesma etapa é
 * espelhada nessa linha também — mantendo a base de prospecção bruta
 * alinhada com o CRM Clínicas já promovido. Falha nesse espelhamento NUNCA
 * derruba a ação principal (a escrita em CRM Clinicas já terá sido
 * confirmada); o retorno informa se o espelhamento ocorreu.
 *
 * data esperado:
 *   clinica_id   (obrigatório) — CLIN-... / CLI-... já cadastrado em CRM Clinicas
 *   etapa        (obrigatório) — precisa estar em _CRM_ETAPAS_OFICIAIS
 *   responsavel  (opcional)    — só sobrescreve se enviado explicitamente
 */
function _updateCrmClinicaEtapa(data) {
  _required(data, ["clinica_id", "etapa"]);

  // request_id único por ação — o espelhamento PCMSO grava um evento
  // derivado apontando para este id via derivado_de.
  var auditRequestId = Utilities.getUuid();
  var auditT0        = Date.now();

  var novaEtapa = String(data.etapa).trim();
  var clinicaId = String(data.clinica_id).trim();

  if (_CRM_ETAPAS_OFICIAIS.indexOf(novaEtapa) < 0) {
    return _err("Etapa inválida: " + novaEtapa + ". Use uma das etapas oficiais.", 400);
  }

  var sheet = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var found = _findRowByIdColumn(sheet, "clinica_id", clinicaId);
  if (!found) {
    _logEntry({ acao: "updateCrmClinicaEtapa", status: "NAO-ENCONTRADO", aba: _SHEETS.CRM_CLINICAS, id: clinicaId, resumo: "Clínica não encontrada para atualização de etapa." });
    return _err("Clínica não encontrada: " + clinicaId, 404);
  }

  var headers  = found.headers;
  var rowIdx   = found.rowIdx;
  var etapaIdx = headers.indexOf("etapa");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.CRM_CLINICAS + ".", 500);

  // Valor anterior lido ANTES da escrita — found.rowData veio de _findRowByIdColumn.
  var etapaAnterior = String(found.rowData[etapaIdx] || "").trim();

  if (!_writeEtapaVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa, _CRM_ETAPAS_OFICIAIS)) {
    var etapaLida = String(sheet.getRange(rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({
      acao:   "updateCrmClinicaEtapa",
      status: "ERRO-GRAVACAO",
      aba:    _SHEETS.CRM_CLINICAS,
      id:     clinicaId,
      resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida,
    });
    _logAudit({
      requestId:     auditRequestId,
      acao:          "update_crm_clinica_etapa",
      entidadeTipo:  "clinica",
      entidadeId:    clinicaId,
      campo:         "etapa",
      valorAnterior: etapaAnterior,
      valorNovo:     novaEtapa,
      origem:        data.audit_origem,
      operador:      data.audit_operador,
      trigger:       "web_app",
      resultado:     "ERROR: falha na gravacao da etapa",
      durationMs:    Date.now() - auditT0,
    });
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // Auditoria da transição de etapa — logo após a escrita confirmada.
  _logAudit({
    requestId:     auditRequestId,
    acao:          "update_crm_clinica_etapa",
    entidadeTipo:  "clinica",
    entidadeId:    clinicaId,
    campo:         "etapa",
    valorAnterior: etapaAnterior,
    valorNovo:     novaEtapa,
    origem:        data.audit_origem,
    operador:      data.audit_operador,
    trigger:       "web_app",
    resultado:     "ok",
    durationMs:    Date.now() - auditT0,
  });

  // Nota curta de histórico em observacao, se a coluna existir — sem dados sensíveis.
  var obsIdx = headers.indexOf("observacao");
  if (obsIdx >= 0) {
    var obsAtual = String(found.rowData[obsIdx] || "").trim();
    var nota     = "[" + _nowBr() + "] Etapa alterada para \"" + novaEtapa + "\" via painel.";
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(sheet, rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "updateCrmClinicaEtapa", status: "ERRO-GRAVACAO", aba: _SHEETS.CRM_CLINICAS, id: clinicaId, resumo: "Falha ao gravar nota em observacao." });
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  // Mantém "última interação" fresca — mesmo campo que registrarInteracaoClinica atualiza.
  var ultimaIdx = headers.indexOf("ultima_interacao");
  if (ultimaIdx >= 0) {
    _writeCellVerified(sheet, rowIdx, ultimaIdx + 1, _nowBr());
  }

  // Responsável pela mudança — só sobrescreve se enviado explicitamente.
  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    _writeCellVerified(sheet, rowIdx, respIdx + 1, String(data.responsavel).trim());
  }

  var nomeClinicaIdx = headers.indexOf("nome_clinica");
  var nomeClinica = nomeClinicaIdx >= 0 ? String(found.rowData[nomeClinicaIdx] || "").trim() : "";
  var pcmsoAtualizado = _mirrorEtapaParaPcmso(nomeClinica, novaEtapa, clinicaId, auditRequestId);

  _logEntry({
    acao:   "updateCrmClinicaEtapa",
    status: "OK",
    aba:    _SHEETS.CRM_CLINICAS + (pcmsoAtualizado ? " + " + _SHEETS.B2B_PCMSO : ""),
    id:     clinicaId,
    resumo: "Etapa alterada para " + novaEtapa,
  });

  return _ok({
    id: clinicaId,
    message: "Etapa atualizada com sucesso." + (pcmsoAtualizado ? " Base PCMSO também atualizada." : ""),
    pcmsoAtualizado: pcmsoAtualizado,
  });
}

/**
 * Espelha (best-effort) a nova etapa na aba "Base Prospecção B2B PCMSO",
 * coluna "Etapa" — garante a coluna de forma idempotente (nunca apaga/reordena
 * colunas existentes) e localiza a linha por nome normalizado (mesma técnica
 * de _keyNorm usada em registrarInteracaoClinica e no matching de
 * promote-pcmso-to-crm.py), nunca por telefone. Se a aba não existir, o nome
 * não for encontrado, ou a coluna de nome não existir, retorna false
 * silenciosamente — isso NUNCA deve derrubar a ação principal
 * (updateCrmClinicaEtapa já confirmou a escrita em CRM Clinicas).
 *
 * Auditoria: quando o espelhamento chega a ESCREVER (sucesso ou falha de
 * gravação), registra um evento derivado em Log Auditoria com derivado_de =
 * requestIdPrincipal da ação que o disparou. Os retornos false silenciosos
 * (aba/linha/coluna ausente) não geram auditoria — nada foi escrito.
 */
function _mirrorEtapaParaPcmso(nomeClinica, novaEtapa, clinicaId, requestIdPrincipal) {
  if (!nomeClinica) return false;

  var ss = _getWorkbook();
  var sheet = ss.getSheetByName(_SHEETS.B2B_PCMSO);
  if (!sheet) return false;

  try {
    var lastRow = sheet.getLastRow();
    var lastCol = sheet.getLastColumn();
    if (lastRow < 2 || lastCol < 1) return false;

    var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
    var headers = allData[0].map(function(h) { return String(h).trim(); });
    var headersLower = headers.map(function(h) { return h.toLowerCase(); });

    var nomeCandidatos = ["nome da clinica/empresa", "nome da clínica/empresa", "nome da clinica", "nome da clínica", "nome"];
    var nomeIdx = -1;
    for (var n = 0; n < nomeCandidatos.length; n++) {
      var idx = headersLower.indexOf(nomeCandidatos[n]);
      if (idx >= 0) { nomeIdx = idx; break; }
    }
    if (nomeIdx < 0) return false;

    var targetKey = _keyNorm(nomeClinica);
    var rowIdx = -1;
    for (var i = 1; i < allData.length; i++) {
      var candKey = _keyNorm(String(allData[i][nomeIdx] || ""));
      if (!candKey) continue;
      if (candKey === targetKey) { rowIdx = i + 1; break; }
      var minLen = Math.min(candKey.length, targetKey.length);
      if (minLen >= 5 && (candKey.indexOf(targetKey) >= 0 || targetKey.indexOf(candKey) >= 0)) {
        rowIdx = i + 1; // não interrompe: prefere igualdade exata se aparecer depois
      }
    }
    if (rowIdx < 0) return false;

    // Garante a coluna "Etapa" de forma idempotente (acrescenta ao final, nunca reordena/apaga).
    var headersAtualizados = _ensureExtraColumns(sheet, ["Etapa"]);
    var etapaIdx = headersAtualizados.indexOf("Etapa");
    if (etapaIdx < 0) return false;

    // Valor anterior só existe em allData se a coluna Etapa já existia
    // antes de _ensureExtraColumns; se acabou de ser criada, era vazio.
    var etapaAnterior = etapaIdx < allData[0].length
      ? String(allData[rowIdx - 1][etapaIdx] || "").trim()
      : "";

    var gravou = _writeCellVerified(sheet, rowIdx, etapaIdx + 1, novaEtapa);

    // Evento derivado: entidade_id é o clinica_id do CRM (a linha PCMSO não
    // tem ID próprio); a correlação com a ação principal é via derivado_de.
    _logAudit({
      acao:          "mirror_etapa_pcmso",
      entidadeTipo:  "pcmso",
      entidadeId:    clinicaId,
      campo:         "etapa",
      valorAnterior: etapaAnterior,
      valorNovo:     novaEtapa,
      trigger:       "web_app",
      resultado:     gravou ? "ok" : "ERROR: falha na gravacao do espelhamento",
      derivadoDe:    requestIdPrincipal,
    });

    return gravou;
  } catch (e) {
    return false;
  }
}

/**
 * Vincula um lead B2B (clínica/parceiro) a uma clínica já existente em CRM Clínicas,
 * marcando o lead como convertido/arquivado — sem removê-lo da aba Leads.
 *
 * Usado pelo botão "Vincular a clínica" / "Converter em parceiro" do painel
 * (leads de serviço Clínicas / PCMSO). Diferente do fluxo de paciente
 * (_cvConverterLeadCore), que remove a linha da aba Leads: aqui o lead
 * continua na aba Leads como histórico, marcado por tipo_lead/status_operacional.
 *
 * IDEMPOTENTE: pode ser chamada várias vezes para o mesmo lead_id + clinica_id
 * sem criar duplicatas — nem na aba Leads (é sempre um update na mesma linha),
 * nem em "Leads Convertidos" (upsert por lead_id + entidade_id, ver
 * _upsertLeadConvertido), nem em "CRM Contatos B2B" (upsert por clinica_id +
 * nome_contato, ver _upsertContatoB2B). Só "Log Conversões Leads" registra uma
 * linha por chamada (é um log de auditoria, não um estado consolidado) — mas
 * deixa claro no campo 'resultado' se foi um vínculo novo, uma atualização ou
 * uma chamada repetida sem mudança nenhuma.
 *
 * Reaproveita 'Leads Convertidos' e 'Log Conversões Leads' (abas já existentes,
 * criadas por converter-lead-em-paciente.gs) para manter o mesmo padrão de
 * auditoria usado pela conversão de pacientes.
 *
 * Nunca inventa telefone/e-mail: telefone do contato vem do próprio lead
 * (telefone_whatsapp), e-mail fica em branco se não informado.
 *
 * data esperado:
 *   lead_id       (obrigatório) — LEAD-...
 *   clinica_id    (obrigatório) — CLIN-... já cadastrado em CRM Clínicas
 *   responsavel   (obrigatório)
 *   etapa         (opcional) — "Convertido em clínica/parceiro" (padrão) ou "Parceiro ativo"
 *   nome_contato, cargo, papel, proxima_acao, data_proxima_acao, email (opcionais,
 *   usados para criar/atualizar um registro em CRM Contatos B2B)
 */
function _vincularLeadAClinica(data) {
  _required(data, ["lead_id", "clinica_id", "responsavel"]);

  var leadId    = String(data.lead_id).trim();
  var clinicaId = String(data.clinica_id).trim();
  var novaEtapa = String(data.etapa || "Convertido em clínica/parceiro").trim();

  if (_LEAD_ETAPAS_B2B_TERMINAL.indexOf(novaEtapa) < 0) {
    return _err(
      "Etapa inválida para vínculo B2B. Use uma destas: " + _LEAD_ETAPAS_B2B_TERMINAL.join(" ou "),
      400
    );
  }

  // Confirma que a clínica de destino existe — nunca inventamos entidade_id.
  var crmSheet   = _getOrCreateSheet(_SHEETS.CRM_CLINICAS);
  var clinicaRow = _findRowByIdColumn(crmSheet, "clinica_id", clinicaId);
  if (!clinicaRow) {
    return _err("Clínica não encontrada em " + _SHEETS.CRM_CLINICAS + ": " + clinicaId, 404);
  }
  var nomeClinicaIdx = clinicaRow.headers.indexOf("nome_clinica");
  var nomeClinica    = nomeClinicaIdx >= 0 ? String(clinicaRow.rowData[nomeClinicaIdx] || "").trim() : clinicaId;

  // Localiza o lead na aba Leads.
  var leadsSheet = _getOrCreateSheet(_SHEETS.LEADS);
  var leadRow    = _findRowByIdColumn(leadsSheet, "lead_id", leadId);
  if (!leadRow) {
    return _err("Lead não encontrado: " + leadId, 404);
  }

  // Garante colunas novas (tipo_lead, entidade_destino, ...) sem tocar nas antigas.
  var headers = _ensureExtraColumns(leadsSheet, _LEAD_COLUNAS_EXTRA_B2B);
  var lastCol = leadsSheet.getLastColumn();
  var rowData = leadsSheet.getRange(leadRow.rowIdx, 1, 1, lastCol).getValues()[0];

  var etapaIdx  = headers.indexOf("etapa");
  var nomeIdx   = headers.indexOf("nome");
  var telIdx    = headers.indexOf("telefone_whatsapp");
  var obsIdx    = headers.indexOf("observacao");
  var respIdx0  = headers.indexOf("responsavel");
  var entIdx0   = headers.indexOf("entidade_id");
  var statusIdx0 = headers.indexOf("status_operacional");
  if (etapaIdx < 0) return _err("Coluna etapa não encontrada na aba " + _SHEETS.LEADS + ".", 500);

  var nomeLead = nomeIdx >= 0 ? String(rowData[nomeIdx] || "").trim() : "";
  var telLead  = telIdx  >= 0 ? String(rowData[telIdx]  || "").trim() : "";
  var hoje     = _nowBr();
  var obsCurta = "Vinculado à " + nomeClinica + " / " + clinicaId;

  // Estado ANTES desta chamada — usado para decidir se isso é um vínculo novo,
  // uma atualização de um vínculo já existente, ou uma repetição sem mudança
  // (idempotência: chamar de novo com os mesmos dados não deve criar duplicata
  // nem ser tratado como "novo vínculo" no log).
  var beforeEtapa       = String(rowData[etapaIdx] || "").trim();
  var beforeEntidadeId  = entIdx0 >= 0 ? String(rowData[entIdx0] || "").trim() : "";
  var beforeStatusOp    = statusIdx0 >= 0 ? String(rowData[statusIdx0] || "").trim() : "";
  var beforeResponsavel = respIdx0 >= 0 ? String(rowData[respIdx0] || "").trim() : "";
  var novoResponsavel   = data.responsavel ? String(data.responsavel).trim() : beforeResponsavel;

  var jaEstavaVinculado = (beforeEntidadeId === clinicaId && beforeStatusOp === "convertido");
  var conversionState;
  if (!jaEstavaVinculado) {
    conversionState = "vinculado";
  } else if (beforeEtapa !== novaEtapa || beforeResponsavel !== novoResponsavel) {
    conversionState = "atualizado";
  } else {
    conversionState = "sem-alteracao";
  }
  var resultadoLog = conversionState === "vinculado" ? "B2B — Vinculado"
    : conversionState === "atualizado" ? "B2B — Já vinculado / atualizado"
    : "B2B — Sem alteração";

  // 1) Grava a etapa com o mesmo mecanismo verificado usado por updateLeadStage
  //    (revalida o dropdown antes de escrever, confirma a gravação lendo de volta;
  //    se a validação não for um dropdown simples, cai em fallback e limpa a validação).
  if (!_writeEtapaVerified(leadsSheet, leadRow.rowIdx, etapaIdx + 1, novaEtapa)) {
    var etapaLida = String(leadsSheet.getRange(leadRow.rowIdx, etapaIdx + 1).getDisplayValue() || "").trim();
    _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida });
    _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar etapa)");
    return _err("Falha ao gravar etapa. Etapa desejada: " + novaEtapa + " | Etapa lida: " + etapaLida, 500);
  }

  // 2) Marca as colunas de conversão/arquivamento — lead permanece na aba Leads.
  // Toda escrita é verificada (lida de volta): se qualquer campo não gravar,
  // a ação inteira falha em vez de retornar ok:true com dado incompleto.
  var extraValues = {
    tipo_lead:          "b2b",
    entidade_destino:   _SHEETS.CRM_CLINICAS,
    entidade_id:        clinicaId,
    data_conversao:     hoje,
    status_operacional: "convertido",
  };
  var extraFailures = [];
  Object.keys(extraValues).forEach(function(col) {
    var idx = headers.indexOf(col);
    if (idx < 0) return;
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, idx + 1, extraValues[col])) extraFailures.push(col);
  });
  if (extraFailures.length > 0) {
    _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar colunas: " + extraFailures.join(", ") });
    _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar colunas de conversão)");
    return _err("Etapa gravada, mas falha ao gravar campos de conversão: " + extraFailures.join(", "), 500);
  }

  // 3) Observação curta de histórico (mesmo padrão de updateLeadStage).
  if (obsIdx >= 0) {
    var obsAtual = String(rowData[obsIdx] || "").trim();
    var nota     = "[" + hoje + "] " + obsCurta;
    var obsNovo  = obsAtual ? (obsAtual + " | " + nota) : nota;
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, obsIdx + 1, obsNovo)) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar nota em observacao." });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar observação)");
      return _err("Etapa gravada, mas falha ao gravar nota em observacao.", 500);
    }
  }

  var respIdx = headers.indexOf("responsavel");
  if (respIdx >= 0 && data.responsavel) {
    if (!_writeCellVerified(leadsSheet, leadRow.rowIdx, respIdx + 1, String(data.responsavel).trim())) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS, id: leadId, resumo: "Falha ao gravar responsavel." });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao gravar responsável)");
      return _err("Etapa gravada, mas falha ao gravar responsavel.", 500);
    }
  }

  // Releitura da linha já atualizada, para arquivar uma cópia fiel em Leads Convertidos.
  var rowFinal = leadsSheet.getRange(leadRow.rowIdx, 1, 1, lastCol).getValues()[0];
  var rowFinalMap = {};
  headers.forEach(function(h, i) { rowFinalMap[h] = rowFinal[i]; });

  // 4) Upsert em "Leads Convertidos" — aba já existente, reaproveitada (não recriada).
  //    Diferente do fluxo de paciente, NÃO removemos a linha da aba Leads.
  //    IDEMPOTENTE: identifica o registro consolidado por lead_id + entidade_id;
  //    se já existir (chamada repetida para o mesmo lead+clínica), atualiza a
  //    linha existente em vez de acrescentar uma nova (evita duplicatas).
  var convertidosSheet = _getOrCreateSheet(_SHEETS.LEADS_CONVERTIDOS);
  _ensureSheetHeader(convertidosSheet, _B2B_LEADS_CONVERTIDOS_CABECALHO_FALLBACK);
  // Garante que "Leads Convertidos" também tenha as colunas B2B (entidade_id etc.),
  // sem o quê não haveria como identificar duplicatas por clínica nessa aba.
  var convHeaders = _ensureExtraColumns(convertidosSheet, _LEAD_COLUNAS_EXTRA_B2B);

  var convValuesByHeader = {};
  convHeaders.forEach(function(h) {
    convValuesByHeader[h] = rowFinalMap.hasOwnProperty(h) ? rowFinalMap[h] : "";
  });

  var existingConvRow = _findRowByColumns(convertidosSheet, convHeaders, {
    lead_id:     leadId,
    entidade_id: clinicaId,
  });

  if (existingConvRow > 0) {
    var convFailures = [];
    convHeaders.forEach(function(h, idx) {
      if (!_writeCellVerified(convertidosSheet, existingConvRow, idx + 1, convValuesByHeader[h])) {
        convFailures.push(h);
      }
    });
    if (convFailures.length > 0) {
      _logEntry({ acao: "vincularLeadAClinica", status: "ERRO-GRAVACAO", aba: _SHEETS.LEADS_CONVERTIDOS, id: leadId, resumo: "Falha ao atualizar: " + convFailures.join(", ") });
      _logConversaoLeads(leadId, nomeLead, novaEtapa, [], "B2B — Erro (falha ao atualizar " + _SHEETS.LEADS_CONVERTIDOS + ")");
      return _err("Etapa gravada, mas falha ao atualizar " + _SHEETS.LEADS_CONVERTIDOS + ": " + convFailures.join(", "), 500);
    }
  } else {
    _appendRowSemValidacao(convertidosSheet, convHeaders.map(function(h) { return convValuesByHeader[h]; }));
  }

  // 5) Cria/atualiza contato B2B, se um nome de contato foi informado.
  //    IDEMPOTENTE: upsert por contato_id (se enviado) ou por clinica_id + nome
  //    normalizado — nunca cria Juan duplicado numa segunda chamada.
  var contatoId = "";
  if (data.nome_contato && String(data.nome_contato).trim()) {
    contatoId = _upsertContatoB2B({
      contatoId:   data.contato_id || "",
      clinicaId:   clinicaId,
      nomeContato: data.nome_contato,
      telefone:    telLead, // vem do lead — nunca inventado
      responsavel: data.responsavel || "",
      observacao:  obsCurta,
      // Presença real (hasOwnProperty), não valor: distingue campo ausente de
      // vazio explícito enviado pelo cliente (cargo/email/papel/proxima_acao/
      // data_proxima_acao) — ver _upsertContatoB2B.
      camposClienteOpcionais: data,
    });
  }

  // 6) Log técnico de conversão — aba já existente, reaproveitada (não recriada).
  //    Toda chamada gera uma linha aqui (é o histórico de auditoria, não um
  //    estado consolidado), mas o campo 'resultado' deixa claro se foi um
  //    vínculo novo, uma atualização, ou uma repetição sem mudança nenhuma.
  var destinosCriados = [_SHEETS.CRM_CLINICAS + ":" + clinicaId];
  if (contatoId) destinosCriados.push(_SHEETS.CRM_CONTATOS_B2B + ":" + contatoId);

  _logConversaoLeads(leadId, nomeLead, novaEtapa, destinosCriados, resultadoLog);

  // Log Centro Comando — apenas metadados, nunca telefone/observação privada.
  _logEntry({
    acao:   "vincularLeadAClinica",
    status: "OK",
    aba:    _SHEETS.LEADS + " + " + _SHEETS.LEADS_CONVERTIDOS + " + " + _SHEETS.LOG_CONVERSOES + (contatoId ? " + " + _SHEETS.CRM_CONTATOS_B2B : ""),
    id:     leadId,
    resumo: resultadoLog + " — clínica " + clinicaId + " (etapa: " + novaEtapa + ").",
  });

  var messageByState = {
    "vinculado":      "Lead vinculado com sucesso à clínica " + nomeClinica + ".",
    "atualizado":     "Vínculo com " + nomeClinica + " já existia — dados atualizados.",
    "sem-alteracao":  "Lead já estava vinculado à " + nomeClinica + " — nenhuma mudança necessária.",
  };

  return _ok({
    id:         leadId,
    clinica_id: clinicaId,
    contato_id: contatoId || null,
    status:     conversionState,
    message:    messageByState[conversionState],
  });
}

/**
 * Registra uma linha em "Log Conversões Leads" — aba já existente, reaproveitada
 * (não recriada). Chamada tanto no sucesso quanto em qualquer falha de gravação
 * de vincularLeadAClinica, para que o log fique interpretável ("o que aconteceu
 * nessa tentativa"), não só as tentativas bem-sucedidas.
 *
 * resultado esperado (padronizado): um destes quatro prefixos —
 *   "B2B — Vinculado"
 *   "B2B — Já vinculado / atualizado"
 *   "B2B — Sem alteração"
 *   "B2B — Erro (...)"
 *
 * Nunca recebe telefone nem observação privada — só metadados operacionais.
 * Falha no log nunca interrompe a ação principal (mesmo padrão de _logEntry).
 */
function _logConversaoLeads(leadId, nome, etapa, destinosCriados, resultado) {
  try {
    var logSheet = _getOrCreateSheet(_SHEETS.LOG_CONVERSOES);
    _ensureSheetHeader(logSheet, _B2B_LOG_CONVERSOES_CABECALHO_FALLBACK);
    _appendRowSemValidacao(logSheet, [
      _nowBr(),
      leadId || "",
      nome || "",
      etapa || "",
      (destinosCriados || []).join("; "),
      resultado || "",
      "",
    ]);
  } catch (_) {
    // Silencioso: falha no log não deve interromper a ação principal.
  }
}

/**
 * Cria ou atualiza um contato B2B em "CRM Contatos B2B", identificado por
 * clinica_id + nome_contato (chave normalizada). Aba criada sob demanda,
 * na primeira vez que um contato é vinculado.
 */
// Mapa campo-do-cliente → coluna: os nomes já coincidem, mas o mapa deixa
// explícito quais opcionais são elegíveis a entrar via presença real.
var _B2B_CONTATO_OPCIONAIS = [
  "cargo", "email", "papel", "proxima_acao", "data_proxima_acao",
];

function _upsertContatoB2B(info) {
  // Função interna (chamada por _vincularLeadAClinica) — o evento de
  // auditoria dela recebe request_id próprio (UUID de fallback em _logAudit);
  // quando _vincularLeadAClinica for instrumentada, passar derivado_de aqui.
  var auditT0 = Date.now();
  if (!_ctDisponivel()) throw new Error(_ERRO_CONTRATOS_AUSENTES);

  var sheet = _getOrCreateSheet(_SHEETS.CRM_CONTATOS_B2B);
  _ensureSheetHeader(sheet, _CRM_CONTATOS_B2B_CABECALHO);

  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  var clinicaIdx    = headers.indexOf("clinica_id");
  var nomeIdx       = headers.indexOf("nome_contato");
  var contatoIdIdx0 = headers.indexOf("contato_id");
  var lastRow       = sheet.getLastRow();

  // Identifica o contato existente por contato_id explícito (se enviado) ou,
  // por padrão, por clinica_id + nome_contato normalizado — nunca cria
  // duplicata para o mesmo par clínica/contato.
  var rowIdx = -1;
  if (lastRow >= 2 && info.contatoId && contatoIdIdx0 >= 0) {
    rowIdx = _findRowByColumns(sheet, headers, { contato_id: String(info.contatoId).trim() });
  }
  if (rowIdx < 0 && lastRow >= 2 && clinicaIdx >= 0 && nomeIdx >= 0) {
    var allData   = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
    var keyTarget = _keyNorm(info.clinicaId) + "|" + _keyNorm(info.nomeContato);
    for (var i = 0; i < allData.length; i++) {
      var key = _keyNorm(String(allData[i][clinicaIdx] || "")) + "|" + _keyNorm(String(allData[i][nomeIdx] || ""));
      if (key === keyTarget) { rowIdx = i + 2; break; }
    }
  }

  // M14.3A (correção final) — contrato executável "crm_contatos_b2b":
  // fail-closed TOTAL (qualquer campo suportado sem coluna correspondente
  // gera erro ANTES de qualquer setValue) + patch por PRESENÇA REAL da
  // propriedade (vazio explícito enviado pelo cliente ≠ ausência — ausência
  // nunca limpa cargo/e-mail/observação já cadastrados). status_relacionamento
  // e ultima_interacao são a regra formal desta ação (sempre aplicadas);
  // telefone vem do lead (nunca digitado direto pelo cliente aqui) e só entra
  // se não vazio; responsavel só entra se informado.
  var campos = {
    status_relacionamento: "Ativo",
    ultima_interacao:      _nowBr(),
  };
  var solicitados = ["status_relacionamento", "ultima_interacao"];
  if (String(info.telefone || "").trim()) {
    campos.telefone_whatsapp = String(info.telefone).trim();
    solicitados.push("telefone_whatsapp");
  }
  if (String(info.responsavel || "").trim()) {
    campos.responsavel = String(info.responsavel).trim();
    solicitados.push("responsavel");
  }
  if (String(info.observacao || "").trim()) {
    campos.observacao = String(info.observacao).trim();
    solicitados.push("observacao");
  }

  var origemCliente = info.camposClienteOpcionais || {};
  _B2B_CONTATO_OPCIONAIS.forEach(function(coluna) {
    if (Object.prototype.hasOwnProperty.call(origemCliente, coluna)) {
      var v = origemCliente[coluna];
      campos[coluna] = String(v === null || v === undefined ? "" : v).trim();
      solicitados.push(coluna);
    }
  });

  var linhaExistente = rowIdx > 0 ? sheet.getRange(rowIdx, 1, 1, lastCol).getValues()[0] : null;
  var newId = "";
  if (!linhaExistente) {
    newId = ctNovoIdServidor("CONT", Utilities.getUuid());
    campos.contato_id   = newId;
    campos.clinica_id   = String(info.clinicaId || "").trim();
    campos.nome_contato = String(info.nomeContato || "").trim();
    solicitados = solicitados.concat(["contato_id", "clinica_id", "nome_contato"]);
  }

  // Validação COMPLETA antes de qualquer escrita: campo solicitado sem coluna
  // correspondente retorna erro aqui — nenhuma célula foi tocada ainda.
  var plano = ctPlanejarUpsert("crm_contatos_b2b", headers, campos, solicitados, linhaExistente);
  if (!plano.ok) {
    _auditAcao(null, auditT0, "upsert_contato_b2b", "contato_b2b", String(info.contatoId || newId || ""), null, "ERROR: " + plano.erro);
    throw new Error(plano.erro);
  }

  try {
    _aplicarPlanoUpsert(sheet, plano, rowIdx > 0 ? rowIdx : -1);
  } catch (e) {
    _auditAcao(null, auditT0, "upsert_contato_b2b", "contato_b2b", String(info.contatoId || newId || ""), null, "ERROR: falha na gravacao");
    throw e;
  }

  if (linhaExistente) {
    var contatoIdIdx = headers.indexOf("contato_id");
    var contatoIdExistente = contatoIdIdx >= 0 ? String(linhaExistente[contatoIdIdx] || "") : "";
    _auditAcao(null, auditT0, "upsert_contato_b2b", "contato_b2b", contatoIdExistente, null, "ok: update");
    return contatoIdExistente;
  }

  _auditAcao(null, auditT0, "upsert_contato_b2b", "contato_b2b", newId, null, "ok: insert");
  return newId;
}

// ── Helpers de infra ──────────────────────────────────────────────────────────


// IMPORTANTE: o serviço SpreadsheetApp enfileira (batch) as escritas — uma
// exceção de validação disparada por um setValue()/setValues() pode não
// aparecer na hora, e sim no PRÓXIMO SpreadsheetApp.flush() (que pode estar
// em outra função, escrevendo em outra célula). Por isso, TODA função abaixo
// dá flush() imediatamente após a própria escrita, dentro do mesmo try/catch
// — assim qualquer falha de validação é presa e identificada no ponto exato
// onde ocorreu, em vez de subir como "Selecione uma opção da lista." genérico
// lá na exceção não tratada do doPost.

function _setValuesSemValidacao(range, values) {
  range.clearDataValidations();
  range.setValues(values);
  SpreadsheetApp.flush();
}

function _ensureSheetSize(sheet, minRows, minCols) {
  var maxRows = sheet.getMaxRows();
  var maxCols = sheet.getMaxColumns();

  if (maxRows < minRows) {
    sheet.insertRowsAfter(maxRows, minRows - maxRows);
  }

  if (maxCols < minCols) {
    sheet.insertColumnsAfter(maxCols, minCols - maxCols);
  }
}


function _firstEmptyRowByHeaders(sheet, keyHeaders) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var maxRows = Math.max(sheet.getMaxRows(), 2);

  var headers = sheet.getRange(1, 1, 1, lastCol).getDisplayValues()[0]
    .map(function(h) { return String(h).trim(); });

  var keyIdxs = keyHeaders
    .map(function(h) { return headers.indexOf(h); })
    .filter(function(idx) { return idx >= 0; });

  if (keyIdxs.length === 0) {
    return sheet.getLastRow() + 1;
  }

  var values = sheet.getRange(2, 1, maxRows - 1, lastCol).getDisplayValues();

  for (var r = 0; r < values.length; r++) {
    var hasMeaningfulData = keyIdxs.some(function(idx) {
      return String(values[r][idx] || "").trim() !== "";
    });
    if (!hasMeaningfulData) return r + 2;
  }

  return maxRows + 1;
}

function _writeRowSkippingColumnsSemValidacao(sheet, targetRow, row, skipCols) {
  var skip = {};
  (skipCols || []).forEach(function(col) { skip[col] = true; });

  _ensureSheetSize(sheet, targetRow, row.length);

  for (var i = 0; i < row.length; i++) {
    var col = i + 1;
    if (skip[col]) continue;
    _setCellSemValidacao(sheet.getRange(targetRow, col), row[i]);
  }

  return targetRow;
}

function _appendRowSemValidacao(sheet, row) {
  var nextRow = sheet.getLastRow() + 1;
  try {
    _ensureSheetSize(sheet, nextRow, row.length);
    var range = sheet.getRange(nextRow, 1, 1, row.length);
    _setValuesSemValidacao(range, [row]);
  } catch (e) {
    throw new Error("Falha ao adicionar linha na aba '" + sheet.getName() + "': " + e.message);
  }
  return nextRow;
}

function _setCellSemValidacao(cell, value) {
  cell.clearDataValidations();
  cell.setValue(value);
  SpreadsheetApp.flush();
}

/**
 * Escreve um valor em uma célula existente de forma segura contra qualquer
 * validação/dropdown ("Selecione uma opção da lista.", fórmula customizada,
 * etc.): remove a validação da célula antes de escrever, grava, confirma
 * (flush + leitura de volta) e retorna true/false conforme o valor lido bate
 * com o esperado. Nunca deixa uma exceção de validação subir sem tratamento
 * — o flush() acontece DENTRO do try, para capturar exceções de validação
 * que só aparecem nesse momento (ver nota acima).
 *
 * Usado para TODO campo que não seja a coluna 'etapa' da aba Leads (que tem
 * seu próprio helper, _writeEtapaVerified, para preservar o dropdown oficial).
 */
function _writeCellVerified(sheet, row, col, value) {
  var cell = sheet.getRange(row, col);
  try {
    cell.clearDataValidations();
    cell.setValue(value);
    SpreadsheetApp.flush();
  } catch (e) {
    return false;
  }

  var gravadoValue = cell.getValue();

  // Datas: o Google Sheets pode reformatar/reinterpretar um Date ao gravar em
  // uma coluna com formatação diferente da origem (ex.: copiando de 'Leads'
  // para 'Leads Convertidos'). Comparar por instante evita falso-negativo.
  if (value instanceof Date && gravadoValue instanceof Date) {
    return value.getTime() === gravadoValue.getTime();
  }

  var esperado = String(value === null || value === undefined ? "" : value).trim();

  // Caminho comum: valor gravado bate exatamente com o esperado.
  var gravadoStr = String(gravadoValue === null || gravadoValue === undefined ? "" : gravadoValue).trim();
  if (gravadoStr === esperado) return true;

  // Fallback: o Sheets pode ter convertido uma string parecida com data
  // (ex.: "25/06/2026") em um valor Date — nesse caso o texto exibido
  // (getDisplayValue) tende a preservar o formato original mais fielmente
  // que a representação padrão de Date.
  var display = String(cell.getDisplayValue() || "").trim();
  return display === esperado;
}

// etapasOficiais é opcional — por padrão preserva o comportamento original
// (dropdown de Leads). updateCrmClinicaEtapa passa _CRM_ETAPAS_OFICIAIS.
function _ensureLeadStageValidationAllows(cell, novaEtapa, etapasOficiais) {
  var etapas = (etapasOficiais || _LEAD_ETAPAS_OFICIAIS).slice();

  if (novaEtapa && etapas.indexOf(novaEtapa) < 0) {
    etapas.push(novaEtapa);
  }

  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(etapas, true)
    .setAllowInvalid(false)
    .build();

  cell.setDataValidation(rule);
}

/**
 * Escreve a etapa (de um lead OU de uma clínica CRM) de forma segura e
 * verificada. Primeiro tenta o caminho "inteligente": estende o dropdown
 * oficial de etapas (etapasOficiais) para incluir a nova etapa (preserva a
 * validação para uso manual futuro na planilha). Se, por qualquer motivo, a
 * validação da célula não for um dropdown simples (ex.: fórmula customizada,
 * validação legada) e a escrita ainda assim falhar, cai em um fallback que
 * remove qualquer validação da célula e tenta de novo. Sempre confirma a
 * gravação lendo o valor de volta.
 *
 * etapasOficiais é opcional — omitir preserva o comportamento original
 * (dropdown de Leads, _LEAD_ETAPAS_OFICIAIS).
 *
 * Retorna true/false — nunca deixa "Selecione uma opção da lista." (ou
 * qualquer outra exceção de validação) subir sem tratamento.
 */
function _writeEtapaVerified(sheet, row, col, novaEtapa, etapasOficiais) {
  var cell = sheet.getRange(row, col);

  try {
    _ensureLeadStageValidationAllows(cell, novaEtapa, etapasOficiais);
    cell.setValue(novaEtapa);
    SpreadsheetApp.flush();
  } catch (e1) {
    try {
      cell.clearDataValidations();
      cell.setValue(novaEtapa);
      SpreadsheetApp.flush();
    } catch (e2) {
      return false;
    }
  }

  var gravado = String(cell.getDisplayValue() || cell.getValue() || "").trim();
  return gravado === String(novaEtapa).trim();
}

function _getWorkbook() {
  var spreadsheetId = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  if (spreadsheetId && String(spreadsheetId).trim()) {
    return SpreadsheetApp.openById(String(spreadsheetId).trim());
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss) {
    throw new Error("SPREADSHEET_ID não configurado e nenhuma planilha ativa encontrada.");
  }
  return ss;
}

function _getOrCreateSheet(name) {
  var ss    = _getWorkbook();
  var sheet = ss.getSheetByName(name);
  if (!sheet) sheet = ss.insertSheet(name);
  return sheet;
}

/**
 * Operação diária (M14.3A, correção final): NUNCA cria aba. Retorna a aba se
 * ela já existir, ou null. Instalação/configuração estrutural é ação
 * explícita separada (ex.: soprolife-sheets-template.gs) — nunca um efeito
 * colateral de uma escrita comum que pode falhar por outro motivo.
 */
function _getSheetSemCriar(name) {
  var ss = _getWorkbook();
  return ss.getSheetByName(name);
}

/**
 * Aplica um cabeçalho a uma aba apenas se ela ainda não tiver dados —
 * nunca sobrescreve um cabeçalho já existente (reaproveita abas existentes).
 */
function _ensureSheetHeader(sheet, headers) {
  if (sheet.getLastRow() === 0 || String(sheet.getRange(1, 1).getValue() || "").trim() === "") {
    var range = sheet.getRange(1, 1, 1, headers.length);
    try {
      range.clearDataValidations();
      range
        .setValues([headers])
        .setFontWeight("bold")
        .setFontColor("#ffffff")
        .setBackground("#08243d");
      SpreadsheetApp.flush();
    } catch (e) {
      throw new Error("Falha ao criar cabeçalho na aba '" + sheet.getName() + "': " + e.message);
    }
  }
}

/**
 * Garante que as colunas em extraHeaders existam no cabeçalho da aba,
 * adicionando-as ao final se ausentes — nunca reordena/remove colunas antigas.
 * Retorna o array de cabeçalhos (antigo + novo).
 */
function _ensureExtraColumns(sheet, extraHeaders) {
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  extraHeaders.forEach(function(h) {
    if (headers.indexOf(h) < 0) {
      lastCol += 1;
      var headerCell = sheet.getRange(1, lastCol);
      try {
        headerCell.clearDataValidations();
        headerCell.setValue(h);
        SpreadsheetApp.flush();
      } catch (e) {
        throw new Error("Falha ao criar coluna '" + h + "' na aba '" + sheet.getName() + "': " + e.message);
      }
      headers.push(h);
    }
  });
  return headers;
}

/**
 * Localiza uma linha pelo valor de uma coluna identificada por nome (não por posição fixa).
 * Retorna { rowIdx (1-based), headers, rowData } ou null se não encontrado.
 */
function _findRowByIdColumn(sheet, idColName, targetId) {
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) return null;

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });
  var idIdx   = headers.indexOf(idColName);
  if (idIdx < 0) return null;

  for (var i = 1; i < allData.length; i++) {
    if (String(allData[i][idIdx] || "").trim() === targetId) {
      return { rowIdx: i + 1, headers: headers, rowData: allData[i] };
    }
  }
  return null;
}

/**
 * Localiza a linha (1-based) cujas colunas batem TODAS com matchMap
 * (ex.: { lead_id: "LEAD-...", entidade_id: "CLIN-006" }). Usado para upsert
 * idempotente (evitar duplicatas) em abas onde a chave é composta por mais de
 * uma coluna. Se alguma coluna de matchMap não existir no cabeçalho, retorna
 * -1 (não há como garantir dedup sem a coluna — o chamador deve então
 * acrescentar uma linha nova, nunca sobrescrever algo incerto).
 */
function _findRowByColumns(sheet, headers, matchMap) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return -1;

  var colIdxs = {};
  var keys = Object.keys(matchMap);
  for (var k = 0; k < keys.length; k++) {
    var idx = headers.indexOf(keys[k]);
    if (idx < 0) return -1;
    colIdxs[keys[k]] = idx;
  }

  var lastCol = sheet.getLastColumn();
  var data = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  for (var i = 0; i < data.length; i++) {
    var match = true;
    for (var j = 0; j < keys.length; j++) {
      var col = keys[j];
      var v = String(data[i][colIdxs[col]] || "").trim();
      if (v !== String(matchMap[col]).trim()) { match = false; break; }
    }
    if (match) return i + 2; // 1-based, +1 para header +1 para índice 0-based
  }
  return -1;
}

/**
 * Ação de limpeza EXPLÍCITA e segura para remover duplicatas em
 * "Leads Convertidos" causadas por chamadas repetidas de vincularLeadAClinica
 * antes da deduplicação (ex.: múltiplos testes com curl). NUNCA apaga nada
 * automaticamente: por padrão roda em modo dry-run (só relata o que faria);
 * só remove de fato se data.confirm === true.
 *
 * Mantém sempre a ocorrência MAIS RECENTE (maior índice de linha — a inserida
 * por último) e remove as anteriores. Nunca remove a única ocorrência de um
 * lead (não há duplicata para remover nesse caso).
 *
 * data esperado:
 *   lead_id     (obrigatório) — remove duplicatas apenas deste lead
 *   clinica_id  (opcional)    — restringe a duplicatas lead_id + esta clínica
 *                                (coluna entidade_id); sem isso, considera
 *                                qualquer duplicata do mesmo lead_id
 *   confirm     (opcional, default false) — true para remover de fato
 */
/**
 * Conta quantas células de uma linha estão preenchidas — usado para escolher
 * a linha "mais completa" entre duplicatas (não apenas a mais recente).
 */
function _rowCompleteness(rowValues) {
  var n = 0;
  for (var i = 0; i < rowValues.length; i++) {
    var v = rowValues[i];
    if (v !== "" && v !== null && v !== undefined) n++;
  }
  return n;
}

/**
 * data esperado:
 *   lead_id     (obrigatório) — remove duplicatas apenas deste lead
 *   clinica_id  (opcional)    — restringe a duplicatas lead_id + esta clínica
 *                                (coluna entidade_id); sem isso, considera
 *                                qualquer duplicata do mesmo lead_id
 *   dryRun      (opcional, default true)  — false para remover de fato
 *   confirm     (opcional, alias legado)  — confirm:true equivale a dryRun:false
 *
 * Critério de escolha da linha a manter: primeiro a MAIS COMPLETA (mais
 * células preenchidas); em empate, a MAIS RECENTE (maior índice de linha).
 * Antes de remover cada duplicata, registra seus dados-chave (lead_id,
 * entidade_id, etapa) em "Log Conversões Leads" — o histórico não é
 * simplesmente apagado, fica rastreável ali.
 */
function _dedupLeadsConvertidos(data) {
  _required(data, ["lead_id"]);
  var leadId    = String(data.lead_id).trim();
  var clinicaId = data.clinica_id ? String(data.clinica_id).trim() : null;
  // dryRun é o nome pedido pela regra de negócio; confirm:true é aceito como
  // alias legado (usado em testes anteriores desta mesma sessão de trabalho).
  var dryRun = data.dryRun === false || data.confirm === true ? false : true;

  // M14.3A (2ª rodada) — o modo de APLICAÇÃO está BLOQUEADO: exclusão de
  // linha, mesmo duplicata, é manutenção destrutiva e exige backup validado,
  // manifesto e aprovação explícita (M14.3B). Só o dry-run (relatório) roda.
  if (!dryRun) {
    return _err(
      "dedupLeadsConvertidos com aplicação está BLOQUEADO pela M14.3A: exclusão de " +
      "linhas exige backup validado + aprovação explícita (M14.3B). Use dryRun:true " +
      "para ver o relatório de duplicatas — nada foi excluído.", 423);
  }

  var sheet   = _getOrCreateSheet(_SHEETS.LEADS_CONVERTIDOS);
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  if (lastRow < 2 || lastCol < 1) {
    return _ok({ message: "Nenhum registro em " + _SHEETS.LEADS_CONVERTIDOS + ".", removed: 0, dryRun: dryRun });
  }

  var allData = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var headers = allData[0].map(function(h) { return String(h).trim(); });
  var idIdx    = headers.indexOf("lead_id");
  var entIdx   = headers.indexOf("entidade_id");
  var etapaIdx = headers.indexOf("etapa");
  if (idIdx < 0) return _err("Coluna lead_id não encontrada em " + _SHEETS.LEADS_CONVERTIDOS + ".", 500);

  var matching = []; // { rowNum (1-based), completeness, values }
  for (var i = 1; i < allData.length; i++) {
    var rowLeadId = String(allData[i][idIdx] || "").trim();
    if (rowLeadId !== leadId) continue;
    if (clinicaId) {
      if (entIdx < 0) continue; // sem coluna entidade_id, não dá pra filtrar por clínica
      var rowEnt = String(allData[i][entIdx] || "").trim();
      if (rowEnt !== clinicaId) continue;
    }
    matching.push({ rowNum: i + 1, completeness: _rowCompleteness(allData[i]), values: allData[i] });
  }

  if (matching.length <= 1) {
    return _ok({
      message: "Nenhuma duplicata encontrada para " + leadId + (clinicaId ? " / " + clinicaId : "") + ".",
      removed: 0,
      dryRun: dryRun,
    });
  }

  // Ordena por completude desc, depois por linha (mais recente) desc.
  // O primeiro da lista ordenada é o que fica; os demais são duplicatas.
  var ordenado = matching.slice().sort(function(a, b) {
    if (b.completeness !== a.completeness) return b.completeness - a.completeness;
    return b.rowNum - a.rowNum;
  });
  var keep      = ordenado[0];
  var toRemove  = ordenado.slice(1);

  // M14.3A (2ª rodada): sempre RELATÓRIO — o braço de exclusão foi removido
  // (a aplicação real exige backup validado + aprovação, M14.3B; ver o
  // bloqueio no início da função). Nenhuma linha é excluída por esta ação.
  return _ok({
    message: "Relatório de duplicatas (somente leitura) — a exclusão está bloqueada até a M14.3B.",
    dryRun: true,
    lead_id: leadId,
    linhas_duplicadas: toRemove.length,
    manteria_linha: keep.rowNum,
    manteria_motivo: "mais completa (" + keep.completeness + " campos preenchidos)",
    removeria_linhas: toRemove.map(function(r) { return r.rowNum; }),
  });
}

/**
 * Organiza a apresentação visual das abas do Command Center — SEM alterar
 * nenhum valor de célula. Só mexe em: linha congelada, filtro básico, largura
 * de coluna, destaque de cabeçalho (formatação, não texto), ordem de colunas
 * técnicas (sempre para o final, nunca reordena colunas de dados originais) e
 * visibilidade da aba (mostrar operacionais, ocultar técnicas — reportado
 * sempre, mesmo em dry-run).
 *
 * data esperado:
 *   dryRun (opcional, default true) — false para aplicar de fato
 *
 * Retorna um relatório por aba com as ações identificadas/aplicadas, para que
 * dryRun:true sirva como preview fiel do que dryRun:false faria.
 */
function _organizarAbasCommandCenter(data) {
  data = data || {};
  var dryRun = data.dryRun === false ? false : true;

  var ss = _getWorkbook();
  var relatorio = [];

  _ABAS_OPERACIONAIS.forEach(function(nome) {
    relatorio.push(_organizarAba(ss, nome, {
      tornarVisivel: true,
      colunasTecnicas: nome === _SHEETS.LEADS ? _LEAD_COLUNAS_EXTRA_B2B : null,
      dryRun: dryRun,
    }));
  });

  _ABAS_TECNICAS_OCULTAVEIS.forEach(function(nome) {
    relatorio.push(_organizarAba(ss, nome, {
      tornarVisivel: false,
      colunasTecnicas: null,
      dryRun: dryRun,
    }));
  });

  _logEntry({
    acao:   "organizarAbasCommandCenter",
    status: dryRun ? "DRY-RUN" : "OK",
    aba:    "múltiplas",
    id:     "",
    resumo: (dryRun ? "Simulação" : "Execução") + " de organização — " + relatorio.length + " abas avaliadas.",
  });

  return _ok({ dryRun: dryRun, abas: relatorio });
}

/**
 * Avalia (e, se !dryRun, aplica) a organização de uma única aba.
 * Nunca cria a aba se ela não existir — só organiza o que já existe.
 */
function _organizarAba(ss, nome, opts) {
  var sheet = ss.getSheetByName(nome);
  if (!sheet) {
    return { aba: nome, existe: false, acoes: [], mensagem: "Aba não encontrada — nada a organizar." };
  }

  var acoes  = [];
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  var temDados = lastRow >= 1 && lastCol >= 1;

  // 1) Congelar linha 1 (cabeçalho sempre visível ao rolar)
  if (temDados && sheet.getFrozenRows() < 1) {
    acoes.push("congelar_linha_1");
    if (!opts.dryRun) sheet.setFrozenRows(1);
  }

  // 2) Filtro básico, se ainda não houver
  if (temDados && lastRow >= 2 && sheet.getFilter() === null) {
    acoes.push("criar_filtro");
    if (!opts.dryRun) {
      try { sheet.getDataRange().createFilter(); } catch (e) { /* algumas abas não suportam filtro — ignora */ }
    }
  }

  // 3) Destacar cabeçalho — formatação apenas, nunca altera o texto/valor
  if (temDados) {
    var headerRange = sheet.getRange(1, 1, 1, lastCol);
    if (headerRange.getFontWeight() !== "bold") {
      acoes.push("destacar_cabecalho");
      if (!opts.dryRun) {
        headerRange.setFontWeight("bold").setBackground("#0d2b4a").setFontColor("#ffffff");
      }
    }
  }

  // 4) Ajustar largura básica de colunas (autoResize — nunca perde dados)
  if (temDados) {
    acoes.push("ajustar_largura_colunas");
    if (!opts.dryRun) {
      try { sheet.autoResizeColumns(1, lastCol); } catch (e) { /* ignora falha pontual de autoresize */ }
    }
  }

  // 5) Mover colunas técnicas para o final, só quando fora de ordem
  if (opts.colunasTecnicas && temDados) {
    var plano = _planReorderTecnicas(sheet, opts.colunasTecnicas);
    if (plano.needed) {
      acoes.push("reordenar_colunas_tecnicas");
      if (!opts.dryRun) _executarReorderTecnicas(sheet, opts.colunasTecnicas);
    }
  }

  // 6) Visibilidade — sempre relatado, mesmo em dry-run
  var estavaOculta = sheet.isSheetHidden();
  if (opts.tornarVisivel === true && estavaOculta) {
    acoes.push("tornar_visivel");
    if (!opts.dryRun) sheet.showSheet();
  } else if (opts.tornarVisivel === false && !estavaOculta) {
    acoes.push("ocultar");
    if (!opts.dryRun) sheet.hideSheet();
  }

  return {
    aba: nome,
    existe: true,
    linhas: lastRow,
    colunas: lastCol,
    oculta_atualmente: estavaOculta,
    acoes: acoes,
    mudou: acoes.length > 0,
  };
}

/**
 * Verifica se as colunas técnicas já estão contíguas no final da aba.
 * Retorna { needed: bool } — needed=true significa que pelo menos uma delas
 * está fora da posição esperada e precisaria ser movida.
 */
function _planReorderTecnicas(sheet, colunasTecnicas) {
  var lastCol = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function(h) { return String(h).trim(); });
  var idxs = colunasTecnicas
    .map(function(c) { return headers.indexOf(c); })
    .filter(function(i) { return i >= 0; });
  if (idxs.length === 0) return { needed: false };

  var expectedStart = lastCol - idxs.length; // 0-based
  var sortedIdxs = idxs.slice().sort(function(a, b) { return a - b; });
  var contiguousAtEnd = sortedIdxs.every(function(idx, i) { return idx === expectedStart + i; });
  return { needed: !contiguousAtEnd };
}

/**
 * Move cada coluna técnica (na ordem definida em colunasTecnicas) para o
 * final da aba, uma de cada vez. Reconsulta o cabeçalho a cada iteração,
 * porque os índices mudam conforme as colunas anteriores são movidas.
 * Nunca apaga ou reescreve valores — apenas reordena colunas inteiras.
 */
function _executarReorderTecnicas(sheet, colunasTecnicas) {
  colunasTecnicas.forEach(function(nomeCol) {
    var lastCol = sheet.getLastColumn();
    var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function(h) { return String(h).trim(); });
    var idx = headers.indexOf(nomeCol);
    if (idx < 0) return; // coluna não existe nesta aba — pula
    var colPos = idx + 1; // 1-based
    if (colPos === lastCol) return; // já está na última posição
    var lastRowForMove = Math.max(sheet.getLastRow(), 1);
    var range = sheet.getRange(1, colPos, lastRowForMove, 1);
    sheet.moveColumns(range, lastCol + 1);
  });
}

// M14.3A — o antigo _nextId (contador sequencial derivado de lastRow, sem
// LockService) foi REMOVIDO: era vulnerável a concorrência e a linha
// removida/reordenada. Novos IDs vêm de ctNovoIdServidor (contratos-
// canonicos.gs): prefixo de entidade + UUID opaco emitido pelo servidor.
// IDs sequenciais históricos (PAC-0001, CON-0001, FIN-0001...) permanecem
// válidos e nunca são recalculados.

function _buildRow(sheet, fieldMap) {
  var lastCol  = Math.max(sheet.getLastColumn(), 1);
  var headers  = sheet.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function(h) { return String(h).trim(); });

  return headers.map(function(h) {
    if (h === "observacao_privada_minima") return ""; // nunca escrevemos aqui
    return fieldMap.hasOwnProperty(h) ? String(fieldMap[h]) : "";
  });
}

/**
 * Garante que colunas calculadas por fórmula (ex.: receita_bruta/custo_total/
 * resultado_liquido da aba Parceria Pastore - Atendimentos) continuem com
 * fórmula na linha recém-inserida. appendRow/setValues NUNCA herda fórmula da
 * linha de cima automaticamente — por isso copiamos explicitamente a fórmula
 * da linha anterior (copyTo ajusta as referências relativas sozinho). Se a
 * linha anterior também não tiver fórmula (ex.: primeira linha de dados),
 * não faz nada — não inventa fórmula do zero.
 */
function _ensureFormulaColumns(sheet, newRow, cols) {
  if (newRow < 2) return;

  cols.forEach(function(col) {
    var newCell = sheet.getRange(newRow, col);
    if (newCell.getFormula()) return;

    if (newRow >= 3) {
      var aboveCell = sheet.getRange(newRow - 1, col);
      if (aboveCell.getFormula()) {
        aboveCell.copyTo(newCell, SpreadsheetApp.CopyPasteType.PASTE_FORMULA, false);
        return;
      }
    }

    // Fallback explícito para a primeira linha real de dados da Pastore.
    // Fórmulas validadas nesta planilha: funções em inglês + separador ';'.
    if (sheet.getName() === _SHEETS.PARCERIA_PASTORE_ATENDIMENTOS) {
      if (col === 19) {
        newCell.setFormula('=IF(K' + newRow + '="";"";K' + newRow + ')');
      } else if (col === 20) {
        newCell.setFormula('=IF(COUNTA(N' + newRow + ':R' + newRow + ')=0;"";SUM(N' + newRow + ':R' + newRow + '))');
      } else if (col === 21) {
        newCell.setFormula('=IF(S' + newRow + '="";"";S' + newRow + '-IF(T' + newRow + '="";0;T' + newRow + '))');
      }
    }
  });

  SpreadsheetApp.flush();
}

function _nowBr() {
  return Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy HH:mm");
}

function _normalizeConsent(val) {
  if (!val) return "Não informado";
  var v = String(val).toLowerCase().trim();
  if (["sim", "s", "yes", "1", "aceito", "ok", "true"].indexOf(v) >= 0) return "Sim";
  if (["não", "nao", "n", "no", "0", "recusa", "false"].indexOf(v) >= 0) return "Não";
  return "Não informado";
}

function _joinNotEmpty(a, b) {
  return [a, b].filter(function(x) { return x && String(x).trim(); }).join(" / ");
}

function _keyNorm(name) {
  return String(name)
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
}

function _required(data, fields) {
  var missing = fields.filter(function(f) { return !data[f] || !String(data[f]).trim(); });
  if (missing.length > 0) throw new Error("Campos obrigatórios ausentes: " + missing.join(", "));
}

function _logEntry(entry) {
  try {
    var sheet = _getOrCreateSheet(_SHEETS.LOG);

    if (sheet.getLastRow() < 1) {
      _appendRowSemValidacao(sheet, ["timestamp", "acao", "status", "aba_afetada", "id_gerado", "email_executor", "resumo"]);
      sheet.getRange(1, 1, 1, 7).setFontWeight("bold");
    }

    var email = "";
    try { email = Session.getActiveUser().getEmail() || ""; } catch (_) {}

    _appendRowSemValidacao(sheet, [
      _nowBr(),
      entry.acao    || "",
      entry.status  || "",
      entry.aba     || "",
      entry.id      || "",
      email,
      entry.resumo  || "",
      // telefone e observacao NUNCA registrados aqui
    ]);
  } catch (_) {
    // Silencioso: falha no log não deve interromper a ação principal
  }
}

// ── Auditoria mínima das escritas (M1 — Etapa 1) ──────────────────────────────
//
// Base da trilha de auditoria append-only na aba "Log Auditoria".
// Etapa 2: _updateLeadStage, _updateCrmClinicaEtapa, _mirrorEtapaParaPcmso
// (evento derivado, com valor anterior/novo da etapa).
// Etapa 3: creates, _registrarInteracao*, _registrarAtendimentoPastore e
// _upsertContatoB2B — via _auditAcao, só ação/tipo/ID/resultado, nunca
// conteúdo de campo. Pendente: _vincularLeadAClinica (decidir na Etapa 4+).
// Metadados de origem/operador: data.audit_origem / data.audit_operador
// (namespace audit_* para não colidir com o campo de negócio "origem" dos
// creates) — injetados pelo proxy (Etapa 4: instanceName/operatorName da
// config local, sobrescrevendo o browser) e sanitizados aqui por
// _auditShortMeta (limite de tamanho + fallback "desconhecido/a").
//
// Regras invioláveis:
//   - O log registra transição de estado, nunca conteúdo sensível.
//   - Allowlist default-fechada: campo fora de _AUDIT_CAMPOS_PERMITIDOS tem
//     valor_anterior/valor_novo substituídos por "[campo privado]" — o log
//     registra QUE o campo mudou, nunca PARA O QUÊ.
//   - Telefone, nome de paciente, observação livre, CPF, WhatsApp, laudo ou
//     pedido médico NUNCA são gravados — nem truncados, nem mascarados.
//   - Append-only: esta seção só adiciona linhas, nunca edita nem apaga.
//   - Falha de auditoria nunca interrompe a operação principal.

var _LOG_AUDITORIA_CABECALHO = [
  "log_id", "request_id", "timestamp", "acao", "entidade_tipo", "entidade_id",
  "campo", "valor_anterior", "valor_novo", "origem", "operador", "trigger",
  "resultado", "derivado_de", "duration_ms", "build_version",
];

// Campos cujo VALOR pode aparecer em valor_anterior/valor_novo.
// Só campos de dropdown/enum ou datas — nunca texto livre nem PII.
// Campo novo no futuro nasce protegido por padrão (default-fechado).
var _AUDIT_CAMPOS_PERMITIDOS = [
  "etapa",
  "status",
  "prioridade",
  "responsavel",           // membro da equipe, nunca paciente
  "status_relacionamento",
  "followup_status",
  "data_proxima_acao",
  "ultima_interacao",
];

var _AUDIT_VALOR_PRIVADO = "[campo privado]";

// Gravado em build_version quando a propriedade BUILD_VERSION não existe.
// Ao publicar nova versão do Web App, atualizar a propriedade BUILD_VERSION
// (Arquivo → Propriedades do projeto) com a versão ou o git sha do repo.
var _AUDIT_BUILD_VERSION_FALLBACK = "cc-api-m1";

/**
 * Sanitiza um valor de campo para valor_anterior/valor_novo.
 * Allowlist default-fechada + defesa em profundidade: mesmo em campo
 * permitido, valor que pareça telefone/CPF/CNPJ (dado colado na célula
 * errada) é suprimido. Datas dd/MM/yyyy e ISO são liberadas.
 */
function _auditSanitizeValor(campo, valor) {
  var v = String(valor === undefined || valor === null ? "" : valor).trim();
  if (v === "") return "";
  if (_AUDIT_CAMPOS_PERMITIDOS.indexOf(String(campo || "").trim()) < 0) {
    return _AUDIT_VALOR_PRIVADO;
  }
  var isData = /^\d{2}\/\d{2}\/\d{4}( \d{2}:\d{2})?$/.test(v) ||
               /^\d{4}-\d{2}-\d{2}$/.test(v);
  if (!isData && v.replace(/\D/g, "").length >= 8) return _AUDIT_VALOR_PRIVADO;
  if (v.length > 80) v = v.slice(0, 77) + "...";
  return v;
}

/**
 * Sanitiza metadado curto do log (acao, origem, operador, trigger, ids):
 * string sem quebra de linha, limitada, com fallback quando vazia.
 */
function _auditShortMeta(valor, fallback, maxLen) {
  var v = String(valor === undefined || valor === null ? "" : valor)
    .replace(/[\r\n\t]+/g, " ").trim();
  if (v === "") return fallback || "";
  var max = maxLen || 40;
  return v.length > max ? v.slice(0, max) : v;
}

/**
 * Atalho de auditoria para ações SEM conteúdo de campo (creates, registros,
 * upserts): grava só ação/tipo/ID/resultado + metadados. O conteúdo criado
 * já está na linha da entidade — repeti-lo no log duplicaria dado sensível.
 * requestId null/vazio → _logAudit gera UUID próprio.
 * data null (funções internas sem audit_*) → origem/operador caem no
 * fallback "desconhecida"/"desconhecido" de _logAudit.
 */
function _auditAcao(requestId, t0, acao, entidadeTipo, entidadeId, data, resultado, derivadoDe) {
  _logAudit({
    requestId:    requestId,
    acao:         acao,
    entidadeTipo: entidadeTipo,
    entidadeId:   entidadeId,
    origem:       data ? data.audit_origem : "",
    operador:     data ? data.audit_operador : "",
    trigger:      "web_app",
    resultado:    resultado,
    derivadoDe:   derivadoDe,
    durationMs:   Date.now() - t0,
  });
}

/**
 * Registra uma linha na aba "Log Auditoria" (append-only). Recebe:
 *   { requestId, acao, entidadeTipo, entidadeId, campo, valorAnterior,
 *     valorNovo, origem, operador, trigger, resultado, derivadoDe, durationMs }
 *
 * entidadeId é sempre o ID (LEAD-/CLI-/etc.), nunca nome de pessoa.
 * Falha aqui NUNCA propaga para a operação principal — o erro é registrado
 * apenas com Logger.log (diferente de _logEntry, que é 100% silencioso:
 * aqui o erro precisa ser diagnosticável).
 */
function _logAudit(info) {
  try {
    info = info || {};
    var sheet = _getOrCreateSheet(_SHEETS.LOG_AUDITORIA);
    _ensureSheetHeader(sheet, _LOG_AUDITORIA_CABECALHO);

    var buildVersion = _AUDIT_BUILD_VERSION_FALLBACK;
    try {
      var prop = PropertiesService.getScriptProperties().getProperty("BUILD_VERSION");
      if (prop && String(prop).trim()) buildVersion = String(prop).trim();
    } catch (_) {}

    var requestId = _auditShortMeta(info.requestId, "", 64);
    if (!requestId) requestId = Utilities.getUuid();

    var campo      = _auditShortMeta(info.campo, "", 40);
    var durationMs = parseInt(info.durationMs, 10);

    _appendRowSemValidacao(sheet, [
      ctNovoIdServidor("AUD", Utilities.getUuid()),
      requestId,
      _nowBr(),
      _auditShortMeta(info.acao,         "desconhecida", 40),
      _auditShortMeta(info.entidadeTipo, "desconhecido", 40),
      _auditShortMeta(info.entidadeId,   "",             40),
      campo,
      _auditSanitizeValor(campo, info.valorAnterior),
      _auditSanitizeValor(campo, info.valorNovo),
      _auditShortMeta(info.origem,   "desconhecida", 40),
      _auditShortMeta(info.operador, "desconhecido", 40),
      _auditShortMeta(info.trigger,  "desconhecido", 40),
      _auditShortMeta(info.resultado, "ok", 80),
      _auditShortMeta(info.derivadoDe, "", 40),
      isNaN(durationMs) ? "" : durationMs,
      _auditShortMeta(buildVersion, _AUDIT_BUILD_VERSION_FALLBACK, 40),
    ]);
  } catch (e) {
    // Falha de auditoria nunca interrompe a operação principal.
    try {
      Logger.log("Falha ao gravar Log Auditoria: " + (e && e.message ? e.message : e));
    } catch (_) {}
  }
}

/**
 * Teste manual de _logAudit — executar no editor do Apps Script (nunca via
 * HTTP). Grava duas linhas evidentes de teste na aba Log Auditoria:
 *   1. campo permitido (etapa) — valores devem aparecer legíveis;
 *   2. campo proibido (observacao) — valores devem virar "[campo privado]".
 * Apagar as linhas manualmente após validar, conforme a regra de teste
 * de soprolife-sheets-sync (linha fictícia evidente → validar → remover).
 */
function _testLogAudit() {
  _logAudit({
    acao:          "teste_apagar",
    entidadeTipo:  "teste",
    entidadeId:    "TESTE-APAGAR-0001",
    campo:         "etapa",
    valorAnterior: "Em conversa",
    valorNovo:     "Agendado",
    origem:        "editor-apps-script",
    operador:      "teste-manual",
    trigger:       "manual_editor",
    resultado:     "ok",
    durationMs:    12,
  });
  _logAudit({
    acao:          "teste_apagar",
    entidadeTipo:  "teste",
    entidadeId:    "TESTE-APAGAR-0002",
    campo:         "observacao",
    valorAnterior: "conteudo que nao pode aparecer",
    valorNovo:     "conteudo que nao pode aparecer",
    origem:        "editor-apps-script",
    operador:      "teste-manual",
    trigger:       "manual_editor",
    resultado:     "ok",
  });
  Logger.log("Duas linhas de teste gravadas em '" + _SHEETS.LOG_AUDITORIA + "' — validar e apagar.");
}

function _ok(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(Object.assign({ ok: true }, payload)))
    .setMimeType(ContentService.MimeType.JSON);
}

function _err(message, code, extra) {
  return ContentService
    .createTextOutput(JSON.stringify(Object.assign({ ok: false, error: message, code: code || 400 }, extra || {})))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Teste manual (executar no editor do Apps Script, nunca via HTTP) ──────────

function _testCreatePaciente() {
  var token = PropertiesService.getScriptProperties().getProperty("API_TOKEN");
  if (!token) throw new Error("API_TOKEN não configurado. Defina em Arquivo → Propriedades do projeto → Propriedades do script.");
  var mock  = {
    postData: {
      contents: JSON.stringify({
        token:  token,
        action: "createPaciente",
        data: {
          primeiro_nome:          "Teste",
          telefone:               "",
          canal:                  "Indicação",
          origem:                 "Teste manual",
          consentimento_whatsapp: "Sim",
          responsavel:            "Adeildo",
        },
      }),
    },
  };
  Logger.log(doPost(mock).getContent());
}
