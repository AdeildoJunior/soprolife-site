#!/usr/bin/env node
// SoproLife — M21: login compatível com gerenciador de senhas, sessão
// persistente segura, limpeza do CRM e navegação de Automação CRM.
//
// Checagens estáticas e determinísticas (sem rede, sem DOM real):
//   A) atributos de gerenciador de senhas no formulário de login;
//   B) sessão por cookie de servidor, nada em storage do navegador;
//   C) CRM sem os quatro cards redundantes, começando pela operação real;
//   D) Automação CRM aparece UMA vez em Sistema, sem destino duplicado;
//   E) aliases antigos de rota resolvem com segurança;
//   F) atualização de Marketing distingue os cinco estados exigidos;
//   G) unidade systemd usa credencial durável e não ADC pessoal;
//   H) acessibilidade e ausência de largura fixa que estoure viewport.
//
// Uso:  node painel-soprolife/scripts/test-m21-auth-crm-nav.js
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
const nucleoSrc = ler("js", "m15-nucleo.js");
const wsSrc = ler("js", "crm-workspace.js");
const mfSrc = ler("js", "marketing-freshness.js");
const indexSrc = ler("index.html");
const styleCss = ler("css", "style.css");
const m15Css = ler("css", "m15.css");
const unitSrc = ler("systemd", "soprolife-update-data.service");
const m15UnitSrc = ler("systemd", "soprolife-m15-api.service");
const updateSh = ler("scripts", "update-local-data.sh");
const proxySrc = ler("scripts", "command-center-local-server.py");
const mktPy = ler("scripts", "read-marketing-seo-adc.py");
const authPy = ler("nucleo-m15", "app", "routers", "auth.py");
const marketingPy = ler("nucleo-m15", "app", "routers", "marketing.py");

// Bloco do formulário de login: do <form> até o fechamento do </form>.
const formIni = nucleoSrc.indexOf('id="m15LoginForm"');
const formFim = nucleoSrc.indexOf('"  </form>"', formIni);
const formBloco = formIni === -1 ? "" : nucleoSrc.slice(formIni - 200, formFim);

// ───────────── A) gerenciador de senhas ─────────────────────────────────────
console.log("A) Formulário de login compatível com gerenciador de senhas");

caso("o login é um <form> de verdade (não dois inputs soltos)",
     formIni !== -1 && /<form class="m15-login-grid" id="m15LoginForm"/.test(nucleoSrc));
