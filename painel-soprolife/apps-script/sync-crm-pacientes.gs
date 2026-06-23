/**
 * sync-crm-pacientes.gs
 * Versão: 1.0  |  SoproLife Command Center
 *
 * Consolida a aba "CRM Pacientes" (carteira-mãe) a partir de
 * "CRM Espirometria" e "CRM Consultas".
 *
 * SEGURANÇA — o que NÃO está neste arquivo:
 *   - ID ou URL da planilha real
 *   - tokens, senhas ou chaves de API
 *   - dados reais de pacientes
 *   - CPF, telefone real, pedido médico ou dado clínico identificável
 *
 * COMO EXECUTAR:
 *   1. Abra a planilha "SoproLife - Painel Interno - Dados Privados".
 *   2. Extensões → Apps Script → cole (ou abra) este arquivo.
 *   3. Selecione a função sincronizarCRMPacientesSoproLife e clique em "Executar".
 *   4. Para agendar: Gatilhos → Adicionar gatilho → periodicidade desejada.
 */

// ── Nomes de aba (únicos ponto de verdade) ─────────────────────────────────

var _ABA = {
  PACIENTES:    "CRM Pacientes",
  ESPIROMETRIA: "CRM Espirometria",
  CONSULTAS:    "CRM Consultas",
};

// ── Cabeçalho canônico de CRM Pacientes ───────────────────────────────────

var _HEADERS_PACIENTES = [
  "paciente_id",
  "data_cadastro",
  "primeiro_nome",
  "telefone",
  "ultimo_servico",
  "status_relacionamento",
  "proximo_contato",
  "motivo_proximo_contato",
  "canal",
  "responsavel",
  "consentimento_whatsapp",
  "observacao_privada_minima",
];

// ── Cabeçalhos esperados nas abas de origem ────────────────────────────────

var _HEADERS_ESPI = [
  "exame_id", "data_entrada", "primeiro_nome", "telefone",
  "servico", "origem", "status_exame", "data_exame",
  "proximo_contato", "motivo_proximo_contato", "canal",
  "responsavel", "consentimento_whatsapp",
];

var _HEADERS_CONS = [
  "consulta_id", "data_entrada", "primeiro_nome", "telefone",
  "origem", "tipo_consulta", "status", "medica",
  "data_consulta", "proximo_contato", "motivo_proximo_contato",
  "canal", "responsavel", "consentimento_whatsapp",
];

// ── Função principal ───────────────────────────────────────────────────────

/**
 * Consolida CRM Pacientes a partir de CRM Espirometria e CRM Consultas.
 * Preserva pacientes já existentes; cria ou atualiza conforme necessário.
 */
function sincronizarCRMPacientesSoproLife() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  Logger.log("=== sincronizarCRMPacientesSoproLife iniciado ===");

  // 1. Ler abas de origem
  var linhasEspi = _lerAba(ss, _ABA.ESPIROMETRIA, _HEADERS_ESPI);
  Logger.log("CRM Espirometria: " + linhasEspi.length + " registro(s) encontrado(s).");

  var linhasCons = _lerAba(ss, _ABA.CONSULTAS, _HEADERS_CONS);
  Logger.log("CRM Consultas: " + linhasCons.length + " registro(s) encontrado(s).");

  // 2. Ler CRM Pacientes atual
  var pacientesExistentes = _lerPacientesExistentes(ss);
  Logger.log("CRM Pacientes atual: " + pacientesExistentes.length + " registro(s).");

  // 3. Montar índice de pacientes existentes (chave → objeto)
  var indicePacientes = _indexarPacientes(pacientesExistentes);

  // 4. Processar registros de espirometria
  linhasEspi.forEach(function(row) {
    var chave = _chaveDeduplicacao(row.telefone, row.primeiro_nome);
    if (!chave) return;

    if (indicePacientes[chave]) {
      _mesclarEspirometria(indicePacientes[chave], row);
    } else {
      indicePacientes[chave] = _novoPacienteDeEspirometria(row);
    }
  });

  // 5. Processar registros de consultas
  linhasCons.forEach(function(row) {
    var chave = _chaveDeduplicacao(row.telefone, row.primeiro_nome);
    if (!chave) return;

    if (indicePacientes[chave]) {
      _mesclarConsulta(indicePacientes[chave], row);
    } else {
      indicePacientes[chave] = _novoPacienteDeConsulta(row);
    }
  });

  // 6. Gerar lista final ordenada (preservar ordem dos existentes + novos ao final)
  var pacientesFinais = _gerarListaFinal(indicePacientes, pacientesExistentes);

  // 7. Atribuir IDs para pacientes sem ID
  _atribuirIds(pacientesFinais);

  // 8. Reescrever CRM Pacientes
  _reescreverAbaPacientes(ss, pacientesFinais);

  Logger.log("CRM Pacientes atualizado: " + pacientesFinais.length + " paciente(s) consolidado(s).");
  Logger.log("=== sincronizarCRMPacientesSoproLife concluído ===");
}

