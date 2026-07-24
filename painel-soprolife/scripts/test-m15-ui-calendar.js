#!/usr/bin/env node
// SoproLife — M15.4A UI + Calendário SoproLife.
//
// 1) Testes FUNCIONAIS do m15-datepicker.js num DOM falso mínimo (sem jsdom,
//    sem rede): abrir/fechar, Escape, clique fora, seleção, Hoje, Limpar,
//    navegação por teclado, DD/MM/AAAA, preservação do valor de API e da
//    precisão parcial (AAAA-MM / AAAA nunca viram dia exato sozinhos).
// 2) Guardas ESTÁTICAS: sem recurso externo, sem persistência de token,
//    payloads dos formulários intactos, classes responsivas presentes e
//    feature flag do M15 ligada (go-live controlado M15.5A) com api_base
//    de mesma origem.
//
// Uso: node painel-soprolife/scripts/test-m15-ui-calendar.js
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
const dpPath = path.join(RAIZ, "js", "m15-datepicker.js");
const nucleoPath = path.join(RAIZ, "js", "m15-nucleo.js");
const cssPath = path.join(RAIZ, "css", "m15.css");
const dpSrc = fs.readFileSync(dpPath, "utf8");
const nucleoSrc = fs.readFileSync(nucleoPath, "utf8");
const cssSrc = fs.readFileSync(cssPath, "utf8");
const indexSrc = fs.readFileSync(path.join(RAIZ, "index.html"), "utf8");
const configSrc = fs.readFileSync(path.join(RAIZ, "data", "m15-config.json"), "utf8");

const dp = require(dpPath);

// ─────────────────────────────── DOM falso ─────────────────────────────────
// Implementa SOMENTE a superfície de DOM que o m15-datepicker.js usa
// (documentada no cabeçalho dele). Sem seletores, sem innerHTML.

function FakeEl(doc, tag) {
  this.ownerDocument = doc;
  this.tagName = String(tag).toUpperCase();
  this.nodeName = this.tagName;
  this.children = [];
  this.parentNode = null;
  this._attrs = {};
  this._listeners = {};
  this.className = "";
  this.textContent = "";
  this.value = "";
  this.disabled = false;
  this.tabIndex = -1;
}
Object.defineProperty(FakeEl.prototype, "firstChild", {
  get() { return this.children[0] || null; },
});
FakeEl.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
FakeEl.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
};
FakeEl.prototype.removeAttribute = function (k) { delete this._attrs[k]; };
FakeEl.prototype.appendChild = function (c) {
  if (c.parentNode) c.parentNode.removeChild(c);
  c.parentNode = this;
  this.children.push(c);
  return c;
};
FakeEl.prototype.insertBefore = function (c, ref) {
  if (c.parentNode) c.parentNode.removeChild(c);
  c.parentNode = this;
  const i = this.children.indexOf(ref);
  if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
  return c;
};
FakeEl.prototype.removeChild = function (c) {
  const i = this.children.indexOf(c);
  if (i >= 0) this.children.splice(i, 1);
  c.parentNode = null;
  return c;
};
FakeEl.prototype.addEventListener = function (t, fn) {
  (this._listeners[t] = this._listeners[t] || []).push(fn);
};
FakeEl.prototype.removeEventListener = function (t, fn) {
  const list = this._listeners[t] || [];
  const i = list.indexOf(fn);
  if (i >= 0) list.splice(i, 1);
};
FakeEl.prototype.focus = function () { this.ownerDocument.activeElement = this; };
FakeEl.prototype.trigger = function (t, ev) {
  ev = ev || {};
  if (!ev.target) ev.target = this;
  if (!ev.preventDefault) ev.preventDefault = function () {};
  (this._listeners[t] || []).slice().forEach((fn) => fn(ev));
  return ev;
};

function FakeDoc() {
  this.activeElement = null;
  this._listeners = {};
}
FakeDoc.prototype.createElement = function (tag) { return new FakeEl(this, tag); };
FakeDoc.prototype.addEventListener = FakeEl.prototype.addEventListener;
FakeDoc.prototype.removeEventListener = FakeEl.prototype.removeEventListener;
FakeDoc.prototype.trigger = FakeEl.prototype.trigger;

