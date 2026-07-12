// SoproLife — Testes dos contratos versionados executáveis (M14.3A).
//
// Carrega apps-script/contratos-canonicos.gs (o MESMO código que roda no
// Apps Script) e exercita: validação fail-closed de cabeçalho, patch seletivo
// de upsert (preservação de colunas desconhecidas/fórmulas/valores não
// enviados), datas valor+precisão, IDs de autoridade do servidor,
// idempotência (clique duplo, retry, timeout, linha removida, concorrência) e
// consistência com core/contracts/registros-schemas.json.
//
// 100% offline e sintético. Uso: node painel-soprolife/scripts/test-contratos.js

"use strict";

const fs = require("fs");
const path = require("path");

const RAIZ = path.resolve(__dirname, "..");
const GS = fs.readFileSync(path.join(RAIZ, "apps-script", "contratos-canonicos.gs"), "utf8");
const SCHEMAS = JSON.parse(fs.readFileSync(path.join(RAIZ, "core", "contracts", "registros-schemas.json"), "utf8"));

// Executa o .gs num sandbox Node com o guard module.exports do próprio arquivo.
// "require" é passado explicitamente porque o Function constructor roda no
// escopo global (não herda o require local deste módulo) — ctFingerprintPayload
// usa require("crypto") como fallback de SHA-256 fora do Apps Script.
const sandboxModule = { exports: {} };
new Function("module", "require", GS)(sandboxModule, require);
const ct = sandboxModule.exports;

let FALHAS = 0;
function caso(nome, cond, detalhe) {
  if (cond) {
    console.log(`  PASS: ${nome}`);
  } else {
    FALHAS += 1;
    console.log(`  FAIL: ${nome}${detalhe ? " — " + detalhe : ""}`);
  }
}

const UUID_A = "0f8fad5b-d9cb-469f-a165-70867728950e";
const UUID_B = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

// ── Consistência .gs ↔ JSON commitável ───────────────────────────────────────
console.log("── Consistência contratos-canonicos.gs ↔ registros-schemas.json ──");
caso("mesma versão de contrato", ct.CT_CONTRATOS_VERSAO === SCHEMAS.schema_version,
     `${ct.CT_CONTRATOS_VERSAO} vs ${SCHEMAS.schema_version}`);
for (const nome of Object.keys(ct.CT_REGISTROS)) {
  const gs = ct.CT_REGISTROS[nome];
  const js = SCHEMAS.entidades[nome];
  caso(`entidade ${nome} existe no JSON`, Boolean(js));
  if (!js) continue;
  caso(`${nome}: cabeçalho v1 idêntico`, JSON.stringify(gs.v1) === JSON.stringify(js.v1));
  caso(`${nome}: v2_extras idêntico`, JSON.stringify(gs.v2_extras) === JSON.stringify(js.v2_extras));
  caso(`${nome}: obrigatórios idênticos`, JSON.stringify(gs.obrigatorios) === JSON.stringify(js.obrigatorios));
  caso(`${nome}: canônicos opcionais idênticos`,
       JSON.stringify(gs.canonicos_opcionais) === JSON.stringify(js.canonicos_opcionais));
  caso(`${nome}: prefixo idêntico`, gs.prefixo === js.prefixo);
  caso(`${nome}: imutáveis idênticos`, JSON.stringify(gs.imutaveis) === JSON.stringify(js.imutaveis));
  caso(`${nome}: colunas técnicas idênticas`,
       JSON.stringify(gs.colunas_tecnicas) === JSON.stringify(js.colunas_tecnicas));
}
caso("JSON não tem entidade a mais",
     Object.keys(SCHEMAS.entidades).every((n) => ct.CT_REGISTROS[n]));
caso("financeiro declara cardinalidade 1:N",
     SCHEMAS.entidades.financeiro_lancamentos.cardinalidade.regra.includes("N movimentos"));
caso("financeiro proíbe 'última vence' silencioso",
     SCHEMAS.entidades.financeiro_lancamentos.cardinalidade.duplicata.includes("CONFLITO"));

