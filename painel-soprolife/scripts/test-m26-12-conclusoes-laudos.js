#!/usr/bin/env node
/* M26.12 — conclusão de laudos (frases frequentes, erro perto da ação,
 * duplo clique) e devolução administrativa para correção médica.
 *
 * Navegador real, API simulada em memória, sem conexão com dados reais.
 * PLAYWRIGHT_MODULE pode apontar para uma instalação local já disponível.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const panel = path.resolve(__dirname, "..");
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m2612-ui-"));

const CATALOG = {
  conclusoes: [
    { codigo: "NORMAL", rotulo: "Normal", texto: "Espirometria dentro dos limites da normalidade.", personalizado: false },
    { codigo: "PERSONALIZADO", rotulo: "Personalizada", texto: "", personalizado: true },
  ],
  complementos_bd: [
    { codigo: "BD_NAO_REALIZADO", rotulo: "Sem BD", texto: "" },
    { codigo: "BD_SEM_RESPOSTA", rotulo: "Sem resposta", texto: "Sem resposta significativa ao broncodilatador." },
  ],
  exame_com_pos_bd: false,
};

function baseDetail(overrides) {
  return {
    id: "doc-1",
    public_code: "LAU-000001",
    status: "em_elaboracao",
    corrects_document_id: null,
    correction_reason_code: null,
    locked: false,
    current_version_id: null,
    versoes: [],
    patient: { full_name: "Paciente Sintético", date_of_birth: "2000-01-01", public_code: "PES-000001" },
    exam: { public_code: "ESP-000001", exam_date: "2027-01-10", exam_time: "09:00", post_bronchodilator: false },
    location: null,
    ...overrides,
  };
}

async function commonSetup(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  await page.route("**/*", (route) => route.abort());
  await page.route("**/data/m15-config.json", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      enabled: true,
      reports_mode: "production",
      reports_enabled: true,
      api_base: "/painel-soprolife/api/m15",
    }),
  }));
  // M26.12 — `report-workflow.js` faz um `fetch()` cru (não via
  // `window.SoproM15`) para ler o manifesto de boot. A partir de
  // `page.setContent` a página fica em `about:blank`, sem origem válida
  // para resolver uma URL relativa — o fetch falhava antes de qualquer
  // rota interceptar nada. Navegar para uma origem sintética (nunca
  // resolvida de verdade: toda rota é interceptada) resolve isso.
  await page.route("https://sopro-sintetico.test/painel/", (route) => route.fulfill({
    contentType: "text/html",
    body: '<html lang="pt-BR"><body style="margin:0;padding:12px;box-sizing:border-box">'
      + '<main style="max-width:1160px;margin:auto;min-width:0">'
      + '<div id="reportWorkflowRoot"></div></main></body></html>',
  }));
  await page.goto("https://sopro-sintetico.test/painel/");
  const html = fs.readFileSync(path.join(panel, "index.html"), "utf8");
  for (const match of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) {
    try {
      await page.addStyleTag({ content: fs.readFileSync(path.join(panel, match[1]), "utf8") });
    } catch (_e) { /* folha opcional não encontrada — segue sem ela */ }
  }
  return errors;
}

