#!/usr/bin/env node
// SoproLife — hotfix: DDI explícito no telefone do paciente.
//
// A regressão que este arquivo existe para travar PARA SEMPRE: o telefone
// canadense +1 647 238-6290 era exibido e salvo como "(16) 47238-6290", um
// celular brasileiro. A máscara antiga descartava o "+" e lia os dois
// primeiros dígitos como DDD.
//
// Provas offline e determinísticas, do lado do navegador:
//   A) separação de DDI (ITU-T E.164) sem adivinhar comprimento;
//   B) composição canônica E.164;
//   C) a REGRESSÃO REAL do Canadá, nas duas vias (DDI+número e colagem);
//   D) Brasil como default, sem o operador digitar DDI;
//   E) telefones brasileiros legados e com +55 explícito;
//   F) "+" no meio do número não é aceito como DDI;
//   G) abrir a edição de cadastro antigo NÃO reescreve o valor;
//   H) troca de DDI desfaz a máscara brasileira;
//   I) wa.me internacional e brasileiro (regra do núcleo, espelhada no CRM);
//   J) telefone ausente;
//   K) desktop e mobile estreito (o par DDI+número não quebra a coluna).
//
// Não há jsdom no repositório; o mínimo de DOM necessário é reproduzido
// aqui, e um stub explícito falha alto se o código passar a depender de mais.
//
// Uso:  node painel-soprolife/scripts/test-hotfix-telefone-internacional.js
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
const ler = (...p) => fs.readFileSync(path.join(RAIZ, ...p), "utf8");

const central = ler("js", "central-cadastros.js");
const centralCss = ler("css", "central.css");
const appJs = ler("js", "app.js");

// ── carga: extrai o bloco do telefone e o avalia com um DOM mínimo ─────────
const ini = central.indexOf('const DDI_PADRAO = "+55";');
const fim = central.indexOf("/* Máscara de CPF durante a digitação");
if (ini < 0 || fim < 0 || fim <= ini) {
  console.log("FAIL: bloco do telefone com DDI não localizado em central-cadastros.js");
  process.exit(1);
}
const bloco = central.slice(ini, fim);

function fakeEl(tag) {
  const el = {
    tagName: tag,
    hidden: false,
    id: "",
    label: "",
    className: "",
    textContent: "",
    type: "text",
    children: [],
    attrs: {},
    classes: new Set(),
    listeners: {},
    pai: null,
  };
  el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
  el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
  el.removeAttribute = (k) => { delete el.attrs[k]; };
  el.appendChild = (c) => {
    if (c.pai) c.pai.children = c.pai.children.filter((x) => x !== c);
    el.children.push(c); c.pai = el; return c;
  };
  el.insertBefore = (novo, ref) => {
    const i = el.children.indexOf(ref);
    if (novo.pai) novo.pai.children = novo.pai.children.filter((x) => x !== novo);
    el.children.splice(i < 0 ? el.children.length : i, 0, novo);
    novo.pai = el; return novo;
  };
  Object.defineProperty(el, "parentNode", { get: () => el.pai });
  el.querySelector = (sel) => {
    const bate = (n) =>
      (sel.startsWith(".") && n.classes.has(sel.slice(1))) ||
      (sel.startsWith(".") && n.className === sel.slice(1));
    const anda = (n) => {
      for (const c of n.children) {
        if (bate(c)) return c;
        const d = anda(c);
        if (d) return d;
      }
      return null;
    };
    return anda(el);
  };
  el.closest = (sel) => {
    let n = el;
    while (n) {
      if (sel.startsWith(".") && (n.classes.has(sel.slice(1)) || n.className === sel.slice(1))) return n;
      n = n.pai;
    }
    return null;
  };
  el.classList = {
    add: (c) => el.classes.add(c),
    remove: (c) => el.classes.delete(c),
    contains: (c) => el.classes.has(c),
  };
  el.addEventListener = (tipo, fn) => {
    (el.listeners[tipo] = el.listeners[tipo] || []).push(fn);
  };
  el.dispatch = (tipo) => (el.listeners[tipo] || []).forEach((fn) => fn({ type: tipo }));
  // Fidelidade ao navegador: reescrever `value` joga o cursor para o fim.
  let _v = "";
  Object.defineProperty(el, "value", {
    get: () => _v,
    set: (nv) => { _v = String(nv); el._sel = _v.length; },
  });
  Object.defineProperty(el, "selectionStart", {
    get: () => el._sel,
    set: (n) => { el._sel = n; },
  });
  el._sel = 0;
  return el;
}

