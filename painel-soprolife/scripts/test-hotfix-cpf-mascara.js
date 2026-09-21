#!/usr/bin/env node
// SoproLife — hotfix: máscara automática de CPF na Central de Cadastros.
//
// Provas offline e determinísticas, do lado do navegador:
//   A) formatação pura, progressiva e limitada a 11 dígitos;
//   B) digitação real (evento `input`) com cursor preservado;
//   C) colagem com e sem pontuação;
//   D) apagar no meio e no fim sem o cursor pular;
//   E) erro só no blur/submit, nunca durante a digitação;
//   F) o que sai para a API são os 11 dígitos, não a máscara;
//   G) o campo continua com teclado numérico e placeholder no celular.
//
// Não há jsdom no repositório; o mínimo de DOM necessário é reproduzido
// aqui. É o bastante porque a máscara só usa value/selectionStart/eventos —
// e um stub explícito falha alto se o código passar a depender de mais.
//
// Uso:  node painel-soprolife/scripts/test-hotfix-cpf-mascara.js
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

// ── carga: extrai o bloco de CPF e o avalia com um DOM mínimo ──────────────
const ini = central.indexOf("const CPF_DIGITOS = 11;");
const fim = central.indexOf("function toast(msg, kind) {");
if (ini < 0 || fim < 0 || fim <= ini) {
  console.log("FAIL: bloco da máscara de CPF não localizado em central-cadastros.js");
  process.exit(1);
}
const bloco = central.slice(ini, fim);

function fakeEl(tag) {
  const el = {
    tagName: tag,
    hidden: false,
    id: "",
    className: "",
    textContent: "",
    value: "",
    selectionStart: 0,
    children: [],
    attrs: {},
    classes: new Set(),
    listeners: {},
    focado: false,
  };
  el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
  el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
  el.removeAttribute = (k) => { delete el.attrs[k]; };
  el.appendChild = (c) => { el.children.push(c); c.pai = el; return c; };
  el.querySelector = (sel) => {
    if (sel === "[data-cpf-erro]") return el.children.find((c) => "data-cpf-erro" in c.attrs) || null;
    return null;
  };
  el.closest = (sel) => {
    let n = el;
    while (n) {
      if (sel === ".m15-field" && n.classes.has("m15-field")) return n;
      n = n.pai;
    }
    return null;
  };
  el.classList = {
    add: (c) => el.classes.add(c),
    remove: (c) => el.classes.delete(c),
    contains: (c) => el.classes.has(c),
    toggle: (c, on) => (on ? el.classes.add(c) : el.classes.delete(c)),
  };
  el.addEventListener = (tipo, fn) => {
    (el.listeners[tipo] = el.listeners[tipo] || []).push(fn);
  };
  el.dispatch = (tipo) => (el.listeners[tipo] || []).forEach((fn) => fn({ type: tipo }));
  el.setSelectionRange = (a) => { el._sel = a; };
  // Fidelidade ao navegador: reescrever `value` joga o cursor para o fim.
  // Sem isso o stub perdoaria justamente o bug que a máscara precisa evitar.
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
  el.focus = () => { el.focado = true; };
  return el;
}

const documentStub = { createElement: (t) => fakeEl(t) };
const api = new Function("document", bloco +
  "\nreturn { CPF_ERRO_INCOMPLETO, cpfDigitos, cpfFormatado, cpfPosDoDigito," +
  " cpfIncompleto, cpfMostrarErro, cpfValidarCampo, cpfMask };")(documentStub);

// Campo CPF montado como na tela: <label.m15-field><input name=..._cpf>
function montarCampo(valorInicial) {
  const campo = fakeEl("label");
  campo.classes.add("m15-field");
  const input = fakeEl("input");
  input.attrs.name = "cadAtP_cpf";
  input.value = valorInicial || "";
  input.selectionStart = input.value.length;
  campo.appendChild(input);
  api.cpfMask(input);
  return { campo, input };
}

// Digita caractere a caractere, como o navegador faz: insere no cursor e
// dispara `input`. É isto que prova a máscara em TEMPO REAL, e não só a
// função de formatar.
function digitar(input, texto) {
  for (const ch of texto) {
    const p = input.selectionStart;
    input.value = input.value.slice(0, p) + ch + input.value.slice(p);
    input.selectionStart = p + 1;
    input.dispatch("input");
  }
}

