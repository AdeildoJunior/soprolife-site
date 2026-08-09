#!/usr/bin/env node
// M25.14 — a bancada clínica não pode congelar depois de um erro.
//
// Defeito comprovado em produção na M25.13: os handlers tinham
//
//     } catch (e) { announce(...); render(); }
//     finally    { state.busy = false; }
//
// O `render()` acontecia ANTES do `finally`, ou seja, a tela era pintada ainda
// com `busy = true` — todos os botões nasciam desabilitados — e nada repintava
// depois. A médica ficava sem saída até apertar F5.
//
// Contrato travado aqui: todo bloco `finally` que zera `state.busy` precisa
// repintar depois de zerar. E o padrão antigo não pode voltar.
"use strict";

const fs = require("fs");
const path = require("path");

const PANEL = path.resolve(__dirname, "..");
const workflow = fs.readFileSync(
  path.join(PANEL, "js", "report-workflow.js"), "utf8"
);

let failures = 0;
function check(rotulo, ok, detalhe) {
  if (!ok) failures += 1;
  console.log(`${ok ? "ok  " : "FALHA"} ${rotulo}`);
  if (!ok && detalhe) console.log(`      ${detalhe}`);
}

// Percorre os blocos `finally { ... }` e devolve o corpo de cada um.
function blocosFinally(fonte) {
  const blocos = [];
  const re = /\}\s*finally\s*\{/g;
  let m;
  while ((m = re.exec(fonte)) !== null) {
    let i = re.lastIndex;
    let profundidade = 1;
    const inicio = i;
    while (i < fonte.length && profundidade > 0) {
      const c = fonte[i];
      if (c === "{") profundidade += 1;
      else if (c === "}") profundidade -= 1;
      i += 1;
    }
    const corpo = fonte.slice(inicio, i - 1);
    const linha = fonte.slice(0, m.index).split("\n").length;
    blocos.push({ linha, corpo });
  }
  return blocos;
}

const blocos = blocosFinally(workflow);
check("há blocos finally para inspecionar", blocos.length > 0);

const zeramBusy = blocos.filter((b) => /\bbusy\s*=\s*false/.test(b.corpo));
check(
  "existem blocos que zeram um flag de ocupado",
  zeramBusy.length > 0,
  `encontrados: ${zeramBusy.length}`
);

const semRepintura = zeramBusy.filter((b) => !/\brender\(\)/.test(b.corpo));
check(
  "todo finally que zera o flag de ocupado repinta a tela depois",
  semRepintura.length === 0,
  semRepintura.length
    ? `blocos sem render(): linhas ${semRepintura.map((b) => b.linha).join(", ")}`
    : ""
);

// A ordem importa: zerar depois de repintar deixaria a tela pintada com o
// estado antigo. `busy = false` precisa vir ANTES do `render()`.
const ordemInvertida = zeramBusy.filter((b) => {
  const iBusy = b.corpo.search(/\bbusy\s*=\s*false/);
  const iRender = b.corpo.search(/\brender\(\)/);
  return iRender !== -1 && iRender < iBusy;
});
check(
  "o flag é zerado antes da repintura, nunca depois",
  ordemInvertida.length === 0,
  ordemInvertida.length
    ? `ordem invertida nas linhas ${ordemInvertida.map((b) => b.linha).join(", ")}`
    : ""
);

// O padrão exato que causou o incidente não pode reaparecer.
check(
  "o padrão antigo (render no catch + finally que só zera) não voltou",
  !/render\(\);\s*\}\s*finally\s*\{\s*state\.busy\s*=\s*false;\s*\}/.test(workflow)
);

// A mensagem de erro precisa continuar visível depois da repintura: quem
// mostra o texto é `announce`, e ele não pode ter sido removido dos catches.
check(
  "os erros continuam sendo anunciados na tela",
  (workflow.match(/announce\(readableError\(error\)/g) || []).length >= 10
);

// ---------------------------------------------------------------- UX M25.14
// A ação do fluxo antigo não pode mais pedir uma prévia que já existe.
check(
  "o passo de assinatura qualificada só habilita com rascunho composto",
  /hasComposedDraft/.test(workflow)
    && /current\.kind === "rascunho"/.test(workflow)
);
check(
  "a tela explica que aquele passo pertence ao caminho da anotação na MIR",
  /anotação técnica sobre o PDF da MIR/.test(workflow)
);
check(
  "a tela aponta o botão correto para liberar o laudo próprio",
  /Assinar e liberar laudo/.test(workflow)
);

// O que a M25.13 comprovou funcionando não pode ter sido derrubado.
[
  ["bancada lado a lado", /report-clinical-split/],
  ["catálogo de conclusões", /data-report-conclusion/],
  ["complementos pós-BD", /data-report-bd/],
  ["confirmação consciente da liberação", /data-report-release-confirm/],
  ["download separado dos documentos", /data-report-download/],
  ["localizador de exame", /reportLocateExamForm/],
].forEach(([rotulo, re]) => {
  check(`preservado: ${rotulo}`, re.test(workflow));
});
check(
  "o aviso do piloto interno continua literal",
  workflow.includes(
    "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE"
  )
);

console.log(
  failures === 0
    ? "\nM25.14: todos os contratos passaram."
    : `\nM25.14: ${failures} falha(s).`
);
process.exit(failures === 0 ? 0 : 1);
