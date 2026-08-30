/* Conciliação de exames extra-Pastore (M18).
 *
 * Mostra o alvo histórico de conciliação bancária (controle de fechamento,
 * NÃO um saldo de conta corrente) e permite vincular exames extra-Pastore
 * sem lançamento a um valor exatamente evidenciado — nunca por média.
 * Reusa a sessão do Núcleo M15 (window.SoproM15); não duplica login.
 */
(function () {
  "use strict";

  const ROOT_ID = "conciliacaoExtraPastoreRoot";
  const SECTION_ID = "financeiro";

  const state = {
    dados: null,
    loading: false,
    erro: null,
    lote: {}, // exam_id -> valor digitado (string) durante conciliação em lote
    loteAberto: false,
  };

  function m15() { return window.SoproM15 || null; }
  function rootEl() { return document.getElementById(ROOT_ID); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const parts = iso.split("-");
    return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : iso;
  }

  function fmtMoney(v) {
    if (v == null) return "—";
    return "R$ " + Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "m15-toast" + (kind === "erro" ? " m15-toast-erro" : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }

  async function carregar() {
    const cli = m15();
    if (!cli || !cli.hasToken()) { state.dados = null; render(); return; }
    state.loading = true;
    state.erro = null;
    render();
    try {
      state.dados = await cli.api("/financeiro/conciliacao/extra-pastore");
    } catch (err) {
      state.erro = err.message || String(err);
      state.dados = null;
    } finally {
      state.loading = false;
      render();
    }
  }

  function render() {
    const root = rootEl();
    if (!root) return;
    const cli = m15();

    if (!cli || !cli.hasToken()) {
      root.innerHTML = `
        <article class="panel conciliacao-panel">
          <div class="panel-header">
            <h3>Conciliação de exames fora da Pastore</h3>
            <span>Requer sessão do Núcleo M15</span>
          </div>
          <p class="m15-muted">Entre pela Central de Cadastros ou pelo Núcleo administrativo (mesma sessão) para ver e conciliar os exames extra-Pastore.</p>
        </article>`;
      return;
    }

    if (state.loading && !state.dados) {
      root.innerHTML = `<article class="panel conciliacao-panel"><div class="crm-empty">Carregando conciliação…</div></article>`;
      return;
    }
    if (state.erro) {
      root.innerHTML = `<article class="panel conciliacao-panel"><div class="m15-erro">Erro ao carregar conciliação: ${esc(state.erro)}</div></article>`;
      return;
    }
    if (!state.dados) return;

    const d = state.dados;
    root.innerHTML = `
      <article class="panel conciliacao-panel">
        <div class="panel-header">
          <h3>Conciliação de exames fora da Pastore</h3>
          <span>${esc(d.rotulo)}</span>
        </div>
        <div class="conciliacao-kpis">
          <div class="kpi-card"><span class="kpi-label">Total histórico informado</span><strong class="kpi-value">${fmtMoney(d.total_alvo)}</strong></div>
          <div class="kpi-card"><span class="kpi-label">Receita vinculada a exames</span><strong class="kpi-value">${fmtMoney(d.total_vinculado)}</strong></div>
          <div class="kpi-card"><span class="kpi-label">Valor ainda a conciliar</span><strong class="kpi-value">${fmtMoney(d.total_pendente)}</strong></div>
          <div class="kpi-card"><span class="kpi-label">Exames conciliados</span><strong class="kpi-value">${d.exames_conciliados} de ${d.total_exames}</strong></div>
          <div class="kpi-card"><span class="kpi-label">Exames pendentes</span><strong class="kpi-value">${d.exames_pendentes}</strong></div>
          ${Number(d.total_alem_do_alvo || 0) > 0 ? `
          <div class="kpi-card"><span class="kpi-label">Receita além do alvo histórico</span><strong class="kpi-value">${fmtMoney(d.total_alem_do_alvo)}</strong></div>` : ""}
          <div class="kpi-card"><span class="kpi-label">Divergências</span><strong class="kpi-value">${d.exames_pendentes > 0 && Number(d.total_pendente) === 0 ? "1" : "0"}</strong></div>
        </div>
        ${Number(d.total_alem_do_alvo || 0) > 0 ? `
        <p class="conciliacao-nota">O alvo histórico de ${fmtMoney(d.total_alvo)} está integralmente
        conciliado. A diferença acima é receita de exames posteriores a esse fechamento — ela não
        pertence ao alvo e não indica erro.</p>` : ""}

        ${d.exames_pendentes > 0 ? `
        <div class="conciliacao-actions">
          <button type="button" class="m15-btn m15-btn-sec" id="conciliacaoToggleLote">${state.loteAberto ? "Fechar conciliação em lote" : "Conciliar em lote"}</button>
        </div>` : ""}

        ${state.loteAberto ? renderLote(d) : ""}

        <div class="table-wrap">
          <table>
            <thead><tr><th>Exame</th><th>Data</th><th>Local</th><th>Status</th><th>Valor</th><th>Ação</th></tr></thead>
            <tbody>
              ${d.pendentes.map((e) => `
                <tr>
                  <td>${esc(e.exam_public_code)}</td>
                  <td>${fmtDate(e.data_exame)}</td>
                  <td>${esc(e.local_atendimento || "—")}</td>
                  <td><span class="badge">Sem lançamento</span></td>
                  <td>—</td>
                  <td><button type="button" class="m15-btn m15-btn-sec conciliar-um-btn" data-exam-id="${esc(e.exam_id)}">Conciliar valor</button></td>
                </tr>`).join("")}
              ${d.conciliados.map((e) => `
                <tr>
                  <td>${esc(e.exam_public_code)}</td>
                  <td>${fmtDate(e.data_exame)}</td>
                  <td>${esc(e.local_atendimento || "—")}</td>
                  <td><span class="badge badge-ok">${esc(e.financial_public_code)}</span></td>
                  <td>${fmtMoney(e.valor)}</td>
                  <td>—</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </article>`;

    root.querySelector("#conciliacaoToggleLote")?.addEventListener("click", () => {
      state.loteAberto = !state.loteAberto;
      render();
    });
    root.querySelectorAll(".conciliar-um-btn").forEach((btn) => {
      btn.addEventListener("click", () => abrirConciliarUm(btn.dataset.examId));
    });
    root.querySelector("#loteSubmitBtn")?.addEventListener("click", submitLote);
    root.querySelectorAll(".lote-valor-input").forEach((inp) => {
      inp.addEventListener("input", () => {
        state.lote[inp.dataset.examId] = inp.value;
        atualizarSomaLote();
      });
    });
  }

  function renderLote(d) {
    return `
      <div class="conciliacao-lote">
        <p class="m15-muted">Digite o valor exato de cada exame (nunca uma média). O envio só é permitido quando a soma bater exatamente com o pendente.</p>
        <table class="conciliacao-lote-table">
          <thead><tr><th>Exame</th><th>Data</th><th>Valor (R$)</th></tr></thead>
          <tbody>
            ${d.pendentes.map((e) => `
              <tr>
                <td>${esc(e.exam_public_code)}</td>
                <td>${fmtDate(e.data_exame)}</td>
                <td><input type="number" step="0.01" min="0" class="lote-valor-input" data-exam-id="${esc(e.exam_id)}" placeholder="0.00"></td>
              </tr>`).join("")}
          </tbody>
        </table>
        <div class="conciliacao-lote-total">
          <span>Soma digitada: <strong id="loteSomaDigitada">R$ 0,00</strong></span>
          <span>Pendente atual: <strong>${fmtMoney(d.total_pendente)}</strong></span>
        </div>
        <button type="button" class="m15-btn" id="loteSubmitBtn" disabled>Confirmar conciliação em lote</button>
      </div>`;
  }

  function atualizarSomaLote() {
    const somaEl = document.getElementById("loteSomaDigitada");
    const btn = document.getElementById("loteSubmitBtn");
    if (!somaEl || !state.dados) return;
    let soma = 0;
    Object.values(state.lote).forEach((v) => { soma += Number(v || 0); });
    soma = Math.round(soma * 100) / 100;
    somaEl.textContent = fmtMoney(soma);
    const pendente = Number(state.dados.total_pendente);
    const bate = Math.abs(soma - pendente) < 0.001 && Object.keys(state.lote).length > 0;
    if (btn) btn.disabled = !bate;
  }

  async function submitLote() {
    const cli = m15();
    if (!cli || !state.dados) return;
    const itens = Object.entries(state.lote)
      .filter(([, v]) => v !== "" && v != null)
      .map(([exam_id, valor]) => ({ spirometry_exam_id: exam_id, valor: Number(valor).toFixed(2) }));
    if (!itens.length) return;
    if (!window.confirm(`Confirma a conciliação em lote de ${itens.length} exame(s)? Esta ação não pode ser desfeita.`)) return;
    const btn = document.getElementById("loteSubmitBtn");
    if (btn) btn.disabled = true;
    try {
      await cli.api("/financeiro/conciliacao/extra-pastore/lote", {
        method: "POST",
        body: JSON.stringify({ itens, total_esperado: state.dados.total_pendente }),
      });
      toast("Conciliação em lote registrada.");
      state.lote = {};
      state.loteAberto = false;
      await carregar();
    } catch (err) {
      toast("Erro na conciliação em lote: " + (err.message || err), "erro");
      if (btn) btn.disabled = false;
    }
  }

  function abrirConciliarUm(examId) {
    const item = state.dados.pendentes.find((e) => e.exam_id === examId);
    if (!item) return;
    const overlay = document.createElement("div");
    overlay.className = "m15-modal-overlay";
    overlay.innerHTML = `
      <div class="m15-modal">
        <h3>Conciliar ${esc(item.exam_public_code)}</h3>
        <p class="m15-muted">${fmtDate(item.data_exame)} · ${esc(item.local_atendimento || "—")}</p>
        <label class="m15-field"><span class="m15-field-label">Valor exato recebido (R$)</span>
          <input type="number" step="0.01" min="0" id="conciliarUmValor"></label>
        <label class="m15-field"><span class="m15-field-label">Forma de pagamento</span>
          <select id="conciliarUmForma">
            <option value="Pix">Pix</option>
            <option value="Dinheiro">Dinheiro</option>
            <option value="Cartão">Cartão</option>
            <option value="Outro">Outro</option>
          </select></label>
        <div class="m15-modal-actions">
          <button type="button" class="m15-btn m15-btn-sec" id="conciliarUmCancelar">Cancelar</button>
          <button type="button" class="m15-btn" id="conciliarUmConfirmar">Confirmar</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#conciliarUmCancelar").addEventListener("click", () => overlay.remove());
    overlay.querySelector("#conciliarUmConfirmar").addEventListener("click", async () => {
      const valor = overlay.querySelector("#conciliarUmValor").value;
      const forma = overlay.querySelector("#conciliarUmForma").value;
      if (!valor || Number(valor) <= 0) { toast("Informe um valor válido.", "erro"); return; }
      if (!window.confirm(`Confirma o lançamento de R$ ${valor} para ${item.exam_public_code}?`)) return;
      try {
        await m15().api("/lancamentos", {
          method: "POST",
          body: JSON.stringify({
            tipo: "receita", categoria: "Espirometria",
            valor: Number(valor).toFixed(2), status: "Recebido",
            forma_pagamento: forma, spirometry_exam_id: item.exam_id,
          }),
        });
        toast("Exame conciliado.");
        overlay.remove();
        await carregar();
      } catch (err) {
        toast("Erro ao conciliar: " + (err.message || err), "erro");
      }
    });
  }

  function boot() {
    const navBtn = document.querySelector(`.nav-item[data-section="${SECTION_ID}"]`);
    if (navBtn) navBtn.addEventListener("click", carregar);
    const tryBind = () => {
      const cli = m15();
      if (cli) {
        cli.onSessionChange(carregar);
        if (document.getElementById(SECTION_ID)?.classList.contains("active")) carregar();
      } else {
        setTimeout(tryBind, 300);
      }
    };
    tryBind();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
