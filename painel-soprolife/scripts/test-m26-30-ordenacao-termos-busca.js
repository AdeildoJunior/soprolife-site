#!/usr/bin/env node
// SoproLife — M26.30: ordenação de apresentação dos "Termos de busca".
//
// Prova, executando o renderizador REAL recortado de js/app.js contra um DOM
// mínimo, que:
//   • ao abrir, a lista sai por impressões ↓ (regra de análise SEO), mesmo
//     recebendo do Search Console a ordem dele (cliques primeiro);
//   • o desempate é impressões → cliques → CTR;
//   • o snapshot do Search Console NÃO é alterado — só a apresentação;
//   • os quatro critérios do seletor ordenam de fato, com indicação visual de
//     qual está ativo, e asc/desc funciona;
//   • "Ver mais" sobrevive à troca de ordenação;
//   • sem consultas reais o seletor desaparece (o modelo demonstrativo não
//     tem CTR: oferecer o critério ali seria prometer métrica inexistente).
//
// Não usa navegador, rede, credencial nem dado privado.
// Uso:  node painel-soprolife/scripts/test-m26-30-ordenacao-termos-busca.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.resolve(__dirname, "../js/app.js");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

// ── Extração do trecho real do app.js ───────────────────────────────────────
// app.js não é módulo (o painel o carrega por <script>): recortamos as funções
// de verdade em vez de duplicá-las. Se o recorte falhar, o teste falha — nunca
// valida uma cópia desatualizada.
const fonte = fs.readFileSync(APP_JS, "utf8");

function recortar(inicio, fim) {
  const i = fonte.indexOf(inicio);
  const f = fonte.indexOf(fim, i + 1);
  if (i < 0 || f < 0) return null;
  return fonte.slice(i, f);
}

const trechos = {
  escapeHtml: recortar("function escapeHtml(str)", "/* Automação CRM (M21)"),
  renderSeoList: recortar("function renderSeoList()",
                          "// ─── Marketing & SEO — dados reais"),
  tip: recortar("function mktTipAttrs(tip, label, value)",
                "function renderMktKpiStripDemo()"),
  miniRow: recortar("function mktMiniRow(", "// ─── Termos de busca"),
  ordenacao: recortar("// ─── Termos de busca — ordenação de APRESENTAÇÃO (M26.30)",
                      "function renderMktPages()"),
};

for (const [nome, trecho] of Object.entries(trechos)) {
  caso(`recorte "${nome}" encontrado em app.js`, Boolean(trecho));
}
if (Object.values(trechos).some((t) => !t)) {
  console.log("\nRESULTADO: recorte falhou — app.js mudou de forma incompatível.");
  process.exit(1);
}

// ── DOM mínimo ──────────────────────────────────────────────────────────────
function no(extra = {}) {
  const n = {
    hidden: false, innerHTML: "", textContent: "", title: "", value: "",
    dataset: {}, atributos: {}, classes: new Set(), ouvintes: {},
    porSeletor: {}, listaPorSeletor: {},
    classList: {
      toggle(c, on) { if (on) n.classes.add(c); else n.classes.delete(c); },
      contains(c) { return n.classes.has(c); },
    },
    setAttribute(k, v) { n.atributos[k] = v; },
    getAttribute(k) { return k in n.atributos ? n.atributos[k] : null; },
    addEventListener(ev, fn) { (n.ouvintes[ev] = n.ouvintes[ev] || []).push(fn); },
    disparar(ev) { for (const fn of (n.ouvintes[ev] || [])) fn(); },
    querySelector(sel) { return n.porSeletor[sel] || null; },
    querySelectorAll(sel) { return n.listaPorSeletor[sel] || []; },
  };
  return Object.assign(n, extra);
}

const CHAVES = ["impressions", "clicks", "ctr", "avgPosition"];

