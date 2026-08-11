const state = {
  resumo: null,
  crm: [],
  leads: [],
  leadsSummary: null,
  leadsPrivate: null,
  marketing: null,
  marketingSeo: null,
  charts: {},
  tarefas: null,
  documentos: [],
  documentosEquipamentos: null,
  financeiro_summary: null,
  automacoes: null,
  runtimeStatus: null,
  dashboardSummary: null,
  crmView: "hub",
  // M21 — contadores do CRM canônico mostrados em Automação CRM (só leitura).
  crmKpisAutomacao: null,
  // M21 — pedido de atualização de Marketing em curso (estado "Atualizando").
  mktRefreshPendente: false,
  mktRefreshPolling: false,
  mktRefreshResultado: null,
  mktRefreshRequestId: null,
  followupSummary: null,
  followupClinicas: null,
  ultimosLancamentos: null,
  custosInvestimentos: null,
  leadsFilter: "Ativos",
  crmFilter: "Ativos",
  crmContatosB2B: [],
  followupClinicasSummary: null,
  crmReportFilters: null,
  parceriaPastore: null,
  auditoria: null,
  saudeOperacional: null,
  cerebroDemo: null,
  briefingDemo: null,
};

const slug = (text) =>
  String(text)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, "-");

// Etapas oficiais do funil de leads (M23). Estes são os valores CANÔNICOS
// gravados no PostgreSQL — exatamente o enum EtapaLead da API
// (nucleo-m15/app/schemas.py). O vocabulário anterior, em português livre,
// vinha da planilha legada e deixou de existir como fonte no M23.
//
// A ordem define a ordem do funil visual; o rótulo é só apresentação e nunca
// é gravado.
const LEAD_ETAPA_OPTIONS = [
  "novo",
  "em_contato",
  "aguardando_retomada",
  "agendado",
  "convertido",
  "nao_respondeu",
  "perdido",
];

const LEAD_ETAPA_LABELS = {
  novo: "Novo contato",
  em_contato: "Em contato",
  aguardando_retomada: "Aguardando retomada",
  agendado: "Agendado",
  convertido: "Convertido",
  nao_respondeu: "Não respondeu",
  perdido: "Perdido",
};

function leadEtapaLabel(etapa) {
  return LEAD_ETAPA_LABELS[etapa] || etapa || "—";
}

// Etapa que encerra o funil: o lead vira atendimento e sai da lista ativa.
// No M22 e antes, a conversão era disparada pelo Apps Script; agora é apenas
// o estado canônico gravado no banco.
const LEAD_ETAPAS_CONVERSAO = ["convertido"];

// Serviços que caracterizam um lead B2B (clínica/parceiro/PCMSO). Mantido
// tolerante ao vocabulário histórico porque o campo servico_interesse aceita
// texto livre vindo de registros antigos.
const LEAD_SERVICOS_B2B = ["clínicas", "clinicas", "pcmso / empresa", "pcmso", "empresa"];

// Etapas terminais que marcam um lead como convertido/arquivado — ocultadas
// por padrão da lista principal de Leads e Agendamentos (filtro "Ativos").
const LEAD_ETAPAS_TERMINAIS = ["convertido", "perdido"];

function isB2BLead(item) {
  const servico = (item.servico_interesse || item.servico || "").toLowerCase().trim();
  return LEAD_SERVICOS_B2B.includes(servico) || item.tipo_lead === "b2b";
}

// Regra de "convertido/arquivado" (oculto do filtro Ativos):
//   status_operacional === "convertido" OU etapa terminal canônica.
function isLeadConvertido(item) {
  if (item.status_operacional === "convertido") return true;
  const etapa = item.etapa || item.status || "";
  return LEAD_ETAPAS_TERMINAIS.includes(etapa);
}

const CHART_COLORS = [
  "rgba(29, 183, 166, .92)",   // teal — cor da marca
  "rgba(99, 102, 241, .92)",   // indigo
  "rgba(245, 158, 11, .92)",   // amber
  "rgba(239, 68, 68, .90)",    // red
  "rgba(16, 185, 129, .90)",   // emerald
  "rgba(37, 99, 235, .90)",    // blue
  "rgba(168, 85, 247, .90)",   // purple
  "rgba(100, 116, 139, .88)",  // slate
];

const CHART_COLORS_FUNNEL = [
  "rgba(100, 116, 139, .65)",
  "rgba(99, 102, 241, .80)",
  "rgba(245, 158, 11, .88)",
  "rgba(37, 99, 235, .88)",
  "rgba(29, 183, 166, .92)",
];

function fmtBRL(value) {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value) || 0);
}

// Evita que o navegador sirva uma versão antiga em cache de um JSON de dados
// local (ex.: depois de atualizar um *.local.json na VPS, o painel continuava
// mostrando os valores antigos até um hard-refresh). Usado por loadJson() e
// loadOptionalJson() — helper único para todas as leituras de dados do painel.
// O valor é fixo POR SESSÃO (M14.3A.1): cada carregamento da página busca
// versão nova, mas releituras na mesma sessão podem reusar o cache HTTP.
// Nunca inclui token ou identificador sensível na URL.
const DATA_CACHE_BUST = Date.now().toString(36);
function withCacheBust(path, cacheBust = DATA_CACHE_BUST) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}_cb=${cacheBust}`;
}

async function loadJson(path) {
  const response = await fetch(withCacheBust(path));
  if (!response.ok) {
    throw new Error(`Erro ao carregar ${path}`);
  }
  return response.json();
}

async function loadOptionalJson(path, cacheBust = DATA_CACHE_BUST) {
  try {
    const response = await fetch(withCacheBust(path, cacheBust));

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

function setPremiumChartDefaults() {
  const d = Chart.defaults;
  d.font.family = "Inter, system-ui, -apple-system, sans-serif";
  d.font.size = 11;
  d.color = "#6d7b8a";
  d.animation.duration = 600;
  d.animation.easing = "easeOutQuart";

  const tt = d.plugins.tooltip;
  tt.backgroundColor = "#0b1f36";
  tt.titleColor = "#ffffff";
  tt.bodyColor = "rgba(200,215,230,.85)";
  tt.padding = { x: 14, y: 10 };
  tt.cornerRadius = 12;
  tt.boxPadding = 5;
  tt.borderColor = "rgba(255,255,255,.06)";
  tt.borderWidth = 1;
  tt.usePointStyle = true;

  d.elements.line.borderWidth = 2.5;
  d.elements.line.tension = 0.42;
  d.elements.point.radius = 0;
  d.elements.point.hoverRadius = 5;
  d.elements.point.hoverBorderWidth = 2;
  d.elements.bar.borderRadius = 10;
  d.elements.arc.borderWidth = 2;

  // M16 — interatividade padrão: hover por índice mostra todas as séries do
  // ponto (sem exigir mira no pixel) e cursor indica áreas clicáveis.
  d.interaction.mode = "index";
  d.interaction.intersect = false;
  d.plugins.legend.labels.usePointStyle = true;
  d.plugins.legend.labels.boxWidth = 8;
  d.onHover = (event, elements) => {
    const target = event.native ? event.native.target : null;
    if (target) target.style.cursor = elements.length ? "pointer" : "default";
  };
}

function chartGradient(canvasEl, r, g, b, alphaTop = 0.25, alphaBottom = 0.02) {
  if (!canvasEl) return `rgba(${r},${g},${b},${alphaTop})`;
  const ctx = canvasEl.getContext("2d");
  const h = canvasEl.parentElement?.clientHeight || 280;
  const gradient = ctx.createLinearGradient(0, 0, 0, h);
  gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${alphaTop})`);
  gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${alphaBottom})`);
  return gradient;
}

// M23 — vocabulário CANÔNICO de status de parceiro, idêntico ao enum aceito
// por PATCH /parceiros/{id} (nucleo-m15/app/schemas.py). O painel exibe um
// rótulo legível, mas SEMPRE grava e compara o valor canônico.
const _ETAPA_ALIAS = {
  // Valores canônicos (identidade).
  "prospecto":     "prospecto",
  "em_negociacao": "em_negociacao",
  "ativa":         "ativa",
  "pausada":       "pausada",
  "encerrada":     "encerrada",
  // Vocabulário histórico da planilha legada: continua LEGÍVEL para registros
  // antigos que ainda apareçam, mas nunca é gravado de volta.
  "não abordada":        "prospecto",
  "nao abordada":        "prospecto",
  "abordada":            "em_negociacao",
  "em conversa":         "em_negociacao",
  "pediu apresentação":  "em_negociacao",
  "proposta enviada":    "em_negociacao",
  "aguardando retorno":  "pausada",
  "parceiro ativo":      "ativa",
  "parceira":            "ativa",
  "sem interesse":       "encerrada",
  "arquivada":           "encerrada",
};

const CRM_ETAPA_LABELS = {
  prospecto: "Prospecto",
  em_negociacao: "Em negociação",
  ativa: "Parceiro ativo",
  pausada: "Pausada",
  encerrada: "Encerrada",
};

function crmEtapaLabel(etapa) {
  return CRM_ETAPA_LABELS[etapa] || etapa || "—";
}

// Etapa terminal = fase comercial que já "fechou" (positiva ou negativa) e
// por isso sai das listas de prospecção ativa (Atrasados, Alta prioridade,
// Em conversa) — mesma ideia de isLeadConvertido()/LEAD_ETAPAS_TERMINAIS,
// aplicada ao domínio de CRM Clínicas/PCMSO. Pessoa ≠ clínica, Status ≠
// etapa, Próximo passo ≠ etapa: Etapa é a única fonte de verdade do funil.
const CRM_ETAPA_TERMINAL_POSITIVA = "ativa";
const CRM_ETAPAS_TERMINAIS_NEGATIVAS = ["encerrada"];
const CRM_ETAPAS_TERMINAIS = [CRM_ETAPA_TERMINAL_POSITIVA, ...CRM_ETAPAS_TERMINAIS_NEGATIVAS];

// Etapas ativas do funil comercial (excluem as terminais, que têm blocos e
// filtros próprios) — usadas no funil visual e no formulário de nova clínica.
const CRM_ETAPAS_ATIVAS = ["prospecto", "em_negociacao", "pausada"];

// M23 — a etapa vem da coluna canônica `status` do PostgreSQL. A heurística
// anterior, que INFERIA etapa negativa a partir de texto livre da planilha
// (Próximo passo / Observação), foi removida: adivinhar fase comercial a
// partir de texto era necessário quando a fonte era uma planilha sem enum,
// e é exatamente o tipo de suposição que o M23 elimina.
function normalizeCrmEtapa(etapa) {
  const bruto = String(etapa ?? "").toLowerCase().trim();
  return _ETAPA_ALIAS[bruto] ?? String(etapa ?? "").trim();
}

function isCrmEtapaTerminal(etapa) {
  return CRM_ETAPAS_TERMINAIS.includes(etapa);
}

function isCrmEtapaTerminalNegativa(etapa) {
  return CRM_ETAPAS_TERMINAIS_NEGATIVAS.includes(etapa);
}

function normalizeCrmRecord(item) {
  const bairro = item.bairro || "";
  const regiao = item.regiao || "";
  return {
    id: item.clinica_id || item.id || "",
    clinica: item.nome_clinica || item.clinica || "",
    bairro: regiao && regiao !== bairro ? `${regiao} · ${bairro}` : bairro,
    tipo: item.tipo_clinica || item.tipo || "",
    etapa: normalizeCrmEtapa(item.etapa || ""),
    // Prioridade e próxima ação eram colunas de texto livre da planilha; o
    // banco canônico não guarda esses campos e o painel não os inventa.
    prioridade: item.prioridade || "",
    proximaAcao: item.proximaAcao || "",
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
    const [resumo, crm, leads, marketing, tarefas, documentos, automacoes] = await Promise.all([
      loadJson("./data/resumo.json"),
      loadJson("./data/crm-clinicas.json"),
      loadJson("./data/leads.json"),
      loadJson("./data/marketing.json"),
      loadJson("./data/tarefas.json"),
      loadJson("./data/documentos.json"),
      loadJson("./data/automacoes.json")
    ]);

    state.resumo = resumo;
    state.crm = crm;
    // M5: lista CRUA de clínicas (etapas no vocabulário oficial da planilha)
    // — o normalizado de state.crm traduz etapas para rótulos visuais.
    state.crmClinicasRaw = Array.isArray(crm) ? crm : (crm?.clinicas || []);
    state.leads = leads;
    state.marketing = marketing;
    state.tarefas = tarefas;
    state.documentos = documentos;
    state.automacoes = automacoes;

    state.runtimeStatus = await loadOptionalJson("./data/runtime-status.local.json");
    state.dashboardSummary = await loadOptionalJson("./data/resumo-dashboard.local.json");

    const crmLocal = await loadOptionalJson("./data/crm-clinicas.local.json");
    if (crmLocal && Array.isArray(crmLocal.clinicas)) {
      state.crm = crmLocal.clinicas.map(normalizeCrmRecord);
      state.crmClinicasRaw = crmLocal.clinicas;
    }

    // Contatos B2B: resumo seguro (sem nome/telefone/email) > privado local (Tailscale) > nada.
    // Gerado por: python3 painel-soprolife/scripts/read-crm-contatos-b2b-adc.py --write
    const contatosSummary = await loadOptionalJson("./data/crm-contatos-b2b-summary.local.json");
    const contatosPrivate = await loadOptionalJson("./data-private/crm-contatos-b2b.local.json");
    if (contatosPrivate?.contatos?.length > 0) {
      state.crmContatosB2B = contatosPrivate.contatos;
    } else if (
      contatosSummary?.source?.safeToDisplay === true &&
      contatosSummary?.source?.containsPersonalData === false &&
      Array.isArray(contatosSummary?.contatos)
    ) {
      state.crmContatosB2B = contatosSummary.contatos;
    }

    const followupSummary = await loadOptionalJson("./data/followup-pacientes-summary.local.json");
    if (followupSummary) {
      state.followupSummary = followupSummary;
    }

    const followupClinicasLocal = await loadOptionalJson("./data-private/followup-clinicas.local.json");
    if (followupClinicasLocal) {
      state.followupClinicas = followupClinicasLocal;
    }

    // Resumo seguro (sem nome/telefone) usado apenas para indicadores agregados
    // em Relatórios CRM — nunca usar state.followupClinicas (privado) nessa página.
    const followupClinicasSummary = await loadOptionalJson("./data/followup-clinicas-summary.local.json");
    if (followupClinicasSummary?.safeToDisplay === true && followupClinicasSummary?.containsPersonalData === false) {
      state.followupClinicasSummary = followupClinicasSummary;
    }

    // Leads: privado (local/Tailscale) > resumo seguro > leads.json demonstrativo
    // Ambos gerados por: python3 painel-soprolife/scripts/read-leads-sheets.py --write
    const leadsSummaryLocal = await loadOptionalJson("./data/leads-summary.local.json");
    const leadsPrivateLocal = await loadOptionalJson("./data-private/leads.local.json");

    if (leadsPrivateLocal?.leads?.length > 0) {
      // Dados reais disponíveis localmente (gitignored) — usa com nome e telefone
      state.leads = leadsPrivateLocal.leads;
      state.leadsPrivate = leadsPrivateLocal.leads;
      if (leadsSummaryLocal?.source?.safeToDisplay === true) {
        state.leadsSummary = leadsSummaryLocal;
      }
    } else if (
      leadsSummaryLocal?.source?.safeToDisplay === true &&
      leadsSummaryLocal?.source?.containsPersonalData === false &&
      Array.isArray(leadsSummaryLocal?.leads) &&
      leadsSummaryLocal.leads.length > 0
    ) {
      // Resumo seguro disponível (sem nome/telefone, mas com etapa/serviço/etc.)
      state.leads = leadsSummaryLocal.leads;
      state.leadsSummary = leadsSummaryLocal;
    }
    // else: mantém leads.json demonstrativo já carregado em state.leads

    state.marketingSeo = await loadOptionalJson("./data/marketing-seo.local.json");

    const financeiroSummaryData = await loadOptionalJson("./data/financeiro-summary.local.json");
    if (
      financeiroSummaryData?.source?.safeToDisplay === true &&
      financeiroSummaryData?.source?.containsPersonalData === false
    ) {
      state.financeiro_summary = financeiroSummaryData;
    }


    const lancamentosData = await loadOptionalJson("./data/ultimos-lancamentos-summary.local.json");
    if (lancamentosData?.source?.safeToDisplay === true && lancamentosData?.source?.containsPersonalData === false) {
      state.ultimosLancamentos = lancamentosData;
    }

    // Auditoria M1: resumo seguro da aba Log Auditoria (só agregados e
    // eventos com ação/tipo/ID/operador/resultado — nunca conteúdo de campo).
    const auditoriaData = await loadOptionalJson("./data/auditoria-summary.local.json");
    if (auditoriaData?.source?.safeToDisplay === true && auditoriaData?.source?.containsPersonalData === false) {
      state.auditoria = auditoriaData;
    }

    // Cérebro Operacional (M8): demo commitável usado como fallback/rótulos.
    const cerebroDemo = await loadOptionalJson("./data/cerebro-operacional.json");
    if (cerebroDemo?.source?.safeToDisplay === true && cerebroDemo?.source?.containsPersonalData === false) {
      state.cerebroDemo = cerebroDemo;
    }

    // Briefing Diário (M9): demo commitável usado só sem fontes vivas.
    const briefingDemo = await loadOptionalJson("./data/briefing-diario.json");
    if (briefingDemo?.source?.safeToDisplay === true && briefingDemo?.source?.containsPersonalData === false) {
      state.briefingDemo = briefingDemo;
    }

    // Saúde Operacional (M3): tenta o resumo REAL gerado na VPS (gitignored);
    // sem ele, cai no demonstrativo commitável; sem nenhum, o card fica oculto.
    const saudeReal = await loadOptionalJson("./data/saude-operacional-summary.local.json");
    if (saudeReal?.source?.safeToDisplay === true && saudeReal?.source?.containsPersonalData === false) {
      state.saudeOperacional = saudeReal;
    } else {
      const saudeDemo = await loadOptionalJson("./data/saude-operacional.json");
      if (saudeDemo?.source?.safeToDisplay === true && saudeDemo?.source?.containsPersonalData === false) {
        state.saudeOperacional = saudeDemo;
      }
    }

    const custosData = await loadOptionalJson("./data/custos-investimentos-summary.local.json");
    if (custosData?.source?.safeToDisplay === true && custosData?.source?.containsPersonalData === false) {
      state.custosInvestimentos = custosData;
    }

    // Parcerias → Pastore: resumo agregado e seguro (agenda, KPIs, financeiro
    // "A definir"/null enquanto os valores comerciais não forem fechados, e
    // agregados de pacientes sem nome/telefone). Dados pessoais reais ficam
    // só em data-private/parcerias-pastore.local.json, fora do Git.
    state.parceriaPastore = await loadParceriaPastoreSummary();

    // Documentos → Equipamentos: só metadados seguros (equipamento, tipo de
    // documento, data, status, validade, observação curta). Os arquivos
    // originais (fotos/PDFs) ficam só em data-private/documentos/equipamentos/,
    // nunca neste arquivo nem no painel.
    const documentosEquipamentosData = await loadOptionalJson("./data/documentos-equipamentos-summary.json");
    if (
      documentosEquipamentosData?.source?.safeToDisplay === true &&
      documentosEquipamentosData?.source?.containsPersonalData === false
    ) {
      state.documentosEquipamentos = documentosEquipamentosData;
    }

    setPremiumChartDefaults();
    renderCards();
    renderDataFreshness();
    renderTasks();
    renderCrmView();
    renderLeadStats();
    renderLeadPipeline();
    renderLeadsTable();
    renderMarketingSection();
    renderCharts();
    renderLeadsCharts();
    renderTaskBoard();
    renderDocuments();
    renderDocumentosEquipamentos();
    renderFinance();
    renderParceriaPastore();
    renderCustosInvestimentos();
    renderAutomations();
    renderSaudeOperacional();
    renderAcoesOperacionais();
    renderB2BAcoes();
    renderCerebroOperacional();
    renderHojeAcoes();
    renderAuditoria();
    renderLancamentos();
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
    // Financeiro, marketing e operação têm poucos cards cada — juntos numa
    // única linha o Painel Geral não fica com três faixas quase vazias.
    title: "Gestão",
    subtitle: "Financeiro, marketing e operação",
    icon: "🗂️",
    keys: ["receitaPrevista", "receitaRecebida", "conteudosPlanejados", "tarefasPendentes", "eventosAgendados"]
  }
];

// ─── Tooltips explicativos dos indicadores — mapa central por chave de métrica ──
// Cada texto explica o que o número significa, de onde vem (quando aplicável) e
// como interpretar. Menções a dado agregado/seguro ficam só aqui, não no card.
const METRIC_INFO = {
  // Painel geral / visão rápida
  totalLeads: "Total de leads cadastrados no PostgreSQL. Dado agregado, sem exibir nome ou telefone neste resumo.",
  leadsNovos: "Leads que ainda estão no início do funil e aguardam qualificação ou primeiro retorno.",
  leadsAgendados: "Leads que avançaram para uma etapa com agendamento ou intenção clara de atendimento.",
  leadsConcluidos: "Leads que já passaram pelo primeiro atendimento (espirometria, consulta ou teleconsulta) e migraram para o CRM de pacientes.",
  clinicasCadastradas: "Número de clínicas/parceiros B2B cadastrados para prospecção, parceria ou acompanhamento.",
  tarefasPendentes: "Tarefas operacionais ainda abertas no painel.",
  receitaPrevista: "Estimativa de receita a partir dos lançamentos e agendamentos já registrados no controle financeiro.",
  receitaRecebida: "Receita já recebida, considerando os lançamentos financeiros confirmados. Quando o financeiro real está ativo, mostra a receita oficial de espirometrias pagas/confirmadas.",
  conteudosPlanejados: "Quantidade de conteúdos previstos no planejamento de marketing, como posts, campanhas ou materiais educativos.",
  eventosAgendados: "Eventos, ações comerciais, atendimentos ou compromissos previstos na agenda operacional.",

  // Fallback demonstrativo (data/resumo.json)
  examesRealizados: "Quantidade de exames de espirometria já realizados no período, segundo o controle demonstrativo do painel.",
  clinicasAbordadas: "Clínicas e parceiros já contatados na prospecção B2B, incluindo etapas iniciais de conversa.",
  taxaResposta: "Percentual de leads e clínicas que retornaram contato após a primeira abordagem.",
  receitaEstimada: "Estimativa de receita do período com base nos dados demonstrativos do painel.",

  // CRM — Clínicas e Parceiros
  crmEmProspeccao: "Clínicas/parceiros que já tiveram algum contato, mas ainda não fecharam parceria nem foram descartados.",
  crmParceirosAtivos: "Clínicas com parceria já fechada e em operação com a SoproLife.",
  crmPrioridadeAlta: "Clínicas ou parceiros marcados como prioridade alta para a próxima ação comercial.",
  crmComAcaoDefinida: "Clínicas ou parceiros que já têm uma próxima ação registrada no pipeline.",

  // CRM — Pacientes / follow-up
  crmPacientesEmAcompanhamento: "Pacientes com acompanhamento ativo de espirometria ou consulta na base de follow-up.",
  crmPacientesEspirometrias: "Pacientes com espirometria em acompanhamento de follow-up.",
  crmPacientesConsultas: "Pacientes com consulta em acompanhamento de follow-up.",
  crmRecorrenciasAtivas: "Pacientes com exames ou consultas periódicas programadas para retorno recorrente.",
  crmFollowupTotal: "Total de registros de pacientes com follow-up ativo, somando espirometrias e consultas.",
  crmFollowupComWhatsapp: "Registros de follow-up com WhatsApp cadastrado, prontos para envio assistido de mensagem.",

  // Leads e Agendamentos
  leadsNovosContatos: "Leads recém-cadastrados que ainda aguardam a primeira resposta da equipe.",
  leadsEmConversa: "Leads em diálogo ativo, incluindo os que aguardam retorno do próprio lead.",
  leadsConvertidos: "Leads que já viraram paciente, exame, consulta ou parceria B2B. Exemplo: Juan/Pastore aparece aqui como parceiro ativo.",
  leadsSemResposta: "Leads que não responderam ao contato inicial e ainda não foram reativados ou encerrados.",

  // Marketing & SEO
  searchConsoleImpressions: "Dados agregados de desempenho orgânico do site no Google Search Console: quantas vezes o site apareceu nos resultados de busca.",
  searchConsoleClicks: "Dados agregados de desempenho orgânico do site no Google Search Console: cliques recebidos a partir da busca do Google.",
  ga4Users: "Dados agregados de tráfego do site no Google Analytics 4: visitantes únicos no período.",
  ga4Sessions: "Dados agregados de tráfego do site no Google Analytics 4: total de visitas (sessões) no período.",

  // Documentos
  documentosMapeados: "Total de documentos institucionais mapeados no controle de compliance da empresa.",
  documentosStatusPositivo: "Documentos com status regular, ativo ou concedido, sem pendência aparente.",
  documentosComValidade: "Documentos que possuem data de validade e por isso precisam de monitoramento periódico.",
  documentosDadosPessoais: "Lembrete de que esta área é só para dados institucionais — nunca CPF, prontuário ou informação pessoal de paciente.",

  // Documentos → Equipamentos
  docsEquipTotal: "Equipamentos com documentação técnica arquivada na pasta privada local — o painel mostra só o resumo por equipamento, nunca os arquivos originais.",
  docsEquipDocumentos: "Total de documentos técnicos identificados (manual, certificado de calibração, garantia etc.), somados entre todos os equipamentos.",
  docsEquipComValidade: "Equipamentos cuja validade ou calibração ainda não pôde ser confirmada com segurança a partir dos documentos arquivados.",
  docsEquipProximaAcao: "Equipamentos com uma próxima ação registrada (ex.: agendar calibração, confirmar validade).",

  // Financeiro
  financeSaldoConta: "Saldo operacional atual em conta, conforme o controle financeiro interno.",
  receitaEspirometrias: "Receita registrada de exames de espirometria pagos/confirmados no controle financeiro.",
  financeTicketMedio: "Valor médio efetivamente recebido por exame, considerando eventuais valores acima do preço base.",
  financeParceriaExcepcional: "Entradas classificadas como parceria ou pagamento espontâneo, fora do preço padrão do serviço.",
  financeConsultas: "Receita de consultas — atualmente em fase estratégica/parceria, sem cobrança direta ao paciente.",
  financeEntradasRecentes: "Lançamentos financeiros mais recentes já destacados no extrato do período.",
  financeResumoPendente: "Nenhum resumo financeiro local foi gerado ainda neste ambiente.",

  // Custos & Investimentos
  custosMensais: "Estimativa de despesas recorrentes mensais já cadastradas, como ferramentas, infraestrutura e parcelamentos.",
  investimentosEquipamentos: "Itens de investimento da SoproLife, como equipamentos, estrutura e implantação.",
  ciParcelasMensais: "Soma das parcelas mensais em aberto referentes aos equipamentos financiados.",
  ciRecorrentesInfra: "Total mensal de assinaturas e infraestrutura recorrente, como workspace, VPN e hospedagem.",
  ciItensCadastrados: "Quantidade de itens de custo/investimento já cadastrados e ativos no controle interno.",
  ciPendenciasCadastro: "Itens que ainda precisam ser formalizados ou regularizados no controle de custos.",
  ciResumoIndisponivel: "Nenhum resumo de custos e investimentos local foi encontrado neste ambiente.",
  ciEquipTotalInvestido: "Valor total já investido em equipamentos da SoproLife.",
  ciEquipParcelaMensal: "Soma das parcelas mensais dos equipamentos financiados.",
  ciEquipPendente: "Saldo restante do parcelamento do Espirômetro Koko — parcelas ainda sem data e sem pagador definido.",

  // Automações
  automationIntegracoesPlanejadas: "Quantidade de integrações mapeadas no plano do centro de comando.",
  automationFontesPrivadas: "Número de fontes de dados privadas previstas para alimentar o painel (Sheets, Gmail, Agenda, Meta, GA4, CRM).",
  automationStatusAtual: "Estágio atual de implantação do painel privado da SoproLife.",
  automationPrioridade: "Foco atual da automação — hoje, manter dados sensíveis fora do repositório público.",

  // Últimos lançamentos
  lancamentosEventos: "Total de eventos registrados na timeline de atualizações do painel.",
  lancamentosHoje: "Eventos de atualização registrados no dia de hoje.",
  lancamentosAltaPrioridade: "Eventos marcados como alta prioridade, que merecem atenção mais rápida.",
  lancamentosPendencias: "Eventos com erro ou pendência que ainda precisam ser resolvidos.",
  lancamentosStatusIndisponivel: "O gerador de últimos lançamentos ainda não foi executado neste ambiente.",
  auditoriaTotal: "Total de escritas feitas pelo painel registradas na trilha de auditoria do PostgreSQL.",
  auditoriaErros: "Escritas rejeitadas pela API — se crescer, investigar os logs do Núcleo M15.",
  auditoriaOperadores: "Quantos operadores distintos já fizeram alterações pelo painel.",
};

// Gera os atributos de tooltip (data-tip, aria-label, tabindex) para um card,
// a partir da chave de métrica. Retorna strings vazias quando não há texto mapeado.
function tipAttrs(key, label, value) {
  const text = key ? METRIC_INFO[key] : null;
  if (!text) return { cls: "", attrs: "" };
  const safeTip = escapeHtml(text);
  const ariaBits = [label, value]
    .filter((v) => v !== undefined && v !== null && String(v) !== "")
    .map((v) => escapeHtml(String(v)));
  const ariaLabel = ariaBits.length ? `${ariaBits.join(": ")}. ${safeTip}` : safeTip;
  return {
    cls: "has-tip",
    attrs: ` tabindex="0" data-tip="${safeTip}" aria-label="${ariaLabel}"`
  };
}

// Renderizador genérico para os "quadradinhos" de indicador ({key,label,value,hint,extraClass}),
// usado em todas as seções para evitar duplicar a lógica de tooltip em cada ponto.
function statCardHtml(baseClass, item) {
  const t = tipAttrs(item.key, item.label, item.value);
  const classes = ["stat-card", baseClass, item.extraClass, t.cls].filter(Boolean).join(" ");
  const valueStyle = item.valueStyle ? ` style="${item.valueStyle}"` : "";
  return `
    <article class="${classes}"${t.attrs}>
      <span>${item.label}</span>
      <strong${valueStyle}>${item.value}</strong>
      ${item.hint !== undefined ? `<small>${item.hint}</small>` : ""}
    </article>
  `;
}

const KPI_ACCENTS = {
  up: "var(--teal)",
  down: "var(--danger)",
  neutral: "var(--muted-light)"
};

function renderMetricCard(card) {
  const t = tipAttrs(card.key, card.label, card.value);
  const classes = ["metric-card", "kpi-card", t.cls].filter(Boolean).join(" ");
  const typeClass = card.type === "down" || card.type === "neutral" ? card.type : "up";
  const accent = KPI_ACCENTS[typeClass];
  const showVariation = Boolean(card.variation && card.variation.trim() !== "");
  return `
    <article class="${classes}"${t.attrs} style="--kpi-accent:${accent}">
      <div class="kpi-top">
        <span class="kpi-label">${card.label}</span>
        <strong class="kpi-value">${card.value}</strong>
      </div>
      ${showVariation ? `<div class="variation ${typeClass}">${card.variation}</div>` : ""}
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
  const finSummary = state.financeiro_summary;

  const isSafeLocalSummary = Boolean(
    localSummary?.source?.safeToDisplay &&
    localSummary?.source?.containsPersonalData === false &&
    localSummary?.source?.containsHealthData === false &&
    Array.isArray(localSummary?.cards)
  );

  const hasRealFinance = Boolean(
    finSummary?.source?.safeToDisplay === true &&
    finSummary?.source?.containsPersonalData === false
  );

  if (!isSafeLocalSummary) {
    return state.resumo.cards;
  }

  let cards = localSummary.cards.map((card) => ({
    key: card.key,
    label: card.label,
    value: formatDashboardValue(card.key, card.value)
  }));

  if (hasRealFinance) {
    // Remove previsão desatualizada; substitui recebido pelo valor real de espirometrias
    cards = cards
      .filter((c) => c.key !== "receitaPrevista")
      .map((c) => {
        if (c.key === "receitaRecebida") {
          return { ...c, label: "Receita oficial de espirometrias", value: fmtBRL(finSummary.receita_exames), variation: "Financeiro real" };
        }
        return c;
      });
  }

  return cards;
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

