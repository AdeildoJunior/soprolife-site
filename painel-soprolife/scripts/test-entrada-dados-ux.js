#!/usr/bin/env node
// SoproLife — M12.1 Pré-preenchimentos transversais da Entrada de Dados.
// Checagens estáticas de texto sobre js/app.js e js/espirometria-financeiro.js
// (sem DOM, sem rede) — confirma que os defaults/selects pedidos na M12.1
// foram aplicados nas 5 abas fora da Nova Espirometria, sem mexer no
// contrato financeiro (ids, actions, Apps Script).
// Uso: node painel-soprolife/scripts/test-entrada-dados-ux.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs   = require("fs");
const path = require("path");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const appJsPath = path.resolve(__dirname, "../js/app.js");
const efPath    = path.resolve(__dirname, "../js/espirometria-financeiro.js");
const appJsSrc  = fs.readFileSync(appJsPath, "utf8");
const efSrc     = fs.readFileSync(efPath, "utf8");

// Recorta o texto de uma função buildFormXxx até o próximo marcador, pra
// checar cada aba isoladamente em vez de procurar strings comuns (ex.:
// "Google", "Normal") soltas no arquivo inteiro.
function trecho(src, marcadorInicio, marcadorFim) {
  const inicio = src.indexOf(marcadorInicio);
  if (inicio < 0) return "";
  const fim = marcadorFim ? src.indexOf(marcadorFim, inicio) : -1;
  return fim > inicio ? src.slice(inicio, fim) : src.slice(inicio);
}

const pacienteSrc  = trecho(appJsSrc, "function buildFormPaciente(", "function buildFormEspi(");
const consultaSrc  = trecho(appJsSrc, "function buildFormConsulta(", "function buildFormClinica(");
const clinicaSrc   = trecho(appJsSrc, "function buildFormClinica(", "function buildFormInteracao(");
const interacaoSrc = trecho(appJsSrc, "function buildFormInteracao(", "function buildFormLead(");
const leadSrc      = trecho(appJsSrc, "function buildFormLead(", "const tabs = {");

console.log("M12.1 — Pré-preenchimentos transversais da Entrada de Dados (checagens estáticas)");

// ── Responsável vira select fechado Adeildo/Luiz Faustino ──────────────────
console.log();
console.log("Responsável (select fechado Adeildo/Luiz Faustino)");
caso("EF_RESPONSAVEL_OPTS contém Adeildo",
     /EF_RESPONSAVEL_OPTS\s*=\s*\[[^\]]*"Adeildo"/.test(appJsSrc));
caso("EF_RESPONSAVEL_OPTS contém Luiz Faustino",
     /EF_RESPONSAVEL_OPTS\s*=\s*\[[^\]]*"Luiz Faustino"/.test(appJsSrc));

const ocorrenciasResponsavel = (appJsSrc.match(/options:\s*EF_RESPONSAVEL_OPTS/g) || []).length;
caso("responsavel usa EF_RESPONSAVEL_OPTS em pelo menos 6 formulários " +
     "(Paciente, Espirometria, Consulta, Clínica B2B, Interação, Lead)",
     ocorrenciasResponsavel >= 6, `encontrado: ${ocorrenciasResponsavel}`);

for (const [nome, src] of [
  ["Novo Paciente", pacienteSrc], ["Nova Consulta", consultaSrc],
  ["Nova Clínica B2B", clinicaSrc], ["Contato com Clínica", interacaoSrc],
  ["Novo Lead", leadSrc],
]) {
  caso(`${nome}: campo responsavel usa select com default "Adeildo"`,
       /id:\s*"responsavel"[^}]*options:\s*EF_RESPONSAVEL_OPTS[^}]*value:\s*"Adeildo"/.test(src),
       "trecho não encontrado ou fora de ordem");
}

// ── A) Novo Lead ────────────────────────────────────────────────────────────
console.log();
console.log("A) Novo Lead");
caso('Novo Lead: campo origem usa select com default "Google"',
     /id:\s*"origem"[^}]*options:\s*origemCanalOpts[^}]*value:\s*"Google"/.test(leadSrc));
