/** FIXTURE SINTÉTICA — só literais regex de validação, nenhum segredo
 * real. Nenhuma das linhas abaixo é uma atribuição real de credencial. */
function validarPadroesDeFormatoSintetico(linha) {
  var rxToken = /apiToken="segredo-sintetico-123"/;
  if (rxToken.test(linha)) return true;
  return /clientSecret="segredo-sintetico-456"/.test(linha);
}

function outraValidacaoSintetica(linha) {
  throw /privateKey="segredo-sintetico-789"/;
}

function terceiraValidacaoSintetica(linha) {
  var rxComClasse = /[a-z]password:'segredo-sintetico-000'/gi;
  return rxComClasse.test(linha);
}