// Por padrão só conta prioridade dentro da prospecção ativa: uma clínica já
// "Parceiro ativo" ou já perdida/arquivada não deveria inflar o card
// "Prioridade alta" da prospecção comum (etapa terminal ≠ atraso comum).
function countCrmPrioridade(prioridade, { incluirTerminais = false } = {}) {
  return state.crm.filter(
    (item) => item.prioridade === prioridade && (incluirTerminais || !isCrmEtapaTerminal(item.etapa))
  ).length;
}

function renderCrmStats() {
  const parceirosAtivos = countCrmEtapa(CRM_ETAPA_TERMINAL_POSITIVA);
  const perdidasArquivadas = state.crm.filter((item) => isCrmEtapaTerminalNegativa(item.etapa)).length;
  const emProspeccao = state.crm.filter((item) => !isCrmEtapaTerminal(item.etapa)).length;

  const stats = [
    { key: "clinicasCadastradas", label: "Clínicas cadastradas", value: state.crm.length,           hint: "base total"          },
    { key: "crmEmProspeccao",     label: "Em prospecção",        value: emProspeccao,                hint: "ativas no funil"     },
    { key: "crmParceirosAtivos",  label: "Parceiros ativos",     value: parceirosAtivos,             hint: "etapa terminal positiva"  },
    { key: "crmPerdidasArquivadas", label: "Perdidas/Arquivadas", value: perdidasArquivadas,          hint: "etapa terminal negativa" },
    { key: "crmPrioridadeAlta",   label: "Prioridade alta",      value: countCrmPrioridade("Alta"),  hint: "foco imediato"       },
    { key: "crmComAcaoDefinida",  label: "Com ação definida",    value: state.crm.filter((c) => c.proximaAcao).length, hint: "próximas ações" }
  ];

  const container = document.querySelector("#crmStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => statCardHtml("crm-stat-card", item)).join("");
}

function renderCrmFunnelVisual() {
  const hints = {
    "Não abordada": "sem contato",
    "Abordada": "abordagem feita",
    "Em conversa": "diálogo ativo",
    "Pediu apresentação": "reunião/apresentação pedida",
    "Aguardando retorno": "aguardando resposta",
    "Proposta enviada": "aguardando decisão",
  };
  const steps = [
    ...CRM_ETAPAS_ATIVAS.map((label) => ({ label, value: countCrmEtapa(label), hint: hints[label] || "" })),
    { label: "Parceiro ativo", value: countCrmEtapa(CRM_ETAPA_TERMINAL_POSITIVA), hint: "meta alcançada · etapa terminal" },
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

  // Etapas terminais (positiva ou negativa) já "fecharam" a prospecção —
  // não entram como atraso/prioridade/conversa de prospecção comum. Cada
  // uma tem seu próprio card abaixo (Parceiros ativos / Perdidas-Arquivadas).
  const ativos = state.crm.filter((c) => !isCrmEtapaTerminal(c.etapa));

  const hoje = ativos
    .filter((c) => { const iso = parseDateIso(c.dataProximaAcao); return iso && iso === today; })
    .sort((a, b) => (a.prioridade === "Alta" ? -1 : 1));

  const atrasados = ativos
    .filter((c) => { const iso = parseDateIso(c.dataProximaAcao); return iso && iso < today; })
    .sort((a, b) => parseDateIso(a.dataProximaAcao).localeCompare(parseDateIso(b.dataProximaAcao)));

  const altaPrio = ativos
    .filter((c) => c.prioridade === "Alta")
    .sort((a, b) => a.clinica.localeCompare(b.clinica));

  const emConversa = ativos
    .filter((c) => c.etapa === "Em conversa")
    .sort((a, b) => a.clinica.localeCompare(b.clinica));

  const parceirosAtivos = state.crm
    .filter((c) => c.etapa === CRM_ETAPA_TERMINAL_POSITIVA)
    .sort((a, b) => (a.prioridade === "Alta" ? -1 : 1) || a.clinica.localeCompare(b.clinica));

  const perdidasArquivadas = state.crm
    .filter((c) => isCrmEtapaTerminalNegativa(c.etapa))
    .sort((a, b) => a.clinica.localeCompare(b.clinica));

  function waButton(item) {
    if (!state.followupClinicas) return "";
    const clinicas = state.followupClinicas.clinicas || [];
    const slugNome = (s) => String(s).toLowerCase().normalize("NFD")
      .replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();
    const match = clinicas.find((c) =>
      slugNome(c.nome_clinica) === slugNome(item.clinica)
    );
    if (!match || !match.whatsapp_url) return "";
    return `<a class="fp-wa-btn followup-wa-btn" href="${match.whatsapp_url}" target="_blank" rel="noopener">WhatsApp</a>`;
  }

  function itemHtml(item) {
    const data = formatDateBr(item.dataProximaAcao);
    return `<li class="followup-item">
      <div class="followup-item-top">
        <strong>${item.clinica}</strong>
        ${data ? `<span class="followup-date">${data}</span>` : ""}
      </div>
      ${item.proximaAcao ? `<small>${item.proximaAcao}</small>` : ""}
      ${waButton(item)}
    </li>`;
  }

  function card({ cls, icon, title, items, empty, tip }) {
    const count = items.length;
    const body = count > 0
      ? `<ul class="followup-list">${items.map(itemHtml).join("")}</ul>`
      : `<p class="followup-empty">${empty}</p>`;
    return `<article class="followup-card ${cls}">
      <div class="followup-card-header"${tip ? ` title="${escapeHtml(tip)}"` : ""}>
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
      ${card({ cls: "card-hoje",     icon: "⚡", title: "Hoje",            items: hoje,          empty: "Nenhuma ação agendada para hoje" })}
      ${card({ cls: "card-atrasado", icon: "⚠",  title: "Atrasados",       items: atrasados,     empty: "Nenhum follow-up em atraso" })}
      ${card({ cls: "card-alta",     icon: "🔴", title: "Alta prioridade", items: altaPrio,      empty: "Nenhuma clínica de alta prioridade" })}
      ${card({ cls: "card-conversa", icon: "💬", title: "Em conversa",     items: emConversa,    empty: "Nenhuma clínica em conversa ativa" })}
      ${card({ cls: "card-parceiro", icon: "🤝", title: "Parceiros ativos", items: parceirosAtivos, empty: "Nenhum parceiro ativo ainda", tip: "Etapa terminal positiva: parceria já fechada. Fica aqui — não conta como atraso ou alta prioridade de prospecção comum." })}
      ${card({ cls: "card-perdido",  icon: "🗂",  title: "Perdidas/Arquivadas", items: perdidasArquivadas, empty: "Nenhuma clínica perdida ou arquivada", tip: "Etapa terminal negativa: sem interesse, não contatar/bloqueou, sem canal válido ou arquivada. Fora do funil ativo." })}
    </div>
  `;
}

/* M21 — os quatro cards de atalho do CRM (Central de Cadastros, Clínicas e
 * Parceiros, Pacientes e Acompanhamento, Automações CRM) foram REMOVIDOS, não
 * escondidos: crmModuleCard() e o grid .crm-hub-grid deixaram de existir.
 * Motivos: Central de Cadastros já é item de sidebar; Clínicas e Parceiros
 * pertence a Parcerias (que ganhou a ação de abrir a lista B2B); a própria
 * página de CRM já é Pacientes e Acompanhamento; e a automação de CRM passou a
 * ser um destino de sidebar em Sistema ("Automação CRM").
 */
function renderCrmView() {
  const container = document.querySelector("#crmView");
  if (!container) return;

  switch (state.crmView) {
    case "clinicas":          renderCrmClinicas(container);             break;
    // M19 — uma única implementação de paciente/acompanhamento, servida pelo
    // workspace canônico (PostgreSQL/Núcleo M15). As telas legadas de
    // planilha ("pacientes" e "followup-detalhe") e a parcial "acompanhamento
    // -m15" foram removidas.
    // M21 — o workspace deixou de ser subview: ele É a página de CRM. Os
    // nomes antigos continuam resolvendo, agora para o próprio hub, para que
    // deep-links salvos não quebrem.
    case "pacientes":
    case "followup-detalhe":
    case "acompanhamento-m15":
    case "pacientes-acompanhamento":
      state.crmView = "hub";
      renderCrmHub(container);
      break;
    case "relatorios":        renderCrmRelatorios(container);               break;
    // M21 — "Automação CRM" virou destino de sidebar em Sistema. O nome antigo
    // de subview resolve para lá, em vez de abrir uma segunda implementação.
    case "automacoes-crm":
      state.crmView = "hub";
      renderCrmHub(container);
      irParaSecao("automacoes-crm");
      break;
    default: renderCrmHub(container);
  }
}

/* Troca de seção programática: reaproveita o item de sidebar (uma única
 * implementação de navegação), então qualquer alias de rota antigo cai na
 * mesma troca de classes que um clique humano. */
function irParaSecao(sectionId) {
  const navItem = document.querySelector(`.nav-item[data-section="${sectionId}"]`);
  if (navItem) navItem.click();
}

// M19 — ponte mínima para o workspace canônico de CRM voltar ao hub sem
// que o módulo precise conhecer o estado interno do painel.
window.SoproCrmHost = {
  voltar: function () {
    state.crmView = "hub";
    renderCrmView();
  },
};

function renderCrmHub(container) {
  const emProspeccao = state.crm.filter((item) => !isCrmEtapaTerminal(item.etapa)).length;

  const parceirosAtivosAlta = state.crm.filter((c) =>
    c.etapa === CRM_ETAPA_TERMINAL_POSITIVA && c.prioridade === "Alta"
  );
  const propostasEstrategicas = state.crm.filter((c) =>
    c.prioridade === "Alta" &&
    c.etapa === "Proposta enviada" &&
    (c.tipo.toLowerCase().includes("estratégica") || c.tipo.toLowerCase().includes("rede"))
  );

  const marcoHtml = parceirosAtivosAlta.length > 0 ? `
    <div class="marco-banner marco-banner-parceiro">
      <span class="marco-icon" aria-hidden="true">🤝</span>
      <div class="marco-body">
        <strong>Implantação / Piloto — parceiro(s) ativo(s)</strong>
        <p>${parceirosAtivosAlta.map((p) => `<b>${escapeHtml(p.clinica)}</b>${p.bairro ? ` (${escapeHtml(p.bairro.split(" · ").pop())})` : ""} — ${escapeHtml(p.proximaAcao || "Alinhar próximo passo")}`).join("<br>")}</p>
      </div>
      <span class="badge parceiro-ativo">Parceiro ativo</span>
    </div>
  ` : propostasEstrategicas.length > 0 ? `
    <div class="marco-banner">
      <span class="marco-icon" aria-hidden="true">🏆</span>
      <div class="marco-body">
        <strong>Marco estratégico em andamento</strong>
        <p>${propostasEstrategicas.map((p) => `<b>${escapeHtml(p.clinica)}</b> — ${escapeHtml(p.proximaAcao)}`).join("<br>")}</p>
      </div>
      <span class="badge proposta-enviada">Aguardando contrato</span>
    </div>
  ` : "";

  // Relatórios CRM agora moram no próprio hub (abaixo dos cards) — mesmos
  // filtros e containers da antiga subview "relatorios".
  if (!state.crmReportFilters) {
    state.crmReportFilters = { periodo: "Todos", tipo: "Todos", origem: "Todas" };
  }
  const f = state.crmReportFilters;

  container.innerHTML = `
    <div class="crm-hub-header">
      <p class="eyebrow">Relacionamento com pacientes</p>
      <h2>CRM SoproLife</h2>
      <p class="section-sub">Pacientes, contatos a realizar, acompanhamento e recorrência.
        Fonte oficial: PostgreSQL / Núcleo M15.</p>
    </div>
    ${marcoHtml}

    <!-- M21 — o CRM começa pela operação real de pacientes (KPIs, filas de
         contato, gráficos de acompanhamento e origem, atividade recente),
         servida pelo workspace canônico. Nenhum card de atalho duplica
         destino de sidebar. -->
    <div id="crmWorkspace"></div>

    <div class="crm-hub-report-header">
      <div>
        <h3>Relatórios CRM</h3>
        <span>Indicadores consolidados de relacionamento</span>
      </div>
      <div class="crm-report-filters">
        <label>Período
          <select id="crmReportPeriodo" class="crm-filter-select">
            <option value="Todos"${f.periodo === "Todos" ? " selected" : ""}>Todos</option>
            <option value="7d"${f.periodo === "7d" ? " selected" : ""}>Últimos 7 dias</option>
            <option value="30d"${f.periodo === "30d" ? " selected" : ""}>Últimos 30 dias</option>
            <option value="mesAtual"${f.periodo === "mesAtual" ? " selected" : ""}>Mês atual</option>
          </select>
        </label>
        <label>Tipo
          <select id="crmReportTipo" class="crm-filter-select">
            <option value="Todos"${f.tipo === "Todos" ? " selected" : ""}>Todos</option>
            <option value="Pacientes"${f.tipo === "Pacientes" ? " selected" : ""}>Pacientes</option>
            <option value="B2B"${f.tipo === "B2B" ? " selected" : ""}>B2B / Clínicas</option>
          </select>
        </label>
        <label>Origem
          <select id="crmReportOrigem" class="crm-filter-select">
            <option value="Todas"${f.origem === "Todas" ? " selected" : ""}>Todas</option>
            <option value="Google"${f.origem === "Google" ? " selected" : ""}>Google</option>
            <option value="Site"${f.origem === "Site" ? " selected" : ""}>Site</option>
            <option value="Indicação"${f.origem === "Indicação" ? " selected" : ""}>Indicação</option>
            <option value="Outro"${f.origem === "Outro" ? " selected" : ""}>Outro</option>
          </select>
        </label>
      </div>
    </div>

    <div id="crmReportChartsGrid"></div>
    <div id="crmReportStats" class="crm-stats"></div>

    <article class="panel">
      <div class="panel-header">
        <h3>Leituras rápidas</h3>
        <span>Insights automáticos com base nos dados atuais</span>
      </div>
      <div id="crmReportInsights" class="crm-report-insights"></div>
    </article>

    <div class="crm-safe-note">
      <span>📊</span>
      <p><strong>Dados agregados do CRM.</strong> Atualizado a partir dos arquivos locais seguros. Sem dados pessoais exibidos nesta visão.</p>
    </div>
  `;

  // M21 — monta o workspace canônico de pacientes DENTRO do hub. Ele é a
  // primeira coisa útil da página; os relatórios agregados ficam abaixo, em
  // um container próprio que o workspace nunca reescreve.
  const mount = container.querySelector("#crmWorkspace");
  if (mount) {
    if (window.SoproCrm && typeof window.SoproCrm.abrir === "function") {
      window.SoproCrm.abrir(mount, null, { landing: true });
    } else {
      mount.innerHTML = `<div class="crm-empty">Workspace de CRM indisponível nesta página.</div>`;
    }
  }

  const filterIdToKey = { crmReportPeriodo: "periodo", crmReportTipo: "tipo", crmReportOrigem: "origem" };
  Object.keys(filterIdToKey).forEach((id) => {
    const select = container.querySelector(`#${id}`);
    if (select) {
      select.addEventListener("change", (e) => {
        state.crmReportFilters[filterIdToKey[id]] = e.target.value;
        renderCrmReportBody();
      });
    }
  });

  renderCrmReportBody();
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
        <option value="Ativos">Ativos</option>
        <option value="Parceiros ativos">Parceiros ativos</option>
        <option value="Perdidas">Perdidas / sem interesse</option>
        <option value="Todos">Todos</option>
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
              <th>Ações</th>
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
  renderCrmTable(state.crmFilter);

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });

  const crmFilterEl = document.querySelector("#crmFilter");
  crmFilterEl.value = state.crmFilter || "Ativos";
  updateCrmFilterTip(crmFilterEl);
  crmFilterEl.addEventListener("change", (e) => {
    state.crmFilter = e.target.value;
    updateCrmFilterTip(crmFilterEl);
    renderCrmTable(e.target.value);
  });

  // Delegado no tbody (persiste entre re-renders de renderCrmTable, já que só
  // o innerHTML do tbody muda — o próprio elemento #crmTable é recriado
  // apenas quando renderCrmClinicas roda de novo, daí o listener ser
  // registrado aqui e não em bindEvents/init).
  document.querySelector("#crmTable").addEventListener("click", (event) => {
    const stageBtn = event.target.closest(".crm-stage-btn");
    if (!stageBtn) return;
    const clinica = state.crm.find((c) => c.id === stageBtn.dataset.clinicaId);
    if (clinica) openCrmStageModal(clinica, stageBtn);
  });
}

// Explica o que cada opção do filtro Ativos/Parceiros ativos/Perdidas/Todos
// mostra — mesmo padrão de LEADS_FILTER_TIP/updateLeadsFilterTip.
const CRM_FILTER_TIP = {
  "Ativos":          "Mostra clínicas ainda em prospecção, sem etapa terminal (nem parceria fechada, nem perdida/arquivada).",
  "Parceiros ativos": "Mostra clínicas na etapa terminal positiva — parceria já fechada.",
  "Perdidas":         "Mostra clínicas em etapa terminal negativa: sem interesse, não contatar/bloqueou, sem canal válido ou arquivada.",
  "Todos":            "Mostra todas as clínicas cadastradas, em qualquer etapa.",
};

function updateCrmFilterTip(el) {
  const tip = CRM_FILTER_TIP[el.value] || "";
  el.title = tip;
  el.setAttribute("aria-label", `Filtro de clínicas: ${el.value}. ${tip}`);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Automação CRM (M21) — destino canônico ÚNICO, agora em Sistema.
 *
 * Antes era o card "Automações CRM" dentro da página de CRM. O conteúdo é o
 * mesmo módulo (nenhuma segunda implementação foi criada): as regras de
 * acompanhamento, lembrete e reativação vêm primeiro, e as rotinas manuais
 * assistidas que já existiam continuam abaixo, intactas.
 *
 * Nada aqui dispara envio automático de WhatsApp: o painel só monta mensagem
 * para revisão humana, e este M21 não habilita envio automático.
 */
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

  // M23 — a saúde da fonte é a do PostgreSQL, nunca a de uma planilha.
  const fonteOk   = rs?.dataSource?.canonical === "postgresql";
  const fonteName = rs?.dataSource?.name ?? "—";
  const fonteAt   = fmtDate(rs?.dataSource?.lastCheckedAt);

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
        fonteOk ? "✅" : "❌",
        "Fonte operacional",
        fonteOk ? fonteName : "Fonte canônica não declarada",
        fonteOk ? `Verificado ${fonteAt}` : "Execute update-local-data.sh"
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
      subtitle: "Regenera os snapshots do painel a partir do PostgreSQL",
      risk: "baixo",
      dados: ["Agrega dados", "CRM clínicas", "Sem dados pessoais"],
      objetivo: "Gera os snapshots .local.json direto do PostgreSQL (fonte operacional única), atualiza Search Console/GA4 pela conta de serviço e valida segurança. Nenhum leitor de Google Sheets é executado.",
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
      subtitle: "Utilitário legado — bloqueado no modo postgresql-only",
      risk: "medio",
      dados: ["CRM clínicas", "Base PCMSO", "Sem dados pessoais"],
      objetivo: "Promoção legada entre abas de planilha. Desde o M23 o painel opera em modo postgresql-only e este utilitário é BLOQUEADO fail-closed (exit 3): só roda com decisão humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1), para migração ou forense. Clínicas e parceiros novos nascem na Central de Cadastros, no PostgreSQL.",
      steps: [
        {
          label: "1. Pré-visualização (dry-run) — obrigatório antes do write",
          hint: "Bloqueado sem o escape explícito. Não altera nada.",
          cmd: `${VENV} ${SCRIPTS}/promote-pcmso-to-crm.py --dry-run`,
        },
        {
          label: "2. Executar promoção (somente após revisar o dry-run)",
          hint: "Escrita legada em planilha. Bloqueada sem o escape explícito.",
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
      title: "Follow-up de pacientes (arquivo histórico)",
      subtitle: "Utilitário legado — bloqueado no modo postgresql-only",
      risk: "baixo",
      dados: ["Pacientes (privado)", "Espirometrias", "Consultas"],
      objetivo: "Extração legada das abas de planilha. Desde o M23 o painel opera em modo postgresql-only e este utilitário é BLOQUEADO fail-closed: só roda com decisão humana explícita (SOPROLIFE_ALLOW_LEGACY_SHEETS_MIGRATION=1), para migração ou forense. O acompanhamento de pacientes vive no CRM, direto do PostgreSQL.",
      aviso: "Executar sem o escape explícito falha com exit 3. O timer de produção nunca consegue disparar este script.",
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
      subtitle: "Atualiza os indicadores da visão geral a partir do PostgreSQL",
      risk: "baixo",
      dados: ["Agrega indicadores", "Sem dados pessoais"],
      objetivo: "Conta os indicadores diretamente no banco canônico e grava o arquivo local. Parte do update-local-data.sh; pode ser executado isoladamente para diagnóstico.",
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
    <div class="section-heading">
      <div>
        <p class="eyebrow">Sistema · CRM</p>
        <h2>Automação CRM</h2>
        <span>Regras de acompanhamento, lembrete e reativação de pacientes,
          mais o status do que está pendente de automação.</span>
      </div>
      <div class="section-actions">
        <span class="safe-badge">Nada é disparado automaticamente</span>
      </div>
    </div>

    ${renderAutomacaoCrmRegras()}

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

  // M21 — esta página é destino de sidebar, não subview: não há "← CRM".
}

/* Renderiza a seção Automação CRM e, quando há sessão do Núcleo, busca os
 * contadores reais do CRM (uma chamada, só leitura). Sem sessão, a página
 * abre igual e diz que os contadores dependem de login. */
function renderAutomacaoCrmSection() {
  const container = document.querySelector("#automacoesCrmView");
  if (!container) return;
  renderCrmAutomacoes(container);

  const m15 = window.SoproM15;
  if (!m15 || !m15.hasToken()) return;
  m15.api("/crm/kpis").then((kpis) => {
    state.crmKpisAutomacao = kpis;
    // Só re-renderiza se a seção ainda estiver aberta — evita sobrescrever
    // uma navegação que o operador já fez.
    const secao = document.querySelector("#automacoes-crm");
    if (secao && secao.classList.contains("active")) renderCrmAutomacoes(container);
  }).catch(() => {
    // Falha de leitura não pode esconder as regras; contadores seguem "—".
  });
}

/* Regras de automação de CRM — o conteúdo próprio desta página.
 *
 * Os números de pendência vêm do CRM canônico (API do Núcleo M15) quando há
 * sessão; sem sessão, a página mostra as REGRAS mesmo assim e diz honestamente
 * que os contadores precisam de login. Nenhum número é inventado.
 */
function renderAutomacaoCrmRegras() {
  const k = state.crmKpisAutomacao || null;
  const num = (v) => (typeof v === "number" ? String(v) : "—");

  const pendencias = [
    ["Contatos de hoje", num(k?.contatos_hoje), "vencendo hoje"],
    ["Contatos atrasados", num(k?.contatos_atrasados), "passaram da data"],
    ["Próximos 7 dias", num(k?.proximos_7), "a programar"],
    ["Sem telefone válido", num(k?.sem_telefone), "sem canal de contato"],
  ].map(([rotulo, valor, sub]) => `
    <div class="auto-status-row">
      <span class="auto-status-icon">${valor !== "0" && valor !== "—" ? "⚠️" : "✅"}</span>
      <div class="auto-status-body">
        <strong>${escapeHtml(rotulo)}</strong>
        <span>${escapeHtml(valor)}</span>
        <small>${escapeHtml(sub)}</small>
      </div>
    </div>`).join("");

  const regra = ({ icon, titulo, quando, acao, estado, obs }) => `
    <article class="auto-regra">
      <div class="auto-regra-head">
        <span class="auto-regra-icon" aria-hidden="true">${icon}</span>
        <h4>${escapeHtml(titulo)}</h4>
        <span class="auto-regra-estado">${escapeHtml(estado)}</span>
      </div>
      <dl class="auto-regra-corpo">
        <dt>Quando</dt><dd>${escapeHtml(quando)}</dd>
        <dt>O que acontece</dt><dd>${escapeHtml(acao)}</dd>
      </dl>
      ${obs ? `<p class="auto-regra-obs">${escapeHtml(obs)}</p>` : ""}
    </article>`;

  return `
    <section class="auto-status-card">
      <h3 class="auto-status-title">Pendências de automação agora</h3>
      ${k ? "" : `<p class="auto-regra-obs">Entre no Núcleo administrativo
        (mesma sessão) para ver os contadores reais do CRM. As regras abaixo
        valem independentemente do login.</p>`}
      <div class="auto-status-grid">${pendencias}</div>
    </section>

    <section class="auto-status-card">
      <h3 class="auto-status-title">Regras ativas</h3>
      <div class="auto-regras">
        ${regra({
          icon: "📅",
          titulo: "Acompanhamento após atendimento",
          quando: "Um exame ou consulta é registrado com data de retorno explícita.",
          acao: "O CRM cria um acompanhamento com vencimento nessa data e ele "
            + "aparece nas filas de contato (hoje, atrasados, próximos dias).",
          estado: "Ativa",
          obs: "Nenhum retorno é presumido: sem escolha explícita do operador, "
            + "nenhum acompanhamento é criado.",
        })}
        ${regra({
          icon: "🔔",
          titulo: "Lembrete de contato a realizar",
          quando: "Um acompanhamento vence hoje ou já passou da data.",
          acao: "O paciente entra na fila correspondente do CRM e o operador "
            + "registra o resultado do contato, que fica auditável.",
          estado: "Ativa",
          obs: "O lembrete é visual, na fila do CRM. Nenhuma mensagem sai sozinha.",
        })}
        ${regra({
          icon: "♻️",
          titulo: "Reativação de paciente",
          quando: "Um paciente sem atendimento recente volta a ser atendido "
            + "ou contatado com sucesso.",
          acao: "Ele é contado como reativado no mês corrente, nos indicadores "
            + "do CRM.",
          estado: "Ativa (indicador)",
          obs: "Regra de medição, não de disparo: nada é enviado ao paciente.",
        })}
        ${regra({
          icon: "💬",
          titulo: "WhatsApp assistido",
          quando: "O operador escolhe abrir o WhatsApp de um paciente com "
            + "consentimento registrado e telefone válido.",
          acao: "O painel monta a mensagem a partir de um modelo e abre o "
            + "WhatsApp para REVISÃO humana. Abrir não conclui o acompanhamento.",
          estado: "Envio automático DESLIGADO",
          obs: "Envio automático não é habilitado nesta etapa. Quando for, "
            + "passará por decisão explícita e ficará controlado aqui.",
        })}
      </div>
    </section>`;
}

// ── Escrita operacional (M23) ────────────────────────────────────────────────
// Até o M22 estas ações passavam por um proxy local que encaminhava para o
// Apps Script e gravava no Google Sheets. A partir do M23 o PostgreSQL é a
// única fonte operacional: toda escrita vai pela API autenticada do Núcleo
// M15, pela mesma sessão que a Central de Cadastros e o CRM já usam.
//
// Nenhuma credencial passa por aqui — window.SoproM15 guarda a sessão em
// cookie HttpOnly e o csrf apenas em memória.

function m15Session() {
  const nucleo = window.SoproM15;
  if (!nucleo || !nucleo.hasSession()) {
    throw new Error(
      "Entre no Núcleo Operacional para gravar. A escrita exige sessão "
      + "autenticada — o painel não grava dado operacional sem identidade."
    );
  }
  return nucleo;
}

// Resolve um código público (LEA-000001, CLI-000002) para o identificador
// interno esperado pelos endpoints de atualização.
async function resolverCodigoPublico(codigo, entidadeEsperada) {
  const nucleo = m15Session();
  const resp = await nucleo.api(
    "/crm/codigos/resolver?codigo=" + encodeURIComponent(codigo)
  );
  const achado = (resp?.resultados || []).find(
    (r) => r.entidade === entidadeEsperada && r.id
  );
  if (!achado) {
    throw new Error(`Código ${codigo} não encontrado no banco canônico.`);
  }
  return achado.id;
}

async function atualizarEtapaLeadNoBanco(publicCode, etapa) {
  const nucleo = m15Session();
  const id = await resolverCodigoPublico(publicCode, "leads");
  await nucleo.api(`/leads/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ etapa }),
  });
  return { message: "Etapa atualizada no banco." };
}

async function atualizarStatusParceiroNoBanco(publicCode, status) {
  const nucleo = m15Session();
  const id = await resolverCodigoPublico(publicCode, "partners");
  await nucleo.api(`/parceiros/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  return { message: "Etapa da clínica atualizada no banco." };
}

// ── Relatórios CRM ───────────────────────────────────────────────────────────
// Página de indicadores consolidados. Usa apenas dados agregados/seguros já
// carregados em state (leads, crm, resumos de follow-up e contatos B2B) —
// nunca state.followupClinicas (privado, com nome e telefone) nem qualquer
// campo pessoal. O detalhe por paciente vive só no CRM canônico, autenticado.

const CRM_REPORT_ORIGENS_CONHECIDAS = ["Google", "Site", "Indicação"];

function parseLeadDate(dateStr) {
  if (!dateStr) return null;
  const datePart = String(dateStr).trim().split(" ")[0];
  const iso = parseDateIso(datePart);
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function leadMatchesPeriodo(lead, periodo) {
  if (periodo === "Todos") return true;
  const d = parseLeadDate(lead.data_contato);
  if (!d) return false;
  const now = new Date();
  if (periodo === "mesAtual") {
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  }
  const diffDias = (now - d) / 86400000;
  if (periodo === "7d") return diffDias >= 0 && diffDias <= 7;
  if (periodo === "30d") return diffDias >= 0 && diffDias <= 30;
  return true;
}

function leadMatchesTipo(lead, tipo) {
  if (tipo === "Todos") return true;
  return tipo === "B2B" ? isB2BLead(lead) : !isB2BLead(lead);
}

function leadMatchesOrigem(lead, origem) {
  if (origem === "Todas") return true;
  const o = (lead.origem || "").trim();
  if (origem === "Outro") return !CRM_REPORT_ORIGENS_CONHECIDAS.includes(o);
  return o === origem;
}

function getCrmReportFilteredLeads() {
  const f = state.crmReportFilters;
  return state.leads.filter((l) =>
    leadMatchesPeriodo(l, f.periodo) &&
    leadMatchesTipo(l, f.tipo) &&
    leadMatchesOrigem(l, f.origem)
  );
}

function crmReportStatCard(key, label, value, hint) {
  const isEmpty = value === null || value === undefined || value === "";
  return statCardHtml("crm-stat-card", {
    key,
    label,
    value: isEmpty ? "—" : value,
    hint: isEmpty ? "dado ainda não disponível" : hint,
  });
}

function crmReportChartPanel({ title, subtitle, canvasId, hasData, emptyMsg }) {
  const body = hasData
    ? `<canvas id="${canvasId}"></canvas>`
    : `<p class="crm-report-chart-empty">${emptyMsg || "Dado ainda não disponível"}</p>`;
  return `
    <article class="panel">
      <div class="panel-header">
        <h3>${title}</h3>
        <span>${subtitle}</span>
      </div>
      ${body}
    </article>
  `;
}

function renderCrmRelatorios(container) {
  if (!state.crmReportFilters) {
    state.crmReportFilters = { periodo: "Todos", tipo: "Todos", origem: "Todas" };
  }
  const f = state.crmReportFilters;

  container.innerHTML = `
    <div class="crm-subview-header">
      <button class="crm-back-btn" id="crmBackBtn">← CRM</button>
      <div>
        <p class="eyebrow">CRM SoproLife</p>
        <h2>Relatórios CRM</h2>
        <p class="section-sub">Indicadores consolidados de relacionamento</p>
      </div>
    </div>

    <div class="crm-report-filters">
      <label>Período
        <select id="crmReportPeriodo" class="crm-filter-select">
          <option value="Todos"${f.periodo === "Todos" ? " selected" : ""}>Todos</option>
          <option value="7d"${f.periodo === "7d" ? " selected" : ""}>Últimos 7 dias</option>
          <option value="30d"${f.periodo === "30d" ? " selected" : ""}>Últimos 30 dias</option>
          <option value="mesAtual"${f.periodo === "mesAtual" ? " selected" : ""}>Mês atual</option>
        </select>
      </label>
      <label>Tipo
        <select id="crmReportTipo" class="crm-filter-select">
          <option value="Todos"${f.tipo === "Todos" ? " selected" : ""}>Todos</option>
          <option value="Pacientes"${f.tipo === "Pacientes" ? " selected" : ""}>Pacientes</option>
          <option value="B2B"${f.tipo === "B2B" ? " selected" : ""}>B2B / Clínicas</option>
        </select>
      </label>
      <label>Origem
        <select id="crmReportOrigem" class="crm-filter-select">
          <option value="Todas"${f.origem === "Todas" ? " selected" : ""}>Todas</option>
          <option value="Google"${f.origem === "Google" ? " selected" : ""}>Google</option>
          <option value="Site"${f.origem === "Site" ? " selected" : ""}>Site</option>
          <option value="Indicação"${f.origem === "Indicação" ? " selected" : ""}>Indicação</option>
          <option value="Outro"${f.origem === "Outro" ? " selected" : ""}>Outro</option>
        </select>
      </label>
    </div>

    <div id="crmReportStats" class="crm-stats"></div>
    <div id="crmReportChartsGrid"></div>

    <article class="panel">
      <div class="panel-header">
        <h3>Leituras rápidas</h3>
        <span>Insights automáticos com base nos dados atuais</span>
      </div>
      <div id="crmReportInsights" class="crm-report-insights"></div>
    </article>

    <div class="crm-safe-note">
      <span>📊</span>
      <p><strong>Dados agregados do CRM.</strong> Atualizado a partir dos arquivos locais seguros. Sem dados pessoais exibidos nesta visão.</p>
    </div>
  `;

  document.querySelector("#crmBackBtn").addEventListener("click", () => {
    state.crmView = "hub";
    renderCrmView();
  });

  const filterIdToKey = { crmReportPeriodo: "periodo", crmReportTipo: "tipo", crmReportOrigem: "origem" };
  Object.keys(filterIdToKey).forEach((id) => {
    document.querySelector(`#${id}`).addEventListener("change", (e) => {
      state.crmReportFilters[filterIdToKey[id]] = e.target.value;
      renderCrmReportBody();
    });
  });

  renderCrmReportBody();
}

function renderCrmReportBody() {
  const leads = getCrmReportFilteredLeads();
  renderCrmReportStats(leads);
  renderCrmReportCharts(leads);
  renderCrmReportInsights(leads);
}

function renderCrmReportStats(leads) {
  const container = document.querySelector("#crmReportStats");
  if (!container) return;

  const ativos = leads.filter((l) => !isLeadConvertido(l)).length;
  const convertidos = leads.filter(isLeadConvertido).length;
  const taxaConversao = leads.length > 0 ? `${Math.round((convertidos / leads.length) * 100)}%` : null;

  const emProspeccao = state.crm.filter((item) => !isCrmEtapaTerminal(item.etapa)).length;
  const parceirosAtivos = countCrmEtapa(CRM_ETAPA_TERMINAL_POSITIVA);

  const espirometrias = state.followupSummary?.espirometria?.total ?? null;
  const consultas = state.followupSummary?.consultas?.total ?? null;

  const pendentesDe = (grupo) =>
    grupo ? (grupo.atrasados || 0) + (grupo.hoje || 0) + (grupo.proximos7dias || 0) : null;
  const pendentesEspi = pendentesDe(state.followupSummary?.espirometria);
  const pendentesConsulta = pendentesDe(state.followupSummary?.consultas);
  const pendentesClinicas = pendentesDe(state.followupClinicasSummary?.clinicas);
  const parcelasPendentes = [pendentesEspi, pendentesConsulta, pendentesClinicas];
  const followupsPendentes = parcelasPendentes.some((v) => v !== null)
    ? parcelasPendentes.reduce((sum, v) => sum + (v || 0), 0)
    : null;

  const stats = [
    crmReportStatCard("crmRelLeadsAtivos", "Leads ativos", ativos, "no filtro atual"),
    crmReportStatCard("crmRelLeadsConv", "Leads convertidos", convertidos, "no filtro atual"),
    crmReportStatCard("crmRelTaxaConv", "Taxa de conversão", taxaConversao, "aproximada"),
    crmReportStatCard("crmRelClinicasCad", "Clínicas cadastradas", state.crm.length, "base total"),
    crmReportStatCard("crmRelClinicasProsp", "Clínicas em prospecção", emProspeccao, "ativas no funil"),
    crmReportStatCard("crmRelParceiros", "Parceiros ativos", parceirosAtivos, "parcerias fechadas"),
    crmReportStatCard("crmRelEspirometrias", "Espirometrias registradas", espirometrias, "base de follow-up"),
    crmReportStatCard("crmRelConsultas", "Consultas registradas", consultas, "base de follow-up"),
    crmReportStatCard("crmRelFollowupsPend", "Follow-ups pendentes", followupsPendentes, "atrasados + hoje + 7 dias"),
    crmReportStatCard("crmRelContatosB2B", "Contatos B2B vinculados", state.crmContatosB2B.length, "vinculados a clínicas"),
  ];

  container.innerHTML = stats.join("");
}

function renderCrmReportCharts(leads) {
  const grid = document.querySelector("#crmReportChartsGrid");
  if (!grid) return;

  // 1) Funil de leads por etapa (segue os filtros da página)
  const etapaCounts = {};
  leads.forEach((l) => {
    const e = l.etapa || l.status || "Desconhecido";
    etapaCounts[e] = (etapaCounts[e] || 0) + 1;
  });
  const etapaOrdenadas = LEAD_ETAPA_OPTIONS.filter((e) => etapaCounts[e] > 0);
  Object.keys(etapaCounts).forEach((e) => {
    if (!etapaOrdenadas.includes(e)) etapaOrdenadas.push(e);
  });
  const hasFunil = etapaOrdenadas.length > 0;

  // 2) Origem dos leads (segue os filtros da página)
  const origemCounts = {};
  leads.forEach((l) => {
    const o = l.origem || "Não informado";
    origemCounts[o] = (origemCounts[o] || 0) + 1;
  });
  const origemLabels = Object.keys(origemCounts);
  const hasOrigem = origemLabels.length > 0;

  // 3) Clínicas por etapa — domínio próprio do CRM B2B, não é filtrado por lead
  const clinicaEtapaCounts = {};
  state.crm.forEach((c) => {
    const e = c.etapa || "Outros";
    clinicaEtapaCounts[e] = (clinicaEtapaCounts[e] || 0) + 1;
  });
  const clinicaEtapaLabels = Object.keys(clinicaEtapaCounts);
  const hasClinicas = clinicaEtapaLabels.length > 0;

  // 4) Atendimentos por tipo — espirometria x consulta (resumo seguro)
  const espiTotal = state.followupSummary?.espirometria?.total;
  const consultaTotal = state.followupSummary?.consultas?.total;
  const hasAtendimentos = espiTotal != null && consultaTotal != null && (espiTotal + consultaTotal) > 0;

  // 5) Status de follow-up — pacientes + clínicas agregados (resumos seguros)
  const statusLabelsMap = { atrasados: "Atrasados", hoje: "Hoje", proximos7dias: "Próx. 7 dias", futuro: "Futuro", semData: "Sem data" };
  const statusKeys = Object.keys(statusLabelsMap);
  const statusTotals = {};
  [state.followupSummary?.espirometria, state.followupSummary?.consultas, state.followupClinicasSummary?.clinicas].forEach((grupo) => {
    if (!grupo) return;
    statusKeys.forEach((k) => { statusTotals[k] = (statusTotals[k] || 0) + (grupo[k] || 0); });
  });
  const hasStatus = Object.values(statusTotals).some((v) => v > 0);

  // 6) Conversões B2B / parceiros ativos (não segue os filtros de lead — visão geral B2B)
  const parceirosAtivosB2B = countCrmEtapa(CRM_ETAPA_TERMINAL_POSITIVA);
  const emProspeccaoB2B = state.crm.filter((c) => !isCrmEtapaTerminal(c.etapa)).length;
  const leadsB2BConvertidos = state.leads.filter((l) => isB2BLead(l) && isLeadConvertido(l)).length;
  const hasB2B = (parceirosAtivosB2B + emProspeccaoB2B + leadsB2BConvertidos) > 0;

  grid.innerHTML = `
    <div class="grid-two">
      ${crmReportChartPanel({ title: "Funil de leads por etapa", subtitle: "Distribuição no filtro atual", canvasId: "crmRelFunilChart", hasData: hasFunil, emptyMsg: "Nenhum lead no filtro selecionado" })}
      ${crmReportChartPanel({ title: "Origem dos leads", subtitle: "Canal de aquisição no filtro atual", canvasId: "crmRelOrigemChart", hasData: hasOrigem, emptyMsg: "Nenhum lead no filtro selecionado" })}
    </div>
    <div class="grid-two">
      ${crmReportChartPanel({ title: "Clínicas por etapa", subtitle: "Prospecção e parceria", canvasId: "crmRelClinicasChart", hasData: hasClinicas, emptyMsg: "Nenhuma clínica cadastrada" })}
      ${crmReportChartPanel({ title: "Atendimentos por tipo", subtitle: "Espirometria x consulta", canvasId: "crmRelAtendimentosChart", hasData: hasAtendimentos, emptyMsg: "Dado ainda não disponível" })}
    </div>
    <div class="grid-two">
      ${crmReportChartPanel({ title: "Status de follow-up", subtitle: "Pacientes e clínicas agregados", canvasId: "crmRelFollowupChart", hasData: hasStatus, emptyMsg: "Dado ainda não disponível" })}
      ${crmReportChartPanel({ title: "Conversões B2B / parceiros", subtitle: "Prospecção, conversão e parceria ativa", canvasId: "crmRelB2BChart", hasData: hasB2B, emptyMsg: "Dado ainda não disponível" })}
    </div>
  `;

  if (hasFunil) {
    createChart("crmRelFunil", "#crmRelFunilChart", {
      type: "bar",
      data: {
        // M23 — o valor canônico do banco é a chave de contagem; o eixo
        // mostra o rótulo humano, nunca o enum cru.
        labels: etapaOrdenadas.map(leadEtapaLabel),
        datasets: [{
          label: "Leads",
          data: etapaOrdenadas.map((e) => etapaCounts[e]),
          backgroundColor: etapaOrdenadas.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
          borderRadius: 10,
          borderSkipped: false,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} lead(s)` } }
        },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, ticks: { stepSize: 1, font: { size: 11 } }, border: { display: false } },
          x: { grid: { display: false }, ticks: { font: { size: 10 } }, border: { display: false } }
        }
      }
    });
  } else {
    destroyChart("crmRelFunil");
  }

  if (hasOrigem) {
    createChart("crmRelOrigem", "#crmRelOrigemChart", {
      type: "doughnut",
      data: {
        labels: origemLabels,
        datasets: [{
          data: Object.values(origemCounts),
          backgroundColor: origemLabels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, font: { size: 11 }, padding: 12 } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw}` } }
        }
      }
    });
  } else {
    destroyChart("crmRelOrigem");
  }

  if (hasClinicas) {
    createChart("crmRelClinicas", "#crmRelClinicasChart", {
      type: "bar",
      data: {
        labels: clinicaEtapaLabels.map(crmEtapaLabel),
        datasets: [{
          label: "Clínicas",
          data: Object.values(clinicaEtapaCounts),
          backgroundColor: clinicaEtapaLabels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
          borderRadius: 8,
          borderSkipped: false,
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} clínica(s)` } }
        },
        scales: {
          x: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, ticks: { stepSize: 1, font: { size: 11 } }, border: { display: false } },
          y: { grid: { display: false }, ticks: { font: { size: 11 } }, border: { display: false } }
        }
      }
    });
  } else {
    destroyChart("crmRelClinicas");
  }

  if (hasAtendimentos) {
    createChart("crmRelAtendimentos", "#crmRelAtendimentosChart", {
      type: "doughnut",
      data: {
        labels: ["Espirometria", "Consulta"],
        datasets: [{
          data: [espiTotal, consultaTotal],
          backgroundColor: [CHART_COLORS[0], CHART_COLORS[1]],
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, font: { size: 11 }, padding: 12 } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw}` } }
        }
      }
    });
  } else {
    destroyChart("crmRelAtendimentos");
  }

  if (hasStatus) {
    createChart("crmRelFollowup", "#crmRelFollowupChart", {
      type: "bar",
      data: {
        labels: statusKeys.map((k) => statusLabelsMap[k]),
        datasets: [{
          label: "Follow-ups",
          data: statusKeys.map((k) => statusTotals[k] || 0),
          backgroundColor: statusKeys.map((_, i) => CHART_COLORS_FUNNEL[i % CHART_COLORS_FUNNEL.length]),
          borderRadius: 10,
          borderSkipped: false,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} follow-up(s)` } }
        },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, ticks: { stepSize: 1, font: { size: 11 } }, border: { display: false } },
          x: { grid: { display: false }, ticks: { font: { size: 10 } }, border: { display: false } }
        }
      }
    });
  } else {
    destroyChart("crmRelFollowup");
  }

  if (hasB2B) {
    createChart("crmRelB2B", "#crmRelB2BChart", {
      type: "bar",
      data: {
        labels: ["Em prospecção", "Convertidos (leads)", "Parceiros ativos"],
        datasets: [{
          label: "B2B",
          data: [emProspeccaoB2B, leadsB2BConvertidos, parceirosAtivosB2B],
          backgroundColor: [CHART_COLORS[5], CHART_COLORS[2], CHART_COLORS[0]],
          borderRadius: 10,
          borderSkipped: false,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw}` } }
        },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, ticks: { stepSize: 1, font: { size: 11 } }, border: { display: false } },
          x: { grid: { display: false }, ticks: { font: { size: 11 } }, border: { display: false } }
        }
      }
    });
  } else {
    destroyChart("crmRelB2B");
  }
}

