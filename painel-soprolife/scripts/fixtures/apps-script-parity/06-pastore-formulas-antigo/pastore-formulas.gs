/** FIXTURE SINTÉTICA — fórmulas antigas da Pastore (arquivo legado). */
var _PARCERIA_PASTORE_FORMULA_COLS = ["S", "T", "U"];

function aplicarFormulasPastoreAntigas() {
  var aba = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Atendimentos");
  aba.getRange("S2:U2").setValues([["=1", "=2", "=3"]]);
}
