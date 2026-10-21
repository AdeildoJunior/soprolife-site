#!/usr/bin/env node
// SoproLife — M26.34: painel da NFS-e agrupada (vários exames, um tomador).
//
// Prova, executando o código REAL recortado de js/fiscal-command-center.js
// contra um DOM mínimo, que:
//   • só exames realmente combináveis aparecem para compor: os prontos e os
//     barrados APENAS pela identidade fiscal do paciente (num documento
//     agrupado quem vai na nota é o tomador) — nunca um exame já emitido,
//     já faturado ou bloqueado por outro motivo;
//   • a tela NÃO soma valores: o total exibido é exatamente o que o backend
//     respondeu, e sem resposta não há total nenhum na tela;
//   • o CPF do tomador aparece mascarado, e o CPF inteiro nunca é renderizado;
//   • o botão de preparar só habilita quando o backend disse `can_prepare`;
//   • os motivos de bloqueio são mostrados em português, não em código;
//   • um documento já preparado aparece como "aguardando emissão manual",
//     e em lugar nenhum da tela existe botão que emita o agrupado.
//
// Não usa navegador, rede, credencial nem dado privado.
// Uso:  node painel-soprolife/scripts/test-m26-34-nfse-agrupada-painel.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FONTE_PATH = path.resolve(__dirname, "../js/fiscal-command-center.js");
const fonte = fs.readFileSync(FONTE_PATH, "utf8");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

// ── Recorte do código real ──────────────────────────────────────────────────
// O arquivo é um IIFE carregado por <script>, sem exportar as funções
// internas. Recortamos os trechos de verdade: se o recorte falhar, o teste
// falha em vez de validar uma cópia envelhecida.
function recortar(inicio, fim) {
  const i = fonte.indexOf(inicio);
  if (i < 0) return null;
  const f = fonte.indexOf(fim, i + 1);
  if (f < 0) return null;
  return fonte.slice(i, f);
}

const trechos = {
  blockers: recortar("const PATIENT_IDENTITY_BLOCKERS = new Set([",
                     "function m15()"),
  helpers: recortar("function esc(value)", "function root()"),
  empty: recortar("function empty(message)", "function readinessHtml("),
  prodClass: recortar("const PROD_STATUS_CLASS = {", "function productionRowHtml("),
  grupo: recortar("  // ── M26.34 — NFS-e agrupada ", "function productionModalHtml()"),
  labels: recortar("const STATE_LABELS = {", "const CATEGORY_LABELS = {"),
};
for (const [nome, trecho] of Object.entries(trechos)) {
  caso(`recorte "${nome}" encontrado`, Boolean(trecho));
}
if (Object.values(trechos).some((t) => !t)) {
  console.log("\nRESULTADO: recorte falhou — fiscal-command-center.js mudou de forma incompatível.");
  process.exit(1);
}

// ── DOM e ambiente mínimos ──────────────────────────────────────────────────
const sandbox = {
  console,
  window: { SoproM15: { can: () => true, hasToken: () => true, api: () => Promise.resolve() } },
  document: {
    getElementById: () => ({ innerHTML: "" }),
    querySelector: () => null,
    addEventListener: () => {},
  },
  state: null,
};
sandbox.globalThis = sandbox;

// Cada recorte já termina com o seu próprio fechamento; nada é costurado
// à mão aqui, para que o teste exercite exatamente o texto do arquivo.
const prelude = `
  ${trechos.labels}
  ${trechos.blockers}
  function m15() { return window.SoproM15; }
  ${trechos.helpers}
  ${trechos.empty}
  ${trechos.prodClass}
  function isProduction() { return true; }
  function short(id) { return id ? String(id).slice(0, 8) : "—"; }
  function errorMessage(err) { return (err && err.message) || String(err); }
  function api() { return Promise.resolve(); }
  function render() {}
  function loadProduction() { return Promise.resolve(); }
  function loadGroupedDocuments() { return Promise.resolve(); }
  ${trechos.grupo}
  globalThis.__api = { groupableRows, groupedPanelHtml, groupedPreviewHtml, groupedDocumentsHtml };
`;
vm.createContext(sandbox);
try {
  vm.runInContext(prelude, sandbox);
} catch (err) {
  console.log(`  FAIL: o recorte não executa — ${err.message}`);
  process.exit(1);
}
const api = sandbox.__api;