// ── Datas: valor + precisão ──────────────────────────────────────────────────
console.log("── Datas valor + precisão ──");
caso("YYYY-MM-DD → dia", (() => { const r = ct.ctParseDataPrecisao("2026-07-02"); return r.ok && r.precisao === "dia"; })());
caso("DD/MM/AAAA → dia", (() => { const r = ct.ctParseDataPrecisao("02/07/2026"); return r.ok && r.precisao === "dia"; })());
caso("YYYY-MM → mes (sem virar dia 01)", (() => {
  const r = ct.ctParseDataPrecisao("2026-06");
  return r.ok && r.precisao === "mes" && r.valor === "2026-06";
})());
caso("MM/AAAA → mes preservando o valor", (() => {
  const r = ct.ctParseDataPrecisao("06/2026");
  return r.ok && r.precisao === "mes" && r.valor === "06/2026";
})());
caso("YYYY → ano", (() => { const r = ct.ctParseDataPrecisao("2026"); return r.ok && r.precisao === "ano"; })());
caso("vazio → desconhecida (nunca hoje)", (() => {
  const r = ct.ctParseDataPrecisao("");
  return r.ok && r.precisao === "desconhecida" && r.valor === "";
})());
caso("data impossível → erro explícito", !ct.ctParseDataPrecisao("45/13/2026").ok);
caso("texto livre → erro explícito", !ct.ctParseDataPrecisao("semana passada").ok);
caso("31/02 → erro (calendário real)", !ct.ctParseDataPrecisao("31/02/2026").ok);
caso("coerência: valor dia + precisao dia OK",
     ct.ctValidarDataComPrecisao("2026-07-02", "dia").ok);
caso("coerência: valor mês + precisao dia → erro",
     !ct.ctValidarDataComPrecisao("06/2026", "dia").ok);
caso("coerência: valor dia + precisao mes → erro",
     !ct.ctValidarDataComPrecisao("02/07/2026", "mes").ok);
caso("coerência: precisão inválida → erro", !ct.ctValidarDataComPrecisao("2026-07-02", "diaria").ok);
caso("coerência: sem precisão → deriva do valor",
     ct.ctValidarDataComPrecisao("06/2026").precisao === "mes");

// ── Cabeçalho fail-closed ────────────────────────────────────────────────────
console.log("── Validação de cabeçalho (fail-closed) ──");
const HEADERS_V1 = ct.CT_REGISTROS.crm_espirometria.v1.slice();
const HEADERS_V2 = HEADERS_V1.concat(ct.CT_REGISTROS.crm_espirometria.v2_extras);
caso("cabeçalho v1 reconhecido", ct.ctValidarCabecalho("crm_espirometria", HEADERS_V1).versao === "v1");
caso("cabeçalho v2 reconhecido", ct.ctValidarCabecalho("crm_espirometria", HEADERS_V2).versao === "v2");
caso("coluna extra desconhecida não invalida",
     ct.ctValidarCabecalho("crm_espirometria", HEADERS_V1.concat(["coluna_da_equipe"])).ok);
caso("sem coluna obrigatória → incompatível com erro claro", (() => {
  const r = ct.ctValidarCabecalho("crm_espirometria", ["primeiro_nome", "telefone"]);
  return !r.ok && r.erro.includes("exame_id") && r.erro.includes("incompatível");
})());
caso("contrato desconhecido → erro", !ct.ctValidarCabecalho("aba_inexistente", HEADERS_V1).ok);

console.log("── Planejamento de escrita ──");
caso("campo solicitado sem coluna → ERRO, nada gravado", (() => {
  const r = ct.ctPlanejarEscrita("crm_espirometria", HEADERS_V1,
    { exame_id: "X", primeiro_nome: "Alfa", status_exame: "Realizado", paciente_id: "PAC-x" },
    ["paciente_id"]);
  return !r.ok && r.colunas_ausentes.includes("paciente_id") && r.erro.includes("nada foi gravado");
})());
caso("campo solicitado com coluna v2 → persiste", (() => {
  const r = ct.ctPlanejarEscrita("crm_espirometria", HEADERS_V2,
    { exame_id: "X", primeiro_nome: "Alfa", status_exame: "Realizado", paciente_id: "PAC-x" },
    ["paciente_id"]);
  return r.ok && r.persistidos.includes("paciente_id") && r.versao_cabecalho === "v2";
})());
caso("campo derivado sem coluna → ignorado com transparência (sem erro)", (() => {
  const r = ct.ctPlanejarEscrita("crm_espirometria", HEADERS_V1,
    { exame_id: "X", primeiro_nome: "Alfa", status_exame: "Realizado", data_exame_precisao: "dia" },
    []);
  return r.ok && r.ignorados.includes("data_exame_precisao") && !r.persistidos.includes("data_exame_precisao");
})());
caso("resposta informa contrato_versao", (() => {
  const r = ct.ctPlanejarEscrita("crm_espirometria", HEADERS_V1,
    { exame_id: "X", primeiro_nome: "A", status_exame: "Realizado" }, []);
  return r.ok && r.contrato_versao === ct.CT_CONTRATOS_VERSAO;
})());

