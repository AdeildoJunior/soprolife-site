/* Núcleo Operacional M15 (beta — implantação controlada) — módulo isolado e reversível.
 *
 * - Feature flag: data/m15-config.json (enabled) OU localStorage soproM15='on'.
 *   Com enabled=true (go-live M15.5A) o núcleo aparece para todos, sem opt-in
 *   local; enabled=false volta o painel exatamente ao estado anterior.
 * - Contexto seguro (M15.5A): o login com credenciais reais só é aceito em
 *   HTTPS ou em loopback de desenvolvimento (js/m15-security.js). Em origem
 *   HTTP remota o formulário de senha/token NÃO é renderizado e nenhuma
 *   requisição de autenticação é enviada (fail-closed, inclusive quando a
 *   guarda não carregou). O restante do painel legado continua acessível.
 * - Backend: proxy de mesma origem → API própria em loopback
 *   (FastAPI + PostgreSQL/SQLite), ver nucleo-m15/README.md.
 * - Sessão: login por e-mail+senha (POST /auth/token) ou token colado
 *   (CLI emitir-token). Token mantido SOMENTE EM MEMÓRIA (nunca em
 *   localStorage/sessionStorage — reduz o impacto de XSS). Recarregar a
 *   página exige entrar de novo.
 * - Papéis: a UI oculta ações fora do papel (via /auth/me), mas a
 *   autorização REAL é sempre do servidor (require_role).
 * - Busca de pessoas via POST (corpo) — nome nunca vai para a URL/logs.
 * - WhatsApp: sempre revisão humana; NUNCA dispara envio automático;
 *   botão só aparece quando a API confirma whatsapp_permitido.
 */