const body = fakeEl("body");
const documentStub = {
  body,
  createElement: (t) => fakeEl(t),
  getElementById: (id) => {
    const anda = (n) => {
      for (const c of n.children) {
        if (c.id === id) return c;
        const d = anda(c);
        if (d) return d;
      }
      return null;
    };
    return anda(body);
  },
};

const api = new Function("document", "setTimeout", bloco +
  "\nreturn { DDI_PADRAO, DDI_SUGESTOES, ddiTamanho, foneDigitos, ddiFormatado," +
  " foneBrFormatado, foneIntlFormatado, foneFormatado, foneSepara, foneCompoe," +
  " phoneField };")(documentStub, setTimeout);

/* Campo montado como na tela:
 *   <label.m15-field><span.m15-field-label>WhatsApp</span><input name=..._fone></label>
 * `phoneField` reescreve o interior: [DDI][número][hidden com o name]. */
function montarCampo(valorInicial, rotulo) {
  const campo = fakeEl("label");
  campo.classes.add("m15-field");
  const lab = fakeEl("span");
  lab.classes.add("m15-field-label");
  lab.textContent = rotulo || "WhatsApp";
  campo.appendChild(lab);
  const input = fakeEl("input");
  input.attrs.name = "cadAtP_fone";
  input.attrs.placeholder = "(21) 99999-9999";
  input.type = "tel";
  input.value = valorInicial || "";
  campo.appendChild(input);
  api.phoneField(input);
  const ddi = campo.querySelector(".cad-fone-ddi");
  const num = campo.querySelector(".cad-fone-num");
  return { campo, input, ddi, num };
}

// Digita caractere a caractere, como o navegador faz.
function digitar(el, texto) {
  for (const ch of texto) {
    const p = el.selectionStart;
    el.value = el.value.slice(0, p) + ch + el.value.slice(p);
    el.selectionStart = p + 1;
    el.dispatch("input");
  }
}

// Colar: substitui todo o conteúdo e dispara `input`.
function colar(el, texto) {
  el.value = texto;
  el.selectionStart = texto.length;
  el.dispatch("input");
}

// ── A) separação de DDI ────────────────────────────────────────────────────
console.log();
console.log("A) Separação de DDI (ITU-T E.164), sem adivinhar comprimento");

caso("+1 tem um dígito: +16472386290 → +1 / 6472386290",
     JSON.stringify(api.foneSepara("+16472386290")) ===
     JSON.stringify({ ddi: "+1", numero: "6472386290" }),
     JSON.stringify(api.foneSepara("+16472386290")));
caso("+55 tem dois dígitos: +5521999999999 → +55 / 21999999999",
     JSON.stringify(api.foneSepara("+5521999999999")) ===
     JSON.stringify({ ddi: "+55", numero: "21999999999" }));
caso("+351 tem três dígitos: +351912345678 → +351 / 912345678",
     JSON.stringify(api.foneSepara("+351912345678")) ===
     JSON.stringify({ ddi: "+351", numero: "912345678" }));
caso("+7 tem um dígito (zona 7)",
     api.foneSepara("+79161234567").ddi === "+7");
caso("DDI desconhecido de três dígitos não é truncado para dois",
     api.foneSepara("+2991234567").ddi === "+299");
caso("separadores no meio não mudam o DDI",
     api.foneSepara("+1 647 238-6290").ddi === "+1" &&
     api.foneSepara("+55 (21) 99999-9999").ddi === "+55");
caso("o 00 do discado internacional também é DDI explícito",
     JSON.stringify(api.foneSepara("0016472386290")) ===
     JSON.stringify({ ddi: "+1", numero: "6472386290" }));
