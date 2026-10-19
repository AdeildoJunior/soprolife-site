#!/usr/bin/env node
/* Fase 1.3A — Centro de Comando Life: contrato da casca de DUAS marcas.
 *
 * Checagens estáticas e determinísticas (sem rede, sem DOM real, sem venv).
 * O comportamento em navegador — troca de marca, autorização por papel,
 * tentativa de forçar EEGLife, e a prova de que a casca EEGLife não busca dado
 * SoproLife — é provado em `test-fase1-3-life-browser-e2e.js`, com Chrome
 * headless e duas sessões reais (admin e não-admin).
 *
 * Blocos:
 *   A) duas marcas, e nenhum vestígio de "Life Geral";
 *   B) padrão SoproLife e queda de valor inválido/legado;
 *   C) autorização do contexto vem da sessão, não do localStorage;
 *   D) a casca não fala com o backend;
 *   E) o painel SoproLife continua intacto;
 *   F) a EEGLife segue o padrão de produto do painel SoproLife;
 *   G) EEGLife sem dado, sem número inventado e sem reuso do respiratório;
 *   H) identidade visual — paleta real e dourado como acento;
 *   I) acessibilidade;
 *   J) responsividade;
 *   K) login e autenticação intocados.
 *
 * Uso:  node painel-soprolife/scripts/test-fase1-3-life-command-center.js
 * Exit: 0 = todos passaram | 1 = houve falha.
 */
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

const indexHtml = ler("index.html");
const shellJs = ler("js", "life-command-center.js");
const appJs = ler("js", "app.js");
const lifeCss = ler("css", "life-command-center.css");
const loginHtml = ler("login.html");
const bootGate = ler("js", "boot-gate.js");
const securityPy = ler("nucleo-m15", "app", "security.py");
const authPy = ler("nucleo-m15", "app", "routers", "auth.py");
const arquitetura = ler("..", "docs", "CENTRO_COMANDO_LIFE_ARCHITECTURE.md");
const eegOperacao = ler("js", "eeg-operacao.js");
const eegRouter = ler("nucleo-m15", "app", "routers", "eeg.py");
const eegModalidades = ler("nucleo-m15", "app", "services", "eeg_modalidades.py");
const eegModels = ler("nucleo-m15", "app", "models.py");

