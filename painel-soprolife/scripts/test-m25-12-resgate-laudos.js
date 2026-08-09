#!/usr/bin/env node
// M25.12 — regressões estruturais do resgate da interface clínica de laudos.
//
// Duas famílias de defeito são travadas aqui:
//
//   1. a busca por código institucional falhando em SILÊNCIO (o caso
//      ESP-TF0001: `pattern` nativo abortava o submit e nada aparecia);
//   2. a bancada clínica perdendo o lado a lado (exame técnico da MIR à
//      esquerda, laudo e siglas de conclusão à direita).
"use strict";

const fs = require("fs");
const path = require("path");

const PANEL = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(PANEL, ...parts), "utf8");

const workflow = read("js", "report-workflow.js");
const css = read("css", "report-workflow.css");
const index = read("index.html");
const conclusions = read(
  "nucleo-m15", "app", "services", "report_conclusions.py"
);

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS: ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
}

console.log("A) Localização do exame nunca falha em silêncio");
check(
  "o campo de código NÃO usa `pattern` (era ele que abortava o submit)",
  !/id="reportExamCode"[^>]*pattern=/.test(workflow)
    && !/reportExamCode[\s\S]{0,400}?pattern="ESP/.test(workflow),
  "o `pattern` nativo recusa sem chamar a aplicação e sem explicar nada"
);
check(
  "o formulário de localização declara `novalidate`",
  /id="reportLocateExamForm"[^>]*novalidate/.test(workflow),
  "sem isso o navegador volta a barrar o submit antes da aplicação"
);
check(
  "a recusa de formato é produzida pela APLICAÇÃO, com o valor digitado",
  /Formato não reconhecido: \$\{normalized\}/.test(workflow)
);
check(
  "existe um bloco de resultado FIXO na tela (não só toast)",
  /report-locate-feedback/.test(workflow)
    && /\.report-locate-feedback\s*\{/.test(css)
);
check(
  "os quatro desfechos têm mensagem própria",
  /Formato não reconhecido/.test(workflow)
    && /não existe/.test(workflow)
    && /já possui laudo/.test(workflow)
    && /Não foi possível consultar/.test(workflow)
);
check(
  "o erro de API mostra o status HTTP real",
  /HTTP \$\{error\.status\}/.test(workflow)
);
check(
  "a mensagem explica a confusão entre LAU- (laudo) e ESP- (exame)",
  /começando com LAU-, ele\s*\n?\s*identifica o laudo, não o exame/.test(workflow)
    || /identifica o laudo, não o exame/.test(workflow)
);
check(
  "o feedback distingue erro, aviso e sucesso no CSS",
  /\.report-locate-feedback\.is-erro/.test(css)
    && /\.report-locate-feedback\.is-aviso/.test(css)
    && /\.report-locate-feedback\.is-ok/.test(css)
);

console.log("\nB) Não é preciso adivinhar o código");
check(
  "a área operacional carrega espirometrias recentes",
  /client\(\)\.api\("\/espirometrias\?tamanho=50"\)/.test(workflow)
    && /labels\.push\("recentExams"\)/.test(workflow)
);
check(
  "a lista exclui exames que já têm laudo",
  /function examsWithoutReport\(\)/.test(workflow)
    && /comLaudo\.has\(exam\.public_code\)/.test(workflow)
);
check(
  "clicar num exame da lista dispara a localização real",
  /data-report-exam-pick/.test(workflow)
    && /locateExam\(code\)/.test(workflow)
);
check(
  "a lista mostra só código e data — nada de paciente",
  !/report-exam-pick[\s\S]{0,600}?nome_completo/.test(workflow)
    && !/report-exam-pick[\s\S]{0,600}?paciente/i.test(workflow)
);

console.log("\nC) Bancada clínica: MIR à esquerda, laudo e siglas à direita");
check(
  "a bancada existe e envolve as duas colunas",
  /report-clinical-split/.test(workflow)
    && /\.report-clinical-split\s*\{[^}]*grid-template-columns:\s*repeat\(2/.test(css)
);
check(
  "a coluna do exame técnico é `sticky`",
  /\.report-source-pane\s*\{[^}]*position:\s*sticky/.test(css)
);
check(
  "o exame técnico da MIR está na coluna da ESQUERDA",
  /report-source-pane[\s\S]{0,400}?Exame técnico \(MIR\)/.test(workflow)
);
check(
  "a prévia do laudo e o formulário de conclusão estão na coluna da DIREITA",
  /report-work-pane[\s\S]{0,2000}?renderNativeReportForm\(detail\)/.test(workflow)
    && /report-work-pane[\s\S]{0,2000}?renderReleaseAction\(detail\)/.test(workflow)
);
check(
  "as siglas ficam DENTRO da bancada (não abaixo dos dois PDFs)",
  workflow.indexOf("renderNativeReportForm(detail)")
    < workflow.indexOf("${renderDocumentsPanel()}"),
  "se o formulário voltar para depois dos PDFs, escolher a conclusão volta a "
    + "exigir rolar para longe do exame"
);
check(
  "a bancada empilha em telas estreitas e solta o `sticky`",
  /@media \(max-width: 1100px\)[\s\S]{0,300}?\.report-clinical-split[\s\S]{0,120}?grid-template-columns:\s*1fr/.test(css)
    && /@media \(max-width: 1100px\)[\s\S]{0,400}?\.report-source-pane[\s\S]{0,120}?position:\s*static/.test(css)
);

console.log("\nD) Catálogo clínico: 17 conclusões + PERSONALIZADO, 5 pós-BD");
const conclusionCodes = [...conclusions.matchAll(
  /ConclusionOption\(\s*(?:"([A-Z_]+)"|CONCLUSION_CUSTOM_CODE)/g
)].map((m) => m[1] || "PERSONALIZADO");
check(
  `o catálogo tem 18 entradas (17 clínicas + PERSONALIZADO) — veio ${conclusionCodes.length}`,
  conclusionCodes.length === 18
);
check(
  "PERSONALIZADO é a última e não conta como conclusão clínica",
  conclusionCodes[conclusionCodes.length - 1] === "PERSONALIZADO"
    && conclusionCodes.filter((c) => c !== "PERSONALIZADO").length === 17
);
check(
  "NORMAL abre a lista",
  conclusionCodes[0] === "NORMAL"
);
const bdCodes = [...conclusions.matchAll(
  /BronchodilatorOption\(\s*(?:"([A-Z_]+)"|BD_NOT_PERFORMED_CODE)/g
)].map((m) => m[1] || "BD_NAO_REALIZADO");
check(
  `há exatamente 5 complementos pós-broncodilatador — vieram ${bdCodes.length}`,
  bdCodes.length === 5
);

console.log("\nE) Siglas viram texto por extenso, imediatamente");
check(
  "DVO Leve → “Distúrbio ventilatório obstrutivo leve.”",
  /"DVO_LEVE",\s*\n\s*"DVO Leve",\s*\n\s*"Distúrbio ventilatório obstrutivo leve\.",/.test(
    conclusions
  )
);
check(
  "RBD+ → “Com resposta significativa ao broncodilatador.”",
  /"RBD_POSITIVO",\s*\n\s*"RBD\+",\s*\n\s*"Com resposta significativa ao broncodilatador\.",/.test(
    conclusions
  )
);
check(
  "clicar numa sigla recompõe o texto na hora, sem ida ao servidor",
  /data-report-conclusion\]"\)\)\s*\{[\s\S]{0,500}?applyCatalogText\(\);\s*\n\s*render\(\);/.test(
    workflow
  )
    && /data-report-bd\]"\)\)\s*\{[\s\S]{0,500}?applyCatalogText\(\);\s*\n\s*render\(\);/.test(
      workflow
    )
);
check(
  "a composição concatena conclusão + complemento por extenso",
  /return extra \? `\$\{base\}\\n\$\{extra\}` : base;/.test(workflow)
);
check(
  "o texto editado à mão pela médica nunca é sobrescrito em silêncio",
  /O texto que você editou foi preservado/.test(workflow)
);
check(
  "PERSONALIZADO abre um campo de texto próprio",
  /state\.conclusionCode === "PERSONALIZADO" \? `[\s\S]{0,400}?reportCustomConclusion/.test(
    workflow
  )
);

console.log("\nF) Nada do que já existia foi perdido");
[
  ["prévia do laudo", /Gerar prévia do laudo/],
  ["assinatura e liberação", /Assinar e liberar laudo/],
  ["finalização de revisão em lote (M25.8)", /renderBatchBar/],
  ["assinatura qualificada VIDaaS (M25.7)", /renderQualifiedAction/],
  ["fila por unidade (M25.6)", /queueUnits/],
  ["assinatura manuscrita (M25.4)", /renderSignaturePanel/],
  ["referência de verificação (M25.11)", /verification_reference/],
  ["download separado dos dois documentos", /renderDocumentsPanel/],
].forEach(([rotulo, re]) => {
  check(`preservado: ${rotulo}`, re.test(workflow));
});
check(
  "o aviso do piloto interno continua obrigatório e literal",
  workflow.includes(
    "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE"
  )
);
check(
  "o cache-bust foi renovado junto com o JS e o CSS",
  /report-workflow\.js\?v=2026080901/.test(index)
    && /report-workflow\.css\?v=2026080901/.test(index)
);

console.log(
  failures === 0
    ? "\nM25.12: todos os contratos passaram."
    : `\nM25.12: ${failures} falha(s).`
);
process.exit(failures === 0 ? 0 : 1);
