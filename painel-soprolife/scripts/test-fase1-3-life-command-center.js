#!/usr/bin/env node
/* Fase 1.3 — Centro de Comando Life: contrato da casca multimarcas.
 *
 * Checagens estáticas e determinísticas (sem rede, sem DOM real, sem venv).
 * O comportamento em navegador de verdade — troca de contexto, persistência,
 * teclado, e a prova de que a casca EEGLife não busca dado SoproLife — é
 * provado em `test-fase1-3-life-browser-e2e.js`, que roda Chrome headless.
 * Aqui ficam os contratos que não precisam de navegador para serem exigidos.
 *
 * Blocos:
 *   A) os três contextos existem, e Life Geral NÃO é marca;
 *   B) SoproLife é o padrão e valor inválido cai nele;
 *   C) a casca não fala com o backend e não inventa parâmetro de marca;
 *   D) o painel SoproLife continua intacto;
 *   E) EEGLife é casca: estrutura real, zero dado, zero número;
 *   F) Life Geral é consolidado e não inventa indicador;
 *   G) identidade visual — paleta EEGLife real e dourado como acento;
 *   H) acessibilidade: botão de verdade, aria, teclado, reduced motion;
 *   I) responsividade declarada e sem largura fixa que estoure;
 *   J) login e autenticação intocados.
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

// Só o código: os comentários desta casca explicam o que ela NÃO faz
// ("não envia nada ao backend", "sem fetch"), e derrubariam as checagens que
// procuram justamente a ausência dessas chamadas.
const semComentariosJs = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");
const shellCodigo = semComentariosJs(shellJs);

// Recorta um painel de contexto pelo marcador de fechamento que o HTML traz.
function painel(id) {
  const ini = indexHtml.indexOf(`id="${id}"`);
  const fim = indexHtml.indexOf(`<!-- /${id} -->`, ini);
  return ini === -1 || fim === -1 ? "" : indexHtml.slice(ini, fim);
}
const painelSopro = painel("lifePanelSoproLife");
const painelEeg = painel("lifePanelEegLife");
const painelLifeGeral = painel("lifePanelLifeGeral");

// Texto visível aproximado: sem tags, sem comentários, sem atributos.
function textoVisivel(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const CONTEXTOS = ["life_geral", "soprolife", "eeglife"];

// ───────────── A) três contextos; Life Geral não é marca ───────────────────
console.log();
console.log("A) Três contextos existem e Life Geral não é marca");

caso("data-life-context identifica só os botões do seletor (não o body)",
     /setAttribute\("data-life-ativo"/.test(shellCodigo) &&
     !/setAttribute\("data-life-context"/.test(shellCodigo));
caso("os três contextos aparecem no seletor",
     CONTEXTOS.every((c) => indexHtml.includes(`data-life-context="${c}"`)),
     CONTEXTOS.filter((c) => !indexHtml.includes(`data-life-context="${c}"`)).join(","));
caso("os três contextos existem na casca (e só eles)",
     CONTEXTOS.every((c) => new RegExp(`\\b${c}:\\s*\\{`).test(shellCodigo)) &&
     /ORDEM\s*=\s*\["life_geral",\s*"soprolife",\s*"eeglife"\]/.test(shellCodigo));
caso("cada contexto tem um painel próprio",
     painelSopro.length > 0 && painelEeg.length > 0 && painelLifeGeral.length > 0);
caso("o título \"Centro de Comando Life\" está no topo",
     /<strong>Centro de Comando Life<\/strong>/.test(indexHtml));

// Life Geral é CONTEXTO CONSOLIDADO; as marcas são duas.
caso("Life Geral é declarado contexto, não marca",
     /data-life-context="life_geral"[^>]*data-life-kind="contexto"/.test(indexHtml) &&
     /life_geral:[\s\S]{0,120}?tipo:\s*"contexto"/.test(shellCodigo));
caso("SoproLife e EEGLife são declaradas marcas",
     /data-life-context="soprolife"[^>]*data-life-kind="marca"/.test(indexHtml) &&
     /data-life-context="eeglife"[^>]*data-life-kind="marca"/.test(indexHtml) &&
     /soprolife:[\s\S]{0,120}?tipo:\s*"marca"/.test(shellCodigo) &&
     /eeglife:[\s\S]{0,120}?tipo:\s*"marca"/.test(shellCodigo));
caso("exatamente duas marcas e um contexto consolidado",
     (shellCodigo.match(/tipo:\s*"marca"/g) || []).length === 2 &&
     (shellCodigo.match(/tipo:\s*"contexto"/g) || []).length === 1);
caso("o painel Life Geral diz em texto que não é uma marca",
     /Life Geral não é uma marca/.test(painelLifeGeral));

// ───────────── B) padrão SoproLife e queda de valor inválido ───────────────
console.log();
console.log("B) SoproLife é o padrão; valor inválido cai nele");

caso("o padrão declarado é soprolife",
     /PADRAO\s*=\s*"soprolife"/.test(shellCodigo));
caso("só os três valores são aceitos",
     /hasOwnProperty\.call\(CONTEXTOS,\s*id\)/.test(shellCodigo));
caso("valor salvo inválido cai no padrão",
     /valido\(bruto\)\s*\?\s*bruto\s*:\s*PADRAO/.test(shellCodigo));
caso("storage indisponível também cai no padrão (janela privada)",
     /catch\s*\(e\)\s*\{[\s\S]{0,200}?return PADRAO;/.test(shellCodigo));
caso("definir contexto inválido cai no padrão",
     /function definir\(id\)\s*\{[\s\S]{0,120}?if \(!valido\(id\)\) id = PADRAO;/.test(shellCodigo));
caso("o HTML já nasce com SoproLife marcado",
     /id="lifeTabSoproLife"[\s\S]{0,300}?aria-selected="true"/.test(indexHtml));
caso("a chave de persistência é de preferência, não de autorização",
     /CHAVE\s*=\s*"soprolife\.life\.contexto"/.test(shellCodigo));

// ───────────── C) a casca não fala com o backend ───────────────────────────
console.log();
console.log("C) A casca é visual: não consulta o servidor nem inventa marca na rota");

caso("a casca não faz fetch",
     !/\bfetch\s*\(/.test(shellCodigo));
caso("a casca não faz XHR nem EventSource nem WebSocket",
     !/XMLHttpRequest|EventSource|WebSocket|navigator\.sendBeacon/.test(shellCodigo));
caso("a casca não monta URL de API",
     !/\/api\/|\/painel-soprolife\/api/.test(shellCodigo));
// Requisito explícito da fase: nenhuma string "brand=" sai para o backend.
caso("nenhum parâmetro de marca é construído (brand=/marca=)",
     !/brand\s*=/i.test(shellCodigo) && !/[?&](brand|marca)=/i.test(indexHtml));
caso("a palavra brand não aparece como parâmetro em lugar nenhum da casca",
     !/brand/i.test(shellCodigo));
caso("o contexto é declarado preferência de interface, não permissão",
     /não é autorização/i.test(shellJs) &&
     /JAMAIS\s+(\*\s+)?libera dado/i.test(shellJs));

// ───────────── D) painel SoproLife intacto ─────────────────────────────────
console.log();
console.log("D) O painel SoproLife continua inteiro e no mesmo lugar");

caso("a app-shell original segue dentro do contexto SoproLife",
     /<div class="app-shell">/.test(painelSopro) &&
     painelSopro.includes('class="sidebar"'));
const SECOES_SOPRO = [
  "overview", "central-cadastros", "crm", "leads", "tarefas", "marketing",
  "financeiro", "fiscal", "parcerias-pastore", "custos-investimentos",
  "documentos", "automacoes-crm", "automacoes", "lancamentos",
];
caso("todos os módulos da sidebar SoproLife continuam presentes",
     SECOES_SOPRO.every((s) => painelSopro.includes(`data-section="${s}"`)),
     SECOES_SOPRO.filter((s) => !painelSopro.includes(`data-section="${s}"`)).join(","));
caso("o topbar e as ações do painel não mudaram de lugar",
     painelSopro.includes('id="globalSearch"') &&
     painelSopro.includes('id="refreshBtn"') &&
     painelSopro.includes('id="topbarSair"'));
caso("a identidade azul/teal do SoproLife não foi redesenhada",
     !/\.sidebar\s*\{[^}]*background/.test(lifeCss) &&
     !/\.topbar\s*\{/.test(lifeCss) &&
     !/\.app-shell\s*\{/.test(lifeCss));
// A única classe antiga que a casca toca é .sidebar, por causa do sticky — e
// só em desktop, escopada em body.life-cc para vencer a cascata do style.css.
caso("a única sobreposição no painel antigo é o apoio da sidebar sticky",
     /body\.life-cc \.sidebar \{[^}]*top: var\(--life-masthead-h\)/.test(lifeCss) &&
     /@media \(min-width: 821px\)/.test(lifeCss));
caso("essa sobreposição não vale no mobile (onde a sidebar já é estática)",
     (() => {
       const i = lifeCss.indexOf("body.life-cc .sidebar");
       const mq = lifeCss.lastIndexOf("@media", i);
       return mq !== -1 && /min-width: 821px/.test(lifeCss.slice(mq, i));
     })());

// ───────────── E) EEGLife é casca: estrutura, zero dado ────────────────────
console.log();
console.log("E) EEGLife mostra estrutura real, sem dado e sem número");

const GRUPOS_EEG = ["Operação", "Crescimento", "Gestão", "Sistema"];
caso("os quatro grupos de módulos existem na configuração",
     GRUPOS_EEG.every((g) => shellCodigo.includes(`grupo: "${g}"`)),
     GRUPOS_EEG.filter((g) => !shellCodigo.includes(`grupo: "${g}"`)).join(","));
const MODULOS_ESPERADOS = [
  "Central de Cadastros", "CRM", "Leads e Agendamentos", "Tarefas",
  "Laudos de EEG", "Marketing & SEO", "Financeiro", "Fiscal", "Parceiros",
  "Custos & Investimentos", "Documentos", "Fontes de dados",
  "Últimos lançamentos", "Núcleo administrativo",
];
caso("os módulos previstos da EEGLife estão declarados",
     MODULOS_ESPERADOS.every((m) => shellCodigo.includes(`"${m}"`)),
     MODULOS_ESPERADOS.filter((m) => !shellCodigo.includes(`"${m}"`)).join(" | "));
caso("o nome do laudo foi adaptado para EEG (não \"espirometria\")",
     shellCodigo.includes('"Laudos de EEG"') &&
     !/Laudos de espirometria/i.test(shellCodigo));
caso("nenhum módulo da EEGLife está marcado como pronto nesta fase",
     (shellCodigo.match(/pronto:\s*false/g) || []).length >= MODULOS_ESPERADOS.length &&
     !/pronto:\s*true/.test(shellCodigo));
caso("a sidebar EEGLife é renderizada da configuração (não é um 2º menu à mão)",
     /id="eegNavList"[^>]*><\/nav>/.test(indexHtml) &&
     /function renderSidebarEeg/.test(shellCodigo) &&
     !painelEeg.includes('data-section="'));
caso("existe o painel \"Estrutura em implantação\"",
     /Estrutura em implantação/.test(painelEeg));
caso("a casca EEGLife avisa que não consulta dado",
     /Nenhum dado é consultado nesta casca/.test(painelEeg));
caso("abrir módulo EEG não dispara requisição",
     /function abrirModuloEeg/.test(shellCodigo) &&
     !/fetch|XMLHttpRequest/.test(
       shellCodigo.slice(shellCodigo.indexOf("function abrirModuloEeg"),
                         shellCodigo.indexOf("function aplicar"))));
// Nada de número: sem paciente fictício, sem receita, sem gráfico.
caso("o painel EEGLife não exibe número algum",
     !/\d/.test(textoVisivel(painelEeg)),
     textoVisivel(painelEeg).match(/[^\s]*\d[^\s]*/g)?.slice(0, 4).join(" ") || "");
