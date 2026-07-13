/** FIXTURE SINTÉTICA — limpeza destrutiva (nunca publicar algo assim). */
function limpezaGeralSintetica() {
  var planilha = SpreadsheetApp.getActiveSpreadsheet();
  var aba = planilha.getSheetByName("Rascunho Sintético");
  aba.getRange("A1:Z100").clearContents();
  aba.deleteRows(2, 10);
  planilha.deleteSheet(aba);
}
