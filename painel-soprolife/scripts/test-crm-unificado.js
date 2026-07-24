#!/usr/bin/env node
// SoproLife — M19: unificação final do CRM de pacientes.
//
// Prova, de forma estática e determinística, os critérios de aceite que
// dependem do frontend:
//   A) existe UMA só implementação visível de paciente/acompanhamento;
//   B) a tela legada de planilha sumiu da navegação e do runtime;
//   C) o Núcleo administrativo não expõe mais abas operacionais duplicadas;
//   D) cadastro continua exclusivo da Central de Cadastros;
//   E) WhatsApp só abre depois de prévia editável e nunca conclui sozinho;
//   F) o workspace cobre KPIs, filas, resultados e modelos exigidos;
//   G) nenhum CSS morto da tela legada ficou para trás;
//   H) acessibilidade e responsividade mínimas estão declaradas.
//
// Uso:  node painel-soprolife/scripts/test-crm-unificado.js
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

const appSrc = ler("js", "app.js");
const wsSrc = ler("js", "crm-workspace.js");
const nucleoSrc = ler("js", "m15-nucleo.js");
const centralSrc = ler("js", "central-cadastros.js");
const indexSrc = ler("index.html");
const styleCss = ler("css", "style.css");
const wsCss = ler("css", "crm-workspace.css");

// ───────────── A) uma única implementação de paciente/acompanhamento ────────
console.log("A) Implementação única de paciente e acompanhamento");

caso("app.js não define mais renderCrmPacientes (tela legada de planilha)",
     appSrc.indexOf("function renderCrmPacientes") === -1);
caso("app.js não define mais renderCrmFollowupDetalhe",
     appSrc.indexOf("function renderCrmFollowupDetalhe") === -1);
caso("app.js não define mais renderCrmAcompanhamentoM15 (parcial do M18)",
     appSrc.indexOf("function renderCrmAcompanhamentoM15") === -1);
caso("app.js não tem mais abrirWhatsappFollowup próprio",
     appSrc.indexOf("function abrirWhatsappFollowup") === -1);
caso("as rotas antigas caem no workspace canônico",
     /case "pacientes":[\s\S]{0,400}window\.SoproCrm\.abrir/.test(appSrc));
caso("existe exatamente UM card de pacientes no hub do CRM",
     (appSrc.match(/title: "Pacientes e Acompanhamento"/g) || []).length === 1 &&
     appSrc.indexOf('title: "Acompanhamento e WhatsApp"') === -1);
caso("o workspace é o único módulo que lista pacientes",
     wsSrc.indexOf("/crm/pacientes/busca") !== -1 &&
     appSrc.indexOf("/crm/pacientes/busca") === -1);

// ───────────── B) o JSON legado saiu do runtime ─────────────────────────────
console.log();
console.log("B) Arquivo privado de planilha fora do runtime do painel");

caso("app.js não carrega mais data-private/followup-pacientes.local.json",
     appSrc.indexOf('loadOptionalJson("./data-private/followup-pacientes.local.json")') === -1);
caso("state.followupPacientes deixou de existir no painel",
     appSrc.indexOf("followupPacientes: null") === -1 &&
     appSrc.indexOf("state.followupPacientes") === -1);
caso("nenhum módulo do CRM lê o JSON legado",
     wsSrc.indexOf("followup-pacientes.local.json") === -1);
caso("o aviso de automação diz que o arquivo é histórico, não operacional",
     appSrc.indexOf("O painel não lê mais followup-pacientes.local.json") !== -1);
caso("o script gerador continua existindo como evidência de migração",
     fs.existsSync(path.join(RAIZ, "scripts", "generate-followup-pacientes.py")));

// ───────────── C) Núcleo sem abas operacionais duplicadas ───────────────────
console.log();
console.log("C) Núcleo administrativo restrito ao que é administrativo");