caso('Novo Lead: campo etapa tem default "Novo contato"',
     /id:\s*"etapa"[^}]*value:\s*"Novo contato"/.test(leadSrc));
caso('Novo Lead: campo servico_interesse tem default "Espirometria"',
     /id:\s*"servico_interesse"[^}]*value:\s*"Espirometria"/.test(leadSrc));
// camposLeadOpcionais é declarado ANTES de "secondaryFields:" no texto (é
// passado por referência de variável) — checa presença nos dois blocos em
// vez de depender da ordem exata em que aparecem no source.
const leadCamposOpcionaisSrc = trecho(leadSrc, "const camposLeadOpcionais", "return formWrapper");
caso("Novo Lead: proxima_acao, data_proxima_acao e observacao movidos para " +
     "seção secundária (camposLeadOpcionais / secondaryFields)",
     leadSrc.includes("secondaryFields: camposLeadOpcionais") &&
     /id:\s*"proxima_acao"/.test(leadCamposOpcionaisSrc) &&
     /id:\s*"data_proxima_acao"/.test(leadCamposOpcionaisSrc) &&
     /id:\s*"observacao"/.test(leadCamposOpcionaisSrc));
caso("Novo Lead: nome e telefone_whatsapp continuam na seção principal (não movidos)",
     !/id:\s*"nome"/.test(leadCamposOpcionaisSrc) &&
     !/id:\s*"telefone_whatsapp"/.test(leadCamposOpcionaisSrc));

// ── B) Novo Paciente ────────────────────────────────────────────────────────
console.log();
console.log("B) Novo Paciente");
caso('Novo Paciente: consentimento_whatsapp tem default "Sim"',
     /id:\s*"consentimento_whatsapp"[^}]*value:\s*"Sim"/.test(pacienteSrc));
caso('Novo Paciente: campo canal usa select padronizado com default "Google"',
     /id:\s*"canal"[^}]*options:\s*origemCanalOpts[^}]*value:\s*"Google"/.test(pacienteSrc));
caso('Novo Paciente: campo origem continua texto livre (semântica = ultimo_servico no Apps Script), pré-preenchido "Espirometria"',
     /id:\s*"origem"[^}]*placeholder:[^}]*value:\s*"Espirometria"/.test(pacienteSrc) &&
     !/id:\s*"origem"[^}]*type:\s*"select"/.test(pacienteSrc));
caso("Novo Paciente: microcopy de nome/telefone presente",
     /microcopy:\s*"Digite nome e telefone/.test(pacienteSrc));

// ── C) Nova Consulta ─────────────────────────────────────────────────────
console.log();
console.log("C) Nova Consulta");
// M14.3A (2ª rodada): data clínica NUNCA nasce preenchida com hoje — a
// ausência não pode virar fato diário sem decisão do operador (ALTO-04).
caso("Nova Consulta: data_consulta NÃO usa hojeISO() como default",
     !/id:\s*"data_consulta"[^}]*value:\s*hojeISO\(\)/.test(consultaSrc));
caso('Nova Consulta: consentimento_whatsapp tem default "Sim"',
     /id:\s*"consentimento_whatsapp"[^}]*value:\s*"Sim"/.test(consultaSrc));
caso('Nova Consulta: campo origem usa select padronizado com default "Google"',
     /id:\s*"origem"[^}]*options:\s*origemCanalOpts[^}]*value:\s*"Google"/.test(consultaSrc));
caso("Nova Consulta: campo medica continua texto livre (sem lista de profissionais no código)",
     !/id:\s*"medica"[^}]*type:\s*"select"/.test(consultaSrc));
const consultaCamposOpcionaisSrc = trecho(consultaSrc, "const camposConsultaOpcionais", "return formWrapper");
caso("Nova Consulta: proximo_contato e motivo_proximo_contato movidos para seção secundária",
     consultaSrc.includes("secondaryFields: camposConsultaOpcionais") &&
     /id:\s*"proximo_contato"/.test(consultaCamposOpcionaisSrc) &&
     /id:\s*"motivo_proximo_contato"/.test(consultaCamposOpcionaisSrc));

