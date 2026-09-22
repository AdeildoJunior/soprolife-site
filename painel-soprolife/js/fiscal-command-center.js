/* Fiscal — Command Center operacional do NFS-e (fundação offline + M29).
 *
 * Mostra só o que o backend já garante com segurança: fila derivada do
 * domínio fiscal aprovado (M26/M27), prontidão do provedor restrito
 * (sempre leitura, nunca um botão que ativa rede real), a configuração
 * fiscal nacional versionada (M29 — inspeção sempre livre; criar uma NOVA
 * versão exige papel admin) e o lote "Emitir pendentes" sobre o provedor
 * mock — o único caminho de emissão realmente ligado nesta fundação.
 * Nenhum dado de paciente (CPF, nome completo, endereço) é buscado ou
 * exibido aqui, e NENHUM controle nesta tela liga o portão de rede
 * restrito — isso continua sendo só variável de ambiente do processo.
 */
(function () {
  "use strict";

  const ROOT = "fiscalRoot";
  const STATE_LABELS = {
    pending: "Elegível", blocked: "Bloqueada", issuing: "Emitindo…",
    // M57 — "issued" é NFS-e real na SEFIN; "simulated" é só o mock, que não
    // emite nada em lugar nenhum. Antes os dois eram o mesmo estado e uma
    // NFS-e oficial aparecia no painel como simulação.
    issued: "Emitida", simulated: "Simulada (mock)",
    failed: "Falhou", uncertain: "Incerta — reconciliar",
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

  let state = {
    status: null, summary: null, documents: [], selected: new Set(), filter: "pending", busy: false, lastBatch: null,
    nationalConfigs: [], activeNationalConfig: null, configFormOpen: false, configBusy: false, configStatus: null,
  };

  const TRIB_ISSQN_LABELS = { 1: "Operação Tributável", 2: "Imunidade", 3: "Exportação de serviço", 4: "Não Incidência" };
  const TP_RET_ISSQN_LABELS = { 1: "Não Retido", 2: "Retido pelo Tomador", 3: "Retido pelo Intermediário" };
  const OP_SIMP_NAC_LABELS = { 1: "Não Optante", 2: "Optante — MEI", 3: "Optante — ME/EPP (Simples Nacional)" };
  const REG_ESP_TRIB_LABELS = {
    0: "Nenhum", 1: "Ato Cooperado", 2: "Estimativa", 3: "Microempresa Municipal",
    4: "Notário ou Registrador", 5: "Profissional Autônomo", 6: "Sociedade de Profissionais", 9: "Outros",
  };

  // Pré-preenchimento com o perfil fiscal REAL da SoproLife (missão M29,
  // seção A) — só uma sugestão editável no formulário; nada é enviado sem
  // clique explícito de um admin autenticado.
  const SOPROLIFE_REAL_PROFILE_DEFAULTS = {
    version: "SOPROLIFE-REAL-v1", environment: "restricted", effective_from: "",
    validation_state: "draft",
    issuer_cnpj: "63544026000110",
    issuer_name: "SoproLife Diagnósticos e Soluções em Saúde LTDA",
    issuer_municipio_ibge: "3304557",
    issuer_op_simp_nac: "3", issuer_reg_ap_trib_sn: "1", issuer_reg_esp_trib: "0",
    codigo_tributacao_nacional: "040201", codigo_tributacao_municipal: "001",
    codigo_nbs: "123019900", trib_issqn: "1", tp_ret_issqn: "1", p_tot_trib_sn: "6.00",
    validation_reference: "SOPROLIFE-M29-REAL-PROFILE",
  };

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

  // -------------------------------------------------- M29 national tax config

  function configSummaryHtml(cfg) {
    if (!cfg) return `<p class="fiscal-empty">Nenhuma configuração fiscal nacional validada para hoje.</p>`;
    const c = cfg.configuration || {};
    const rows = [
      ["Versão", esc(cfg.version)],
      ["Vigente desde", fmtDate(cfg.effective_from)],
      ["Situação", cfg.validation_state === "validated" ? "Validada" : "Rascunho"],
      ["CNPJ emissor", esc(c.issuer_cnpj)],
      ["Razão social", esc(c.issuer_name)],
      ["Regime — Simples Nacional", esc(OP_SIMP_NAC_LABELS[c.issuer_op_simp_nac] || c.issuer_op_simp_nac)],
      ["Regime especial", esc(REG_ESP_TRIB_LABELS[c.issuer_reg_esp_trib] ?? c.issuer_reg_esp_trib)],
      ["Código tributação nacional", esc(c.codigo_tributacao_nacional)],
      ["Código tributação municipal", esc(c.codigo_tributacao_municipal || "—")],
      ["NBS", esc(c.codigo_nbs || "—")],
      ["Tributação ISSQN", esc(TRIB_ISSQN_LABELS[c.trib_issqn] || c.trib_issqn)],
      ["Retenção ISSQN", esc(TP_RET_ISSQN_LABELS[c.tp_ret_issqn] || c.tp_ret_issqn)],
      ["% aproximado do Simples Nacional", c.p_tot_trib_sn != null ? `${esc(c.p_tot_trib_sn)}%` : "—"],
    ];
    return `<dl class="fiscal-readiness-grid">
      ${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}
    </dl>`;
  }

  function configHistoryHtml(versions) {
    if (!versions.length) return "";
    return `<details class="fiscal-blockers"><summary>${versions.length} versão(ões) no histórico (imutável)</summary>
      <div class="fiscal-table-wrap"><table class="fiscal-table">
        <thead><tr><th>Versão</th><th>Ambiente</th><th>Vigente desde</th><th>Situação</th><th>Criada em</th></tr></thead>
        <tbody>${versions.map((v) => `<tr>
          <td><code>${esc(v.version)}</code></td><td>${esc(v.environment)}</td>
          <td>${fmtDate(v.effective_from)}</td>
          <td><span class="fiscal-chip ${v.validation_state === "validated" ? "fiscal-state-issued" : "fiscal-chip-uncertain"}">${v.validation_state === "validated" ? "Validada" : "Rascunho"}</span></td>
          <td>${fmtDate(v.created_at)}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </details>`;
  }

  function configFormHtml() {
    if (!state.configFormOpen) return "";
    const d = SOPROLIFE_REAL_PROFILE_DEFAULTS;
    const opts = (labels, current) => Object.entries(labels)
      .map(([value, label]) => `<option value="${value}"${String(current) === value ? " selected" : ""}>${value} — ${esc(label)}</option>`).join("");
    return `<form class="fiscal-config-form" data-fiscal-config-form>
      <p class="safe-label">Cria uma NOVA versão efetiva — nenhuma versão antiga é alterada. Nunca pede nem grava senha/certificado aqui.</p>
      <div class="fiscal-config-grid">
        <label>Identificador da versão<input name="version" required value="${esc(d.version)}"></label>
        <label>Ambiente
          <select name="environment">
            <option value="restricted"${d.environment === "restricted" ? " selected" : ""}>restricted (Produção Restrita)</option>
            <option value="production"${d.environment === "production" ? " selected" : ""}>production</option>
          </select>
        </label>
        <label>Vigente a partir de<input type="date" name="effective_from" required value="${esc(d.effective_from)}"></label>
        <label>Situação
          <select name="validation_state">
            <option value="draft"${d.validation_state === "draft" ? " selected" : ""}>Rascunho</option>
            <option value="validated"${d.validation_state === "validated" ? " selected" : ""}>Validada (torna-se ativa)</option>
          </select>
        </label>
        <label>CNPJ do emissor<input name="issuer_cnpj" required maxlength="14" value="${esc(d.issuer_cnpj)}"></label>
        <label>Razão social do emissor<input name="issuer_name" required value="${esc(d.issuer_name)}"></label>
        <label>Município do emissor (IBGE)<input name="issuer_municipio_ibge" required pattern="[0-9]{7}" value="${esc(d.issuer_municipio_ibge)}"></label>
        <label>Situação — Simples Nacional<select name="issuer_op_simp_nac">${opts(OP_SIMP_NAC_LABELS, d.issuer_op_simp_nac)}</select></label>
        <label>Regime de apuração (Simples, opcional)
          <select name="issuer_reg_ap_trib_sn">
            <option value="">— não informado —</option>
            <option value="1"${d.issuer_reg_ap_trib_sn === "1" ? " selected" : ""}>1 — Tributos federais e municipal pelo SN</option>
            <option value="2"${d.issuer_reg_ap_trib_sn === "2" ? " selected" : ""}>2 — Federais pelo SN, ISSQN fora do SN</option>
            <option value="3"${d.issuer_reg_ap_trib_sn === "3" ? " selected" : ""}>3 — Federais e municipal fora do SN</option>
          </select>
        </label>
        <label>Regime especial de tributação<select name="issuer_reg_esp_trib">${opts(REG_ESP_TRIB_LABELS, d.issuer_reg_esp_trib)}</select></label>
        <label>Código de tributação nacional<input name="codigo_tributacao_nacional" required pattern="[0-9]{6}" value="${esc(d.codigo_tributacao_nacional)}"></label>
        <label>Código de tributação municipal<input name="codigo_tributacao_municipal" value="${esc(d.codigo_tributacao_municipal)}"></label>
        <label>NBS<input name="codigo_nbs" maxlength="9" value="${esc(d.codigo_nbs)}"></label>
        <label>Tributação do ISSQN<select name="trib_issqn">${opts(TRIB_ISSQN_LABELS, d.trib_issqn)}</select></label>
        <label>Retenção do ISSQN<select name="tp_ret_issqn">${opts(TP_RET_ISSQN_LABELS, d.tp_ret_issqn)}</select></label>
        <label>% aproximado do Simples Nacional (pTotTribSN)<input name="p_tot_trib_sn" inputmode="decimal" value="${esc(d.p_tot_trib_sn)}"></label>
        <label>Referência de validação<input name="validation_reference" required value="${esc(d.validation_reference)}"></label>
      </div>
      <div class="fiscal-actions">
        <button type="submit" ${state.configBusy ? "disabled" : ""}>${state.configBusy ? "Salvando…" : "Salvar nova versão"}</button>
        <button type="button" data-fiscal-config-cancel>Cancelar</button>
      </div>
    </form>`;
  }

  function nationalConfigHtml() {
    const canAdmin = m15().can("admin");
    return `<article class="panel fiscal-national-config-panel">
      <div class="panel-header">
        <h3>Configuração Fiscal Nacional (M29)</h3>
        <span class="safe-label">Versionada e imutável — uma nova decisão contábil é sempre uma versão nova</span>
      </div>
      ${configSummaryHtml(state.activeNationalConfig)}
      ${configHistoryHtml(state.nationalConfigs)}
      ${state.configStatus ? `<p class="fiscal-live-status${state.configStatus.error ? " is-error" : ""}" role="status">${esc(state.configStatus.message)}</p>` : ""}
      ${canAdmin ? (state.configFormOpen ? configFormHtml()
        : `<div class="fiscal-actions"><button type="button" data-fiscal-config-new>Nova versão efetiva</button></div>`)
        : `<p class="fiscal-empty">Somente um administrador pode criar uma nova versão.</p>`}
    </article>`;
  }

  // ------------------------------------------------------------ queue summary

  function summaryHtml(summary) {
    if (!summary) return "";
    const cards = [
      ["Elegíveis", summary.eligible, "eligible"],
      ["Bloqueadas", summary.blocked_total, "blocked"],
      ["Aguardando reconciliação", summary.reconciliation_required, "uncertain"],
      ["Emitidas", summary.issued || 0, "issued"],
      ["Simuladas (mock)", summary.simulated || 0, "simulated"],
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
      ${nationalConfigHtml()}
      <article class="panel fiscal-queue-panel">
        <div class="panel-header">
          <h3>Fila fiscal</h3>
          <div class="fiscal-actions">
            <label>Filtro
              <select data-fiscal-filter-select>
                ${["pending", "blocked", "uncertain", "reconciling", "issued", "simulated", "failed", "cancelled"]
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
      api("/fiscal/configuracao-nacional").catch(() => []),
      api("/fiscal/configuracao-nacional/ativa?environment=restricted").catch(() => null),
    ]).then(([status, summary, docs, configs, activeConfig]) => {
      state.status = status;
      state.summary = summary;
      state.documents = (docs && docs.itens) || [];
      state.nationalConfigs = Array.isArray(configs) ? configs : [];
      state.activeNationalConfig = activeConfig;
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

    if (event.target.closest("[data-fiscal-config-new]")) {
      state.configFormOpen = true;
      state.configStatus = null;
      render();
      return;
    }
    if (event.target.closest("[data-fiscal-config-cancel]")) {
      state.configFormOpen = false;
      render();
      return;
    }

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

  document.addEventListener("submit", (event) => {
    const form = event.target.closest && event.target.closest("[data-fiscal-config-form]");
    if (!form || !form.closest(`#${ROOT}`)) return;
    event.preventDefault();
    const f = form.elements;
    const optionalInt = (name) => (f[name].value === "" ? null : Number(f[name].value));
    const optionalText = (name) => (f[name].value.trim() === "" ? null : f[name].value.trim());
    const optionalDecimal = (name) => (f[name].value.trim() === "" ? null : f[name].value.trim());
    const payload = {
      environment: f.environment.value,
      effective_from: f.effective_from.value,
      validation_state: f.validation_state.value,
      configuration: {
        version: f.version.value.trim(),
        layout_version: "restricted-v1.01-20260727",
        issuer_cnpj: f.issuer_cnpj.value.trim(),
        issuer_name: f.issuer_name.value.trim(),
        issuer_municipio_ibge: f.issuer_municipio_ibge.value.trim(),
        issuer_op_simp_nac: Number(f.issuer_op_simp_nac.value),
        issuer_reg_ap_trib_sn: optionalInt("issuer_reg_ap_trib_sn"),
        issuer_reg_esp_trib: Number(f.issuer_reg_esp_trib.value),
        codigo_tributacao_nacional: f.codigo_tributacao_nacional.value.trim(),
        codigo_tributacao_municipal: optionalText("codigo_tributacao_municipal"),
        codigo_nbs: optionalText("codigo_nbs"),
        trib_issqn: Number(f.trib_issqn.value),
        tp_ret_issqn: Number(f.tp_ret_issqn.value),
        p_tot_trib_sn: optionalDecimal("p_tot_trib_sn"),
        amount_basis: "financial_entry.valor",
        competence_rule: "service_date",
        own_revenue_confirmed: true,
        validation_reference: f.validation_reference.value.trim(),
      },
    };
    state.configBusy = true;
    state.configStatus = null;
    render();
    api("/fiscal/configuracao-nacional", { method: "POST", body: JSON.stringify(payload) })
      .then(() => {
        state.configBusy = false;
        state.configFormOpen = false;
        state.configStatus = { message: "Nova versão criada." };
        return load();
      }).catch((err) => {
        state.configBusy = false;
        state.configStatus = { message: (err && err.message) || String(err), error: true };
        render();
      });
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
