#!/usr/bin/env node
// M24A — contratos estruturais do fluxo frontend de laudos.
// Offline: lê somente fontes versionadas; não abre PDF, rede ou dado privado.
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(ROOT, ...parts), "utf8");

const index = read("index.html");
const workflow = read("js", "report-workflow.js");
const m15 = read("js", "m15-nucleo.js");
const css = read("css", "report-workflow.css");
const proxy = read("scripts", "command-center-local-server.py");
const reports = read("nucleo-m15", "app", "routers", "reports.py");
const operations = read("nucleo-m15", "app", "routers", "operations.py");
const runbook = read("docs", "m24a-laudos-pdf-operacao.md");
const envExample = read("nucleo-m15", ".env.example");
const publicConfig = JSON.parse(read("data", "m15-config.json"));
const backendConfig = read("nucleo-m15", "app", "config.py");

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) console.log(`  PASS: ${label}`);
  else {
    failures += 1;
    console.log(`  FAIL: ${label}${detail ? " — " + detail : ""}`);
  }
}

console.log("A) Integração ao painel existente");
const sidebarEntries = [...index.matchAll(
  /class="nav-item"[^>]*data-section="laudos-espirometria"/g
)];
check("sidebar possui exatamente um destino de laudos", sidebarEntries.length === 1);
check("seção clínica é distinta de Documentos institucionais",
      /<section id="laudos-espirometria" class="section"/.test(index) &&
      /<section id="documentos" class="section"/.test(index));
check("entrada respeita flag de laudos independente e default-off",
      (index.match(/data-report-entry/g) || []).length === 3 &&
      publicConfig.enabled === true && publicConfig.reports_enabled === false &&
      /config\.enabled !== true/.test(workflow) &&
      /config\.reports_enabled === true/.test(workflow) &&
      /reports_enabled: bool = False/.test(backendConfig));
check("CSS e módulo M24A estão versionados no index",
      /report-workflow\.css\?v=2026072602/.test(index) &&
      /report-workflow\.js\?v=2026072602/.test(index));
check("layout reutiliza section-title, panel, safe-label e botões M15",
      ["section-title", "panel report-panel", "safe-label", "m15-btn"]
        .every((token) => index.includes(token) || workflow.includes(token)));

console.log("\nB) Sessão, mesma origem e ausência de snapshot público");
check("workflow consome o cliente compartilhado window.SoproM15",
      workflow.includes("window.SoproM15") &&
      workflow.includes("c.api(") && workflow.includes("c.apiBlob("));
check("sessão é observada sem acessar cookie/token",
      workflow.includes("c.onSessionChange(") &&
      workflow.includes("c.hasToken()") &&
      !/document\.cookie|Authorization\s*=/.test(workflow));
check("FormData preserva boundary no helper compartilhado",
      /options\.body instanceof FormData/.test(m15) &&
      /var base = isFormData \? \{\}/.test(m15));
