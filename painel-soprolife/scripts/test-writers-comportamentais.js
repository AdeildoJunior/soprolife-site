// SoproLife — Testes COMPORTAMENTAIS dos writers reais (M14.3A, 2ª rodada).
//
// Diferente de test-contratos.js (planner puro), esta suíte executa as
// funções REAIS do Apps Script — _createEspirometria, _createConsulta,
// _registrarEspirometriaFinanceiro, _registrarAtendimentoPastore e o
// conversor bloqueado — dentro de um sandbox Node (vm) com mocks fiéis de
// SpreadsheetApp/LockService/Utilities/etc. Reproduz exatamente as sondas
// que reprovaram a segunda auditoria independente:
//
//   - retry parcial NÃO limpa campos (mesma chave + payload diferente = 409);
//   - campo comum (telefone) enviado sem coluna = erro, zero mutação;
//   - clique duplo/retry/timeout convergem para UMA linha (replay);
//   - chave "x"/"PAC-0001"/prefixo cruzado recusada;
//   - lock envolve busca+validação+escrita;
//   - financeiro: id_atendimento obrigatório; ausência nunca vira 250/
//     Tabela/0 silencioso; update preserva coluna extra/fórmula/imutáveis;
//   - datas impossíveis falham no writer (31/02/2026);
//   - conversor de leads está bloqueado;
//   - Pastore staging valida data/status e não inventa zeros.
//
// 100% offline e sintético. Uso:
//   node painel-soprolife/scripts/test-writers-comportamentais.js

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const crypto = require("crypto");

const RAIZ = path.resolve(__dirname, "..");
const src = (rel) => fs.readFileSync(path.join(RAIZ, rel), "utf8");

let FALHAS = 0;
function caso(nome, cond, detalhe) {
  if (cond) {
    console.log(`  PASS: ${nome}`);
  } else {
    FALHAS += 1;
    console.log(`  FAIL: ${nome}${detalhe ? " — " + detalhe : ""}`);
  }
}

// ── Mocks do runtime Apps Script ─────────────────────────────────────────────