caso("o painel EEGLife não traz canvas/gráfico",
     !/<canvas/i.test(painelEeg) && !/Chart\b/.test(painelEeg));
caso("o painel EEGLife não traz KPI nem stat-card do painel SoproLife",
     !/kpi-card|stat-card|lancamento-item/.test(painelEeg));

// ───────────── F) Life Geral consolidado, sem indicador inventado ──────────
console.log();
console.log("F) Life Geral é consolidado e não inventa indicador");

caso("a frase sobre indicadores consolidados está presente",
     /Indicadores consolidados serão disponibilizados após a ativação do\s+modelo multimarcas/.test(painelLifeGeral));
caso("o painel Life Geral não exibe número algum",
     !/\d/.test(textoVisivel(painelLifeGeral)),
     textoVisivel(painelLifeGeral).match(/[^\s]*\d[^\s]*/g)?.slice(0, 4).join(" ") || "");
caso("não há KPI, gráfico ou stat-card no Life Geral",
     !/kpi-card|stat-card|<canvas/i.test(painelLifeGeral));
caso("existe cartão de cada marca",
     /data-life-marca="soprolife"/.test(painelLifeGeral) &&
     /data-life-marca="eeglife"/.test(painelLifeGeral));
caso("o estado de cada marca é qualitativo, não numérico",
     /data-estado="ativa"/.test(painelLifeGeral) &&
     /data-estado="implantacao"/.test(painelLifeGeral));
