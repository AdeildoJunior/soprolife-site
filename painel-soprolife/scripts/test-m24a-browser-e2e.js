#!/usr/bin/env node
/* M24A browser/E2E sem dependência npm.
 *
 * Sobe SQLite + API + proxy em portas loopback efêmeras e dirige o Google
 * Chrome real via DevTools Protocol. O fluxo usa somente usuários, pessoa,
 * exame, template e PDF marcadamente sintéticos; banco, storage, download e
 * perfil do navegador ficam em diretório temporário removido ao terminar.
 *
 * Uso:
 *   M15_TEST_PYTHON=/caminho/venv/bin/python \
 *     node painel-soprolife/scripts/test-m24a-browser-e2e.js
 */
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const M15_DIR = path.join(ROOT, "painel-soprolife", "nucleo-m15");
const PROXY = path.join(
  ROOT, "painel-soprolife", "scripts", "command-center-local-server.py"
);
const PYTHON = process.env.M15_TEST_PYTHON;
const CHROME = process.env.M24A_CHROME || "/usr/bin/google-chrome";
const PASSWORD = "senha-browser-m24a-sintetica-123";
const USERS = {
  admin: ["admin-m24a-browser@teste.local", "Admin Browser M24A", "admin"],
  operacional: [
    "oper-m24a-browser@teste.local", "Operacional Browser M24A", "operacional",
  ],
  gestor: ["gestor-m24a-browser@teste.local", "Gestor Browser M24A", "gestor"],
};

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS: ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function waitFor(fn, label, timeoutMs = 25000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const result = await fn();
      if (result) return result;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `timeout: ${label}${lastError ? ` (${lastError.message})` : ""}`
  );
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.sequence = 0;
    this.pending = new Map();
    this.exceptions = [];
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (!message.id) {
        if (message.method === "Runtime.exceptionThrown") {
          this.exceptions.push(message.params);
        }
        return;
      }
      const handler = this.pending.get(message.id);
      if (!handler) return;
      this.pending.delete(message.id);
      if (message.error) handler.reject(new Error(message.error.message));
      else handler.resolve(message.result || {});
    });
  }

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", reject, { once: true });
    });
    return new Cdp(ws);
  }

  send(method, params = {}) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      const description = result.exceptionDetails.exception &&
        result.exceptionDetails.exception.description;
      throw new Error(description || result.exceptionDetails.text ||
        "Runtime.evaluate falhou");
    }
    return result.result ? result.result.value : undefined;
  }

  close() {
    this.ws.close();
  }
}

