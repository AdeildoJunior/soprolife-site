/* M26.9 — repasses médicos por competência, separados de receitas/Pastore. */
(function () {
  "use strict";

  const ROOT = "medicalTransfersRoot";
  let data = null;
  let currentMonth = null; // O mês atual vem do servidor, no fuso do painel.
  let loadSequence = 0;
  let helpBubble = null;
  let helpAnchor = null;
  let helpTimer = null;
  // M26.13 — produção por médica: cartões + donut, sob demanda (um clique
  // por médica), com cache por competência para não refazer a chamada ao
  // trocar de aba e voltar. Mesma paleta de `app.js` (`CHART_COLORS`) — os
  // dois módulos são closures separadas e não compartilham `const`.
  const CHART_COLORS = [
    "rgba(29, 183, 166, .92)", "rgba(99, 102, 241, .92)", "rgba(245, 158, 11, .92)",
    "rgba(239, 68, 68, .90)", "rgba(16, 185, 129, .90)", "rgba(37, 99, 235, .90)",
  ];
  const GROUP_LABELS = {
    normal: "Normal", obstrutivo: "Obstrutivo", restritivo: "Restritivo",
    misto: "Misto", inespecifico: "Inespecífico", personalizado: "Personalizado",
  };
  let productionCache = {};
  let productionChart = null;
  let openProductionFor = null;
  const MONTHS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
  const HELP = {
    competencia: "Mês em que a médica concluiu o laudo. Assinatura e entrega posteriores não mudam a competência.",
    laudos: "Laudos concluídos pela médica nesta competência, contados uma única vez. No fechamento, a quantidade usada no cálculo fica registrada.",
    unitario: "Valor por laudo informado manualmente no fechamento da competência. Não há valor padrão.",
    referencia: "Total de referência do fechamento: quantidade de laudos × valor unitário. Não é uma observação nem confirmação de pagamento.",
    pago: "Valor efetivamente pago e registrado para a médica nesta competência.",
    pagamento: "Data do pagamento informada ao registrar o valor pago. Não altera a competência dos laudos.",
    status: "Pendente: pagamento ainda não registrado. Pago: valor e data de pagamento registrados.",
    total: "Soma dos totais de referência dos repasses registrados nesta competência, inclusive os já pagos. Não representa saldo restante. Repasses sem valor definido ainda não entram nessa soma.",
    totalPago: "Soma dos valores efetivamente pagos e registrados na competência selecionada.",
  };

  function monthLabel(key, short) {
    const [year, month] = key.split("-").map(Number);
    return `${short ? MONTHS[month - 1].slice(0, 3) : MONTHS[month - 1]} ${year}`;
  }
  function shiftMonth(key, offset) {
    const [year, month] = key.split("-").map(Number);
    const index = year * 12 + month - 1 + offset;
    return `${String(Math.floor(index / 12)).padStart(4, "0")}-${String(index % 12 + 1).padStart(2, "0")}`;
  }
  function help(label, key) {
    return `<span class="medical-help-label">${esc(label)}<button type="button" class="medical-help" aria-label="Ajuda: ${esc(label)}" data-medical-help="${esc(HELP[key])}">?</button></span>`;
  }
  function hideHelp() {
    clearTimeout(helpTimer);
    if (helpAnchor) helpAnchor.removeAttribute("aria-describedby");
    if (helpBubble) helpBubble.hidden = true;
    helpAnchor = null;
  }
  function showHelp(anchor) {
    hideHelp();
    if (!helpBubble) {
      helpBubble = document.createElement("div");
      helpBubble.id = "medicalTransfersHelp";
      helpBubble.className = "medical-tooltip";
      helpBubble.setAttribute("role", "tooltip");
      document.body.appendChild(helpBubble);
    }
    helpAnchor = anchor;
    helpBubble.textContent = anchor.dataset.medicalHelp;
    helpBubble.hidden = false;
    anchor.setAttribute("aria-describedby", helpBubble.id);
    const rect = anchor.getBoundingClientRect();
    const box = helpBubble.getBoundingClientRect();
    helpBubble.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - box.width - 12))}px`;
    helpBubble.style.top = `${Math.max(12, rect.bottom + box.height + 12 < window.innerHeight ? rect.bottom + 8 : rect.top - box.height - 8)}px`;
  }
  document.addEventListener("pointerover", (event) => {
    if (helpBubble && helpBubble.contains(event.target)) { clearTimeout(helpTimer); return; }
    const anchor = event.target.closest && event.target.closest("[data-medical-help]");
    if (anchor) showHelp(anchor);
  });
  document.addEventListener("pointerout", (event) => {
    if ((event.target.closest && event.target.closest("[data-medical-help]")) || event.target === helpBubble) {
      helpTimer = setTimeout(hideHelp, 150);
    }
  });
  document.addEventListener("focusin", (event) => {
    if (event.target.matches("[data-medical-help]")) showHelp(event.target);
    else hideHelp();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") hideHelp(); });
  document.addEventListener("click", (event) => {
    const anchor = event.target.closest && event.target.closest("[data-medical-help]");
    if (anchor) showHelp(anchor);
    else if (!helpBubble || !helpBubble.contains(event.target)) hideHelp();
  });
  window.addEventListener("resize", hideHelp);
  document.addEventListener("scroll", () => {
    if (!helpAnchor) return;
    const rect = helpAnchor.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) hideHelp();
    else showHelp(helpAnchor);
  }, true);
  function monthPicker(selected) {
    const recent = Array.from({ length: 6 }, (_, i) => shiftMonth(currentMonth, -i));
    return `<div class="medical-month-picker">
      <div class="medical-month-toolbar"><div>
        <div class="medical-eyebrow">${help("Competência", "competencia")}</div>
        <div class="medical-month-navigation">
          <button type="button" class="medical-icon-btn" data-medical-month="${shiftMonth(selected, -1)}" aria-label="Mês anterior" ${selected === "0001-01" ? "disabled" : ""}>‹</button>
          <h4 tabindex="-1" data-medical-selected>${monthLabel(selected)}</h4>
          <button type="button" class="medical-icon-btn" data-medical-month="${shiftMonth(selected, 1)}" aria-label="Próximo mês" ${selected === "9999-12" ? "disabled" : ""}>›</button>
        </div>
      </div><div class="medical-month-actions">
        <button type="button" class="m15-btn m15-btn-sec" data-medical-current>Mês atual</button>
        <button type="button" class="m15-btn m15-btn-sec" data-medical-refresh>Atualizar</button>
      </div></div>
      <div class="medical-month-chips" role="group" aria-label="Competências recentes">
        ${recent.map((month) => `<button type="button" data-medical-month="${month}" aria-label="${monthLabel(month)}" aria-pressed="${month === selected}">${monthLabel(month, true)}</button>`).join("")}
      </div></div>`;
  }

  function m15() { return window.SoproM15 || null; }
  function api(path, options) { return m15().api(path, options); }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function brl(value) {
    if (value == null || value === "") return "A definir";
    const n = Number(value);
    return Number.isFinite(n)
      ? n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" }) : "—";
  }
  function parseMoney(value, allowZero) {
    let raw = String(value || "").trim().replace(/^R\$\s*/, "");
    if (!raw && allowZero) return "0.00";
    if (raw.includes(",")) raw = raw.replace(/\./g, "").replace(",", ".");
    const n = Number(raw);
    return Number.isFinite(n) && (allowZero ? n >= 0 : n > 0) ? n.toFixed(2) : "";
  }
  function fmtDate(value) {
    if (!value) return "—";
    const p = value.split("-");
    return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : value;
  }
  function status(message, error) {
    const el = document.querySelector(`#${ROOT} .medical-transfers-form-status`);
    if (!el) return;
    el.textContent = message || "";
    el.className = "medical-transfers-form-status" + (error ? " is-error" : "");
  }

  function rowHtml(row, canManage) {
    const open = openProductionFor === row.physician_profile_id;
    return `<tr>
      <td class="medical-doctor">${esc(row.medica)}</td>
      <td class="num">${esc(row.quantidade_laudos_elegiveis)}</td>
      <td class="num">${brl(row.valor_unitario)}</td>
      <td class="num">${brl(row.total_calculado)}</td>
      <td class="num">${brl(row.valor_pago)}</td>
      <td>${fmtDate(row.data_pagamento)}</td>
      <td><span class="medical-transfer-status ${row.status === "Pago" ? "is-paid" : "is-pending"}">${esc(row.status)}</span></td>
      <td class="medical-actions-cell">
        <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" aria-expanded="${open}"
          data-medical-production-toggle="${esc(row.physician_profile_id)}">${
            open ? "Ocultar produção" : "Ver produção"
          }</button>
        ${canManage && !row.id
          ? `<button type="button" class="m15-btn m15-btn-sec cad-btn-mini" data-medical-create="${esc(row.physician_profile_id)}">Fechar</button>`
          : canManage && row.id && row.status !== "Pago"
            ? `<button type="button" class="m15-btn m15-btn-sec cad-btn-mini" data-medical-pay="${esc(row.id)}">Registrar pagamento</button>`
            : ""}
      </td>
    </tr>`;
  }

  // M26.13 — cartões resumidos + donut por grupo de conclusão. O grupo
  // (normal/obstrutivo/restritivo/misto/inespecifico/personalizado) vem do
  // catálogo FECHADO de `report_conclusions.py` — nunca inferido aqui.
  //
  // O painel mora FORA da tabela (mesmo padrão de `.medical-transfers-form-
  // slot`, usado por "Registrar repasse"/"Registrar pagamento" acima) — não
  // dentro de uma `<tr>`. A tabela tem scroll horizontal próprio
  // (`.medical-transfers-table-wrap`); um grid de cartões PRECISA da largura
  // real da viewport para refluir no celular, e dentro da `<tr>` ele herdava
  // a largura INTEIRA da tabela destravada, sem nunca poder encolher.
  function productionHtml(payload) {
    const groups = {};
    (payload.distribuicao_conclusao || []).forEach((item) => {
      groups[item.grupo] = (groups[item.grupo] || 0) + item.quantidade;
    });
    const entries = Object.entries(groups);
    return `<div class="medical-production">
      <div class="medical-production-header">
        <h4>Produção de ${esc(payload.medica)} · ${esc(monthLabel(payload.competencia, true))}</h4>
        <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" data-medical-production-toggle="${esc(payload.physician_profile_id)}">Fechar</button>
      </div>
      <div class="medical-production-cards">
        <div class="medical-production-card"><strong>${esc(payload.efetivos)}</strong><span>Laudos efetivos</span></div>
        <div class="medical-production-card"><strong>${esc(payload.corrigidos)}</strong><span>Corrigidos</span></div>
        <div class="medical-production-card"><strong>${esc(payload.assinados)}</strong><span>Assinados</span></div>
        <div class="medical-production-card"><strong>${esc(payload.entregues)}</strong><span>Entregues</span></div>
        <div class="medical-production-card"><strong>${esc(payload.aguardando_assinatura)}</strong><span>Aguardando assinatura</span></div>
        <div class="medical-production-card"><strong>${esc(payload.pendentes)}</strong><span>Pendentes agora</span></div>
      </div>
      ${entries.length ? `
        <div class="medical-production-chart-row">
          <div class="medical-production-chart-wrap">
            <canvas id="medicalProductionChart" role="img" aria-label="Distribuição por grupo de conclusão"></canvas>
          </div>
          <ul class="medical-production-legend">
            ${entries.map(([group, count], i) => `<li><span class="medical-legend-dot" style="background:${
              CHART_COLORS[i % CHART_COLORS.length]
            }"></span>${esc(GROUP_LABELS[group] || group)} · ${count}</li>`).join("")}
          </ul>
        </div>` : `<p class="medical-transfers-note">Sem conclusões liberadas nesta competência.</p>`}
      <p class="medical-transfers-note">"Pendentes agora" é a bancada atual da médica — não é filtrado pela competência selecionada, porque um laudo ainda não concluído não tem data de conclusão para filtrar.</p>
    </div>`;
  }

  function destroyProductionChart() {
    if (productionChart) { productionChart.destroy(); productionChart = null; }
  }

  function renderProduction(slot, payload) {
    slot.innerHTML = productionHtml(payload);
    destroyProductionChart();
    const canvas = slot.querySelector("#medicalProductionChart");
    if (!canvas || typeof Chart === "undefined") return;
    const groups = {};
    (payload.distribuicao_conclusao || []).forEach((item) => {
      groups[item.grupo] = (groups[item.grupo] || 0) + item.quantidade;
    });
    const entries = Object.entries(groups);
    if (!entries.length) return;
    productionChart = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: entries.map(([group]) => GROUP_LABELS[group] || group),
        datasets: [{
          data: entries.map(([, count]) => count),
          backgroundColor: entries.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw}` } },
        },
      },
    });
  }

  function loadProduction(profileId) {
    const slot = document.querySelector(`#${ROOT} .medical-production-slot`);
    if (!slot || !data) return;
    const key = `${profileId}|${data.competencia}`;
    if (productionCache[key]) { renderProduction(slot, productionCache[key]); return; }
    slot.innerHTML = '<p class="medical-transfers-note">Carregando produção…</p>';
    api(`/financeiro/repasses-medicos/${encodeURIComponent(profileId)}/producao?competencia=${encodeURIComponent(data.competencia)}`)
      .then((payload) => {
        productionCache[key] = payload;
        if (openProductionFor === profileId) renderProduction(slot, payload);
      })
      .catch((err) => {
        slot.innerHTML = `<p class="medical-transfers-note is-error" role="alert">${esc(err.message || err)}</p>`;
      });
  }

  function historyHtml(rows) {
    if (!rows.length) return '<p class="medical-transfers-note">Nenhuma competência registrada.</p>';
    return `<div class="medical-transfers-table-wrap" role="region" aria-label="Histórico de repasses" tabindex="0"><table>
      <thead><tr><th scope="col">${help("Competência", "competencia")}</th><th scope="col">Médica</th><th scope="col" class="num">${help("Laudos", "laudos")}</th><th scope="col" class="num">${help("Unitário", "unitario")}</th><th scope="col" class="num">${help("Referência", "referencia")}</th><th scope="col" class="num">${help("Pago", "pago")}</th><th scope="col">${help("Pagamento", "pagamento")}</th><th scope="col">${help("Status", "status")}</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td><button type="button" class="medical-history-month" data-medical-month="${esc(row.competencia)}">${esc(monthLabel(row.competencia, true))}</button></td><td>${esc(row.medica)}</td>
        <td class="num">${esc(row.quantidade_laudos_elegiveis)}</td><td class="num">${brl(row.valor_unitario)}</td>
        <td class="num">${brl(row.total_calculado)}</td><td class="num">${brl(row.valor_pago)}</td>
        <td>${fmtDate(row.data_pagamento)}</td><td><span class="medical-transfer-status ${row.status === "Pago" ? "is-paid" : "is-pending"}">${esc(row.status)}</span></td></tr>`).join("")}</tbody>
    </table></div>`;
  }

  function render(payload) {
    hideHelp();
    data = payload;
    const root = document.getElementById(ROOT);
    if (!root) return;
    const canManage = m15().can("gestor");
    const rows = payload.medicas || [];
    currentMonth = currentMonth || payload.competencia;
    root.innerHTML = `<article class="panel medical-transfers" aria-labelledby="medicalTransfersTitle">
      <header class="medical-transfers-header"><div>
        <h3 id="medicalTransfersTitle">Repasses médicos</h3>
        <p>Acompanhe os fechamentos e pagamentos por mês.</p>
      </div>${canManage ? '<button type="button" class="finance-novo-btn" data-medical-open>Registrar repasse</button>' : ""}</header>
      ${monthPicker(payload.competencia)}
      <div class="medical-transfers-kpis">
        <div class="medical-transfers-kpi">${help("Total a pagar", "total")}<strong>${brl(payload.total_a_pagar)}</strong><small>${payload.total_a_pagar_tem_valor_a_definir ? "+ repasses com valor a definir" : "Referência dos fechamentos"}</small></div>
        <div class="medical-transfers-kpi is-paid">${help("Total pago", "totalPago")}<strong>${brl(payload.total_pago)}</strong><small>Pagamentos registrados</small></div>
      </div>
      <div class="medical-transfers-form-slot"></div>
      <div class="medical-transfers-form-status" role="status"></div>
      <div class="medical-transfers-table-wrap" role="region" aria-label="Repasses por médica" tabindex="0"><table>
        <thead><tr><th scope="col">Médica</th><th scope="col" class="num">${help("Laudos", "laudos")}</th><th scope="col" class="num">${help("Unitário", "unitario")}</th><th scope="col" class="num">${help("Referência", "referencia")}</th><th scope="col" class="num">${help("Pago", "pago")}</th><th scope="col">${help("Pagamento", "pagamento")}</th><th scope="col">${help("Status", "status")}</th><th scope="col"><span class="medical-sr-only">Ações</span></th></tr></thead>
        <tbody>${rows.length ? rows.map((row) => rowHtml(row, canManage)).join("") : '<tr><td colspan="8" class="medical-empty">Nenhum laudo concluído nesta competência.<small>Escolha outro mês para consultar os repasses.</small></td></tr>'}</tbody>
      </table></div>
      <div class="medical-production-slot"></div>
      <details class="medical-transfers-history"><summary>Histórico de competências</summary>${historyHtml(payload.historico || [])}</details>
    </article>`;
    // M26.13 — o slot nasce vazio; se uma médica já estava com o painel
    // aberto antes da competência mudar, busca a produção do mês novo agora.
    if (openProductionFor) loadProduction(openProductionFor);
  }

  function openCreate(profileId) {
    const slot = document.querySelector(`#${ROOT} .medical-transfers-form-slot`);
    if (!slot || !data) return;
    const available = (data.medicas || []).filter((row) => !row.id);
    if (!available.length) { status("Todos os repasses desta competência já foram registrados.", true); return; }
    const selected = available.find((row) => row.physician_profile_id === profileId) || available[0];
    slot.innerHTML = `<form class="medical-transfers-form" data-medical-create-form>
      <h4>Registrar repasse</h4>
      <label>Médica<select name="physician_profile_id">${available.map((row) =>
        `<option value="${esc(row.physician_profile_id)}"${row === selected ? " selected" : ""}>${esc(row.medica)} · ${esc(row.quantidade_laudos_elegiveis)} laudo(s)</option>`).join("")}</select></label>
      <label>Valor unitário por laudo (R$)<input name="unit_amount" inputmode="decimal" required placeholder="Informe o valor"></label>
      <label>Valor efetivamente pago (R$)<input name="paid_amount" inputmode="decimal" placeholder="Deixe vazio se pendente"></label>
      <label>Data do pagamento<input name="payment_date" type="date"></label>
      <div class="medical-transfers-actions"><span data-medical-total></span><button type="submit" class="m15-btn">Registrar repasse</button><button type="button" class="m15-btn m15-btn-sec" data-medical-cancel>Cancelar</button></div>
    </form>`;
    updateTotal(slot.querySelector("form"));
  }

  function openPayment(transferId) {
    const slot = document.querySelector(`#${ROOT} .medical-transfers-form-slot`);
    const row = (data.medicas || []).find((item) => item.id === transferId);
    if (!slot || !row) return;
    slot.innerHTML = `<form class="medical-transfers-form" data-medical-payment-form="${esc(transferId)}">
      <h4>Registrar pagamento · ${esc(row.medica)} · ${esc(row.competencia)}</h4>
      <label>Valor de referência<input value="${esc(brl(row.total_calculado))}" disabled></label>
      <label>Valor efetivamente pago (R$)<input name="paid_amount" inputmode="decimal" required></label>
      <label>Data do pagamento<input name="payment_date" type="date" required></label>
      <div class="medical-transfers-actions"><button type="submit" class="m15-btn">Confirmar pagamento</button><button type="button" class="m15-btn m15-btn-sec" data-medical-cancel>Cancelar</button></div>
    </form>`;
  }

  function updateTotal(form) {
    if (!form || !data) return;
    const profileId = form.elements.physician_profile_id.value;
    const row = (data.medicas || []).find((item) => item.physician_profile_id === profileId);
    const unit = parseMoney(form.elements.unit_amount.value, false);
    const out = form.querySelector("[data-medical-total]");
    out.textContent = unit && row
      ? `${row.quantidade_laudos_elegiveis} × ${brl(unit)} = ${brl(Number(unit) * row.quantidade_laudos_elegiveis)}`
      : `${row ? row.quantidade_laudos_elegiveis : 0} laudo(s) · informe o valor unitário`;
  }

  function load(month, focusMonth) {
    const sequence = ++loadSequence;
    hideHelp();
    const root = document.getElementById(ROOT);
    if (!root) return Promise.resolve();
    if (!m15() || !m15().hasToken() || !m15().can("gestor")) {
      data = null; currentMonth = null; root.innerHTML = ""; root.removeAttribute("aria-busy");
      destroyProductionChart();
      openProductionFor = null;
      productionCache = {};
      return Promise.resolve();
    }
    if (!data) root.innerHTML = '<article class="panel medical-transfers">Carregando Repasses médicos…</article>';
    root.setAttribute("aria-busy", "true");
    const controls = Array.from(root.querySelectorAll("button, input, select"), (el) => [el, el.disabled]);
    controls.forEach(([el]) => { el.disabled = true; });
    status("Carregando competência…");
    const query = month ? `?competencia=${encodeURIComponent(month)}` : "";
    return api("/financeiro/repasses-medicos" + query).then((payload) => {
      if (sequence !== loadSequence) return;
      if (!month) currentMonth = payload.competencia;
      render(payload);
      if (focusMonth) root.querySelector("[data-medical-selected]").focus({ preventScroll: true });
    }).catch((err) => {
      if (sequence !== loadSequence) return;
      const message = `Não foi possível carregar ${month ? monthLabel(month) : "o mês atual"}. ${err.message || err}`;
      if (data) status(message + " A competência exibida foi mantida.", true);
      else root.innerHTML = `<article class="panel medical-transfers"><p role="alert">${esc(message)}</p><button type="button" class="m15-btn" data-medical-current>Tentar novamente</button></article>`;
    }).finally(() => {
      if (sequence !== loadSequence) return;
      root.removeAttribute("aria-busy");
      controls.forEach(([el, disabled]) => { el.disabled = disabled; });
    });
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest && event.target.closest("[data-medical-open],[data-medical-create],[data-medical-pay],[data-medical-refresh],[data-medical-cancel],[data-medical-month],[data-medical-current],[data-medical-production-toggle]");
    if (!target) return;
    if (target.hasAttribute("data-medical-production-toggle")) {
      const profileId = target.dataset.medicalProductionToggle;
      openProductionFor = openProductionFor === profileId ? null : profileId;
      render(data);
      return;
    }
    if (target.hasAttribute("data-medical-open")) openCreate();
    else if (target.hasAttribute("data-medical-create")) openCreate(target.dataset.medicalCreate);
    else if (target.hasAttribute("data-medical-pay")) openPayment(target.dataset.medicalPay);
    else if (target.hasAttribute("data-medical-refresh")) load(data && data.competencia, true);
    else if (target.hasAttribute("data-medical-month")) load(target.dataset.medicalMonth, true);
    else if (target.hasAttribute("data-medical-current")) load(null, true);
    else if (target.hasAttribute("data-medical-cancel")) target.closest(".medical-transfers-form-slot").innerHTML = "";
  });

  document.addEventListener("input", (event) => {
    const form = event.target.closest && event.target.closest("[data-medical-create-form]");
    if (form) updateTotal(form);
  });
  document.addEventListener("submit", (event) => {
    const create = event.target.closest && event.target.closest("[data-medical-create-form]");
    const payment = event.target.closest && event.target.closest("[data-medical-payment-form]");
    if (!create && !payment) return;
    event.preventDefault();
    const form = create || payment;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    if (create) {
      const profileId = form.elements.physician_profile_id.value;
      const row = (data.medicas || []).find((item) => item.physician_profile_id === profileId);
      const unit = parseMoney(form.elements.unit_amount.value, false);
      const paid = parseMoney(form.elements.paid_amount.value, true);
      const date = form.elements.payment_date.value || null;
      if (!unit || !row || ((Number(paid) > 0) !== Boolean(date))) {
        button.disabled = false;
        status("Informe valor unitário; valor pago e data devem ser preenchidos juntos.", true);
        return;
      }
      status("Registrando repasse…");
      api("/financeiro/repasses-medicos", { method: "POST", body: JSON.stringify({
        physician_profile_id: profileId,
        competencia: data.competencia,
        expected_eligible_report_count: row.quantidade_laudos_elegiveis,
        unit_amount: unit,
        paid_amount: paid,
        payment_date: date,
      }) }).then(render).catch((err) => { button.disabled = false; status(err.message || err, true); });
      return;
    }
    const paid = parseMoney(form.elements.paid_amount.value, false);
    if (!paid) { button.disabled = false; status("Informe o valor efetivamente pago.", true); return; }
    status("Registrando pagamento…");
    api(`/financeiro/repasses-medicos/${encodeURIComponent(payment.dataset.medicalPaymentForm)}/pagamento`, {
      method: "PATCH", body: JSON.stringify({ paid_amount: paid, payment_date: form.elements.payment_date.value }),
    }).then(render).catch((err) => { button.disabled = false; status(err.message || err, true); });
  });

  if (m15()) m15().onSessionChange(() => load());
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => load());
  else load();
  window.SoproMedicalTransfers = { refresh: load };
})();