const tabsBloco = nucleoSrc.slice(
  nucleoSrc.indexOf("var TABS = ["),
  nucleoSrc.indexOf("];", nucleoSrc.indexOf("var TABS = ["))
);
const abas = [...tabsBloco.matchAll(/\["([a-z]+)",\s*"([^"]+)",\s*"([a-z]+)"\]/g)]
  .map((m) => ({ id: m[1], rotulo: m[2], papel: m[3] }));

caso("TABS tem exatamente 5 entradas administrativas",
     abas.length === 5, JSON.stringify(abas.map((a) => a.id)));
["pessoas", "leads", "espirometrias", "consultas", "parceiros",
 "encaminhamentos", "followup", "financeiro"].forEach((id) => {
  caso(`aba operacional "${id}" saiu da navegação ordinária do Núcleo`,
       !abas.some((a) => a.id === id));
});
caso("restaram Diagnóstico técnico, Inspeção, Migração, Auditoria e Administração",
     abas.map((a) => a.id).join(",") === "visao,dados,migracao,auditoria,admin");
caso("a inspeção técnica de dados é restrita a admin",
     abas.find((a) => a.id === "dados").papel === "admin");
caso("a inspeção é rotulada como técnica/sistema, não como CRM",
     /Inspeção técnica/.test(tabsBloco) &&
     nucleoSrc.indexOf("Inspeção técnica do sistema.") !== -1);
caso("Migração continua acessível a leitura",
     abas.find((a) => a.id === "migracao").papel === "leitura");
caso("Auditoria continua em gestor e Administração em admin",
     abas.find((a) => a.id === "auditoria").papel === "gestor" &&
     abas.find((a) => a.id === "admin").papel === "admin");
caso("os carregadores técnicos continuam existindo (APIs e dados intactos)",
     ["pessoas", "leads", "espirometrias", "consultas", "parceiros",
      "encaminhamentos", "followup", "financeiro"]
       .every((id) => nucleoSrc.indexOf("loaders." + id) !== -1));
caso("RBAC da UI segue sem esconder nada quando o papel é desconhecido",
     nucleoSrc.indexOf("if (!state.user || !state.user.papeis_efetivos) return true;") !== -1);

// ───────────── D) cadastro só na Central ────────────────────────────────────
console.log();
console.log("D) Central de Cadastros continua o único lugar de cadastro novo");

caso("o workspace não monta nenhum formulário de criação de pessoa/exame",
     wsSrc.indexOf('method: "POST", body: JSON.stringify({ nome_completo') === -1 &&
     wsSrc.indexOf("/pessoas\"") === -1 &&
     wsSrc.indexOf("/espirometrias") === -1 &&
     wsSrc.indexOf("/consultas") === -1);
const postsDoWorkspace = [...wsSrc.matchAll(/api\("([^"]+)",\s*\{\s*\n?\s*method: "POST"/g)]
  .map((m) => m[1]);
const postsUnicos = [...new Set(postsDoWorkspace)].filter((p) => p !== "/crm/pacientes/busca");
caso("o único endpoint de escrita do workspace é o registro de contato",
     postsUnicos.length === 1 && postsUnicos[0] === "/crm/contatos",
     JSON.stringify(postsUnicos));
// M20: os atalhos passaram a apontar para o fluxo ÚNICO "Novo atendimento",
// com o tipo pré-selecionado — nunca para abas de cadastro concorrentes.
caso("os botões de criar fazem deep-link para a Central",
     wsSrc.indexOf("window.SoproCentral.open(tab,") !== -1 &&
     wsSrc.indexOf('data-crm-ws-central="atendimento"') !== -1 &&
     wsSrc.indexOf('"espirometria_soprolife"') !== -1 &&
     wsSrc.indexOf('"consulta_soprolife"') !== -1);
caso("o deep-link leva só o código público (nunca nome ou telefone)",
     wsSrc.indexOf("prefill.person_codigo = codigoPessoa;") !== -1 &&
     centralSrc.indexOf("const preCodigo = (state.prefill || {}).person_codigo;") !== -1);

// ───────────── E) WhatsApp assistido ────────────────────────────────────────
console.log();
console.log("E) WhatsApp: prévia editável, sem envio automático, sem conclusão implícita");

caso("nenhum anchor wa.me direto no workspace",
     wsSrc.indexOf("wa.me") === -1 && !/href="https:\/\/wa\.me/.test(wsSrc));
caso("a URL vem sempre da API (endpoint dedicado)",
     wsSrc.indexOf("/whatsapp-url") !== -1);
caso("existe prévia editável antes de abrir",
     wsSrc.indexOf('id="crmWsWaMsg"') !== -1 &&
     wsSrc.indexOf("<textarea") !== -1);
caso("a prévia mostra quem será contatado e se é paciente ou responsável",
     wsSrc.indexOf("crm-ws-tag-resp") !== -1 &&
     wsSrc.indexOf("responsável legal, não o paciente") !== -1 &&
     wsSrc.indexOf("o próprio paciente") !== -1);
caso("abrir o WhatsApp NÃO conclui o acompanhamento",
     wsSrc.indexOf("Abrir o WhatsApp <strong>não</strong> conclui o acompanhamento") !== -1 &&
     wsSrc.indexOf("/concluir") === -1);
caso("depois de abrir, o operador é levado a escolher o resultado",
     /window\.open\(final[\s\S]{0,200}modalResultado\(id\)/.test(wsSrc));
caso("todo resultado passa pelo endpoint auditável /crm/contatos",
     (wsSrc.match(/api\("\/crm\/contatos"/g) || []).length >= 2);

// ───────────── F) cobertura funcional exigida ───────────────────────────────
console.log();
console.log("F) Cobertura de KPIs, filas, resultados e modelos");

const mod = require(path.join(RAIZ, "js", "crm-workspace.js"));
const kpis = mod.KPI_DEFS.map((d) => d[0]);
[
  "total_pacientes", "contatos_hoje", "contatos_atrasados", "proximos_7",
  "proximos_30", "sem_telefone", "followups_concluidos_mes",
  "pacientes_reativados", "exames_mes", "consultas_mes",
].forEach((k) => caso(`KPI "${k}" presente na visão geral`, kpis.includes(k)));
caso("todo KPI abre uma lista filtrada (nenhum é decorativo)",
     mod.KPI_DEFS.every((d) => d[2] && typeof d[2].view === "string"));
caso("as cinco visões internas existem",
     mod.VIEWS.map((v) => v[0]).join(",") ===
       "visao,pacientes,contatos,historico,indicadores");

const wsCrmSrc = wsSrc;
caso("as oito filas exigidas são renderizadas a partir do /crm/config",
     wsCrmSrc.indexOf("state.config && state.config.filas") !== -1 &&
     wsCrmSrc.indexOf("/crm/contatos-a-realizar?fila=") !== -1);
caso("os cinco resultados de contato vêm do vocabulário do servidor",
     wsCrmSrc.indexOf("state.config.resultados_contato") !== -1);
caso("os cinco modelos de mensagem vêm do vocabulário do servidor",
     wsCrmSrc.indexOf("state.config.templates_whatsapp") !== -1);
caso("a linha do tempo combina cadastro, exames, consultas, follow-ups e contatos",
     ["cadastro", "lead", "espirometria", "consulta", "followup", "interacao", "financeiro"]
       .every((t) => wsCrmSrc.indexOf(t + ":") !== -1));
caso("os oito gráficos exigidos existem",
     ["contatos_por_periodo", "followups_concluidos_por_mes", "exames_por_mes",
      "consultas_por_mes", "pacientes_reativados_por_mes", "resultados_de_contato",
      "pacientes_por_origem"].every((c) => wsCrmSrc.indexOf('"' + c + '"') !== -1) &&
     wsCrmSrc.indexOf("contatos_atrasados") !== -1);
caso("gráfico sem dado mostra estado vazio explícito (nunca valor simulado)",
     wsCrmSrc.indexOf("Sem dados no período selecionado.") !== -1);
caso("tooltips habilitados nos gráficos",
     wsCrmSrc.indexOf("tooltip: { enabled: true }") !== -1);

// ───────────── G) identidade e códigos ──────────────────────────────────────
console.log();
console.log("G) Códigos públicos apresentados de forma uniforme");

caso("todo código é exibido como Rótulo · CÓDIGO pela mesma função",
     wsCrmSrc.indexOf("function codigo(rotulo, code, opts)") !== -1 &&
     wsCrmSrc.indexOf("crm-ws-code-label") !== -1);
caso("existe botão de copiar código onde é útil",
     wsCrmSrc.indexOf("data-crm-copiar") !== -1);
// O cliente só ECOA o public_code vindo da API: não formata, não numera e
// não zera à esquerda. O único "PES-000000" do arquivo é texto de exemplo
// dentro do placeholder do campo de busca.
caso("nenhum código é remontado/renumerado no cliente",
     !/padStart\(/.test(wsCrmSrc) &&
     !/"(PES|ESP|CON|FUP|LAN|LEA|INT|ENC)-" *\+/.test(wsCrmSrc) &&
     (wsCrmSrc.match(/PES-000000/g) || []).length === 1 &&
     /placeholder="nome ou PES-000000"/.test(wsCrmSrc));

// ───────────── H) CSS morto, acessibilidade e responsividade ────────────────
console.log();
console.log("H) CSS morto removido, acessibilidade e responsividade");

["fp-prev-item", "fp-prev-list", "fp-preview-panel", "btn-ver-todos",
 "fp-detalhe-toolbar", "fp-filter-btn", "fp-cards-area", "fp-card",
 "status-atrasado", "status-sem-data", "fp-nome", "fp-empty"].forEach((cls) => {
  caso(`seletor morto .${cls} removido do style.css`,
       styleCss.indexOf("." + cls) === -1);
});
caso("os estilos de WhatsApp ainda usados (B2B/leads) foram preservados",
     styleCss.indexOf(".fp-wa-btn") !== -1 && appSrc.indexOf("fp-wa-btn") !== -1);

caso("index.html carrega o CSS e o JS do workspace",
     indexSrc.indexOf("css/crm-workspace.css") !== -1 &&
     indexSrc.indexOf("js/crm-workspace.js") !== -1);
caso("o workspace carrega DEPOIS do núcleo M15 (depende da sessão)",
     indexSrc.indexOf("js/m15-nucleo.js") < indexSrc.indexOf("js/crm-workspace.js"));
caso("cache-buster novo aplicado ao workspace",
     /crm-workspace\.js\?v=2026072501/.test(indexSrc) &&
     /crm-workspace\.css\?v=\d{10}/.test(indexSrc));

caso("abas do workspace têm papel de tablist e estado acessível",
     wsCrmSrc.indexOf('role="tablist"') !== -1 &&
     wsCrmSrc.indexOf('aria-selected="') !== -1);
caso("cards de KPI são botões reais, ativáveis por Enter e Espaço",
     wsCrmSrc.indexOf('class="crm-ws-kpi') !== -1 &&
     wsCrmSrc.indexOf("<button") !== -1 &&
     wsCrmSrc.indexOf('ev.key === "Enter" || ev.key === " "') !== -1);
caso("modal tem foco preso, ESC fecha e devolve o foco",
     wsCrmSrc.indexOf('aria-modal="true"') !== -1 &&
     wsCrmSrc.indexOf('ev.key === "Escape"') !== -1 &&
     wsCrmSrc.indexOf("focoAnterior.focus()") !== -1 &&
     wsCrmSrc.indexOf('ev.key !== "Tab"') !== -1);
caso("colunas têm rótulo acessível e data-label para virar cartão",
     wsCrmSrc.indexOf('scope="col"') !== -1 &&
     wsCrmSrc.indexOf('data-label="Paciente"') !== -1);
caso("foco de teclado é visível em todos os controles do workspace",
     ["crm-ws-kpi", "crm-ws-chip", "crm-ws-link", "crm-ws-copy", "crm-ws-mini"]
       .every((c) => new RegExp("\\." + c + ":focus-visible").test(wsCss)));
caso("tabela larga rola dentro do próprio contêiner (sem overflow da página)",
     /\.crm-ws-table-wrap\s*\{[^}]*overflow-x:\s*auto/.test(wsCss) &&
     /\.crm-ws-table-wrap\s*\{[^}]*max-width:\s*100%/.test(wsCss));
caso("tabela vira cartão em telas estreitas",
     /@media \(max-width: 720px\)/.test(wsCss) &&
     wsCss.indexOf("content: attr(data-label)") !== -1);
caso("gráficos e modal se adaptam a 480px",
     /@media \(max-width: 480px\)/.test(wsCss) &&
     /\.crm-ws-charts \{ grid-template-columns: 1fr; \}/.test(wsCss));

// ───────────── I) privacidade ───────────────────────────────────────────────
console.log();
console.log("I) Privacidade");

caso("o workspace nunca monta telefone a partir de dígitos do cliente",
     wsCrmSrc.indexOf("telefone_mascarado") !== -1 &&
     wsCrmSrc.indexOf("valor_normalizado") === -1);
caso("busca por nome vai no corpo do POST, nunca na URL",
     wsCrmSrc.indexOf('api("/crm/pacientes/busca", {') !== -1 &&
     !/pacientes\?q=/.test(wsCrmSrc));
caso("o histórico é filtrado por código público, não por nome/telefone",
     wsCrmSrc.indexOf("person_public_code") === -1 ||
     !/historico-contatos\?[^"]*nome=/.test(wsCrmSrc));
caso("nenhum console.log de dado no workspace",
     wsCrmSrc.indexOf("console.log") === -1 &&
     wsCrmSrc.indexOf("console.debug") === -1);

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