function renderCrmReportInsights(leads) {
  const container = document.querySelector("#crmReportInsights");
  if (!container) return;

  const insights = [];

  if (leads.length > 0) {
    const etapaCounts = {};
    leads.forEach((l) => {
      const e = l.etapa || l.status || "Desconhecido";
      etapaCounts[e] = (etapaCounts[e] || 0) + 1;
    });
    const etapaEntries = Object.entries(etapaCounts);
    const [topEtapa, topEtapaCount] = etapaEntries.reduce((best, cur) => (cur[1] > best[1] ? cur : best), etapaEntries[0]);
    insights.push({
      icon: "📌", type: "info",
      label: "Etapa predominante",
      title: `A maior parte dos leads está em "${topEtapa}"`,
      meta: `${topEtapaCount} de ${leads.length} lead(s) no filtro atual`
    });

    const origemCounts = {};
    leads.forEach((l) => {
      const o = l.origem || "Não informado";
      origemCounts[o] = (origemCounts[o] || 0) + 1;
    });
    const origemEntries = Object.entries(origemCounts);
    const [topOrigem, topOrigemCount] = origemEntries.reduce((best, cur) => (cur[1] > best[1] ? cur : best), origemEntries[0]);
    insights.push({
      icon: "📡", type: "neutral",
      label: "Canal principal",
      title: `O canal com mais leads é ${topOrigem}`,
      meta: `${topOrigemCount} lead(s) no filtro atual`
    });
  }

  if (state.crm.length > 0) {
    const emProspeccao = state.crm.filter((c) => !isCrmEtapaTerminal(c.etapa)).length;
    const parceirosAtivos = countCrmEtapa(CRM_ETAPA_TERMINAL_POSITIVA);
    insights.push({
      icon: "🏥", type: "neutral",
      label: "Clínicas",
      title: `Há ${emProspeccao} clínica(s) em prospecção e ${parceirosAtivos} parceira(s)`,
      meta: `${state.crm.length} clínica(s) cadastradas no total`
    });
  }

  if (state.crmContatosB2B.length > 0) {
    insights.push({
      icon: "🤝", type: "neutral",
      label: "Contatos B2B",
      title: `Existem ${state.crmContatosB2B.length} contato(s) B2B vinculados a clínicas`,
      meta: "Base de relacionamento institucional"
    });
  }

  if (insights.length === 0) {
    container.innerHTML = `<p class="crm-report-chart-empty">Dados ainda insuficientes para gerar leituras automáticas.</p>`;
    return;
  }

  container.innerHTML = insights.map((ins) => `
    <article class="crm-report-insight ins-${ins.type}">
      <span class="ins-icon">${ins.icon}</span>
      <div class="ins-body">
        <small>${escapeHtml(ins.label)}</small>
        <strong>${escapeHtml(ins.title)}</strong>
        <span>${escapeHtml(ins.meta)}</span>
      </div>
    </article>
  `).join("");
}

function countLeadEtapa(etapa) {
  return state.leads.filter((item) => (item.etapa || item.status || "") === etapa).length;
}

function countLeadServico(term) {
  return state.leads.filter((item) =>
    (item.servico_interesse || item.servico || "").toLowerCase().includes(term.toLowerCase())
  ).length;
}

function renderLeadStats() {
  const stats = [
    { key: "totalLeads",         label: "Total de leads",    value: state.leads.length,                                            hint: "base total" },
    { key: "leadsNovosContatos", label: "Novos contatos",    value: countLeadEtapa("Novo contato"),                                hint: "aguardando 1º resposta" },
    { key: "leadsEmConversa",    label: "Em conversa",       value: countLeadEtapa("Em conversa") + countLeadEtapa("Aguardando retorno"), hint: "diálogo ativo" },
    { key: "leadsAgendados",     label: "Agendados",         value: countLeadEtapa("Agendado"),                                    hint: "confirmados" },
    { key: "leadsConvertidos",   label: "Convertidos",       value: state.leads.filter(isLeadConvertido).length, hint: "migraram ao CRM ou parceria" },
    { key: "leadsSemResposta",   label: "Sem resposta",      value: countLeadEtapa("Não respondeu"),                               hint: "inativos" },
  ];

  const container = document.querySelector("#leadStats");
  if (!container) return;

  container.innerHTML = stats.map((item) => statCardHtml("lead-stat-card", item)).join("");
}

function renderLeadPipeline() {
  const stages = [
    { label: "Novo contato",         value: countLeadEtapa("Novo contato"),          hint: "aguarda 1ª resposta",  tone: "warning" },
    { label: "Em conversa",          value: countLeadEtapa("Em conversa"),            hint: "diálogo ativo",        tone: "" },
    { label: "Aguardando retorno",   value: countLeadEtapa("Aguardando retorno"),     hint: "aguardando o lead",    tone: "warning" },
    { label: "Agendado",             value: countLeadEtapa("Agendado"),               hint: "confirmar preparo",    tone: "success" },
    { label: "Não respondeu",        value: countLeadEtapa("Não respondeu"),          hint: "reativar ou encerrar", tone: "danger" },
    {
      label: "Convertido / parceiro ativo",
      value: state.leads.filter(isLeadConvertido).length,
      hint: "migrado ao CRM ou parceria",
      tone: "success",
      tip: "Leads que já viraram paciente, exame, consulta ou parceria B2B. Exemplo: Juan/Pastore aparece aqui como parceiro ativo."
    },
  ];

  const container = document.querySelector("#leadPipeline");
  if (!container) return;

  container.innerHTML = stages.map((stage) => {
    const cls = ["lead-stage", stage.tone, stage.tip ? "has-tip" : ""].filter(Boolean).join(" ");
    const tipAttrs = stage.tip
      ? ` tabindex="0" data-tip="${escapeHtml(stage.tip)}" aria-label="${escapeHtml(stage.label)}: ${stage.value}. ${escapeHtml(stage.tip)}"`
      : "";
    return `
    <div class="${cls}"${tipAttrs}>
      <small>${stage.label}</small>
      <strong>${stage.value}</strong>
      <span>${stage.hint}</span>
    </div>
  `;
  }).join("");
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

// Retorna o contato B2B vinculado a uma clínica (por clinica_id), se houver.
// Prefere um contato "Ativo"; nome só aparece se o arquivo privado local
// (data-private/crm-contatos-b2b.local.json) estiver disponível — o resumo
// seguro traz apenas cargo/papel/status.
function findContatoB2B(clinicaId) {
  if (!clinicaId || !Array.isArray(state.crmContatosB2B)) return null;
  const contatos = state.crmContatosB2B.filter((c) => c.clinica_id === clinicaId);
  if (contatos.length === 0) return null;
  return contatos.find((c) => c.status_relacionamento === "Ativo") || contatos[0];
}

// Filtro do CRM Clínicas é por meta-categoria (Ativos/Parceiros ativos/
// Perdidas/Todos), não mais por etapa exata — mesma ideia do filtro
// Ativos/Convertidos/Todos de Leads.
function renderCrmTable(filter = "Ativos") {
  const tbody = document.querySelector("#crmTable");
  const rows =
    filter === "Todos"           ? state.crm :
    filter === "Parceiros ativos" ? state.crm.filter((item) => item.etapa === CRM_ETAPA_TERMINAL_POSITIVA) :
    filter === "Perdidas"         ? state.crm.filter((item) => isCrmEtapaTerminalNegativa(item.etapa)) :
    /* Ativos */                    state.crm.filter((item) => !isCrmEtapaTerminal(item.etapa));

  const today = todayIso();

  if (rows.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" class="crm-empty">Nenhuma clínica cadastrada nesta lista.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = rows.map((item) => {
    const iso = parseDateIso(item.dataProximaAcao);
    const dataCls = iso && iso < today ? "data-atrasada" : iso === today ? "data-hoje" : "";
    const dataLabel = formatDateBr(item.dataProximaAcao);
    const contato = findContatoB2B(item.id);
    const contatoTag = contato
      ? `<br><small class="crm-contato-tag">👤 ${escapeHtml(contato.nome_contato || "Contato vinculado")}${contato.cargo ? " — " + escapeHtml(contato.cargo) : ""}${contato.papel ? " · " + escapeHtml(contato.papel) : ""}</small>`
      : "";
    const stageBtn = item.id
      ? `<button type="button" class="lead-stage-btn crm-stage-btn" data-clinica-id="${escapeHtml(item.id)}" data-tip="Mudar a etapa desta clínica" aria-label="Mudar etapa de ${escapeHtml(item.clinica)}">
           <svg width="13" height="13" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.5 2.5l3 3-9 9-3.6.6.6-3.6z"/></svg>
           Mudar etapa
         </button>`
      : `<span class="lead-stage-disabled" tabindex="0" data-tip="Esta clínica não tem clinica_id — não é possível editar pelo painel." aria-label="Ação indisponível: clínica sem identificador">—</span>`;
    return `
    <tr>
      <td><strong>${item.clinica}</strong>${contatoTag}</td>
      <td>${item.bairro}</td>
      <td><span class="crm-tipo">${item.tipo}</span></td>
      <td>${crmEtapaBadgeHtml(item.etapa)}</td>
      <td><span class="badge prio-${slug(item.prioridade)}">${item.prioridade}</span></td>
      <td>${item.proximaAcao}</td>
      <td>${dataLabel ? `<span class="crm-data ${dataCls}">${dataLabel}</span>` : ""}</td>
      <td>${item.responsavel}</td>
      <td class="lead-actions-cell">${stageBtn}</td>
    </tr>`;
  }).join("");
}

function resolveLeadWhatsApp(item) {
  if (item.whatsapp_url) return item.whatsapp_url;
  const phone = item.telefone_whatsapp || "";
  if (!phone) return "";
  const digits = String(phone).replace(/\D/g, "");
  if (!digits) return "";
  const country = digits.startsWith("55") ? digits : "55" + digits;
  return `https://wa.me/${country}`;
}

// Badges de etapa terminal (positiva ou negativa) do CRM Clínicas recebem
// tooltip explicando o que isso significa — mesmo padrão de
// LEAD_ETAPA_BADGE_TIP/leadEtapaBadgeHtml, aplicado ao domínio de clínicas.
// M23 — as dicas são indexadas pelo valor CANÔNICO do banco; o badge exibe
// sempre o rótulo humano, nunca o enum cru.
const CRM_ETAPA_BADGE_TIP = {
  "ativa": "Etapa terminal positiva: parceria já fechada. Não conta como atraso/alta prioridade da prospecção comum — aparece em Parceiros ativos.",
  "encerrada": "Etapa terminal: contato encerrado ou sem interesse. Fora do funil ativo.",
  "pausada": "Negociação pausada, aguardando retorno da clínica. Continua no funil ativo.",
};

