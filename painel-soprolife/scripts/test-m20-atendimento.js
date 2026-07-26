#!/usr/bin/env node
// SoproLife — M20: consolidação Pastore, status de espirometria e fluxo
// único "Novo atendimento".
//
// Prova, de forma estática/determinística no frontend:
//   A) o formatador ÚNICO de status exibe "Espirometria realizada" para os
//      quatro valores em escopo e NUNCA toca "Liberado";
//   B) a Central tem UMA aba primária "Novo atendimento" e não tem mais as
//      abas primárias Paciente, Espirometria e Consulta;
//   C) "Cadastrar somente paciente" é ação secundária dentro do fluxo;
//   D) os quatro tipos exatos existem, Pastore exige unidade e nenhum tipo
//      SoproLife carrega parceiro;
//   E) o CRM de pacientes continua vivo e não reimplementa cadastro;
//   F) os botões contextuais abrem o fluxo único com o tipo certo;
//   G) nada de PII em rota interna e nada de escrita no Google Sheets.
//
// Uso:  node painel-soprolife/scripts/test-m20-atendimento.js
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

const centralSrc = ler("js", "central-cadastros.js");
const nucleoSrc = ler("js", "m15-nucleo.js");
const wsSrc = ler("js", "crm-workspace.js");
const appSrc = ler("js", "app.js");
const statusSrc = ler("js", "status-atendimento.js");
const indexSrc = ler("index.html");
const cssSrc = ler("css", "central.css");
const pastoreServiceSrc = ler("nucleo-m15", "app", "services", "pastore.py");

// ── A) formatador único de status ──────────────────────────────────────────
console.log();
console.log("A) Formatador único de status de espirometria");

// carrega o módulo real num window sintético (mesmo código do navegador)
const sandbox = { window: {} };
new Function("window", statusSrc)(sandbox.window);
const S = sandbox.window.SoproStatus;

caso("status-atendimento.js publica window.SoproStatus", !!S);
["Realizado", "realizado", "Exame realizado", "exame realizado"].forEach((v) => {
  caso(`"${v}" exibe "Espirometria realizada"`,
       S.espirometria(v) === "Espirometria realizada");
});
["Liberado", "liberado"].forEach((v) => {
  caso(`"${v}" permanece exatamente "${v}" (FORA DE ESCOPO)`, S.espirometria(v) === v);
});
["Aguardando", "Laudo Liberado", "Cancelado", "Remarcado", "Agendada",
 "Realizada", "Consulta realizada", "Não compareceu"].forEach((v) => {
  caso(`nenhuma alteração em "${v}"`, S.espirometria(v) === v);
});
caso("null/undefined não viram texto", S.espirometria(null) === null &&
     S.espirometria(undefined) === undefined);
caso("opções de seletor não duplicam o rótulo canônico",
     JSON.stringify(S.opcoesEspirometria(["Aguardando", "Realizado", "Liberado"])) ===
     JSON.stringify([["Aguardando", "Aguardando"],
                     ["Realizado", "Espirometria realizada"],
                     ["Liberado", "Liberado"]]));

caso("index.html carrega o formatador ANTES dos módulos que o consomem",
     indexSrc.indexOf("status-atendimento.js") !== -1 &&
     indexSrc.indexOf("status-atendimento.js") < indexSrc.indexOf("m15-nucleo.js") &&
     indexSrc.indexOf("status-atendimento.js") < indexSrc.indexOf("central-cadastros.js") &&
     indexSrc.indexOf("status-atendimento.js") < indexSrc.indexOf("crm-workspace.js"));

