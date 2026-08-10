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
 * - Sessão (M21): login por e-mail+senha (POST /auth/token) em um <form> de
 *   verdade, compatível com gerenciador de senhas do navegador. A sessão vive
 *   num cookie de mesma origem emitido pelo servidor — HttpOnly, Secure,
 *   SameSite=Strict e Path restrito — então recarregar a página NÃO derruba
 *   mais o login: GET /auth/sessao restaura a identidade no carregamento.
 *   NENHUM token vai para localStorage/sessionStorage; o cookie é invisível
 *   ao JavaScript e o csrf vive só em memória. "Sair" chama POST /auth/logout,
 *   que revoga a sessão no servidor e apaga o cookie.
 *   O token colado da CLI continua existindo para uso avançado e continua
 *   somente em memória.
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
    token: "", // token bearer da CLI — em memória apenas, nunca persistido
    // M21 — sessão de navegador: o cookie é HttpOnly (invisível aqui). Só o
    // csrf fica em memória, e apenas para os métodos que mudam estado.
    sessao: false,
    csrf: "",
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

  // Autenticado por qualquer um dos dois caminhos: cookie de sessão (normal)
  // ou token bearer colado da CLI (avançado).
  function autenticado() { return !!state.token || state.sessao === true; }

  var STATE_CHANGING = { POST: 1, PATCH: 1, PUT: 1, DELETE: 1 };

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

  function readableApiError(body, status) {
    var envelope = body && body.erro;
    var raw = envelope && envelope.mensagem;
    var message = typeof raw === "string" ? raw :
      (raw && typeof raw.mensagem === "string" ? raw.mensagem : "");
    if (!message && body && typeof body.error === "string") message = body.error;
    if (!message) message = "A operação foi recusada pela API (HTTP " + status + ").";
    return {
      message: message,
      code: (envelope && envelope.codigo) ||
        (raw && typeof raw.codigo === "string" ? raw.codigo : "http_" + status)
    };
  }

  // M25.17 — lê o nome do arquivo de um Content-Disposition.
  //
  // Prefere `filename*` (RFC 5987, UTF-8 percent-encoded), que é quem
  // carrega os acentos; `filename` ASCII é a reserva para o caso de o
  // servidor mandar só ele. Devolve string vazia quando não dá para
  // confiar no valor — aí quem chama decide o fallback.
  function nomeDoContentDisposition(valor) {
    if (!valor) return "";
    var estendido = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(valor);
    var nome = "";
    if (estendido) {
      try {
        nome = decodeURIComponent(estendido[1]);
      } catch (e) {
        nome = "";
      }
    }
    if (!nome) {
      var simples = /filename\s*=\s*"([^"]*)"/i.exec(valor)
        || /filename\s*=\s*([^;]+)/i.exec(valor);
      if (simples) nome = simples[1].trim();
    }
    // Última barreira do lado do cliente: separador de caminho e caractere
    // de controle nunca chegam ao atributo `download`. Espaço e hífen são
    // preservados — fazem parte do nome pedido ("Fulano - Assinado.pdf").
    // O filtro é por código de caractere, e não por classe de regex com
    // escapes hexadecimais, para que nenhuma edição futura deste arquivo
    // consiga gravar um byte de controle real no lugar do escape.
    nome = nome.split("").filter(function (c) {
      var codigo = c.charCodeAt(0);
      return codigo >= 32 && codigo !== 127 && c !== '/' && c !== '\\';
    }).join("").trim();
    return nome === "." || nome === ".." ? "" : nome;
  }

  function api(path, options) {
    // Guarda de contexto seguro (M15.5A): em origem bloqueada NENHUMA
    // requisição de autenticação sai do navegador — nem senha, nem token.
    if (!state.access.secure && path.indexOf("/auth/") === 0) {
      return Promise.reject(new Error(
        "Login bloqueado em origem HTTP insegura. Abra o endereço HTTPS privado do painel."
      ));
    }
    options = Object.assign({}, options || {});
    var responseType = options.responseType || "json";
    delete options.responseType;
    // FormData precisa preservar o boundary gerado pelo navegador. Definir
    // Content-Type manualmente quebraria o upload multipart dos laudos.
    var isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    var base = isFormData ? {} : { "Content-Type": "application/json" };
    // Bearer só quando existe de fato: mandar "Bearer " vazio faria a API
    // recusar antes de chegar a olhar o cookie de sessão.
    if (state.token) base.Authorization = "Bearer " + state.token;
    var metodo = String(options.method || "GET").toUpperCase();
    if (!state.token && state.csrf && STATE_CHANGING[metodo]) {
      base["X-CSRF-Token"] = state.csrf;
    }
    options.headers = Object.assign(base, options.headers || {});
    // same-origin: o cookie de sessão viaja para o proxy do próprio painel e
    // para mais nenhum lugar.
    options.credentials = "same-origin";
    return fetch(state.apiBase + path, options).then(function (resp) {
      if (responseType === "blob") {
        if (!resp.ok) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            var detail = readableApiError(body, resp.status);
            var err = new Error(detail.message);
            err.code = detail.code;
            err.status = resp.status;
            if (resp.status === 401 && (state.sessao || state.token)) {
              return encerrarSessao("Sua sessão expirou. Entre novamente.").then(
                function () { throw err; }
              );
            }
            throw err;
          });
        }
        var contentType = (resp.headers.get("Content-Type") || "").toLowerCase();
        if (contentType.indexOf("application/pdf") !== 0) {
          throw new Error("A API não devolveu um PDF válido para visualização.");
        }
        // M25.17 — o nome do arquivo é decidido pelo SERVIDOR
        // (Content-Disposition) e precisa sobreviver até o clique de
        // download. Como a resposta vira um Blob e depois uma object URL, o
        // navegador perde o cabeçalho: sem carregá-lo junto, o arquivo cai
        // na pasta do usuário com um nome gerado, que foi exatamente o
        // problema relatado.
        var disposicao = resp.headers.get("Content-Disposition") || "";
        return resp.blob().then(function (blob) {
          blob.nomeSugerido = nomeDoContentDisposition(disposicao);
          return blob;
        });
      }
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var detail = readableApiError(body, resp.status);
          var err = new Error(detail.message);
          err.code = detail.code;
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  // Entrega autenticada de conteúdo binário. Usa exatamente a mesma sessão,
  // cookie HttpOnly, token em memória, origem e tratamento de 401 do cliente
  // JSON; o consumidor recebe somente um Blob, nunca uma URL pública.
  function apiBlob(path, options) {
    return api(path, Object.assign({}, options || {}, { responseType: "blob" }));
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

  // Cadastro novo é SEMPRE na Central de Cadastros — as abas do núcleo
  // mantêm listagem e edição; o botão abaixo leva à central com a aba certa.
  /* Botão contextual: abre a Central no fluxo canônico. `prefill` leva o
   * tipo/modo do "Novo atendimento" (M20) — nunca dado pessoal. */
  function centralBtn(tab, label, prefill) {
    var attrPrefill = prefill
      ? ' data-m15-central-prefill="' + esc(JSON.stringify(prefill)) + '"' : "";
    return '<div class="m15-central-link">' +
      '<button class="m15-btn" type="button" data-m15-central="' + esc(tab) + '"' +
      attrPrefill + ">" +
      esc(label) + "</button>" +
      '<span class="m15-muted">Cadastros novos acontecem na Central de Cadastros — um único fluxo para toda a operação.</span>' +
      "</div>";
  }

  function wireClose(box) {
    var btn = box.querySelector("[data-m15-fechar]");
    if (btn) btn.addEventListener("click", function () { box.innerHTML = ""; });
  }

  // ------------------------------------------------------------ estrutura

  // [id, rótulo, papel mínimo p/ VER a aba] — o servidor revalida tudo.
  //
  // M19: o Núcleo deixou de ser uma segunda aplicação operacional. As abas
  // de Pessoas, Leads, Espirometrias, Consultas, Clínicas, Encaminhamentos,
  // Follow-up e Financeiro saíram da navegação ordinária — a operação
  // acontece em CRM Pacientes e Acompanhamento, Financeiro e Central de
  // Cadastros. As listagens técnicas continuam existindo, agrupadas numa
  // única aba de INSPEÇÃO restrita a administrador; tabelas, APIs e
  // histórico de auditoria permanecem intactos.
  var TABS = [
    ["visao", "Diagnóstico técnico", "leitura"],
    ["dados", "Inspeção técnica de dados", "admin"],
    ["migracao", "Migração", "leitura"],
    ["auditoria", "Auditoria", "gestor"],
    ["admin", "Administração", "admin"],
  ];

  // Sub-abas da inspeção técnica: reaproveitam os mesmos carregadores, sem
  // duplicar CRM nem Financeiro como sistema paralelo.
  var SUBTABS_DADOS = [
    ["pessoas", "Pessoas"],
    ["leads", "Leads"],
    ["espirometrias", "Espirometrias"],
    ["consultas", "Consultas"],
    ["parceiros", "Clínicas e Parceiros"],
    ["encaminhamentos", "Pacientes de Parceiros"],
    ["followup", "Follow-up"],
    ["financeiro", "Financeiro"],
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
      "<span>Núcleo administrativo</span>";
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
    if (autenticado() && state.user) {
      var origem = state.token
        ? "Token da CLI — vive apenas em memória nesta aba."
        : (state.sessaoPersistente
          ? "Sessão salva neste dispositivo — continua após recarregar e reabrir o navegador."
          : "Sessão deste navegador — continua após recarregar; encerra ao fechar o navegador.");
      area.innerHTML =
        '<div class="m15-session">' +
        '  <span class="m15-session-user"><strong>Conectado:</strong> ' + esc(state.user.nome) +
        '    <span class="m15-pill">' + esc((state.user.papeis || []).join(", ")) + "</span></span>" +
        '  <button class="m15-btn m15-btn-sec" id="m15Sair" type="button">Sair</button>' +
        '  <span class="m15-muted" id="m15ApiStatus"></span>' +
        '  <span class="m15-muted">' + esc(origem) + "</span>" +
        "</div>";
      document.getElementById("m15Sair").addEventListener("click", function () {
        encerrarSessao();
      });
    } else {
      // FORMULÁRIO REAL (M21): <form> com submit de verdade, name e
      // autocomplete padrão, para que Chrome e Firefox reconheçam o par de
      // credenciais e ofereçam salvar depois do login bem-sucedido.
      // O campo de token da CLI fica fora deste form, dentro de <details>,
      // para não confundir a heurística do gerenciador de senhas.
      area.innerHTML =
        '<div class="m15-login">' +
        '  <form class="m15-login-grid" id="m15LoginForm" method="post" action="#" autocomplete="on">' +
        '    <label class="m15-field" for="m15Email"><span class="m15-field-label">E-mail</span>' +
        '      <input type="email" id="m15Email" name="email" autocomplete="username" ' +
        '        required placeholder="voce@soprolife.com.br"></label>' +
        '    <label class="m15-field" for="m15Senha"><span class="m15-field-label">Senha</span>' +
        '      <input type="password" id="m15Senha" name="password" autocomplete="current-password" ' +
        '        required placeholder="••••••••••"></label>' +
        '    <button class="m15-btn" id="m15Entrar" type="submit">Entrar</button>' +
        '    <label class="m15-field m15-field-check m15-login-manter" for="m15Manter">' +
        '      <input type="checkbox" id="m15Manter" name="manter_conectado" value="1">' +
        '      <span>Manter conectado neste dispositivo</span></label>' +
        "  </form>" +
        '  <span class="m15-muted m15-login-status" id="m15ApiStatus">Proxy/API: verificando…</span>' +
        '  <details class="m15-login-alt">' +
        '    <summary>Entrar com token da CLI (avançado)</summary>' +
        '    <div class="m15-login-grid">' +
        '      <label class="m15-field" for="m15Token"><span class="m15-field-label">Token da API ' +
        "(python -m app.cli emitir-token)</span>" +
        '        <input type="password" id="m15Token" autocomplete="off" ' +
        '          placeholder="cole o token da API aqui"></label>' +
        '      <button class="m15-btn m15-btn-sec" id="m15TokenSave" type="button">Usar token</button>' +
        "    </div>" +
        '    <span class="m15-muted">O token colado vive apenas em memória — nunca em ' +
        "localStorage/sessionStorage.</span>" +
        "  </details>" +
        '  <span class="m15-muted">Sem "manter conectado", a sessão termina quando o ' +
        "navegador fecha. Nenhum token é gravado no navegador em nenhum dos casos.</span>" +
        "</div>";

      var form = document.getElementById("m15LoginForm");
      form.addEventListener("submit", function (ev) {
        ev.preventDefault(); // navegação é nossa; o submit real dispara o gerenciador
        var campoEmail = document.getElementById("m15Email");
        var campoSenha = document.getElementById("m15Senha");
        var email = campoEmail.value.trim();
        var senha = campoSenha.value;
        if (!email || !senha) { toast("Informe e-mail e senha.", "erro"); return; }
        var btn = document.getElementById("m15Entrar");
        btn.disabled = true;
        var manter = !!document.getElementById("m15Manter").checked;
        // senha só trafega no corpo do POST via proxy local — nunca em URL.
        // O campo NÃO é limpo aqui: o navegador precisa ver o par credencial
        // + resposta de sucesso para oferecer salvar. Ele é limpo depois.
        api("/auth/token", {
          method: "POST",
          body: JSON.stringify({ email: email, password: senha, manter_conectado: manter }),
        }).then(function (resp) {
          // Sessão por cookie: o token bearer da resposta é deliberadamente
          // IGNORADO no navegador — nada de credencial em memória longa.
          state.token = "";
          state.sessao = true;
          state.csrf = resp && resp.csrf ? resp.csrf : "";
          state.sessaoPersistente = !!(resp && resp.sessao && resp.sessao.persistente);
          return afterAuth();
        }).catch(function (err) {
          btn.disabled = false;
          toast("Erro no login: " + (err.message || err), "erro");
        }).finally(function () {
          // Só depois de a resposta terminar: gerenciadores de senha veem o
          // submit completo, mas a credencial nunca permanece no DOM.
          campoSenha.value = "";
        });
      });
      document.getElementById("m15TokenSave").addEventListener("click", function () {
        var tokenInput = document.getElementById("m15Token");
        // token vive só na variável de módulo — nunca em storage persistente
        state.token = tokenInput.value.trim();
        tokenInput.value = "";
        if (!state.token) { toast("Cole um token primeiro.", "erro"); return; }
        state.sessao = false;
        state.csrf = "";
        afterAuth();
      });
      checkApi();
    }
  }

  // Encerra a sessão de verdade: o servidor revoga a linha e apaga o cookie.
  // Depois disso outro usuário pode entrar no mesmo navegador sem herdar nada.
  function encerrarSessao(mensagem) {
    var eraSessao = state.sessao === true;
    var limpar = function () {
      state.token = "";
      state.sessao = false;
      state.csrf = "";
      state.sessaoPersistente = false;
      state.user = null;
      renderAuthArea();
      renderTabs();
      render();
      notifySession();
      if (mensagem) toast(mensagem, "erro");
    };
    if (!eraSessao) { limpar(); return Promise.resolve(); }
    return api("/auth/logout", { method: "POST" })
      .catch(function () { /* sair nunca falha para o operador */ })
      .then(limpar);
  }

  // Restauração automática ao carregar a página (o que faz o F5 não deslogar).
  function restaurarSessao() {
    if (!state.access.secure) return Promise.resolve(false);
    return api("/auth/sessao").then(function (resp) {
      state.sessao = true;
      state.csrf = resp && resp.csrf ? resp.csrf : "";
      state.sessaoPersistente = !!(resp && resp.sessao && resp.sessao.persistente);
      state.user = (resp && resp.usuario) || null;
      return true;
    }).catch(function () {
      state.sessao = false;
      state.csrf = "";
      state.user = null;
      return false; // sem sessão viva: segue no formulário de login
    });
  }

  // Ouvintes externos de sessão (Central de Cadastros) — avisados a cada
  // login/logout para re-renderizar sem acoplamento direto.
  var sessionListeners = [];
  function notifySession() {
    sessionListeners.forEach(function (cb) {
      try { cb(); } catch (e) { /* ouvinte não pode quebrar o núcleo */ }
    });
  }

  function afterAuth() {
    return api("/auth/me").then(function (me) {
      state.user = me;
    }).catch(function () {
      state.user = null; // segue sem ocultar ações; servidor decide (403)
    }).then(function () {
      // Sucesso confirmado: só AGORA o formulário (e com ele o campo de
      // senha) sai do DOM. A senha nunca entrou em state.

      renderAuthArea();
      renderTabs();
      render();
      notifySession();
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
        '<div class="m15-aviso">Área técnica. A operação do dia a dia acontece em ' +
        "<strong>CRM Pacientes e Acompanhamento</strong> (pacientes, contato e follow-up), " +
        "<strong>Financeiro</strong> (lançamentos e conciliação) e <strong>Central de Cadastros</strong> " +
        "(único lugar de cadastro novo). Aqui ficam apenas migração, auditoria, administração e " +
        "diagnóstico. Registros de demonstração seguem marcados como “sintético”.</div>";
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
          "Nenhuma pessoa cadastrada. Use a Central de Cadastros."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeEditar
          ? centralBtn("atendimento", "+ Nova pessoa / paciente",
                       { somente_paciente: true })
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
          "Nenhum lead. Cadastre pela Central de Cadastros."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeEditar ? centralBtn("lead", "+ Novo lead") : "");
      document.getElementById("m15LeadsEtapa").addEventListener("change", function (ev) {
        state.filters.leadsEtapa = ev.target.value;
        render();
      });
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

  /* O status GRAVADO do registro entra na lista mesmo quando não é um dos
   * valores oferecidos para registros novos. Sem isto, abrir e salvar um
   * exame histórico (ex.: "Liberado") reescreveria o status silenciosamente
   * para a primeira opção do select — o valor histórico tem de sobreviver. */
  function statusComAtual(opcoes, atual) {
    if (!atual) return opcoes;
    var ja = opcoes.some(function (o) {
      return String(Array.isArray(o) ? o[0] : o) === String(atual);
    });
    if (ja) return opcoes;
    var rotulo = window.SoproStatus ? window.SoproStatus.espirometria(atual) : atual;
    return opcoes.concat([[atual, rotulo + " (valor atual)"]]);
  }

  function attendanceLoader(kind) {
    var isExam = kind === "espirometrias";
    var statusKey = isExam ? "espStatus" : "conStatus";
    var statusList = isExam
      ? ["Aguardando", "Realizado", "Laudo Liberado", "Cancelado", "Remarcado"]
      : ["Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"];
    // Exibição SEMPRE pelo formatador único (M20): valor gravado intacto,
    // rótulo normalizado. "Liberado" nunca é tocado.
    var statusOpts = isExam && window.SoproStatus
      ? window.SoproStatus.opcoesEspirometria(statusList) : statusList;
    var statusRotulo = isExam && window.SoproStatus
      ? window.SoproStatus.espirometria : function (v) { return v; };
    return function () {
      var status = state.filters[statusKey] || "";
      var podeEditar = can("operacional");
      return api("/" + kind + "?tamanho=50" + (status ? "&status=" + encodeURIComponent(status) : ""))
        .then(function (data) {
        var items = byId(data.itens);
        body().innerHTML =
          '<div class="m15-filtros"><select id="m15AttStatus">' +
          optionsHtml([["", "todos os status"]].concat(statusOpts), status) +
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
                pill(isExam ? (e.status_exibicao || statusRotulo(e.status)) : e.status),
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
            ? centralBtn("atendimento",
                isExam ? "+ Nova espirometria" : "+ Nova consulta",
                { tipo: isExam ? "espirometria_soprolife" : "consulta_soprolife" })
            : "");
        document.getElementById("m15AttStatus").addEventListener("change", function (ev) {
          state.filters[statusKey] = ev.target.value;
          render();
        });
        body().querySelectorAll("[data-m15-edit]").forEach(function (btn) {
          btn.addEventListener("click", function () {
            var e = items[btn.getAttribute("data-m15-edit")];
            if (!e) return;
            openEdit(
              '<div class="m15-panel"><h3>Editar ' + esc(e.public_code) + "</h3>" +
              '<form class="m15-form" id="m15EditAtt">' +
              fld("Status", sel("status", statusComAtual(statusOpts, e.status), e.status), 3) +
              fld("Data", dateInp("data", (isExam ? e.data_exame : e.data_consulta) || "",
                { parcial: true }), { span: 3, help: HELP_DATA_PARCIAL }) +
              (isExam
                ? fld("Broncodilatador", sel("broncodilatador",
                    [["", "não informado"], ["true", "com BD"], ["false", "sem BD"]],
                    e.broncodilatador == null ? "" : String(e.broncodilatador)), 3) +
                  fld("Responsável", inp("responsavel", e.responsavel || ""), 3)
                : fld("Profissional", inp("profissional", e.profissional || ""), 6)) +
              fld("Observação", inp("observacao", e.observacao || ""), true) +
              '<div class="m15-form-full m15-actions"><button class="m15-btn" type="submit">Salvar</button> ' +
              closeEditButton() + "</div></form>" +
              '<p class="m15-muted">Marcar como ' +
              (isExam
                ? '"' + (window.SoproStatus
                    ? window.SoproStatus.espirometria("Realizado") : "Realizado") + '"'
                : '"Realizada"') +
              " agenda o follow-up de 6 meses automaticamente.</p></div>",
              function (box) {
                wireClose(box);
                box.querySelector("#m15EditAtt").addEventListener("submit", function (ev2) {
                  ev2.preventDefault();
                  var f = ev2.target;
                  var payload = {};
                  // Status só viaja quando o humano REALMENTE o mudou. Assim
                  // um valor histórico fora do vocabulário atual (ex.:
                  // "Liberado") continua exatamente como está ao editar
                  // qualquer outro campo do registro.
                  if (val(f, "status") !== e.status) {
                    setIf(payload, "status", val(f, "status"));
                  }
                  setIf(payload, isExam ? "data_exame" : "data_consulta", val(f, "data"));
                  if (isExam) {
                    setIf(payload, "responsavel", val(f, "responsavel"));
                    if (val(f, "broncodilatador") !== "") {
                      payload.broncodilatador = val(f, "broncodilatador") === "true";
                    }
                  } else {
                    setIf(payload, "profissional", val(f, "profissional"));
                  }
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
        (podeEditar ? centralBtn("clinica", "+ Novo parceiro / clínica") : "") +
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
        (podeEditar ? centralBtn("clinica", "+ Nova unidade física") : "") +
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
        (podeEditar ? centralBtn("contato-b2b", "+ Novo contato B2B") : "") +
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
    return api("/lancamentos?tamanho=50&incluir_contexto=true").then(function (data) {
      var items = byId(data.itens);
      var total = data.itens.reduce(function (acc, e) {
        // valores chegam como string monetária ("250.00") — sem float no backend
        return e.tipo === "receita" && e.status === "Recebido"
          ? acc + (parseFloat(e.valor) || 0) : acc;
      }, 0);
      // Contexto do paciente derivado por relação técnica (nunca armazenado
      // no lançamento): LAN → ESP/CON/ENC → pessoa.
      function vinculoHtml(e) {
        var ctx = e.contexto || {};
        var ref = ctx.exame || ctx.consulta || ctx.encaminhamento;
        if (!ref) return "—";
        var pessoa = ref.pessoa
          ? " · " + esc(ref.pessoa.nome_completo) +
            ' <span class="m15-muted">(' + esc(ref.pessoa.public_code) + ")</span>"
          : "";
        return "<strong>" + esc(ref.public_code) + "</strong>" + pessoa;
      }
      body().innerHTML =
        cards([
          { label: "Lançamentos", value: data.total },
          { label: "Receitas recebidas (página)", value: "R$ " + total.toFixed(2), cls: "m15-ok" },
        ]) +
        '<div class="m15-aviso">Financeiro do núcleo é separado dos dados pessoais: ' +
        "sem nome, telefone ou CPF gravados — o paciente exibido ao lado vem da " +
        "relação técnica (LAN ↔ ESP/CON/ENC) no momento da consulta.</div>" +
        table(
          ["Código", "Tipo", "Categoria", "Valor", "Competência", "Status", "Vínculo · Paciente"]
            .concat(podeGestor ? ["Ações"] : []),
          data.itens.map(function (e) {
            var row = [
              "<strong>" + esc(e.public_code) + "</strong>",
              esc(e.tipo),
              esc(e.categoria || "—"),
              "R$ " + esc(e.valor || "0.00"),
              fmtDate(e.data_competencia) + (e.data_competencia_dia_assumido ? " (dia assumido)" : ""),
              pill(e.status),
              vinculoHtml(e),
            ];
            if (podeGestor) row.push(editBtn(e.id));
            return row;
          }),
          "Nenhum lançamento no núcleo M15. A fonte financeira legada (Financeiro_Lancamentos) permanece intacta."
        ) +
        '<div id="m15EditBox"></div>' +
        (podeGestor ? centralBtn("financeiro", "+ Novo lançamento") : "");
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

  function multiSheetDetailHtml(det) {
    var totals = det.totals || {};
    var recon = det.reconciliation_preview || {};
    var finance = det.financial_relationship_coverage || {};
    var reviews = det.review_summary || {};
    var decisions = det.decision_summary || {};
    var reviewRows = Object.keys(reviews).sort().map(function (category) {
      var item = reviews[category] || {};
      var state = decisions[category] || {};
      var hasDecisionState = Object.keys(state).length > 0;
      return [
        esc(category), String(item.count || 0),
        String(hasDecisionState
          ? ((state.pendente || 0) + (state.adiado || 0))
          : (item.pending || 0)),
        String(hasDecisionState
          ? ((state.registrada || 0) + (state.resolvido || 0) + (state.excluido || 0))
          : (item.registered || 0)),
      ];
    });
    return (
      "<h3>Dry-run multiaba</h3>" +
      "<p>" + pill(det.status) +
      ' <span class="m15-muted">mapping ' + esc(det.mapping_version || "—") +
      " · adapters " + esc(det.adapters_version || "—") + "</span></p>" +
      "<p><strong>Fontes:</strong> total " + esc(String(totals.source_total || 0)) +
      " · válidas " + esc(String(totals.valid || 0)) +
      " · rejeitadas " + esc(String(totals.rejected || 0)) +
      " · excluídas " + esc(String(totals.excluded || 0)) +
      " · revisão " + esc(String(totals.pending_review || 0)) +
      " · duplicadas " + esc(String(totals.duplicates || 0)) +
      " · existentes " + esc(String(totals.already_existing || 0)) + "</p>" +
      "<p><strong>Reconciliação prévia:</strong> " +
      pill(recon.fechamento_ok ? "fechamento ok" : "divergência") +
      " · contabilizadas " + esc(String(recon.linhas_contabilizadas || 0)) +
      " · diferença " + esc(String(recon.diferenca || 0)) +
      " · gravações operacionais " +
      esc(String(recon.registros_operacionais_gravados || 0)) + "</p>" +
      "<p><strong>Financeiro:</strong> relações técnicas " +
      esc(String(finance.vinculadas_tecnicamente || 0)) +
      " · não resolvidas " + esc(String(finance.nao_resolvidas || 0)) +
      " · fontes não autorizadas " +
      esc(String(finance.bloqueadas_fonte_nao_autorizada || 0)) + "</p>" +
      "<h4>Revisão humana protegida</h4>" +
      table(
        ["Categoria", "Total", "Pendente", "Registrada"],
        reviewRows,
        "Nenhum item de revisão.",
      ) +
      multiSheetExecutionHtml(det.execution || {}) +
      '<div class="m15-aviso">Execução real SOMENTE pela CLI local ' +
      "(<code>migracao executar-multiaba</code>), com admin exato, portões " +
      "completos e frase digitada. Esta tela não possui botão de execução " +
      "e não exibe valores de origem.</div>"
    );
  }

  function multiSheetExecutionHtml(ex) {
    var hash = ex.plan_hash ? String(ex.plan_hash).slice(0, 16) + "…" : "—";
    var recon = "—";
    if (ex.executado) {
      recon = ex.reconciliacao_ok === true
        ? "fechada"
        : (ex.reconciliacao_ok === false ? "divergente" : "pendente");
    }
    return (
      "<h4>Execução final (M15.6C) — somente status</h4>" +
      "<p><strong>Prontidão:</strong> " +
      pill(ex.pronto_para_preflight ? "pronta para preflight" : "bloqueada") +
      " · revisões pendentes " + esc(String(ex.revisoes_pendentes || 0)) +
      " · plano de rollback " +
      pill(ex.plano_rollback_gerado ? "gerado" : "não gerado") + "</p>" +
      "<p><strong>Plan hash:</strong> <code>" + esc(hash) + "</code>" +
      " · <strong>Backup:</strong> " +
      esc(ex.backup || "validação somente pela CLI local") + "</p>" +
      "<p><strong>Execução:</strong> " +
      pill(ex.executado ? (ex.status_execucao || "executado") : "não executada") +
      " · <strong>Reconciliação:</strong> " + pill(recon) +
      " · <strong>Evidência de rollback:</strong> " +
      pill(ex.executado
        ? (ex.rollback_realizado
          ? "rollback realizado"
          : (ex.evidencia_rollback_registrada ? "registrada" : "ausente"))
        : "—") + "</p>"
    );
  }

  loaders.migracao = function () {
    return Promise.all([
      api("/importacoes?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/identidade/candidatos?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/migracao/snapshots?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/migracao/multiaba?limite=25").catch(function () { return { items: [], total: 0 }; }),
    ]).then(function (r) {
      body().innerHTML =
        '<div class="m15-aviso">Fluxo governado (M15.6C): snapshot privado → adapters explícitos → staging multiaba → ' +
        "dry-run → revisão humana → preflight → backup → execução explícita (somente CLI, admin, frase digitada) → " +
        "reconciliação → rollback seletivo. " +
        "Comandos: <code>python -m app.cli migracao --help</code>. " +
        "Dry-run é sempre o padrão; esta tela é somente status e nada aqui grava registro operacional.</div>" +
        '<div class="m15-panel"><h3>Dry-runs multiaba (' + r[3].total + ")</h3>" +
        table(
          ["Mapping", "Fonte", "Válidas", "Revisão", "Fechamento", "Status", "Ações"],
          (r[3].items || []).map(function (m) {
            var totals = m.totals || {};
            var recon = m.reconciliation_preview || {};
            return [
              esc(m.mapping_version || "—"), String(totals.source_total || 0),
              String(totals.valid || 0), String(m.pending_decisions || 0),
              pill(recon.fechamento_ok ? "ok" : "divergente"), pill(m.status),
              '<button class="m15-btn m15-btn-sec" data-m15-multi="' +
                esc(m.batch_id) + '">Detalhes</button>',
            ];
          }),
          "Nenhum dry-run multiaba sanitizado registrado.",
        ) + '<div id="m15MultiDetail"></div></div>' +
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
      Array.prototype.forEach.call(
        body().querySelectorAll("[data-m15-multi]"),
        function (btn) {
          btn.addEventListener("click", function () {
            var box = document.getElementById("m15MultiDetail");
            box.innerHTML = '<p class="m15-muted">Carregando…</p>';
            api("/migracao/multiaba/" + btn.getAttribute("data-m15-multi"))
              .then(function (det) {
                box.innerHTML = '<div class="m15-panel">' + multiSheetDetailHtml(det) + "</div>";
              })
              .catch(function () {
                box.innerHTML = '<div class="m15-aviso">Não foi possível carregar o dry-run multiaba.</div>';
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
        "Senhas nunca aparecem em URL, log ou auditoria; redefinir senha derruba tokens e sessões antigos na hora.</div>" +
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
            '<p class="m15-muted">Inativar derruba tokens e sessões do usuário imediatamente. ' +
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
            '<p class="m15-muted">Todos os tokens e sessões antigos deste usuário serão revogados imediatamente.</p></div>',
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

  // ------------------------------------------------- inspeção técnica (admin)

  // Não é um CRM paralelo: agrupa as listagens técnicas já existentes sob um
  // rótulo honesto, sem oferecer fluxo ordinário de cadastro.
  loaders.dados = function () {
    var sub = state.dadosSub || SUBTABS_DADOS[0][0];
    if (!SUBTABS_DADOS.some(function (t) { return t[0] === sub; })) sub = SUBTABS_DADOS[0][0];
    state.dadosSub = sub;
    var loader = loaders[sub];
    return loader().then(function () {
      var el = body();
      if (!el) return;
      var subtabs = SUBTABS_DADOS.map(function (t) {
        return '<button class="m15-tab' + (t[0] === sub ? " active" : "") +
          '" type="button" data-m15-dados-sub="' + t[0] + '">' + esc(t[1]) + "</button>";
      }).join("");
      el.insertAdjacentHTML("afterbegin",
        '<div class="m15-aviso m15-aviso-tecnico"><strong>Inspeção técnica do sistema.</strong> ' +
        "Leitura e correção pontual de registros pelo administrador. Não é a tela de operação: " +
        "pacientes e acompanhamento ficam em <strong>CRM Pacientes e Acompanhamento</strong>, " +
        "dinheiro em <strong>Financeiro</strong> e cadastro novo na <strong>Central de Cadastros</strong>." +
        "</div>" +
        '<div class="m15-tabs m15-subtabs" role="tablist" aria-label="Entidades técnicas">' +
        subtabs + "</div>");
      el.querySelectorAll("[data-m15-dados-sub]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          state.dadosSub = btn.getAttribute("data-m15-dados-sub");
          render();
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
    if (!autenticado()) {
      body().innerHTML =
        '<div class="m15-empty">Entre com e-mail e senha (ou cole um token da CLI) para operar o núcleo M15.</div>';
      return;
    }
    var loader = loaders[state.tab] || loaders.visao;
    body().innerHTML = '<div class="m15-empty">Carregando…</div>';
    loader().then(function () { attachDates(); }).catch(function (err) {
      // Sessão vencida ou revogada no servidor: cai fechado, com mensagem
      // clara, em vez de deixar a tela travada num erro genérico.
      if (err && err.status === 401 && state.sessao) {
        encerrarSessao("Sua sessão expirou. Entre novamente.");
        return;
      }
      showError(err);
    });
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
    // M17 — o Núcleo M15 não aparece mais como uma aplicação separada com
    // rótulo de grupo próprio ("M15 Beta"): o botão é anexado ao final do
    // grupo estático "Sistema" (Automações, Últimos lançamentos), como mais
    // um item administrativo do mesmo grupo, não uma segunda aplicação.
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

    // M21 — restaura a sessão do cookie ANTES de qualquer interação: é isso
    // que faz recarregar a página manter o mesmo usuário conectado.
    restaurarSessao().then(function (ok) {
      renderAuthArea();
      renderTabs();
      render();
      if (ok) notifySession();
    });
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

  // Deep-link "+ Novo …" → Central de Cadastros (delegado no documento:
  // sobrevive a qualquer re-render das abas do núcleo).
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-m15-central]");
    if (!btn) return;
    if (window.SoproCentral && typeof window.SoproCentral.open === "function") {
      var raw = btn.getAttribute("data-m15-central-prefill");
      var prefill = null;
      if (raw) { try { prefill = JSON.parse(raw); } catch (e) { prefill = null; } }
      window.SoproCentral.open(btn.getAttribute("data-m15-central"), prefill);
    } else {
      toast("Central de Cadastros indisponível nesta página.", "erro");
    }
  });

  // Cliente compartilhado do núcleo M15 — usado pela Central de Cadastros,
  // pelo CRM de pacientes e pela conciliação financeira. Nenhum consumidor vê
  // a credencial: o cookie de sessão é HttpOnly e o token da CLI, quando
  // existe, fica nesta variável de módulo. hasToken() continua sendo o nome
  // histórico da pergunta "estou autenticado?" para não quebrar consumidores.
  window.SoproM15 = {
    api: api,
    apiBlob: apiBlob,
    can: can,
    idemKey: idemKey,
    hasToken: autenticado,
    hasSession: function () { return state.sessao === true; },
    // M25.18 — URL absoluta de mesma origem para um caminho da API.
    //
    // Serve a um caso específico: um <iframe> que precisa ser autenticado
    // pelo COOKIE de sessão. Um iframe não manda cabeçalho `Authorization`,
    // então este endereço só é utilizável quando `hasSession()` é verdade —
    // e é justamente aí que o visualizador de PDF do navegador passa a
    // receber o `Content-Disposition` com o nome do arquivo.
    apiUrl: function (path) { return state.apiBase + path; },
    getUser: function () { return state.user; },
    access: function () { classifyAccess(); return state.access; },
    login: function (email, senha, manterConectado) {
      return api("/auth/token", {
        method: "POST",
        body: JSON.stringify({
          email: email, password: senha, manter_conectado: !!manterConectado,
        }),
      }).then(function (resp) {
        state.token = "";
        state.sessao = true;
        state.csrf = resp && resp.csrf ? resp.csrf : "";
        state.sessaoPersistente = !!(resp && resp.sessao && resp.sessao.persistente);
        return afterAuth();
      });
    },
    useToken: function (token) {
      state.token = (token || "").trim();
      state.sessao = false;
      state.csrf = "";
      return afterAuth();
    },
    restore: restaurarSessao,
    logout: function () { return encerrarSessao(); },
    onSessionChange: function (cb) {
      if (typeof cb === "function") sessionListeners.push(cb);
    },
    refresh: function () { render(); },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
