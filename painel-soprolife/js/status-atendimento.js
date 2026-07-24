/* Apresentação canônica de status de atendimento (M20) — formatador ÚNICO.
 *
 * Consumido por Central de Cadastros, CRM, linha do tempo do paciente,
 * listas de espirometrias recentes, filtros, inspeção técnica e relatórios.
 * O valor armazenado NUNCA é reescrito: isto é só camada de apresentação.
 *
 * Em escopo (todos exibem "Espirometria realizada"):
 *   "Realizado", "realizado", "Exame realizado", "exame realizado"
 *
 * FORA DE ESCOPO por decisão explícita do operador:
 *   "Liberado" / "liberado" — permanecem exatamente como estão. Nenhum
 *   outro status é renomeado, migrado, reinterpretado ou remapeado.
 */
(function () {
  "use strict";

  var EXAM_PERFORMED_DISPLAY = "Espirometria realizada";

  // Lista fechada: qualquer valor fora dela sai intacto.
  var EXAM_PERFORMED_STORED = [
    "Realizado", "realizado", "Exame realizado", "exame realizado",
  ];

  var PERFORMED_KEYS = EXAM_PERFORMED_STORED.map(function (v) {
    return v.toLowerCase();
  });

  function isPerformed(status) {
    if (typeof status !== "string") return false;
    return PERFORMED_KEYS.indexOf(status.trim().toLowerCase()) !== -1;
  }

  /* Rótulo de exibição do status da espirometria. */
  function espirometria(status) {
    if (status == null) return status;
    return isPerformed(status) ? EXAM_PERFORMED_DISPLAY : status;
  }

  /* Rótulo de uma opção de filtro: mesmo mapa, mesmo resultado. */
  function rotuloFiltro(status) {
    return espirometria(status);
  }

  /* Lista de status para um seletor, já sem sinônimos repetidos na exibição.
   * Recebe os valores ARMAZENADOS e devolve pares [valor, rótulo]. */
  function opcoesEspirometria(lista) {
    var vistos = {};
    var out = [];
    (lista || []).forEach(function (valor) {
      var rotulo = espirometria(valor);
      if (vistos[rotulo]) return;
      vistos[rotulo] = true;
      out.push([valor, rotulo]);
    });
    return out;
  }

  window.SoproStatus = {
    EXAM_PERFORMED_DISPLAY: EXAM_PERFORMED_DISPLAY,
    EXAM_PERFORMED_STORED: EXAM_PERFORMED_STORED.slice(),
    espirometria: espirometria,
    exameRealizado: isPerformed,
    rotuloFiltro: rotuloFiltro,
    opcoesEspirometria: opcoesEspirometria,
  };
})();