caso("Central usa o formatador (não reimplementa o mapa)",
     /window\.SoproStatus\.espirometria/.test(centralSrc) &&
     !/["']Espirometria realizada["']/.test(centralSrc));
caso("Núcleo usa o formatador nas listas e nos seletores de exame",
     /window\.SoproStatus\.opcoesEspirometria/.test(nucleoSrc) &&
     /status_exibicao/.test(nucleoSrc));
caso("edição de exame preserva status histórico fora do vocabulário atual",
     /function statusComAtual\(/.test(nucleoSrc) &&
     /if \(val\(f, "status"\) !== e\.status\)/.test(nucleoSrc));

// ── B) abas primárias da Central ───────────────────────────────────────────
console.log();
console.log("B) Central de Cadastros: uma aba primária Novo atendimento");

const tabsBloco = centralSrc.slice(centralSrc.indexOf("const TABS = ["),
                                   centralSrc.indexOf("const TAB_ALIASES"));
caso("existe exatamente uma aba primária \"Novo atendimento\"",
     (tabsBloco.match(/"Novo atendimento"/g) || []).length === 1 &&
     /\["atendimento", "Novo atendimento", "operacional"\]/.test(tabsBloco));
caso("não há mais aba primária Paciente",
     !/\["paciente",/.test(tabsBloco));
caso("não há mais aba primária Espirometria",
     !/\["espirometria",/.test(tabsBloco));
caso("não há mais aba primária Consulta",
     !/\["consulta",/.test(tabsBloco));
const chavesTabs = (tabsBloco.match(/\["([a-z0-9-]+)",/g) || [])
  .map((m) => m.slice(2, -2));
caso("abas primárias finais são exatamente as cinco previstas",
     JSON.stringify(chavesTabs) ===
     JSON.stringify(["lead", "atendimento", "clinica", "contato-b2b", "financeiro"]),
     `encontrado: ${JSON.stringify(chavesTabs)}`);
caso("os formulários antigos foram REMOVIDOS (sem implementação duplicada)",
     !/LOADERS\.paciente\s*=/.test(centralSrc) &&
     !/LOADERS\.espirometria\s*=/.test(centralSrc) &&
     !/LOADERS\.consulta\s*=/.test(centralSrc) &&
     /LOADERS\.atendimento\s*=/.test(centralSrc));

// ── C) cadastro de pessoa nova × cadastro só da pessoa (M23.1) ─────────────
console.log();
console.log("C) Nova pessoa tem verbo próprio; \"só a pessoa\" só existe dentro desse fluxo");

caso("botão do seletor de pessoa diz \"+ Cadastrar nova pessoa\" (não mais \"+ Nova pessoa\")",
     /\+ Cadastrar nova pessoa/.test(centralSrc) &&
     !/>\+ Nova pessoa</.test(centralSrc));
caso("\"cadastrar apenas a pessoa\" só é renderizado quando o chamador pede (opts.somentePessoaOpcao)",
     /opts\.somentePessoaOpcao \? `[\s\S]{0,200}?Cadastrar apenas a pessoa, sem criar exame ou consulta/
       .test(centralSrc) &&
     /id="\$\{prefix\}SoPessoa"/.test(centralSrc));
caso("o checkbox mora DENTRO da caixa de pessoa nova, não solto como ação flutuante",
     (() => {
       const boxStart = centralSrc.indexOf('id="${prefix}NovaBox"');
       const boxCheckbox = centralSrc.indexOf('id="${prefix}SoPessoa"');
       const boxEnd = centralSrc.indexOf("</div>`}", boxStart);
       return boxStart !== -1 && boxCheckbox > boxStart && boxCheckbox < boxEnd;
     })());
caso("só o fluxo de Novo atendimento pede a opção (Lead não a usa)",
     /personPickerHtml\("cadAtP", \{ somentePessoaOpcao: true \}\)/.test(centralSrc) &&
     /personPickerHtml\("cadLeadP"\)/.test(centralSrc));
caso("a opção só conta como ativa quando a pessoa é NOVA (picker.somentePessoaAtivo)",
     /picker\.somentePessoaAtivo = function \(\) \{[\s\S]{0,120}?return picker\.modoNova && !!\(soPessoaEl && soPessoaEl\.checked\)/
       .test(centralSrc));
caso("no modo \"somente pessoa\" nenhum atendimento/exame/consulta/lançamento é criado",
     /if \(picker\.somentePessoaAtivo\(\)\) \{[\s\S]{0,300}?somentePessoa: true/.test(centralSrc));
caso("modo \"somente pessoa\" esconde os passos 2 e 3 e troca o rótulo do botão",
     /const soPessoa = picker\.somentePessoaAtivo\(\);[\s\S]{0,200}?passo2\.hidden = soPessoa;[\s\S]{0,80}?passo3\.hidden = soPessoa;[\s\S]{0,120}?"Salvar pessoa" : "Salvar atendimento"/
       .test(centralSrc));
caso("busca de pessoa existente nunca aciona a criação (resolve só retorna a selecionada)",
     /if \(picker\.selected\) return Promise\.resolve\(picker\.selected\);/.test(centralSrc));

// ── D) tipos de atendimento ────────────────────────────────────────────────
console.log();
console.log("D) Passo 2 — os quatro tipos exatos");

[["espirometria_soprolife", "Espirometria SoproLife"],
 ["espirometria_pastore", "Espirometria Pastore"],
 ["consulta_soprolife", "Consulta SoproLife"],
 ["espirometria_consulta_soprolife", "Espirometria + Consulta SoproLife"],
].forEach(([chave, rotulo]) => {
  caso(`tipo "${rotulo}" presente`,
       centralSrc.includes(`["${chave}", "${rotulo}"`));
});
caso("não existe tipo de consulta Pastore",
     !/consulta_pastore/.test(centralSrc) && !/Consulta Pastore/.test(centralSrc));
caso("Pastore exige a unidade operacional antes de enviar",
     /Espirometria Pastore exige a unidade operacional/.test(centralSrc));
caso("parceiro/unidade só entram no payload no tipo Pastore",
     /if \(tipo === TIPO_PASTORE\) \{[\s\S]{0,400}?bloco\.partner_id = pastore\.partner\.id;/
       .test(centralSrc));
caso("resolução de Pastore é fail-closed quando há mais de um parceiro",
     /\/pastore\/configuracao-atendimento/.test(centralSrc) &&
     /len\(rows\) != 1/.test(pastoreServiceSrc) &&
     /Partner\.arquivado\.is_\(False\)/.test(pastoreServiceSrc));
caso("retorno da consulta nunca é assumido",
     /"sem_retorno", "sem retorno programado"/.test(centralSrc) &&
     /Nenhum retorno é assumido/.test(centralSrc));
caso("receita bruta da consulta é declarada como da SoproLife",
     /receita bruta da consulta é da SoproLife/i.test(centralSrc) &&
     /repasse ao\s+médico é uma obrigação financeira SEPARADA/i.test(
       centralSrc.replace(/\n\s*/g, " ")));
caso("nenhum valor monetário é inferido pelo formulário",
     /Nenhum valor é inferido/.test(centralSrc) &&
     /Em branco, nenhum lançamento é criado/.test(centralSrc));

// ── E) CRM de pacientes segue vivo e sem cadastro duplicado ────────────────
console.log();
console.log("E) CRM de pacientes preservado");

caso("workspace do CRM mantém lista, fila e linha do tempo",
     /crm\/pacientes/.test(wsSrc) && /timeline/.test(wsSrc) &&
     /filaAtiva/.test(wsSrc));
caso("CRM não reimplementa formulário de cadastro",
     !/\/pessoas["'`]\s*,\s*\{\s*method:\s*["']POST/.test(wsSrc) &&
     !/\/espirometrias["'`]\s*,\s*\{\s*method:\s*["']POST/.test(wsSrc));
caso("Central não reproduz lista de pacientes nem CRM de acompanhamento",
     !/crm\/pacientes/.test(centralSrc) && !/status_acompanhamento/.test(centralSrc));

// ── F) botões contextuais ──────────────────────────────────────────────────
console.log();
console.log("F) Botões contextuais abrem o fluxo único com o tipo certo");

caso("app.js: Pastore abre Novo atendimento com tipo espirometria_pastore",
     /SoproCentral\.open\("atendimento",\s*\{\s*tipo:\s*"espirometria_pastore"\s*\}\)/
       .test(appSrc));
caso("núcleo: + Nova espirometria / + Nova consulta abrem o fluxo único",
     /centralBtn\("atendimento",[\s\S]{0,200}?tipo: isExam \? "espirometria_soprolife" : "consulta_soprolife"/
       .test(nucleoSrc));
caso("núcleo: + Nova pessoa abre o modo somente paciente",
     /centralBtn\("atendimento", "\+ Nova pessoa \/ paciente",[\s\S]{0,120}?somente_paciente: true/
       .test(nucleoSrc));
caso("CRM: atalhos cobrem os quatro tipos, inclusive o combinado",
     /"espirometria_soprolife", "\+ Espirometria"/.test(wsSrc) &&
     /"espirometria_pastore", "\+ Espirometria Pastore"/.test(wsSrc) &&
     /"consulta_soprolife", "\+ Consulta"/.test(wsSrc) &&
     /"espirometria_consulta_soprolife", "\+ Espirometria \+ Consulta"/.test(wsSrc));
caso("aliases mantêm deep-links antigos vivos apontando para o fluxo único",
     /TAB_ALIASES = \{[\s\S]{0,400}?paciente:[\s\S]{0,120}?somente_paciente: true/
       .test(centralSrc) &&
     /espirometria: \{ tab: "atendimento", prefill: \{ tipo: "espirometria_soprolife" \} \}/
       .test(centralSrc) &&
     /consulta: \{ tab: "atendimento", prefill: \{ tipo: "consulta_soprolife" \} \}/
       .test(centralSrc));

// ── G) segurança: PII, rotas e Sheets ──────────────────────────────────────
console.log();
console.log("G) Segurança — sem PII em rota, sem escrita no Google Sheets");

caso("deep-link de paciente carrega só o código público",
     /prefill\.person_codigo = codigoPessoa;/.test(wsSrc) &&
     !/prefill\.nome|prefill\.telefone|prefill\.cpf/.test(wsSrc));
caso("nenhuma rota da Central concatena nome/telefone/CPF na URL",
     !/api\(`?["'`]?\/[a-z-]+\?[^`"']*\$\{[^}]*(nome|telefone|cpf|whatsapp)/i
       .test(centralSrc));
caso("Central nunca fala com script.google.com",
     !/script\.google\.com/.test(centralSrc) && !/script\.google\.com/.test(statusSrc));
caso("fluxo novo não usa nenhum endpoint de escrita em planilha",
     !/gviz|spreadsheets|sheets\.googleapis/.test(centralSrc));

// ── H) acessibilidade e responsividade ─────────────────────────────────────
console.log();
console.log("H) Acessibilidade e responsividade do novo passo a passo");

caso("grupo de tipos é um radiogroup rotulado",
     /role="radiogroup" aria-label="Tipo de atendimento"/.test(centralSrc));
caso("cada tipo é um radio real (navegável por teclado)",
     /<input type="radio" name="tipo"/.test(centralSrc));
caso("foco no cartão de tipo é visível",
     /\.cad-tipo:focus-within \{[\s\S]{0,120}?outline:/.test(cssSrc));
caso("passos e blocos têm títulos semânticos",
     /class="cad-passo-titulo"/.test(centralSrc) &&
     /class="cad-bloco-titulo"/.test(centralSrc));
caso("tipos empilham em telas estreitas",
     /@media \(max-width: 820px\)[\s\S]{0,400}?\.cad-tipos \{ grid-template-columns: 1fr; \}/
       .test(cssSrc));
caso("grade de tipos é fluida (sem largura fixa)",
     /\.cad-tipos \{[\s\S]{0,160}?minmax\(220px, 1fr\)/.test(cssSrc));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
