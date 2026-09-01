#!/usr/bin/env node
// SoproLife — M25.26: o fluxo de Espirometria SoproLife se explica sozinho.
//
// Provas do lado do navegador, offline e determinísticas:
//   A) máscara de data com barra automática, SEM destruir data parcial;
//   B) paciente primeiro — campos visíveis sem clique intermediário;
//   C) "cadastrar pessoa sem atendimento" como ação secundária;
//   D) modalidade × local dependentes, sem duas listas concorrentes;
//   E) valor de tabela vindo do servidor e editável;
//   F) botão Sair visível, ligado ao logout existente;
//   G) ajuda contextual acessível (toque/teclado), no padrão M25.24;
//   H) responsividade em 430 (mobile) e 1920 (desktop).
//
// Uso:  node painel-soprolife/scripts/test-m25-26-fluxo-espirometria.js
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
const nucleo = ler("js", "m15-nucleo.js");
const indexHtml = ler("index.html");
const centralCss = ler("css", "central.css");
const styleCss = ler("css", "style.css");

// Só o CÓDIGO: comentários narram o defeito antigo e derrubariam checagens
// que procuram a ausência do que foi removido.
const semComentarios = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");
const centralCodigo = semComentarios(central);

// ── A) máscara de data ─────────────────────────────────────────────────────
console.log();
console.log("A) Barra automática na data, preservando precisão parcial");

const dp = require(path.join(RAIZ, "js", "m15-datepicker.js"));
const m = (v, modo, apagando) => dp.mascararData(v, modo, !!apagando);

caso("digitar 12082026 num campo de data completa vira 12/08/2026",
     m("12082026", "full") === "12/08/2026");
caso("colar 12082026 num campo parcial vira 12/08/2026",
     m("12082026", "partial") === "12/08/2026");
/* M26.4 — em campo COMPLETO as DUAS barras entram sozinhas: cada bloco fecha
   assim que enche (12 → "12/", 1208 → "12/08/"), de modo que digitar apenas
   números produz DD/MM/AAAA sem o operador tocar em "/". */
caso("a barra aparece progressivamente enquanto digita",
     m("1", "full") === "1" && m("12", "full") === "12/" &&
     m("12/0", "full") === "12/0" && m("1208", "full") === "12/08/");
caso("a SEGUNDA barra também entra sozinha (12122012 → 12/12/2012)",
     m("12/122012", "full") === "12/12/2012" &&
     m("12/12/2012", "full") === "12/12/2012");
caso("digitar só números fecha a data completa",
     "12122012".split("").reduce((acc, d) => m(acc + d, "full"), "") === "12/12/2012" &&
     "01012000".split("").reduce((acc, d) => m(acc + d, "full"), "") === "01/01/2000");
caso("barra digitada à mão não duplica nem quebra o formato livre",
     m("12//", "full") === "12/" && m("12/12//", "full") === "12/12/" &&
     m("1/2/2012", "full") === "1/2/2012");

/* O ponto delicado da fase F: "2026" é ANO válido no campo de data do exame.
   Uma máscara ingênua o transformaria em "20/26" e o operador ficaria preso —
   ao corrigir, a máscara reescreveria de novo. */
caso("ano solto sobrevive num campo parcial (2026 continua 2026)",
     m("2026", "partial") === "2026");
caso("mês/ano digitado com barra é respeitado (08/2026)",
     m("08/2026", "partial") === "08/2026");
caso("barra digitada pelo humano nunca é reescrita",
     m("1/2026", "partial") === "1/2026" && m("12/08/2026", "full") === "12/08/2026");

caso("apagar não remascara (backspace funciona)",
     m("12/08/202", "full", true) === "12/08/202" &&
     m("12/0", "full", true) === "12/0" &&
     m("12/", "full", true) === "12/");
caso("texto não numérico passa intacto (não é engolido)",
     m("dezembro/2026", "partial") === "dezembro/2026");
caso("mais de 8 dígitos não estoura o formato",
     m("120820261234", "full") === "12/08/2026");

caso("a máscara roda antes da leitura do valor enviado à API",
     /var mascarado = mascararData\(display\.value, picker\.mode, apagando\);[\s\S]{0,600}?syncFromDisplay\(false\)/
       .test(ler("js", "m15-datepicker.js")));
caso("a máscara distingue apagar por inputType (não por heurística)",
     /String\(ev\.inputType\)\.indexOf\("delete"\) === 0/.test(ler("js", "m15-datepicker.js")));