function criarAmbiente(opts) {
  opts = opts || {};
  const semAbas = new Set(opts.semAbas || []);
  const eventos = []; // trilha: lock/unlock/leituras/escritas — ordem real

  class MockRange {
    constructor(sheet, r, c, nr = 1, nc = 1) {
      Object.assign(this, { sheet, r, c, nr, nc });
    }
    _cell(i, j) { return this.sheet._cell(this.r + i - 1, this.c + j - 1); }
    getValues() {
      const out = [];
      for (let i = 1; i <= this.nr; i++) {
        const row = [];
        for (let j = 1; j <= this.nc; j++) row.push(this._cell(i, j).v);
        out.push(row);
      }
      return out;
    }
    getDisplayValues() { return this.getValues().map((r) => r.map((v) => String(v ?? ""))); }
    getValue() { return this._cell(1, 1).v; }
    getDisplayValue() { return String(this._cell(1, 1).v ?? ""); }
    getFormula() { return this._cell(1, 1).f || ""; }
    setFormula(f) { const c = this._cell(1, 1); c.f = f; c.v = f; return this; }
    setValue(v) {
      // Sentinela de teste (M14.3A, correção final): simula a planilha
      // rejeitando UMA célula específica (ex.: validação de dropdown), para
      // exercer o caminho honesto de escrita parcial de _aplicarPlanoUpsert.
      if (v === "__FALHA_SIMULADA__") throw new Error("falha simulada de validação (teste)");
      eventos.push(`write:${this.sheet.nome}!r${this.r}c${this.c}`);
      const c = this._cell(1, 1); c.v = v; c.f = null; return this;
    }
    setValues(vals) {
      eventos.push(`write:${this.sheet.nome}!r${this.r}c${this.c}:${this.nr}x${this.nc}`);
      for (let i = 1; i <= this.nr; i++) {
        for (let j = 1; j <= this.nc; j++) {
          const c = this._cell(i, j); c.v = vals[i - 1][j - 1]; c.f = null;
        }
      }
      return this;
    }
    clearDataValidations() { return this; }
    setFontWeight() { return this; }
    setFontColor() { return this; }
    setBackground() { return this; }
    setNote() { return this; }
    setNumberFormat() { return this; }
    setDataValidation() { return this; }
  }

  class MockSheet {
    constructor(nome, headers) {
      this.nome = nome;
      this.grid = [];
      this.maxRows = 50;
      this.maxCols = 30;
      if (headers) this.getRange(1, 1, 1, headers.length).setValues([headers]);
    }
    _cell(r, c) {
      while (this.grid.length < r) this.grid.push([]);
      const row = this.grid[r - 1];
      while (row.length < c) row.push({ v: "", f: null });
      return row[c - 1];
    }
    getName() { return this.nome; }
    getLastRow() {
      for (let r = this.grid.length; r >= 1; r--) {
        if ((this.grid[r - 1] || []).some((c) => String(c.v ?? "").trim() !== "")) return r;
      }
      return 0;
    }
    getLastColumn() {
      let max = 0;
      this.grid.forEach((row) => {
        for (let c = row.length; c >= 1; c--) {
          if (String(row[c - 1].v ?? "").trim() !== "") { if (c > max) max = c; break; }
        }
      });
      return max;
    }
    getMaxRows() { return Math.max(this.maxRows, this.grid.length); }
    getMaxColumns() { return this.maxCols; }
    insertRowsAfter(after, n) { this.maxRows = this.getMaxRows() + n; return this; }
    insertColumnsAfter(after, n) { this.maxCols += n; return this; }
    getRange(r, c, nr, nc) {
      eventos.push(`read:${this.nome}`);
      return new MockRange(this, r, c, nr ?? 1, nc ?? 1);
    }
    getDataRange() { return this.getRange(1, 1, Math.max(this.getLastRow(), 1), Math.max(this.getLastColumn(), 1)); }
    setFrozenRows() { return this; }
    autoResizeColumns() { return this; }
    autoResizeRows() { return this; }
    setColumnWidth() { return this; }
    // snapshot profundo para asserções de "zero mutação"
    snapshot() { return JSON.stringify(this.grid.map((row) => row.map((c) => [c.v, c.f]))); }
    linhas() { return this.getLastRow(); }
    valor(r, nomeColuna) {
      const headers = (this.grid[0] || []).map((c) => String(c.v).trim());
      const idx = headers.indexOf(nomeColuna);
      return idx < 0 ? undefined : String((this.grid[r - 1]?.[idx] || { v: "" }).v ?? "");
    }
  }

  const planilha = {
    abas: {},
    getSheetByName(n) { return this.abas[n] || null; },
    insertSheet(n) { const s = new MockSheet(n); this.abas[n] = s; return s; },
  };

  let uuidSeq = 0;
  let lockAtivo = false;

  const ctx = {
    console,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => planilha,
      openById: () => planilha,
      flush: () => {},
      MimeType: { JSON: "json" },
    },
    LockService: {
      getScriptLock: () => ({
        waitLock() {
          if (lockAtivo) throw new Error("lock já em uso (concorrência)");
          lockAtivo = true;
          eventos.push("lock");
        },
        releaseLock() { lockAtivo = false; eventos.push("unlock"); },
      }),
    },
    Utilities: {
      getUuid: () => {
        uuidSeq += 1;
        return `00000000-0000-4000-8000-${String(uuidSeq).padStart(12, "0")}`;
      },
      formatDate: () => "11/07/2026 10:00",
      // Mock fiel de Utilities.computeDigest (mesma assinatura do Apps Script
      // real) — ctFingerprintPayload usa isto para SHA-256; sem este mock o
      // sandbox vm cairia no fallback require("crypto"), que não reflete o
      // runtime real do Apps Script.
      DigestAlgorithm: { SHA_256: "SHA_256" },
      Charset: { UTF_8: "UTF_8" },
      computeDigest: (_algo, texto) => {
        const buf = crypto.createHash("sha256").update(String(texto), "utf8").digest();
        return Array.from(buf).map((b) => (b > 127 ? b - 256 : b));
      },
    },
    Session: { getActiveUser: () => ({ getEmail: () => "" }) },
    Logger: { log: () => {} },
    ContentService: {
      createTextOutput(t) { return { _t: t, setMimeType() { return this; }, getContent() { return this._t; } }; },
      MimeType: { JSON: "json" },
    },
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => null }) },
  };
  vm.createContext(ctx);
  vm.runInContext(src("apps-script/contratos-canonicos.gs"), ctx);
  vm.runInContext(src("apps-script/command-center-api.gs"), ctx);
  vm.runInContext(src("apps-script/pastore-staging.gs"), ctx);
  vm.runInContext(src("apps-script/converter-lead-em-paciente.gs"), ctx);

  // Pré-provisiona as abas operacionais que a operação diária espera já
  // existir (M14.3A, correção final: _inserirEventoClinicoIdempotente não
  // cria mais aba/cabeçalho — isso simula um workbook já instalado). Passe
  // { semAbas: ["CRM Espirometria"] } para simular a aba ausente.
  const preSeed = {
    "CRM Espirometria": ctx.CT_REGISTROS.crm_espirometria.v1,
    "CRM Consultas":    ctx.CT_REGISTROS.crm_consultas.v1,
  };
  Object.keys(preSeed).forEach((nome) => {
    if (semAbas.has(nome)) return;
    planilha.abas[nome] = new MockSheet(nome, preSeed[nome]);
  });
  // O pré-provisionamento acima é "instalação", não comportamento sob teste —
  // limpa os eventos de leitura/escrita gerados por ele para que a trilha
  // lock→busca→escrita de cada teste comece do zero.
  eventos.length = 0;

  const chamar = (fn, data) => JSON.parse(ctx[fn](data).getContent());
  return { ctx, planilha, eventos, chamar, MockSheet };
}