function montarCampo(doc, opts) {
  opts = opts || {};
  const form = doc.createElement("form");
  const label = doc.createElement("label");
  const input = doc.createElement("input");
  input.setAttribute("name", opts.name || "data");
  input.setAttribute("data-m15-date", opts.mode || "partial");
  if (opts.required) input.setAttribute("required", "");
  input.value = opts.value || "";
  form.appendChild(label);
  label.appendChild(input);
  return { form, label, input };
}

function achar(el, pred, out) {
  out = out || [];
  if (pred(el)) out.push(el);
  (el.children || []).forEach((c) => achar(c, pred, out));
  return out;
}
const porClasse = (cls) => (el) => (" " + el.className + " ").indexOf(" " + cls + " ") !== -1;

// ───────────────────── A) formatos e precisão parcial ──────────────────────
console.log("A) Formatos DD/MM/AAAA ↔ backend e precisão parcial");
caso('isoToBr("2026-07-18") = 18/07/2026', dp.isoToBr("2026-07-18") === "18/07/2026");
caso('isoToBr("2026-07") = 07/2026 (mês preservado)', dp.isoToBr("2026-07") === "07/2026");
caso('isoToBr("2026") = 2026 (ano preservado)', dp.isoToBr("2026") === "2026");
caso('brToIso("18/07/2026") = 2026-07-18', dp.brToIso("18/07/2026") === "2026-07-18");
caso('brToIso("1/7/2026") normaliza = 2026-07-01', dp.brToIso("1/7/2026") === "2026-07-01");
caso('brToIso("07/2026") = 2026-07 (não vira dia)', dp.brToIso("07/2026") === "2026-07");
caso('brToIso("2026") = 2026 (não vira dia)', dp.brToIso("2026") === "2026");
caso('ISO digitado passa direto ("2026-07-18")', dp.brToIso("2026-07-18") === "2026-07-18");
caso("31/02 → inválido (calendário real)", !dp.parseFlex("31/02/2026").ok);
caso("29/02/2024 → válido (bissexto)", dp.parseFlex("29/02/2024").ok);
caso("29/02/2026 → inválido (não bissexto)", !dp.parseFlex("29/02/2026").ok);
caso("13/2026 → inválido (mês 13)", !dp.parseFlex("13/2026").ok);
caso("texto irreconhecível volta cru (fail-open p/ servidor validar)",
     dp.brToIso("sem-data") === "sem-data");
caso("segunda-feira primeiro: 01/07/2026 é qua → índice 2",
     dp.mondayIndex(2026, 7, 1) === 2);

// ───────────────────── B) comportamento do calendário ──────────────────────
console.log();
console.log("B) Calendário SoproLife (DOM falso, hoje = 19/07/2026)");
dp._setToday(() => ({ y: 2026, m: 7, d: 19 }));

