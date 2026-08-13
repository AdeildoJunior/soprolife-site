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

  /* Abas primárias canônicas (M20). Paciente, Espirometria e Consulta
   * DEIXARAM de ser abas: viraram um fluxo único "Novo atendimento", porque
   * o paciente é sempre o primeiro passo do mesmo cadastro — não um cadastro
   * concorrente. O cadastro só de paciente é ação SECUNDÁRIA lá dentro. */
  const TABS = [
    ["lead", "Lead", "operacional"],
    ["atendimento", "Novo atendimento", "operacional"],
    ["clinica", "Clínica / Parceiro", "operacional"],
    ["contato-b2b", "Contato B2B", "operacional"],
    ["financeiro", "Financeiro", "gestor"],
  ];

  /* Deep-links contextuais antigos continuam funcionando, mas abrem o fluxo
   * único com o tipo certo pré-selecionado — sem formulário duplicado. */
  const TAB_ALIASES = {
    paciente: { tab: "atendimento", prefill: { somente_paciente: true } },
    espirometria: { tab: "atendimento", prefill: { tipo: "espirometria_soprolife" } },
    consulta: { tab: "atendimento", prefill: { tipo: "consulta_soprolife" } },
  };

  const TIPOS_ATENDIMENTO = [
    ["espirometria_soprolife", "Espirometria SoproLife",
     "Operação clínica e financeira da SoproLife."],
    ["espirometria_pastore", "Espirometria Pastore",
     "Exame realizado na unidade do parceiro Pastore."],
    ["consulta_soprolife", "Consulta SoproLife",
     "Receita bruta da SoproLife; repasse ao médico é lançamento separado."],
    ["espirometria_consulta_soprolife", "Espirometria + Consulta SoproLife",
     "Um paciente, um exame e uma consulta — criados juntos ou nenhum."],
  ];

  const TIPOS_COM_ESPIROMETRIA = [
    "espirometria_soprolife", "espirometria_pastore",
    "espirometria_consulta_soprolife",
  ];
  const TIPOS_COM_CONSULTA = ["consulta_soprolife", "espirometria_consulta_soprolife"];
  const TIPO_PASTORE = "espirometria_pastore";

  // Valores ARMAZENADOS do status do exame; a exibição passa pelo formatador
  // único (SoproStatus) — nada aqui reescreve o que vai para o banco.
  const STATUS_EXAME = ["Aguardando", "Realizado", "Laudo Liberado", "Cancelado", "Remarcado"];
  const STATUS_CONSULTA = ["Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"];

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

  /* M25.26 — ajuda contextual no padrão firmado pela M25.24.
   *
   * Botão real + irmão `role="tooltip"`, e não `title=""`: `title` não abre
   * por toque (o celular do operador em campo simplesmente não o mostra),
   * não abre por teclado e tem aparência decidida pelo sistema. A explicação
   * é anunciada por `aria-describedby` já ao pousar no botão.
   *
   * Usada com parcimônia: só onde a escolha tem consequência que a tela não
   * consegue mostrar sozinha (modalidade, local, valor, pessoa sem
   * atendimento). Texto permanente em todo campo vira ruído e ninguém lê.
   */
  let ajudaSeq = 0;

  function ajudaTip(texto, rotulo) {
    if (!texto) return "";
    ajudaSeq += 1;
    const id = "cadAjuda-" + ajudaSeq;
    return `<span class="cad-help" data-cad-help>
      <button type="button" class="cad-help-toggle" data-cad-help-toggle
        aria-describedby="${esc(id)}" aria-expanded="false"
        aria-label="${esc(rotulo || "Ajuda")}"><span aria-hidden="true">?</span></button>
      <span class="cad-help-bubble" role="tooltip" id="${esc(id)}" hidden>${esc(texto)}</span>
    </span>`;
  }

  // Abre/fecha sem re-render: reconstruir a árvore perderia o foco do teclado
  // e o que o operador já digitou no meio do formulário.
  function wireAjuda(root) {
    if (!root || root.getAttribute("data-cad-help-wired")) return;
    root.setAttribute("data-cad-help-wired", "1");
    root.addEventListener("click", (ev) => {
      const toggle = ev.target.closest("[data-cad-help-toggle]");
      if (!toggle) {
        root.querySelectorAll("[data-cad-help-toggle][aria-expanded='true']")
          .forEach((t) => setAjudaOpen(t, false));
        return;
      }
      ev.preventDefault();
      const aberto = toggle.getAttribute("aria-expanded") === "true";
      root.querySelectorAll("[data-cad-help-toggle][aria-expanded='true']")
        .forEach((t) => { if (t !== toggle) setAjudaOpen(t, false); });
      setAjudaOpen(toggle, !aberto);
    });
    root.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      const abertos = root.querySelectorAll("[data-cad-help-toggle][aria-expanded='true']");
      if (!abertos.length) return;
      abertos.forEach((t) => setAjudaOpen(t, false));
      ev.stopPropagation();
    });
  }

  function setAjudaOpen(toggle, aberto) {
    const wrap = toggle.closest("[data-cad-help]");
    const bolha = wrap && wrap.querySelector(".cad-help-bubble");
    if (!bolha) return;
    toggle.setAttribute("aria-expanded", aberto ? "true" : "false");
    bolha.hidden = !aberto;
  }

  // Campos no MESMO sistema visual do núcleo (m15-form / grid de 12 colunas)
  function fld(label, inner, opt) {
    let span = 3, help = "", req = false, ajuda = "";
    if (typeof opt === "number") span = opt;
    else if (opt && typeof opt === "object") {
      span = opt.span || 3; help = opt.help || ""; req = !!opt.req;
      ajuda = opt.ajuda || "";
    }
    const cls = "m15-field " + (span >= 12 ? "m15-form-full" : "m15-span-" + span);
    return `<label class="${cls}"><span class="m15-field-label" title="${esc(label)}">${esc(label)}${req ? ' <b class="cad-req" title="Campo obrigatório">*</b>' : ""}${ajudaTip(ajuda, label)}</span>${inner}` +
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
          if (status) status.hidden = true;
          // Conflitos já tratados com uma ação própria na tela (ex.: banner
          // de "lançamento duplicado" com botão de atualizar) não repetem o
          // erro genérico por cima.
          if (err && err.silencioso) return;
          const msg = (err && err.message) || String(err);
          // M25.26 — o erro deixou de ser só uma frase. O 422 traz
          // `campos_faltantes` em formato de máquina, e o campo correspondente
          // é marcado na tela: dizer "falta a data do exame" sem mostrar ONDE
          // ainda deixa o operador procurando num formulário longo.
          destacarCamposFaltantes(form, err);
          const comoCorrigir = err && err.detalhe && err.detalhe.como_corrigir;
          if (status) {
            status.hidden = false;
            status.textContent = "Erro: " + msg +
              (comoCorrigir ? " — " + comoCorrigir : "");
            status.className = "cad-submit-status cad-erro";
          }
          toast("Erro: " + msg, "erro");
        });
    });
  }

  /* Caminho do payload -> campo do formulário desta tela.
   *
   * O servidor devolve `espirometria.data_exame`; aqui o input se chama
   * `esp_data`. A ponte é explícita e curta de propósito: um caminho sem
   * tradução conhecida simplesmente não destaca nada, em vez de adivinhar um
   * seletor e marcar o campo errado. */
  const CAMPO_PARA_INPUT = {
    "espirometria.data_exame": "esp_data",
    "espirometria.modalidade": "esp_modalidade",
    "espirometria.local_atendimento": "esp_local",
    "espirometria.origem": "esp_origem",
    "espirometria.responsavel": "esp_responsavel",
    "espirometria.status": "esp_status",
    "consulta.data_consulta": "con_data",
    "consulta.status": "con_status",
    "consulta.profissional": "con_profissional",
    "consulta.retorno_data": "con_retorno_data",
    "consulta.retorno_intervalo_meses": "con_retorno_meses",
    "financeiro.espirometria.valor": "esp_valor",
    "financeiro.espirometria.data_recebimento": "esp_pgto_data",
    "financeiro.consulta.valor_bruto": "con_valor",
    "financeiro.consulta.data_recebimento": "con_pgto_data",
    "pessoa.nome_completo": "cadAtP_nome",
    "pessoa.data_nascimento": "cadAtP_nasc",
    "pessoa.cpf": "cadAtP_cpf",
  };

  function destacarCamposFaltantes(form, err) {
    form.querySelectorAll(".cad-campo-pendente").forEach((el) => {
      el.classList.remove("cad-campo-pendente");
    });
    const faltantes = (err && err.detalhe && err.detalhe.campos_faltantes) || [];
    let primeiro = null;
    faltantes.forEach((f) => {
      const nome = CAMPO_PARA_INPUT[f.campo];
      if (!nome) return;
      const campo = form.elements[nome];
      if (!campo) return;
      // O calendário troca o input original por um portador escondido; marcar
      // o invisível não mostraria nada. O rótulo é o que o operador enxerga.
      const alvo = campo.closest(".m15-field") || campo;
      alvo.classList.add("cad-campo-pendente");
      if (!primeiro) primeiro = alvo;
    });
    if (primeiro && primeiro.scrollIntoView) {
      primeiro.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function successBanner(container, html) {
    const box = document.createElement("div");
    box.className = "cad-sucesso";
    box.innerHTML = html;
    container.prepend(box);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // M23.1 — proteção de duplicidade: quando o backend recusa (409) uma
  // segunda receita para o mesmo exame/consulta, oferece atualizar o
  // lançamento existente com os dados (status/recebimento/forma) que o
  // operador já tinha preenchido, em vez de só avisar e deixar por conta
  // dele procurar o código na lista.
  function mostrarConflitoReceita(container, detalhe, dadosParaAtualizar) {
    const box = document.createElement("div");
    box.className = "cad-dup-aviso";
    box.innerHTML =
      `<strong>Lançamento duplicado evitado:</strong> ${esc(detalhe.mensagem)}` +
      `<button type="button" class="m15-btn m15-btn-sec" id="cadFinAtualizarExistente">` +
      `Atualizar ${esc(detalhe.lancamento_existente)} com estes dados</button>`;
    container.prepend(box);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    box.querySelector("#cadFinAtualizarExistente").addEventListener("click", () => {
      const btn = box.querySelector("#cadFinAtualizarExistente");
      btn.disabled = true;
      api(`/lancamentos/${encodeURIComponent(detalhe.lancamento_existente_id)}`, {
        method: "PATCH",
        body: JSON.stringify(dadosParaAtualizar),
      }).then((atualizado) => {
        box.remove();
        successBanner(container,
          `<strong>Lançamento ${esc(atualizado.public_code)} atualizado</strong> — ` +
          `${fmtMoneyBR(atualizado.valor)} (${esc(atualizado.status)}).`);
        loadFinRecents(container);
      }).catch((err) => {
        btn.disabled = false;
        toast("Erro ao atualizar: " + (err.message || err), "erro");
      });
    });
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

  /* Seletor de pessoa — M25.26 reescreveu a ordem da conversa.
   *
   * ANTES: a tela abria só com um campo de busca. Os dados de um paciente
   * NOVO (nome, WhatsApp, nascimento) só existiam depois de descobrir e
   * clicar em "+ Cadastrar nova pessoa". Como todo atendimento pertence
   * obrigatoriamente a uma pessoa, esse clique não decidia nada — era um
   * enigma antes do trabalho, e foi onde o teste real travou.
   *
   * AGORA: buscar em cima; abaixo, o formulário do paciente novo JÁ VISÍVEL.
   * Escolher alguém existente substitui o formulário pelo cartão da pessoa.
   * Nenhum clique é exigido para chegar aos campos, e nenhuma pessoa
   * duplicada nasce disso — a busca continua sendo o primeiro elemento e o
   * aviso de duplicado continua antes da criação.
   *
   * API (após wire):
   *   picker.selecionada()      → pessoa existente escolhida, ou null
   *   picker.dadosNovaPessoa()  → payload da pessoa nova (lança se inválido)
   *   picker.resolve()          → Promise<pessoa> PERSISTIDA (cria se nova)
   */
  function personPickerHtml(prefix, opts) {
    opts = opts || {};
    return `
    <div class="cad-picker" id="${prefix}Picker">
      <div class="cad-picker-search">
        <input id="${prefix}Q" type="search" placeholder="Buscar por nome, telefone ou PES-…"
          aria-label="Buscar paciente já cadastrado por nome, telefone ou código">
        <button type="button" class="m15-btn m15-btn-sec" id="${prefix}Buscar">Buscar</button>
      </div>
      <div class="cad-picker-results" id="${prefix}Resultados" hidden></div>
      <div class="cad-picker-selected" id="${prefix}Selecionada" hidden></div>
      ${opts.semCriar ? "" : `
      <div class="cad-picker-nova" id="${prefix}NovaBox">
        <p class="cad-picker-nova-titulo">
          Paciente novo — preencha abaixo, ou busque acima se ele já tem cadastro
        </p>
        <div class="m15-form cad-subgrid">
          ${fld("Nome completo", inp(prefix + "_nome", "", 'minlength="2" autocomplete="off"'), { span: 6, req: true })}
          ${fld("WhatsApp", inp(prefix + "_fone", "", 'type="tel" placeholder="(21) 99999-9999" autocomplete="off"'), 3)}
          ${fld("Nascimento", dateInp(prefix + "_nasc", ""), 3)}
          ${fld("E-mail (opcional)", inp(prefix + "_email", "", 'type="email" autocomplete="off"'), 4)}
          ${fld("CPF", inp(prefix + "_cpf", "", 'inputmode="numeric" placeholder="000.000.000-00" autocomplete="off"'),
            { span: 4, ajuda: "A CFM 2.381/2024 pede o CPF no laudo. Sem ele o laudo sai, mas fica marcado como pendente para entrega oficial. Deixe em branco se não houver CPF." })}
          ${fld("Sexo", sel(prefix + "_sexo",
            [["", "não informado"], ["feminino", "feminino"], ["masculino", "masculino"],
             ["outro", "outro"]], ""),
            { span: 4, ajuda: "Entra na identificação impressa do laudo." })}
          ${fld("Consentimento WhatsApp", sel(prefix + "_consent",
            [["", "não informado"], "concedido", "desconhecido", "revogado"], "concedido"), 4)}
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
      dupConfirmado: false,    // humano confirmou criar apesar do aviso
      guardianSelected: null,
    };
    const q = root.querySelector("#" + prefix + "Q");
    const buscar = root.querySelector("#" + prefix + "Buscar");
    const resultados = root.querySelector("#" + prefix + "Resultados");
    const selecionada = root.querySelector("#" + prefix + "Selecionada");
    const novaBox = root.querySelector("#" + prefix + "NovaBox");

    function notifyChange() {
      if (opts.onChange) opts.onChange();
    }

    // Cartão do paciente escolhido: contexto suficiente para confirmar que é
    // a pessoa certa, MAIS o que falta no cadastro dela. A pendência aparece
    // aqui, no instante da escolha — antes era descoberta semanas depois, na
    // emissão do laudo, por outra pessoa.
    function cartaoPessoa(p) {
      const nasc = p.data_nascimento ? fmtDate(p.data_nascimento) : "não informado";
      const fone = (p.contatos || []).find((c) => c.tipo === "whatsapp" || c.tipo === "telefone");
      const pend = p.cadastro_pendencias || [];
      const linhas = [
        `<span class="cad-cartao-linha">Nascimento: ${esc(nasc)}</span>`,
        `<span class="cad-cartao-linha">Contato: ${esc(fone ? fone.valor : "não informado")}</span>`,
        `<span class="cad-cartao-linha">CPF: ${esc(p.cpf_mascarado || "não informado")}</span>`,
      ].join("");
      const pendHtml = pend.length
        ? `<div class="cad-pendencias" id="${prefix}Pendencias">
             <strong>Falta no cadastro deste paciente:</strong>
             <ul>${pend.map((x) =>
               `<li>${esc(x.rotulo)}${x.bloqueia_laudo
                 ? ' <span class="cad-pend-bloqueia">pendência para o laudo</span>' : ""}
                 <span class="cad-pend-porque">${esc(x.por_que)}</span></li>`).join("")}</ul>
             <button type="button" class="m15-btn m15-btn-sec cad-btn-mini"
               id="${prefix}Corrigir">Corrigir cadastro</button>
           </div>`
        : `<p class="cad-cartao-ok">Cadastro completo.</p>`;
      return `<div class="cad-cartao-pessoa">
        <div class="cad-cartao-topo">
          <span class="cad-chip-pessoa"><strong>${esc(p.nome_completo)}</strong> ${esc(p.public_code)}</span>
          <button type="button" class="m15-btn m15-btn-sec cad-btn-mini" id="${prefix}Trocar">Trocar paciente</button>
        </div>
        <div class="cad-cartao-dados">${linhas}</div>
        ${pendHtml}
        <div class="cad-corrigir-box" id="${prefix}CorrigirBox" hidden></div>
      </div>`;
    }

    function showSelected(p) {
      picker.selected = p;
      if (novaBox) novaBox.hidden = true;
      resultados.hidden = true;
      selecionada.hidden = false;
      selecionada.innerHTML = cartaoPessoa(p);
      selecionada.querySelector("#" + prefix + "Trocar").addEventListener("click", () => {
        picker.selected = null;
        selecionada.hidden = true;
        if (novaBox) novaBox.hidden = false;
        q.value = "";
        q.focus();
        notifyChange();
      });
      const corrigir = selecionada.querySelector("#" + prefix + "Corrigir");
      if (corrigir) {
        corrigir.addEventListener("click", () => abrirCorrecao(p));
      }
      state.dirty = true;
      if (opts.onSelect) opts.onSelect(p);
      notifyChange();
    }

    /* "Corrigir cadastro" — Fase C.
     *
     * Edita SÓ os campos pendentes, dentro do cartão, sem trocar de tela e
     * sem re-renderizar o formulário do atendimento. É o que garante a
     * exigência da missão: nada do que já foi digitado no exame se perde.
     * Ao salvar, o cartão é redesenhado com as pendências recalculadas pelo
     * servidor — nunca por dedução do navegador.
     */
    function abrirCorrecao(p) {
      const box = selecionada.querySelector("#" + prefix + "CorrigirBox");
      if (!box) return;
      if (!box.hidden) { box.hidden = true; box.innerHTML = ""; return; }
      const pend = p.cadastro_pendencias || [];
      const campos = pend.map((x) => {
        if (x.campo === "cpf") {
          return fld("CPF", inp(prefix + "_fixCpf", "", 'inputmode="numeric" placeholder="000.000.000-00"'), 4);
        }
        if (x.campo === "data_nascimento") {
          return fld("Data de nascimento", dateInp(prefix + "_fixNasc", ""), 4);
        }
        if (x.campo === "sexo") {
          return fld("Sexo", sel(prefix + "_fixSexo",
            [["", "não informado"], ["feminino", "feminino"], ["masculino", "masculino"],
             ["outro", "outro"]], ""), 4);
        }
        return fld("WhatsApp", inp(prefix + "_fixFone", "", 'type="tel" placeholder="(21) 99999-9999"'), 4);
      }).join("");
      box.hidden = false;
      box.innerHTML = `<p class="cad-microcopy">Preencha o que falta. O atendimento
        que você já começou a digitar continua aqui.</p>
        <div class="m15-form cad-subgrid">${campos}</div>
        <div class="cad-actions">
          <button type="button" class="m15-btn" id="${prefix}SalvarFix">Salvar cadastro</button>
          <span class="cad-submit-status" id="${prefix}FixStatus" hidden></span>
        </div>`;
      const fixFone = box.querySelector(`[name="${prefix}_fixFone"]`);
      if (fixFone) phoneMask(fixFone);
      attachDates(box);
      wireAjuda(box);
      box.querySelector("#" + prefix + "SalvarFix").addEventListener("click", () => {
        salvarCorrecao(p, box);
      });
    }

    function salvarCorrecao(p, box) {
      const btn = box.querySelector("#" + prefix + "SalvarFix");
      const status = box.querySelector("#" + prefix + "FixStatus");
      const leia = (nome) => {
        const el = box.querySelector(`[name="${prefix}_${nome}"]`);
        return el ? (el.value || "").trim() : "";
      };
      const patch = {};
      setIf(patch, "cpf", leia("fixCpf"));
      setIf(patch, "data_nascimento", leia("fixNasc"));
      setIf(patch, "sexo", leia("fixSexo"));
      const fone = leia("fixFone");
      if (!Object.keys(patch).length && !fone) {
        toast("Preencha ao menos um campo para salvar.", "erro");
        return;
      }
      btn.disabled = true;
      status.hidden = false;
      status.className = "cad-submit-status";
      status.textContent = "Salvando…";
      const passos = [];
      if (Object.keys(patch).length) {
        passos.push(api("/pessoas/" + encodeURIComponent(p.id), {
          method: "PATCH", body: JSON.stringify(patch),
        }));
      }
      if (fone) {
        passos.push(api(`/pessoas/${encodeURIComponent(p.id)}/contatos`, {
          method: "POST",
          body: JSON.stringify({ tipo: "whatsapp", valor: fone, principal: true }),
        }));
      }
      Promise.all(passos)
        // Relê do servidor: as pendências que sobraram são as que ELE
        // reconhece, não as que o navegador imagina ter resolvido.
        .then(() => api("/pessoas/" + encodeURIComponent(p.id)))
        .then((atualizada) => {
          showSelected(atualizada);
          toast("Cadastro atualizado.");
        })
        .catch((err) => {
          btn.disabled = false;
          status.hidden = false;
          status.className = "cad-submit-status cad-erro";
          status.textContent = "Erro: " + (err.message || err);
        });
    }

    wireMiniSearch(q, buscar, resultados, showSelected);

    // Deep-link contextual (M19): o CRM abre a Central já com o paciente no
    // campo de busca. Só o código público viaja — nunca nome nem telefone.
    const preCodigo = (state.prefill || {}).person_codigo;
    if (preCodigo && q && buscar) {
      q.value = preCodigo;
      buscar.click();
    }

    if (novaBox) {
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

    // Volta o seletor ao estado inicial após um envio bem-sucedido. O estado
    // inicial agora é "formulário de paciente novo visível", que é o mesmo do
    // primeiro carregamento — sem isto o segundo cadastro na mesma aba
    // começaria num estado diferente do primeiro.
    picker.resetState = function () {
      picker.selected = null;
      picker.dupConfirmado = false;
      picker.guardianSelected = null;
      if (novaBox) novaBox.hidden = false;
      if (selecionada) { selecionada.hidden = true; selecionada.innerHTML = ""; }
      if (resultados) resultados.hidden = true;
      if (q) q.value = "";
      const dupAviso = root.querySelector("#" + prefix + "DupAviso");
      if (dupAviso) dupAviso.hidden = true;
      const gBox = root.querySelector("#" + prefix + "GuardianBox");
      if (gBox) gBox.hidden = true;
    };

    picker.selecionada = function () { return picker.selected; };

    picker.confirmouDuplicado = function () { return picker.dupConfirmado; };

    /* Dados do paciente novo, SEM persistir nada.
     *
     * Existe para o fluxo atômico: quem envia é `POST /atendimentos/
     * novo-paciente`, que cria pessoa e atendimento na mesma transação. O
     * navegador não cria mais a pessoa por conta própria e depois torce para
     * o atendimento dar certo. */
    picker.dadosNovaPessoa = function () {
      if (!novaBox) throw new Error("Formulário de paciente novo indisponível.");
      const leia = (nome) => {
        const el = root.querySelector(`[name="${prefix}_${nome}"]`);
        return el ? (el.value || "").trim() : "";
      };
      const nome = leia("nome");
      if (nome.length < 2) {
        throw new Error("Informe o nome completo do paciente, ou busque um paciente já cadastrado.");
      }
      const fone = leia("fone");
      const email = leia("email");
      const payload = { nome_completo: nome, contatos: [] };
      if (fone) payload.contatos.push({ tipo: "whatsapp", valor: fone, principal: true });
      if (email) payload.contatos.push({ tipo: "email", valor: email, principal: !fone });
      setIf(payload, "data_nascimento", leia("nasc"));
      setIf(payload, "cpf", leia("cpf"));
      setIf(payload, "sexo", leia("sexo"));
      setIf(payload, "consentimento_whatsapp", leia("consent"));
      return payload;
    };

    // Mostra os candidatos a duplicado devolvidos pelo servidor e registra a
    // decisão humana. Fusão automática continua não existindo.
    picker.mostrarDuplicados = function (candidatos) {
      const dupAviso = root.querySelector("#" + prefix + "DupAviso");
      if (!dupAviso) return;
      dupAviso.hidden = false;
      dupAviso.innerHTML =
        `<strong>Possível duplicado (${candidatos.length}):</strong> já existe cadastro ` +
        `parecido. Escolha um existente abaixo ou confirme a criação.` +
        candidatos.map((c) =>
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
    };

    // Resolve a seleção em UMA pessoa PERSISTIDA (cria quando é nova).
    // Continua sendo o caminho do Lead, que não tem operação combinada.
    picker.resolve = function () {
      if (picker.selected) return Promise.resolve(picker.selected);
      let payload;
      try {
        payload = picker.dadosNovaPessoa();
      } catch (err) {
        return Promise.reject(err);
      }
      const nome = payload.nome_completo;
      const fone = root.querySelector(`[name="${prefix}_fone"]`).value.trim();
      const nasc = root.querySelector(`[name="${prefix}_nasc"]`).value.trim();

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
        link.href = href + "?v=2026081201";
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

  // Status de espirometria SEMPRE pelo formatador único (M20).
  function statusExame(v) {
    return window.SoproStatus ? window.SoproStatus.espirometria(v) : v;
  }

  function pillExame(v) {
    return pill(statusExame(v));
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

  // ------------------------------------------------------- NOVO ATENDIMENTO

  /* Fluxo canônico ÚNICO (M20). Passo 1: paciente (existente ou novo).
   * Passo 2: tipo do atendimento. Passo 3: os dados que o tipo exige.
   *
   * O paciente permanece entidade estável: a origem (SoproLife/Pastore)
   * pertence ao ATENDIMENTO, nunca à identidade da pessoa. O mesmo paciente
   * pode ter origens diferentes ao longo do tempo sem virar dois cadastros.
   *
   * "Cadastrar somente paciente" é ação SECUNDÁRIA daqui — não é aba. */

  /* Catálogo do fluxo (M25.26): modalidades, rótulo/sugestões de local e o
   * valor de tabela. Vem do servidor para não existir uma segunda lista aqui
   * que um dia discorde da que o backend valida — e para o preço não virar
   * número mágico espalhado por arquivos de tela.
   *
   * Falha de rede não derruba o formulário: sem catálogo o operador ainda
   * cadastra, apenas sem sugestão de modalidade/valor. O que NÃO acontece é
   * o navegador inventar uma lista de reserva. */
  function resolveConfigAtendimento() {
    return api("/atendimentos/configuracao").catch(() => ({}));
  }

  function resolvePastore() {
    // O backend aplica a resolução canônica fail-closed e devolve somente
    // unidades ativas da Pastore; o navegador não tenta adivinhar por nome.
    return api("/pastore/configuracao-atendimento")
      .then((d) => ({
        partner: d.partner,
        unidades: d.unidades || [],
        modalidade: d.modalidade,
        origem: d.origem,
      }))
      .catch((err) => ({ erro: err.message || String(err) }));
  }

  function pastoreReadonly(label, value, span) {
    return `<div class="m15-field m15-span-${span || 4} cad-readonly-field">
      <span class="m15-field-label">${esc(label)}</span>
      <output>${esc(value || "—")}</output>
    </div>`;
  }

  function blocoEspirometriaConteudoHtml(pastore, ehPastore, cfg) {
    const commonStart = `
      <h4 class="cad-bloco-titulo">Espirometria</h4>
      ${ehPastore ? '<div class="cad-pastore-aviso" id="cadAtPastoreAviso"></div>' : ""}
      <div class="m15-form cad-subgrid">
        ${fld("Data do exame", dateInp("esp_data", "", { parcial: true }),
          { span: 3, help: HELP_PARCIAL, req: true })}
        ${fld("Status", sel("esp_status",
          (window.SoproStatus
            ? window.SoproStatus.opcoesEspirometria(STATUS_EXAME)
            : STATUS_EXAME), "Realizado"), 3)}
        ${fld("Broncodilatador", sel("esp_bd",
          [["", "não informado"], ["false", "sem broncodilatador"],
           ["true", "com broncodilatador"]], "false"), 3)}`;
    const commonEnd = `
        ${fld("Técnico / responsável", inp("esp_responsavel", "Adeildo", 'list="cadResp"'), 4)}
        ${fld("Próximo acompanhamento", dateInp("esp_followup", ""),
          { span: 4, help: "Opcional — sem data vale a regra vigente do exame." })}
        ${fld("Observações do exame", inp("esp_observacao", ""), 12)}
      </div>`;

    if (!ehPastore) {
      /* M25.26 — modalidade e local pararam de ser duas listas concorrentes.
       *
       * O catálogo vem do servidor (`GET /atendimentos/configuracao`): as
       * modalidades oferecidas, o rótulo do local para cada uma e o valor de
       * tabela. Nada disso é constante escrita aqui — inclusive o R$ 220,00,
       * que antes só existia como placeholder e agora nasce preenchido e
       * editável, vindo de `M15_ESPIROMETRIA_SOPROLIFE_VALOR_PADRAO`.
       *
       * "Clínica parceira" saiu da lista: o tipo SoproLife não aceita
       * parceiro/unidade, então escolhê-la produzia um exame que a emissão do
       * laudo depois recusava por falta de unidade. Existe um registro assim
       * em produção, criado exatamente por essa armadilha. */
      const cfgEsp = (cfg && cfg.espirometria_soprolife) || {};
      const modalidades = cfgEsp.modalidades || [];
      const indisponiveis = cfgEsp.modalidades_indisponiveis || [];
      const valorPadrao = cfgEsp.valor_padrao
        ? String(cfgEsp.valor_padrao).replace(".", ",") : "";
      const ajudaModalidade =
        "A natureza do atendimento — onde o exame foi feito, em termos de tipo. " +
        "Decide o local que o laudo imprime." +
        (indisponiveis.length
          ? " " + indisponiveis.map((m) => m.motivo).join(" ")
          : "");
      return commonStart + `
        ${fld("Modalidade", sel("esp_modalidade",
          [["", "selecione…"]].concat(modalidades.map((m) => [m.valor, m.rotulo])), ""),
          { span: 4, ajuda: ajudaModalidade })}
        ${fld("Local do atendimento", inp("esp_local", "", 'list="cadLocais" disabled placeholder="escolha a modalidade primeiro"'),
          { span: 4, ajuda: "Onde especificamente o exame aconteceu — o NOME do lugar, não uma categoria. A modalidade já diz o tipo." })}
        ${fld("Origem", inp("esp_origem", "", 'list="cadOrigens"'),
          { span: 4, ajuda: "Como o paciente chegou até a SoproLife (Google, indicação, empresa…). Não é o lugar do exame." })}
        ${fld("Valor da espirometria (R$)", inp("esp_valor", valorPadrao,
          'inputmode="decimal" placeholder="220,00"'),
          { span: 4, ajuda: "Nasce com o valor de tabela e pode ser alterado. Apagando o campo, nenhum lançamento financeiro é criado — nada é inferido." })}
        ${fld("Status do pagamento", sel("esp_pgto_status",
          ["Recebido", "Pendente", "Parcial", "Cortesia"], "Recebido"), 4)}
        ${fld("Data de recebimento", dateInp("esp_pgto_data", ""), 4)}
        ${fld("Forma de pagamento", sel("esp_pgto_forma",
          [["", "—"], "Pix", "Dinheiro", "Cartão", "Outro"], "Pix"), 4)}
        ${datalist("cadLocais", [])}
      ` + commonEnd;
    }

    let unitField = pastoreReadonly("Unidade operacional", "Nenhuma unidade ativa", 6);
    const unidades = (pastore && pastore.unidades) || [];
    if (unidades.length === 1) {
      unitField = pastoreReadonly("Unidade operacional", unidades[0].nome, 6);
    } else if (unidades.length > 1) {
      unitField = fld(
        "Unidade operacional",
        sel("esp_unidade", [["", "selecione a unidade…"]].concat(
          unidades.map((u) => [u.id, u.nome])
        ), ""),
        { span: 6, req: true, help: "Somente unidades Pastore ativas." }
      );
    }
    return commonStart + `
        ${pastoreReadonly("Parceiro", pastore && pastore.partner && pastore.partner.nome, 6)}
        ${unitField}
        ${pastoreReadonly("Modalidade", "Clínica parceira", 4)}
        ${pastoreReadonly("Origem", (pastore && pastore.origem) || "Pastore", 4)}
      ` + commonEnd;
  }

  function blocoEspirometriaHtml(pastore, ehPastore, cfg) {
    return `
      <div class="cad-bloco" id="cadAtBlocoEsp" hidden>
        ${blocoEspirometriaConteudoHtml(pastore, ehPastore, cfg)}
      </div>`;
  }

  function blocoConsultaHtml() {
    return `
      <div class="cad-bloco" id="cadAtBlocoCon" hidden>
        <h4 class="cad-bloco-titulo">Consulta SoproLife</h4>
        <p class="cad-microcopy">A receita bruta da consulta é da SoproLife. O repasse ao
          médico é uma obrigação financeira SEPARADA: mesmo a 100%, a receita bruta
          continua registrada e auditável dentro da SoproLife.</p>
        <div class="m15-form cad-subgrid">
          ${fld("Data da consulta", dateInp("con_data", "", { parcial: true }),
            { span: 3, help: HELP_PARCIAL, req: true })}
          ${fld("Status", sel("con_status", STATUS_CONSULTA, "Realizada"), 3)}
          ${fld("Modalidade", sel("con_modalidade",
            [["", "—"], ["teleconsulta", "teleconsulta"], ["residencial", "residencial"],
             ["cowork", "cowork"], ["clinica_parceira", "clínica parceira"]], "teleconsulta"), 3)}
          ${fld("Médico / profissional", inp("con_profissional", ""), 3)}
          ${fld("Retorno", sel("con_retorno",
            [["sem_retorno", "sem retorno programado"], ["data", "em uma data específica"],
             ["intervalo_meses", "em um intervalo de meses"]], "sem_retorno"),
            { span: 4, help: "Nenhum retorno é assumido — inclusive o de 6 meses." })}
          ${fld("Data do retorno", dateInp("con_retorno_data", ""), 4)}
          ${fld("Intervalo (meses)", inp("con_retorno_meses", "",
            'type="number" min="1" max="60" placeholder="6"'), 4)}
          ${fld("Receita bruta da consulta (R$)", inp("con_valor", "",
            'inputmode="decimal" placeholder="300,00"'),
            { span: 4, help: "Opcional. Em branco, nenhum lançamento é criado." })}
          ${fld("Status do pagamento", sel("con_pgto_status",
            ["Recebido", "Pendente", "Parcial", "Cortesia"], "Recebido"), 4)}
          ${fld("Data de recebimento", dateInp("con_pgto_data", ""), 4)}
          ${fld("Repasse ao médico", sel("con_repasse_modo",
            [["", "não informado"], ["percentual", "percentual da consulta"],
             ["valor", "valor fixo"]], ""),
            { span: 4, help: "Lançamento separado — nunca abatido da receita bruta." })}
          ${fld("Percentual do repasse (%)", inp("con_repasse_pct", "",
            'type="number" min="0" max="100" step="0.01" placeholder="100"'), 4)}
          ${fld("Valor do repasse (R$)", inp("con_repasse_valor", "",
            'inputmode="decimal" placeholder="300,00"'), 4)}
          ${fld("Observações da consulta", inp("con_observacao", ""), 12)}
        </div>
      </div>`;
  }

  LOADERS.atendimento = function (bodyEl) {
    const pre = state.prefill || {};
    return Promise.all([resolvePastore(), resolveConfigAtendimento()])
      .then(([pastore, cfg]) => {
      const tipoInicial = TIPOS_ATENDIMENTO.some((t) => t[0] === pre.tipo)
        ? pre.tipo : "espirometria_soprolife";
      const somentePacienteInicial = !!pre.somente_paciente;

      bodyEl.innerHTML = `
        <div class="m15-panel">
          <h3>Novo atendimento</h3>
          <p class="cad-microcopy">Um fluxo só: escolha o paciente, escolha o tipo e preencha
            o que o tipo pede. O paciente é a identidade estável — a origem pertence ao
            atendimento, então o mesmo paciente pode ter atendimentos SoproLife e Pastore
            sem virar dois cadastros.</p>
          <form class="m15-form" id="cadFormAtend" novalidate>

            <div class="m15-form-full cad-passo">
              <h4 class="cad-passo-titulo"><span class="cad-passo-num">1</span> Paciente</h4>
              ${personPickerHtml("cadAtP")}
            </div>

            <div class="m15-form-full cad-passo" id="cadAtPasso2">
              <h4 class="cad-passo-titulo"><span class="cad-passo-num">2</span> Tipo de atendimento</h4>
              <div class="cad-tipos" role="radiogroup" aria-label="Tipo de atendimento">
                ${TIPOS_ATENDIMENTO.map((t) => `
                  <label class="cad-tipo">
                    <input type="radio" name="tipo" value="${esc(t[0])}"${t[0] === tipoInicial ? " checked" : ""}>
                    <span class="cad-tipo-nome">${esc(t[1])}</span>
                    <span class="cad-tipo-desc">${esc(t[2])}</span>
                  </label>`).join("")}
              </div>
            </div>

            <div class="m15-form-full cad-passo" id="cadAtPasso3">
              <h4 class="cad-passo-titulo"><span class="cad-passo-num">3</span> Dados do atendimento</h4>
              ${blocoEspirometriaHtml(pastore, tipoInicial === TIPO_PASTORE, cfg)}
              ${blocoConsultaHtml()}
            </div>

            ${submitBtn("Salvar atendimento")}
          </form>

          <!-- M25.26 — Fase B. Cadastrar só a pessoa saiu do caminho
               principal. Era um checkbox grande NO MEIO do passo 1 que, ao
               ser marcado, apagava os passos 2 e 3 — parecia etapa normal do
               Novo atendimento e escondia metade do formulário. É uma
               operação DIFERENTE (pré-cadastro, contato, CRM), então virou
               ação secundária, separada e rotulada pelo que faz. -->
          <div class="cad-acao-secundaria">
            <button type="button" class="m15-btn m15-btn-sec" id="cadAtSoPessoa">
              Cadastrar pessoa sem atendimento
            </button>
            <span class="cad-acao-secundaria-nota">
              Cria somente o cadastro da pessoa e seus contatos. Nenhum exame ou
              consulta será criado agora.
            </span>
          </div>
        </div>
        ${recentsPanel("Atendimentos recentes", "Espirometrias e consultas — na hora")}`;

      const form = bodyEl.querySelector("#cadFormAtend");
      markDirtyOn(form);
      const picker = wirePersonPicker(form, "cadAtP", {
        onChange: () => aplicarModo(),
      });
      const passo2 = bodyEl.querySelector("#cadAtPasso2");
      const passo3 = bodyEl.querySelector("#cadAtPasso3");
      const blocoEsp = bodyEl.querySelector("#cadAtBlocoEsp");
      const blocoCon = bodyEl.querySelector("#cadAtBlocoCon");
      const btnSalvar = form.querySelector('button[type="submit"]');
      let renderedPastore = tipoInicial === TIPO_PASTORE;

      function tipoAtual() {
        const marcado = form.querySelector('input[name="tipo"]:checked');
        return marcado ? marcado.value : "";
      }

      /* Local depende da modalidade — Fase D.
       *
       * Enquanto não há modalidade, o campo de local fica desabilitado e diz
       * por quê. Escolhida a modalidade, ele ganha o rótulo, o placeholder e
       * as sugestões DAQUELA modalidade, vindos do servidor. É o que impede a
       * contradição "residencial + Clínica" de ser digitável, em vez de
       * recusá-la só depois do envio. */
      function aplicarModalidade() {
        const modSel = form.elements.esp_modalidade;
        const localEl = form.elements.esp_local;
        if (!modSel || !localEl) return;
        const cfgEsp = (cfg && cfg.espirometria_soprolife) || {};
        const escolhida = (cfgEsp.modalidades || [])
          .find((m) => m.valor === modSel.value);
        const dl = bodyEl.querySelector("#cadLocais");
        if (!escolhida) {
          localEl.disabled = true;
          localEl.placeholder = "escolha a modalidade primeiro";
          if (!localEl.dataset.tocado) localEl.value = "";
          if (dl) dl.innerHTML = "";
          return;
        }
        localEl.disabled = false;
        localEl.placeholder = escolhida.local_rotulo || "Local do atendimento";
        // Só preenche sozinho o que o operador ainda não tocou — nunca
        // sobrescreve um lugar que ele digitou.
        if (!localEl.dataset.tocado) {
          localEl.value = escolhida.local_padrao || "";
        }
        if (dl) {
          dl.innerHTML = (escolhida.local_sugestoes || [])
            .map((v) => `<option value="${esc(v)}">`).join("");
        }
      }

      function aplicarModo() {
        const tipo = tipoAtual();
        const temEsp = TIPOS_COM_ESPIROMETRIA.indexOf(tipo) !== -1;
        const temCon = TIPOS_COM_CONSULTA.indexOf(tipo) !== -1;
        blocoEsp.hidden = !temEsp;
        blocoCon.hidden = !temCon;
        const ehPastore = tipo === TIPO_PASTORE;
        if (temEsp && renderedPastore !== ehPastore) {
          blocoEsp.innerHTML = blocoEspirometriaConteudoHtml(pastore, ehPastore, cfg);
          renderedPastore = ehPastore;
          // attachDates só roda uma vez no carregamento inicial da aba; sem
          // chamar de novo aqui, o "Data do exame" recém-injetado ao trocar
          // para/de Pastore ficava sem o calendário (attachAll é idempotente
          // via data-m15-date-attached, então repetir a chamada é seguro).
          attachDates(blocoEsp);
          wireAjuda(blocoEsp);
          wireModalidade();
        }
        if (ehPastore) {
          const pastoreAviso = bodyEl.querySelector("#cadAtPastoreAviso");
          if (pastore.erro) {
            pastoreAviso.className = "cad-pastore-aviso cad-erro";
            pastoreAviso.textContent = "Pastore indisponível: " + pastore.erro;
          } else if (!pastore.unidades.length) {
            pastoreAviso.className = "cad-pastore-aviso cad-erro";
            pastoreAviso.textContent = "Pastore indisponível: nenhuma unidade operacional ativa.";
          } else {
            pastoreAviso.className = "cad-pastore-aviso";
            pastoreAviso.textContent =
              "O paciente não paga a SoproLife. Este exame entra no fechamento mensal " +
              "da Pastore sem criar recebimento ou valor individual.";
          }
        }
      }

      function wireModalidade() {
        const modSel = form.elements.esp_modalidade;
        const localEl = form.elements.esp_local;
        if (modSel) modSel.addEventListener("change", aplicarModalidade);
        if (localEl) {
          localEl.addEventListener("input", () => {
            localEl.dataset.tocado = localEl.value.trim() ? "1" : "";
          });
        }
        aplicarModalidade();
      }

      form.querySelectorAll('input[name="tipo"]').forEach((r) => {
        r.addEventListener("change", aplicarModo);
      });

      wireAjuda(bodyEl);
      wireModalidade();

      // Deep-link contextual (M4/M19): outras telas (CRM, aba Pessoas) abrem
      // a Central pedindo "cadastrar só a pessoa". Agora isso é a ação
      // secundária explícita, e não um checkbox escondido dentro do passo 1.
      if (somentePacienteInicial) {
        const nome = bodyEl.querySelector('[name="cadAtP_nome"]');
        if (nome) nome.focus();
        toast("Preencha os dados e use “Cadastrar pessoa sem atendimento”.");
      }
      aplicarModo();

      // Fase B — ação secundária: cria a pessoa e NADA mais. Sem atendimento,
      // sem espirometria, sem consulta, sem lançamento financeiro.
      const btnSoPessoa = bodyEl.querySelector("#cadAtSoPessoa");
      btnSoPessoa.addEventListener("click", () => {
        if (picker.selecionada()) {
          toast("Este paciente já está cadastrado — use “Trocar paciente” para cadastrar outro.", "erro");
          return;
        }
        btnSoPessoa.disabled = true;
        picker.resolve().then((pessoa) => {
          state.dirty = false;
          successBanner(bodyEl,
            `<strong>Pessoa ${esc(pessoa.public_code)} criada</strong> — ` +
            `${esc(pessoa.nome_completo)}. Nenhum exame ou consulta foi criado.`);
          form.reset();
          picker.resetState();
          aplicarModo();
          if (m15() && m15().refresh) m15().refresh();
        }).catch((err) => {
          toast("Erro: " + (err.message || err), "erro");
        }).then(() => { btnSoPessoa.disabled = false; });
      });

      wireSubmit(form, () => {
        const tipo = tipoAtual();
        if (!tipo) throw new Error("Escolha o tipo do atendimento.");
        if (tipo === TIPO_PASTORE && (pastore.erro || !pastore.unidades.length)) {
          throw new Error("Pastore indisponível: " +
            (pastore.erro || "nenhuma unidade operacional ativa."));
        }
        const blocos = { tipo, idempotency_key: m15().idemKey() };
        if (TIPOS_COM_ESPIROMETRIA.indexOf(tipo) !== -1) {
          blocos.espirometria = montarEspirometria(form, tipo, pastore, cfg);
        }
        if (TIPOS_COM_CONSULTA.indexOf(tipo) !== -1) {
          blocos.consulta = montarConsulta(form);
        }
        const financeiro = montarFinanceiro(form, tipo);
        if (financeiro) blocos.financeiro = financeiro;

        const existente = picker.selecionada();
        if (existente) {
          return api("/atendimentos", {
            method: "POST",
            body: JSON.stringify(Object.assign({ person_id: existente.id }, blocos)),
          }).then((criado) => ({ criado, pessoa: existente }));
        }

        /* Paciente novo: UMA operação. Antes eram duas chamadas (POST
         * /pessoas e depois POST /atendimentos) e uma falha na segunda
         * deixava o paciente criado sozinho — o operador então recomeçava e
         * criava a mesma pessoa de novo. */
        const pessoa = picker.dadosNovaPessoa();
        const corpo = Object.assign({ pessoa }, blocos);
        if (picker.confirmouDuplicado()) corpo.confirmar_duplicado = true;
        return api("/atendimentos/novo-paciente", {
          method: "POST", body: JSON.stringify(corpo),
        }).then((criado) => ({
          criado, pessoa: { public_code: criado.person_public_code, nome_completo: pessoa.nome_completo },
        })).catch((err) => {
          const cands = err && err.detalhe && err.detalhe.candidatos;
          if (err && err.code === "possivel_duplicado" && cands) {
            picker.mostrarDuplicados(cands);
          }
          throw err;
        });
      }, (res) => {
        const c = res.criado;
        const partes = [];
        if (c.espirometria) partes.push("Espirometria " + c.espirometria.public_code);
        if (c.consulta) partes.push("Consulta " + c.consulta.public_code);
        const lanc = (c.lancamentos || [])
          .map((l) => `${l.public_code} (${l.componente}, ${l.tipo})`).join(", ");
        // Pendência de cadastro aparece no SUCESSO, não como erro: o exame
        // aconteceu e está registrado. O que falta é o cadastro do paciente,
        // e quem acabou de atendê-lo é quem tem a informação à mão.
        const pend = c.cadastro_pendencias || [];
        const avisoPend = pend.length
          ? `<span class="cad-sucesso-pendencia">Falta no cadastro do paciente: ` +
            `${esc(pend.map((x) => x.rotulo).join(", "))}. ` +
            `Busque o paciente acima e use “Corrigir cadastro”.</span>`
          : "";
        successBanner(bodyEl,
          `<strong>${esc(partes.join(" + "))} criada(s)</strong> para ` +
          `${esc(res.pessoa.nome_completo)} (${esc(res.pessoa.public_code)}).` +
          (lanc ? ` Lançamentos: ${esc(lanc)}.` : " Nenhum lançamento financeiro criado.") +
          avisoPend);
        form.reset();
        picker.resetState();
        aplicarModo();
        loadAtendRecents(bodyEl);
      });
      return loadAtendRecents(bodyEl);
    });
  };

  function montarEspirometria(form, tipo, pastore, cfg) {
    const bloco = { status: val(form, "esp_status") };
    if (!val(form, "esp_data")) throw new Error("Informe a data do exame.");
    bloco.data_exame = val(form, "esp_data");
    if (val(form, "esp_bd") !== "") bloco.broncodilatador = val(form, "esp_bd") === "true";
    setIf(bloco, "responsavel", val(form, "esp_responsavel"));
    setIf(bloco, "observacao", val(form, "esp_observacao"));
    setIf(bloco, "proximo_followup", val(form, "esp_followup"));
    if (tipo === TIPO_PASTORE) {
      const unidade = pastore.unidades.length === 1
        ? pastore.unidades[0].id : val(form, "esp_unidade");
      if (!unidade) throw new Error("Espirometria Pastore exige a unidade operacional.");
      bloco.partner_id = pastore.partner.id;
      bloco.partner_unit_id = unidade;
    } else {
      const modalidade = val(form, "esp_modalidade");
      const local = val(form, "esp_local");
      /* Fase D — a combinação é conferida ANTES do envio.
       *
       * O servidor aplica as mesmas regras (report_origin), então isto não é
       * a única defesa: é a que evita o operador descobrir o problema depois
       * de preencher a tela inteira. A ausência total continua permitida —
       * 13 exames em produção vieram de importação sem modalidade, e exigi-la
       * agora quebraria a compatibilidade com o histórico. */
      const cfgEsp = (cfg && cfg.espirometria_soprolife) || {};
      const escolhida = (cfgEsp.modalidades || []).find((m) => m.valor === modalidade);
      if (modalidade && !escolhida) {
        throw new Error("Modalidade não reconhecida — escolha uma das opções da lista.");
      }
      if (escolhida && escolhida.local_obrigatorio && !local) {
        throw new Error(
          `Informe o local do atendimento (${escolhida.local_rotulo}) para a modalidade "${escolhida.rotulo}".`
        );
      }
      if (!modalidade && local) {
        throw new Error(
          "Escolha a modalidade do atendimento — sem ela, o local sozinho não " +
          "diz onde o exame aconteceu e o laudo não consegue derivar o endereço."
        );
      }
      setIf(bloco, "modalidade", modalidade);
      setIf(bloco, "local_atendimento", local);
      setIf(bloco, "origem", val(form, "esp_origem"));
    }
    return bloco;
  }

  function montarConsulta(form) {
    const bloco = { status: val(form, "con_status") };
    if (!val(form, "con_data")) throw new Error("Informe a data da consulta.");
    bloco.data_consulta = val(form, "con_data");
    setIf(bloco, "modalidade", val(form, "con_modalidade"));
    setIf(bloco, "profissional", val(form, "con_profissional"));
    setIf(bloco, "observacao", val(form, "con_observacao"));
    const retorno = val(form, "con_retorno") || "sem_retorno";
    bloco.retorno = retorno;
    if (retorno === "data") {
      const d = val(form, "con_retorno_data");
      if (!d) throw new Error("Informe a data do retorno da consulta.");
      bloco.retorno_data = d;
    } else if (retorno === "intervalo_meses") {
      const meses = parseInt(val(form, "con_retorno_meses"), 10);
      if (!meses || meses < 1) throw new Error("Informe o intervalo de retorno em meses.");
      bloco.retorno_intervalo_meses = meses;
    }
    return bloco;
  }

  function montarFinanceiro(form, tipo) {
    // Pastore não cria nem envia controles de pagamento do paciente.
    if (tipo === TIPO_PASTORE) return null;
    const financeiro = {};
    if (TIPOS_COM_ESPIROMETRIA.indexOf(tipo) !== -1) {
      const valor = parseMoneyBR(val(form, "esp_valor"));
      if (valor) {
        const comp = { valor, status: val(form, "esp_pgto_status") };
        setIf(comp, "data_recebimento", val(form, "esp_pgto_data"));
        setIf(comp, "forma_pagamento", val(form, "esp_pgto_forma"));
        setIf(comp, "data_competencia", val(form, "esp_data"));
        if (comp.status === "Recebido" && !comp.data_recebimento) {
          throw new Error('Espirometria com pagamento "Recebido" exige a data de recebimento.');
        }
        financeiro.espirometria = comp;
      }
    }
    if (TIPOS_COM_CONSULTA.indexOf(tipo) !== -1) {
      const bruto = parseMoneyBR(val(form, "con_valor"));
      const modo = val(form, "con_repasse_modo");
      if (!bruto && modo) {
        throw new Error("Repasse ao médico exige a receita bruta da consulta.");
      }
      if (bruto) {
        const comp = { valor_bruto: bruto, status: val(form, "con_pgto_status") };
        setIf(comp, "data_recebimento", val(form, "con_pgto_data"));
        setIf(comp, "data_competencia", val(form, "con_data"));
        if (comp.status === "Recebido" && !comp.data_recebimento) {
          throw new Error('Consulta com pagamento "Recebido" exige a data de recebimento.');
        }
        if (modo === "percentual") {
          const pct = val(form, "con_repasse_pct");
          if (!pct) throw new Error("Informe o percentual do repasse ao médico.");
          comp.repasse_medico_percentual = pct;
        } else if (modo === "valor") {
          const v = parseMoneyBR(val(form, "con_repasse_valor"));
          if (!v) throw new Error("Informe o valor do repasse ao médico.");
          comp.repasse_medico_valor = v;
        }
        financeiro.consulta = comp;
      }
    }
    return Object.keys(financeiro).length ? financeiro : null;
  }

  function loadAtendRecents(bodyEl) {
    const panel = bodyEl.querySelector(".cad-recentes");
    return Promise.all([
      api("/espirometrias?tamanho=5"),
      api("/consultas?tamanho=5"),
    ]).then(([exames, consultas]) => {
      const linhas = (exames.itens || []).map((e) => [
        `<strong>${esc(e.public_code)}</strong>`, "Espirometria", fmtDate(e.data_exame),
        pillExame(e.status_exibicao || e.status),
        esc(e.modalidade || "—"),
      ]).concat((consultas.itens || []).map((c) => [
        `<strong>${esc(c.public_code)}</strong>`, "Consulta", fmtDate(c.data_consulta),
        pill(c.status), esc(c.profissional || "—"),
      ]));
      fillRecents(panel, ["Código", "Tipo", "Data", "Status", "Detalhe"], linhas,
        "Nenhum atendimento ainda.");
    });
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
        <div class="cad-financeiro-aviso">Atendimentos SoproLife geram lançamentos financeiros
          automaticamente. Use este formulário para despesas, repasses, ajustes e receitas
          avulsas. Exames Pastore entram no fechamento mensal.</div>
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
      return api("/lancamentos", { method: "POST", body: JSON.stringify(payload) })
        .catch((err) => {
          // M23.1: o backend recusa (409) uma receita duplicada do mesmo
          // exame/consulta. A mensagem chega como JSON dentro de err.message
          // (ver m15-nucleo.js api()); em vez de mostrar o JSON cru, oferece
          // atualizar o lançamento existente com os dados já preenchidos.
          let detalhe = null;
          try { detalhe = JSON.parse(err.message); } catch (_e) { /* mensagem simples */ }
          if (detalhe && detalhe.codigo === "receita_ja_existe") {
            mostrarConflitoReceita(bodyEl, detalhe, {
              status: payload.status,
              data_recebimento: payload.data_recebimento,
              forma_pagamento: payload.forma_pagamento,
            });
            const silencioso = new Error(detalhe.mensagem);
            silencioso.silencioso = true;
            throw silencioso;
          }
          throw err;
        });
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
    prefill = prefill || null;
    // Botão contextual antigo ("Novo paciente", "Nova espirometria", "Nova
    // consulta") abre o fluxo ÚNICO já no modo/tipo certo — sem manter um
    // segundo formulário vivo só por compatibilidade.
    const alias = TAB_ALIASES[tab];
    if (alias) {
      tab = alias.tab;
      prefill = Object.assign({}, alias.prefill, prefill || {});
    }
    if (prefill && prefill.partner_name && /pastore/i.test(prefill.partner_name)) {
      prefill = Object.assign({}, prefill, { tipo: "espirometria_pastore" });
      tab = "atendimento";
    }
    state.tab = TABS.some((t) => t[0] === tab) ? tab : "lead";
    state.prefill = prefill;
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
