/* Centro de Comando Life — casca de duas marcas (Fase 1.3A).
 *
 * O QUE ESTE ARQUIVO É
 * --------------------
 * A casca que decide qual operação aparece — SoproLife ou EEGLife — e desenha
 * a casca da EEGLife no MESMO padrão de produto do painel SoproLife.
 *
 * DUAS MARCAS, NÃO TRÊS CONTEXTOS
 * -------------------------------
 * A versão anterior trazia um terceiro contexto, "Life Geral", consolidado.
 * Ele foi REMOVIDO por decisão de produto: o topo mostra marcas, e marcas são
 * duas. Consolidação entre as marcas continua fazendo sentido, mas como
 * relatório ou painel administrativo DENTRO de um módulo — nunca como uma
 * terceira marca no seletor.
 *
 * AUTORIZAÇÃO DO CONTEXTO
 * -----------------------
 * Quem pode entrar na EEGLife é decidido pelos PAPÉIS DA SESSÃO, que vêm do
 * servidor em GET /auth/me (cookie de sessão), pela mesma via que o
 * `boot-gate.js` usa para podar o DOM por papel. Regra provisória desta fase:
 *
 *     papéis efetivos contêm "admin"  → SoproLife + EEGLife
 *     qualquer outro papel            → somente SoproLife
 *
 * `admin` implica gestor/operacional/leitura, mas NUNCA `medico`
 * (`ROLE_IMPLIES` em security.py). Por isso uma conta exclusivamente clínica
 * não entra na EEGLife — sem que o nome de ninguém apareça no código.
 *
 * Para quem não pode, a aba e o painel inteiro da EEGLife são REMOVIDOS da
 * árvore, não escondidos — o mesmo princípio do `boot-gate.js`: o que não está
 * no DOM não vaza por CSS, por inspetor, por Ctrl+F nem por leitor de tela.
 * E `setContext("eeglife")` é recusado, de modo que nem localStorage nem
 * JavaScript de console conseguem forçar a entrada.
 *
 * O localStorage guarda SOMENTE preferência entre contextos que a sessão já
 * pode acessar. Ele nunca concede acesso, e nunca sai para o servidor: não
 * existe `brand=` em requisição alguma.
 *
 * HONESTIDADE SOBRE O ALCANCE
 * ---------------------------
 * A EEGLife ainda não tem endpoint próprio, então não há o que proteger no
 * servidor além do que já está protegido. Esta guarda é de interface sobre um
 * sinal do servidor: ela impede a navegação, não substitui autorização de
 * dados. Quando a EEGLife tiver endpoints, a autorização real terá de ser
 * deles — está registrado como pendência da Fase 2.
 *
 * POR QUE O EEGLIFE NÃO CARREGA DADO SOPROLIFE
 * --------------------------------------------
 * `app.js` só chama `init()` — que lê todos os `data/*.json` da espirometria —
 * quando o contexto SoproLife é ENTRADO. Em EEGLife nenhuma leitura acontece,
 * e por isso não há como dado da espirometria aparecer com rótulo de EEG.
 */