{
  const doc = new FakeDoc();
  const { form, input } = montarCampo(doc, { mode: "partial", value: "2026-07-15" });
  const pickers = dp.attachAll(form, { document: doc });
  caso("attachAll anexa 1 calendário", pickers.length === 1);
  const p = pickers[0];
  caso("input original vira portador escondido (type=hidden)", p.holder.type === "hidden");
  caso("portador mantém o name do payload", p.holder.getAttribute("name") === "data");
  caso("exibição inicial em DD/MM/AAAA", p.display.value === "15/07/2026");
  caso("attachAll é idempotente (2ª chamada = 0)",
       dp.attachAll(form, { document: doc }).length === 0);

  caso("fechado por padrão", !p.isOpen());
  p.btn.trigger("click");
  caso("clique no botão abre", p.isOpen());
  caso("aria-expanded=true ao abrir", p.btn.getAttribute("aria-expanded") === "true");
  caso("cabeçalho mostra mês e ano", p.titleEl.textContent === "julho 2026");
  caso("popup é role=dialog com aria-label", p.pop.getAttribute("role") === "dialog" &&
       !!p.pop.getAttribute("aria-label"));

  const dows = achar(p.grid, porClasse("m15-cal-dow"));
  caso("semana começa na segunda-feira", dows.length === 7 && dows[0].textContent === "seg" &&
       dows[6].textContent === "dom");
  const outDays = achar(p.grid, porClasse("m15-cal-out"));
  caso("dias fora do mês aparecem desabilitados", outDays.length === 2 &&
       outDays.every((b) => b.disabled === true));
  caso("hoje (19) marcado com aria-current=date",
       p.dayButtons[19].getAttribute("aria-current") === "date" &&
       porClasse("m15-cal-today")(p.dayButtons[19]));
  caso("dia selecionado (15) marcado com aria-selected",
       p.dayButtons[15].getAttribute("aria-selected") === "true" &&
       porClasse("m15-cal-selected")(p.dayButtons[15]));
  caso("foco inicial no dia selecionado (roving tabindex)",
       p.focused === 15 && p.dayButtons[15].tabIndex === 0 &&
       doc.activeElement === p.dayButtons[15]);

  p.grid.trigger("keydown", { key: "ArrowRight" });
  caso("ArrowRight move o foco para 16", p.focused === 16);
  p.grid.trigger("keydown", { key: "ArrowDown" });
  caso("ArrowDown avança uma semana (23)", p.focused === 23);
  p.grid.trigger("keydown", { key: "Home" });
  caso("Home vai à segunda-feira da semana (20)", p.focused === 20);
  p.grid.trigger("keydown", { key: "End" });
  caso("End vai ao domingo da semana (26)", p.focused === 26);
  p.grid.trigger("keydown", { key: "PageDown" });
  caso("PageDown muda para o mês seguinte", p.titleEl.textContent === "agosto 2026");
  p.grid.trigger("keydown", { key: "PageUp" });
  caso("PageUp volta para julho", p.titleEl.textContent === "julho 2026");
  p.grid.trigger("keydown", { key: "Enter" });
  caso("Enter seleciona o dia focado (26) no formato de API",
       p.holder.value === "2026-07-26" && p.display.value === "26/07/2026");
  caso("selecionar fecha e devolve o foco ao campo",
       !p.isOpen() && doc.activeElement === p.display);

  p.btn.trigger("click");
  const fora = doc.createElement("div");
  doc.trigger("mousedown", { target: fora });
  caso("clique fora fecha", !p.isOpen());

  p.btn.trigger("click");
  doc.trigger("mousedown", { target: p.dayButtons[10] });
  caso("clique dentro não fecha", p.isOpen());
  doc.trigger("keydown", { key: "Escape" });
  caso("Escape fecha e devolve o foco", !p.isOpen() && doc.activeElement === p.display);

  p.btn.trigger("click");
  const navs = achar(p.pop, porClasse("m15-cal-nav"));
  navs[0].trigger("click");
  caso("‹ navega para o mês anterior (junho 2026)", p.titleEl.textContent === "junho 2026");
  navs[1].trigger("click");
  caso("› navega para o mês seguinte (julho 2026)", p.titleEl.textContent === "julho 2026");

  const foot = achar(p.pop, porClasse("m15-cal-foot"))[0];
  const hojeBtn = foot.children[0], limparBtn = foot.children[1];
  caso("rodapé tem Hoje e Limpar", hojeBtn.textContent === "Hoje" &&
       limparBtn.textContent === "Limpar");
  hojeBtn.trigger("click");
  caso("Hoje seleciona 19/07/2026 no formato de API",
       p.holder.value === "2026-07-19" && p.display.value === "19/07/2026" && !p.isOpen());
  p.btn.trigger("click");
  achar(p.pop, porClasse("m15-cal-foot"))[0].children[1].trigger("click");
  caso("Limpar esvazia exibição e valor de API",
       p.holder.value === "" && p.display.value === "" && !p.isOpen());
}