// ── Upsert com patch seletivo ────────────────────────────────────────────────
console.log("── Upsert: patch seletivo preserva o que não foi enviado ──");
// Aba simulada: v1 + coluna adicional da equipe (desconhecida do contrato) no
// meio, com valor manual que NUNCA pode ser tocado.
const HEADERS_COM_EXTRA = HEADERS_V1.concat(["anotacao_da_equipe"]);
const linhaExistente = HEADERS_COM_EXTRA.map((h) => `antigo:${h}`);

caso("insert monta a linha completa", (() => {
  const r = ct.ctPlanejarUpsert("crm_espirometria", HEADERS_COM_EXTRA,
    { exame_id: "ESP-0009", primeiro_nome: "Alfa", status_exame: "Aguardando" }, [], null);
  return r.ok && r.modo === "insert" && r.linha.length === HEADERS_COM_EXTRA.length
      && r.linha[0] === "ESP-0009";
})());
caso("patch altera SOMENTE colunas do payload", (() => {
  const r = ct.ctPlanejarUpsert("crm_espirometria", HEADERS_COM_EXTRA,
    { exame_id: "antigo:exame_id", status_exame: "Realizado", primeiro_nome: "antigo:primeiro_nome" },
    [], linhaExistente);
  if (!r.ok || r.modo !== "patch") return false;
  // única célula com valor diferente do atual: status_exame
  return r.celulas.length === 1 && r.celulas[0].coluna === "status_exame";
})());
caso("coluna desconhecida da equipe é preservada", (() => {
  const r = ct.ctPlanejarUpsert("crm_espirometria", HEADERS_COM_EXTRA,
    { status_exame: "Realizado", exame_id: "antigo:exame_id", primeiro_nome: "x" }, [], linhaExistente);
  return r.ok && r.celulas.every((c) => c.coluna !== "anotacao_da_equipe");
})());
caso("ausência de campo no payload NÃO apaga valor existente", (() => {
  const r = ct.ctPlanejarUpsert("crm_espirometria", HEADERS_COM_EXTRA,
    { exame_id: "antigo:exame_id", primeiro_nome: "antigo:primeiro_nome", status_exame: "Realizado" },
    [], linhaExistente);
  // telefone/servico/origem/etc. não estão no payload → nenhuma célula deles
  return r.ok && r.celulas.every((c) => !["telefone", "servico", "origem", "data_entrada"].includes(c.coluna));
})());
caso("patch fail-closed em campo solicitado sem coluna", (() => {
  const r = ct.ctPlanejarUpsert("crm_espirometria", HEADERS_COM_EXTRA,
    { exame_id: "antigo:exame_id", primeiro_nome: "x", status_exame: "Realizado", parceiro: "Pastore" },
    ["parceiro"], linhaExistente);
  return !r.ok && r.colunas_ausentes.includes("parceiro");
})());

// ── IDs e idempotência ───────────────────────────────────────────────────────
console.log("── IDs de servidor e idempotência ──");
caso("ID do servidor = prefixo + UUID opaco",
     ct.ctNovoIdServidor("PAC", UUID_A) === "PAC-" + UUID_A);
caso("prefixo inválido → erro", (() => {
  try { ct.ctNovoIdServidor("P4", UUID_A); return false; } catch (e) { return true; }
})());
caso("uuid inválido → erro (nunca aceita contador/linha)", (() => {
  try { ct.ctNovoIdServidor("PAC", "0001"); return false; } catch (e) { return true; }
})());
caso("idempotency key legada do navegador é aceita",
     ct.ctIdempotencyKeySegura("ESP-20260709-143055-ABC123") === "ESP-20260709-143055-ABC123");
