/**
 * SoproLife — Atualização automática do Resumo Dashboard
 *
 * Calcula indicadores agregados a partir das abas privadas da planilha
 * "SoproLife - Painel Interno - Dados Privados" e grava somente totais
 * numéricos na aba "Resumo Dashboard".
 *
 * SEGURANÇA:
 *   - Nunca grava nomes, telefones, CPF ou dados clínicos individuais.
 *   - Nunca grava nem envia ao painel nomes, telefones ou dados clínicos.
 *   - Usa dados privados apenas dentro da própria planilha para calcular totais agregados.
 *
 * Como usar:
 *   1. Abra a planilha privada no Google Sheets.
 *   2. Vá em Extensões → Apps Script.
 *   3. Cole este código e salve.
 *   4. Execute a função: atualizarResumoDashboardAutomaticoSoproLife
 *   5. No terminal local, rode:
 *        painel-soprolife/scripts/update-local-data.sh
 */

// ---------------------------------------------------------------------------
// Função principal
// ---------------------------------------------------------------------------

function atualizarResumoDashboardAutomaticoSoproLife() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // ─── Helpers ─────────────────────────────────────────────────────────────

  /** Retorna os dados de uma aba a partir da linha 2 (ignora cabeçalho). Os dados ficam apenas no Apps Script da própria planilha. */
  function getData(sheetName) {
    var sheet = ss.getSheetByName(sheetName);
    if (!sheet || sheet.getLastRow() < 2) return [];
    var lastRow = sheet.getLastRow();
    var lastCol = sheet.getLastColumn();
    if (lastCol < 1) return [];
    return sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  }

  /** Retorna os cabeçalhos da linha 1 em minúsculas. */
  function getHeaders(sheetName) {
    var sheet = ss.getSheetByName(sheetName);
    if (!sheet || sheet.getLastRow() < 1 || sheet.getLastColumn() < 1) return [];
    return sheet.getRange(1, 1, 1, sheet.getLastColumn())
      .getValues()[0]
      .map(function (h) { return String(h).toLowerCase().trim(); });
  }

  /**
   * Encontra o índice de uma coluna pelo nome (busca parcial, case-insensitive).
   * Retorna -1 se não encontrado.
   */
  function col(headers, term) {
    var t = term.toLowerCase();
    for (var i = 0; i < headers.length; i++) {
      if (headers[i].indexOf(t) !== -1) return i;
    }
    return -1;
  }

  /** Retorna true se a linha tiver ao menos um campo não-vazio. */
  function rowHasContent(row) {
    for (var c = 0; c < row.length; c++) {
      if (String(row[c] || '').trim() !== '') return true;
    }
    return false;
  }

  /**
   * Conta linhas com conteúdo onde a célula na coluna [colIdx] contém [term]
   * (busca parcial, case-insensitive). Retorna 0 se colIdx === -1.
   */
  function countContains(data, colIdx, term) {
    if (colIdx === -1) return 0;
    var t = term.toLowerCase();
    var n = 0;
    for (var i = 0; i < data.length; i++) {
      if (!rowHasContent(data[i])) continue;
      if (String(data[i][colIdx] || '').toLowerCase().indexOf(t) !== -1) n++;
    }
    return n;
  }

  /**
   * Conta linhas com conteúdo onde a célula na coluna [colIdx] contém
   * qualquer um dos termos em [terms]. Retorna 0 se colIdx === -1.
   */
  function countContainsAny(data, colIdx, terms) {
    if (colIdx === -1) return 0;
    var n = 0;
    for (var i = 0; i < data.length; i++) {
      if (!rowHasContent(data[i])) continue;
      var val = String(data[i][colIdx] || '').toLowerCase();
      for (var j = 0; j < terms.length; j++) {
        if (val.indexOf(terms[j].toLowerCase()) !== -1) { n++; break; }
      }
    }
    return n;
  }

  /**
   * Conta linhas com conteúdo onde a célula na coluna [colIdx] NÃO contém
   * nenhum dos termos em [terms]. Retorna 0 se colIdx === -1.
   * Linhas com campo vazio também são contadas (status não preenchido = pendente).
   */
  function countNotContainsAny(data, colIdx, terms) {
    if (colIdx === -1) return 0;
    var n = 0;
    for (var i = 0; i < data.length; i++) {
      if (!rowHasContent(data[i])) continue;
      var val = String(data[i][colIdx] || '').toLowerCase();
      var excluded = false;
      for (var j = 0; j < terms.length; j++) {
        if (val.indexOf(terms[j].toLowerCase()) !== -1) { excluded = true; break; }
      }
      if (!excluded) n++;
    }
    return n;
  }

  /** Conta linhas com conteúdo onde a célula na coluna [colIdx] é não-vazia. */
  function countNonEmpty(data, colIdx) {
    if (colIdx === -1) return 0;
    var n = 0;
    for (var i = 0; i < data.length; i++) {
      if (!rowHasContent(data[i])) continue;
      if (String(data[i][colIdx] || '').trim() !== '') n++;
    }
    return n;
  }

  /** Conta todas as linhas com conteúdo (total de registros). */
  function countRows(data) {
    var n = 0;
    for (var i = 0; i < data.length; i++) {
      if (rowHasContent(data[i])) n++;
    }
    return n;
  }

  /**
   * Converte valor para número, aceitando formato brasileiro (R$ 1.234,56).
   * Retorna 0 se não for possível converter.
   */
  function parseBr(val) {
    if (typeof val === 'number') return isNaN(val) ? 0 : val;
    var s = String(val || '').trim()
      .replace(/R\$\s*/gi, '')
      .replace(/\s/g, '');
    if (s.indexOf(',') !== -1 && s.indexOf('.') !== -1) {
      s = s.replace(/\./g, '').replace(',', '.');
    } else if (s.indexOf(',') !== -1) {
      s = s.replace(',', '.');
    }
    var n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }

  /** Soma uma coluna numérica (aceita formato brasileiro). */
  function sumCol(data, colIdx) {
    if (colIdx === -1) return 0;
    var total = 0;
    for (var i = 0; i < data.length; i++) {
      if (!rowHasContent(data[i])) continue;
      total += parseBr(data[i][colIdx]);
    }
    return total;
  }

  // ─── Ler dados e cabeçalhos de cada aba ──────────────────────────────────

  var leadsH        = getHeaders('Leads');
  var leadsD        = getData('Leads');
  var leadsEtapa    = col(leadsH, 'etapa');

  var crmCliD       = getData('CRM Clinicas');

  var tarefasH      = getHeaders('Tarefas');
  var tarefasD      = getData('Tarefas');
  var tarefasStatus = col(tarefasH, 'status');

  var finH          = getHeaders('Financeiro');
  var finD          = getData('Financeiro');
  var finPrevista   = col(finH, 'valor_estimado');
  var finRecebida   = col(finH, 'valor_recebido');

  var mktH          = getHeaders('Marketing Conteudo');
  var mktD          = getData('Marketing Conteudo');
  var mktStatus     = col(mktH, 'status');
  var mktEtapa      = col(mktH, 'etapa');

  var agendaH       = getHeaders('Agenda Operacional');
  var agendaD       = getData('Agenda Operacional');
  var agendaStatus  = col(agendaH, 'status');

  var pacH          = getHeaders('CRM Pacientes');
  var pacD          = getData('CRM Pacientes');
  var pacStatusRel  = col(pacH, 'status_relacionamento');

  var espH          = getHeaders('CRM Espirometria');
  var espD          = getData('CRM Espirometria');
  var espStatusEx   = col(espH, 'status_exame');
  var espProximo    = col(espH, 'proximo_contato');

  var conH          = getHeaders('CRM Consultas');
  var conD          = getData('CRM Consultas');
  var conStatus     = col(conH, 'status');

  var fupH          = getHeaders('Follow-up WhatsApp');
  var fupD          = getData('Follow-up WhatsApp');
  var fupStatus     = col(fupH, 'status');
  var fupCanal      = col(fupH, 'canal');

  // ─── Calcular indicadores agregados ──────────────────────────────────────

  var totalLeads          = countRows(leadsD);
  var leadsNovos          = countContains(leadsD, leadsEtapa, 'novo');
  var leadsAgendados      = countContains(leadsD, leadsEtapa, 'agendado');
  var leadsConcluidos     = countContainsAny(leadsD, leadsEtapa,
                              ['concluído', 'concluido', 'finalizado']);
  var clinicasCadastradas = countRows(crmCliD);
  var tarefasPendentes    = countNotContainsAny(tarefasD, tarefasStatus,
                              ['concluído', 'concluido', 'feito']);
  var receitaPrevista     = Math.round(sumCol(finD, finPrevista));
  var receitaRecebida     = Math.round(sumCol(finD, finRecebida));

  // Marketing: conta onde status OU etapa contém "planejado"
  var conteudosPlanejados = 0;
  for (var r = 0; r < mktD.length; r++) {
    if (!rowHasContent(mktD[r])) continue;
    var mktS = mktStatus !== -1 ? String(mktD[r][mktStatus] || '').toLowerCase() : '';
    var mktE = mktEtapa  !== -1 ? String(mktD[r][mktEtapa]  || '').toLowerCase() : '';
    if (mktS.indexOf('planejado') !== -1 || mktE.indexOf('planejado') !== -1) {
      conteudosPlanejados++;
    }
  }

  var eventosAgendados           = countContains(agendaD, agendaStatus, 'agendado');
  var pacientesEmAcompanhamento  = countContains(pacD, pacStatusRel, 'acompanhamento');
  var examesEspirometriaRealizados = countContains(espD, espStatusEx, 'realizado');
  var teleconsultasRealizadas    = countContainsAny(conD, conStatus,
                                     ['realizada', 'realizado']);

  var followupsPendentes         = countContains(fupD, fupStatus, 'pendente');

  // Lembretes WhatsApp pendentes: pendente E canal contém "whatsapp"
  var lembretesWhatsAppPendentes = 0;
  for (var r = 0; r < fupD.length; r++) {
    if (!rowHasContent(fupD[r])) continue;
    var fupS = fupStatus !== -1 ? String(fupD[r][fupStatus] || '').toLowerCase() : '';
    var fupC = fupCanal  !== -1 ? String(fupD[r][fupCanal]  || '').toLowerCase() : '';
    if (fupS.indexOf('pendente') !== -1 && fupC.indexOf('whatsapp') !== -1) {
      lembretesWhatsAppPendentes++;
    }
  }

  var recorrenciasAtivas  = countNonEmpty(espD, espProximo);
  var consultasPrevistas  = countContainsAny(conD, conStatus,
                              ['prevista', 'agendada', 'pendente']);

  // ─── Montar tabela (cabeçalho + indicadores) ─────────────────────────────

  var tabela = [
    ['key',                          'label',                        'value'                      ],
    ['totalLeads',                   'Total de leads',               totalLeads                   ],
    ['leadsNovos',                   'Leads novos',                  leadsNovos                   ],
    ['leadsAgendados',               'Leads agendados',              leadsAgendados               ],
    ['leadsConcluidos',              'Leads concluídos',             leadsConcluidos              ],
    ['clinicasCadastradas',          'Clínicas cadastradas',         clinicasCadastradas          ],
    ['tarefasPendentes',             'Tarefas pendentes',            tarefasPendentes             ],
    ['receitaPrevista',              'Receita prevista',             receitaPrevista              ],
    ['receitaRecebida',              'Receita recebida',             receitaRecebida              ],
    ['conteudosPlanejados',          'Conteúdos planejados',         conteudosPlanejados          ],
    ['eventosAgendados',             'Eventos agendados',            eventosAgendados             ],
    ['pacientesEmAcompanhamento',    'Pacientes em acompanhamento',  pacientesEmAcompanhamento    ],
    ['examesEspirometriaRealizados', 'Espirometrias realizadas',     examesEspirometriaRealizados ],
    ['teleconsultasRealizadas',      'Teleconsultas realizadas',     teleconsultasRealizadas      ],
    ['followupsPendentes',           'Follow-ups pendentes',         followupsPendentes           ],
    ['lembretesWhatsAppPendentes',   'Lembretes WhatsApp pendentes', lembretesWhatsAppPendentes   ],
    ['recorrenciasAtivas',           'Recorrências ativas',          recorrenciasAtivas           ],
    ['consultasPrevistas',           'Consultas previstas',          consultasPrevistas           ],
  ];

  // ─── Gravar no Resumo Dashboard ──────────────────────────────────────────

  var resumo = ss.getSheetByName('Resumo Dashboard');
  if (!resumo) {
    Logger.log('ERRO: aba "Resumo Dashboard" nao encontrada. Crie a aba antes de executar.');
    return;
  }

  resumo.clearContents();
  resumo.getRange(1, 1, tabela.length, 3).setValues(tabela);

  // ─── Log seguro (sem dados pessoais) ─────────────────────────────────────

  Logger.log('=== SoproLife — Resumo Dashboard atualizado ===');
  Logger.log('Linhas gravadas: ' + tabela.length + ' (1 cabecalho + ' + (tabela.length - 1) + ' indicadores)');
  Logger.log('Somente totais agregados foram gravados. Nenhum dado individual de paciente.');
  Logger.log('');
  Logger.log('Indicadores:');
  for (var i = 1; i < tabela.length; i++) {
    Logger.log('  ' + tabela[i][0] + ': ' + tabela[i][2]);
  }
  Logger.log('');
  Logger.log('Proximo passo: execute no terminal local:');
  Logger.log('  painel-soprolife/scripts/update-local-data.sh');
}
