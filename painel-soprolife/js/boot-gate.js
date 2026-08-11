/* Gate de boot por papel (M25.23).
 *
 * Roda ANTES de app.js e é a primeira coisa que a página autenticada executa.
 *
 * Por que existe
 * -------------
 * Duas falhas somadas produziam o vazamento corrigido nesta etapa:
 *
 *  1. o servidor entregava a página inteira a quem não tinha sessão (resolvido
 *     no servidor: sem cookie válido, sai a casca de login e nada mais);
 *  2. o isolamento por papel era feito com CLASSE CSS — o DOM restrito
 *     continuava montado, e bastava o inspetor para lê-lo.
 *
 * Aqui o item 2 é resolvido do jeito certo: os nós fora do papel são
 * REMOVIDOS da árvore (`Element.remove()`), não escondidos. O que não está no
 * DOM não vaza por CSS, por inspetor, por Ctrl+F nem por leitor de tela.
 *
 * Autoridade
 * ----------
 * O papel vem SEMPRE de GET /auth/me, no servidor, a cada carregamento.
 * Nunca de localStorage, nunca de cookie legível, nunca de um estado herdado
 * da navegação anterior. A UI é conveniência; o servidor continua recusando
 * por conta própria (require_role → 403) e é ele quem realmente protege.
 */
(function () {
  "use strict";

  var API = "/painel-soprolife/api/m15";
  var ENTRADA = "/painel-soprolife/";

  // Papéis que enxergam a operação administrativa. `medico` NÃO está aqui —
  // e essa ausência é o ponto todo desta etapa.
  var PAPEIS_ADMINISTRATIVOS = ["admin", "gestor", "operacional", "leitura"];

  // Única seção que o papel exclusivamente clínico enxerga.
  var SECAO_CLINICA = "laudos-espirometria";

  var gate = {
    identidade: null,
    somenteClinico: false,
    pronto: null,
  };
  window.SoproBootGate = gate;

  function temPapel(papeis, lista) {
    for (var i = 0; i < lista.length; i++) {
      if (papeis.indexOf(lista[i]) !== -1) return true;
    }
    return false;
  }

  /* Remove da árvore tudo que não pertence ao papel.
   *
   * `permitidas` = null significa "sem restrição" (perfil administrativo).
   */
  function podarDOM(permitidas) {
    if (permitidas === null) return;

    function permitida(secao) {
      return permitidas.indexOf(secao) !== -1;
    }

    // 1. Itens do menu lateral e cartões do hub.
    var alvos = document.querySelectorAll(
      ".nav-item[data-section], .nav-hub-card[data-section]"
    );
    Array.prototype.forEach.call(alvos, function (el) {
      if (!permitida(el.getAttribute("data-section"))) el.remove();
    });

    // 2. As seções de conteúdo — é aqui que moravam os valores.
    var secoes = document.querySelectorAll(".section[id]");
    Array.prototype.forEach.call(secoes, function (el) {
      if (!permitida(el.id)) el.remove();
    });

    // 3. Rótulos de grupo que ficaram sem nenhum item abaixo. Sem isto o
    //    menu do papel clínico exibiria cabeçalhos de áreas inexistentes —
    //    o nome da área já é informação.
    var rotulos = document.querySelectorAll(".nav-group-label");
    Array.prototype.forEach.call(rotulos, function (rotulo) {
      var proximo = rotulo.nextElementSibling;
      var temItem = false;
      while (proximo) {
        if (proximo.classList.contains("nav-group-label")) break;
        if (proximo.classList.contains("nav-item")) { temItem = true; break; }
        proximo = proximo.nextElementSibling;
      }
      if (!temItem) rotulo.remove();
    });

    // 4. O hub inteiro some quando não sobrou cartão.
    var hub = document.querySelector("#navHub");
    if (hub && !hub.querySelector(".nav-hub-card")) {
      var painelHub = hub.closest(".panel") || hub;
      painelHub.remove();
    }

    // 5. Busca global: percorre módulos que este papel não tem. Remover é
    //    mais honesto do que devolver resultado vazio.
    var busca = document.querySelector("#globalSearch");
    if (busca) {
      var caixa = busca.closest(".search") || busca.closest("form") || busca;
      caixa.remove();
    }
  }

  /* Garante que a seção permitida esteja visível e marcada no menu. */
  function ativar(secao) {
    var secoes = document.querySelectorAll(".section[id]");
    Array.prototype.forEach.call(secoes, function (el) {
      el.classList.toggle("active", el.id === secao);
    });
    var itens = document.querySelectorAll(".nav-item[data-section]");
    Array.prototype.forEach.call(itens, function (el) {
      var alvo = el.getAttribute("data-section") === secao;
      el.classList.toggle("active", alvo);
      if (alvo) el.hidden = false;
    });
    var entradas = document.querySelectorAll("[data-report-entry]");
    Array.prototype.forEach.call(entradas, function (el) { el.hidden = false; });
  }

  function semSessao() {
    // O servidor decide o que mostrar sem cookie: recarregar traz a casca de
    // login. `replace` evita que o botão Voltar traga a página restrita do
    // histórico. O marcador quebra qualquer laço de recarga.
    try {
      if (window.sessionStorage.getItem("soproGateRecarga") === "1") {
        window.sessionStorage.removeItem("soproGateRecarga");
        return;
      }
      window.sessionStorage.setItem("soproGateRecarga", "1");
    } catch (e) { /* storage indisponível: segue para a recarga única */ }
    window.location.replace(ENTRADA);
  }

  gate.pronto = fetch(API + "/auth/me", {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  })
    .then(function (resposta) {
      if (resposta.status === 401) { semSessao(); return null; }
      if (!resposta.ok) throw new Error("auth/me HTTP " + resposta.status);
      return resposta.json();
    })
    .then(function (identidade) {
      if (!identidade) return null;
      try {
        window.sessionStorage.removeItem("soproGateRecarga");
      } catch (e) { /* irrelevante para o gate */ }

      var papeis = Array.isArray(identidade.papeis_efetivos)
        ? identidade.papeis_efetivos
        : [];
      var administrativo = temPapel(papeis, PAPEIS_ADMINISTRATIVOS);
      var clinico = papeis.indexOf("medico") !== -1;

      gate.identidade = identidade;
      gate.somenteClinico = clinico && !administrativo;

      if (administrativo) {
        podarDOM(null);            // perfil completo — nada é removido
      } else if (clinico) {
        podarDOM([SECAO_CLINICA]); // só a bancada médica sobrevive
        ativar(SECAO_CLINICA);
      } else {
        // Sessão válida sem papel útil: não é erro do usuário, mas também
        // não é motivo para montar nada. Fail-closed.
        podarDOM([]);
      }
      return identidade;
    })
    .catch(function () {
      // Falha de rede ou resposta inesperada: fail-closed. Poda tudo em vez
      // de assumir que a sessão é administrativa.
      gate.somenteClinico = true;
      podarDOM([]);
      return null;
    });
})();
