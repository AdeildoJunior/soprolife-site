/* M26.9 — repasses médicos por competência, separados de receitas/Pastore. */
(function () {
  "use strict";

  const ROOT = "medicalTransfersRoot";
  let data = null;

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
    return `<tr>
      <td>${esc(row.medica)}</td>
      <td class="num">${esc(row.quantidade_laudos_elegiveis)}</td>
      <td class="num">${brl(row.valor_unitario)}</td>
      <td class="num">${brl(row.total_calculado)}</td>
      <td class="num">${brl(row.valor_pago)}</td>
      <td>${fmtDate(row.data_pagamento)}</td>
      <td><span class="medical-transfer-status ${row.status === "Pago" ? "is-paid" : "is-pending"}">${esc(row.status)}</span></td>
      <td>${canManage && !row.id
        ? `<button type="button" class="m15-btn m15-btn-sec cad-btn-mini" data-medical-create="${esc(row.physician_profile_id)}">Fechar</button>`
        : canManage && row.id && row.status !== "Pago"
          ? `<button type="button" class="m15-btn m15-btn-sec cad-btn-mini" data-medical-pay="${esc(row.id)}">Registrar pagamento</button>`
          : ""}</td>
    </tr>`;
  }

  function historyHtml(rows) {
    if (!rows.length) return '<p class="medical-transfers-note">Nenhuma competência registrada.</p>';
    return `<div class="medical-transfers-table-wrap"><table>
      <thead><tr><th>Competência</th><th>Médica</th><th class="num">Laudos</th><th class="num">Unitário</th><th class="num">Referência</th><th class="num">Pago</th><th>Data</th><th>Status</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${esc(row.competencia)}</td><td>${esc(row.medica)}</td>
        <td class="num">${esc(row.quantidade_laudos_elegiveis)}</td><td class="num">${brl(row.valor_unitario)}</td>
        <td class="num">${brl(row.total_calculado)}</td><td class="num">${brl(row.valor_pago)}</td>
        <td>${fmtDate(row.data_pagamento)}</td><td>${esc(row.status)}</td></tr>`).join("")}</tbody>
    </table></div>`;
  }

  function render(payload) {
    data = payload;
    const root = document.getElementById(ROOT);
    if (!root) return;
    const canManage = m15().can("gestor");
    const rows = payload.medicas || [];
    const totalDue = brl(payload.total_a_pagar) +
      (payload.total_a_pagar_tem_valor_a_definir ? " + valor a definir" : "");
    root.innerHTML = `<article class="panel medical-transfers" aria-labelledby="medicalTransfersTitle">
      <header class="medical-transfers-header"><div>
        <h3 id="medicalTransfersTitle">Repasses médicos</h3>
        <p>Laudos concluídos por <code>released_at</code>. Assinatura e entrega não alteram a competência.</p>
      </div>${canManage ? '<button type="button" class="finance-novo-btn" data-medical-open>Registrar repasse</button>' : ""}</header>
      <div class="medical-transfers-toolbar"><label>Competência
        <input type="month" value="${esc(payload.competencia)}" data-medical-month>
      </label><button type="button" class="m15-btn m15-btn-sec" data-medical-refresh>Atualizar</button></div>
      <div class="medical-transfers-kpis">
        <div class="medical-transfers-kpi"><span>Competência atual</span><strong>${esc(payload.competencia)}</strong></div>
        <div class="medical-transfers-kpi"><span>Total a pagar</span><strong>${esc(totalDue)}</strong></div>
        <div class="medical-transfers-kpi"><span>Total pago</span><strong>${brl(payload.total_pago)}</strong></div>
      </div>
      <div class="medical-transfers-form-slot"></div>
      <div class="medical-transfers-form-status" role="status"></div>
      <div class="medical-transfers-table-wrap"><table>
        <thead><tr><th>Médica</th><th class="num">Laudos</th><th class="num">Unitário</th><th class="num">Referência</th><th class="num">Pago</th><th>Pagamento</th><th>Status</th><th></th></tr></thead>
        <tbody>${rows.length ? rows.map((row) => rowHtml(row, canManage)).join("") : '<tr><td colspan="8">Nenhum laudo concluído nesta competência.</td></tr>'}</tbody>
      </table></div>
      <details class="medical-transfers-history"><summary>Histórico de competências</summary>${historyHtml(payload.historico || [])}</details>
    </article>`;
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

  function load(month) {
    const root = document.getElementById(ROOT);
    if (!root) return Promise.resolve();
    if (!m15() || !m15().hasToken() || !m15().can("gestor")) { root.innerHTML = ""; return Promise.resolve(); }
    root.innerHTML = '<article class="panel medical-transfers">Carregando Repasses médicos…</article>';
    const query = month ? `?competencia=${encodeURIComponent(month)}` : "";
    return api("/financeiro/repasses-medicos" + query).then(render).catch((err) => {
      root.innerHTML = `<article class="panel medical-transfers">Repasses médicos indisponíveis: ${esc(err.message || err)}</article>`;
    });
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest && event.target.closest("[data-medical-open],[data-medical-create],[data-medical-pay],[data-medical-refresh],[data-medical-cancel]");
    if (!target) return;
    if (target.hasAttribute("data-medical-open")) openCreate();
    else if (target.hasAttribute("data-medical-create")) openCreate(target.dataset.medicalCreate);
    else if (target.hasAttribute("data-medical-pay")) openPayment(target.dataset.medicalPay);
    else if (target.hasAttribute("data-medical-refresh")) load(document.querySelector("[data-medical-month]").value);
    else if (target.hasAttribute("data-medical-cancel")) target.closest(".medical-transfers-form-slot").innerHTML = "";
  });

  document.addEventListener("input", (event) => {
    const form = event.target.closest && event.target.closest("[data-medical-create-form]");
    if (form) updateTotal(form);
  });
  document.addEventListener("change", (event) => {
    if (event.target.matches && event.target.matches("[data-medical-month]")) load(event.target.value);
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
