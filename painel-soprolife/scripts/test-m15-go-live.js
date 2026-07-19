#!/usr/bin/env node
// SoproLife — M15.5A Go-live controlado do Núcleo M15.
//
// 1) Testes FUNCIONAIS da guarda de contexto seguro (js/m15-security.js):
//    HTTPS permite login; loopback HTTP (localhost/127.x/::1) permite
//    desenvolvimento; HTTP remoto (hostname, IP Tailscale 100.x, rede
//    privada) bloqueia senha E token; origem desconhecida bloqueia
//    (fail-closed).
// 2) Ativação global: enabled=true no m15-config.json torna o núcleo
//    visível sem o opt-in localStorage soproM15 (a expressão de decisão do
//    boot é reproduzida com o config REAL do repositório).
// 3) Guardas ESTÁTICAS do núcleo: bloqueio antes de qualquer campo de
//    senha/token, nenhuma requisição /auth/* em origem bloqueada, token
//    nunca persistido, api_base de mesma origem, RBAC da UI intacto,
//    sem recurso externo, ordem correta dos scripts no index.html.
//
// Uso: node painel-soprolife/scripts/test-m15-go-live.js
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
const secPath = path.join(RAIZ, "js", "m15-security.js");
const secSrc = fs.readFileSync(secPath, "utf8");
const nucleoSrc = fs.readFileSync(path.join(RAIZ, "js", "m15-nucleo.js"), "utf8");
const indexSrc = fs.readFileSync(path.join(RAIZ, "index.html"), "utf8");
const configSrc = fs.readFileSync(path.join(RAIZ, "data", "m15-config.json"), "utf8");

const sec = require(secPath);

// ─────────────── A) guarda de contexto seguro (funcional) ──────────────────
console.log("A) Contexto seguro: HTTPS e loopback permitem, HTTP remoto bloqueia");

const loc = (protocol, hostname) => ({ protocol, hostname });

// Permitidos
caso("HTTPS em hostname remoto → modo https, login permitido",
     sec.classify(loc("https:", "painel.exemplo-tailnet.ts.net")).mode === "https" &&
     sec.canAuthenticate(loc("https:", "painel.exemplo-tailnet.ts.net")) === true);
caso("HTTPS em qualquer hostname → seguro (sem tailnet fixado no código)",
     sec.classify(loc("https:", "outro-host.exemplo.net")).secure === true);
caso("HTTP em localhost → modo localdev, login permitido",
     sec.classify(loc("http:", "localhost")).mode === "localdev" &&
     sec.canAuthenticate(loc("http:", "localhost")) === true);
caso("HTTP em 127.0.0.1 → modo localdev, login permitido",
     sec.classify(loc("http:", "127.0.0.1")).mode === "localdev" &&
     sec.canAuthenticate(loc("http:", "127.0.0.1")) === true);
caso("HTTP em 127.0.0.2 (loopback /8) → localdev",
     sec.classify(loc("http:", "127.0.0.2")).mode === "localdev");
caso("HTTP em ::1 → localdev", sec.classify(loc("http:", "::1")).mode === "localdev");
caso("HTTP em [::1] (colchetes) → localdev",
     sec.classify(loc("http:", "[::1]")).mode === "localdev");
caso("LOCALHOST maiúsculo normaliza → localdev",
     sec.classify(loc("HTTP:", "LOCALHOST")).mode === "localdev");

// Bloqueados
caso("HTTP em hostname remoto → bloqueado",
     sec.classify(loc("http:", "painel.exemplo.com.br")).mode === "blocked" &&
     sec.canAuthenticate(loc("http:", "painel.exemplo.com.br")) === false);
caso("HTTP em IP Tailscale (100.x) → bloqueado (privado ≠ seguro)",
     sec.classify(loc("http:", "100.83.200.10")).mode === "blocked");
caso("HTTP em rede privada 192.168.x → bloqueado",
     sec.classify(loc("http:", "192.168.0.10")).mode === "blocked");
caso("HTTP em 10.x → bloqueado", sec.classify(loc("http:", "10.1.2.3")).mode === "blocked");
caso("hostname que TERMINA em localhost não engana (evil-localhost) → bloqueado",
     sec.classify(loc("http:", "evil-localhost")).mode === "blocked");
caso("127.0.0.1 com sufixo (127.0.0.1.evil.com) → bloqueado",
     sec.classify(loc("http:", "127.0.0.1.evil.com")).mode === "blocked");
caso("file: → bloqueado", sec.classify(loc("file:", "")).mode === "blocked");
caso("location ausente → bloqueado (fail-closed)",
     sec.classify(null).mode === "blocked" && sec.canAuthenticate(null) === false);
caso("location vazio → bloqueado", sec.classify({}).mode === "blocked");
caso("bloqueado nunca é secure",
     sec.classify(loc("http:", "painel.exemplo.com.br")).secure === false);