caso("texto livre nunca vira idempotency key", ct.ctIdempotencyKeySegura("exame do João!") === "");
for (const legado of ["ESP-0001", "ESM-jun-01", "PAC-0001", "PAC-20260615-001", "CON-0001", "FIN-0001"]) {
  caso(`legado preservado/classificado: ${legado}`,
       ct.ctIdEhLegadoConhecido(legado) || legado === "ESM-jun-01" && ct.ctIdEhLegadoConhecido(legado));
}
caso("ID novo de servidor não é padrão legado",
     !ct.ctIdEhLegadoConhecido(ct.ctNovoIdServidor("PAC", UUID_A)));

// Simulação de planilha em memória para os cenários de idempotência —
// espelha exatamente o algoritmo do writer: buscar idempotency key na coluna
// de id; achou → patch; não achou → insert.
function novaPlanilha(headers) {
  return { headers, linhas: [] };
}
function upsertSimulado(planilha, chave, campos, solicitados) {
  const idCol = 0; // exame_id
  const idx = planilha.linhas.findIndex((l) => l[idCol] === chave);
  const existente = idx >= 0 ? planilha.linhas[idx] : null;
  const plano = ct.ctPlanejarUpsert("crm_espirometria", planilha.headers, campos, solicitados || [], existente);
  if (!plano.ok) return plano;
  if (plano.modo === "insert") {
    planilha.linhas.push(plano.linha.slice());
  } else {
    plano.celulas.forEach((c) => { planilha.linhas[idx][c.indice] = String(c.valor); });
  }
  return plano;
}

console.log("── Chave de idempotência por AÇÃO ──");
caso("chave legada do navegador válida para ESP",
     ct.ctChaveIdempotenciaAcao("ESP-20260709-143055-ABC123", "ESP").ok === true);
caso("chave uuid válida para ESP",
     ct.ctChaveIdempotenciaAcao(`ESP-${UUID_A}`, "ESP").ok === true);
caso("'x' é RECUSADA", ct.ctChaveIdempotenciaAcao("x", "ESP").ok === false);
caso("'PAC-0001' é RECUSADA em espirometria (prefixo cruzado)",
     ct.ctChaveIdempotenciaAcao("PAC-0001", "ESP").ok === false);
caso("chave ESP legada é RECUSADA em consulta (prefixo por ação)",
     ct.ctChaveIdempotenciaAcao("ESP-20260709-143055-ABC123", "CON").ok === false);
caso("sequencial ESP-0001 é RECUSADO como chave",
     ct.ctChaveIdempotenciaAcao("ESP-0001", "ESP").ok === false);
caso("ausente → ok com chave vazia (servidor emite ID)",
     ct.ctChaveIdempotenciaAcao("", "ESP").ok === true && ct.ctChaveIdempotenciaAcao("", "ESP").chave === "");

console.log("── Fingerprint de payload ──");
const FP_A = ct.ctFingerprintPayload({ primeiro_nome: "Alfa", status_exame: "Realizado" });
caso("fingerprint é determinístico",
     FP_A === ct.ctFingerprintPayload({ status_exame: "Realizado", primeiro_nome: "Alfa" }));
caso("payload diferente → fingerprint diferente",
     FP_A !== ct.ctFingerprintPayload({ primeiro_nome: "Alfa", status_exame: "Aguardando" }));
caso("fingerprint da linha equivale ao do payload (mesmas colunas)", (() => {
  const headers = ["exame_id", "primeiro_nome", "status_exame"];
  const linha = ["ESP-x", "Alfa", "Realizado"];
  return ct.ctFingerprintDaLinha(headers, linha, ["primeiro_nome", "status_exame"]) === FP_A;
})());
// M14.3A (correção final) — substitui o FNV-1a de 32 bits (colisão sintética
// real demonstrada em auditoria) por SHA-256 sobre serialização canônica
// tipada. As checagens abaixo cobrem exatamente as garantias exigidas.
caso("fingerprint é SHA-256 hex completo (64 chars, não mais 8)",
     /^[0-9a-f]{64}$/.test(FP_A));
caso("tipo diferente não colide por coerção textual (123 número vs \"123\" string)",
     ct.ctFingerprintPayload({ valor: 123 }) !== ct.ctFingerprintPayload({ valor: "123" }));