// ── Leitura das abas ───────────────────────────────────────────────────────

function _lerAba(ss, nomeAba, headersEsperados) {
  var sheet = ss.getSheetByName(nomeAba);
  if (!sheet) {
    Logger.log("AVISO: aba '" + nomeAba + "' não encontrada. Ignorando.");
    return [];
  }

  var ultimaLinha = sheet.getLastRow();
  var ultimaCol   = sheet.getLastColumn();

  if (ultimaLinha < 2 || ultimaCol < 1) {
    Logger.log("AVISO: aba '" + nomeAba + "' vazia ou sem dados. Ignorando.");
    return [];
  }

  var dados   = sheet.getRange(1, 1, ultimaLinha, ultimaCol).getValues();
  var headers = dados[0].map(function(h) { return String(h).trim().toLowerCase(); });

  var resultado = [];
  for (var i = 1; i < dados.length; i++) {
    var row = dados[i];
    if (_linhaVazia(row)) continue;

    var obj = {};
    headersEsperados.forEach(function(campo) {
      var idx = headers.indexOf(campo.toLowerCase());
      obj[campo] = idx >= 0 ? String(row[idx] || "").trim() : "";
    });
    resultado.push(obj);
  }
  return resultado;
}

function _lerPacientesExistentes(ss) {
  var sheet = ss.getSheetByName(_ABA.PACIENTES);
  if (!sheet || sheet.getLastRow() < 2) return [];

  var ultimaCol = Math.max(sheet.getLastColumn(), _HEADERS_PACIENTES.length);
  var dados     = sheet.getRange(1, 1, sheet.getLastRow(), ultimaCol).getValues();
  var headers   = dados[0].map(function(h) { return String(h).trim().toLowerCase(); });

  var resultado = [];
  for (var i = 1; i < dados.length; i++) {
    var row = dados[i];
    if (_linhaVazia(row)) continue;

    var obj = {};
    _HEADERS_PACIENTES.forEach(function(campo) {
      var idx = headers.indexOf(campo.toLowerCase());
      obj[campo] = idx >= 0 ? String(row[idx] || "").trim() : "";
    });
    resultado.push(obj);
  }
  return resultado;
}

// ── Indexação e chave de deduplicação ─────────────────────────────────────

function _indexarPacientes(lista) {
  var indice = {};
  lista.forEach(function(p) {
    var chave = _chaveDeduplicacao(p.telefone, p.primeiro_nome);
    if (chave) indice[chave] = p;
  });
  return indice;
}

/**
 * Retorna a chave de deduplicação do paciente.
 * Prioridade: telefone normalizado → nome normalizado.
 */
function _chaveDeduplicacao(telefone, nome) {
  var tel = _normalizarTelefone(telefone);
  if (tel) return "tel:" + tel;

  var n = _normalizarNome(nome);
  if (n) return "nome:" + n;

  return null;
}

function _normalizarTelefone(tel) {
  if (!tel) return "";
  // Remove tudo que não é dígito
  return String(tel).replace(/\D/g, "");
}

function _normalizarNome(nome) {
  if (!nome) return "";
  return String(nome)
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
}

// ── Criação de novo paciente ───────────────────────────────────────────────

