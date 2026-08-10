#!/usr/bin/env node
// M24C — contratos estruturais offline do fluxo controlado de laudos.
"use strict";

const fs = require("fs");
const path = require("path");

const PANEL = path.resolve(__dirname, "..");
const read = (...parts) => fs.readFileSync(path.join(PANEL, ...parts), "utf8");

const index = read("index.html");
const workflow = read("js", "report-workflow.js");
const m15 = read("js", "m15-nucleo.js");
const css = read("css", "report-workflow.css");
const publicConfig = JSON.parse(read("data", "m15-config.json"));
const backendConfig = read("nucleo-m15", "app", "config.py");
const security = read("nucleo-m15", "app", "security.py");
const reports = read("nucleo-m15", "app", "routers", "reports.py");
const models = read("nucleo-m15", "app", "models.py");
const catalog = read(
  "nucleo-m15", "app", "services", "report_catalog.py"
);
const signature = read(
  "nucleo-m15", "app", "services", "signature_provider.py"
);
const migration = read(
  "nucleo-m15", "migrations", "versions",
  "4c9e2f7a6b31_m24c_physician_assignment_workflow.py"
);
const proxy = read("scripts", "command-center-local-server.py");
const envExample = read("nucleo-m15", ".env.example");
const runbook = read("docs", "m24c-medical-assignment-workflow.md");
const signatureDecision = read("docs", "m24c-signature-provider-decision.md");

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS: ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
}

console.log("A) Feature gate e integração no painel");
check(
  "backend continua default-off por código (independente do release ativo)",
  /reports_enabled: bool = False/.test(backendConfig)
    && /reports_mode: Literal\["disabled", "pilot", "production"\] = "disabled"/.test(
      backendConfig
    )
    && envExample.includes("M15_REPORTS_ENABLED=false")
);
check(
  "config pública do release não deriva reports_enabled de `enabled`",
  ["disabled", "pilot", "production"].includes(publicConfig.reports_mode)
    && publicConfig.reports_enabled === (publicConfig.reports_mode !== "disabled")
);
check(
  "entrada inteira começa oculta",
  (index.match(/data-report-entry hidden/g) || []).length === 3
    && /config\.enabled !== true/.test(workflow)
    && /reportsFeatureEnabled\(config\)/.test(workflow)
);
check(
  "override local é limitado a loopback",
  workflow.includes('"127.0.0.1", "::1", "localhost"')
    && workflow.includes('localStorage.getItem("soproM24AReports")')
    && !/localStorage\.setItem|sessionStorage|indexedDB/.test(workflow)
);
check(
  "assets M24C/M25.2 versionados",
  // Versão de cache-busting: atualizada a cada release que muda o CSS/JS
  // do fluxo de laudos. O que a asserção prova é que os dois assets
  // continuam versionados juntos, com o MESMO selo — e não qual selo é.
  // Fixar o número aqui só obrigava a editar o teste a cada release, sem
  // provar nada a mais (M25.3).
  (() => {
    const css = index.match(/report-workflow\.css\?v=(\d{10})/);
    const js = index.match(/report-workflow\.js\?v=(\d{10})/);
    return Boolean(css && js && css[1] === js[1]);
  })()
);

console.log("\nB) Papel médico e autoria explícita");
check(
  "medico é papel próprio, sem herança administrativa",
  /ROLE_MEDICO = "medico"/.test(security)
    && /ROLE_MEDICO: \{ROLE_MEDICO\}/.test(security)
    && /def user_has_explicit_role/.test(security)
);
check(
  "fila e clínica exigem perfil médico ativo",
  /def list_my_report_queue/.test(reports)
    && /def _require_active_physician/.test(reports)
    && /user_has_explicit_role\(user, ROLE_MEDICO\)/.test(reports)
);
check(
  "autoria e PDF exigem atribuição ativa",
  /def _require_assigned_physician/.test(reports)
    && /def download_report_version/.test(reports)
    && /def compose_report_document/.test(reports)
);
check(
  "navegação do médico puro isola outras seções",
  workflow.includes("report-physician-only")
    && css.includes(
      'body.report-physician-only .section:not(#laudos-espirometria)'
    )
    && css.includes(
      'body.report-physician-only .nav-item:not([data-section="laudos-espirometria"])'
    )
);