caso("há atalho para entrar em cada marca",
     /data-life-entrar="soprolife"/.test(painelLifeGeral) &&
     /data-life-entrar="eeglife"/.test(painelLifeGeral));
caso("a ausência de EEG é explicada, não lida como zero",
     /zero operacional/.test(painelLifeGeral));
caso("a arquitetura futura de consolidação é apresentada",
     /lg-arquitetura/.test(painelLifeGeral) &&
     /Permissão por marca/.test(painelLifeGeral));

// ───────────── G) identidade visual ───────────────────────────────────────
console.log();
console.log("G) Identidade: paleta EEGLife real, dourado como acento");

const PALETA_EEG = {
  "--eeg-dourado": "#c48a32",
  "--eeg-dourado-suave": "#d9ad61",
  "--eeg-dourado-tinta": "#865b1d",
  "--eeg-grafite": "#222c34",
  "--eeg-grafite-claro": "#2e3a44",
  "--eeg-areia": "#f8f5ee",
  "--eeg-areia-funda": "#f1ebde",
  "--eeg-tinta-suave": "#4a5560",
};
caso("a paleta EEGLife usa os valores reais do site",
     Object.entries(PALETA_EEG).every(([k, v]) => lifeCss.includes(`${k}: ${v}`)),
     Object.entries(PALETA_EEG).filter(([k, v]) => !lifeCss.includes(`${k}: ${v}`))
       .map(([k]) => k).join(","));
