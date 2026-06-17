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
  runtimeStatus: null
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

    renderCards();
    renderTasks();
    renderCrmStats();
    renderCrmFunnelVisual();
    renderCrmTable();
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

function renderCards() {
  const grid = document.querySelector("#cardsGrid");
  grid.innerHTML = state.resumo.cards.map((card) => `
    <article class="metric-card">
      <div>
        <span>${card.label}</span>
        <strong>${card.value}</strong>
      </div>
      <div class="variation ${card.type === "neutral" ? "neutral" : ""}">${card.variation}</div>
    </article>
  `).join("");
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


function countCrmStatus(status) {
  return state.crm.filter((item) => item.status === status).length;
}

function renderCrmStats() {
  const stats = [
    { label: "Total no CRM", value: state.crm.length },
    { label: "Responderam", value: countCrmStatus("Respondeu") },
    { label: "Reuniões", value: countCrmStatus("Reunião") },
    { label: "Propostas", value: countCrmStatus("Proposta") },
    { label: "Sem resposta", value: countCrmStatus("Não respondeu") }
  ];

  const container = document.querySelector("#crmStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => `
    <article class="crm-stat-card">
      <span>${item.label}</span>
      <strong>${item.value}</strong>
    </article>
  `).join("");
}

function renderCrmFunnelVisual() {
  const steps = [
    { label: "Abordadas", value: state.crm.length, hint: "base trabalhada" },
    { label: "Responderam", value: countCrmStatus("Respondeu"), hint: "abriram conversa" },
    { label: "Reunião", value: countCrmStatus("Reunião"), hint: "próximo contato" },
    { label: "Proposta", value: countCrmStatus("Proposta"), hint: "em negociação" },
    { label: "Piloto", value: 1, hint: "meta inicial" }
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
    : state.crm.filter((item) => item.status === filter);

  tbody.innerHTML = rows.map((item) => `
    <tr>
      <td><strong>${item.clinica}</strong><br><span>${item.contato}</span></td>
      <td>${item.bairro}</td>
      <td>${item.canal}</td>
      <td><span class="badge ${slug(item.status)}">${item.status}</span></td>
      <td>${item.ultimaAcao}</td>
      <td>${item.proximaAcao}</td>
    </tr>
  `).join("");
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
    });
  });

  document.querySelector("#crmFilter").addEventListener("change", (event) => {
    renderCrmTable(event.target.value);
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