caso("mensagem de bloqueio em PT aponta para o endereço HTTPS privado",
     /HTTPS privado/.test(sec.MENSAGEM_BLOQUEIO) && /Tailscale/.test(sec.MENSAGEM_BLOQUEIO));
caso("mensagem de bloqueio não fixa hostname de tailnet (.ts.net)",
     sec.MENSAGEM_BLOQUEIO.indexOf(".ts.net") === -1);

// Módulo puro: sem rede, sem storage, sem DOM, sem recurso externo.
caso("m15-security.js sem fetch/XMLHttpRequest/WebSocket",
     secSrc.indexOf("fetch(") === -1 && secSrc.indexOf("XMLHttpRequest") === -1 &&
     secSrc.indexOf("WebSocket") === -1);
caso("m15-security.js sem localStorage/sessionStorage",
     secSrc.indexOf("localStorage") === -1 && secSrc.indexOf("sessionStorage") === -1);
caso("m15-security.js sem URL externa (http/https)", !/https?:\/\//.test(secSrc));

// ───────────── B) ativação global (enabled=true, sem opt-in) ───────────────
console.log();
console.log("B) Ativação global — enabled=true sem opt-in de localStorage");
{
  const cfg = JSON.parse(configSrc);
  caso("m15-config.json: enabled === true (go-live M15.5A)", cfg.enabled === true);
  caso("m15-config.json: api_base de mesma origem preservado",
       cfg.api_base === "/painel-soprolife/api/m15" && cfg.api_base.indexOf("://") === -1);

  // Reproduz a expressão de decisão do boot() com o config REAL e SEM opt-in:
  const decisaoBoot = (config, optInLocalStorage) =>
    (config && config.enabled === true) || optInLocalStorage === "on";
  caso("menu M15 ativa com o config real SEM localStorage soproM15",
       decisaoBoot(cfg, null) === true);
  caso("expressão de decisão do boot permanece a mesma no código",
       nucleoSrc.indexOf("config.enabled === true") !== -1 &&
       nucleoSrc.indexOf('localStorage.getItem("soproM15") === "on"') !== -1);
  caso("compatibilidade com o opt-in local antigo preservada (flag false → opt-in ainda liga)",
       decisaoBoot({ enabled: false }, "on") === true);
}

// ───────── C) guardas estáticas do núcleo (bloqueio fail-closed) ───────────
console.log();
console.log("C) Núcleo: bloqueio antes de senha/token, sem requisição /auth/*");
{
  caso("estado nasce bloqueado (fail-closed) até a guarda classificar",
       /access:\s*\{\s*mode:\s*"blocked",\s*secure:\s*false/.test(nucleoSrc));
  caso("classifyAccess usa window.SoproM15Security (guarda reutilizável)",
       nucleoSrc.indexOf("window.SoproM15Security") !== -1);
  const activateFn = nucleoSrc.slice(nucleoSrc.indexOf("function activate(config)"),
                                     nucleoSrc.indexOf("function boot()"));
  caso("activate classifica a origem antes de montar a UI",
       activateFn.indexOf("classifyAccess();") !== -1 &&
       activateFn.indexOf("classifyAccess();") < activateFn.indexOf("buildSection()"));

  // api(): origem bloqueada rejeita /auth/* ANTES de qualquer fetch.
  const apiFn = nucleoSrc.slice(nucleoSrc.indexOf("function api(path, options)"),
                                nucleoSrc.indexOf("// ------------------------------------------------------------ utilitários"));
  caso("api() rejeita /auth/* quando a origem não é segura",
       apiFn.indexOf('!state.access.secure && path.indexOf("/auth/") === 0') !== -1);
  caso("rejeição de /auth/* acontece ANTES do fetch (nenhuma requisição sai)",
       apiFn.indexOf('path.indexOf("/auth/")') < apiFn.indexOf("fetch("));

  // renderAuthArea(): branch bloqueado retorna sem renderizar senha/token.
  const authFn = nucleoSrc.slice(nucleoSrc.indexOf("function renderAuthArea()"),
                                 nucleoSrc.indexOf("function afterAuth()"));
  const iBloq = authFn.indexOf("!state.access.secure");
  caso("renderAuthArea tem branch bloqueado", iBloq !== -1);
  caso("branch bloqueado vem ANTES do campo de senha",
       iBloq !== -1 && iBloq < authFn.indexOf('type="password"'));
  caso("branch bloqueado vem ANTES do campo de token da CLI",
       iBloq !== -1 && iBloq < authFn.indexOf("m15Token"));
  caso("branch bloqueado retorna cedo (return) sem montar formulário",
       /if \(!state\.access\.secure\) \{[\s\S]{0,400}?return;\s*\}/.test(authFn));
  caso("aviso de bloqueio em PT instrui a abrir o endereço HTTPS privado",
       nucleoSrc.indexOf("Abra o endereço HTTPS privado do painel") !== -1);

  // render(): corpo também mostra o bloqueio (sem convite a login).
  const renderFn = nucleoSrc.slice(nucleoSrc.indexOf("function render() {"),
                                   nucleoSrc.indexOf("function checkApi()"));
  caso("render() mostra bloqueio antes do convite de login",
       renderFn.indexOf("!state.access.secure") !== -1 &&
       renderFn.indexOf("!state.access.secure") < renderFn.indexOf("!state.token"));
}

// ─────────────── D) sessão, RBAC e superfícies preservadas ─────────────────
console.log();
console.log("D) Sessão em memória, RBAC e contratos preservados");
{
  caso("token nunca é gravado em storage (nenhum setItem no núcleo)",
       (nucleoSrc.match(/\.setItem\(/g) || []).length === 0);
  caso("limpeza do token legado preservada",
       nucleoSrc.indexOf('localStorage.removeItem("soproM15Token")') !== -1);
  caso("sessão continua somente em memória (aviso na UI)",
       nucleoSrc.indexOf("Sessão só em memória") !== -1);
  caso("logout (Sair) preservado", nucleoSrc.indexOf('id="m15Sair"') !== -1);
  caso("login continua no endpoint /auth/token via POST",
       nucleoSrc.indexOf('api("/auth/token"') !== -1);
  caso("identidade continua via /auth/me", nucleoSrc.indexOf('api("/auth/me")') !== -1);

  // RBAC da UI: papéis mínimos por aba inalterados.
  caso("aba auditoria continua exigindo gestor",
       /\["auditoria", "Auditoria", "gestor"\]/.test(nucleoSrc));
  caso("aba admin continua exigindo admin",
       /\["admin", "Administração", "admin"\]/.test(nucleoSrc));
  const tabsSrc = nucleoSrc.slice(nucleoSrc.indexOf("var TABS = ["),
                                  nucleoSrc.indexOf("function buildSection()"));
  caso("demais abas continuam com papel mínimo leitura",
       (tabsSrc.match(/"leitura"\]/g) || []).length === 10);
  caso("can() continua fail-open na UI com autorização real no servidor",
       nucleoSrc.indexOf("papeis_efetivos.indexOf(role) !== -1") !== -1);

  caso("apiBase padrão de mesma origem preservado",
       nucleoSrc.indexOf('apiBase: "/painel-soprolife/api/m15"') !== -1);
  caso("activate continua recusando api_base de outra origem",
       nucleoSrc.indexOf('config.api_base === "/painel-soprolife/api/m15"') !== -1);
}

// ─────────────── E) estado visual honesto e index.html ─────────────────────
console.log();
console.log("E) Estado visual de go-live e carregamento dos scripts");
{
  caso("cabeçalho exibe M15 Beta", nucleoSrc.indexOf(">M15 Beta<") !== -1);
  caso("cabeçalho exibe implantação controlada",
       nucleoSrc.indexOf(">implantação controlada<") !== -1);
  caso("selo de acesso distingue HTTPS seguro",
       nucleoSrc.indexOf(">Acesso seguro (HTTPS)<") !== -1);
  caso("selo de acesso distingue desenvolvimento local",
       nucleoSrc.indexOf(">Desenvolvimento local<") !== -1);
  caso("selo de acesso denuncia HTTP inseguro bloqueado",
       nucleoSrc.indexOf(">HTTP inseguro — login bloqueado<") !== -1);
  caso("selo antigo de ambiente 100% sintético saiu do cabeçalho",
       nucleoSrc.indexOf("ambiente com dados sintéticos") === -1);
  caso("marcação por item de dado sintético continua (honestidade preservada)",
       nucleoSrc.indexOf('title="Dado sintético de demonstração"') !== -1);
  caso("IP privado nunca é anunciado como seguro (comentário-contrato + selo)",
       nucleoSrc.indexOf("NUNCA é anunciado como seguro") !== -1);

  caso("index.html carrega m15-security.js ANTES do núcleo",
       indexSrc.indexOf("js/m15-security.js") !== -1 &&
       indexSrc.indexOf("js/m15-security.js") < indexSrc.indexOf("js/m15-nucleo.js"));
  caso("index.html mantém datepicker antes do núcleo",
       indexSrc.indexOf("js/m15-datepicker.js") < indexSrc.indexOf("js/m15-nucleo.js"));
  caso("scripts M15 com cache-buster atualizado (?v=2026071902)",
       /m15-security\.js\?v=2026071902/.test(indexSrc) &&
       /m15-nucleo\.js\?v=2026071902/.test(indexSrc));
}

console.log();
if (falhas) {
  console.log(`RESULTADO: ${falhas} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
process.exit(0);
