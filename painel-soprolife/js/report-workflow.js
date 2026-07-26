/* M24A — fluxo seguro de laudos de espirometria.
 *
 * Integração deliberadamente fina com o painel existente:
 * - sessão, cookie HttpOnly, CSRF, RBAC e mesma origem: window.SoproM15;
 * - nenhum token, PDF ou dado clínico vai para storage/JSON público;
 * - o PDF é buscado autenticado como Blob e só então aberto em um object URL;
 * - documento finalizado não recebe composição nova; correção cria outro
 *   documento pelo contrato do backend;
 * - ROLE_GESTOR é o papel privilegiado existente para finalizar.
 */
(function () {
  "use strict";

  const SECTION_ID = "laudos-espirometria";
  const ROOT_ID = "reportWorkflowRoot";
  const CONFIG_URL = "data/m15-config.json";
  const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
  const EXAM_CODE_RE = /^ESP-\d{1,9}$/i;

  const state = {
    exams: [],
    templates: [],
    documents: [],
    selectedExamId: "",
    selectedDocument: null,
    selectedTemplateId: "",
    previewObjectUrl: "",
    previewVersionId: "",
    previewLoading: false,
    previewError: "",
    busy: false,
    notice: "",
    noticeKind: "",
    requestEpoch: 0,
    previewEpoch: 0,
  };

  function root() {
    return document.getElementById(ROOT_ID);
  }

  function client() {
    return window.SoproM15 || null;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtDate(value, withTime) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return esc(String(value).slice(0, 10));
    return new Intl.DateTimeFormat("pt-BR", withTime
      ? { dateStyle: "short", timeStyle: "short" }
      : { dateStyle: "short", timeZone: "UTC" }).format(date);
  }

  function fmtBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function statusLabel(value) {
    return {
      rascunho: "Rascunho",
      em_revisao: "Em revisão clínica",
      finalizado: "Finalizado",
    }[value] || value || "—";
  }

  function kindLabel(value) {
    return {
      original: "PDF original",
      rascunho: "Prévia de rascunho",
      finalizado: "Versão finalizada",
    }[value] || value || "Versão";
  }

  function placementLabel(value) {
    return { topo: "Topo da página", rodape: "Rodapé da página" }[value] || "—";
  }

  function selectedExam() {
    return state.exams.find((exam) => exam.id === state.selectedExamId) || null;
  }

  function versions() {
    return state.selectedDocument && Array.isArray(state.selectedDocument.versoes)
      ? state.selectedDocument.versoes : [];
  }

  function currentVersion() {
    const doc = state.selectedDocument;
    if (!doc) return null;
    return versions().find((version) => version.id === doc.current_version_id) || null;
  }

  function originalVersion() {
    return versions().find((version) => version.kind === "original") || null;
  }

  function hasDraft() {
    return versions().some((version) => version.kind === "rascunho");
  }

  function can(role) {
    const c = client();
    return Boolean(c && typeof c.can === "function" && c.can(role));
  }

  function isAuthenticated() {
    const c = client();
    return Boolean(c && typeof c.hasToken === "function" && c.hasToken());
  }

  function secureAccess() {
    const c = client();
    return c && typeof c.access === "function"
      ? c.access()
      : { secure: false, mode: "blocked" };
  }

  function announce(message, kind) {
    state.notice = message || "";
    state.noticeKind = kind || "";
    const el = document.getElementById("reportStatus");
    if (el) {
      el.className = "report-status" + (kind ? ` report-status-${kind}` : "");
      el.textContent = state.notice;
      el.hidden = !state.notice;
    }
  }

  function releasePreview() {
    state.previewEpoch += 1;
    const frame = document.getElementById("reportPdfFrame");
    if (frame) frame.removeAttribute("src");
    if (state.previewObjectUrl) {
      URL.revokeObjectURL(state.previewObjectUrl);
    }
    state.previewObjectUrl = "";
    state.previewVersionId = "";
    state.previewLoading = false;
    state.previewError = "";
  }

  function reportContentPath(documentId, versionId, mode) {
    return `/laudos/${encodeURIComponent(documentId)}/versoes/` +
      `${encodeURIComponent(versionId)}/conteudo?modo=${mode}`;
  }

  function safeDownloadName(exam, version) {
    const code = exam && EXAM_CODE_RE.test(exam.public_code || "")
      ? exam.public_code.toUpperCase() : "ESP";
    const number = Number(version && version.version_number) || 1;
    const kind = ["original", "rascunho", "finalizado"].includes(version && version.kind)
      ? version.kind : "documento";
    return `laudo-${code}-v${number}-${kind}.pdf`;
  }

  function gateHtml() {
    const access = secureAccess();
    if (!access.secure) {
      return `
        <div class="report-gate report-gate-danger" role="alert">
          <strong>Acesso bloqueado em origem HTTP insegura.</strong>
          <p>Abra o endereço HTTPS privado do painel ou o loopback local de
          desenvolvimento. Nenhuma credencial ou requisição de laudo foi enviada.</p>
        </div>`;
    }
    return `
      <div class="report-gate" role="status">
        <strong>Entre no Núcleo administrativo para acessar laudos.</strong>
        <p>O PDF e os metadados só são carregados depois de uma sessão autenticada.</p>
        <button type="button" class="m15-btn" data-report-open-login>Ir para o login seguro</button>
      </div>`;
  }

  function examListHtml() {
    if (!state.exams.length) {
      return `
        <div class="report-empty">
          Nenhum exame encontrado. Confira o código institucional ESP-… ou atualize
          a lista de exames recentes.
        </div>`;
    }
    return `
      <div class="report-exam-list" role="listbox" aria-label="Exames de espirometria">
        ${state.exams.map((exam) => {
          const active = exam.id === state.selectedExamId;
          return `
            <button type="button" class="report-exam-option${active ? " active" : ""}"
              role="option" aria-selected="${active ? "true" : "false"}"
              data-report-exam="${esc(exam.id)}" ${state.busy ? "disabled" : ""}>
              <span class="report-exam-code">${esc(exam.public_code)}</span>
              <span>${fmtDate(exam.data_exame, false)} · ${esc(exam.status_exibicao || exam.status)}</span>
              <small>${esc(exam.modalidade || "Modalidade não informada")}</small>
            </button>`;
        }).join("")}
      </div>`;
  }

  function examPanelHtml() {
    return `
      <aside class="panel report-panel report-exam-panel" aria-labelledby="reportExamTitle">
        <div class="panel-header">
          <h3 id="reportExamTitle">1. Localizar exame</h3>
          <span>Sem busca por nome</span>
        </div>
        <form id="reportExamSearch" class="report-search" role="search">
          <label for="reportExamCode">Código institucional do exame</label>
          <div class="report-search-row">
            <input id="reportExamCode" name="public_code" type="search"
              inputmode="text" autocomplete="off" placeholder="ESP-000001"
              aria-describedby="reportExamSearchHelp" ${state.busy ? "disabled" : ""}>
            <button type="submit" class="m15-btn" ${state.busy ? "disabled" : ""}>
              Localizar
            </button>
          </div>
          <p id="reportExamSearchHelp">Use somente ESP-…. Nome, telefone e CPF
            nunca entram na URL nem nos logs desta busca.</p>
        </form>
        <div class="report-exam-list-head">
          <strong>Exames recentes</strong>
          <button type="button" class="report-link-btn" data-report-refresh
            ${state.busy ? "disabled" : ""}>Atualizar</button>
        </div>
        ${examListHtml()}
      </aside>`;
  }

  function documentTabsHtml() {
    const selectedId = state.selectedDocument && state.selectedDocument.id;
    if (!state.documents.length) {
      return '<div class="report-empty">Este exame ainda não possui laudo PDF.</div>';
    }
    return `
      <div class="report-document-list" role="list" aria-label="Documentos deste exame">
        ${state.documents.map((doc, index) => {
          const correctionSource = state.documents.find(
            (candidate) => candidate.superseded_by_id === doc.id
          );
          const selected = doc.id === selectedId;
          const suffix = correctionSource
            ? "Versão corretiva"
            : (index === state.documents.length - 1 ? "Documento inicial" : "Documento");
          return `
            <div role="listitem">
              <button type="button" class="report-document-tab${selected ? " active" : ""}"
                data-report-document="${esc(doc.id)}"
                aria-pressed="${selected ? "true" : "false"}"
                ${state.busy ? "disabled" : ""}>
                <span>${esc(suffix)}</span>
                <strong>${esc(statusLabel(doc.status))}</strong>
                <small>${fmtDate(doc.created_at_local || doc.created_at_utc, true)}</small>
              </button>
            </div>`;
        }).join("")}
      </div>`;
  }

  function uploadHtml() {
    if (!can("operacional")) {
      return `
        <div class="report-permission-note">
          Seu papel permite consultar, visualizar e baixar. O envio do PDF original
          exige o papel operacional.
        </div>`;
    }
    return `
      <form id="reportUploadForm" class="report-upload-form">
        <label class="report-file-label" for="reportPdfFile">
          <span>PDF original do equipamento</span>
          <input id="reportPdfFile" name="file" type="file"
            accept="application/pdf,.pdf" required>
          <small>PDF não criptografado, até 25 MiB. O nome do arquivo não é
            usado como caminho e não será exibido nesta tela.</small>
        </label>
        <button type="submit" class="m15-btn" ${state.busy ? "disabled" : ""}>
          Enviar PDF original
        </button>
      </form>`;
  }

  function workflowProgressHtml(doc) {
    const order = { rascunho: 1, em_revisao: 2, finalizado: 3 };
    const current = order[doc.status] || 1;
    const steps = [
      ["PDF e rascunho", 1],
      ["Revisão clínica", 2],
      ["Finalizado", 3],
    ];
    return `
      <ol class="report-progress" aria-label="Ciclo de vida do laudo">
        ${steps.map(([label, step]) => `
          <li class="${step < current ? "done" : (step === current ? "current" : "")}"
            ${step === current ? 'aria-current="step"' : ""}>
            <span>${step}</span>${label}
          </li>`).join("")}
      </ol>`;
  }

  function metadataHtml(doc) {
    const exam = selectedExam();
    const version = currentVersion();
    return `
      <article class="panel report-panel" aria-labelledby="reportMetadataTitle">
        <div class="panel-header">
          <h3 id="reportMetadataTitle">Metadados seguros</h3>
          <span>Sem caminho de arquivo</span>
        </div>
        <dl class="report-metadata">
          <div><dt>Exame</dt><dd>${esc(exam ? exam.public_code : "—")}</dd></div>
          <div><dt>Estado</dt><dd><span class="report-badge report-${esc(doc.status)}">${esc(statusLabel(doc.status))}</span></dd></div>
          <div><dt>Versão atual</dt><dd>${version ? `v${version.version_number} · ${esc(kindLabel(version.kind))}` : "—"}</dd></div>
          <div><dt>Páginas</dt><dd>${version ? esc(version.page_count) : "—"}</dd></div>
          <div><dt>Tamanho</dt><dd>${version ? esc(fmtBytes(version.size_bytes)) : "—"}</dd></div>
          <div><dt>Integridade</dt><dd>${version ? `<code>sha256:${esc(version.sha256.slice(0, 16))}…</code>` : "—"}</dd></div>
          <div><dt>Criado em</dt><dd>${fmtDate(doc.created_at_local || doc.created_at_utc, true)}</dd></div>
          <div><dt>Posicionamento</dt><dd>${version && version.page_number
            ? `página ${esc(version.page_number)} · ${esc(placementLabel(version.placement))}` : "Ainda não composto"}</dd></div>
        </dl>
        ${doc.status === "finalizado" ? `
          <div class="report-signature-pending" role="status">
            <strong>assinatura digital pendente</strong>
            <span>Nenhum provedor ICP-Brasil real está configurado. Este estado
              não afirma que o PDF esteja assinado.</span>
          </div>` : ""}
        ${doc.superseded_by_id ? `
          <div class="report-immutable-note">
            Este documento finalizado foi sucedido por uma correção. Seu PDF,
            hash, data e estado final permanecem imutáveis.
          </div>` : ""}
      </article>`;
  }

  function versionsHtml() {
    if (!versions().length) return "";
    return `
      <article class="panel report-panel" aria-labelledby="reportVersionsTitle">
        <div class="panel-header">
          <h3 id="reportVersionsTitle">Versões imutáveis do arquivo</h3>
          <span>${versions().length} registro(s)</span>
        </div>
        <div class="report-version-list">
          ${versions().map((version) => `
            <div class="report-version-row">
              <div>
                <strong>v${esc(version.version_number)} · ${esc(kindLabel(version.kind))}</strong>
                <span>${esc(version.page_count)} pág. · ${esc(fmtBytes(version.size_bytes))} · ${fmtDate(version.created_at, true)}</span>
              </div>
              <div class="report-version-actions">
                <button type="button" class="m15-btn m15-btn-sec"
                  data-report-preview="${esc(version.id)}"
                  aria-label="Visualizar v${esc(version.version_number)} — ${esc(kindLabel(version.kind))}"
                  ${state.previewLoading || state.busy ? "disabled" : ""}>
                  Visualizar
                </button>
                <button type="button" class="m15-btn m15-btn-sec"
                  data-report-download="${esc(version.id)}"
                  aria-label="Baixar v${esc(version.version_number)} — ${esc(kindLabel(version.kind))}"
                  ${state.busy ? "disabled" : ""}>
                  Baixar
                </button>
              </div>
            </div>`).join("")}
        </div>
      </article>`;
  }

  function templatesHtml() {
    const activeTemplates = state.templates.filter((template) => template.ativo);
    if (!state.templates.length) {
      return `
        <div class="report-empty">
          Nenhum modelo clínico foi configurado. A decisão sobre textos clínicos
          permanece pendente; esta tela não inventa interpretação.
        </div>`;
    }
    if (!state.selectedTemplateId ||
        !activeTemplates.some((template) => template.id === state.selectedTemplateId)) {
      state.selectedTemplateId = activeTemplates.length ? activeTemplates[0].id : "";
    }
    return `
      <fieldset class="report-template-fieldset">
        <legend>Abreviações disponíveis</legend>
        <div class="report-template-grid">
          ${state.templates.map((template) => {
            const tooltip = template.texto_tooltip || template.titulo;
            const checked = template.id === state.selectedTemplateId;
            return `
              <article class="report-template-card${template.ativo ? "" : " inactive"}">
                <div class="report-template-head">
                  <label>
                    <input type="radio" name="template_id" value="${esc(template.id)}"
                      ${checked ? "checked" : ""}
                      ${template.ativo && !state.busy ? "" : "disabled"}>
                    <span>
                      <abbr title="${esc(tooltip)}">${esc(template.codigo)}</abbr>
                      ${esc(template.titulo)}
                    </span>
                  </label>
                  <small>v${esc(template.versao)}${template.ativo ? "" : " · inativo"}</small>
                </div>
                <details>
                  <summary>Texto completo do modelo ${esc(template.codigo)}</summary>
                  <div class="report-template-full-text">${esc(template.texto_completo || "Modelo sem texto cadastrado.")}</div>
                </details>
              </article>`;
          }).join("")}
        </div>
      </fieldset>`;
  }

  function composeHtml(doc) {
    if (doc.status !== "rascunho") return "";
    if (!can("operacional")) {
      return `
        <article class="panel report-panel">
          <div class="report-permission-note">
            A composição do rascunho exige o papel operacional.
          </div>
        </article>`;
    }
    const original = originalVersion();
    const maxPages = original ? original.page_count : 1;
    const activeTemplates = state.templates.some((template) => template.ativo);
    return `
      <article class="panel report-panel" aria-labelledby="reportComposeTitle">
        <div class="panel-header">
          <h3 id="reportComposeTitle">2. Modelo e posicionamento</h3>
          <span>Gera nova prévia</span>
        </div>
        ${activeTemplates ? `
          <form id="reportComposeForm" class="report-compose-form">
            ${templatesHtml()}
            <div class="report-compose-controls">
              <label for="reportPageNumber">
                Página de destino
                <input id="reportPageNumber" name="page_number" type="number"
                  min="1" max="${esc(maxPages)}" value="1" required
                  ${state.busy ? "disabled" : ""}>
                <small>Entre 1 e ${esc(maxPages)}.</small>
              </label>
              <fieldset>
                <legend>Posição do bloco</legend>
                <label><input type="radio" name="placement" value="topo"
                  ${state.busy ? "disabled" : ""}> Topo</label>
                <label><input type="radio" name="placement" value="rodape" checked
                  ${state.busy ? "disabled" : ""}> Rodapé</label>
              </fieldset>
            </div>
            <button type="submit" class="m15-btn" ${state.busy ? "disabled" : ""}>
              Gerar prévia de rascunho
            </button>
          </form>` : templatesHtml()}
      </article>`;
  }

  function lifecycleActionsHtml(doc) {
    let content = "";
    if (doc.status === "rascunho") {
      content = hasDraft()
        ? (can("operacional") ? `
            <p>A prévia está pronta. Envie para revisão clínica quando página,
              posição e texto selecionado estiverem conferidos.</p>
            <button type="button" class="m15-btn" data-report-review
              ${state.busy ? "disabled" : ""}>Enviar para revisão clínica</button>`
          : '<p>Seu papel permite somente consultar este rascunho.</p>')
        : '<p>Gere ao menos uma prévia antes de enviar para revisão.</p>';
    } else if (doc.status === "em_revisao") {
      content = can("gestor") ? `
        <p>A finalização é irreversível para este documento. O PDF final será
          preservado e continuará com assinatura digital pendente.</p>
        <button type="button" class="m15-btn report-finalize-btn"
          data-report-finalize ${state.busy ? "disabled" : ""}>
          Finalizar laudo
        </button>` : `
        <p>Documento aguardando o papel gestor, conforme o contrato privilegiado
          existente. Composição e nova submissão ficam bloqueadas.</p>`;
    } else if (doc.status === "finalizado") {
      if (doc.superseded_by_id) {
        content = `
          <p>Este documento permanece finalizado e imutável. A correção já foi
            aberta como outro documento.</p>`;
      } else if (can("operacional")) {
        content = `
          <p>Não altere o PDF finalizado. Se houver correção clínica autorizada,
            abra um novo documento vinculado ao mesmo exame.</p>
          <button type="button" class="m15-btn m15-btn-sec"
            data-report-corrective ${state.busy ? "disabled" : ""}>
            Abrir versão corretiva
          </button>`;
      } else {
        content = '<p>Documento finalizado e imutável.</p>';
      }
    }
    return `
      <article class="panel report-panel" aria-labelledby="reportLifecycleTitle">
        <div class="panel-header">
          <h3 id="reportLifecycleTitle" tabindex="-1">3. Revisão e ciclo de vida</h3>
          <span>RBAC aplicado na interface e no servidor</span>
        </div>
        <div class="report-lifecycle-actions">${content}</div>
      </article>`;
  }

  function previewHtml() {
    const exam = selectedExam();
    const version = versions().find(
      (item) => item.id === state.previewVersionId
    ) || currentVersion();
    let bodyHtml;
    if (state.previewLoading) {
      bodyHtml = '<div class="report-preview-state" role="status">Carregando PDF autenticado…</div>';
    } else if (state.previewError) {
      bodyHtml = `<div class="report-preview-state report-preview-error" role="alert">${esc(state.previewError)}</div>`;
    } else if (state.previewObjectUrl) {
      bodyHtml = `
        <iframe id="reportPdfFrame" class="report-pdf-frame"
          src="${esc(state.previewObjectUrl)}"
          title="Visualização autenticada do PDF — ${esc(exam ? exam.public_code : "exame")}"
          referrerpolicy="no-referrer"></iframe>`;
    } else {
      bodyHtml = `
        <div class="report-preview-state">
          Selecione uma versão para abrir a prévia autenticada.
        </div>`;
    }
    return `
      <article class="panel report-panel report-preview-panel" aria-labelledby="reportPreviewTitle">
        <div class="panel-header">
          <h3 id="reportPreviewTitle">Visualização autenticada do PDF</h3>
          <span>${version ? `v${esc(version.version_number)} · ${esc(kindLabel(version.kind))}` : "Nenhuma versão aberta"}</span>
        </div>
        <p class="report-preview-security">
          O visualizador recebe um Blob temporário da sessão. Nenhum caminho de
          sistema de arquivos ou URL pública de documento é exposto.
        </p>
        ${bodyHtml}
      </article>`;
  }

  function selectedDocumentHtml() {
    const doc = state.selectedDocument;
    if (!doc) {
      return `
        <article class="panel report-panel">
          <div class="panel-header">
            <h3>2. Enviar PDF original</h3>
            <span>Armazenamento privado</span>
          </div>
          ${uploadHtml()}
        </article>`;
    }
    return `
      ${workflowProgressHtml(doc)}
      <div class="report-document-grid">
        <div class="report-stack">
          ${metadataHtml(doc)}
          ${versionsHtml()}
          ${composeHtml(doc)}
          ${lifecycleActionsHtml(doc)}
        </div>
        ${previewHtml()}
      </div>`;
  }

  function workspaceHtml() {
    const exam = selectedExam();
    return `
      <div id="reportStatus" class="report-status${state.noticeKind ? ` report-status-${esc(state.noticeKind)}` : ""}"
        role="status" aria-live="polite" tabindex="-1" ${state.notice ? "" : "hidden"}>
        ${esc(state.notice)}
      </div>
      <div class="report-layout" aria-busy="${state.busy ? "true" : "false"}">
        ${examPanelHtml()}
        <main class="report-main">
          ${exam ? `
            <article class="panel report-panel report-document-picker" aria-labelledby="reportDocumentTitle">
              <div class="panel-header">
                <h3 id="reportDocumentTitle">Laudos de ${esc(exam.public_code)}</h3>
                <span>${esc(exam.status_exibicao || exam.status)} · ${fmtDate(exam.data_exame, false)}</span>
              </div>
              ${documentTabsHtml()}
            </article>
            ${selectedDocumentHtml()}`
          : `
            <div class="report-state">
              Selecione um exame pelo código institucional para iniciar ou
              continuar o fluxo de laudo.
            </div>`}
        </main>
      </div>`;
  }

  function render() {
    const mount = root();
    if (!mount) return;
    if (!secureAccess().secure || !isAuthenticated()) {
      releasePreview();
      mount.innerHTML = gateHtml();
      return;
    }
    mount.innerHTML = workspaceHtml();
  }

  async function openPreview(documentId, versionId) {
    const c = client();
    if (!c || typeof c.apiBlob !== "function") {
      announce("Cliente autenticado de PDF indisponível.", "error");
      return;
    }
    releasePreview();
    const epoch = ++state.previewEpoch;
    state.previewLoading = true;
    state.previewVersionId = versionId;
    render();
    try {
      const blob = await c.apiBlob(
        reportContentPath(documentId, versionId, "inline")
      );
      if (epoch !== state.previewEpoch) return;
      state.previewObjectUrl = URL.createObjectURL(blob);
      state.previewLoading = false;
      state.previewError = "";
      render();
    } catch (error) {
      if (epoch !== state.previewEpoch) return;
      state.previewLoading = false;
      state.previewError = error.message || String(error);
      render();
    }
  }

  async function downloadVersion(versionId) {
    const c = client();
    const doc = state.selectedDocument;
    const exam = selectedExam();
    const version = versions().find((item) => item.id === versionId);
    if (!c || !doc || !version || typeof c.apiBlob !== "function") return;
    announce("Preparando download autenticado…");
    try {
      const blob = await c.apiBlob(
        reportContentPath(doc.id, version.id, "download")
      );
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = safeDownloadName(exam, version);
      link.rel = "noopener";
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      announce("Download autenticado preparado.", "success");
    } catch (error) {
      announce(`Não foi possível baixar o PDF: ${error.message || error}`, "error");
    }
  }

  async function loadDocument(documentId, previewCurrent) {
    const c = client();
    if (!c) return;
    const epoch = ++state.requestEpoch;
    state.busy = true;
    render();
    try {
      const detail = await c.api(`/laudos/${encodeURIComponent(documentId)}`);
      if (epoch !== state.requestEpoch) return;
      if (detail.spirometry_exam_id !== state.selectedExamId) {
        throw new Error("O laudo retornado não corresponde ao exame selecionado.");
      }
      state.selectedDocument = detail;
      state.busy = false;
      render();
      if (previewCurrent !== false && detail.current_version_id) {
        await openPreview(detail.id, detail.current_version_id);
      }
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      announce(`Não foi possível abrir o laudo: ${error.message || error}`, "error");
      render();
    }
  }

  async function loadDocuments(examId, preferredDocumentId) {
    const c = client();
    if (!c) return;
    const epoch = ++state.requestEpoch;
    releasePreview();
    state.busy = true;
    render();
    try {
      const docs = await c.api(`/laudos?exam_id=${encodeURIComponent(examId)}`);
      if (epoch !== state.requestEpoch || examId !== state.selectedExamId) return;
      state.documents = Array.isArray(docs) ? docs : [];
      const preferred = state.documents.find((doc) => doc.id === preferredDocumentId);
      const target = preferred || state.documents[0] || null;
      if (!target) {
        state.selectedDocument = null;
        state.busy = false;
        render();
        return;
      }
      state.busy = false;
      await loadDocument(target.id, true);
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      announce(`Não foi possível listar os laudos: ${error.message || error}`, "error");
      render();
    }
  }

  async function loadWorkspace() {
    const mount = root();
    if (!mount) return;
    if (!secureAccess().secure || !isAuthenticated()) {
      state.requestEpoch += 1;
      state.exams = [];
      state.templates = [];
      state.documents = [];
      state.selectedExamId = "";
      state.selectedDocument = null;
      render();
      return;
    }
    const c = client();
    const epoch = ++state.requestEpoch;
    releasePreview();
    state.busy = true;
    state.notice = "";
    render();
    try {
      const [examPage, templates] = await Promise.all([
        c.api("/espirometrias?tamanho=50"),
        c.api("/laudos/templates"),
      ]);
      if (epoch !== state.requestEpoch) return;
      state.exams = Array.isArray(examPage.itens) ? examPage.itens : [];
      state.templates = Array.isArray(templates) ? templates : [];
      if (!state.exams.some((exam) => exam.id === state.selectedExamId)) {
        state.selectedExamId = "";
        state.documents = [];
        state.selectedDocument = null;
      }
      state.busy = false;
      render();
      if (state.selectedExamId) {
        await loadDocuments(
          state.selectedExamId,
          state.selectedDocument && state.selectedDocument.id
        );
      }
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      render();
      announce(`Fluxo de laudos indisponível: ${error.message || error}`, "error");
    }
  }

  async function locateExam(rawCode) {
    const code = String(rawCode || "").trim().toUpperCase();
    if (!EXAM_CODE_RE.test(code)) {
      announce("Informe somente um código institucional no formato ESP-000001.", "error");
      return;
    }
    const c = client();
    const epoch = ++state.requestEpoch;
    state.busy = true;
    render();
    try {
      const page = await c.api(
        `/espirometrias?public_code=${encodeURIComponent(code)}&tamanho=10`
      );
      if (epoch !== state.requestEpoch) return;
      state.exams = Array.isArray(page.itens) ? page.itens : [];
      state.busy = false;
      if (state.exams.length === 1) {
        state.selectedExamId = state.exams[0].id;
        announce(`Exame ${code} localizado.`, "success");
        await loadDocuments(state.selectedExamId);
      } else {
        state.selectedExamId = "";
        state.documents = [];
        state.selectedDocument = null;
        render();
        announce(`Nenhum exame encontrado para ${code}.`, "error");
      }
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      render();
      announce(`Falha ao localizar exame: ${error.message || error}`, "error");
    }
  }

  async function uploadOriginal(file) {
    const exam = selectedExam();
    const c = client();
    if (!exam || !c) return;
    const pdfName = file && /\.pdf$/i.test(file.name || "");
    const pdfType = file && (!file.type || file.type === "application/pdf");
    if (!file || !pdfName || !pdfType) {
      announce("Selecione um arquivo PDF.", "error");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      announce("O PDF excede o limite de 25 MiB.", "error");
      return;
    }
    const data = new FormData();
    data.append("exam_id", exam.id);
    data.append("file", file);
    const epoch = ++state.requestEpoch;
    state.busy = true;
    render();
    try {
      const created = await c.api("/laudos", { method: "POST", body: data });
      if (epoch !== state.requestEpoch || exam.id !== state.selectedExamId) return;
      state.busy = false;
      announce("PDF original recebido e validado.", "success");
      await loadDocuments(exam.id, created.id);
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      render();
      announce(`Upload recusado: ${error.message || error}`, "error");
    }
  }

  async function composeDraft(form) {
    const doc = state.selectedDocument;
    const c = client();
    if (!doc || !c) return;
    const templateId = form.elements.template_id
      ? form.elements.template_id.value : "";
    const pageNumber = Number(form.elements.page_number.value);
    const placement = form.elements.placement.value;
    if (!templateId) {
      announce("Escolha uma abreviação de modelo ativa.", "error");
      return;
    }
    state.selectedTemplateId = templateId;
    const epoch = ++state.requestEpoch;
    state.busy = true;
    render();
    try {
      await c.api(`/laudos/${encodeURIComponent(doc.id)}/compor`, {
        method: "POST",
        body: JSON.stringify({
          template_id: templateId,
          page_number: pageNumber,
          placement,
        }),
      });
      if (epoch !== state.requestEpoch || doc.id !== state.selectedDocument?.id) return;
      state.busy = false;
      announce("Nova prévia de rascunho gerada.", "success");
      await loadDocuments(state.selectedExamId, doc.id);
    } catch (error) {
      if (epoch !== state.requestEpoch) return;
      state.busy = false;
      render();
      announce(`Não foi possível gerar a prévia: ${error.message || error}`, "error");
    }
  }

  async function transition(path, successMessage, preferredDocumentId) {
    const c = client();
    const examId = state.selectedExamId;
    const epoch = ++state.requestEpoch;
    state.busy = true;
    render();
    try {
      const result = await c.api(path, { method: "POST" });
      if (epoch !== state.requestEpoch || examId !== state.selectedExamId) return result;
      state.busy = false;
      announce(successMessage, "success");
      await loadDocuments(
        state.selectedExamId,
        preferredDocumentId === "result" ? result.id : preferredDocumentId
      );
    } catch (error) {
      if (epoch !== state.requestEpoch) throw error;
      state.busy = false;
      render();
      announce(`Ação recusada: ${error.message || error}`, "error");
      throw error;
    }
  }

  function openConfirm(options) {
    const previousFocus = document.activeElement;
    const overlay = document.createElement("div");
    const titleId = `reportDialogTitle-${Date.now()}`;
    const descId = `reportDialogDesc-${Date.now()}`;
    overlay.className = "m15-modal-overlay report-modal-overlay";
    overlay.innerHTML = `
      <div class="m15-modal report-modal" role="dialog" aria-modal="true"
        aria-labelledby="${titleId}" aria-describedby="${descId}">
        <h3 id="${titleId}">${esc(options.title)}</h3>
        <p id="${descId}">${esc(options.description)}</p>
        <div class="report-modal-error" role="alert" hidden></div>
        <div class="m15-modal-actions">
          <button type="button" class="m15-btn m15-btn-sec" data-report-modal-cancel>
            Cancelar
          </button>
          <button type="button" class="m15-btn ${options.danger ? "report-finalize-btn" : ""}"
            data-report-modal-confirm>${esc(options.confirmLabel)}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const modal = overlay.querySelector(".report-modal");
    const confirm = overlay.querySelector("[data-report-modal-confirm]");
    const cancel = overlay.querySelector("[data-report-modal-cancel]");
    let submitting = false;

    function focusable() {
      return Array.from(modal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )).filter((element) => !element.disabled && element.offsetParent !== null);
    }
    function close() {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      if (previousFocus && previousFocus.isConnected && previousFocus.focus) {
        previousFocus.focus();
      } else {
        const fallback = document.getElementById("reportStatus") ||
          document.getElementById("reportLifecycleTitle");
        if (fallback && fallback.focus) fallback.focus();
      }
    }
    function onKey(event) {
      if (event.key === "Escape") {
        if (submitting) return;
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey, true);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay && !submitting) close();
    });
    cancel.addEventListener("click", close);
    confirm.addEventListener("click", async () => {
      submitting = true;
      confirm.disabled = true;
      cancel.disabled = true;
      try {
        await options.onConfirm();
        submitting = false;
        close();
      } catch (error) {
        submitting = false;
        const alert = overlay.querySelector(".report-modal-error");
        alert.textContent = error.message || String(error);
        alert.hidden = false;
        confirm.disabled = false;
        cancel.disabled = false;
        confirm.focus();
      }
    });
    confirm.focus();
  }

  function handleClick(event) {
    const target = event.target.closest && event.target.closest("button");
    if (!target) return;
    if (target.matches("[data-report-open-login]")) {
      const nav = document.querySelector('.nav-item[data-section="m15-nucleo"]');
      if (nav) nav.click();
      return;
    }
    if (target.matches("[data-report-refresh]")) {
      loadWorkspace();
      return;
    }
    if (target.matches("[data-report-exam]")) {
      state.selectedExamId = target.getAttribute("data-report-exam");
      state.documents = [];
      state.selectedDocument = null;
      loadDocuments(state.selectedExamId);
      return;
    }
    if (target.matches("[data-report-document]")) {
      loadDocument(target.getAttribute("data-report-document"), true);
      return;
    }
    if (target.matches("[data-report-preview]") && state.selectedDocument) {
      openPreview(
        state.selectedDocument.id,
        target.getAttribute("data-report-preview")
      );
      return;
    }
    if (target.matches("[data-report-download]")) {
      downloadVersion(target.getAttribute("data-report-download"));
      return;
    }
    const doc = state.selectedDocument;
    if (!doc) return;
    if (target.matches("[data-report-review]")) {
      openConfirm({
        title: "Enviar para revisão clínica?",
        description: "A composição deste documento ficará bloqueada durante a revisão.",
        confirmLabel: "Enviar para revisão",
        onConfirm: () => transition(
          `/laudos/${encodeURIComponent(doc.id)}/revisao`,
          "Documento enviado para revisão clínica.",
          doc.id
        ),
      });
      return;
    }
    if (target.matches("[data-report-finalize]")) {
      openConfirm({
        title: "Finalizar este laudo?",
        description: "Esta ação é irreversível para este documento. O PDF ficará imutável e a assinatura digital permanecerá pendente até existir um provedor real.",
        confirmLabel: "Finalizar de forma imutável",
        danger: true,
        onConfirm: () => transition(
          `/laudos/${encodeURIComponent(doc.id)}/finalizar`,
          "Laudo finalizado. Assinatura digital pendente.",
          doc.id
        ),
      });
      return;
    }
    if (target.matches("[data-report-corrective]")) {
      openConfirm({
        title: "Abrir uma versão corretiva?",
        description: "Um novo documento em rascunho será criado. O laudo finalizado atual não terá PDF, hash, data ou estado alterados.",
        confirmLabel: "Criar novo documento corretivo",
        onConfirm: () => transition(
          `/laudos/${encodeURIComponent(doc.id)}/nova-versao-corretiva`,
          "Versão corretiva aberta como novo documento.",
          "result"
        ),
      });
    }
  }

  function handleSubmit(event) {
    if (event.target.id === "reportExamSearch") {
      event.preventDefault();
      locateExam(event.target.elements.public_code.value);
      return;
    }
    if (event.target.id === "reportUploadForm") {
      event.preventDefault();
      uploadOriginal(event.target.elements.file.files[0]);
      return;
    }
    if (event.target.id === "reportComposeForm") {
      event.preventDefault();
      composeDraft(event.target);
    }
  }

  function handleChange(event) {
    if (event.target.matches('input[name="template_id"]')) {
      state.selectedTemplateId = event.target.value;
    }
  }

  function bindClient() {
    const c = client();
    if (!c) {
      window.setTimeout(bindClient, 250);
      return;
    }
    if (typeof c.onSessionChange === "function") {
      c.onSessionChange(() => {
        if (!isAuthenticated()) releasePreview();
        if (document.getElementById(SECTION_ID).classList.contains("active")) {
          loadWorkspace();
        } else {
          render();
        }
      });
    }
    render();
  }

  async function boot() {
    const mount = root();
    if (!mount) return;
    let config = {};
    try {
      const response = await fetch(CONFIG_URL, { credentials: "same-origin" });
      config = response.ok ? await response.json() : {};
    } catch (error) {
      config = {};
    }
    if (config.enabled !== true ||
        config.api_base !== "/painel-soprolife/api/m15") {
      return;
    }
    document.querySelectorAll("[data-report-entry]").forEach((entry) => {
      entry.hidden = false;
    });
    mount.addEventListener("click", handleClick);
    mount.addEventListener("submit", handleSubmit);
    mount.addEventListener("change", handleChange);
    const nav = document.querySelector(`.nav-item[data-section="${SECTION_ID}"]`);
    if (nav) nav.addEventListener("click", loadWorkspace);
    document.addEventListener("click", (event) => {
      const otherNav = event.target.closest && event.target.closest(".nav-item[data-section]");
      if (otherNav && otherNav.getAttribute("data-section") !== SECTION_ID) {
        releasePreview();
      }
    });
    window.addEventListener("beforeunload", releasePreview);
    bindClient();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
