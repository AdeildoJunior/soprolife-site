/* Fiscal — Command Center operacional do NFS-e (fundação offline).
 *
 * Mostra só o que o backend já garante com segurança: fila derivada do
 * domínio fiscal aprovado (M26/M27), prontidão do provedor restrito
 * (sempre leitura, nunca um botão que ativa rede real) e o lote
 * "Emitir pendentes" sobre o provedor mock — o único caminho de emissão
 * realmente ligado nesta fundação. Nenhum dado de paciente (CPF, nome
 * completo, endereço) é buscado ou exibido aqui.
 */
(function () {
  "use strict";

  const ROOT = "fiscalRoot";
  const STATE_LABELS = {
    pending: "Elegível", blocked: "Bloqueada", issuing: "Emitindo…",
    simulated: "Emitida (simulada)", failed: "Falhou", uncertain: "Incerta — reconciliar",
    reconciling: "Reconciliando…", cancelled: "Cancelada",
  };
  const CATEGORY_LABELS = {
    blocked_by_partner_model: "Modelo de parceria não autorizado (Pastore/SPLIT)",
    missing_required_tax_configuration: "Configuração tributária incompleta",
    blocked_by_fiscal_policy: "Sem política fiscal vigente",
    blocked_by_financial_source: "Fonte financeira ausente ou inválida",
    requires_reprepare: "Precisa ser repreparada (valor/política mudou)",
    pending_clinical_or_identity_data: "Aguardando dado clínico/identidade",
    blocked_other: "Outro motivo",
  };

  let state = { status: null, summary: null, documents: [], selected: new Set(), filter: "pending", busy: false, lastBatch: null };

  function m15() { return window.SoproM15 || null; }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function brl(value) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" }) : "—";
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const parts = String(iso).slice(0, 10).split("-");
    return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : iso;
  }
  function api(path, options) { return m15().api(path, options); }
  function short(id) { return id ? String(id).slice(0, 8) : "—"; }

  function root() { return document.getElementById(ROOT); }

  function empty(message) { return `<p class="fiscal-empty">${esc(message)}</p>`; }

  // ------------------------------------------------------------- readiness

  function readinessHtml(status) {
    if (!status) return "";
    const block = status.restricted_provider_foundation || {};
    const r = block.readiness || {};
    const prod = block.production_readiness || { gates: [] };
    const cert = r.certificate_summary;
    const rows = [
      ["Ambiente ativo", esc(status.environment)],
      ["Provedor em uso", esc(status.provider)],
      ["Emissão real disponível", status.real_issuance_available ? "Sim" : "Não"],
      ["Leiaute restrito", esc(block.layout_version)],
      ["Schema", esc(r.schema_version || "—")],
      ["Portão de rede restrito", block.network_gate_enabled ? "Aberto" : "Fechado (padrão)"],
      ["Certificado configurado", r.certificate_configured ? "Sim" : "Não"],
      ["Certificado válido/legível", r.certificate_syntactically_valid === true ? "Sim"
        : r.certificate_syntactically_valid === false ? "Não" : "Não verificável"],
      ["Segredo configurado", r.secret_configured ? "Sim" : "Não (nunca exibido)"],
      ["Política fiscal completa (restrito)", r.fiscal_policy_ready ? "Sim" : "Não"],
      ["Armazenamento de artefatos pronto", r.artifact_storage_ready ? "Sim" : "Não"],
    ];
    const certHtml = cert ? `<div class="fiscal-cert-summary">
        <strong>Certificado restrito (resumo seguro)</strong>
        <span>Titular: ${esc(cert.subject_common_name || "—")}</span>
        <span>Validade: ${fmtDate(cert.not_before)} a ${fmtDate(cert.not_after)}
          ${cert.expired ? '<span class="fiscal-chip fiscal-chip-blocked">expirado</span>' : ''}</span>
      </div>` : "";
    const blockers = (r.blockers || []).map((b) => `<li>${esc(b)}</li>`).join("");
    const gates = (prod.gates || []).map((g) => `<li class="fiscal-gate ${g.satisfied ? 'fiscal-gate-ok' : 'fiscal-gate-blocked'}">
        <span>${g.satisfied ? '✓' : '✕'} ${esc(g.name)}</span><small>${esc(g.detail)}</small>
      </li>`).join("");
    return `<article class="panel fiscal-readiness-panel">
      <div class="panel-header"><h3>Prontidão do provedor (Produção Restrita)</h3>
        <span class="safe-label">Somente leitura — nenhum botão aqui liga rede real</span></div>
      <dl class="fiscal-readiness-grid">
        ${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}
      </dl>
      ${certHtml}
      ${blockers ? `<details class="fiscal-blockers"><summary>${(r.blockers || []).length} bloqueio(s) para a primeira chamada restrita real</summary><ul>${blockers}</ul></details>` : ""}
      <details class="fiscal-blockers">
        <summary>Estrutura de ativação de produção (${prod.all_satisfied ? "completa" : "incompleta"})</summary>
        <ul class="fiscal-gate-list">${gates}</ul>
      </details>
    </article>`;
  }

  // ------------------------------------------------------------ queue summary

  function summaryHtml(summary) {
    if (!summary) return "";
    const cards = [
      ["Elegíveis", summary.eligible, "eligible"],
      ["Bloqueadas", summary.blocked_total, "blocked"],
      ["Aguardando reconciliação", summary.reconciliation_required, "uncertain"],
      ["Emitidas", summary.simulated, "simulated"],
      ["Falharam", summary.failed, "failed"],
    ];
    const breakdown = Object.entries(summary.blocked_breakdown || {})
      .filter(([, n]) => n > 0)
      .map(([key, n]) => `<li>${n} — ${esc(CATEGORY_LABELS[key] || key)}</li>`).join("");
    return `<div class="fiscal-summary-cards">
      ${cards.map(([label, n, key]) => `<button type="button" class="fiscal-summary-card"
        data-fiscal-filter="${key === 'eligible' ? 'pending' : key}">
        <strong>${n}</strong><span>${esc(label)}</span></button>`).join("")}
      </div>
      ${breakdown ? `<ul class="fiscal-breakdown">${breakdown}</ul>` : ""}`;
  }

  // ------------------------------------------------------------------- queue

  function docRowHtml(doc, canManage) {
    const prep = doc.preparation || {};
    const checked = state.selected.has(doc.id) ? " checked" : "";
    const canSelect = doc.state === "pending";
    const reasons = (doc.blocking_reasons || []).map((r) => `<span class="fiscal-chip fiscal-chip-blocked">${esc(r)}</span>`).join(" ");
    return `<tr class="fiscal-row fiscal-row-${esc(doc.state)}">
      <td>${canManage && canSelect ? `<input type="checkbox" data-fiscal-select="${esc(doc.id)}"${checked}>` : ""}</td>
      <td><code title="${esc(doc.id)}">${short(doc.id)}</code></td>
      <td>${esc(prep.flow || "—")}</td>
      <td>${fmtDate(prep.service_date)}</td>
      <td>${fmtDate(prep.competence)}</td>
      <td class="fin-num">${brl(prep.amount_snapshot)}</td>
      <td><span class="fiscal-chip fiscal-state-${esc(doc.state)}">${esc(STATE_LABELS[doc.state] || doc.state)}</span></td>
      <td>${reasons || (doc.reconciliation_required ? '<span class="fiscal-chip fiscal-chip-uncertain">reconciliação necessária</span>' : "—")}</td>
    </tr>`;
  }

  function queueHtml(documents, canManage) {
    if (!documents.length) return empty("Nenhum documento fiscal neste filtro.");
    return `<div class="fiscal-table-wrap"><table class="fiscal-table">
      <thead><tr>
        <th></th><th>Documento</th><th>Fluxo</th><th>Data do serviço</th>
        <th>Competência</th><th class="fin-num">Valor</th><th>Status</th><th>Detalhe</th>
      </tr></thead>
      <tbody>${documents.map((d) => docRowHtml(d, canManage)).join("")}</tbody>
    </table></div>`;
  }

  function batchResultHtml(result) {
    if (!result) return "";
    const counts = result.itens.reduce((acc, item) => {
      const key = item.error ? (item.error.codigo || "erro") : item.state;
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    const summaryLine = Object.entries(counts).map(([k, n]) => `${n} ${esc(k)}`).join(" · ");
    return `<div class="fiscal-batch-result" role="status">
      <strong>${result.itens.length} processada(s):</strong> ${summaryLine}
      <ul>${result.itens.map((item) => `<li><code>${short(item.id)}</code> — ${
        item.error ? `<span class="fiscal-chip fiscal-chip-blocked">${esc(item.error.codigo)}</span>`
                    : esc(STATE_LABELS[item.state] || item.state)}</li>`).join("")}</ul>
    </div>`;
  }

  // --------------------------------------------------------------- rendering

  function render() {
    const el = root();
    if (!el) return;
    if (!m15() || !m15().hasToken()) {
      el.innerHTML = empty("Entre no Núcleo M15 para ver a fila fiscal.");
      return;
    }
    if (!state.status || !state.status.enabled) {
      el.innerHTML = empty("Fundação fiscal desativada nesta instância (M15_NFSE_ENABLED).");
      return;
    }
    const canManage = m15().can("gestor");
    const eligibleOnScreen = state.documents.filter((d) => d.state === "pending");
    el.innerHTML = `
      ${readinessHtml(state.status)}
      <article class="panel fiscal-queue-panel">
        <div class="panel-header">
          <h3>Fila fiscal</h3>
          <div class="fiscal-actions">
            <label>Filtro
              <select data-fiscal-filter-select>
                ${["pending", "blocked", "uncertain", "reconciling", "simulated", "failed", "cancelled"]
                  .map((s) => `<option value="${s}"${state.filter === s ? " selected" : ""}>${esc(STATE_LABELS[s] || s)}</option>`).join("")}
              </select>
            </label>
            <button type="button" data-fiscal-refresh>Atualizar</button>
          </div>
        </div>
        ${summaryHtml(state.summary)}
        ${canManage ? `<div class="fiscal-batch-bar">
          <button type="button" data-fiscal-select-eligible>Selecionar elegíveis</button>
          <span>${state.selected.size} selecionada(s) de ${eligibleOnScreen.length} elegível(is) nesta tela</span>
          <button type="button" class="fiscal-emit-btn" data-fiscal-emit ${state.selected.size ? "" : "disabled"}
            ${state.busy ? "disabled" : ""}>${state.busy ? "Emitindo…" : "Emitir pendentes"}</button>
        </div>` : ""}
        <div class="fiscal-live-status" role="status" aria-live="polite"></div>
        ${queueHtml(state.documents, canManage)}
        ${batchResultHtml(state.lastBatch)}
      </article>`;
  }

  function setStatus(message, error) {
    const el = document.querySelector(`#${ROOT} .fiscal-live-status`);
    if (!el) return;
    el.textContent = message || "";
    el.className = "fiscal-live-status" + (error ? " is-error" : "");
  }

  // ------------------------------------------------------------------- load

  function load() {
    const el = root();
    if (!el) return Promise.resolve();
    if (!m15() || !m15().hasToken()) { render(); return Promise.resolve(); }
    return Promise.all([
      api("/fiscal/status"),
      api("/fiscal/fila-resumo").catch(() => null),
      api(`/fiscal/documentos?state=${encodeURIComponent(state.filter)}&tamanho=100`).catch(() => ({ itens: [] })),
    ]).then(([status, summary, docs]) => {
      state.status = status;
      state.summary = summary;
      state.documents = (docs && docs.itens) || [];
      const visibleIds = new Set(state.documents.map((d) => d.id));
      state.selected.forEach((id) => { if (!visibleIds.has(id)) state.selected.delete(id); });
      render();
    }).catch((err) => {
      const el2 = root();
      if (el2) el2.innerHTML = empty("Fiscal indisponível: " + ((err && err.message) || String(err)));
    });
  }

  document.addEventListener("click", (event) => {
    const within = event.target.closest && event.target.closest(`#${ROOT}`);
    if (!within) return;

    if (event.target.closest("[data-fiscal-refresh]")) { load(); return; }

    const filterCard = event.target.closest("[data-fiscal-filter]");
    if (filterCard) {
      state.filter = filterCard.dataset.fiscalFilter;
      state.selected.clear();
      load();
      return;
    }

    if (event.target.closest("[data-fiscal-select-eligible]")) {
      state.documents.filter((d) => d.state === "pending").forEach((d) => state.selected.add(d.id));
      render();
      return;
    }

    const emit = event.target.closest("[data-fiscal-emit]");
    if (emit && !emit.disabled) {
      const documentIds = Array.from(state.selected);
      if (!documentIds.length) return;
      state.busy = true;
      state.lastBatch = null;
      setStatus(`Emitindo ${documentIds.length} documento(s)…`);
      render();
      api("/fiscal/emitir-pendentes", {
        method: "POST",
        body: JSON.stringify({ idempotency_key: m15().idemKey(), document_ids: documentIds }),
      }).then((result) => {
        state.busy = false;
        state.lastBatch = result;
        state.selected.clear();
        setStatus("Lote processado.");
        return load();
      }).catch((err) => {
        state.busy = false;
        setStatus((err && err.message) || String(err), true);
        render();
      });
    }
  });

  document.addEventListener("change", (event) => {
    const within = event.target.closest && event.target.closest(`#${ROOT}`);
    if (!within) return;
    const checkbox = event.target.closest("[data-fiscal-select]");
    if (checkbox) {
      const id = checkbox.dataset.fiscalSelect;
      if (checkbox.checked) state.selected.add(id); else state.selected.delete(id);
      render();
      return;
    }
    const select = event.target.closest("[data-fiscal-filter-select]");
    if (select) {
      state.filter = select.value;
      state.selected.clear();
      load();
    }
  });

  document.addEventListener("soprolife:cadastro", load);
  if (m15()) m15().onSessionChange(load);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }

  window.SoproFiscalCommandCenter = { refresh: load };
})();
