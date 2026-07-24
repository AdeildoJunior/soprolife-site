/* Central de Cadastros — fluxo canônico ÚNICO de entrada de dados (M16).
 *
 * Toda criação de registro operacional acontece aqui, contra a API nativa
 * M15 (FastAPI + PostgreSQL). Google Sheets NUNCA é destino de cadastro novo.
 *
 * - Autenticação: reutiliza a sessão do Núcleo M15 (window.SoproM15).
 *   O token vive só em memória dentro do módulo do núcleo — a Central
 *   dispara chamadas autenticadas sem jamais ver a credencial.
 * - Seletor de pessoa reutilizável: busca por nome OU telefone normalizado,
 *   criação inline sem sair do fluxo, aviso de duplicado ANTES de criar
 *   (nunca fusão automática) e fluxo de responsável para menor de idade.
 * - Financeiro sem PII: o lançamento carrega apenas vínculos técnicos
 *   (LAN ↔ ESP/CON); o nome do paciente exibido é derivado da relação.
 * - Anti duplo clique: botão desabilitado + chave de idempotência da API.
 * - Proteção de alterações não salvas: aviso ao trocar de aba/fechar.
 */
(function () {
  "use strict";

  const ROOT_ID = "centralRoot";
  const SECTION_ID = "central-cadastros";

  const state = {
    tab: "lead",
    prefill: null,   // dados pré-carregados por deep-link contextual
    dirty: false,    // formulário com alteração não salva
    booted: false,
  };

  const TABS = [
    ["lead", "Lead", "operacional"],
    ["paciente", "Paciente", "operacional"],
    ["espirometria", "Espirometria", "operacional"],
    ["consulta", "Consulta", "operacional"],
    ["clinica", "Clínica / Parceiro", "operacional"],
    ["contato-b2b", "Contato B2B", "operacional"],
    ["financeiro", "Financeiro", "gestor"],
  ];

  const RESPONSAVEIS = ["Adeildo", "Luiz Faustino"];
  const ORIGENS = [
    "Google", "Instagram", "WhatsApp", "Site SoproLife", "Indicação médica",
    "Indicação de paciente", "Clínica parceira", "LinkedIn", "Tráfego pago",
    "Retorno / recorrente", "Presencial", "Ligação", "Outro",
  ];
  const LEAD_ETAPAS = ["novo", "em_contato", "agendado", "convertido", "perdido",
    "nao_respondeu", "aguardando_retomada"];

  // ------------------------------------------------------------------ helpers

  function m15() { return window.SoproM15 || null; }
  function api(path, opts) { return m15().api(path, opts); }

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const p = String(iso).split("-");
    return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : iso;
  }

  function fmtMoneyBR(v) {
    const n = parseFloat(v);
    if (!isFinite(n)) return "—";
    return "R$ " + n.toFixed(2).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  // "1.234,56" | "1234.56" | "1234,56" → "1234.56" (string decimal da API)
  function parseMoneyBR(raw) {
    let s = String(raw || "").trim().replace(/^R\$\s*/, "");
    if (!s) return "";
    if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
    const n = Number(s);
    return isFinite(n) && n > 0 ? n.toFixed(2) : "";
  }

  // Máscara BR de telefone durante a digitação: (21) 99999-9999
  function phoneMask(input) {
    input.addEventListener("input", () => {
      const d = input.value.replace(/\D/g, "").slice(0, 11);
      let out = d;
      if (d.length > 2) {
        out = `(${d.slice(0, 2)}) ${d.slice(2)}`;
        const rest = d.slice(2);
        if (rest.length > 5) out = `(${d.slice(0, 2)}) ${rest.slice(0, rest.length - 4)}-${rest.slice(-4)}`;
      }
      input.value = out;
    });
  }

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "m15-toast" + (kind === "erro" ? " m15-toast-erro" : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), kind === "erro" ? 7000 : 4000);
  }

  function val(form, name) {
    const f = form.elements[name];
    if (!f) return "";
    if (f.type === "checkbox") return f.checked;
    return (f.value || "").trim();
  }

  function setIf(payload, key, value) {
    if (value !== "" && value != null) payload[key] = value;
  }

  // Campos no MESMO sistema visual do núcleo (m15-form / grid de 12 colunas)
  function fld(label, inner, opt) {
    let span = 3, help = "", req = false;
    if (typeof opt === "number") span = opt;
    else if (opt && typeof opt === "object") {
      span = opt.span || 3; help = opt.help || ""; req = !!opt.req;
    }
    const cls = "m15-field " + (span >= 12 ? "m15-form-full" : "m15-span-" + span);
    return `<label class="${cls}"><span class="m15-field-label" title="${esc(label)}">${esc(label)}${req ? ' <b class="cad-req" title="Campo obrigatório">*</b>' : ""}</span>${inner}` +
      (help ? `<span class="m15-field-help">${esc(help)}</span>` : "") + "</label>";
  }

  function inp(name, value, attrs) {
    return `<input name="${esc(name)}" value="${esc(value == null ? "" : value)}" ${attrs || ""}>`;
  }

  function sel(name, list, selected, attrs) {
    const opts = list.map((o) => {
      const v = Array.isArray(o) ? o[0] : o;
      const l = Array.isArray(o) ? o[1] : (o || "—");
      return `<option value="${esc(v)}"${String(v) === String(selected == null ? "" : selected) ? " selected" : ""}>${esc(l)}</option>`;
    }).join("");
    return `<select name="${esc(name)}" ${attrs || ""}>${opts}</select>`;
  }

  function dateInp(name, value, opts) {
    opts = opts || {};
    let attrs = `data-m15-date="${opts.parcial ? "partial" : "full"}"`;
    if (!opts.parcial) attrs += ' type="date"';
    if (opts.required) attrs += " required";
    return inp(name, value, attrs);
  }

  function datalist(id, values) {
    return `<datalist id="${esc(id)}">` +
      values.map((v) => `<option value="${esc(v)}">`).join("") + "</datalist>";
  }

  const HELP_PARCIAL = "Aceita dia (DD/MM/AAAA), mês (MM/AAAA) ou só ano (AAAA).";

  function attachDates(root) {
    if (window.SoproM15DatePicker) window.SoproM15DatePicker.attachAll(root);
  }

  function submitBtn(label) {
    return `<div class="m15-form-full m15-actions cad-actions">` +
      `<button class="m15-btn" type="submit">${esc(label)}</button>` +
      `<span class="cad-submit-status" hidden></span></div>`;
  }

  // Envio com anti duplo clique + resultado visível + refresh das listas
  function wireSubmit(form, buildRequest, onSuccess) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (!form.reportValidity()) return;
      const btn = form.querySelector('button[type="submit"]');
      const status = form.querySelector(".cad-submit-status");
      btn.disabled = true;
      if (status) { status.hidden = false; status.textContent = "Salvando…"; status.className = "cad-submit-status"; }
      Promise.resolve()
        .then(buildRequest)
        .then((created) => {
          state.dirty = false;
          btn.disabled = false;
          if (status) status.hidden = true;
          onSuccess(created);
          document.dispatchEvent(new CustomEvent("soprolife:cadastro", { detail: { tab: state.tab } }));
          if (m15() && m15().refresh) m15().refresh();
        })
        .catch((err) => {
          btn.disabled = false;
          const msg = (err && err.message) || String(err);
          if (status) {
            status.hidden = false;
            status.textContent = "Erro: " + msg;
            status.className = "cad-submit-status cad-erro";
          }
          toast("Erro: " + msg, "erro");
        });
    });
  }

  function successBanner(container, html) {
    const box = document.createElement("div");
    box.className = "cad-sucesso";
    box.innerHTML = html;
    container.prepend(box);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function markDirtyOn(form) {
    form.addEventListener("input", () => { state.dirty = true; });
  }

  function idade(dataNasc) {
    if (!dataNasc) return null;
    const d = new Date(dataNasc + "T00:00:00");
    if (Number.isNaN(d.getTime())) return null;
    const hoje = new Date();
    let anos = hoje.getFullYear() - d.getFullYear();
    const m = hoje.getMonth() - d.getMonth();
    if (m < 0 || (m === 0 && hoje.getDate() < d.getDate())) anos--;
    return anos;
  }

  function partnerOptions() {
    return api("/parceiros?tamanho=100").then((d) =>
      d.itens.map((p) => [p.id, `${p.public_code} — ${p.nome}`]));
  }

  function unitOptions(partnerId) {
    if (!partnerId) return Promise.resolve([]);
    return api(`/unidades?tamanho=100&partner_id=${encodeURIComponent(partnerId)}`)
      .then((d) => d.itens.map((u) => [u.id, `${u.public_code} — ${u.nome}`]));
  }

  function wireUnitSelect(form, partnerField, unitField) {
    const pSel = form.elements[partnerField];
    const uSel = form.elements[unitField];
    if (!pSel || !uSel) return;
    const load = () => {
      uSel.innerHTML = '<option value="">sem unidade</option>';
      unitOptions(pSel.value).then((units) => {
        uSel.innerHTML = '<option value="">sem unidade</option>' +
          units.map((u) => `<option value="${esc(u[0])}">${esc(u[1])}</option>`).join("");
      }).catch(() => { /* mantém "sem unidade" */ });
    };
    pSel.addEventListener("change", load);
    if (pSel.value) load();
  }

  // -------------------------------------------------- seletor de pessoa (base)

  /* Componente reutilizável: busca por nome/telefone, contexto suficiente
   * para distinguir candidatos, criação inline e aviso de duplicado.
   * API do componente (após wire): picker.resolve() → Promise<pessoa>
   * (cria a pessoa inline quando o modo "nova" está ativo). */
  function personPickerHtml(prefix, opts) {
    opts = opts || {};
    return `
    <div class="cad-picker" id="${prefix}Picker">
      <div class="cad-picker-search">
        <input id="${prefix}Q" type="search" placeholder="Buscar por nome, telefone ou PES-…"
          aria-label="Buscar pessoa por nome, telefone ou código">
        <button type="button" class="m15-btn m15-btn-sec" id="${prefix}Buscar">Buscar</button>
        ${opts.semCriar ? "" : `<button type="button" class="m15-btn m15-btn-sec" id="${prefix}Nova">+ Nova pessoa</button>`}
      </div>
      <div class="cad-picker-results" id="${prefix}Resultados" hidden></div>
      <div class="cad-picker-selected" id="${prefix}Selecionada" hidden></div>
      ${opts.semCriar ? "" : `
      <div class="cad-picker-nova" id="${prefix}NovaBox" hidden>
        <p class="cad-picker-nova-titulo">Nova pessoa — criada junto com este cadastro</p>
        <div class="m15-form cad-subgrid">
          ${fld("Nome completo", inp(prefix + "_nome", "", 'minlength="2" autocomplete="off"'), { span: 6, req: true })}
          ${fld("WhatsApp", inp(prefix + "_fone", "", 'type="tel" placeholder="(21) 99999-9999" autocomplete="off"'), 3)}
          ${fld("Nascimento", dateInp(prefix + "_nasc", ""), 3)}
          ${fld("E-mail (opcional)", inp(prefix + "_email", "", 'type="email" autocomplete="off"'), 6)}
          ${fld("Consentimento WhatsApp", sel(prefix + "_consent",
            [["", "não informado"], "concedido", "desconhecido", "revogado"], "concedido"), 3)}
        </div>
        <div class="cad-guardian" id="${prefix}GuardianBox" hidden>
          <p class="cad-picker-nova-titulo">Paciente menor de idade — responsável legal</p>
          <div class="cad-picker-search">
            <input id="${prefix}GQ" type="search" placeholder="Buscar responsável por nome, telefone ou PES-…">
            <button type="button" class="m15-btn m15-btn-sec" id="${prefix}GBuscar">Buscar</button>
          </div>
          <div class="cad-picker-results" id="${prefix}GResultados" hidden></div>
          <div class="cad-picker-selected" id="${prefix}GSelecionada" hidden></div>
          <div class="m15-form cad-subgrid">
            ${fld("Nome do responsável (se novo)", inp(prefix + "_gnome", "", 'minlength="2" autocomplete="off"'), 6)}
            ${fld("WhatsApp do responsável", inp(prefix + "_gfone", "", 'type="tel" autocomplete="off"'), 3)}
            ${fld("Parentesco", sel(prefix + "_grel",
              [["mother", "mãe"], ["father", "pai"], ["legal_guardian", "responsável legal"],
               ["grandparent", "avô/avó"], ["other", "outro"]], "mother"), 3)}
          </div>
        </div>
        <div class="cad-dup-aviso" id="${prefix}DupAviso" hidden></div>
      </div>`}
    </div>`;
  }

  function renderCandidates(listEl, itens, onPick) {
    if (!itens.length) {
      listEl.innerHTML = '<p class="cad-picker-vazio">Nenhuma pessoa encontrada — confira o termo ou cadastre uma nova.</p>';
      listEl.hidden = false;
      return;
    }
    listEl.innerHTML = itens.map((p) => {
      const contatos = (p.contatos || []).map((c) => esc(c.tipo)).join(", ") || "sem contato";
      const nasc = p.data_nascimento ? " · nasc. " + fmtDate(p.data_nascimento) : "";
      return `<button type="button" class="cad-cand" data-pid="${esc(p.id)}">
        <strong>${esc(p.nome_completo)}</strong>
        <span>${esc(p.public_code)}${nasc} · ${contatos}</span>
      </button>`;
    }).join("");
    listEl.hidden = false;
    listEl.querySelectorAll(".cad-cand").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = itens.find((i) => i.id === btn.dataset.pid);
        if (p) onPick(p);
      });
    });
  }

  function wireMiniSearch(qEl, btnEl, listEl, onPick) {
    const run = () => {
      const q = qEl.value.trim();
      if (!q) { listEl.hidden = true; return; }
      listEl.innerHTML = '<p class="cad-picker-vazio">Buscando…</p>';
      listEl.hidden = false;
      api("/pessoas/busca", { method: "POST", body: JSON.stringify({ q, tamanho: 8 }) })
        .then((d) => renderCandidates(listEl, d.itens, onPick))
        .catch((err) => {
          listEl.innerHTML = `<p class="cad-picker-vazio">Erro na busca: ${esc(err.message || err)}</p>`;
        });
    };
    btnEl.addEventListener("click", run);
    qEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); run(); }
    });
  }

  function wirePersonPicker(root, prefix, opts) {
    opts = opts || {};
    const picker = {
      selected: null,          // pessoa existente escolhida
      modoNova: false,
      dupConfirmado: false,    // humano confirmou criar apesar do aviso
      guardianSelected: null,
    };
    const q = root.querySelector("#" + prefix + "Q");
    const buscar = root.querySelector("#" + prefix + "Buscar");
    const resultados = root.querySelector("#" + prefix + "Resultados");
    const selecionada = root.querySelector("#" + prefix + "Selecionada");
    const novaBtn = root.querySelector("#" + prefix + "Nova");
    const novaBox = root.querySelector("#" + prefix + "NovaBox");

    function showSelected(p) {
      picker.selected = p;
      picker.modoNova = false;
      if (novaBox) novaBox.hidden = true;
      resultados.hidden = true;
      selecionada.hidden = false;
      selecionada.innerHTML =
        `<span class="cad-chip-pessoa"><strong>${esc(p.nome_completo)}</strong> ${esc(p.public_code)}</span>
         <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" id="${prefix}Trocar">Trocar</button>`;
      selecionada.querySelector("#" + prefix + "Trocar").addEventListener("click", () => {
        picker.selected = null;
        selecionada.hidden = true;
        q.focus();
      });
      state.dirty = true;
      if (opts.onSelect) opts.onSelect(p);
    }

    wireMiniSearch(q, buscar, resultados, showSelected);

    // Deep-link contextual (M19): o CRM abre a Central já com o paciente no
    // campo de busca. Só o código público viaja — nunca nome nem telefone.
    const preCodigo = (state.prefill || {}).person_codigo;
    if (preCodigo && q && buscar) {
      q.value = preCodigo;
      buscar.click();
    }

    if (novaBtn) {
      novaBtn.addEventListener("click", () => {
        picker.modoNova = !picker.modoNova;
        novaBox.hidden = !picker.modoNova;
        if (picker.modoNova) {
          picker.selected = null;
          selecionada.hidden = true;
          const nome = root.querySelector(`[name="${prefix}_nome"]`);
          if (nome) nome.focus();
        }
      });
      const fone = root.querySelector(`[name="${prefix}_fone"]`);
      if (fone) phoneMask(fone);
      const gfone = root.querySelector(`[name="${prefix}_gfone"]`);
      if (gfone) phoneMask(gfone);
      // menor de idade → revela o bloco de responsável
      const nasc = root.querySelector(`[name="${prefix}_nasc"]`);
      const gBox = root.querySelector("#" + prefix + "GuardianBox");
      if (nasc && gBox) {
        nasc.addEventListener("change", () => {
          const anos = idade(nasc.value);
          gBox.hidden = !(anos != null && anos < 18);
        });
      }
      const gq = root.querySelector("#" + prefix + "GQ");
      const gbuscar = root.querySelector("#" + prefix + "GBuscar");
      const gres = root.querySelector("#" + prefix + "GResultados");
      const gsel = root.querySelector("#" + prefix + "GSelecionada");
      if (gq && gbuscar) {
        wireMiniSearch(gq, gbuscar, gres, (p) => {
          picker.guardianSelected = p;
          gres.hidden = true;
          gsel.hidden = false;
          gsel.innerHTML = `<span class="cad-chip-pessoa"><strong>${esc(p.nome_completo)}</strong> ${esc(p.public_code)}</span>`;
        });
      }
    }

    // Volta o seletor ao estado inicial (busca) após um envio bem-sucedido —
    // sem isto, "+ Nova pessoa" continuava marcado como aberto internamente
    // e o SEGUNDO clique (para o próximo cadastro na mesma aba) o FECHAVA em
    // vez de abrir, quebrando o próximo envio silenciosamente.
    picker.resetState = function () {
      picker.selected = null;
      picker.modoNova = false;
      picker.dupConfirmado = false;
      picker.guardianSelected = null;
      if (novaBox) novaBox.hidden = true;
      if (selecionada) selecionada.hidden = true;
      if (resultados) resultados.hidden = true;
      if (q) q.value = "";
      const dupAviso = root.querySelector("#" + prefix + "DupAviso");
      if (dupAviso) dupAviso.hidden = true;
      const gBox = root.querySelector("#" + prefix + "GuardianBox");
      if (gBox) gBox.hidden = true;
    };

    // Resolve a seleção em UMA pessoa persistida (criando quando "nova").
    picker.resolve = function () {
      if (picker.selected) return Promise.resolve(picker.selected);
      if (!picker.modoNova || !novaBox) {
        return Promise.reject(new Error("Selecione uma pessoa (busque acima) ou cadastre uma nova."));
      }
      const nome = root.querySelector(`[name="${prefix}_nome"]`).value.trim();
      const fone = root.querySelector(`[name="${prefix}_fone"]`).value.trim();
      const nasc = root.querySelector(`[name="${prefix}_nasc"]`).value.trim();
      const email = root.querySelector(`[name="${prefix}_email"]`).value.trim();
      const consent = root.querySelector(`[name="${prefix}_consent"]`).value;
      if (nome.length < 2) return Promise.reject(new Error("Informe o nome completo da nova pessoa."));

      const dupAviso = root.querySelector("#" + prefix + "DupAviso");
      const preCheck = picker.dupConfirmado
        ? Promise.resolve({ total: 0, candidatos: [] })
        : api("/pessoas/verificar-duplicados", {
            method: "POST",
            body: JSON.stringify({ nome_completo: nome, telefones: fone ? [fone] : [] }),
          });

      return preCheck.then((dup) => {
        if (dup.total > 0) {
          // aviso explícito: usar existente OU confirmar a criação
          dupAviso.hidden = false;
          dupAviso.innerHTML =
            `<strong>Possível duplicado (${dup.total}):</strong> já existe cadastro com o mesmo ` +
            `${dup.candidatos.some((c) => c.motivo === "telefone_igual") ? "telefone" : "nome"}. ` +
            `Escolha um existente abaixo ou confirme a criação.` +
            dup.candidatos.map((c) =>
              `<button type="button" class="cad-cand" data-dup="${esc(c.id)}">
                 <strong>${esc(c.nome_completo)}</strong>
                 <span>${esc(c.public_code)}${c.data_nascimento ? " · nasc. " + fmtDate(c.data_nascimento) : ""} · motivo: ${esc(c.motivo === "telefone_igual" ? "telefone igual" : "nome igual")}</span>
               </button>`).join("") +
            `<button type="button" class="m15-btn m15-btn-sec" data-dup-confirmar>Criar mesmo assim</button>`;
          dupAviso.querySelectorAll("[data-dup]").forEach((btn) => {
            btn.addEventListener("click", () => {
              api("/pessoas/" + btn.dataset.dup).then((p) => {
                dupAviso.hidden = true;
                showSelected(p);
              }).catch((e) => toast("Erro: " + e.message, "erro"));
            });
          });
          dupAviso.querySelector("[data-dup-confirmar]").addEventListener("click", () => {
            picker.dupConfirmado = true;
            dupAviso.hidden = true;
            toast("Confirmação registrada — envie o formulário novamente para criar.");
          });
          throw new Error("Possível duplicado — escolha uma pessoa existente ou confirme a criação.");
        }
        const payload = { nome_completo: nome, contatos: [] };
        if (fone) payload.contatos.push({ tipo: "whatsapp", valor: fone, principal: true });
        if (email) payload.contatos.push({ tipo: "email", valor: email, principal: !fone });
        setIf(payload, "data_nascimento", nasc);
        setIf(payload, "consentimento_whatsapp", consent);
        return api("/pessoas", { method: "POST", body: JSON.stringify(payload) })
          .then((pessoa) => criarResponsavelSePreciso(root, prefix, picker, pessoa, nasc)
            .then(() => pessoa));
      });
    };

    return picker;
  }

  // Menor de idade: cria/associa o responsável e o vínculo legal na sequência.
  function criarResponsavelSePreciso(root, prefix, picker, pessoa, nasc) {
    const anos = idade(nasc);
    if (anos == null || anos >= 18) return Promise.resolve();
    const rel = root.querySelector(`[name="${prefix}_grel"]`).value || "mother";
    const vincular = (guardianId) =>
      api(`/pessoas/${pessoa.id}/responsaveis`, {
        method: "POST",
        body: JSON.stringify({
          guardian_person_id: guardianId,
          relationship_type: rel,
          is_legal_guardian: true,
          active: true,
        }),
      });
    if (picker.guardianSelected) return vincular(picker.guardianSelected.id);
    const gnome = root.querySelector(`[name="${prefix}_gnome"]`).value.trim();
    const gfone = root.querySelector(`[name="${prefix}_gfone"]`).value.trim();
    if (!gnome) {
      toast("Paciente menor sem responsável informado — cadastro criado; associe o responsável depois na aba Pessoas.", "erro");
      return Promise.resolve();
    }
    const payload = { nome_completo: gnome, contatos: [] };
    if (gfone) payload.contatos.push({ tipo: "whatsapp", valor: gfone, principal: true });
    return api("/pessoas", { method: "POST", body: JSON.stringify(payload) })
      .then((g) => vincular(g.id));
  }

  // -------------------------------------------------------------- estrutura

  function sectionEl() { return document.getElementById(SECTION_ID); }
  function rootEl() { return document.getElementById(ROOT_ID); }

  function ensureStyles() {
    [["m15css", "css/m15.css"], ["cadcss", "css/central.css"]].forEach(([id, href]) => {
      if (!document.getElementById(id) &&
          !document.querySelector(`link[href^="${href}"]`)) {
        const link = document.createElement("link");
        link.id = id;
        link.rel = "stylesheet";
        link.href = href + "?v=2026072301";
        document.head.appendChild(link);
      }
    });
  }

  function visibleTabs() {
    const cli = m15();
    return TABS.filter((t) => !cli || cli.can(t[2]));
  }

  function render() {
    const root = rootEl();
    if (!root) return;
    ensureStyles();
    const cli = m15();

    if (!cli) {
      root.innerHTML = '<div class="m15-aviso">Núcleo M15 indisponível nesta página — a Central de Cadastros depende da API nativa.</div>';
      return;
    }
    const access = cli.access();
    if (!access.secure) {
      root.innerHTML = '<div class="m15-block" role="alert"><strong>Acesso HTTP inseguro — Central bloqueada.</strong> Abra o endereço HTTPS privado do painel para operar.</div>';
      return;
    }
    if (!cli.hasToken()) {
      renderLogin(root);
      return;
    }

    const tabs = visibleTabs();
    if (!tabs.some((t) => t[0] === state.tab)) state.tab = tabs[0] ? tabs[0][0] : "lead";
    const user = cli.getUser();
    root.innerHTML = `
      <div class="cad-header">
        <div class="cad-header-info">
          <span class="cad-header-fonte">Fonte oficial: API M15 · PostgreSQL</span>
          ${user ? `<span class="cad-header-user">${esc(user.nome)} · ${esc((user.papeis || []).join(", "))}</span>` : ""}
        </div>
        <div class="m15-tabs cad-tabs" role="tablist">
          ${tabs.map((t) => `<button role="tab" aria-selected="${t[0] === state.tab}" class="m15-tab${t[0] === state.tab ? " active" : ""}" data-cad-tab="${t[0]}">${esc(t[1])}</button>`).join("")}
        </div>
      </div>
      <div id="cadBody"><div class="m15-empty">Carregando…</div></div>`;

    root.querySelectorAll("[data-cad-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.cadTab === state.tab) return;
        if (state.dirty && !window.confirm("Há alterações não salvas neste formulário. Trocar de aba mesmo assim?")) return;
        state.dirty = false;
        state.prefill = null;
        state.tab = btn.dataset.cadTab;
        render();
      });
    });

    const loader = LOADERS[state.tab] || LOADERS.lead;
    loader(document.getElementById("cadBody"))
      .then(() => attachDates(root))
      .catch((err) => {
        document.getElementById("cadBody").innerHTML =
          `<div class="m15-erro">Erro: ${esc(err.message || err)}${err && err.status === 401 ? " Entre novamente." : ""}</div>`;
      });
  }

  function renderLogin(root) {
    root.innerHTML = `
      <div class="m15-panel cad-login">
        <h3>Entrar para cadastrar</h3>
        <p class="m15-muted">A Central usa a mesma sessão segura do Núcleo M15 — token apenas em memória.</p>
        <div class="m15-login-grid">
          <label class="m15-field"><span class="m15-field-label">E-mail</span>
            <input type="email" id="cadEmail" placeholder="voce@soprolife.com.br" autocomplete="off"></label>
          <label class="m15-field"><span class="m15-field-label">Senha</span>
            <input type="password" id="cadSenha" placeholder="••••••••••" autocomplete="off"></label>
          <button class="m15-btn" id="cadEntrar">Entrar</button>
        </div>
        <details class="m15-login-alt">
          <summary>Entrar com token da CLI (avançado)</summary>
          <div class="m15-login-grid">
            <label class="m15-field"><span class="m15-field-label">Token da API</span>
              <input type="password" id="cadToken" autocomplete="off"></label>
            <button class="m15-btn m15-btn-sec" id="cadTokenSave">Usar token</button>
          </div>
        </details>
      </div>`;
    const doLogin = () => {
      const email = root.querySelector("#cadEmail").value.trim();
      const senha = root.querySelector("#cadSenha").value;
      if (!email || !senha) { toast("Informe e-mail e senha.", "erro"); return; }
      const btn = root.querySelector("#cadEntrar");
      btn.disabled = true;
      m15().login(email, senha).catch((err) => {
        btn.disabled = false;
        toast("Erro no login: " + (err.message || err), "erro");
      });
    };
    root.querySelector("#cadEntrar").addEventListener("click", doLogin);
    root.querySelector("#cadSenha").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") doLogin();
    });
    root.querySelector("#cadTokenSave").addEventListener("click", () => {
      const t = root.querySelector("#cadToken");
      const token = t.value.trim();
      t.value = "";
      if (!token) { toast("Cole um token primeiro.", "erro"); return; }
      m15().useToken(token);
    });
  }

  // --------------------------------------------------------------- recentes

  function recentsPanel(title, subtitle) {
    return `<div class="m15-panel cad-recentes"><div class="cad-recentes-head">
      <h3>${esc(title)}</h3><span class="m15-muted">${esc(subtitle)}</span></div>
      <div class="cad-recentes-body"><div class="m15-empty">Carregando…</div></div></div>`;
  }

  function fillRecents(container, headers, rows, emptyMsg) {
    const bodyEl = container.querySelector(".cad-recentes-body");
    if (!rows.length) {
      bodyEl.innerHTML = `<div class="m15-empty">${esc(emptyMsg)}</div>`;
      return;
    }
    bodyEl.innerHTML = `<div class="m15-table-wrap"><table class="m15-table"><thead><tr>` +
      headers.map((h) => `<th>${esc(h)}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") +
      "</tbody></table></div>";
  }

  function pill(v) {
    const key = String(v || "").replace(/\s+/g, "_");
    return `<span class="m15-pill ${esc(key)}">${esc(v || "—")}</span>`;
  }

  // ------------------------------------------------------------------- abas

  const LOADERS = {};

  // ------------------------------------------------------------------- LEAD

  LOADERS.lead = function (bodyEl) {
    bodyEl.innerHTML = `
      <div class="m15-panel">
        <h3>Novo lead</h3>
        <p class="cad-microcopy">Interessado ANTES do primeiro atendimento. O lead não vira paciente automaticamente — a conversão é uma decisão explícita depois.</p>
        <form class="m15-form" id="cadFormLead" novalidate>
          <div class="m15-form-full">${personPickerHtml("cadLeadP")}</div>
          ${fld("Serviço de interesse", sel("servico_interesse",
            [["", "—"], ["espirometria", "Espirometria"], ["consulta", "Consulta"],
             ["ambos", "Ambos"], ["outro", "Outro"]], "espirometria"), 3)}
          ${fld("Origem do lead", inp("origem", "", 'list="cadOrigens" placeholder="ex.: Google"'), 3)}
          ${fld("Canal de entrada", sel("canal_entrada",
            [["", "—"], "whatsapp", "telefone", "site", "presencial", "outro"], "whatsapp"), 3)}
          ${fld("Etapa atual", sel("etapa", LEAD_ETAPAS, "novo"), 3)}
          ${fld("Responsável", inp("responsavel", "Adeildo", 'list="cadResp"'), 3)}
          ${fld("1º contato", dateInp("data_primeiro_contato", "", { parcial: true }), { span: 3, help: HELP_PARCIAL })}
          ${fld("Próxima ação", inp("proxima_acao", "", 'placeholder="ex.: enviar preparo do exame"'), 3)}
          ${fld("Data da próxima ação", dateInp("data_retomada_manual", ""), 3)}
          ${fld("Observações", inp("observacao", ""), 12)}
          ${submitBtn("Salvar lead")}
        </form>
        ${datalist("cadOrigens", ORIGENS)}${datalist("cadResp", RESPONSAVEIS)}
      </div>
      ${recentsPanel("Leads recentes", "Funil CRM — aparecem aqui na hora")}`;

    const form = bodyEl.querySelector("#cadFormLead");
    markDirtyOn(form);
    const picker = wirePersonPicker(form, "cadLeadP");
    wireSubmit(form, () =>
      picker.resolve().then((p) => {
        const payload = { person_id: p.id, etapa: val(form, "etapa") };
        setIf(payload, "servico_interesse", val(form, "servico_interesse"));
        setIf(payload, "origem", val(form, "origem"));
        setIf(payload, "canal_entrada", val(form, "canal_entrada"));
        setIf(payload, "responsavel", val(form, "responsavel"));
        setIf(payload, "data_primeiro_contato", val(form, "data_primeiro_contato"));
        setIf(payload, "data_retomada_manual", val(form, "data_retomada_manual"));
        const acao = val(form, "proxima_acao");
        const obs = val(form, "observacao");
        const nota = [acao ? "Próxima ação: " + acao : "", obs].filter(Boolean).join(" · ");
        setIf(payload, "observacao", nota);
        return api("/leads", { method: "POST", body: JSON.stringify(payload) })
          .then((lead) => ({ lead, pessoa: p }));
      }),
    ({ lead, pessoa }) => {
      successBanner(bodyEl, `<strong>Lead ${esc(lead.public_code)} criado</strong> para ${esc(pessoa.nome_completo)} (${esc(pessoa.public_code)}). Já está no funil do CRM abaixo e na aba Leads do núcleo.`);
      form.reset();
      picker.resetState();
      loadLeadRecents(bodyEl);
    });
    return loadLeadRecents(bodyEl);
  };

  function loadLeadRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/leads?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Etapa", "Origem", "1º contato", "Retomada"],
        d.itens.map((l) => [
          `<strong>${esc(l.public_code)}</strong>`, pill(l.etapa), esc(l.origem || "—"),
          fmtDate(l.data_primeiro_contato), fmtDate(l.data_retomada_manual),
        ]),
        "Nenhum lead ainda."));
  }

  // --------------------------------------------------------------- PACIENTE

  LOADERS.paciente = function (bodyEl) {
    bodyEl.innerHTML = `
      <div class="m15-panel">
        <h3>Novo paciente</h3>
        <p class="cad-microcopy">Nome e WhatsApp são a base do cadastro. Duplicados são avisados antes de salvar — nunca fundidos automaticamente.</p>
        <form class="m15-form" id="cadFormPac" novalidate>
          ${fld("Nome completo", inp("nome_completo", "", 'required minlength="2" autocomplete="off"'), { span: 6, req: true })}
          ${fld("WhatsApp", inp("whatsapp", "", 'type="tel" placeholder="(21) 99999-9999" autocomplete="off"'), 3)}
          ${fld("Nascimento", dateInp("data_nascimento", ""), 3)}
          ${fld("E-mail (opcional)", inp("email", "", 'type="email" autocomplete="off"'), 4)}
          ${fld("Origem", inp("origem", "", 'list="cadOrigens"'), 4)}
          ${fld("Consentimento WhatsApp", sel("consentimento_whatsapp",
            [["", "não informado"], "concedido", "desconhecido", "revogado"], "concedido"), 4)}
          <div class="m15-form-full cad-guardian" id="cadPacGuardian" hidden>
            <p class="cad-picker-nova-titulo">Paciente menor de idade — responsável legal</p>
            <div class="cad-picker-search">
              <input id="cadPacGQ" type="search" placeholder="Buscar responsável existente (nome, telefone ou PES-…)">
              <button type="button" class="m15-btn m15-btn-sec" id="cadPacGBuscar">Buscar</button>
            </div>
            <div class="cad-picker-results" id="cadPacGResultados" hidden></div>
            <div class="cad-picker-selected" id="cadPacGSelecionada" hidden></div>
            <div class="m15-form cad-subgrid">
              ${fld("Nome do responsável (se novo)", inp("gnome", "", 'minlength="2" autocomplete="off"'), 6)}
              ${fld("WhatsApp do responsável", inp("gfone", "", 'type="tel" autocomplete="off"'), 3)}
              ${fld("Parentesco", sel("grel",
                [["mother", "mãe"], ["father", "pai"], ["legal_guardian", "responsável legal"],
                 ["grandparent", "avô/avó"], ["other", "outro"]], "mother"), 3)}
            </div>
          </div>
          ${fld("Observações operacionais", inp("observacao", ""), 12)}
          <div class="m15-form-full cad-dup-aviso" id="cadPacDup" hidden></div>
          ${submitBtn("Salvar paciente")}
        </form>
        ${datalist("cadOrigens", ORIGENS)}
      </div>
      ${recentsPanel("Pessoas recentes", "Cadastro canônico único — sem duplicar identidades")}`;

    const form = bodyEl.querySelector("#cadFormPac");
    markDirtyOn(form);
    phoneMask(form.elements.whatsapp);
    phoneMask(form.elements.gfone);

    let dupConfirmado = false;
    let guardianSelected = null;
    form.elements.data_nascimento.addEventListener("change", () => {
      const anos = idade(form.elements.data_nascimento.value);
      bodyEl.querySelector("#cadPacGuardian").hidden = !(anos != null && anos < 18);
    });
    wireMiniSearch(
      bodyEl.querySelector("#cadPacGQ"), bodyEl.querySelector("#cadPacGBuscar"),
      bodyEl.querySelector("#cadPacGResultados"), (p) => {
        guardianSelected = p;
        const selEl = bodyEl.querySelector("#cadPacGSelecionada");
        selEl.hidden = false;
        selEl.innerHTML = `<span class="cad-chip-pessoa"><strong>${esc(p.nome_completo)}</strong> ${esc(p.public_code)}</span>`;
      });

    wireSubmit(form, () => {
      const nome = val(form, "nome_completo");
      const fone = val(form, "whatsapp");
      const dupBox = bodyEl.querySelector("#cadPacDup");
      const preCheck = dupConfirmado
        ? Promise.resolve({ total: 0, candidatos: [] })
        : api("/pessoas/verificar-duplicados", {
            method: "POST",
            body: JSON.stringify({ nome_completo: nome, telefones: fone ? [fone] : [] }),
          });
      return preCheck.then((dup) => {
        if (dup.total > 0) {
          dupBox.hidden = false;
          dupBox.innerHTML =
            `<strong>Possível duplicado (${dup.total}):</strong> ` +
            dup.candidatos.map((c) =>
              `<span class="cad-chip-pessoa">${esc(c.nome_completo)} · ${esc(c.public_code)} · ${esc(c.motivo === "telefone_igual" ? "telefone igual" : "nome igual")}</span>`).join(" ") +
            ` <button type="button" class="m15-btn m15-btn-sec" id="cadPacDupOk">Criar mesmo assim</button>`;
          dupBox.querySelector("#cadPacDupOk").addEventListener("click", () => {
            dupConfirmado = true;
            dupBox.hidden = true;
            toast("Confirmação registrada — envie o formulário novamente para criar.");
          });
          throw new Error("Possível duplicado detectado — confirme antes de criar.");
        }
        const payload = { nome_completo: nome, contatos: [] };
        if (fone) payload.contatos.push({ tipo: "whatsapp", valor: fone, principal: true });
        const email = val(form, "email");
        if (email) payload.contatos.push({ tipo: "email", valor: email, principal: !fone });
        setIf(payload, "data_nascimento", val(form, "data_nascimento"));
        setIf(payload, "consentimento_whatsapp", val(form, "consentimento_whatsapp"));
        const origem = val(form, "origem");
        const obs = [origem ? "Origem: " + origem : "", val(form, "observacao")]
          .filter(Boolean).join(" · ");
        setIf(payload, "observacao", obs);
        return api("/pessoas", { method: "POST", body: JSON.stringify(payload) })
          .then((pessoa) => {
            const anos = idade(val(form, "data_nascimento"));
            if (anos != null && anos < 18) {
              const rel = val(form, "grel") || "mother";
              const vincular = (gid) => api(`/pessoas/${pessoa.id}/responsaveis`, {
                method: "POST",
                body: JSON.stringify({
                  guardian_person_id: gid, relationship_type: rel,
                  is_legal_guardian: true, active: true,
                }),
              });
              if (guardianSelected) return vincular(guardianSelected.id).then(() => pessoa);
              const gnome = val(form, "gnome");
              if (gnome) {
                const gp = { nome_completo: gnome, contatos: [] };
                const gfone = val(form, "gfone");
                if (gfone) gp.contatos.push({ tipo: "whatsapp", valor: gfone, principal: true });
                return api("/pessoas", { method: "POST", body: JSON.stringify(gp) })
                  .then((g) => vincular(g.id)).then(() => pessoa);
              }
            }
            return pessoa;
          });
      });
    }, (pessoa) => {
      successBanner(bodyEl, `<strong>Paciente ${esc(pessoa.public_code)} criado</strong> — ${esc(pessoa.nome_completo)}.` +
        (pessoa.candidatos_identidade ? ` <em>${pessoa.candidatos_identidade} candidato(s) de identidade registrados para revisão humana.</em>` : ""));
      form.reset();
      dupConfirmado = false;
      guardianSelected = null;
      bodyEl.querySelector("#cadPacGuardian").hidden = true;
      loadPacRecents(bodyEl);
    });
    return loadPacRecents(bodyEl);
  };

  function loadPacRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/pessoas?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Nome", "Status", "Contatos", "Criado em"],
        d.itens.map((p) => [
          `<strong>${esc(p.public_code)}</strong>`, esc(p.nome_completo),
          pill(p.nao_contatar ? "não contatar" : p.status),
          esc((p.contatos || []).map((c) => c.tipo).join(", ") || "—"),
          esc((p.created_at_local || "").slice(0, 10)),
        ]),
        "Nenhuma pessoa ainda."));
  }

  // ----------------------------------------------------------- ESPIROMETRIA

  LOADERS.espirometria = function (bodyEl) {
    return partnerOptions().catch(() => []).then((partners) => {
      const pre = state.prefill || {};
      bodyEl.innerHTML = `
        <div class="m15-panel">
          <h3>Nova espirometria</h3>
          <p class="cad-microcopy">Comece pelo paciente: busque um existente ou cadastre na hora. Status "Realizado" agenda o follow-up de 6 meses automaticamente.</p>
          <form class="m15-form" id="cadFormEsp" novalidate>
            <div class="m15-form-full">${personPickerHtml("cadEspP")}</div>
            ${fld("Data do exame", dateInp("data_exame", "", { parcial: true }), { span: 3, help: HELP_PARCIAL, req: true })}
            ${fld("Status", sel("status",
              ["Aguardando", "Realizado", "Laudo Liberado", "Cancelado", "Remarcado"], "Realizado"), 3)}
            ${fld("Broncodilatador", sel("broncodilatador",
              [["", "não informado"], ["false", "sem broncodilatador"], ["true", "com broncodilatador"]], "false"), 3)}
            ${fld("Modalidade", sel("modalidade",
              [["", "—"], ["residencial", "residencial (domiciliar)"], ["cowork", "cowork"],
               ["clinica_parceira", "clínica parceira"]], ""), 3)}
            ${fld("Local / unidade de atendimento", inp("local_atendimento", "", 'list="cadLocais"'), 4)}
            ${fld("Parceiro (quando aplicável)", sel("partner_id", [["", "sem parceiro"]].concat(partners), pre.partner_id || ""), 4)}
            ${fld("Unidade do parceiro", sel("partner_unit_id", [["", "sem unidade"]], ""), 4)}
            ${fld("Origem", inp("origem", "", 'list="cadOrigens"'), 4)}
            ${fld("Técnico / responsável", inp("responsavel", "Adeildo", 'list="cadResp"'), 4)}
            ${fld("Próximo acompanhamento", dateInp("proximo_followup", ""), { span: 4, help: "Opcional — sem data, o follow-up de 6 meses é automático." })}
            ${fld("Observações (laudo/resultado)", inp("observacao", ""), 12)}
            ${submitBtn("Salvar espirometria")}
          </form>
          ${datalist("cadOrigens", ORIGENS)}${datalist("cadResp", RESPONSAVEIS)}
          ${datalist("cadLocais", ["Domiciliar", "Clínica", "Empresa", "Parceiro", "Outro"])}
        </div>
        ${recentsPanel("Espirometrias recentes", "Histórico do paciente e indicadores — na hora")}`;

      const form = bodyEl.querySelector("#cadFormEsp");
      markDirtyOn(form);
      const picker = wirePersonPicker(form, "cadEspP");
      wireUnitSelect(form, "partner_id", "partner_unit_id");
      if (pre.partner_name) {
        // deep-link contextual (ex.: Parcerias → Pastore): pré-seleciona o
        // parceiro pelo nome sem depender de ID embutido em botão legado
        const pSel = form.elements.partner_id;
        const alvo = Array.from(pSel.options).find((o) =>
          o.textContent.toLowerCase().includes(pre.partner_name.toLowerCase()));
        if (alvo) {
          pSel.value = alvo.value;
          pSel.dispatchEvent(new Event("change"));
          if (!form.elements.modalidade.value) form.elements.modalidade.value = "clinica_parceira";
        }
      }
      wireSubmit(form, () =>
        picker.resolve().then((p) => {
          const payload = {
            person_id: p.id,
            status: val(form, "status"),
            idempotency_key: m15().idemKey(),
          };
          if (!val(form, "data_exame")) throw new Error("Informe a data do exame.");
          payload.data_exame = val(form, "data_exame");
          if (val(form, "broncodilatador") !== "") {
            payload.broncodilatador = val(form, "broncodilatador") === "true";
          }
          setIf(payload, "modalidade", val(form, "modalidade"));
          setIf(payload, "local_atendimento", val(form, "local_atendimento"));
          setIf(payload, "partner_id", val(form, "partner_id"));
          setIf(payload, "partner_unit_id", val(form, "partner_unit_id"));
          setIf(payload, "origem", val(form, "origem"));
          setIf(payload, "responsavel", val(form, "responsavel"));
          setIf(payload, "observacao", val(form, "observacao"));
          return api("/espirometrias", { method: "POST", body: JSON.stringify(payload) })
            .then((exame) => {
              const fupManual = val(form, "proximo_followup");
              const extra = fupManual
                ? api("/followups", {
                    method: "POST",
                    body: JSON.stringify({
                      patient_person_id: p.id, tipo: "manual", due_date: fupManual,
                      responsavel: val(form, "responsavel") || undefined,
                      observacao: "Acompanhamento definido no cadastro do exame " + exame.public_code,
                    }),
                  }).catch(() => null)
                : Promise.resolve(null);
              return extra.then(() => ({ exame, pessoa: p }));
            });
        }),
      ({ exame, pessoa }) => {
        const gestor = m15().can("gestor");
        successBanner(bodyEl,
          `<strong>Espirometria ${esc(exame.public_code)} criada</strong> para ${esc(pessoa.nome_completo)} (${esc(pessoa.public_code)}).` +
          (exame.followup && exame.followup.id ? " Follow-up de 6 meses agendado." : "") +
          (gestor ? ` <button type="button" class="m15-btn cad-btn-mini" id="cadEspFin">+ Lançamento financeiro vinculado</button>` : ""));
        const finBtn = bodyEl.querySelector("#cadEspFin");
        if (finBtn) {
          finBtn.addEventListener("click", () => {
            open("financeiro", {
              spirometry_exam_id: exame.id,
              vinculo_code: exame.public_code,
              vinculo_pessoa: pessoa.nome_completo,
              categoria: "Espirometria",
            });
          });
        }
        form.reset();
        picker.resetState();
        loadEspRecents(bodyEl);
      });
      return loadEspRecents(bodyEl);
    });
  };

  function loadEspRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/espirometrias?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Data", "Status", "BD", "Modalidade"],
        d.itens.map((e) => [
          `<strong>${esc(e.public_code)}</strong>`, fmtDate(e.data_exame), pill(e.status),
          e.broncodilatador == null ? "—" : (e.broncodilatador ? "com BD" : "sem BD"),
          esc(e.modalidade || "—"),
        ]),
        "Nenhuma espirometria ainda."));
  }

  // --------------------------------------------------------------- CONSULTA

  LOADERS.consulta = function (bodyEl) {
    bodyEl.innerHTML = `
      <div class="m15-panel">
        <h3>Nova consulta</h3>
        <p class="cad-microcopy">Consultas pagas diretamente ao médico não geram lançamento financeiro da SoproLife — registre apenas o atendimento.</p>
        <form class="m15-form" id="cadFormCon" novalidate>
          <div class="m15-form-full">${personPickerHtml("cadConP")}</div>
          ${fld("Data da consulta", dateInp("data_consulta", "", { parcial: true }), { span: 3, help: HELP_PARCIAL, req: true })}
          ${fld("Status", sel("status",
            ["Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"], "Realizada"), 3)}
          ${fld("Modalidade", sel("modalidade",
            [["", "—"], ["teleconsulta", "teleconsulta"], ["residencial", "residencial"],
             ["cowork", "cowork"], ["clinica_parceira", "clínica parceira"]], "teleconsulta"), 3)}
          ${fld("Médico / profissional", inp("profissional", ""), 3)}
          ${fld("Origem / parceiro", inp("origem", "", 'list="cadOrigens"'), 4)}
          ${fld("Responsável", inp("responsavel", "Adeildo", 'list="cadResp"'), 4)}
          ${fld("Pagamento", sel("pagamento_modelo",
            [["", "—"], ["soprolife", "recebido pela SoproLife (lançar no Financeiro)"],
             ["medico", "direto ao médico (sem lançamento SoproLife)"]], ""), 4)}
          ${fld("Observações", inp("observacao", ""), 12)}
          ${submitBtn("Salvar consulta")}
        </form>
        ${datalist("cadOrigens", ORIGENS)}${datalist("cadResp", RESPONSAVEIS)}
      </div>
      ${recentsPanel("Consultas recentes", "Histórico e indicadores — na hora")}`;

    const form = bodyEl.querySelector("#cadFormCon");
    markDirtyOn(form);
    const picker = wirePersonPicker(form, "cadConP");
    wireSubmit(form, () =>
      picker.resolve().then((p) => {
        const payload = {
          person_id: p.id,
          status: val(form, "status"),
          idempotency_key: m15().idemKey(),
        };
        if (!val(form, "data_consulta")) throw new Error("Informe a data da consulta.");
        payload.data_consulta = val(form, "data_consulta");
        setIf(payload, "modalidade", val(form, "modalidade"));
        setIf(payload, "profissional", val(form, "profissional"));
        setIf(payload, "origem", val(form, "origem"));
        setIf(payload, "responsavel", val(form, "responsavel"));
        const pagto = val(form, "pagamento_modelo");
        const obs = [
          pagto === "medico" ? "Pagamento direto ao médico — sem lançamento SoproLife" :
            (pagto === "soprolife" ? "Pagamento recebido pela SoproLife" : ""),
          val(form, "observacao"),
        ].filter(Boolean).join(" · ");
        setIf(payload, "observacao", obs);
        return api("/consultas", { method: "POST", body: JSON.stringify(payload) })
          .then((consulta) => ({ consulta, pessoa: p, pagto }));
      }),
    ({ consulta, pessoa, pagto }) => {
      const gestor = m15().can("gestor");
      successBanner(bodyEl,
        `<strong>Consulta ${esc(consulta.public_code)} criada</strong> para ${esc(pessoa.nome_completo)} (${esc(pessoa.public_code)}).` +
        (pagto === "soprolife" && gestor
          ? ` <button type="button" class="m15-btn cad-btn-mini" id="cadConFin">+ Lançamento financeiro vinculado</button>` : ""));
      const finBtn = bodyEl.querySelector("#cadConFin");
      if (finBtn) {
        finBtn.addEventListener("click", () => {
          open("financeiro", {
            consultation_id: consulta.id,
            vinculo_code: consulta.public_code,
            vinculo_pessoa: pessoa.nome_completo,
            categoria: "Consulta",
          });
        });
      }
      form.reset();
      picker.resetState();
      loadConRecents(bodyEl);
    });
    return loadConRecents(bodyEl);
  };

  function loadConRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/consultas?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Data", "Status", "Profissional", "Modalidade"],
        d.itens.map((c) => [
          `<strong>${esc(c.public_code)}</strong>`, fmtDate(c.data_consulta), pill(c.status),
          esc(c.profissional || "—"), esc(c.modalidade || "—"),
        ]),
        "Nenhuma consulta ainda."));
  }

  // ------------------------------------------------------ CLÍNICA / PARCEIRO

  LOADERS.clinica = function (bodyEl) {
    return partnerOptions().catch(() => []).then((partners) => {
      bodyEl.innerHTML = `
        <div class="m15-panel">
          <h3>Nova clínica / parceiro</h3>
          <p class="cad-microcopy">Uma clínica = um parceiro. Horários diferentes NÃO criam unidades duplicadas — a mesma unidade física carrega todas as agendas.</p>
          <form class="m15-form" id="cadFormParc" novalidate>
            ${fld("Nome (razão social ou público)", inp("nome", "", 'required minlength="2"'), { span: 6, req: true })}
            ${fld("Tipo", sel("tipo", [["clinica", "clínica"], ["consultorio", "consultório"], ["outro", "outro"]], "clinica"), 2)}
            ${fld("Status da parceria", sel("status",
              ["prospecto", "em_negociacao", "ativa", "pausada", "encerrada"], "prospecto"), 2)}
            ${fld("Cidade", inp("cidade", ""), 2)}
            ${fld("Observações (serviços, horários, acordo)", inp("observacao", ""), 12)}
            ${submitBtn("Salvar parceiro")}
          </form>
          <p class="m15-muted">Percentuais e repasses são configurados pelo papel gestor na aba Clínicas e Parceiros do Núcleo M15.</p>
        </div>
        <div class="m15-panel">
          <h3>Nova unidade física</h3>
          <form class="m15-form" id="cadFormUni" novalidate>
            ${fld("Parceiro", sel("partner_id", [["", "selecione…"]].concat(partners), "", "required"), { span: 4, req: true })}
            ${fld("Nome da unidade", inp("nome", "", "required"), { span: 4, req: true })}
            ${fld("Bairro", inp("bairro", ""), 2)}
            ${fld("Cidade", inp("cidade", ""), 2)}
            ${submitBtn("Salvar unidade")}
          </form>
        </div>
        ${recentsPanel("Parceiros recentes", "Prospecção e parcerias ativas")}`;

      const formP = bodyEl.querySelector("#cadFormParc");
      markDirtyOn(formP);
      wireSubmit(formP, () => {
        const payload = {
          nome: val(formP, "nome"), tipo: val(formP, "tipo"), status: val(formP, "status"),
        };
        setIf(payload, "cidade", val(formP, "cidade"));
        setIf(payload, "observacao", val(formP, "observacao"));
        return api("/parceiros", { method: "POST", body: JSON.stringify(payload) });
      }, (parc) => {
        successBanner(bodyEl, `<strong>Parceiro ${esc(parc.public_code)} criado</strong> — ${esc(parc.nome)}. Cadastre a unidade física abaixo, se aplicável.`);
        formP.reset();
        // Atualiza só o <select> de parceiro da Unidade (não o corpo inteiro
        // da aba — um reload completo apagaria o banner de sucesso acima
        // antes do operador conseguir vê-lo).
        const uniSel = bodyEl.querySelector('#cadFormUni [name="partner_id"]');
        if (uniSel) {
          partnerOptions().then((partners) => {
            const atual = uniSel.value;
            uniSel.innerHTML = '<option value="">selecione…</option>' +
              partners.map((p) => `<option value="${esc(p[0])}">${esc(p[1])}</option>`).join("");
            uniSel.value = atual;
          }).catch(() => { /* select mantém as opções anteriores */ });
        }
        loadParcRecents(bodyEl);
      });

      const formU = bodyEl.querySelector("#cadFormUni");
      markDirtyOn(formU);
      wireSubmit(formU, () => {
        if (!val(formU, "partner_id")) throw new Error("Selecione o parceiro da unidade.");
        const payload = { partner_id: val(formU, "partner_id"), nome: val(formU, "nome") };
        setIf(payload, "bairro", val(formU, "bairro"));
        setIf(payload, "cidade", val(formU, "cidade"));
        return api("/unidades", { method: "POST", body: JSON.stringify(payload) });
      }, (uni) => {
        successBanner(bodyEl, `<strong>Unidade ${esc(uni.public_code)} criada</strong> — ${esc(uni.nome)}.`);
        formU.reset();
        loadParcRecents(bodyEl);
      });
      return loadParcRecents(bodyEl);
    });
  };

  function loadParcRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/parceiros?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Nome", "Tipo", "Status", "Cidade"],
        d.itens.map((p) => [
          `<strong>${esc(p.public_code)}</strong>`, esc(p.nome), esc(p.tipo),
          pill(p.status), esc(p.cidade || "—"),
        ]),
        "Nenhum parceiro ainda."));
  }

  // ------------------------------------------------------------ CONTATO B2B

  LOADERS["contato-b2b"] = function (bodyEl) {
    return partnerOptions().catch(() => []).then((partners) => {
      bodyEl.innerHTML = `
        <div class="m15-panel">
          <h3>Novo contato B2B</h3>
          <p class="cad-microcopy">Pessoa de relacionamento dentro de uma clínica/parceiro — não é paciente.</p>
          <form class="m15-form" id="cadFormB2B" novalidate>
            ${fld("Clínica / parceiro", sel("partner_id", [["", "selecione…"]].concat(partners), "", "required"), { span: 4, req: true })}
            ${fld("Nome do contato", inp("nome", "", 'required minlength="2"'), { span: 4, req: true })}
            ${fld("Cargo / função", inp("cargo", ""), 4)}
            ${fld("Telefone / WhatsApp", inp("telefone", "", 'type="tel" placeholder="(21) 99999-9999"'), 4)}
            ${fld("E-mail", inp("email", "", 'type="email"'), 4)}
            ${fld("Contato principal", sel("principal", [["false", "não"], ["true", "sim"]], "false"), 4)}
            ${fld("Status do relacionamento", sel("rel_status",
              [["", "—"], "primeiro contato", "em conversa", "aguardando retorno", "ativo", "esfriou"], ""), 4)}
            ${fld("Próxima ação", inp("proxima_acao", "", 'placeholder="ex.: enviar proposta"'), 4)}
            ${fld("Data do próximo contato", dateInp("proximo_contato", ""), 4)}
            ${fld("Observações", inp("observacao", ""), 12)}
            ${submitBtn("Salvar contato B2B")}
          </form>
        </div>
        ${recentsPanel("Contatos B2B recentes", "Relacionamento comercial por parceiro")}`;

      const form = bodyEl.querySelector("#cadFormB2B");
      markDirtyOn(form);
      phoneMask(form.elements.telefone);
      wireSubmit(form, () => {
        if (!val(form, "partner_id")) throw new Error("Selecione a clínica/parceiro do contato.");
        const payload = {
          partner_id: val(form, "partner_id"),
          nome: val(form, "nome"),
          principal: val(form, "principal") === "true",
        };
        setIf(payload, "cargo", val(form, "cargo"));
        setIf(payload, "telefone", val(form, "telefone"));
        setIf(payload, "email", val(form, "email"));
        const obs = [
          val(form, "rel_status") ? "Relacionamento: " + val(form, "rel_status") : "",
          val(form, "proxima_acao") ? "Próxima ação: " + val(form, "proxima_acao") : "",
          val(form, "proximo_contato") ? "Próximo contato: " + fmtDate(val(form, "proximo_contato")) : "",
          val(form, "observacao"),
        ].filter(Boolean).join(" · ");
        setIf(payload, "observacao", obs);
        return api("/contatos-parceiros", { method: "POST", body: JSON.stringify(payload) });
      }, (contato) => {
        successBanner(bodyEl, `<strong>Contato ${esc(contato.public_code)} criado</strong> — ${esc(contato.nome)}.`);
        form.reset();
        loadB2BRecents(bodyEl);
      });
      return loadB2BRecents(bodyEl);
    });
  };

  function loadB2BRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/contatos-parceiros?tamanho=8").then((d) =>
      fillRecents(panel, ["Código", "Nome", "Cargo", "Principal", "Ativo"],
        d.itens.map((c) => [
          `<strong>${esc(c.public_code)}</strong>`, esc(c.nome), esc(c.cargo || "—"),
          c.principal ? "sim" : "—", pill(c.ativo ? "ativo" : "inativo"),
        ]),
        "Nenhum contato B2B ainda."));
  }

  // -------------------------------------------------------------- FINANCEIRO

  LOADERS.financeiro = function (bodyEl) {
    const pre = state.prefill || {};
    bodyEl.innerHTML = `
      <div class="m15-panel">
        <h3>Novo lançamento financeiro</h3>
        <p class="cad-microcopy">O financeiro NÃO guarda nome, telefone ou CPF. O paciente entra por vínculo técnico (exame/consulta) e o nome exibido é derivado da relação.</p>
        <form class="m15-form" id="cadFormFin" novalidate>
          ${fld("Tipo", sel("tipo", [["receita", "receita"], ["despesa", "despesa"], ["repasse", "repasse"]], "receita"), 2)}
          ${fld("Valor (R$)", inp("valor", "", 'inputmode="decimal" placeholder="250,00" required'), { span: 2, req: true })}
          ${fld("Categoria", inp("categoria", pre.categoria || "Espirometria", 'list="cadCat"'), 3)}
          ${fld("Competência", dateInp("data_competencia", "", { parcial: true }), { span: 3, help: HELP_PARCIAL })}
          ${fld("Origem do preço", sel("origem_preco",
            [["", "—"], "Tabela", "Promoção", "Parceria", "Negociação", "Cortesia"], "Tabela"), 2)}
          ${fld("Status do pagamento", sel("status", ["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"], "Recebido"), 3)}
          ${fld("Data de recebimento", dateInp("data_recebimento", ""), 3)}
          ${fld("Forma de pagamento", sel("forma_pagamento", [["", "—"], "Pix", "Dinheiro", "Cartão", "Outro"], "Pix"), 3)}
          ${fld("Descrição (sem dados pessoais)", inp("descricao", ""), { span: 3, help: "A API recusa nome, telefone, CPF ou termo clínico aqui." })}
          <div class="m15-form-full cad-vinculo" id="cadFinVinculo">
            <p class="cad-picker-nova-titulo">Vínculo técnico — exame ou consulta de origem</p>
            <div class="cad-vinculo-atual" id="cadFinVincAtual" ${pre.vinculo_code ? "" : "hidden"}>
              ${pre.vinculo_code ? `<span class="cad-chip-pessoa">Vinculado a <strong>${esc(pre.vinculo_code)}</strong>${pre.vinculo_pessoa ? " · " + esc(pre.vinculo_pessoa) : ""}</span>
              <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" id="cadFinVincLimpar">Remover vínculo</button>` : ""}
            </div>
            <div id="cadFinVincBusca" ${pre.vinculo_code ? "hidden" : ""}>
              <div class="cad-picker-search">
                <input id="cadFinQ" type="search" placeholder="Buscar paciente por nome, telefone ou PES-…">
                <button type="button" class="m15-btn m15-btn-sec" id="cadFinBuscar">Buscar</button>
              </div>
              <div class="cad-picker-results" id="cadFinResultados" hidden></div>
              <div class="cad-picker-results" id="cadFinAtendimentos" hidden></div>
            </div>
          </div>
          ${submitBtn("Salvar lançamento")}
        </form>
        ${datalist("cadCat", ["Espirometria", "Consulta", "Teleconsulta", "Atendimento domiciliar", "Outro"])}
      </div>
      ${recentsPanel("Lançamentos recentes", "Paciente exibido via vínculo técnico — sem PII gravada")}`;

    const form = bodyEl.querySelector("#cadFormFin");
    markDirtyOn(form);
    const vinculo = {
      spirometry_exam_id: pre.spirometry_exam_id || "",
      consultation_id: pre.consultation_id || "",
    };
    const limparBtn = bodyEl.querySelector("#cadFinVincLimpar");
    if (limparBtn) {
      limparBtn.addEventListener("click", () => {
        vinculo.spirometry_exam_id = "";
        vinculo.consultation_id = "";
        bodyEl.querySelector("#cadFinVincAtual").hidden = true;
        bodyEl.querySelector("#cadFinVincBusca").hidden = false;
      });
    }
    // busca de vínculo: paciente → atendimentos dele → escolha do registro
    wireMiniSearch(
      bodyEl.querySelector("#cadFinQ"), bodyEl.querySelector("#cadFinBuscar"),
      bodyEl.querySelector("#cadFinResultados"), (p) => {
        const atEl = bodyEl.querySelector("#cadFinAtendimentos");
        atEl.hidden = false;
        atEl.innerHTML = '<p class="cad-picker-vazio">Carregando atendimentos…</p>';
        Promise.all([
          api(`/espirometrias?person_id=${encodeURIComponent(p.id)}&tamanho=10`),
          api(`/consultas?person_id=${encodeURIComponent(p.id)}&tamanho=10`),
        ]).then(([exames, consultas]) => {
          const linhas = exames.itens.map((e) =>
            `<button type="button" class="cad-cand" data-esp="${esc(e.id)}" data-code="${esc(e.public_code)}">
               <strong>${esc(e.public_code)}</strong><span>espirometria · ${fmtDate(e.data_exame)} · ${esc(e.status)}</span></button>`)
            .concat(consultas.itens.map((c) =>
            `<button type="button" class="cad-cand" data-con="${esc(c.id)}" data-code="${esc(c.public_code)}">
               <strong>${esc(c.public_code)}</strong><span>consulta · ${fmtDate(c.data_consulta)} · ${esc(c.status)}</span></button>`));
          atEl.innerHTML = linhas.length
            ? `<p class="cad-picker-nova-titulo">Atendimentos de ${esc(p.nome_completo)}:</p>` + linhas.join("")
            : `<p class="cad-picker-vazio">${esc(p.nome_completo)} ainda não tem exame ou consulta — cadastre o atendimento primeiro.</p>`;
          atEl.querySelectorAll(".cad-cand").forEach((btn) => {
            btn.addEventListener("click", () => {
              vinculo.spirometry_exam_id = btn.dataset.esp || "";
              vinculo.consultation_id = btn.dataset.con || "";
              const atual = bodyEl.querySelector("#cadFinVincAtual");
              atual.hidden = false;
              atual.innerHTML = `<span class="cad-chip-pessoa">Vinculado a <strong>${esc(btn.dataset.code)}</strong> · ${esc(p.nome_completo)}</span>
                <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" id="cadFinVincLimpar2">Remover vínculo</button>`;
              bodyEl.querySelector("#cadFinVincBusca").hidden = true;
              atual.querySelector("#cadFinVincLimpar2").addEventListener("click", () => {
                vinculo.spirometry_exam_id = "";
                vinculo.consultation_id = "";
                atual.hidden = true;
                bodyEl.querySelector("#cadFinVincBusca").hidden = false;
              });
              state.dirty = true;
            });
          });
        }).catch((err) => {
          atEl.innerHTML = `<p class="cad-picker-vazio">Erro: ${esc(err.message || err)}</p>`;
        });
      });

    wireSubmit(form, () => {
      const valor = parseMoneyBR(val(form, "valor"));
      if (!valor) throw new Error("Informe um valor válido, ex.: 250,00.");
      const payload = {
        tipo: val(form, "tipo"),
        valor,
        status: val(form, "status"),
        idempotency_key: m15().idemKey(),
      };
      setIf(payload, "categoria", val(form, "categoria"));
      setIf(payload, "descricao", val(form, "descricao"));
      setIf(payload, "data_competencia", val(form, "data_competencia"));
      setIf(payload, "data_recebimento", val(form, "data_recebimento"));
      setIf(payload, "forma_pagamento", val(form, "forma_pagamento"));
      setIf(payload, "origem_preco", val(form, "origem_preco"));
      setIf(payload, "spirometry_exam_id", vinculo.spirometry_exam_id);
      setIf(payload, "consultation_id", vinculo.consultation_id);
      if (payload.status === "Recebido" && !payload.data_recebimento) {
        throw new Error('Status "Recebido" exige a data de recebimento.');
      }
      return api("/lancamentos", { method: "POST", body: JSON.stringify(payload) });
    }, (lanc) => {
      successBanner(bodyEl, `<strong>Lançamento ${esc(lanc.public_code)} criado</strong> — ${fmtMoneyBR(lanc.valor)} (${esc(lanc.status)}).`);
      form.reset();
      vinculo.spirometry_exam_id = "";
      vinculo.consultation_id = "";
      state.prefill = null;
      loadFinRecents(bodyEl);
    });
    return loadFinRecents(bodyEl);
  };

  function loadFinRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return api("/lancamentos?tamanho=8&incluir_contexto=true").then((d) =>
      fillRecents(panel, ["Código", "Valor", "Status", "Competência", "Vínculo · Paciente"],
        d.itens.map((e) => {
          const ctx = e.contexto || {};
          const ref = ctx.exame || ctx.consulta || ctx.encaminhamento;
          const vinc = ref
            ? `<strong>${esc(ref.public_code)}</strong>${ref.pessoa ? " · " + esc(ref.pessoa.nome_completo) : ""}`
            : "—";
          return [
            `<strong>${esc(e.public_code)}</strong>`, fmtMoneyBR(e.valor), pill(e.status),
            fmtDate(e.data_competencia), vinc,
          ];
        }),
        "Nenhum lançamento ainda."));
  }

  // ------------------------------------------------------------- navegação

  function activateSection() {
    document.querySelectorAll(".nav-item").forEach((i) => i.classList.remove("active"));
    document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
    const navBtn = document.querySelector(`.nav-item[data-section="${SECTION_ID}"]`);
    if (navBtn) navBtn.classList.add("active");
    const sec = sectionEl();
    if (sec) sec.classList.add("active");
  }

  function open(tab, prefill) {
    if (state.dirty && !window.confirm("Há alterações não salvas. Sair mesmo assim?")) return;
    state.dirty = false;
    state.tab = TABS.some((t) => t[0] === tab) ? tab : "lead";
    state.prefill = prefill || null;
    activateSection();
    render();
    const sec = sectionEl();
    if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function boot() {
    if (state.booted) return;
    state.booted = true;
    ensureStyles();

    // renderiza quando a seção é aberta pela sidebar/nav-hub
    const navBtn = document.querySelector(`.nav-item[data-section="${SECTION_ID}"]`);
    if (navBtn) navBtn.addEventListener("click", () => render());

    // sessão do núcleo mudou (login/logout) → re-renderiza a Central
    const cli = m15();
    if (cli && cli.onSessionChange) cli.onSessionChange(() => render());

    // botão contextual "Novo lançamento" da página Financeiro
    const finBtn = document.getElementById("financeNovoLancamentoBtn");
    if (finBtn) finBtn.addEventListener("click", () => open("financeiro"));

    // proteção contra fechamento com alterações não salvas
    window.addEventListener("beforeunload", (ev) => {
      if (state.dirty) {
        ev.preventDefault();
        ev.returnValue = "";
      }
    });

    render();
  }

  window.SoproCentral = { open, render };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