const CHAVE = "ESP-20260709-143055-ABC123";
const PAYLOAD_ESPI = () => ({
  id_atendimento: CHAVE,
  primeiro_nome: "Alfa",
  responsavel: "Adeildo",
  telefone: "21 90000-0001",
  servico: "Espirometria",
  origem: "Google",
  status_exame: "Realizado",
  data_exame: "02/07/2026",
  proximo_contato: "",
  motivo_proximo_contato: "Follow-up",
  canal: "WhatsApp",
  consentimento_whatsapp: "Sim",
});

// ── 1) Espirometria: insert, replay, 409, retry parcial ─────────────────────
{
  const amb = criarAmbiente();
  console.log("── Espirometria (writer real): identidade × idempotência ──");

  const r1 = amb.chamar("_createEspirometria", PAYLOAD_ESPI());
  const aba = amb.planilha.getSheetByName("CRM Espirometria");
  caso("insert com chave: sucesso e 1 linha", r1.ok === true && aba.linhas() === 2);
  caso("ID canônico é do SERVIDOR (UUID), nunca a chave do navegador",
       /^ESP-00000000-0000-4000-8000-/.test(r1.id) && r1.id !== CHAVE);
  caso("chave vai para a coluna técnica idempotency_key",
       aba.valor(2, "idempotency_key") === CHAVE);
  caso("fingerprint gravado (SHA-256 hex completo)",
       /^[0-9a-f]{64}$/.test(aba.valor(2, "idempotency_fingerprint") || ""));
  caso("resposta informa campos persistidos + contrato",
       Array.isArray(r1.campos_persistidos) && r1.campos_persistidos.includes("telefone") &&
       r1.contrato_versao === "m14.3a");

  const snap = aba.snapshot();
  const r2 = amb.chamar("_createEspirometria", PAYLOAD_ESPI()); // clique duplo / retry pós-timeout
  caso("clique duplo (mesma chave+payload) = replay, mesma linha e mesmo ID",
       r2.ok === true && r2.replayed === true && r2.id === r1.id && aba.linhas() === 2);
  caso("replay não regrava NENHUMA célula", aba.snapshot() === snap);

  // Sonda da auditoria: retry PARCIAL (só nome/responsável/chave) — o antigo
  // full-map limparia telefone/origem/status/data/canal/consentimento.
  const rParcial = amb.chamar("_createEspirometria",
    { id_atendimento: CHAVE, primeiro_nome: "Alfa", responsavel: "Adeildo" });
  caso("retry parcial com a mesma chave = conflito 409 (payload difere)",
       rParcial.ok === false && rParcial.code === 409);
  caso("retry parcial NÃO limpa nenhum campo existente", aba.snapshot() === snap);
  caso("telefone continua na linha", aba.valor(2, "telefone") === "21 90000-0001");
  caso("data_exame/status/canal/consentimento intactos",
       aba.valor(2, "data_exame") === "02/07/2026" && aba.valor(2, "status_exame") === "Realizado" &&
       aba.valor(2, "canal") === "WhatsApp" && aba.valor(2, "consentimento_whatsapp") === "Sim");

  const rOutra = amb.chamar("_createEspirometria",
    { ...PAYLOAD_ESPI(), id_atendimento: "ESP-20260710-090000-DEF456", primeiro_nome: "Beta" });
  caso("chave distinta = linha nova", rOutra.ok === true && aba.linhas() === 3);

  const idx = amb.eventos.indexOf("lock");
  const idxUnlock = amb.eventos.indexOf("unlock");
  const leituraAba = amb.eventos.findIndex((e) => e.startsWith("read:CRM Espirometria"));
  caso("lock envolve busca+validação+escrita (busca só depois do lock)",
       idx >= 0 && idxUnlock > idx && leituraAba > idx);
}

