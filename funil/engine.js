/* =========================================================================
   Sopro Life — Motor dos funis (genérico)
   Renderiza qualquer funil definido em flows.js. Não precisa editar para
   adicionar funis novos — apenas crie o flow e a página HTML.
   ========================================================================= */
(function () {
  "use strict";

  var cfg = window.FUNNEL_CONFIG || {};
  var flowName = document.body.getAttribute("data-flow") || "geral";
  var FLOWS = window.FUNNEL_FLOWS || {};
  var flow = FLOWS[flowName] || FLOWS.geral;

  if (!flow) { console.error("Funil não encontrado:", flowName); return; }

  /* Aplica a cor de destaque do funil */
  if (flow.accent) document.documentElement.style.setProperty("--accent", flow.accent);
  if (flow.accentDark) document.documentElement.style.setProperty("--accent-dark", flow.accentDark);
  document.body.setAttribute("data-funnel-active", flowName);

  /* Rótulos amigáveis para a mensagem do WhatsApp e o resumo */
  var LABELS = {
    perfil: "Perfil",
    objetivo: "Interesse",
    pedido: "Pedido médico",
    local: "Local / região",
    empresa: "Empresa/Instituição",
    nome: "Nome",
    whatsapp: "WhatsApp",
    bairro: "Bairro/região",
    melhorHorario: "Melhor horário",
    observacao: "Observação"
  };

  var MESSAGE_ORDER = ["nome", "empresa", "whatsapp", "perfil", "objetivo", "pedido", "local", "bairro", "melhorHorario", "observacao"];

  var state = {};
  var historyStack = [];
  var current = flow.steps[0].id;
  var finalId = flow.steps[flow.steps.length - 1].id;
  var estimated = flow.estimatedSteps || Math.max(4, flow.steps.length - 2);

  var quiz = document.getElementById("quiz");
  var bar = document.getElementById("progressBar");
  var stepLabel = document.getElementById("stepLabel");

  function stepById(id) {
    for (var i = 0; i < flow.steps.length; i++) if (flow.steps[i].id === id) return flow.steps[i];
    return null;
  }

  function escapeHTML(str) {
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function setProgress() {
    var pct = current === finalId
      ? 100
      : Math.min(92, Math.round(((historyStack.length + 1) / (estimated + 1)) * 100));
    bar.style.width = pct + "%";
    if (stepLabel) {
      stepLabel.textContent = current === finalId
        ? "Concluído ✓"
        : "Etapa " + (historyStack.length + 1);
    }
  }

  /* ---------- Renderização por tipo de etapa ---------- */

  function renderHeader(step) {
    var html = "";
    if (step.eyebrow) html += '<p class="eyebrow">' + step.eyebrow + "</p>";
    html += '<h1 tabindex="-1">' + step.title + "</h1>";
    if (step.lead) html += '<p class="lead">' + step.lead + "</p>";
    return html;
  }

  function renderOptions(step) {
    var html = '<div class="options">';
    for (var i = 0; i < step.options.length; i++) {
      var o = step.options[i];
      html += '<button class="option" data-idx="' + i + '">';
      if (o.icon) html += '<span class="option-icon" aria-hidden="true">' + o.icon + "</span>";
      html += '<span class="option-text"><span class="option-label">' + o.label + "</span>";
      if (o.desc) html += '<span class="option-desc">' + o.desc + "</span>";
      html += "</span><span class=\"option-arrow\" aria-hidden=\"true\">→</span>";
      html += "</button>";
    }
    html += "</div>";
    if (historyStack.length) {
      html += '<div class="actions"><button class="btn btn-ghost" type="button" data-back>← Voltar</button></div>';
    }
    return html;
  }

  function renderForm(step) {
    var html = '<form class="form" id="leadForm" novalidate>';
    for (var i = 0; i < step.fields.length; i++) {
      var f = step.fields[i];
      var req = f.required ? " required" : "";
      var ac = f.autocomplete ? ' autocomplete="' + f.autocomplete + '"' : "";
      var ph = f.placeholder ? ' placeholder="' + escapeHTML(f.placeholder) + '"' : "";
      var val = state[f.name] ? escapeHTML(state[f.name]) : "";
      html += "<label>" + f.label + (f.required ? ' <span class="req">*</span>' : "");
      if (f.type === "select") {
        html += '<select name="' + f.name + '">';
        html += '<option value="">Selecione</option>';
        for (var j = 0; j < f.options.length; j++) {
          var sel = state[f.name] === f.options[j] ? " selected" : "";
          html += "<option" + sel + ">" + f.options[j] + "</option>";
        }
        html += "</select>";
      } else if (f.type === "textarea") {
        html += '<textarea name="' + f.name + '"' + ph + ">" + val + "</textarea>";
      } else {
        var inputmode = f.type === "tel" ? ' inputmode="tel"' : "";
        html += '<input type="' + (f.type || "text") + '" name="' + f.name + '"' + req + ac + ph + inputmode + ' value="' + val + '" />';
      }
      html += "</label>";
    }
    html += '<div id="formError" class="error" hidden>Preencha os campos obrigatórios para continuar.</div>';
    html += '<div class="actions">';
    html += '<button class="btn btn-primary" type="submit"><span class="wa-icon" aria-hidden="true">●</span> ' + (step.submitLabel || "Enviar pelo WhatsApp") + "</button>";
    html += '<button class="btn btn-ghost" type="button" data-back>← Voltar</button>';
    html += "</div></form>";
    return html;
  }

  function renderFinal(step) {
    var html = '<div class="final-check" aria-hidden="true">✓</div>';
    html = renderHeader(step) + html;
    var rows = "";
    var keys = step.summaryKeys || [];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (state[k]) {
        rows += '<div class="sum-row"><span>' + (LABELS[k] || k) + "</span><strong>" + escapeHTML(state[k]) + "</strong></div>";
      }
    }
    if (rows) html += '<div class="summary">' + rows + "</div>";
    html += '<div class="actions">';
    html += '<a class="btn btn-whats" id="whatsLink" href="' + buildWhatsAppLink() + '" target="_blank" rel="noopener"><span class="wa-icon" aria-hidden="true">●</span> Abrir WhatsApp</a>';
    html += '<button class="btn btn-ghost" type="button" data-restart>↻ Começar de novo</button>';
    html += "</div>";
    return html;
  }

  function render(id) {
    current = id;
    var step = stepById(id);
    if (!step) return;
    setProgress();

    var inner;
    if (step.type === "form") inner = renderHeader(step) + renderForm(step);
    else if (step.type === "final") inner = renderFinal(step);
    else inner = renderHeader(step) + renderOptions(step);

    quiz.classList.remove("enter");
    quiz.innerHTML = inner;
    /* força reflow para reiniciar a animação */
    void quiz.offsetWidth;
    quiz.classList.add("enter");

    bindEvents(step);
    track("funnel_step", { funnel: flowName, step: id, index: historyStack.length + 1 });

    /* foco acessível no título */
    var h1 = quiz.querySelector("h1");
    if (h1) { try { h1.focus({ preventScroll: true }); } catch (e) {} }
  }

  /* ---------- Eventos ---------- */

  function bindEvents(step) {
    var opts = quiz.querySelectorAll(".option");
    for (var i = 0; i < opts.length; i++) {
      opts[i].addEventListener("click", function () {
        var o = step.options[parseInt(this.getAttribute("data-idx"), 10)];
        if (o.set) for (var k in o.set) if (o.set.hasOwnProperty(k)) state[k] = o.set[k];
        this.classList.add("selected");
        var btn = this;
        setTimeout(function () {
          historyStack.push(current);
          render(o.next);
        }, 140);
      });
    }

    var back = quiz.querySelector("[data-back]");
    if (back) back.addEventListener("click", function () {
      var prev = historyStack.pop() || flow.steps[0].id;
      render(prev);
    });

    var restart = quiz.querySelector("[data-restart]");
    if (restart) restart.addEventListener("click", function () {
      for (var k in state) if (state.hasOwnProperty(k)) delete state[k];
      historyStack.length = 0;
      render(flow.steps[0].id);
    });

    var form = quiz.querySelector("#leadForm");
    if (form) form.addEventListener("submit", function (e) {
      e.preventDefault();
      var ok = true;
      var fields = step.fields;
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i];
        var el = form.elements[f.name];
        if (el) state[f.name] = (el.value || "").trim();
        if (f.required && !state[f.name]) ok = false;
      }
      var err = form.querySelector("#formError");
      if (!ok) { if (err) err.hidden = false; return; }
      if (err) err.hidden = true;

      saveLead();
      track("funnel_complete", { funnel: flowName });
      historyStack.push(current);
      render(step.next);
    });

    var whats = quiz.querySelector("#whatsLink");
    if (whats) whats.addEventListener("click", function () {
      track("whatsapp_click", { funnel: flowName });
    });
  }

  /* ---------- Integrações ---------- */

  function saveLead() {
    if (!cfg.leadEndpoint) return;
    var payload = { origem: "funil-" + flowName, criadoEm: new Date().toISOString() };
    for (var k in state) if (state.hasOwnProperty(k)) payload[k] = state[k];
    try {
      fetch(cfg.leadEndpoint, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (err) { console.warn("Lead não salvo no endpoint:", err); }
  }

  function buildWhatsAppLink() {
    var lines = ["Olá, Sopro Life! Vim pelo funil (" + (flow.tagline || flowName) + ")."];
    lines.push("");
    for (var i = 0; i < MESSAGE_ORDER.length; i++) {
      var k = MESSAGE_ORDER[i];
      if (state[k]) lines.push((LABELS[k] || k) + ": " + state[k]);
    }
    lines.push("");
    lines.push("Gostaria de orientação para concluir o atendimento.");
    var msg = lines.join("\n");
    return "https://api.whatsapp.com/send?phone=" + (cfg.whatsapp || "") + "&text=" + encodeURIComponent(msg);
  }

  /* ---------- Rodapé dinâmico ---------- */
  var phoneEl = document.getElementById("phoneDisplay");
  if (phoneEl && cfg.phoneDisplay) phoneEl.textContent = cfg.phoneDisplay;

  /* ---------- Início ---------- */
  track("funnel_start", { funnel: flowName });
  render(flow.steps[0].id);
})();