function criarDom() {
  const setas = {};
  const botoes = {};
  const lista = CHAVES.map((chave) => {
    const seta = no();
    const botao = no();
    botao.dataset.mktSort = chave;
    botao.porSeletor[".mkt-sort-arrow"] = seta;
    setas[chave] = seta;
    botoes[chave] = botao;
    return botao;
  });

  const select = no();
  const botaoDir = no();
  const box = no({ hidden: true });
  box.listaPorSeletor[".mkt-sort-btn"] = lista;
  box.porSeletor["#seoSortSelect"] = select;
  box.porSeletor["#seoSortDir"] = botaoDir;

  const nos = {
    seoList: no(),
    seoListSubtitle: no(),
    seoMoreBtn: no({ hidden: true }),
    seoSort: box,
  };

  return {
    nos, box, botoes, setas, select, botaoDir,
    document: {
      querySelector(sel) {
        const id = sel.replace("#", "");
        return Object.prototype.hasOwnProperty.call(nos, id) ? nos[id] : null;
      },
    },
  };
}

// Um contexto por cenário, reaproveitado entre renderizações: os listeners de
// clique ficam presos ao DOM, como no painel.
function montar(snapshot, demo = null) {
  const dom = criarDom();
  const ctx = {
    document: dom.document,
    state: { marketingSeo: snapshot, marketing: demo, mktSeoSort: { key: "impressions", dir: "desc" }, mktSeoExpandido: false },
    console,
  };
  vm.createContext(ctx);
  vm.runInContext(
    [trechos.escapeHtml, trechos.renderSeoList, trechos.tip,
     trechos.miniRow, trechos.ordenacao].join("\n"),
    ctx,
  );
  const render = () => vm.runInContext("renderMktSeoList();", ctx);
  render();
  return { dom, ctx, render };
}

function termos(html) {
  return [...html.matchAll(/class="mkt-mini-term">([^<]*)</g)].map((m) => m[1]);
}

function larguras(html) {
  return [...html.matchAll(/style="width:(\d+)%"/g)].map((m) => Number(m[1]));
}

// ── Fixture no formato que o leitor real grava ───────────────────────────────
// Mesmo formato de painel-soprolife/scripts/read-marketing-seo-adc.py:
// {query, impressions, clicks, ctr, avgPosition} — ctr arredondado em 4 casas,
// avgPosition em 1. A ordem de entrada é a do Search Console (cliques ↓),
// com a cauda longa de 1 impressão / 0 clique que o relatório do M25.28
// registrou na conta real (107 consultas, 2308 impressões, 69 cliques).
const META = {
  configured: true, schemaVersion: 2, safeToDisplay: true,
  containsPersonalData: false,
  periodStart: "2026-09-13", periodEnd: "2026-10-10", lookbackDays: 28,
};

function linha(query, impressions, clicks, avgPosition) {
  return {
    query, impressions, clicks,
    ctr: impressions ? Number((clicks / impressions).toFixed(4)) : 0,
    avgPosition,
  };
}

const QUERIES = [
  // Ordem de chegada: cliques decrescentes, como o Search Console devolve.
  linha("soprolife", 118, 21, 1.4),
  linha("espirometria rio de janeiro", 402, 14, 7.8),
  linha("espirometria onde fazer rj", 47, 9, 4.0),
  linha("espirometria domiciliar rj", 96, 6, 5.2),
  linha("espirometria ipanema", 63, 4, 3.1),
  // Muita exposição e quase nenhum clique — o caso que a ordenação por
  // impressões existe para revelar.
  linha("espirometria preco", 731, 2, 14.6),
  linha("exame de sopro", 288, 1, 11.9),
  // Empate de impressões: desempata por cliques.
  linha("espirometria tijuca", 40, 3, 8.4),
  linha("espirometria copacabana", 40, 1, 9.1),
  // Empate de impressões E de cliques: desempata por CTR — aqui o CTR é
  // igual por construção, então cai no alfabético.
  linha("espirometria barra", 25, 0, 12.2),
  linha("espirometria botafogo", 25, 0, 10.8),
  // Cauda longa real: 1 impressão, 0 clique.
  ...Array.from({ length: 12 }, (_, i) =>
    linha(`cauda longa ${String(i + 1).padStart(2, "0")}`, 1, 0, 20 + i)),
];