// ── 2) Chaves inválidas por ação ─────────────────────────────────────────────
{
  const amb = criarAmbiente();
  console.log("── Chaves de idempotência inválidas ──");
  for (const chave of ["x", "PAC-0001", "CON-20260709-143055-ABC123", "ESP-0001"]) {
    const r = amb.chamar("_createEspirometria", { ...PAYLOAD_ESPI(), id_atendimento: chave });
    caso(`chave '${chave}' é recusada (400) sem gravar`,
         r.ok === false && r.code === 400 &&
         (amb.planilha.getSheetByName("CRM Espirometria")?.linhas() || 0) <= 1);
  }
  const rSem = amb.chamar("_createEspirometria", { ...PAYLOAD_ESPI(), id_atendimento: "" });
  caso("sem chave: insert normal com ID do servidor", rSem.ok === true && /^ESP-0000/.test(rSem.id));
}

// ── 3) Fail-closed TOTAL: campo comum sem coluna ─────────────────────────────
{
  const amb = criarAmbiente();
  console.log("── Fail-closed para TODO campo enviado (sonda da auditoria) ──");
  // Aba pré-existente SEM a coluna telefone (mas com as obrigatórias).
  const headersSemTelefone = ["exame_id", "data_entrada", "primeiro_nome", "servico",
                              "origem", "status_exame", "data_exame", "proximo_contato",
                              "motivo_proximo_contato", "canal", "responsavel",
                              "consentimento_whatsapp"];
  const aba = new amb.MockSheet("CRM Espirometria", headersSemTelefone);
  amb.planilha.abas["CRM Espirometria"] = aba;
  const snap = aba.snapshot();

  const r = amb.chamar("_createEspirometria", PAYLOAD_ESPI());
  caso("telefone enviado + coluna ausente = ERRO (nunca sucesso)",
       r.ok === false && r.code === 422 && String(r.error).includes("telefone"));
  caso("nenhuma célula foi modificada", aba.snapshot() === snap);

  // cabeçalho sem coluna OBRIGATÓRIA
  const aba2 = new amb.MockSheet("CRM Espirometria", ["primeiro_nome", "telefone"]);
  amb.planilha.abas["CRM Espirometria"] = aba2;
  const r2 = amb.chamar("_createEspirometria", PAYLOAD_ESPI());
  caso("cabeçalho sem coluna obrigatória = erro de schema claro",
       r2.ok === false && String(r2.error).includes("incompatível"));
}

// ── 4) Datas impossíveis no writer real ──────────────────────────────────────
{
  const amb = criarAmbiente();
  console.log("── Datas: writer real recusa impossíveis, preserva precisão ──");
  const r = amb.chamar("_createEspirometria", { ...PAYLOAD_ESPI(), data_exame: "31/02/2026" });
  caso("31/02/2026 é recusada (400)", r.ok === false && r.code === 400);
  const r2 = amb.chamar("_createEspirometria",
    { ...PAYLOAD_ESPI(), id_atendimento: "", data_exame: "06/2026" });
  const aba = amb.planilha.getSheetByName("CRM Espirometria");
  caso("06/2026 aceita como mês (valor preservado, sem dia inventado)",
       r2.ok === true && aba.valor(2, "data_exame") === "06/2026");
  const r3 = amb.chamar("_createEspirometria",
    { ...PAYLOAD_ESPI(), id_atendimento: "", data_exame: "", primeiro_nome: "Gama" });
  caso("data vazia permanece vazia (nunca vira hoje)",
       r3.ok === true && aba.valor(3, "data_exame") === "");
}

// ── 5) Consulta: mesmos contratos ────────────────────────────────────────────
{
  const amb = criarAmbiente();
  console.log("── Consulta (writer real) ──");
  const payload = { primeiro_nome: "Alfa", responsavel: "Adeildo", tipo_consulta: "Pneumo",
                    status: "Realizada", data_consulta: "05/07/2026",
                    idempotency_key: "CON-20260709-143055-ABC123" };
  const r1 = amb.chamar("_createConsulta", payload);
  const aba = amb.planilha.getSheetByName("CRM Consultas");
  caso("insert consulta ok, ID do servidor", r1.ok === true && /^CON-0000/.test(r1.id));
  const r2 = amb.chamar("_createConsulta", payload);
  caso("replay consulta converge (1 linha)", r2.replayed === true && aba.linhas() === 2);
  const r3 = amb.chamar("_createConsulta", { ...payload, status: "Agendada" });
  caso("mesma chave + payload diferente = 409", r3.ok === false && r3.code === 409);
}