check("apiBlob está exportado pelo cliente M15",
      /function apiBlob\(/.test(m15) && /apiBlob: apiBlob/.test(m15));
check("401 do PDF encerra sessão/token expirado",
      /responseType === "blob"[\s\S]{0,800}?resp\.status === 401/.test(m15) &&
      /encerrarSessao\("Sua sessão expirou/.test(m15));
check("storage do navegador só contém opt-in loopback, nunca estado de laudo",
      /localStorage\.getItem\("soproM24AReports"\)/.test(workflow) &&
      !/localStorage\.setItem|sessionStorage|indexedDB/.test(workflow) &&
      workflow.includes('"127.0.0.1", "::1", "localhost"'));
const workflowWithoutFeatureConfig = workflow.replaceAll("data/m15-config.json", "");
check("nenhum JSON público/privado é carregado para laudos",
      !/data-private|\.local\.json|(?:^|[\"'])\.?\/?data\/[^\"']+\.json/.test(
        workflowWithoutFeatureConfig
      ));
check("único fetch direto do módulo é a configuração pública da feature",
      (workflow.match(/\bfetch\(/g) || []).length === 1 &&
      /fetch\(CONFIG_URL/.test(workflow));
check("nenhuma URL absoluta ou host de backend remoto existe no módulo",
      !/https?:\/\//.test(workflow));

console.log("\nC) Localização, upload e contrato API/frontend");
check("busca aceita somente código ESP institucional",
      /EXAM_CODE_RE = \/\^ESP-/.test(workflow) &&
      workflow.includes("public_code=") &&
      workflow.includes("Sem busca por nome"));
check("backend oferece filtro exato por public_code",
      /public_code: str \| None = None/.test(operations) &&
      /SpirometryExam\.public_code == public_code\.strip\(\)\.upper\(\)/.test(operations));
check("upload usa multipart no endpoint /laudos",
      /new FormData\(\)/.test(workflow) &&
      /c\.api\("\/laudos", \{ method: "POST", body: data \}\)/.test(workflow));
check("limite de 25 MiB também é validado no cliente",
      workflow.includes("25 * 1024 * 1024") &&
      workflow.includes("file.size > MAX_UPLOAD_BYTES"));
for (const endpoint of [
  "/laudos?exam_id=",
  "/laudos/templates",
  "/compor",
  "/revisao",
  "/finalizar",
  "/devolver-para-ajuste",
  "/nova-versao-corretiva",
]) {
  check(`frontend cobre endpoint ${endpoint}`, workflow.includes(endpoint));
}

console.log("\nD) Metadados seguros e entrega PDF autenticada");
check("UI não renderiza nome original, UUID de pessoa/usuário ou storage_path",
      !/original_filename_display|person_id|created_by_user_id|storage_path/.test(workflow));
check("metadados exibidos são técnicos e limitados",
      ["Páginas", "Tamanho", "Integridade", "sha256:"]
        .every((token) => workflow.includes(token)) &&
      workflow.includes("doc.created_at_local || doc.created_at_utc"));
check("preview usa Blob/object URL, não endpoint como src",
      /URL\.createObjectURL\(blob\)/.test(workflow) &&
      /src="\$\{esc\(state\.previewObjectUrl\)\}"/.test(workflow));
check("object URLs são revogados ao trocar/sair",
      /URL\.revokeObjectURL\(state\.previewObjectUrl\)/.test(workflow) &&
      /beforeunload", releasePreview/.test(workflow));
check("iframe tem título e referrerpolicy",
      /title="Visualização autenticada do PDF/.test(workflow) &&
      /referrerpolicy="no-referrer"/.test(workflow));
check("download também busca Blob autenticado e deriva nome seguro",
      /reportContentPath\(doc\.id, version\.id, "download"\)/.test(workflow) &&
      /safeDownloadName\(exam, version\)/.test(workflow));
check("backend e proxy marcam PDF como private/no-store + nosniff",
      reports.includes('"Cache-Control": "private, no-store"') &&
      reports.includes('"X-Content-Type-Options": "nosniff"') &&
      proxy.includes('"Cache-Control", "private, no-store"') &&
      proxy.includes('"X-Content-Type-Options", "nosniff"'));
check("cada entrega bem-sucedida é auditada antes da Response",
      /laudo_conteudo_entregue/.test(reports) &&
      /db\.commit\(\)[\s\S]{0,100}?return Response/.test(reports));
check("erros da API exibem mensagem humana e preservam código",
      /function readableApiError/.test(m15) &&
      /err\.code = detail\.code/.test(m15) &&
      !/new Error\(typeof msg === "string" \? msg : JSON\.stringify\(msg\)\)/.test(m15));
check("proxy aceita PDF somente na rota exata de conteúdo",
      /_M15_REPORT_CONTENT_RE/.test(proxy) &&
      /report_content\s+and 200 <= status < 300/.test(proxy) &&
      (proxy.match(/\[0-9a-fA-F\]\{12\}/g) || []).length >= 2);
check("limites ampliados ficam restritos a upload/conteúdo de laudo",
      /_is_report_upload/.test(proxy) && /_is_report_content/.test(proxy) &&
      /_M15_MAX_REPORT_REQUEST_BODY/.test(proxy) &&
      /_M15_MAX_REPORT_RESPONSE_BODY/.test(proxy));

console.log("\nE) Templates, tooltip e ajuda acessível");
check("todas as abreviações vêm da API, sem texto clínico hardcoded",
      /state\.templates\.map/.test(workflow) &&
      !/diagnóstico|obstru(tivo|ção)|restri(tivo|ção)/i.test(workflow));
check("abreviação possui tooltip nativo",
      /<abbr title="\$\{esc\(tooltip\)\}"/.test(workflow));
check("cada modelo oferece details/summary com texto completo",
      /<details>[\s\S]*?<summary>Texto completo do modelo/.test(workflow) &&
      /report-template-full-text/.test(workflow));
check("seleção de template pertence ao formulário enviado",
      /<form id="reportComposeForm"[\s\S]{0,160}?\$\{templatesHtml\(\)\}/.test(workflow));
check("modelo inativo não pode ser escolhido",
      /template\.ativo && !state\.busy \? "" : "disabled"/.test(workflow));
check("página e posição topo/rodapé são controles rotulados",
      /id="reportPageNumber"/.test(workflow) &&
      /name="placement" value="topo"/.test(workflow) &&
      /name="placement" value="rodape"/.test(workflow));

console.log("\nF) RBAC e ciclo de vida na interface");
check("upload/composição exigem operacional",
      (workflow.match(/can\("operacional"\)/g) || []).length >= 3);
check("finalização só aparece com can gestor",
      /doc\.status === "em_revisao"[\s\S]{0,240}?can\("gestor"\)/.test(workflow));
check("botão de finalizar só existe no estado em revisão",
      /data-report-finalize/.test(workflow) &&
      !/doc\.status === "rascunho"[\s\S]{0,200}?data-report-finalize/.test(workflow));
check("finalizado não renderiza formulário de composição",
      /if \(doc\.status !== "rascunho"\) return ""/.test(workflow));
check("assinatura digital pendente é clara e não afirma assinatura real",
      workflow.includes("<strong>assinatura digital pendente</strong>") &&
      workflow.includes("não afirma que o PDF esteja assinado"));
check("correção chama novo documento e explica imutabilidade",
      workflow.includes("Abrir versão corretiva") &&
      workflow.includes("novo documento em rascunho") &&
      workflow.includes("não terá PDF, hash, data ou estado alterados"));
check("documento sucedido continua identificado como finalizado/imutável",
      workflow.includes("Seu PDF,") && workflow.includes("permanecem imutáveis"));
check("gestor pode devolver revisão com motivo técnico fechado",
      workflow.includes("Devolver para ajuste") &&
      workflow.includes("data-report-adjustment") &&
      workflow.includes("ajuste_de_composicao") &&
      workflow.includes("reason_code"));

console.log("\nG) Acessibilidade, diálogo e responsividade");
check("status assíncrono usa aria-live polite",
      /id="reportStatus"[\s\S]{0,180}?aria-live="polite"/.test(workflow));
check("listas de exame e documento têm semântica acessível",
      /role="listbox" aria-label="Exames/.test(workflow) &&
      /aria-selected=/.test(workflow) &&
      /role="list" aria-label="Documentos/.test(workflow));
check("modal tem role dialog, aria-modal, nome e descrição",
      /role="dialog" aria-modal="true"/.test(workflow) &&
      /aria-labelledby/.test(workflow) && /aria-describedby/.test(workflow));
check("modal prende foco, fecha com Escape e devolve foco",
      workflow.includes('event.key === "Escape"') &&
      workflow.includes('event.key !== "Tab"') &&
      workflow.includes("previousFocus.focus()") &&
      workflow.includes("previousFocus.isConnected") &&
      workflow.includes("if (submitting) return"));
check("respostas assíncronas obsoletas não trocam o exame em tela",
      (workflow.match(/epoch !== state\.requestEpoch/g) || []).length >= 7 &&
      workflow.includes("examId !== state.selectedExamId") &&
      workflow.includes("detail.spirometry_exam_id !== state.selectedExamId"));
check("foco visível cobre controles do fluxo",
      css.includes(".report-exam-option:focus-visible") &&
      css.includes(".report-template-card summary:focus-visible"));
for (const width of ["1180px", "900px", "720px", "480px"]) {
  check(`CSS contém breakpoint ${width}`, css.includes(`max-width: ${width}`));
}
check("grids usam minmax(0, ...) e quebram para uma coluna",
      /grid-template-columns: minmax\(260px, 310px\) minmax\(0, 1fr\)/.test(css) &&
      (css.match(/grid-template-columns: 1fr;/g) || []).length >= 4);
const pdfFrameRule = (css.match(/\.report-pdf-frame\s*\{([^}]*)\}/) || [])[1] || "";
check("iframe é fluido e sem largura fixa",
      /width:\s*100%/.test(pdfFrameRule) &&
      !/width:\s*\d+px/.test(pdfFrameRule));
check("movimento reduzido é respeitado",
      /prefers-reduced-motion: reduce/.test(css));

console.log("\nH) Operação, LGPD e decisões não inventadas");
check("env example documenta raiz absoluta e fora do Git",
      envExample.includes("M15_REPORTS_STORAGE_DIR=/opt/soprolife/private/m15-reports") &&
      envExample.includes("fora do repositório Git") &&
      envExample.includes("M15_REPORTS_ENABLED=false"));
for (const topic of [
  "Backup coordenado",
  "Restauração e ensaio",
  "Retenção e exclusão controlada",
  "LGPD, acesso, rastreabilidade e auditoria",
  "Pré-requisitos de implantação",
  "Rollback",
]) {
  check(`runbook cobre ${topic}`, runbook.includes(`## ${topic}`));
}
for (const decision of [
  "templates clínicos",
  "identidade do médico",
  "provedor ICP-Brasil",
  "período de retenção",
  "redação jurídica final do rodapé",
]) {
  check(`runbook mantém pendente: ${decision}`, runbook.includes(decision));
}
check("runbook não afirma assinatura real",
      runbook.includes("assinatura digital pendente") &&
      runbook.includes("nenhuma assinatura é alegada"));

console.log();
if (failures) {
  console.log(`RESULTADO: ${failures} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
