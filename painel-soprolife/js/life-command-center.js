/* Centro de Comando Life — casca multimarcas (Fase 1.3).
 *
 * O QUE ESTE ARQUIVO É
 * --------------------
 * Navegação e aparência. Ele escolhe qual CASCA aparece — SoproLife, EEGLife
 * ou Life Geral — e nada mais.
 *
 * O QUE ELE NÃO É
 * ---------------
 * Não é autorização. O contexto mora em localStorage, que é do navegador e
 * portanto do usuário: qualquer pessoa pode editá-lo. Por isso ele JAMAIS
 * libera dado. Quem autoriza continua sendo o servidor (`require_role` →
 * 403) com a sessão por cookie, e quem poda o DOM por papel continua sendo o
 * `boot-gate.js`. Trocar de contexto aqui não concede nem revoga nada.
 *
 * Não envia nada ao backend. Nenhuma requisição sai deste arquivo, e o valor
 * do contexto nunca entra em URL, header ou corpo de requisição — não existe
 * `brand=` em lugar nenhum. A Fase 1.3 é casca; a segregação real de dados é
 * assunto da Fase 2.
 *
 * POR QUE SOPROLIFE É O PADRÃO
 * ----------------------------
 * É a única operação funcional hoje. Abrir no Life Geral ou no EEGLife faria
 * o painel parecer vazio para quem entra para trabalhar.
 *
 * POR QUE O EEGLIFE NÃO CARREGA NADA
 * ----------------------------------
 * `app.js` só chama `init()` — que lê todos os `data/*.json` do SoproLife —
 * quando o contexto SoproLife é ENTRADO, não quando a página carrega. Em
 * EEGLife ou Life Geral nenhuma leitura de dado SoproLife acontece; se o
 * usuário voltar para SoproLife, a carga ocorre naquele momento, uma única
 * vez. É o que impede dado SoproLife de aparecer com cara de EEG.
 */
