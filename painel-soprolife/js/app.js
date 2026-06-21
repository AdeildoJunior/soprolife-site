const state = {
  resumo: null,
  crm: [],
  leads: [],
  marketing: null,
  charts: {},
  tarefas: null,
  documentos: [],
  financeiro: null,
  automacoes: null,
  runtimeStatus: null,
  dashboardSummary: null,
  crmView: "hub",
  followupPacientes: null,
  followupSummary: null,
};

const slug = (text) =>
  String(text)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, "-");

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Erro ao carregar ${path}`);
  }
  return response.json();
}

async function loadOptionalJson(path) {
  try {
    const response = await fetch(path);

    if (response.status === 404) {
      return null;
    }

    if (!response.ok) {
      console.warn(`Status local indisponível: ${path}`);
      return null;
    }

    return response.json();
  } catch (error) {
    console.warn("Status local indisponível", error);
    return null;
  }
}

function destroyChart(key) {
  if (state.charts[key]) {
    state.charts[key].destroy();
    delete state.charts[key];
  }
}

function createChart(key, canvasSelector, config) {
  const canvas = document.querySelector(canvasSelector);
  if (!canvas) return null;

  destroyChart(key);
  state.charts[key] = new Chart(canvas, config);
  return state.charts[key];
}

function resizeCharts() {
  Object.values(state.charts).forEach((chart) => chart.resize());
}

function normalizeCrmRecord(item) {
  const bairro = item.bairro || "";
  const regiao = item.regiao || "";
  return {
    clinica: item.nome_clinica || item.clinica || "",
    bairro: regiao && regiao !== bairro ? `${regiao} · ${bairro}` : bairro,
    tipo: item.tipo_clinica || item.tipo || "",
    etapa: item.etapa || "",
    prioridade: item.prioridade || "",
    proximaAcao: item.proxima_acao || item.proximaAcao || "",
    dataProximaAcao: item.data_proxima_acao || item.dataProximaAcao || "",
    responsavel: item.responsavel || "",
    ultimaInteracao: item.ultima_interacao || null,
  };
}

function todayIso() {
  return new Date().toISOString().split("T")[0];
}

function parseDateIso(dateStr) {
  if (!dateStr) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) return dateStr;
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(dateStr)) {
    const [d, m, y] = dateStr.split("/");
    return `${y}-${m}-${d}`;
  }
  return "";
}

function formatDateBr(dateStr) {
  const iso = parseDateIso(dateStr);
  if (!iso) return "";
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

async function init() {
  try {
    const [resumo, crm, leads, marketing, tarefas, documentos, financeiro, automacoes] = await Promise.all([
      loadJson("./data/resumo.json"),
      loadJson("./data/crm-clinicas.json"),
      loadJson("./data/leads.json"),
      loadJson("./data/marketing.json"),
      loadJson("./data/tarefas.json"),
      loadJson("./data/documentos.json"),
      loadJson("./data/financeiro.json"),
      loadJson("./data/automacoes.json")
    ]);

    state.resumo = resumo;
    state.crm = crm;
    state.leads = leads;
    state.marketing = marketing;
    state.tarefas = tarefas;
    state.documentos = documentos;
    state.financeiro = financeiro;
    state.automacoes = automacoes;

    state.runtimeStatus = await loadOptionalJson("./data/runtime-status.local.json");
    state.dashboardSummary = await loadOptionalJson("./data/resumo-dashboard.local.json");

    const crmLocal = await loadOptionalJson("./data/crm-clinicas.local.json");
    if (crmLocal && Array.isArray(crmLocal.clinicas)) {
      state.crm = crmLocal.clinicas.map(normalizeCrmRecord);
    }

    const followupLocal = await loadOptionalJson("./data-private/followup-pacientes.local.json");
    if (followupLocal) {
      state.followupPacientes = followupLocal;
    }

    const followupSummary = await loadOptionalJson("./data/followup-pacientes-summary.local.json");
    if (followupSummary) {
      state.followupSummary = followupSummary;
    }

    renderCards();
    renderDataFreshness();
    renderTasks();
    renderCrmView();
    renderLeadStats();
    renderLeadPipeline();
    renderLeadsTable();
    renderMarketingStats();
    renderSeoFocus();
    renderSeoList();
    renderCharts();
    renderTaskBoard();
    renderDocuments();
    renderFinance();
    renderAutomations();
    bindEvents();
  } catch (error) {
    document.body.innerHTML = `
      <main style="padding:32px;font-family:system-ui">
        <h1>Não foi possível carregar o painel</h1>
        <p>${error.message}</p>
        <p>Abra usando um servidor local, por exemplo: <code>python3 -m http.server 8000</code>.</p>
      </main>
    `;
  }
}

const CARD_GROUPS = [
  {
    title: "Comercial",
    subtitle: "Prospecção e pipeline",
    icon: "📈",
    keys: ["totalLeads", "leadsNovos", "leadsAgendados", "leadsConcluidos", "clinicasCadastradas"]
  },
  {
    title: "Atendimentos / CRM",
    subtitle: "Acompanhamento e recorrência",
    icon: "🩺",
    keys: [
      "pacientesEmAcompanhamento",
      "examesEspirometriaRealizados",
      "teleconsultasRealizadas",
      "followupsPendentes",
      "lembretesWhatsAppPendentes",
      "recorrenciasAtivas",
      "consultasPrevistas"
    ]
  },
  {
    title: "Financeiro",
    subtitle: "Receita prevista e realizada",
    icon: "💰",
    keys: ["receitaPrevista", "receitaRecebida"]
  },
  {
    title: "Marketing",
    subtitle: "Produção de conteúdo",
    icon: "📣",
    keys: ["conteudosPlanejados"]
  },
  {
    title: "Operação",
    subtitle: "Tarefas e agenda",
    icon: "⚙️",
    keys: ["tarefasPendentes", "eventosAgendados"]
  }
];

function renderMetricCard(card) {
  return `
    <article class="metric-card">
      <div>
        <span>${card.label}</span>
        <strong>${card.value}</strong>
      </div>
      <div class="variation ${card.type === "neutral" ? "neutral" : ""}">${card.variation}</div>
    </article>
  `;
}

function renderCards() {
  const container = document.querySelector("#cardsGrid");
  const cards = getDashboardCards();

  const byKey = new Map(cards.filter((c) => c.key).map((c) => [c.key, c]));
  const usedKeys = new Set();
  const sections = [];

  for (const group of CARD_GROUPS) {
    const groupCards = group.keys
      .filter((key) => byKey.has(key))
      .map((key) => {
        usedKeys.add(key);
        return byKey.get(key);
      });

    if (groupCards.length === 0) continue;

    sections.push(`
      <section class="cards-section">
        <div class="cards-section-header">
          ${group.icon ? `<span class="cards-section-icon">${group.icon}</span>` : ""}
          <h3>${group.title}</h3>
          ${group.subtitle ? `<span class="cards-section-sub">${group.subtitle}</span>` : ""}
        </div>
        <div class="cards-group">${groupCards.map(renderMetricCard).join("")}</div>
      </section>
    `);
  }

  const others = cards.filter((c) => !c.key || !usedKeys.has(c.key));
  if (others.length > 0) {
    sections.push(`
      <section class="cards-section">
        <div class="cards-section-header">
          <h3>Outros indicadores</h3>
        </div>
        <div class="cards-group">${others.map(renderMetricCard).join("")}</div>
      </section>
    `);
  }

  container.innerHTML = sections.join("");
}

function getDashboardCards() {
  const localSummary = state.dashboardSummary;

  const isSafeLocalSummary = Boolean(
    localSummary?.source?.safeToDisplay &&
    localSummary?.source?.containsPersonalData === false &&
    localSummary?.source?.containsHealthData === false &&
    Array.isArray(localSummary?.cards)
  );

  if (!isSafeLocalSummary) {
    return state.resumo.cards;
  }

  return localSummary.cards.map((card) => ({
    key: card.key,
    label: card.label,
    value: formatDashboardValue(card.key, card.value),
    variation: "Local seguro",
    type: "neutral"
  }));
}

function formatDashboardValue(key, value) {
  const currencyKeys = new Set(["receitaPrevista", "receitaRecebida"]);

  if (currencyKeys.has(key)) {
    return new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: "BRL",
      maximumFractionDigits: 0
    }).format(Number(value) || 0);
  }

  return value;
}

function renderTasks() {
  const list = document.querySelector("#tasksList");
  list.innerHTML = state.resumo.tarefas.map((task) => `
    <div class="task">
      <div>
        <strong>${task.title}</strong>
        <span>${task.tag}</span>
      </div>
      <span class="badge ${slug(task.priority)}">${task.priority}</span>
    </div>
  `).join("");
}


function countCrmEtapa(etapa) {
  return state.crm.filter((item) => item.etapa === etapa).length;
}

function countCrmPrioridade(prioridade) {
  return state.crm.filter((item) => item.prioridade === prioridade).length;
}

function renderCrmStats() {
  const emProspeccao = state.crm.filter(
    (item) => item.etapa !== "Parceiro ativo" && item.etapa !== "Não abordado"
  ).length;

  const stats = [
    { label: "Clínicas cadastradas", value: state.crm.length, hint: "base total" },
    { label: "Em prospecção", value: emProspeccao, hint: "ativas no funil" },
    { label: "Prioridade alta", value: countCrmPrioridade("Alta"), hint: "foco imediato" },
    { label: "Com ação definida", value: state.crm.filter((c) => c.proximaAcao).length, hint: "próximas ações" }
  ];

  const container = document.querySelector("#crmStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => `
    <article class="crm-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
      ${item.hint ? `<small>${item.hint}</small>` : ""}
    </article>
  `).join("");
}

function renderCrmFunnelVisual() {
  const steps = [
    { label: "Não abordado", value: countCrmEtapa("Não abordado"), hint: "sem contato" },
    { label: "Primeiro contato", value: countCrmEtapa("Primeiro contato"), hint: "abordagem feita" },
    { label: "Em conversa", value: countCrmEtapa("Em conversa"), hint: "diálogo ativo" },
    { label: "Proposta enviada", value: countCrmEtapa("Proposta enviada"), hint: "aguardando retorno" },
    { label: "Parceiro ativo", value: countCrmEtapa("Parceiro ativo"), hint: "meta alcançada" }
  ];

  const max = Math.max(...steps.map((step) => step.value), 1);
  const container = document.querySelector("#crmFunnelVisual");
  if (!container) return;

  container.innerHTML = steps.map((step) => {
    const fill = Math.max(10, Math.round((step.value / max) * 100));
    return `
      <div class="funnel-step" style="--fill: ${fill}%">
        <small>${step.label}</small>
        <strong>${step.value}</strong>
        <span>${step.hint}</span>
      </div>
    `;
  }).join("");
}


function renderFollowupB2B() {
  const container = document.querySelector("#followupB2B");
  if (!container) return;

  const today = todayIso();

  const hoje = state.crm
    .filter((c) => { const iso = parseDateIso(c.dataProximaAcao); return iso && iso === today; })
    .sort((a, b) => (a.prioridade === "Alta" ? -1 : 1));

  const atrasados = state.crm
    .filter((c) => { const iso = parseDateIso(c.dataProximaAcao); return iso && iso < today; })
    .sort((a, b) => parseDateIso(a.dataProximaAcao).localeCompare(parseDateIso(b.dataProximaAcao)));

  const altaPrio = state.crm
    .filter((c) => c.prioridade === "Alta")
    .sort((a, b) => a.clinica.localeCompare(b.clinica));

  const emConversa = state.crm
    .filter((c) => c.etapa === "Em conversa")
    .sort((a, b) => a.clinica.localeCompare(b.clinica));

  function itemHtml(item) {
    const data = formatDateBr(item.dataProximaAcao);
    return `<li class="followup-item">
      <div class="followup-item-top">
        <strong>${item.clinica}</strong>
        ${data ? `<span class="followup-date">${data}</span>` : ""}
      </div>
      ${item.proximaAcao ? `<small>${item.proximaAcao}</small>` : ""}
    </li>`;
  }

  function card({ cls, icon, title, items, empty }) {
    const count = items.length;
    const body = count > 0
      ? `<ul class="followup-list">${items.map(itemHtml).join("")}</ul>`
      : `<p class="followup-empty">${empty}</p>`;
    return `<article class="followup-card ${cls}">
      <div class="followup-card-header">
        <span class="followup-icon" aria-hidden="true">${icon}</span>
        <div>
          <strong>${title}</strong>
          <small>${count} ${count === 1 ? "item" : "itens"}</small>
        </div>
      </div>
      ${body}
    </article>`;
  }

  container.innerHTML = `
    <div class="panel-header">
      <h3>Follow-up B2B / PCMSO</h3>
      <span>Ações prioritárias do CRM</span>
    </div>
    <div class="followup-grid">
      ${card({ cls: "card-hoje",     icon: "⚡", title: "Hoje",            items: hoje,      empty: "Nenhuma ação agendada para hoje" })}
      ${card({ cls: "card-atrasado", icon: "⚠",  title: "Atrasados",       items: atrasados, empty: "Nenhum follow-up em atraso" })}
      ${card({ cls: "card-alta",     icon: "🔴", title: "Alta prioridade", items: altaPrio,  empty: "Nenhuma clínica de alta prioridade" })}
      ${card({ cls: "card-conversa", icon: "💬", title: "Em conversa",     items: emConversa, empty: "Nenhuma clínica em conversa ativa" })}
    </div>
  `;
}

function getCrmCardValue(key) {
  const cards = state.dashboardSummary?.cards;
  if (!Array.isArray(cards)) return "—";
  const card = cards.find((c) => c.key === key);
  return card != null ? card.value : "—";
}

function crmModuleCard({ icon, title, subtitle, view, stats }) {
  const statsHtml = stats.length > 0
    ? `<div class="crm-module-stats">${stats.map((s) => `
        <div class="crm-module-stat">
          <strong>${s.value}</strong>
          <span>${s.label}</span>
        </div>
      `).join("")}</div>`
    : `<div class="crm-module-stats crm-module-stats-empty"><span>Em breve</span></div>`;

  return `
    <article class="crm-module-card" data-crm-view="${view}" tabindex="0" role="button">
      <div class="crm-module-icon">${icon}</div>
      <h3 class="crm-module-title">${title}</h3>
      <p class="crm-module-subtitle">${subtitle}</p>
      ${statsHtml}
      <div class="crm-module-cta">Ver detalhes →</div>
    </article>
  `;
}

function renderCrmView() {
  const container = document.querySelector("#crmView");
  if (!container) return;

  switch (state.crmView) {
    case "clinicas": renderCrmClinicas(container); break;
    case "pacientes": renderCrmPacientes(container); break;
    case "relatorios": renderCrmPlaceholder(container, "relatorios"); break;
    case "automacoes-crm": renderCrmAutomacoes(container); break;
    default: renderCrmHub(container);
  }
}

function renderCrmHub(container) {
  const emProspeccao = state.crm.filter(
    (item) => item.etapa !== "Parceiro ativo" && item.etapa !== "Não abordado"
  ).length;

  container.innerHTML = `
    <div class="crm-hub-header">
      <p class="eyebrow">Relacionamento</p>
      <h2>CRM SoproLife</h2>
      <p class="section-sub">Relacionamento, parcerias, pacientes e recorrência</p>
    </div>
    <div class="crm-hub-grid">
      ${crmModuleCard({
        icon: "🏥",
        title: "Clínicas e Parceiros",
        subtitle: "Prospecção B2B, parcerias e PCMSO",
        view: "clinicas",
        stats: [
          { label: "Cadastradas", value: state.crm.length },
          { label: "Em prospecção", value: emProspeccao },
          { label: "Prio. alta", value: countCrmPrioridade("Alta") }
        ]
      })}
      ${crmModuleCard({
        icon: "🩺",
        title: "Pacientes",
        subtitle: "Consultas, espirometrias, recorrência e follow-up",
        view: "pacientes",
        stats: [
          { label: "Acompanhamento", value: getCrmCardValue("pacientesEmAcompanhamento") },
          { label: "Espirometrias", value: getCrmCardValue("examesEspirometriaRealizados") },
          { label: "Follow-ups", value: getCrmCardValue("followupsPendentes") }
        ]
      })}
      ${crmModuleCard({
        icon: "📊",
        title: "Relatórios CRM",
        subtitle: "Indicadores consolidados de relacionamento",
        view: "relatorios",
        stats: []
      })}
      ${crmModuleCard({
        icon: "⚡",
        title: "Automações CRM",
        subtitle: "Lembretes, reativação e tarefas automáticas",
        view: "automacoes-crm",
        stats: []
      })}
    </div>
  `;

  container.querySelectorAll("[data-crm-view]").forEach((card) => {
    card.addEventListener("click", () => {
      state.crmView = card.dataset.crmView;
      renderCrmView();
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        state.crmView = card.dataset.crmView;
        renderCrmView();
      }
    });
  });
}

function renderCrmClinicas(container) {
  container.innerHTML = `
    <div class="crm-subview-header">
      <button class="crm-back-btn" id="crmBackBtn">← CRM</button>
      <div>
        <p class="eyebrow">Comercial B2B · PCMSO</p>
        <h2>Clínicas e Parceiros</h2>
        <p class="section-sub">Prospecção B2B, parcerias e PCMSO</p>
      </div>
      <select id="crmFilter" class="crm-filter-select">
        <option value="Todos">Todas as etapas</option>
        <option value="Não abordado">Não abordado</option>
        <option value="Primeiro contato">Primeiro contato</option>
        <option value="Em conversa">Em conversa</option>
        <option value="Proposta enviada">Proposta enviada</option>
        <option value="Parceiro ativo">Parceiro ativo</option>
      </select>
    </div>

    <div id="crmStats" class="crm-stats"></div>

    <article class="panel crm-funnel-panel">
      <div class="panel-header">
        <h3>Pipeline B2B</h3>
        <span>Da prospecção à parceria ativa</span>
      </div>
      <div id="crmFunnelVisual" class="crm-funnel-visual"></div>
    </article>

    <article class="panel followup-b2b-panel">
      <div id="followupB2B"></div>
    </article>

    <article class="panel">
      <div class="panel-header">
        <h3>Clínicas e parceiros</h3>
        <span class="safe-label">Somente dados institucionais</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Clínica</th>
              <th>Região / Bairro</th>
              <th>Tipo</th>
              <th>Etapa</th>
              <th>Prioridade</th>
              <th>Próxima ação</th>
              <th>Data</th>
              <th>Responsável</th>
            </tr>
          </thead>
          <tbody id="crmTable"></tbody>
        </table>
      </div>
    </article>
  `;

  renderCrmStats();
  renderCrmFunnelVisual();
  renderFollowupB2B();
  renderCrmTable();

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });

  document.querySelector("#crmFilter").addEventListener("change", (e) => {
    renderCrmTable(e.target.value);
  });
}

function renderCrmPacientes(container) {
  const fp = state.followupPacientes;
  const espi  = fp ? fp.espirometria  : [];
  const cons  = fp ? fp.consultas     : [];
  const hoje  = todayIso();

  function statusClass(r) {
    if (r.status_followup === "atrasado") return "status-atrasado";
    if (r.status_followup === "hoje")     return "status-hoje";
    if (r.status_followup === "em_breve") return "status-breve";
    if (r.status_followup === "futuro")   return "status-futuro";
    return "status-sem-data";
  }

  function statusLabel(r) {
    if (r.status_followup === "atrasado") return "Atrasado";
    if (r.status_followup === "hoje")     return "Hoje";
    if (r.status_followup === "em_breve") return formatDateBr(r.data_followup);
    if (r.status_followup === "futuro")   return formatDateBr(r.data_followup) || "Agendado";
    return "Sem data";
  }

  function patientItem(r) {
    const tag = `<span class="fp-status ${statusClass(r)}">${statusLabel(r)}</span>`;
    const waBtn = r.whatsapp_url
      ? `<a class="fp-wa-btn" href="${r.whatsapp_url}" target="_blank" rel="noopener noreferrer" title="Abrir WhatsApp com mensagem pré-preenchida">WhatsApp</a>`
      : `<span class="fp-wa-btn fp-wa-disabled">Sem tel.</span>`;
    return `
      <li class="fp-item">
        <div class="fp-item-row">
          <span class="fp-nome">${escapeHtml(r.nome)}</span>
          ${tag}
          ${waBtn}
        </div>
        ${r.motivo ? `<div class="fp-motivo">${escapeHtml(r.motivo)}</div>` : ""}
      </li>`;
  }

  function followupBlock(title, icon, records) {
    const atr    = records.filter(r => r.status_followup === "atrasado");
    const hoje_  = records.filter(r => r.status_followup === "hoje");
    const breve  = records.filter(r => r.status_followup === "em_breve");
    const futuro = records.filter(r => r.status_followup === "futuro");
    const semdat = records.filter(r => r.status_followup === "sem_data");

    function card(cls, label, iconC, items) {
      const body = items.length
        ? `<ul class="fp-list">${items.map(patientItem).join("")}</ul>`
        : `<p class="fp-empty">Nenhum registro</p>`;
      return `
        <article class="fp-card ${cls}">
          <div class="fp-card-header">
            <span class="fp-icon">${iconC}</span>
            <div>
              <strong>${label}</strong>
              <span class="fp-count">${items.length}</span>
            </div>
          </div>
          ${body}
        </article>`;
    }

    return `
      <section class="fp-section">
        <h3 class="fp-section-title">${icon} ${title}</h3>
        <div class="fp-grid">
          ${card("fp-card-atrasado", "Atrasados",      "🔴", atr)}
          ${card("fp-card-hoje",     "Hoje",            "🟡", hoje_)}
          ${card("fp-card-breve",    "Próximos 7 dias", "🟢", breve)}
          ${card("fp-card-futuro",   "Agendados",       "🔵", futuro)}
        </div>
        ${semdat.length ? `<div class="fp-semdata-note">Sem data agendada: ${semdat.length}</div>` : ""}
      </section>`;
  }

  const hasData = fp !== null;
  const privNote = hasData
    ? `<div class="crm-private-note"><span>🔒</span><p>Dados carregados do arquivo privado local (<code>data-private/followup-pacientes.local.json</code>). Este arquivo é gitignored e não é enviado ao GitHub. O envio de WhatsApp é sempre assistido — nenhuma mensagem é disparada automaticamente.</p></div>`
    : `<div class="crm-safe-note"><span>ℹ️</span><p>Arquivo privado não encontrado. Execute <code>python3 painel-soprolife/scripts/generate-followup-pacientes.py --write</code> para gerar os dados de follow-up.</p></div>`;

  container.innerHTML = `
    <div class="crm-subview-header">
      <button class="crm-back-btn" id="crmBackBtn">← CRM</button>
      <div>
        <p class="eyebrow">B2C · Clínico</p>
        <h2>Pacientes</h2>
        <p class="section-sub">Follow-up de espirometrias e consultas — envio assistido via WhatsApp</p>
      </div>
    </div>

    <div class="crm-stats">
      <article class="crm-stat-card">
        <span>Em acompanhamento</span>
        <strong>${getCrmCardValue("pacientesEmAcompanhamento")}</strong>
        <small>base ativa</small>
      </article>
      <article class="crm-stat-card">
        <span>Espirometrias</span>
        <strong>${espi.length || getCrmCardValue("examesEspirometriaRealizados")}</strong>
        <small>com follow-up</small>
      </article>
      <article class="crm-stat-card">
        <span>Consultas</span>
        <strong>${cons.length || getCrmCardValue("teleconsultasRealizadas")}</strong>
        <small>com follow-up</small>
      </article>
      <article class="crm-stat-card">
        <span>Recorrências ativas</span>
        <strong>${getCrmCardValue("recorrenciasAtivas")}</strong>
        <small>periódicos</small>
      </article>
    </div>

    ${hasData ? `
      ${followupBlock("Follow-up Espirometria", "🫁", espi)}
      ${followupBlock("Follow-up Consultas",    "💊", cons)}
    ` : ""}

    ${privNote}
  `;

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderCrmAutomacoes(container) {
  // ── helpers internos ────────────────────────────────────────────────────────

  function fmtDate(isoStr) {
    if (!isoStr) return "—";
    const d = new Date(isoStr);
    if (isNaN(d)) return "—";
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function riskBadge(level) {
    const map = {
      baixo:  ["risco-baixo",  "Risco baixo"],
      medio:  ["risco-medio",  "Risco médio"],
      alto:   ["risco-alto",   "Risco alto"],
    };
    const [cls, label] = map[level] ?? ["risco-baixo", level];
    return `<span class="auto-risco ${cls}">${label}</span>`;
  }

  function dataBadge(label) {
    return `<span class="auto-dado">${label}</span>`;
  }

  function cmdBlock(cmd) {
    const id = "cmd-" + Math.random().toString(36).slice(2, 8);
    return `
      <div class="auto-cmd-block">
        <code id="${id}" class="auto-cmd-code">${escapeHtml(cmd)}</code>
        <button class="auto-cmd-copy" data-target="${id}" title="Copiar comando">Copiar</button>
      </div>`;
  }

  function autoSection({ id, icon, title, subtitle, risk, dados, objetivo, steps, aviso }) {
    const dadosHtml = dados.map(dataBadge).join(" ");
    const avisoHtml = aviso
      ? `<div class="auto-aviso"><span>⚠️</span><span>${aviso}</span></div>`
      : "";
    const stepsHtml = steps.map(({ label, cmd, hint }) => `
      <div class="auto-step">
        <div class="auto-step-label">${label}</div>
        ${hint ? `<div class="auto-step-hint">${hint}</div>` : ""}
        ${cmdBlock(cmd)}
      </div>`).join("");

    return `
      <section class="auto-section" id="${id}">
        <div class="auto-section-header">
          <span class="auto-section-icon">${icon}</span>
          <div class="auto-section-meta">
            <h3>${title}</h3>
            <p class="auto-section-sub">${subtitle}</p>
          </div>
          <div class="auto-section-badges">
            ${riskBadge(risk)}
            ${dadosHtml}
          </div>
        </div>
        <p class="auto-objetivo">${objetivo}</p>
        ${avisoHtml}
        <div class="auto-steps">${stepsHtml}</div>
      </section>`;
  }

  // ── Status atual ───────────────────────────────────────────────────────────

  const rs  = state.runtimeStatus;
  const ds  = state.dashboardSummary;
  const crm = state.crm;
  const fp  = state.followupSummary;

  const sheetsOk   = rs?.googleSheets?.configured === true;
  const sheetsName = rs?.googleSheets?.name ?? "—";
  const sheetsAt   = fmtDate(rs?.googleSheets?.lastCheckedAt);

  const crmLocal   = state.crm.length;
  const crmAt      = fmtDate(ds?.source?.generatedAt);
  const dashAt     = fmtDate(ds?.source?.generatedAt);

  const fpEspi  = fp?.espirometria?.total ?? "—";
  const fpCons  = fp?.consultas?.total    ?? "—";
  const fpAt    = fmtDate(fp?.geradoEm);

  function statusRow(icon, label, value, sub) {
    return `
      <div class="auto-status-row">
        <span class="auto-status-icon">${icon}</span>
        <div class="auto-status-body">
          <strong>${label}</strong>
          <span>${value}</span>
          ${sub ? `<small>${sub}</small>` : ""}
        </div>
      </div>`;
  }

  const statusBlock = `
    <div class="auto-status-grid">
      ${statusRow(
        sheetsOk ? "✅" : "❌",
        "Google Sheets",
        sheetsOk ? sheetsName : "Não configurado",
        sheetsOk ? `Verificado ${sheetsAt}` : "Execute gcloud auth application-default login"
      )}
      ${statusRow(
        crmLocal > 0 ? "✅" : "ℹ️",
        "CRM Clínicas local",
        crmLocal > 0 ? `${crmLocal} clínicas` : "Arquivo local ausente",
        crmLocal > 0 ? `Atualizado ${crmAt}` : "Execute update-local-data.sh"
      )}
      ${statusRow(
        ds ? "✅" : "ℹ️",
        "Resumo Dashboard",
        ds ? "Carregado" : "Arquivo local ausente",
        ds ? `Atualizado ${dashAt}` : "Execute update-local-data.sh"
      )}
      ${statusRow(
        fp ? "✅" : "ℹ️",
        "Follow-up de pacientes",
        fp ? `${fpEspi} espirometrias · ${fpCons} consultas` : "Resumo não encontrado",
        fp ? `Gerado ${fpAt}` : "Execute generate-followup-pacientes.py --write"
      )}
    </div>`;

  // ── Definição das automações ───────────────────────────────────────────────

  const VENV = "~/.local/share/soprolife/venvs/google-sheets/bin/python";
  const SCRIPTS = "painel-soprolife/scripts";

  const automacoes = [
    {
      id: "auto-sync",
      icon: "🔄",
      title: "Sincronização do painel",
      subtitle: "Atualiza todos os dados locais a partir do Google Sheets",
      risk: "baixo",
      dados: ["Agrega dados", "CRM clínicas", "Sem dados pessoais"],
      objetivo: "Executa os conectores de leitura (resumo + CRM clínicas), gera os arquivos .local.json e valida segurança. Nunca escreve na planilha.",
      steps: [
        {
          label: "Executar sincronização completa",
          hint: "Atualiza resumo-dashboard, CRM clínicas e valida segurança.",
          cmd: `bash ${SCRIPTS}/update-local-data.sh`,
        },
        {
          label: "Verificar segurança após sincronização",
          hint: "Confirma que nenhum dado sensível foi exportado.",
          cmd: `bash ${SCRIPTS}/check-access.sh`,
        },
      ],
    },
    {
      id: "auto-pcmso",
      icon: "🏢",
      title: "Promoção PCMSO → CRM Clínicas",
      subtitle: "Promove registros qualificados da Base PCMSO para o CRM",
      risk: "medio",
      dados: ["CRM clínicas", "Base PCMSO", "Sem dados pessoais"],
      objetivo: "Lê a aba 'Base Prospecção B2B PCMSO' e atualiza/cria registros na aba 'CRM Clinicas'. Nunca copia telefone, Instagram, pessoa de contato, observações, CPF ou e-mail.",
      steps: [
        {
          label: "1. Pré-visualização (dry-run) — obrigatório antes do write",
          hint: "Mostra o que seria criado ou atualizado. Não altera a planilha.",
          cmd: `${VENV} ${SCRIPTS}/promote-pcmso-to-crm.py --dry-run`,
        },
        {
          label: "2. Executar promoção (somente após revisar o dry-run)",
          hint: "Escreve na planilha. Risco médio — valide o dry-run antes.",
          cmd: `${VENV} ${SCRIPTS}/promote-pcmso-to-crm.py --write`,
        },
        {
          label: "3. Deduplicação (opcional, se houver duplicatas)",
          hint: "Preview seguro — não altera nada.",
          cmd: `${VENV} ${SCRIPTS}/promote-pcmso-to-crm.py --dedup`,
        },
        {
          label: "4. Sincronizar painel após promoção",
          cmd: `bash ${SCRIPTS}/update-local-data.sh`,
        },
      ],
    },
    {
      id: "auto-followup",
      icon: "💬",
      title: "Follow-up de pacientes",
      subtitle: "Gera lista privada local para follow-up assistido via WhatsApp",
      risk: "baixo",
      dados: ["Pacientes (privado)", "Espirometrias", "Consultas"],
      objetivo: "Lê CRM Espirometria e CRM Consultas. Gera arquivo privado local com nome, telefone e URL de WhatsApp pré-preenchida. Não envia mensagens — o envio é sempre assistido e revisado pelo operador.",
      aviso: "O arquivo gerado (followup-pacientes.local.json) é privado e gitignored. Nunca é enviado ao GitHub. Nenhuma mensagem é disparada automaticamente.",
      steps: [
        {
          label: "1. Inspecionar estrutura das abas (seguro)",
          hint: "Mostra apenas cabeçalhos e contagens. Não exibe nomes nem telefones.",
          cmd: `${VENV} ${SCRIPTS}/inspect-crm-pacientes.py`,
        },
        {
          label: "2. Pré-visualização (dry-run)",
          hint: "Mostra apenas contagens por status. Não exibe dados de pacientes.",
          cmd: `${VENV} ${SCRIPTS}/generate-followup-pacientes.py --dry-run`,
        },
        {
          label: "3. Gerar arquivo privado local",
          hint: "Cria data-private/followup-pacientes.local.json (chmod 600, gitignored).",
          cmd: `${VENV} ${SCRIPTS}/generate-followup-pacientes.py --write`,
        },
        {
          label: "4. Verificar segurança do arquivo gerado",
          cmd: `bash ${SCRIPTS}/check-access.sh`,
        },
      ],
    },
    {
      id: "auto-seguranca",
      icon: "🔒",
      title: "Verificação de segurança",
      subtitle: "Valida que nenhum dado sensível está exposto no painel",
      risk: "baixo",
      dados: ["Somente metadados", "Sem dados pessoais"],
      objetivo: "Verifica portas abertas, arquivos privados gitignored, ausência de tokens/segredos nos arquivos locais, validade dos JSON exportados e conformidade dos dados de pacientes.",
      steps: [
        {
          label: "Executar verificação completa",
          hint: "Deve terminar com todos os checks OK.",
          cmd: `bash ${SCRIPTS}/check-access.sh`,
        },
      ],
    },
    {
      id: "auto-resumo",
      icon: "📊",
      title: "Resumo Dashboard",
      subtitle: "Atualiza os indicadores da visão geral a partir do Google Sheets",
      risk: "baixo",
      dados: ["Agrega indicadores", "Sem dados pessoais"],
      objetivo: "Lê a aba 'Resumo Dashboard' do Google Sheets e gera o arquivo local de indicadores. Parte do update-local-data.sh mas pode ser executado isoladamente para diagnóstico.",
      steps: [
        {
          label: "Atualizar somente o resumo (incluído no sync completo)",
          hint: "Use update-local-data.sh para atualizar tudo de uma vez.",
          cmd: `bash ${SCRIPTS}/update-local-data.sh`,
        },
      ],
    },
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  container.innerHTML = `
    <div class="crm-subview-header">
      <button class="crm-back-btn" id="crmBackBtn">← CRM</button>
      <div>
        <p class="eyebrow">CRM SoproLife</p>
        <h2>Automações CRM</h2>
        <p class="section-sub">Central de status, orientação e comandos seguros. Nenhuma ação é executada automaticamente por este painel.</p>
      </div>
    </div>

    <section class="auto-status-card">
      <h3 class="auto-status-title">Status atual dos dados locais</h3>
      ${statusBlock}
    </section>

    <div class="auto-nav">
      ${automacoes.map(a => `<a class="auto-nav-link" href="#${a.id}">${a.icon} ${a.title}</a>`).join("")}
    </div>

    <div class="auto-list">
      ${automacoes.map(autoSection).join("")}
    </div>

    <div class="auto-footer-note">
      <span>ℹ️</span>
      <p>Todos os comandos devem ser executados no terminal, dentro da pasta <code>~/soprolife-site</code>. Nenhum script é executado por este painel. O envio de mensagens WhatsApp é sempre revisado e disparado manualmente pelo operador.</p>
    </div>
  `;

  // Copy buttons
  container.querySelectorAll(".auto-cmd-copy").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      navigator.clipboard.writeText(target.textContent).then(() => {
        btn.textContent = "Copiado!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = "Copiar"; btn.classList.remove("copied"); }, 1800);
      }).catch(() => {
        btn.textContent = "Erro";
        setTimeout(() => { btn.textContent = "Copiar"; }, 1800);
      });
    });
  });

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });
}

function renderCrmPlaceholder(container, area) {
  const configs = {
    relatorios: {
      icon: "📊",
      title: "Relatórios CRM",
      subtitle: "Indicadores consolidados de relacionamento",
      description: "Gráficos e indicadores de desempenho comercial e de relacionamento. Conectará com Google Sheets, CRM e histórico de atendimentos."
    },
    "automacoes-crm": {
      icon: "⚡",
      title: "Automações CRM",
      subtitle: "Lembretes, reativação e tarefas automáticas",
      description: "Configuração de lembretes automáticos, reativação de pacientes inativos e tarefas recorrentes de relacionamento."
    }
  };

  const config = configs[area] ?? { icon: "📁", title: area, subtitle: "", description: "Em desenvolvimento." };

  container.innerHTML = `
    <div class="crm-subview-header">
      <button class="crm-back-btn" id="crmBackBtn">← CRM</button>
      <div>
        <p class="eyebrow">CRM SoproLife</p>
        <h2>${config.title}</h2>
        <p class="section-sub">${config.subtitle}</p>
      </div>
    </div>
    <div class="crm-placeholder">
      <div class="crm-placeholder-icon">${config.icon}</div>
      <h3>${config.title}</h3>
      <p>${config.description}</p>
      <span class="badge">Em breve</span>
    </div>
  `;

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });
}


function countLeadStatus(status) {
  return state.leads.filter((item) => item.status === status).length;
}

function countLeadService(term) {
  return state.leads.filter((item) => item.servico.toLowerCase().includes(term.toLowerCase())).length;
}

function renderLeadStats() {
  const stats = [
    { label: "Total de leads", value: state.leads.length },
    { label: "Espirometria", value: countLeadService("Espirometria") },
    { label: "Teleconsulta", value: countLeadService("Teleconsulta") },
    { label: "Agendados", value: countLeadStatus("Agendado") },
    { label: "Pendências", value: countLeadStatus("Aguardando pedido médico") }
  ];

  const container = document.querySelector("#leadStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => `
    <article class="lead-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
    </article>
  `).join("");
}

function renderLeadPipeline() {
  const stages = [
    { label: "Novo", value: countLeadStatus("Novo"), hint: "precisa resposta", tone: "warning" },
    { label: "Aguardando pedido", value: countLeadStatus("Aguardando pedido médico"), hint: "oferecer teleconsulta", tone: "danger" },
    { label: "Agendado", value: countLeadStatus("Agendado"), hint: "confirmar preparo", tone: "success" },
    { label: "Negociação", value: countLeadStatus("Em negociação"), hint: "parceria ou condição", tone: "" },
    { label: "Realizado", value: countLeadStatus("Realizado"), hint: "laudo/retorno", tone: "success" }
  ];

  const container = document.querySelector("#leadPipeline");
  if (!container) return;

  container.innerHTML = stages.map((stage) => `
    <div class="lead-stage ${stage.tone}">
      <small>${stage.label}</small>
      <strong>${stage.value}</strong>
      <span>${stage.hint}</span>
    </div>
  `).join("");
}


function renderMarketingStats() {
  const totalImpressions = state.marketing.seo.reduce((sum, item) => sum + item.impressoes, 0);
  const totalClicks = state.marketing.seo.reduce((sum, item) => sum + item.cliques, 0);
  const avgPosition = state.marketing.seo.reduce((sum, item) => sum + item.posicao, 0) / state.marketing.seo.length;
  const topTerm = state.marketing.seo.reduce((best, item) => item.cliques > best.cliques ? item : best, state.marketing.seo[0]);

  const stats = [
    { label: "Impressões Google", value: totalImpressions },
    { label: "Cliques estimados", value: totalClicks },
    { label: "Posição média", value: avgPosition.toFixed(1) },
    { label: "Melhor termo", value: topTerm.termo }
  ];

  const container = document.querySelector("#marketingStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => `
    <article class="marketing-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
    </article>
  `).join("");
}

function renderSeoFocus() {
  const cards = [
    {
      label: "Alta intenção",
      title: "espirometria RJ",
      text: "Termo principal para captar pacientes que já procuram o exame."
    },
    {
      label: "Busca local",
      title: "espirometria Barra da Tijuca",
      text: "Prioridade para fortalecer presença na região de atendimento."
    },
    {
      label: "Termo médico",
      title: "prova de função pulmonar",
      text: "Importante para pacientes que pesquisam pelo nome técnico."
    },
    {
      label: "Procedimento",
      title: "espirometria com broncodilatador",
      text: "Busca específica de quem já recebeu orientação médica."
    }
  ];

  const container = document.querySelector("#seoFocus");
  if (!container) return;

  container.innerHTML = cards.map((card) => `
    <article class="seo-focus-card">
      <small>${card.label}</small>
      <strong>${card.title}</strong>
      <span>${card.text}</span>
    </article>
  `).join("");
}

function renderCrmTable(filter = "Todos") {
  const tbody = document.querySelector("#crmTable");
  const rows = filter === "Todos"
    ? state.crm
    : state.crm.filter((item) => item.etapa === filter);

  const today = todayIso();

  if (rows.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" class="crm-empty">Nenhuma clínica cadastrada nesta etapa.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = rows.map((item) => {
    const iso = parseDateIso(item.dataProximaAcao);
    const dataCls = iso && iso < today ? "data-atrasada" : iso === today ? "data-hoje" : "";
    const dataLabel = formatDateBr(item.dataProximaAcao);
    return `
    <tr>
      <td><strong>${item.clinica}</strong></td>
      <td>${item.bairro}</td>
      <td><span class="crm-tipo">${item.tipo}</span></td>
      <td><span class="badge ${slug(item.etapa)}">${item.etapa}</span></td>
      <td><span class="badge prio-${slug(item.prioridade)}">${item.prioridade}</span></td>
      <td>${item.proximaAcao}</td>
      <td>${dataLabel ? `<span class="crm-data ${dataCls}">${dataLabel}</span>` : ""}</td>
      <td>${item.responsavel}</td>
    </tr>`;
  }).join("");
}

function renderLeadsTable() {
  const tbody = document.querySelector("#leadsTable");
  tbody.innerHTML = state.leads.map((item) => `
    <tr>
      <td><strong>${item.lead}</strong></td>
      <td>${item.servico}</td>
      <td>${item.origem}</td>
      <td><span class="badge ${slug(item.status)}">${item.status}</span></td>
      <td>${item.data}</td>
      <td>${item.proximaAcao}</td>
    </tr>
  `).join("");
}

function renderSeoList() {
  const list = document.querySelector("#seoList");
  list.innerHTML = state.marketing.seo.map((item) => `
    <div class="seo-item">
      <strong>${item.termo}</strong>
      <div class="seo-meta">
        <span>${item.impressoes} impressões</span>
        <span>${item.cliques} cliques</span>
        <span>posição ${item.posicao}</span>
      </div>
    </div>
  `).join("");
}

function renderCharts() {
  const defaultOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: {
          boxWidth: 12,
          font: { family: "system-ui" }
        }
      }
    },
    scales: {
      y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.14)" } },
      x: { grid: { display: false } }
    }
  };

  createChart("weekly", "#weeklyChart", {
    type: "line",
    data: {
      labels: state.resumo.evolucaoSemanal.labels,
      datasets: [
        {
          label: "Leads",
          data: state.resumo.evolucaoSemanal.leads,
          borderWidth: 3,
          tension: .38,
          fill: true
        },
        {
          label: "Agendamentos",
          data: state.resumo.evolucaoSemanal.agendamentos,
          borderWidth: 3,
          tension: .38
        }
      ]
    },
    options: defaultOptions
  });

  createChart("funnel", "#funnelChart", {
    type: "bar",
    data: {
      labels: state.resumo.funilClinicas.labels,
      datasets: [
        {
          label: "Clínicas",
          data: state.resumo.funilClinicas.values,
          borderWidth: 1,
          borderRadius: 12
        }
      ]
    },
    options: defaultOptions
  });

  createChart("channels", "#channelsChart", {
    type: "doughnut",
    data: {
      labels: state.marketing.canais.labels,
      datasets: [
        {
          label: "Origem dos contatos",
          data: state.marketing.canais.values,
          borderWidth: 2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom" }
      }
    }
  });
}

function bindEvents() {
  document.querySelectorAll(".nav-item[data-section]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".section").forEach((section) => section.classList.remove("active"));

      button.classList.add("active");
      document.querySelector(`#${button.dataset.section}`).classList.add("active");
      resizeCharts();

      if (button.dataset.section === "crm") {
        state.crmView = "hub";
        renderCrmView();
      }
    });
  });

  document.querySelector("#refreshBtn").addEventListener("click", () => {
    window.location.reload();
  });

  document.querySelector("#globalSearch").addEventListener("input", (event) => {
    const term = event.target.value.toLowerCase().trim();

    document.querySelectorAll("tbody tr").forEach((row) => {
      const visible = row.textContent.toLowerCase().includes(term);
      row.style.display = visible || !term ? "" : "none";
    });
  });
}