{
  // precisão parcial digitada nunca vira dia exato
  const doc = new FakeDoc();
  const { form } = montarCampo(doc, { mode: "partial" });
  const p = dp.attachAll(form, { document: doc })[0];
  p.display.value = "07/2026";
  p.display.trigger("input");
  caso('digitar "07/2026" envia "2026-07" (mês, sem dia inventado)',
       p.holder.value === "2026-07");
  p.btn.trigger("click");
  caso("abrir o calendário não altera valor parcial", p.holder.value === "2026-07");
  caso("calendário abre no mês do valor parcial", p.titleEl.textContent === "julho 2026");
  caso("modo parcial mostra dica de MM/AAAA no popup",
       achar(p.pop, porClasse("m15-cal-hint")).length === 1);
  doc.trigger("keydown", { key: "Escape" });
  caso("fechar sem selecionar preserva a precisão original", p.holder.value === "2026-07");
  p.display.value = "2026";
  p.display.trigger("input");
  caso('digitar "2026" envia "2026" (ano, sem mês/dia inventados)',
       p.holder.value === "2026");
  p.display.value = "ab/cd";
  p.display.trigger("input");
  caso("texto inválido segue cru para o servidor validar (fail-closed lá)",
       p.holder.value === "ab/cd" && porClasse("m15-date-invalid")(p.wrapper));
  p.display.value = "1/7/2026";
  p.display.trigger("change");
  caso("change normaliza a exibição (01/07/2026) e o valor (2026-07-01)",
       p.display.value === "01/07/2026" && p.holder.value === "2026-07-01");
}

{
  // campo de data completa: required migra para o campo visível
  const doc = new FakeDoc();
  const { form } = montarCampo(doc, { mode: "full", required: true, value: "2026-07-18" });
  const p = dp.attachAll(form, { document: doc })[0];
  caso("campo completo exibe 18/07/2026", p.display.value === "18/07/2026");
  caso("required migra do portador para o campo visível",
       p.display.getAttribute("required") != null &&
       p.holder.getAttribute("required") == null);
  caso("Alt+ArrowDown abre pelo teclado", (p.display.trigger("keydown",
       { key: "ArrowDown", altKey: true }), p.isOpen()));
  doc.trigger("keydown", { key: "Escape" });
}

// ───────────────── C) guardas estáticas (arquivos da UI) ───────────────────
console.log();
console.log("C) Sem recurso externo, sem persistência de token");
caso("m15-datepicker.js nunca usa innerHTML (DOM nó a nó)",
     !/\.innerHTML/.test(dpSrc));