console.log("\nC) Perfil, origem e atribuição");
check(
  "perfil profissional é um-para-um e CRM ativo/UF é único",
  /class PhysicianProfile/.test(models)
    && /unique=True, index=True/.test(models)
    && migration.includes("uq_physician_profiles_active_crm_uf")
    && migration.includes("CRM must contain digits only")
);
for (const uf of ["AC", "DF", "RJ", "SP", "TO"]) {
  check(`allowlist de UF contém ${uf}`, workflow.includes(`"${uf}"`));
}
for (const origin of [
  "pastore",
  "coworking",
  "residencial",
  "clinica_parceira",
  "empresa_pcmso",
  "outro",
]) {
  check(`origem fechada ${origin}`, workflow.includes(`"${origin}"`)
    && reports.includes(`"${origin}"`));
}
check(
  // M25.15 — o localizador deixou de ser só `public_code=` no endpoint
  // genérico de espirometrias: passou a `/laudos/exames`, que aceita nome do
  // paciente, ESP e LAU. O que esta checagem protege não mudou — localizar e
  // atribuir continuam acontecendo no MESMO POST, com os dois campos
  // obrigatórios no servidor.
  "upload localiza exame e atribui no mesmo POST",
  /EXAM_CODE_RE = \/\^ESP-/.test(workflow)
    && /REPORT_CODE_RE = \/\^LAU-/.test(workflow)
    && workflow.includes("/laudos/exames?q=")
    && workflow.includes('client().api("/laudos"')
    && /exam_code: str = Form\(\.\.\.\)/.test(reports)
    && /physician_profile_id: str = Form\(\.\.\.\)/.test(reports)
);
check(
  "uma atribuição ativa e histórico append-only são constraints reais",
  migration.includes("uq_report_assignments_one_active_per_document")
    && migration.includes("m24c_guard_assignment_history")
    && migration.includes("report_assignment_events")
);
check(
  "reatribuição usa versão esperada e motivo fechado",
  workflow.includes("expected_assignment_id")
    && workflow.includes("REASSIGN_REASONS")
    && reports.includes("atribuicao_desatualizada")
    && reports.includes("clinical_started_at is not None")
);

console.log("\nD) Workspace clínico restrito");
check(
  "fila mínima Meus laudos e filtros",
  workflow.includes("Meus laudos")
    && workflow.includes("/laudos/meus")
    && workflow.includes("reportStatusFilter")
    && reports.includes("_technical_report_row")
);
check(
  // M25.15 inverteu a regra desta checagem para as interfaces AUTENTICADAS:
  // o nome do paciente passou a ser a referência humana das filas. O que
  // continua valendo — e é o que se verifica agora — é que a identidade
  // exposta na fila é um bloco FECHADO (nome + código do cadastro), montado
  // por uma função única, e que a rota PÚBLICA de validação segue sem
  // qualquer dado de paciente.
  "identidade na fila é bloco fechado e não vaza na validação pública",
  workflow.includes("detail.patient.full_name")
    && reports.includes('"patient": {')
    && reports.includes("def _patient_reference(")
    && /"full_name": person\.nome_completo,\s*\n\s*"public_code": person\.public_code,/
      .test(reports)
    && !/patient|nome_completo|full_name/.test(
      reports.slice(
        reports.indexOf("def validate_released_report"),
        reports.indexOf("def _ser_signature_asset")
      )
    )
);
check(
  "original e prévia usam Blob autenticado em comparação",
  workflow.includes("apiBlob(")
    && /URL\.createObjectURL\(blob\)/.test(workflow)
    && workflow.includes("Exame técnico da MIR e laudo da SoproLife")
    && (workflow.match(/report-pdf-frame/g) || []).length >= 1
);
check(
  // M25.18 — `pdfUrls` passou a guardar object URL (token da CLI) OU um
  // endereço da própria API (sessão por cookie), porque o visualizador de
  // PDF precisava receber o Content-Disposition para nomear o download. A
  // revogação continua obrigatória, agora condicionada ao que É blob.
  "object URLs são sempre revogados",
  /startsWith\("blob:"\)\)\s*URL\.revokeObjectURL\(valor\)/.test(workflow)
    && /beforeunload", releasePdfUrls/.test(workflow)
);
check(
  "templates mostram abreviação, tooltip e texto completo",
  /<abbr title="\$\{esc\(template\.texto_tooltip/.test(workflow)
    && workflow.includes("<summary>Texto completo do modelo")
    && workflow.includes("report-template-full-text")
);
check(
  "editor, página, posição e preparação existem",
  workflow.includes('id="reportInterpretation"')
    && workflow.includes('id="reportPageNumber"')
    && workflow.includes('name="placement" value="topo"')
    && workflow.includes('name="placement" value="rodape"')
    && workflow.includes("data-report-prepare-signature")
);
check(
  "não há diagnóstico ou conclusão automática",
  workflow.includes("não há interpretação automática")
    && workflow.includes("O sistema não calcula diagnóstico nem conclusão")
    && !/spirometria.*(?:valor|número).*diagn/i.test(workflow)
);