function crmEtapaBadgeHtml(etapa) {
  const tip = CRM_ETAPA_BADGE_TIP[etapa];
  const rotulo = crmEtapaLabel(etapa);
  if (!tip) return `<span class="badge ${slug(etapa)}">${escapeHtml(rotulo)}</span>`;
  // title nativo (não o balão .has-tip::after) porque este badge fica dentro
  // de .table-wrap, que tem overflow:auto — um popup customizado ficaria
  // cortado ao rolar a tabela.
  return `<span class="badge ${slug(etapa)}" tabindex="0" title="${escapeHtml(tip)}" aria-label="${escapeHtml(rotulo)}. ${escapeHtml(tip)}">${escapeHtml(rotulo)}</span>`;
}

const LEAD_ETAPA_BADGE_TIP = {
  "convertido": "Lead que já virou atendimento, paciente ou parceria. Continua no histórico, mas sai da lista de ativos.",
  "perdido": "Etapa terminal: o lead não avançou. Fora da lista de ativos.",
  "aguardando_retomada": "Retomada agendada para uma data futura definida pelo operador.",
};

function leadEtapaBadgeHtml(etapa) {
  const tip = LEAD_ETAPA_BADGE_TIP[etapa];
  const rotulo = leadEtapaLabel(etapa);
  if (!tip) return `<span class="badge ${slug(etapa)}">${escapeHtml(rotulo)}</span>`;
  // Usa title nativo (não o balão .has-tip::after) porque este badge fica
  // dentro de .table-wrap, que tem overflow:auto — um popup customizado
  // ficaria cortado ao rolar a tabela. title escapa esse corte.
  return `<span class="badge ${slug(etapa)}" tabindex="0" title="${escapeHtml(tip)}" aria-label="${escapeHtml(rotulo)}. ${escapeHtml(tip)}">${escapeHtml(rotulo)}</span>`;
}

function renderLeadsTable() {
  const tbody = document.querySelector("#leadsTable");
  if (!tbody) return;

  const filter = state.leadsFilter || "Ativos";
  const rows = filter === "Todos"
    ? state.leads
    : filter === "Convertidos"
      ? state.leads.filter(isLeadConvertido)
      : state.leads.filter((item) => !isLeadConvertido(item));

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="crm-empty">Nenhum lead nesta lista.</td></tr>`;
    return;
  }

  const today = todayIso();

  tbody.innerHTML = rows.map((item) => {
    const nome      = item.nome || item.lead || item.lead_id || "—";
    const leadId    = item.lead_id || "";
    const servico   = item.servico_interesse || item.servico || "—";
    const etapa     = item.etapa || item.status || "—";
    // Resumo real não traz o texto de proxima_acao (texto livre, M2) — só o
    // booleano tem_proxima_acao; o texto completo fica na planilha privada.
    const acao      = item.proxima_acao || item.proximaAcao ||
      (item.tem_proxima_acao ? "Ação definida" : "—");
    const dataField = item.data_proxima_acao || item.dataProximaAcao || "";
    const iso       = parseDateIso(dataField);
    const dataCls   = iso && iso < today ? "data-atrasada" : iso === today ? "data-hoje" : "";
    const dataLabel = formatDateBr(dataField);
    const responsavel = item.responsavel || "—";

    const waUrl = resolveLeadWhatsApp(item);
    const waBtn = waUrl
      ? `<a class="fp-wa-btn" href="${escapeHtml(waUrl)}" target="_blank" rel="noopener" title="Abrir WhatsApp">WhatsApp</a>`
      : `<span class="fp-wa-disabled">—</span>`;

    const stageBtn = leadId
      ? `<button type="button" class="lead-stage-btn" data-lead-id="${escapeHtml(leadId)}" data-tip="Mudar a etapa deste lead" aria-label="Mudar etapa de ${escapeHtml(nome)}">
           <svg width="13" height="13" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.5 2.5l3 3-9 9-3.6.6.6-3.6z"/></svg>
           Mudar etapa
         </button>`
      : `<span class="lead-stage-disabled" tabindex="0" data-tip="Este lead não tem lead_id — não é possível editar pelo painel." aria-label="Ação indisponível: lead sem identificador">—</span>`;

    const linkBtn = leadId && isB2BLead(item) && !isLeadConvertido(item)
      ? `<button type="button" class="lead-link-btn" data-lead-id="${escapeHtml(leadId)}" data-tip="Vincular a uma clínica parceira" aria-label="Vincular a clínica: ${escapeHtml(nome)}">
           Vincular a clínica
         </button>`
      : "";

    return `
    <tr>
      <td>
        <strong>${escapeHtml(nome)}</strong>
        ${leadId ? `<br><small class="lead-id-tag">${escapeHtml(leadId)}</small>` : ""}
      </td>
      <td>${waBtn}</td>
      <td>${escapeHtml(servico)}</td>
      <td>${leadEtapaBadgeHtml(etapa)}</td>
      <td>${escapeHtml(acao)}</td>
      <td>${dataLabel ? `<span class="crm-data ${dataCls}">${dataLabel}</span>` : "—"}</td>
      <td>${escapeHtml(responsavel)}</td>
      <td class="lead-actions-cell">${stageBtn}${linkBtn}</td>
    </tr>`;
  }).join("");
}

// ─── Mudar etapa de um lead — modal + chamada ao Command Center ────────────

let _leadStageTrigger = null;

function closeLeadStageModal() {
  const overlay = document.querySelector(".lead-stage-overlay");
  if (overlay) overlay.remove();
  document.removeEventListener("keydown", _leadStageKeydown);
  if (_leadStageTrigger) {
    _leadStageTrigger.focus();
    _leadStageTrigger = null;
  }
}

function _leadStageKeydown(event) {
  if (event.key === "Escape") closeLeadStageModal();
}

function openLeadStageModal(lead, triggerBtn) {
  closeLeadStageModal();
  _leadStageTrigger = triggerBtn || null;

  const nome    = lead.nome || lead.lead || lead.lead_id || "Lead";
  const current = lead.etapa || lead.status || "";

  const options = LEAD_ETAPA_OPTIONS.map((op) =>
    `<option value="${escapeHtml(op)}"${op === current ? " selected" : ""}>${escapeHtml(leadEtapaLabel(op))}</option>`
  ).join("");

  const overlay = document.createElement("div");
  overlay.className = "lead-stage-overlay";
  overlay.innerHTML = `
    <div class="lead-stage-modal" role="dialog" aria-modal="true" aria-labelledby="leadStageTitle">
      <div class="lead-stage-modal-header">
        <div>
          <p class="eyebrow">Mudar etapa <span class="legacy-write-tag" title="A alteração é gravada no PostgreSQL pela API autenticada do Núcleo M15. Cadastros novos: Central de Cadastros.">Grava no PostgreSQL</span></p>
          <h4 id="leadStageTitle">${escapeHtml(nome)}</h4>
        </div>
        <button type="button" class="lead-stage-close" aria-label="Fechar">✕</button>
      </div>

      <p class="lead-stage-current">
        Etapa atual: <span class="badge ${slug(current || "—")}">${escapeHtml(leadEtapaLabel(current))}</span>
      </p>

      <label class="cc-label" for="leadStageSelect">Nova etapa</label>
      <select id="leadStageSelect" class="cc-select">${options}</select>

      <p class="lead-stage-convert-hint" hidden>
        Esta etapa converte o lead automaticamente para o CRM de atendimento
        e o remove desta lista de leads.
      </p>

      <div class="cc-result lead-stage-result" hidden></div>

      <div class="lead-stage-actions">
        <button type="button" class="lead-stage-cancel">Cancelar</button>
        <button type="button" class="lead-stage-save">Salvar</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  document.addEventListener("keydown", _leadStageKeydown);

  const select      = overlay.querySelector("#leadStageSelect");
  const convertHint = overlay.querySelector(".lead-stage-convert-hint");
  const result       = overlay.querySelector(".lead-stage-result");
  const saveBtn  = overlay.querySelector(".lead-stage-save");
  const cancelBtn = overlay.querySelector(".lead-stage-cancel");

  const toggleConvertHint = () => {
    convertHint.hidden = !LEAD_ETAPAS_CONVERSAO.includes(select.value);
  };
  toggleConvertHint();
  select.addEventListener("change", toggleConvertHint);

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeLeadStageModal();
  });
  overlay.querySelector(".lead-stage-close").addEventListener("click", closeLeadStageModal);
  cancelBtn.addEventListener("click", closeLeadStageModal);

  saveBtn.addEventListener("click", async () => {
    const novaEtapa = select.value;
    if (!novaEtapa || novaEtapa === current) {
      closeLeadStageModal();
      return;
    }

    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    select.disabled = true;
    saveBtn.textContent = "Salvando…";
    result.hidden = true;

    try {
      const resp = await atualizarEtapaLeadNoBanco(lead.lead_id, novaEtapa);

      result.className = "cc-result lead-stage-result cc-result-ok";
      result.textContent = resp.message || "Etapa atualizada com sucesso.";
      result.hidden = false;

      // "convertido" é etapa terminal do funil: o lead sai desta lista.
      applyLeadStageUpdate(lead.lead_id, novaEtapa, novaEtapa === "convertido");

      setTimeout(closeLeadStageModal, 1100);
    } catch (err) {
      result.className = "cc-result lead-stage-result cc-result-err";
      result.textContent = "Erro: " + err.message;
      result.hidden = false;
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      select.disabled = false;
      saveBtn.textContent = "Salvar";
    }
  });

  select.focus();
}

// Aplica o resultado da mudança de etapa localmente, sem recarregar a página.
// Se o lead foi convertido (migrou para os CRMs), ele sai da lista de Leads.
function applyLeadStageUpdate(leadId, novaEtapa, converted) {
  if (converted) {
    state.leads = state.leads.filter((l) => l.lead_id !== leadId);
  } else {
    const item = state.leads.find((l) => l.lead_id === leadId);
    if (item) item.etapa = novaEtapa;
  }

  renderLeadsTable();
  renderLeadStats();
  renderLeadPipeline();
  renderLeadsCharts();
  renderOverviewExtraCharts();
}

// ─── Mudar etapa de uma clínica CRM — modal + chamada ao Command Center ────────
// Mesmo padrão visual/UX do "Mudar etapa" de Leads (openLeadStageModal), com
// estado e handlers próprios para não interferir no modal de Leads.

let _crmStageTrigger = null;

function closeCrmStageModal() {
  const overlay = document.querySelector(".crm-stage-overlay");
  if (overlay) overlay.remove();
  document.removeEventListener("keydown", _crmStageKeydown);
  if (_crmStageTrigger) {
    _crmStageTrigger.focus();
    _crmStageTrigger = null;
  }
}

function _crmStageKeydown(event) {
  if (event.key === "Escape") closeCrmStageModal();
}