caso("texto pequeno sobre fundo claro usa o dourado-tinta",
     /\.eeg-eyebrow \{[^}]*color: var\(--eeg-dourado-tinta\)/.test(lifeCss) &&
     /\.eeg-selo \{[^}]*color: var\(--eeg-dourado-tinta\)/.test(lifeCss));
caso("o selo EEG não é texto branco sobre dourado",
     (() => {
       const i = lifeCss.indexOf(".eeg-selo {");
       const bloco = lifeCss.slice(i, lifeCss.indexOf("}", i));
       return !/color:\s*(#fff|#ffffff|white)/i.test(bloco);
     })());
caso("o dourado puro aparece como acento, não como fundo de texto pequeno",
     /background: var\(--eeg-dourado\)/.test(lifeCss) &&
     !/\.eeg-nav-item \{[^}]*background: var\(--eeg-dourado\)/.test(lifeCss));
caso("Life Geral usa tema neutro próprio, sem identidade comercial",
     /--lg-grafite/.test(lifeCss) && /--lg-tinta-suave/.test(lifeCss) &&
     !/--lg-dourado|--lg-teal/.test(lifeCss));
caso("SoproLife mantém teal e EEGLife dourado como acento do contexto",
     /body\[data-life-ativo="soprolife"\][^}]*#17b5a5/.test(lifeCss) &&
     /body\[data-life-ativo="eeglife"\][^}]*--eeg-dourado/.test(lifeCss));