// Backspace: remove o caractere ANTES do cursor e dispara `input`.
function backspace(input, vezes = 1) {
  for (let i = 0; i < vezes; i++) {
    const p = input.selectionStart;
    if (p <= 0) return;
    input.value = input.value.slice(0, p - 1) + input.value.slice(p);
    input.selectionStart = p - 1;
    input.dispatch("input");
  }
}

// Colar: substitui a seleção (aqui, campo vazio) e dispara `input`.
function colar(input, texto) {
  input.value = texto;
  input.selectionStart = texto.length;
  input.dispatch("input");
}

const erroDe = (campo) => campo.children.find((c) => "data-cpf-erro" in c.attrs) || null;
const erroVisivel = (campo) => { const b = erroDe(campo); return !!b && !b.hidden; };

// ── A) formatação pura ─────────────────────────────────────────────────────
console.log();
console.log("A) Formatação progressiva, sem separador solto à direita");

caso("09744590773 vira 097.445.907-73",
     api.cpfFormatado("09744590773") === "097.445.907-73");
caso("o ponto entra só quando há dígito depois dele",
     api.cpfFormatado("097") === "097" && api.cpfFormatado("0974") === "097.4");
caso("progressão completa sem saltos",
     api.cpfFormatado("0") === "0" &&
     api.cpfFormatado("097445") === "097.445" &&
     api.cpfFormatado("0974459") === "097.445.9" &&
     api.cpfFormatado("097445907") === "097.445.907" &&
     api.cpfFormatado("0974459077") === "097.445.907-7");
caso("letras e símbolos são descartados, não exibidos",
     api.cpfFormatado("09a7b4/4c5") === "097.445");
caso("nada além de 11 dígitos entra",
     api.cpfDigitos("09744590773999999") === "09744590773" &&
     api.cpfFormatado("09744590773999999") === "097.445.907-73");
caso("campo vazio continua vazio (CPF é opcional no núcleo)",
     api.cpfFormatado("") === "" && api.cpfDigitos(null) === "");

// ── B) digitação real ──────────────────────────────────────────────────────
console.log();
console.log("B) Digitação progressiva com evento `input` de verdade");

{
  const { input } = montarCampo();
  const vistos = [];
  for (const ch of "09744590773") {
    digitar(input, ch);
    vistos.push(input.value);
  }
  caso("digitar 09744590773 exibe 097.445.907-73",
       input.value === "097.445.907-73", input.value);
  caso("a máscara aparece durante a digitação, não só no fim",
       vistos[2] === "097" && vistos[3] === "097.4" &&
       vistos[5] === "097.445" && vistos[6] === "097.445.9" &&
       vistos[8] === "097.445.907" && vistos[9] === "097.445.907-7",
       vistos.join("|"));
  caso("o cursor termina no fim do texto",
       input.selectionStart === input.value.length, String(input.selectionStart));
}

{
  const { input } = montarCampo();
  digitar(input, "abc09xy744");
  caso("teclas não numéricas não movem nem sujam o campo",
       input.value === "097.44", input.value);
}

{
  const { input } = montarCampo();
  digitar(input, "0974459077399");
  caso("digitar além de 11 dígitos simplesmente não acrescenta",
       input.value === "097.445.907-73", input.value);
}

// ── C) colagem ─────────────────────────────────────────────────────────────
console.log();
console.log("C) Colar com e sem pontuação");

{
  const { input } = montarCampo();
  colar(input, "09744590773");
  caso("colar 09744590773 → 097.445.907-73", input.value === "097.445.907-73", input.value);
}
{
  const { input } = montarCampo();
  colar(input, "097.445.907-73");
  caso("colar 097.445.907-73 → 097.445.907-73 (idempotente)",
       input.value === "097.445.907-73", input.value);
}
{
  const { input } = montarCampo();
  colar(input, "  097 445 907 73 ");
  caso("colar com espaços também normaliza", input.value === "097.445.907-73", input.value);
}
{
  const { input } = montarCampo();
  colar(input, "09744590773000");
  caso("colar mais de 11 dígitos corta no 11º",
       input.value === "097.445.907-73", input.value);
}

// ── D) apagar ──────────────────────────────────────────────────────────────
console.log();
console.log("D) Apagar sem cursor irritante");