function spawnLogged(command, args, options) {
  const proc = childProcess.spawn(command, args, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  proc.stdout.on("data", (chunk) => { log += chunk; });
  proc.stderr.on("data", (chunk) => { log += chunk; });
  proc.getLog = () => log.slice(-6000);
  return proc;
}

function stop(proc) {
  if (proc && proc.exitCode === null && !proc.killed) proc.kill("SIGTERM");
}

function runChecked(command, args, options) {
  const result = childProcess.spawnSync(command, args, {
    ...options,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} falhou: ${
        (result.stderr || result.stdout || "").slice(-2000)
      }`
    );
  }
  return result;
}

async function login(cdp, email) {
  await waitFor(
    () => cdp.evaluate("Boolean(document.querySelector('#m15LoginForm'))"),
    `login disponível para ${email}`
  );
  await cdp.evaluate(`(() => {
    const form = document.querySelector("#m15LoginForm");
    form.elements.email.value = ${JSON.stringify(email)};
    form.elements.password.value = ${JSON.stringify(PASSWORD)};
    form.elements.manter_conectado.checked = false;
    form.requestSubmit();
  })()`);
  await waitFor(
    () => cdp.evaluate("Boolean(document.querySelector('#m15Sair'))"),
    `login concluído para ${email}`
  );
}

async function logout(cdp) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await cdp.evaluate(`document.querySelector(
    '.sidebar .nav-item[data-section="m15-nucleo"]'
  ).click()`);
  await waitFor(
    () => cdp.evaluate(
      "document.querySelector('#m15-nucleo')?.classList.contains('active') && " +
      "Boolean(document.querySelector('#m15Sair'))"
    ),
    "seção de sessão para logout"
  );
  await cdp.evaluate("document.querySelector('#m15Sair').click()");
  await waitFor(
    () => cdp.evaluate("Boolean(document.querySelector('#m15LoginForm'))"),
    "logout concluído"
  );
}

async function openReports(cdp, examCode) {
  await waitFor(
    () => cdp.evaluate(
      "!document.querySelector(" +
      "'.sidebar .nav-item[data-section=\"laudos-espirometria\"]'" +
      ").hidden"
    ),
    "entrada de laudos liberada pela feature flag"
  );
  await cdp.evaluate(`document.querySelector(
    '.sidebar .nav-item[data-section="laudos-espirometria"]'
  ).click()`);
  await waitFor(
    () => cdp.evaluate(
      "document.querySelector('#laudos-espirometria')" +
      "?.classList.contains('active') && " +
      "Boolean(document.querySelector('#reportExamSearch'))"
    ),
    "workspace de laudos"
  );
  await waitFor(
    () => cdp.evaluate(
      `[...document.querySelectorAll("[data-report-exam]")].some(` +
      `(button) => button.textContent.includes(${JSON.stringify(examCode)}))`
    ),
    `exame ${examCode} na lista`
  );
  await cdp.evaluate(`(() => {
    const button = [...document.querySelectorAll("[data-report-exam]")]
      .find((item) => item.textContent.includes(${JSON.stringify(examCode)}));
    button.click();
  })()`);
}

async function setFile(cdp, selector, filePath) {
  const documentNode = await cdp.send("DOM.getDocument", {
    depth: -1,
    pierce: true,
  });
  const input = await cdp.send("DOM.querySelector", {
    nodeId: documentNode.root.nodeId,
    selector,
  });
  if (!input.nodeId) throw new Error(`input não encontrado: ${selector}`);
  await cdp.send("DOM.setFileInputFiles", {
    nodeId: input.nodeId,
    files: [filePath],
  });
}

async function main() {
  if (!PYTHON || !fs.existsSync(PYTHON)) {
    throw new Error("defina M15_TEST_PYTHON para o Python do venv M15");
  }
  if (!fs.existsSync(CHROME)) {
    throw new Error(`Google Chrome não encontrado: ${CHROME}`);
  }

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m24a-e2e-"));
  const dbPath = path.join(temp, "m24a-e2e.db");
  const reportsPath = path.join(temp, "reports");
  const downloadsPath = path.join(temp, "downloads");
  const pdfPath = path.join(temp, "TESTE-APAGAR-original.pdf");
  const profile = path.join(temp, "chrome-profile");
  fs.mkdirSync(downloadsPath, { mode: 0o700 });

  const apiPort = await freePort();
  const panelPort = await freePort();
  const chromePort = await freePort();
  const commonEnv = {
    ...process.env,
    PYTHONDONTWRITEBYTECODE: "1",
    M15_ENV: "dev",
    M15_DATABASE_URL: `sqlite:///${dbPath}`,
    M15_API_HOST: "127.0.0.1",
    M15_API_PORT: String(apiPort),
    M15_AUTH_SECRET: "m24a-browser-e2e-secret-synthetic-only-0123456789",
    M15_SESSION_COOKIE_PATH: "/painel-soprolife/api/m15",
    M15_REPORTS_STORAGE_DIR: reportsPath,
    M15_MARKETING_REFRESH_QUEUE: path.join(temp, "marketing-request.json"),
  };
  // A primeira subida prova o default real; não herda opt-in do shell.
  delete commonEnv.M15_REPORTS_ENABLED;
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    runChecked(
      PYTHON,
      ["-c",
        "import sys\nfrom pypdf import PdfWriter\n" +
        "w=PdfWriter()\nw.add_blank_page(width=595,height=842)\n" +
        "w.add_blank_page(width=595,height=842)\n" +
        "with open(sys.argv[1],'wb') as f:w.write(f)",
        pdfPath],
      { cwd: M15_DIR, env: commonEnv }
    );
    runChecked(alembic, ["upgrade", "head"], {
      cwd: M15_DIR,
      env: commonEnv,
    });
    for (const values of Object.values(USERS)) {
      runChecked(
        PYTHON,
        ["-m", "app.cli", "criar-usuario", "--email", values[0],
          "--nome", values[1], "--papel", values[2]],
        {
          cwd: M15_DIR,
          env: { ...commonEnv, M15_NOVA_SENHA: PASSWORD },
        }
      );
    }

    const defaultApi = spawnLogged(PYTHON, ["-m", "app.serve"], {
      cwd: M15_DIR,
      env: commonEnv,
    });
    children.push(defaultApi);
    const proxy = spawnLogged("python3", [PROXY], {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONDONTWRITEBYTECODE: "1",
        SOPROLIFE_PANEL_HOST: "127.0.0.1",
        SOPROLIFE_PANEL_PORT: String(panelPort),
        SOPROLIFE_M15_UPSTREAM: `http://127.0.0.1:${apiPort}/api/v1`,
      },
    });
    children.push(proxy);

    const base = `http://127.0.0.1:${panelPort}/painel-soprolife`;
    await waitFor(async () => {
      const response = await fetch(`${base}/api/m15/health`);
      return response.ok;
    }, "API/proxy local");

    const chrome = spawnLogged(CHROME, [
      "--headless=new",
      "--disable-gpu",
      "--disable-background-networking",
      "--disable-component-update",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${profile}`,
      "about:blank",
    ], { cwd: ROOT, env: process.env });
    children.push(chrome);

    await waitFor(async () => {
      const response = await fetch(
        `http://127.0.0.1:${chromePort}/json/version`
      );
      return response.ok ? response.json() : null;
    }, "Google Chrome DevTools");
    const targetResponse = await fetch(
      `http://127.0.0.1:${chromePort}/json/new?${
        encodeURIComponent(`${base}/`)
      }`,
      { method: "PUT" }
    );
    if (!targetResponse.ok) throw new Error("não foi possível criar aba Chrome");
    const target = await targetResponse.json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
      cdp.send("DOM.enable"),
      cdp.send("Accessibility.enable"),
    ]);
    await cdp.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: downloadsPath,
    });

    console.log("── Flags M24A: default desligado e opt-in isolado ──");
    await waitFor(
      () => cdp.evaluate(
        "document.readyState === 'complete' && Boolean(window.SoproM15) && " +
        "Boolean(document.querySelector('#m15LoginForm'))"
      ),
      "painel carregado com configuração padrão"
    );
    await new Promise((resolve) => setTimeout(resolve, 300));
    const defaultFlags = await cdp.evaluate(`(async () => {
      const entries = [...document.querySelectorAll("[data-report-entry]")];
      const response = await fetch("/painel-soprolife/api/m15/laudos");
      const body = await response.json();
      return {
        entriesHidden: entries.length === 3 && entries.every((entry) => entry.hidden),
        apiStatus: response.status,
        apiCode: body?.erro?.codigo
      };
    })()`);
    check("menu e workspace M24A ficam ocultos no frontend padrão",
      defaultFlags.entriesHidden, JSON.stringify(defaultFlags));
    check("backend M24A padrão recusa uso operacional antes da autenticação",
      defaultFlags.apiStatus === 503 &&
      defaultFlags.apiCode === "relatorios_desabilitados",
      JSON.stringify(defaultFlags));

    stop(defaultApi);
    await new Promise((resolve) => {
      if (defaultApi.exitCode !== null) resolve();
      else defaultApi.once("exit", resolve);
    });
    const enabledApi = spawnLogged(PYTHON, ["-m", "app.serve"], {
      cwd: M15_DIR,
      env: { ...commonEnv, M15_REPORTS_ENABLED: "true" },
    });
    children.push(enabledApi);
    await waitFor(async () => {
      try {
        const response = await fetch(`${base}/api/m15/health`);
        return response.ok;
      } catch (error) {
        return false;
      }
    }, "API reiniciada com flag backend explícita");

    await cdp.evaluate(
      'localStorage.setItem("soproM24AReports", "on"); location.reload()'
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(window.SoproM15) && " +
        "!document.querySelector(" +
        "'.sidebar .nav-item[data-section=\"laudos-espirometria\"]'" +
        ").hidden && Boolean(document.querySelector('#m15LoginForm'))"
      ),
      "opt-in frontend exclusivo do E2E"
    );

    console.log("── Preparação sintética pela API autenticada ──");
    await login(cdp, USERS.admin[0]);
    const seeded = await cdp.evaluate(`(async () => {
      const person = await window.SoproM15.api("/pessoas", {
        method: "POST",
        body: JSON.stringify({
          nome_completo: "TESTE - APAGAR - Pessoa Browser M24A 001"
        })
      });
      const attendance = await window.SoproM15.api("/atendimentos", {
        method: "POST",
        body: JSON.stringify({
          person_id: person.id,
          tipo: "espirometria_soprolife",
          espirometria: {
            data_exame: "2026-07-22",
            status: "Realizado",
            modalidade: "cowork"
          },
          idempotency_key: "m24a-browser-synthetic-attendance"
        })
      });
      const template = await window.SoproM15.api("/laudos/templates", {
        method: "POST",
        body: JSON.stringify({
          codigo: "TST-UI",
          titulo: "TESTE - APAGAR",
          texto_tooltip: "Ajuda curta sintética",
          texto_completo:
            "TESTE - APAGAR: conteúdo sintético sem interpretação clínica.",
          ativo: true
        })
      });
      return {
        examCode: attendance.espirometria.public_code,
        examId: attendance.espirometria.id,
        templateCode: template.codigo
      };
    })()`);
    check("exame sintético criado com código institucional",
      /^ESP-\d+$/.test(seeded.examCode), JSON.stringify(seeded));
    check("template sintético administrável criado",
      seeded.templateCode === "TST-UI");
    await logout(cdp);

    console.log("── Operacional: seleção, upload, preview e revisão ──");
    await login(cdp, USERS.operacional[0]);
    await openReports(cdp, seeded.examCode);
    await waitFor(
      () => cdp.evaluate("Boolean(document.querySelector('#reportUploadForm'))"),
      "formulário de upload operacional"
    );
    check("consulta não exibe identificador da pessoa",
      !(await cdp.evaluate(
        "document.querySelector('#reportWorkflowRoot').textContent" +
        ".includes('TESTE - APAGAR - Pessoa')"
      )));

    await setFile(cdp, "#reportPdfFile", pdfPath);
    await cdp.evaluate(
      "document.querySelector('#reportUploadForm').requestSubmit()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#reportPdfFrame')?.src.startsWith('blob:') && " +
        "document.querySelectorAll('.report-version-row').length === 1"
      ),
      "upload e preview autenticado do original"
    );
    const preview = await cdp.evaluate(`(() => {
      const frame = document.querySelector("#reportPdfFrame");
      const text = document.querySelector("#reportWorkflowRoot").textContent;
      return {
        src: frame.src,
        title: frame.title,
        referrerPolicy: frame.referrerPolicy,
        text,
        originalNameVisible: text.includes("TESTE-APAGAR-original.pdf"),
        endpointAsSrc: frame.src.includes("/api/m15/") ||
          frame.src.includes("/versoes/")
      };
    })()`);
    check("iframe usa somente object URL temporária",
      preview.src.startsWith("blob:") && !preview.endpointAsSrc);
    check("iframe autenticado tem nome e referrer policy",
      preview.title.includes(seeded.examCode) &&
      preview.referrerPolicy === "no-referrer");
    check("metadados não exibem nome original nem caminho",
      !preview.originalNameVisible &&
      !preview.text.includes("storage_path") &&
      !preview.text.includes(reportsPath));

    const templateUi = await cdp.evaluate(`(() => {
      const card = document.querySelector(".report-template-card");
      const details = card && card.querySelector("details");
      if (details) details.open = true;
      return {
        abbreviation: card?.querySelector("abbr")?.textContent.trim(),
        tooltip: card?.querySelector("abbr")?.title,
        summary: card?.querySelector("summary")?.textContent.trim(),
        full: card?.querySelector(".report-template-full-text")?.textContent.trim()
      };
    })()`);
    check("abreviação expõe tooltip acessível",
      templateUi.abbreviation === "TST-UI" &&
      templateUi.tooltip === "Ajuda curta sintética");
    check("área de ajuda expõe o texto completo",
      templateUi.summary === "Texto completo do modelo TST-UI" &&
      templateUi.full.includes("conteúdo sintético"));

    await cdp.evaluate(`(() => {
      const form = document.querySelector("#reportComposeForm");
      form.elements.page_number.value = "2";
      form.elements.placement.value = "topo";
      form.requestSubmit();
    })()`);
    await waitFor(
      () => cdp.evaluate(
        "document.querySelectorAll('.report-version-row').length === 2 && " +
        "document.querySelector('#reportPdfFrame')?.src.startsWith('blob:') && " +
        "document.querySelector('#reportWorkflowRoot').textContent" +
        ".includes('Prévia de rascunho')"
      ),
      "composição e preview do rascunho"
    );
    check("página e topo retornam nos metadados",
      await cdp.evaluate(
        "document.querySelector('.report-metadata').textContent" +
        ".includes('página 2 · Topo da página')"
      ));

    await cdp.evaluate(`(() => {
      const button = document.querySelector("[data-report-review]");
      button.focus();
      button.click();
    })()`);
    const dialog = await cdp.evaluate(`(() => {
      const modal = document.querySelector('[role="dialog"]');
      return {
        exists: Boolean(modal),
        ariaModal: modal?.getAttribute("aria-modal"),
        labelled: Boolean(modal?.getAttribute("aria-labelledby")),
        described: Boolean(modal?.getAttribute("aria-describedby")),
        focusIsConfirm: document.activeElement?.hasAttribute(
          "data-report-modal-confirm"
        )
      };
    })()`);
    check("confirmação é diálogo modal nomeado e descrito",
      dialog.exists && dialog.ariaModal === "true" &&
      dialog.labelled && dialog.described);
    check("foco inicial está na ação explícita", dialog.focusIsConfirm);
    await cdp.evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Escape", bubbles: true
    }))`);
    check("Escape fecha e devolve foco ao botão de revisão",
      await waitFor(
        () => cdp.evaluate(
          "!document.querySelector('[role=\"dialog\"]') && " +
          "document.activeElement?.hasAttribute('data-report-review')"
        ),
        "fechamento do diálogo por Escape"
      ));

    await cdp.evaluate(
      "document.querySelector('[data-report-review]').click()"
    );
    await cdp.evaluate(
      "document.querySelector('[data-report-modal-confirm]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('.report-em_revisao')?.textContent" +
        ".includes('Em revisão clínica') && " +
        "!document.querySelector('[role=\"dialog\"]')"
      ),
      "submissão para revisão"
    );
    check("operacional não recebe ação de finalizar",
      !(await cdp.evaluate(
        "Boolean(document.querySelector('[data-report-finalize]'))"
      )));
    check("composição desaparece durante revisão",
      !(await cdp.evaluate(
        "Boolean(document.querySelector('#reportComposeForm'))"
      )));
    await logout(cdp);

    console.log("── Gestor: finalização, download e correção imutável ──");
    await login(cdp, USERS.gestor[0]);
    await openReports(cdp, seeded.examCode);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('[data-report-finalize]:not(:disabled)'))"
      ),
      "ação privilegiada de finalização habilitada para gestor"
    );
    check("gestor vê finalização somente em revisão",
      await cdp.evaluate(
        "document.querySelector('.report-em_revisao')?.textContent" +
        ".includes('Em revisão clínica')"
      ));

    await cdp.evaluate(
      "document.querySelector('[data-report-finalize]').click()"
    );
    await cdp.evaluate(
      "document.querySelector('[data-report-modal-confirm]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('.report-signature-pending strong')" +
        "?.textContent.trim() === 'assinatura digital pendente' && " +
        "!document.querySelector('[role=\"dialog\"]')"
      ),
      "finalização com assinatura pendente"
    );
    const finalUi = await cdp.evaluate(`(() => {
      const text = document.querySelector("#reportWorkflowRoot").textContent;
      return {
        hasCompose: Boolean(document.querySelector("#reportComposeForm")),
        hasFinalize: Boolean(document.querySelector("[data-report-finalize]")),
        hasCorrective: Boolean(document.querySelector("[data-report-corrective]")),
        honestSignature: text.includes("Nenhum provedor ICP-Brasil real") &&
          text.includes("não afirma que o PDF esteja assinado")
      };
    })()`);
    check("finalizado não pode ser recomposto ou refinalizado",
      !finalUi.hasCompose && !finalUi.hasFinalize);
    check("mensagem de assinatura não faz alegação falsa",
      finalUi.honestSignature);
    check("fluxo corretivo está disponível sem mutar o finalizado",
      finalUi.hasCorrective);

    const finalDocumentId = await cdp.evaluate(
      "document.querySelector('[data-report-document][aria-pressed=\"true\"]')" +
      ".getAttribute('data-report-document')"
    );
    await cdp.evaluate(`(async () => {
      window.__m24FinalBefore = await window.SoproM15.api(
        ${JSON.stringify(`/laudos/${finalDocumentId}`)}
      );
      return true;
    })()`);

    await cdp.evaluate(`(() => {
      const row = [...document.querySelectorAll(".report-version-row")]
        .find((item) => item.textContent.includes("Versão finalizada"));
      row.querySelector("[data-report-download]").click();
    })()`);
    const downloadedName = await waitFor(() => {
      const files = fs.readdirSync(downloadsPath)
        .filter((name) => name.endsWith(".pdf") && !name.endsWith(".crdownload"));
      return files[0] || "";
    }, "download PDF pelo Blob");
    const downloaded = fs.readFileSync(path.join(downloadsPath, downloadedName));
    check("download usa nome técnico seguro",
      downloadedName === `laudo-${seeded.examCode}-v3-finalizado.pdf`,
      downloadedName);
    check("download real contém PDF e fica fora do repositório",
      downloaded.subarray(0, 5).toString() === "%PDF-" &&
      path.dirname(path.join(downloadsPath, downloadedName)) === downloadsPath);

    await cdp.evaluate(
      "document.querySelector('[data-report-corrective]').click()"
    );
    await cdp.evaluate(
      "document.querySelector('[data-report-modal-confirm]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelectorAll('[data-report-document]').length === 2 && " +
        "document.querySelector('.report-rascunho')?.textContent" +
        ".includes('Rascunho') && !document.querySelector('[role=\"dialog\"]')"
      ),
      "novo documento corretivo"
    );
    const corrective = await cdp.evaluate(`(async () => {
      const selected = document.querySelector(
        '[data-report-document][aria-pressed="true"]'
      ).getAttribute("data-report-document");
      const oldDocument = await window.SoproM15.api(
        ${JSON.stringify(`/laudos/${finalDocumentId}`)}
      );
      const newDocument = await window.SoproM15.api("/laudos/" + selected);
      const before = window.__m24FinalBefore;
      const oldCurrent = oldDocument.versoes.find(
        (version) => version.id === oldDocument.current_version_id
      );
      const beforeCurrent = before.versoes.find(
        (version) => version.id === before.current_version_id
      );
      return {
        selected,
        newStatus: newDocument.status,
        oldStatus: oldDocument.status,
        supersededBy: oldDocument.superseded_by_id,
        finalizedAtSame: oldDocument.finalized_at === before.finalized_at,
        versionSame: oldDocument.current_version_id === before.current_version_id,
        hashSame: oldCurrent.sha256 === beforeCurrent.sha256,
        sizeSame: oldCurrent.size_bytes === beforeCurrent.size_bytes
      };
    })()`);
    check("corretiva nasce como outro rascunho",
      corrective.newStatus === "rascunho" &&
      corrective.selected !== finalDocumentId);
    check("finalizado mantém estado, marco, versão, hash e tamanho",
      corrective.oldStatus === "finalizado" &&
      corrective.supersededBy === corrective.selected &&
      corrective.finalizedAtSame && corrective.versionSame &&
      corrective.hashSame && corrective.sizeSame,
      JSON.stringify(corrective));

    await cdp.evaluate(`document.querySelector(
      '[data-report-document="${finalDocumentId}"]'
    ).click()`);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('.report-immutable-note'))"
      ),
      "aviso de imutabilidade no documento sucedido"
    );
    check("documento sucedido não oferece mutação",
      await cdp.evaluate(
        "!document.querySelector('#reportComposeForm') && " +
        "!document.querySelector('[data-report-finalize]') && " +
        "!document.querySelector('[data-report-corrective]')"
      ));
    await cdp.evaluate(`document.querySelector(
      '[data-report-document="${corrective.selected}"]'
    ).click()`);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('#reportComposeForm')) && " +
        "document.querySelector('#reportPdfFrame')?.src.startsWith('blob:')"
      ),
      "rascunho corretivo reaberto"
    );

    console.log("── Responsividade e árvore de acessibilidade ──");
    for (const width of [1440, 1000, 800, 420]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width,
        height: 1000,
        deviceScaleFactor: 1,
        mobile: width <= 420,
      });
      await new Promise((resolve) => setTimeout(resolve, 180));
      const metrics = await cdp.evaluate(`(() => {
        const workflow = document.querySelector("#reportWorkflowRoot");
        return {
          inner: window.innerWidth,
          body: document.body.scrollWidth,
          root: document.documentElement.scrollWidth,
          workflowClient: workflow.clientWidth,
          workflowScroll: workflow.scrollWidth
        };
      })()`);
      check(`viewport ${width}px sem overflow horizontal`,
        metrics.body <= metrics.inner &&
        metrics.root <= metrics.inner &&
        metrics.workflowScroll <= metrics.workflowClient,
        JSON.stringify(metrics));
    }
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.evaluate(
      "document.querySelector('.report-template-card details').open = true"
    );
    const ax = (await cdp.send("Accessibility.getFullAXTree")).nodes || [];
    const axNames = new Set(
      ax.map((node) => node.name && node.name.value).filter(Boolean)
    );
    for (const name of [
      "Laudos de espirometria",
      "Código institucional do exame",
      "Abreviações disponíveis",
      "Texto completo do modelo TST-UI",
      "Página de destino",
      "Posição do bloco",
    ]) {
      check(`árvore acessível nomeia: ${name}`, axNames.has(name));
    }
    check("árvore acessível nomeia a visualização autenticada",
      [...axNames].some((name) =>
        String(name).includes("Visualização autenticada do PDF")
      ));
    check("nenhuma exceção JavaScript não tratada no fluxo",
      cdp.exceptions.length === 0, String(cdp.exceptions.length));

    if (failures) {
      children.forEach((proc) => {
        const log = proc.getLog();
        if (log) {
          console.error(
            `── log sintético ${path.basename(proc.spawnfile)} ──\n${log}`
          );
        }
      });
      throw new Error(`${failures} falha(s) de browser/E2E M24A`);
    }
    console.log("RESULTADO: todos os casos passaram.");
  } finally {
    if (cdp) cdp.close();
    children.reverse().forEach(stop);
    await new Promise((resolve) => setTimeout(resolve, 400));
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(`RESULTADO: FALHOU — ${error.message}`);
  process.exit(1);
});