// ───────────── H) acessibilidade ──────────────────────────────────────────
console.log();
console.log("H) Acessibilidade: botão de verdade, aria, teclado, reduced motion");

caso("o seletor usa <button type=button>, não div clicável",
     (indexHtml.match(/<button type="button" role="tab" class="life-tab"/g) || []).length === 3 &&
     !/<div[^>]*data-life-context/.test(indexHtml));
caso("o seletor é um tablist com tabpanel correspondente",
     /role="tablist"/.test(indexHtml) &&
     (indexHtml.match(/role="tabpanel"/g) || []).length === 3);
caso("cada aba aponta para o painel que controla",
     CONTEXTOS.every((c) => {
       const cfg = { life_geral: "lifePanelLifeGeral", soprolife: "lifePanelSoproLife", eeglife: "lifePanelEegLife" }[c];
       return new RegExp(`data-life-context="${c}"[\\s\\S]{0,260}?aria-controls="${cfg}"`).test(indexHtml);
     }));
caso("cada painel é rotulado pela sua aba",
     /aria-labelledby="lifeTabSoproLife"/.test(indexHtml) &&
     /aria-labelledby="lifeTabEegLife"/.test(indexHtml) &&
     /aria-labelledby="lifeTabLifeGeral"/.test(indexHtml));