{
  const { input } = montarCampo();
  digitar(input, "09744590773");
  backspace(input, 1);
  caso("backspace no fim tira UM dígito e nada mais",
       input.value === "097.445.907-7" && input.selectionStart === 13,
       `${input.value} @${input.selectionStart}`);
  backspace(input, 1);
  caso("o backspace seguinte leva o dígito E o traço que ficou órfão",
       input.value === "097.445.907" && input.selectionStart === 11,
       `${input.value} @${input.selectionStart}`);
}

{
  const { input } = montarCampo();
  digitar(input, "09744590773");
  backspace(input, 14);
  caso("apagar tudo esvazia o campo (não sobra pontuação)",
       input.value === "" && input.selectionStart === 0,
       `"${input.value}" @${input.selectionStart}`);
}

{
  // Cursor no meio: apagar o "4" logo após "097." deve manter o cursor ali,
  // e não jogá-lo para o fim do campo.
  const { input } = montarCampo();
  digitar(input, "09744590773");        // 097.445.907-73
  input.selectionStart = 5;             // logo depois do primeiro "4"
  backspace(input, 1);                  // remove esse "4"
  /* O cursor para logo APÓS o 3º dígito (posição 3), e não depois do ponto
     (posição 4): as duas parecem iguais na tela, mas só a primeira faz o
     próximo backspace apagar o "7". Parado depois do ponto, o backspace
     removeria o separador que a máscara recoloca na hora — o cursor
     "travado" que esta tarefa existe para evitar. */
  caso("apagar no MEIO mantém o cursor no lugar, sem pular para o fim",
       input.value === "097.459.077-3" && input.selectionStart === 3,
       `${input.value} @${input.selectionStart}`);
  caso("apagar no meio não perde nenhum outro dígito",
       api.cpfDigitos(input.value) === "0974590773", api.cpfDigitos(input.value));
}

{
  // Digitar no MEIO de um CPF já preenchido: o cursor acompanha o dígito.
  const { input } = montarCampo();
  digitar(input, "0974459077");         // 097.445.907-7
  input.selectionStart = 3;             // antes do primeiro ponto
  digitar(input, "1");                  // insere 1 como 4º dígito
  caso("inserir dígito no meio reposiciona o cursor logo após ele",
       input.value === "097.144.590-77" && input.selectionStart === 5,
       `${input.value} @${input.selectionStart}`);
}

// ── E) erro no blur/submit, nunca digitando ────────────────────────────────
console.log();
console.log("E) \"CPF deve ter 11 dígitos.\" só no blur/submit");

caso("a frase é exatamente a pedida",
     api.CPF_ERRO_INCOMPLETO === "CPF deve ter 11 dígitos.", api.CPF_ERRO_INCOMPLETO);

{
  const { campo, input } = montarCampo();
  digitar(input, "097445");
  caso("nenhum erro aparece enquanto a pessoa digita", !erroVisivel(campo));
  input.dispatch("blur");
  caso("no blur, incompleto mostra a mensagem",
       erroVisivel(campo) && erroDe(campo).textContent === "CPF deve ter 11 dígitos.",
       erroDe(campo) ? erroDe(campo).textContent : "(sem caixa)");
  caso("o campo fica marcado como inválido",
       campo.classes.has("cad-campo-invalido") && input.attrs["aria-invalid"] === "true");
  caso("a mensagem é anunciada por leitor de tela",
       erroDe(campo).attrs.role === "alert" &&
       input.attrs["aria-describedby"] === erroDe(campo).id);
  digitar(input, "9");
  caso("voltar a digitar apaga o erro imediatamente",
       !erroVisivel(campo) && !campo.classes.has("cad-campo-invalido"));
  digitar(input, "0773");
  input.dispatch("blur");
  caso("CPF completo no blur não acusa nada",
       !erroVisivel(campo) && input.value === "097.445.907-73", input.value);
}

{
  const { campo, input } = montarCampo();
  input.dispatch("blur");
  caso("campo VAZIO no blur não acusa erro (CPF é opcional)",
       !erroVisivel(campo) && !campo.classes.has("cad-campo-invalido"));
  caso("vazio não conta como incompleto", api.cpfIncompleto(input) === false);
}

{
  const { input } = montarCampo();
  digitar(input, "0974459");
  caso("cpfValidarCampo (usada no submit) reprova incompleto",
       api.cpfValidarCampo(input) === false);
  digitar(input, "0773");
  caso("cpfValidarCampo aprova 11 dígitos", api.cpfValidarCampo(input) === true);
}

