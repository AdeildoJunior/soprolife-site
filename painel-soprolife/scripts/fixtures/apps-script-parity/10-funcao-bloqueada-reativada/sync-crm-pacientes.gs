/** FIXTURE SINTÉTICA — versão ANTIGA e ATIVA do sync (nunca reativar). */
function sincronizarCRMPacientesSoproLife() {
  _reescreverAbaPacientes([["Paciente 001", "exemplo sintético"]]);
}

function _reescreverAbaPacientes(linhas) {
  var aba = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Pacientes");
  aba.getRange(2, 1, linhas.length, linhas[0].length).setValues(linhas);
}

function onOpen_syncCRM() {
  SpreadsheetApp.getUi().createMenu("Sync Antigo Sintético").addToUi();
}