function _novoPacienteDeEspirometria(row) {
  return {
    paciente_id:            "",
    data_cadastro:          row.data_entrada || _hoje(),
    primeiro_nome:          row.primeiro_nome,
    telefone:               row.telefone,
    ultimo_servico:         "Espirometria",
    status_relacionamento:  "Em acompanhamento",
    proximo_contato:        row.proximo_contato,
    motivo_proximo_contato: row.motivo_proximo_contato,
    canal:                  row.canal || "WhatsApp",
    responsavel:            row.responsavel || "Não informado",
    consentimento_whatsapp: _normalizarConsentimento(row.consentimento_whatsapp),
    observacao_privada_minima: "",
    _origem_espi:           true,
    _origem_cons:           false,
    _tipo_consulta:         "",
  };
}

function _novoPacienteDeConsulta(row) {
  return {
    paciente_id:            "",
    data_cadastro:          row.data_entrada || _hoje(),
    primeiro_nome:          row.primeiro_nome,
    telefone:               row.telefone,
    ultimo_servico:         row.tipo_consulta || "Consulta",
    status_relacionamento:  "Em acompanhamento",
    proximo_contato:        row.proximo_contato,
    motivo_proximo_contato: row.motivo_proximo_contato,
    canal:                  row.canal || "WhatsApp",
    responsavel:            row.responsavel || "Não informado",
    consentimento_whatsapp: _normalizarConsentimento(row.consentimento_whatsapp),
    observacao_privada_minima: "",
    _origem_espi:           false,
    _origem_cons:           true,
    _tipo_consulta:         row.tipo_consulta,
  };
}

// ── Mesclagem com registro existente ──────────────────────────────────────

function _mesclarEspirometria(pac, row) {
  pac._origem_espi = true;

  // data_cadastro: manter a mais antiga
  pac.data_cadastro = _dataMaisAntiga(pac.data_cadastro, row.data_entrada);

  // telefone: preservar se já existir, senão usar da origem
  if (!pac.telefone && row.telefone) pac.telefone = row.telefone;

  // canal: preferir WhatsApp
  pac.canal = _escolherCanal(pac.canal, row.canal);

  // responsavel: preservar se já definido
  if (!pac.responsavel || pac.responsavel === "Não informado") {
    pac.responsavel = row.responsavel || "Não informado";
  }

  // consentimento: preservar positivo
  pac.consentimento_whatsapp = _mesclarConsentimento(pac.consentimento_whatsapp, row.consentimento_whatsapp);

  // proximo_contato: preservar o mais recente/útil
  if (!pac.proximo_contato && row.proximo_contato) pac.proximo_contato = row.proximo_contato;

  // motivo: combinar se ainda não tiver
  pac.motivo_proximo_contato = _combinarMotivos(
    pac.motivo_proximo_contato,
    row.motivo_proximo_contato
  );

  // ultimo_servico: calculado depois, ao gerar lista final
  _atualizarUltimoServico(pac);
}

function _mesclarConsulta(pac, row) {
  pac._origem_cons  = true;
  pac._tipo_consulta = pac._tipo_consulta || row.tipo_consulta || "";

  pac.data_cadastro = _dataMaisAntiga(pac.data_cadastro, row.data_entrada);

  if (!pac.telefone && row.telefone) pac.telefone = row.telefone;

  pac.canal = _escolherCanal(pac.canal, row.canal);

  if (!pac.responsavel || pac.responsavel === "Não informado") {
    pac.responsavel = row.responsavel || "Não informado";
  }

  pac.consentimento_whatsapp = _mesclarConsentimento(pac.consentimento_whatsapp, row.consentimento_whatsapp);

  if (!pac.proximo_contato && row.proximo_contato) pac.proximo_contato = row.proximo_contato;

  pac.motivo_proximo_contato = _combinarMotivos(
    pac.motivo_proximo_contato,
    row.motivo_proximo_contato
  );

  _atualizarUltimoServico(pac);
}

