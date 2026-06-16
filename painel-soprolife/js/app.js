const state = {
  resumo: null,
  crm: [],
  leads: [],
  marketing: null,
  charts: {}
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

async function init() {
  try {
    const [resumo, crm, leads, marketing] = await Promise.all([
      loadJson("./data/resumo.json"),
      loadJson("./data/crm-clinicas.json"),
      loadJson("./data/leads.json"),
      loadJson("./data/marketing.json")
    ]);

    state.resumo = resumo;
    state.crm = crm;
    state.leads = leads;
    state.marketing = marketing;

    renderCards();
    renderTasks();
    renderCrmTable();
    renderLeadsTable();
    renderSeoList();
    renderCharts();
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

  state.charts.weekly = new Chart(document.querySelector("#weeklyChart"), {
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

  state.charts.funnel = new Chart(document.querySelector("#funnelChart"), {
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

  state.charts.channels = new Chart(document.querySelector("#channelsChart"), {
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