const SNAPSHOT = {
  meta: META,
  searchConsole: {
    totals: { impressions: 2308, clicks: 69, ctr: 0.0299, avgPosition: 6.5 },
    topQueries: QUERIES,
  },
};

// ── 1. Padrão ao abrir: impressões ↓ ────────────────────────────────────────
console.log("\n── Padrão ao abrir ──");
const a = montar(SNAPSHOT);
const listaA = termos(a.dom.nos.seoList.innerHTML);

caso("seletor aparece quando há consultas reais", a.dom.box.hidden === false);
caso("mostra 5 termos antes de 'Ver mais'", listaA.length === 5,
     `${listaA.length}`);
caso("abre por impressões ↓, não pela ordem de cliques do Search Console",
     listaA.join(" | ") === [
       "espirometria preco",            // 731 impressões
       "espirometria rio de janeiro",   // 402
       "exame de sopro",                // 288
       "soprolife",                     // 118
       "espirometria domiciliar rj",    // 96
     ].join(" | "), listaA.join(" | "));
caso("o termo com muita exposição e 2 cliques lidera (era o 6º da API)",
     listaA[0] === "espirometria preco");
caso("botão Impressões é o ativo", a.dom.botoes.impressions.classes.has("is-active"));
caso("aria-pressed=true só no ativo",
     a.dom.botoes.impressions.getAttribute("aria-pressed") === "true"
     && CHAVES.slice(1).every((k) => a.dom.botoes[k].getAttribute("aria-pressed") === "false"));
caso("seta ↓ no ativo e vazia nos demais",
     a.dom.setas.impressions.textContent === "↓"
     && CHAVES.slice(1).every((k) => a.dom.setas[k].textContent === ""));
caso("select espelha o critério ativo", a.dom.select.value === "impressions");
caso("botão de sentido mostra ↓", a.dom.botaoDir.textContent === "↓");
caso("subtítulo continua marcando dados reais",
     a.dom.nos.seoListSubtitle.textContent === "Search Console · dados reais");
caso("barras não crescem de cima para baixo",
     larguras(a.dom.nos.seoList.innerHTML).every((w, i, v) => i === 0 || v[i - 1] >= w),
     larguras(a.dom.nos.seoList.innerHTML).join(","));
caso("posição não entra nos chips quando não é o critério ativo",
     !a.dom.nos.seoList.innerHTML.includes("pos. "));

// ── 2. Os dados do Search Console não são tocados ───────────────────────────
console.log("── O snapshot não é alterado ──");
const antes = QUERIES.map((q) => q.query).join("|");
const b = montar(SNAPSHOT);
vm.runInContext("state.mktSeoSort = { key: 'clicks', dir: 'asc' };", b.ctx);
b.render();
caso("a lista recebida do Search Console mantém a ordem original",
     QUERIES.map((q) => q.query).join("|") === antes);
caso("o array do snapshot é o mesmo objeto, sem reordenação no lugar",
     SNAPSHOT.searchConsole.topQueries === QUERIES);
caso("totais do Search Console intactos",
     SNAPSHOT.searchConsole.totals.impressions === 2308
     && SNAPSHOT.searchConsole.totals.clicks === 69);

// ── 3. Desempate: impressões → cliques → CTR ────────────────────────────────
console.log("── Desempate ──");
const c = montar(SNAPSHOT);
vm.runInContext("state.mktSeoExpandido = true;", c.ctx);
c.render();
const todosC = termos(c.dom.nos.seoList.innerHTML);
caso("lista completa renderizada ao expandir", todosC.length === QUERIES.length,
     `${todosC.length}/${QUERIES.length}`);
caso("40 impressões: 3 cliques vem antes de 1 clique",
     todosC.indexOf("espirometria tijuca") < todosC.indexOf("espirometria copacabana"));
caso("25 impressões, 0 clique e CTR igual: desempata pela melhor posição",
     todosC.indexOf("espirometria botafogo") < todosC.indexOf("espirometria barra"));