function _atualizarUltimoServico(pac) {
  if (pac._origem_espi && pac._origem_cons) {
    var tipoC = pac._tipo_consulta || "Consulta";
    pac.ultimo_servico = "Espirometria + " + tipoC;
  } else if (pac._origem_espi) {
    pac.ultimo_servico = "Espirometria";
  } else if (pac._origem_cons) {
    pac.ultimo_servico = pac._tipo_consulta || "Consulta";
  }
}

// ── Geração da lista final ─────────────────────────────────────────────────

/**
 * Monta a lista final mantendo a ordem dos pacientes já existentes,
 * adicionando novos ao final.
 */
function _gerarListaFinal(indicePacientes, pacientesExistentes) {
  var visto   = {};
  var final   = [];

  // Primeiro: percorrer na ordem dos existentes para preservar posição
  pacientesExistentes.forEach(function(p) {
    var chave = _chaveDeduplicacao(p.telefone, p.primeiro_nome);
    if (!chave) {
      // Paciente sem chave identificável: preservar como está
      final.push(p);
      return;
    }
    if (visto[chave]) return;
    visto[chave] = true;

    var atualizado = indicePacientes[chave];
    if (atualizado) {
      // Preservar observacao_privada_minima original se o índice não a trouxe
      if (!atualizado.observacao_privada_minima && p.observacao_privada_minima) {
        atualizado.observacao_privada_minima = p.observacao_privada_minima;
      }
      // Preservar paciente_id original
      if (p.paciente_id && !atualizado.paciente_id) {
        atualizado.paciente_id = p.paciente_id;
      }
      final.push(atualizado);
    } else {
      // Paciente existente que não aparece em nenhuma origem: preservar
      final.push(p);
    }
  });

  // Depois: adicionar novos (que não existiam em CRM Pacientes)
  Object.keys(indicePacientes).forEach(function(chave) {
    if (!visto[chave]) {
      final.push(indicePacientes[chave]);
    }
  });

  return final;
}

// ── Atribuição de IDs ──────────────────────────────────────────────────────

function _atribuirIds(lista) {
  var contadorDia = {};

  lista.forEach(function(p) {
    if (p.paciente_id && p.paciente_id.startsWith("PAC-")) return;

    var hoje = _hoje();       // YYYYMMDD
    var data = _extrairDataId(p.data_cadastro) || hoje;

    contadorDia[data] = (contadorDia[data] || 0) + 1;
    p.paciente_id = "PAC-" + data + "-" + String(contadorDia[data]).padStart(3, "0");
  });
}

function _extrairDataId(dataCadastro) {
  if (!dataCadastro) return null;

  // Tenta dd/MM/yyyy (formato BR)
  var m = String(dataCadastro).match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m) return m[3] + m[2] + m[1];

  // Tenta yyyy-MM-dd (ISO)
  var iso = String(dataCadastro).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) return iso[1] + iso[2] + iso[3];

  return null;
}

// ── Reescrita da aba CRM Pacientes ────────────────────────────────────────

function _reescreverAbaPacientes(ss, lista) {
  var sheet = ss.getSheetByName(_ABA.PACIENTES);
  if (!sheet) {
    sheet = ss.insertSheet(_ABA.PACIENTES);
    Logger.log("Aba '" + _ABA.PACIENTES + "' criada.");
  }

  // Limpar conteúdo
  sheet.clearContents();

  // Cabeçalho
  sheet.getRange(1, 1, 1, _HEADERS_PACIENTES.length).setValues([_HEADERS_PACIENTES]);
  sheet.getRange(1, 1, 1, _HEADERS_PACIENTES.length)
    .setFontWeight("bold")
    .setFontColor("#ffffff")
    .setBackground("#08243d");
  sheet.setFrozenRows(1);

  if (lista.length === 0) {
    Logger.log("Nenhum paciente para escrever.");
    SpreadsheetApp.flush();
    return;
  }

  // Dados
  var linhas = lista.map(function(p) {
    return _HEADERS_PACIENTES.map(function(campo) {
      // Campos internos de controle não vão para a planilha
      if (campo.charAt(0) === "_") return "";
      return p[campo] !== undefined ? String(p[campo]) : "";
    });
  });

  sheet.getRange(2, 1, linhas.length, _HEADERS_PACIENTES.length).setValues(linhas);
  sheet.setColumnWidth(1, 160);  // paciente_id
  sheet.autoResizeColumns(2, _HEADERS_PACIENTES.length - 1);

  SpreadsheetApp.flush();
  Logger.log("Aba '" + _ABA.PACIENTES + "' reescrita com " + lista.length + " linha(s).");
}