function openCrmStageModal(clinica, triggerBtn) {
  closeCrmStageModal();
  _crmStageTrigger = triggerBtn || null;

  const nome    = clinica.clinica || clinica.id || "Clínica";
  const current = clinica.etapa || "";

  const etapaOpcoes = [...CRM_ETAPAS_ATIVAS, ...CRM_ETAPAS_TERMINAIS];
  const options = etapaOpcoes.map((op) =>
    `<option value="${escapeHtml(op)}"${op === current ? " selected" : ""}>${escapeHtml(crmEtapaLabel(op))}</option>`
  ).join("");

  const overlay = document.createElement("div");
  overlay.className = "crm-stage-overlay lead-stage-overlay";
  overlay.innerHTML = `
    <div class="lead-stage-modal" role="dialog" aria-modal="true" aria-labelledby="crmStageTitle">
      <div class="lead-stage-modal-header">
        <div>
          <p class="eyebrow">Mudar etapa <span class="legacy-write-tag" title="A alteração é gravada no PostgreSQL pela API autenticada do Núcleo M15. Cadastros novos: Central de Cadastros.">Grava no PostgreSQL</span></p>
          <h4 id="crmStageTitle">${escapeHtml(nome)}</h4>
        </div>
        <button type="button" class="lead-stage-close" aria-label="Fechar">✕</button>
      </div>

      <p class="lead-stage-current">
        Etapa atual: <span class="badge ${slug(current || "—")}">${escapeHtml(crmEtapaLabel(current))}</span>
      </p>

      <label class="cc-label" for="crmStageSelect">Nova etapa</label>
      <select id="crmStageSelect" class="cc-select">${options}</select>

      <p class="lead-stage-convert-hint" hidden>
        Etapa terminal: esta clínica sai da lista Ativos. "Parceiro ativo" passa
        a aparecer em Parceiros ativos; as demais aparecem em Perdidas / sem interesse.
      </p>

      <div class="cc-result lead-stage-result" hidden></div>

      <div class="lead-stage-actions">
        <button type="button" class="lead-stage-cancel">Cancelar</button>
        <button type="button" class="lead-stage-save">Salvar</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  document.addEventListener("keydown", _crmStageKeydown);

  const select    = overlay.querySelector("#crmStageSelect");
  const stageHint = overlay.querySelector(".lead-stage-convert-hint");
  const result    = overlay.querySelector(".lead-stage-result");
  const saveBtn   = overlay.querySelector(".lead-stage-save");
  const cancelBtn = overlay.querySelector(".lead-stage-cancel");

  const toggleStageHint = () => {
    stageHint.hidden = !isCrmEtapaTerminal(select.value);
  };
  toggleStageHint();
  select.addEventListener("change", toggleStageHint);

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeCrmStageModal();
  });
  overlay.querySelector(".lead-stage-close").addEventListener("click", closeCrmStageModal);
  cancelBtn.addEventListener("click", closeCrmStageModal);

  saveBtn.addEventListener("click", async () => {
    const novaEtapa = select.value;
    if (!novaEtapa || novaEtapa === current) {
      closeCrmStageModal();
      return;
    }

    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    select.disabled = true;
    saveBtn.textContent = "Salvando…";
    result.hidden = true;

    try {
      const resp = await atualizarStatusParceiroNoBanco(clinica.id, novaEtapa);

      result.className = "cc-result lead-stage-result cc-result-ok";
      result.textContent = resp.message || "Etapa atualizada com sucesso.";
      result.hidden = false;

      applyCrmStageUpdate(clinica.id, novaEtapa);

      setTimeout(closeCrmStageModal, 1100);
    } catch (err) {
      result.className = "cc-result lead-stage-result cc-result-err";
      result.textContent = "Erro: " + err.message;
      result.hidden = false;
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      select.disabled = false;
      saveBtn.textContent = "Salvar";
    }
  });

  select.focus();
}

// Aplica o resultado da mudança de etapa localmente, sem recarregar a página,
// e rerenderiza tudo que depende de state.crm — stats, funil, follow-up B2B e
// a própria tabela (respeitando o filtro ativo, para a clínica sumir/aparecer
// na lista certa automaticamente conforme a nova etapa).
function applyCrmStageUpdate(clinicaId, novaEtapa) {
  const item = state.crm.find((c) => c.id === clinicaId);
  if (item) item.etapa = novaEtapa;

  renderCrmStats();
  renderCrmFunnelVisual();
  renderFollowupB2B();
  renderCrmTable(state.crmFilter || "Ativos");
}

// ─── Vincular lead B2B a clínica parceira (M23) ───────────────────────────────
// Até o M22 esta ação montava um registro B2B e o gravava no Google Sheets
// via Apps Script. No M23 não existe mais destino de escrita fora do
// PostgreSQL: a criação de contato B2B é a aba canônica "Contato B2B" da
// Central de Cadastros, que grava pela API autenticada.
//
// O painel não duplica esse formulário aqui. Ele leva o operador ao fluxo
// canônico já com o lead em contexto — um único lugar onde o contato B2B
// nasce, com validação, idempotência e auditoria do backend.

function abrirVinculoB2BCanonico(lead, triggerBtn) {
  const central = window.SoproCentral;
  if (!central || typeof central.open !== "function") {
    window.alert(
      "A Central de Cadastros não está disponível nesta sessão. "
      + "Entre no Núcleo Operacional para registrar o contato B2B."
    );
    return;
  }
  central.open("contato-b2b", {
    lead_codigo: lead.lead_id || "",
    origem_deep_link: "leads",
  });
  if (triggerBtn) triggerBtn.blur();
}


function renderSeoList() {
  const list = document.querySelector("#seoList");
  if (!list) return;
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

// ─── Marketing & SEO — dados reais vs demonstrativos ────────────────────────

function isMarketingReal() {
  const m = state.marketingSeo;
  return Boolean(
    m?.meta?.configured === true &&
    m?.meta?.safeToDisplay === true &&
    m?.meta?.containsPersonalData === false
  );
}

function renderMarketingSection() {
  if (isMarketingReal()) {
    renderMktHeader();
    renderMktKpiStrip();
    renderMktInsights();
    renderMktSeoList();
    renderMktPages();
    renderMktTrafficSources();
    renderMktFunnel();
    renderMktAlerts();
    renderMktTrendChart();
  } else {
    renderMktKpiStripDemo();
    renderSeoFocus();
    renderSeoList();
  }
}

/* ── Helpers de formatação para Marketing & SEO ─────────────────────────── */

const MKT_SOURCE_NAMES = {
  "(direct)": "Direto",
  "google": "Google",
  "bing": "Bing",
  "ig": "Instagram",
  "instagram": "Instagram",
  "facebook": "Facebook",
  "chatgpt.com": "ChatGPT",
  "youtube": "YouTube",
  "(not set)": "Não identificado",
};

const MKT_MEDIUM_NAMES = {
  "(none)": "",
  "organic": "Orgânico",
  "social": "Social",
  "ai-assistant": "AI",
  "cpc": "Pago",
  "email": "E-mail",
  "referral": "Referência",
  "newsletter": "E-mail",
  "(not set)": "",
};

const MKT_SOURCE_TIPS = {
  "(direct)/(none)": "Acesso direto. Sessões de pessoas que entraram no site digitando o endereço, usando favoritos ou por origem não identificada.",
  "google/organic": "Tráfego orgânico do Google. Sessões vindas de resultados não pagos da busca.",
  "(not set)/(not set)": "Origem não definida. O GA4 recebeu a sessão sem identificar corretamente a origem/mídia.",
  "chatgpt.com/ai-assistant": "Sessões vindas de assistentes de IA, como o ChatGPT, quando o usuário clicou no link do site.",
  "ig/social": "Tráfego social do Instagram.",
  "instagram/social": "Tráfego social do Instagram.",
  "facebook/social": "Tráfego social do Facebook.",
};

const MKT_INSIGHT_TIPS = {
  "success": "Palavra-chave com melhor combinação de relevância e desempenho no período.",
  "info":    "Termo com potencial de crescimento: bom volume de impressões, mas ainda com espaço para melhorar posição e cliques.",
  "warning": "Página ou termo com impressões relevantes, mas baixa taxa de cliques. Pode precisar de título, descrição ou snippet melhores.",
  "neutral": "Página com melhor desempenho orgânico no período, considerando impressões e/ou cliques.",
};

function mktCleanPath(rawPath) {
  let p = rawPath.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
  p = p.replace(/^\/+|\/+$/g, "");
  if (!p) return "Página inicial";
  p = p.replace(/-/g, " ");
  return p.charAt(0).toUpperCase() + p.slice(1);
}

function mktCleanSource(source, medium) {
  const src = MKT_SOURCE_NAMES[source] || source;
  const med = Object.prototype.hasOwnProperty.call(MKT_MEDIUM_NAMES, medium)
    ? MKT_MEDIUM_NAMES[medium]
    : medium;
  if (!med) return src;
  return `${src} · ${med}`;
}

function mktSourceTip(source, medium) {
  return MKT_SOURCE_TIPS[`${source}/${medium}`] || null;
}

function mktInclusiveDateCount(start, end) {
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) return null;
  return Math.floor((endMs - startMs) / 86400000) + 1;
}

/* ──────────────────────────────────────────────────────────────────────────── */

// Classes de estado do selo de Marketing — removidas todas antes de aplicar a
// atual, para o selo nunca acumular dois estados contraditórios.
const MKT_STATE_CLASSES = ["safe-label-real", "mf-fresh", "mf-refreshing", "mf-stale",
                           "mf-auth", "mf-credential", "mf-unavailable", "mf-error",
                           "mf-unknown"];

function renderMktHeader() {
  const m = state.marketingSeo;
  const aval = mfAvaliar(m);
  // "Atualizando" é estado de cliente: existe um pedido na fila do servidor e
  // ainda não chegou snapshot novo. Não substitui o diagnóstico da fonte —
  // apenas informa que uma nova leitura está a caminho.
  const atualizando = state.mktRefreshPendente === true;
  const rotulo = atualizando ? mfRotulo(MF_REFRESHING) : aval.rotulo;

  // Selo principal: estado de frescor honesto (nunca "dados reais" com
  // snapshot vencido, erro de auth ou fonte indisponível).
  const label = document.querySelector("#marketingDataLabel");
  if (label) {
    label.textContent = rotulo.label;
    MKT_STATE_CLASSES.forEach((c) => label.classList.remove(c));
    label.classList.add(rotulo.cls);
    if (!atualizando && aval.overall === MF_FRESH) label.classList.add("safe-label-real");
  }

  // Selos por fonte: GA4 e Search Console têm estados independentes.
  const badgesEl = document.querySelector("#mktSourceBadges");
  if (badgesEl) {
    const badges = [];
    for (const f of MF_FONTES) {
      const fonte = aval.fontes[f.key];
      if (!fonte || fonte.errorCode === "NOT_CONFIGURED") continue;
      const tip = fonte.errorMessageSafe
        || `Última sincronização OK: ${mfFormatarDataHora(fonte.lastSuccessAt)}`;
      badges.push(`<span class="mkt-src-badge ${f.key === "ga4" ? "src-ga4" : "src-sc"} ${fonte.rotulo.cls}"
        title="${escapeHtml(tip)}">${escapeHtml(f.nome)} · ${escapeHtml(fonte.rotulo.label)}</span>`);
    }
    badgesEl.innerHTML = badges.join("");
    badgesEl.hidden = badges.length === 0;
  }

  // Consulta do backend, intervalo realmente enviado ao Search Console e
  // atraso de publicação do Google são conceitos diferentes.
  const periodEl = document.querySelector("#mktPeriod");
  if (periodEl) {
    const partes = [];
    const scStatus = aval.fontes.searchConsole;
    partes.push(`Última consulta ao Google: ${mfFormatarDataHora(scStatus?.lastAttemptAt)}`);
    const scRequest = m?.searchConsole?.request;
    const dataStart = scRequest?.startDate || aval.period?.start;
    const dataEnd = scRequest?.endDate || aval.period?.end;
    if (dataStart && dataEnd) {
      partes.push(`Dados disponíveis no Google: ${dataStart} a ${dataEnd}`);
    }
    partes.push("Os dados do Search Console podem ter atraso de processamento.");
    const proxima = mfProximaAtualizacao(aval.lastAttemptAt);
    if (proxima) {
      partes.push(proxima.atrasada
        ? "Próxima atualização: a qualquer momento"
        : `Próxima atualização: ${mfFormatarDataHora(proxima.iso)}`);
    }
    periodEl.textContent = partes.join(" · ");
    periodEl.hidden = false;
  }

  // Faixa de aviso quando o estado não é fresh: explica que a última versão
  // válida está preservada e como atualizar (instrução, sem executar nada).
  const bannerEl = document.querySelector("#mktFreshnessBanner");
  if (bannerEl) {
    if (aval.overall === MF_FRESH) {
      bannerEl.hidden = true;
    } else if (atualizando) {
      bannerEl.innerHTML = `
        <strong>${escapeHtml(mfRotulo(MF_REFRESHING).label)}</strong> —
        o servidor recebeu o pedido e vai reler Search Console e GA4 na próxima
        execução do serviço agendado. Os números abaixo continuam sendo a última
        versão válida até a nova leitura terminar.`;
      bannerEl.hidden = false;
    } else {
      const msgs = [];
      for (const f of MF_FONTES) {
        const fonte = aval.fontes[f.key];
        if (fonte && fonte.errorMessageSafe) msgs.push(`${f.nome}: ${fonte.errorMessageSafe}`);
        else if (fonte && fonte.status === MF_STALE) msgs.push(`${f.nome}: dados vencidos (limite ${aval.staleAfterHours}h).`);
      }
      const ultimaOk = [aval.fontes.searchConsole, aval.fontes.ga4]
        .map((f) => mfParseIso(f?.lastSuccessAt))
        .filter((v) => v !== null)
        .sort((a, b) => b - a)[0];
      bannerEl.innerHTML = `
        <strong>${escapeHtml(aval.rotulo.label)}</strong> —
        ${msgs.length ? escapeHtml(msgs.join(" ")) : "Snapshot fora do limite de frescor."}
        ${ultimaOk ? `Mostrando a última versão válida (${escapeHtml(mfFormatarDataHora(new Date(ultimaOk).toISOString()))}).` : "Nenhuma versão válida disponível ainda."}
        <details class="mkt-howto">
          <summary>Como resolver</summary>
          ${aval.overall === MF_CREDENTIAL ? `
          <ol>
            ${aval.credentialKind === "service_account" ? `
            <li>A identidade e a credencial de leitura estão instaladas no
              servidor. Falta conceder acesso <b>somente leitura</b> nas
              propriedades: Search Console (usuário restrito) e GA4 (papel
              Viewer).</li>` : `
            <li>A identidade dedicada e seu arquivo de credencial precisam ser
              criados e instalados no servidor. Depois, conceda acesso
              <b>somente leitura</b> nas propriedades: Search Console (usuário
              restrito) e GA4 (papel Viewer).</li>`}
            <li>Quando identidade, credencial e concessões estiverem prontas, a
              atualização automática volta sozinha na próxima execução do
              serviço agendado.</li>
          </ol>
          <p>Nenhuma ação no navegador é necessária e nenhuma credencial é
             executada por ele.</p>` : `
          <ol>
            <li>Use o botão <b>Atualizar dados</b> acima: ele pede ao servidor
              uma nova leitura das fontes. Recarregar a página não busca dado
              novo no Google.</li>
            <li>Se o estado persistir, verifique no servidor:
              <code>python3 painel-soprolife/scripts/read-marketing-seo-adc.py --credential-check</code></li>
            <li>A atualização automática roda a cada 10 minutos pelo serviço
              agendado; a última versão válida nunca é apagada por uma falha.</li>
          </ol>
          <p>Nenhuma credencial é executada pelo navegador.</p>`}
        </details>`;
      bannerEl.hidden = false;
    }
  }

  const msgEl = document.querySelector("#mktRefreshMsg");
  if (msgEl) {
    if (atualizando) {
      msgEl.textContent = "Consultando o Google e aguardando o novo snapshot.";
      msgEl.hidden = false;
    } else if (state.mktRefreshResultado) {
      msgEl.textContent = state.mktRefreshResultado.success
        ? "Consulta concluída; novo snapshot carregado."
        : (state.mktRefreshResultado.errorMessageSafe
          || "Falha ao consultar o Google; último snapshot válido preservado.");
      msgEl.hidden = false;
    } else {
      msgEl.hidden = true;
    }
  }
}

/* ── Atualização manual das fontes de Marketing (M21) ──────────────────────
 * O botão ENFILEIRA uma atualização no servidor; o navegador não executa
 * script nem toca credencial. Recarregar a página continua sendo apenas
 * recarregar a página — nunca é anunciado como atualização de fonte.
 */
const MKT_REFRESH_URL = "/marketing/refresh";
const MKT_REFRESH_STATUS_URL = "/marketing/refresh-status";
const MKT_POLL_INTERVALO_MS = 20000;
// O timer roda a cada 10 min; 40 tentativas cobrem uma janela completa com
// margem, sem polling infinito (~13 min).
const MKT_POLL_MAX = 40;

async function pedirAtualizacaoMarketing() {
  const btn = document.querySelector("#mktRefreshBtn");
  const msgEl = document.querySelector("#mktRefreshMsg");
  if (state.mktRefreshPendente || state.mktRefreshPolling) return;
  if (btn) btn.disabled = true;
  try {
    const m15 = window.SoproM15;
    if (!m15 || !m15.hasToken()) {
      throw new Error("Entre no Núcleo para pedir uma atualização.");
    }
    const body = await m15.api(MKT_REFRESH_URL, { method: "POST" });
    if (body.ok !== true) throw new Error("Pedido não confirmado.");
    state.mktRefreshPendente = true;
    state.mktRefreshResultado = null;
    state.mktRefreshRequestId = body.requestId || null;
    state.mktGeneratedAtNoPedido = state.marketingSeo?.meta?.generatedAt || null;
    renderMktHeader();
    acompanharAtualizacaoMarketing();
  } catch (err) {
    if (msgEl) {
      msgEl.textContent = "Não foi possível pedir a atualização agora. "
        + "O serviço agendado continua rodando normalmente.";
      msgEl.hidden = false;
    }
    if (btn) btn.disabled = false;
  }
}

function acompanharAtualizacaoMarketing() {
  if (state.mktRefreshPolling) return;
  state.mktRefreshPolling = true;
  let tentativas = 0;
  const parar = (resultado) => {
    state.mktRefreshPendente = false;
    state.mktRefreshPolling = false;
    state.mktRefreshResultado = resultado || null;
    state.mktRefreshRequestId = null;
    const btn = document.querySelector("#mktRefreshBtn");
    if (btn) btn.disabled = false;
    renderMarketingSection();
  };
  const tick = async () => {
    tentativas += 1;
    let status = null;
    try {
      const m15 = window.SoproM15;
      status = m15 && m15.hasToken()
        ? await m15.api(MKT_REFRESH_STATUS_URL)
        : null;
    } catch (err) {
      status = null; // rede instável não encerra o acompanhamento
    }
    const requestCorreto = !state.mktRefreshRequestId
      || !status?.requestId
      || status.requestId === state.mktRefreshRequestId;
    if (status?.ok === true && status.state === "completed" && requestCorreto) {
      // O backend terminou: busca o snapshot persistido com cache-buster novo,
      // inclusive no caso degradado (dados antigos + estado de falha).
      const novo = await loadOptionalJson(
        "./data/marketing-seo.local.json",
        `${Date.now().toString(36)}-${tentativas}`
      );
      if (novo) state.marketingSeo = novo;
      parar({
        success: status.success === true,
        degraded: status.degraded === true,
        errorMessageSafe: status.errorMessageSafe || null,
      });
      return;
    }
    if (tentativas >= MKT_POLL_MAX) {
      parar({
        success: false,
        degraded: true,
        errorMessageSafe: "A consulta não terminou no prazo; o último snapshot válido permanece exibido.",
      });
      return;
    }
    window.setTimeout(tick, MKT_POLL_INTERVALO_MS);
  };
  window.setTimeout(tick, MKT_POLL_INTERVALO_MS);
}

function renderMktKpiStrip() {
  const sc  = state.marketingSeo?.searchConsole?.totals;
  const ga4 = state.marketingSeo?.ga4?.totals;
  const container = document.querySelector("#mktKpiStrip");
  if (!container) return;

  // Configurado porém sem NENHUM dado sincronizado: ausência é diferente de
  // zero — mostra estado vazio honesto, nunca números demonstrativos nem 0.
  if (!sc && !ga4) {
    container.innerHTML = `
      <article class="mkt-kpi-card mkt-kpi-empty">
        <span class="mkt-kpi-label">Sem dados sincronizados</span>
        <strong class="mkt-kpi-value">—</strong>
        <small class="mkt-kpi-src">Ver aviso de frescor acima</small>
      </article>`;
    return;
  }

  const kpis = [];
  if (sc) {
    kpis.push(
      { label: "Impressões", value: sc.impressions.toLocaleString("pt-BR"), src: "sc", tip: "Vezes que o site apareceu no Google Search." },
      { label: "Cliques", value: sc.clicks.toLocaleString("pt-BR"), src: "sc", tip: "Cliques orgânicos vindos do Google." },
      { label: "CTR", value: (sc.ctr * 100).toFixed(1) + "%", src: "sc", tip: "CTR = Click-Through Rate, ou taxa de cliques. Mostra a porcentagem de impressões que viraram cliques no Google Search." },
      { label: "Pos. média", value: sc.avgPosition.toFixed(1), src: "sc", tip: "Posição média no ranking do Google Search." },
    );
  }
  if (ga4) {
    kpis.push(
      { label: "Usuários", value: ga4.users.toLocaleString("pt-BR"), src: "ga4", tip: "Visitantes únicos no período (GA4)." },
      { label: "Sessões", value: ga4.sessions.toLocaleString("pt-BR"), src: "ga4", tip: "Total de visitas ao site no período (GA4)." },
      { label: "Visualizações", value: ga4.pageviews.toLocaleString("pt-BR"), src: "ga4", tip: "Total de páginas visualizadas no período (GA4)." },
    );
  }

  container.innerHTML = kpis.map((k) => `
    <article class="mkt-kpi-card kpi-${k.src}${k.tip ? " mkt-tip" : ""}"${mktTipAttrs(k.tip, k.label, k.value)}>
      <span class="mkt-kpi-label">${escapeHtml(k.label)}</span>
      <strong class="mkt-kpi-value">${escapeHtml(k.value)}</strong>
      <small class="mkt-kpi-src">${k.src === "sc" ? "Search Console" : "GA4"}</small>
    </article>
  `).join("");
}

// Anexa tabindex/data-tip/aria-label a cards que já carregam seu próprio texto de tooltip
// (usado nas seções que constroem o texto por item em vez de por chave central).
function mktTipAttrs(tip, label, value) {
  if (!tip) return "";
  const safeTip = escapeHtml(tip);
  const ariaBits = [label, value]
    .filter((v) => v !== undefined && v !== null && String(v) !== "")
    .map((v) => escapeHtml(String(v)));
  const aria = ariaBits.length ? `${ariaBits.join(": ")}. ${safeTip}` : safeTip;
  return ` tabindex="0" data-tip="${safeTip}" aria-label="${aria}"`;
}

function renderMktKpiStripDemo() {
  const container = document.querySelector("#mktKpiStrip");
  if (!container || !state.marketing?.seo) return;

  const totalImp = state.marketing.seo.reduce((s, i) => s + i.impressoes, 0);
  const totalClk = state.marketing.seo.reduce((s, i) => s + i.cliques, 0);
  const avgPos   = state.marketing.seo.reduce((s, i) => s + i.posicao, 0) / state.marketing.seo.length;
  const topTerm  = state.marketing.seo.reduce((b, i) => i.cliques > b.cliques ? i : b, state.marketing.seo[0]);

  const kpis = [
    { label: "Impressões", value: totalImp, src: "sc", tip: "Dados demonstrativos de desempenho orgânico do site — vezes que o site apareceria nos resultados de busca do Google." },
    { label: "Cliques estimados", value: totalClk, src: "sc", tip: "Estimativa demonstrativa de cliques orgânicos vindos da busca do Google." },
    { label: "Posição média", value: avgPos.toFixed(1), src: "sc", tip: "Posição média demonstrativa no ranking de busca do Google." },
    { label: "Melhor termo", value: topTerm.termo, src: "sc", tip: "Termo de busca com melhor desempenho de cliques no período demonstrativo." },
  ];

  container.innerHTML = kpis.map((k) => `
    <article class="mkt-kpi-card kpi-${k.src}${k.tip ? " mkt-tip" : ""}"${mktTipAttrs(k.tip, k.label, k.value)}>
      <span class="mkt-kpi-label">${k.label}</span>
      <strong class="mkt-kpi-value">${k.value}</strong>
      <small class="mkt-kpi-src">Demonstrativo</small>
    </article>
  `).join("");
}

function renderMktInsights() {
  const sc = state.marketingSeo?.searchConsole;
  const container = document.querySelector("#mktInsights");
  if (!container) return;

  const insights = [];

  if (sc?.topQueries?.length) {
    const best = sc.topQueries.reduce((b, q) => q.clicks > b.clicks ? q : b, sc.topQueries[0]);
    if (best.clicks > 0) {
      insights.push({ icon: "⭐", type: "success", label: "Termo estrela", title: best.query, meta: `${best.clicks} cliques · pos. ${best.avgPosition.toFixed(1)}` });
    }
    const opp = sc.topQueries.find((q) => q.avgPosition >= 5 && q.avgPosition <= 20 && q.impressions >= 8);
    if (opp) {
      insights.push({ icon: "🎯", type: "info", label: "Oportunidade", title: opp.query, meta: `pos. ${opp.avgPosition.toFixed(1)} · ${opp.impressions} impr.` });
    }
    const lowCtr = sc.topQueries.find((q) => q.impressions >= 5 && q.ctr < 0.02 && q !== opp);
    if (lowCtr) {
      insights.push({ icon: "⚠", type: "warning", label: "CTR baixo", title: lowCtr.query, meta: `${lowCtr.impressions} impr. · CTR ${(lowCtr.ctr * 100).toFixed(1)}%` });
    }
  }

  if (sc?.topPages?.length) {
    const top = sc.topPages[0];
    const name = mktCleanPath(top.page);
    insights.push({ icon: "📄", type: "neutral", label: "Melhor página", title: name, meta: `${top.impressions} impr. · ${top.clicks} cliques` });
  }

  if (insights.length === 0) { container.hidden = true; return; }

  container.innerHTML = insights.slice(0, 4).map((ins) => {
    const tip = MKT_INSIGHT_TIPS[ins.type] || null;
    return `
    <article class="mkt-insight ins-${ins.type}${tip ? " mkt-tip" : ""}"${mktTipAttrs(tip, ins.label, ins.title)}>
      <span class="ins-icon">${ins.icon}</span>
      <div class="ins-body">
        <small>${escapeHtml(ins.label)}</small>
        <strong>${escapeHtml(ins.title)}</strong>
        <span>${escapeHtml(ins.meta)}</span>
      </div>
    </article>
  `;
  }).join("");
  container.hidden = false;
}

function mktMiniRow(labelHtml, chips, barVal, barMax, tip) {
  const pct = Math.max(4, Math.round((barVal / barMax) * 100));
  const extraCls = tip ? " mkt-tip" : "";
  return `<div class="mkt-mini-row${extraCls}"${mktTipAttrs(tip, "", "")}>
    <div class="mkt-mini-row-top">
      <span class="mkt-mini-term">${labelHtml}</span>
      <span class="mkt-mini-chips">${chips.map((c) => `<span>${c}</span>`).join("")}</span>
    </div>
    <div class="mkt-bar-wrap"><div class="mkt-bar" style="width:${pct}%"></div></div>
  </div>`;
}

function renderMktSeoList() {
  const queries = state.marketingSeo?.searchConsole?.topQueries;
  const list    = document.querySelector("#seoList");
  const sub     = document.querySelector("#seoListSubtitle");
  const btn     = document.querySelector("#seoMoreBtn");
  if (!list) return;

  if (!queries?.length) { renderSeoList(); return; }

  if (sub) sub.textContent = "Search Console · dados reais";

  const maxImpr = Math.max(...queries.map((q) => q.impressions), 1);

  function buildRows(items) {
    return items.map((q) => mktMiniRow(
      escapeHtml(q.query),
      [
        `${q.impressions.toLocaleString("pt-BR")} impr.`,
        `${q.clicks} cliques`,
        `<span class="tt" data-tip="Click Through Rate — percentual de cliques em relação às impressões.">CTR</span> ${(q.ctr * 100).toFixed(1)}%`,
      ],
      q.impressions, maxImpr
    )).join("");
  }

  const top5 = queries.slice(0, 5);
  const rest  = queries.slice(5);

  list.innerHTML = buildRows(top5);

  if (rest.length > 0 && btn) {
    btn.hidden = false;
    btn.textContent = `Ver mais ${rest.length} termos ↓`;
    let expanded = false;
    btn.onclick = () => {
      expanded = !expanded;
      list.innerHTML = expanded ? buildRows(top5) + buildRows(rest) : buildRows(top5);
      btn.textContent = expanded ? "Recolher ↑" : `Ver mais ${rest.length} termos ↓`;
    };
  }
}

function renderMktPages() {
  const ga4Pages = state.marketingSeo?.ga4?.topPages;
  const scPages  = state.marketingSeo?.searchConsole?.topPages;

  const ga4El = document.querySelector("#ga4TopPages");
  if (ga4El) {
    if (ga4Pages?.length) {
      const maxPv = Math.max(...ga4Pages.map((p) => p.pageviews), 1);
      ga4El.innerHTML = ga4Pages.slice(0, 5).map((p) => {
        const name = mktCleanPath(p.page);
        return mktMiniRow(escapeHtml(name), [`${p.pageviews.toLocaleString("pt-BR")} views`, `${p.users} usuários`], p.pageviews, maxPv);
      }).join("");
    } else {
      ga4El.innerHTML = `<p class="mkt-empty">GA4 não configurado.</p>`;
    }
  }

  const scEl = document.querySelector("#scTopPages");
  if (scEl) {
    if (scPages?.length) {
      const maxImpr = Math.max(...scPages.map((p) => p.impressions), 1);
      scEl.innerHTML = scPages.slice(0, 5).map((p) => {
        const name = mktCleanPath(p.page);
        return mktMiniRow(escapeHtml(name), [`${p.impressions.toLocaleString("pt-BR")} impr.`, `${p.clicks} cliques`, `CTR ${(p.ctr * 100).toFixed(1)}%`], p.impressions, maxImpr);
      }).join("");
    } else {
      scEl.innerHTML = `<p class="mkt-empty">Search Console não configurado.</p>`;
    }
  }
}

function renderMktTrafficSources() {
  const sources = state.marketingSeo?.ga4?.trafficSources;
  const subEl   = document.querySelector("#channelsSubtitle");
  const listEl  = document.querySelector("#trafficSourceList");
  if (!sources?.length || !listEl) return;

  if (subEl) subEl.textContent = "GA4 · dados reais";

  const maxSess = Math.max(...sources.map((s) => s.sessions), 1);
  listEl.innerHTML = sources.slice(0, 6).map((s) => {
    const label = mktCleanSource(s.source, s.medium);
    const tip   = mktSourceTip(s.source, s.medium);
    return mktMiniRow(escapeHtml(label), [`${s.sessions.toLocaleString("pt-BR")} sessões`], s.sessions, maxSess, tip);
  }).join("");
  listEl.hidden = false;
}

function renderMktFunnel() {
  const events = state.marketingSeo?.ga4?.events;
  const panel  = document.querySelector("#marketingFunnelPanel");
  if (!panel) return;

  if (!events?.length) { panel.hidden = true; return; }

  panel.hidden = false;
  const el = document.querySelector("#marketingFunnel");
  if (!el) return;

  const maxCount = Math.max(...events.map((e) => e.count), 1);
  el.innerHTML = events.slice(0, 5).map((e) =>
    mktMiniRow(escapeHtml(e.event), [`${e.count.toLocaleString("pt-BR")} ocorr.`], e.count, maxCount)
  ).join("");
}

function renderMktAlerts() {
  const sc       = state.marketingSeo?.searchConsole;
  const titleEl  = document.querySelector("#seoFocusTitle");
  const subEl    = document.querySelector("#seoFocusSubtitle");
  const alertsEl = document.querySelector("#seoAlerts");
  const focusEl  = document.querySelector("#seoFocus");

  if (titleEl) titleEl.textContent = "Alertas e oportunidades de SEO";
  if (subEl)   subEl.textContent   = "Gerado automaticamente dos dados reais";
  if (focusEl) focusEl.innerHTML   = "";
  if (!alertsEl) return;

  const alerts = [];

  if (sc?.topQueries) {
    sc.topQueries.forEach((q) => {
      if (q.impressions >= 5 && q.ctr < 0.02) {
        alerts.push({
          type: "warning", icon: "📉",
          title: `CTR baixo: "${q.query}"`,
          text: `${q.impressions.toLocaleString("pt-BR")} impressões · CTR ${(q.ctr * 100).toFixed(1)}% · pos. ${q.avgPosition.toFixed(1)}`,
          action: "Melhorar título e meta description para aumentar cliques.",
        });
      }
    });
    sc.topQueries.forEach((q) => {
      if (q.avgPosition >= 5 && q.avgPosition <= 20 && q.impressions >= 8) {
        alerts.push({
          type: "info", icon: "🎯",
          title: `Subir para top 5: "${q.query}"`,
          text: `Posição média ${q.avgPosition.toFixed(1)} · ${q.impressions.toLocaleString("pt-BR")} impressões`,
          action: "Reforçar conteúdo e backlinks para essa palavra-chave.",
        });
      }
    });
  }

  if (sc?.topPages) {
    sc.topPages.forEach((p) => {
      if (p.impressions >= 100 && p.ctr < 0.03) {
        const name = p.page.replace(/^https?:\/\/[^/]+/, "").split("?")[0] || p.page;
        alerts.push({
          type: "warning", icon: "📄",
          title: `Página com CTR baixo: ${name}`,
          text: `${p.impressions.toLocaleString("pt-BR")} impressões · CTR ${(p.ctr * 100).toFixed(1)}%`,
          action: "Revisar snippet e adicionar structured data.",
        });
      }
    });
  }

  if (alerts.length === 0) {
    alertsEl.innerHTML = `<p class="mkt-empty">Nenhum alerta identificado — bom desempenho!</p>`;
  } else {
    alertsEl.innerHTML = alerts.slice(0, 6).map((a) => `
      <article class="mkt-alert alert-${a.type}">
        <div class="mkt-alert-top"><span>${a.icon}</span><strong>${escapeHtml(a.title)}</strong></div>
        <p>${escapeHtml(a.text)}</p>
        <small>${escapeHtml(a.action)}</small>
      </article>
    `).join("");
  }
  alertsEl.hidden = false;
}

function renderMktTrendChart() {
  const byDate = state.marketingSeo?.searchConsole?.byDate;
  const panel  = document.querySelector("#mktTrendPanel");
  if (!byDate?.length || !panel) return;

  panel.hidden = false;
  const subtitle = document.querySelector("#mktTrendSubtitle");
  if (subtitle) {
    const req = state.marketingSeo?.searchConsole?.request;
    const dias = mktInclusiveDateCount(req?.startDate, req?.endDate);
    subtitle.textContent = dias === 28
      ? "Search Console · 28 dias"
      : "Search Console · por dia";
  }
  const canvas = document.querySelector("#mktTrendChart");

  createChart("mktTrend", "#mktTrendChart", {
    type: "line",
    data: {
      labels: byDate.map((d) => d.date.slice(5)),
      datasets: [{
        label: "Impressões",
        data: byDate.map((d) => d.impressions),
        borderColor: "rgba(29, 183, 166, .95)",
        backgroundColor: chartGradient(canvas, 29, 183, 166, 0.22),
        fill: true,
        pointRadius: 0,
        pointHoverRadius: 5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "rgba(109,123,138,.07)" },
          ticks: { font: { size: 10 }, maxTicksLimit: 4 },
          border: { display: false }
        },
        x: {
          grid: { display: false },
          ticks: { font: { size: 10 }, maxTicksLimit: 7 },
          border: { display: false }
        },
      },
    },
  });
}

function renderCharts() {
  const weeklyCanvas = document.querySelector("#weeklyChart");
  createChart("weekly", "#weeklyChart", {
    type: "line",
    data: {
      labels: state.resumo.evolucaoSemanal.labels,
      datasets: [
        {
          label: "Leads",
          data: state.resumo.evolucaoSemanal.leads,
          borderColor: "rgba(29, 183, 166, .95)",
          backgroundColor: chartGradient(weeklyCanvas, 29, 183, 166, 0.28),
          fill: true,
        },
        {
          label: "Agendamentos",
          data: state.resumo.evolucaoSemanal.agendamentos,
          borderColor: "rgba(99, 102, 241, .95)",
          backgroundColor: chartGradient(weeklyCanvas, 99, 102, 241, 0.15),
          fill: true,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, padding: 16 } }
      },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, border: { display: false } },
        x: { grid: { display: false }, border: { display: false } }
      }
    }
  });

  createChart("funnel", "#funnelChart", {
    type: "bar",
    data: {
      labels: state.resumo.funilClinicas.labels,
      datasets: [
        {
          label: "Clínicas",
          data: state.resumo.funilClinicas.values,
          backgroundColor: CHART_COLORS_FUNNEL,
          borderRadius: 14,
          borderSkipped: false,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, border: { display: false } },
        x: { grid: { display: false }, border: { display: false } }
      }
    }
  });

  const ga4Sources = state.marketingSeo?.ga4?.trafficSources;
  const channelsData = ga4Sources?.length
    ? {
        labels: ga4Sources.slice(0, 8).map((s) => `${s.source}/${s.medium}`),
        values: ga4Sources.slice(0, 8).map((s) => s.sessions),
      }
    : state.marketing.canais;

  // Sem dados reais do GA4, o donut cai no demo — rotula honestamente para
  // nunca parecer dado atual (contrato de frescor M14.3A.1).
  const channelsSubEl = document.querySelector("#channelsSubtitle");
  if (channelsSubEl && !ga4Sources?.length) {
    channelsSubEl.textContent = isMarketingReal()
      ? "Demonstrativo — GA4 sem dados sincronizados"
      : "Canais de aquisição";
  }

  createChart("channels", "#channelsChart", {
    type: "doughnut",
    data: {
      labels: channelsData.labels,
      datasets: [
        {
          label: ga4Sources?.length ? "Sessões por origem (GA4)" : "Origem dos contatos",
          data: channelsData.values,
          backgroundColor: CHART_COLORS,
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "70%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, padding: 14 }
        }
      }
    }
  });

  renderOverviewExtraCharts();
}

function renderOverviewExtraCharts() {
  // Leads por etapa — donut
  const leadsByEtapa = {};
  state.leads.forEach((l) => {
    const e = l.etapa || l.status || "Desconhecido";
    leadsByEtapa[e] = (leadsByEtapa[e] || 0) + 1;
  });
  const etapaLabels = Object.keys(leadsByEtapa);
  if (etapaLabels.length > 0) {
    createChart("leadsEtapa", "#leadsEtapaChart", {
      type: "doughnut",
      data: {
        labels: etapaLabels,
        datasets: [{
          data: Object.values(leadsByEtapa),
          backgroundColor: CHART_COLORS.slice(0, etapaLabels.length),
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, font: { size: 11 }, padding: 12 } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${ctx.raw} lead(s)` } }
        }
      }
    });
  }

  // Clínicas B2B por etapa — barra horizontal (etapas dinâmicas da base real)
  const crmByEtapa = {};
  state.crm.forEach((c) => {
    const e = c.etapa || "Outros";
    crmByEtapa[e] = (crmByEtapa[e] || 0) + 1;
  });
  const crmEtapaLabels = Object.keys(crmByEtapa);
  const crmEtapaValues = Object.values(crmByEtapa);
  if (crmEtapaLabels.length > 0) {
    createChart("crmEtapa", "#crmEtapaChart", {
      type: "bar",
      data: {
        labels: crmEtapaLabels,
        datasets: [{
          label: "Clínicas",
          data: crmEtapaValues,
          backgroundColor: CHART_COLORS.slice(0, crmEtapaLabels.length),
          borderRadius: 8,
          borderSkipped: false,
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.raw} clínica(s)` } }
        },
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: "rgba(109,123,138,.07)" },
            ticks: { stepSize: 1, font: { size: 11 } },
            border: { display: false }
          },
          y: { grid: { display: false }, ticks: { font: { size: 11 } }, border: { display: false } }
        }
      }
    });
  }
}

function renderLeadsCharts() {
  // Origem dos leads — donut
  const leadsByOrigem = {};
  state.leads.forEach((l) => {
    const o = l.origem || "Não informado";
    leadsByOrigem[o] = (leadsByOrigem[o] || 0) + 1;
  });
  const origemLabels = Object.keys(leadsByOrigem);
  if (origemLabels.length > 0) {
    createChart("leadsOrigem", "#leadsOrigemChart", {
      type: "doughnut",
      data: {
        labels: origemLabels,
        datasets: [{
          data: Object.values(leadsByOrigem),
          backgroundColor: CHART_COLORS.slice(0, origemLabels.length),
          borderWidth: 3,
          borderColor: "#f3f7fb",
          hoverOffset: 8,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, font: { size: 11 }, padding: 12 } },
          tooltip: {
            callbacks: {
              // Canal + quantidade na primeira linha, lembrete de que isto não
              // é a etapa comercial na segunda — evita a confusão de achar que
              // este gráfico mostra quem já converteu.
              label: (ctx) => [
                ` ${ctx.label}: ${ctx.raw} lead(s)`,
                "Canal de entrada — não indica se o lead foi convertido."
              ]
            }
          }
        }
      }
    });
  }

  // Funil comercial — barras verticais por etapa atual. "Convertido / parceiro
  // ativo" usa a mesma regra de isLeadConvertido() do card "Convertidos" e do
  // filtro da tabela (Ativos/Convertidos/Todos), para não haver dois números
  // diferentes de "convertido" na mesma tela.
  const FUNIL_ETAPA_EXPLICACAO = {
    "Convertido / parceiro ativo": "Inclui pacientes convertidos e parceiros B2B (ex.: Juan/Pastore)."
  };
  const etapasFunil = [
    ["Novo contato", countLeadEtapa("Novo contato")],
    ["Em conversa",  countLeadEtapa("Em conversa")],
    ["Aguardando",   countLeadEtapa("Aguardando retorno")],
    ["Agendado",     countLeadEtapa("Agendado")],
    ["Sem resposta", countLeadEtapa("Não respondeu")],
    ["Convertido / parceiro ativo", state.leads.filter(isLeadConvertido).length]
  ];
  createChart("leadsFunil", "#leadsFunilChart", {
    type: "bar",
    data: {
      labels: etapasFunil.map((e) => e[0]),
      datasets: [{
        label: "Leads",
        data: etapasFunil.map((e) => e[1]),
        backgroundColor: CHART_COLORS.slice(0, etapasFunil.length),
        borderRadius: 10,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const lines = [` ${ctx.raw} lead(s)`];
              const extra = FUNIL_ETAPA_EXPLICACAO[ctx.label];
              if (extra) lines.push(extra);
              return lines;
            }
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          grid: { color: "rgba(109,123,138,.07)" },
          ticks: { stepSize: 1, font: { size: 11 } },
          border: { display: false }
        },
        x: { grid: { display: false }, ticks: { font: { size: 10 } }, border: { display: false } }
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
      // M21 — Automação CRM é destino de sidebar: renderiza ao abrir e busca
      // os contadores reais do CRM quando existe sessão.
      if (button.dataset.section === "automacoes-crm") {
        renderAutomacaoCrmSection();
      }
    });
  });

  // Hub de navegação do Painel Geral (M13.4B): cada card dispara o clique do
  // item correspondente da sidebar — mesma troca de seção, sem lógica nova.
  document.querySelectorAll(".nav-hub-card[data-section]").forEach((card) => {
    card.addEventListener("click", () => {
      const navItem = document.querySelector(`.nav-item[data-section="${card.dataset.section}"]`);
      if (navItem) navItem.click();
    });
  });

  // Este botão RECARREGA A PÁGINA. Ele nunca busca dado novo nas fontes
  // externas — quem faz isso é o serviço agendado no servidor (e, sob pedido,
  // o botão "Atualizar dados" da tela de Marketing).
  document.querySelector("#refreshBtn").addEventListener("click", () => {
    window.location.reload();
  });

  const mktRefreshBtn = document.querySelector("#mktRefreshBtn");
  if (mktRefreshBtn) {
    mktRefreshBtn.addEventListener("click", pedirAtualizacaoMarketing);
  }

  // M21 — Parcerias abre a lista B2B de clínicas/parceiros (rota "clinicas",
  // implementação única, sem card de atalho no CRM).
  const parceriaClinicasBtn = document.querySelector("#parceriaClinicasBtn");
  if (parceriaClinicasBtn) {
    parceriaClinicasBtn.addEventListener("click", () => {
      // A ordem importa: o clique no item de sidebar reseta crmView para "hub",
      // então a view desejada é definida DEPOIS da troca de seção.
      irParaSecao("crm");
      state.crmView = "clinicas";
      renderCrmView();
    });
  }

  document.querySelector("#globalSearch").addEventListener("input", (event) => {
    const term = event.target.value.toLowerCase().trim();

    document.querySelectorAll("tbody tr").forEach((row) => {
      const visible = row.textContent.toLowerCase().includes(term);
      row.style.display = visible || !term ? "" : "none";
    });
  });

  // Delegado no tbody (persiste entre re-renders de renderLeadsTable)
  const leadsTbody = document.querySelector("#leadsTable");
  if (leadsTbody) {
    leadsTbody.addEventListener("click", (event) => {
      const stageBtn = event.target.closest(".lead-stage-btn");
      if (stageBtn) {
        const lead = state.leads.find((l) => l.lead_id === stageBtn.dataset.leadId);
        if (lead) openLeadStageModal(lead, stageBtn);
        return;
      }

      const linkBtn = event.target.closest(".lead-link-btn");
      if (linkBtn) {
        const lead = state.leads.find((l) => l.lead_id === linkBtn.dataset.leadId);
        if (lead) abrirVinculoB2BCanonico(lead, linkBtn);
      }
    });
  }

  const leadsFilterEl = document.querySelector("#leadsFilter");
  if (leadsFilterEl) {
    leadsFilterEl.value = state.leadsFilter || "Ativos";
    updateLeadsFilterTip(leadsFilterEl);
    leadsFilterEl.addEventListener("change", (event) => {
      state.leadsFilter = event.target.value;
      updateLeadsFilterTip(leadsFilterEl);
      renderLeadsTable();
    });
  }
}

// Explica o que cada opção do filtro Ativos/Convertidos/Todos mostra — o
// texto muda junto com a seleção via title + aria-label nativos do <select>.
const LEADS_FILTER_TIP = {
  Ativos:      "Mostra leads ainda em andamento, sem conversão final.",
  Convertidos: "Mostra leads que já foram convertidos ou vinculados a paciente, atendimento ou parceria.",
  Todos:       "Mostra ativos e convertidos juntos.",
};

function updateLeadsFilterTip(el) {
  const tip = LEADS_FILTER_TIP[el.value] || "";
  el.title = tip;
  el.setAttribute("aria-label", `Filtro de leads: ${el.value}. ${tip}`);
}

// M25.23 — o painel legado só começa depois que o papel real voltou do
// servidor. Antes disto, init() disparava a leitura de todos os data/*.json
// imediatamente, para qualquer visitante. Papel exclusivamente clínico não
// carrega dado administrativo: os JSONs correspondentes respondem 403 e as
// seções nem existem mais no DOM, então buscá-los seria só ruído de erro.
(function () {
  const gate = window.SoproBootGate;
  if (!gate || !gate.pronto || typeof gate.pronto.then !== "function") {
    // Sem o gate carregado, fail-closed: não monta o painel legado.
    console.warn("Gate de boot ausente — painel administrativo não iniciado.");
    return;
  }
  gate.pronto.then((identidade) => {
    if (!identidade || gate.somenteClinico) return;
    init();
  });
})();


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
    { key: "documentosMapeados",      label: "Documentos mapeados", value: state.documentos.length },
    { key: "documentosStatusPositivo", label: "Status positivo", value: ativos },
    { key: "documentosComValidade",   label: "Com validade/monitoramento", value: comValidade },
    { key: "documentosDadosPessoais", label: "Dados pessoais", value: "Não usar" }
  ];

  statsContainer.innerHTML = stats.map((item) => statCardHtml("document-stat-card", item)).join("");

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

// Documentos → Equipamentos: resumo técnico-documental por equipamento (não
// por foto/arquivo) — ver data/documentos-equipamentos-summary.json. Nunca
// número de série, QR code, nota fiscal, CPF/CNPJ, chave Pix, código de
// compra ou etiqueta de rastreio — nenhum desses campos existe nesse arquivo,
// e esta função não faz nenhuma tentativa de ler/exibir os arquivos reais
// (que ficam só em data-private/documentos/equipamentos/, fora do Git).
// Rótulo curto para o chip + texto completo preservado em title/aria-label,
// para não perder informação (só compacta o que fica visível no card).
const DOCUMENT_EQUIP_SHORT_LABELS = {
  "Nota fiscal de compra": "Nota fiscal",
  "Nota fiscal de compra (mesma nota do espirômetro)": "Nota fiscal",
  "Guia de recolhimento tributário (ICMS-ST)": "Guia ICMS-ST",
  "Manual/instruções de uso": "Manual",
  "Guia de instalação de software": "Instalação software",
  "Certificado de calibração": "Certificado calibração",
};

function shortDocEquipLabel(doc) {
  return DOCUMENT_EQUIP_SHORT_LABELS[doc] || doc;
}

function renderDocumentosEquipamentos() {
  const statsContainer = document.querySelector("#documentEquipStats");
  const grid = document.querySelector("#documentEquipGrid");
  if (!statsContainer || !grid) return;

  const equipamentos = Array.isArray(state.documentosEquipamentos?.equipamentos)
    ? state.documentosEquipamentos.equipamentos
    : [];

  const isPendente = (valor) => {
    const v = (valor || "").toLowerCase();
    return !v || v.includes("a confirmar") || v.includes("não identificado");
  };

  const totalDocumentos = equipamentos.reduce(
    (sum, eq) => sum + (Array.isArray(eq.documentos) ? eq.documentos.length : 0),
    0
  );

  const stats = [
    { key: "docsEquipTotal",        label: "Equipamentos documentados",         value: equipamentos.length, hint: "metadados seguros" },
    { key: "docsEquipDocumentos",   label: "Documentos técnicos identificados", value: totalDocumentos, hint: "por equipamento" },
    { key: "docsEquipComValidade",  label: "Validades a confirmar",             value: equipamentos.filter((eq) => isPendente(eq.validade_ou_calibracao)).length, hint: "revisão manual pendente" },
    { key: "docsEquipProximaAcao",  label: "Próximas ações",                    value: equipamentos.filter((eq) => eq.proxima_acao).length, hint: "ações registradas" },
  ];
  statsContainer.innerHTML = stats.map((item) => statCardHtml("document-stat-card", item)).join("");

  if (equipamentos.length === 0) {
    grid.innerHTML = `<p class="crm-empty">Nenhum equipamento documentado ainda.</p>`;
    return;
  }

  grid.innerHTML = equipamentos.map((eq) => {
    const nome = eq.equipamento || "Não identificado";
    const fabricante = eq.fabricante || "A confirmar";
    const modelo = eq.modelo || "A confirmar";
    const status = eq.status_documental || "Arquivado";
    const validade = eq.validade_ou_calibracao || "A confirmar";
    const proximaAcao = eq.proxima_acao || "—";
    const documentos = Array.isArray(eq.documentos) && eq.documentos.length
      ? eq.documentos.map((d) => {
          const full = escapeHtml(d);
          const short = escapeHtml(shortDocEquipLabel(d));
          return `<span class="badge" title="${full}" tabindex="0" aria-label="${full}">${short}</span>`;
        }).join("")
      : `<span class="crm-empty">—</span>`;
    return `
    <article class="document-card document-equip-card">
      <h3>${escapeHtml(nome)}</h3>
      <p class="document-equip-brand">${escapeHtml(fabricante)} · ${escapeHtml(modelo)}</p>
      <div class="document-equip-chips">${documentos}</div>
      <div class="document-equip-fields">
        <div class="document-equip-field">
          <span class="document-equip-field-label">Validade/Calibração</span>
          <p class="document-equip-field-value">${escapeHtml(validade)}</p>
        </div>
        <div class="document-equip-field">
          <span class="document-equip-field-label">Próxima ação</span>
          <p class="document-equip-field-value">${escapeHtml(proximaAcao)}</p>
        </div>
        <div class="document-equip-field">
          <span class="document-equip-field-label">Status documental</span>
          <p class="document-equip-field-value"><span class="badge ${slug(status)}">${escapeHtml(status)}</span></p>
        </div>
      </div>
    </article>`;
  }).join("");
}


function renderFinance() {
  const statsContainer = document.querySelector("#financeStats");
  const financeNote = document.querySelector("#financeNote");
  const table = document.querySelector("#financeTable");
  const serviceCanvas = document.querySelector("#serviceRevenueChart");
  const originCanvas = document.querySelector("#originRevenueChart");

  if (!statsContainer || !table) return;

  const summary = state.financeiro_summary;
  const hasRealData = Boolean(
    summary?.source?.safeToDisplay === true &&
    summary?.source?.containsPersonalData === false
  );

  if (hasRealData) {
    // Fonte financeira única (M14.2): todos os valores vêm da aba
    // "Financeiro_Lancamentos" via read-financeiro-lancamentos-adc.py.
    // O CRM Espirometria é operacional — nunca origem de valor monetário.
    const totais = (summary.totais && typeof summary.totais === "object") ? summary.totais : null;
    const periodo = (summary.periodo && typeof summary.periodo === "object") ? summary.periodo : null;

    const safeLabel = document.querySelector("#financeiro .safe-label");
    if (safeLabel) {
      const faixa = periodo?.de && periodo?.ate
        ? ` · ${formatDateBr(periodo.de)} → ${formatDateBr(periodo.ate)}`
        : "";
      safeLabel.textContent = `Dados reais — Financeiro_Lancamentos${faixa}`;
    }

    const nExames = Number(summary.espirometrias_pagas) || 0;
    const baseExame = Number(summary.valor_base_exame) || 0;
    const saldo = summary.saldo_operacional;

    const cards = [
      { key: "receitaEspirometrias", label: "Receita recebida",  value: fmtBRL(summary.receita_exames),    hint: `${nExames} exame(s) pago(s) — Financeiro_Lancamentos` },
      { key: "financeTicketMedio",   label: "Ticket médio real", value: summary.ticket_medio_real != null ? fmtBRL(summary.ticket_medio_real) : "—", hint: baseExame > 0 ? `valor de tabela ${fmtBRL(baseExame)}` : "por exame pago" },
    ];
    if (totais) {
      cards.push(
        { key: "financeReceitaPendente",    label: "Receita pendente",      value: fmtBRL(totais.receita_pendente),     hint: "a receber (Pendente/Parcial)" },
        { key: "financeDescontos",          label: "Descontos concedidos",  value: fmtBRL(totais.descontos_concedidos), hint: `${totais.cortesias || 0} cortesia(s) · ${totais.cancelados || 0} cancelado(s)` }
      );
    }
    cards.push({ key: "financeEntradasRecentes", label: "Entradas no mês", value: fmtBRL(summary.total_entradas_mes_atual), hint: `${summary.total_lancamentos} lançamento(s) válidos no total` });
    // Saldo bancário não vem da fonte oficial — só aparece se um valor
    // numérico real existir (nunca R$ 0,00 inventado, nunca "—" gratuito).
    if (typeof saldo === "number" && Number.isFinite(saldo)) {
      cards.push({ key: "financeSaldoConta", label: "Saldo em conta", value: fmtBRL(saldo), hint: "registro manual — fora da fonte oficial" });
    }

    statsContainer.innerHTML = cards.map((item) => statCardHtml("finance-stat-card", item)).join("");

    if (financeNote) {
      const linhas = [
        "Fonte oficial dos valores: aba <strong>Financeiro_Lancamentos</strong> — um lançamento por exame, atualizado por id_atendimento.",
        "O CRM Espirometria guarda apenas o histórico operacional do exame; nenhum valor monetário é derivado dele.",
      ];
      if (totais) {
        if (Number(totais.linhas_invalidas) > 0) {
          linhas.push(`${totais.linhas_invalidas} lançamento(s) com valor/status inválido ficaram fora das somas — revisar no Financeiro.`);
        }
        if (Number(totais.linhas_inconsistentes) > 0) {
          linhas.push(`${totais.linhas_inconsistentes} lançamento(s) com valor recebido diferente do esperado para o status — revisar no Financeiro.`);
        }
        if (Number(totais.duplicados_ignorados) > 0) {
          linhas.push(`${totais.duplicados_ignorados} linha(s) duplicadas por id_atendimento foram deduplicadas (a mais recente vale).`);
        }
      } else {
        linhas.push("Resumo em formato antigo — regenere com: painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --write");
      }
      financeNote.innerHTML = linhas.join("<br>");
      financeNote.removeAttribute("hidden");
    }

    table.innerHTML = (summary.lancamentos_agregados || []).map((item) => `
      <tr>
        <td><strong>${escapeHtml(item.descricao)}</strong></td>
        <td>${escapeHtml(item.servico)}</td>
        <td>${escapeHtml(item.local || "—")}</td>
        <td><strong>${fmtBRL(item.valor)}</strong></td>
        <td><span class="badge ${slug(item.status)}">${escapeHtml(item.status)}</span></td>
        <td>${escapeHtml(item.data)}</td>
      </tr>
    `).join("");

    // Atualiza títulos dos painéis de gráficos para refletir os dados reais
    const porServico = summary.por_servico || [];
    // Novo schema traz receita por local de atendimento; o resumo antigo
    // tinha "por_origem" — aceita os dois sem quebrar.
    const porLocal = (summary.por_local || summary.por_origem || []).map((i) => ({
      label: i.local || i.origem || "—",
      valor: i.valor,
    }));

    if (serviceCanvas) {
      const servicePanel = serviceCanvas.closest(".panel")?.querySelector(".panel-header");
      if (servicePanel) {
        const h3 = servicePanel.querySelector("h3");
        const span = servicePanel.querySelector("span");
        if (h3) h3.textContent = "Receita por serviço";
        if (span) span.textContent = "Receita recebida — fonte Financeiro_Lancamentos";
      }
      createChart("serviceRevenue", "#serviceRevenueChart", {
        type: "bar",
        data: {
          labels: porServico.map((i) => i.servico),
          datasets: [{
            label: "Receita (R$)",
            data: porServico.map((i) => i.valor),
            backgroundColor: CHART_COLORS.slice(0, porServico.length),
            borderRadius: 14,
            borderSkipped: false,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => ` ${fmtBRL(ctx.raw)}` } }
          },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: "rgba(109,123,138,.07)" },
              ticks: { callback: (v) => fmtBRL(v), font: { size: 11 } },
              border: { display: false }
            },
            x: { grid: { display: false }, border: { display: false } }
          }
        }
      });
    }

    if (originCanvas && porLocal.length > 0) {
      const originPanel = originCanvas.closest(".panel")?.querySelector(".panel-header");
      if (originPanel) {
        const h3 = originPanel.querySelector("h3");
        const span = originPanel.querySelector("span");
        if (h3) h3.textContent = "Receita por local de atendimento";
        if (span) span.textContent = "Domiciliar, clínica, empresa/PCMSO e parceiros";
      }
      createChart("originRevenue", "#originRevenueChart", {
        type: "doughnut",
        data: {
          labels: porLocal.map((i) => i.label),
          datasets: [{
            data: porLocal.map((i) => i.valor),
            backgroundColor: CHART_COLORS,
            borderWidth: 3,
            borderColor: "#f3f7fb",
            hoverOffset: 8,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "68%",
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, padding: 12 } },
            tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${fmtBRL(ctx.raw)}` } }
          }
        }
      });
    }

    // Gráfico de entradas recentes destacadas
    const entradasCanvas = document.querySelector("#financeEntradasChart");
    const entradasPanel = document.querySelector("#financeEntradasPanel");
    if (entradasCanvas && entradasPanel && (summary.lancamentos_agregados || []).length > 0) {
      const entradasHeader = entradasPanel.querySelector(".panel-header span");
      if (entradasHeader) entradasHeader.textContent = "Lançamentos recentes visíveis no extrato — não representam o total da operação";
      entradasPanel.removeAttribute("hidden");
      createChart("financeEntradas", "#financeEntradasChart", {
        type: "bar",
        data: {
          labels: summary.lancamentos_agregados.map((i) => i.descricao),
          datasets: [{
            label: "Valor (R$)",
            data: summary.lancamentos_agregados.map((i) => i.valor),
            backgroundColor: CHART_COLORS.slice(0, summary.lancamentos_agregados.length),
            borderRadius: 12,
            borderSkipped: false,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => ` ${fmtBRL(ctx.raw)}` } }
          },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: "rgba(109,123,138,.07)" },
              ticks: { callback: (v) => fmtBRL(v), font: { size: 11 } },
              border: { display: false }
            },
            x: { grid: { display: false }, ticks: { font: { size: 11 } }, border: { display: false } }
          }
        }
      });
    }

  } else {
    // Resumo financeiro local não disponível — exibe estado vazio/pendente
    statsContainer.innerHTML = statCardHtml("finance-stat-card finance-stat-pending", {
      key: "financeResumoPendente",
      label: "Resumo financeiro",
      value: "—",
      hint: "Resumo financeiro local não encontrado"
    });

    if (financeNote) {
      financeNote.textContent = "Resumo financeiro local não encontrado. Gere com: painel-soprolife/scripts/read-financeiro-lancamentos-adc.py --write (fonte: aba Financeiro_Lancamentos).";
      financeNote.removeAttribute("hidden");
    }

    table.innerHTML = `
      <tr>
        <td colspan="6" class="table-empty-cell">
          <div class="empty-state">Dados ainda não carregados neste ambiente de preview.</div>
        </td>
      </tr>
    `;
  }
}