// ── 6) Financeiro: sem órfão novo, sem defaults inventados ───────────────────
{
  const amb = criarAmbiente();
  console.log("── Financeiro (writer real): id obrigatório e ausência ausente ──");

  const base = () => ({
    id_atendimento: CHAVE,
    data_exame: "02/07/2026",
    status_exame: "Realizado",
    status_pagamento: "Recebido",
    valor_cobrado: "230",
    valor_recebido: "230",
    forma_pagamento: "Pix",
    local_atendimento: "Domiciliar",
    observacao_financeira: "",
  });

  const rSemId = amb.chamar("_registrarEspirometriaFinanceiro", { ...base(), id_atendimento: "" });
  caso("id_atendimento vazio = erro (nenhum órfão novo pela API)",
       rSemId.ok === false && rSemId.code === 400 && String(rSemId.error).includes("obrigatório"));
  const rIdRuim = amb.chamar("_registrarEspirometriaFinanceiro", { ...base(), id_atendimento: "x" });
  caso("id_atendimento 'x' = erro de formato", rIdRuim.ok === false && rIdRuim.code === 400);
  caso("nada foi gravado nos erros", !amb.planilha.getSheetByName("Financeiro_Lancamentos")?.getLastRow?.() ||
       amb.planilha.getSheetByName("Financeiro_Lancamentos").linhas() <= 1);

  const r1 = amb.chamar("_registrarEspirometriaFinanceiro", base());
  const aba = amb.planilha.getSheetByName("Financeiro_Lancamentos");
  caso("insert financeiro ok", r1.ok === true && aba.linhas() === 2);
  caso("valor_tabela AUSENTE permanece vazio (nunca 250)", aba.valor(2, "valor_tabela") === "");
  caso("origem_preco AUSENTE permanece vazia (nunca Tabela)", aba.valor(2, "origem_preco") === "");
  caso("desconto não derivado sem valor_tabela", aba.valor(2, "desconto") === "");
  caso("id_lancamento é FIN-uuid do servidor", /^FIN-0000/.test(aba.valor(2, "id_lancamento")));
  caso("criado_em é timestamp ISO real (servidor), nunca vazio", /^\d{4}-\d{2}-\d{2}T/.test(aba.valor(2, "criado_em")));

  // Sonda da auditoria: criado_em enviado pelo cliente deve ser IGNORADO —
  // o servidor sempre emite o próprio timestamp no insert.
  const criadoEmForjado = "2001-01-01T00:00:00.000Z";
  const rForjado = amb.chamar("_registrarEspirometriaFinanceiro",
    { ...base(), id_atendimento: "ESP-20260710-100000-FAKE01", criado_em: criadoEmForjado });
  caso("criado_em forjado pelo cliente é ignorado (servidor emite o seu)",
       rForjado.ok === true && aba.valor(3, "criado_em") !== criadoEmForjado &&
       /^\d{4}-\d{2}-\d{2}T/.test(aba.valor(3, "criado_em")));
  caso("resposta avisa que criado_em do cliente foi ignorado",
       rForjado.aviso_criado_em_ignorado === true);

  const rRecebidoSemValor = amb.chamar("_registrarEspirometriaFinanceiro",
    { ...base(), id_atendimento: "ESP-20260710-090000-DEF456", valor_recebido: "" });
  caso("Recebido sem valor_recebido = erro (nunca 0 silencioso)",
       rRecebidoSemValor.ok === false && String(rRecebidoSemValor.error).includes("obrigatório"));

  const rPendente = amb.chamar("_registrarEspirometriaFinanceiro",
    { id_atendimento: "ESP-20260710-090000-DEF456", data_exame: "10/07/2026",
      status_exame: "Realizado", status_pagamento: "Pendente", valor_cobrado: "180",
      local_atendimento: "Clínica" });
  caso("Pendente sem valor_recebido → 0 pela REGRA FORMAL (única exceção)",
       rPendente.ok === true && aba.valor(4, "valor_recebido") === "0");

  // Update por id_atendimento (chave de negócio): patch por presença preserva
  // coluna extra da equipe, fórmula e imutáveis.
  const headers = aba.getRange(1, 1, 1, aba.getLastColumn()).getValues()[0];
  const extraCol = headers.length + 1;
  aba.getRange(1, extraCol).setValue("anotacao_da_equipe");
  aba.getRange(2, extraCol).setValue("valor conferido pelo sócio");
  const formulaCol = extraCol + 1;
  aba.getRange(1, formulaCol).setValue("calculo_manual");
  aba.getRange(2, formulaCol).setFormula("=I2-J2");
  const criadoEmAntes = aba.valor(2, "criado_em");

  const rUpd = amb.chamar("_registrarEspirometriaFinanceiro",
    { ...base(), status_pagamento: "Recebido", valor_recebido: "230", criado_em: criadoEmForjado });
  caso("update de pagamento (chave de negócio) tem sucesso", rUpd.ok === true);
  caso("coluna extra da equipe preservada no patch",
       aba.valor(2, "anotacao_da_equipe") === "valor conferido pelo sócio");
  caso("fórmula manual preservada no patch",
       aba.getRange(2, formulaCol).getFormula() === "=I2-J2");
  caso("imutáveis preservados (criado_em/id_atendimento) mesmo com criado_em forjado no update",
       aba.valor(2, "criado_em") === criadoEmAntes && aba.valor(2, "id_atendimento") === CHAVE);
  caso("campo ausente no update não vira default (valor_tabela segue vazio)",
       aba.valor(2, "valor_tabela") === "");
}