caso("cursor só é jogado para o fim quando já estava no fim",
     /var noFim = display\.selectionStart === display\.value\.length/
       .test(ler("js", "m15-datepicker.js")));
caso("o teclado numérico é pedido no celular",
     /setAttribute\("inputmode", "numeric"\)/.test(ler("js", "m15-datepicker.js")));

// ── B) paciente primeiro ───────────────────────────────────────────────────
console.log();
console.log("B) Passo 1 — paciente primeiro, sem clique artificial");

caso("a caixa de paciente novo NÃO nasce escondida",
     /id="\$\{prefix\}NovaBox">/.test(centralCodigo) &&
     !/id="\$\{prefix\}NovaBox" hidden/.test(centralCodigo));
caso("o botão \"+ Cadastrar nova pessoa\" deixou de existir",
     !/id="\$\{prefix\}Nova"/.test(centralCodigo));
caso("buscar paciente existente vem ANTES do formulário de novo",
     centralCodigo.indexOf('id="${prefix}Q"') <
       centralCodigo.indexOf('id="${prefix}NovaBox"'));
caso("escolher um paciente existente esconde o formulário de novo",
     /function showSelected[\s\S]{0,400}?if \(novaBox\) novaBox\.hidden = true/
       .test(centralCodigo));
caso("\"Trocar paciente\" devolve o formulário de novo",
     /Trocar[\s\S]{0,900}?if \(novaBox\) novaBox\.hidden = false/.test(centralCodigo));
caso("o cartão do paciente mostra contexto suficiente para conferir a pessoa",
     /Nascimento: \$\{esc\(nasc\)\}/.test(central) &&
     /Contato: /.test(central) && /CPF: /.test(central));
caso("CPF e sexo passaram a ser preenchíveis no cadastro",
     /_cpf"/.test(centralCodigo) && /_sexo"/.test(centralCodigo));

// ── C) pessoa sem atendimento ──────────────────────────────────────────────
console.log();
console.log("C) \"Cadastrar pessoa sem atendimento\" é ação secundária");

caso("o checkbox grande no meio do passo 1 foi removido",
     !/cad-check-somente-pessoa/.test(centralCodigo) &&
     !/Cadastrar apenas a pessoa, sem criar exame ou consulta/.test(centralCodigo));
caso("a ação vive FORA do <form> do atendimento",
     centralCodigo.indexOf('id="cadAtSoPessoa"') > centralCodigo.indexOf("</form>"));
caso("a ação explica em uma frase o que NÃO cria",
     /Nenhum exame ou\s+consulta será criado agora/.test(central));
caso("a ação não dispara nenhuma criação de atendimento",
     (() => {
       const ini = centralCodigo.indexOf("btnSoPessoa.addEventListener");
       const bloco = centralCodigo.slice(ini, centralCodigo.indexOf("wireSubmit(form,", ini));
       return !/\/atendimentos/.test(bloco) &&
         !/montarEspirometria|montarConsulta|montarFinanceiro/.test(bloco);
     })());
