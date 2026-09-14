#!/usr/bin/env node
// SoproLife — M32: município de atendimento estruturado exposto na intake
// normal de espirometria (criação e edição).
//
// Provas do lado do navegador, offline e determinísticas (mesmo padrão
// estático usado por test-m25-26-fluxo-espirometria.js — sem headless
// browser, sem framework novo):
//   A) formulário de "Novo atendimento" (central-cadastros.js) oferece um
//      SELECT estruturado, rotulado em português, sem default para Rio;
//   B) a lista de municípios vem do backend — nunca uma segunda lista
//      hardcoded de Rio/Niterói no frontend;
//   C) o valor enviado ao servidor é o código IBGE, nunca o nome da cidade;
//   D) local_atendimento (texto livre) nunca alimenta o município sozinho;
//   E) o formulário de edição (m15-nucleo.js) oferece o mesmo SELECT,
//      preserva o valor gravado e permite limpar.
//
// Uso:  node painel-soprolife/scripts/test-m32-municipio-atendimento.js
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

const semComentarios = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");
const centralCodigo = semComentarios(central);
const nucleoCodigo = semComentarios(nucleo);

// ── A) formulário de criação (Novo atendimento) ────────────────────────────
console.log();
console.log("A) Central de cadastros oferece o seletor estruturado");

caso('rótulo em português "Município onde o exame foi realizado" existe',
     /Município onde o exame foi realizado/.test(central));
caso("o campo é um SELECT (esp_municipio), não uma caixa de texto livre",
     /sel\("esp_municipio",/.test(centralCodigo));
caso('a opção em branco significa "não informado", não Rio',
     /\[\["", "não informado"\]\]\.concat\(\(municipios \|\| \[\]\)\.map/.test(centralCodigo));
caso('o SELECT nasce sem valor selecionado (sem default para Rio)',
     /sel\("esp_municipio",\s*\n\s*\[\["", "não informado"\]\]\.concat\([^)]*\)\), ""\)/.test(centralCodigo) ||
     /esp_municipio[\s\S]{0,220}\), ""\),/.test(centralCodigo));

// ── B) fonte única — nunca uma segunda lista hardcoded ──────────────────────
console.log();
console.log("B) Município vem do backend — sem lista própria no frontend");

caso("existe um resolver que busca a lista no backend",
     /function resolveMunicipiosAtendimento\(\)[\s\S]{0,160}api\("\/espirometrias\/municipios-atendimento"\)/
       .test(centralCodigo));
caso("o resolver entra no Promise.all do fluxo de Novo atendimento",
     /Promise\.all\(\[resolvePastore\(\), resolveConfigAtendimento\(\), resolveMunicipiosAtendimento\(\)\]\)/
       .test(centralCodigo));
caso('nenhum array hardcoded ["3304557"/"3303302"] de município existe no frontend',
     !/3304557/.test(central) && !/3303302/.test(central));
caso("a edição (m15-nucleo.js) busca a mesma rota, não uma lista própria",
     /api\("\/espirometrias\/municipios-atendimento"\)/.test(nucleoCodigo));

// ── C) valor enviado é o código IBGE, nunca o nome da cidade ────────────────
console.log();
console.log("C) O valor submetido é o código IBGE (codigo), não o rótulo");

caso("as opções do SELECT usam [m.codigo, m.rotulo] — valor = código, rótulo = nome",
     /\(municipios \|\| \[\]\)\.map\(\(m\) => \[m\.codigo, m\.rotulo\]\)/.test(centralCodigo));
caso("montarEspirometria envia o valor do campo (o código) sem tradução para nome de cidade",
     /setIf\(bloco, "municipio_atendimento_ibge", val\(form, "esp_municipio"\)\)/.test(centralCodigo));
caso("o mapeamento de erro do servidor conhece o campo (mensagens caem no controle certo)",
     /"espirometria\.municipio_atendimento_ibge": "esp_municipio"/.test(centralCodigo));

// ── D) local_atendimento nunca alimenta o município sozinho ─────────────────
console.log();
console.log("D) Texto livre (local_atendimento) nunca vira município");

caso("esp_municipio nunca é escrito a partir de esp_local (nenhuma atribuição cruzada)",
     !/esp_local["'\)][\s\S]{0,80}esp_municipio\s*=/.test(centralCodigo) &&
     !/esp_municipio\s*=\s*[\s\S]{0,40}esp_local/.test(centralCodigo));
caso("montarEspirometria lê esp_municipio do próprio campo, não de esp_local",
     /val\(form, "esp_municipio"\)/.test(centralCodigo) &&
     !/val\(form, "esp_local"\)[\s\S]{0,120}municipio_atendimento_ibge/.test(centralCodigo));

// ── E) edição preserva/limpa corretamente ───────────────────────────────────
console.log();
console.log("E) Edição (m15-nucleo.js): mesmo seletor, sem default, limpável");

caso('rótulo em português também na tela de edição',
     /Município onde o exame foi realizado/.test(nucleo));
caso("o SELECT de edição nasce com o valor GRAVADO (nunca Rio por padrão)",
     /sel\("municipio_atendimento_ibge",[\s\S]{0,220}e\.municipio_atendimento_ibge \|\| ""\)/
       .test(nucleoCodigo));
caso("só envia o campo quando o operador realmente muda o valor (preserva se intocado)",
     /municipioNovo !== \(e\.municipio_atendimento_ibge \|\| ""\)/.test(nucleoCodigo));
caso('opção em branco explícita ("não informado") permite limpar o campo',
     /\[\["", "não informado"\]\]\.concat\(municipios\.map/.test(nucleoCodigo));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos M32 passaram.");
