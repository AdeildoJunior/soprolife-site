#!/usr/bin/env node
// M25.27 — a bancada médica não pode ficar carregando para sempre.
//
// O placeholder "Carregando o fluxo seguro de laudos…" mora no HTML e só sai
// quando `render()` escreve por cima. Enquanto `boot()` tinha saídas mudas
// (`return` sem pintar nada) e `bindClient()` tinha um `setTimeout` sem teto,
// QUALQUER falha de inicialização virava uma tela parada para sempre — sem
// erro, sem console, sem ação. Foi assim que um 403 no manifesto de boot
// chegou à médica como "o sistema não faz nada".
//
// Contrato travado aqui:
//   1. toda saída de `boot()` pinta um estado — sucesso, falha ou desabilitado;
//   2. a espera pelo núcleo M15 é limitada e termina em erro visível;
//   3. existe "Tentar novamente", e ele não duplica os ouvintes clínicos;
//   4. o núcleo não monta superfície administrativa para sessão só clínica.
"use strict";

const fs = require("fs");
const path = require("path");

const PANEL = path.resolve(__dirname, "..");
const workflow = fs.readFileSync(
  path.join(PANEL, "js", "report-workflow.js"), "utf8"
);
const nucleo = fs.readFileSync(
  path.join(PANEL, "js", "m15-nucleo.js"), "utf8"
);
const html = fs.readFileSync(path.join(PANEL, "index.html"), "utf8");

let failures = 0;
function check(rotulo, ok, detalhe) {
  if (!ok) failures += 1;
  console.log(`${ok ? "ok   " : "FALHA"} ${rotulo}`);
  if (!ok && detalhe) console.log(`      ${detalhe}`);
}

// Extrai o corpo de uma função pelo cabeçalho, contando chaves.
function corpoDe(fonte, cabecalho) {
  const inicio = fonte.indexOf(cabecalho);
  if (inicio === -1) return null;
  let i = fonte.indexOf("{", inicio);
  if (i === -1) return null;
  let profundidade = 0;
  const abre = i;
  while (i < fonte.length) {
    const c = fonte[i];
    if (c === "{") profundidade += 1;
    else if (c === "}") {
      profundidade -= 1;
      if (profundidade === 0) return fonte.slice(abre + 1, i);
    }
    i += 1;
  }
  return null;
}

console.log("── O placeholder existe e é o estado inicial ──");
check(
  "index.html ainda traz o placeholder de carregamento",
  /Carregando o fluxo seguro de laudos/.test(html)
);

