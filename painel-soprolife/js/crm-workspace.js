/* CRM Pacientes e Acompanhamento — workspace canônico ÚNICO (M19).
 *
 * Substitui, de uma vez, as duas implementações anteriores:
 *   - a tela legada de pacientes (planilha -> data-private/followup-
 *     pacientes.local.json, com links diretos de WhatsApp);
 *   - a view parcial "Acompanhamento e WhatsApp" do M18.
 *
 * Fonte de dados: SOMENTE a API do Núcleo M15 / PostgreSQL, pela sessão
 * compartilhada window.SoproM15. Nenhum valor de demonstração, nenhum
 * arquivo local, nenhum cadastro novo aqui — criar é sempre na Central de
 * Cadastros (deep-link), este módulo apenas acompanha e registra contato.
 *
 * Privacidade: telefone só aparece mascarado; o número completo existe
 * apenas dentro da URL do WhatsApp montada pela API para revisão humana.
 * Abrir o WhatsApp NUNCA conclui um acompanhamento — o resultado é sempre
 * escolhido explicitamente pelo operador e vira UM registro auditável.
 */
(function () {
  "use strict";

  var VIEWS = [
    ["visao", "Visão geral"],
    ["pacientes", "Pacientes"],
    ["contatos", "Contatos a realizar"],
    ["historico", "Histórico de contatos"],
    ["indicadores", "Indicadores"],
  ];

  var state = {
    view: "visao",
    config: null,
    kpis: null,
    pacientes: null,
    contatos: null,
    historico: null,
    indicadores: null,
    erro: null,
    carregando: false,
    filtroPacientes: { q: "", origem: "todas", status_acompanhamento: "", fila: "" },
    filaAtiva: "hoje",
    filtroHistorico: { inicio: "", fim: "", resultado: "", canal: "", operador: "", origem: "todas" },
    filtroIndicadores: { meses: 12, origem: "todas" },
    charts: {},
    timelineAberta: null,
  };

  /* Atalhos contextuais para o fluxo único "Novo atendimento" (M20).
   * O CRM NÃO reimplementa formulário de cadastro: só abre a Central com o
   * paciente e o tipo já escolhidos. */
  var CENTRAL_ATALHOS = [
    ["espirometria_soprolife", "+ Espirometria"],
    ["espirometria_pastore", "+ Espirometria Pastore"],
    ["consulta_soprolife", "+ Consulta"],
    ["espirometria_consulta_soprolife", "+ Espirometria + Consulta"],
  ];

  // ------------------------------------------------------------ utilitários

  function cli() { return window.SoproM15 || null; }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var p = String(iso).slice(0, 10).split("-");
    return p.length === 3 ? p[2] + "/" + p[1] + "/" + p[0] : iso;
  }

  function fmtMes(periodo) {
    if (!periodo) return "—";
    var p = String(periodo).split("-");
    var meses = ["jan", "fev", "mar", "abr", "mai", "jun",
                 "jul", "ago", "set", "out", "nov", "dez"];
    return p.length >= 2 ? meses[Number(p[1]) - 1] + "/" + p[0].slice(2) : periodo;
  }

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "m15-toast" + (kind === "erro" ? " m15-toast-erro" : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "erro" ? 7000 : 4000);
  }

  // Código sempre exibido do mesmo jeito em toda tela: "Rótulo · PREFIXO-000000".
  function codigo(rotulo, code, opts) {
    if (!code) return '<span class="crm-ws-code crm-ws-code-vazio">—</span>';
    var copia = (opts && opts.copiar)
      ? '<button type="button" class="crm-ws-copy" data-crm-copiar="' + esc(code) +
        '" title="Copiar código" aria-label="Copiar código ' + esc(code) + '">⧉</button>'
      : "";
    return '<span class="crm-ws-code"><span class="crm-ws-code-label">' + esc(rotulo) +
      '</span><span class="crm-ws-code-value">' + esc(code) + "</span>" + copia + "</span>";
  }

  function pill(chave, rotulo) {
    return '<span class="crm-ws-pill crm-ws-pill-' + esc(chave || "neutro") + '">' +
      esc(rotulo || chave || "—") + "</span>";
  }

  function statusRotulo(chave) {
    var lista = (state.config && state.config.status_acompanhamento) || [];
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].chave === chave) return lista[i].rotulo;
    }
    return chave || "—";
  }

  function filaRotulo(chave) {
    var lista = (state.config && state.config.filas) || [];
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].chave === chave) return lista[i].rotulo;
    }
    return chave;
  }

  function telefoneHtml(contato) {
    if (!contato) return '<span class="crm-ws-muted">—</span>';
    if (!contato.telefone_utilizavel) {
      return '<span class="crm-ws-sem-tel" title="Sem telefone discável cadastrado">sem telefone</span>';
    }
    return '<span class="crm-ws-tel" title="Número mascarado por privacidade">' +
      esc(contato.telefone_mascarado || "") + "</span>";
  }

  function contatoHtml(contato) {
    if (!contato) return "—";
    var marca = contato.eh_responsavel
      ? '<span class="crm-ws-tag-resp" title="Contato é o responsável legal, não o paciente">responsável</span>'
      : "";
    return '<div class="crm-ws-contato"><span>' + esc(contato.nome_completo || "—") + "</span>" +
      marca + "<br>" + telefoneHtml(contato) + "</div>";
  }

  // ------------------------------------------------------------ carregamento

  function carregarConfig() {
    if (state.config) return Promise.resolve(state.config);
    return cli().api("/crm/config").then(function (cfg) {
      state.config = cfg;
      return cfg;
    });
  }

  function carregarView() {
    var c = cli();
    if (!c) return Promise.reject(new Error("Núcleo M15 indisponível nesta página."));
    if (state.view === "visao") {
      return c.api("/crm/kpis").then(function (d) { state.kpis = d; });
    }
    if (state.view === "pacientes") {
      var f = state.filtroPacientes;
      var body = { tamanho: 100 };
      if (f.q) body.q = f.q;
      if (f.origem && f.origem !== "todas") body.origem = f.origem;
      if (f.status_acompanhamento) body.status_acompanhamento = f.status_acompanhamento;
      if (f.fila) body.fila = f.fila;
      return c.api("/crm/pacientes/busca", {
        method: "POST", body: JSON.stringify(body),
      }).then(function (d) { state.pacientes = d; });
    }
    if (state.view === "contatos") {
      return c.api("/crm/contatos-a-realizar?fila=" + encodeURIComponent(state.filaAtiva))
        .then(function (d) { state.contatos = d; });
    }
    if (state.view === "historico") {
      var h = state.filtroHistorico;
      var qs = [];
      if (h.inicio) qs.push("inicio=" + encodeURIComponent(h.inicio));
      if (h.fim) qs.push("fim=" + encodeURIComponent(h.fim));
      if (h.resultado) qs.push("resultado=" + encodeURIComponent(h.resultado));
      if (h.canal) qs.push("canal=" + encodeURIComponent(h.canal));
      if (h.operador) qs.push("operador=" + encodeURIComponent(h.operador));
      if (h.origem && h.origem !== "todas") qs.push("origem=" + encodeURIComponent(h.origem));
      return c.api("/crm/historico-contatos" + (qs.length ? "?" + qs.join("&") : ""))
        .then(function (d) { state.historico = d; });
    }
    if (state.view === "indicadores") {
      var i = state.filtroIndicadores;
      var iq = "meses=" + encodeURIComponent(i.meses);
      if (i.origem && i.origem !== "todas") iq += "&origem=" + encodeURIComponent(i.origem);
      return c.api("/crm/indicadores?" + iq).then(function (d) { state.indicadores = d; });
    }
    return Promise.resolve();
  }

  function recarregar(container) {
    state.carregando = true;
    state.erro = null;
    render(container);
    carregarConfig()
      .then(carregarView)
      .then(function () { state.carregando = false; render(container); })
      .catch(function (err) {
        state.carregando = false;
        state.erro = err.message || String(err);
        render(container);
      });
  }

  // ------------------------------------------------------------------ render

  function containerEl() { return document.querySelector("#crmView"); }

  function render(container) {
    container = container || containerEl();
    if (!container) return;
    destruirCharts();

    var c = cli();
    var cabecalho =
      '<div class="crm-subview-header">' +
      '  <button class="crm-back-btn" id="crmWsVoltar" type="button">← CRM</button>' +
      "  <div>" +
      '    <p class="eyebrow">Relacionamento com pacientes</p>' +
      "    <h2>CRM Pacientes e Acompanhamento</h2>" +
      '    <p class="section-sub">Fonte oficial: PostgreSQL / Núcleo M15. Cadastros novos ficam na Central de Cadastros.</p>' +
      "  </div>" +
      "</div>";

    if (!c) {
      container.innerHTML = cabecalho +
        '<div class="m15-aviso">Núcleo M15 indisponível nesta página.</div>';
      ligarVoltar(container);
      return;
    }
    if (!c.hasToken()) {
      container.innerHTML = cabecalho +
        '<div class="m15-panel"><p class="m15-muted">Entre pela Central de Cadastros ou pelo ' +
        "Núcleo administrativo (mesma sessão) para abrir o CRM de pacientes.</p></div>";
      ligarVoltar(container);
      return;
    }

    var abas = VIEWS.map(function (v) {
      return '<button type="button" class="m15-tab' + (v[0] === state.view ? " active" : "") +
        '" data-crm-ws-view="' + v[0] + '" role="tab" aria-selected="' +
        (v[0] === state.view) + '">' + esc(v[1]) + "</button>";
    }).join("");

    var corpo;
    if (state.erro) {
      corpo = '<div class="m15-erro">Erro ao carregar: ' + esc(state.erro) + "</div>";
    } else if (state.carregando) {
      corpo = '<div class="m15-empty">Carregando…</div>';
    } else if (state.view === "visao") {
      corpo = viewVisao();
    } else if (state.view === "pacientes") {
      corpo = viewPacientes();
    } else if (state.view === "contatos") {
      corpo = viewContatos();
    } else if (state.view === "historico") {
      corpo = viewHistorico();
    } else {
      corpo = viewIndicadores();
    }

    container.innerHTML = cabecalho +
      '<div class="m15-tabs crm-ws-tabs" role="tablist" aria-label="Áreas do CRM de pacientes">' +
      abas + "</div>" +
      '<div id="crmWsBody">' + corpo + "</div>";

    ligarVoltar(container);
    ligarAbas(container);
    ligarAcoes(container);
    if (state.view === "indicadores" && !state.erro && !state.carregando) desenharCharts();
  }

  function ligarVoltar(container) {
    var btn = container.querySelector("#crmWsVoltar");
    if (btn) {
      btn.addEventListener("click", function () {
        if (window.SoproCrmHost && typeof window.SoproCrmHost.voltar === "function") {
          window.SoproCrmHost.voltar();
        }
      });
    }
  }

  function ligarAbas(container) {
    container.querySelectorAll("[data-crm-ws-view]").forEach(function (btn) {
      var ativar = function () {
        state.view = btn.getAttribute("data-crm-ws-view");
        recarregar(container);
      };
      btn.addEventListener("click", ativar);
      btn.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ativar(); }
      });
    });
  }

  // ------------------------------------------------------------ VISÃO GERAL

  var KPI_DEFS = [
    ["total_pacientes", "Total de pacientes", { view: "pacientes" }],
    ["contatos_hoje", "Contatos hoje", { view: "contatos", fila: "hoje" }],
    ["contatos_atrasados", "Contatos atrasados", { view: "contatos", fila: "atrasados" }],
    ["proximos_7", "Próximos 7 dias", { view: "contatos", fila: "proximos_7" }],
    ["proximos_30", "Próximos 30 dias", { view: "contatos", fila: "proximos_30" }],
    ["sem_telefone", "Sem telefone válido", { view: "contatos", fila: "sem_telefone" }],
    ["followups_concluidos_mes", "Follow-ups concluídos no mês",
      { view: "historico", resultado: "contato_realizado" }],
    ["pacientes_reativados", "Pacientes reativados", { view: "indicadores" }],
    ["exames_mes", "Exames realizados no mês", { view: "indicadores" }],
    ["consultas_mes", "Consultas realizadas no mês", { view: "indicadores" }],
  ];

  function viewVisao() {
    var k = state.kpis;
    if (!k) return '<div class="m15-empty">Sem dados.</div>';
    var cards = KPI_DEFS.map(function (def) {
      var destino = JSON.stringify(def[2]).replace(/"/g, "&quot;");
      var alerta = (def[0] === "contatos_atrasados" && k[def[0]] > 0) ? " crm-ws-kpi-alerta" : "";
      var hoje = (def[0] === "contatos_hoje" && k[def[0]] > 0) ? " crm-ws-kpi-hoje" : "";
      return '<button type="button" class="crm-ws-kpi' + alerta + hoje +
        '" data-crm-ws-ir="' + destino + '">' +
        '<span class="crm-ws-kpi-label">' + esc(def[1]) + "</span>" +
        '<strong class="crm-ws-kpi-value">' + esc(k[def[0]]) + "</strong>" +
        '<span class="crm-ws-kpi-cta">abrir lista →</span></button>';
    }).join("");

    return '<div class="crm-ws-kpis">' + cards + "</div>" +
      '<div class="crm-ws-nota"><span aria-hidden="true">🔒</span><p>Todos os números vêm ' +
      "das tabelas reais do Núcleo M15 (PostgreSQL) na data de referência " +
      esc(fmtDate(k.data_referencia)) + ". Nenhum valor de demonstração é exibido aqui.</p></div>" +
      '<div class="crm-ws-nota crm-ws-nota-central"><span aria-hidden="true">📝</span>' +
      "<p>Para cadastrar paciente, exame, consulta, lead, parceiro ou lançamento, use a " +
      '<button type="button" class="crm-ws-link" data-crm-ws-central="atendimento" ' +
      'data-crm-ws-tipo="somente_paciente">Central de Cadastros</button>' +
      " — este CRM não duplica formulários de cadastro.</p></div>";
  }

  // -------------------------------------------------------------- PACIENTES

  function viewPacientes() {
    var d = state.pacientes;
    if (!d) return '<div class="m15-empty">Sem dados.</div>';
    var f = state.filtroPacientes;

    var origens = [];
    (d.itens || []).forEach(function (i) {
      if (origens.indexOf(i.origem) === -1) origens.push(i.origem);
    });
    origens.sort();

    var statusOpts = ((state.config && state.config.status_acompanhamento) || [])
      .map(function (s) {
        return '<option value="' + esc(s.chave) + '"' +
          (f.status_acompanhamento === s.chave ? " selected" : "") + ">" + esc(s.rotulo) + "</option>";
      }).join("");

    var filtros =
      '<div class="m15-filtros crm-ws-filtros">' +
      '  <label class="crm-ws-filtro"><span>Buscar</span>' +
      '    <input type="search" id="crmWsBusca" value="' + esc(f.q) +
      '" placeholder="nome ou PES-000000" aria-label="Buscar paciente por nome ou código"></label>' +
      '  <label class="crm-ws-filtro"><span>Origem</span><select id="crmWsOrigem">' +
      '    <option value="todas">Todas</option>' +
      origens.map(function (o) {
        return '<option value="' + esc(o) + '"' + (f.origem === o ? " selected" : "") +
          ">" + esc(o) + "</option>";
      }).join("") +
      "  </select></label>" +
      '  <label class="crm-ws-filtro"><span>Acompanhamento</span><select id="crmWsStatus">' +
      '    <option value="">Todos</option>' + statusOpts + "</select></label>" +
      '  <button type="button" class="m15-btn m15-btn-sec" id="crmWsAplicar">Aplicar</button>' +
      (f.fila ? '  <span class="crm-ws-filtro-ativo">Fila: ' + esc(filaRotulo(f.fila)) +
        ' <button type="button" class="crm-ws-link" id="crmWsLimparFila">limpar</button></span>' : "") +
      "</div>";

    if (!(d.itens || []).length) {
      return filtros + '<div class="m15-empty">Nenhum paciente encontrado com estes filtros.</div>';
    }

    var colFin = d.com_financeiro
      ? "<th scope=\"col\">Financeiro</th>" : "";

    var linhas = (d.itens || []).map(function (p) {
      var fin = "";
      if (d.com_financeiro && p.financeiro) {
        var rot = { conciliado: "conciliado", parcial: "parcial",
                    sem_lancamento: "sem lançamento", nao_aplicavel: "—" };
        fin = '<td data-label="Financeiro">' +
          pill("fin-" + p.financeiro.status, rot[p.financeiro.status] || p.financeiro.status) +
          (p.financeiro.lancamentos && p.financeiro.lancamentos.length
            ? '<div class="crm-ws-codes">' + p.financeiro.lancamentos.map(function (c) {
                return codigo("Lançamento", c);
              }).join("") + "</div>"
            : "") + "</td>";
      }
      var prox = p.proximo_contato;
      return "<tr>" +
        '<td data-label="Paciente">' + codigo("Paciente", p.public_code, { copiar: true }) +
        '<div class="crm-ws-nome">' + esc(p.nome_completo) + "</div>" +
        (p.nao_contatar ? pill("nao_contatar", "Não contatar") : "") + "</td>" +
        '<td data-label="Contato">' + contatoHtml(p.contato) + "</td>" +
        '<td data-label="Última espirometria">' +
        (p.ultimo_exame
          ? codigo("Espirometria", p.ultimo_exame.public_code) +
            '<div class="crm-ws-sub">' + esc(fmtDate(p.ultimo_exame.data)) + "</div>"
          : '<span class="crm-ws-muted">—</span>') + "</td>" +
        '<td data-label="Última consulta">' +
        (p.ultima_consulta
          ? codigo("Consulta", p.ultima_consulta.public_code) +
            '<div class="crm-ws-sub">' + esc(fmtDate(p.ultima_consulta.data)) + "</div>"
          : '<span class="crm-ws-muted">—</span>') + "</td>" +
        '<td data-label="Próximo contato">' +
        (prox ? esc(fmtDate(prox.due_date)) + "<br>" +
                pill(prox.status_apresentacao, statusRotulo(prox.status_apresentacao))
              : '<span class="crm-ws-muted">—</span>') + "</td>" +
        '<td data-label="Origem">' + esc(p.origem) + "</td>" +
        fin +
        '<td data-label="Ações" class="crm-ws-acoes-col">' + acoesPaciente(p) + "</td>" +
        "</tr>";
    }).join("");

    return filtros +
      '<div class="crm-ws-total">' + esc(d.total) + " paciente(s) · fonte PostgreSQL</div>" +
      '<div class="m15-table-wrap crm-ws-table-wrap"><table class="m15-table crm-ws-table">' +
      '<thead><tr><th scope="col">Paciente</th><th scope="col">Contato</th>' +
      '<th scope="col">Última espirometria</th><th scope="col">Última consulta</th>' +
      '<th scope="col">Próximo contato</th><th scope="col">Origem</th>' + colFin +
      '<th scope="col">Ações</th></tr></thead><tbody>' + linhas + "</tbody></table></div>";
  }

  function acoesPaciente(p) {
    var botoes = [];
    if (p.contato && p.contato.telefone_utilizavel && !p.nao_contatar) {
      botoes.push('<button type="button" class="m15-btn m15-btn-wa crm-ws-mini" ' +
        'data-crm-ws-wa-pessoa="' + esc(p.person_id) + '">WhatsApp</button>');
    }
    botoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
      'data-crm-ws-timeline="' + esc(p.person_id) + '">Histórico</button>');
    if (p.proximo_contato) {
      botoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
        'data-crm-ws-resultado="' + esc(p.proximo_contato.followup_id) + '">Resultado</button>');
      botoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
        'data-crm-ws-reagendar="' + esc(p.proximo_contato.followup_id) + '">Reagendar</button>');
    }
    // Botoes contextuais abrem o fluxo unico "Novo atendimento" (M20) com o
    // tipo ja selecionado. So o codigo publico viaja — nunca nome ou telefone.
    CENTRAL_ATALHOS.forEach(function (a) {
      botoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
        'data-crm-ws-central="atendimento" data-crm-ws-tipo="' + esc(a[0]) + '" ' +
        'data-crm-ws-codigo="' + esc(p.public_code) + '">' + esc(a[1]) + "</button>");
    });
    return '<div class="crm-ws-acoes">' + botoes.join("") + "</div>";
  }

  // ------------------------------------------------------- CONTATOS A FAZER

  function viewContatos() {
    var d = state.contatos;
    if (!d) return '<div class="m15-empty">Sem dados.</div>';
    var totais = d.totais || {};

    var chips = ((state.config && state.config.filas) || []).map(function (f) {
      return '<button type="button" class="crm-ws-chip' +
        (state.filaAtiva === f.chave ? " active" : "") + '" data-crm-ws-fila="' +
        esc(f.chave) + '" aria-pressed="' + (state.filaAtiva === f.chave) + '">' +
        esc(f.rotulo) + '<span class="crm-ws-chip-n">' + esc(totais[f.chave] || 0) + "</span></button>";
    }).join("");

    var itens = d.itens || [];
    var corpo;
    if (!itens.length) {
      corpo = '<div class="m15-empty">Nenhum contato nesta fila. ' +
        "Nada a fazer aqui hoje.</div>";
    } else {
      corpo = '<div class="m15-table-wrap crm-ws-table-wrap"><table class="m15-table crm-ws-table">' +
        '<thead><tr><th scope="col">Paciente</th><th scope="col">Contato</th>' +
        '<th scope="col">Motivo</th><th scope="col">Origem</th><th scope="col">Vencimento</th>' +
        '<th scope="col">Situação</th><th scope="col">Ações</th></tr></thead><tbody>' +
        itens.map(function (it) {
          var origem = it.origem
            ? codigo(it.origem.entidade === "consultations" ? "Consulta" : "Espirometria",
                     it.origem.public_code) +
              '<div class="crm-ws-sub">' + esc(fmtDate(it.origem.data)) + "</div>"
            : '<span class="crm-ws-muted">—</span>';
          var acoes = [];
          if (it.contato.telefone_utilizavel && !it.controlado_por_parceiro &&
              it.filas.indexOf("nao_contatar") === -1) {
            acoes.push('<button type="button" class="m15-btn m15-btn-wa crm-ws-mini" ' +
              'data-crm-ws-wa-followup="' + esc(it.followup_id) + '">WhatsApp</button>');
          }
          acoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
            'data-crm-ws-resultado="' + esc(it.followup_id) + '">Registrar resultado</button>');
          acoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
            'data-crm-ws-reagendar="' + esc(it.followup_id) + '">Reagendar</button>');
          acoes.push('<button type="button" class="m15-btn m15-btn-sec crm-ws-mini" ' +
            'data-crm-ws-timeline="' + esc(it.paciente.person_id) + '">Histórico</button>');
          return "<tr>" +
            '<td data-label="Paciente">' +
            codigo("Paciente", it.paciente.public_code, { copiar: true }) +
            '<div class="crm-ws-nome">' + esc(it.paciente.nome_completo) + "</div>" +
            codigo("Follow-up", it.followup_public_code) + "</td>" +
            '<td data-label="Contato">' + contatoHtml(it.contato) + "</td>" +
            '<td data-label="Motivo">' + esc(it.motivo) +
            (it.controlado_por_parceiro
              ? '<div class="crm-ws-sub">controlado pela clínica parceira</div>' : "") + "</td>" +
            '<td data-label="Origem">' + origem + "</td>" +
            '<td data-label="Vencimento">' + esc(fmtDate(it.due_date)) +
            (it.tentativas ? '<div class="crm-ws-sub">' + esc(it.tentativas) +
              " tentativa(s)</div>" : "") + "</td>" +
            '<td data-label="Situação">' +
            pill(it.status_apresentacao, statusRotulo(it.status_apresentacao)) + "</td>" +
            '<td data-label="Ações" class="crm-ws-acoes-col"><div class="crm-ws-acoes">' +
            acoes.join("") + "</div></td></tr>";
        }).join("") + "</tbody></table></div>";
    }

    return '<div class="crm-ws-chips" role="group" aria-label="Filas de contato">' + chips + "</div>" +
      '<div class="crm-ws-total">Referência ' + esc(fmtDate(d.data_referencia)) +
      " · " + esc(itens.length) + " item(ns) na fila " + esc(filaRotulo(state.filaAtiva)) + "</div>" +
      corpo +
      '<div class="crm-ws-nota"><span aria-hidden="true">💬</span><p>Abrir o WhatsApp ' +
      "<strong>não</strong> conclui o acompanhamento. Depois de enviar, registre o resultado " +
      "— cada resultado grava um registro auditável de tentativa.</p></div>";
  }

  // -------------------------------------------------------------- HISTÓRICO

  function viewHistorico() {
    var d = state.historico;
    if (!d) return '<div class="m15-empty">Sem dados.</div>';
    var h = state.filtroHistorico;

    var filtros =
      '<div class="m15-filtros crm-ws-filtros">' +
      '  <label class="crm-ws-filtro"><span>De</span><input type="date" id="crmWsHIni" value="' +
      esc(h.inicio) + '"></label>' +
      '  <label class="crm-ws-filtro"><span>Até</span><input type="date" id="crmWsHFim" value="' +
      esc(h.fim) + '"></label>' +
      '  <label class="crm-ws-filtro"><span>Resultado</span><select id="crmWsHRes">' +
      '<option value="">Todos</option>' +
      (d.resultados || []).map(function (r) {
        return '<option value="' + esc(r.chave) + '"' + (h.resultado === r.chave ? " selected" : "") +
          ">" + esc(r.rotulo) + "</option>";
      }).join("") + "</select></label>" +
      '  <label class="crm-ws-filtro"><span>Canal</span><select id="crmWsHCanal">' +
      ["", "whatsapp", "telefone", "email", "presencial", "outro"].map(function (c) {
        return '<option value="' + esc(c) + '"' + (h.canal === c ? " selected" : "") + ">" +
          (c ? esc(c) : "Todos") + "</option>";
      }).join("") + "</select></label>" +
      '  <label class="crm-ws-filtro"><span>Operador</span><select id="crmWsHOper">' +
      '<option value="">Todos</option>' +
      (d.operadores || []).map(function (o) {
        return '<option value="' + esc(o.id) + '"' + (h.operador === o.id ? " selected" : "") +
          ">" + esc(o.nome || o.id) + "</option>";
      }).join("") + "</select></label>" +
      '  <button type="button" class="m15-btn m15-btn-sec" id="crmWsHAplicar">Aplicar</button>' +
      "</div>";

    if (!(d.itens || []).length) {
      return filtros + '<div class="m15-empty">Nenhuma tentativa de contato registrada ' +
        "com estes filtros.</div>";
    }

    var linhas = d.itens.map(function (i) {
      return "<tr>" +
        '<td data-label="Quando">' + esc(fmtDate(i.ts_utc)) +
        '<div class="crm-ws-sub">' + esc((i.ts_utc || "").slice(11, 16)) + " UTC</div></td>" +
        '<td data-label="Paciente">' +
        (i.paciente ? codigo("Paciente", i.paciente.public_code) +
          '<div class="crm-ws-nome">' + esc(i.paciente.nome_completo) + "</div>" : "—") + "</td>" +
        '<td data-label="Contatado">' +
        (i.contatado
          ? esc(i.contatado.nome_completo) +
            (i.contatado.diferente_do_paciente
              ? '<span class="crm-ws-tag-resp">responsável</span>' : "")
          : "—") + "</td>" +
        '<td data-label="Canal">' + esc(i.canal) + "</td>" +
        '<td data-label="Resultado">' + pill(i.resultado, i.resultado_rotulo) + "</td>" +
        '<td data-label="Follow-up">' +
        (i.followup ? codigo("Follow-up", i.followup.public_code) +
          '<div class="crm-ws-sub">venc. ' + esc(fmtDate(i.followup.due_date)) + "</div>" : "—") +
        "</td>" +
        '<td data-label="Operador">' + esc((i.operador && i.operador.nome) || "—") + "</td>" +
        '<td data-label="Observação">' +
        (i.observacao ? esc(i.observacao) : '<span class="crm-ws-muted">—</span>') + "</td>" +
        "</tr>";
    }).join("");

    return filtros +
      '<div class="crm-ws-total">' + esc(d.total) + " tentativa(s) registrada(s)</div>" +
      '<div class="m15-table-wrap crm-ws-table-wrap"><table class="m15-table crm-ws-table">' +
      '<thead><tr><th scope="col">Quando</th><th scope="col">Paciente</th>' +
      '<th scope="col">Contatado</th><th scope="col">Canal</th><th scope="col">Resultado</th>' +
      '<th scope="col">Follow-up</th><th scope="col">Operador</th>' +
      '<th scope="col">Observação</th></tr></thead><tbody>' + linhas + "</tbody></table></div>" +
      '<div class="crm-ws-nota"><span aria-hidden="true">🔒</span><p>O histórico exibe ' +
      "apenas dado operacional: nenhum telefone completo, CPF ou narrativa clínica é " +
      "guardado ou exportado por esta tela.</p></div>";
  }

  // ------------------------------------------------------------ INDICADORES

  var CHART_DEFS = [
    ["contatos_por_periodo", "Contatos por período", "bar", "periodo"],
    ["followups_concluidos_por_mes", "Follow-ups concluídos por mês", "line", "periodo"],
    ["exames_por_mes", "Espirometrias por mês", "bar", "periodo"],
    ["consultas_por_mes", "Consultas por mês", "bar", "periodo"],
    ["pacientes_reativados_por_mes", "Pacientes reativados", "bar", "periodo"],
    ["resultados_de_contato", "Resultados de contato", "doughnut", "rotulo"],
    ["pacientes_por_origem", "Pacientes por origem", "doughnut", "origem"],
  ];

  function viewIndicadores() {
    var d = state.indicadores;
    if (!d) return '<div class="m15-empty">Sem dados.</div>';
    var f = state.filtroIndicadores;

    var filtros =
      '<div class="m15-filtros crm-ws-filtros">' +
      '  <label class="crm-ws-filtro"><span>Período</span><select id="crmWsIMeses">' +
      [[6, "Últimos 6 meses"], [12, "Últimos 12 meses"], [24, "Últimos 24 meses"],
       [60, "Tudo"]].map(function (o) {
        return '<option value="' + o[0] + '"' + (Number(f.meses) === o[0] ? " selected" : "") +
          ">" + esc(o[1]) + "</option>";
      }).join("") + "</select></label>" +
      '  <label class="crm-ws-filtro"><span>Origem</span><select id="crmWsIOrigem">' +
      '<option value="todas">Todas</option>' +
      (d.origens_disponiveis || []).map(function (o) {
        return '<option value="' + esc(o) + '"' + (f.origem === o ? " selected" : "") + ">" +
          esc(o) + "</option>";
      }).join("") + "</select></label>" +
      '  <button type="button" class="m15-btn m15-btn-sec" id="crmWsIAplicar">Aplicar</button>' +
      "</div>";

    var destaque = '<div class="crm-ws-kpis crm-ws-kpis-3">' +
      '<div class="crm-ws-kpi crm-ws-kpi-estatico' +
      (d.contatos_atrasados > 0 ? " crm-ws-kpi-alerta" : "") + '">' +
      '<span class="crm-ws-kpi-label">Contatos atrasados</span>' +
      '<strong class="crm-ws-kpi-value">' + esc(d.contatos_atrasados) + "</strong></div>" +
      '<div class="crm-ws-kpi crm-ws-kpi-estatico"><span class="crm-ws-kpi-label">Origens</span>' +
      '<strong class="crm-ws-kpi-value">' + esc((d.origens_disponiveis || []).length) +
      "</strong></div>" +
      '<div class="crm-ws-kpi crm-ws-kpi-estatico"><span class="crm-ws-kpi-label">Referência</span>' +
      '<strong class="crm-ws-kpi-value crm-ws-kpi-data">' + esc(fmtDate(d.data_referencia)) +
      "</strong></div></div>";

    var graficos = CHART_DEFS.map(function (def) {
      var serie = d[def[0]] || [];
      var vazio = !serie.length || serie.every(function (p) { return !p.valor; });
      return '<article class="crm-ws-chart-card"><h3>' + esc(def[1]) + "</h3>" +
        (vazio
          ? '<p class="crm-ws-chart-vazio">Sem dados no período selecionado.</p>'
          : '<div class="crm-ws-chart-box"><canvas id="crmWsChart_' + def[0] +
            '" role="img" aria-label="' + esc(def[1]) + '"></canvas></div>') +
        "</article>";
    }).join("");

    return filtros + destaque +
      '<div class="crm-ws-charts">' + graficos + "</div>" +
      '<div class="crm-ws-nota"><span aria-hidden="true">📊</span><p>Todos os gráficos ' +
      "agregam registros reais do PostgreSQL. Séries sem dado mostram estado vazio " +
      "explícito — nenhum valor é simulado.</p></div>";
  }

  var PALETA = ["#1db7a6", "#0b1f36", "#e7a93f", "#e45c64", "#5b8def", "#16a37d", "#8a7fd1"];

  function destruirCharts() {
    Object.keys(state.charts).forEach(function (k) {
      try { state.charts[k].destroy(); } catch (e) { /* ignorado */ }
      delete state.charts[k];
    });
  }

  function desenharCharts() {
    if (typeof window.Chart === "undefined") return;
    var d = state.indicadores;
    if (!d) return;
    CHART_DEFS.forEach(function (def) {
      var serie = d[def[0]] || [];
      if (!serie.length || serie.every(function (p) { return !p.valor; })) return;
      var canvas = document.getElementById("crmWsChart_" + def[0]);
      if (!canvas) return;
      var labels = serie.map(function (p) {
        return def[3] === "periodo" ? fmtMes(p.periodo) : String(p[def[3]] || "—");
      });
      var valores = serie.map(function (p) { return p.valor; });
      var doughnut = def[2] === "doughnut";
      state.charts[def[0]] = new window.Chart(canvas.getContext("2d"), {
        type: def[2],
        data: {
          labels: labels,
          datasets: [{
            label: def[1],
            data: valores,
            backgroundColor: doughnut
              ? labels.map(function (_, i) { return PALETA[i % PALETA.length]; })
              : "#1db7a6",
            borderColor: "#0f8f83",
            borderWidth: def[2] === "line" ? 2 : 0,
            fill: false,
            tension: 0.25,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: doughnut, position: "bottom",
                      labels: { boxWidth: 10, font: { size: 11 } } },
            tooltip: { enabled: true },
          },
          scales: doughnut ? {} : {
            y: { beginAtZero: true, ticks: { precision: 0 } },
            x: { ticks: { font: { size: 11 } } },
          },
        },
      });
    });
  }

  // ---------------------------------------------------------------- MODAIS

  var focoAnterior = null;

  function abrirModal(html, wire) {
    focoAnterior = document.activeElement;
    var overlay = document.createElement("div");
    overlay.className = "m15-modal-overlay crm-ws-modal-overlay";
    overlay.innerHTML = '<div class="m15-modal crm-ws-modal" role="dialog" aria-modal="true">' +
      html + "</div>";
    document.body.appendChild(overlay);

    var modal = overlay.querySelector(".crm-ws-modal");
    function fechar() {
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
      if (focoAnterior && focoAnterior.focus) focoAnterior.focus();
    }
    function focaveis() {
      return Array.prototype.slice.call(modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )).filter(function (el) { return !el.disabled && el.offsetParent !== null; });
    }
    function onKey(ev) {
      if (ev.key === "Escape") { ev.preventDefault(); fechar(); return; }
      if (ev.key !== "Tab") return;
      var lista = focaveis();
      if (!lista.length) return;
      var primeiro = lista[0], ultimo = lista[lista.length - 1];
      if (ev.shiftKey && document.activeElement === primeiro) {
        ev.preventDefault(); ultimo.focus();
      } else if (!ev.shiftKey && document.activeElement === ultimo) {
        ev.preventDefault(); primeiro.focus();
      }
    }
    document.addEventListener("keydown", onKey, true);
    overlay.addEventListener("click", function (ev) { if (ev.target === overlay) fechar(); });
    overlay.querySelectorAll("[data-crm-ws-fechar]").forEach(function (b) {
      b.addEventListener("click", fechar);
    });
    if (wire) wire(overlay, fechar);
    var iniciais = focaveis();
    if (iniciais.length) iniciais[0].focus();
    return fechar;
  }

  function templatesOptions(selecionado) {
    return ((state.config && state.config.templates_whatsapp) || []).map(function (t) {
      return '<option value="' + esc(t.chave) + '"' +
        (t.chave === selecionado ? " selected" : "") + ">" + esc(t.rotulo) + "</option>";
    }).join("");
  }

  function previaWhatsapp(fonte, id, templateInicial) {
    var c = cli();
    var url = fonte === "followup"
      ? "/followups/" + encodeURIComponent(id) + "/whatsapp-url"
      : "/crm/pacientes/" + encodeURIComponent(id) + "/whatsapp-url?template=" +
        encodeURIComponent(templateInicial || "geral");
    return c.api(url);
  }

  function modalWhatsapp(fonte, id) {
    previaWhatsapp(fonte, id, "geral").then(function (resp) {
      var contato = resp.contato || {};
      var quemHtml = fonte === "followup"
        ? '<p class="m15-muted">Confirme com quem você vai falar antes de abrir o WhatsApp.</p>'
        : '<p class="crm-ws-modal-quem"><strong>' + esc(contato.nome_completo || "") + "</strong>" +
          (contato.eh_responsavel
            ? '<span class="crm-ws-tag-resp">responsável legal, não o paciente</span>'
            : '<span class="crm-ws-tag-pac">o próprio paciente</span>') +
          '<br><span class="crm-ws-tel">' + esc(contato.telefone_mascarado || "") + "</span></p>";

      var seletor = fonte === "followup" ? "" :
        '<label class="crm-ws-filtro crm-ws-modal-campo"><span>Modelo de mensagem</span>' +
        '<select id="crmWsWaTpl">' + templatesOptions("geral") + "</select></label>";

      abrirModal(
        "<h3>Prévia da mensagem de WhatsApp</h3>" +
        quemHtml +
        seletor +
        '<label class="crm-ws-modal-campo"><span>Mensagem (edite antes de enviar)</span>' +
        '<textarea id="crmWsWaMsg" rows="5">' + esc(resp.mensagem_sugerida || "") +
        "</textarea></label>" +
        '<p class="m15-muted">Abrir o WhatsApp <strong>não</strong> conclui o acompanhamento. ' +
        "Depois de enviar, registre o resultado.</p>" +
        '<div class="m15-modal-actions">' +
        '<button type="button" class="m15-btn m15-btn-sec" data-crm-ws-fechar>Cancelar</button>' +
        '<button type="button" class="m15-btn m15-btn-wa" id="crmWsWaAbrir">Abrir WhatsApp</button>' +
        "</div>",
        function (overlay, fechar) {
          var msgEl = overlay.querySelector("#crmWsWaMsg");
          var estado = { url: resp.url, base: resp.mensagem_sugerida || "" };
          var tpl = overlay.querySelector("#crmWsWaTpl");
          if (tpl) {
            tpl.addEventListener("change", function () {
              previaWhatsapp(fonte, id, tpl.value).then(function (r2) {
                estado.url = r2.url;
                estado.base = r2.mensagem_sugerida || "";
                msgEl.value = estado.base;
              }).catch(function (err) { toast(err.message || String(err), "erro"); });
            });
          }
          overlay.querySelector("#crmWsWaAbrir").addEventListener("click", function () {
            // A URL da API já traz a mensagem sugerida; se o operador editou,
            // reescreve só o parâmetro de texto — o telefone nunca é remontado
            // aqui no cliente.
            var final = estado.url;
            if (msgEl.value !== estado.base) {
              final = estado.url.split("?")[0] + "?text=" + encodeURIComponent(msgEl.value);
            }
            window.open(final, "_blank", "noopener,noreferrer");
            fechar();
            if (fonte === "followup") modalResultado(id);
            else toast("Registre o resultado do contato quando terminar.");
          });
        }
      );
    }).catch(function (err) {
      toast("Não foi possível montar a prévia: " + (err.message || err), "erro");
    });
  }

  function modalResultado(followupId, personId) {
    var opts = ((state.config && state.config.resultados_contato) || []).map(function (r) {
      return '<option value="' + esc(r.chave) + '">' + esc(r.rotulo) + "</option>";
    }).join("");
    abrirModal(
      "<h3>Resultado do contato</h3>" +
      '<p class="m15-muted">Todo resultado cria exatamente um registro auditável de ' +
      "tentativa. Escolha o que realmente aconteceu.</p>" +
      '<label class="crm-ws-modal-campo"><span>Resultado</span>' +
      '<select id="crmWsResSel">' + opts + "</select></label>" +
      '<label class="crm-ws-modal-campo" id="crmWsResDataBox" hidden><span>Nova data</span>' +
      '<input type="date" id="crmWsResData"></label>' +
      '<label class="crm-ws-modal-campo"><span>Observação operacional (sem telefone/CPF)</span>' +
      '<textarea id="crmWsResObs" rows="3" maxlength="2000"></textarea></label>' +
      '<div class="m15-modal-actions">' +
      '<button type="button" class="m15-btn m15-btn-sec" data-crm-ws-fechar>Cancelar</button>' +
      '<button type="button" class="m15-btn" id="crmWsResSalvar">Registrar</button></div>',
      function (overlay, fechar) {
        var sel = overlay.querySelector("#crmWsResSel");
        var dataBox = overlay.querySelector("#crmWsResDataBox");
        sel.addEventListener("change", function () {
          dataBox.hidden = sel.value !== "reagendar";
        });
        overlay.querySelector("#crmWsResSalvar").addEventListener("click", function () {
          var payload = { resultado: sel.value, canal: "whatsapp" };
          if (followupId) payload.followup_id = followupId;
          else payload.person_id = personId;
          var obs = overlay.querySelector("#crmWsResObs").value.trim();
          if (obs) payload.observacao = obs;
          if (sel.value === "reagendar") {
            var nd = overlay.querySelector("#crmWsResData").value;
            if (!nd) { toast("Escolha a nova data para reagendar.", "erro"); return; }
            payload.nova_data = nd;
          }
          cli().api("/crm/contatos", { method: "POST", body: JSON.stringify(payload) })
            .then(function () {
              fechar();
              toast("Contato registrado.");
              recarregar(containerEl());
            })
            .catch(function (err) { toast(err.message || String(err), "erro"); });
        });
      }
    );
  }

  function modalReagendar(followupId) {
    abrirModal(
      "<h3>Reagendar contato</h3>" +
      '<p class="m15-muted">O novo vencimento passa a ser manual e o acompanhamento ' +
      "volta para a fila de reagendados.</p>" +
      '<label class="crm-ws-modal-campo"><span>Nova data</span>' +
      '<input type="date" id="crmWsReagData"></label>' +
      '<label class="crm-ws-modal-campo"><span>Observação (opcional)</span>' +
      '<textarea id="crmWsReagObs" rows="3" maxlength="2000"></textarea></label>' +
      '<div class="m15-modal-actions">' +
      '<button type="button" class="m15-btn m15-btn-sec" data-crm-ws-fechar>Cancelar</button>' +
      '<button type="button" class="m15-btn" id="crmWsReagSalvar">Reagendar</button></div>',
      function (overlay, fechar) {
        overlay.querySelector("#crmWsReagSalvar").addEventListener("click", function () {
          var nd = overlay.querySelector("#crmWsReagData").value;
          if (!nd) { toast("Escolha a nova data.", "erro"); return; }
          var payload = { followup_id: followupId, resultado: "reagendar", nova_data: nd };
          var obs = overlay.querySelector("#crmWsReagObs").value.trim();
          if (obs) payload.observacao = obs;
          cli().api("/crm/contatos", { method: "POST", body: JSON.stringify(payload) })
            .then(function () {
              fechar();
              toast("Contato reagendado.");
              recarregar(containerEl());
            })
            .catch(function (err) { toast(err.message || String(err), "erro"); });
        });
      }
    );
  }

  var ICONE_EVENTO = {
    cadastro: "👤", lead: "📥", espirometria: "🫁", consulta: "🩺",
    followup: "📅", interacao: "💬", financeiro: "💰",
  };

  function modalTimeline(personId) {
    cli().api("/crm/pacientes/" + encodeURIComponent(personId) + "/timeline")
      .then(function (d) {
        var p = d.paciente || {};
        var eventos = (d.eventos || []).slice().reverse();
        var lista = eventos.length
          ? '<ol class="crm-ws-timeline">' + eventos.map(function (e) {
              var rotuloCodigo = {
                people: "Pessoa", leads: "Lead", spirometry_exams: "Espirometria",
                consultations: "Consulta", followups: "Follow-up",
                interactions: "Interação", financial_entries: "Lançamento",
              }[e.entidade] || "Registro";
              return '<li class="crm-ws-tl-item crm-ws-tl-' + esc(e.tipo) + '">' +
                '<span class="crm-ws-tl-icone" aria-hidden="true">' +
                (ICONE_EVENTO[e.tipo] || "•") + "</span>" +
                '<div class="crm-ws-tl-corpo">' +
                '<div class="crm-ws-tl-topo"><strong>' + esc(e.titulo) + "</strong>" +
                '<span class="crm-ws-tl-data">' + esc(fmtDate(e.data)) + "</span></div>" +
                '<div class="crm-ws-tl-meta">' + codigo(rotuloCodigo, e.public_code) +
                (e.detalhe ? '<span class="crm-ws-tl-detalhe">' + esc(e.detalhe) + "</span>" : "") +
                (e.parceiro ? '<span class="crm-ws-tl-detalhe">' + esc(e.parceiro) + "</span>" : "") +
                (e.valor ? '<span class="crm-ws-tl-detalhe">R$ ' + esc(e.valor) + "</span>" : "") +
                "</div></div></li>";
            }).join("") + "</ol>"
          : '<p class="m15-muted">Nenhum evento registrado para este paciente.</p>';

        abrirModal(
          "<h3>Linha do tempo do paciente</h3>" +
          '<p class="crm-ws-modal-quem"><strong>' + esc(p.nome_completo || "") + "</strong> " +
          codigo("Paciente", p.public_code, { copiar: true }) +
          (d.responsavel
            ? '<br><span class="crm-ws-sub">Responsável: ' + esc(d.responsavel.nome_completo) +
              " " + codigo("Pessoa", d.responsavel.public_code) + "</span>"
            : "") + "</p>" +
          '<div class="crm-ws-timeline-wrap">' + lista + "</div>" +
          (d.com_financeiro ? "" :
            '<p class="m15-muted">Eventos financeiros ficam visíveis apenas para gestão.</p>') +
          '<div class="m15-modal-actions">' +
          '<button type="button" class="m15-btn m15-btn-sec" data-crm-ws-fechar>Fechar</button>' +
          CENTRAL_ATALHOS.map(function (a) {
            return '<button type="button" class="m15-btn m15-btn-sec" ' +
              'data-crm-ws-central="atendimento" data-crm-ws-tipo="' + esc(a[0]) + '" ' +
              'data-crm-ws-codigo="' + esc(p.public_code) + '">' + esc(a[1]) + "</button>";
          }).join("") + "</div>",
          function (overlay, fechar) {
            overlay.querySelectorAll("[data-crm-ws-central]").forEach(function (b) {
              b.addEventListener("click", function () {
                fechar();
                abrirCentral(b.getAttribute("data-crm-ws-central"),
                             b.getAttribute("data-crm-ws-codigo"),
                             b.getAttribute("data-crm-ws-tipo"));
              });
            });
          }
        );
      })
      .catch(function (err) { toast(err.message || String(err), "erro"); });
  }

  function abrirCentral(tab, codigoPessoa, tipo) {
    if (!window.SoproCentral || typeof window.SoproCentral.open !== "function") {
      toast("Central de Cadastros indisponível nesta página.", "erro");
      return;
    }
    var prefill = {};
    if (codigoPessoa) prefill.person_codigo = codigoPessoa;
    if (tipo === "somente_paciente") prefill.somente_paciente = true;
    else if (tipo) prefill.tipo = tipo;
    window.SoproCentral.open(tab, Object.keys(prefill).length ? prefill : null);
  }

  // ---------------------------------------------------------------- eventos

  function ligarAcoes(container) {
    container.querySelectorAll("[data-crm-ws-ir]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var destino = JSON.parse(btn.getAttribute("data-crm-ws-ir"));
        state.view = destino.view;
        if (destino.fila) state.filaAtiva = destino.fila;
        if (destino.resultado) state.filtroHistorico.resultado = destino.resultado;
        recarregar(container);
      });
    });

    container.querySelectorAll("[data-crm-ws-fila]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.filaAtiva = btn.getAttribute("data-crm-ws-fila");
        recarregar(container);
      });
    });

    var aplicar = container.querySelector("#crmWsAplicar");
    if (aplicar) {
      var aplicarFiltros = function () {
        state.filtroPacientes.q = container.querySelector("#crmWsBusca").value.trim();
        state.filtroPacientes.origem = container.querySelector("#crmWsOrigem").value;
        state.filtroPacientes.status_acompanhamento =
          container.querySelector("#crmWsStatus").value;
        recarregar(container);
      };
      aplicar.addEventListener("click", aplicarFiltros);
      container.querySelector("#crmWsBusca").addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); aplicarFiltros(); }
      });
    }
    var limparFila = container.querySelector("#crmWsLimparFila");
    if (limparFila) {
      limparFila.addEventListener("click", function () {
        state.filtroPacientes.fila = "";
        recarregar(container);
      });
    }

    var hAplicar = container.querySelector("#crmWsHAplicar");
    if (hAplicar) {
      hAplicar.addEventListener("click", function () {
        state.filtroHistorico.inicio = container.querySelector("#crmWsHIni").value;
        state.filtroHistorico.fim = container.querySelector("#crmWsHFim").value;
        state.filtroHistorico.resultado = container.querySelector("#crmWsHRes").value;
        state.filtroHistorico.canal = container.querySelector("#crmWsHCanal").value;
        state.filtroHistorico.operador = container.querySelector("#crmWsHOper").value;
        recarregar(container);
      });
    }

    var iAplicar = container.querySelector("#crmWsIAplicar");
    if (iAplicar) {
      iAplicar.addEventListener("click", function () {
        state.filtroIndicadores.meses = container.querySelector("#crmWsIMeses").value;
        state.filtroIndicadores.origem = container.querySelector("#crmWsIOrigem").value;
        recarregar(container);
      });
    }

    container.querySelectorAll("[data-crm-ws-wa-followup]").forEach(function (b) {
      b.addEventListener("click", function () {
        modalWhatsapp("followup", b.getAttribute("data-crm-ws-wa-followup"));
      });
    });
    container.querySelectorAll("[data-crm-ws-wa-pessoa]").forEach(function (b) {
      b.addEventListener("click", function () {
        modalWhatsapp("pessoa", b.getAttribute("data-crm-ws-wa-pessoa"));
      });
    });
    container.querySelectorAll("[data-crm-ws-resultado]").forEach(function (b) {
      b.addEventListener("click", function () {
        modalResultado(b.getAttribute("data-crm-ws-resultado"));
      });
    });
    container.querySelectorAll("[data-crm-ws-reagendar]").forEach(function (b) {
      b.addEventListener("click", function () {
        modalReagendar(b.getAttribute("data-crm-ws-reagendar"));
      });
    });
    container.querySelectorAll("[data-crm-ws-timeline]").forEach(function (b) {
      b.addEventListener("click", function () {
        modalTimeline(b.getAttribute("data-crm-ws-timeline"));
      });
    });
    container.querySelectorAll("[data-crm-ws-central]").forEach(function (b) {
      b.addEventListener("click", function () {
        abrirCentral(b.getAttribute("data-crm-ws-central"),
                     b.getAttribute("data-crm-ws-codigo"),
                     b.getAttribute("data-crm-ws-tipo"));
      });
    });
    container.querySelectorAll("[data-crm-copiar]").forEach(function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var valor = b.getAttribute("data-crm-copiar");
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(valor)
            .then(function () { toast("Código " + valor + " copiado."); })
            .catch(function () { toast("Não foi possível copiar.", "erro"); });
        }
      });
    });
  }

  // ------------------------------------------------------------------ API

  function abrir(container, view) {
    if (view) state.view = view;
    recarregar(container || containerEl());
  }

  var glob = typeof window !== "undefined" ? window : null;
  if (glob) glob.SoproCrm = {
    abrir: abrir,
    render: function (container) { abrir(container); },
    // exposto para teste em Node: funções puras, sem DOM nem rede
    _internals: { fmtDate: fmtDate, fmtMes: fmtMes, esc: esc, VIEWS: VIEWS, KPI_DEFS: KPI_DEFS },
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { fmtDate: fmtDate, fmtMes: fmtMes, esc: esc, VIEWS: VIEWS, KPI_DEFS: KPI_DEFS };
  }
})();