init();


function renderTaskGroup(selector, tasks) {
  const container = document.querySelector(selector);
  if (!container) return;

  container.innerHTML = tasks.map((task) => {
    const priorityClass = task.prioridade === "Alta"
      ? "danger"
      : task.prioridade === "Média"
        ? "medium"
        : "";

    return `
      <article class="task-board-card">
        <span class="task-dot ${priorityClass}"></span>
        <div class="task-body">
          <strong>${task.titulo}</strong>
          <p><b>${task.area}</b> · ${task.status} · prioridade ${task.prioridade.toLowerCase()}</p>
        </div>
      </article>
    `;
  }).join("");
}

function renderTaskBoard() {
  if (!state.tarefas) return;

  renderTaskGroup("#todayTasks", state.tarefas.hoje);
  renderTaskGroup("#weekTasks", state.tarefas.semana);
  renderTaskGroup("#criticalTasks", state.tarefas.criticas);
}


function renderDocuments() {
  const statsContainer = document.querySelector("#documentStats");
  const grid = document.querySelector("#documentGrid");
  if (!statsContainer || !grid) return;

  const ativos = state.documentos.filter((doc) =>
    ["Ativo", "Ativa", "Regular", "Cadastrado", "Concedido", "Arquivado"].includes(doc.status)
  ).length;

  const comValidade = state.documentos.filter((doc) =>
    doc.validade && !doc.validade.toLowerCase().includes("sem validade")
  ).length;

  const stats = [
    { label: "Documentos mapeados", value: state.documentos.length },
    { label: "Status positivo", value: ativos },
    { label: "Com validade/monitoramento", value: comValidade },
    { label: "Dados pessoais", value: "Não usar" }
  ];

  statsContainer.innerHTML = stats.map((item) => `
    <article class="document-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
    </article>
  `).join("");

  grid.innerHTML = state.documentos.map((doc) => {
    const warning = doc.validade.includes("30/04") || doc.validade.includes("29/05") ? "warning" : "";

    return `
      <article class="document-card ${warning}">
        <h3>${doc.nome}</h3>
        <div class="document-category">${doc.categoria}</div>

        <div class="document-meta">
          <div>
            <span>Status</span>
            <strong>${doc.status}</strong>
          </div>
          <div>
            <span>Validade</span>
            <strong>${doc.validade}</strong>
          </div>
          <div>
            <span>Referência</span>
            <strong>${doc.referencia}</strong>
          </div>
        </div>

        <p class="document-action">${doc.acao}</p>
      </article>
    `;
  }).join("");
}


