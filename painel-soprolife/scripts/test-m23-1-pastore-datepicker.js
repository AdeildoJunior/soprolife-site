#!/usr/bin/env node
// SoproLife M23.1 — regressão do calendário em "Data do exame" da
// Espirometria Pastore.
//
// Causa raiz encontrada: "Data do exame" é definida UMA VEZ em
// blocoEspirometriaConteudoHtml() (commonStart), compartilhada entre as
// variantes SoproLife e Pastore — então a marcação em si sempre foi
// idêntica para as duas. O bug era de TEMPO DE EXECUÇÃO: attachDates(root)
// só rodava uma vez, logo após o primeiro render da aba "Novo atendimento".
// Quando o operador trocava o tipo de atendimento para/de
// "Espirometria Pastore", aplicarModo() substituía blocoEsp.innerHTML por um
// HTML novo (blocoEspirometriaConteudoHtml de novo) para atualizar os campos
// somente-leitura da Pastore — e esse novo <input data-m15-date="esp_data">
// nunca passava pelo attachAll de novo, então nunca ganhava o calendário.
// Corrigido chamando attachDates(blocoEsp) logo após a reatribuição de
// innerHTML; window.SoproM15DatePicker.attachAll já é idempotente (marca
// data-m15-date-attached), então repetir a chamada é seguro e só afeta o
// input recém-injetado.
//
// Este teste prova DUAS coisas sem precisar de navegador real:
//   A) estruturalmente, que "Data do exame" nunca foi duplicado nem
//      divergiu entre SoproLife/Pastore (mesma função, mesmo commonStart),
//      e que a chamada corretiva attachDates(blocoEsp) existe logo após a
//      reatribuição de innerHTML;
//   B) funcionalmente, com o DOM falso REAL do m15-datepicker.js (mesmo
//      usado no M15.4A): reproduz o cenário exato do bug — um input novo,
//      idêntico ao "esp_data" da Pastore, injetado numa árvore que já foi
//      escaneada uma vez — e confirma que SEM chamar attachAll de novo ele
//      fica sem calendário, e QUE chamar de novo (o fix) o anexa,
//      preservando o que já existia (idempotência).
//
// Uso: node painel-soprolife/scripts/test-m23-1-pastore-datepicker.js
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
const centralSrc = fs.readFileSync(path.join(RAIZ, "js", "central-cadastros.js"), "utf8");
const dp = require(path.join(RAIZ, "js", "m15-datepicker.js"));

// ── A) estrutura: campo único compartilhado + chamada corretiva ────────────
console.log("A) \"Data do exame\" é um único campo compartilhado, com re-anexação corrigida");

/* M25.26 — a lista de parâmetros deixou de ser fixa no padrão.
 *
 * A função ganhou `cfg` (catálogo de modalidade/local/valor vindo do
 * servidor) e a regex, que exigia literalmente `(pastore, ehPastore)`,
 * deixou de casar — derrubando de uma vez os seis casos abaixo, sem que a
 * propriedade protegida pela M23.1 tivesse mudado.
 *
 * O que estes casos protegem é o CORPO da função: um único "Data do exame"
 * compartilhado pelas duas variantes e a re-anexação do calendário após a
 * troca. Nada disso depende de quantos argumentos a função recebe. */
const funcMatch = centralSrc.match(
  /function blocoEspirometriaConteudoHtml\([^)]*\) \{([\s\S]*?)\n  \}\n\n  function blocoEspirometriaHtml/
);
caso("blocoEspirometriaConteudoHtml() foi localizada", !!funcMatch);
const funcBody = funcMatch ? funcMatch[1] : "";

const commonStartMatch = funcBody.match(/const commonStart = `([\s\S]*?)`;/);
caso("commonStart existe e contém \"Data do exame\"",
     !!commonStartMatch && /Data do exame/.test(commonStartMatch[1]) &&
     /dateInp\("esp_data", "", \{ parcial: true \}/.test(commonStartMatch[1]));

const ocorrenciasCommonStart = (funcBody.match(/return commonStart \+/g) || []).length;
caso("AMBOS os retornos (SoproLife e Pastore) partem do mesmo commonStart " +
     "(campo nunca foi duplicado/divergente entre as duas variantes)",
     ocorrenciasCommonStart === 2);
caso("nenhuma segunda definição de \"Data do exame\" existe fora do commonStart",
     (funcBody.match(/Data do exame/g) || []).length === 1);

const aplicarModoMatch = centralSrc.match(
  /function aplicarModo\(\) \{([\s\S]*?)\n      \}\n\n      form\.querySelectorAll/
);
caso("aplicarModo() foi localizada", !!aplicarModoMatch);
const aplicarModoBody = aplicarModoMatch ? aplicarModoMatch[1] : "";
caso("blocoEsp.innerHTML é reatribuído ao trocar entre SoproLife/Pastore",
     /blocoEsp\.innerHTML = blocoEspirometriaConteudoHtml\([^)]*\);/
       .test(aplicarModoBody));