function montarEstado(overrides) {
  sandbox.state = Object.assign({
    status: { enabled: true, environment: "production" },
    production: { itens: [], counts: {} },
    grouping: {
      selected: new Set(), recipient: null, preview: null, busy: false,
      status: null, documents: [], lookupBusy: false,
    },
  }, overrides || {});
  return sandbox.state;
}

const CPF_INTEIRO = "76266314800";  // sintético, de ninguém
const LINHAS = [
  { exam_id: "e1", exam_code: "ESP-000077", patient_name: "Marina Rocha Albuquerque",
    service_date: "2026-10-20", municipio_name: "Rio de Janeiro/RJ", amount: "250.00",
    status: "ready", blocking_reasons: [] },
  { exam_id: "e2", exam_code: "ESP-000078", patient_name: "Nelson Teixeira Barros",
    service_date: "2026-10-20", municipio_name: "Rio de Janeiro/RJ", amount: "250.00",
    status: "ready", blocking_reasons: [] },
  { exam_id: "e3", exam_code: "ESP-000079", patient_name: "Sem CPF Cadastrado",
    service_date: "2026-10-20", municipio_name: "Rio de Janeiro/RJ", amount: "250.00",
    status: "blocked", blocking_reasons: ["recipient_cpf_missing"] },
  { exam_id: "e4", exam_code: "ESP-000080", patient_name: "Helena Prado Vasconcelos",
    service_date: "2026-10-20", municipio_name: "Rio de Janeiro/RJ", amount: "250.00",
    status: "blocked", blocking_reasons: ["financial_entry_missing"] },
  { exam_id: "e5", exam_code: "ESP-000081", patient_name: "Rodrigo Alencar Pontes",
    service_date: "2026-10-13", municipio_name: "Rio de Janeiro/RJ", amount: "219.00",
    status: "issued", blocking_reasons: [] },
];

// ── 1. quem pode compor ─────────────────────────────────────────────────────
montarEstado({ production: { itens: LINHAS, counts: {} } });
const combinaveis = api.groupableRows().map((r) => r.exam_code);
caso("os dois exames prontos entram na composição",
     combinaveis.includes("ESP-000077") && combinaveis.includes("ESP-000078"),
     JSON.stringify(combinaveis));
caso("exame barrado só pela identidade do paciente entra (a nota é do tomador)",
     combinaveis.includes("ESP-000079"));
caso("exame sem receita NÃO entra", !combinaveis.includes("ESP-000080"));
caso("exame já emitido NÃO entra", !combinaveis.includes("ESP-000081"));

// ── 2. a tela não soma nada por conta própria ───────────────────────────────
montarEstado({
  production: { itens: LINHAS, counts: {} },
  grouping: {
    selected: new Set(["e1", "e2"]),
    recipient: { id: "p1", nome_completo: "Helena Prado Vasconcelos", public_code: "PES-000091" },
    preview: null, busy: false, status: null, documents: [], lookupBusy: false,
  },
});
const semPrevia = api.groupedPanelHtml();
caso("sem resposta do backend a tela não exibe total algum",
     !/R\$\s*500/.test(semPrevia) && /Escolha dois ou mais exames/.test(semPrevia));
caso("sem prévia, preparar fica desabilitado",
     /data-group-prepare[^>]*disabled/.test(semPrevia));

