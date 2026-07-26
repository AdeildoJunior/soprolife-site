#!/usr/bin/env node
// SoproLife M23.1 — banner informativo do formulário financeiro manual +
// resolução de conflito de receita duplicada no frontend.
//
// A proteção de duplicidade real é do BACKEND (finance.py,
// _bloquear_receita_duplicada — provado em tests/test_finance.py). Este
// teste cobre a metade do frontend, sem navegador real:
//   A) o banner informativo existe com o texto exato pedido, explicando que
//      atendimentos SoproLife geram lançamento automaticamente e que exames
//      Pastore entram no fechamento mensal;
//   B) quando o backend recusa (409, codigo=receita_ja_existe), a UI NÃO
//      mostra o JSON cru — converte em um banner com botão para atualizar o
//      lançamento existente (PATCH), usando os dados que o operador já
//      tinha preenchido, e silencia o toast de erro genérico duplicado.
//
// Uso: node painel-soprolife/scripts/test-m23-1-financeiro-duplicidade.js
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
const cssSrc = fs.readFileSync(path.join(RAIZ, "css", "central.css"), "utf8");

console.log("A) Banner informativo do formulário manual");

caso("banner aparece dentro de LOADERS.financeiro, antes do formulário",
     /LOADERS\.financeiro = function[\s\S]{0,600}?cad-financeiro-aviso[\s\S]{0,500}?<form/
       .test(centralSrc));
caso("texto explica que atendimentos SoproLife geram lançamento automaticamente",
     /Atendimentos SoproLife geram lançamentos financeiros\s+automaticamente\./
       .test(centralSrc.replace(/\s+/g, " ").replace(/\s+/g, " ")) ||
     /Atendimentos SoproLife geram lançamentos financeiros/.test(centralSrc));
caso("texto orienta o uso correto do formulário manual",
     /Use este formulário para despesas, repasses, ajustes e receitas\s+avulsas\./
       .test(centralSrc) ||
     /despesas, repasses, ajustes e receitas/.test(centralSrc));
caso("texto declara Pastore excluído (fechamento mensal)",
     /Exames Pastore entram no fechamento mensal\./.test(centralSrc));
caso("banner tem estilo visual definido (não é texto solto sem destaque)",
     /\.cad-financeiro-aviso/.test(cssSrc));

console.log();
console.log("B) Conflito de receita duplicada vira ação, não erro cru");

caso("submit do financeiro interpreta o código receita_ja_existe do backend",
     /detalhe && detalhe\.codigo === "receita_ja_existe"/.test(centralSrc));
caso("em conflito, chama mostrarConflitoReceita em vez de deixar o erro genérico estourar",
     /mostrarConflitoReceita\(bodyEl, detalhe, \{/.test(centralSrc));
caso("os dados já preenchidos pelo operador (status/recebimento/forma) são reaproveitados",
     /mostrarConflitoReceita\(bodyEl, detalhe, \{\s*\n\s*status: payload\.status,\s*\n\s*data_recebimento: payload\.data_recebimento,\s*\n\s*forma_pagamento: payload\.forma_pagamento,/
       .test(centralSrc));
caso("o erro do conflito é marcado silencioso (não duplica toast genérico)",
     /silencioso\.silencioso = true;/.test(centralSrc) &&
     /if \(err && err\.silencioso\) return;/.test(centralSrc));
caso("função mostrarConflitoReceita existe e usa PATCH no lançamento existente",
     /function mostrarConflitoReceita\(/.test(centralSrc) &&
     /\/lancamentos\/\$\{encodeURIComponent\(detalhe\.lancamento_existente_id\)\}/
       .test(centralSrc) &&
     /method: "PATCH"/.test(centralSrc));
caso("botão de ação nomeia o código do lançamento existente (não é genérico)",
     /Atualizar \$\{esc\(detalhe\.lancamento_existente\)\} com estes dados/.test(centralSrc));
caso("após atualizar, a lista de lançamentos recentes é atualizada",
     /atualizado\.public_code[\s\S]{0,200}?loadFinRecents\(container\);/.test(centralSrc));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
