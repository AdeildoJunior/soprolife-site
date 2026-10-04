#!/usr/bin/env node
// SoproLife — gráfico "Evolução mensal" do Painel Geral.
//
// Provas offline e determinísticas sobre o bloco REAL de app.js:
//   A) começa em Mai/2026 (início da operação) e Jan–Abr/2026 não aparecem;
//   B) inclui o mês corrente e nunca um mês futuro, sem último mês fixo;
//   C) virada Dez/2026 → Jan/27 com ano curto fora do ano de início;
//   D) meses sem dados valem 0;
//   E) a contagem reproduz o agregado do PostgreSQL (lead no mês do primeiro
//      contato; agendamento = etapa ATUAL "agendado"), nos dois formatos de
//      data que chegam ao painel;
//   F) cache-buster de app.js presente no index.
//
// Uso:  node painel-soprolife/scripts/test-evolucao-mensal.js
// Exit: 0 = todos passaram | 1 = houve falha.

"use strict";

const fs = require("fs");
const path = require("path");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const RAIZ = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(RAIZ, "js", "app.js"), "utf8");
const index = fs.readFileSync(path.join(RAIZ, "index.html"), "utf8");

function trecho(inicio, fim) {
  const i = app.indexOf(inicio);
  const f = app.indexOf(fim, i);
  if (i < 0 || f < 0) {
    console.log(`FAIL: bloco não localizado em app.js: ${inicio}`);
    process.exit(1);
  }
  return app.slice(i, f);
}

const codigo = [
  trecho("const slug = (text) =>", "\n\n"),
  trecho("function parseDateIso(dateStr) {", "function formatDateBr("),
  trecho("function parseLeadDate(dateStr) {", "function leadMatchesPeriodo("),
  trecho("const MESES_CURTOS = ", "function renderCharts() {"),
].join("\n");
const build = new Function(`${codigo}\nreturn buildEvolucaoMensal;`)();

const dia = (a, m, d = 15) => new Date(a, m - 1, d, 12, 0, 0);
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

console.log("A/B) intervalo");
{
  const r = build([], dia(2026, 10, 3));
  caso("Out/2026 → Mai..Out", eq(r.labels, ["Mai", "Jun", "Jul", "Ago", "Set", "Out"]), r.labels.join(","));
  caso("Jan–Abr/2026 ausentes", !["Jan", "Fev", "Mar", "Abr"].some((m) => r.labels.includes(m)));
  caso("último = mês corrente", r.meses.at(-1).ano === 2026 && r.meses.at(-1).mes === 9);
  const nov = build([], dia(2026, 11, 1));
  caso("Novembro entra sozinho", nov.labels.at(-1) === "Nov" && nov.labels.length === 7);
  const mai = build([], dia(2026, 5, 1));
  caso("em maio/2026 só Mai", eq(mai.labels, ["Mai"]));
  const futuro = build([{ data_contato: "10/11/2026", etapa: "agendado" }], dia(2026, 10, 3));
  caso("lead em mês futuro não cria bucket nem soma", futuro.labels.length === 6 && futuro.leads.every((v) => v === 0));
  const antigo = build([{ data_contato: "2026-04-30", etapa: "agendado" }], dia(2026, 10, 3));
  caso("lead de abril/2026 fica fora", antigo.leads.every((v) => v === 0));
}

console.log("C) virada de ano");
{
  const dez = build([], dia(2026, 12, 31));
  caso("Dez/2026 sem sufixo", dez.labels.at(-1) === "Dez");
  const jan = build([{ data_contato: "02/01/2027", etapa: "agendado" }], dia(2027, 1, 5));
  caso("Jan/2027 vira Jan/27", jan.labels.at(-1) === "Jan/27", jan.labels.join(","));
  caso("Dez → Jan/27 contíguos", eq(jan.labels.slice(-2), ["Dez", "Jan/27"]));
  caso("Jan/27 conta o lead", jan.leads.at(-1) === 1 && jan.agendamentos.at(-1) === 1);
  const mar = build([], dia(2027, 3, 1));
  caso("Mar/2027 → 11 meses", mar.labels.length === 11 && eq(mar.labels.slice(-3), ["Jan/27", "Fev/27", "Mar/27"]));
}

console.log("D/E) valores");
{
  // Agregado do PostgreSQL de produção em 03/10/2026 (somente contagens):
  // Jun = 7 leads, Jul = 5 leads, nenhum na etapa agendado.
  const pg = [
    ...Array.from({ length: 7 }, (_, i) => ({ data_contato: `${22 + i}/06/2026`, etapa: "Desistiu" })),
    ...Array.from({ length: 4 }, (_, i) => ({ data_contato: `0${4 + i}/07/2026`, etapa: "Desistiu" })),
    { data_contato: "25/07/2026", etapa: "convertido" },
  ];
  const r = build(pg, dia(2026, 10, 3));
  caso("leads Mai..Out = 0,7,5,0,0,0", eq(r.leads, [0, 7, 5, 0, 0, 0]), r.leads.join(","));
  caso("agendamentos todos 0", eq(r.agendamentos, [0, 0, 0, 0, 0, 0]));

  const mix = build([
    { data_contato: "03/09/2026", etapa: "agendado" },
    { data_primeiro_contato: "2026-09-20", etapa: "Agendado" },
    { data_contato: "2026-09-21 10:00", etapa: "novo" },
    { data_contato: "", etapa: "agendado" },
    { etapa: "agendado" },
  ], dia(2026, 10, 3));
  caso("setembro conta DD/MM/AAAA e ISO", mix.leads[4] === 3, mix.leads.join(","));
  caso("agendado nos dois vocabulários", mix.agendamentos[4] === 2);
  caso("sem data não entra", mix.leads.reduce((a, b) => a + b, 0) === 3);
  caso("lista inválida → zeros", eq(build(null, dia(2026, 7, 1)).leads, [0, 0, 0]));
}

console.log("F) estáticos");
caso("sem último mês fixo no código", !/MESES_CURTOS\.slice\(0,/.test(app));
caso("cache-buster de app.js", /js\/app\.js\?v=\d{10}/.test(index));

console.log(falhas ? `\n${falhas} falha(s).` : "\nTodos passaram.");
process.exit(falhas ? 1 : 0);