caso("cauda longa de 1 impressão fica no fim",
     todosC.slice(-12).every((t) => t.startsWith("cauda longa")));

// Empate de impressões e cliques com CTR diferente só existe se o CTR não for
// derivado — o Search Console arredonda, então é um caso real. Fixture própria:
const d = montar({
  meta: META,
  searchConsole: {
    totals: { impressions: 60, clicks: 4, ctr: 0.0667, avgPosition: 9 },
    topQueries: [
      { query: "ctr menor", impressions: 30, clicks: 2, ctr: 0.0600, avgPosition: 9.0 },
      { query: "ctr maior", impressions: 30, clicks: 2, ctr: 0.0700, avgPosition: 9.0 },
    ],
  },
});
caso("impressões e cliques empatados: maior CTR primeiro",
     termos(d.dom.nos.seoList.innerHTML).join("|") === "ctr maior|ctr menor",
     termos(d.dom.nos.seoList.innerHTML).join("|"));

// Empate nas QUATRO métricas: acontece de verdade na cauda longa (1 impressão,
// 0 clique, mesma posição média). Aí só o alfabético mantém a lista estável.
const dd = montar({
  meta: META,
  searchConsole: {
    totals: { impressions: 3, clicks: 0, ctr: 0, avgPosition: 21 },
    topQueries: [
      linha("zelador espirometria", 1, 0, 21.0),
      linha("aparelho de sopro preco", 1, 0, 21.0),
      linha("medir capacidade pulmonar", 1, 0, 21.0),
    ],
  },
});
caso("empate nas quatro métricas cai no alfabético (ordem estável)",
     termos(dd.dom.nos.seoList.innerHTML).join("|")
       === "aparelho de sopro preco|medir capacidade pulmonar|zelador espirometria",
     termos(dd.dom.nos.seoList.innerHTML).join("|"));

// ── 4. Os quatro critérios ordenam de fato ──────────────────────────────────
console.log("── Os quatro critérios ──");
const e = montar(SNAPSHOT);
vm.runInContext("state.mktSeoExpandido = true;", e.ctx);
e.render();

function ordenarPor(sessao, chave) {
  sessao.dom.botoes[chave].disparar("click");
  return termos(sessao.dom.nos.seoList.innerHTML);
}

const porCliques = ordenarPor(e, "clicks");
caso("Cliques ↓ coloca 'soprolife' (21 cliques) em primeiro",
     porCliques[0] === "soprolife", porCliques.slice(0, 3).join(" | "));
caso("Cliques ↓ é monotônico",
     (() => {
       const mapa = new Map(QUERIES.map((q) => [q.query, q.clicks]));
       return porCliques.every((t, i) => i === 0 || mapa.get(porCliques[i - 1]) >= mapa.get(t));
     })());
caso("botão Cliques fica ativo e Impressões sai",
     e.dom.botoes.clicks.classes.has("is-active")
     && !e.dom.botoes.impressions.classes.has("is-active"));
caso("barra passa a medir cliques (o 1º termo fica em 100%)",
     larguras(e.dom.nos.seoList.innerHTML)[0] === 100);

const porCtr = ordenarPor(e, "ctr");
caso("CTR ↓ coloca 'espirometria onde fazer rj' (9/47) em primeiro",
     porCtr[0] === "espirometria onde fazer rj", porCtr.slice(0, 3).join(" | "));
caso("CTR ↓ é monotônico",
     (() => {
       const mapa = new Map(QUERIES.map((q) => [q.query, q.ctr]));
       return porCtr.every((t, i) => i === 0 || mapa.get(porCtr[i - 1]) >= mapa.get(t));
     })());

const porPosicao = ordenarPor(e, "avgPosition");
caso("Posição abre em ASCENDENTE (1 é o melhor lugar no Google)",
     porPosicao[0] === "soprolife", porPosicao.slice(0, 3).join(" | "));