(function () {
  "use strict";

  var CONFIG_URL = "data/m15-config.json";
  var state = {
    apiBase: "/painel-soprolife/api/m15",
    tab: "visao",
    filters: {},
    token: "", // em memória apenas; some ao recarregar (decisão de segurança)
    user: null, // {id, nome, papeis, papeis_efetivos} via /auth/me
    // Contexto de acesso (M15.5A) — fail-closed: nasce bloqueado e só muda
    // depois que a guarda js/m15-security.js classifica a origem real.
    access: { mode: "blocked", secure: false, motivo: "guarda de segurança indisponível (fail-closed)" },
  };

  function classifyAccess() {
    var gate = (typeof window !== "undefined" && window.SoproM15Security) || null;
    if (gate && typeof gate.classify === "function") {
      state.access = gate.classify(window.location);
    }
    return state.access;
  }

  function blockedMessage() {
    var gate = (typeof window !== "undefined" && window.SoproM15Security) || null;
    return (gate && gate.MENSAGEM_BLOQUEIO) ||
      "Origem HTTP insegura: o login do Núcleo M15 fica desativado. " +
      "Abra o endereço HTTPS privado do painel para entrar.";
  }

  function blockedNotice() {
    return '<div class="m15-block" role="alert"><strong>Acesso HTTP inseguro — login desativado.</strong> ' +
      esc(blockedMessage()) + "</div>";
  }

  function getToken() { return state.token; }

  function can(role) {
    // Sem identidade resolvida (ex.: /auth/me falhou), não esconde nada:
    // o servidor continua negando o que o papel não permite (fail-closed lá).
    if (!state.user || !state.user.papeis_efetivos) return true;
    return state.user.papeis_efetivos.indexOf(role) !== -1;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var parts = iso.split("-");
    if (parts.length !== 3) return iso;
    return parts[2] + "/" + parts[1] + "/" + parts[0];
  }

  function isSynthetic(item) {
    if (!item) return false;
    if (item.legacy_source === "seed_demo") return true;
    var obs = item.observacao || item.observacao_operacional || "";
    return obs.indexOf("SINTÉTICO") !== -1;
  }

  function sintBadge(item) {
    return isSynthetic(item)
      ? ' <span class="m15-badge-sint" title="Dado sintético de demonstração">sintético</span>'
      : "";
  }

  function api(path, options) {
    // Guarda de contexto seguro (M15.5A): em origem bloqueada NENHUMA
    // requisição de autenticação sai do navegador — nem senha, nem token.
    if (!state.access.secure && path.indexOf("/auth/") === 0) {
      return Promise.reject(new Error(
        "Login bloqueado em origem HTTP insegura. Abra o endereço HTTPS privado do painel."
      ));
    }
    options = options || {};
    options.headers = Object.assign({
      "Authorization": "Bearer " + getToken(),
      "Content-Type": "application/json",
    }, options.headers || {});
    return fetch(state.apiBase + path, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var msg = (body && body.erro && body.erro.mensagem) || ("HTTP " + resp.status);
          var err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  // ------------------------------------------------------------ utilitários

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "m15-toast" + (kind === "erro" ? " m15-toast-erro" : "");
    el.textContent = msg; // textContent: nunca interpreta HTML
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "erro" ? 7000 : 4000);
  }

  function submitVia(form, factory, successMsg) {
    var btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    factory().then(function () {
      toast(successMsg || "Salvo com sucesso.");
      render();
    }).catch(function (err) {
      if (btn) btn.disabled = false;
      toast("Erro: " + (err.message || err), "erro");
    });
  }

  function val(form, name) {
    var field = form.elements[name];
    if (!field) return "";
    if (field.type === "checkbox") return field.checked;
    return (field.value || "").trim();
  }

  function setIf(payload, key, value) {
    if (value !== "" && value != null) payload[key] = value;
  }

  function idemKey() {
    // chave de idempotência da UI (anti duplo clique) — não é segredo
    var arr = new Uint8Array(12);
    if (window.crypto && crypto.getRandomValues) {
      crypto.getRandomValues(arr);
    } else {
      for (var i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
    }
    return "ui-" + Array.prototype.map.call(arr, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }

  function moneyStr(raw) {
    var n = Number(String(raw).replace(",", "."));
    if (!isFinite(n)) return "";
    return n.toFixed(2);
  }

  // Resolve "PES-000001" ou nome em UMA pessoa; ambiguidade exige o código.
  function resolvePerson(value) {
    var v = (value || "").trim();
    if (!v) return Promise.reject(new Error("Informe a pessoa (PES-… ou nome)."));
    return api("/pessoas/busca", {
      method: "POST", body: JSON.stringify({ q: v, tamanho: 5 }),
    }).then(function (data) {
      if (data.total === 0) {
        throw new Error("Pessoa não encontrada — confira o código PES-… na aba Pessoas.");
      }
      if (data.total === 1) return data.itens[0];
      var exact = data.itens.filter(function (p) {
        return p.public_code === v.toUpperCase();
      });
      if (exact.length === 1) return exact[0];
      throw new Error("Mais de uma pessoa corresponde — use o código PES-… exato.");
    });
  }

  function optionsHtml(list, selected) {
    return list.map(function (o) {
      var value = Array.isArray(o) ? o[0] : o;
      var label = Array.isArray(o) ? o[1] : (o || "—");
      return '<option value="' + esc(value) + '"' +
        (String(value) === String(selected == null ? "" : selected) ? " selected" : "") +
        ">" + esc(label) + "</option>";
    }).join("");
  }

  // Campo do sistema compartilhado de formulários (M15.4A).
  // opt: true = linha inteira (compat), número = colunas do grid de 12,
  // objeto = { span, help } — rótulo tem SEMPRE 1 linha (texto completo no
  // title); dica longa vai para o help abaixo do controle, assim os
  // controles vizinhos ficam alinhados na mesma linha de base.
  function fld(label, inner, opt) {
    var span = 3, help = "";
    if (opt === true) span = 12;
    else if (typeof opt === "number") span = opt;
    else if (opt && typeof opt === "object") {
      span = opt.span || 3;
      help = opt.help || "";
    }
    var classes = "m15-field " + (span >= 12 ? "m15-form-full" : "m15-span-" + span);
    return '<label class="' + classes + '"><span class="m15-field-label" title="' +
      esc(label) + '">' + esc(label) + "</span>" + inner +
      (help ? '<span class="m15-field-help">' + esc(help) + "</span>" : "") +
      "</label>";
  }

  // Grupo visual: campos relacionais/opcionais coesos; cls "m15-group-tech"
  // marca IDs técnicos (disponíveis, porém visualmente secundários).
  function grp(title, inner, cls) {
    return '<div class="m15-group' + (cls ? " " + cls : "") + '">' +
      '<span class="m15-group-title">' + esc(title) + "</span>" + inner + "</div>";
  }

  function inp(name, value, attrs) {
    return '<input name="' + esc(name) + '" value="' + esc(value == null ? "" : value) +
      '" ' + (attrs || "") + ">";
  }

  // Campo de data do calendário SoproLife. O valor no payload permanece no
  // formato do backend (AAAA-MM-DD; parcial aceita também AAAA-MM e AAAA).
  function dateInp(name, value, opts) {
    opts = opts || {};
    var attrs = 'data-m15-date="' + (opts.parcial ? "partial" : "full") + '"';
    if (!opts.parcial) attrs += ' type="date"';
    if (opts.required) attrs += " required";
    return inp(name, value, attrs);
  }

  var HELP_DATA_PARCIAL = "Aceita dia (DD/MM/AAAA), mês (MM/AAAA) ou só ano (AAAA).";

  function attachDates(root) {
    if (window.SoproM15DatePicker) {
      window.SoproM15DatePicker.attachAll(root || body());
    }
  }

  function sel(name, list, selected, attrs) {
    return '<select name="' + esc(name) + '" ' + (attrs || "") + ">" +
      optionsHtml(list, selected) + "</select>";
  }

  function partnerOptions() {
    return api("/parceiros?tamanho=100").then(function (d) {
      return d.itens.map(function (p) { return [p.id, p.public_code + " — " + p.nome]; });
    });
  }

  function unitOptions(partnerId) {
    if (!partnerId) return Promise.resolve([]);
    return api("/unidades?tamanho=100&partner_id=" + encodeURIComponent(partnerId))
      .then(function (d) {
        return d.itens.map(function (u) { return [u.id, u.public_code + " — " + u.nome]; });
      });
  }

  // Select de unidade dependente do parceiro escolhido no mesmo formulário.
  function wireUnitSelect(form, partnerField, unitField) {
    var pSel = form.elements[partnerField];
    var uSel = form.elements[unitField];
    if (!pSel || !uSel) return;
    pSel.addEventListener("change", function () {
      uSel.innerHTML = optionsHtml([["", "sem unidade"]], "");
      unitOptions(pSel.value).then(function (units) {
        uSel.innerHTML = optionsHtml([["", "sem unidade"]].concat(units), "");
      }).catch(function () { /* mantém "sem unidade" */ });
    });
  }

  function editBox() { return document.getElementById("m15EditBox"); }

  function openEdit(html, wire) {
    var box = editBox();
    if (!box) return;
    box.innerHTML = html;
    if (wire) wire(box);
    attachDates(box);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function closeEditButton() {
    return '<button class="m15-btn m15-btn-sec" type="button" data-m15-fechar>Fechar</button>';
  }

  function wireClose(box) {
    var btn = box.querySelector("[data-m15-fechar]");
    if (btn) btn.addEventListener("click", function () { box.innerHTML = ""; });
  }

  // ------------------------------------------------------------ estrutura

  // [id, rótulo, papel mínimo p/ VER a aba] — o servidor revalida tudo.
  var TABS = [
    ["visao", "Visão geral", "leitura"],
    ["pessoas", "Pessoas", "leitura"],
    ["leads", "Leads", "leitura"],
    ["espirometrias", "Espirometrias", "leitura"],
    ["consultas", "Consultas", "leitura"],
    ["parceiros", "Clínicas e Parceiros", "leitura"],
    ["encaminhamentos", "Pacientes de Parceiros", "leitura"],
    ["followup", "Follow-up", "leitura"],
    ["financeiro", "Financeiro", "leitura"],
    ["migracao", "Migração", "leitura"],
    ["auditoria", "Auditoria", "gestor"],
    ["admin", "Administração", "admin"],
  ];

  // Selo honesto do modo de acesso — distingue HTTPS seguro, desenvolvimento
  // local e HTTP remoto bloqueado. IP privado (ex.: Tailscale 100.x) via HTTP
  // NUNCA é anunciado como seguro.
  function accessBadge() {
    if (state.access.mode === "https") {
      return '<span class="m15-badge-access m15-badge-https" title="Página servida por HTTPS (acesso privado via Tailscale Serve)">Acesso seguro (HTTPS)</span>';
    }
    if (state.access.mode === "localdev") {
      return '<span class="m15-badge-access m15-badge-dev" title="Loopback local — tráfego não sai desta máquina">Desenvolvimento local</span>';
    }
    return '<span class="m15-badge-access m15-badge-blocked" title="' +
      esc(blockedMessage()) + '">HTTP inseguro — login bloqueado</span>';
  }

  function buildSection() {
    var section = document.createElement("section");
    section.id = "m15-nucleo";
    section.className = "section";
    section.innerHTML =
      '<div class="m15-header">' +
      '  <h2>Núcleo Operacional' +
      '    <span class="m15-badge-exp">M15 Beta</span>' +
      '    <span class="m15-badge-ctrl">implantação controlada</span>' +
      accessBadge() +
      "  </h2>" +
      "</div>" +
      '<div class="m15-panel"><div id="m15AuthArea"></div></div>' +
      '<div class="m15-tabs" id="m15Tabs"></div>' +
      '<div id="m15Body"><div class="m15-empty">Carregando…</div></div>';
    return section;
  }

  function navButton() {
    var btn = document.createElement("button");
    btn.className = "nav-item";
    btn.setAttribute("data-section", "m15-nucleo");
    btn.innerHTML =
      '<svg class="nav-icon" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="9" cy="9" r="7"/><circle cx="9" cy="9" r="2.6"/><path d="M9 2v2.5M9 13.5V16M2 9h2.5M13.5 9H16"/></svg>' +
      "<span>Núcleo M15</span>";
    btn.addEventListener("click", function () {
      document.querySelectorAll(".nav-item").forEach(function (item) { item.classList.remove("active"); });
      document.querySelectorAll(".section").forEach(function (sec) { sec.classList.remove("active"); });
      btn.classList.add("active");
      document.getElementById("m15-nucleo").classList.add("active");
      render();
    });
    return btn;
  }

  // ------------------------------------------------------------ autenticação

  function renderAuthArea() {
    var area = document.getElementById("m15AuthArea");
    if (!area) return;
    if (!state.access.secure) {
      // Origem bloqueada: nada de campo de senha, nada de campo de token,
      // nenhuma credencial jamais é pedida ou enviada a partir daqui.
      area.innerHTML = blockedNotice();
      return;
    }
    if (state.token && state.user) {
      area.innerHTML =
        '<div class="m15-session">' +
        '  <span class="m15-session-user"><strong>Conectado:</strong> ' + esc(state.user.nome) +
        '    <span class="m15-pill">' + esc((state.user.papeis || []).join(", ")) + "</span></span>" +
        '  <button class="m15-btn m15-btn-sec" id="m15Sair">Sair</button>' +
        '  <span class="m15-muted" id="m15ApiStatus"></span>' +
        '  <span class="m15-muted">Sessão só em memória — recarregou, entre de novo.</span>' +
        "</div>";
      document.getElementById("m15Sair").addEventListener("click", function () {
        state.token = "";
        state.user = null;
        renderAuthArea();
        renderTabs();
        render();
      });
    } else {
      area.innerHTML =
        '<div class="m15-login">' +
        '  <div class="m15-login-grid">' +
        '    <label class="m15-field"><span class="m15-field-label">E-mail</span>' +
        '      <input type="email" id="m15Email" placeholder="voce@soprolife.com.br" autocomplete="off"></label>' +
        '    <label class="m15-field"><span class="m15-field-label">Senha</span>' +
        '      <input type="password" id="m15Senha" placeholder="••••••••••" autocomplete="off"></label>' +
        '    <button class="m15-btn" id="m15Entrar">Entrar</button>' +
        "  </div>" +
        '  <span class="m15-muted m15-login-status" id="m15ApiStatus">Proxy/API: verificando…</span>' +
        '  <details class="m15-login-alt">' +
        '    <summary>Entrar com token da CLI (avançado)</summary>' +
        '    <div class="m15-login-grid">' +
        '      <label class="m15-field"><span class="m15-field-label">Token da API ' +
        "(python -m app.cli emitir-token)</span>" +
        '        <input type="password" id="m15Token" placeholder="cole o token da API aqui" autocomplete="off"></label>' +
        '      <button class="m15-btn m15-btn-sec" id="m15TokenSave">Usar token</button>' +
        "    </div>" +
        '    <span class="m15-muted">O token vive apenas em memória — nunca em localStorage/sessionStorage.</span>' +
        "  </details>" +
        "</div>";
      var doLogin = function () {
        var email = document.getElementById("m15Email").value.trim();
        var senha = document.getElementById("m15Senha").value;
        if (!email || !senha) { toast("Informe e-mail e senha.", "erro"); return; }
        var btn = document.getElementById("m15Entrar");
        btn.disabled = true;
        // senha só trafega no corpo do POST via proxy local — nunca em URL
        api("/auth/token", {
          method: "POST", body: JSON.stringify({ email: email, password: senha }),
        }).then(function (resp) {
          state.token = resp.token;
          document.getElementById("m15Senha").value = "";
          return afterAuth();
        }).catch(function (err) {
          btn.disabled = false;
          toast("Erro no login: " + (err.message || err), "erro");
        });
      };
      document.getElementById("m15Entrar").addEventListener("click", doLogin);
      document.getElementById("m15Senha").addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") doLogin();
      });
      document.getElementById("m15TokenSave").addEventListener("click", function () {
        var tokenInput = document.getElementById("m15Token");
        // token vive só na variável de módulo — nunca em storage persistente
        state.token = tokenInput.value.trim();
        tokenInput.value = "";
        if (!state.token) { toast("Cole um token primeiro.", "erro"); return; }
        afterAuth();
      });
      checkApi();
    }
  }

  function afterAuth() {
    return api("/auth/me").then(function (me) {
      state.user = me;
    }).catch(function () {
      state.user = null; // segue sem ocultar ações; servidor decide (403)
    }).then(function () {
      renderAuthArea();
      renderTabs();
      render();
    });
  }

  function renderTabs() {
    var wrap = document.getElementById("m15Tabs");
    if (!wrap) return;
    var visible = TABS.filter(function (t) { return can(t[2]); });
    if (!visible.some(function (t) { return t[0] === state.tab; })) state.tab = "visao";
    wrap.innerHTML = visible.map(function (t) {
      return '<button class="m15-tab' + (t[0] === state.tab ? " active" : "") +
        '" data-m15-tab="' + t[0] + '">' + t[1] + "</button>";
    }).join("");
  }

  // ------------------------------------------------------------ renderização

  function body() { return document.getElementById("m15Body"); }

  function showError(err) {
    var hint = err && err.status === 401
      ? " Entre com e-mail e senha (ou um token válido) e tente de novo."
      : (err && err.message && err.message.indexOf("fetch") !== -1
        ? " O proxy e a API loopback estão rodando? Veja nucleo-m15/README.md." : "");
    body().innerHTML = '<div class="m15-erro">Erro: ' + esc(err.message || err) + hint + "</div>";
  }

  function cards(defs) {
    return '<div class="m15-cards">' + defs.map(function (c) {
      return '<div class="m15-card ' + (c.cls || "") + '">' +
        '<div class="m15-card-label">' + esc(c.label) + "</div>" +
        '<div class="m15-card-value">' + esc(c.value) + "</div></div>";
    }).join("") + "</div>";
  }

  function table(headers, rows, emptyMsg) {
    if (!rows.length) {
      return '<div class="m15-empty">' + esc(emptyMsg || "Nenhum registro ainda.") + "</div>";
    }
    return '<div class="m15-table-wrap"><table class="m15-table"><thead><tr>' +
      headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }

  function pill(value) {
    var key = String(value || "").replace(/\s+/g, "_");
    return '<span class="m15-pill ' + esc(key) + '">' + esc(value || "—") + "</span>";
  }

  function editBtn(id) {
    return '<button class="m15-btn m15-btn-sec" data-m15-edit="' + esc(id) + '">Editar</button>';
  }

  function byId(items) {
    var map = {};
    items.forEach(function (i) { map[i.id] = i; });
    return map;
  }

  var loaders = {};

  // ------------------------------------------------------------ visão geral

  loaders.visao = function () {
    return Promise.all([
      api("/pessoas?tamanho=1"), api("/leads?tamanho=1"),
      api("/espirometrias?tamanho=1"), api("/consultas?tamanho=1"),
      api("/parceiros?tamanho=1"), api("/encaminhamentos?tamanho=1"),
      api("/followups/fila"),
    ]).then(function (r) {
      var fila = r[6];
      body().innerHTML =
        cards([
          { label: "Pessoas", value: r[0].total },
          { label: "Leads", value: r[1].total },
          { label: "Espirometrias", value: r[2].total },
          { label: "Consultas", value: r[3].total },
          { label: "Parceiros", value: r[4].total },
          { label: "Encaminhamentos", value: r[5].total },
        ]) +
        '<div class="m15-panel"><h3>Fila de follow-up (hoje: ' + esc(fila.data_referencia) + ")</h3>" +
        cards([
          { label: "Atrasados", value: fila.totais.atrasado, cls: "m15-alerta" },
          { label: "Retomar hoje", value: fila.totais.retomar_hoje, cls: "m15-hoje" },
          { label: "Nesta semana", value: fila.totais.retomar_semana },
          { label: "Aguardando data", value: fila.totais.aguardando_data },
          { label: "Não contatar (ocultos)", value: fila.excluidos_nao_contatar },
        ]) + "</div>" +
        '<div class="m15-aviso">M15 Beta em implantação controlada — acesso seguro via Tailscale (HTTPS). ' +
        "O núcleo nativo coexiste com o painel atual: as telas antigas e o Google Sheets continuam intactos. " +
        "Registros de demonstração seguem marcados como “sintético”.</div>";
    });
  };

  // ---------------------------------------------------------------- pessoas

  loaders.pessoas = function () {
    var q = state.filters.pessoasQ || "";
    // busca por POST: o nome nunca aparece na query string (logs sem PII)
    var request = q
      ? api("/pessoas/busca", { method: "POST", body: JSON.stringify({ q: q, tamanho: 50 }) })
      : api("/pessoas?tamanho=50");
    return request.then(function (data) {
      var items = byId(data.itens);
      var podeEditar = can("operacional");
      body().innerHTML =
        '<div class="m15-filtros">' +
        '  <input id="m15PessoasQ" placeholder="Buscar por nome ou PES-…" value="' + esc(q) + '">' +
        '  <button class="m15-btn m15-btn-sec" id="m15PessoasBuscar">Buscar</button>' +
        "</div>" +
        table(
          ["Código", "Nome", "Status", "Contatos", "Criado em"].concat(podeEditar ? ["Ações"] : []),
          data.itens.map(function (p) {
            var row = [
              "<strong>" + esc(p.public_code) + "</strong>",
              esc(p.nome_completo) + sintBadge(p),
              pill(p.nao_contatar ? "não contatar" : p.status),
              (p.contatos || []).map(function (c) { return esc(c.tipo); }).join(", ") || "—",
              esc((p.created_at_local || "").slice(0, 10)),
            ];
            if (podeEditar) row.push(editBtn(p.id));
            return row;
          }),
          "Nenhuma pessoa cadastrada. Use o formulário abaixo."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeEditar
          ? '<div class="m15-panel"><h3>Nova pessoa</h3><form class="m15-form" id="m15FormPessoa">' +
            fld("Nome completo", inp("nome_completo", "", 'required minlength="2"'), 6) +
            fld("Data de nascimento", dateInp("data_nascimento", ""), 3) +
            fld("WhatsApp (opcional)", inp("whatsapp", "", 'placeholder="(21) 9…"'), 3) +
            fld("Consentimento WhatsApp", sel("consentimento_whatsapp",
              [["", "não informado"], "concedido", "desconhecido", "revogado"], ""), 3) +
            fld("Observação", inp("observacao", ""), 9) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar pessoa</button></div>' +
            "</form></div>"
          : "");
      document.getElementById("m15PessoasBuscar").addEventListener("click", function () {
        state.filters.pessoasQ = document.getElementById("m15PessoasQ").value;
        render();
      });
      document.getElementById("m15PessoasQ").addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          state.filters.pessoasQ = ev.target.value;
          render();
        }
      });
      var formPessoa = document.getElementById("m15FormPessoa");
      if (formPessoa) {
        formPessoa.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          var payload = { nome_completo: val(f, "nome_completo"), contatos: [] };
          if (val(f, "whatsapp")) {
            payload.contatos.push({ tipo: "whatsapp", valor: val(f, "whatsapp"), principal: true });
          }
          setIf(payload, "data_nascimento", val(f, "data_nascimento"));
          setIf(payload, "consentimento_whatsapp", val(f, "consentimento_whatsapp"));
          setIf(payload, "observacao", val(f, "observacao"));
          submitVia(f, function () {
            return api("/pessoas", { method: "POST", body: JSON.stringify(payload) });
          }, "Pessoa criada.");
        });
      }
      body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var p = items[btn.getAttribute("data-m15-edit")];
          if (p) openPersonEdit(p);
        });
      });
    });
  };

  function openPersonEdit(p) {
    openEdit(
      '<div class="m15-panel"><h3>Editar ' + esc(p.public_code) + " — " + esc(p.nome_completo) + "</h3>" +
      '<form class="m15-form" id="m15EditPessoa">' +
      fld("Nome completo", inp("nome_completo", p.nome_completo, 'minlength="2"'), 6) +
      fld("Data de nascimento", dateInp("data_nascimento", p.data_nascimento || ""), 3) +
      fld("Status", sel("status", ["ativo", "inativo"], p.status), 3) +
      fld("Não contatar", sel("nao_contatar", [["false", "pode contatar"], ["true", "NÃO contatar"]],
        p.nao_contatar ? "true" : "false"), 3) +
      fld("Observação", inp("observacao", p.observacao || ""), 9) +
      '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
      closeEditButton() + "</div></form>" +
      '<h3>Adicionar contato</h3><form class="m15-form" id="m15EditContato">' +
      fld("Tipo", sel("tipo", ["whatsapp", "telefone", "email", "outro"], "whatsapp"), 3) +
      fld("Valor", inp("valor", "", "required"), 6) +
      fld("Principal", sel("principal", [["false", "não"], ["true", "sim"]], "false"), 3) +
      '<div class="m15-form-full m15-actions"><button class="m15-btn m15-btn-sec" type="submit">Adicionar contato</button></div>' +
      "</form>" +
      '<h3>Registrar consentimento</h3><form class="m15-form" id="m15EditConsent">' +
      fld("Canal", sel("canal", ["whatsapp", "telefone", "email"], "whatsapp"), 3) +
      fld("Status", sel("status", ["concedido", "revogado", "desconhecido"], "concedido"), 3) +
      fld("Origem", inp("origem", "", 'placeholder="ex.: conversa telefônica"'), 6) +
      '<div class="m15-form-full m15-actions"><button class="m15-btn m15-btn-sec" type="submit">Registrar consentimento</button></div>' +
      "</form></div>",
      function (box) {
        wireClose(box);
        box.querySelector("#m15EditPessoa").addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          var payload = { nao_contatar: val(f, "nao_contatar") === "true" };
          setIf(payload, "nome_completo", val(f, "nome_completo"));
          setIf(payload, "data_nascimento", val(f, "data_nascimento"));
          setIf(payload, "status", val(f, "status"));
          if (val(f, "observacao") !== (p.observacao || "")) {
            payload.observacao = val(f, "observacao");
          }
          submitVia(f, function () {
            return api("/pessoas/" + p.id, { method: "PATCH", body: JSON.stringify(payload) });
          }, "Pessoa atualizada.");
        });
        box.querySelector("#m15EditContato").addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          submitVia(f, function () {
            return api("/pessoas/" + p.id + "/contatos", {
              method: "POST",
              body: JSON.stringify({
                tipo: val(f, "tipo"), valor: val(f, "valor"),
                principal: val(f, "principal") === "true",
              }),
            });
          }, "Contato adicionado.");
        });
        box.querySelector("#m15EditConsent").addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          var payload = { canal: val(f, "canal"), status: val(f, "status") };
          setIf(payload, "origem", val(f, "origem"));
          submitVia(f, function () {
            return api("/pessoas/" + p.id + "/consentimentos", {
              method: "POST", body: JSON.stringify(payload),
            });
          }, "Consentimento registrado.");
        });
      }
    );
  }

  // ------------------------------------------------------------------ leads

  var LEAD_ETAPAS = ["novo", "em_contato", "agendado", "convertido", "perdido",
    "nao_respondeu", "aguardando_retomada"];
  var MODALIDADES = ["residencial", "cowork", "clinica_parceira", "indefinida"];

  loaders.leads = function () {
    var etapa = state.filters.leadsEtapa || "";
    return api("/leads?tamanho=50" + (etapa ? "&etapa=" + etapa : "")).then(function (data) {
      var items = byId(data.itens);
      var podeEditar = can("operacional");
      body().innerHTML =
        '<div class="m15-filtros"><select id="m15LeadsEtapa">' +
        optionsHtml([["", "todas as etapas"]].concat(LEAD_ETAPAS), etapa) +
        "</select></div>" +
        table(
          ["Código", "Etapa", "Origem", "Modalidade", "1º contato", "Retomada manual"]
            .concat(podeEditar ? ["Ações"] : []),
          data.itens.map(function (l) {
            var row = [
              "<strong>" + esc(l.public_code) + "</strong>" + sintBadge(l),
              pill(l.etapa),
              esc(l.origem || "—"),
              esc(l.modalidade || "—"),
              fmtDate(l.data_primeiro_contato) +
                (l.data_primeiro_contato_dia_assumido
                  ? ' <span class="m15-muted" title="Original: ' + esc(l.data_primeiro_contato_original) + '">(dia assumido)</span>'
                  : ""),
              fmtDate(l.data_retomada_manual),
            ];
            if (podeEditar) row.push(editBtn(l.id));
            return row;
          }),
          "Nenhum lead. Crie abaixo a partir de uma pessoa cadastrada."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeEditar
          ? '<div class="m15-panel"><h3>Novo lead</h3><form class="m15-form" id="m15FormLead">' +
            fld("Pessoa", inp("pessoa", "", "required"),
              { span: 6, help: "Código PES-… ou nome já cadastrado na aba Pessoas." }) +
            fld("Origem", inp("origem", "", 'placeholder="ex.: site, indicação"'), 3) +
            fld("Canal de entrada", inp("canal_entrada", "", 'placeholder="ex.: whatsapp"'), 3) +
            fld("Serviço de interesse", sel("servico_interesse",
              [["", "—"], "espirometria", "consulta", "ambos", "outro"], ""), 3) +
            fld("Modalidade", sel("modalidade", [["", "—"]].concat(MODALIDADES), ""), 3) +
            fld("Etapa", sel("etapa", LEAD_ETAPAS, "novo"), 3) +
            fld("Responsável", inp("responsavel", ""), 3) +
            fld("1º contato", dateInp("data_primeiro_contato", "", { parcial: true }),
              { span: 3, help: HELP_DATA_PARCIAL }) +
            fld("Retomada manual", dateInp("data_retomada_manual", ""), 3) +
            fld("Observação", inp("observacao", ""), 6) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar lead</button></div>' +
            "</form></div>"
          : "");
      document.getElementById("m15LeadsEtapa").addEventListener("change", function (ev) {
        state.filters.leadsEtapa = ev.target.value;
        render();
      });
      var formLead = document.getElementById("m15FormLead");
      if (formLead) {
        formLead.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          submitVia(f, function () {
            return resolvePerson(val(f, "pessoa")).then(function (p) {
              var payload = { person_id: p.id, etapa: val(f, "etapa") };
              setIf(payload, "origem", val(f, "origem"));
              setIf(payload, "canal_entrada", val(f, "canal_entrada"));
              setIf(payload, "servico_interesse", val(f, "servico_interesse"));
              setIf(payload, "modalidade", val(f, "modalidade"));
              setIf(payload, "data_primeiro_contato", val(f, "data_primeiro_contato"));
              setIf(payload, "data_retomada_manual", val(f, "data_retomada_manual"));
              setIf(payload, "responsavel", val(f, "responsavel"));
              setIf(payload, "observacao", val(f, "observacao"));
              return api("/leads", { method: "POST", body: JSON.stringify(payload) });
            });
          }, "Lead criado.");
        });
      }
      body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var l = items[btn.getAttribute("data-m15-edit")];
          if (!l) return;
          openEdit(
            '<div class="m15-panel"><h3>Editar lead ' + esc(l.public_code) + "</h3>" +
            '<form class="m15-form" id="m15EditLead">' +
            fld("Etapa", sel("etapa", LEAD_ETAPAS, l.etapa), 3) +
            fld("Modalidade", sel("modalidade", [["", "—"]].concat(MODALIDADES), l.modalidade || ""), 3) +
            fld("Retomada manual", dateInp("data_retomada_manual", l.data_retomada_manual || ""), 3) +
            fld("Responsável", inp("responsavel", l.responsavel || ""), 3) +
            fld("Observação", inp("observacao", l.observacao || ""), true) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
            closeEditButton() + "</div></form></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15EditLead").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                var payload = {};
                setIf(payload, "etapa", val(f, "etapa"));
                setIf(payload, "modalidade", val(f, "modalidade"));
                setIf(payload, "data_retomada_manual", val(f, "data_retomada_manual"));
                setIf(payload, "responsavel", val(f, "responsavel"));
                setIf(payload, "observacao", val(f, "observacao"));
                submitVia(f, function () {
                  return api("/leads/" + l.id, { method: "PATCH", body: JSON.stringify(payload) });
                }, "Lead atualizado.");
              });
            }
          );
        });
      });
    });
  };

  // ------------------------------------------- espirometrias e consultas

  function attendanceLoader(kind) {
    var isExam = kind === "espirometrias";
    var statusKey = isExam ? "espStatus" : "conStatus";
    var statusList = isExam
      ? ["Aguardando", "Realizado", "Cancelado", "Remarcado"]
      : ["Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"];
    return function () {
      var status = state.filters[statusKey] || "";
      var podeEditar = can("operacional");
      return Promise.all([
        api("/" + kind + "?tamanho=50" + (status ? "&status=" + encodeURIComponent(status) : "")),
        podeEditar && isExam ? partnerOptions().catch(function () { return []; }) : Promise.resolve([]),
      ]).then(function (r) {
        var data = r[0];
        var partners = r[1];
        var items = byId(data.itens);
        body().innerHTML =
          '<div class="m15-filtros"><select id="m15AttStatus">' +
          optionsHtml([["", "todos os status"]].concat(statusList), status) +
          "</select></div>" +
          table(
            ["Código", "Data", "Status", isExam ? "Modalidade" : "Profissional", "Origem"]
              .concat(podeEditar ? ["Ações"] : []),
            data.itens.map(function (e) {
              var dt = isExam ? e.data_exame : e.data_consulta;
              var assumed = isExam ? e.data_exame_dia_assumido : e.data_consulta_dia_assumido;
              var original = isExam ? e.data_exame_original : e.data_consulta_original;
              var row = [
                "<strong>" + esc(e.public_code) + "</strong>" + sintBadge(e),
                fmtDate(dt) + (assumed
                  ? ' <span class="m15-muted" title="Original: ' + esc(original) + '">(dia assumido)</span>' : ""),
                pill(e.status),
                esc(isExam ? (e.modalidade || "—") : (e.profissional || "—")),
                esc(e.origem || "—"),
              ];
              if (podeEditar) row.push(editBtn(e.id));
              return row;
            }),
            isExam ? "Nenhuma espirometria registrada no núcleo M15."
                   : "Nenhuma consulta registrada no núcleo M15."
          ) +
          '<div id="m15EditBox"></div>' +
          (podeEditar
            ? '<div class="m15-panel"><h3>' + (isExam ? "Nova espirometria" : "Nova consulta") +
              '</h3><form class="m15-form" id="m15FormAtt">' +
              fld("Pessoa", inp("pessoa", "", "required"),
                { span: 6, help: "Código PES-… ou nome já cadastrado na aba Pessoas." }) +
              fld(isExam ? "Data do exame" : "Data da consulta",
                dateInp("data", "", { parcial: true }), { span: 3, help: HELP_DATA_PARCIAL }) +
              fld("Status", sel("status", statusList, statusList[0]), 3) +
              (isExam
                ? fld("Modalidade", sel("modalidade",
                    [["", "—"], "residencial", "cowork", "clinica_parceira"], ""), 4) +
                  fld("Origem", inp("origem", ""), 4) +
                  fld("Responsável", inp("responsavel", ""), 4) +
                  grp("Local e parceria (opcional)",
                    fld("Local de atendimento", inp("local_atendimento", ""), 4) +
                    fld("Parceiro", sel("partner_id", [["", "sem parceiro"]].concat(partners), ""), 4) +
                    fld("Unidade", sel("partner_unit_id", [["", "sem unidade"]], ""), 4))
                : fld("Modalidade", sel("modalidade",
                    [["", "—"], "teleconsulta", "residencial", "cowork", "clinica_parceira"], ""), 4) +
                  fld("Profissional", inp("profissional", ""), 4) +
                  fld("Origem", inp("origem", ""), 4) +
                  fld("Responsável", inp("responsavel", ""), 4)) +
              fld("Observação", inp("observacao", ""), isExam ? true : 8) +
              '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">' +
              (isExam ? "Criar espirometria" : "Criar consulta") + "</button></div>" +
              "</form></div>"
            : "");
        document.getElementById("m15AttStatus").addEventListener("change", function (ev) {
          state.filters[statusKey] = ev.target.value;
          render();
        });
        var form = document.getElementById("m15FormAtt");
        if (form) {
          if (isExam) wireUnitSelect(form, "partner_id", "partner_unit_id");
          form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var f = ev.target;
            submitVia(f, function () {
              return resolvePerson(val(f, "pessoa")).then(function (p) {
                var payload = {
                  person_id: p.id,
                  status: val(f, "status"),
                  idempotency_key: idemKey(),
                };
                setIf(payload, isExam ? "data_exame" : "data_consulta", val(f, "data"));
                setIf(payload, "modalidade", val(f, "modalidade"));
                if (isExam) {
                  setIf(payload, "local_atendimento", val(f, "local_atendimento"));
                  setIf(payload, "partner_id", val(f, "partner_id"));
                  setIf(payload, "partner_unit_id", val(f, "partner_unit_id"));
                } else {
                  setIf(payload, "profissional", val(f, "profissional"));
                }
                setIf(payload, "origem", val(f, "origem"));
                setIf(payload, "responsavel", val(f, "responsavel"));
                setIf(payload, "observacao", val(f, "observacao"));
                return api("/" + kind, { method: "POST", body: JSON.stringify(payload) });
              });
            }, isExam ? "Espirometria criada." : "Consulta criada.");
          });
        }
        body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var e = items[btn.getAttribute("data-m15-edit")];
            if (!e) return;
            openEdit(
              '<div class="m15-panel"><h3>Editar ' + esc(e.public_code) + "</h3>" +
              '<form class="m15-form" id="m15EditAtt">' +
              fld("Status", sel("status", statusList, e.status), 3) +
              fld("Data", dateInp("data", (isExam ? e.data_exame : e.data_consulta) || "",
                { parcial: true }), { span: 3, help: HELP_DATA_PARCIAL }) +
              (isExam
                ? fld("Responsável", inp("responsavel", e.responsavel || ""), 6)
                : fld("Profissional", inp("profissional", e.profissional || ""), 6)) +
              fld("Observação", inp("observacao", e.observacao || ""), true) +
              '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
              closeEditButton() + "</div></form>" +
              '<p class="m15-muted">Marcar como ' + (isExam ? '"Realizado"' : '"Realizada"') +
              " agenda o follow-up de 6 meses automaticamente.</p></div>",
              function (box) {
                wireClose(box);
                box.querySelector("#m15EditAtt").addEventListener("submit", function (ev2) {
                  ev2.preventDefault();
                  var f = ev2.target;
                  var payload = {};
                  setIf(payload, "status", val(f, "status"));
                  setIf(payload, isExam ? "data_exame" : "data_consulta", val(f, "data"));
                  if (isExam) setIf(payload, "responsavel", val(f, "responsavel"));
                  else setIf(payload, "profissional", val(f, "profissional"));
                  setIf(payload, "observacao", val(f, "observacao"));
                  submitVia(f, function () {
                    return api("/" + kind + "/" + e.id, {
                      method: "PATCH", body: JSON.stringify(payload),
                    });
                  }, "Atualizado.");
                });
              }
            );
          });
        });
      });
    };
  }

  loaders.espirometrias = attendanceLoader("espirometrias");
  loaders.consultas = attendanceLoader("consultas");

  // -------------------------------------------------- parceiros e ecossistema

  var PARTNER_STATUS = ["prospecto", "em_negociacao", "ativa", "pausada", "encerrada"];

  loaders.parceiros = function () {
    var podeEditar = can("operacional");
    var podeGestor = can("gestor");
    return Promise.all([
      api("/parceiros?tamanho=50"),
      api("/unidades?tamanho=50"),
      api("/contatos-parceiros?tamanho=50"),
      api("/parcerias?tamanho=50"),
    ]).then(function (r) {
      var partners = r[0].itens;
      var pmap = byId(partners);
      var units = byId(r[1].itens);
      var contacts = byId(r[2].itens);
      var partnerships = byId(r[3].itens);
      var pOpts = partners.map(function (p) { return [p.id, p.public_code + " — " + p.nome]; });
      var pname = function (id) { return pmap[id] ? pmap[id].nome : "—"; };

      var html =
        '<div class="m15-panel"><h3>Parceiros (' + r[0].total + ")</h3>" +
        table(
          ["Código", "Nome", "Tipo", "Status", "Cidade"].concat(podeEditar ? ["Ações"] : []),
          partners.map(function (p) {
            var row = [
              "<strong>" + esc(p.public_code) + "</strong>" + sintBadge(p),
              esc(p.nome), esc(p.tipo), pill(p.status), esc(p.cidade || "—"),
            ];
            if (podeEditar) row.push('<button class="m15-btn m15-btn-sec" data-m15-edit-parceiro="' + esc(p.id) + '">Editar</button>');
            return row;
          }),
          "Nenhum parceiro. O cadastro institucional (ex.: Pastore) entra pelo comando privado seed-institucional."
        ) +
        (podeEditar
          ? '<h3>Novo parceiro</h3><form class="m15-form" id="m15FormParceiro">' +
            fld("Nome", inp("nome", "", 'required minlength="2"'), 6) +
            fld("Tipo", sel("tipo", ["clinica", "consultorio", "outro"], "clinica"), 2) +
            fld("Status", sel("status", PARTNER_STATUS, "prospecto"), 2) +
            fld("Cidade", inp("cidade", ""), 2) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar parceiro</button></div>' +
            "</form>"
          : "") +
        "</div>" +
        '<div class="m15-panel"><h3>Unidades (' + r[1].total + ")</h3>" +
        table(
          ["Código", "Parceiro", "Nome", "Bairro", "Ativa"].concat(podeEditar ? ["Ações"] : []),
          r[1].itens.map(function (u) {
            var row = [
              "<strong>" + esc(u.public_code) + "</strong>",
              esc(pname(u.partner_id)), esc(u.nome), esc(u.bairro || "—"),
              pill(u.ativo ? "ativa" : "inativa"),
            ];
            if (podeEditar) row.push('<button class="m15-btn m15-btn-sec" data-m15-edit-unidade="' + esc(u.id) + '">Editar</button>');
            return row;
          }),
          "Nenhuma unidade cadastrada."
        ) +
        (podeEditar
          ? '<h3>Nova unidade</h3><form class="m15-form" id="m15FormUnidade">' +
            fld("Parceiro", sel("partner_id", pOpts, "", "required"), 4) +
            fld("Nome", inp("nome", "", "required"), 4) +
            fld("Bairro", inp("bairro", ""), 2) +
            fld("Cidade", inp("cidade", ""), 2) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar unidade</button></div>' +
            "</form>"
          : "") +
        "</div>" +
        '<div class="m15-panel"><h3>Contatos de parceiros (' + r[2].total + ")</h3>" +
        table(
          ["Código", "Parceiro", "Nome", "Cargo", "Principal", "Ativo"].concat(podeEditar ? ["Ações"] : []),
          r[2].itens.map(function (c) {
            var row = [
              "<strong>" + esc(c.public_code) + "</strong>",
              esc(pname(c.partner_id)), esc(c.nome), esc(c.cargo || "—"),
              c.principal ? "sim" : "—", pill(c.ativo ? "ativo" : "inativo"),
            ];
            if (podeEditar) row.push('<button class="m15-btn m15-btn-sec" data-m15-edit-contato="' + esc(c.id) + '">Editar</button>');
            return row;
          }),
          "Nenhum contato de parceiro."
        ) +
        (podeEditar
          ? '<h3>Novo contato</h3><form class="m15-form" id="m15FormContato">' +
            fld("Parceiro", sel("partner_id", pOpts, "", "required"), 4) +
            fld("Nome", inp("nome", "", 'required minlength="2"'), 4) +
            fld("Cargo", inp("cargo", ""), 4) +
            fld("Telefone", inp("telefone", ""), 4) +
            fld("E-mail", inp("email", ""), 4) +
            fld("Principal", sel("principal", [["false", "não"], ["true", "sim"]], "false"), 4) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar contato</button></div>' +
            "</form>"
          : "") +
        "</div>" +
        '<div class="m15-panel"><h3>Parcerias (' + r[3].total + ")</h3>" +
        '<p class="m15-muted">Percentuais e valores de repasse são definidos aqui (papel gestor/admin) — ' +
        "o encaminhamento individual não redefine acordo.</p>" +
        table(
          ["Código", "Parceiro", "Status", "Início", "Modelo", "%", "Fixo", "Follow-up"]
            .concat(podeGestor ? ["Ações"] : []),
          r[3].itens.map(function (pt) {
            var row = [
              "<strong>" + esc(pt.public_code) + "</strong>",
              esc(pname(pt.partner_id)), pill(pt.status), fmtDate(pt.data_inicio),
              esc(pt.modelo_repasse),
              esc(pt.percentual_repasse == null ? "—" : pt.percentual_repasse),
              esc(pt.valor_repasse_fixo == null ? "—" : "R$ " + pt.valor_repasse_fixo),
              esc(pt.responsavel_followup),
            ];
            if (podeGestor) row.push('<button class="m15-btn m15-btn-sec" data-m15-edit-parceria="' + esc(pt.id) + '">Editar</button>');
            return row;
          }),
          "Nenhuma parceria registrada."
        ) +
        (podeGestor
          ? '<h3>Nova parceria (gestor)</h3><form class="m15-form" id="m15FormParceria">' +
            fld("Parceiro", sel("partner_id", pOpts, "", "required"), 6) +
            fld("Status", sel("status", ["em_negociacao", "ativa", "pausada", "encerrada"], "em_negociacao"), 3) +
            fld("Início", dateInp("data_inicio", "", { parcial: true }),
              { span: 3, help: HELP_DATA_PARCIAL }) +
            fld("Modelo de repasse", sel("modelo_repasse", ["indefinido", "percentual", "fixo", "nenhum"], "indefinido"), 3) +
            fld("% repasse", inp("percentual_repasse", "", 'type="number" step="0.01" min="0" max="100"'), 3) +
            fld("Valor fixo (R$)", inp("valor_repasse_fixo", "", 'type="number" step="0.01" min="0"'), 3) +
            fld("Follow-up por", sel("responsavel_followup", ["soprolife", "parceiro"], "soprolife"), 3) +
            fld("Responsável SoproLife", inp("responsavel_soprolife", ""), 6) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar parceria</button></div>' +
            "</form>"
          : "") +
        "</div>" +
        '<div id="m15EditBox"></div>';
      body().innerHTML = html;

      function onSubmit(id, handler) {
        var form = document.getElementById(id);
        if (form) form.addEventListener("submit", handler);
      }
      onSubmit("m15FormParceiro", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        var payload = { nome: val(f, "nome"), tipo: val(f, "tipo"), status: val(f, "status") };
        setIf(payload, "cidade", val(f, "cidade"));
        submitVia(f, function () {
          return api("/parceiros", { method: "POST", body: JSON.stringify(payload) });
        }, "Parceiro criado.");
      });
      onSubmit("m15FormUnidade", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        var payload = { partner_id: val(f, "partner_id"), nome: val(f, "nome") };
        setIf(payload, "bairro", val(f, "bairro"));
        setIf(payload, "cidade", val(f, "cidade"));
        submitVia(f, function () {
          return api("/unidades", { method: "POST", body: JSON.stringify(payload) });
        }, "Unidade criada.");
      });
      onSubmit("m15FormContato", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        var payload = {
          partner_id: val(f, "partner_id"), nome: val(f, "nome"),
          principal: val(f, "principal") === "true",
        };
        setIf(payload, "cargo", val(f, "cargo"));
        setIf(payload, "telefone", val(f, "telefone"));
        setIf(payload, "email", val(f, "email"));
        submitVia(f, function () {
          return api("/contatos-parceiros", { method: "POST", body: JSON.stringify(payload) });
        }, "Contato criado.");
      });
      onSubmit("m15FormParceria", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        var payload = {
          partner_id: val(f, "partner_id"), status: val(f, "status"),
          modelo_repasse: val(f, "modelo_repasse"),
          responsavel_followup: val(f, "responsavel_followup"),
        };
        setIf(payload, "data_inicio", val(f, "data_inicio"));
        if (val(f, "percentual_repasse")) payload.percentual_repasse = moneyStr(val(f, "percentual_repasse"));
        if (val(f, "valor_repasse_fixo")) payload.valor_repasse_fixo = moneyStr(val(f, "valor_repasse_fixo"));
        setIf(payload, "responsavel_soprolife", val(f, "responsavel_soprolife"));
        submitVia(f, function () {
          return api("/parcerias", { method: "POST", body: JSON.stringify(payload) });
        }, "Parceria criada.");
      });

      function wireEdit(attr, map, opener) {
        body().querySelectorAll("[" + attr + "]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var item = map[btn.getAttribute(attr)];
            if (item) opener(item);
          });
        });
      }
      wireEdit("data-m15-edit-parceiro", pmap, function (p) {
        openEdit(
          '<div class="m15-panel"><h3>Editar parceiro ' + esc(p.public_code) + "</h3>" +
          '<form class="m15-form" id="m15EditParceiro">' +
          fld("Nome", inp("nome", p.nome, 'minlength="2"'), 6) +
          fld("Tipo", sel("tipo", ["clinica", "consultorio", "outro"], p.tipo), 3) +
          fld("Status", sel("status", PARTNER_STATUS, p.status), 3) +
          fld("Cidade", inp("cidade", p.cidade || ""), 4) +
          fld("Observação", inp("observacao", p.observacao || ""), 8) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
          closeEditButton() + "</div></form></div>",
          function (box) {
            wireClose(box);
            box.querySelector("#m15EditParceiro").addEventListener("submit", function (ev) {
              ev.preventDefault();
              var f = ev.target;
              var payload = {};
              setIf(payload, "nome", val(f, "nome"));
              setIf(payload, "tipo", val(f, "tipo"));
              setIf(payload, "status", val(f, "status"));
              setIf(payload, "cidade", val(f, "cidade"));
              setIf(payload, "observacao", val(f, "observacao"));
              submitVia(f, function () {
                return api("/parceiros/" + p.id, { method: "PATCH", body: JSON.stringify(payload) });
              }, "Parceiro atualizado.");
            });
          }
        );
      });
      wireEdit("data-m15-edit-unidade", units, function (u) {
        openEdit(
          '<div class="m15-panel"><h3>Editar unidade ' + esc(u.public_code) + "</h3>" +
          '<form class="m15-form" id="m15EditUnidade">' +
          fld("Nome", inp("nome", u.nome), 4) +
          fld("Bairro", inp("bairro", u.bairro || ""), 3) +
          fld("Cidade", inp("cidade", u.cidade || ""), 3) +
          fld("Ativa", sel("ativo", [["true", "sim"], ["false", "não"]], u.ativo ? "true" : "false"), 2) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
          closeEditButton() + "</div></form></div>",
          function (box) {
            wireClose(box);
            box.querySelector("#m15EditUnidade").addEventListener("submit", function (ev) {
              ev.preventDefault();
              var f = ev.target;
              var payload = { ativo: val(f, "ativo") === "true" };
              setIf(payload, "nome", val(f, "nome"));
              setIf(payload, "bairro", val(f, "bairro"));
              setIf(payload, "cidade", val(f, "cidade"));
              submitVia(f, function () {
                return api("/unidades/" + u.id, { method: "PATCH", body: JSON.stringify(payload) });
              }, "Unidade atualizada.");
            });
          }
        );
      });
      wireEdit("data-m15-edit-contato", contacts, function (c) {
        openEdit(
          '<div class="m15-panel"><h3>Editar contato ' + esc(c.public_code) + "</h3>" +
          '<form class="m15-form" id="m15EditContatoP">' +
          fld("Nome", inp("nome", c.nome, 'minlength="2"'), 4) +
          fld("Cargo", inp("cargo", c.cargo || ""), 4) +
          fld("Telefone", inp("telefone", c.telefone || ""), 4) +
          fld("E-mail", inp("email", c.email || ""), 6) +
          fld("Principal", sel("principal", [["false", "não"], ["true", "sim"]], c.principal ? "true" : "false"), 3) +
          fld("Ativo", sel("ativo", [["true", "sim"], ["false", "não"]], c.ativo ? "true" : "false"), 3) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
          closeEditButton() + "</div></form></div>",
          function (box) {
            wireClose(box);
            box.querySelector("#m15EditContatoP").addEventListener("submit", function (ev) {
              ev.preventDefault();
              var f = ev.target;
              var payload = {
                principal: val(f, "principal") === "true",
                ativo: val(f, "ativo") === "true",
              };
              setIf(payload, "nome", val(f, "nome"));
              setIf(payload, "cargo", val(f, "cargo"));
              setIf(payload, "telefone", val(f, "telefone"));
              setIf(payload, "email", val(f, "email"));
              submitVia(f, function () {
                return api("/contatos-parceiros/" + c.id, { method: "PATCH", body: JSON.stringify(payload) });
              }, "Contato atualizado.");
            });
          }
        );
      });
      wireEdit("data-m15-edit-parceria", partnerships, function (pt) {
        openEdit(
          '<div class="m15-panel"><h3>Editar parceria ' + esc(pt.public_code) + " (gestor)</h3>" +
          '<form class="m15-form" id="m15EditParceria">' +
          fld("Status", sel("status", ["em_negociacao", "ativa", "pausada", "encerrada"], pt.status), 4) +
          fld("Início", dateInp("data_inicio", pt.data_inicio || "", { parcial: true }),
            { span: 4, help: HELP_DATA_PARCIAL }) +
          fld("Modelo de repasse", sel("modelo_repasse", ["indefinido", "percentual", "fixo", "nenhum"], pt.modelo_repasse), 4) +
          fld("% repasse", inp("percentual_repasse", pt.percentual_repasse == null ? "" : pt.percentual_repasse,
            'type="number" step="0.01" min="0" max="100"'), 4) +
          fld("Valor fixo (R$)", inp("valor_repasse_fixo", pt.valor_repasse_fixo == null ? "" : pt.valor_repasse_fixo,
            'type="number" step="0.01" min="0"'), 4) +
          fld("Follow-up por", sel("responsavel_followup", ["soprolife", "parceiro"], pt.responsavel_followup), 4) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
          closeEditButton() + "</div></form></div>",
          function (box) {
            wireClose(box);
            box.querySelector("#m15EditParceria").addEventListener("submit", function (ev) {
              ev.preventDefault();
              var f = ev.target;
              var payload = {};
              setIf(payload, "status", val(f, "status"));
              setIf(payload, "data_inicio", val(f, "data_inicio"));
              setIf(payload, "modelo_repasse", val(f, "modelo_repasse"));
              if (val(f, "percentual_repasse")) payload.percentual_repasse = moneyStr(val(f, "percentual_repasse"));
              if (val(f, "valor_repasse_fixo")) payload.valor_repasse_fixo = moneyStr(val(f, "valor_repasse_fixo"));
              setIf(payload, "responsavel_followup", val(f, "responsavel_followup"));
              submitVia(f, function () {
                return api("/parcerias/" + pt.id, { method: "PATCH", body: JSON.stringify(payload) });
              }, "Parceria atualizada.");
            });
          }
        );
      });
    });
  };

  // --------------------------------------------------------- encaminhamentos

  var REFERRAL_STATUS = ["Recebido da clínica", "Aguardando contato", "Contato realizado",
    "Agendado", "Realizado", "Laudo enviado", "Cancelado", "Não compareceu",
    "Aguardando pagamento", "Concluído"];

  loaders.encaminhamentos = function () {
    var status = state.filters.encStatus || "";
    var podeEditar = can("operacional");
    var podeGestor = can("gestor");
    return Promise.all([
      api("/encaminhamentos?tamanho=50" + (status ? "&status=" + encodeURIComponent(status) : "")),
      podeEditar ? partnerOptions().catch(function () { return []; }) : Promise.resolve([]),
    ]).then(function (r) {
      var data = r[0];
      var partners = r[1];
      var items = byId(data.itens);
      body().innerHTML =
        '<div class="m15-filtros"><select id="m15EncStatus">' +
        optionsHtml([["", "todos os status"]].concat(REFERRAL_STATUS), status) +
        "</select></div>" +
        table(
          ["Código", "Encaminhado em", "Serviço", "Status", "Laudo", "Repasse", "Follow-up"]
            .concat(podeEditar ? ["Ações"] : []),
          data.itens.map(function (ref) {
            var acoes = "";
            if (podeEditar) acoes += editBtn(ref.id);
            if (podeGestor) {
              acoes += ' <button class="m15-btn m15-btn-sec" data-m15-fin="' + esc(ref.id) + '">Financeiro</button>';
            }
            var row = [
              "<strong>" + esc(ref.public_code) + "</strong>" + sintBadge(ref),
              fmtDate(ref.data_encaminhamento),
              esc(ref.servico_solicitado || "—"),
              pill(ref.status),
              ref.laudo_enviado ? "enviado " + fmtDate(ref.data_envio_laudo) : "pendente",
              esc(ref.status_repasse || "—"),
              esc(ref.responsavel_followup),
            ];
            if (podeEditar) row.push(acoes);
            return row;
          }),
          "Nenhum paciente de parceiro. Encaminhamentos ligam Pessoa ↔ Parceiro ↔ Unidade ↔ Atendimento sem duplicar cadastros."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeEditar
          ? '<div class="m15-panel"><h3>Novo encaminhamento</h3><form class="m15-form" id="m15FormEnc">' +
            fld("Pessoa", inp("pessoa", "", "required"),
              { span: 6, help: "Código PES-… ou nome já cadastrado na aba Pessoas." }) +
            fld("Parceiro", sel("partner_id", partners, "", "required"), 3) +
            fld("Unidade (opcional)", sel("partner_unit_id", [["", "sem unidade"]], ""), 3) +
            fld("Serviço", sel("servico_solicitado", [["", "—"], "espirometria", "consulta", "ambos", "outro"], ""), 3) +
            fld("Encaminhado em", dateInp("data_encaminhamento", "", { parcial: true }),
              { span: 3, help: HELP_DATA_PARCIAL }) +
            fld("Data agendada", dateInp("data_agendada", ""), 3) +
            fld("Status", sel("status", REFERRAL_STATUS, "Recebido da clínica"), 3) +
            fld("Autorização de contato", sel("autorizacao",
              [["", "não informado (sem contato)"], ["true", "sim"], ["false", "não"]], ""),
              { span: 4, help: "A clínica autorizou a SoproLife a contatar o paciente?" }) +
            fld("Follow-up por", sel("responsavel_followup", ["soprolife", "parceiro"], "soprolife"), 4) +
            fld("Responsável SoproLife", inp("responsavel_soprolife", ""), 4) +
            fld("Observação operacional", inp("observacao_operacional", ""), true) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar encaminhamento</button></div>' +
            "</form></div>"
          : "");
      document.getElementById("m15EncStatus").addEventListener("change", function (ev) {
        state.filters.encStatus = ev.target.value;
        render();
      });
      var formEnc = document.getElementById("m15FormEnc");
      if (formEnc) {
        wireUnitSelect(formEnc, "partner_id", "partner_unit_id");
        // popula unidades do primeiro parceiro selecionado
        if (formEnc.elements.partner_id.value) {
          formEnc.elements.partner_id.dispatchEvent(new Event("change"));
        }
        formEnc.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          submitVia(f, function () {
            return resolvePerson(val(f, "pessoa")).then(function (p) {
              var payload = {
                person_id: p.id,
                partner_id: val(f, "partner_id"),
                status: val(f, "status"),
                responsavel_followup: val(f, "responsavel_followup"),
              };
              setIf(payload, "partner_unit_id", val(f, "partner_unit_id"));
              setIf(payload, "servico_solicitado", val(f, "servico_solicitado"));
              setIf(payload, "data_encaminhamento", val(f, "data_encaminhamento"));
              setIf(payload, "data_agendada", val(f, "data_agendada"));
              if (val(f, "autorizacao") !== "") {
                payload.autorizacao_contato_soprolife = val(f, "autorizacao") === "true";
              }
              setIf(payload, "responsavel_soprolife", val(f, "responsavel_soprolife"));
              setIf(payload, "observacao_operacional", val(f, "observacao_operacional"));
              return api("/encaminhamentos", { method: "POST", body: JSON.stringify(payload) });
            });
          }, "Encaminhamento criado.");
        });
      }
      body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var ref = items[btn.getAttribute("data-m15-edit")];
          if (!ref) return;
          openEdit(
            '<div class="m15-panel"><h3>Editar encaminhamento ' + esc(ref.public_code) + "</h3>" +
            '<form class="m15-form" id="m15EditEnc">' +
            fld("Status", sel("status", REFERRAL_STATUS, ref.status), 4) +
            fld("Data agendada", dateInp("data_agendada", ref.data_agendada || ""), 4) +
            fld("Data de realização", dateInp("data_realizacao", ref.data_realizacao || ""), 4) +
            fld("Laudo enviado", sel("laudo_enviado", [["", "—"], ["true", "sim"], ["false", "não"]],
              ref.laudo_enviado ? "true" : ""), 4) +
            fld("Data envio do laudo", dateInp("data_envio_laudo", ref.data_envio_laudo || ""), 4) +
            fld("Próximo follow-up", dateInp("proximo_followup", ref.proximo_followup || ""), 4) +
            fld("Autorização de contato", sel("autorizacao",
              [["", "manter"], ["true", "sim"], ["false", "não"]], ""), 3) +
            fld("Follow-up por", sel("responsavel_followup", ["soprolife", "parceiro"], ref.responsavel_followup), 3) +
            fld("Observação operacional", inp("observacao_operacional", ref.observacao_operacional || ""), 6) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
            closeEditButton() + "</div></form></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15EditEnc").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                var payload = {};
                setIf(payload, "status", val(f, "status"));
                setIf(payload, "data_agendada", val(f, "data_agendada"));
                setIf(payload, "data_realizacao", val(f, "data_realizacao"));
                if (val(f, "laudo_enviado") !== "") payload.laudo_enviado = val(f, "laudo_enviado") === "true";
                setIf(payload, "data_envio_laudo", val(f, "data_envio_laudo"));
                if (val(f, "autorizacao") !== "") {
                  payload.autorizacao_contato_soprolife = val(f, "autorizacao") === "true";
                }
                setIf(payload, "responsavel_followup", val(f, "responsavel_followup"));
                setIf(payload, "proximo_followup", val(f, "proximo_followup"));
                setIf(payload, "observacao_operacional", val(f, "observacao_operacional"));
                submitVia(f, function () {
                  return api("/encaminhamentos/" + ref.id, { method: "PATCH", body: JSON.stringify(payload) });
                }, "Encaminhamento atualizado.");
              });
            }
          );
        });
      });
      body().querySelectorAll("[data-m15-fin]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var ref = items[btn.getAttribute("data-m15-fin")];
          if (!ref) return;
          openEdit(
            '<div class="m15-panel"><h3>Financeiro do encaminhamento ' + esc(ref.public_code) + " (gestor)</h3>" +
            '<p class="m15-muted">O lançamento em Financeiro continua sendo a única fonte monetária; ' +
            "estes campos registram a visão do encaminhamento sem duplicar receita.</p>" +
            '<form class="m15-form" id="m15EditEncFin">' +
            fld("Valor cobrado (R$)", inp("valor_cobrado", ref.valor_cobrado == null ? "" : ref.valor_cobrado,
              'type="number" step="0.01" min="0"'), 4) +
            fld("Valor recebido (R$)", inp("valor_recebido", ref.valor_recebido == null ? "" : ref.valor_recebido,
              'type="number" step="0.01" min="0"'), 4) +
            fld("Status do repasse", sel("status_repasse", [["", "—"], "previsto", "aguardando", "pago", "cancelado"],
              ref.status_repasse || ""), 4) +
            grp("Acordo de repasse",
              fld("Tipo de repasse", sel("tipo_repasse", [["", "—"], "percentual", "fixo", "nenhum"], ref.tipo_repasse || ""), 4) +
              fld("% repasse", inp("percentual_repasse", ref.percentual_repasse == null ? "" : ref.percentual_repasse,
                'type="number" step="0.01" min="0" max="100"'), 4) +
              fld("Valor do repasse (R$)", inp("valor_repasse", ref.valor_repasse == null ? "" : ref.valor_repasse,
                'type="number" step="0.01" min="0"'), 4)) +
            grp("Vínculo técnico (opcional)",
              fld("ID do lançamento (LAN)", inp("financial_entry_id", ref.financial_entry_id || ""),
                { span: 6, help: "Identificador técnico do lançamento no Financeiro — nunca digite dados de pessoas." }),
              "m15-group-tech") +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar financeiro</button> ' +
            closeEditButton() + "</div></form></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15EditEncFin").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                var payload = {};
                if (val(f, "valor_cobrado")) payload.valor_cobrado = moneyStr(val(f, "valor_cobrado"));
                if (val(f, "valor_recebido")) payload.valor_recebido = moneyStr(val(f, "valor_recebido"));
                setIf(payload, "tipo_repasse", val(f, "tipo_repasse"));
                if (val(f, "percentual_repasse")) payload.percentual_repasse = moneyStr(val(f, "percentual_repasse"));
                if (val(f, "valor_repasse")) payload.valor_repasse = moneyStr(val(f, "valor_repasse"));
                setIf(payload, "status_repasse", val(f, "status_repasse"));
                setIf(payload, "financial_entry_id", val(f, "financial_entry_id"));
                submitVia(f, function () {
                  return api("/encaminhamentos/" + ref.id + "/financeiro", {
                    method: "PATCH", body: JSON.stringify(payload),
                  });
                }, "Financeiro do encaminhamento atualizado.");
              });
            }
          );
        });
      });
    });
  };

  // --------------------------------------------------------------- follow-up

  loaders.followup = function () {
    var podeEditar = can("operacional");
    return Promise.all([
      api("/followups/fila"),
      api("/interacoes?tamanho=15").catch(function () { return { itens: [], total: 0 }; }),
    ]).then(function (r) {
      var data = r[0];
      var interacoes = r[1];
      var order = ["atrasado", "retomar_hoje", "retomar_semana", "aguardando_data", "concluido"];
      var labels = {
        atrasado: "Atrasado", retomar_hoje: "Retomar hoje",
        retomar_semana: "Retomar nesta semana", aguardando_data: "Aguardando data",
        concluido: "Concluído",
      };
      var html = cards(order.map(function (k) {
        return {
          label: labels[k], value: data.totais[k],
          cls: k === "atrasado" ? "m15-alerta" : (k === "retomar_hoje" ? "m15-hoje" : (k === "concluido" ? "m15-ok" : "")),
        };
      }).concat([{ label: "Não contatar (ocultos)", value: data.excluidos_nao_contatar }]));
      order.forEach(function (key) {
        var itens = data.filas[key];
        html += '<div class="m15-panel"><h3>' + labels[key] + " (" + itens.length + ")</h3>";
        if (!itens.length) {
          html += '<div class="m15-empty">Fila vazia.</div>';
        } else {
          html += table(
            ["Pessoa", "Tipo", "Vencimento", "Tentativas", "Consentimento", "Ações"],
            itens.map(function (f) {
              var actions = "";
              if (f.status === "pendente" && podeEditar) {
                // WhatsApp só quando a API confirma (consentimento concedido,
                // follow-up da SoproLife, registro real — fail-closed)
                if (f.whatsapp_permitido) {
                  actions +=
                    '<button class="m15-btn m15-btn-wa" data-m15-wa="' + esc(f.id) + '">WhatsApp</button> ';
                }
                actions +=
                  '<button class="m15-btn m15-btn-sec" data-m15-done="' + esc(f.id) + '">Concluir</button> ' +
                  '<button class="m15-btn m15-btn-sec" data-m15-retry="' + esc(f.id) + '">Nova tentativa</button>';
              }
              var etiquetas = "";
              if (f.controlado_por_parceiro) etiquetas += ' <span class="m15-pill">parceiro contata</span>';
              if (f.sintetico) etiquetas += ' <span class="m15-badge-sint">sintético</span>';
              return [
                "<strong>" + esc(f.pessoa.public_code) + "</strong> " + esc(f.pessoa.nome_completo) + etiquetas,
                esc(f.tipo),
                fmtDate(f.due_date),
                String(f.tentativas),
                f.aviso_consentimento
                  ? '<span class="m15-pill atrasado" title="Sem consentimento concedido — contato bloqueado (fail-closed)">' +
                    esc(f.consentimento_whatsapp) + "</span>"
                  : pill(f.consentimento_whatsapp),
                actions || "—",
              ];
            })
          );
        }
        html += "</div>";
      });
      html += '<div id="m15WaBox"></div><div id="m15EditBox"></div>';
      if (podeEditar) {
        html +=
          '<div class="m15-panel"><h3>Novo follow-up manual</h3><form class="m15-form" id="m15FormFup">' +
          fld("Pessoa", inp("pessoa", "", "required"),
            { span: 6, help: "Código PES-… ou nome já cadastrado na aba Pessoas." }) +
          fld("Vencimento", dateInp("due_date", ""), 3) +
          fld("Responsável", inp("responsavel", ""), 3) +
          fld("Observação", inp("observacao", ""), true) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar follow-up</button></div>' +
          "</form></div>" +
          '<div class="m15-panel"><h3>Registrar interação</h3><form class="m15-form" id="m15FormInt">' +
          fld("Pessoa", inp("pessoa", "", "required"),
            { span: 6, help: "Código PES-… ou nome já cadastrado na aba Pessoas." }) +
          fld("Canal", sel("canal", ["whatsapp", "telefone", "email", "presencial", "outro"], "telefone"), 3) +
          fld("Direção", sel("direcao", ["enviado", "recebido"], "enviado"), 3) +
          fld("Resultado", inp("resultado", "", 'placeholder="ex.: respondeu, sem resposta"'), 4) +
          fld("Resumo", inp("resumo", ""), 8) +
          '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Registrar interação</button></div>' +
          "</form></div>";
      }
      html +=
        '<div class="m15-panel"><h3>Interações recentes (' + interacoes.total + ")</h3>" +
        table(
          ["Quando", "Canal", "Direção", "Resultado", "Resumo"],
          interacoes.itens.map(function (i) {
            return [
              esc((i.ts_local || "").replace("T", " ").slice(0, 16)),
              esc(i.canal), esc(i.direcao), esc(i.resultado || "—"), esc(i.resumo || "—"),
            ];
          }),
          "Nenhuma interação registrada."
        ) + "</div>";
      body().innerHTML = html;

      body().querySelectorAll("[data-m15-wa]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-m15-wa");
          api("/followups/" + id + "/whatsapp-url").then(function (info) {
            var box = document.getElementById("m15WaBox");
            box.innerHTML =
              '<div class="m15-panel"><h3>WhatsApp — revisão humana obrigatória</h3>' +
              (info.aviso_consentimento
                ? '<div class="m15-aviso">Atenção: consentimento de WhatsApp "' +
                  esc(info.consentimento_whatsapp) + '". Avalie antes de contatar.</div>'
                : "") +
              '<div class="m15-mensagem-preview">' + esc(info.mensagem_sugerida) + "</div>" +
              '<a class="m15-btn m15-btn-wa" href="' + esc(info.url) + '" target="_blank" rel="noopener noreferrer">' +
              "Abrir conversa no WhatsApp</a> " +
              '<button class="m15-btn" id="m15WaConfirm">Enviei — registrar interação</button> ' +
              '<button class="m15-btn m15-btn-sec" id="m15WaCancel">Cancelar</button>' +
              '<p class="m15-muted">Nada é enviado automaticamente. O registro só acontece após a sua confirmação.</p></div>';
            box.scrollIntoView({ behavior: "smooth" });
            document.getElementById("m15WaConfirm").addEventListener("click", function () {
              api("/followups/" + id + "/whatsapp-confirmacao", {
                method: "POST",
                body: JSON.stringify({ resultado: "enviado", resumo: "Mensagem de follow-up enviada manualmente" }),
              }).then(function () {
                toast("Interação registrada.");
                render();
              }).catch(showError);
            });
            document.getElementById("m15WaCancel").addEventListener("click", function () {
              box.innerHTML = "";
            });
          }).catch(showError);
        });
      });
      body().querySelectorAll("[data-m15-done]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          api("/followups/" + btn.getAttribute("data-m15-done") + "/concluir", {
            method: "POST", body: JSON.stringify({ resultado: "concluído pela fila" }),
          }).then(function () {
            toast("Follow-up concluído.");
            render();
          }).catch(showError);
        });
      });
      body().querySelectorAll("[data-m15-retry]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-m15-retry");
          openEdit(
            '<div class="m15-panel"><h3>Nova tentativa de follow-up</h3>' +
            '<form class="m15-form" id="m15FormRetry">' +
            fld("Nova data", dateInp("nova_data", "", { required: true }), 4) +
            fld("Observação", inp("observacao", ""), 8) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Reagendar</button> ' +
            closeEditButton() + "</div></form></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15FormRetry").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                var payload = { nova_data: val(f, "nova_data") };
                setIf(payload, "observacao", val(f, "observacao"));
                submitVia(f, function () {
                  return api("/followups/" + id + "/nova-tentativa", {
                    method: "POST", body: JSON.stringify(payload),
                  });
                }, "Nova tentativa agendada.");
              });
            }
          );
        });
      });
      var formFup = document.getElementById("m15FormFup");
      if (formFup) {
        formFup.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          submitVia(f, function () {
            return resolvePerson(val(f, "pessoa")).then(function (p) {
              var payload = { person_id: p.id, tipo: "manual" };
              setIf(payload, "due_date", val(f, "due_date"));
              setIf(payload, "responsavel", val(f, "responsavel"));
              setIf(payload, "observacao", val(f, "observacao"));
              return api("/followups", { method: "POST", body: JSON.stringify(payload) });
            });
          }, "Follow-up criado.");
        });
      }
      var formInt = document.getElementById("m15FormInt");
      if (formInt) {
        formInt.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          submitVia(f, function () {
            return resolvePerson(val(f, "pessoa")).then(function (p) {
              var payload = { person_id: p.id, canal: val(f, "canal"), direcao: val(f, "direcao") };
              setIf(payload, "resultado", val(f, "resultado"));
              setIf(payload, "resumo", val(f, "resumo"));
              return api("/interacoes", { method: "POST", body: JSON.stringify(payload) });
            });
          }, "Interação registrada.");
        });
      }
    });
  };

  // --------------------------------------------------------------- financeiro

  loaders.financeiro = function () {
    var podeGestor = can("gestor");
    return api("/lancamentos?tamanho=50").then(function (data) {
      var items = byId(data.itens);
      var total = data.itens.reduce(function (acc, e) {
        // valores chegam como string monetária ("250.00") — sem float no backend
        return e.tipo === "receita" && e.status === "Recebido"
          ? acc + (parseFloat(e.valor) || 0) : acc;
      }, 0);
      body().innerHTML =
        cards([
          { label: "Lançamentos", value: data.total },
          { label: "Receitas recebidas (página)", value: "R$ " + total.toFixed(2), cls: "m15-ok" },
        ]) +
        '<div class="m15-aviso">Financeiro do núcleo é separado dos dados pessoais: ' +
        "sem nome, telefone ou CPF — apenas IDs técnicos (LAN ↔ ESP/CON/ENC).</div>" +
        table(
          ["Código", "Tipo", "Categoria", "Valor", "Competência", "Status", "Vínculos"]
            .concat(podeGestor ? ["Ações"] : []),
          data.itens.map(function (e) {
            var links = [];
            if (e.spirometry_exam_id) links.push("exame");
            if (e.consultation_id) links.push("consulta");
            if (e.partner_referral_id) links.push("encaminhamento");
            var row = [
              "<strong>" + esc(e.public_code) + "</strong>",
              esc(e.tipo),
              esc(e.categoria || "—"),
              "R$ " + esc(e.valor || "0.00"),
              fmtDate(e.data_competencia) + (e.data_competencia_dia_assumido ? " (dia assumido)" : ""),
              pill(e.status),
              links.join(", ") || "—",
            ];
            if (podeGestor) row.push(editBtn(e.id));
            return row;
          }),
          "Nenhum lançamento no núcleo M15. A fonte financeira legada (Financeiro_Lancamentos) permanece intacta."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeGestor
          ? '<div class="m15-panel"><h3>Novo lançamento (gestor)</h3>' +
            '<p class="m15-muted">Nunca digite nome, telefone ou CPF — a API rejeita PII em categoria/descrição.</p>' +
            '<form class="m15-form" id="m15FormLan">' +
            fld("Tipo", sel("tipo", ["receita", "despesa", "repasse"], "receita"), 3) +
            fld("Valor (R$)", inp("valor", "", 'type="number" step="0.01" min="0.01" required'), 3) +
            fld("Categoria", inp("categoria", "", 'placeholder="ex.: Espirometria"'), 3) +
            fld("Competência", dateInp("data_competencia", "", { parcial: true }),
              { span: 3, help: HELP_DATA_PARCIAL }) +
            grp("Pagamento e baixa",
              fld("Status", sel("status", ["Pendente", "Recebido", "Parcial", "Cortesia", "Cancelado"], "Pendente"), 3) +
              fld("Data de recebimento", dateInp("data_recebimento", ""), 3) +
              fld("Forma de pagamento", sel("forma_pagamento", [["", "—"], "Pix", "Dinheiro", "Cartão", "Outro"], ""), 3) +
              fld("Origem do preço", sel("origem_preco", [["", "—"], "Tabela", "Promoção", "Parceria", "Negociação", "Cortesia"], ""), 3)) +
            grp("Vínculos técnicos (opcionais) — LAN ↔ ESP/CON/ENC",
              fld("ID exame (ESP)", inp("spirometry_exam_id", ""), 4) +
              fld("ID consulta (CON)", inp("consultation_id", ""), 4) +
              fld("ID encaminhamento (ENC)", inp("partner_referral_id", ""), 4),
              "m15-group-tech") +
            fld("Descrição (sem PII)", inp("descricao", ""),
              { span: 12, help: "Texto livre opcional — nunca inclua nome, telefone ou CPF." }) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar lançamento</button></div>' +
            "</form></div>"
          : "");
      var formLan = document.getElementById("m15FormLan");
      if (formLan) {
        formLan.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var f = ev.target;
          var payload = {
            tipo: val(f, "tipo"),
            valor: moneyStr(val(f, "valor")),
            status: val(f, "status"),
            idempotency_key: idemKey(),
          };
          setIf(payload, "categoria", val(f, "categoria"));
          setIf(payload, "descricao", val(f, "descricao"));
          setIf(payload, "data_competencia", val(f, "data_competencia"));
          setIf(payload, "data_recebimento", val(f, "data_recebimento"));
          setIf(payload, "forma_pagamento", val(f, "forma_pagamento"));
          setIf(payload, "origem_preco", val(f, "origem_preco"));
          setIf(payload, "spirometry_exam_id", val(f, "spirometry_exam_id"));
          setIf(payload, "consultation_id", val(f, "consultation_id"));
          setIf(payload, "partner_referral_id", val(f, "partner_referral_id"));
          submitVia(f, function () {
            return api("/lancamentos", { method: "POST", body: JSON.stringify(payload) });
          }, "Lançamento criado.");
        });
      }
      body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var e = items[btn.getAttribute("data-m15-edit")];
          if (!e) return;
          openEdit(
            '<div class="m15-panel"><h3>Editar lançamento ' + esc(e.public_code) + " (gestor)</h3>" +
            '<p class="m15-muted">Valor e tipo são imutáveis — correção monetária é um novo lançamento.</p>' +
            '<form class="m15-form" id="m15EditLan">' +
            fld("Status", sel("status", ["Pendente", "Recebido", "Parcial", "Cortesia", "Cancelado"], e.status), 3) +
            fld("Data de recebimento", dateInp("data_recebimento", e.data_recebimento || ""), 3) +
            fld("Forma de pagamento", sel("forma_pagamento", [["", "—"], "Pix", "Dinheiro", "Cartão", "Outro"],
              e.forma_pagamento || ""), 3) +
            fld("Categoria (sem PII)", inp("categoria", e.categoria || ""), 3) +
            fld("Descrição (sem PII)", inp("descricao", e.descricao || ""),
              { span: 12, help: "Texto livre opcional — nunca inclua nome, telefone ou CPF." }) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
            closeEditButton() + "</div></form></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15EditLan").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                var payload = {};
                setIf(payload, "status", val(f, "status"));
                setIf(payload, "data_recebimento", val(f, "data_recebimento"));
                setIf(payload, "forma_pagamento", val(f, "forma_pagamento"));
                setIf(payload, "categoria", val(f, "categoria"));
                setIf(payload, "descricao", val(f, "descricao"));
                submitVia(f, function () {
                  return api("/lancamentos/" + e.id, { method: "PATCH", body: JSON.stringify(payload) });
                }, "Lançamento atualizado.");
              });
            }
          );
        });
      });
    });
  };

  // ----------------------------------------------------------------- migração

  // Rótulos dos portões de execução (mesmos nomes do backend/CLI).
  var GATE_LABELS = {
    snapshot_registrado: "Snapshot registrado",
    checksum_inalterado: "Checksum inalterado",
    dry_run_realizado: "Dry-run realizado",
    sem_erros_criticos: "Zero erros críticos",
    mapping_version_revisada: "Mapping version revisada",
    execucao_disponivel: "Execução disponível",
    backup_validado: "Backup privado validado",
    evidencia_rollback_disponivel: "Evidência de rollback",
    aprovacao_humana: "Aprovação humana",
    idempotencia: "Idempotência (sem execução prévia)",
  };

  function gatesChecklist(gates) {
    var itens = Object.keys(GATE_LABELS).map(function (nome) {
      var g = gates[nome];
      if (!g) { return ""; }
      return (
        "<li>" + pill(g.ok ? "ok" : "pendente") + " " +
        esc(GATE_LABELS[nome]) +
        ' <span class="m15-muted">' + esc(String(g.detalhe || "")) + "</span></li>"
      );
    }).join("");
    return '<ul class="m15-gates">' + itens + "</ul>";
  }

  function snapshotDetailHtml(det) {
    var s = det.snapshot;
    var dry = s.dry_run || null;
    var resumo = dry && dry.resumo ? dry.resumo : null;
    var execb = s.execucao_batch || null;
    var recon = execb ? execb.reconciliacao : null;
    var rollback = execb ? execb.evidencia_rollback : null;
    var html =
      "<h3>Snapshot " + esc(s.workbook_alias) + " / " + esc(s.sheet_alias) + "</h3>" +
      "<p>" + pill(s.source_type) + " " + pill(s.status) +
      ' <span class="m15-muted">mapping ' + esc(s.mapping_version) +
      (s.mapping_version === s.mapping_version_atual ? "" : " (desatualizada — atual " + esc(s.mapping_version_atual) + ")") +
      " · <code>" + esc((s.sha256 || "").slice(0, 12)) + "…</code></span></p>";
    if (resumo) {
      html +=
        "<p><strong>Dry-run:</strong> total " + esc(String(resumo.total)) +
        " · válidas " + esc(String(resumo.validas)) +
        " · rejeitadas " + esc(String(resumo.rejeitadas)) +
        " · excluídas " + esc(String(resumo.excluidas)) +
        " · ambíguas " + esc(String(resumo.ambiguas)) +
        " · críticos " + esc(String(resumo.criticos)) +
        " · avisos " + esc(String(resumo.avisos)) + "</p>";
    } else {
      html += '<p class="m15-muted">Sem dry-run registrado ainda.</p>';
    }
    html += "<h4>Portões de execução</h4>" + gatesChecklist(det.gates || {});
    html += s.aprovacao
      ? "<p><strong>Aprovação:</strong> " + pill(s.aprovacao.tipo === "aprovacao_execucao" ? "aprovado" : "revogado") +
        ' <span class="m15-muted">' + esc((s.aprovacao.ts_utc || "").slice(0, 19)) + "</span></p>"
      : '<p class="m15-muted">Sem aprovação humana registrada.</p>';
    if (recon) {
      html +=
        "<h4>Reconciliação</h4><p>aceitas " + esc(String(recon.fonte_aceitas)) +
        " · rejeitadas " + esc(String(recon.fonte_rejeitadas)) +
        " · inalteradas " + esc(String(recon.alvo_inalterados)) +
        " · identidades pendentes " + esc(String(recon.identidades_nao_resolvidas)) +
        " · checksum " + pill(recon.checksum && recon.checksum.confere ? "confere" : "divergente") + "</p>";
    }
    if (rollback) {
      html += "<p><strong>Evidência de rollback:</strong> <code>" +
        esc(String(rollback.arquivo_backup || rollback.arquivo || "—")) + "</code></p>";
    }
    html +=
      '<div class="m15-aviso">Execução permanece DESABILITADA nesta interface. ' +
      "Ela só existe na CLI local (<code>python -m app.cli migracao executar</code>), " +
      "após todos os portões verdes, com lote exato, evidência de backup e frase exata digitada." +
      "</div>";
    return html;
  }

  loaders.migracao = function () {
    return Promise.all([
      api("/importacoes?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/identidade/candidatos?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/migracao/snapshots?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
    ]).then(function (r) {
      body().innerHTML =
        '<div class="m15-aviso">Fluxo governado (M15.6A): snapshot privado → validação → mapeamento → ' +
        "dry-run → revisão humana → aprovação → execução explícita (CLI) → reconciliação → rollback. " +
        "Registro de snapshot e dry-run: <code>python -m app.cli migracao --help</code>. " +
        "Dry-run é sempre o padrão; nada aqui grava registro operacional.</div>" +
        '<div class="m15-panel"><h3>Snapshots privados (' + r[2].total + ")</h3>" +
        table(
          ["Fonte", "Workbook/Aba", "Linhas", "SHA-256", "Mapping", "Status", "Ações"],
          r[2].itens.map(function (s) {
            return [
              esc(s.source_type),
              esc(s.workbook_alias) + " / " + esc(s.sheet_alias),
              String(s.row_count),
              "<code>" + esc((s.sha256 || "").slice(0, 12)) + "…</code>",
              esc(s.mapping_version),
              pill(s.status),
              '<button class="m15-btn m15-btn-sec" data-m15-snap="' + esc(s.id) + '">Detalhes</button>',
            ];
          }),
          "Nenhum snapshot registrado — use a CLI (migracao registrar-snapshot)."
        ) + '<div id="m15SnapDetail"></div></div>' +
        '<div class="m15-panel"><h3>Lotes de importação (' + r[0].total + ")</h3>" +
        table(
          ["Fonte", "Arquivo", "SHA-256", "Modo", "Total", "Válidas", "Rejeitadas", "Ambíguas"],
          r[0].itens.map(function (b) {
            return [
              esc(b.source_type), esc(b.source_name),
              "<code>" + esc(b.sha256.slice(0, 12)) + "…</code>",
              pill(b.modo), String(b.total_rows), String(b.valid_rows),
              String(b.rejected_rows), String(b.ambiguous_rows),
            ];
          }),
          "Nenhum lote executado ainda."
        ) + "</div>" +
        '<div class="m15-panel"><h3>Candidatos de identidade pendentes (' + r[1].total + ")</h3>" +
        '<p class="m15-muted">Telefone/nome iguais geram CANDIDATOS — nunca fusão automática. Decisão é humana.</p>' +
        table(
          ["Motivo", "Pessoa", "Candidata", "Origem", "Status"],
          r[1].itens.map(function (c) {
            var det = c.detalhes || {};
            return [
              pill(c.motivo),
              esc(det.person_public_code || c.person_id),
              esc(det.candidate_public_code || c.candidate_person_id || "—"),
              esc(c.origem), pill(c.status),
            ];
          }),
          "Nenhuma ambiguidade pendente."
        ) + "</div>";
      Array.prototype.forEach.call(
        body().querySelectorAll("[data-m15-snap]"),
        function (btn) {
          btn.addEventListener("click", function () {
            var box = document.getElementById("m15SnapDetail");
            box.innerHTML = '<p class="m15-muted">Carregando…</p>';
            api("/migracao/snapshots/" + btn.getAttribute("data-m15-snap"))
              .then(function (det) {
                box.innerHTML = '<div class="m15-panel">' + snapshotDetailHtml(det) + "</div>";
              })
              .catch(function () {
                box.innerHTML = '<div class="m15-aviso">Não foi possível carregar o detalhe do snapshot.</div>';
              });
          });
        }
      );
    });
  };

  // ---------------------------------------------------------------- auditoria

  loaders.auditoria = function () {
    return api("/auditoria?tamanho=50").then(function (data) {
      body().innerHTML =
        '<p class="m15-muted">Trilha append-only, sem PII nos detalhes. Consulta restrita a gestor/admin.</p>' +
        table(
          ["Quando (local)", "Ação", "Entidade", "Request", "Detalhes"],
          data.itens.map(function (a) {
            return [
              esc((a.ts_local || "").replace("T", " ").slice(0, 19)),
              "<strong>" + esc(a.acao) + "</strong>",
              esc(a.entidade || "—"),
              "<code>" + esc(a.request_id || "—") + "</code>",
              esc(a.detalhes ? JSON.stringify(a.detalhes) : "—"),
            ];
          }),
          "Nenhum evento de auditoria ainda."
        );
    }).catch(function (err) {
      if (err.status === 403) {
        body().innerHTML = '<div class="m15-aviso">Auditoria exige papel gestor ou admin.</div>';
        return;
      }
      throw err;
    });
  };

  // ------------------------------------------------------------ administração

  var PAPEIS = ["admin", "gestor", "operacional", "leitura"];

  loaders.admin = function () {
    return api("/admin/usuarios?tamanho=100").then(function (data) {
      var items = byId(data.itens);
      body().innerHTML =
        '<div class="m15-aviso">Administração de usuários — exclusiva do papel admin. ' +
        "Senhas nunca aparecem em URL, log ou auditoria; redefinir senha derruba os tokens antigos na hora.</div>" +
        table(
          ["E-mail", "Nome", "Papéis", "Estado", "Criado em", "Ações"],
          data.itens.map(function (u) {
            return [
              esc(u.email),
              esc(u.nome),
              (u.papeis || []).map(pill).join(" "),
              pill(u.ativo ? "ativo" : "inativo"),
              esc((u.created_at_local || "").slice(0, 10)),
              editBtn(u.id) +
                ' <button class="m15-btn m15-btn-sec" data-m15-senha="' + esc(u.id) + '">Senha</button>',
            ];
          }),
          "Nenhum usuário — o bootstrap inicial é pela CLI local (criar-usuario)."
        ) +
        '<div id="m15EditBox"></div>' +
        '<div class="m15-panel"><h3>Novo usuário</h3><form class="m15-form" id="m15FormUser">' +
        fld("Nome", inp("nome", "", 'required minlength="2"'), 4) +
        fld("E-mail", inp("email", "", 'type="email" required'), 4) +
        fld("Papel", sel("papel", PAPEIS, "leitura"), 4) +
        fld("Senha (mín. 10)", inp("senha", "", 'type="password" required minlength="10" autocomplete="new-password"'), 6) +
        fld("Confirmar senha", inp("senha2", "", 'type="password" required minlength="10" autocomplete="new-password"'), 6) +
        '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Criar usuário</button></div>' +
        "</form>" +
        '<p class="m15-muted">Menor privilégio: comece por leitura/operacional; gestor cuida de financeiro e ' +
        "parcerias; admin administra usuários. Nenhuma conta é criada automaticamente.</p></div>";
      document.getElementById("m15FormUser").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        if (val(f, "senha") !== val(f, "senha2")) {
          toast("As senhas não conferem.", "erro");
          return;
        }
        submitVia(f, function () {
          return api("/admin/usuarios", {
            method: "POST",
            body: JSON.stringify({
              nome: val(f, "nome"), email: val(f, "email"),
              papel: val(f, "papel"), senha: f.elements.senha.value,
            }),
          });
        }, "Usuário criado.");
      });
      body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var u = items[btn.getAttribute("data-m15-edit")];
          if (!u) return;
          openEdit(
            '<div class="m15-panel"><h3>Editar usuário — ' + esc(u.nome) + "</h3>" +
            '<form class="m15-form" id="m15EditUser">' +
            fld("Papel", sel("papel", PAPEIS, (u.papeis || [])[0] || "leitura"), 6) +
            fld("Estado", sel("ativo", [["true", "ativo"], ["false", "inativo"]], u.ativo ? "true" : "false"), 6) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
            closeEditButton() + "</div></form>" +
            '<p class="m15-muted">Inativar derruba os tokens do usuário imediatamente. ' +
            "O último admin ativo nunca pode ser rebaixado ou inativado.</p></div>",
            function (box) {
              wireClose(box);
              box.querySelector("#m15EditUser").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                submitVia(f, function () {
                  return api("/admin/usuarios/" + u.id, {
                    method: "PATCH",
                    body: JSON.stringify({
                      papel: val(f, "papel"),
                      ativo: val(f, "ativo") === "true",
                    }),
                  });
                }, "Usuário atualizado.");
              });
            }
          );
        });
      });
      body().querySelectorAll("[data-m15-senha]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var u = items[btn.getAttribute("data-m15-senha")];
          if (!u) return;
          openEdit(
            '<div class="m15-panel"><h3>Redefinir senha — ' + esc(u.nome) + "</h3>" +
            '<form class="m15-form" id="m15FormSenha">' +
            fld("Nova senha (mín. 10)", inp("senha", "", 'type="password" required minlength="10" autocomplete="new-password"'), 6) +
            fld("Confirmar", inp("senha2", "", 'type="password" required minlength="10" autocomplete="new-password"'), 6) +
            '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Redefinir senha</button> ' +
            closeEditButton() + "</div></form>" +
            '<p class="m15-muted">Todos os tokens antigos deste usuário serão revogados imediatamente.</p></div>',
            function (box) {
              wireClose(box);
              box.querySelector("#m15FormSenha").addEventListener("submit", function (ev) {
                ev.preventDefault();
                var f = ev.target;
                if (f.elements.senha.value !== f.elements.senha2.value) {
                  toast("As senhas não conferem.", "erro");
                  return;
                }
                submitVia(f, function () {
                  return api("/admin/usuarios/" + u.id + "/redefinir-senha", {
                    method: "POST",
                    body: JSON.stringify({ senha: f.elements.senha.value }),
                  });
                }, "Senha redefinida — tokens antigos revogados.");
              });
            }
          );
        });
      });
    });
  };

  // -------------------------------------------------------------- ciclo geral

  function render() {
    if (!body()) return;
    if (!state.access.secure) {
      body().innerHTML = blockedNotice();
      return;
    }
    if (!state.token) {
      body().innerHTML =
        '<div class="m15-empty">Entre com e-mail e senha (ou cole um token da CLI) para operar o núcleo M15.</div>';
      return;
    }
    var loader = loaders[state.tab] || loaders.visao;
    body().innerHTML = '<div class="m15-empty">Carregando…</div>';
    loader().then(function () { attachDates(); }).catch(showError);
  }

  function checkApi() {
    var el = document.getElementById("m15ApiStatus");
    if (!el) return;
    fetch(state.apiBase + "/health").then(function (r) { return r.json(); }).then(function (h) {
      el.textContent = "Proxy/API: " + h.status + " (" + h.ambiente + ", banco " + h.banco + ")";
    }).catch(function () {
      el.textContent = "Proxy/API: indisponível — veja nucleo-m15/README.md";
    });
  }

  // ------------------------------------------------------------ bootstrap

  function activate(config) {
    // Fail-closed: configuração pública nunca pode transformar o frontend em
    // cliente de URL absoluta/outro host. Desenvolvimento troca só o upstream
    // server-side; a rota do navegador permanece a mesma.
    if (config && config.api_base === "/painel-soprolife/api/m15") {
      state.apiBase = config.api_base;
    }

    // Classifica a origem ANTES de montar qualquer UI: cabeçalho, área de
    // login e corpo dependem do modo de acesso (https/localdev/blocked).
    classifyAccess();

    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "css/m15.css";
    document.head.appendChild(link);

    var nav = document.querySelector(".sidebar .nav");
    if (!nav) return;
    var label = document.createElement("div");
    label.className = "nav-group-label";
    label.textContent = "M15 Beta";
    nav.appendChild(label);
    nav.appendChild(navButton());

    var main = document.querySelector("main") || document.querySelector(".content");
    var anySection = document.querySelector(".section");
    var container = anySection ? anySection.parentElement : main;
    if (!container) return;
    container.appendChild(buildSection());

    // migração de segurança: limpa qualquer token persistido por versão antiga
    try { localStorage.removeItem("soproM15Token"); } catch (e) { /* noop */ }

    renderAuthArea();
    renderTabs();
    document.getElementById("m15Tabs").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-m15-tab]");
      if (!btn) return;
      state.tab = btn.getAttribute("data-m15-tab");
      document.querySelectorAll(".m15-tab").forEach(function (t) { t.classList.remove("active"); });
      btn.classList.add("active");
      render();
    });
    render();
  }

  function boot() {
    fetch(CONFIG_URL).then(function (r) { return r.json(); }).catch(function () { return {}; })
      .then(function (config) {
        var enabled = (config && config.enabled === true) ||
          localStorage.getItem("soproM15") === "on";
        if (!enabled) return; // flag desligada: painel permanece intocado
        activate(config);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