(function () {
  "use strict";

  var CHAVE = "soprolife.life.contexto";
  var PADRAO = "soprolife";
  var PAPEL_EEG = "admin";

  /* As duas marcas. Não existe um terceiro contexto. */
  var CONTEXTOS = {
    soprolife: {
      id: "soprolife",
      tipo: "marca",
      rotulo: "SoproLife",
      kind: "marca",
      descricao: "espirometria",
      painel: "lifePanelSoproLife",
      aba: "lifeTabSoproLife",
      restrito: false,
    },
    eeglife: {
      id: "eeglife",
      tipo: "marca",
      rotulo: "EEGLife",
      kind: "marca",
      descricao: "eletroencefalografia",
      painel: "lifePanelEegLife",
      aba: "lifeTabEegLife",
      restrito: true,
    },
  };

  var ORDEM = ["soprolife", "eeglife"];

  /* Módulos da EEGLife.
   *
   * Isto é CONFIGURAÇÃO: a sidebar e todas as telas saem daqui. Não existe uma
   * segunda sidebar escrita à mão, e é o formato que a Fase 2 deve herdar —
   * casca compartilhada mais mapa de módulos por marca.
   *
   * `forma` escolhe o desenho da tela vazia, sempre no padrão do painel
   * SoproLife: `painel` (dashboard com indicadores), `lista` (busca + filtro +
   * tabela) e `registro` (ficha clínica/documental). Nenhum módulo tem backend
   * nesta fase, então todos nascem vazios — mas vazios como uma tela
   * operacional à espera de dado, não como página de obra.
   */
  var MODULOS_EEG = [
    { grupo: "Operação", itens: [
      { id: "visao", rotulo: "Painel Geral", forma: "painel",
        eyebrow: "Operação EEGLife",
        resumo: "Visão consolidada da operação de eletroencefalografia." },
      { id: "cadastros", rotulo: "Central de Cadastros", forma: "lista",
        eyebrow: "Operação · cadastro",
        resumo: "Paciente, exame e origem cadastrados em um só lugar.",
        busca: "Buscar paciente da EEGLife…",
        filtros: ["Todos os cadastros", "Com pendência", "Completos"],
        colunas: ["Código", "Paciente", "Nascimento", "Contato", "Pendências", "Cadastro"] },
      { id: "crm", rotulo: "CRM", forma: "lista",
        eyebrow: "Operação · relacionamento",
        resumo: "Clínicas, pacientes e acompanhamento da marca EEGLife.",
        busca: "Buscar clínica ou paciente…",
        filtros: ["Todas as etapas", "Em conversa", "Convertidos"],
        colunas: ["Clínica", "Etapa", "Responsável", "Próxima ação", "Atualizado"] },
      { id: "leads", rotulo: "Leads e Agendamentos", forma: "lista",
        eyebrow: "Pré-atendimento · CRM",
        resumo: "Contatos antes do atendimento e agenda de exames de EEG.",
        busca: "Buscar lead…",
        filtros: ["Ativos", "Convertidos", "Todos"],
        colunas: ["Lead", "Origem", "Etapa", "Agendamento", "Responsável"] },
      { id: "tarefas", rotulo: "Tarefas", forma: "lista",
        eyebrow: "Operação · rotina",
        resumo: "Rotina operacional do dia, por responsável.",
        busca: "Buscar tarefa…",
        filtros: ["Hoje", "Da semana", "Todas"],
        colunas: ["Tarefa", "Área", "Responsável", "Prioridade", "Status"] },
    ] },
    { grupo: "Clínico EEG", itens: [
      { id: "exames", rotulo: "Exames e traçados", forma: "registro",
        eyebrow: "Clínico · exame",
        resumo: "Registro do exame de EEG e do traçado correspondente.",
        colunas: ["Código", "Paciente", "Modalidade", "Data", "Local", "Status"],
        nota: "Modalidades de EEG (rotina, sono, prolongado, vídeo-EEG) serão " +
              "definidas com a operação. Nenhuma está ativa nesta fase." },
      { id: "laudos", rotulo: "Laudos de EEG", forma: "registro",
        eyebrow: "Clínico · laudo",
        resumo: "Fila médica, redação, assinatura e entrega do laudo de EEG.",
        colunas: ["Código", "Paciente", "Médica", "Estado", "Assinatura", "Entrega"],
        nota: "Fila própria da EEGLife. Laudos de espirometria não são " +
              "reaproveitados aqui: são exame, autoria e documento diferentes." },
    ] },
    { grupo: "Crescimento", itens: [
      { id: "marketing", rotulo: "Marketing & SEO", forma: "painel",
        eyebrow: "Crescimento · presença",
        resumo: "Busca, presença local e origem dos contatos da marca EEGLife." },
    ] },
    { grupo: "Gestão", itens: [
      { id: "financeiro", rotulo: "Financeiro", forma: "lista",
        eyebrow: "Gestão · receita",
        resumo: "Lançamentos, recebimentos e repasses da operação de EEG.",
        busca: "Buscar lançamento…",
        filtros: ["Competência atual", "Em aberto", "Todos"],
        colunas: ["Data", "Descrição", "Origem", "Valor", "Situação"] },
      { id: "fiscal", rotulo: "Fiscal", forma: "lista",
        eyebrow: "Gestão · fiscal",
        resumo: "Emissão e acompanhamento fiscal da EEGLife.",
        busca: "Buscar documento fiscal…",
        filtros: ["Pendentes", "Emitidos", "Todos"],
        colunas: ["Documento", "Tomador", "Competência", "Valor", "Situação"] },
      { id: "parceiros", rotulo: "Parceiros", forma: "lista",
        eyebrow: "Gestão · parcerias",
        resumo: "Clínicas e consultórios parceiros da EEGLife e suas condições.",
        busca: "Buscar parceiro…",
        filtros: ["Ativos", "Em negociação", "Todos"],
        colunas: ["Parceiro", "Unidade", "Condição", "Responsável", "Situação"] },
      { id: "custos", rotulo: "Custos & Investimentos", forma: "lista",
        eyebrow: "Gestão · custo",
        resumo: "Custo da operação de EEG e rateio do que é compartilhado.",
        busca: "Buscar custo…",
        filtros: ["Competência atual", "Recorrentes", "Todos"],
        colunas: ["Item", "Categoria", "Pagador", "Valor", "Rateio"] },
      { id: "documentos", rotulo: "Documentos", forma: "lista",
        eyebrow: "Gestão · documentos",
        resumo: "POPs, contratos e documentos institucionais da marca.",
        busca: "Buscar documento…",
        filtros: ["Vigentes", "A vencer", "Todos"],
        colunas: ["Documento", "Tipo", "Responsável", "Validade", "Situação"] },
    ] },
    { grupo: "Sistema", itens: [
      { id: "fontes", rotulo: "Fontes de dados", forma: "painel",
        eyebrow: "Sistema · integrações",
        resumo: "De onde vem cada número da EEGLife, com frescor declarado." },
      { id: "lancamentos", rotulo: "Últimos lançamentos", forma: "lista",
        eyebrow: "Sistema · trilha",
        resumo: "Trilha do que foi registrado recentemente na operação de EEG.",
        busca: "Buscar lançamento…",
        filtros: ["Hoje", "7 dias", "Todos"],
        colunas: ["Quando", "Categoria", "Evento", "Responsável", "Status"] },
      { id: "nucleo", rotulo: "Núcleo administrativo", forma: "painel",
        eyebrow: "Sistema · núcleo",
        resumo: "Usuários, papéis e configuração — hoje compartilhados com a SoproLife." },
    ] },
  ];

  /* Indicadores do Painel Geral da EEGLife.
   *
   * Valor "—" e não "0": zero afirmaria que houve medição e deu zero. Não
   * houve medição — não existe fonte ligada. A diferença importa porque este
   * painel vai conviver com o da SoproLife, onde zero significa zero.
   */
  var INDICADORES_EEG = [
    { label: "Exames agendados", hint: "Aguardando ativação" },
    { label: "Exames realizados", hint: "Aguardando ativação" },
    { label: "Laudos pendentes", hint: "Aguardando ativação" },
    { label: "Laudos concluídos", hint: "Aguardando ativação" },
    { label: "Pacientes", hint: "Sem fonte ativa" },
    { label: "Parceiros", hint: "Sem fonte ativa" },
    { label: "Financeiro", hint: "Sem fonte ativa" },
  ];

  var ouvintes = { soprolife: [], eeglife: [] };
  var atual = null;
  var aplicado = false;
  var permitidos = [PADRAO];
  var moduloAtivo = null;

  function valido(id) {
    return typeof id === "string" && Object.prototype.hasOwnProperty.call(CONTEXTOS, id);
  }

  /* Permitido = existe E a sessão autoriza. As duas coisas, sempre. */
  function permitido(id) {
    return valido(id) && permitidos.indexOf(id) !== -1;
  }

  function lerSalvo() {
    try {
      var bruto = window.localStorage.getItem(CHAVE);
      // "life_geral" é valor legado da versão de três contextos. Cai no
      // padrão como qualquer outro valor desconhecido — sem caso especial.
      return permitido(bruto) ? bruto : PADRAO;
    } catch (e) {
      return PADRAO;
    }
  }

  function salvar(id) {
    try { window.localStorage.setItem(CHAVE, id); } catch (e) { /* preferência */ }
  }

  function contexto() {
    if (atual === null) atual = PADRAO;
    return atual;
  }

  function escapar(txt) {
    return String(txt == null ? "" : txt)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ── Papéis da sessão ───────────────────────────────────────────────────── */

  /* Fail-closed por construção: sem gate, sem identidade ou sem o papel, a
   * lista de contextos permitidos continua sendo só a SoproLife. */
  function calcularPermitidos(identidade) {
    var lista = [PADRAO];
    var papeis = identidade && Array.isArray(identidade.papeis_efetivos)
      ? identidade.papeis_efetivos
      : [];
    if (papeis.indexOf(PAPEL_EEG) !== -1) lista.push("eeglife");
    return lista;
  }

  /* Remove da árvore a marca que a sessão não pode acessar — não esconde.
   * Mesmo princípio do boot-gate: o que não está no DOM não vaza. */
  function podarMarcasNaoPermitidas() {
    ORDEM.forEach(function (chave) {
      if (permitido(chave)) return;
      var cfg = CONTEXTOS[chave];
      var aba = document.getElementById(cfg.aba);
      if (aba) aba.remove();
      var painel = document.getElementById(cfg.painel);
      if (painel) painel.remove();
    });
    // Com uma marca só, o seletor não tem o que selecionar.
    var trilho = document.querySelector(".life-switcher-track");
    if (trilho && trilho.querySelectorAll("[data-life-context]").length < 2) {
      var seletor = document.querySelector(".life-switcher");
      if (seletor) seletor.remove();
    }
  }

  /* ── Casca da EEGLife, no padrão do painel SoproLife ────────────────────── */

  function moduloEeg(id) {
    for (var g = 0; g < MODULOS_EEG.length; g++) {
      var itens = MODULOS_EEG[g].itens;
      for (var i = 0; i < itens.length; i++) {
        if (itens[i].id === id) return itens[i];
      }
    }
    return null;
  }

  function htmlIndicadores() {
    return '<div class="eeg-stats">' + INDICADORES_EEG.map(function (ind) {
      return '<article class="stat-card">' +
        "<span>" + escapar(ind.label) + "</span>" +
        '<strong class="eeg-sem-valor">—</strong>' +
        "<small>" + escapar(ind.hint) + "</small>" +
        "</article>";
    }).join("") + "</div>";
  }

  function htmlTabelaVazia(colunas, mensagem) {
    var cabecalho = (colunas || []).map(function (c) {
      return "<th>" + escapar(c) + "</th>";
    }).join("");
    return '<div class="table-wrap"><table><thead><tr>' + cabecalho +
      '</tr></thead><tbody><tr><td class="table-empty-cell" colspan="' +
      (colunas || []).length + '"><div class="empty-state">' +
      escapar(mensagem) + "</div></td></tr></tbody></table></div>";
  }

  function htmlControles(mod) {
    // Controles reais, desabilitados: a tela precisa parecer operacional e à
    // espera de dado, não um aviso de obra. Desabilitado porque não há o que
    // buscar nem o que filtrar — habilitá-los prometeria uma ação que não
    // existe.
    var busca = mod.busca
      ? '<input type="search" class="crm-filter-input" placeholder="' +
        escapar(mod.busca) + '" aria-label="' + escapar(mod.busca) +
        '" disabled />'
      : "";
    var filtro = (mod.filtros && mod.filtros.length)
      ? '<select class="crm-filter-select" aria-label="Filtro" disabled>' +
        mod.filtros.map(function (f) {
          return "<option>" + escapar(f) + "</option>";
        }).join("") + "</select>"
      : "";
    return busca || filtro
      ? '<div class="eeg-toolbar">' + busca + filtro + "</div>"
      : "";
  }

  var AVISO = "Operação EEGLife ainda sem dados vinculados.";

  function htmlModulo(mod, grupo) {
    var cabecalho = '<div class="section-title"><div>' +
      '<p class="eyebrow">' + escapar(mod.eyebrow || grupo) + "</p>" +
      "<h2>" + escapar(mod.rotulo) + "</h2></div>" +
      '<span class="safe-label">' + escapar(AVISO) + "</span></div>";

    var corpo = "";
    if (mod.forma === "painel") {
      corpo = htmlIndicadores() +
        '<div class="grid-two">' +
        '<article class="panel"><div class="panel-header"><h3>' +
        escapar(mod.rotulo) + "</h3><span>" + escapar(mod.resumo) +
        '</span></div><div class="empty-state">Sem fonte ativa para a operação EEGLife.</div></article>' +
        '<article class="panel"><div class="panel-header"><h3>Fontes</h3>' +
        "<span>De onde viriam estes números</span></div>" +
        '<div class="empty-state">Nenhuma fonte de dados vinculada à EEGLife.</div></article></div>';
    } else {
      corpo = '<article class="panel"><div class="panel-header"><h3>' +
        escapar(mod.rotulo) + "</h3><span>" + escapar(mod.resumo) + "</span>" +
        htmlControles(mod) + "</div>" +
        htmlTabelaVazia(mod.colunas, AVISO) + "</article>";
      if (mod.nota) {
        corpo += '<p class="eeg-nota">' + escapar(mod.nota) + "</p>";
      }
    }
    return '<section class="eeg-section" id="eegSec-' + escapar(mod.id) +
      '" data-eeg-section="' + escapar(mod.id) + '"' +
      ' data-eeg-titulo="' + escapar(mod.rotulo) + '"' +
      ' data-eeg-eyebrow="' + escapar(mod.eyebrow || "") + '" hidden>' +
      cabecalho + corpo + "</section>";
  }

  function renderCascaEeg() {
    var lista = document.getElementById("eegNavList");
    var conteudo = document.getElementById("eegConteudo");
    if (!lista || !conteudo) return;

    var navHtml = "";
    var secHtml = "";
    MODULOS_EEG.forEach(function (grupo) {
      navHtml += '<div class="eeg-nav-group">' + escapar(grupo.grupo) + "</div>";
      grupo.itens.forEach(function (item) {
        navHtml += '<button type="button" class="eeg-nav-item" data-eeg-modulo="' +
          escapar(item.id) + '" aria-current="false">' +
          '<span class="eeg-nav-icon" aria-hidden="true"></span>' +
          escapar(item.rotulo) + "</button>";
        secHtml += htmlModulo(item, grupo.grupo);
      });
    });
    lista.innerHTML = navHtml;
    conteudo.innerHTML = secHtml;

    Array.prototype.forEach.call(
      lista.querySelectorAll("[data-eeg-modulo]"),
      function (botao) {
        botao.addEventListener("click", function () {
          abrirModuloEeg(botao.getAttribute("data-eeg-modulo"));
        });
      }
    );
  }

  /* Troca de módulo dentro da EEGLife. Não há requisição aqui — nenhum módulo
   * tem backend, e inventar uma chamada só para preencher a tela acabaria
   * trazendo dado da SoproLife com rótulo de EEG. */
  function abrirModuloEeg(id) {
    var mod = moduloEeg(id);
    if (!mod) return;
    moduloAtivo = id;

    Array.prototype.forEach.call(
      document.querySelectorAll("[data-eeg-modulo]"),
      function (b) {
        b.setAttribute("aria-current",
          b.getAttribute("data-eeg-modulo") === id ? "true" : "false");
      }
    );
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-eeg-section]"),
      function (s) { s.hidden = s.getAttribute("data-eeg-section") !== id; }
    );
    var titulo = document.getElementById("eegTopbarTitulo");
    if (titulo) titulo.textContent = mod.rotulo;

    // Fase 2A — alguns módulos deixaram de ser casca: eles buscam dado real
    // nas rotas /eeg. Quem sabe disso é `js/eeg-operacao.js`; se ele estiver
    // presente e assumir o módulo, a estrutura vazia some. Os demais módulos
    // seguem como estrutura, e é por isso que a troca é opcional em vez de
    // obrigatória.
    var secao = document.querySelector('[data-eeg-section="' + id + '"]');
    var operacao = window.SoproEegOperacao;
    if (secao && operacao && typeof operacao.montar === "function") {
      operacao.montar(id, secao);
    }
  }

  /* ── Aplicação do contexto ──────────────────────────────────────────────── */

  function aplicar(id, origem) {
    if (!permitido(id)) id = PADRAO;
    atual = id;

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
        aba.setAttribute("aria-current", ativo ? "true" : "false");
        aba.setAttribute("tabindex", ativo ? "0" : "-1");
      }
    });

    var rotulo = document.getElementById("lifeContextoRotulo");
    if (rotulo) {
      rotulo.innerHTML = "Contexto: <b>" + escapar(CONTEXTOS[id].rotulo) + "</b>";
    }
    document.title = CONTEXTOS[id].rotulo === "SoproLife"
      ? "Painel SoproLife · Centro de Comando Life"
      : CONTEXTOS[id].rotulo + " · Centro de Comando Life";

    salvar(id);

    if (origem === "usuario") {
      var painelAtivo = document.getElementById(CONTEXTOS[id].painel);
      if (painelAtivo) {
        painelAtivo.setAttribute("tabindex", "-1");
        try { painelAtivo.focus({ preventScroll: true }); } catch (e) { painelAtivo.focus(); }
      }
    }

    (ouvintes[id] || []).forEach(function (cb) {
      try { cb(id); } catch (e) { console.error("ouvinte de contexto falhou", e); }
    });
  }

  /* Porta única de troca de contexto.
   *
   * Recusa o que a sessão não permite — é isto que impede forçar a EEGLife por
   * localStorage, por console ou por qualquer caminho de interface. Quando
   * recusa, o contexto permanece (ou volta a ser) SoproLife. */
  function definir(id) {
    if (!permitido(id)) {
      if (valido(id) && id !== PADRAO) {
        console.warn("Contexto não autorizado para esta sessão: " + id);
      }
      id = PADRAO;
    }
    if (id === atual && aplicado) return;
    aplicar(id, "usuario");
  }

  /* ── Teclado: padrão de tablist ─────────────────────────────────────────── */

  function teclado(evento) {
    var passo = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    var visiveis = ORDEM.filter(permitido);
    if (visiveis.length < 2) return;
    var alvo;
    if (evento.key === "Home") alvo = visiveis[0];
    else if (evento.key === "End") alvo = visiveis[visiveis.length - 1];
    else if (Object.prototype.hasOwnProperty.call(passo, evento.key)) {
      var i = visiveis.indexOf(contexto());
      if (i === -1) i = 0;
      alvo = visiveis[(i + passo[evento.key] + visiveis.length) % visiveis.length];
    } else {
      return;
    }
    evento.preventDefault();
    definir(alvo);
    var aba = document.getElementById(CONTEXTOS[alvo].aba);
    if (aba) aba.focus();
  }

  /* ── Ligação ────────────────────────────────────────────────────────────── */

  function ligarInterface() {
    renderCascaEeg();
    if (MODULOS_EEG[0] && MODULOS_EEG[0].itens[0]) {
      abrirModuloEeg(MODULOS_EEG[0].itens[0].id);
    }
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-life-context]"),
      function (aba) {
        aba.addEventListener("click", function () {
          definir(aba.getAttribute("data-life-context"));
        });
        aba.addEventListener("keydown", teclado);
      }
    );
  }

  /* O contexto só é aplicado DEPOIS que o servidor disse quem é a sessão.
   *
   * A ordem importa: aplicar antes e corrigir depois faria o painel SoproLife
   * carregar os dados para quem salvou "eeglife", e faria a casca EEGLife
   * piscar na tela de quem não pode vê-la. */
  function decidirEaplicar(identidade) {
    permitidos = calcularPermitidos(identidade);
    podarMarcasNaoPermitidas();
    aplicar(lerSalvo(), "inicial");
    aplicado = true;
  }

  function ligar() {
    ligarInterface();
    var gate = window.SoproBootGate;
    if (gate && gate.pronto && typeof gate.pronto.then === "function") {
      gate.pronto.then(decidirEaplicar).catch(function () { decidirEaplicar(null); });
    } else {
      // Sem gate não há identidade confiável: fail-closed, só SoproLife.
      decidirEaplicar(null);
    }
  }

  window.LifeCommandCenter = {
    CONTEXTOS: CONTEXTOS,
    ORDEM: ORDEM,
    MODULOS_EEG: MODULOS_EEG,
    INDICADORES_EEG: INDICADORES_EEG,
    PADRAO: PADRAO,
    CHAVE: CHAVE,
    PAPEL_EEG: PAPEL_EEG,
    getContext: contexto,
    setContext: definir,
    isValid: valido,
    isAllowed: permitido,
    allowedContexts: function () { return permitidos.slice(); },
    openEegModule: abrirModuloEeg,
    activeEegModule: function () { return moduloAtivo; },
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