caso("Posição é monotônica crescente",
     (() => {
       const mapa = new Map(QUERIES.map((q) => [q.query, q.avgPosition]));
       return porPosicao.every((t, i) => i === 0 || mapa.get(porPosicao[i - 1]) <= mapa.get(t));
     })());
caso("ordenando por Posição a linha passa a mostrar a posição",
     e.dom.nos.seoList.innerHTML.includes("pos. 1,4"));
caso("seta do ativo em Posição é ↑ (ascendente)",
     e.dom.setas.avgPosition.textContent === "↑"
     && e.dom.botaoDir.textContent === "↑");
caso("barra na Posição volta às impressões (posição não tem comprimento)",
     larguras(e.dom.nos.seoList.innerHTML)[0] < 100);

// ── 5. Ascendente/descendente ───────────────────────────────────────────────
console.log("── Ascendente / descendente ──");
const f = montar(SNAPSHOT);
vm.runInContext("state.mktSeoExpandido = true;", f.ctx);
f.render();
const desc = termos(f.dom.nos.seoList.innerHTML);
f.dom.botoes.impressions.disparar("click");   // clicar no ativo inverte
const asc = termos(f.dom.nos.seoList.innerHTML);
caso("clicar no critério ativo inverte o sentido",
     asc[0] !== desc[0] && asc.join("|") !== desc.join("|"));
caso("impressões ↑ começa pela cauda longa de 1 impressão",
     asc[0].startsWith("cauda longa"), asc[0]);
caso("seta do ativo vira ↑", f.dom.setas.impressions.textContent === "↑");
caso("título do ativo convida a inverter",
     f.dom.botoes.impressions.title.includes("clique para inverter"));
f.dom.botaoDir.disparar("click");
caso("botão de sentido volta para ↓",
     f.dom.setas.impressions.textContent === "↓"
     && termos(f.dom.nos.seoList.innerHTML).join("|") === desc.join("|"));

// Trocar de critério recomeça no padrão daquele critério, sem herdar o sentido.
f.dom.botoes.impressions.disparar("click");    // impressões ↑
f.dom.botoes.clicks.disparar("click");
caso("trocar de critério não herda o sentido invertido",
     f.dom.setas.clicks.textContent === "↓"
     && termos(f.dom.nos.seoList.innerHTML)[0] === "soprolife");

// ── 6. Select compacto (celular) ────────────────────────────────────────────
console.log("── Select compacto ──");
const g = montar(SNAPSHOT);
g.dom.select.value = "ctr";
g.dom.select.disparar("change");
caso("select troca a ordenação",
     termos(g.dom.nos.seoList.innerHTML)[0] === "espirometria onde fazer rj");
caso("botões acompanham a escolha do select",
     g.dom.botoes.ctr.classes.has("is-active"));
g.dom.select.value = "avgPosition";
g.dom.select.disparar("change");
caso("select em Posição adota o padrão ascendente",
     g.dom.botaoDir.textContent === "↑"
     && termos(g.dom.nos.seoList.innerHTML)[0] === "soprolife");
caso("botão de sentido traz rótulo acessível",
     String(g.dom.botaoDir.getAttribute("aria-label")).includes("clique para inverter"));
g.dom.select.value = "nao-existe";
g.dom.select.disparar("change");
caso("valor desconhecido no select não muda a ordenação",
     termos(g.dom.nos.seoList.innerHTML)[0] === "soprolife");
caso("select volta a mostrar a ordenação em vigor",
     g.dom.select.value === "avgPosition", g.dom.select.value);

// ── 7. "Ver mais" sobrevive à troca de ordenação ────────────────────────────
console.log("── 'Ver mais' e período/filtros ──");
const h = montar(SNAPSHOT);
caso("botão 'Ver mais' anuncia o resto",
     h.dom.nos.seoMoreBtn.hidden === false
     && h.dom.nos.seoMoreBtn.textContent === `Ver mais ${QUERIES.length - 5} termos ↓`,
     h.dom.nos.seoMoreBtn.textContent);
h.dom.nos.seoMoreBtn.onclick();
caso("expandir mostra todos os termos",
     termos(h.dom.nos.seoList.innerHTML).length === QUERIES.length);