// ── D) Nova Clínica B2B ─────────────────────────────────────────────────
console.log();
console.log("D) Nova Clínica B2B");
caso('Nova Clínica B2B: prioridade tem default "Normal"',
     /id:\s*"prioridade"[^}]*options:\s*prioOpts[^}]*value:\s*"Normal"/.test(clinicaSrc));
caso('Nova Clínica B2B: status tem default "Não abordada" (etapa existente mais próxima)',
     /id:\s*"status"[^}]*value:\s*"Não abordada"/.test(clinicaSrc));
caso('Nova Clínica B2B: campo origem usa select padronizado com default "Google"',
     /id:\s*"origem"[^}]*options:\s*origemCanalOpts[^}]*value:\s*"Google"/.test(clinicaSrc));
caso("Nova Clínica B2B: proximo_passo e data_proximo_passo movidos para seção secundária",
     /secondaryFields:[\s\S]*?id:\s*"proximo_passo"/.test(clinicaSrc) &&
     /secondaryFields:[\s\S]*?id:\s*"data_proximo_passo"/.test(clinicaSrc));
caso("Nova Clínica B2B: nome_clinica, bairro, regiao e telefone_whatsapp continuam na seção principal",
     !/secondaryFields:[\s\S]*?id:\s*"nome_clinica"/.test(clinicaSrc) &&
     !/secondaryFields:[\s\S]*?id:\s*"bairro"/.test(clinicaSrc) &&
     !/secondaryFields:[\s\S]*?id:\s*"telefone_whatsapp"/.test(clinicaSrc));

// ── E) Contato com Clínica ──────────────────────────────────────────────
console.log();
console.log("E) Contato com Clínica");
caso('Contato com Clínica: prioridade tem default "Normal"',
     /id:\s*"prioridade"[^}]*options:\s*prioOpts[^}]*value:\s*"Normal"/.test(interacaoSrc));
caso('Contato com Clínica: etapa tem default "Em conversa" (etapa existente mais próxima)',
     /id:\s*"etapa"[^}]*value:\s*"Em conversa"/.test(interacaoSrc));
caso("Contato com Clínica: proxima_acao e data_proxima_acao movidos para seção secundária",
     /secondaryFields:[\s\S]*?id:\s*"proxima_acao"/.test(interacaoSrc) &&
     /secondaryFields:[\s\S]*?id:\s*"data_proxima_acao"/.test(interacaoSrc));

// ── Seção "Dados opcionais / CRM" reaproveitada além da Espirometria ────────
console.log();
console.log("Seção 'Dados opcionais / CRM' fora da Nova Espirometria");
const ocorrenciasSecondaryFields = (appJsSrc.match(/secondaryFields:/g) || []).length;
caso('formWrapper() ganhou suporte a "secondaryFields" (Dados opcionais / CRM)',
     /function formWrapper\(title, fields, opts = \{\}\)/.test(appJsSrc));
caso("secondaryFields é usado em pelo menos 4 abas (Lead, Consulta, Clínica B2B, Interação)",
     ocorrenciasSecondaryFields >= 4, `encontrado: ${ocorrenciasSecondaryFields}`);

// ── Guard rails de segurança/contrato (não regrediram) ──────────────────────
console.log();
console.log("Guard rails de segurança e contrato (M12.1 não deve quebrar)");
caso("app.js não contém URL do Apps Script (script.google.com)",
     !/script\.google\.com/.test(appJsSrc));
caso("espirometria-financeiro.js continua sem fetch",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest",
     !/XMLHttpRequest/.test(efSrc));
caso("ACTION_MAP continua com as 6 actions originais (contrato preservado)",
     /createLead/.test(appJsSrc) && /createPaciente/.test(appJsSrc) &&
     /createEspirometria/.test(appJsSrc) && /createConsulta/.test(appJsSrc) &&
     /createClinicaB2B/.test(appJsSrc) && /registrarInteracaoClinica/.test(appJsSrc));
caso("buildEspirometriaFinanceiroPayload não foi tocado (M12.1 não mexe no financeiro)",
     /function buildEspirometriaFinanceiroPayload\(formData\)/.test(efSrc));

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