// Lê o rateio de sócios diretamente dos campos estruturados do resumo
// (ci.rateio_socios / ci.rateio_itens). Nunca infere pagamento a partir do
// campo "responsavel" nem faz parsing de texto de observação — se o resumo
// ainda não tiver essa estrutura, retorna null/[] e a UI mostra "dado ainda
// não disponível" em vez de adivinhar quem pagou o quê.
function getRateioSocios(ci) {
  return Array.isArray(ci.rateio_socios) && ci.rateio_socios.length > 0 ? ci.rateio_socios : null;
}

function getRateioItens(ci) {
  return Array.isArray(ci.rateio_itens) ? ci.rateio_itens : [];
}

function findSocio(socios, nomeParcial) {
  return socios.find((s) => new RegExp(nomeParcial, "i").test(s.nome || "")) || null;
}

// Parcerias → Pastore — resumo executivo (produção, financeiro, agenda e
// CRM agregado da parceria). Valor financeiro desconhecido usa "—"/"A definir",
// nunca R$ 0,00 inventado (ver soprolife-finance-costs). Dados pessoais de
// pacientes ficam só em data-private/parcerias-pastore.local.json.
//
// Cadeia de carregamento (para funcionar em qualquer ambiente, inclusive VPS
// recém-clonada sem *.local.json): .local.json (dev/privado, gitignored) →
// .json committável (fallback seguro no Git) → objeto interno abaixo (nunca
// deve faltar dado — evita a tela mostrar "arquivo não encontrado").
const PARCERIA_PASTORE_FALLBACK = {
  source: { type: "parcerias_pastore_summary", safeToDisplay: true, containsPersonalData: false, generatedAt: null },
  parceria: {
    nome: "Parceria Pastore",
    unidade: "Pastore Ipanema",
    servico: "Espirometria com ou sem broncodilatador",
    status: "planejada",
    status_label: "Estrutura inicial da parceria",
    chips: ["Pastore Ipanema", "Terças e sábados", "08h às 12h"],
  },
  kpis: { exames_realizados: 0, receita_estimada: null, resultado_liquido_estimado: null, ocupacao_agenda_pct: 0 },
  producao_por_data: { labels: [], exames: [] },
  financeiro_por_periodo: { labels: [], receita: [], custos: [], resultado: [] },
  agenda: [
    { unidade: "Pastore Ipanema", dia_semana: "Terça-feira", horario: "08h às 12h", status: "planejada", capacidade_estimada_por_turno: null },
    { unidade: "Pastore Ipanema", dia_semana: "Sábado",      horario: "08h às 12h", status: "planejada", capacidade_estimada_por_turno: null },
  ],
  financeiro_parametros: {
    valor_exame_sem_broncodilatador: null, valor_exame_com_broncodilatador: null, repasse_percentual_pastore: null,
    custo_deslocamento: null, custo_insumos: null, custo_profissional: null, outros_custos: null,
    receita_bruta: null, custo_total: null, resultado_liquido: null, margem_estimada_pct: null,
    observacao: "Valores comerciais ainda não definidos com a Pastore.",
  },
  pacientes_pastore: { total_atendidos: 0, followup_pendente: 0, recorrentes: 0, distribuicao_tipo_exame: { sem_broncodilatador: 0, com_broncodilatador: 0 } },
};

function isSafeParceriaSummary(d) {
  return d?.source?.safeToDisplay === true && d?.source?.containsPersonalData === false;
}

async function loadParceriaPastoreSummary() {
  const local = await loadOptionalJson("./data/parcerias-pastore-summary.local.json");
  if (isSafeParceriaSummary(local)) return local;

  const committed = await loadOptionalJson("./data/parcerias-pastore-summary.json");
  if (isSafeParceriaSummary(committed)) return committed;

  return PARCERIA_PASTORE_FALLBACK;
}

function fmtBRLOrDash(value) {
  return (value === null || value === undefined) ? "—" : fmtBRL(value);
}

function fmtPctOrDash(value) {
  return (value === null || value === undefined) ? "—" : `${value}%`;
}

function renderParceriaPastore() {
  const chipsContainer = document.querySelector("#parceriaPastoreChips");
  const statsContainer = document.querySelector("#parceriaPastoreStats");
  const statusLabel = document.querySelector("#parceriaPastoreStatusLabel");
  if (!chipsContainer || !statsContainer) return;

  const pp = state.parceriaPastore;

  // Na prática isto não deve mais acontecer — loadParceriaPastoreSummary()
  // sempre retorna um objeto seguro (local → committável → fallback interno).
  // Mantido só como rede de segurança para um estado inesperado do state.
  if (!pp) {
    if (statusLabel) statusLabel.textContent = "Aguardando início da produção";
    chipsContainer.innerHTML = "";
    statsContainer.innerHTML = statCardHtml("parceria-stat-card", {
      key: "parceriaPastoreIndisponivel",
      label: "Parceria Pastore",
      value: "—",
      hint: "Aguardando início da produção",
    });
    ["#parceria-agenda", "#parceria-financeiro", "#parceria-pacientes"].forEach((sel) => {
      const pane = document.querySelector(sel);
      if (pane) pane.innerHTML = `
        <article class="panel">
          <div class="panel-header"><h3>Estrutura inicial da parceria</h3></div>
          <p style="padding:1rem 1.25rem;color:var(--muted);font-size:.88rem">
            Aguardando início da produção — os dados aparecerão aqui assim que o atendimento começar.
          </p>
        </article>
      `;
    });
    return;
  }

  if (statusLabel) {
    statusLabel.textContent = pp.parceria?.status_label || "Estrutura inicial da parceria";
  }

  const chips = Array.isArray(pp.parceria?.chips) && pp.parceria.chips.length
    ? pp.parceria.chips
    : [pp.parceria?.unidade].filter(Boolean);
  chipsContainer.innerHTML = chips.map((c) => `<span class="badge parceria-chip">${escapeHtml(c)}</span>`).join("");

  const kpis = pp.kpis || {};
  const semProducaoAinda = (kpis.exames_realizados ?? 0) === 0;
  statsContainer.innerHTML = [
    { key: "parceriaExamesRealizados",    label: "Exames realizados",          value: String(kpis.exames_realizados ?? 0),        hint: "produção acumulada" },
    { key: "parceriaReceitaEstimada",     label: "Receita estimada",           value: fmtBRLOrDash(kpis.receita_estimada),         hint: semProducaoAinda ? "Aguardando início de produção" : "com base nos exames realizados" },
    { key: "parceriaResultadoLiquido",    label: "Resultado líquido estimado", value: fmtBRLOrDash(kpis.resultado_liquido_estimado), hint: semProducaoAinda ? "Aguardando início de produção" : "receita − custos" },
    { key: "parceriaOcupacaoAgenda",      label: "Ocupação da agenda",         value: fmtPctOrDash(kpis.ocupacao_agenda_pct),      hint: semProducaoAinda ? "Aguardando início de produção" : "realizado ÷ capacidade" },
  ].map((c) => statCardHtml("parceria-stat-card", c)).join("");

  // Gráfico: produção por agenda/data — sem dado real ainda, mostra estado vazio
  // em vez de inventar números (a parceria ainda não iniciou o atendimento).
  const producao = pp.producao_por_data || { labels: [], exames: [] };
  const producaoCanvas = document.querySelector("#parceriaProducaoChart");
  const producaoEmpty  = document.querySelector("#parceriaProducaoEmpty");
  if (producao.labels?.length) {
    if (producaoCanvas) producaoCanvas.hidden = false;
    if (producaoEmpty) producaoEmpty.hidden = true;
    createChart("parceriaProducao", "#parceriaProducaoChart", {
      type: "bar",
      data: {
        labels: producao.labels,
        datasets: [{
          label: "Exames realizados",
          data: producao.exames,
          backgroundColor: "rgba(29, 183, 166, .85)",
          borderRadius: 10,
          borderSkipped: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, border: { display: false } },
          x: { grid: { display: false }, border: { display: false } },
        },
      },
    });
  } else {
    destroyChart("parceriaProducao");
    if (producaoCanvas) producaoCanvas.hidden = true;
    if (producaoEmpty) producaoEmpty.hidden = false;
  }

  // Gráfico: receita x custos x resultado — mesmo raciocínio de estado vazio.
  const fin = pp.financeiro_por_periodo || { labels: [], receita: [], custos: [], resultado: [] };
  const finCanvas = document.querySelector("#parceriaFinanceiroChart");
  const finEmpty  = document.querySelector("#parceriaFinanceiroEmpty");
  if (fin.labels?.length) {
    if (finCanvas) finCanvas.hidden = false;
    if (finEmpty) finEmpty.hidden = true;
    createChart("parceriaFinanceiro", "#parceriaFinanceiroChart", {
      type: "bar",
      data: {
        labels: fin.labels,
        datasets: [
          { label: "Receita",   data: fin.receita,   backgroundColor: "rgba(29, 183, 166, .85)", borderRadius: 8, borderSkipped: false },
          { label: "Custos",    data: fin.custos,    backgroundColor: "rgba(228, 92, 100, .8)",  borderRadius: 8, borderSkipped: false },
          { label: "Resultado", data: fin.resultado, backgroundColor: "rgba(11, 31, 54, .85)",   borderRadius: 8, borderSkipped: false },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, padding: 14 } },
        },
        scales: {
          y: { beginAtZero: true, grid: { color: "rgba(109,123,138,.07)" }, border: { display: false } },
          x: { grid: { display: false }, border: { display: false } },
        },
      },
    });
  } else {
    destroyChart("parceriaFinanceiro");
    if (finCanvas) finCanvas.hidden = true;
    if (finEmpty) finEmpty.hidden = false;
  }

  // Aba: Agenda — poucos registros e campos curtos, tabela compacta cabe bem aqui.
  const agendaPane = document.querySelector("#parceria-agenda");
  if (agendaPane) {
    const agenda = Array.isArray(pp.agenda) ? pp.agenda : [];
    const rows = agenda.map((a) => `
      <tr>
        <td>${escapeHtml(a.unidade || "—")}</td>
        <td>${escapeHtml(a.dia_semana || "—")}</td>
        <td>${escapeHtml(a.horario || "—")}</td>
        <td><span class="badge ${slug(a.status || "planejada")}">${escapeHtml(a.status || "planejada")}</span></td>
        <td>${a.capacidade_estimada_por_turno != null ? escapeHtml(String(a.capacidade_estimada_por_turno)) : "A definir"}</td>
      </tr>
    `).join("");
    agendaPane.innerHTML = `
      <article class="panel">
        <div class="panel-header">
          <h3>Agenda da parceria</h3>
          <span>Dias, horário e capacidade por turno</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Unidade</th>
                <th>Dia da semana</th>
                <th>Horário</th>
                <th>Status</th>
                <th>Capacidade estimada/turno</th>
              </tr>
            </thead>
            <tbody>${rows || `<tr><td colspan="5" class="crm-empty">Nenhuma agenda cadastrada ainda.</td></tr>`}</tbody>
          </table>
        </div>
      </article>
    `;
  }

  // Aba: Financeiro — blocos premium (label acima, valor abaixo), nunca
  // R$ 0,00 quando o valor real ainda não foi definido com a Pastore.
  const financeiroPane = document.querySelector("#parceria-financeiro");
  if (financeiroPane) {
    const fp = pp.financeiro_parametros || {};
    const field = (label, value) => `
      <div class="parceria-field">
        <span class="parceria-field-label">${escapeHtml(label)}</span>
        <p class="parceria-field-value">${value}</p>
      </div>
    `;
    financeiroPane.innerHTML = `
      <article class="panel">
        <div class="panel-header">
          <h3>Preço por exame</h3>
          <span>Valores acordados com a Pastore</span>
        </div>
        <div class="parceria-fields">
          ${field("Valor sem broncodilatador", fmtBRLOrDash(fp.valor_exame_sem_broncodilatador))}
          ${field("Valor com broncodilatador", fmtBRLOrDash(fp.valor_exame_com_broncodilatador))}
          ${field("Repasse/percentual da Pastore", fp.repasse_percentual_pastore != null ? `${fp.repasse_percentual_pastore}%` : "A definir")}
        </div>
      </article>

      <article class="panel">
        <div class="panel-header">
          <h3>Custos operacionais</h3>
          <span>Por turno/exame, quando aplicável</span>
        </div>
        <div class="parceria-fields">
          ${field("Custo de deslocamento", fmtBRLOrDash(fp.custo_deslocamento))}
          ${field("Custo de insumos", fmtBRLOrDash(fp.custo_insumos))}
          ${field("Custo profissional", fmtBRLOrDash(fp.custo_profissional))}
          ${field("Outros custos", fmtBRLOrDash(fp.outros_custos))}
        </div>
      </article>

      <article class="panel">
        <div class="panel-header">
          <h3>Resultado da parceria</h3>
          <span>Consolidado — receita, custo e margem</span>
        </div>
        <div class="parceria-fields">
          ${field("Receita bruta", fmtBRLOrDash(fp.receita_bruta))}
          ${field("Custo total", fmtBRLOrDash(fp.custo_total))}
          ${field("Resultado líquido", fmtBRLOrDash(fp.resultado_liquido))}
          ${field("Margem estimada", fp.margem_estimada_pct != null ? `${fp.margem_estimada_pct}%` : "A definir")}
        </div>
        ${fp.observacao ? `<p class="parceria-financeiro-note">${escapeHtml(fp.observacao)}</p>` : ""}
      </article>
    `;
  }

  // Aba: Pacientes — CRM da parceria, só agregados (nunca nome/telefone aqui).
  const pacientesPane = document.querySelector("#parceria-pacientes");
  if (pacientesPane) {
    const pac = pp.pacientes_pastore || {};
    const dist = pac.distribuicao_tipo_exame || {};
    const total = pac.total_atendidos ?? 0;
    const stats = [
      { key: "parceriaPacientesTotal",       label: "Pacientes atendidos via Pastore", value: String(total), hint: "acumulado" },
      { key: "parceriaPacientesFollowup",    label: "Follow-up pendente",              value: String(pac.followup_pendente ?? 0), hint: "precisam de contato" },
      { key: "parceriaPacientesRecorrentes", label: "Pacientes recorrentes",           value: String(pac.recorrentes ?? 0), hint: "mais de um exame" },
    ];
    pacientesPane.innerHTML = `
      <div class="crm-stats">
        ${stats.map((item) => statCardHtml("crm-stat-card", item)).join("")}
      </div>
      <article class="panel">
        <div class="panel-header">
          <h3>Distribuição por tipo de exame</h3>
          <span>Sem broncodilatador x com broncodilatador</span>
        </div>
        <div class="ci-table-inner">
          <div class="ci-socio-stat"><span>Sem broncodilatador</span><strong>${dist.sem_broncodilatador ?? 0}</strong></div>
          <div class="ci-socio-stat"><span>Com broncodilatador</span><strong>${dist.com_broncodilatador ?? 0}</strong></div>
        </div>
      </article>
      ${total === 0 ? `<p class="crm-empty">Nenhum paciente atendido via Pastore ainda — os dados aparecerão aqui quando o atendimento começar.</p>` : ""}
      <div class="crm-private-note">
        <span>🔒</span>
        <p>Nome, telefone e observações ficam apenas no arquivo privado local (<code>data-private/parcerias-pastore.local.json</code>) — gitignored, nunca enviado ao GitHub. Aqui só aparecem agregados seguros.</p>
      </div>
    `;
  }

  // Wiring das abas — classes próprias (.parceria-tab/.parceria-pane), isoladas
  // de .ci-tab para não acoplar com a lógica de Custos & Investimentos.
  document.querySelectorAll(".parceria-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".parceria-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".parceria-pane").forEach((p) => p.setAttribute("hidden", ""));
      btn.classList.add("active");
      const pane = document.querySelector(`#parceria-${btn.dataset.parceriaTab}`);
      if (pane) pane.removeAttribute("hidden");
    });
  });

  const novoAtendimentoBtn = document.querySelector("#parceriaNovoAtendimentoBtn");
  if (novoAtendimentoBtn) {
    novoAtendimentoBtn.addEventListener("click", () => openNovoAtendimentoPastoreModal(novoAtendimentoBtn));
  }
}

