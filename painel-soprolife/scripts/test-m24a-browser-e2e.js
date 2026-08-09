#!/usr/bin/env node
/* M24C browser/E2E sem dependência npm.
 *
 * Sobe SQLite + API + proxy em loopback efêmero e dirige Google Chrome real
 * por CDP. Somente usuários, pessoa, exame, texto e PDFs sintéticos são
 * criados; banco, storage e perfil Chrome ficam em diretório temporário.
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
const PASSWORD = "senha-browser-m24c-sintetica-123";
const USERS = {
  admin: [
    "admin-m24c-browser@teste.local",
    "TESTE APAGAR Admin Browser M24C",
    "admin",
  ],
  operacional: [
    "oper-m24c-browser@teste.local",
    "TESTE APAGAR Operacional Browser M24C",
    "operacional",
  ],
  medico: [
    "medico-m24c-browser@teste.local",
    "TESTE APAGAR Médico Browser M24C",
    "medico",
  ],
};

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  PASS: ${label}`);
    return;
  }
  failures += 1;
  console.log(`  FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
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

async function waitFor(fn, label, timeoutMs = 30000) {
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
      const exception = result.exceptionDetails.exception;
      throw new Error(
        (exception && exception.description)
          || result.exceptionDetails.text
          || "Runtime.evaluate falhou"
      );
    }
    return result.result ? result.result.value : undefined;
  }

  close() {
    this.ws.close();
  }
}

function spawnLogged(command, args, options) {
  const processHandle = childProcess.spawn(command, args, {
    ...options,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  processHandle.stdout.on("data", (chunk) => { log += chunk; });
  processHandle.stderr.on("data", (chunk) => { log += chunk; });
  processHandle.getLog = () => log.slice(-8000);
  return processHandle;
}

function stop(processHandle) {
  if (
    processHandle
    && processHandle.exitCode === null
    && !processHandle.killed
  ) {
    processHandle.kill("SIGTERM");
  }
}

function runChecked(command, args, options) {
  const result = childProcess.spawnSync(command, args, {
    ...options,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `${path.basename(command)} falhou: ${
        (result.stderr || result.stdout || "").slice(-3000)
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
    () => cdp.evaluate(
      "Boolean(window.SoproM15?.hasToken()) && " +
      "Boolean(document.querySelector('[data-report-logout]') || " +
      "document.querySelector('#m15Sair'))"
    ),
    `login concluído para ${email}`
  );
}

async function logout(cdp) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await cdp.evaluate(`(() => {
    const reportLogout = document.querySelector("[data-report-logout]");
    if (reportLogout) {
      reportLogout.click();
      return;
    }
    const nav = document.querySelector(
      '.sidebar .nav-item[data-section="m15-nucleo"]'
    );
    if (nav) nav.click();
    const button = document.querySelector("#m15Sair");
    if (button) button.click();
  })()`);
  await waitFor(
    () => cdp.evaluate(
      "!window.SoproM15?.hasToken() && " +
      "Boolean(document.querySelector('#m15LoginForm'))"
    ),
    "logout concluído"
  );
}

async function openReports(cdp) {
  await waitFor(
    () => cdp.evaluate(
      `(() => {
        const entry = document.querySelector(
          '.sidebar .nav-item[data-section="laudos-espirometria"]'
        );
        return Boolean(entry && !entry.hidden);
      })()`
    ),
    "entrada de laudos liberada no teste isolado"
  );
  await cdp.evaluate(`document.querySelector(
    '.sidebar .nav-item[data-section="laudos-espirometria"]'
  ).click()`);
  await waitFor(
    () => cdp.evaluate(
      "document.querySelector('#laudos-espirometria')" +
      "?.classList.contains('active') && " +
      "Boolean(document.querySelector('#reportWorkflowRoot'))"
    ),
    "workspace de laudos"
  );
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

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "soprolife-m24c-e2e-"));
  const dbPath = path.join(temp, "m24c-e2e.db");
  const reportsPath = path.join(temp, "reports");
  const pdfPath = path.join(temp, "TESTE-APAGAR-original.pdf");
  const chromeProfile = path.join(temp, "chrome-profile");
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
    M15_AUTH_SECRET:
      "m24c-browser-e2e-secret-synthetic-only-0123456789",
    M15_SESSION_COOKIE_PATH: "/painel-soprolife/api/m15",
    M15_REPORTS_STORAGE_DIR: reportsPath,
    M15_MARKETING_REFRESH_QUEUE: path.join(
      temp, "marketing-request.json"
    ),
  };
  delete commonEnv.M15_REPORTS_ENABLED;
  delete commonEnv.M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES;
  const alembic = path.join(path.dirname(PYTHON), "alembic");
  const children = [];
  let cdp;

  try {
    runChecked(
      PYTHON,
      [
        "-c",
        "import sys\nfrom pypdf import PdfWriter\n"
          + "w=PdfWriter()\n"
          + "w.add_blank_page(width=595,height=842)\n"
          + "w.add_blank_page(width=595,height=842)\n"
          + "with open(sys.argv[1],'wb') as f:w.write(f)",
        pdfPath,
      ],
      { cwd: M15_DIR, env: commonEnv }
    );
    runChecked(alembic, ["upgrade", "head"], {
      cwd: M15_DIR,
      env: commonEnv,
    });
    for (const values of Object.values(USERS)) {
      runChecked(
        PYTHON,
        [
          "-m",
          "app.cli",
          "criar-usuario",
          "--email",
          values[0],
          "--nome",
          values[1],
          "--papel",
          values[2],
        ],
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
        SOPROLIFE_M15_UPSTREAM:
          `http://127.0.0.1:${apiPort}/api/v1`,
      },
    });
    children.push(proxy);
    const base = `http://127.0.0.1:${panelPort}/painel-soprolife`;
    await waitFor(async () => {
      const response = await fetch(`${base}/api/m15/health`);
      return response.ok;
    }, "API/proxy local");

    const chrome = spawnLogged(
      CHROME,
      [
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--no-first-run",
        "--no-default-browser-check",
        `--remote-debugging-port=${chromePort}`,
        `--user-data-dir=${chromeProfile}`,
        "about:blank",
      ],
      { cwd: ROOT, env: process.env }
    );
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
    if (!targetResponse.ok) {
      throw new Error("não foi possível criar aba Chrome");
    }
    const target = await targetResponse.json();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await Promise.all([
      cdp.send("Page.enable"),
      cdp.send("Runtime.enable"),
      cdp.send("Network.enable"),
      cdp.send("DOM.enable"),
      cdp.send("Accessibility.enable"),
    ]);

    console.log("── Default-off real ──");
    await waitFor(
      () => cdp.evaluate(
        "document.readyState === 'complete' && Boolean(window.SoproM15) && "
        + "Boolean(document.querySelector('#m15LoginForm'))"
      ),
      "painel padrão"
    );
    const defaultState = await cdp.evaluate(`(async () => {
      const entries = [...document.querySelectorAll("[data-report-entry]")];
      const response = await fetch("/painel-soprolife/api/m15/laudos");
      const body = await response.json();
      return {
        entries: entries.length,
        hidden: entries.every((entry) => entry.hidden),
        status: response.status,
        code: body?.erro?.codigo,
      };
    })()`);
    check(
      "as três entradas de laudos ficam ocultas por padrão",
      defaultState.entries === 3 && defaultState.hidden,
      JSON.stringify(defaultState)
    );
    check(
      "backend padrão recusa laudos antes da autenticação",
      defaultState.status === 503
        && defaultState.code === "relatorios_desabilitados",
      JSON.stringify(defaultState)
    );

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
        return (await fetch(`${base}/api/m15/health`)).ok;
      } catch (error) {
        return false;
      }
    }, "API reiniciada com habilitação isolada");
    await cdp.evaluate(
      'localStorage.setItem("soproM24AReports", "on"); location.reload()'
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(window.SoproM15) && "
        + "!document.querySelector("
        + "'.sidebar .nav-item[data-section=\"laudos-espirometria\"]'"
        + ").hidden && Boolean(document.querySelector('#m15LoginForm'))"
      ),
      "opt-in frontend somente em loopback"
    );

    console.log("── Administração sintética ──");
    await login(cdp, USERS.admin[0]);
    const seeded = await cdp.evaluate(`(async () => {
      const accounts = await window.SoproM15.api("/laudos/admin/medicos");
      const doctor = accounts.find(
        (item) => item.user.email === ${JSON.stringify(USERS.medico[0])}
      );
      const configured = await window.SoproM15.api(
        "/laudos/admin/medicos/" + doctor.user.id,
        {
          method: "PATCH",
          body: JSON.stringify({
            grant_physician_role: true,
            professional_name: "TESTE APAGAR Profissional Browser",
            crm_number: "990001",
            crm_state: "AC",
            rqe: "RQE-TESTE-001",
            verification_status: "verified",
            // M25.11 passou a exigir a referência técnica para aceitar o
            // status verified. Sem ela o PATCH responde 422 e este roteiro
            // parava antes de chegar ao fluxo clínico.
            verification_reference: "CRM-VERIF-TESTE-BROWSER-M24C",
            active: true,
          }),
        }
      );
      const person = await window.SoproM15.api("/pessoas", {
        method: "POST",
        body: JSON.stringify({
          nome_completo: "TESTE APAGAR Pessoa Browser M24C",
        }),
      });
      const attendance = await window.SoproM15.api("/atendimentos", {
        method: "POST",
        body: JSON.stringify({
          person_id: person.id,
          tipo: "espirometria_soprolife",
          espirometria: {
            data_exame: "2026-07-22",
            status: "Realizado",
            modalidade: "cowork",
          },
          idempotency_key: "m24c-browser-synthetic-attendance",
        }),
      });
      const template = await window.SoproM15.api("/laudos/templates", {
        method: "POST",
        body: JSON.stringify({
          codigo: "TESTE_BROWSER_M24C",
          titulo: "TESTE - APAGAR",
          texto_tooltip: "Ajuda sintética do E2E",
          texto_completo:
            "TESTE - APAGAR: texto controlado sintético sem validade clínica.",
          ativo: true,
          status: "approved",
          clinically_approved: true,
        }),
      });
      const adminCatalog = await window.SoproM15.api(
        "/laudos/templates?catalog=admin"
      );
      return {
        profileId: configured.profile.id,
        examCode: attendance.espirometria.public_code,
        templateCode: template.codigo,
        provisionalCount: adminCatalog.filter(
          (item) => item.codigo.endsWith("_PROVISORIO")
        ).length,
        provisionalUnsafe: adminCatalog.some(
          (item) => item.codigo.endsWith("_PROVISORIO")
            && (item.status !== "draft" || item.clinically_approved)
        ),
      };
    })()`);
    check(
      "perfil médico sintético ativo/verificado foi configurado",
      Boolean(seeded.profileId)
    );
    check(
      "exame sintético possui código institucional",
      /^ESP-\d+$/.test(seeded.examCode),
      JSON.stringify(seeded)
    );
    check(
      "seis templates provisórios permanecem draft e não aprovados",
      seeded.provisionalCount === 6 && !seeded.provisionalUnsafe
    );
    await logout(cdp);

    console.log("── Operacional: localização, origem e atribuição ──");
    await login(cdp, USERS.operacional[0]);
    await openReports(cdp);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('#reportLocateExamForm'))"
      ),
      "formulário operacional"
    );
    await cdp.evaluate(`(() => {
      const form = document.querySelector("#reportLocateExamForm");
      form.elements.exam_code.value = ${JSON.stringify(seeded.examCode)};
      form.requestSubmit();
    })()`);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('#reportUploadForm'))"
      ),
      "exame localizado sem busca por paciente"
    );
    const operationalBefore = await cdp.evaluate(
      "document.querySelector('#reportWorkflowRoot').textContent"
    );
    check(
      "localização operacional não mostra identidade do paciente",
      !operationalBefore.includes("TESTE APAGAR Pessoa Browser")
    );
    await cdp.evaluate(`(() => {
      const form = document.querySelector("#reportUploadForm");
      form.elements.physician_profile_id.value = ${
        JSON.stringify(seeded.profileId)
      };
      form.elements.origin_type.value = "coworking";
      form.elements.origin_label.value = "unidade-browser-teste";
    })()`);
    await setFile(cdp, "#reportPdfFile", pdfPath);
    await cdp.evaluate(
      "document.querySelector('#reportUploadForm').requestSubmit()"
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('[data-report-operational]'))"
      ),
      "upload e atribuição operacional"
    );
    const operational = await cdp.evaluate(`(() => {
      const root = document.querySelector("#reportWorkflowRoot");
      const row = document.querySelector("[data-report-operational]");
      return {
        documentId: row?.getAttribute("data-report-operational"),
        text: root.textContent,
        hasEditor: Boolean(document.querySelector("#reportInterpretation")),
      };
    })()`);
    check(
      "acompanhamento mostra código/status sem interpretação ou paciente",
      Boolean(operational.documentId)
        && operational.text.includes(seeded.examCode)
        && !operational.text.includes("TESTE APAGAR Pessoa Browser")
        && !operational.text.includes("texto controlado sintético")
        && !operational.hasEditor
    );
    const operationalDenied = await cdp.evaluate(`(async () => {
      try {
        await window.SoproM15.api(
          "/laudos/" + ${JSON.stringify(operational.documentId)}
            + "/preparar-assinatura",
          { method: "POST" }
        );
        return 200;
      } catch (error) {
        return error.status;
      }
    })()`);
    check(
      "operacional não prepara assinatura nem edita clínica",
      operationalDenied === 403
    );
    await logout(cdp);

    console.log("── Médico: fila restrita, prévia e assinatura pendente ──");
    await login(cdp, USERS.medico[0]);
    await openReports(cdp);
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('[data-report-open]'))"
      ),
      "Meus laudos do médico"
    );
    const restrictedQueue = await cdp.evaluate(`(() => {
      const rootText = document.querySelector("#reportWorkflowRoot").textContent;
      const hiddenOtherNavigation = [
        ...document.querySelectorAll(
          '.sidebar .nav-item:not([data-section="laudos-espirometria"])'
        ),
      ].every((item) => getComputedStyle(item).display === "none");
      return {
        rootText,
        hiddenOtherNavigation,
        physicianOnly: document.body.classList.contains(
          "report-physician-only"
        ),
      };
    })()`);
    check(
      "fila médica contém somente códigos/origem/status",
      restrictedQueue.rootText.includes("Meus laudos")
        && restrictedQueue.rootText.includes(seeded.examCode)
        && !restrictedQueue.rootText.includes(
          "TESTE APAGAR Pessoa Browser"
        )
    );
    check(
      "conta exclusivamente médica não navega pelas áreas gerais",
      restrictedQueue.physicianOnly
        && restrictedQueue.hiddenOtherNavigation
    );
    const peopleDenied = await cdp.evaluate(`(async () => {
      const response = await fetch(
        "/painel-soprolife/api/m15/pessoas",
        { credentials: "same-origin" }
      );
      return response.status;
    })()`);
    check("API geral recusa papel médico isolado", peopleDenied === 403);

    await cdp.evaluate(
      "document.querySelector('[data-report-open]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#reportPdfOriginal')?.src.startsWith('blob:')"
        + " && Boolean(document.querySelector('#reportComposeForm'))"
      ),
      "detalhe e PDF original autenticado"
    );
    const opened = await cdp.evaluate(`(() => {
      const root = document.querySelector("#reportWorkflowRoot");
      const original = document.querySelector("#reportPdfOriginal");
      const approved = [...document.querySelectorAll(
        ".report-template-card abbr"
      )].map((item) => item.textContent.trim());
      return {
        text: root.textContent,
        src: original?.src,
        title: original?.title,
        referrerPolicy: original?.referrerPolicy,
        approved,
      };
    })()`);
    check(
      "identidade necessária aparece somente após abrir o atribuído",
      opened.text.includes("TESTE APAGAR Pessoa Browser")
    );
    check(
      "viewer usa Blob autenticado e política sem referrer",
      opened.src.startsWith("blob:")
        && opened.title.includes("PDF original")
        && opened.referrerPolicy === "no-referrer"
        && !opened.src.includes("/api/m15/")
    );
    check(
      "seletor clínico mostra só template aprovado",
      opened.approved.length === 1
        && opened.approved[0] === seeded.templateCode
        && !opened.text.includes("NORMAL_PROVISORIO")
    );

    await cdp.evaluate(`(() => {
      const input = [...document.querySelectorAll(
        'input[name="template_id"]'
      )].find((item) => item.value);
      input.click();
    })()`);
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#reportInterpretation')?.value.includes("
        + "'texto controlado sintético')"
      ),
      "template preenche editor controlado"
    );
    await cdp.evaluate(`(() => {
      const form = document.querySelector("#reportComposeForm");
      const editor = form.elements.interpretation_text;
      editor.value =
        "TESTE - APAGAR: edição médica sintética sem validade clínica.";
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      form.elements.page_number.value = "2";
      form.elements.placement.value = "topo";
      form.requestSubmit();
    })()`);
    await waitFor(
      () => cdp.evaluate(
        "document.querySelector('#reportPdfOriginal')?.src.startsWith('blob:')"
        + " && document.querySelector('#reportPdfGenerated')"
        + "?.src.startsWith('blob:')"
        + " && Boolean(document.querySelector("
        + "'.report-em_elaboracao'))"
        + " && Boolean(document.querySelector("
        + "'[data-report-prepare-signature]'))"
      ),
      "prévia clínica comparável"
    );
    const preview = await cdp.evaluate(`(async () => {
      const documentId = ${
        JSON.stringify(operational.documentId)
      };
      const detail = await window.SoproM15.api("/laudos/" + documentId);
      const current = detail.versoes.find(
        (item) => item.id === detail.current_version_id
      );
      return {
        status: detail.status,
        kind: current.kind,
        page: current.page_number,
        placement: current.placement,
        author: current.physician_name_snapshot,
        crmState: current.physician_crm_state_snapshot,
        origin: current.origin_type_snapshot,
        interpretation: current.interpretation_text_snapshot,
        footer: current.footer_text_snapshot,
        originalSrc: document.querySelector("#reportPdfOriginal")?.src,
        generatedSrc: document.querySelector("#reportPdfGenerated")?.src,
      };
    })()`);
    check(
      "prévia congela médico, origem, página e interpretação",
      preview.status === "em_elaboracao"
        && preview.kind === "rascunho"
        && preview.page === 2
        && preview.placement === "topo"
        && preview.author === "TESTE APAGAR Profissional Browser"
        && preview.crmState === "AC"
        && preview.origin === "coworking"
        && preview.interpretation.includes("edição médica sintética")
    );
    check(
      "rodapé da prévia declara documento TESTE não assinado",
      preview.footer.includes(
        "MODELO DE TESTE — DOCUMENTO NÃO ASSINADO E SEM VALIDADE PARA LIBERAÇÃO"
      )
    );
    check(
      "comparação mantém dois object URLs distintos",
      preview.originalSrc.startsWith("blob:")
        && preview.generatedSrc.startsWith("blob:")
        && preview.originalSrc !== preview.generatedSrc
    );

    console.log("── Responsividade e acessibilidade ──");
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
          workflowScroll: workflow.scrollWidth,
        };
      })()`);
      check(
        `viewport ${width}px sem overflow horizontal`,
        metrics.body <= metrics.inner
          && metrics.root <= metrics.inner
          && metrics.workflowScroll <= metrics.workflowClient,
        JSON.stringify(metrics)
      );
    }
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    });
    const ax = (await cdp.send("Accessibility.getFullAXTree")).nodes || [];
    const axNames = ax
      .map((node) => node.name && node.name.value)
      .filter(Boolean)
      .map(String);
    for (const expected of [
      "Meus laudos",
      "Interpretação clínica",
      "Página de destino",
      "Posição do bloco",
      "Marcar conteúdo pronto para assinatura",
    ]) {
      check(
        `árvore acessível nomeia ${expected}`,
        axNames.some((name) => name.includes(expected))
      );
    }
    check(
      "árvore acessível nomeia os dois viewers",
      axNames.some((name) => name.includes("PDF original"))
        && axNames.some((name) => name.includes("PDF gerado"))
    );

    console.log("── Preparação fail-closed ──");
    await cdp.evaluate(
      "document.querySelector('[data-report-prepare-signature]').click()"
    );
    await waitFor(
      () => cdp.evaluate(
        "Boolean(document.querySelector('.report-assinatura_pendente'))"
        + " && document.querySelector('.report-signature-panel')"
        + "?.textContent.includes('assinatura qualificada pendente')"
      ),
      "assinatura pendente"
    );
    const pending = await cdp.evaluate(`(async () => {
      const documentId = ${JSON.stringify(operational.documentId)};
      const detail = await window.SoproM15.api("/laudos/" + documentId);
      const signature = await window.SoproM15.api(
        "/laudos/" + documentId + "/assinatura"
      );
      const current = detail.versoes.find(
        (item) => item.id === detail.current_version_id
      );
      let correctionStatus = 200;
      let correctionCode = "";
      try {
        await window.SoproM15.api(
          "/laudos/" + documentId + "/nova-versao-corretiva",
          {
            method: "POST",
            body: JSON.stringify({ reason_code: "clinical_correction" }),
          }
        );
      } catch (error) {
        correctionStatus = error.status;
        correctionCode = error.code;
      }
      return {
        status: detail.status,
        signatureStatus: detail.signature_status,
        releasable: detail.releasable,
        kind: current.kind,
        provider: signature.provider,
        providerStatus: signature.status,
        providerReleasable: signature.releasable,
        correctionStatus,
        correctionCode,
        text: document.querySelector("#reportWorkflowRoot").textContent,
        hasCompose: Boolean(document.querySelector("#reportComposeForm")),
      };
    })()`);
    check(
      "runtime termina somente em assinatura_pendente/unconfigured",
      pending.status === "assinatura_pendente"
        && pending.signatureStatus === "assinatura_pendente"
        && pending.kind === "assinatura_pendente"
        && pending.provider === "unconfigured"
        && pending.providerStatus === "assinatura_pendente"
    );
    check(
      "documento não é assinado nem liberável",
      pending.releasable === false
        && pending.providerReleasable === false
        && !pending.text.includes("assinado digitalmente")
        && pending.text.includes(
          "Nenhum documento deste fluxo é assinado ou liberado"
        )
        && !pending.hasCompose
    );
    check(
      "correção recusa predecessor não assinado",
      pending.correctionStatus === 409
        && pending.correctionCode === "laudo_nao_assinado"
    );
    check(
      "nenhuma exceção JavaScript não tratada ocorreu",
      cdp.exceptions.length === 0,
      String(cdp.exceptions.length)
    );

    if (failures) {
      children.forEach((processHandle) => {
        const log = processHandle.getLog();
        if (log) {
          console.error(
            `── log sintético ${path.basename(processHandle.spawnfile)} ──\n`
            + log
          );
        }
      });
      throw new Error(`${failures} falha(s) de browser/E2E M24C`);
    }
    console.log("RESULTADO: todos os casos M24C passaram.");
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