(function () {
  "use strict";

  var CHAVE = "soprolife.life.contexto";
  var PADRAO = "soprolife";

  /* Os três contextos. `tipo` é o que impede o Life Geral de ser lido como uma
   * terceira marca: ele é contexto consolidado, as marcas reais são duas. */
  var CONTEXTOS = {
    life_geral: {
      id: "life_geral",
      tipo: "contexto",
      rotulo: "Life Geral",
      kind: "consolidado",
      descricao: "visão consolidada",
      painel: "lifePanelLifeGeral",
      aba: "lifeTabLifeGeral",
    },
    soprolife: {
      id: "soprolife",
      tipo: "marca",
      rotulo: "SoproLife",
      kind: "marca",
      descricao: "operação ativa",
      painel: "lifePanelSoproLife",
      aba: "lifeTabSoproLife",
    },
    eeglife: {
      id: "eeglife",
      tipo: "marca",
      rotulo: "EEGLife",
      kind: "marca",
      descricao: "estrutura em implantação",
      painel: "lifePanelEegLife",
      aba: "lifeTabEegLife",
    },
  };

  var ORDEM = ["life_geral", "soprolife", "eeglife"];

  /* Módulos previstos da EEGLife.
   *
   * Isto é CONFIGURAÇÃO, não tela: a sidebar e o painel de implantação são
   * renderizados a partir daqui, de modo que não existem duas nem três
   * sidebars escritas à mão no HTML. É também o começo do formato que a Fase 2
   * vai usar — uma casca compartilhada mais um mapa de módulos por contexto.
   *
   * `pronto` é sempre false nesta fase: nenhum módulo EEG tem backend. Quando
   * um tiver, ele deixa de cair no painel "em implantação" — e a mudança é
   * nesta linha, não na casca.
   *
   * Os nomes seguem o painel SoproLife onde o conceito é o mesmo (CRM,
   * Financeiro, Fiscal) e mudam onde o exame muda: "Laudos de EEG",
   * "Exames e traçados".
   */
  var MODULOS_EEG = [
    { grupo: "Operação", itens: [
      { id: "visao",      rotulo: "Painel Geral",          pronto: false,
        resumo: "Visão consolidada da operação de EEG, quando houver operação para consolidar." },
      { id: "cadastros",  rotulo: "Central de Cadastros",  pronto: false,
        resumo: "Cadastro de paciente, exame e origem num único lugar, como na Central da SoproLife." },
      { id: "crm",        rotulo: "CRM",                   pronto: false,
        resumo: "Clínicas, pacientes e acompanhamento — a mesma espinha do CRM atual, com vocabulário de EEG." },
      { id: "leads",      rotulo: "Leads e Agendamentos",  pronto: false,
        resumo: "Entrada comercial e agenda de exames de EEG." },
      { id: "tarefas",    rotulo: "Tarefas",               pronto: false,
        resumo: "Rotina operacional do dia, por responsável." },
      { id: "exames",     rotulo: "Exames e traçados",     pronto: false,
        resumo: "Registro do exame de EEG e do traçado correspondente. Conceito próprio da EEGLife, sem equivalente na espirometria." },
      { id: "laudos",     rotulo: "Laudos de EEG",         pronto: false,
        resumo: "Fila médica, redação, assinatura e entrega do laudo de EEG, no padrão já provado na espirometria." },
    ] },
    { grupo: "Crescimento", itens: [
      { id: "marketing",  rotulo: "Marketing & SEO",       pronto: false,
        resumo: "Busca, presença local e origem dos contatos da marca EEGLife." },
    ] },
    { grupo: "Gestão", itens: [
      { id: "financeiro", rotulo: "Financeiro",            pronto: false,
        resumo: "Lançamentos, recebimentos e repasses da operação de EEG." },
      { id: "fiscal",     rotulo: "Fiscal",                pronto: false,
        resumo: "Emissão e acompanhamento fiscal da EEGLife, separados dos da SoproLife." },
      { id: "parceiros",  rotulo: "Parceiros",             pronto: false,
        resumo: "Clínicas e consultórios parceiros da EEGLife e suas condições." },
      { id: "custos",     rotulo: "Custos & Investimentos", pronto: false,
        resumo: "Custo da operação de EEG e rateio do que é compartilhado entre as marcas." },
      { id: "documentos", rotulo: "Documentos",            pronto: false,
        resumo: "POPs, contratos e documentos institucionais da marca." },
    ] },
    { grupo: "Sistema", itens: [
      { id: "fontes",     rotulo: "Fontes de dados",       pronto: false,
        resumo: "De onde vem cada número da EEGLife, com frescor declarado." },
      { id: "lancamentos", rotulo: "Últimos lançamentos",  pronto: false,
        resumo: "Trilha do que foi registrado recentemente na operação de EEG." },
      { id: "nucleo",     rotulo: "Núcleo administrativo", pronto: false,
        resumo: "Usuários, papéis e configuração — hoje compartilhados com a SoproLife." },
    ] },
  ];

  var ouvintes = { life_geral: [], soprolife: [], eeglife: [] };
  var atual = null;
  var aplicado = false;

  function valido(id) {
    return typeof id === "string" && Object.prototype.hasOwnProperty.call(CONTEXTOS, id);
  }

  /* Lê a preferência salva. Qualquer coisa diferente dos três valores — lixo,
   * valor de outra versão, edição manual — cai no padrão, sem reclamar. */
  function lerSalvo() {
    try {
      var bruto = window.localStorage.getItem(CHAVE);
      return valido(bruto) ? bruto : PADRAO;
    } catch (e) {
      // Janela privada, storage bloqueado: o contexto vira só de sessão.
      return PADRAO;
    }
  }

  function salvar(id) {
    try {
      window.localStorage.setItem(CHAVE, id);
    } catch (e) { /* preferência de interface; perdê-la não quebra nada */ }
  }

  function contexto() {
    if (atual === null) atual = lerSalvo();
    return atual;
  }

  function escapar(txt) {
    return String(txt == null ? "" : txt)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ── Render da casca EEGLife a partir da configuração ───────────────────── */

  function renderSidebarEeg() {
    var lista = document.getElementById("eegNavList");
    if (!lista) return;
    var html = "";
    MODULOS_EEG.forEach(function (grupo) {
      html += '<div class="eeg-nav-group">' + escapar(grupo.grupo) + "</div>";
      grupo.itens.forEach(function (item) {
        html += '<button type="button" class="eeg-nav-item" data-eeg-modulo="' +
          escapar(item.id) + '" aria-current="false">' +
          '<span class="eeg-nav-icon" aria-hidden="true"></span>' +
          escapar(item.rotulo) + "</button>";
      });
    });
    lista.innerHTML = html;

    Array.prototype.forEach.call(
      lista.querySelectorAll("[data-eeg-modulo]"),
      function (botao) {
        botao.addEventListener("click", function () {
          abrirModuloEeg(botao.getAttribute("data-eeg-modulo"));
        });
      }
    );
  }

  function moduloEeg(id) {
    for (var g = 0; g < MODULOS_EEG.length; g++) {
      var itens = MODULOS_EEG[g].itens;
      for (var i = 0; i < itens.length; i++) {
        if (itens[i].id === id) return itens[i];
      }
    }
    return null;
  }

  /* Abre um módulo da EEGLife.
   *
   * Nenhum módulo tem backend nesta fase, então o destino é sempre o painel
   * "Estrutura em implantação". O ponto é que ele NÃO chama o servidor: não há
   * fetch aqui, nem um que devolveria dado SoproLife com rótulo de EEG. */
  function abrirModuloEeg(id) {
    var mod = moduloEeg(id);
    if (!mod) return;

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-eeg-modulo]"),
      function (b) {
        b.setAttribute("aria-current", b.getAttribute("data-eeg-modulo") === id ? "true" : "false");
      }
    );

    var titulo = document.getElementById("eegModuloTitulo");
    var resumo = document.getElementById("eegModuloResumo");
    if (titulo) titulo.textContent = mod.rotulo;
    if (resumo) resumo.textContent = mod.resumo;
  }

  /* ── Aplicação do contexto ──────────────────────────────────────────────── */

  function aplicar(id, origem) {
    if (!valido(id)) id = PADRAO;
    atual = id;

    // Nome próprio de propósito: `data-life-context` identifica os BOTÕES do
    // seletor, e só eles. Usar o mesmo atributo no <body> tornava
    // `[data-life-context]` ambíguo — o body entrava na conta das abas.
    document.body.setAttribute("data-life-ativo", id);
    document.body.classList.add("life-cc");

    ORDEM.forEach(function (chave) {
      var cfg = CONTEXTOS[chave];
      var painel = document.getElementById(cfg.painel);
      if (painel) painel.hidden = chave !== id;

      var aba = document.getElementById(cfg.aba);
      if (aba) {
        var ativo = chave === id;
        aba.setAttribute("aria-selected", ativo ? "true" : "false");
        // aria-current além de aria-selected: leitores de tela e testes têm
        // duas formas independentes de saber qual contexto está ativo.
        aba.setAttribute("aria-current", ativo ? "true" : "false");
        // Só a aba ativa recebe foco pelo Tab; as irmãs são alcançadas pelas
        // setas, que é o padrão de tablist.
        aba.setAttribute("tabindex", ativo ? "0" : "-1");
      }
    });

    var rotulo = document.getElementById("lifeContextoRotulo");
    if (rotulo) {
      var cfg2 = CONTEXTOS[id];
      rotulo.innerHTML = "Contexto: <b>" + escapar(cfg2.rotulo) + "</b> · " +
        escapar(cfg2.descricao);
    }

    document.title = CONTEXTOS[id].rotulo === "SoproLife"
      ? "Painel SoproLife · Centro de Comando Life"
      : CONTEXTOS[id].rotulo + " · Centro de Comando Life";

    salvar(id);

    if (origem === "usuario") {
      var cfg3 = CONTEXTOS[id];
      var painelAtivo = document.getElementById(cfg3.painel);
      if (painelAtivo) {
        // Foco no painel recém-aberto: quem navega por teclado não fica
        // preso no seletor sem saber que a tela mudou.
        painelAtivo.setAttribute("tabindex", "-1");
        try { painelAtivo.focus({ preventScroll: true }); } catch (e) { painelAtivo.focus(); }
      }
    }

    (ouvintes[id] || []).forEach(function (cb) {
      try { cb(id); } catch (e) { console.error("ouvinte de contexto falhou", e); }
    });
  }

  function definir(id) {
    if (!valido(id)) id = PADRAO;
    if (id === atual && aplicado) return;
    aplicar(id, "usuario");
  }

  /* ── Teclado: padrão de tablist ─────────────────────────────────────────── */

  function teclado(evento) {
    var teclas = {
      ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1,
    };
    var alvo;
    if (evento.key === "Home") alvo = ORDEM[0];
    else if (evento.key === "End") alvo = ORDEM[ORDEM.length - 1];
    else if (Object.prototype.hasOwnProperty.call(teclas, evento.key)) {
      var i = ORDEM.indexOf(contexto());
      if (i === -1) i = ORDEM.indexOf(PADRAO);
      alvo = ORDEM[(i + teclas[evento.key] + ORDEM.length) % ORDEM.length];
    } else {
      return;
    }
    evento.preventDefault();
    definir(alvo);
    var aba = document.getElementById(CONTEXTOS[alvo].aba);
    if (aba) aba.focus();
  }

  function ligar() {
    renderSidebarEeg();

    var abas = document.querySelectorAll("[data-life-context]");
    Array.prototype.forEach.call(abas, function (aba) {
      aba.addEventListener("click", function () {
        definir(aba.getAttribute("data-life-context"));
      });
      aba.addEventListener("keydown", teclado);
    });

    // Atalhos "Entrar em …" dos cartões do Life Geral.
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-life-entrar]"),
      function (botao) {
        botao.addEventListener("click", function () {
          definir(botao.getAttribute("data-life-entrar"));
        });
      }
    );

    aplicar(contexto(), "inicial");
    aplicado = true;

    // Primeiro módulo da EEGLife já selecionado: a casca nunca aparece vazia.
    if (MODULOS_EEG[0] && MODULOS_EEG[0].itens[0]) {
      abrirModuloEeg(MODULOS_EEG[0].itens[0].id);
    }
  }

  window.LifeCommandCenter = {
    CONTEXTOS: CONTEXTOS,
    ORDEM: ORDEM,
    MODULOS_EEG: MODULOS_EEG,
    PADRAO: PADRAO,
    CHAVE: CHAVE,
    getContext: contexto,
    setContext: definir,
    isValid: valido,
    /* Registra um retorno para CADA entrada no contexto informado. Se o
     * contexto já estiver ativo, dispara na hora. É por aqui que o app.js
     * adia a carga dos dados do SoproLife. */
    onContext: function (id, cb) {
      if (!valido(id) || typeof cb !== "function") return;
      ouvintes[id].push(cb);
      if (aplicado && contexto() === id) {
        try { cb(id); } catch (e) { console.error("ouvinte de contexto falhou", e); }
      }
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ligar);
  } else {
    ligar();
  }
})();
