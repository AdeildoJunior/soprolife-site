/* M22 — fechamento mensal Pastore.
 *
 * Somente agregados institucionais e códigos públicos são exibidos. Exames
 * aguardando fechamento não entram nos totais financeiros. Um FinancialEntry
 * nasce apenas ao confirmar o recebimento mensal.
 */
(function () {
  "use strict";

  const ROOT = "pastoreSettlementRoot";
  const FIN_ROOT = "pastoreFinanceIndicatorRoot";

  function m15() { return window.SoproM15 || null; }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function brl(value) {
    if (value == null || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n)
      ? n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" }) : "—";
  }
  function parseMoney(value) {
    let raw = String(value || "").trim().replace(/^R\$\s*/, "");
    if (!raw) return "";
    if (raw.includes(",")) raw = raw.replace(/\./g, "").replace(",", ".");
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n.toFixed(2) : "";
  }
  function api(path, options) { return m15().api(path, options); }

  function empty(message) {
    return `<p class="pastore-settlement-empty">${esc(message)}</p>`;
  }

  function indicatorHtml(data) {
    const i = data.indicadores || {};
    const cards = [
      ["Pastore — aguardando fechamento", i.aguardando_fechamento || 0, "exames elegíveis; sem valor inferido"],
      ["Pastore — fechamento em aberto", i.fechamento_em_aberto || 0, "fechamentos incluídos ou enviados"],
      ["Pastore — a receber", i.a_receber || 0,
       Number(i.a_receber || 0) ? brl(i.valor_a_receber_confirmado) + " confirmado" : "nenhum valor confirmado"],
      ["Pastore — recebido", i.recebido || 0,
       Number(i.recebido || 0) ? brl(i.valor_recebido) + " efetivamente recebido" : "nenhum recibo mensal"],
    ];
    return `<div class="pastore-indicators" aria-label="Situação dos fechamentos Pastore">
      ${cards.map((c) => `<article class="pastore-indicator">
        <span>${esc(c[0])}</span><strong>${esc(c[1])}</strong><small>${esc(c[2])}</small>
      </article>`).join("")}
    </div>`;
  }

  function settlementActions(row, canManage) {
    if (!canManage || row.status === "recebido") return "";
    const disabledReceive = row.status === "cancelado" ? " disabled" : "";
    return `<details class="pastore-settlement-actions">
      <summary>Gerenciar fechamento</summary>
      <form data-pastore-update="${esc(row.id)}" class="pastore-settlement-form">
        <label>Estado
          <select name="status">
            ${[
              ["incluido", "Incluído no fechamento"],
              ["enviado", "Fechamento enviado"],
              ["a_receber", "A receber da Pastore"],
              ["cancelado", "Fechamento cancelado"],
            ].map((o) => `<option value="${o[0]}"${row.status === o[0] ? " selected" : ""}>${o[1]}</option>`).join("")}
          </select>
        </label>
        <label>Valor mensal confirmado (R$)
          <input name="valor_total" inputmode="decimal" value="${esc(row.valor_total || "")}"
            placeholder="Somente após conferir acordo/extrato">
        </label>
        <label>Data de envio
          <input name="data_envio" type="date" value="${esc(row.data_envio || "")}">
        </label>
        <button type="submit">Atualizar fechamento</button>
      </form>
      <form data-pastore-receive="${esc(row.id)}" class="pastore-settlement-form pastore-receive-form">
        <strong>Confirmar pagamento efetivamente recebido</strong>
        <label>Valor recebido (R$)
          <input name="valor_confirmado" inputmode="decimal"
            value="${esc(row.valor_total || "")}" required${disabledReceive}>
        </label>
        <label>Data real do recebimento
          <input name="data_recebimento" type="date" required${disabledReceive}>
        </label>
        <label>Forma de pagamento
          <select name="forma_pagamento" required${disabledReceive}>
            <option value="">selecione…</option>
            <option>Pix</option><option>Dinheiro</option><option>Cartão</option><option>Outro</option>
          </select>
        </label>
        <button type="submit"${disabledReceive}>Registrar recibo mensal único</button>
      </form>
    </details>`;
  }

  function renderFull(data) {
    const root = document.getElementById(ROOT);
    if (!root) return;
    const canManage = m15().can("gestor");
    const groups = data.grupos_elegiveis || [];
    const settlements = data.fechamentos || [];
    root.innerHTML = `<section class="pastore-settlement-panel" aria-labelledby="pastoreSettlementTitle">
      <header>
        <div>
          <p class="eyebrow">Financeiro da parceria</p>
          <h3 id="pastoreSettlementTitle">Fechamento mensal Pastore</h3>
          <p>Competência pelo mês do exame. Nenhum preço ou repasse é inferido.
             Exame realizado depois de o mês já ter fechado entra num fechamento
             complementar — o valor já conferido nunca é reescrito.</p>
        </div>
        <button type="button" data-pastore-refresh>Atualizar</button>
      </header>
      <div class="pastore-live-status" role="status" aria-live="polite"></div>
      ${indicatorHtml(data)}
      <div class="pastore-settlement-grid">
        <article>
          <h4>Aguardando fechamento mensal</h4>
          ${groups.length ? groups.map((g) => `<div class="pastore-eligible-group">
            <div><strong>${esc(g.unidade)}</strong>
              <span>Competência ${esc(g.competencia)} · ${esc(g.quantidade)} exame(s)</span>
              ${g.acao_prevista === "complementar" ? `<small class="pastore-eligible-note">
                A competência já tem ${esc(g.fechamentos_existentes)} fechamento(s) com valor
                conferido. Estes exames entram num fechamento complementar, com valor próprio.
              </small>` : ""}
              ${g.acao_prevista === "incorporar" ? `<small class="pastore-eligible-note">
                Entram no fechamento já aberto desta competência, que ainda não tem valor confirmado.
              </small>` : ""}
            </div>
            ${canManage ? `<button type="button" data-pastore-create
              data-unit="${esc(g.partner_unit_id)}" data-month="${esc(g.competencia)}">
              ${esc(g.acao_rotulo || "Criar fechamento")}
            </button>` : ""}
          </div>`).join("") : empty("Nenhum exame concluído aguardando fechamento.")}
        </article>
        <article>
          <h4>Fechamentos</h4>
          ${settlements.length ? settlements.map((row) => `<div class="pastore-settlement-row">
            <div class="pastore-settlement-row-head">
              <div><strong>${esc(row.unidade && row.unidade.nome)}</strong>
                <span>${esc(row.competencia)}${row.complementar
                  ? " · complementar " + esc(row.sequencia) : ""} · ${esc(row.itens.total)} exame(s)</span></div>
              <span class="pastore-status pastore-status-${esc(row.status)}">${esc(row.status_label)}</span>
            </div>
            <dl>
              <div><dt>Valor confirmado</dt><dd>${brl(row.valor_total)}</dd></div>
              <div><dt>Recibo</dt><dd>${row.recebimento ? esc(row.recebimento.public_code) : "—"}</dd></div>
              <div><dt>Exames</dt><dd>${esc(row.itens.exames_public_codes.join(", "))}</dd></div>
            </dl>
            ${settlementActions(row, canManage)}
          </div>`).join("") : empty("Nenhum fechamento mensal criado.")}
        </article>
      </div>
    </section>`;
  }

  function setStatus(message, error) {
    const el = document.querySelector(`#${ROOT} .pastore-live-status`);
    if (!el) return;
    el.textContent = message || "";
    el.className = "pastore-live-status" + (error ? " is-error" : "");
  }

  function load() {
    const root = document.getElementById(ROOT);
    const fin = document.getElementById(FIN_ROOT);
    if (!root && !fin) return Promise.resolve();
    if (!m15() || !m15().hasToken()) {
      if (root) root.innerHTML = empty("Entre no Núcleo M15 para consultar os fechamentos Pastore.");
      if (fin) fin.innerHTML = "";
      return Promise.resolve();
    }
    if (root) root.innerHTML = empty("Carregando fechamentos Pastore…");
    return api("/pastore/fechamentos").then((data) => {
      if (fin) fin.innerHTML = indicatorHtml(data);
      renderFull(data);
      return data;
    }).catch((err) => {
      const message = (err && err.message) || String(err);
      if (root) root.innerHTML = empty("Fechamentos Pastore indisponíveis: " + message);
      if (fin) fin.innerHTML = "";
    });
  }

  function postJson(path, body, method) {
    return api(path, { method: method || "POST", body: JSON.stringify(body) });
  }

  document.addEventListener("click", (event) => {
    const refresh = event.target.closest && event.target.closest("[data-pastore-refresh]");
    if (refresh) {
      load();
      return;
    }
    const create = event.target.closest && event.target.closest("[data-pastore-create]");
    if (!create) return;
    create.disabled = true;
    setStatus("Criando fechamento…");
    postJson("/pastore/fechamentos", {
      partner_unit_id: create.dataset.unit,
      competencia: create.dataset.month,
    }).then((data) => {
      const n = (data && data.exames_adicionados) || 0;
      setStatus(data && data.acao === "incorporado"
        ? `${n} exame(s) incluídos no fechamento aberto. Falta confirmar o valor mensal.`
        : `Fechamento criado com ${n} exame(s). Falta confirmar o valor mensal.`);
      return load();
    }).catch((err) => {
      create.disabled = false;
      setStatus((err && err.message) || String(err), true);
    });
  });

  document.addEventListener("submit", (event) => {
    const update = event.target.closest && event.target.closest("[data-pastore-update]");
    const receive = event.target.closest && event.target.closest("[data-pastore-receive]");
    if (!update && !receive) return;
    event.preventDefault();
    const form = update || receive;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    if (update) {
      const body = {
        status: form.elements.status.value,
        data_envio: form.elements.data_envio.value || null,
      };
      const value = parseMoney(form.elements.valor_total.value);
      if (value) body.valor_total = value;
      setStatus("Atualizando fechamento…");
      postJson(`/pastore/fechamentos/${update.dataset.pastoreUpdate}`, body, "PATCH")
        .then(() => load())
        .catch((err) => {
          button.disabled = false;
          setStatus((err && err.message) || String(err), true);
        });
      return;
    }
    const value = parseMoney(form.elements.valor_confirmado.value);
    if (!value) {
      button.disabled = false;
      setStatus("Informe um valor recebido válido.", true);
      return;
    }
    setStatus("Registrando recibo mensal…");
    postJson(`/pastore/fechamentos/${receive.dataset.pastoreReceive}/receber`, {
      valor_confirmado: value,
      data_recebimento: form.elements.data_recebimento.value,
      forma_pagamento: form.elements.forma_pagamento.value,
      idempotency_key: m15().idemKey(),
    }).then(() => load()).catch((err) => {
      button.disabled = false;
      setStatus((err && err.message) || String(err), true);
    });
  });

  document.addEventListener("soprolife:cadastro", load);
  if (m15()) m15().onSessionChange(load);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }

  window.SoproPastoreSettlement = { refresh: load };
})();