// ── 7) Conversor bloqueado ───────────────────────────────────────────────────
{
  const amb = criarAmbiente();
  console.log("── Conversor de leads: bloqueado ──");
  let lancou = false, msg = "";
  try { amb.ctx._cvConverterLeadCore(null, null, 2, "Realizou espirometria"); }
  catch (e) { lancou = true; msg = e.message; }
  caso("_cvConverterLeadCore lança erro de bloqueio",
       lancou && msg.includes("BLOQUEADA"));
  caso("stub não contém dedupe por telefone nem deleteRow",
       !src("apps-script/converter-lead-em-paciente.gs").includes("_cvConjuntoTelefones") &&
       !src("apps-script/converter-lead-em-paciente.gs").includes("deleteRow("));
}

// ── 8) Pastore staging: data validada, sem zeros/Realizado inventados ───────
{
  const amb = criarAmbiente();
  console.log("── Pastore staging (writer real) ──");
  const basePastore = () => ({
    data_atendimento: "08/07/2026", paciente_nome: "Epsilon Sintetico",
    tipo_exame: "Espirometria", status: "Realizado",
  });
  const rData = amb.chamar("_registrarAtendimentoPastore", { ...basePastore(), data_atendimento: "31/02/2026" });
  caso("data impossível recusada no staging", rData.ok === false && rData.code === 400);
  let rSemStatus;
  try {
    rSemStatus = amb.chamar("_registrarAtendimentoPastore", { ...basePastore(), status: "" });
  } catch (e) {
    rSemStatus = { ok: false, error: e.message };
  }
  caso("status ausente NÃO vira 'Realizado' (erro)",
       rSemStatus.ok === false && /status/i.test(String(rSemStatus.error)));
  const r1 = amb.chamar("_registrarAtendimentoPastore", basePastore());
  const aba = amb.planilha.getSheetByName("Parceria Pastore - Atendimentos");
  caso("insert staging ok", r1.ok === true);
  caso("repasse/custos ausentes ficam em BRANCO (nunca 0 factual)",
       aba.valor(2, "repasse_pastore") === "" && aba.valor(2, "custo_deslocamento") === "" &&
       aba.valor(2, "custo_profissional") === "" && aba.valor(2, "outros_custos") === "");
}

// ── 8b) status_exame/status vazio EXPLÍCITO ≠ ausência (sonda da auditoria) ──
{
  const amb = criarAmbiente();
  console.log("── Vazio explícito não vira default silencioso (status_exame/status) ──");

  // Payload SEM a chave status_exame (ausência real) → default de insert.
  const { status_exame: _omit, ...payloadSemStatus } = PAYLOAD_ESPI();
  const rSemStatus = amb.chamar("_createEspirometria", { ...payloadSemStatus, id_atendimento: "" });
  const abaEspi = amb.planilha.getSheetByName("CRM Espirometria");
  caso("status_exame AUSENTE (chave não enviada) vira 'Aguardando' (default de insert)",
       rSemStatus.ok === true && abaEspi.valor(2, "status_exame") === "Aguardando");

  // Payload COM a chave status_exame:"" (vazio explícito) → nunca vira default.
  const rEspiVazio = amb.chamar("_createEspirometria",
    { ...PAYLOAD_ESPI(), id_atendimento: "", primeiro_nome: "Vazio", status_exame: "" });
  caso("status_exame vazio EXPLICITAMENTE enviado permanece vazio (nunca 'Aguardando')",
       rEspiVazio.ok === true && abaEspi.valor(3, "status_exame") === "");

  const amb2 = criarAmbiente();
  const rConsultaSemStatus = amb2.chamar("_createConsulta",
    { primeiro_nome: "Beta", responsavel: "Adeildo" });
  const abaConsulta = amb2.planilha.getSheetByName("CRM Consultas");
  caso("status AUSENTE (chave não enviada) vira 'Agendada' (default de insert)",
       rConsultaSemStatus.ok === true && abaConsulta.valor(2, "status") === "Agendada");

  const rConsultaVazio = amb2.chamar("_createConsulta",
    { primeiro_nome: "Alfa", responsavel: "Adeildo", status: "" });
  caso("status vazio EXPLICITAMENTE enviado permanece vazio (nunca 'Agendada')",
       rConsultaVazio.ok === true && abaConsulta.valor(3, "status") === "");
}