caso("m15-datepicker.js sem URL externa (http/https)",
     !/https?:\/\//.test(dpSrc));
caso("m15-datepicker.js sem localStorage/sessionStorage",
     dpSrc.indexOf("localStorage") === -1 && dpSrc.indexOf("sessionStorage") === -1);
caso("m15-datepicker.js sem fetch/XMLHttpRequest/WebSocket",
     dpSrc.indexOf("fetch(") === -1 && dpSrc.indexOf("XMLHttpRequest") === -1 &&
     dpSrc.indexOf("WebSocket") === -1);
caso("m15.css sem @import nem url() remota",
     cssSrc.indexOf("@import") === -1 && !/url\(\s*['"]?https?:/.test(cssSrc));
caso("m15-nucleo.js: token nunca é gravado em storage",
     !/(localStorage|sessionStorage)\.setItem\(["']soproM15Token/.test(nucleoSrc));
caso("m15-nucleo.js mantém a limpeza do token legado",
     nucleoSrc.indexOf('localStorage.removeItem("soproM15Token")') !== -1);
caso("único setItem permitido continua sendo apenas o flag legado (nenhum novo)",
     (nucleoSrc.match(/\.setItem\(/g) || []).length === 0);
caso("index.html carrega o datepicker ANTES do núcleo (defer em ordem)",
     /m15-datepicker\.js[^"]*" defer><\/script>\s*<script src="\.\/js\/m15-nucleo\.js/.test(indexSrc));

console.log();
console.log("C2) Feature flag ligada (go-live M15.5A) e API base intocada");
{
  const cfg = JSON.parse(configSrc);
  caso("m15-config.json: enabled === true (go-live M15.5A)", cfg.enabled === true);
  caso("m15-config.json: api_base de mesma origem",
       cfg.api_base === "/painel-soprolife/api/m15" && cfg.api_base.indexOf("://") === -1);
  caso("m15-nucleo.js mantém apiBase de mesma origem",
       nucleoSrc.indexOf('apiBase: "/painel-soprolife/api/m15"') !== -1);
}

// ───────────── D) payloads dos formulários preservados ─────────────────────
// M16 — a criação de Lead, Espirometria/Consulta e Financeiro saiu do Núcleo
// M15 (m15-nucleo.js) e passou a viver exclusivamente na Central de
// Cadastros (js/central-cadastros.js), única implementação de cada
// formulário. Encaminhamento (Pacientes de Parceiros) NÃO é uma das 7 abas
// canônicas da Central e continua no Núcleo M15, por isso sua checagem
// permanece sobre nucleoSrc, intocada.
console.log();
console.log("D) Payloads intactos (nomes de campo e chaves de API) — agora na Central");

const centralJsPath = path.join(RAIZ, "js", "central-cadastros.js");
const centralJsSrc = fs.readFileSync(centralJsPath, "utf8");

function trechoCentral(marcadorInicio, marcadorFim) {
  const i = centralJsSrc.indexOf(marcadorInicio);
  if (i < 0) return "";
  const f = marcadorFim ? centralJsSrc.indexOf(marcadorFim, i) : -1;
  return f > i ? centralJsSrc.slice(i, f) : "";
}

const leadSrc = trechoCentral("LOADERS.lead = function", "function loadLeadRecents(");
["person_id", '"servico_interesse"', '"origem"', '"canal_entrada"', '"responsavel"',
 '"data_primeiro_contato"', '"data_retomada_manual"', '"observacao"'].forEach((k) => {
  caso(`Central — Novo lead envia ${k}`, leadSrc.indexOf(k) !== -1);
});
caso("Central — Novo lead: responsável está antes da linha de datas",
     leadSrc.indexOf('fld("Responsável"') < leadSrc.indexOf('fld("1º contato"'));

// M20: Espirometria e Consulta viraram blocos do fluxo único "Novo
// atendimento"; os mesmos campos continuam sendo enviados, agora pelos
// montadores montarEspirometria/montarConsulta.
const espSrc = trechoCentral("function montarEspirometria(", "function montarConsulta(");
['status: val(form, "esp_status")', '"modalidade"', '"local_atendimento"',
 "partner_id", "partner_unit_id", '"origem"', '"responsavel"', '"observacao"',
 "broncodilatador"].forEach((k) => {
  caso(`Central — Espirometria envia ${k}`, espSrc.indexOf(k) !== -1);
});
caso("Central — Novo atendimento envia idempotency_key",
     trechoCentral("LOADERS.atendimento = function", "function montarEspirometria(")
       .indexOf("idempotency_key: m15().idemKey()") !== -1);

const conSrc = trechoCentral("function montarConsulta(", "function montarFinanceiro(");
['status: val(form, "con_status")', '"modalidade"', '"profissional"',
 '"observacao"'].forEach((k) => {
  caso(`Central — Consulta envia ${k}`, conSrc.indexOf(k) !== -1);
});

const finSrc = trechoCentral("LOADERS.financeiro = function", "function loadFinRecents(");
['tipo: val(form, "tipo")', "valor,", 'status: val(form, "status")',
 "idempotency_key: m15().idemKey()", '"categoria"', '"descricao"', '"data_competencia"',
 '"data_recebimento"', '"forma_pagamento"', '"origem_preco"',
 "vinculo.spirometry_exam_id", "vinculo.consultation_id"].forEach((k) => {
  caso(`Central — Financeiro envia ${k}`, finSrc.indexOf(k) !== -1);
});
caso("Central — Financeiro nunca expõe campo de nome/telefone/CPF no formulário",
     !/name="nome"|name="telefone"|name="cpf"/.test(finSrc));
caso("Central — Financeiro avisa que o vínculo substitui nome/telefone/CPF",
     finSrc.indexOf("O financeiro NÃO guarda nome, telefone ou CPF") !== -1);

caso("Encaminhamento (Núcleo M15): vínculo LAN em grupo técnico — entidade fora das 7 abas da Central",
     /grp\("Vínculo técnico \(opcional\)"[\s\S]{0,400}financial_entry_id[\s\S]{0,600}"m15-group-tech"/.test(nucleoSrc));

console.log();
console.log("E) Grid responsivo e datas do calendário nos formulários");
caso("m15.css define grid de 12 colunas", cssSrc.indexOf("repeat(12, 1fr)") !== -1);
[".m15-span-2", ".m15-span-3", ".m15-span-4", ".m15-span-6", ".m15-span-8",
 ".m15-span-9", ".m15-field", ".m15-field-label", ".m15-field-help", ".m15-group",
 ".m15-group-tech", ".m15-actions", ".m15-cal", ".m15-cal-day", ".m15-date-btn"]
  .forEach((cls) => {
    caso(`m15.css tem ${cls}`, cssSrc.indexOf(cls) !== -1);
  });
caso("m15.css colapsa em 1 coluna no mobile (max-width: 760px)",
     cssSrc.indexOf("max-width: 760px") !== -1);
caso("m15.css tem passo intermediário (max-width: 1120px)",
     cssSrc.indexOf("max-width: 1120px") !== -1);
caso("altura padrão de controle 40px", cssSrc.indexOf("height: 40px") !== -1);
caso("rótulo com altura fixa de 1 linha (não empurra o controle)",
     /\.m15-field-label\s*\{[^}]*height: 18px/.test(cssSrc));

// Campos que continuam no Núcleo M15 (edição de registros existentes, ou
// entidades fora das 7 abas canônicas da Central — Encaminhamento/Follow-up).
const camposDataNucleo = [
  ['dateInp("data_nascimento"', "Pessoas (edição)"],
  ['dateInp("data_retomada_manual"', "Leads (edição)"],
  ['dateInp("data_inicio", "", { parcial: true })', "Parceria início (parcial)"],
  ['dateInp("data_encaminhamento", "", { parcial: true })', "Encaminhamento (parcial)"],
  ['dateInp("data_agendada"', "Encaminhamento agendada"],
  ['dateInp("data_realizacao"', "Encaminhamento realização"],
  ['dateInp("data_envio_laudo"', "Encaminhamento laudo"],
  ['dateInp("proximo_followup"', "Encaminhamento próximo follow-up"],
  ['dateInp("due_date"', "Follow-up vencimento"],
  ['dateInp("nova_data", "", { required: true })', "Follow-up nova tentativa"],
  ['dateInp("data_recebimento"', "Financeiro recebimento (edição)"],
];
camposDataNucleo.forEach(([marca, nome]) => {
  caso(`calendário SoproLife (Núcleo M15) em: ${nome}`, nucleoSrc.indexOf(marca) !== -1);
});
caso("attachDates roda após cada render e no editor (Núcleo M15)",
     nucleoSrc.indexOf("loader().then(function () { attachDates(); })") !== -1 &&
     nucleoSrc.indexOf("attachDates(box)") !== -1);

// Campos de criação que migraram para a Central de Cadastros.
const camposDataCentral = [
  ['dateInp(prefix + "_nasc", "")', "Central — pessoa inline (nascimento)"],
  ['dateInp("data_primeiro_contato", "", { parcial: true })', "Central — Leads (parcial)"],
  ['dateInp("data_retomada_manual", "")', "Central — Lead próxima ação"],
  ['dateInp("esp_data", "", { parcial: true })', "Central — Espirometria (parcial)"],
  ['dateInp("con_data", "", { parcial: true })', "Central — Consulta (parcial)"],
  ['dateInp("data_competencia", "", { parcial: true })', "Central — Financeiro competência (parcial)"],
  ['dateInp("data_recebimento", "")', "Central — Financeiro recebimento"],
];
camposDataCentral.forEach(([marca, nome]) => {
  caso(`calendário SoproLife em: ${nome}`, centralJsSrc.indexOf(marca) !== -1);
});
caso("Central: attachDates roda após cada render",
     centralJsSrc.indexOf(".then(() => attachDates(root))") !== -1);

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