function renderFinance() {
  const statsContainer = document.querySelector("#financeStats");
  const table = document.querySelector("#financeTable");
  const serviceCanvas = document.querySelector("#serviceRevenueChart");
  const originCanvas = document.querySelector("#originRevenueChart");

  if (!statsContainer || !table || !serviceCanvas || !originCanvas || !state.financeiro) return;

  statsContainer.innerHTML = state.financeiro.resumo.map((item) => `
    <article class="finance-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
      <small>${item.hint}</small>
    </article>
  `).join("");

  table.innerHTML = state.financeiro.lancamentos.map((item) => `
    <tr>
      <td><strong>${item.descricao}</strong></td>
      <td>${item.servico}</td>
      <td>${item.origem}</td>
      <td>${item.valor}</td>
      <td><span class="badge ${slug(item.status)}">${item.status}</span></td>
      <td>${item.data}</td>
    </tr>
  `).join("");

  createChart("serviceRevenue", "#serviceRevenueChart", {
    type: "bar",
    data: {
      labels: state.financeiro.porServico.map((item) => item.servico),
      datasets: [
        {
          label: "Receita por serviço",
          data: state.financeiro.porServico.map((item) => item.valor),
          borderWidth: 1,
          borderRadius: 12
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.14)" } },
        x: { grid: { display: false } }
      }
    }
  });

  createChart("originRevenue", "#originRevenueChart", {
    type: "doughnut",
    data: {
      labels: state.financeiro.porOrigem.map((item) => item.origem),
      datasets: [
        {
          label: "Receita por origem",
          data: state.financeiro.porOrigem.map((item) => item.valor),
          borderWidth: 2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom" }
      }
    }
  });
}


function automationPriorityClass(priority) {
  if (priority === "Alta") return "danger";
  if (priority === "Média") return "medium";
  return "";
}

function automationStatusClass(status) {
  return slug(status);
}

function renderAutomations() {
  if (!state.automacoes) return;
  renderRuntimeStatus();

  const statsContainer = document.querySelector("#automationStats");
  const sourcesContainer = document.querySelector("#automationSources");
  const phasesContainer = document.querySelector("#automationPhases");

  if (!statsContainer || !sourcesContainer || !phasesContainer) return;

  statsContainer.innerHTML = state.automacoes.resumo.map((item) => `
    <article class="automation-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
      <small>${item.hint}</small>
    </article>
  `).join("");

  sourcesContainer.innerHTML = state.automacoes.fontes.map((item) => `
    <article class="automation-source-card">
      <div class="automation-source-top">
        <strong>${item.nome}</strong>
        <span class="badge ${automationStatusClass(item.status)}">${item.status}</span>
      </div>
      <p>${item.area}</p>
      <div class="automation-source-footer">
        <span class="priority ${automationPriorityClass(item.prioridade)}">Prioridade ${item.prioridade}</span>
        <small>${item.seguranca}</small>
      </div>
    </article>
  `).join("");

  phasesContainer.innerHTML = state.automacoes.fases.map((item) => `
    <article class="automation-phase">
      <span>${item.fase}</span>
      <div>
        <strong>${item.titulo}</strong>
        <p>${item.descricao}</p>
      </div>
    </article>
  `).join("");
}


function renderRuntimeStatus() {
  const container = document.querySelector("#runtimeStatus");
  if (!container) return;

  const googleSheets = state.runtimeStatus?.googleSheets;
  const configured = Boolean(
    googleSheets?.configured &&
    googleSheets?.safeToDisplay &&
    googleSheets?.configValid !== false
  );

  const statusLabel = configured
    ? "Configurado localmente"
    : "Não configurado neste ambiente";

  const badgeClass = configured ? "success" : "neutral";

  const description = configured
    ? "Fonte privada externa detectada. Nenhuma URL, ID, token ou dado real é exibido no painel."
    : "Gere o status local seguro para indicar a configuração privada sem expor segredos.";

  container.innerHTML = `
    <div>
      <span class="runtime-kicker">Fonte privada</span>
      <strong>Google Sheets</strong>
      <p>${description}</p>
    </div>
    <div class="runtime-status-side">
      <span class="runtime-badge ${badgeClass}">${statusLabel}</span>
      <small>Segredos fora do GitHub</small>
    </div>
  `;
}


function renderDataFreshness() {
  const container = document.querySelector("#dataFreshness");
  if (!container) return;

  const source = state.dashboardSummary?.source;
  const runtimeGS = state.runtimeStatus?.googleSheets;

  const isSafe = Boolean(
    source?.safeToDisplay &&
    source?.containsPersonalData === false &&
    source?.containsHealthData === false
  );

  if (!isSafe) {
    container.innerHTML = `
      <div class="freshness-meta">
        <span>Fonte dos dados</span>
        <strong>Local seguro</strong>
        <small>Última atualização: não informada</small>
      </div>
    `;
    return;
  }

  const isGoogleSheets = Boolean(
    runtimeGS?.configured &&
    runtimeGS?.safeToDisplay &&
    runtimeGS?.configValid !== false
  );

  const sourceLabel = isGoogleSheets ? "Google Sheets via ADC" : "Local seguro";
  const updatedAt = formatDateTime(source.generatedAt);

  const securityBadge = (!source.containsPersonalData && !source.containsHealthData)
    ? `<span class="freshness-badge">Dados agregados · sem dados pessoais ou clínicos</span>`
    : "";

  container.innerHTML = `
    <div class="freshness-meta">
      <span>Fonte dos dados</span>
      <strong>${sourceLabel}</strong>
      <small>Atualizado em: ${updatedAt}</small>
    </div>
    ${securityBadge}
  `;
}

function formatDateTime(value) {
  if (!value) return "data não informada";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "data inválida";
  }

  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(date);
}