// ── 9) registrarInteracaoClinica: fail-closed + presença real (M14.3A final) ─
{
  const amb = criarAmbiente();
  console.log("── registrarInteracaoClinica (writer real): fail-closed + presença ──");

  const HEADERS_CLINICAS = amb.ctx.CT_REGISTROS.crm_clinicas.v1.slice();
  const aba = new amb.MockSheet("CRM Clinicas", HEADERS_CLINICAS);
  amb.planilha.abas["CRM Clinicas"] = aba;

  const base = () => ({ nome_clinica: "Clínica Alfa", etapa: "Em conversa", responsavel: "Adeildo" });

  const r1 = amb.chamar("_registrarInteracaoClinica", base());
  caso("insert (clínica nova) tem sucesso e cria 1 linha",
       r1.ok === true && r1.created === true && aba.linhas() === 2);
  caso("resposta lista campos_persistidos",
       Array.isArray(r1.campos_persistidos) && r1.campos_persistidos.includes("etapa"));

  const r2 = amb.chamar("_registrarInteracaoClinica",
    { ...base(), etapa: "Proposta enviada", prioridade: "Alta", data_proxima_acao: "10/07/2026" });
  caso("update por nome_clinica normalizado (mesma linha, não duplica)",
       r2.ok === true && r2.updated === true && aba.linhas() === 2);
  caso("prioridade e data_proxima_acao enviadas foram gravadas",
       aba.valor(2, "prioridade") === "Alta" && aba.valor(2, "data_proxima_acao") === "10/07/2026");

  const r3 = amb.chamar("_registrarInteracaoClinica", { ...base(), etapa: "Reunião marcada" });
  caso("ausência de prioridade/data_proxima_acao NÃO limpa valor existente (patch por presença)",
       r3.ok === true && aba.valor(2, "prioridade") === "Alta" &&
       aba.valor(2, "data_proxima_acao") === "10/07/2026");

  const r4 = amb.chamar("_registrarInteracaoClinica", { ...base(), data_proxima_acao: "" });
  caso("vazio EXPLÍCITO em data_proxima_acao limpa o campo (≠ ausência)",
       r4.ok === true && aba.valor(2, "data_proxima_acao") === "");

  // Sonda da auditoria: campo SUPORTADO (prioridade) enviado sem a coluna
  // correspondente na aba deve ser ERRO, nunca sucesso com mutação parcial.
  const HEADERS_SEM_PRIORIDADE = HEADERS_CLINICAS.filter((h) => h !== "prioridade");
  const abaSemPrioridade = new amb.MockSheet("CRM Clinicas", HEADERS_SEM_PRIORIDADE);
  amb.planilha.abas["CRM Clinicas"] = abaSemPrioridade;
  const snapSemPrioridade = abaSemPrioridade.snapshot();
  const r5 = amb.chamar("_registrarInteracaoClinica",
    { nome_clinica: "Clínica Beta", etapa: "Em conversa", responsavel: "Adeildo", prioridade: "Alta" });
  caso("prioridade sem coluna = ERRO (nunca sucesso com mutação parcial)",
       r5.ok === false && r5.code === 422 && String(r5.error).includes("prioridade"));
  caso("nenhuma célula foi modificada (zero mutação antes da validação)",
       abaSemPrioridade.snapshot() === snapSemPrioridade);
}

