/** FIXTURE SINTÉTICA — implementação ANTIGA da Pastore (nunca reativar). */
var _nextId = 1;

function _registrarAtendimentoPastore(dados) {
  var aba = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Atendimentos");
  aba.appendRow(["SINTETICO-" + _nextId, dados && dados.exemplo]);
  _nextId = _nextId + 1;
}