caso("attachDates(blocoEsp) é chamado IMEDIATAMENTE depois da reatribuição " +
     "(correção M23.1 — sem isto o campo novo fica sem calendário)",
     /blocoEsp\.innerHTML = blocoEspirometriaConteudoHtml\([^)]*\);\s*\n\s*renderedPastore = ehPastore;\s*\n[\s\S]{0,400}?attachDates\(blocoEsp\);/
       .test(aplicarModoBody));

// ── B) funcional: DOM falso real do m15-datepicker.js ──────────────────────
console.log();
console.log("B) Reprodução funcional do bug + prova da correção (DOM falso real)");

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

function FakeDoc() {
  this.activeElement = null;
  this._listeners = {};
}
FakeDoc.prototype.createElement = function (tag) { return new FakeEl(this, tag); };
FakeDoc.prototype.addEventListener = FakeEl.prototype.addEventListener;
FakeDoc.prototype.removeEventListener = FakeEl.prototype.removeEventListener;

// Reproduz o campo exatamente como dateInp("esp_data", "", { parcial: true })
// o gera: data-m15-date="partial", sem type="date" (precisão parcial).
function campoDataDoExame(doc, value) {
  const input = doc.createElement("input");
  input.setAttribute("name", "esp_data");
  input.setAttribute("data-m15-date", "partial");
  input.value = value || "";
  return input;
}

dp._setToday(() => ({ y: 2026, m: 7, d: 19 }));

{
  const doc = new FakeDoc();
  const blocoEsp = doc.createElement("div"); // equivalente a #cadAtBlocoEsp

  // 1) Render inicial (tipo = espirometria_soprolife): attachDates roda uma vez.
  const espLifeInput = campoDataDoExame(doc, "2026-07-10");
  blocoEsp.appendChild(espLifeInput);
  let pickers = dp.attachAll(blocoEsp, { document: doc });
  caso("render inicial (SoproLife): o calendário é anexado",
       pickers.length === 1 && espLifeInput.getAttribute("data-m15-date-attached") === "1");

  // 2) Operador troca o tipo para "Espirometria Pastore": aplicarModo()
  //    substitui TODO o conteúdo de blocoEsp (innerHTML = novo HTML) — o
  //    input antigo desaparece e um NOVO, sem o atributo *-attached, ocupa
  //    o lugar. Simulado aqui removendo os filhos e inserindo um novo nó,
  //    exatamente como innerHTML faz no DOM real.
  blocoEsp.children = [];
  const espPastoreInput = campoDataDoExame(doc, "2026-07-12");
  blocoEsp.appendChild(espPastoreInput);

  caso("logo após a troca para Pastore, o campo novo AINDA não tem calendário " +
       "(reproduz o bug antes da correção)",
       espPastoreInput.getAttribute("data-m15-date-attached") !== "1");

  // 3) A correção: aplicarModo() agora chama attachDates(blocoEsp) de novo.
  pickers = dp.attachAll(blocoEsp, { document: doc });
  caso("chamar attachDates(blocoEsp) de novo (a correção) anexa o calendário " +
       "no campo da Pastore",
       pickers.length === 1 &&
       espPastoreInput.getAttribute("data-m15-date-attached") === "1");
  caso("o picker da Pastore é funcionalmente idêntico ao da SoproLife " +
       "(mesmo módulo, mesmo contrato — não é um ícone decorativo à parte)",
       pickers[0].holder.getAttribute("name") === "esp_data" &&
       pickers[0].display.value === "12/07/2026");

  // 4) Terceira chamada (ex.: reabrir a mesma aba) não duplica nada — a
  //    idempotência do attachAll é o que torna seguro chamar attachDates()
  //    de novo a cada troca de tipo, sem precisar rastrear o que já foi feito.
  pickers = dp.attachAll(blocoEsp, { document: doc });
  caso("chamadas repetidas continuam idempotentes (0 novos anexos)",
       pickers.length === 0);
}

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