// ── Helpers de valor ──────────────────────────────────────────────────────

function _normalizarConsentimento(val) {
  if (!val) return "Não confirmado";
  var v = String(val).toLowerCase().trim();
  if (["sim", "s", "yes", "1", "aceito", "ok", "true"].indexOf(v) >= 0) return "Sim";
  if (["não", "nao", "n", "no", "0", "recusa", "false"].indexOf(v) >= 0) return "Não";
  return "Não confirmado";
}

function _mesclarConsentimento(atual, novo) {
  // "Sim" prevalece sobre qualquer coisa
  if (atual === "Sim" || _normalizarConsentimento(novo) === "Sim") return "Sim";
  if (atual === "Não" || _normalizarConsentimento(novo) === "Não") return "Não";
  return "Não confirmado";
}

function _escolherCanal(canalAtual, canalNovo) {
  if (canalAtual === "WhatsApp") return "WhatsApp";
  if (canalNovo  === "WhatsApp") return "WhatsApp";
  return canalAtual || canalNovo || "";
}

function _combinarMotivos(motivoAtual, motivoNovo) {
  var a = (motivoAtual || "").trim();
  var n = (motivoNovo  || "").trim();
  if (!a && !n) return "";
  if (!a)       return n;
  if (!n)       return a;
  if (a === n)  return a;
  return a + " / " + n;
}

/**
 * Compara duas datas (dd/MM/yyyy ou yyyy-MM-dd) e retorna a mais antiga.
 * Se alguma for inválida, retorna a outra.
 */
function _dataMaisAntiga(d1, d2) {
  var t1 = _dataParaTimestamp(d1);
  var t2 = _dataParaTimestamp(d2);
  if (t1 === null && t2 === null) return d1 || d2 || _hoje();
  if (t1 === null) return d2;
  if (t2 === null) return d1;
  return t1 <= t2 ? d1 : d2;
}

function _dataParaTimestamp(data) {
  if (!data || String(data).trim() === "") return null;

  // dd/MM/yyyy
  var m = String(data).match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m) {
    var d = new Date(parseInt(m[3]), parseInt(m[2]) - 1, parseInt(m[1]));
    return isNaN(d.getTime()) ? null : d.getTime();
  }

  // yyyy-MM-dd
  var iso = String(data).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) {
    var d2 = new Date(parseInt(iso[1]), parseInt(iso[2]) - 1, parseInt(iso[3]));
    return isNaN(d2.getTime()) ? null : d2.getTime();
  }

  return null;
}

function _hoje() {
  return Utilities.formatDate(new Date(), "America/Sao_Paulo", "dd/MM/yyyy");
}

function _linhaVazia(row) {
  return row.every(function(c) { return String(c || "").trim() === ""; });
}

// ── Menu de instalação ─────────────────────────────────────────────────────

/**
 * Adiciona item "Sincronizar CRM Pacientes" ao menu SoproLife.
 * Se o onOpen do soprolife-sheets-template.gs já existir, pode-se
 * substituí-lo por este ou combinar os dois manualmente.
 */
function onOpen_syncCRM() {
  SpreadsheetApp.getUi()
    .createMenu("SoproLife")
    .addItem("Sincronizar CRM Pacientes", "sincronizarCRMPacientesSoproLife")
    .addToUi();
}

// ── Teste manual ──────────────────────────────────────────────────────────

/**
 * Execute no editor do Apps Script para validar sem dados reais.
 * Requer que a planilha ativa tenha as abas de teste.
 */
function _testarSincronizacao() {
  Logger.log("Iniciando teste manual de sincronizarCRMPacientesSoproLife...");
  sincronizarCRMPacientesSoproLife();
  Logger.log("Teste concluído. Verifique a aba 'CRM Pacientes' e o Logger.log acima.");
}