caso("paciente novo + atendimento usam o endpoint atômico",
     /api\("\/atendimentos\/novo-paciente"/.test(centralCodigo));
caso("paciente existente continua no endpoint de atendimento",
     /Object\.assign\(\{ person_id: existente\.id \}, blocos\)/.test(centralCodigo));

// ── D) modalidade × local ──────────────────────────────────────────────────
console.log();
console.log("D) Modalidade e local pararam de competir");

caso("a lista de categorias concorrente foi removida do campo de local",
     !/datalist\("cadLocais", \["Domiciliar", "Clínica", "Empresa", "Parceiro", "Outro"\]\)/
       .test(centralCodigo));
caso("as modalidades vêm do servidor, não de uma lista fixa na tela",
     /cfgEsp\.modalidades/.test(centralCodigo) &&
     /api\("\/atendimentos\/configuracao"\)/.test(centralCodigo));
caso("o campo de local nasce desabilitado até haver modalidade",
     /disabled placeholder="escolha a modalidade primeiro"/.test(centralCodigo));
caso("escolher a modalidade habilita e rotula o local",
     /function aplicarModalidade[\s\S]{0,900}?localEl\.disabled = false;[\s\S]{0,200}?escolhida\.local_rotulo/
       .test(centralCodigo));
caso("o local sugerido nunca sobrescreve o que o operador digitou",
     /if \(!localEl\.dataset\.tocado\)/.test(centralCodigo));
caso("combinação inválida é barrada ANTES do envio",
     /Escolha a modalidade do atendimento — sem ela, o local sozinho não/
       .test(central) &&
     /Modalidade não reconhecida/.test(central));
caso("modalidade que exige local não passa sem local",
     /escolhida\.local_obrigatorio && !local/.test(centralCodigo));

// ── E) valor de tabela ─────────────────────────────────────────────────────
console.log();
console.log("E) Valor da espirometria nasce preenchido e editável");

caso("o valor inicial vem da configuração do servidor",
     /const valorPadrao = cfgEsp\.valor_padrao/.test(centralCodigo));
caso("o valor é formatado em padrão brasileiro (vírgula decimal)",
     /String\(cfgEsp\.valor_padrao\)\.replace\("\.", ","\)/.test(centralCodigo));
caso("o campo é preenchido com esse valor, e não só sugerido no placeholder",
     /inp\("esp_valor", valorPadrao,/.test(centralCodigo));
caso("o campo continua editável (sem readonly/disabled)",
     !/inp\("esp_valor"[^)]*readonly/.test(centralCodigo) &&
     !/inp\("esp_valor"[^)]*disabled/.test(centralCodigo));
caso("220 não está codificado como número no JavaScript",
     !/\b220\b/.test(centralCodigo.replace(/placeholder="220,00"/g, "")));

// ── F) botão Sair ──────────────────────────────────────────────────────────
console.log();
console.log("F) Sair visível no cabeçalho do Command Center");

caso("o botão existe no cabeçalho, junto de busca/Atualizar/avatar",
     /id="topbarSair"/.test(indexHtml) &&
     indexHtml.indexOf('id="topbarSair"') > indexHtml.indexOf('id="refreshBtn"') &&
     indexHtml.indexOf('id="topbarSair"') < indexHtml.indexOf('class="topbar-avatar"'));
caso("nasce oculto e só aparece com sessão",
     /id="topbarSair"[\s\S]{0,220}?hidden/.test(indexHtml) &&
     /btn\.hidden = !\(state\.sessao \|\| state\.token\)/.test(nucleo));
caso("usa o logout existente — nenhum segundo contrato de autenticação",
     /function sincronizarSairDoTopo/.test(nucleo) &&
     /encerrarSessao\("Sessão encerrada\."\)/.test(nucleo) &&
     (nucleo.match(/api\("\/auth\/logout"/g) || []).length === 1);
caso("a visibilidade acompanha login e logout",
     /function notifySession\(\)[\s\S]{0,320}?sincronizarSairDoTopo\(\)/.test(nucleo) &&
     /restaurarSessao\(\)\.then[\s\S]{0,220}?sincronizarSairDoTopo\(\)/.test(nucleo));
caso("o rótulo textual \"Sair\" existe (não é só um ícone)",
     /topbar-sair-texto">Sair</.test(indexHtml));
caso("a ligação do clique é idempotente (não acumula ouvintes)",
     /data-m15-sair-wired/.test(nucleo));

// ── G) ajuda contextual ────────────────────────────────────────────────────
console.log();
console.log("G) Ajuda contextual no padrão M25.24");

caso("a ajuda é um <button> real, não title=\"\"",
     /class="cad-help-toggle" data-cad-help-toggle/.test(central) &&
     /role="tooltip"/.test(central));
caso("a explicação é anunciada por aria-describedby",
     /aria-describedby="\$\{esc\(id\)\}"/.test(central));
caso("o estado aberto/fechado é exposto por aria-expanded",
     /aria-expanded="false"/.test(central) &&
     /toggle\.setAttribute\("aria-expanded", aberto \? "true" : "false"\)/.test(central));
caso("Escape fecha a ajuda",
     /if \(ev\.key !== "Escape"\) return;/.test(central));
caso("abrir a ajuda não re-renderiza o formulário",
     /function setAjudaOpen[\s\S]{0,400}?bolha\.hidden = !aberto/.test(central));
caso("a ajuda cobre modalidade, local, valor e CPF",
     /ajuda: ajudaModalidade/.test(central) &&
     /ajuda: "Onde especificamente o exame aconteceu/.test(central) &&
     /ajuda: "Nasce com o valor de tabela/.test(central) &&
     /ajuda: "A CFM 2\.381\/2024 pede o CPF/.test(central));
caso("o alvo de toque da ajuda é maior que o círculo desenhado",
     /\.cad-help-toggle::after \{[\s\S]{0,140}?inset: -6px/.test(centralCss));

// ── H) campos faltantes e correção de cadastro ─────────────────────────────
console.log();
console.log("H) Campos faltantes viram destaque e \"Corrigir cadastro\"");

caso("a tela lê campos_faltantes do servidor (não interpreta a frase)",
     /err\.detalhe && err\.detalhe\.campos_faltantes/.test(centralCodigo));
caso("o envelope de erro chega inteiro ao consumidor",
     /err\.detalhe = detail\.detalhe/.test(nucleo));
caso("o campo faltante é destacado e trazido para a vista",
     /classList\.add\("cad-campo-pendente"\)/.test(centralCodigo) &&
     /scrollIntoView/.test(centralCodigo));
caso("o \"como corrigir\" do servidor é mostrado junto do erro",
     /err\.detalhe && err\.detalhe\.como_corrigir/.test(centralCodigo));
caso("\"Corrigir cadastro\" edita no lugar, sem trocar de tela",
     /function abrirCorrecao/.test(central) &&
     /id="\$\{prefix\}CorrigirBox"/.test(central));
caso("a correção só oferece os campos realmente pendentes",
     /const pend = p\.cadastro_pendencias \|\| \[\];[\s\S]{0,200}?pend\.map/.test(central));
caso("após salvar, as pendências são RELIDAS do servidor",
     /\.then\(\(\) => api\("\/pessoas\/" \+ encodeURIComponent\(p\.id\)\)\)/.test(central));
caso("corrigir o cadastro não re-renderiza o formulário do atendimento",
     (() => {
       const ini = central.indexOf("function salvarCorrecao");
       const bloco = central.slice(ini, central.indexOf("wireMiniSearch(q,", ini));
       return /showSelected\(atualizada\)/.test(bloco) &&
         !/render\(\)/.test(bloco) && !/form\.reset\(\)/.test(bloco);
     })());
caso("pendência aparece também no sucesso do atendimento",
     /cad-sucesso-pendencia/.test(central) &&
     /c\.cadastro_pendencias/.test(centralCodigo));

// ── I) responsividade 430 / 1920 ───────────────────────────────────────────
console.log();
console.log("I) Mobile 430 e desktop 1920");

caso("o cabeçalho reflui em tela estreita sem estourar",
     /\.topbar \{[\s\S]{0,120}?flex-wrap: wrap/.test(styleCss));
caso("\"Sair\" encolhe o espaçamento mas MANTÉM a palavra",
     /@media \(max-width: 560px\)[\s\S]{0,260}?\.topbar-sair \{[\s\S]{0,120}?padding: 10px 12px/
       .test(styleCss) &&
     !/\.topbar-sair-texto \{[\s\S]{0,80}?display: none/.test(styleCss));
caso("o balão de ajuda não vaza da viewport no celular",
     /@media \(max-width: 560px\)[\s\S]{0,400}?\.cad-help-bubble \{[\s\S]{0,160}?max-width: min\(280px, 78vw\)/
       .test(centralCss));
caso("o cartão do paciente empilha em telas estreitas",
     /\.cad-cartao-topo \{[\s\S]{0,180}?flex-wrap: wrap/.test(centralCss));
caso("a ação secundária quebra linha em vez de comprimir",
     /\.cad-acao-secundaria \{[\s\S]{0,220}?flex-wrap: wrap/.test(centralCss));
caso("nenhuma largura fixa em pixel foi introduzida nos blocos novos",
     !/\.cad-cartao-pessoa \{[\s\S]{0,220}?width: \d+px/.test(centralCss) &&
     !/\.cad-acao-secundaria \{[\s\S]{0,220}?width: \d+px/.test(centralCss));
caso("o grid de 12 colunas do núcleo continua sendo a base do formulário",
     /m15-form cad-subgrid/.test(central) && /m15-span-/.test(central));

// ── J) segurança preservada ────────────────────────────────────────────────
console.log();
console.log("J) Nada de PII em rota e nada de escrita em planilha");

caso("busca de pessoa continua por POST (nome nunca vai na URL)",
     /api\("\/pessoas\/busca", \{ method: "POST"/.test(centralCodigo));
caso("a Central segue sem falar com o Apps Script",
     !/script\.google\.com/.test(central));
caso("o CPF não é enviado em query string em lugar nenhum",
     !/cpf=\$\{/.test(central) && !/\?cpf=/.test(central));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos M25.26 passaram.");