caso("o estado ativo é anunciado por aria-selected E aria-current",
     /setAttribute\("aria-selected"/.test(shellCodigo) &&
     /setAttribute\("aria-current"/.test(shellCodigo));
caso("o contexto ativo também é dito em texto (não depende de cor)",
     /id="lifeContextoRotulo"/.test(indexHtml) &&
     /Contexto: <b>/.test(shellCodigo) &&
     /aria-live="polite"/.test(indexHtml));
caso("o estado ativo tem sinal visual além da cor (preenchimento e peso)",
     /\.life-tab\[aria-selected="true"\] \{[^}]*background: #fff/.test(lifeCss) &&
     /\.life-tab\[aria-selected="true"\] \{[^}]*font-weight: 800/.test(lifeCss));
caso("o item ativo da EEGLife tem barra à esquerda, não só cor",
     /\.eeg-nav-item\[aria-current="true"\] \{[^}]*box-shadow: inset 3px 0 0/.test(lifeCss));
caso("navegação por setas, Home e End",
     /ArrowRight|ArrowLeft/.test(shellCodigo) &&
     /evento\.key === "Home"/.test(shellCodigo) &&
     /evento\.key === "End"/.test(shellCodigo));
caso("só a aba ativa entra na ordem do Tab (padrão tablist)",
     /setAttribute\("tabindex", ativo \? "0" : "-1"\)/.test(shellCodigo));
caso("há foco visível no seletor e no menu EEGLife",
     /\.life-tab:focus-visible \{[^}]*outline:/.test(lifeCss) &&
     /\.eeg-nav-item:focus-visible \{[^}]*outline:/.test(lifeCss));
caso("o foco vai para o painel recém-aberto",
     /painelAtivo\.focus\(/.test(shellCodigo));
caso("transições respeitam prefers-reduced-motion",
     /@media \(prefers-reduced-motion: reduce\)[\s\S]{0,120}?transition: none/.test(lifeCss));

// ───────────── I) responsividade ──────────────────────────────────────────
console.log();
console.log("I) Responsividade declarada, sem largura fixa que estoure");

caso("a barra deixa de ser fixa no celular (não come a tela)",
     /@media \(max-width: 820px\)[\s\S]{0,260}?\.life-masthead \{ position: static; \}/.test(lifeCss));
caso("os botões do seletor viram fluidos no celular",
     /@media \(max-width: 820px\)[\s\S]{0,700}?\.life-tab \{[^}]*flex: 1 1 0/.test(lifeCss));
caso("o rótulo secundário sai em 430px, mas o contexto ativo continua dito",
     /@media \(max-width: 430px\)[\s\S]{0,200}?\.life-tab-kind \{ display: none; \}/.test(lifeCss) &&
     !/life-context-label \{[^}]*display: none/.test(lifeCss));
caso("há ajuste para 360px",
     /@media \(max-width: 360px\)/.test(lifeCss));
caso("as cascas EEGLife e Life Geral empilham no celular",
     /@media \(max-width: 820px\)[\s\S]{0,400}?\.eeg-shell \{ grid-template-columns: minmax\(0, 1fr\)/.test(lifeCss) &&
     /\.lg-marcas \{ grid-template-columns: minmax\(0, 1fr\); \}/.test(lifeCss));
caso("as grades usam minmax(0,…) — sem estouro horizontal por conteúdo largo",
     (lifeCss.match(/grid-template-columns:\s*(repeat\(\d+,\s*)?minmax\(0,/g) || []).length >= 4);
// O risco real de estouro é largura fixa em ESCALA DE LAYOUT. Toda largura
// fixa desta folha é de elemento decorativo (marca, ponto, ícone): nenhuma
// passa de 40px, então nada aqui pode estourar uma viewport de 360px. A
// coluna do menu EEGLife é `grid-template-columns`, que colapsa em 820px.
caso("nenhuma largura fixa em escala de layout (nada acima de 40px)",
     (() => {
       const larguras = (lifeCss.match(/(?<!min-)(?<!max-)\bwidth:\s*(\d+)px/g) || [])
         .map((t) => parseInt(t.match(/(\d+)px/)[1], 10));
       return larguras.length > 0 && larguras.every((n) => n <= 40);
     })(),
     (lifeCss.match(/(?<!min-)(?<!max-)\bwidth:\s*\d+px/g) || []).join(" "));
caso("a coluna do menu EEGLife colapsa no celular em vez de ser largura fixa",
     /\.eeg-shell \{[^}]*grid-template-columns: 248px 1fr/.test(lifeCss) &&
     /@media \(max-width: 820px\)[\s\S]{0,400}?\.eeg-shell \{ grid-template-columns: minmax\(0, 1fr\)/.test(lifeCss));
caso("o seletor pode quebrar linha sem sumir",
     /\.life-masthead-inner \{[^}]*flex-wrap: wrap/.test(lifeCss));

// ───────────── J) login e autenticação intocados ──────────────────────────
console.log();
console.log("J) Login e autenticação seguem como estavam");

caso("a casca não entra na tela de login",
     !/life-masthead|life-command-center|data-life-context/.test(loginHtml));
caso("o boot-gate continua sendo a autoridade de papel",
     /auth\/me/.test(bootGate) && /SoproBootGate/.test(bootGate));
caso("a casca não mexe em sessão, cookie, token ou papel",
     !/cookie|sessionStorage|auth|token|papel|login|logout/i.test(shellCodigo));
caso("o painel SoproLife só inicia depois do papel do servidor",
     /gate\.pronto\.then\(\(identidade\) => \{[\s\S]{0,200}?if \(!identidade \|\| gate\.somenteClinico\) return;/.test(appJs));
// O coração do requisito: a leitura dos data/*.json espera a ENTRADA no
// contexto SoproLife, por isso a casca EEGLife não busca dado SoproLife.
caso("a carga de dados SoproLife espera a entrada no contexto SoproLife",
     /life\.onContext\("soprolife", entrarEmSoproLife\)/.test(appJs));
caso("sem a casca, o painel inicia como antes (sem regressão)",
     /\} else \{[\s\S]{0,260}?entrarEmSoproLife\(\);/.test(appJs));
caso("init() roda no máximo uma vez",
     /if \(!painelIniciado\) \{[\s\S]{0,120}?painelIniciado = true;[\s\S]{0,60}?init\(\);/.test(appJs));
caso("reentrada em SoproLife remede os gráficos em vez de recarregar tudo",
     /resizeCharts\(\);[\s\S]{0,40}?\}\s*\n\s*gate\.pronto/.test(appJs));

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos da Fase 1.3 passaram.");