// M16/M20 — o modal legado de staging (gravava na planilha "Parceria Pastore -
// Atendimentos" via Apps Script) foi substituído pelo fluxo canônico: o botão
// "+ Novo atendimento" abre a Central de Cadastros no fluxo único "Novo
// atendimento" com o tipo Espirometria Pastore já selecionado (o parceiro é
// definido automaticamente). Registro nasce direto no núcleo M15.
function openNovoAtendimentoPastoreModal() {
  if (window.SoproCentral) {
    window.SoproCentral.open("atendimento", { tipo: "espirometria_pastore" });
  }
}


function renderCustosInvestimentos() {
  const statsContainer = document.querySelector("#ciStats");
  if (!statsContainer) return;

  const ci = state.custosInvestimentos;

  if (!ci?.source?.safeToDisplay) {
    statsContainer.innerHTML = statCardHtml("ci-stat-card", {
      key: "ciResumoIndisponivel",
      extraClass: "accent-warn",
      label: "Custos & Investimentos",
      value: "—",
      hint: "Resumo local não encontrado"
    });
    const pane = document.querySelector("#ci-resumo");
    if (pane) pane.innerHTML = `
      <article class="panel">
        <div class="panel-header"><h3>Dados não disponíveis</h3></div>
        <p style="padding:1rem 1.25rem;color:var(--muted);font-size:.88rem">
          Arquivo <code>data/custos-investimentos-summary.local.json</code> não encontrado ou não seguro.
        </p>
      </article>
    `;
    return;
  }

  const safeLabel = document.querySelector("#ciSafeLabel");
  if (safeLabel) safeLabel.textContent = "Dados locais — jun/2026";

  statsContainer.innerHTML = [
    { key: "custosMensais",             label: "Custo mensal atual",        value: fmtBRL(ci.total_mensal_atual),              hint: "recorrentes + infraestrutura + parcelas",     extraClass: "accent-teal"   },
    { key: "investimentosEquipamentos", label: "Investido em equipamentos", value: fmtBRL(ci.total_investido_equipamentos),     hint: "Spirobank, Seringa e Koko",                   extraClass: "accent-navy"   },
    { key: "ciParcelasMensais",         label: "Parcelas mensais",          value: fmtBRL(ci.parcelas_mensais_equipamentos),    hint: "Equipamentos parcelados ativos",              extraClass: "accent-navy"   },
    { key: "ciRecorrentesInfra",        label: "Recorrentes + infra/mês",   value: fmtBRL(ci.total_mensal_recorrente),          hint: "Workspace · Tailscale · Hostinger",           extraClass: "accent-teal"   },
    { key: "ciItensCadastrados",        label: "Itens cadastrados",         value: String(ci.itens_ativos),                    hint: "Adeildo e Faustino",                          extraClass: "accent-teal"   },
    { key: "ciPendenciasCadastro",      label: "Pendências de cadastro",    value: String(ci.pendencias_cadastro),             hint: "Regularização + parcelas Koko sem pagador",   extraClass: "accent-warn"   },
  ].map((c) => statCardHtml("ci-stat-card", c)).join("");

  function statusBadge(status) {
    const cls = status === "ativo" || status === "pago" ? "ci-badge-ativo"
              : status === "parcelado" ? "ci-badge-parcelado"
              : "ci-badge-pendente";
    return `<span class="ci-badge ${cls}">${escapeHtml(status)}</span>`;
  }

  function catBadge(cat) {
    const cls = cat === "Equipamento" ? "ci-badge-equipamento" : "ci-badge-recorrente";
    return `<span class="ci-badge ${cls}">${escapeHtml(cat)}</span>`;
  }

  function buildResumoPane() {
    const rows = ci.itens.map((it) => {
      const parcProgress = it.parcelas_total != null ? `${it.parcelas_pagas}/${it.parcelas_total}` : "—";
      const saldo = it.parcelas_total != null
        ? fmtBRL((it.parcelas_total - it.parcelas_pagas) * it.valor_mensal)
        : "—";
      const totalFmt = it.valor_total != null ? fmtBRL(it.valor_total) : "—";
      return `
        <tr>
          <td><strong>${escapeHtml(it.nome)}</strong>${it.obs_curta ? `<br><small style="color:var(--muted);font-size:.76rem">${escapeHtml(it.obs_curta)}</small>` : ""}</td>
          <td>${catBadge(it.categoria)}</td>
          <td>${escapeHtml(it.responsavel)}</td>
          <td>${totalFmt}</td>
          <td><strong>${fmtBRL(it.valor_mensal)}</strong></td>
          <td style="font-variant-numeric:tabular-nums">${parcProgress}</td>
          <td>${saldo}</td>
          <td>${escapeHtml(it.inicio)}</td>
          <td>${statusBadge(it.status)}</td>
        </tr>
      `;
    }).join("");

    // Números apenas — detalhamento completo (com observações) fica na aba
    // "Sócios / Rateio", não aqui no resumo geral.
    const porRespHtml = ci.por_responsavel.map((r) => `
      <div class="ci-socio-stat">
        <span>${escapeHtml(r.responsavel)}</span>
        <strong>${fmtBRL(r.total_mensal)}/mês · ${r.itens} item(ns)</strong>
      </div>
    `).join("");

    const porCatHtml = ci.por_categoria.map((c) => `
      <div class="ci-socio-stat">
        <span>${escapeHtml(c.categoria)}</span>
        <strong>${fmtBRL(c.total_mensal)}/mês</strong>
      </div>
    `).join("");

    return `
      <article class="panel">
        <div class="panel-header">
          <h3>Todos os itens cadastrados</h3>
          <span>${ci.itens_ativos} ativo(s) · total ${fmtBRL(ci.total_mensal_atual)}/mês</span>
        </div>
        <div class="table-wrap">
          <table class="ci-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Categoria</th>
                <th>Responsável</th>
                <th>Total</th>
                <th>Mensal / Parcela</th>
                <th>Parcelas</th>
                <th>Saldo</th>
                <th>Início</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </article>
      <div class="grid-two" style="margin-top:1rem">
        <article class="panel">
          <div class="panel-header"><h3>Por responsável</h3></div>
          <div class="ci-table-inner">${porRespHtml}</div>
        </article>
        <article class="panel">
          <div class="panel-header"><h3>Por categoria</h3></div>
          <div class="ci-table-inner">${porCatHtml}</div>
        </article>
      </div>
    `;
  }

  function buildRecorrentesPane() {
    const itens = ci.itens.filter((it) => it.categoria === "Recorrente" || it.categoria === "Infraestrutura");
    const total = itens.reduce((s, it) => s + it.valor_mensal, 0);

    const rows = itens.map((it) => {
      const isHist = it.id === "ws-001";
      return `
        <tr>
          <td>
            <strong>${escapeHtml(it.nome)}</strong>
            ${isHist ? `<div class="ci-hist-note">Pagamentos históricos desde dez/2025 — número de parcelas a revisar</div>` : ""}
          </td>
          <td>${catBadge(it.categoria)}</td>
          <td>${escapeHtml(it.responsavel)}</td>
          <td><strong>${fmtBRL(it.valor_mensal)}/mês</strong></td>
          <td>${escapeHtml(it.inicio)}</td>
          <td>${statusBadge(it.status)}</td>
        </tr>
      `;
    }).join("");

    return `
      <article class="panel">
        <div class="panel-header">
          <h3>Custos recorrentes e infraestrutura</h3>
          <span>Total mensal: ${fmtBRL(total)}</span>
        </div>
        <div class="table-wrap">
          <table class="ci-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Categoria</th>
                <th>Responsável</th>
                <th>Valor mensal</th>
                <th>Início</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </article>
      <div class="finance-real-note" style="margin-top:.75rem">
        Google Workspace: ativo desde dez/2025. O número exato de meses históricos pode ser ajustado a qualquer momento sem travar o sistema.
        Tailscale: US$ 8/mês convertido e arredondado para R$ 42,00 para controle interno.
        Hostinger VPS: plano anual de 12 meses — vencimento em jun/2027.
      </div>
    `;
  }

  function buildEquipamentosPane() {
    const equips = ci.itens.filter((it) => it.categoria === "Equipamento");
    const totalInv = equips.reduce((s, it) => s + (it.valor_total || 0), 0);
    const totalParc = equips.reduce((s, it) => s + it.valor_mensal, 0);

    const rows = equips.map((it) => {
      const parcPagas = it.parcelas_pagas || 0;
      const parcTotal = it.parcelas_total || 10;
      const pct = parcTotal > 0 ? Math.round((parcPagas / parcTotal) * 100) : 0;
      const saldo = fmtBRL((parcTotal - parcPagas) * it.valor_mensal);
      return `
        <tr>
          <td><strong>${escapeHtml(it.nome)}</strong><br><small style="color:var(--muted);font-size:.76rem">${escapeHtml(it.obs_curta || "")}</small></td>
          <td>${escapeHtml(it.responsavel)}</td>
          <td>${fmtBRL(it.valor_total)}</td>
          <td><strong>${fmtBRL(it.valor_mensal)}</strong></td>
          <td>
            <div class="ci-progress-wrap">
              <div class="ci-progress-bar"><div class="ci-progress-fill" style="width:${pct}%"></div></div>
              <span class="ci-progress-label">${parcPagas}/${parcTotal}×</span>
            </div>
          </td>
          <td>${saldo}</td>
          <td>${escapeHtml(it.inicio)}</td>
          <td>${statusBadge(it.status)}</td>
        </tr>
      `;
    }).join("");

    const koko = equips.find((it) => it.id === "koko-001");
    const kokoSaldo = koko && koko.parcelas_total != null
      ? (koko.parcelas_total - koko.parcelas_pagas) * koko.valor_mensal
      : null;

    const equipStats = [
      { key: "ciEquipTotalInvestido", extraClass: "accent-navy", label: "Total investido (equipamentos)", value: fmtBRL(totalInv), hint: "Spirobank, Seringa e Koko" },
      { key: "ciEquipParcelaMensal",  extraClass: "accent-teal", label: "Parcela mensal total",           value: fmtBRL(totalParc), hint: "Equipamentos parcelados ativos" },
      { key: "ciEquipPendente",       extraClass: "accent-warn", label: "Saldo pendente — Koko", value: kokoSaldo != null ? fmtBRL(kokoSaldo) : "Pendente", hint: kokoSaldo != null ? "6 parcelas sem data definida" : "Parcelas a cadastrar — Faustino" },
    ];

    return `
      <div class="ci-stats" style="margin-bottom:1rem">
        ${equipStats.map((c) => statCardHtml("ci-stat-card", c)).join("")}
      </div>
      <article class="panel">
        <div class="panel-header">
          <h3>Equipamentos &amp; Parcelamentos</h3>
          <span>Total investido: ${fmtBRL(totalInv)}</span>
        </div>
        <div class="table-wrap">
          <table class="ci-table">
            <thead>
              <tr>
                <th>Equipamento</th>
                <th>Responsável</th>
                <th>Valor total</th>
                <th>Valor parcela</th>
                <th>Progresso</th>
                <th>Saldo restante</th>
                <th>Início</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </article>
    `;
  }

  function buildSociosPane() {
    const socios = getRateioSocios(ci);
    const itens = getRateioItens(ci);

    if (!socios) {
      return `<p class="crm-report-chart-empty">Dado ainda não disponível — o resumo ainda não tem a estrutura rateio_socios.</p>`;
    }

    const adeildo = findSocio(socios, "adeildo") || {};
    const faustino = findSocio(socios, "faustino") || {};
    const semPagador = itens.reduce((s, it) => s + (it.pendente_sem_pagador || 0), 0);

    const hasDesembolso = ((adeildo.total_desembolsado || 0) + (faustino.total_desembolsado || 0)) > 0;
    const hasPagoPendente = (
      (adeildo.total_desembolsado || 0) + (faustino.total_desembolsado || 0) +
      (adeildo.saldo_pendente_atribuido || 0) + (faustino.saldo_pendente_atribuido || 0) +
      semPagador
    ) > 0;

    // A) Gráficos primeiro — visão geral antes do detalhe.
    const chartsHtml = `
      <div class="grid-two">
        <article class="panel">
          <div class="panel-header">
            <h3>Desembolsado por sócio</h3>
            <span>Quanto cada um já pagou</span>
          </div>
          ${hasDesembolso
            ? `<canvas id="ciSociosDesembolsoChart"></canvas>`
            : `<p class="crm-report-chart-empty">Dado ainda não disponível</p>`}
        </article>
        <article class="panel">
          <div class="panel-header">
            <h3>Pago x Pendente por sócio</h3>
            <span>Pago, pendente atribuído e sem pagador definido</span>
          </div>
          ${hasPagoPendente
            ? `<canvas id="ciSociosPagoPendenteChart"></canvas>`
            : `<p class="crm-report-chart-empty">Dado ainda não disponível</p>`}
        </article>
      </div>
    `;

    // B) Cards compactos por sócio — só números principais, sem parágrafos.
    const cardFor = (s) => {
      const initials = (s.nome || "—").slice(0, 2).toUpperCase();
      return `
        <article class="ci-socio-card">
          <div class="socio-name">
            <div class="socio-avatar">${escapeHtml(initials)}</div>
            ${escapeHtml(s.nome || "—")}
          </div>
          <div class="ci-socio-stat">
            <span>Total desembolsado</span>
            <strong>${fmtBRL(s.total_desembolsado)}</strong>
          </div>
          <div class="ci-socio-stat">
            <span>Compromisso mensal</span>
            <strong>${fmtBRL(s.compromisso_mensal)}</strong>
          </div>
          <div class="ci-socio-stat">
            <span>Saldo pendente atribuído</span>
            <strong>${fmtBRL(s.saldo_pendente_atribuido)}</strong>
          </div>
          ${s.pendente_sem_pagador_relacionado > 0 ? `
          <div class="ci-socio-stat">
            <span>Sem pagador definido</span>
            <strong>${fmtBRL(s.pendente_sem_pagador_relacionado)}</strong>
          </div>` : ""}
          <div class="ci-socio-stat">
            <span>Itens vinculados</span>
            <strong>${s.itens_vinculados ?? "—"}</strong>
          </div>
        </article>
      `;
    };
    const cardsHtml = `<div class="ci-socios-grid" style="margin-top:1rem">${socios.map(cardFor).join("")}</div>`;

    // C) Tabela detalhada por item, lida direto de rateio_itens — nunca
    // inferida do campo "responsavel". "—" quando o valor é desconhecido;
    // R$ 0,00 só quando o zero é um fato confirmado (ex.: 0/10 parcelas pagas).
    const fmtOrDash = (v, suffix) => (v == null ? "—" : `${fmtBRL(v)}${suffix || ""}`);
    const itemRows = itens.map((it) => {
      const isMensal = it.tipo_valor === "mensal";
      const totalFmt = it.valor_total != null ? fmtBRL(it.valor_total) : "—";
      const pagoAFmt = fmtOrDash(it.pago_adeildo, isMensal ? "/mês" : "");
      const pagoFFmt = fmtOrDash(it.pago_faustino, isMensal ? "/mês" : "");

      let saldoFmt = "—";
      if (!isMensal) {
        const semDado = it.pendente_adeildo == null && it.pendente_faustino == null && !it.pendente_sem_pagador;
        if (!semDado) {
          const total = (it.pendente_adeildo || 0) + (it.pendente_faustino || 0) + (it.pendente_sem_pagador || 0);
          saldoFmt = fmtBRL(total);
          if (it.pendente_sem_pagador > 0) {
            saldoFmt += ` <small style="color:var(--warn,#b5730a)">(sem pagador: ${fmtBRL(it.pendente_sem_pagador)})</small>`;
          }
        }
      }

      return `
        <tr>
          <td><strong>${escapeHtml(it.item)}</strong></td>
          <td>${totalFmt}</td>
          <td>${pagoAFmt}</td>
          <td>${pagoFFmt}</td>
          <td>${saldoFmt}</td>
          <td><small style="color:var(--muted);font-size:.78rem">${escapeHtml(it.observacao_curta || "")}</small></td>
        </tr>
      `;
    }).join("");

    const itemTableHtml = `
      <article class="panel" style="margin-top:1rem">
        <div class="panel-header">
          <h3>Rateio por item</h3>
          <span>Quanto cada sócio já pagou, item a item</span>
        </div>
        <div class="table-wrap">
          <table class="ci-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Valor total</th>
                <th>Pago Adeildo</th>
                <th>Pago Faustino</th>
                <th>Saldo restante</th>
                <th>Observação</th>
              </tr>
            </thead>
            <tbody>${itemRows}</tbody>
          </table>
        </div>
      </article>
    `;

    return `${chartsHtml}${cardsHtml}${itemTableHtml}`;
  }

  function renderCiSociosCharts() {
    const socios = getRateioSocios(ci);
    if (!socios) {
      destroyChart("ciSociosDesembolso");
      destroyChart("ciSociosPagoPendente");
      return;
    }
    const itens = getRateioItens(ci);
    const adeildo = findSocio(socios, "adeildo") || {};
    const faustino = findSocio(socios, "faustino") || {};
    const semPagador = itens.reduce((s, it) => s + (it.pendente_sem_pagador || 0), 0);

    const pagoAdeildo = adeildo.total_desembolsado || 0;
    const pagoFaustino = faustino.total_desembolsado || 0;
    const pendenteAdeildo = adeildo.saldo_pendente_atribuido || 0;
    const pendenteFaustino = faustino.saldo_pendente_atribuido || 0;

    if (pagoAdeildo + pagoFaustino > 0) {
      createChart("ciSociosDesembolso", "#ciSociosDesembolsoChart", {
        type: "doughnut",
        data: {
          labels: ["Adeildo", "Faustino"],
          datasets: [{
            data: [pagoAdeildo, pagoFaustino],
            backgroundColor: [CHART_COLORS[0], CHART_COLORS[1]],
            borderWidth: 3,
            borderColor: "#f3f7fb",
            hoverOffset: 8,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "68%",
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, font: { size: 11 }, padding: 12 } },
            tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${fmtBRL(ctx.raw)}` } }
          }
        }
      });
    } else {
      destroyChart("ciSociosDesembolso");
    }

    const hasPagoPendente = (pagoAdeildo + pagoFaustino + pendenteAdeildo + pendenteFaustino + semPagador) > 0;
    if (hasPagoPendente) {
      // "Sem pagador definido" entra como uma 3ª categoria no eixo X, separada
      // de Adeildo/Faustino — não é somada a nenhum dos dois (ex.: parcelas
      // 10 a 15 do Koko, que ninguém confirmou que vai pagar ainda).
      createChart("ciSociosPagoPendente", "#ciSociosPagoPendenteChart", {
        type: "bar",
        data: {
          labels: ["Adeildo", "Faustino", "Sem pagador definido"],
          datasets: [
            {
              label: "Pago",
              data: [pagoAdeildo, pagoFaustino, 0],
              backgroundColor: CHART_COLORS[4],
              borderRadius: 8,
              borderSkipped: false,
            },
            {
              label: "Pendente",
              data: [pendenteAdeildo, pendenteFaustino, semPagador],
              backgroundColor: CHART_COLORS[2],
              borderRadius: 8,
              borderSkipped: false,
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, pointStyleWidth: 10, padding: 12 } },
            tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtBRL(ctx.raw)}` } }
          },
          scales: {
            y: {
              beginAtZero: true,
              grid: { color: "rgba(109,123,138,.07)" },
              ticks: { font: { size: 10 }, callback: (v) => fmtBRL(v) },
              border: { display: false }
            },
            x: { grid: { display: false }, border: { display: false } }
          }
        }
      });
    } else {
      destroyChart("ciSociosPagoPendente");
    }
  }

  function buildPendenciasPane() {
    const alertCards = ci.alertas.map((alerta, i) => `
      <div class="ci-pending-card">
        <div class="ci-pending-icon">⚠️</div>
        <div class="ci-pending-body">
          <strong>Pendência ${i + 1} de ${ci.pendencias_cadastro}</strong>
          <p>${escapeHtml(alerta)}</p>
        </div>
      </div>
    `).join("");

    return `
      <div class="ci-pending-list">
        ${alertCards}
        <div class="ci-pending-card" style="border-left-color:var(--muted)">
          <div class="ci-pending-icon">📋</div>
          <div class="ci-pending-body">
            <strong>Regularização / Documentos da empresa</strong>
            <p>CREMERJ PJ já registrado (pago em 11/03/2026). Pendente cadastrar: alvará, CNES, licenciamento sanitário, contador, custos de abertura e outros documentos.</p>
            <div class="ci-pending-meta">Responsável: Adeildo · Status: aguardando levantamento completo dos custos</div>
          </div>
        </div>
        <div class="ci-pending-card" style="border-left-color:var(--navy)">
          <div class="ci-pending-icon">ℹ️</div>
          <div class="ci-pending-body">
            <strong>Como cadastrar novos itens</strong>
            <p>Adicione ao arquivo <code>data-private/custos-investimentos.local.json</code> e atualize o summary em <code>data/custos-investimentos-summary.local.json</code>. Os dados são carregados automaticamente ao recarregar o painel.</p>
          </div>
        </div>
      </div>
    `;
  }

  const panes = {
    resumo:       buildResumoPane(),
    recorrentes:  buildRecorrentesPane(),
    equipamentos: buildEquipamentosPane(),
    socios:       buildSociosPane(),
    pendencias:   buildPendenciasPane(),
  };

  Object.entries(panes).forEach(([key, html]) => {
    const el = document.querySelector(`#ci-${key}`);
    if (el) el.innerHTML = html;
  });

  // Os canvases dos gráficos de sócios só existem no DOM depois do innerHTML
  // acima — precisam ser criados agora, e não dentro de buildSociosPane().
  renderCiSociosCharts();

  document.querySelectorAll(".ci-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".ci-tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".ci-pane").forEach((p) => p.setAttribute("hidden", ""));
      btn.classList.add("active");
      const pane = document.querySelector(`#ci-${btn.dataset.ciTab}`);
      if (pane) pane.removeAttribute("hidden");
      // A aba "Sócios" nasce oculta ([hidden] = display:none), então os canvases
      // dos gráficos são criados com largura/altura zero. Ao reexibir a aba,
      // força o Chart.js a remedir o container e redesenhar corretamente.
      if (btn.dataset.ciTab === "socios") resizeCharts();
    });
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

  statsContainer.innerHTML = state.automacoes.resumo.map((item) => statCardHtml("automation-stat-card", item)).join("");

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


// ── Saúde Operacional (M3) — painel na seção Automações ──────────────────────

// Níveis de alerta: rótulo curto para operador não técnico + classe CSS.
const SAUDE_NIVEIS = {
  ok:           { rotulo: "OK",           cls: "saude-ok" },
  atencao:      { rotulo: "Atenção",      cls: "saude-atencao" },
  critico:      { rotulo: "Crítico",      cls: "saude-critico" },
  desconhecido: { rotulo: "Desconhecido", cls: "saude-desconhecido" },
};

function saudeNivel(status) {
  return SAUDE_NIVEIS[String(status || "").toLowerCase()] || SAUDE_NIVEIS.desconhecido;
}

function saudeFormataData(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return escapeHtml(String(iso));
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function renderSaudeOperacional() {
  const panel  = document.querySelector("#saudePanel");
  const banner = document.querySelector("#saudeBannerCritico");
  if (!panel) return;

  const dados = state.saudeOperacional;
  // Fallback elegante: sem dados (nem demo), o painel fica oculto e nada quebra.
  if (!dados || !Array.isArray(dados.indicadores)) {
    panel.hidden = true;
    if (banner) banner.hidden = true;
    return;
  }
  panel.hidden = false;

  const geral = saudeNivel(dados.status_geral);
  const chip  = panel.querySelector("#saudeStatusGeral");
  if (chip) {
    chip.textContent = `Status geral: ${geral.rotulo}`;
    chip.className = `saude-status-chip ${geral.cls}`;
  }

  const meta = panel.querySelector("#saudeMeta");
  if (meta) {
    // "Pipeline real" e não "VPS": o gerador pode rodar localmente também.
    const fonte = dados.source?.dadosReais === true ? "Pipeline real" : "Dados demonstrativos";
    meta.innerHTML = `
      <span class="saude-meta-item" tabindex="0"
        data-tip="Quando o pipeline da VPS gerou este retrato de saúde pela última vez."
        aria-label="Última atualização: ${escapeHtml(saudeFormataData(dados.ultima_atualizacao))}">
        Última atualização: <strong>${escapeHtml(saudeFormataData(dados.ultima_atualizacao))}</strong>
      </span>
      <span class="saude-meta-item">Fonte: <strong>${escapeHtml(fonte)}</strong></span>`;
  }

  const grid = panel.querySelector("#saudeGrid");
  if (grid) {
    grid.innerHTML = dados.indicadores.map((ind) => {
      const nivel = saudeNivel(ind.status);
      const tip   = ind.tip ? escapeHtml(String(ind.tip)) : "";
      return `
        <article class="saude-card ${nivel.cls}"${tip ? ` tabindex="0" data-tip="${tip}" aria-label="${escapeHtml(String(ind.label || ""))}: ${nivel.rotulo}. ${tip}"` : ""}>
          <span class="saude-card-label">${escapeHtml(String(ind.label || "—"))}</span>
          <strong class="saude-card-status">${nivel.rotulo}</strong>
          ${ind.detalhe ? `<small class="saude-card-detalhe">${escapeHtml(String(ind.detalhe))}</small>` : ""}
        </article>`;
    }).join("");
  }

  const alertasBox = panel.querySelector("#saudeAlertas");
  const alertas = Array.isArray(dados.alertas) ? dados.alertas : [];
  if (alertasBox) {
    alertasBox.innerHTML = alertas.length === 0
      ? `<p class="saude-vazio">Nenhum alerta ativo — tudo funcionando dentro do esperado.</p>`
      : `<ul class="saude-alertas">${alertas.map((a) => {
          const nivel = saudeNivel(a.nivel);
          return `
            <li class="saude-alerta ${nivel.cls}">
              <span class="badge ${nivel.cls}">${nivel.rotulo}</span>
              <div class="saude-alerta-body">
                <strong>${escapeHtml(String(a.titulo || "Alerta"))}</strong>
                ${a.mensagem ? `<p>${escapeHtml(String(a.mensagem))}</p>` : ""}
                ${a.proximo_passo ? `<small><b>Próximo passo:</b> ${escapeHtml(String(a.proximo_passo))}</small>` : ""}
              </div>
            </li>`;
        }).join("")}</ul>`;
  }

  // Banner no Painel Geral: SÓ com alerta crítico (discrição por padrão).
  if (banner) {
    const criticos = alertas.filter((a) => String(a.nivel).toLowerCase() === "critico");
    if (criticos.length > 0) {
      banner.hidden = false;
      banner.innerHTML = `
        <strong>⚠ ${criticos.length} alerta(s) crítico(s) na operação.</strong>
        <span>${escapeHtml(String(criticos[0].titulo || ""))} — ver detalhes na seção Automações → Saúde Operacional.</span>`;
    } else {
      banner.hidden = true;
      banner.innerHTML = "";
    }
  }
}

// ── Ações Operacionais (M4) — painel na seção Automações ─────────────────────
// Consome o MESMO estado da Saúde Operacional (state.saudeOperacional — real
// ou demo, sem fetch extra) via buildOperationalActions (js/operational-actions.js).

function renderAcoesOperacionais() {
  const panel = document.querySelector("#acoesPanel");
  if (!panel) return;

  // Mesma regra do painel de Saúde: sem payload nenhum, fica oculto.
  if (!state.saudeOperacional) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const lista = panel.querySelector("#acoesLista");
  if (!lista) return;

  const acoes = buildOperationalActions(state.saudeOperacional);

  if (acoes.length === 0) {
    lista.innerHTML = `
      <div class="acoes-vazio">
        <strong>Nenhuma ação operacional pendente.</strong>
        <span>A Saúde Operacional não trouxe alertas no último resumo carregado.</span>
      </div>`;
    return;
  }

  lista.innerHTML = `
    <ul class="acoes-lista">
      ${acoes.map((acao, i) => {
        const nivel = saudeNivel(acao.nivel);
        const quando = acao.geradoEm ? saudeFormataData(acao.geradoEm) : "";
        return `
          <li class="acoes-item ${nivel.cls}">
            <div class="acoes-topo">
              <span class="badge ${nivel.cls}">${nivel.rotulo}</span>
              <strong class="acoes-titulo">${escapeHtml(acao.titulo)}</strong>
            </div>
            <p class="acoes-acao">${escapeHtml(acao.acao)}</p>
            <div class="acoes-meta">
              <span>Origem: ${escapeHtml(acao.origem)}</span>
              <span>Status: <b>${escapeHtml(acao.status)}</b></span>
              ${quando ? `<span>Gerado: ${escapeHtml(quando)}</span>` : ""}
              <button type="button" class="acoes-toggle" data-acao-idx="${i}"
                aria-expanded="false" aria-controls="acoesPasso${i}">Ver próximo passo</button>
            </div>
            <small class="acoes-passo" id="acoesPasso${i}" hidden>
              <b>Próximo passo:</b> ${escapeHtml(acao.proximoPasso)}
            </small>
          </li>`;
      }).join("")}
    </ul>`;

  lista.querySelectorAll(".acoes-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const alvo = document.getElementById(`acoesPasso${btn.dataset.acaoIdx}`);
      if (!alvo) return;
      const aberto = !alvo.hidden;
      alvo.hidden = aberto;
      btn.setAttribute("aria-expanded", String(!aberto));
      btn.textContent = aberto ? "Ver próximo passo" : "Ocultar próximo passo";
    });
  });
}