caso("SEM + não se inventa DDI — legado brasileiro fica intacto",
     JSON.stringify(api.foneSepara("21999999999")) ===
     JSON.stringify({ ddi: "", numero: "21999999999" }) &&
     JSON.stringify(api.foneSepara("(21) 99999-9999")) ===
     JSON.stringify({ ddi: "", numero: "(21) 99999-9999" }));
caso("ausência de telefone não vira DDI nem número",
     JSON.stringify(api.foneSepara("")) === JSON.stringify({ ddi: "", numero: "" }) &&
     JSON.stringify(api.foneSepara(null)) === JSON.stringify({ ddi: "", numero: "" }));
caso("a tabela de comprimento cobre 1, 2 e 3 dígitos",
     api.ddiTamanho("1") === 1 && api.ddiTamanho("55") === 2 &&
     api.ddiTamanho("351") === 3 && api.ddiTamanho("") === 0);

// ── B) composição canônica ────────────────────────────────────────────────
console.log();
console.log("B) Composição canônica compatível com E.164");

caso("+1 + 647 238-6290 → +16472386290",
     api.foneCompoe("+1", "647 238-6290") === "+16472386290",
     api.foneCompoe("+1", "647 238-6290"));
caso("+55 + (21) 99999-9999 → +5521999999999",
     api.foneCompoe("+55", "(21) 99999-9999") === "+5521999999999");
caso("sem DDI devolve só os dígitos (formato legado, nada inventado)",
     api.foneCompoe("", "(21) 99999-9999") === "21999999999");
caso("número vazio não gera DDI órfão",
     api.foneCompoe("+55", "") === "" && api.foneCompoe("+1", "   ") === "");

// ── C) A REGRESSÃO REAL — Canadá ──────────────────────────────────────────
console.log();
console.log("C) REGRESSÃO REAL: +1 647 238-6290 nunca vira (16) 47238-6290");