// ── F) o que vai para a API ────────────────────────────────────────────────
console.log();
console.log("F) Payload: 11 dígitos, nunca a máscara");

caso("o payload passa o valor por cpfDigitos antes de enviar",
     /setIf\(payload, "cpf", cpfDigitos\(leia\("cpf"\)\)\)/.test(central));
caso("o envio é bloqueado quando o CPF está incompleto",
     /if \(cpfEl && !cpfValidarCampo\(cpfEl\)\) \{[\s\S]{0,120}?throw new Error\(CPF_ERRO_INCOMPLETO\)/.test(central));
caso("o que a tela mostra e o que a API recebe divergem só na pontuação",
     api.cpfDigitos("097.445.907-73") === "09744590773" &&
     api.cpfFormatado("09744590773") === "097.445.907-73");
caso("11 dígitos é o comprimento que o núcleo aceita (people.cpf String(11))",
     api.cpfDigitos("097.445.907-73").length === 11);

/* ATENÇÃO ao testar à mão: 09744590773 NÃO é um CPF válido — os dois dígitos
 * verificadores não fecham (o 1º deveria ser 0, veio 7). Por isso ele serve
 * de exemplo aqui sem violar a regra de não usar dado real de paciente.
 *
 * A consequência prática: a máscara o exibe como 097.445.907-73 e o envio
 * sai com os 11 dígitos, mas `POST` devolve 422 `cpf_invalido` vindo de
 * `services/cpf.py`. Isso é o comportamento CORRETO e pré-existente — a
 * máscara cuida do formato, o núcleo continua dono da validade. Para ver o
 * cadastro concluir, use um CPF fictício com verificadores coerentes. */
caso("o exemplo do hotfix é fictício: reprova nos dígitos verificadores",
     (function () {
       const d = api.cpfDigitos("09744590773");
       const dv = (base, peso) => {
         let s = 0;
         for (let i = 0; i < base.length; i++) s += Number(base[i]) * (peso - i);
         const r = (s * 10) % 11;
         return r === 10 ? 0 : r;
       };
       return dv(d.slice(0, 9), 10) !== Number(d[9]);
     })());
caso("o CPF continua fora de query string",
     !/cpf=\$\{/.test(central) && !/\?cpf=/.test(central));
caso("a máscara é ligada no campo de CPF do paciente novo",
     /const cpf = root\.querySelector\(`\[name="\$\{prefix\}_cpf"\]`\);\s*\n\s*if \(cpf\) cpfMask\(cpf\);/.test(central));
caso("o erro é limpo quando o formulário é reiniciado",
     /picker\.resetState[\s\S]{0,900}?cpfMostrarErro\(cpfEl, false\)/.test(central));

// ── G) mobile e apresentação ───────────────────────────────────────────────
console.log();
console.log("G) Teclado numérico, placeholder e estilo do erro");

caso("o campo de CPF pede teclado numérico no celular",
     /inp\(prefix \+ "_cpf", "", 'inputmode="numeric"/.test(central));
caso("o placeholder é 000.000.000-00",
     /prefix \+ "_cpf"[\s\S]{0,80}?placeholder="000\.000\.000-00"/.test(central));
caso("a mensagem de erro tem estilo próprio, distinto do âmbar de pendência",
     /\.m15-field-help\.cad-campo-erro \{[\s\S]{0,120}?color: #b23b37/.test(centralCss) &&
     /\.m15-field\.cad-campo-invalido \{/.test(centralCss));
caso("o estilo do erro não introduz largura fixa (quebraria o mobile)",
     !/\.m15-field\.cad-campo-invalido \{[\s\S]{0,200}?width: \d+px/.test(centralCss));
caso("o CPF ocupa 4 colunas do grid de 12 — cabe empilhado no celular",
     /fld\("CPF", inp\(prefix \+ "_cpf"[\s\S]{0,400}?span: 4/.test(central));

// ── H) nada fora de escopo ─────────────────────────────────────────────────
console.log();
console.log("H) Escopo: nada de edição de CPF, schema ou busca nova");

caso("o formulário de correção de cadastro continua SEM campo de CPF",
     !/_editCpf|editCpf/.test(central));
caso("o PATCH de cadastro continua sem enviar cpf",
     !/patch = \{[\s\S]{0,300}?cpf/.test(central));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos da máscara de CPF passaram.");