async function runMedicaScenario(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = await commonSetup(page);
  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS (médica) ${name}`); }

  const QUEUE_ITEM = {
    document_id: "doc-1", status: "em_elaboracao",
    patient: { full_name: "Paciente Sintético" }, exam_code: "ESP-000001", report_code: "LAU-000001",
    location_key: "pastore", location_name: "Pastore", is_corrective: false, correction_reason_code: null, locked: false,
  };

  await page.evaluate(({ catalog, detail, queueItem }) => {
    window.calls = [];
    window.SoproM15 = {
      hasToken: () => true,
      can: () => false,
      getUser: () => ({ nome: "Dra. Sintética Ana", papeis: ["medico"] }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "application/pdf" }),
      api: async (url, options) => {
        const method = (options && options.method) || "GET";
        const body = options && options.body ? JSON.parse(options.body) : null;
        window.calls.push({ url, method, body });
        if (url.startsWith("/laudos/meus")) return [queueItem];
        if (url === "/laudos/templates?catalog=clinical") return [];
        if (url === "/laudos/assinatura-externa/pendentes") return { laudos: [] };
        if (url === "/laudos/doc-1") return detail;
        if (url.endsWith("/catalogo-conclusoes")) return catalog;
        if (url.endsWith("/documentos")) return [];
        if (url.endsWith("/laudo/previa")) {
          if (body && body.conclusion_code === "PERSONALIZADO" && !body.conclusion_custom_text && !body.final_text) {
            const err = new Error("Escreva a conclusão personalizada.");
            err.code = "texto_personalizado_ausente";
            throw err;
          }
          return { final_text: body.final_text || "", preview_version_id: "prev-1", final_text_sha256: "hash1" };
        }
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { catalog: CATALOG, detail: baseDetail(), queueItem: QUEUE_ITEM });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-queue-item");
  await page.locator("[data-report-open]").click();
  await page.waitForSelector("#reportFinalText");

  await check("frases frequentes aparecem e clique insere no texto final", async () => {
    const chips = page.locator(".report-frequent-phrase-chip");
    assert.ok((await chips.count()) >= 5, "esperava ao menos 5 frases frequentes");
    assert.equal(await page.locator("#reportFinalText").inputValue(), "");
    await chips.first().click();
    assert.equal(await page.locator("#reportFinalText").inputValue(), "Espirometria dentro dos limites da normalidade.");
  });

  await check("duas frases se combinam com quebra de linha, sem apagar o que já existe", async () => {
    await page.locator(".report-frequent-phrase-chip").nth(1).click();
    assert.equal(
      await page.locator("#reportFinalText").inputValue(),
      "Espirometria dentro dos limites da normalidade.\nSem resposta significativa ao broncodilatador."
    );
  });

  await check("clicar na mesma frase de novo não duplica", async () => {
    const antes = await page.locator("#reportFinalText").inputValue();
    await page.locator(".report-frequent-phrase-chip").first().click();
    assert.equal(await page.locator("#reportFinalText").inputValue(), antes);
    assert.match(await page.locator("#reportStatus").innerText(), /já está no texto/);
  });

  await check("texto livre continua editável por cima das frases inseridas", async () => {
    await page.locator("#reportFinalText").fill("Texto totalmente reescrito pela médica.");
    assert.equal(await page.locator("#reportFinalText").inputValue(), "Texto totalmente reescrito pela médica.");
  });

  await check("selecionar uma conclusão do catálogo não apaga o que a médica editou", async () => {
    await page.getByRole("button", { name: "Normal", exact: true }).click();
    assert.equal(await page.locator("#reportFinalText").inputValue(), "Texto totalmente reescrito pela médica.");
    assert.match(await page.locator("#reportStatus").innerText(), /texto que você editou foi preservado/);
  });

  await check("erro de validação foca a mensagem perto da ação, sem apagar o texto digitado", async () => {
    await page.evaluate(() => { document.getElementById("reportFinalText").value = ""; });
    await page.getByRole("button", { name: "Personalizada", exact: true }).click();
    await page.locator('button[type="submit"].report-conclude-cta').click();
    await page.waitForFunction(() => document.getElementById("reportStatus").textContent.includes("personalizada"));
    assert.equal(
      await page.evaluate(() => document.activeElement === document.getElementById("reportStatus")),
      true,
      "o foco deveria ir para a mensagem de status"
    );
    // O erro veio do servidor por texto vazio; o campo continua vazio (não
    // foi "apagado por engano" pela chamada — ele já estava vazio antes).
    assert.equal(await page.locator("#reportFinalText").inputValue(), "");
  });

  await check("botão desabilita no clique e duplo disparo não gera duas chamadas de prévia", async () => {
    await page.locator(".report-frequent-phrase-chip").first().click();
    const antes = await page.evaluate(() =>
      window.calls.filter((c) => c.url.endsWith("/laudo/previa")).length
    );
    await page.evaluate(() => {
      const botao = document.querySelector("[data-report-preview-only]");
      botao.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      // Disparado de novo, síncrono, antes de qualquer `await` ceder o
      // controle: um botão HTML `disabled` não entrega clique nenhum a
      // ouvinte nenhum — nativo do navegador, não é o app que filtra.
      botao.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await page.waitForFunction(
      (n) => window.calls.filter((c) => c.url.endsWith("/laudo/previa")).length > n,
      antes
    );
    const depois = await page.evaluate(() =>
      window.calls.filter((c) => c.url.endsWith("/laudo/previa")).length
    );
    assert.equal(depois, antes + 1, "duplo disparo não deveria gerar duas chamadas de prévia");
  });

  await check("spellcheck habilitado no texto final", async () => {
    assert.equal(await page.locator("#reportFinalText").getAttribute("spellcheck"), "true");
  });

  await check("larguras 1440/1024/768/430/390 sem overflow horizontal", async () => {
    for (const width of [1440, 1024, 768, 430, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        true,
        `overflow horizontal em ${width}px`
      );
      await page.screenshot({ path: path.join(screenshots, `medica-${width}.png`), fullPage: true });
    }
  });

  assert.deepEqual(errors, []);
  await page.close();
  return passed;
}

async function runAdminScenario(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = await commonSetup(page);
  let passed = 0;
  async function check(name, run) { await run(); passed++; console.log(`PASS (admin) ${name}`); }

  const items = [
    { document_id: "doc-liberado", patient: { full_name: "Paciente Liberado" }, report_code: "LAU-000002", exam_code: "ESP-000002",
      exam_date: "2027-01-05", estado: "aguardando_conferencia", estado_rotulo: "Aguardando conferência",
      status_clinico: "liberado", has_corrective: false, released_at: "2027-01-05T12:00:00Z",
      physician_profile_id: "profile-1", assinado: null, resultado: { existe: false } },
    { document_id: "doc-pendente", patient: { full_name: "Paciente Pendente" }, report_code: "LAU-000003", exam_code: "ESP-000003",
      exam_date: "2027-01-06", estado: "aguardando_pdf", estado_rotulo: "Aguardando PDF",
      status_clinico: "atribuido", has_corrective: false, released_at: null,
      physician_profile_id: "profile-1", assinado: null, resultado: { existe: false } },
    { document_id: "doc-falha", patient: { full_name: "Paciente Falha" }, report_code: "LAU-000004", exam_code: "ESP-000004",
      exam_date: "2027-01-07", estado: "aguardando_conferencia", estado_rotulo: "Aguardando conferência",
      status_clinico: "assinado", has_corrective: false, released_at: null,
      physician_profile_id: "profile-1", assinado: null, resultado: { existe: false } },
  ];

  await page.evaluate(({ items }) => {
    window.calls = [];
    window.userRoles = ["admin", "operacional"];
    window.SoproM15 = {
      hasToken: () => true,
      can: (role) => window.userRoles.includes(role),
      getUser: () => ({ nome: "Admin Sintético", papeis: window.userRoles }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob(),
      api: async (url, options) => {
        const method = (options && options.method) || "GET";
        const body = options && options.body ? JSON.parse(options.body) : null;
        window.calls.push({ url, method, body });
        if (url === "/laudos") return [];
        if (url === "/laudos/medicos-disponiveis") return [];
        if (url === "/laudos/exames?somente_sem_laudo=true") return { itens: [] };
        if (url === "/laudos/assinatura-externa/fila") {
          return { estados: [{ chave: "aguardando_conferencia", rotulo: "Aguardando conferência", total: 2 }], itens: items };
        }
        if (url === "/laudos/exames/encerrados") return { itens: [] };
        if (url === "/laudos/exames/motivos-encerramento") return { motivos: [] };
        if (url === "/laudos/admin/medicos") return [];
        if (url.endsWith("/retornar-para-correcao")) {
          const id = url.split("/")[2];
          if (id === "doc-falha") {
            const err = new Error("Este laudo já possui documento corretivo.");
            err.code = "laudo_ja_possui_corretiva";
            throw err;
          }
          const item = items.find((i) => i.document_id === id);
          if (item) item.has_corrective = true;
          return {
            id: `${id}-corretiva`, public_code: "LAU-000009", status: "atribuido",
            assignment: { id: "assign-9" }, predecessor_document_id: id,
          };
        }
        throw new Error(`rota sintética não coberta: ${method} ${url}`);
      },
    };
  }, { items });

  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-delivery-row");

  await check("botão só aparece em laudo liberado/assinado, nunca em atribuído", async () => {
    assert.equal(await page.locator('[data-report-return-correction-open="doc-liberado"]').count(), 1);
    assert.equal(await page.locator('[data-report-return-correction-open="doc-falha"]').count(), 1);
    assert.equal(await page.locator('[data-report-return-correction-open="doc-pendente"]').count(), 0);
  });

  await check("motivo é obrigatório: `required` nativo bloqueia o submit vazio, sem chamar a API", async () => {
    await page.locator('[data-report-return-correction-open="doc-liberado"]').click();
    const antes = await page.evaluate(() => window.calls.length);
    await page.locator('form[data-report-return-correction-form="doc-liberado"] button[type="submit"]').click();
    assert.equal(await page.evaluate(() => window.calls.length), antes, "não deveria ter chamado a API sem motivo");
    assert.equal(
      await page.evaluate(() =>
        document.querySelector('form[data-report-return-correction-form="doc-liberado"] select').validity.valid
      ),
      false,
      "o select vazio deveria estar inválido pela constraint nativa"
    );
  });

  await check("devolução com motivo chama a API certa e some o botão depois", async () => {
    await page.locator('form[data-report-return-correction-form="doc-liberado"] select[name="reason_code"]').selectOption("clinical_correction");
    await page.locator('form[data-report-return-correction-form="doc-liberado"] button[type="submit"]').click();
    await page.waitForFunction(() => window.calls.some((c) => c.url.endsWith("/retornar-para-correcao")));
    const call = await page.evaluate(() => window.calls.find((c) => c.url.endsWith("/retornar-para-correcao")));
    assert.equal(call.url, "/laudos/doc-liberado/retornar-para-correcao");
    assert.deepEqual(call.body, { reason_code: "clinical_correction" });
    await page.waitForFunction(() => document.querySelector('[data-report-return-correction-open="doc-liberado"]') === null);
  });

  await check("erro do servidor (já tem corretiva) mantém o formulário aberto e avisa perto da ação", async () => {
    await page.locator('[data-report-return-correction-open="doc-falha"]').click();
    await page.locator('form[data-report-return-correction-form="doc-falha"] select[name="reason_code"]').selectOption("technical_document_correction");
    await page.locator('form[data-report-return-correction-form="doc-falha"] button[type="submit"]').click();
    await page.waitForFunction(() => document.getElementById("reportStatus").textContent.includes("corretivo"));
    assert.equal(
      await page.evaluate(() => document.activeElement === document.getElementById("reportStatus")),
      true
    );
    assert.equal(await page.locator('form[data-report-return-correction-form="doc-falha"]').count(), 1, "o formulário deveria continuar aberto após erro");
  });

  await check("larguras 1440/1024/768/430/390 sem overflow com o formulário aberto", async () => {
    for (const width of [1440, 1024, 768, 430, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
        true,
        `overflow horizontal em ${width}px`
      );
      await page.screenshot({ path: path.join(screenshots, `admin-${width}.png`), fullPage: true });
    }
  });

  assert.deepEqual(errors, []);
  await page.close();
  return passed;
}

async function runOperationalOnlyScenario(browser) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = await commonSetup(page);
  const item = {
    document_id: "doc-liberado", patient: { full_name: "Paciente Liberado" }, report_code: "LAU-000002", exam_code: "ESP-000002",
    exam_date: "2027-01-05", estado: "aguardando_conferencia", estado_rotulo: "Aguardando conferência",
    status_clinico: "liberado", has_corrective: false, released_at: "2027-01-05T12:00:00Z",
    physician_profile_id: "profile-1", assinado: null, resultado: { existe: false },
  };
  await page.evaluate(({ item }) => {
    window.userRoles = ["operacional"];
    window.SoproM15 = {
      hasToken: () => true,
      can: (role) => window.userRoles.includes(role),
      getUser: () => ({ nome: "Operacional Sintético", papeis: window.userRoles }),
      onSessionChange: (cb) => { setTimeout(cb, 0); },
      apiUrl: (u) => u,
      hasSession: () => false,
      apiBlob: async () => new Blob(),
      api: async (url) => {
        if (url === "/laudos") return [];
        if (url === "/laudos/medicos-disponiveis") return [];
        if (url === "/laudos/exames?somente_sem_laudo=true") return { itens: [] };
        if (url === "/laudos/assinatura-externa/fila") {
          return { estados: [{ chave: "aguardando_conferencia", rotulo: "Aguardando conferência", total: 1 }], itens: [item] };
        }
        if (url === "/laudos/exames/encerrados") return { itens: [] };
        if (url === "/laudos/exames/motivos-encerramento") return { motivos: [] };
        throw new Error(`rota sintética não coberta (operacional-only não deveria chamar admin): ${url}`);
      },
    };
  }, { item });
  await page.addScriptTag({ path: path.join(panel, "js/report-workflow.js") });
  await page.waitForSelector(".report-delivery-row");
  assert.equal(
    await page.locator('[data-report-return-correction-open]').count(),
    0,
    "operacional sem admin não deveria ver 'Retornar para laudadora'"
  );
  assert.deepEqual(errors, []);
  await page.close();
  console.log("PASS (operacional) botão administrativo ausente para quem não é admin");
  return 1;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const a = await runMedicaScenario(browser);
    const b = await runAdminScenario(browser);
    const c = await runOperationalOnlyScenario(browser);
    console.log(`${a + b + c} cenários passaram. Capturas sintéticas: ${screenshots}`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error("FALHA:", error.message);
  process.exitCode = 1;
});