console.log("── boot(): nenhuma saída muda ──");
const boot = corpoDe(workflow, "async function boot()");
check("boot() foi localizada", boot !== null);
if (boot) {
  // Cada `return;` de saída precisa ser precedido por uma pintura de estado.
  // Só a guarda `if (!mount) return;` pode sair sem pintar: sem ponto de
  // montagem não existe tela para pintar.
  const linhas = boot.split("\n");
  const saidasMudas = [];
  linhas.forEach((linha, idx) => {
    if (!/^\s*return;\s*$/.test(linha)) return;
    const janela = linhas.slice(Math.max(0, idx - 6), idx).join("\n");
    const pinta = /renderBootFailure\(|renderBootDisabled\(|render\(\)/.test(janela);
    const guardaMount = /if \(!mount\) return;/.test(linhas[idx]) ||
      /!mount/.test(linhas[idx - 1] || "");
    if (!pinta && !guardaMount) saidasMudas.push(idx + 1);
  });
  check(
    "toda saída de boot() pinta um estado antes de retornar",
    saidasMudas.length === 0,
    saidasMudas.length ? `linhas mudas (relativas a boot): ${saidasMudas.join(", ")}` : ""
  );

  check(
    "falha ao LER o manifesto vira estado de falha",
    /renderBootFailure\(/.test(boot)
  );
  check(
    "feature desligada vira mensagem própria, não 'Carregando'",
    /renderBootDisabled\(/.test(boot)
  );
  check(
    "o manifesto ilegível não é mais confundido com feature desligada",
    !/config = \{\};/.test(boot),
    "o antigo `catch { config = {} }` apagava a diferença entre 403 e desabilitado"
  );
}

console.log("── A espera pelo núcleo M15 é limitada ──");
const bindClient = corpoDe(workflow, "function bindClient()");
check("bindClient() foi localizada", bindClient !== null);
if (bindClient) {
  check(
    "o laço de 200 ms tem teto",
    /CLIENT_MAX_WAITS/.test(bindClient),
    "sem teto, `window.SoproM15` ausente = carregando para sempre"
  );
  check(
    "estourar o teto pinta falha",
    /renderBootFailure\(/.test(bindClient)
  );
}
check(
  "CLIENT_MAX_WAITS é uma constante declarada",
  /const CLIENT_MAX_WAITS = \d+;/.test(workflow)
);
// Guarda contra reintrodução do laço sem teto.
check(
  "não sobrou setTimeout(bindClient, …) fora do caminho com teto",
  (workflow.match(/setTimeout\(bindClient/g) || []).length === 1
);

console.log("── 'Tentar novamente' existe e é seguro ──");
check(
  "o estado de falha oferece a ação",
  /data-report-retry/.test(workflow) && /Tentar novamente/.test(workflow)
);
check(
  "a mensagem ao usuário é a acordada",
  /Não foi possível carregar os laudos\./.test(workflow)
);
check(
  "o ouvinte de retry é ligado uma única vez",
  /retryWired/.test(workflow)
);
check(
  "reentrar em boot() não duplica os ouvintes clínicos",
  /listenersWired/.test(workflow),
  "sem isto, cada clique da médica valeria dois depois de um retry"
);

console.log("── Nada de PII, valor ou detalhe técnico na tela de erro ──");
const falha = corpoDe(workflow, "function renderBootFailure(");
if (falha) {
  check(
    "o motivo técnico vai para o console, não para o DOM",
    /console\.error/.test(falha) && !/\$\{motivoTecnico\}/.test(falha)
  );
}

console.log("── O núcleo não devolve superfície administrativa à médica ──");
const activate = corpoDe(nucleo, "function activate(config)");
check("activate() foi localizada", activate !== null);
if (activate) {
  check(
    "activate() consulta o gate de boot antes de montar",
    /somenteClinico/.test(activate)
  );
  check(
    "a seção e o menu administrativos saíram do caminho comum",
    !/nav\.appendChild\(navButton\(\)\)/.test(activate)
      && !/container\.appendChild\(buildSection\(\)\)/.test(activate),
    "montar aqui recolocaria 'Núcleo administrativo' no DOM já podado pela M25.23"
  );
  check(
    "o 'Sair' continua sendo de toda sessão autenticada",
    /wireSairDoTopo\(\)/.test(activate)
  );
}
const bootNucleo = corpoDe(nucleo, "function boot()");
if (bootNucleo) {
  check(
    "o núcleo espera o papel real antes de decidir o que montar",
    /SoproBootGate/.test(bootNucleo),
    "sem esperar, a corrida entre os dois fetches decidiria por sorteio"
  );
}

console.log("── Os selos de cache acompanharam os arquivos alterados ──");
["m15-nucleo.js", "report-workflow.js"].forEach((arquivo) => {
  const m = html.match(new RegExp(`${arquivo.replace(".", "\\.")}\\?v=(\\d{10})`));
  check(
    `${arquivo} tem selo >= 2026081301`,
    Boolean(m) && Number(m[1]) >= 2026081301,
    m ? `selo atual: ${m[1]}` : "selo não encontrado"
  );
});

console.log();
if (failures) {
  console.log(`RESULTADO: ${failures} regressão(ões) detectada(s).`);
  process.exit(1);
}
console.log("RESULTADO: boot da área médica é resiliente e isolado por papel.");
process.exit(0);