// Python sem docstring nem comentário. Necessário porque estes arquivos
// explicam em prosa justamente o que NÃO fazem ("não há coluna de blob",
// "preço e repasse não são desta fase") — e a explicação derrubaria a
// checagem que procura a ausência dessas coisas no código.
const semComentariosPy = (src) => src
  .replace(/"""[\s\S]*?"""/g, "")
  .replace(/^\s*#.*$/gm, "")
  .replace(/\s#.*$/gm, "");
const eegRouterCodigo = semComentariosPy(eegRouter);
const eegModelsCodigo = semComentariosPy(eegModels);

// Só o código: os comentários desta casca explicam o que ela NÃO faz ("não
// envia nada ao backend", "Life Geral foi removido") e derrubariam as
// checagens que procuram justamente a ausência dessas coisas.
const semComentariosJs = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");
const shellCodigo = semComentariosJs(shellJs);
const eegOperacaoCodigo = semComentariosJs(eegOperacao);

function painel(id) {
  const ini = indexHtml.indexOf(`id="${id}"`);
  const fim = indexHtml.indexOf(`<!-- /${id} -->`, ini);
  return ini === -1 || fim === -1 ? "" : indexHtml.slice(ini, fim);
}
const painelSopro = painel("lifePanelSoproLife");
const painelEeg = painel("lifePanelEegLife");

function textoVisivel(html) {
  return html.replace(/<!--[\s\S]*?-->/g, " ").replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ").trim();
}

const MARCAS = ["soprolife", "eeglife"];

// ───────────── A) duas marcas, nenhum Life Geral ──────────────────────────
console.log();
console.log("A) Duas marcas no seletor e nenhum vestígio de Life Geral");

caso("as duas marcas aparecem no seletor",
     MARCAS.every((c) => indexHtml.includes(`data-life-context="${c}"`)));
caso("o seletor tem exatamente duas abas",
     (indexHtml.match(/data-life-context="/g) || []).length === 2,
     String((indexHtml.match(/data-life-context="/g) || []).length));
caso("ambas são declaradas marca",
     MARCAS.every((c) => new RegExp(`data-life-context="${c}"[^>]*data-life-kind="marca"`).test(indexHtml)));
caso("a casca conhece só as duas marcas",
     /ORDEM\s*=\s*\["soprolife",\s*"eeglife"\]/.test(shellCodigo) &&
     (shellCodigo.match(/tipo:\s*"marca"/g) || []).length === 2);

// "Life Geral" foi revogado: não pode sobrar em tela, rota, estado ou valor.
caso("nenhuma referência a life_geral no HTML",
     !/life_geral|lifePanelLifeGeral|lifeTabLifeGeral/.test(indexHtml));
caso("nenhuma referência a Life Geral no texto visível da página",
     !/Life Geral/i.test(textoVisivel(indexHtml)));
caso("nenhuma referência a life_geral no código da casca",
     !/life_geral/.test(shellCodigo));
caso("nenhum contexto consolidado declarado",
     !/tipo:\s*"contexto"/.test(shellCodigo) && !/"consolidado"/.test(shellCodigo));
caso("nenhum estilo remanescente de Life Geral",
     !/\.lg-|lifePanelLifeGeral/.test(lifeCss));
caso("o título do topo continua Centro de Comando Life",
     /<strong>Centro de Comando Life<\/strong>/.test(indexHtml));
caso("o subtítulo mostra só a marca ativa",
     /Contexto: <b>/.test(shellCodigo) && !/· operação ativa|visão consolidada/.test(shellCodigo));
caso("a documentação de arquitetura reflete duas marcas sem Life Geral no seletor",
     /duas marcas/i.test(arquitetura) &&
     !/\bLife Geral\b(?![^.]*remov)/i.test(arquitetura.replace(/Life Geral/g, (m, o, s) =>
       /remov|revog|deixou|não é|nao e/i.test(s.slice(Math.max(0, o - 180), o + 180)) ? "X" : m)));

// ───────────── B) padrão e queda de valor ─────────────────────────────────
console.log();
console.log("B) SoproLife é o padrão; valor inválido ou legado cai nele");

caso("o padrão declarado é soprolife",
     /PADRAO\s*=\s*"soprolife"/.test(shellCodigo));
caso("o HTML já nasce com SoproLife marcado",
     /id="lifeTabSoproLife"[\s\S]{0,300}?aria-selected="true"/.test(indexHtml));
caso("valor salvo só é aceito se existir E for permitido",
     /return permitido\(bruto\) \? bruto : PADRAO;/.test(shellCodigo));
caso("o valor legado life_geral cai no padrão sem caso especial",
     !/life_geral/.test(shellCodigo) &&
     /function permitido\(id\)[\s\S]{0,160}?valido\(id\) && permitidos\.indexOf\(id\) !== -1/.test(shellCodigo));
caso("storage indisponível também cai no padrão (janela privada)",
     /catch\s*\(e\)\s*\{[\s\S]{0,120}?return PADRAO;/.test(shellCodigo));
caso("definir contexto não permitido cai no padrão",
     /function definir\(id\)\s*\{[\s\S]{0,200}?if \(!permitido\(id\)\)[\s\S]{0,200}?id = PADRAO;/.test(shellCodigo));

// ───────────── C) autorização pela sessão ─────────────────────────────────
console.log();
console.log("C) Quem entra na EEGLife é decidido pela sessão, não pelo navegador");

caso("o papel exigido para a EEGLife é o admin do sistema real",
     /PAPEL_EEG\s*=\s*"admin"/.test(shellCodigo) &&
     /ROLE_ADMIN = "admin"/.test(securityPy));
caso("nenhum nome de pessoa foi usado como regra",
     !/\bana\b/i.test(shellCodigo));
caso("nenhum papel novo foi inventado",
     (shellCodigo.match(/"(admin|gestor|operacional|leitura|medico)"/g) || [])
       .every((p) => securityPy.includes(`= ${p}`) || securityPy.includes(`${p}`)));
caso("os papéis vêm de papeis_efetivos da sessão",
     /identidade\.papeis_efetivos/.test(shellCodigo) &&
     /"papeis_efetivos": sorted\(user_effective_roles\(user\)\)/.test(authPy));
caso("a decisão usa o mesmo gate de papel do painel (boot-gate / auth/me)",
     /window\.SoproBootGate/.test(shellCodigo) &&
     /gate\.pronto\.then\(decidirEaplicar\)/.test(shellCodigo) &&
     /auth\/me/.test(bootGate));
caso("sem identidade, a lista de permitidos é só SoproLife (fail-closed)",
     /function calcularPermitidos\(identidade\)[\s\S]{0,320}?var lista = \[PADRAO\];/.test(shellCodigo) &&
     /decidirEaplicar\(null\)/.test(shellCodigo));
caso("admin NÃO implica medico no sistema de papéis",
     /ROLE_ADMIN: set\(ADMINISTRATIVE_ROLES\)/.test(securityPy) &&
     (() => {
       const i = securityPy.indexOf("ADMINISTRATIVE_ROLES = [");
       const lista = securityPy.slice(i, securityPy.indexOf("]", i));
       return !/ROLE_MEDICO/.test(lista);
     })() &&
     /ROLE_MEDICO: \{ROLE_MEDICO\}/.test(securityPy));
// Esconder não basta: a marca não autorizada sai da árvore.
caso("a marca não autorizada é REMOVIDA do DOM, não escondida",
     /function podarMarcasNaoPermitidas[\s\S]{0,420}?aba\.remove\(\)[\s\S]{0,160}?painel\.remove\(\)/.test(shellCodigo));
caso("com uma marca só, o seletor inteiro é removido",
     /querySelectorAll\("\[data-life-context\]"\)\.length < 2[\s\S]{0,160}?seletor\.remove\(\)/.test(shellCodigo));
caso("o contexto é aplicado SÓ depois da resposta do servidor",
     /function ligar\(\)[\s\S]{0,400}?gate\.pronto\.then\(decidirEaplicar\)/.test(shellCodigo) &&
     /function decidirEaplicar\(identidade\)[\s\S]{0,260}?permitidos = calcularPermitidos\(identidade\);/.test(shellCodigo));
caso("o localStorage é declarado preferência, nunca permissão",
     /não é autorização|nunca\s+(\*\s+)?concede acesso/i.test(shellJs) &&
     /preferência entre contextos que a sessão já\s+(\*\s+)?pode acessar/i.test(shellJs));
caso("o teclado só circula entre as marcas permitidas",
     /var visiveis = ORDEM\.filter\(permitido\);/.test(shellCodigo) &&
     /if \(visiveis\.length < 2\) return;/.test(shellCodigo));

// ───────────── D) a casca não fala com o backend ──────────────────────────
console.log();
console.log("D) A casca é visual: não consulta o servidor nem inventa marca na rota");

caso("a casca não faz fetch",
     !/\bfetch\s*\(/.test(shellCodigo));
caso("a casca não faz XHR, EventSource, WebSocket nem beacon",
     !/XMLHttpRequest|EventSource|WebSocket|navigator\.sendBeacon/.test(shellCodigo));
caso("a casca não monta URL de API",
     !/\/api\/|\/painel-soprolife\/api/.test(shellCodigo));
caso("nenhum parâmetro de marca é construído",
     !/brand/i.test(shellCodigo) && !/[?&](brand|marca)=/i.test(indexHtml));
caso("o contexto não é lido da URL (não há como forçar por link)",
     !/location\.(search|hash|href)|URLSearchParams/.test(shellCodigo));

// ───────────── E) painel SoproLife intacto ────────────────────────────────
console.log();
console.log("E) O painel SoproLife continua inteiro e no mesmo lugar");

caso("a app-shell original segue dentro do contexto SoproLife",
     /<div class="app-shell">/.test(painelSopro) && painelSopro.includes('class="sidebar"'));
const SECOES_SOPRO = [
  "overview", "central-cadastros", "crm", "leads", "tarefas", "marketing",
  "financeiro", "fiscal", "parcerias-pastore", "custos-investimentos",
  "documentos", "automacoes-crm", "automacoes", "lancamentos",
];
caso("todos os módulos da sidebar SoproLife continuam presentes",
     SECOES_SOPRO.every((s) => painelSopro.includes(`data-section="${s}"`)),
     SECOES_SOPRO.filter((s) => !painelSopro.includes(`data-section="${s}"`)).join(","));
caso("o topbar e as ações do painel não mudaram de lugar",
     painelSopro.includes('id="globalSearch"') && painelSopro.includes('id="refreshBtn"') &&
     painelSopro.includes('id="topbarSair"'));
caso("a identidade azul/teal do SoproLife não foi redesenhada",
     !/^\.sidebar\s*\{/m.test(lifeCss) && !/^\.topbar\s*\{/m.test(lifeCss) &&
     !/^\.app-shell\s*\{/m.test(lifeCss));
caso("o tema dourado fica confinado ao painel da EEGLife",
     (lifeCss.match(/#lifePanelEegLife/g) || []).length >= 10 &&
     !/^\.panel\s*\{/m.test(lifeCss) && !/^\.stat-card\s*\{/m.test(lifeCss));
caso("a única sobreposição no painel antigo é o apoio da sidebar sticky",
     /body\.life-cc \.sidebar \{[^}]*top: var\(--life-masthead-h\)/.test(lifeCss));
caso("essa sobreposição não vale no mobile (onde a sidebar já é estática)",
     (() => {
       const i = lifeCss.indexOf("body.life-cc .sidebar");
       const mq = lifeCss.lastIndexOf("@media", i);
       return mq !== -1 && /min-width: 821px/.test(lifeCss.slice(mq, i));
     })());

// ───────────── F) EEGLife segue o padrão de produto ───────────────────────
console.log();
console.log("F) A EEGLife usa o mesmo padrão de produto do painel SoproLife");

caso("a EEGLife usa a mesma casca de duas colunas",
     /<div class="app-shell">/.test(painelEeg) && painelEeg.includes('class="sidebar"') &&
     painelEeg.includes('class="main"'));
caso("usa o mesmo topbar, com marca e ações",
     /<header class="topbar">/.test(painelEeg) && /class="topbar-brand"/.test(painelEeg) &&
     /class="top-actions"/.test(painelEeg));
caso("usa marca, rodapé e avatar da sidebar como o painel SoproLife",
     /class="brand"/.test(painelEeg) && /class="sidebar-footer"/.test(painelEeg) &&
     /class="sidebar-user"/.test(painelEeg));
const COMPONENTES = ["section-title", "panel", "panel-header", "stat-card",
                     "table-wrap", "empty-state", "grid-two", "safe-label", "eyebrow"];
caso("as telas são feitas dos componentes já existentes do produto",
     COMPONENTES.every((c) => shellCodigo.includes(c)),
     COMPONENTES.filter((c) => !shellCodigo.includes(c)).join(","));
// Quatro classes que o app.js consulta GLOBALMENTE. Reaproveitá-las faria o
// painel SoproLife mexer no DOM da EEGLife.
caso("a EEGLife evita as classes que o app.js consulta globalmente",
     !/class="section"/.test(painelEeg) && !/class="nav-item/.test(painelEeg) &&
     !/parceria-|ci-tab|ci-pane/.test(painelEeg) && !/data-section=/.test(painelEeg));
caso("a sidebar e as telas são renderizadas da configuração",
     /id="eegNavList"[^>]*><\/nav>/.test(painelEeg) &&
     /id="eegConteudo"><\/div>/.test(painelEeg) &&
     /function renderCascaEeg/.test(shellCodigo));
caso("o tema é aplicado redefinindo tokens, não recriando componentes",
     /#lifePanelEegLife \{[\s\S]{0,900}?--navy: #222c34;[\s\S]{0,900}?--teal: #c48a32;/.test(lifeCss));

const GRUPOS = ["Operação", "Clínico EEG", "Crescimento", "Gestão", "Sistema"];
caso("os cinco grupos de módulos existem",
     GRUPOS.every((g) => shellCodigo.includes(`grupo: "${g}"`)),
     GRUPOS.filter((g) => !shellCodigo.includes(`grupo: "${g}"`)).join(","));
const MODULOS = ["Painel Geral", "Central de Cadastros", "CRM", "Leads e Agendamentos",
  "Tarefas", "Exames e traçados", "Laudos de EEG", "Marketing & SEO", "Financeiro",
  "Fiscal", "Parceiros", "Custos & Investimentos", "Documentos", "Fontes de dados",
  "Últimos lançamentos", "Núcleo administrativo"];
caso("os dezesseis módulos previstos estão declarados",
     MODULOS.every((m) => shellCodigo.includes(`"${m}"`)),
     MODULOS.filter((m) => !shellCodigo.includes(`"${m}"`)).join(" | "));

// ───────────── G) EEGLife sem dado e sem número ───────────────────────────
console.log();
console.log("G) EEGLife sem dado, sem número inventado e sem reuso do respiratório");

caso("abrir módulo da EEGLife não dispara requisição",
     /function abrirModuloEeg/.test(shellCodigo) &&
     !/fetch|XMLHttpRequest/.test(shellCodigo.slice(
       shellCodigo.indexOf("function abrirModuloEeg"),
       shellCodigo.indexOf("function aplicar"))));
caso("o painel estático da EEGLife não traz número algum",
     !/\d/.test(textoVisivel(painelEeg)),
     (textoVisivel(painelEeg).match(/[^\s]*\d[^\s]*/g) || []).slice(0, 4).join(" "));
caso("os indicadores nascem com travessão, nunca com zero",
     /'<strong class="eeg-sem-valor">—<\/strong>'/.test(shellCodigo) &&
     !/valor:\s*0|>0</.test(shellCodigo));
caso("a ausência é dita como aguardando ativação / sem fonte",
     /Aguardando ativação/.test(shellCodigo) && /Sem fonte ativa/.test(shellCodigo));
caso("o aviso de ausência de dados é discreto e único",
     /var AVISO = "Operação EEGLife ainda sem dados vinculados\.";/.test(shellCodigo));
caso("as telas de lista têm cabeçalho, controles e tabela reais",
     /function htmlControles/.test(shellCodigo) && /function htmlTabelaVazia/.test(shellCodigo) &&
     /crm-filter-select/.test(shellCodigo));
caso("os controles nascem desabilitados (não prometem ação inexistente)",
     /disabled/.test(shellCodigo) &&
     /eeg-toolbar input:disabled/.test(lifeCss));
caso("a EEGLife não traz canvas nem gráfico",
     !/<canvas/i.test(painelEeg) && !/canvas/.test(shellCodigo));
// Nada de paciente, lead, laudo ou receita da SoproLife com cara de EEG.
caso("a casca não referencia dados da SoproLife",
     !/data\/(resumo|crm-clinicas|leads|marketing|tarefas|documentos|automacoes)\.json/.test(shellCodigo) &&
     !/data-private/.test(shellCodigo));
caso("os laudos de EEG são fila própria, não reuso do respiratório",
     /"Laudos de EEG"/.test(shellCodigo) &&
     !/espirometria_soprolife|laudos-espirometria|report-workflow/.test(shellCodigo) &&
     /não são\s*\+?\s*"?\s*reaproveitados|reaproveitados aqui/.test(shellJs));
caso("há módulo clínico próprio de exame e traçado",
     /"Exames e traçados"/.test(shellCodigo) && /grupo: "Clínico EEG"/.test(shellCodigo));
caso("as modalidades de EEG ficam declaradas como não ativas",
     /Nenhuma está ativa nesta fase/.test(shellCodigo));

// ───────────── H) identidade visual ───────────────────────────────────────
console.log();
console.log("H) Identidade: paleta EEGLife real, dourado como acento");

const PALETA = {
  "--eeg-dourado": "#c48a32", "--eeg-dourado-suave": "#d9ad61",
  "--eeg-dourado-tinta": "#865b1d", "--eeg-grafite": "#222c34",
  "--eeg-grafite-claro": "#2e3a44", "--eeg-areia": "#f8f5ee",
  "--eeg-areia-funda": "#f1ebde", "--eeg-tinta-suave": "#4a5560",
};
caso("a paleta EEGLife usa os valores reais do site",
     Object.entries(PALETA).every(([k, v]) => lifeCss.includes(`${k}: ${v}`)),
     Object.entries(PALETA).filter(([k, v]) => !lifeCss.includes(`${k}: ${v}`)).map(([k]) => k).join(","));
caso("texto pequeno sobre fundo claro usa o dourado-tinta",
     /\.eeg-selo \{[^}]*color: var\(--eeg-dourado-tinta\)/.test(lifeCss) &&
     /#lifePanelEegLife \.eyebrow \{ color: var\(--eeg-dourado-tinta\); \}/.test(lifeCss));
caso("o selo EEG não é texto branco sobre dourado",
     (() => {
       const i = lifeCss.indexOf(".eeg-selo {");
       return !/color:\s*(#fff|#ffffff|white)/i.test(lifeCss.slice(i, lifeCss.indexOf("}", i)));
     })());
caso("o dourado aparece como acento, não como fundo de texto pequeno",
     /background: var\(--eeg-dourado\)/.test(lifeCss) &&
     !/\.eeg-nav-item \{[^}]*background: var\(--eeg-dourado\)/.test(lifeCss));
caso("SoproLife mantém teal e EEGLife dourado como acento do contexto",
     /body\[data-life-ativo="soprolife"\][^}]*#17b5a5/.test(lifeCss) &&
     /body\[data-life-ativo="eeglife"\][^}]*--eeg-dourado/.test(lifeCss));

// ───────────── I) acessibilidade ──────────────────────────────────────────
console.log();
console.log("I) Acessibilidade: botão de verdade, aria, teclado, reduced motion");

caso("o seletor usa <button type=button>, não div clicável",
     (indexHtml.match(/<button type="button" role="tab" class="life-tab"/g) || []).length === 2 &&
     !/<div[^>]*data-life-context/.test(indexHtml));
caso("o seletor é um tablist com um tabpanel por marca",
     /role="tablist"/.test(indexHtml) &&
     (indexHtml.match(/role="tabpanel"/g) || []).length === 2);
caso("cada aba aponta para o painel que controla",
     /data-life-context="soprolife"[\s\S]{0,260}?aria-controls="lifePanelSoproLife"/.test(indexHtml) &&
     /data-life-context="eeglife"[\s\S]{0,260}?aria-controls="lifePanelEegLife"/.test(indexHtml));
caso("cada painel é rotulado pela sua aba",
     /aria-labelledby="lifeTabSoproLife"/.test(indexHtml) &&
     /aria-labelledby="lifeTabEegLife"/.test(indexHtml));
caso("o estado ativo é anunciado por aria-selected E aria-current",
     /setAttribute\("aria-selected"/.test(shellCodigo) &&
     /setAttribute\("aria-current"/.test(shellCodigo));
caso("a marca ativa também é dita em texto (não depende de cor)",
     /id="lifeContextoRotulo"/.test(indexHtml) && /aria-live="polite"/.test(indexHtml));
caso("o estado ativo tem sinal visual além da cor",
     /\.life-tab\[aria-selected="true"\] \{[^}]*background: #fff/.test(lifeCss) &&
     /\.life-tab\[aria-selected="true"\] \{[^}]*font-weight: 800/.test(lifeCss));
caso("o item ativo da EEGLife tem barra à esquerda, não só cor",
     /\.eeg-nav-item\[aria-current="true"\] \{[^}]*box-shadow: inset 3px 0 0/.test(lifeCss));
caso("navegação por setas, Home e End",
     /ArrowRight|ArrowLeft/.test(shellCodigo) && /evento\.key === "Home"/.test(shellCodigo) &&
     /evento\.key === "End"/.test(shellCodigo));
caso("só a aba ativa entra na ordem do Tab",
     /setAttribute\("tabindex", ativo \? "0" : "-1"\)/.test(shellCodigo));
caso("há foco visível no seletor e no menu EEGLife",
     /\.life-tab:focus-visible \{[^}]*outline:/.test(lifeCss) &&
     /\.eeg-nav-item:focus-visible \{[^}]*outline:/.test(lifeCss));
caso("o foco vai para o painel recém-aberto",
     /painelAtivo\.focus\(/.test(shellCodigo));
caso("a navegação da EEGLife é feita de <button>",
     /<button type="button" class="eeg-nav-item"/.test(shellCodigo));
caso("transições respeitam prefers-reduced-motion",
     /@media \(prefers-reduced-motion: reduce\)[\s\S]{0,120}?transition: none/.test(lifeCss));

// ───────────── J) responsividade ──────────────────────────────────────────
console.log();
console.log("J) Responsividade declarada, sem largura fixa que estoure");

caso("a barra deixa de ser fixa no celular",
     /@media \(max-width: 820px\)[\s\S]{0,260}?\.life-masthead \{ position: static; \}/.test(lifeCss));
caso("os botões do seletor viram fluidos no celular",
     /@media \(max-width: 820px\)[\s\S]{0,700}?\.life-tab \{ flex: 1 1 0/.test(lifeCss));
caso("o rótulo MARCA sai em 430px, mas a marca ativa continua dita",
     /@media \(max-width: 430px\)[\s\S]{0,200}?\.life-tab-kind \{ display: none; \}/.test(lifeCss) &&
     !/life-context-label \{[^}]*display: none/.test(lifeCss));
caso("há ajuste para 360px",
     /@media \(max-width: 360px\)/.test(lifeCss));
caso("o menu e os indicadores da EEGLife empilham no celular",
     /#lifePanelEegLife \.eeg-nav-list \{[\s\S]{0,160}?grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/.test(lifeCss) &&
     /@media \(max-width: 430px\)[\s\S]{0,280}?\.eeg-stats \{ grid-template-columns: minmax\(0, 1fr\); \}/.test(lifeCss));
caso("as grades usam minmax(0,…) — sem estouro por conteúdo largo",
     (lifeCss.match(/minmax\(0,/g) || []).length >= 3);
caso("nenhuma largura fixa em escala de layout (nada acima de 40px)",
     (() => {
       const l = (lifeCss.match(/(?<!min-)(?<!max-)\bwidth:\s*(\d+)px/g) || [])
         .map((t) => parseInt(t.match(/(\d+)px/)[1], 10));
       return l.length > 0 && l.every((n) => n <= 40);
     })(),
     (lifeCss.match(/(?<!min-)(?<!max-)\bwidth:\s*\d+px/g) || []).join(" "));
caso("o seletor pode quebrar linha sem sumir",
     /\.life-masthead-inner \{[^}]*flex-wrap: wrap/.test(lifeCss));

// ───────────── K) login e autenticação intocados ──────────────────────────
console.log();
console.log("K) Login e autenticação seguem como estavam");

caso("a casca não entra na tela de login",
     !/life-masthead|life-command-center|data-life-context/.test(loginHtml));
caso("o boot-gate continua sendo a autoridade de papel",
     /auth\/me/.test(bootGate) && /SoproBootGate/.test(bootGate));
caso("a casca não mexe em sessão, cookie, token ou logout",
     !/cookie|sessionStorage|logout|hasToken/i.test(shellCodigo));
caso("o painel SoproLife só inicia depois do papel do servidor",
     /gate\.pronto\.then\(\(identidade\) => \{[\s\S]{0,200}?if \(!identidade \|\| gate\.somenteClinico\) return;/.test(appJs));
caso("a carga de dados SoproLife espera a entrada no contexto SoproLife",
     /life\.onContext\("soprolife", entrarEmSoproLife\)/.test(appJs));
caso("sem a casca, o painel inicia como antes (sem regressão)",
     /\} else \{[\s\S]{0,260}?entrarEmSoproLife\(\);/.test(appJs));
caso("init() roda no máximo uma vez",
     /if \(!painelIniciado\) \{[\s\S]{0,120}?painelIniciado = true;[\s\S]{0,60}?init\(\);/.test(appJs));

// ───────────── L) Fase 2A — operação real da EEGLife ─────────────────────
console.log();
console.log("L) Fase 2A: operação real da EEGLife, com autorização no servidor");

caso("os módulos vivos leem as rotas /eeg do núcleo",
     /MODULOS_VIVOS = \{[\s\S]{0,260}?cadastros:[\s\S]{0,260}?exames:[\s\S]{0,260}?laudos:/.test(eegOperacao) &&
     /todos\("\/eeg\/pessoas/.test(eegOperacao) &&
     /todos\("\/eeg\/exames"\)/.test(eegOperacao) &&
     /todos\("\/eeg\/laudos"\)/.test(eegOperacao));
caso("a casca entrega o módulo à operação real quando ela existe",
     /window\.SoproEegOperacao/.test(shellCodigo) &&
     /operacao\.montar\(id, secao\)/.test(shellCodigo));
// O ponto que não pode afrouxar: autorização é do servidor.
caso("TODA rota de EEG depende de require_eeg_access",
     (() => {
       const rotas = (eegRouter.match(/@router\.(get|post|patch|delete)\(/g) || []).length;
       const guardas = (eegRouter.match(/require_eeg_access/g) || []).length;
       // uma ocorrência é o import; as demais são as dependências.
       return rotas > 0 && guardas >= rotas + 1;
     })(),
     `rotas=${(eegRouter.match(/@router\.(get|post|patch|delete)\(/g) || []).length} ` +
     `guardas=${(eegRouter.match(/require_eeg_access/g) || []).length}`);
caso("a política de acesso é por papel, sem nome de pessoa",
     !/\bana\b/i.test(eegRouter) && !/\bana\b/i.test(eegOperacao));
caso("a Central EEG consulta o vínculo, nunca a tabela de pessoas inteira",
     /select\(EegEnrollment\)[\s\S]{0,200}?\.join\(Person/.test(eegRouter));
caso("o frontend não decide autorização",
     !/papeis_efetivos|require_|ROLE_/.test(eegOperacaoCodigo));
caso("o traçado não trafega nem é exibido no navegador",
     !/<canvas|FileReader/.test(eegOperacaoCodigo) &&
     !/arquivo-tecnico[^;]{0,100}FormData/.test(eegOperacaoCodigo) &&
     /não é exibido nem armazenado aqui/.test(eegOperacao));
caso("o registro do arquivo técnico é só metadado (sem coluna de blob)",
     /class EegTechnicalFile/.test(eegModelsCodigo) &&
     !/LargeBinary|BYTEA|BLOB/i.test(
       eegModelsCodigo.slice(eegModelsCodigo.indexOf("class EegTechnicalFile"),
                             eegModelsCodigo.indexOf("class EegReportDispatch"))));
caso("o laudo de EEG tem tabela própria, separada do respiratório",
     /class EegReportDocument/.test(eegModels) &&
     /eeg_report_documents/.test(eegModels) &&
     /eeg\/laudos/.test(ler("nucleo-m15", "app", "services", "eeg_storage.py")));
caso("as sete modalidades reais vêm do conteúdo publicado da EEGLife",
     /eeglife-site\/src\/content\/modalidades\.mjs/.test(eegModalidades) &&
     (eegModalidades.match(/codigo="/g) || []).length === 7);
caso("modalidade sem duração declarada não ganha número inventado",
     /duracao_minutos_aproximada=None/.test(eegModalidades));
caso("nenhum preço, repasse ou prazo entrou nesta fase",
     !/pre[çc]o|valor_|repasse|cobran[çc]a|nfse/i.test(eegRouterCodigo) &&
     !/pre[çc]o|repasse/i.test(eegOperacaoCodigo) &&
     !/pre[çc]o|repasse|valor_/i.test(
       eegModelsCodigo.slice(eegModelsCodigo.indexOf("class EegEnrollment"))));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos da Fase 1.3A passaram.");