// ── Cérebro Operacional (M8, esqueleto) — painel no Painel Geral ─────────────
// Computa AO VIVO a partir do estado já carregado (via operational-brain.js);
// quando não há nenhuma fonte, usa o demo commitável como vitrine.

function renderCerebroOperacional() {
  const panel = document.querySelector("#cerebroPanel");
  if (!panel) return;

  const payloads = {
    saudeOperacional: state.saudeOperacional,
    b2bStats: (state.crmClinicasRaw?.length || state.leadsSummary)
      ? buildB2BStats({
          leads: state.leadsSummary?.leads || state.leads || [],
          clinicas: state.crmClinicasRaw || [],
          contatosB2B: state.crmContatosB2B || [],
          followupStats: state.followupClinicasSummary?.clinicas || null,
        }) : null,
    followupPacientes: state.followupSummary || null,
    financeiro: state.financeiro_summary || null,
    custos: state.custosInvestimentos || null,
    marketing: state.marketingSeo || null,
    auditoria: state.auditoria || null,
    ultimosLancamentos: state.ultimosLancamentos || null,
  };

  const temFonteViva = Object.values(payloads).some((v) => v);
  if (!temFonteViva && !state.cerebroDemo) {
    panel.hidden = true;
    state.cerebroLive = null;
    return;
  }
  panel.hidden = false;

  const brainState = buildOperationalBrainState(payloads);
  const acoes = {
    acoesOperacionais: state.saudeOperacional ? buildOperationalActions(state.saudeOperacional) : [],
    acoesB2B: buildB2BActions({
      leads: payloads.b2bStats ? (state.leadsSummary?.leads || state.leads || []) : [],
      clinicas: state.crmClinicasRaw || [],
      contatosB2B: state.crmContatosB2B || [],
      followupStats: state.followupClinicasSummary?.clinicas || null,
    }),
  };
  const dq = buildDecisionQueue(brainState, acoes);
  // M9: briefing REAL (camada acima do M8) — shape completo com resumo
  // executivo, riscos e por área. O demo entra só sem fontes vivas.
  const briefingReal = buildDailyBriefingReal(brainState, dq, payloads);
  const briefing = briefingReal;
  const projecoes = buildProjectionSkeleton(brainState);
  const demo = state.cerebroDemo;
  const briefingDemo = state.briefingDemo;

  const chip = panel.querySelector("#cerebroStatus");
  if (chip) {
    chip.textContent = temFonteViva ? "Ao vivo" : "Demo seguro";
  }

  const top3Box = panel.querySelector("#cerebroTop3");
  if (top3Box) {
    const top3 = dq.top3.length ? dq.top3
      : (demo?.prioridades_exemplo || []).map((p) => ({
          titulo: p.titulo, nivel: p.nivel, origem: p.origem, score: "" }));
    top3Box.innerHTML = top3.length
      ? top3.map((p) => `
          <li>
            <span class="badge ${p.nivel === "alta" ? "saude-critico" : p.nivel === "baixa" ? "saude-ok" : "saude-atencao"}">${escapeHtml(String(p.nivel || ""))}</span>
            <span class="cerebro-top3-titulo">${escapeHtml(String(p.titulo || ""))}</span>
            <small>${escapeHtml(String(p.origem || ""))}</small>
          </li>`).join("")
      : `<li class="cerebro-vazio">Nenhuma prioridade pendente agora.</li>`;
  }

  // Briefing exibido: real quando há fontes vivas; demo commitável senão.
  const b = briefing.status !== "demo" ? briefing : (briefingDemo || briefing);

  // M10: expõe o cálculo do dia para o card "Hoje eu tenho que fazer o quê?"
  // (renderHojeAcoes) — mesmo estado, sem recomputar nem buscar nada novo.
  state.cerebroLive = { brainState, decisionQueue: dq, briefing: b };

  const execBox = panel.querySelector("#cerebroResumoExec");
  if (execBox) {
    const nivelCls = b.status === "critico" ? "saude-critico"
      : b.status === "ok" ? "saude-ok" : "saude-atencao";
    execBox.hidden = false;
    execBox.className = `cerebro-resumo-exec ${nivelCls}`;
    execBox.innerHTML = `<strong>${escapeHtml(String(b.resumoExecutivo || "—"))}</strong>`;
  }

  // M9.1: síntese compacta no topo — o detalhe fica no <details> colapsável.
  const retornoBox = panel.querySelector("#cerebroMaiorRetorno");
  if (retornoBox) retornoBox.textContent = String(b.maiorRetorno || "—");
  const hojeBox = panel.querySelector("#cerebroAcoesHoje");
  if (hojeBox) hojeBox.textContent = String(Array.isArray(b.fazerHoje) ? b.fazerHoje.length : 0);

  const brBox = panel.querySelector("#cerebroBriefing");
  if (brBox) {
    const linha = (rotulo, itens) => (Array.isArray(itens) && itens.length)
      ? `<p><b>${escapeHtml(rotulo)}</b> ${itens.map((t) => escapeHtml(String(t))).join(" · ")}</p>` : "";
    brBox.innerHTML =
      linha("Aconteceu:", b.aconteceu) +
      linha("Mudou:", b.mudou) +
      linha("Atrasado:", b.atrasado) +
      linha("Fazer hoje:", b.fazerHoje) +
      linha("Pode esperar:", b.podeEsperar) +
      `<p><b>Maior retorno:</b> ${escapeHtml(String(b.maiorRetorno || "—"))}</p>`;
  }

  const riscosBox = panel.querySelector("#cerebroRiscos");
  if (riscosBox) {
    const riscos = Array.isArray(b.riscos) ? b.riscos : [];
    riscosBox.hidden = riscos.length === 0;
    riscosBox.innerHTML = riscos.length
      ? `<b>Riscos:</b> ${riscos.map((r) => escapeHtml(String(r))).join(" · ")}` : "";
  }

  const areaBox = panel.querySelector("#cerebroPorArea");
  if (areaBox) {
    const pa = b.porArea && typeof b.porArea === "object" ? b.porArea : {};
    const comConteudo = Object.entries(pa).filter(([, v]) => Array.isArray(v) && v.length);
    areaBox.hidden = comConteudo.length === 0;
    areaBox.innerHTML = comConteudo.length
      ? `<b>Por área:</b> ` + comConteudo.map(([area, itens]) =>
          `<span class="cerebro-area"><i>${escapeHtml(area)}</i> ${escapeHtml(String(itens[0]))}</span>`
        ).join(" ")
      : "";
  }

  const projBox = panel.querySelector("#cerebroProjecoes");
  if (projBox) {
    projBox.innerHTML = projecoes.map((pr) => `
      <div class="cerebro-proj" tabindex="0" data-tip="${escapeHtml(String(pr.premissa || ""))}"
        aria-label="${escapeHtml(String(pr.label))}: ${pr.valorBase === null ? "sem dados" : pr.valorBase}. ${escapeHtml(String(pr.premissa || ""))}">
        <span>${escapeHtml(String(pr.label))}</span>
        <strong>${pr.valorBase === null ? "—" : escapeHtml(String(pr.valorBase))}</strong>
        <small>${escapeHtml(String(pr.status))}</small>
      </div>`).join("");
  }
}

// ── "Hoje eu tenho que fazer o quê?" (M10 / M10.1) — card compacto no hub ────
// Consome o cálculo JÁ feito pelo Cérebro Operacional (state.cerebroLive) via
// buildTodayQueue (js/today-actions.js). Sem fetch novo, sem persistência —
// marcar feito/pendente fica para etapa futura.
// M10.1 (UX BlueDox): só a ação nº1 + status + total ficam sempre visíveis;
// a fila inteira (nº1 incluído, com motivo/origem/próximo passo) mora dentro
// de um <details> fechado por padrão — "Ver fila completa".

const HOJE_NIVEIS = {
  critico: { rotulo: "Crítico", cls: "saude-critico" },
  alta:    { rotulo: "Alta",    cls: "saude-critico" },
  media:   { rotulo: "Média",   cls: "saude-atencao" },
  baixa:   { rotulo: "Baixa",   cls: "saude-ok" },
};

const HOJE_STATUS_CHIP = {
  critico: { texto: "Dia crítico",  cls: "saude-critico" },
  atencao: { texto: "Ao vivo",      cls: "saude-atencao" },
  ok:      { texto: "Em dia",       cls: "saude-ok" },
  demo:    { texto: "Demo seguro",  cls: "saude-atencao" },
  vazio:   { texto: "Sem pendência", cls: "saude-ok" },
};

function renderHojeAcoes() {
  const panel = document.querySelector("#hojePanel");
  if (!panel) return;

  // Mesma regra do Cérebro: sem cálculo do dia (nem demo), o card fica oculto.
  if (!state.cerebroLive) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const hoje = buildTodayQueue(state.cerebroLive);

  const chip = panel.querySelector("#hojeStatus");
  if (chip) {
    const info = HOJE_STATUS_CHIP[hoje.status] || HOJE_STATUS_CHIP.vazio;
    chip.textContent = info.texto;
    chip.className = `saude-status-chip ${info.cls}`;
  }

  const principalBox = panel.querySelector("#hojeAcaoPrincipal");
  const totalChip = panel.querySelector("#hojeTotalChip");
  const filaWrap = panel.querySelector("#hojeFilaWrap");
  const filaLista = panel.querySelector("#hojeFila");
  if (!principalBox || !totalChip || !filaWrap || !filaLista) return;

  const p = hoje.acaoPrincipal;
  if (!p) {
    principalBox.className = "hoje-acao-principal saude-ok";
    principalBox.innerHTML = `
      <strong class="hoje-acao-titulo">Nada urgente na fila de hoje.</strong>
      <span class="hoje-acao-sub">Seguir a rotina planejada.</span>`;
    totalChip.hidden = true;
    filaWrap.hidden = true;
    filaLista.innerHTML = "";
    return;
  }

  // Linha sempre visível: só o essencial (nível + título da ação nº 1).
  const nivelP = HOJE_NIVEIS[p.nivel] || HOJE_NIVEIS.media;
  principalBox.className = `hoje-acao-principal ${nivelP.cls}`;
  principalBox.innerHTML = `
    <span class="badge ${nivelP.cls}">${nivelP.rotulo}</span>
    <strong class="hoje-acao-titulo">${escapeHtml(p.titulo)}</strong>`;

  totalChip.hidden = false;
  totalChip.textContent = `${hoje.totalFila} ${hoje.totalFila === 1 ? "ação" : "ações"} na fila`;

  // Fila completa (nº1 + próximas, com motivo/origem/próximo passo) só
  // aparece se o usuário abrir o <details> — fechado por padrão.
  filaWrap.hidden = false;
  filaLista.innerHTML = [p, ...hoje.proximas].map((a) => {
    const nivel = HOJE_NIVEIS[a.nivel] || HOJE_NIVEIS.media;
    return `
      <li>
        <span class="hoje-fila-num" aria-hidden="true">${a.ordem}</span>
        <span class="badge ${nivel.cls}">${nivel.rotulo}</span>
        <div class="hoje-fila-item-body">
          <strong class="hoje-proxima-titulo">${escapeHtml(a.titulo)}</strong>
          <span class="hoje-fila-item-meta">${escapeHtml(a.motivo)} · Origem: ${escapeHtml(a.origem)}</span>
          ${a.proximoPasso && a.proximoPasso !== "—"
            ? `<span class="hoje-fila-item-passo"><b>Próximo passo:</b> ${escapeHtml(a.proximoPasso)}</span>` : ""}
        </div>
      </li>`;
  }).join("");
}

// ── Próximas Ações B2B/PCMSO (M5) — painel no Painel Geral ───────────────────
// Consome estado JÁ carregado e gateado (leadsSummary, crm normalizado,
// crmContatosB2B, followupClinicasSummary) via buildB2BActions/buildB2BStats
// (js/b2b-actions.js). Sem fetch novo, sem PII.

const B2B_PRIO = {
  alta:  { rotulo: "Alta",  cls: "saude-critico" },
  media: { rotulo: "Média", cls: "saude-atencao" },
  baixa: { rotulo: "Baixa", cls: "saude-ok" },
};

function renderB2BAcoes() {
  const panel = document.querySelector("#b2bPanel");
  if (!panel) return;

  const payloads = {
    leads: state.leadsSummary?.leads || state.leads || [],
    clinicas: state.crmClinicasRaw || [],
    contatosB2B: state.crmContatosB2B || [],
    followupStats: state.followupClinicasSummary?.clinicas || null,
  };

  // Sem NENHUMA fonte B2B carregada: painel oculto (nada a priorizar).
  if (!payloads.leads.length && !payloads.clinicas.length &&
      !payloads.contatosB2B.length && !payloads.followupStats) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const stats = buildB2BStats(payloads);
  const acoes = buildB2BActions(payloads);

  const statsBox = panel.querySelector("#b2bStats");
  if (statsBox) {
    const item = (rotulo, valor) => `
      <div class="b2b-stat">
        <span>${escapeHtml(rotulo)}</span>
        <strong>${valor === null ? "—" : valor}</strong>
      </div>`;
    statsBox.innerHTML =
      item("Oportunidades B2B", stats.totalOportunidades) +
      item("Precisam follow-up", stats.precisamFollowup) +
      item("Em conversa", stats.emConversa) +
      item("Aguardando retorno", stats.aguardandoRetorno) +
      item("Convertidas", stats.convertidas) +
      item("Sem próximo passo", stats.semProximoPasso);
  }

  const lista = panel.querySelector("#b2bLista");
  if (!lista) return;

  if (acoes.length === 0) {
    lista.innerHTML = `
      <div class="acoes-vazio">
        <strong>Nenhuma ação comercial pendente.</strong>
        <span>Os summaries atuais não indicam follow-up B2B em aberto.</span>
      </div>`;
    return;
  }

  lista.innerHTML = `
    <ul class="acoes-lista">
      ${acoes.map((acao) => {
        const prio = B2B_PRIO[acao.prioridade] || B2B_PRIO.media;
        return `
          <li class="acoes-item ${prio.cls}">
            <div class="acoes-topo">
              <span class="badge ${prio.cls}">${prio.rotulo}</span>
              <strong class="acoes-titulo">${escapeHtml(acao.titulo)}</strong>
            </div>
            <p class="acoes-acao">${escapeHtml(acao.motivo)}</p>
            <div class="acoes-meta">
              <span>Origem: ${escapeHtml(acao.origem)}</span>
              <span class="b2b-passo"><b>Próximo passo:</b> ${escapeHtml(acao.proximoPasso)}</span>
            </div>
          </li>`;
      }).join("")}
    </ul>`;
}

// ── Auditoria M1 — card "Últimas alterações" (seção Automações) ───────────────

// Rótulos amigáveis por slug de ação. Slug desconhecido cai no próprio slug
// (novas ações aparecem sem quebrar o card).
//
// Dois conjuntos convivem de propósito: os slugs com underscore vêm da trilha
// HISTÓRICA importada da aba Log Auditoria, que continua no banco e ainda
// aparece nos eventos mais antigos; os slugs com ponto são os da API do
// Núcleo M15, únicos gerados desde o M23. Remover os antigos deixaria o
// histórico ilegível.
const AUDITORIA_ACAO_LABELS = {
  // Trilha histórica (origem Google Sheets, somente leitura).
  update_lead_stage:            "Etapa de lead alterada",
  update_crm_clinica_etapa:     "Etapa de clínica alterada",
  mirror_etapa_pcmso:           "Espelhamento na base PCMSO",
  create_lead:                  "Lead criado",
  create_paciente:              "Paciente cadastrado",
  create_espirometria:          "Espirometria registrada",
  create_consulta:              "Consulta registrada",
  create_clinica_b2b:           "Clínica B2B cadastrada",
  registrar_interacao_clinica:  "Interação com clínica",
  registrar_interacao_paciente: "Follow-up de paciente",
  registrar_atendimento_pastore:"Atendimento Pastore",
  upsert_contato_b2b:           "Contato B2B atualizado",
  teste_apagar:                 "Teste (apagar)",
  // Núcleo M15 / PostgreSQL (M23 em diante).
  "auth.token_emitido":         "Sessão iniciada",
  "auth.logout":                "Sessão encerrada",
  "auth.falha":                 "Falha de autenticação",
  "auth.bloqueado_rate_limit":  "Login bloqueado por limite",
  "pessoa.criada":              "Pessoa cadastrada",
  "pessoa.atualizada":          "Pessoa atualizada",
  "pessoa.contato_adicionado":  "Contato de pessoa adicionado",
  "pessoa.consentimento_registrado": "Consentimento registrado",
  "lead.criado":                "Lead criado",
  "lead.atualizado":            "Lead atualizado",
  "espirometria.criada":        "Espirometria registrada",
  "espirometria.atualizada":    "Espirometria atualizada",
  "consulta.criada":            "Consulta registrada",
  "consulta.atualizada":        "Consulta atualizada",
  "atendimento.criado":         "Atendimento registrado",
  "followup.criado":            "Follow-up criado",
  "followup.concluido":         "Follow-up concluído",
  "followup.nova_tentativa":    "Follow-up reagendado",
  "followup.whatsapp_confirmado": "Follow-up confirmado por WhatsApp",
  "interacao.criada":           "Interação registrada",
  "crm.contato_registrado":     "Contato de CRM registrado",
  "lancamento.criado":          "Lançamento criado",
  "lancamento.atualizado":      "Lançamento atualizado",
  "repasse.criado":             "Repasse registrado",
  "parceiro.criado":            "Parceiro cadastrado",
  "parceiro.atualizado":        "Parceiro atualizado",
  "parceiro.consolidado":       "Parceiro consolidado",
  "parceiro.recebeu_consolidacao": "Parceiro recebeu consolidação",
  "unidade.criada":             "Unidade cadastrada",
  "unidade.atualizada":         "Unidade atualizada",
  "contato_parceiro.criado":    "Contato de parceiro cadastrado",
  "contato_parceiro.atualizado":"Contato de parceiro atualizado",
  "parceria.criada":            "Parceria criada",
  "parceria.atualizada":        "Parceria atualizada",
  "encaminhamento.criado":      "Encaminhamento criado",
  "encaminhamento.atualizado":  "Encaminhamento atualizado",
  "encaminhamento.financeiro_atualizado": "Encaminhamento — financeiro atualizado",
  "usuario.criado":             "Usuário criado",
  "usuario.senha_redefinida":   "Senha de usuário redefinida",
  "pcmso.rejeitado":            "Escrita PCMSO rejeitada",
};

// Rótulos por TIPO de entidade (nome da tabela auditada). Substituem o antigo
// entidade_id no detalhe do evento: o snapshot deixou de exportar o
// identificador da linha (M23, 2º incidente), e o tipo é o contexto que
// realmente ajuda a ler a trilha. Tipo desconhecido cai no próprio valor.
const AUDITORIA_ENTIDADE_LABELS = {
  people:             "Pessoa",
  leads:              "Lead",
  followups:          "Follow-up",
  interactions:       "Interação",
  spirometry_exams:   "Espirometria",
  consultations:      "Consulta",
  financial_entries:  "Lançamento",
  partner_transfers:  "Repasse",
  partners:           "Parceiro",
  partner_units:      "Unidade",
  partner_contacts:   "Contato de parceiro",
  partnerships:       "Parceria",
  partner_referrals:  "Encaminhamento",
  users:              "Usuário",
  attendances:        "Atendimento",
};

function renderAuditoria() {
  const panel = document.querySelector("#auditoriaPanel");
  if (!panel) return;

  const stats = state.auditoria?.stats;
  const eventos = state.auditoria?.ultimos_eventos;

  // Sem summary (pipeline ainda não rodou / aba ainda não existe): card oculto.
  if (!stats || !Array.isArray(eventos)) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const statsContainer = panel.querySelector("#auditoriaStats");
  const listContainer  = panel.querySelector("#auditoriaList");
  if (!statsContainer || !listContainer) return;

  const operadores = Object.keys(stats.por_operador || {}).length;
  statsContainer.innerHTML = [
    { key: "auditoriaTotal",      label: "Escritas registradas", value: stats.total_eventos ?? 0 },
    { key: "auditoriaErros",      label: "Erros de gravação",    value: stats.erros ?? 0,
      extraClass: (stats.erros ?? 0) > 0 ? "auditoria-stat-alerta" : "" },
    { key: "auditoriaOperadores", label: "Operadores",           value: operadores },
  ].map((item) => statCardHtml("automation-stat-card", item)).join("");

  if (!eventos.length) {
    listContainer.innerHTML = `<p class="auditoria-empty">Nenhuma escrita registrada ainda — as alterações feitas pelo painel aparecerão aqui.</p>`;
    return;
  }

  listContainer.innerHTML = `
    <ul class="auditoria-events">
      ${eventos.map((evt) => {
        const acaoLabel = AUDITORIA_ACAO_LABELS[evt.acao] || evt.acao || "Ação desconhecida";
        const ok = String(evt.resultado || "").toLowerCase().startsWith("ok");
        // M23 (2º incidente): o snapshot não traz mais entidade_id — um
        // identificador de linha do banco não é exportável para o navegador
        // e não dizia nada ao leitor. O contexto útil é o TIPO da entidade.
        const entidadeLabel = AUDITORIA_ENTIDADE_LABELS[evt.entidade_tipo] || evt.entidade_tipo;
        const detalhe = [entidadeLabel, evt.operador ? `por ${evt.operador}` : ""]
          .filter(Boolean).map((v) => escapeHtml(String(v))).join(" · ");
        return `
          <li class="auditoria-event">
            <span class="auditoria-event-time">${escapeHtml(String(evt.timestamp || ""))}</span>
            <div class="auditoria-event-body">
              <strong>${escapeHtml(acaoLabel)}</strong>
              ${detalhe ? `<small>${detalhe}</small>` : ""}
            </div>
            <span class="badge ${ok ? "lancamento-badge-ok" : "lancamento-badge-pendente"}">${ok ? "OK" : "Erro"}</span>
          </li>`;
      }).join("")}
    </ul>`;
}

function renderRuntimeStatus() {
  const container = document.querySelector("#runtimeStatus");
  if (!container) return;

  // M23 — este card mostra a FONTE CANÔNICA. Antes anunciava o Google Sheets
  // como fonte privada do painel; a planilha deixou de ser fonte e não pode
  // mais aparecer aqui como origem do dado operacional.
  const fonte = state.runtimeStatus?.dataSource;
  const canonica = Boolean(fonte?.canonical === "postgresql" && fonte?.safeToDisplay);

  const statusLabel = canonica
    ? (fonte.statusLabel || "Fonte única operacional")
    : "Fonte canônica não declarada";

  const badgeClass = canonica ? "success" : "neutral";

  const description = canonica
    ? "Todo dado operacional vive no PostgreSQL e é gravado pela API autenticada do Núcleo M15. Nenhuma credencial ou identificador privado é exibido no painel."
    : "Rode a atualização local para declarar a fonte canônica. O painel não substitui dado operacional por exemplos.";

  container.innerHTML = `
    <div>
      <span class="runtime-kicker">Fonte operacional</span>
      <strong>${escapeHtml(canonica ? (fonte.name || "PostgreSQL") : "PostgreSQL")}</strong>
      <p>${description}</p>
    </div>
    <div class="runtime-status-side">
      <span class="runtime-badge ${badgeClass}">${escapeHtml(statusLabel)}</span>
      <small>Segredos fora do GitHub</small>
    </div>
  `;
}


function renderDataFreshness() {
  const container = document.querySelector("#dataFreshness");
  if (!container) return;

  const source = state.dashboardSummary?.source;
  const runtimeSource = state.runtimeStatus?.dataSource;

  const isSafe = Boolean(
    source?.safeToDisplay &&
    source?.containsPersonalData === false &&
    source?.containsHealthData === false
  );

  if (!isSafe) {
    container.innerHTML = `
      <div class="freshness-meta">
        <span>Fonte dos dados</span>
        <strong>Ambiente de preview</strong>
        <small>Dados ainda não carregados neste ambiente de preview.</small>
      </div>
    `;
    return;
  }

  // M23 — o rótulo da fonte vem da declaração canônica. Nenhuma tela pode
  // voltar a apresentar o Google Sheets como origem do dado operacional.
  const isPostgres = Boolean(
    runtimeSource?.canonical === "postgresql" && runtimeSource?.safeToDisplay
  );

  const sourceLabel = isPostgres
    ? (runtimeSource.name || "PostgreSQL — Núcleo Operacional M15")
    : "Arquivo local (preview)";
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

function renderLancamentos() {
  const statsEl    = document.querySelector("#lancamentosStats");
  const timelineEl = document.querySelector("#lancamentosTimeline");
  const subtitleEl = document.querySelector("#lancamentosSubtitle");
  if (!statsEl || !timelineEl) return;

  const data = state.ultimosLancamentos;

  if (!data) {
    statsEl.innerHTML = statCardHtml("lancamentos-stat-card", {
      key: "lancamentosStatusIndisponivel",
      label: "Status",
      value: "—",
      valueStyle: "font-size:1.1rem",
      hint: "Execute o gerador"
    });
    timelineEl.innerHTML = `
      <div class="lancamentos-empty">
        <p>Dados não disponíveis. Execute o gerador de últimos lançamentos:</p>
        <code>python3 painel-soprolife/scripts/generate-ultimos-lancamentos.py --write</code>
      </div>`;
    return;
  }

  const st = data.stats || {};
  const cards = [
    { key: "lancamentosEventos",         label: "Eventos",    value: st.totalEventos ?? 0,      hint: "na timeline",       extraClass: "accent-teal"   },
    { key: "lancamentosHoje",             label: "Hoje",       value: st.hoje ?? 0,               hint: "nesta data",        extraClass: "accent-navy"   },
    { key: "lancamentosAltaPrioridade",   label: "Alta prio.", value: st.prioridade_alta ?? 0,    hint: "requer atenção",    extraClass: "accent-warn"   },
    { key: "lancamentosPendencias",       label: "Pendências", value: st.pendencias ?? 0,         hint: "erros ou avisos",   extraClass: "accent-danger" },
  ];
  statsEl.innerHTML = cards.map((c) => statCardHtml("lancamentos-stat-card", c)).join("");

  if (subtitleEl) {
    const gen     = data.source?.generatedAt;
    const eventos = Array.isArray(data.eventos) ? data.eventos : [];
    subtitleEl.textContent = gen
      ? `Gerado em ${formatDateTime(gen)} · ${eventos.length} evento(s)`
      : `${eventos.length} evento(s) recentes`;
  }

  const eventos = Array.isArray(data.eventos) ? data.eventos : [];
  if (eventos.length === 0) {
    timelineEl.innerHTML = `<div class="lancamentos-empty"><p>Nenhum evento registrado ainda.</p></div>`;
    return;
  }

  // Categoria → cor/ícone textual
  const CAT_META = {
    leads:      { cls: "cat-leads",     label: "Leads"      },
    crm:        { cls: "cat-crm",       label: "CRM"        },
    followup:   { cls: "cat-followup",  label: "Follow-up"  },
    b2b:        { cls: "cat-b2b",       label: "B2B"        },
    marketing:  { cls: "cat-marketing", label: "Marketing"  },
    financeiro: { cls: "cat-financeiro",label: "Financeiro" },
    custos:     { cls: "cat-custos",    label: "Custos"     },
    sistema:    { cls: "cat-sistema",   label: "Sistema"    },
  };

  function catBadge(categoria) {
    const m = CAT_META[categoria] || { cls: "cat-sistema", label: escapeHtml(categoria || "—") };
    return `<span class="lancamento-cat ${m.cls}">${m.label}</span>`;
  }

  function prioBadge(prioridade) {
    if (prioridade === "alta")
      return `<span class="badge lancamento-badge-alta">Alta</span>`;
    if (prioridade === "baixa")
      return `<span class="badge lancamento-badge-baixa">Baixa</span>`;
    return "";
  }

  function statusBadge(status) {
    const map = {
      ok:       ["badge lancamento-badge-ok",       "OK"      ],
      pendente: ["badge lancamento-badge-pendente",  "Pendente"],
      erro:     ["badge lancamento-badge-erro",      "Erro"    ],
      info:     ["badge lancamento-badge-info",      "Info"    ],
    };
    const [cls, lbl] = map[status] || [];
    return cls ? `<span class="${cls}">${lbl}</span>` : "";
  }

  function dotClass(evt) {
    if (evt.status === "erro")           return "dot-erro";
    if (evt.prioridade === "alta")       return "dot-alta";
    if (evt.categoria === "leads" && evt.tipo === "Novo lead") return "dot-lead";
    return `dot-${evt.status || "info"}`;
  }

  function isToday(ts) {
    if (!ts) return false;
    const d = new Date(ts);
    const n = new Date();
    return d.toDateString() === n.toDateString();
  }

  timelineEl.innerHTML = eventos.map((evt, i) => {
    const hoje   = isToday(evt.timestamp);
    const last   = i === eventos.length - 1;
    return `
    <div class="lancamento-item${last ? " last" : ""}${hoje ? " lancamento-hoje" : ""}">
      <div class="lancamento-dot ${dotClass(evt)}"></div>
      <div class="lancamento-body">
        <div class="lancamento-header">
          ${catBadge(evt.categoria)}
          <span class="lancamento-titulo">${escapeHtml(evt.titulo || evt.tipo || "")}</span>
          ${prioBadge(evt.prioridade)}
          ${statusBadge(evt.status)}
        </div>
        <p class="lancamento-descricao">${escapeHtml(evt.descricao || "")}</p>
        <time class="lancamento-time">${escapeHtml(evt.data_br || "—")}</time>
      </div>
    </div>`;
  }).join("");
}
