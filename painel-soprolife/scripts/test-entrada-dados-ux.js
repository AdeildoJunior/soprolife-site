#!/usr/bin/env node
// SoproLife — M16 Central de Cadastros consolidou as 6 abas antigas da
// "Entrada de Dados" (que escreviam direto no Google Sheets via Apps
// Script) em UM ÚNICO formulário canônico por entidade, contra a API M15/
// PostgreSQL (js/central-cadastros.js). As funções buildFormPaciente,
// buildFormConsulta, buildFormClinica, buildFormInteracao, buildFormLead e
// o ACTION_MAP que este arquivo testava (M12.1) foram REMOVIDAS de app.js
// por decisão explícita do produto — não há mais nenhuma implementação
// duplicada desses formulários. M17 removeu também a antiga tela de
// redirecionamento "Entrada de Dados virou Central de Cadastros"
// (renderEntradaDados): os cards e botões contextuais agora chamam
// window.SoproCentral.open(...) diretamente. Este teste passou a checar o
// estado atual: nenhuma duplicata sobrevive em app.js, nenhuma view de
// redirecionamento remanesce, e a Central usa o mesmo tipo de defaults
// fechados (responsável, origem, etapa) que a M12.1 introduziu — agora em
// um só lugar.
// Uso: node painel-soprolife/scripts/test-entrada-dados-ux.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs   = require("fs");
const path = require("path");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const appJsPath     = path.resolve(__dirname, "../js/app.js");
const efPath        = path.resolve(__dirname, "../js/espirometria-financeiro.js");
const centralJsPath = path.resolve(__dirname, "../js/central-cadastros.js");
const appJsSrc      = fs.readFileSync(appJsPath, "utf8");
const efSrc         = fs.readFileSync(efPath, "utf8");
const centralJsSrc  = fs.readFileSync(centralJsPath, "utf8");
const indexSrc      = fs.readFileSync(path.resolve(__dirname, "../index.html"), "utf8");

console.log("M16 — Central de Cadastros substitui a Entrada de Dados legada (checagens estáticas)");

// ── Nenhuma duplicata sobrevive em app.js ───────────────────────────────────
console.log();
console.log("Nenhum formulário duplicado remanescente em app.js");
for (const fn of [
  "buildFormPaciente", "buildFormConsulta", "buildFormClinica",
  "buildFormInteracao", "buildFormLead", "buildFormEspi",
]) {
  caso(`app.js não contém mais function ${fn}(`, !appJsSrc.includes(`function ${fn}(`));
}
caso("app.js não contém mais o ACTION_MAP legado (createLead/createPaciente/...)",
     !/const ACTION_MAP\s*=\s*\{/.test(appJsSrc));
caso("renderEntradaDados (tela de redirecionamento) foi removida de app.js (M17)",
     !/function renderEntradaDados\(/.test(appJsSrc));
caso("nenhuma view de CRM aponta mais para a tela de redirecionamento legada",
     !/case "central-cadastros":\s*renderEntradaDados/.test(appJsSrc));
// M21 — o card "Central de Cadastros" do hub do CRM foi REMOVIDO: a Central já
// é item de sidebar, e o atalho duplicava esse destino. O que precisa
// continuar verdadeiro é que não sobrou nenhum caminho de redirecionamento.
caso("card 'Central de Cadastros' saiu do hub do CRM (destino já é item de sidebar)",
     appJsSrc.indexOf('dataset.crmView === "central-cadastros"') === -1 &&
     appJsSrc.indexOf('title: "Central de Cadastros"') === -1 &&
     /data-section="central-cadastros"/.test(indexSrc));

// ── Responsável: mesmo padrão fechado Adeildo/Luiz Faustino, agora 1 lugar ──
console.log();
console.log("Responsável (select fechado Adeildo/Luiz Faustino) — agora só na Central");
caso("central-cadastros.js define RESPONSAVEIS com Adeildo e Luiz Faustino",
     /const RESPONSAVEIS\s*=\s*\["Adeildo",\s*"Luiz Faustino"\]/.test(centralJsSrc));
const ocorrenciasResponsavelDatalist = (centralJsSrc.match(/list="cadResp"/g) || []).length;
// M20: Espirometria e Consulta deixaram de ser abas — viraram blocos do
// fluxo único "Novo atendimento". A lista continua servindo Lead + exame.
caso("campo responsável usa a lista RESPONSAVEIS no Lead e no Novo atendimento",
     ocorrenciasResponsavelDatalist >= 2, `encontrado: ${ocorrenciasResponsavelDatalist}`);

// ── Defaults fechados equivalentes aos da M12.1, agora centralizados ───────
console.log();
console.log("Defaults fechados (origem, etapa, serviço) na Central");
caso("central-cadastros.js define ORIGENS com Google/Instagram/WhatsApp/Indicação médica",
     /const ORIGENS\s*=\s*\[[\s\S]{0,300}?"Google"[\s\S]{0,300}?"Indicação médica"/.test(centralJsSrc));
caso("aba Lead tem etapa padrão 'novo' e serviço de interesse padrão 'espirometria'",
     /LOADERS\.lead[\s\S]{0,3000}?sel\("etapa", LEAD_ETAPAS, "novo"\)/.test(centralJsSrc) &&
     /LOADERS\.lead[\s\S]{0,3000}?sel\("servico_interesse",[\s\S]{0,300}?\], "espirometria"\)/.test(centralJsSrc));
caso("cadastro de pessoa pré-preenche consentimento_whatsapp como 'concedido'",
     /personPickerHtml[\s\S]{0,3000}?_consent"[\s\S]{0,200}?"concedido"\)/.test(centralJsSrc));

// ── Seletor de pessoa reutilizável substitui os campos soltos de nome/tel ──
console.log();
console.log("Seletor de pessoa reutilizável (substitui nome/telefone soltos por aba)");
caso("personPickerHtml existe e é usado por Lead e Novo atendimento",
     /function personPickerHtml\(/.test(centralJsSrc) &&
     (centralJsSrc.match(/personPickerHtml\(/g) || []).length >= 3);
caso("seletor de pessoa avisa duplicado antes de criar (verificar-duplicados)",
     centralJsSrc.includes("/pessoas/verificar-duplicados"));

// ── Guard rails de segurança/contrato (não regrediram) ──────────────────────
console.log();
console.log("Guard rails de segurança e contrato");
caso("app.js não contém URL do Apps Script (script.google.com)",
     !/script\.google\.com/.test(appJsSrc));
caso("central-cadastros.js não contém URL do Apps Script (script.google.com)",
     !/script\.google\.com/.test(centralJsSrc));
caso("espirometria-financeiro.js continua sem fetch",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest",
     !/XMLHttpRequest/.test(efSrc));
caso("buildEspirometriaFinanceiroPayload não foi tocado (função pura preservada)",
     /function buildEspirometriaFinanceiroPayload\(formData\)/.test(efSrc));

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