{
  // Via 1 — o operador troca o DDI e digita o número.
  const { input, ddi, num } = montarCampo();
  colar(ddi, "+1");
  digitar(num, "6472386290");
  caso("DDI +1 + digitação: valor canônico é +16472386290",
       input.value === "+16472386290", input.value);
  caso("DDI +1 + digitação: a tela NÃO mostra máscara brasileira",
       num.value === "6472386290" && !/^\(/.test(num.value), num.value);
  caso("DDI +1 + digitação: o valor NUNCA é (16) 47238-6290",
       input.value !== "(16) 47238-6290" && num.value !== "(16) 47238-6290");
  caso("DDI +1 + digitação: o valor NUNCA ganha 55 na frente",
       input.value.indexOf("+55") !== 0 && input.value !== "+5516472386290");
}

{
  // Via 2 — o operador COLA o telefone completo no campo do número.
  const { input, ddi, num } = montarCampo();
  colar(num, "+1 647 238-6290");
  caso("colar \"+1 647 238-6290\": o DDI do campo passa a ser +1",
       ddi.value === "+1", ddi.value);
  caso("colar \"+1 647 238-6290\": valor canônico é +16472386290",
       input.value === "+16472386290", input.value);
  caso("colar \"+1 647 238-6290\": a tela não vira (16) 47238-6290",
       num.value !== "(16) 47238-6290", num.value);
}

{
  const { input, ddi, num } = montarCampo();
  colar(num, "+16472386290");
  caso("colar \"+16472386290\" (sem separador) também reconfigura o DDI",
       ddi.value === "+1" && input.value === "+16472386290",
       ddi.value + " / " + input.value);
  caso("colar \"+16472386290\": o número exibido não recebe parênteses de DDD",
       num.value === "6472386290", num.value);
}

{
  // Recarga/reedição: o valor salvo volta como DDI +1 + número.
  const { input, ddi, num } = montarCampo("+16472386290");
  caso("reload/reedição: DDI permanece +1",
       ddi.value === "+1", ddi.value);
  caso("reload/reedição: o número volta sem máscara brasileira",
       num.value === "6472386290", num.value);
  caso("reload/reedição: abrir a tela não altera o valor",
       input.value === "+16472386290", input.value);
  // E editar de novo e salvar mantém o canônico.
  colar(num, "6472386290");
  caso("editar de novo e confirmar mantém +16472386290",
       input.value === "+16472386290", input.value);
}

// ── D) Brasil como default ────────────────────────────────────────────────
console.log();
console.log("D) Brasil é o default — o operador brasileiro não digita DDI");

{
  const { input, ddi, num } = montarCampo();
  caso("novo cadastro inicia com +55 preenchido",
       ddi.value === "+55" && api.DDI_PADRAO === "+55", ddi.value);
  caso("novo cadastro inicia com telefone vazio",
       input.value === "" && num.value === "");
  digitar(num, "21999999999");
  caso("digitar 21999999999 sem tocar no DDI exibe (21) 99999-9999",
       num.value === "(21) 99999-9999", num.value);
  caso("digitar 21999999999 sem tocar no DDI salva +5521999999999",
       input.value === "+5521999999999", input.value);
}

{
  const { input, num } = montarCampo();
  digitar(num, "2199999999");
  caso("fixo de 10 dígitos continua funcionando: (21) 9999-9999",
       num.value === "(21) 9999-9999" && input.value === "+552199999999",
       num.value + " / " + input.value);
}

// ── E) telefones brasileiros (todas as formas de hoje) ────────────────────
console.log();
console.log("E) Telefones brasileiros existentes continuam funcionando");

const BR_ACEITOS = [
  "(21) 99999-9999",
  "21 99999-9999",
  "21999999999",
  "+55 21 99999-9999",
  "+5521999999999",
];
for (const valor of BR_ACEITOS) {
  const { ddi, num } = montarCampo(valor);
  // A EXIBIÇÃO é sempre normalizada para a máscara brasileira; o que não
  // muda é o valor ENVIADO quando o operador não toca no campo (bloco G).
  caso(`"${valor}" abre com DDI +55 e número reconhecido`,
       ddi.value === "+55" && num.value === "(21) 99999-9999",
       ddi.value + " / " + num.value);
}

{
  const { input, ddi, num } = montarCampo("+5521999999999");
  caso("brasileiro já internacional: edição recompõe o MESMO valor",
       (colar(num, "(21) 99999-9999"), input.value === "+5521999999999"),
       input.value);
  caso("brasileiro já internacional: DDI continua +55",
       ddi.value === "+55");
}

// ── F) o caractere "+" ────────────────────────────────────────────────────
console.log();
console.log('F) O "+" pertence ao DDI — no meio do número não é DDI válido');

{
  const { input, ddi, num } = montarCampo();
  colar(ddi, "+1");
  colar(num, "647+2386290");
  caso('"+" no MEIO do número é descartado, não lido como DDI',
       num.value === "6472386290" && ddi.value === "+1",
       num.value + " / " + ddi.value);
  caso('"+" no meio não produz valor com dois "+"',
       (input.value.match(/\+/g) || []).length === 1, input.value);
}

{
  const { ddi } = montarCampo();
  colar(ddi, "1+5");
  caso('o campo de DDI aceita "+" só como prefixo: "1+5" → "+15"',
       ddi.value === "+15", ddi.value);
}

{
  const { input, num } = montarCampo();
  colar(num, "21+99999999");
  caso('"+" no meio com DDI +55: máscara brasileira dos dígitos restantes',
       num.value === "(21) 9999-9999" && input.value === "+552199999999",
       num.value + " / " + input.value);
}

// ── G) abrir a edição não reescreve o banco ───────────────────────────────
console.log();
console.log("G) Abrir a edição de um cadastro legado NÃO reescreve o valor");

for (const legado of ["21999999999", "(21) 99999-9999", "2199999999"]) {
  const { input, ddi } = montarCampo(legado);
  caso(`"${legado}": a tela assume +55 para exibir…`, ddi.value === "+55");
  caso(`"${legado}": …mas o valor enviado é o do banco, byte a byte`,
       input.value === legado, input.value);
}

{
  // Só a confirmação do operador muda o valor. Mexer no DDI já é confirmar.
  const { input } = montarCampo("21999999999");
  caso("sem toque no telefone, salvar outro campo não migra o telefone",
       input.value === "21999999999", input.value);
}

{
  const { input, num } = montarCampo("21999999999");
  digitar(num, "9");
  caso("ao EDITAR de fato, o valor passa a ser canônico",
       input.value.startsWith("+55"), input.value);
}

// ── H) troca de DDI desfaz a máscara brasileira ───────────────────────────
console.log();
console.log("H) Trocar +55 → +1 desfaz a máscara brasileira já na tela");

{
  const { input, ddi, num } = montarCampo();
  digitar(num, "6472386290");
  caso("com +55 o número aparece mascarado (estado intermediário)",
       num.value === "(64) 7238-6290", num.value);
  colar(ddi, "+1");
  caso("trocar para +1 remove parênteses e hífen do número",
       num.value === "6472386290", num.value);
  caso("trocar para +1 corrige o valor canônico para +16472386290",
       input.value === "+16472386290", input.value);
}

{
  // Reclassificar o MESMO número como brasileiro é decisão do operador, e a
  // tela obedece: os dez dígitos voltam a ser lidos como DDD + número.
  const { input, ddi, num } = montarCampo("+16472386290");
  colar(ddi, "+55");
  caso("voltar para +55 reaplica a máscara brasileira",
       num.value === "(64) 7238-6290" && input.value === "+556472386290",
       num.value + " / " + input.value);
}

// ── I) wa.me ──────────────────────────────────────────────────────────────
console.log();
console.log("I) wa.me — DDI + número, sem \"+\" e sem separador");

// A regra canônica é do núcleo (`normalize_phone`); aqui prova-se o espelho
// do painel (CRM Clínicas), que montava a URL por conta própria.
const waIni = appJs.indexOf("function resolveLeadWhatsApp(item) {");
const waFim = appJs.indexOf("\n}", waIni) + 2;
const resolveLeadWhatsApp = new Function(
  appJs.slice(waIni, waFim) + "\nreturn resolveLeadWhatsApp;")();

caso("internacional: +16472386290 → https://wa.me/16472386290",
     resolveLeadWhatsApp({ telefone_whatsapp: "+16472386290" }) ===
     "https://wa.me/16472386290",
     resolveLeadWhatsApp({ telefone_whatsapp: "+16472386290" }));
caso("internacional com separadores: +1 647 238-6290 → wa.me/16472386290",
     resolveLeadWhatsApp({ telefone_whatsapp: "+1 647 238-6290" }) ===
     "https://wa.me/16472386290");
caso("internacional NUNCA recebe 55 na frente",
     resolveLeadWhatsApp({ telefone_whatsapp: "+16472386290" })
       .indexOf("wa.me/55") === -1);
caso("brasileiro internacional: +5521999999999 → wa.me/5521999999999",
     resolveLeadWhatsApp({ telefone_whatsapp: "+5521999999999" }) ===
     "https://wa.me/5521999999999");
caso("brasileiro legado: (21) 99999-9999 → wa.me/5521999999999",
     resolveLeadWhatsApp({ telefone_whatsapp: "(21) 99999-9999" }) ===
     "https://wa.me/5521999999999");
caso("brasileiro legado só dígitos: 21999999999 → wa.me/5521999999999",
     resolveLeadWhatsApp({ telefone_whatsapp: "21999999999" }) ===
     "https://wa.me/5521999999999");
caso("telefone ausente não gera URL",
     resolveLeadWhatsApp({}) === "" &&
     resolveLeadWhatsApp({ telefone_whatsapp: "" }) === "" &&
     resolveLeadWhatsApp({ telefone_whatsapp: "sem número" }) === "");
caso("url pronta do servidor continua tendo precedência",
     resolveLeadWhatsApp({ whatsapp_url: "https://wa.me/1", telefone_whatsapp: "x" }) ===
     "https://wa.me/1");

// ── J) telefone ausente ───────────────────────────────────────────────────
console.log();
console.log("J) Telefone ausente");

{
  const { input, ddi, num } = montarCampo();
  caso("campo vazio envia string vazia (não \"+55\")",
       input.value === "", JSON.stringify(input.value));
  colar(ddi, "+1");
  caso("mexer só no DDI, sem número, continua enviando vazio",
       input.value === "", JSON.stringify(input.value));
  caso("o campo do número segue vazio e sem máscara", num.value === "");
}

// ── K) desktop, mobile e consistência de montagem ─────────────────────────
console.log();
console.log("K) Montagem, acessibilidade e largura (desktop e mobile estreito)");

{
  const { campo, input, ddi, num } = montarCampo();
  caso("o input com `name` continua existindo e vira portador do valor",
       input.getAttribute("name") === "cadAtP_fone" && input.type === "hidden",
       input.type);
  caso("o par DDI+número vive num grupo .cad-fone dentro do campo",
       !!campo.querySelector(".cad-fone"));
  caso("os dois controles têm aria-label próprio (o label do campo é um só)",
       /DDI/.test(ddi.getAttribute("aria-label") || "") &&
       num.getAttribute("aria-label") === "WhatsApp",
       (ddi.getAttribute("aria-label") || "") + " | " + num.getAttribute("aria-label"));
  caso("o DDI oferece sugestões por datalist, sem dependência externa",
       ddi.getAttribute("list") === "cadDdiSugestoes" &&
       !!documentStub.getElementById("cadDdiSugestoes"));
  caso("o placeholder brasileiro migra para o campo do número",
       num.getAttribute("placeholder") === "(21) 99999-9999" &&
       input.getAttribute("placeholder") === null);
  caso('o número mantém type="tel" (teclado com "+" no celular)',
       num.type === "tel" && ddi.getAttribute("inputmode") === "tel");
  caso("chamar phoneField duas vezes não duplica controle",
       (api.phoneField(input), campo.querySelector(".cad-fone").children.length === 3),
       String(campo.querySelector(".cad-fone").children.length));
}

caso("+1 é rotulado como NANP, não como um país só (§3)",
     api.DDI_SUGESTOES.some(([c, l]) => c === "+1" && /NANP/.test(l)));
caso("Brasil é a primeira sugestão da lista",
     api.DDI_SUGESTOES[0][0] === "+55");

caso("o grupo é flex em linha, com min-width 0 (não estoura a coluna 3/12)",
     /\.cad-fone\s*\{[^}]*display:\s*flex/.test(centralCss) &&
     /\.cad-fone\s*\{[^}]*min-width:\s*0/.test(centralCss));
caso("o DDI tem largura fixa e o número ocupa o resto",
     /\.cad-fone-ddi\s*\{[^}]*flex:\s*0 0/.test(centralCss) &&
     /\.cad-fone-num\s*\{[^}]*flex:\s*1 1 auto/.test(centralCss));
caso("no mobile estreito nada força o par a empilhar",
     !/@media[^{]*\{[^}]*\.cad-fone\s*\{[^}]*flex-direction:\s*column/.test(centralCss));

caso("a máscara brasileira antiga (sem DDI) não existe mais no arquivo",
     central.indexOf("function phoneMask") === -1);
// Os QUATRO campos de telefone da Central — paciente novo, responsável
// legal, correção de cadastro e contato B2B — todos pelo mesmo enhancer.
// Um campo novo com `type="tel"` que esqueça o phoneField reabre o bug.
const CAMPOS_FONE = ["fixFone", "fone", "gfone", "form.elements.telefone"];
caso("todo campo de telefone da tela passa pelo phoneField",
     CAMPOS_FONE.every((n) => central.indexOf(`phoneField(${n})`) > -1) &&
     (central.match(/phoneField\(/g) || []).length === CAMPOS_FONE.length + 1,
     String((central.match(/phoneField\(/g) || []).length));
caso("nenhum input de telefone ficou sem enhancer",
     (central.match(/inp\([^)]*'type="tel"/g) || []).length === CAMPOS_FONE.length,
     String((central.match(/inp\([^)]*'type="tel"/g) || []).length));

console.log();
if (falhas) {
  console.log(`FALHOU — ${falhas} caso(s).`);
  process.exit(1);
}
console.log("OK — todos os casos passaram.");