console.log("\nE) Administração e templates provisórios");
check(
  "admin só seleciona conta existente e não recebe senha",
  workflow.includes("Selecione uma conta existente")
    && workflow.includes("Este espaço não cria usuários nem recebe senhas")
    && workflow.includes("/laudos/admin/medicos/")
);
check(
  "perfil expõe CRM/UF/RQE, verificação e suspensão",
  [
    "reportProfessionalName",
    "reportCrmNumber",
    "reportCrmState",
    "reportRqe",
    "reportVerification",
    'name="active"',
  ].every((token) => workflow.includes(token))
);
const provisionalCodes = [
  "NORMAL_PROVISORIO",
  "OBSTRUTIVO_PROVISORIO",
  "OBSTRUTIVO_BD_PROVISORIO",
  "SUGESTIVO_RESTRITIVO_PROVISORIO",
  "MISTO_PROVISORIO",
  "INESPECIFICO_QUALIDADE_PROVISORIO",
];
check(
  "existem exatamente seis categorias provisórias",
  provisionalCodes.every((code) => catalog.includes(`"${code}"`))
    && (catalog.match(/_PROVISORIO"/g) || []).length === 6
);
check(
  "texto provisório é inequívoco e bloqueado normalmente",
  catalog.includes(
    "TEXTO CLÍNICO PENDENTE DE APROVAÇÃO — NÃO UTILIZAR EM PRODUÇÃO"
  )
    && reports.includes("template_nao_aprovado")
    && backendConfig.includes("reports_test_allow_provisional_templates")
);
check(
  "admin sinaliza provisório e cria revisão",
  workflow.includes("PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO")
    && workflow.includes("Criar nova revisão")
    && workflow.includes("A revisão anterior permanece imutável")
);

console.log("\nF) Rodapé e assinatura fail-closed");
check(
  "rodapé TESTE versionado contém aviso obrigatório",
  catalog.includes("TESTE_NAO_ASSINADO")
    && catalog.includes(
      "MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"
    )
    && /class ReportFooterTemplate/.test(models)
);
check(
  "provedor runtime é unconfigured e nunca conclui",
  /name = "unconfigured"/.test(signature)
    && /status=SIGNATURE_STATUS_PENDENTE/.test(signature)
    && !/status=.*assinada/.test(signature)
);
check(
  "única transição clínica termina em assinatura pendente",
  workflow.includes("/preparar-assinatura")
    && reports.includes('provider="unconfigured"')
    && reports.includes("STATUS_ASSINATURA_PENDENTE")
    && !/def finalize_report_document/.test(reports)
);
check(
  "UI não promete assinatura qualificada",
  // M25.4 — a asserção antiga exigia a frase "Nenhum documento deste fluxo é
  // assinado ou liberado nesta versão". Ela nasceu no M24C, quando não havia
  // liberação nenhuma; a M25.2 introduziu o estado `liberado` e a frase virou
  // FALSA — a UI passou a afirmar que nada era liberado enquanto liberava.
  // O que precisa ser garantido é o que sempre foi a intenção: a interface
  // nunca pode vender a liberação institucional como assinatura ICP-Brasil.
  /não é assinatura ICP-Brasil/i.test(workflow)
    && !/assinado digitalmente|assinatura digital qualificada aplicada/i.test(
      workflow
    )
);

console.log("\nF2) M24D — piloto interno controlado");
const PILOT_WARNING =
  "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE";
check(
  "contrato de três estados: disabled é o padrão, production continua bloqueado",
  backendConfig.includes('reports_mode: Literal["disabled", "pilot", "production"]')
    && reports.includes("relatorios_producao_bloqueada")
);
check(
  "rodapé PILOTO INTERNO versionado contém o aviso exato",
  catalog.includes("PILOTO_INTERNO_NAO_ASSINADO")
    && catalog.includes(PILOT_WARNING)
);
check(
  // M25.18 — a faixa "PILOTO INTERNO" saiu da tela. O fluxo virou operação
  // real com assinatura qualificada aplicada fora do sistema, e um alarme
  // vermelho permanente virava ruído. O que ficou no lugar não é silêncio:
  // o estado de cada laudo diz o que falta, onde a informação é usada. O
  // modo continua sendo lido — só não vira faixa.
  "workspace não tem mais faixa de piloto e diz o que falta",
  !workflow.includes('class="report-pilot-warning"')
    && !workflow.includes("const PILOT_WARNING =")
    && workflow.includes('config.reports_mode === "pilot"')
    && workflow.includes(
      'liberado: "Concluído — aguardando assinatura qualificada"'
    )
);
check(
  "config pública traz reports_mode consistente com reports_enabled",
  ["disabled", "pilot", "production"].includes(publicConfig.reports_mode)
    && publicConfig.reports_enabled === (publicConfig.reports_mode !== "disabled")
);
check(
  "F2/F3/F4 fechados: autoverificação, recuperação e oráculo de existência",
  reports.includes("autoverificacao_medica_proibida")
    && reports.includes("recuperar-medico-suspenso")
    && reports.includes("physician_unavailable_after_draft")
);

console.log("\nG) Sessão, privacidade, acessibilidade e responsividade");
check(
  "usa cliente de sessão compartilhado sem token/cookie manual",
  workflow.includes("window.SoproM15")
    && workflow.includes("c.onSessionChange(")
    && workflow.includes("c.hasToken()")
    && !/document\.cookie|Authorization\s*=/.test(workflow)
);
check(
  "único fetch direto é configuração pública",
  (workflow.match(/\bfetch\(/g) || []).length === 1
    && /fetch\(CONFIG_URL/.test(workflow)
    && !/https?:\/\//.test(workflow)
);
check(
  "erros humanos evitam JSON cru",
  /function readableError/.test(workflow)
    && !/JSON\.stringify\(error|JSON\.stringify\(msg/.test(workflow)
    && /function readableApiError/.test(m15)
);
check(
  "PDF é private/no-store e nosniff em API e proxy",
  reports.includes('"Cache-Control": "private, no-store"')
    && reports.includes('"X-Content-Type-Options": "nosniff"')
    && proxy.includes('"Cache-Control", "private, no-store"')
    && proxy.includes('"X-Content-Type-Options", "nosniff"')
);
check(
  "controles têm labels, live region e foco pós-navegação",
  workflow.includes('role="status" aria-live="polite"')
    && workflow.includes('id="reportInterpretation"')
    && workflow.includes('id="reportPhysician"')
    && workflow.includes("heading.focus()")
    && workflow.includes("physician.focus()")
);
check(
  "foco visível e movimento reduzido",
  css.includes(".report-workflow-root button:focus-visible")
    && css.includes(".report-template-card summary:focus-visible")
    && css.includes("prefers-reduced-motion: reduce")
);
for (const width of ["1180px", "900px", "720px", "480px"]) {
  check(`CSS contém breakpoint ${width}`, css.includes(`max-width: ${width}`));
}
check(
  "grids protegem contra overflow e colapsam",
  (css.match(/minmax\(0, 1fr\)/g) || []).length >= 5
    && (css.match(/grid-template-columns: 1fr;/g) || []).length >= 3
    && css.includes("min-width: 0")
);

console.log("\nH) Decisões operacionais e legais");
check(
  "raiz privada segue não provisionada e sem enable",
  envExample.includes(
    "M15_REPORTS_STORAGE_DIR=/opt/soprolife/private/m15-reports"
  )
    && envExample.includes("M15_REPORTS_ENABLED=false")
    && envExample.includes("fora do repositório Git")
);
for (const topic of [
  "papel médico",
  "atribuição",
  "origem",
  "templates provisórios",
  "retenção",
  "assinatura",
  "downgrade",
]) {
  check(`runbook cobre ${topic}`, runbook.toLowerCase().includes(topic));
}
for (const need of [
  "documentação oficial",
  "credencial",
  "consentimento",
  "PAdES",
  "carimbo",
  "revogação",
  "ITI",
  "webhook",
  "custódia",
  "incidente",
]) {
  check(
    `decisão de assinatura mantém pendente: ${need}`,
    signatureDecision.toLowerCase().includes(need.toLowerCase())
  );
}
check(
  "retenção preserva sem inventar prazo ou purge",
  runbook.includes("Nenhum prazo de retenção foi inventado")
    && runbook.includes("não há job de purge")
    && runbook.includes("órfãos confirmados")
);

console.log();
if (failures) {
  console.log(`RESULTADO: ${failures} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos M24C passaram.");
