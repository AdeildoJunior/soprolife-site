#!/usr/bin/env node
// SoproLife — M26.35: o documento agrupado ganha o botão "Emitir NFS-e".
//
// Prova, executando o código REAL recortado de js/fiscal-command-center.js:
//   • o cartão do documento agrupado mostra Documento, Exames, Tomadora,
//     Total e Estado, e o botão "Emitir NFS-e" SÓ quando o backend disse
//     `can_confirm`, o executor está configurado e não há pedido vivo;
//   • a confirmação tem duas etapas com os textos combinados; a primeira
//     não tem o botão que grava o pedido, só a segunda tem;
//   • o CPF da tomadora aparece só mascarado.
//
// Não usa navegador, rede, credencial nem dado privado.
// Uso:  node painel-soprolife/scripts/test-m26-35-nfse-agrupada-emissao-painel.js
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
  modal: recortar("function productionModalHtml()", "const PROD_ERRORS = {"),
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
  ${trechos.modal}
  globalThis.__api = { groupedDocumentsHtml, productionModalHtml, joinCodes };
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
const DOC_ID = "38aa0000-0000-4000-8000-000000000001";
const DOC = {
  id: DOC_ID, kind: "grouped", state: "pending", itens_total: "500.00",
  itens: [{ exam_code: "ESP-000077" }, { exam_code: "ESP-000078" }],
};
const RESUMO = {
  document_id: DOC_ID, preparation_id: "prep-1", kind: "grouped",
  patient_name: "Helena Prado Vasconcelos", cpf_masked: "***.***.***-00",
  service_date: "2026-10-20", competence: "2026-10-20", flow: "HOME",
  municipio_ibge: "3304557", municipio_name: "Rio de Janeiro/RJ",
  amount: "500.00", amount_label: "R$ 500,00",
  service_description: "2 espirometrias com broncodilatador, com emissão de laudo médico.",
  state: "pending", eligibility: "eligible", blocking_reasons: [],
  confirmation_phrase: "Confirmar emissão de R$ 500,00", can_confirm: true,
  itens: [{ position: 1, exam_code: "ESP-000077", amount: "250.00" },
          { position: 2, exam_code: "ESP-000078", amount: "250.00" }],
  itens_count: 2,
};
const FILA = (request) => ({ itens: [
  { exam_code: "ESP-000077", grouped_document_id: DOC_ID, request: request || null },
  { exam_code: "ESP-000078", grouped_document_id: DOC_ID, request: request || null },
], counts: {} });

function estado({ available = true, resumo = RESUMO, request = null, doc = DOC } = {}) {
  sandbox.state = {
    status: { enabled: true, environment: "production",
              production_issuance: { available } },
    production: FILA(request), prodBusy: false, prodModal: null,
    grouping: { selected: new Set(), recipient: null, preview: null, busy: false,
                status: null, lookupBusy: false, documents: [doc],
                summaries: resumo ? { [doc.id]: resumo } : {} },
  };
  return sandbox.state;
}

// ── 1. o cartão ─────────────────────────────────────────────────────────────
estado();
const cartao = api.groupedDocumentsHtml();
for (const rotulo of ["Documento", "Exames", "Tomadora", "Total", "Estado"]) {
  caso(`o cartão mostra "${rotulo}"`, cartao.includes(`<dt>${rotulo}</dt>`));
}
caso("o cartão mostra os dois exames", /ESP-000077 \+ ESP-000078/.test(cartao));
caso("o cartão mostra a tomadora", cartao.includes("Helena Prado Vasconcelos"));
caso("o cartão mostra o total do backend", /500,00/.test(cartao));
caso("elegível: botão Emitir NFS-e presente",
     cartao.includes(`data-group-issue="${DOC_ID}"`) && />Emitir NFS-e</.test(cartao));
caso("o CPF inteiro nunca é renderizado", !cartao.includes(CPF_INTEIRO));

estado({ available: false });
caso("sem executor configurado: sem botão", !/data-group-issue/.test(api.groupedDocumentsHtml()));
estado({ resumo: Object.assign({}, RESUMO, { can_confirm: false, block_label: "Dados mudaram — preparar de novo" }) });
const bloqueado = api.groupedDocumentsHtml();
caso("backend sem can_confirm: sem botão, com o motivo",
     !/data-group-issue/.test(bloqueado) && /preparar de novo/.test(bloqueado));
estado({ request: { id: "r1", status: "authorized" } });
const vivo = api.groupedDocumentsHtml();
caso("pedido vivo: sem segundo botão, com o estado do pedido",
     !/data-group-issue/.test(vivo) && /aguardando o executor/.test(vivo));
estado({ request: { id: "r1", status: "refused" } });
caso("pedido anterior recusado não impede novo botão",
     /data-group-issue/.test(api.groupedDocumentsHtml()));
estado({ doc: Object.assign({}, DOC, { state: "issued" }) });
caso("documento emitido: sem botão", !/data-group-issue/.test(api.groupedDocumentsHtml()));

// ── 2. confirmação em duas etapas ───────────────────────────────────────────
caso("lista de exames lida em português", api.joinCodes(["A", "B", "C"]) === "A, B e C");
const st = estado();
st.prodModal = { summary: RESUMO, grouped: true, stage: 1, idemKey: "k1" };
const etapa1 = api.productionModalHtml();
caso("etapa 1: texto exato da primeira confirmação", etapa1.includes(
  "Você está prestes a emitir uma única NFS-e de R$ 500,00 para Helena Prado Vasconcelos "
  + "referente aos exames ESP-000077 e ESP-000078."));
caso("etapa 1: só 'Continuar', sem o botão que grava o pedido",
     /data-prod-group-next/.test(etapa1) && !/data-prod-confirm/.test(etapa1));
caso("etapa 1: mostra itens, total, CPF mascarado e data",
     (etapa1.match(/250,00/g) || []).length >= 2 && etapa1.includes("R$ 500,00")
     && etapa1.includes("***.***.***-00") && etapa1.includes("20/10/2026"));
caso("etapa 1: o CPF inteiro nunca é renderizado", !etapa1.includes(CPF_INTEIRO));

st.prodModal.stage = 2;
const etapa2 = api.productionModalHtml();
caso("etapa 2: texto exato da segunda confirmação", etapa2.includes(
  "Esta ação enviará a NFS-e ao provedor fiscal e poderá gerar obrigação fiscal. Confirmar emissão?"));
caso("etapa 2: o botão grava o pedido com a frase exigida pelo servidor",
     /data-prod-confirm[^>]*>Confirmar emissão de R\$ 500,00</.test(etapa2));
st.prodBusy = true;
caso("etapa 2: enquanto envia, o botão fica desabilitado (duplo clique)",
     /data-prod-confirm disabled/.test(api.productionModalHtml()));
st.prodBusy = false;
st.prodModal = { summary: Object.assign({}, RESUMO, { can_confirm: false, block_label: "Dados mudaram" }),
                 grouped: true, stage: 2 };
const naoPode = api.productionModalHtml();
caso("resumo sem can_confirm: nenhum botão de confirmar em nenhuma etapa",
     !/data-prod-confirm/.test(naoPode) && !/data-prod-group-next/.test(naoPode));

console.log(falhas === 0
  ? "\nRESULTADO: todos os casos passaram."
  : `\nRESULTADO: ${falhas} caso(s) falharam.`);
process.exit(falhas === 0 ? 0 : 1);