caso("booleano não colide com string equivalente",
     ct.ctFingerprintPayload({ a: true }) !== ct.ctFingerprintPayload({ a: "true" }));
caso("vazio explícito ≠ ausência de propriedade (chaves diferentes no payload)",
     ct.ctFingerprintPayload({ a: "" }) !== ct.ctFingerprintPayload({}));
caso("concatenação sem delimitador não cria colisão (comprimento prefixado)",
     ct.ctFingerprintPayload({ a: "12", b: "3" }) !== ct.ctFingerprintPayload({ a: "1", b: "23" }));
caso("fallback legado (ctFingerprintDaLinha) também distingue tipo/presença", (() => {
  const headers = ["exame_id", "status_exame", "valor"];
  const linhaTexto  = ["ESP-x", "Realizado", "123"];
  const fpPayloadNumero = ct.ctFingerprintPayload({ status_exame: "Realizado", valor: 123 });
  const fpLinhaTexto = ct.ctFingerprintDaLinha(headers, linhaTexto, ["status_exame", "valor"]);
  // A linha legada sempre traz strings (célula de planilha); comparar contra
  // um payload com o campo como NÚMERO não deve ser aceito como o mesmo
  // fingerprint — reforça que o fallback legado não trata payload parcial
  // como replay automático por coerção de tipo.
  return fpLinhaTexto !== fpPayloadNumero;
})());

console.log("── Campos presentes (fail-closed total) ──");
caso("presença cobre campos v1 comuns (telefone enviado conta)", (() => {
  const p = ct.ctCamposPresentes("crm_espirometria", { primeiro_nome: "A", telefone: "x", paciente_id: "PAC-1" });
  return p.includes("telefone") && p.includes("primeiro_nome") && p.includes("paciente_id");
})());
caso("propriedade ausente NÃO conta (≠ enviada vazia)", (() => {
  const p = ct.ctCamposPresentes("crm_espirometria", { primeiro_nome: "A", telefone: "" });
  return p.includes("telefone") && !p.includes("origem");
})());
caso("imutáveis/técnicos nunca entram via payload", (() => {
  const p = ct.ctCamposPresentes("crm_espirometria",
    { exame_id: "hack", data_entrada: "hack", idempotency_key: "hack", primeiro_nome: "A" });
  return !p.includes("exame_id") && !p.includes("data_entrada") && !p.includes("idempotency_key");
})());

console.log("── Cenários: clique duplo, retry, timeout, concorrência, linha removida ──");
{
  const p = novaPlanilha(HEADERS_V1.slice());
  const chave = "ESP-20260709-143055-ABC123";
  const campos = { exame_id: chave, primeiro_nome: "Alfa", status_exame: "Aguardando" };
  const r1 = upsertSimulado(p, chave, campos);
  const r2 = upsertSimulado(p, chave, campos); // clique duplo
  caso("clique duplo com a mesma chave não duplica linha",
       r1.modo === "insert" && r2.modo === "patch" && p.linhas.length === 1);

  // retry após timeout (a 1ª gravação persistiu, o cliente não viu a resposta)
  const r3 = upsertSimulado(p, chave, { exame_id: chave, primeiro_nome: "Alfa", status_exame: "Realizado" });
  caso("retry após timeout vira patch (não duplica, não zera colunas)",
       r3.modo === "patch" && p.linhas.length === 1 && p.linhas[0][6] === "Realizado");

  // concorrência: duas submissões diferentes (chaves distintas) coexistem
  const chaveB = "ESP-20260709-150000-DEF456";
  upsertSimulado(p, chaveB, { exame_id: chaveB, primeiro_nome: "Beta", status_exame: "Aguardando" });
  caso("chaves distintas geram linhas distintas (sem colisão por contador)",
       p.linhas.length === 2);

  // linha removida manualmente: retry recria em vez de falhar
  p.linhas.splice(0, 1);
  const r5 = upsertSimulado(p, chave, campos);
  caso("linha removida + retry → insert novo (nunca erro silencioso)",
       r5.modo === "insert" && p.linhas.length === 2);
}

console.log();
if (FALHAS > 0) {
  console.log(`RESULTADO: ${FALHAS} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