caso("o envio acontece por submit do form (não por click handler)",
     /form\.addEventListener\("submit"/.test(nucleoSrc) &&
     !/getElementById\("m15Entrar"\)\.addEventListener\("click"/.test(nucleoSrc));
caso("o botão Entrar é type=submit",
     /id="m15Entrar" type="submit"/.test(formBloco));
caso("e-mail: type=email, name=email, autocomplete=username",
     /type="email" id="m15Email" name="email" autocomplete="username"/.test(formBloco));
caso("senha: type=password, name=password, autocomplete=current-password",
     /type="password" id="m15Senha" name="password" autocomplete="current-password"/
       .test(formBloco));
caso("nenhum autocomplete=off sobrou no par de credenciais",
     !/id="m15Email"[^>]*autocomplete="off"/.test(nucleoSrc) &&
     !/id="m15Senha"[^>]*autocomplete="off"/.test(nucleoSrc));
caso("não há input de senha escondido duplicando o login",
     !/type="hidden"[^>]*password/i.test(nucleoSrc));
caso("o campo de token da CLI fica FORA do form de login (não confunde a heurística)",
     nucleoSrc.indexOf('id="m15Token"') > formFim);
caso("o campo de token da CLI não é reconhecido como senha salvável",
     /id="m15Token" autocomplete="off"/.test(nucleoSrc));
caso("a senha NÃO é limpa antes da resposta de sucesso",
     !/document\.getElementById\("m15Senha"\)\.value = "";/.test(nucleoSrc));
caso("a senha é limpa depois que a requisição termina (sucesso ou falha)",
     /\.finally\(function \(\) \{[\s\S]{0,240}campoSenha\.value = ""/.test(nucleoSrc));
caso("o formulário sai do DOM só depois do sucesso confirmado",
     /Sucesso confirmado[\s\S]{0,200}renderAuthArea/.test(nucleoSrc));
caso("a senha nunca entra no estado da aplicação",
     !/state\.(senha|password)\b/.test(nucleoSrc));
caso("labels preservados e associados por for/id",
     /<label class="m15-field" for="m15Email">/.test(formBloco) &&
     /<label class="m15-field" for="m15Senha">/.test(formBloco));

// ───────────── B) sessão persistente segura ─────────────────────────────────
console.log();
console.log("B) Sessão persistente por cookie de servidor");

caso('existe a opção "Manter conectado neste dispositivo"',
     /id="m15Manter"/.test(nucleoSrc) &&
     nucleoSrc.indexOf("Manter conectado neste dispositivo") !== -1);
caso("a escolha é enviada ao servidor como manter_conectado",
     /manter_conectado: manter/.test(nucleoSrc));
caso("a sessão é restaurada ao carregar a página (GET /auth/sessao)",
     /api\("\/auth\/sessao"\)/.test(nucleoSrc) &&
     /restaurarSessao\(\)\.then/.test(nucleoSrc));
caso("Sair chama o logout do servidor (revogação real)",
     /api\("\/auth\/logout", \{ method: "POST" \}\)/.test(nucleoSrc));
caso("o token bearer devolvido no login é ignorado pelo navegador",
     /state\.token = "";\s*\n\s*state\.sessao = true;/.test(nucleoSrc));
caso("NENHUM token vai para localStorage/sessionStorage",
     !/localStorage\.setItem/.test(nucleoSrc) &&
     !/sessionStorage\.(set|get)Item/.test(nucleoSrc));
caso("o token legado em localStorage continua sendo apagado na subida",
     /localStorage\.removeItem\("soproM15Token"\)/.test(nucleoSrc));
caso("as requisições enviam o cookie de mesma origem",
     /options\.credentials = "same-origin"/.test(nucleoSrc));
caso("Authorization só é enviado quando existe token de fato",
     /if \(state\.token\) base\.Authorization = "Bearer " \+ state\.token;/.test(nucleoSrc));
caso("CSRF é enviado nos métodos que mudam estado",
     /base\["X-CSRF-Token"\] = state\.csrf;/.test(nucleoSrc) &&
     /STATE_CHANGING\[metodo\]/.test(nucleoSrc));
caso("sessão expirada mostra mensagem clara e cai fechado",
     nucleoSrc.indexOf("Sua sessão expirou. Entre novamente.") !== -1);
caso("a guarda de contexto seguro (HTTPS/loopback) continua antes de tudo",
     /if \(!state\.access\.secure\) return Promise\.resolve\(false\)/.test(nucleoSrc));
caso("o proxy repassa o cookie do painel e o cabeçalho CSRF",
     proxySrc.indexOf('"x-csrf-token": "X-CSRF-Token"') !== -1 &&
     /headers\["Cookie"\] = cookie/.test(proxySrc));
caso("o proxy repassa Set-Cookie sem reescrever atributos",
     /if _allowed_set_cookie\(cookie_header\):/.test(proxySrc) &&
     /self\.send_header\("Set-Cookie", cookie_header\)/.test(proxySrc));
caso("logout por cookie passa pela validação de autenticação/CSRF",
     /def logout\([\s\S]{0,300}_authenticated_user: User = Depends\(get_current_user\)/
       .test(authPy));
caso("consumidores compartilhados seguem usando hasToken() (contrato preservado)",
     /hasToken: autenticado/.test(nucleoSrc) &&
     /c\.hasToken\(\)/.test(wsSrc));

// ───────────── C) CRM sem cards redundantes ─────────────────────────────────
console.log();
console.log("C) CRM começa pela operação real de pacientes");

const cardsRemovidos = [
  ["Central de Cadastros", 'title: "Central de Cadastros"'],
  ["Clínicas e Parceiros", 'title: "Clínicas e Parceiros"'],
  ["Pacientes e Acompanhamento", 'title: "Pacientes e Acompanhamento"'],
  ["Automações CRM", 'title: "Automações CRM"'],
];
cardsRemovidos.forEach(([nome, marca]) => {
  caso(`card "${nome}" removido do CRM`, appSrc.indexOf(marca) === -1);
});
caso("a função que montava os cards não existe mais",
     !/function crmModuleCard\(/.test(appSrc));
caso("o grid de cards não é mais renderizado",
     appSrc.indexOf('class="crm-hub-grid"') === -1);
caso("os handlers de card (data-crm-view) foram removidos",
     appSrc.indexOf("[data-crm-view]") === -1 &&
     appSrc.indexOf("card.dataset.crmView") === -1);
caso("o CSS dos cards foi removido (não apenas escondido)",
     styleCss.indexOf(".crm-module-card") === -1 &&
     styleCss.indexOf(".crm-hub-grid") === -1 &&
     styleCss.indexOf(".crm-module-stat") === -1 &&
     styleCss.indexOf(".crm-module-cta") === -1);
caso("nada foi escondido com display:none no lugar de remover",
     !/crm-module[^{]*\{[^}]*display:\s*none/.test(styleCss));
caso("o CRM monta o workspace canônico como primeiro conteúdo",
     appSrc.indexOf('<div id="crmWorkspace"></div>') !== -1 &&
     /SoproCrm\.abrir\(mount, null, \{ landing: true \}\)/.test(appSrc));
caso("o workspace re-renderiza só o próprio container",
     /querySelector\("#crmWorkspace"\) \|\| document\.querySelector\("#crmView"\)/
       .test(wsSrc));
caso("no modo landing o workspace não repete título nem botão voltar",
     /state\.landing\s*\n?\s*\? ""/.test(wsSrc));

// KPIs exigidos, servidos pelo workspace a partir da API real.
[
  ["total_pacientes", "Total de pacientes"],
  ["contatos_hoje", "Contatos hoje"],
  ["contatos_atrasados", "Contatos atrasados"],
  ["proximos_7", "Próximos 7 dias"],
  ["proximos_30", "Próximos 30 dias"],
  ["sem_telefone", "Sem telefone válido"],
  ["followups_concluidos_mes", "Follow-ups concluídos no mês"],
  ["pacientes_reativados", "Pacientes reativados"],
].forEach(([chave, rotulo]) => {
  caso(`KPI "${rotulo}" presente na visão inicial do CRM`,
       wsSrc.indexOf(`["${chave}", "${rotulo}"`) !== -1);
});
caso("os KPIs vêm da API real do Núcleo (nenhum valor de demonstração)",
     /c\.api\("\/crm\/kpis"\)/.test(wsSrc));
caso("filas de contato, histórico e indicadores seguem disponíveis",
     ["contatos", "historico", "indicadores"].every(
       (v) => wsSrc.indexOf(`["${v}", `) !== -1));
caso("o banner do parceiro ativo (Pastore) foi mantido compacto",
     appSrc.indexOf("marco-banner-parceiro") !== -1 &&
     appSrc.indexOf("${marcoHtml}") !== -1);
caso("nenhum formulário de cadastro foi introduzido no CRM",
     wsSrc.indexOf("window.SoproCentral.open(tab,") !== -1 &&
     !/api\("\/pessoas", \{\s*method: "POST"/.test(wsSrc));

// ───────────── D) Automação CRM na sidebar ──────────────────────────────────
console.log();
console.log("D) Automação CRM: um único destino em Sistema");

const navItens = [...indexSrc.matchAll(
  /<button class="nav-item"[^>]*data-section="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g
)].map((m) => ({ secao: m[1], rotulo: m[2].replace(/<[^>]*>/g, " ").trim() }));

const automacaoCrm = navItens.filter((i) => i.secao === "automacoes-crm");
caso("Automação CRM aparece exatamente UMA vez na sidebar",
     automacaoCrm.length === 1, JSON.stringify(automacaoCrm));
caso('o rótulo é exatamente "Automação CRM"',
     automacaoCrm.length === 1 && automacaoCrm[0].rotulo === "Automação CRM",
     automacaoCrm.map((i) => i.rotulo).join("|"));
caso("existe a seção #automacoes-crm no HTML",
     /<section class="section" id="automacoes-crm">/.test(indexSrc));
caso("nenhum item de sidebar continua rotulado só 'Automações'",
     !navItens.some((i) => i.rotulo === "Automações"));
caso("a página técnica mais ampla foi preservada com rótulo próprio",
     navItens.some((i) => i.secao === "automacoes" && i.rotulo === "Fontes de dados") &&
     /<section class="section" id="automacoes">/.test(indexSrc));
caso("nenhum data-section de sidebar aponta duas vezes para o mesmo destino",
     new Set(navItens.map((i) => i.secao)).size === navItens.length,
     JSON.stringify(navItens.map((i) => i.secao)));
caso("não existe segunda implementação de automação de CRM",
     (appSrc.match(/function renderCrmAutomacoes\(/g) || []).length === 1);
caso("a página de Automação CRM traz as regras exigidas",
     ["Acompanhamento após atendimento", "Lembrete de contato a realizar",
      "Reativação de paciente", "WhatsApp assistido"]
       .every((t) => appSrc.indexOf(t) !== -1));
caso("a página mostra o status do que está pendente de automação",
     appSrc.indexOf("Pendências de automação agora") !== -1 &&
     /m15\.api\("\/crm\/kpis"\)/.test(appSrc));
caso("envio automático de WhatsApp continua DESLIGADO",
     appSrc.indexOf("Envio automático DESLIGADO") !== -1 &&
     appSrc.indexOf("Envio automático não é habilitado nesta etapa") !== -1);
caso("a página não é mais subview (sem botão ← CRM)",
     !/id="crmBackBtn"[\s\S]{0,600}renderCrmAutomacoes/.test(appSrc) &&
     appSrc.indexOf("esta página é destino de sidebar, não subview") !== -1);

// ───────────── E) aliases antigos ───────────────────────────────────────────
console.log();
console.log("E) Deep-links antigos resolvem com segurança");

caso('a rota antiga "automacoes-crm" do CRM leva ao novo destino',
     /case "automacoes-crm":[\s\S]{0,200}irParaSecao\("automacoes-crm"\)/.test(appSrc));
["pacientes", "followup-detalhe", "acompanhamento-m15", "pacientes-acompanhamento"]
  .forEach((rota) => {
    caso(`rota antiga "${rota}" continua resolvendo`,
         appSrc.indexOf(`case "${rota}":`) !== -1);
  });
caso("Central de Cadastros continua acessível pela sidebar",
     navItens.some((i) => i.secao === "central-cadastros"));
caso("Parcerias continua o destino canônico de parceiros",
     navItens.some((i) => i.secao === "parcerias-pastore") &&
     /id="parceriaClinicasBtn"/.test(indexSrc));
caso("a lista B2B de clínicas segue com implementação única",
     (appSrc.match(/function renderCrmClinicas\(/g) || []).length === 1 &&
     /state\.crmView = "clinicas";/.test(appSrc));

// ───────────── F) estados de Marketing ─────────────────────────────────────
console.log();
console.log("F) Marketing distingue os cinco estados exigidos");

[
  ["Atualizado", "MF_FRESH"],
  ["Atualizando", "MF_REFRESHING"],
  ["Dados antigos", "MF_STALE"],
  ["Credencial/configuração pendente", "MF_CREDENTIAL"],
  ["Falha temporária", "MF_ERROR"],
].forEach(([rotulo, constante]) => {
  caso(`estado "${rotulo}" existe (${constante})`,
       mfSrc.indexOf(`label: "${rotulo}"`) !== -1 &&
       mfSrc.indexOf(`const ${constante} =`) !== -1);
});
caso("o selo nunca acumula dois estados contraditórios",
     /MKT_STATE_CLASSES\.forEach\(\(c\) => label\.classList\.remove\(c\)\)/.test(appSrc));
caso("a última atualização com SUCESSO é exibida",
     appSrc.indexOf("Última atualização com sucesso:") !== -1);
caso("a próxima atualização agendada é exibida quando dá",
     appSrc.indexOf("Próxima atualização:") !== -1 &&
     /mfProximaAtualizacao/.test(mfSrc));
caso('existe ação "Atualizar dados" que fala com o servidor',
     /id="mktRefreshBtn"/.test(indexSrc) &&
     /MKT_REFRESH_URL = "\/marketing\/refresh"/.test(appSrc) &&
     /m15\.api\(MKT_REFRESH_URL, \{ method: "POST" \}\)/.test(appSrc));
caso("a ação manual exige sessão, RBAC e CSRF do Núcleo",
     appSrc.indexOf("if (!m15 || !m15.hasToken())") !== -1 &&
     /Depends\(require_role\(ROLE_OPERACIONAL\)\)/.test(marketingPy));
caso("recarregar a página não se disfarça de atualização de fonte",
     appSrc.indexOf("Ele nunca busca dado novo nas fontes") !== -1 &&
     indexSrc.indexOf("Não busca dados novos nas fontes externas.") !== -1);
caso("o navegador não executa script nem credencial na atualização",
     !/subprocess|systemctl/.test(proxySrc));
caso("o CSS dos dois estados novos existe",
     styleCss.indexOf(".mf-refreshing") !== -1 &&
     styleCss.indexOf(".mf-credential") !== -1);

// ───────────── G) unidade systemd e credencial durável ─────────────────────
console.log();
console.log("G) Serviço agendado usa credencial durável de leitura");

caso("a unit aponta a credencial de conta de serviço explicitamente",
     /SOPROLIFE_MARKETING_CREDENTIALS=\/opt\/soprolife\/secrets\//.test(unitSrc));
caso("a unit exige conta de serviço (sem volta silenciosa ao ADC pessoal)",
     /SOPROLIFE_MARKETING_REQUIRE_SERVICE_ACCOUNT=1/.test(unitSrc));
caso("a unit não contém chave, token ou senha",
     !/private_key|BEGIN [A-Z ]*PRIVATE KEY|refresh_token|password=/i.test(unitSrc));
const unitDiretivas = unitSrc.split("\n")
  .filter((l) => !l.trimStart().startsWith("#")).join("\n");
caso("a unit reflete o usuário real de produção (não root)",
     /^User=soprolife$/m.test(unitDiretivas) && !/^User=root$/m.test(unitDiretivas));
caso("a unit tem teto de tempo por execução",
     /TimeoutStartSec=\d+/.test(unitSrc));
caso("o script tem proteção de concorrência (flock)",
     /flock -n -E 99/.test(updateSh) &&
     updateSh.indexOf("outra atualização já está em execução") !== -1);
caso("o script consome o pedido manual enfileirado",
     /rm -f -- "\$_MKT_QUEUE"/.test(updateSh));
caso("API e timer compartilham a mesma fila privada explicitamente",
     /M15_MARKETING_REFRESH_QUEUE=\/opt\/soprolife\/soprolife-site\/painel-soprolife\/nucleo-m15\/var\//
       .test(m15UnitSrc) &&
     /SOPROLIFE_MARKETING_REFRESH_QUEUE=\/opt\/soprolife\/soprolife-site\/painel-soprolife\/nucleo-m15\/var\//
       .test(unitSrc));
caso("a credencial durável tem precedência sobre o ADC pessoal",
     /if \[ -f "\$_MARKETING_CREDENTIAL" \]/.test(updateSh));
caso("falha de atualização preserva o último snapshot válido",
     updateSh.indexOf("último snapshot válido preservado") !== -1 &&
     /escrever_json_atomico/.test(mktPy));
caso("o conector expõe diagnóstico de credencial sem rede",
     /--credential-check/.test(mktPy));
caso("nenhum caminho de credencial é enviado ao navegador",
     !/opt\/soprolife\/secrets/.test(appSrc) &&
     !/opt\/soprolife\/secrets/.test(mfSrc));

// ───────────── H) acessibilidade e viewport ────────────────────────────────
console.log();
console.log("H) Acessibilidade e ausência de estouro horizontal");

caso("o checkbox de manter conectado tem foco visível",
     m15Css.indexOf(".m15-login-manter input[type=\"checkbox\"]:focus-visible") !== -1);
caso("o botão Atualizar dados tem foco visível",
     /\.mkt-refresh-btn:focus-visible/.test(styleCss));
caso("a mensagem de atualização é anunciada a leitores de tela",
     /id="mktRefreshMsg"[^>]*role="status"[^>]*aria-live="polite"/.test(indexSrc));
caso("os novos botões são type=button (não submetem formulário)",
     /id="mktRefreshBtn" class="mkt-refresh-btn"/.test(indexSrc) &&
     /<button type="button" id="mktRefreshBtn"/.test(indexSrc) &&
     /<button type="button" id="parceriaClinicasBtn"/.test(indexSrc));
caso("o grid de regras é fluido (sem largura fixa que estoure em 420px)",
     /\.auto-regras \{[^}]*minmax\(min\(100%, 280px\), 1fr\)/.test(styleCss));
caso("os cards de regra não têm largura mínima rígida",
     /\.auto-regra \{[^}]*min-width: 0;/.test(styleCss));
caso("a linha longa de datas de Marketing continua podendo quebrar",
     /\.mkt-header-right \.mkt-period \{[^}]*overflow-wrap: anywhere/.test(styleCss));
caso("as abas do workspace continuam expondo aria-selected",
     /role="tab" aria-selected="/.test(wsSrc));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
