/* Indicadores agregados. Modal independente: abrir/fechar não remonta nem
   perde o formulário clínico. Somente GET autenticado, sem armazenamento.

   M26.20 — paleta validada (ΔE CVD/visão normal e contraste conferidos por
   script, não a olho), barras de série única em UMA cor, rampa ordinal na
   faixa etária, rosca só para parte-do-todo, e o detalhamento das conclusões
   personalizadas. O servidor manda rótulo + critério + contagem; nenhuma
   redação clínica chega aqui. */
(function () {
  "use strict";
  let dialog, trigger, epoch = 0, sessionBound = false;
  let charts = [];
  // Paleta categórica validada (6 slots, ordem FIXA — nunca reciclada nem
  // reatribuída por tamanho da fatia). NEUTRAL é reservado para
  // "desconhecido / não classificável" e não conta como um 7º slot de
  // identidade. ORDINAL é a rampa de hue único para categorias ordenadas.
  const CATEGORICAL = ["#0d8d80", "#2f62a8", "#b5721a", "#9c4bc4", "#3f9ad1", "#c04f66"];
  const NEUTRAL = "#8c9bab";
  const ORDINAL = ["#5cbcaf", "#2ea396", "#14897d", "#0c6d63", "#07514a"];
  const PRIMARY = CATEGORICAL[0];
  const INK = "#1d3a56", MUTED = "#64788c", GRID = "#e9eff5", SURFACE = "#ffffff";
  // Chaves que representam ausência de informação: recebem o cinza reservado
  // em qualquer gráfico, para que "não informado" nunca se confunda com uma
  // categoria real.
  const UNKNOWN = new Set(["nao_informado", "nao_catalogado", "nao_classificavel",
    "Não informado", "Não registrado", "Não informada"]);
  const number = (value) => Number(value || 0).toLocaleString("pt-BR");
  const pct = (value) => `${Number(value || 0).toLocaleString("pt-BR", {maximumFractionDigits: 1})}%`;
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const api = () => window.SoproM15;
  const calm = () => Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function allowed() {
    const c = api(), user = c && c.getUser();
    return Boolean(c && c.hasToken() && user && Array.isArray(user.papeis)
      && (user.papeis.includes("admin") || user.papeis.includes("medico")));
  }
  function destroyCharts() { charts.forEach((c) => c.destroy()); charts = []; }
  function close() {
    epoch += 1;
    destroyCharts();
    if (dialog) { dialog.remove(); dialog = null; }
    if (trigger && trigger.isConnected) trigger.focus();
  }
  /* Um card por gráfico. `tall` reserva mais altura para a rosca, que precisa
     de espaço para a legenda sem comer o raio. O <details> da tabela mora
     DENTRO do card e rola dentro dele: nunca transborda para o vizinho. */
  function card(id, title, subtitle, options) {
    const opts = options || {};
    return `<article class="rs-chart-card${opts.tall ? " rs-chart-card--tall" : ""}${opts.span ? " rs-chart-card--span" : ""}">
      <header><h3>${title}</h3><p>${subtitle}</p></header>
      <div class="rs-chart"><canvas id="${id}" aria-label="${title}" role="img"></canvas></div>
      <details class="rs-data"><summary>${esc(opts.summary || "Ver dados do gráfico")}</summary><div class="rs-table-wrap" id="${id}-data"></div></details></article>`;
  }
  /* Cores de uma série. Fatia/barra de identidade recebe a paleta na ordem
     declarada; chave de ausência recebe o cinza reservado. Série única de
     barras recebe UMA cor (slot 1): colorir cada barra de um tom diferente
     só repetiria em hue o que o comprimento já diz. */
  function paint(rows, kind) {
    if (kind === "single") return PRIMARY;
    if (kind === "ordinal") {
      let step = 0;
      return rows.map((s) => UNKNOWN.has(s.chave) || UNKNOWN.has(s.rotulo)
        ? NEUTRAL : ORDINAL[Math.min(step++, ORDINAL.length - 1)]);
    }
    let slot = 0;
    return rows.map((s) => UNKNOWN.has(s.chave) || UNKNOWN.has(s.rotulo)
      ? NEUTRAL : CATEGORICAL[slot++ % CATEGORICAL.length]);
  }
  function tableRows(rows, total) {
    return rows.map((s) => `<tr><th scope="row">${esc(s.rotulo)}</th><td>${number(s.quantidade)}</td><td>${total ? pct(s.quantidade * 100 / total) : "—"}</td></tr>`).join("");
  }
  /* Tooltip comum: fundo navy, sem quadradinho de cor duplicando a legenda,
     e disponível ao toque (Chart.js trata touchstart como hover, mas só se
     o evento estiver declarado). */
  function tooltipStyle(extra) {
    return Object.assign({
      backgroundColor: "rgba(12,31,61,.95)", titleColor: "#fff", bodyColor: "#dfe9f2",
      padding: {top: 11, right: 14, bottom: 11, left: 14}, cornerRadius: 10,
      displayColors: false, titleFont: {size: 13, weight: "700"},
      bodyFont: {size: 12, weight: "400"}, bodySpacing: 5, caretPadding: 8,
      boxPadding: 4,
    }, extra || {});
  }
  function plot(id, series, type, config) {
    const cfg = config || {};
    const canvas = dialog.querySelector(`#${id}`);
    const data = series.filter((s) => s.quantidade > 0);
    const rows = type === "line" ? series : data;
    const total = series.reduce((sum, s) => sum + Number(s.quantidade || 0), 0);
    const tableHost = dialog.querySelector(`#${id}-data`);
    const showPct = type !== "line";
    // `keepTable`: o card já montou uma tabela mais rica (caso do
    // detalhamento, que tem percentual e critério). Sem esta guarda o plot
    // sobrescrevia aquela tabela pela genérica de duas colunas.
    if (!cfg.keepTable) tableHost.innerHTML = `<table><caption>${esc(canvas.getAttribute("aria-label"))}</caption><thead><tr><th scope="col">Categoria</th><th scope="col">Laudos</th>${showPct ? '<th scope="col">%</th>' : ""}</tr></thead><tbody>${
      rows.map((s) => `<tr><th scope="row">${esc(s.rotulo)}</th><td>${number(s.quantidade)}</td>${showPct ? `<td>${total ? pct(s.quantidade * 100 / total) : "—"}</td>` : ""}</tr>`).join("")
    }</tbody></table>`;
    if (!data.length) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Nenhum registro disponível neste recorte.</p>';
      return;
    }
    if (!window.Chart) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Visualização indisponível. Consulte os valores abaixo.</p>';
      tableHost.parentElement.open = true;
      return;
    }
    const horizontal = type === "bar";
    const colors = paint(rows, cfg.paint || (type === "line" ? "single" : type === "doughnut" ? "categorical" : "single"));
    const dataset = {label: "Laudos", data: rows.map((s) => s.quantidade),
      backgroundColor: type === "line" ? "rgba(13,141,128,.12)" : colors,
      borderColor: type === "line" ? PRIMARY : SURFACE,
      // Anel de 2px na cor da superfície separa as fatias sem desenhar borda
      // decorativa em volta de cada marca.
      borderWidth: type === "doughnut" ? 2 : type === "line" ? 2 : 0,
      hoverBorderColor: SURFACE, hoverBorderWidth: type === "doughnut" ? 2 : 0,
      hoverOffset: type === "doughnut" ? 8 : 0,
      borderRadius: horizontal ? 4 : 0, maxBarThickness: 26, fill: type === "line",
      pointRadius: type === "line" ? 4 : 0, pointHoverRadius: type === "line" ? 6 : 0,
      pointBackgroundColor: PRIMARY, pointBorderColor: SURFACE, pointBorderWidth: 2,
      tension: 0};
    charts.push(new Chart(canvas, {type,
      data: {labels: rows.map((s) => s.rotulo), datasets: [dataset]},
      options: {responsive: true, maintainAspectRatio: false,
        animation: calm() ? false : {duration: 420, easing: "easeOutQuart"},
        // Toque precisa estar na lista ou o tooltip só existe com mouse.
        events: ["mousemove", "mouseout", "click", "touchstart", "touchmove"],
        interaction: {mode: type === "line" ? "index" : "nearest", intersect: type !== "line"},
        layout: {padding: {top: 2, bottom: 0}},
        indexAxis: horizontal ? "y" : "x", cutout: cfg.cutout || "66%",
        plugins: {legend: cfg.legend !== undefined ? cfg.legend
            : {display: type === "doughnut", position: "bottom",
               onClick: (event, item, legend) => legendShowsTooltip(legend.chart, item.index),
               labels: {color: MUTED, font: {size: 11.5}, boxWidth: 9, boxHeight: 9,
                        padding: 15, usePointStyle: true, pointStyle: "circle"}},
          tooltip: tooltipStyle(cfg.tooltip || {callbacks: {
            label: (ctx) => {
              const value = Number(ctx.raw || 0);
              const share = total ? ` · ${pct(value * 100 / total)}` : "";
              return `${number(value)} laudo(s)${type === "line" ? "" : share}`;
            },
          }})},
        scales: type === "doughnut" ? {} : {
          x: horizontal
            ? {beginAtZero: true, ticks: {precision: 0, color: MUTED, font: {size: 11}}, border: {display: false}, grid: {color: GRID, drawTicks: false}}
            : {grid: {display: false}, border: {color: GRID}, ticks: {color: MUTED, font: {size: 11}, maxRotation: 0, maxTicksLimit: 8}},
          y: horizontal
            ? {grid: {display: false}, border: {display: false}, ticks: {color: INK, font: {size: 11.5}, autoSkip: false}}
            : {beginAtZero: true, ticks: {precision: 0, color: MUTED, font: {size: 11}}, border: {display: false}, grid: {color: GRID, drawTicks: false}}},
        ...cfg.options}}));
  }
  /* O tooltip do Chart.js desenha no canvas e NÃO quebra linha sozinho: uma
     descrição de 120 caracteres sairia como uma régua saindo do card. Cada
     elemento do array vira uma linha, então a quebra é feita aqui. */
  function wrapLines(text, width) {
    const lines = [];
    let line = "";
    for (const word of String(text || "").split(/\s+/)) {
      if (!word) continue;
      if (line && (line.length + 1 + word.length) > width) { lines.push(line); line = word; }
      else line = line ? `${line} ${word}` : word;
    }
    if (line) lines.push(line);
    return lines;
  }
  /* Toque na legenda mostra a MESMA informação da fatia, em vez do
     comportamento padrão do Chart.js (ocultar a série) — no celular a
     legenda é o alvo fácil, e esconder dado ao tocar seria o contrário do
     pedido. */
  function legendShowsTooltip(chart, index) {
    const meta = chart.getDatasetMeta(0);
    if (!meta || !meta.data[index]) return;
    chart.setActiveElements([{datasetIndex: 0, index}]);
    chart.tooltip.setActiveElements([{datasetIndex: 0, index}], {x: 0, y: 0});
    chart.update("none");
  }
  /* Rosca do detalhamento: cada fatia nomeia a categoria, a quantidade, o
     percentual e a DESCRIÇÃO do que está sendo contado — a descrição vem do
     servidor junto da categoria, nunca é montada aqui. */
  function plotCustom(detail) {
    const rows = detail.categorias.filter((c) => c.quantidade > 0);
    const byLabel = new Map(detail.categorias.map((c) => [c.rotulo, c]));
    const ignored = Array.isArray(detail.ignorado_na_classificacao) ? detail.ignorado_na_classificacao : [];
    const tableHost = dialog.querySelector('#rsCustom-data');
    tableHost.innerHTML = `<table><caption>Categorias do detalhamento, critério e volume</caption>
      <thead><tr><th scope="col">Categoria</th><th scope="col">Laudos</th><th scope="col">%</th><th scope="col">Critério de agrupamento</th></tr></thead>
      <tbody>${detail.categorias.map((c) => `<tr><th scope="row">${esc(c.rotulo)}</th><td>${number(c.quantidade)}</td><td>${pct(c.percentual)}</td><td class="rs-criterion">${esc(c.criterio)}</td></tr>`).join("")}</tbody>
      <tfoot><tr><th scope="row">Total de laudos personalizados</th><td>${number(detail.total)}</td><td>${detail.total ? "100%" : "—"}</td><td class="rs-criterion">Mesmo período e origem dos demais gráficos.</td></tr></tfoot></table>
      ${ignored.length ? `<p class="rs-criterion-note">Não entra na classificação: ${ignored.map(esc).join(" · ")}.</p>` : ""}`;
    const canvas = dialog.querySelector('#rsCustom');
    if (!detail.total) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Nenhum laudo com conclusão personalizada neste recorte.</p>';
      return;
    }
    if (!window.Chart) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Visualização indisponível. Consulte a tabela abaixo.</p>';
      tableHost.parentElement.open = true;
      return;
    }
    plot('rsCustom', rows, 'doughnut', {
      paint: 'categorical', cutout: '62%', keepTable: true,
      legend: {display: true, position: "bottom",
        onClick: (event, item, legend) => legendShowsTooltip(legend.chart, item.index),
        labels: {color: MUTED, font: {size: 11.5}, boxWidth: 9, boxHeight: 9,
                 padding: 14, usePointStyle: true, pointStyle: "circle"}},
      tooltip: {callbacks: {
        title: (items) => items[0].label,
        label: (ctx) => {
          const row = byLabel.get(ctx.label) || {};
          return `${number(ctx.raw)} laudo(s) · ${pct(row.percentual)}`;
        },
        // A descrição explica O QUE está sendo contado. Vem pronta do
        // servidor; o navegador não compõe frase clínica nenhuma.
        afterBody: (items) => {
          const row = byLabel.get(items[0].label);
          return row ? wrapLines(row.descricao, 44) : "";
        },
      }, bodyFont: {size: 12}},
    });
  }
  function present(data) {
    destroyCharts();
    const t = data.totais, total = t.laudos;
    const detail = data.personalizados;
    dialog.querySelector('#rsScope').textContent = data.escopo === "meus_laudos" ? "Seus laudos atribuídos" : "Visão institucional";
    const normal = t.com_conclusao ? `${Math.round(t.normais / t.com_conclusao * 100)}%` : "—";
    const hasDates = data.filtros.inicio || data.filtros.fim;
    const dateLabel = (s) => s ? s.split('-').reverse().join('/') : "sem limite";
    dialog.querySelector('#rsPeriod').textContent = hasDates ? `${dateLabel(data.filtros.inicio)} a ${dateLabel(data.filtros.fim)}` : "Todo o período registrado";
    const source = dialog.querySelector('#rsOrigin');
    source.innerHTML = '<option value="">Todas as origens</option>' + data.opcoes_origem.map((s) => `<option value="${esc(s.chave)}"${s.chave === data.filtros.origem ? " selected" : ""}>${esc(s.rotulo)}</option>`).join('');
    const customCard = detail ? card('rsCustom', 'Detalhamento dos laudos personalizados',
      `${number(detail.total)} laudo(s) com conclusão escrita pela médica · agrupados pelo que o texto registra explicitamente`,
      {tall: true, summary: 'Ver conclusões'}) : '';
    dialog.querySelector('#rsResults').innerHTML = `
      <div class="rs-kpis">
        <article class="rs-kpi--accent"><span>Laudos vigentes</span><strong>${number(total)}</strong><small>No período e origem selecionados</small></article>
        <article><span>Com conclusão publicada</span><strong>${number(t.com_conclusao)}</strong><small>Resultado registrado pela médica</small></article>
        <article><span>Classificados como normal</span><strong>${normal}</strong><small>${number(t.normais)} de ${number(t.com_conclusao)} com conclusão</small></article>
        <article><span>Sem conclusão publicada</span><strong>${number(t.sem_conclusao_publicada)}</strong><small>Fora dos gráficos de resultados</small></article>
      </div>
      ${total ? `<div class="rs-grid">
        ${card('rsEvolution', 'Evolução dos laudos', 'Por mês de realização do exame', {span: true})}
        ${card('rsResultsChart', 'Resultados registrados', 'Classificação selecionada no laudo publicado')}
        ${customCard}
        ${card('rsBD', 'Resposta ao broncodilatador', 'Complemento registrado nos laudos com conclusão')}
        ${card('rsAge', 'Faixa etária', 'Idade na data do exame · por laudo')}
        ${card('rsSex', 'Sexo cadastrado', 'Distribuição por laudo vigente')}
        ${card('rsOrigins', 'Origem dos exames', 'Local de atendimento registrado no laudo')}
      </div>
      <details class="rs-breakdown"><summary>Conclusões por classificação e grau</summary><div class="rs-table-wrap"><table><thead><tr><th scope="col">Conclusão registrada</th><th scope="col">Laudos</th><th scope="col">%</th></tr></thead><tbody>${tableRows(data.conclusoes.filter((s) => s.quantidade), t.com_conclusao)}</tbody></table></div></details>` : '<div class="rs-no-results"><h3>Nenhum laudo neste recorte</h3><p>Altere o período ou a origem para consultar outros registros.</p></div>'}
      <p class="rs-method">Cada laudo vigente é contado uma vez. Correções substituídas e cadastros arquivados ficam fora desta visão. Resultados usam a classificação escolhida no laudo publicado; textos personalizados são agrupados somente por correspondência explícita do que a médica escreveu, nunca por interpretação clínica nova — o que não corresponde fica em "Não classificável com segurança". ${hasDates ? 'O filtro de datas considera apenas exames com data completa.' : `${number(t.sem_data_completa)} laudo(s) sem data completa ficam fora da evolução mensal e da idade na data do exame.`}</p>`;
    if (total) {
      plot('rsEvolution', data.evolucao.map((s) => ({chave: s.mes, rotulo: `${s.mes.slice(5)}/${s.mes.slice(0,4)}`, quantidade: s.quantidade})), 'line');
      plot('rsResultsChart', data.resultados, 'bar');
      if (detail) plotCustom(detail);
      plot('rsBD', data.broncodilatador, 'bar');
      plot('rsAge', data.faixa_etaria, 'bar', {paint: 'ordinal'});
      plot('rsSex', data.sexo, 'doughnut');
      plot('rsOrigins', data.origens, 'doughnut');
    }
    dialog.querySelector('#rsMessage').textContent = `${number(total)} laudo(s) encontrados.`;
  }
  async function load() {
    if (!dialog || !allowed()) { close(); return; }
    const start = dialog.querySelector('#rsStart').value, end = dialog.querySelector('#rsEnd').value;
    const message = dialog.querySelector('#rsMessage');
    if (start && end && start > end) { message.textContent = 'A data inicial deve anteceder a data final.'; dialog.querySelector('#rsStart').focus(); return; }
    const ticket = ++epoch;
    const params = new URLSearchParams();
    if (start) params.set('inicio', start);
    if (end) params.set('fim', end);
    const origin = dialog.querySelector('#rsOrigin').value;
    if (origin) params.set('origem', origin);
    destroyCharts();
    dialog.querySelector('#rsResults').innerHTML = '<div class="rs-loading">Carregando indicadores dos laudos…</div>';
    dialog.querySelector('#rsResults').setAttribute('aria-busy','true');
    message.textContent = 'Consultando os registros…';
    const submit = dialog.querySelector('[type="submit"]'); submit.disabled = true;
    try {
      const data = await api().api(`/laudos/estatisticas${params.size ? '?' + params : ''}`);
      if (ticket !== epoch || !dialog || !allowed()) return;
      present(data);
    } catch (error) {
      if (ticket !== epoch || !dialog) return;
      message.textContent = 'Não foi possível carregar os indicadores.';
      dialog.querySelector('#rsResults').innerHTML = '<div class="rs-no-results"><h3>Os gráficos não carregaram</h3><p>Tente atualizar novamente. Os laudos continuam disponíveis na área de trabalho.</p><button type="button" data-rs-retry> Tentar novamente </button></div>';
    } finally {
      if (ticket === epoch && dialog) {
        submit.disabled = false;
        dialog.querySelector('#rsResults').setAttribute('aria-busy','false');
      }
    }
  }
  function open(button) {
    if (!allowed()) return;
    if (dialog) { dialog.focus(); return; }
    trigger = button;
    if (!sessionBound) { api().onSessionChange(close); sessionBound = true; }
    dialog = document.createElement('dialog'); dialog.className = 'report-statistics';
    dialog.setAttribute('aria-labelledby','rsTitle');
    dialog.innerHTML = `<div class="rs-top"><div class="rs-top-text"><p class="rs-eyebrow">ESPIROMETRIA · INDICADORES</p><h2 id="rsTitle">Gráficos dos laudos</h2><p>Uma visão dos resultados e da produção da SoproLife.</p></div><button type="button" class="rs-close" data-rs-close aria-label="Fechar gráficos e voltar aos laudos"><span class="rs-close-text">Fechar</span> <span aria-hidden="true">×</span></button></div>
      <div class="rs-body"><div class="rs-context"><span id="rsScope">Consultando…</span><span id="rsPeriod">Todo o período registrado</span></div>
      <form class="rs-filters"><label>Data inicial<input id="rsStart" type="date"></label><label>Data final<input id="rsEnd" type="date"></label><label>Origem<select id="rsOrigin"><option value="">Todas as origens</option></select></label><div class="rs-filter-actions"><button type="submit">Aplicar filtros</button><button type="button" data-rs-clear>Limpar</button></div></form>
      <p id="rsMessage" class="rs-message" role="status" aria-live="polite"></p><div id="rsResults"></div></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); close(); });
    dialog.addEventListener('submit', (e) => { e.preventDefault(); load(); });
    dialog.addEventListener('click', (e) => {
      if (e.target.closest('[data-rs-close]')) close();
      else if (e.target.closest('[data-rs-retry]')) load();
      else if (e.target.closest('[data-rs-clear]')) { dialog.querySelector('form').reset(); dialog.querySelector('#rsOrigin').value=''; load(); }
    });
    dialog.showModal();
    load();
  }
  window.SoproReportStatistics = {open, close};
})();
