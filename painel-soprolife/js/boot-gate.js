/* Bootstrap único de apresentação. O backend continua sendo a autoridade RBAC.
 * O HTML protegido nasce em template inerte. Só a sessão confirmada permite
 * montá-lo; a publicação é atômica após a preparação dos módulos/contextos.
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
    estado: "loading",
    identidade: null,
    sessao: null,
    config: null,
    administrativo: false,
    somenteClinico: true,
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
  function podarDOM(permitidas, root) {
    root = root || document;
    if (permitidas === null) return;

    function permitida(secao) {
      return permitidas.indexOf(secao) !== -1;
    }

    // 1. Itens do menu lateral e cartões do hub.
    var alvos = root.querySelectorAll(
      ".nav-item[data-section], .nav-hub-card[data-section]"
    );
    Array.prototype.forEach.call(alvos, function (el) {
      if (!permitida(el.getAttribute("data-section"))) el.remove();
    });

    // 2. As seções de conteúdo — é aqui que moravam os valores.
    var secoes = root.querySelectorAll(".section[id]");
    Array.prototype.forEach.call(secoes, function (el) {
      if (!permitida(el.id)) el.remove();
    });

    // 3. Rótulos de grupo que ficaram sem nenhum item abaixo. Sem isto o
    //    menu do papel clínico exibiria cabeçalhos de áreas inexistentes —
    //    o nome da área já é informação.
    var rotulos = root.querySelectorAll(".nav-group-label");
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
    var hub = root.querySelector("#navHub");
    if (hub && !hub.querySelector(".nav-hub-card")) {
      var painelHub = hub.closest(".panel") || hub;
      painelHub.remove();
    }

    // 5. Busca global: percorre módulos que este papel não tem. Remover é
    //    mais honesto do que devolver resultado vazio.
    var busca = root.querySelector("#globalSearch");
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

  var painel = null;
  function estado(valor) {
    gate.estado = valor;
    document.body.dataset.authState = valor;
  }
  gate.can = function (papel) {
    return gate.estado === "ready" && !!gate.identidade &&
      gate.identidade.papeis_efetivos.indexOf(papel) !== -1;
  };
  gate.fechar = function () {
    estado("denied");
    gate.identidade = null;
    gate.sessao = null;
    if (painel) painel.remove();
    delete document.body.dataset.authPublished;
    var shell = document.getElementById("authShell");
    if (shell) { shell.hidden = false; shell.setAttribute("aria-busy", "false"); }
    document.getElementById("authMessage").textContent = "Não foi possível preparar sua sessão. Entre novamente ou tente recarregar.";
    document.getElementById("authRetry").hidden = false;
  };

  async function json(url) {
    if (!window.SoproM15Security || !window.SoproM15Security.canAuthenticate(location)) throw new Error("Origem insegura");
    var res = await fetch(url, { credentials: "same-origin", cache: "no-store",
      headers: { Accept: "application/json" }, signal: AbortSignal.timeout(30000) });
    if (res.status === 401) semSessao();
    if (!res.ok) throw new Error("Bootstrap HTTP " + res.status);
    return res.json();
  }

  gate.pronto = Promise.all([
    json(API + "/auth/me"), json(API + "/auth/sessao"), json("./data/m15-config.json")
  ]).then(async function (resultados) {
    var identidade = resultados[0], sessao = resultados[1], config = resultados[2];
    var papeis = identidade && identidade.papeis_efetivos;
    // Nunca combinar permissões de uma identidade com a sessão de outra.
    if (!identidade.id || !Array.isArray(papeis) || !papeis.length ||
        !sessao.usuario || identidade.id !== sessao.usuario.id || !sessao.csrf ||
        JSON.stringify(papeis) !== JSON.stringify(sessao.usuario.papeis_efetivos) ||
        !config || config.enabled !== true || config.api_base !== API) {
      throw new Error("Bootstrap incompleto");
    }
    try { window.sessionStorage.removeItem("soproGateRecarga"); } catch (e) { /* opcional */ }
    gate.identidade = identidade;
    gate.sessao = sessao;
    gate.config = config;
    gate.administrativo = temPapel(papeis, PAPEIS_ADMINISTRATIVOS);
    var clinico = papeis.indexOf("medico") !== -1;
    gate.somenteClinico = !gate.administrativo;
    var template = document.getElementById("authorizedPanelTemplate");
    var fragmento = template.content.cloneNode(true);
    // Reutiliza a projeção de marcas existente antes de anexar o fragmento.
    var lifeScript = document.querySelector('script[src*="js/life-command-center.js"]');
    await carregarModulo(lifeScript, true);
    lifeScript.dataset.loaded = "true";
    if (!window.LifeCommandCenter.prepare(fragmento, identidade)) throw new Error("Sem contexto autorizado");
    podarDOM(gate.administrativo ? null : (clinico ? [SECAO_CLINICA] : []), fragmento);
    if (!gate.administrativo) {
      var refresh = fragmento.querySelector("#refreshBtn");
      if (refresh) refresh.remove();
    }
    // Autenticação, usuário e RBAC conhecidos antes de anexar qualquer nó.
    // O contexto é preparado pela fonte existente LifeCommandCenter antes da publicação.
    painel = document.createElement("div");
    painel.id = "authorizedPanel";
    painel.hidden = true;
    painel.inert = true;
    painel.appendChild(fragmento);
    document.body.appendChild(painel);
    template.remove();
    estado("ready");
    if (clinico && !gate.administrativo) ativar(SECAO_CLINICA);
    return identidade;
  }).catch(function () { gate.fechar(); return null; });

  function carregarModulo(declaracao, preparando) {
    return new Promise(function (resolve, reject) {
      if (gate.estado !== "ready" && !preparando) { reject(new Error("Sessão encerrada")); return; }
      var script = document.createElement("script");
      script.src = declaracao.src;
      script.onload = resolve;
      script.onerror = function () { reject(new Error("Módulo indisponível")); };
      document.head.appendChild(script);
    });
  }
  var moduleQueue = Promise.resolve();
  var modulePromises = new Map();
  var legacySections = ["crm","tarefas","marketing","financeiro","parcerias-pastore","custos-investimentos","documentos","automacoes-crm","automacoes","lancamentos"];
  var groupsBySection = {
    "central-cadastros": ["central"], "laudos-espirometria": ["clinical"],
    "crm": ["legacy","crm"], "leads": ["leads"],
    "financeiro": ["legacy","financeiro"], "fiscal": ["fiscal"],
    "parcerias-pastore": ["legacy","pastore"],
  };
  gate.carregar = function (group) {
    if (gate.estado !== "ready" || (!gate.administrativo &&
        ["core","clinical","report-stats","eeg"].indexOf(group) === -1) ||
        (group === "eeg" && !window.LifeCommandCenter.isAllowed("eeglife"))) {
      return Promise.reject(new Error("Módulo não autorizado"));
    }
    var task = moduleQueue.then(async function () {
      var declarations = document.querySelectorAll('script[type="application/x-soprolife-module"]');
      for (var declaration of declarations) {
        if (!(declaration.dataset.ccGroups || "").split(" ").includes(group) || declaration.dataset.loaded) continue;
        if (!modulePromises.has(declaration.src)) modulePromises.set(declaration.src, carregarModulo(declaration));
        try { await modulePromises.get(declaration.src); declaration.dataset.loaded = "true"; }
        catch (error) { modulePromises.delete(declaration.src); throw error; }
        if (declaration.src.includes("/m15-nucleo.js") && window.SoproM15) await window.SoproM15.ready;
      }
    });
    moduleQueue = task.catch(function () {});
    return task;
  };
  var dispatching = false;
  gate.navegar = async function (section, options) {
    if (gate.estado !== "ready") throw new Error("Sessão indisponível");
    var button = document.querySelector('.nav-item[data-section="' + CSS.escape(section) + '"]');
    var panel = document.getElementById(section);
    if (!button || !panel) throw new Error("Seção não autorizada");
    var alreadyActive = panel.classList.contains("active");
    var firstClinical = section === "laudos-espirometria" && !window.SoproReports;
    var groups = groupsBySection[section] || (legacySections.includes(section) ? ["legacy"] : []);
    var firstLegacy = groups.includes("legacy") && !window.SoproLegacy?.ready;
    for (var group of groups) await gate.carregar(group);
    if (gate.can("operacional") && (groups.includes("legacy") || groups.includes("leads"))) await gate.carregar("central");
    if (groups.includes("legacy") && window.SoproLegacy) await window.SoproLegacy.start();
    if (section === "laudos-espirometria" && window.SoproReports) await window.SoproReports.ready;
    document.querySelectorAll(".section").forEach(function (el) { el.classList.toggle("active", el === panel); });
    document.querySelectorAll(".nav-item").forEach(function (el) { el.classList.toggle("active", el === button); });
    dispatching = true;
    try { if ((!options || options.dispatch !== false) && !alreadyActive && !firstClinical && !firstLegacy) button.click(); } finally { dispatching = false; }
    if (section === "overview" && window.SoproCommandCenter) await window.SoproCommandCenter.refresh();
    panel.scrollIntoView({ block: "start" });
    return panel;
  };
  document.addEventListener("click", function (event) {
    if (dispatching) return;
    var button = event.target.closest && event.target.closest('.nav-item[data-section],.nav-hub-card[data-section]');
    if (!button) return;
    event.preventDefault(); event.stopImmediatePropagation();
    gate.navegar(button.dataset.section).catch(function () {
      var message = document.getElementById("ccAnnouncement");
      if (message) message.textContent = "Não foi possível abrir o módulo. Tente novamente.";
    });
  }, true);
  document.addEventListener("click", function (event) {
    var shortcut = event.target.closest && event.target.closest("[data-m15-central]");
    if (!shortcut || window.SoproCentral) return;
    event.preventDefault(); event.stopImmediatePropagation();
    gate.carregar("central").then(function () { shortcut.click(); }).catch(function () {});
  }, true);
  gate.interfacePronta = gate.pronto.then(async function (identidade) {
    if (!identidade) return false;
    await gate.carregar("core");
    if (window.SoproM15 && window.SoproM15.ready) await window.SoproM15.ready;
    if (!window.SoproM15 || !window.SoproM15.hasSession() ||
        !window.LifeCommandCenter || !window.LifeCommandCenter.allowedContexts().length ||
        gate.estado !== "ready") throw new Error("Interface incompleta");
    if (gate.config.reports_enabled === true && gate.config.reports_mode === "pilot") {
      document.querySelectorAll("[data-report-entry]").forEach(function (el) { el.hidden = false; });
    }
    if (gate.somenteClinico && window.LifeCommandCenter.getContext() === "soprolife") {
      await gate.carregar("clinical");
      if (window.SoproReports) await window.SoproReports.ready;
    }
    if (window.LifeCommandCenter.getContext() === "eeglife") {
      await gate.carregar("eeg");
      window.LifeCommandCenter.openEegModule(window.LifeCommandCenter.activeEegModule());
    }
    if (window.SoproCommandCenter) {
      await window.SoproCommandCenter.ready;
      await window.SoproCommandCenter.route();
    }
    document.getElementById("ccLegacy")?.addEventListener("toggle", async function (event) {
      if (!event.target.open) return;
      try { await gate.carregar("legacy"); await window.SoproLegacy.start(); }
      catch (error) { event.target.open = false; }
    });
    await Promise.all(Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
      .filter(function (link) { return new URL(link.href).origin === location.origin && !link.sheet; })
      .map(function (link) { return new Promise(function (resolve, reject) {
        link.addEventListener("load", resolve, { once: true });
        link.addEventListener("error", reject, { once: true });
      }); }));
    if (gate.estado !== "ready") throw new Error("Sessão encerrada");
    document.getElementById("authShell").hidden = true;
    painel.inert = false;
    painel.hidden = false;
    document.body.dataset.authPublished = "true";
    window.dispatchEvent(new Event("soprolife:authorized-ready"));
    window.dispatchEvent(new Event("resize"));
    return true;
  }).catch(function () { gate.fechar(); return false; });
  window.addEventListener("pagehide", function () { if (painel) { painel.hidden = true; painel.inert = true; } });
  window.addEventListener("pageshow", function (e) { if (e.persisted) location.reload(); });
})();