caso("rótulo passa a 'Recolher'", h.dom.nos.seoMoreBtn.textContent === "Recolher ↑");
h.dom.botoes.clicks.disparar("click");
caso("trocar a ordenação não recolhe a lista aberta",
     termos(h.dom.nos.seoList.innerHTML).length === QUERIES.length);
caso("rótulo continua 'Recolher' depois de reordenar",
     h.dom.nos.seoMoreBtn.textContent === "Recolher ↑");
h.dom.nos.seoMoreBtn.onclick();
caso("recolher volta aos 5 primeiros da ordenação vigente",
     termos(h.dom.nos.seoList.innerHTML).length === 5
     && termos(h.dom.nos.seoList.innerHTML)[0] === "soprolife");

// Lista curta: nada a expandir.
const curto = montar({
  meta: META,
  searchConsole: { totals: { impressions: 3, clicks: 0, ctr: 0, avgPosition: 9 },
                   topQueries: [linha("termo unico", 3, 0, 9.0)] },
});
caso("lista com até 5 termos não mostra 'Ver mais'",
     curto.dom.nos.seoMoreBtn.hidden === true);

// ── 8. Métrica ausente nunca passa à frente ─────────────────────────────────
console.log("── Métrica ausente ──");
const i = montar({
  meta: META,
  searchConsole: {
    totals: { impressions: 30, clicks: 3, ctr: 0.1, avgPosition: 5 },
    topQueries: [
      { query: "sem posicao", impressions: 20, clicks: 2, ctr: 0.1 },
      { query: "com posicao", impressions: 10, clicks: 1, ctr: 0.1, avgPosition: 12.0 },
    ],
  },
});
vm.runInContext("state.mktSeoSort = { key: 'avgPosition', dir: 'asc' };", i.ctx);
i.render();
caso("sem posição vai para o fim no ascendente",
     termos(i.dom.nos.seoList.innerHTML).join("|") === "com posicao|sem posicao");
vm.runInContext("state.mktSeoSort = { key: 'avgPosition', dir: 'desc' };", i.ctx);
i.render();
caso("sem posição continua no fim no descendente",
     termos(i.dom.nos.seoList.innerHTML).join("|") === "com posicao|sem posicao");
caso("posição ausente aparece como N/D, não como número inventado",
     i.dom.nos.seoList.innerHTML.includes("pos. N/D"));

// ── 9. Sem consultas reais: seletor desaparece ──────────────────────────────
console.log("── Modelo demonstrativo ──");
const demo = montar(
  { meta: { ...META, safeToDisplay: false }, searchConsole: { topQueries: [] } },
  { seo: [{ termo: "espirometria rj", impressoes: 120, cliques: 8, posicao: 6 }] },
);
caso("seletor fica oculto sem consultas reais", demo.dom.box.hidden === true);
caso("a lista cai no modelo demonstrativo",
     demo.dom.nos.seoList.innerHTML.includes("espirometria rj")
     && demo.dom.nos.seoList.innerHTML.includes("seo-item"));
caso("subtítulo não mente dizendo 'dados reais'",
     demo.dom.nos.seoListSubtitle.textContent !== "Search Console · dados reais");

const semSnapshot = montar(null, { seo: [] });
caso("snapshot ausente também esconde o seletor",
     semSnapshot.dom.box.hidden === true);

// ── 10. Guarda de escopo: a ordenação é só de apresentação ──────────────────
console.log("── Guarda de escopo ──");
caso("o renderizador copia a lista antes de ordenar (nenhum .sort no original)",
     trechos.ordenacao.includes("[...queries].sort("));
caso("nenhuma chamada de rede foi introduzida no trecho",
     !/\bfetch\s*\(|XMLHttpRequest/.test(trechos.ordenacao));
caso("o trecho não mexe em período, filtros nem refresh do Marketing",
     !/mktPeriod|mktRefresh|periodStart|lookbackDays/.test(trechos.ordenacao));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
