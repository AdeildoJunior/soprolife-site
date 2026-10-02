/* Indicadores agregados. Modal independente: abrir/fechar não remonta nem
   perde o formulário clínico. Somente GET autenticado, sem armazenamento. */
(function () {
  "use strict";
  let dialog, trigger, epoch = 0, sessionBound = false;
  let charts = [];
  const COLORS = ["#16a596", "#335b89", "#af81ca", "#df9b39", "#6d8ba8", "#b4c3d3", "#7b8796"];
  const number = (value) => Number(value || 0).toLocaleString("pt-BR");
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const api = () => window.SoproM15;
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
  function card(id, title, subtitle) {
    return `<article class="rs-chart-card"><header><h3>${title}</h3><p>${subtitle}</p></header>
      <div class="rs-chart"><canvas id="${id}" aria-label="${title}" role="img"></canvas></div>
      <details class="rs-data"><summary>Ver dados do gráfico</summary><div id="${id}-data"></div></details></article>`;
  }
  function plot(id, series, type, options) {
    const canvas = dialog.querySelector(`#${id}`);
    const data = series.filter((s) => s.quantidade > 0);
    const rows = type === "line" ? series : data;
    const table = dialog.querySelector(`#${id}-data`);
    table.innerHTML = `<table><caption>${esc(canvas.getAttribute("aria-label"))}</caption><thead><tr><th scope="col">Categoria</th><th scope="col">Laudos</th></tr></thead><tbody>${rows.map((s) => `<tr><th scope="row">${esc(s.rotulo)}</th><td>${number(s.quantidade)}</td></tr>`).join("")}</tbody></table>`;
    if (!data.length) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Nenhum registro disponível neste recorte.</p>';
      return;
    }
    if (!window.Chart) {
      canvas.parentElement.innerHTML = '<p class="rs-empty">Visualização indisponível. Consulte os valores abaixo.</p>';
      table.parentElement.open = true;
      return;
    }
    const horizontal = type === "bar";
    const dataset = {label:"Laudos", data:rows.map((s) => s.quantidade),
      backgroundColor:type === "line" ? "rgba(22,165,150,.10)" : COLORS,
      borderColor:type === "line" ? COLORS[0] : "#fff", borderWidth:type === "doughnut" ? 3 : 2,
      borderRadius:horizontal ? 5 : 0, maxBarThickness:28, fill:type === "line",
      pointRadius:type === "line" ? 3 : 0, tension:0};
    charts.push(new Chart(canvas, {type,
      data:{labels:rows.map((s) => s.rotulo), datasets:[dataset]},
      options:{responsive:true, maintainAspectRatio:false, animation:false,
        indexAxis:horizontal ? "y" : "x", cutout:"72%",
        plugins:{legend:{display:type === "doughnut", position:"bottom", labels:{font:{size:11}, boxWidth:10, boxHeight:10, padding:14, usePointStyle:true}},
          tooltip:{callbacks:{label:(ctx) => `${ctx.label}: ${number(ctx.raw)} laudo(s)`}}},
        scales:type === "doughnut" ? {} : {
          x:horizontal ? {beginAtZero:true, ticks:{precision:0}, border:{display:false}, grid:{color:"#edf1f6"}} : {grid:{display:false}, ticks:{maxRotation:0, maxTicksLimit:8}},
          y:horizontal ? {grid:{display:false}, border:{display:false}, ticks:{font:{size:11}}} : {beginAtZero:true, ticks:{precision:0}, grid:{color:"#edf1f6"}}},
        ...options}}));
  }
  function present(data) {
    destroyCharts();
    const t = data.totais, total = t.laudos;
    dialog.querySelector('#rsScope').textContent = data.escopo === "meus_laudos" ? "Seus laudos atribuídos" : "Visão institucional";
    const normal = t.com_conclusao ? `${Math.round(t.normais / t.com_conclusao * 100)}%` : "—";
    const hasDates = data.filtros.inicio || data.filtros.fim;
    const dateLabel = (s) => s ? s.split('-').reverse().join('/') : "sem limite";
    dialog.querySelector('#rsPeriod').textContent = hasDates ? `${dateLabel(data.filtros.inicio)} a ${dateLabel(data.filtros.fim)}` : "Todo o período registrado";
    const source = dialog.querySelector('#rsOrigin');
    source.innerHTML = '<option value="">Todas as origens</option>' + data.opcoes_origem.map((s) => `<option value="${esc(s.chave)}"${s.chave === data.filtros.origem ? " selected" : ""}>${esc(s.rotulo)}</option>`).join('');
    dialog.querySelector('#rsResults').innerHTML = `
      <div class="rs-kpis">
        <article><span>Laudos vigentes</span><strong>${number(total)}</strong><small>No período e origem selecionados</small></article>
        <article><span>Com conclusão publicada</span><strong>${number(t.com_conclusao)}</strong><small>Resultado registrado pela médica</small></article>
        <article><span>Classificados como normal</span><strong>${normal}</strong><small>${number(t.normais)} de ${number(t.com_conclusao)} com conclusão</small></article>
        <article><span>Sem conclusão publicada</span><strong>${number(t.sem_conclusao_publicada)}</strong><small>Fora dos gráficos de resultados</small></article>
      </div>
      ${total ? `<div class="rs-grid">
        ${card('rsEvolution', 'Evolução dos laudos', 'Por mês de realização do exame')}
        ${card('rsResultsChart', 'Resultados registrados', 'Classificação selecionada no laudo publicado')}
        ${card('rsBD', 'Resposta ao broncodilatador', 'Complemento registrado nos laudos com conclusão')}
        ${card('rsAge', 'Faixa etária', 'Idade na data do exame · por laudo')}
        ${card('rsSex', 'Sexo cadastrado', 'Distribuição por laudo vigente')}
        ${card('rsOrigins', 'Origem dos exames', 'Local de atendimento registrado no laudo')}
      </div>
      <details class="rs-breakdown"><summary>Conclusões por classificação e grau</summary><div class="rs-table-wrap"><table><thead><tr><th scope="col">Conclusão registrada</th><th scope="col">Laudos</th></tr></thead><tbody>${data.conclusoes.filter((s) => s.quantidade).map((s) => `<tr><th scope="row">${esc(s.rotulo)}</th><td>${number(s.quantidade)}</td></tr>`).join('')}</tbody></table></div></details>` : '<div class="rs-no-results"><h3>Nenhum laudo neste recorte</h3><p>Altere o período ou a origem para consultar outros registros.</p></div>'}
      <p class="rs-method">Cada laudo vigente é contado uma vez. Correções substituídas e cadastros arquivados ficam fora desta visão. Resultados usam a classificação escolhida no laudo publicado; textos personalizados não são reinterpretados. ${hasDates ? 'O filtro de datas considera apenas exames com data completa.' : `${number(t.sem_data_completa)} laudo(s) sem data completa ficam fora da evolução mensal e da idade na data do exame.`}</p>`;
    if (total) {
      plot('rsEvolution', data.evolucao.map((s) => ({rotulo:`${s.mes.slice(5)}/${s.mes.slice(0,4)}`,quantidade:s.quantidade})), 'line');
      plot('rsResultsChart', data.resultados, 'bar');
      plot('rsBD', data.broncodilatador, 'bar');
      plot('rsAge', data.faixa_etaria, 'bar');
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
    dialog.innerHTML = `<div class="rs-top"><div><p class="rs-eyebrow">ESPIROMETRIA · INDICADORES</p><h2 id="rsTitle">Gráficos dos laudos</h2><p>Uma visão dos resultados e da produção da SoproLife.</p></div><button type="button" class="rs-close" data-rs-close aria-label="Fechar gráficos e voltar aos laudos">Fechar <span aria-hidden="true">×</span></button></div>
      <div class="rs-body"><div class="rs-context"><span id="rsScope">Consultando…</span><span id="rsPeriod">Todo o período registrado</span></div>
      <form class="rs-filters"><label>Data inicial<input id="rsStart" type="date"></label><label>Data final<input id="rsEnd" type="date"></label><label>Origem<select id="rsOrigin"><option value="">Todas as origens</option></select></label><button type="submit">Aplicar filtros</button><button type="button" data-rs-clear>Limpar</button></form>
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