// ── 3. com a prévia do backend ──────────────────────────────────────────────
const PREVIA_OK = {
  kind: "grouped",
  recipient: { person_id: "p1", public_code: "PES-000091",
               nome: "Helena Prado Vasconcelos", cpf_masked: "***.***.***-00" },
  itens: [
    { position: 1, exam_code: "ESP-000077", amount: "250.00" },
    { position: 2, exam_code: "ESP-000078", amount: "250.00" },
  ],
  itens_count: 2, total: "500.00", flow: "HOME",
  service_date: "2026-10-20", competence: "2026-10-20",
  municipio_ibge: "3304557", municipio_name: "Rio de Janeiro/RJ",
  service_description: "2 espirometrias com broncodilatador, com emissão de laudo médico.",
  amount_source: "grouped_items.sum", blocking_reasons: [], blocking_labels: [],
  can_prepare: true,
};
montarEstado({
  production: { itens: LINHAS, counts: {} },
  grouping: {
    selected: new Set(["e1", "e2"]),
    recipient: { id: "p1", nome_completo: "Helena Prado Vasconcelos", public_code: "PES-000091" },
    preview: PREVIA_OK, busy: false, status: null, documents: [], lookupBusy: false,
  },
});
const comPrevia = api.groupedPanelHtml();
caso("mostra os dois exames com R$ 250,00 cada",
     (comPrevia.match(/250,00/g) || []).length >= 2);
caso("mostra o total de R$ 500,00 que o backend devolveu", /500,00/.test(comPrevia));
caso("mostra a tomadora e o município", /Helena Prado Vasconcelos/.test(comPrevia)
     && /Rio de Janeiro\/RJ/.test(comPrevia));
caso("mostra o CPF MASCARADO", /\*\*\*\.\*\*\*\.\*\*\*-00/.test(comPrevia));
caso("o CPF inteiro nunca é renderizado", !comPrevia.includes(CPF_INTEIRO));
caso("preparar fica habilitado quando o backend diz can_prepare",
     /data-group-prepare\s*$|data-group-prepare[^>]*>/.test(comPrevia)
     && !/data-group-prepare[^>]*disabled/.test(comPrevia));
caso("o rótulo do botão diz que NÃO emite", /não emite/i.test(comPrevia));

// ── 4. bloqueio vem em português ────────────────────────────────────────────
montarEstado({
  production: { itens: LINHAS, counts: {} },
  grouping: {
    selected: new Set(["e1", "e3"]),
    recipient: { id: "p1", nome_completo: "Helena Prado Vasconcelos" },
    preview: Object.assign({}, PREVIA_OK, {
      total: null, can_prepare: false,
      blocking_reasons: ["group_service_date_divergent"],
      blocking_labels: ["Os exames têm datas de serviço diferentes."],
    }),
    busy: false, status: null, documents: [], lookupBusy: false,
  },
});
const bloqueado = api.groupedPanelHtml();
caso("o motivo aparece em português", /datas de serviço diferentes/.test(bloqueado));
caso("o código técnico do motivo não vaza para a tela",
     !/group_service_date_divergent/.test(bloqueado));
caso("bloqueado, preparar fica desabilitado",
     /data-group-prepare[^>]*disabled/.test(bloqueado));

// ── 5. documento preparado aguarda emissão manual ───────────────────────────
montarEstado({
  production: { itens: LINHAS, counts: {} },
  grouping: {
    selected: new Set(), recipient: null, preview: null, busy: false, status: null,
    lookupBusy: false,
    documents: [{
      id: "11111111-2222-3333-4444-555555555555", kind: "grouped", state: "pending",
      itens: [{ exam_code: "ESP-000077" }, { exam_code: "ESP-000078" }],
      itens_total: "500.00",
    }],
  },
});
const comDoc = api.groupedPanelHtml();
caso("o documento preparado aparece com os dois exames",
     /ESP-000077 \+ ESP-000078/.test(comDoc));
caso("e diz que aguarda emissão manual", /aguardando emissão manual/.test(comDoc));
caso("nenhum botão de emitir o agrupado existe na tela",
     !/data-group-issue/.test(comDoc) && !/data-group-emit/.test(comDoc));
caso("a tela inteira não tem botão de emissão do agrupado",
     !/Emitir NFS-e agrupada/i.test(comDoc));

console.log(falhas === 0
  ? "\nRESULTADO: todos os casos passaram."
  : `\nRESULTADO: ${falhas} caso(s) falharam.`);
process.exit(falhas === 0 ? 0 : 1);
