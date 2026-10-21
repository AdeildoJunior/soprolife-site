/* Fiscal — Command Center operacional do NFS-e (fundação offline + M29).
 *
 * Mostra só o que o backend já garante com segurança: fila derivada do
 * domínio fiscal aprovado (M26/M27), prontidão do provedor restrito
 * (sempre leitura, nunca um botão que ativa rede real), a configuração
 * fiscal nacional versionada (M29 — inspeção sempre livre; criar uma NOVA
 * versão exige papel admin) e o lote "Emitir pendentes" sobre o provedor
 * mock — o único caminho de emissão realmente ligado nesta fundação.
 * Sobre dado pessoal: a tela mostra o NOME de quem a nota vai nomear
 * (paciente na fila de produção desde a M66, tomador escolhido desde a
 * M26.34) porque é isso que um humano confere antes de autorizar uma
 * emissão. CPF nunca aparece inteiro — só mascarado, como o backend o
 * devolve. E NENHUM controle nesta tela liga o portão de rede restrito:
 * isso continua sendo só variável de ambiente do processo.
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
    // M66 — produção: fila por exame, um de cada vez, com confirmação dupla.
    production: null, prodModal: null, prodBusy: false, prodPoll: null,
    // M26.34 — NFS-e agrupada: composição escolhida à mão, tomador próprio.
    // `preview` é sempre o que o BACKEND respondeu: a tela nunca soma valores
    // por conta própria, para não existir um total que só a tela conhece.
    grouping: {
      selected: new Set(), recipient: null, preview: null, busy: false,
      status: null, documents: [], lookupBusy: false,
      // M26.35 — o resumo de confirmação de cada documento agrupado pendente,
      // por id: é dele que vêm a tomadora (nome) e `can_confirm`.
      summaries: {},
    },
  };

  // Bloqueios que dizem respeito à identidade fiscal do PACIENTE. Num
  // documento agrupado quem vai na nota é o tomador escolhido, então um
  // exame barrado só por estes continua podendo compor um grupo — é a mesma
  // regra que `nfse.evaluate(recipient_identity=False)` aplica no backend.
  const PATIENT_IDENTITY_BLOCKERS = new Set([
    "recipient_cpf_missing", "recipient_cpf_malformed",
    "recipient_cpf_repeated_digits", "recipient_cpf_check_digits_invalid",
    "recipient_name_missing", "recipient_name_not_a_full_name",
    "recipient_name_looks_like_placeholder", "recipient_name_too_long",
  ]);

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
    const prodIssue = status.production_issuance || {};
    // M26.35 — "unavailable"/"Não" abaixo descrevem o PROCESSO WEB, que por
    // desenho nunca tem A1 nem rede. Quem emite em produção é o executor
    // one-shot; sem esta linha a tela parecia dizer que não há emissão.
    const rows = [
      ["Ambiente ativo", esc(status.environment)],
      ...(status.environment === "production" ? [["Emissão de produção (executor one-shot)",
        prodIssue.available ? "Disponível — por botão, com confirmação dupla" : "Executor não configurado"]] : []),
      ["Provedor no processo web", esc(status.provider)],
      ["Emissão direta pelo processo web", status.real_issuance_available ? "Sim" : "Não (por desenho)"],
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

  // ------------------------------------------- M66 — emissão de produção
  //
  // O botão NÃO emite: registra a confirmação humana e o executor one-shot
  // do servidor (único processo com o certificado A1) faz no máximo UM envio.
  // Não existe emissão em lote em produção. O CPF nunca chega ao navegador:
  // a confirmação mostra só a forma mascarada.

  const PROD_STATUS_CLASS = {
    ready: "fiscal-state-pending", blocked: "fiscal-state-blocked", authorized: "fiscal-state-issuing",
    running: "fiscal-state-issuing", issued: "fiscal-state-issued", imported: "fiscal-state-issued",
    rejected: "fiscal-state-failed", uncertain: "fiscal-state-uncertain", in_progress: "fiscal-state-issuing",
    cancelled: "fiscal-state-cancelled",
    // M26.34 — o exame já compõe uma nota agrupada: nem pendente nem emitido.
    grouped: "fiscal-state-issuing",
  };
  const REQUEST_STATUS_LABELS = {
    authorized: "Confirmado — aguardando o executor", running: "Emitindo… (um único envio)",
    issued: "NFS-e emitida", reconciled: "Reconciliada — NFS-e confirmada",
    rejected: "Rejeitada pela SEFIN — nenhuma nova tentativa", uncertain: "Resultado incerto — reconciliar (só consulta)",
    refused: "Recusado antes do envio — nada foi enviado", expired: "Confirmação expirada — nada foi enviado",
    interrupted: "Executor interrompido — reconciliar (só consulta)",
  };
  const FINAL_REQUEST = new Set(["issued", "reconciled", "rejected", "uncertain", "refused", "expired", "interrupted"]);

  function isProduction() { return !!(state.status && state.status.environment === "production"); }

  function productionRowHtml(row, canManage) {
    const reason = row.block_label
      ? `<span class="fiscal-chip fiscal-chip-blocked" title="${esc((row.blocking_reasons || []).join(", "))}">${esc(row.block_label)}</span>`
      : "";
    const key = row.external_id ? `<small class="fiscal-key" title="Chave de acesso">${esc(row.external_id)}</small>` : "";
    let action = "";
    if (canManage && row.can_issue) {
      action = `<button type="button" class="fiscal-emit-btn" data-prod-issue="${esc(row.exam_id)}">Emitir NFS-e</button>`;
    } else if (canManage && row.can_reconcile && row.document_id) {
      action = `<button type="button" data-prod-reconcile="${esc(row.document_id)}">Reconciliar (só consulta)</button>`;
    }
    return `<tr class="fiscal-row">
      <td><strong>${esc(row.exam_code)}</strong></td>
      <td>${action}</td>
      <td>${esc(row.patient_name || "—")}</td>
      <td>${fmtDate(row.service_date)}</td>
      <td>${esc(row.flow_label || row.flow || "—")}</td>
      <td>${esc(row.municipio_name || "—")}</td>
      <td class="fin-num">${brl(row.amount)}</td>
      <td><span class="fiscal-chip ${PROD_STATUS_CLASS[row.status] || ""}">${esc(row.status_label || row.status)}</span>
        ${row.fiscal_validity ? '<span class="fiscal-chip fiscal-state-issued">válida fiscalmente</span>' : ""} ${key}</td>
      <td>${reason}</td>
    </tr>`;
  }

  function productionPanelHtml() {
    const prod = state.production;
    if (!isProduction()) return "";
    if (!m15().can("gestor")) {
      return `<article class="panel"><p class="fiscal-empty">A emissão de produção é restrita a gestor/admin.</p></article>`;
    }
    const info = (state.status && state.status.production_issuance) || {};
    const rows = (prod && prod.itens) || [];
    const counts = (prod && prod.counts) || {};
    return `<article class="panel fiscal-production-panel">
      <div class="panel-header">
        <h3>Emissão de NFS-e — produção</h3>
        <span class="safe-label">Uma nota por vez, com confirmação dupla. Sem emissão em lote.</span>
      </div>
      ${info.available ? "" : '<p class="fiscal-live-status is-error">Executor de produção não configurado nesta instância: nenhuma emissão pode ser confirmada.</p>'}
      <p class="fiscal-prod-counts">${esc(counts.ready || 0)} pronto(s) para emitir · ${esc(counts.blocked || 0)} bloqueado(s) · ${esc((counts.issued || 0) + (counts.imported || 0))} emitida(s)</p>
      ${rows.length ? `<div class="fiscal-table-wrap"><table class="fiscal-table">
        <thead><tr><th>Exame</th><th>Ação</th><th>Paciente</th><th>Data do serviço</th><th>Fluxo</th><th>Município</th>
          <th class="fin-num">Valor</th><th>Status fiscal</th><th>Motivo</th></tr></thead>
        <tbody>${rows.map((r) => productionRowHtml(r, info.available)).join("")}</tbody>
      </table></div>` : empty("Nenhum exame realizado para avaliar.")}
    </article>`;
  }

  // ── M26.34 — NFS-e agrupada ─────────────────────────────────────────────

  function groupableRows() {
    const rows = (state.production && state.production.itens) || [];
    return rows.filter((row) => row.status === "ready"
      || (row.status === "blocked"
          && (row.blocking_reasons || []).length > 0
          && (row.blocking_reasons || []).every((r) => PATIENT_IDENTITY_BLOCKERS.has(r))));
  }

  function groupedPreviewHtml() {
    const g = state.grouping;
    if (g.busy) return `<p class="fiscal-live-status" role="status">Conferindo a composição…</p>`;
    const p = g.preview;
    if (!p) {
      return `<p class="fiscal-empty">Escolha dois ou mais exames e o tomador para ver a composição.</p>`;
    }
    const itens = (p.itens || []).map((item) => `<tr>
        <td>${esc(item.position)}</td>
        <td><strong>${esc(item.exam_code || "—")}</strong></td>
        <td class="fin-num">${brl(item.amount)}</td>
      </tr>`).join("");
    const bloqueios = (p.blocking_labels || []).length
      ? `<ul class="fiscal-group-blocks">${(p.blocking_labels || [])
          .map((label) => `<li>${esc(label)}</li>`).join("")}</ul>`
      : "";
    return `<div class="fiscal-group-preview">
      <dl class="fiscal-readiness-grid">
        <div><dt>Tomadora</dt><dd>${esc((p.recipient && p.recipient.nome) || "—")}</dd></div>
        <div><dt>CPF</dt><dd><code>${esc((p.recipient && p.recipient.cpf_masked) || "—")}</code></dd></div>
        <div><dt>Data do serviço</dt><dd>${fmtDate(p.service_date)}</dd></div>
        <div><dt>Município</dt><dd>${esc(p.municipio_name || p.municipio_ibge || "—")}</dd></div>
        <div class="fiscal-wide"><dt>Descrição do serviço</dt>
          <dd>${esc(p.service_description || "—")}</dd></div>
      </dl>
      <div class="fiscal-table-wrap"><table class="fiscal-table">
        <thead><tr><th>#</th><th>Exame</th><th class="fin-num">Valor</th></tr></thead>
        <tbody>${itens}</tbody>
        <tfoot><tr><th colspan="2">Total (${esc(p.itens_count || 0)} exame(s))</th>
          <th class="fin-num">${brl(p.total)}</th></tr></tfoot>
      </table></div>
      ${bloqueios}
    </div>`;
  }

  const ACTIVE_REQUEST = new Set(["authorized", "running"]);

  // "ESP-000077 e ESP-000078", "a, b e c" — como a confirmação lê em voz alta.
  function joinCodes(codes) {
    const list = codes.filter(Boolean);
    if (list.length < 2) return list.join("");
    return `${list.slice(0, -1).join(", ")} e ${list[list.length - 1]}`;
  }

  // M26.35 — o pedido de emissão do documento agrupado vem da fila de
  // produção: cada exame do grupo carrega o último pedido do documento.
  function groupedRequest(docId) {
    const rows = (state.production && state.production.itens) || [];
    const row = rows.find((r) => r.grouped_document_id === docId && r.request);
    return row ? row.request : null;
  }

  function groupedDocumentsHtml() {
    const docs = state.grouping.documents || [];
    if (!docs.length) return "";
    const info = (state.status && state.status.production_issuance) || {};
    return `<h4 class="fiscal-group-docs-title">Documentos agrupados</h4>
      <div class="fiscal-group-docs">${docs.map((doc) => {
        const resumo = (state.grouping.summaries || {})[doc.id] || null;
        const request = groupedRequest(doc.id);
        const ativo = !!(request && ACTIVE_REQUEST.has(request.status));
        const podeEmitir = doc.state === "pending" && !!info.available && !ativo
          && !!(resumo && resumo.can_confirm);
        let situacao = "";
        if (ativo) {
          situacao = `<span class="fiscal-chip fiscal-state-issuing">${esc(REQUEST_STATUS_LABELS[request.status] || request.status)}</span>`;
        } else if (doc.state === "pending" && !info.available) {
          situacao = '<span class="safe-label">Executor de produção não configurado: emissão indisponível.</span>';
        } else if (doc.state === "pending" && resumo && !resumo.can_confirm) {
          situacao = `<span class="fiscal-chip fiscal-chip-blocked">${esc(resumo.block_label || "Não pode ser emitido agora")}</span>`;
        }
        return `<div class="fiscal-group-doc">
          <dl class="fiscal-readiness-grid">
            <div><dt>Documento</dt><dd><code>${short(doc.id)}</code></dd></div>
            <div><dt>Exames</dt><dd>${esc((doc.itens || []).map((i) => i.exam_code).join(" + ") || "—")}</dd></div>
            <div><dt>Tomadora</dt><dd>${esc((resumo && resumo.patient_name) || "—")}</dd></div>
            <div><dt>Total</dt><dd><strong>${brl(doc.itens_total)}</strong></dd></div>
            <div><dt>Estado</dt><dd><span class="fiscal-chip ${PROD_STATUS_CLASS[doc.state] || ""}">${
              esc(STATE_LABELS[doc.state] || doc.state)}</span></dd></div>
          </dl>
          <div class="fiscal-group-doc-actions">${situacao}
            ${podeEmitir ? `<button type="button" class="fiscal-emit-btn" data-group-issue="${esc(doc.id)}">Emitir NFS-e</button>` : ""}
          </div>
        </div>`;
      }).join("")}</div>`;
  }

  function groupedPanelHtml() {
    if (!isProduction() || !m15().can("gestor")) return "";
    const g = state.grouping;
    const rows = groupableRows();
    const linhas = rows.map((row) => `<tr class="fiscal-row">
        <td><input type="checkbox" data-group-select="${esc(row.exam_id)}"
          ${g.selected.has(row.exam_id) ? "checked" : ""}></td>
        <td><strong>${esc(row.exam_code)}</strong></td>
        <td>${esc(row.patient_name || "—")}</td>
        <td>${fmtDate(row.service_date)}</td>
        <td>${esc(row.municipio_name || "—")}</td>
        <td class="fin-num">${brl(row.amount)}</td>
      </tr>`).join("");
    const podePreparar = !!(g.preview && g.preview.can_prepare) && !g.busy;
    return `<article class="panel fiscal-group-panel">
      <div class="panel-header">
        <h3>NFS-e agrupada — vários exames, um tomador</h3>
        <span class="safe-label">Preparar não emite. A emissão sai pelo mesmo
          executor das notas individuais, com confirmação dupla.</span>
      </div>
      <p class="fiscal-prod-counts">${esc(rows.length)} exame(s) disponível(is) para compor ·
        ${esc(g.selected.size)} selecionado(s)</p>
      ${rows.length ? `<div class="fiscal-table-wrap"><table class="fiscal-table">
        <thead><tr><th></th><th>Exame</th><th>Paciente</th><th>Data</th>
          <th>Município</th><th class="fin-num">Valor</th></tr></thead>
        <tbody>${linhas}</tbody>
      </table></div>` : empty("Nenhum exame disponível para compor uma nota agrupada.")}
      <div class="fiscal-group-recipient">
        <label>CPF do tomador
          <input type="text" data-group-recipient-cpf inputmode="numeric"
            autocomplete="off" placeholder="somente dígitos" maxlength="14">
        </label>
        <button type="button" data-group-recipient-find ${g.lookupBusy ? "disabled" : ""}>
          ${g.lookupBusy ? "Procurando…" : "Localizar tomador"}</button>
        ${g.recipient ? `<span class="fiscal-chip">${esc(g.recipient.nome_completo
          || g.recipient.nome || "")} · ${esc(g.recipient.public_code || "")}</span>` : ""}
      </div>
      ${groupedPreviewHtml()}
      <div class="fiscal-batch-bar">
        <button type="button" data-group-preview ${g.busy ? "disabled" : ""}>Conferir composição</button>
        <button type="button" class="fiscal-emit-btn" data-group-prepare
          ${podePreparar ? "" : "disabled"}>Preparar documento (não emite)</button>
      </div>
      ${g.status ? `<p class="fiscal-live-status${g.status.error ? " is-error" : ""}"
        role="status">${esc(g.status.message)}</p>` : ""}
      ${groupedDocumentsHtml()}
    </article>`;
  }

  function productionModalHtml() {
    const modal = state.prodModal;
    if (!modal) return "";
    const s = modal.summary;
    let body;
    if (modal.loading) {
      body = `<p>Preparando os dados fiscais…</p>`;
    } else if (modal.request) {
      const r = modal.request;
      body = `<p class="fiscal-live-status${["rejected", "refused", "expired"].includes(r.status) ? " is-error" : ""}" role="status">
          ${esc(REQUEST_STATUS_LABELS[r.status] || r.status)}</p>
        <dl class="fiscal-readiness-grid">
          <div><dt>Pedido</dt><dd><code>${short(r.id)}</code></dd></div>
          <div><dt>Valor confirmado</dt><dd>${brl(r.amount_confirmed)}</dd></div>
          <div><dt>Envios à SEFIN</dt><dd>${esc(r.provider_post_count)}</dd></div>
          ${r.external_id ? `<div class="fiscal-wide"><dt>Chave de acesso</dt><dd><code>${esc(r.external_id)}</code></dd></div>` : ""}
          ${r.result_code ? `<div><dt>Código</dt><dd><code>${esc(r.result_code)}</code></dd></div>` : ""}
          ${r.fiscal_validity ? `<div><dt>Validade fiscal</dt><dd>Sim</dd></div>` : ""}
        </dl>
        ${FINAL_REQUEST.has(r.status) ? "" : "<p class=\"fiscal-empty\">Aguarde: esta janela atualiza sozinha.</p>"}`;
    } else if (s && modal.grouped) {
      // M26.35 — documento agrupado: duas confirmações explícitas antes de
      // qualquer escrita. A primeira diz O QUE será emitido; a segunda diz
      // a consequência, e só ela grava o pedido para o executor.
      const codigos = (s.itens || []).map((i) => i.exam_code);
      const itens = (s.itens || []).map((i) => `<li><span>${esc(i.position)}. <strong>${esc(i.exam_code || "—")}</strong></span>
          <span>${brl(i.amount)}</span></li>`).join("");
      body = !s.can_confirm
        ? `<p class="fiscal-live-status is-error">Não pode ser emitido: ${esc(s.block_label || (s.blocking_reasons || []).join(", ") || s.state)}</p>`
        : modal.stage === 2
          ? `<p class="fiscal-confirm-lead">Esta ação enviará a NFS-e ao provedor fiscal e poderá gerar obrigação fiscal. Confirmar emissão?</p>
             <p class="safe-label">Uma única NFS-e de <strong>${esc(s.amount_label)}</strong> para ${esc(s.patient_name || "—")} (${esc(joinCodes(codigos))}). O envio é feito uma única vez e não é repetido automaticamente.</p>`
          : `<p class="fiscal-confirm-lead">Você está prestes a emitir uma única NFS-e de ${esc(s.amount_label)} para ${esc(s.patient_name || "—")} referente aos exames ${esc(joinCodes(codigos))}.</p>
             <dl class="fiscal-readiness-grid">
               <div><dt>Tomadora</dt><dd>${esc(s.patient_name || "—")}</dd></div>
               <div><dt>CPF</dt><dd>${esc(s.cpf_masked || "ausente")}</dd></div>
               <div><dt>Data do serviço / competência</dt><dd>${fmtDate(s.service_date)} / ${fmtDate(s.competence)}</dd></div>
               <div><dt>Município de prestação</dt><dd>${esc(s.municipio_name || s.municipio_ibge || "—")}</dd></div>
             </dl>
             <ul class="fiscal-group-items">${itens}
               <li class="fiscal-group-items-total"><span>Total</span><span>${esc(s.amount_label || "—")}</span></li>
             </ul>
             <p class="fiscal-prod-description"><strong>Descrição do serviço:</strong> ${esc(s.service_description || "—")}</p>`;
    } else if (s) {
      body = `<dl class="fiscal-readiness-grid">
          <div><dt>Exame</dt><dd>${esc(s.exam_code)}</dd></div>
          <div><dt>Paciente (tomador)</dt><dd>${esc(s.patient_name || "—")}</dd></div>
          <div><dt>CPF</dt><dd>${esc(s.cpf_masked || "ausente")}</dd></div>
          <div><dt>Data do serviço / competência</dt><dd>${fmtDate(s.service_date)} / ${fmtDate(s.competence)}</dd></div>
          <div><dt>${esc(s.amount_title || "Valor")}</dt><dd><strong>${esc(s.amount_label || "—")}</strong></dd></div>
          <div><dt>Município de prestação</dt><dd>${esc(s.municipio_name || s.municipio_ibge || "—")}</dd></div>
          <div><dt>Fluxo</dt><dd>${esc(s.flow_label || s.flow || "—")}</dd></div>
          <div><dt>Broncodilatador</dt><dd>${s.broncodilatador === true ? "Com" : s.broncodilatador === false ? "Sem" : "Não informado"}</dd></div>
        </dl>
        <p class="fiscal-prod-description"><strong>Descrição do serviço:</strong> ${esc(s.service_description || "—")}</p>
        ${s.can_confirm
          ? `<p class="safe-label">Isto emite uma NFS-e REAL, com validade fiscal. Confira os dados acima. O envio é feito uma única vez e não é repetido automaticamente.</p>`
          : `<p class="fiscal-live-status is-error">Não pode ser emitido: ${esc(s.block_label || (s.blocking_reasons || []).join(", ") || s.state)}</p>`}`;
    }
    const error = modal.error ? `<p class="fiscal-live-status is-error" role="alert">${esc(modal.error)}</p>` : "";
    let confirm = "";
    if (s && s.can_confirm && !modal.request && !modal.loading) {
      confirm = modal.grouped && modal.stage !== 2
        ? `<button type="button" class="fiscal-emit-btn" data-prod-group-next>Continuar</button>`
        : `<button type="button" class="fiscal-emit-btn" data-prod-confirm ${state.prodBusy ? "disabled" : ""}>${esc(s.confirmation_phrase)}</button>`;
    }
    return `<div class="fiscal-modal-backdrop" data-prod-close-backdrop>
      <div class="fiscal-modal" role="dialog" aria-modal="true" aria-labelledby="fiscalProdModalTitle">
        <h3 id="fiscalProdModalTitle">${modal.request ? "Emissão de NFS-e" : "Confirmar emissão de NFS-e"}</h3>
        ${body || ""}${error}
        <div class="fiscal-actions">${confirm}
          <button type="button" data-prod-close>${modal.request && FINAL_REQUEST.has(modal.request.status) ? "Fechar" : "Cancelar"}</button>
        </div>
      </div>
    </div>`;
  }

  const PROD_ERRORS = {
    confirmation_stale: "Os dados mudaram depois que a janela abriu. Feche e abra de novo para conferir os dados atuais.",
    confirmation_amount_mismatch: "O valor confirmado não confere com o lançamento financeiro.",
    confirmation_phrase_mismatch: "Confirmação inválida.",
    document_not_pending_eligible: "Este exame não está pronto para emissão.",
    document_not_issuable: "Este exame já tem NFS-e ou uma emissão em andamento.",
    document_already_attempted: "Já houve uma tentativa para este exame. Não será enviado de novo.",
    document_already_sent_once: "Este exame já foi enviado uma vez. Só é possível reconciliar.",
    issuance_request_already_active: "Já existe uma confirmação em andamento para este exame.",
    exam_already_in_fiscal_document: "Este exame já compõe outra NFS-e (agrupada ou individual).",
    production_worker_unavailable: "O executor de produção não está disponível. Nada foi enviado.",
    reconciliation_not_required: "Este documento não precisa de reconciliação.",
  };

  function errorMessage(err) {
    const code = err && err.detalhe && err.detalhe.codigo;
    return (code && (PROD_ERRORS[code] || code)) || (err && err.message) || String(err);
  }

  function stopPolling() {
    if (state.prodPoll) { clearTimeout(state.prodPoll); state.prodPoll = null; }
  }

  function pollRequest(requestId, attempt) {
    stopPolling();
    api(`/fiscal/producao/pedidos/${encodeURIComponent(requestId)}`).then((request) => {
      if (!state.prodModal) return;
      state.prodModal.request = request;
      render();
      if (!FINAL_REQUEST.has(request.status) && attempt < 120) {
        state.prodPoll = setTimeout(() => pollRequest(requestId, attempt + 1), 3000);
      } else {
        loadProduction();
      }
    }).catch((err) => {
      if (state.prodModal) { state.prodModal.error = errorMessage(err); render(); }
    });
  }

  function loadProduction() {
    if (!isProduction() || !m15().can("gestor")) { state.production = null; return Promise.resolve(); }
    return api("/fiscal/producao/fila").then((data) => { state.production = data; })
      .catch((err) => { state.production = { itens: [], counts: {}, error: errorMessage(err) }; })
      // Os documentos agrupados vêm no mesmo passo: a tela mostra a fila e o
      // que já está preparado aguardando emissão, nunca uma sem a outra.
      .then(loadGroupedDocuments)
      .then(render);
  }

  function setGroupStatus(message, error) {
    state.grouping.status = message ? { message: message, error: !!error } : null;
  }

  function loadGroupedDocuments() {
    if (!isProduction() || !m15().can("leitura")) return Promise.resolve();
    return api("/fiscal/producao/agrupados")
      .then((data) => { state.grouping.documents = (data && data.itens) || []; })
      .catch(() => { state.grouping.documents = []; })
      .then(loadGroupedSummaries);
  }

  // M26.35 — o resumo de confirmação (gestor/admin) de cada agrupado ainda
  // pendente. Só leitura: é o mesmo GET que o modal usa.
  function loadGroupedSummaries() {
    const pendentes = (state.grouping.documents || []).filter((d) => d.state === "pending");
    if (!pendentes.length || !m15().can("gestor")) { state.grouping.summaries = {}; return Promise.resolve(); }
    return Promise.all(pendentes.map((doc) =>
      api(`/fiscal/producao/documentos/${encodeURIComponent(doc.id)}/confirmacao`)
        .then((resumo) => [doc.id, resumo]).catch(() => [doc.id, null])))
      .then((pares) => { state.grouping.summaries = Object.fromEntries(pares); });
  }

  function groupPayload() {
    const g = state.grouping;
    return {
      spirometry_exam_ids: Array.from(g.selected),
      recipient_person_id: g.recipient ? g.recipient.id : null,
    };
  }

  function groupReady() {
    const g = state.grouping;
    return g.selected.size >= 2 && !!g.recipient;
  }

  function refreshGroupPreview() {
    const g = state.grouping;
    if (!groupReady()) {
      g.preview = null;
      setGroupStatus(g.selected.size < 2
        ? "Escolha ao menos dois exames."
        : "Localize o tomador pelo CPF.", false);
      render();
      return Promise.resolve();
    }
    g.busy = true;
    setGroupStatus(null);
    render();
    // O total vem SEMPRE do backend: a tela não soma valores por conta
    // própria, para que não exista um total que só ela conheça.
    return api("/fiscal/producao/agrupados/previa", {
      method: "POST", body: JSON.stringify(groupPayload()),
    }).then((data) => { g.preview = data; })
      .catch((err) => { g.preview = null; setGroupStatus(errorMessage(err), true); })
      .then(() => { g.busy = false; render(); });
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
    const canManage = m15().can("gestor") && !isProduction();
    const eligibleOnScreen = state.documents.filter((d) => d.state === "pending");
    el.innerHTML = `
      ${productionPanelHtml()}
      ${groupedPanelHtml()}
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
      </article>
      ${productionModalHtml()}`;
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
      return loadProduction();
    }).catch((err) => {
      const el2 = root();
      if (el2) el2.innerHTML = empty("Fiscal indisponível: " + ((err && err.message) || String(err)));
    });
  }

  document.addEventListener("click", (event) => {
    const within = event.target.closest && event.target.closest(`#${ROOT}`);
    if (!within) return;

    if (event.target.closest("[data-fiscal-refresh]")) { load(); return; }

    const issueBtn = event.target.closest("[data-prod-issue]");
    if (issueBtn) {
      state.prodModal = { loading: true };
      render();
      api(`/fiscal/producao/exames/${encodeURIComponent(issueBtn.dataset.prodIssue)}/preparar`, { method: "POST" })
        .then((summary) => { state.prodModal = { summary }; render(); })
        .catch((err) => { state.prodModal = { error: errorMessage(err) }; render(); });
      return;
    }
    const groupIssueBtn = event.target.closest("[data-group-issue]");
    if (groupIssueBtn) {
      const docId = groupIssueBtn.dataset.groupIssue;
      state.prodModal = { loading: true, grouped: true };
      render();
      api(`/fiscal/producao/documentos/${encodeURIComponent(docId)}/confirmacao`)
        .then((summary) => {
          // A chave nasce com o modal: repetir o clique (ou a rede falhar e
          // o humano tentar de novo) reencontra o MESMO pedido no servidor.
          state.prodModal = { summary, grouped: true, stage: 1, idemKey: m15().idemKey() };
          render();
        })
        .catch((err) => { state.prodModal = { grouped: true, error: errorMessage(err) }; render(); });
      return;
    }
    if (event.target.closest("[data-prod-group-next]")) {
      const modal = state.prodModal;
      if (!modal || !modal.grouped || !modal.summary || !modal.summary.can_confirm) return;
      modal.stage = 2;
      render();
      return;
    }
    if (event.target.closest("[data-prod-confirm]")) {
      const s = state.prodModal && state.prodModal.summary;
      if (!s || !s.can_confirm || state.prodBusy) return;
      if (state.prodModal.grouped && state.prodModal.stage !== 2) return;
      state.prodBusy = true;
      render();
      api(`/fiscal/producao/documentos/${encodeURIComponent(s.document_id)}/confirmar-emissao`, {
        method: "POST",
        body: JSON.stringify({ preparation_id: s.preparation_id, amount: s.amount,
          confirmation: s.confirmation_phrase, idempotency_key: state.prodModal.idemKey || m15().idemKey() }),
      }).then((request) => {
        state.prodBusy = false;
        state.prodModal.request = request;
        render();
        pollRequest(request.id, 0);
      }).catch((err) => {
        state.prodBusy = false;
        state.prodModal.error = errorMessage(err);
        render();
      });
      return;
    }
    const reconcileBtn = event.target.closest("[data-prod-reconcile]");
    if (reconcileBtn) {
      state.prodModal = { loading: true };
      render();
      api(`/fiscal/producao/documentos/${encodeURIComponent(reconcileBtn.dataset.prodReconcile)}/solicitar-reconciliacao`, {
        method: "POST", body: JSON.stringify({ idempotency_key: m15().idemKey() }),
      }).then((request) => {
        state.prodModal = { request };
        render();
        pollRequest(request.id, 0);
      }).catch((err) => { state.prodModal = { error: errorMessage(err) }; render(); });
      return;
    }
    if (event.target.closest("[data-prod-close]") ||
        (event.target.matches && event.target.matches("[data-prod-close-backdrop]"))) {
      stopPolling();
      state.prodModal = null;
      render();
      loadProduction();
      return;
    }

    if (event.target.closest("[data-group-recipient-find]")) {
      const input = document.querySelector(`#${ROOT} [data-group-recipient-cpf]`);
      const cpf = ((input && input.value) || "").replace(/\D/g, "");
      if (cpf.length !== 11) { setGroupStatus("Informe os 11 dígitos do CPF.", true); render(); return; }
      state.grouping.lookupBusy = true;
      setGroupStatus(null);
      render();
      api("/pessoas/busca-cpf", { method: "POST", body: JSON.stringify({ cpf: cpf }) })
        .then((data) => {
          if (data && data.encontrada && data.pessoa) {
            state.grouping.recipient = data.pessoa;
            setGroupStatus(null);
          } else {
            state.grouping.recipient = null;
            setGroupStatus("Nenhuma pessoa cadastrada com este CPF.", true);
          }
        })
        .catch((err) => { state.grouping.recipient = null; setGroupStatus(errorMessage(err), true); })
        .then(() => {
          state.grouping.lookupBusy = false;
          render();
          if (state.grouping.recipient) refreshGroupPreview();
        });
      return;
    }

    if (event.target.closest("[data-group-preview]")) { refreshGroupPreview(); return; }

    if (event.target.closest("[data-group-prepare]")) {
      const g = state.grouping;
      if (!g.preview || !g.preview.can_prepare || g.busy) return;
      g.busy = true;
      setGroupStatus("Preparando o documento…");
      render();
      api("/fiscal/producao/agrupados", {
        method: "POST", body: JSON.stringify(groupPayload()),
      }).then((doc) => {
        g.selected = new Set();
        g.preview = null;
        g.recipient = null;
        const input = document.querySelector(`#${ROOT} [data-group-recipient-cpf]`);
        if (input) input.value = "";
        setGroupStatus("Documento " + short(doc && doc.id) + " preparado. Nada foi enviado à SEFIN: "
          + "confira abaixo e use \"Emitir NFS-e\" para emitir.", false);
      }).catch((err) => { setGroupStatus(errorMessage(err), true); })
        .then(() => {
          g.busy = false;
          return Promise.all([loadGroupedDocuments(), loadProduction()]);
        })
        .then(render);
      return;
    }

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
    const groupBox = event.target.closest("[data-group-select]");
    if (groupBox) {
      const examId = groupBox.getAttribute("data-group-select");
      if (groupBox.checked) state.grouping.selected.add(examId);
      else state.grouping.selected.delete(examId);
      state.grouping.preview = null;
      if (groupReady()) { refreshGroupPreview(); } else { render(); }
      return;
    }

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