// ── 10) _upsertContatoB2B: fail-closed + presença real (M14.3A final) ───────
{
  const amb = criarAmbiente();
  console.log("── _upsertContatoB2B (writer real): fail-closed + presença ──");

  const HEADERS_CONTATOS = amb.ctx.CT_REGISTROS.crm_contatos_b2b.v1.slice();
  const aba = new amb.MockSheet("CRM Contatos B2B", HEADERS_CONTATOS);
  amb.planilha.abas["CRM Contatos B2B"] = aba;

  const infoBase = () => ({
    contatoId: "", clinicaId: "CLI-fixo-teste", nomeContato: "Juan",
    telefone: "21 90000-0002", responsavel: "Adeildo", observacao: "obs",
    camposClienteOpcionais: { email: "juan@example.com" },
  });

  const id1 = amb.ctx._upsertContatoB2B(infoBase());
  caso("insert cria contato com ID do servidor", /^CONT-00000000/.test(id1) && aba.linhas() === 2);
  caso("e-mail enviado foi gravado", aba.valor(2, "email") === "juan@example.com");

  const id2 = amb.ctx._upsertContatoB2B({ ...infoBase(), contatoId: id1, camposClienteOpcionais: {} });
  caso("update sem enviar e-mail (ausência) preserva o valor existente",
       id2 === id1 && aba.valor(2, "email") === "juan@example.com" && aba.linhas() === 2);

  const id3 = amb.ctx._upsertContatoB2B({ ...infoBase(), contatoId: id1, camposClienteOpcionais: { email: "" } });
  caso("update com e-mail vazio EXPLÍCITO limpa o campo (≠ ausência)",
       id3 === id1 && aba.valor(2, "email") === "");

  // Sonda da auditoria: campo suportado (cargo) sem coluna na aba = ERRO,
  // nunca sucesso com mutação parcial de outras células.
  const HEADERS_SEM_CARGO = HEADERS_CONTATOS.filter((h) => h !== "cargo");
  const abaSemCargo = new amb.MockSheet("CRM Contatos B2B", HEADERS_SEM_CARGO);
  amb.planilha.abas["CRM Contatos B2B"] = abaSemCargo;
  const snapSemCargo = abaSemCargo.snapshot();
  let erroCargo = null;
  try {
    amb.ctx._upsertContatoB2B({
      contatoId: "", clinicaId: "CLI-outro", nomeContato: "Beatriz",
      telefone: "", responsavel: "Adeildo", observacao: "",
      camposClienteOpcionais: { cargo: "Gerente" },
    });
  } catch (e) { erroCargo = e; }
  caso("cargo sem coluna = ERRO (nunca sucesso com mutação parcial)",
       erroCargo !== null && String(erroCargo.message).includes("cargo"));
  caso("nenhuma célula foi modificada (zero mutação antes da validação)",
       abaSemCargo.snapshot() === snapSemCargo);
}

// ── 10b) Escrita parcial honesta: erro estruturado, sem rollback (M14.3A) ───
{
  const amb = criarAmbiente();
  console.log("── _aplicarPlanoUpsert: escrita parcial documentada honestamente ──");

  const HEADERS_CLINICAS = amb.ctx.CT_REGISTROS.crm_clinicas.v1.slice();
  const aba = new amb.MockSheet("CRM Clinicas", HEADERS_CLINICAS);
  amb.planilha.abas["CRM Clinicas"] = aba;

  amb.chamar("_registrarInteracaoClinica",
    { nome_clinica: "Clínica Gama", etapa: "Em conversa", responsavel: "Adeildo" });

  const rFalhaParcial = amb.chamar("_registrarInteracaoClinica", {
    nome_clinica: "Clínica Gama", etapa: "Reunião marcada", responsavel: "Adeildo",
    prioridade: "Alta", data_proxima_acao: "__FALHA_SIMULADA__",
  });
  caso("erro estruturado (500), nunca silencioso", rFalhaParcial.ok === false && rFalhaParcial.code === 500);
  caso("campos_gravados_parcialmente lista o que JÁ foi persistido nesta tentativa",
       Array.isArray(rFalhaParcial.campos_gravados_parcialmente) &&
       rFalhaParcial.campos_gravados_parcialmente.includes("etapa") &&
       rFalhaParcial.campos_gravados_parcialmente.includes("prioridade"));
  caso("campos_com_falha aponta exatamente a célula rejeitada",
       Array.isArray(rFalhaParcial.campos_com_falha) &&
       rFalhaParcial.campos_com_falha.includes("data_proxima_acao"));
  caso("a célula que falhou realmente não foi gravada (planilha reflete a falha real)",
       aba.valor(2, "data_proxima_acao") === "");
  caso("as células que tiveram sucesso ANTES da falha ficam persistidas (sem rollback nesta fase)",
       aba.valor(2, "etapa") === "Reunião marcada" && aba.valor(2, "prioridade") === "Alta");
}

// ── 11) Aba inexistente: operação diária NUNCA cria aba/cabeçalho ───────────
{
  const amb = criarAmbiente({ semAbas: ["CRM Consultas"] });
  console.log("── Aba inexistente (sonda da auditoria): erro e zero criação ──");

  const r = amb.chamar("_createConsulta", {
    primeiro_nome: "Alfa", responsavel: "Adeildo", tipo_consulta: "Pneumo",
    status: "Realizada", paciente_id: "PAC-0001", // campo v2, aba nem existe
  });
  caso("aba ausente = erro 422 (nunca cria a aba)",
       r.ok === false && r.code === 422 && String(r.error).includes("não existe"));
  caso("nenhuma aba foi criada (fail-closed antes de qualquer mutação estrutural)",
       amb.planilha.getSheetByName("CRM Consultas") === null);
}

console.log();
if (FALHAS > 0) {
  console.log(`RESULTADO: ${FALHAS} falha(s).`);
  process.exit(1);
}
console.log("RESULTADO: todos os casos passaram.");
