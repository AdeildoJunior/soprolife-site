/* M24C — atribuição médica e workspace clínico controlado.
 *
 * A feature continua default-off em duas camadas. Quando explicitamente
 * habilitada, todo JSON/PDF usa o cliente autenticado window.SoproM15;
 * nenhum documento, identidade de paciente ou interpretação entra em
 * localStorage, URL pública ou snapshot estático.
 */
(function () {
  "use strict";

  const SECTION_ID = "laudos-espirometria";
  const ROOT_ID = "reportWorkflowRoot";
  const CONFIG_URL = "data/m15-config.json";
  // M24D — aviso obrigatório do piloto interno controlado. Precisa
  // aparecer, ao vivo, sempre que o modo ativo for "pilot" — o mesmo texto
  // exato usado no rodapé congelado do PDF (services/report_catalog.py).
  const PILOT_WARNING =
    "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR AO PACIENTE";
  const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
  const EXAM_CODE_RE = /^ESP-\d{1,9}$/i;
  const UF_VALUES = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
    "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
    "RR", "SC", "SP", "SE", "TO",
  ];
  const ORIGINS = [
    ["pastore", "Pastore"],
    ["coworking", "coworking"],
    ["residencial", "residencial"],
    ["clinica_parceira", "clínica parceira"],
    ["empresa_pcmso", "empresa / PCMSO"],
    ["outro", "outro"],
  ];
  const REASSIGN_REASONS = [
    ["assignment_correction", "Correção de atribuição"],
    ["physician_unavailable", "Médico indisponível"],
    ["profile_suspended", "Perfil suspenso"],
    ["operational_redistribution", "Redistribuição operacional"],
  ];
  // M25.2 — confirmações conscientes exigidas pela API. O texto exato é
  // parte do contrato: nenhum clique isolado assina ou libera um laudo.
  const RELEASE_CONFIRMATION = "ASSINAR E LIBERAR";
  const ADDENDUM_CONFIRMATION = "PUBLICAR ADENDO";

  const state = {
    reportsMode: "disabled",
    queue: [],
    operational: [],
    physicians: [],
    templates: [],
    adminAccounts: [],
    adminTemplates: [],
    selectedDocumentId: "",
    selectedOperationalId: "",
    selectedAdminUserId: "",
    selectedAdminTemplateId: "",
    detail: null,
    locatedExam: null,
    interpretation: "",
    selectedTemplateId: "",
    statusFilter: "",
    busy: false,
    notice: "",
    noticeKind: "",
    pdfUrls: { original: "", generated: "" },
    loadEpoch: 0,
    // ------------------------------------------------------------ M25.2
    // Estado do laudo PRÓPRIO da SoproLife. O catálogo vem do servidor por
    // documento (ele sabe se o exame tem fase pós-broncodilatador); o
    // navegador nunca decide grau, conclusão ou compatibilidade.
    catalog: null,
    conclusionCode: "",
    customConclusion: "",
    bronchodilatorCode: "",
    finalText: "",
    observations: "",
    // Identidade EXATA da prévia conferida, exigida na liberação.
    previewVersionId: "",
    previewTextSha256: "",
    confirmRelease: false,
    addendumText: "",
    documents: null,
    suggestedText: "",
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

  function options(items, selected) {
    return items.map(([value, label]) =>
      `<option value="${esc(value)}"${String(value) === String(selected || "")
        ? " selected" : ""}>${esc(label)}</option>`
    ).join("");
  }

  function fmtDate(value, withTime) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return esc(String(value).slice(0, 10));
    return new Intl.DateTimeFormat("pt-BR", withTime
      ? { dateStyle: "short", timeStyle: "short" }
      : { dateStyle: "short", timeZone: "UTC" }).format(parsed);
  }

  function statusLabel(value) {
    return {
      atribuido: "Atribuído",
      em_elaboracao: "Em elaboração clínica",
      assinatura_pendente: "Assinatura qualificada pendente",
      assinado: "Assinado",
      liberado: "Laudo liberado",
    }[value] || value || "—";
  }

  function kindLabel(value) {
    return {
      original: "Exame técnico (MIR)",
      rascunho: "Prévia clínica",
      assinatura_pendente: "Preparado para assinatura",
      assinado: "Versão assinada",
      laudo_previa: "Prévia do laudo",
      laudo_liberado: "Laudo liberado",
      laudo_adendo: "Laudo com adendo",
    }[value] || value || "Versão";
  }

  // M25.2 — a versão de laudo NATIVO mais recente (prévia, liberado ou
  // adendo). É o "documento 2"; o PDF da MIR continua sendo o documento 1.
  const NATIVE_KINDS = ["laudo_previa", "laudo_liberado", "laudo_adendo"];

  function latestNativeVersion() {
    const versions = state.detail && Array.isArray(state.detail.versoes)
      ? state.detail.versoes : [];
    return [...versions]
      .filter((version) => NATIVE_KINDS.includes(version.kind))
      .sort((a, b) => a.version_number - b.version_number)
      .pop() || null;
  }

  function currentUser() {
    const c = client();
    return c && typeof c.getUser === "function" ? c.getUser() : null;
  }

  function explicit(role) {
    const user = currentUser();
    return Boolean(user && Array.isArray(user.papeis) && user.papeis.includes(role));
  }

  function can(role) {
    const c = client();
    return Boolean(c && typeof c.can === "function" && c.can(role));
  }

  function authenticated() {
    const c = client();
    return Boolean(c && typeof c.hasToken === "function" && c.hasToken());
  }

  function readableError(error) {
    if (!error) return "Não foi possível concluir a operação.";
    const message = typeof error.message === "string"
      ? error.message : "Não foi possível concluir a operação.";
    return error.code ? `${message} (${error.code})` : message;
  }

  function announce(message, kind) {
    state.notice = message || "";
    state.noticeKind = kind || "";
    const target = document.getElementById("reportStatus");
    if (target) {
      target.textContent = state.notice;
      target.className = `report-status${kind ? ` report-status-${kind}` : ""}`;
      target.hidden = !state.notice;
    }
  }

  function releasePdfUrls() {
    Object.keys(state.pdfUrls).forEach((key) => {
      if (state.pdfUrls[key]) URL.revokeObjectURL(state.pdfUrls[key]);
      state.pdfUrls[key] = "";
    });
  }

  function reportContentPath(documentId, versionId, mode) {
    return `/laudos/${encodeURIComponent(documentId)}/versoes/` +
      `${encodeURIComponent(versionId)}/conteudo?modo=${mode || "inline"}`;
  }

  function reportsFeatureEnabled(config) {
    if (config && config.reports_enabled === true) return true;
    const loopback = ["127.0.0.1", "::1", "localhost"].includes(
      window.location.hostname
    );
    if (!loopback) return false;
    try {
      // Override aceito somente em loopback para E2E/desenvolvimento isolado.
      return window.localStorage.getItem("soproM24AReports") === "on";
    } catch (error) {
      return false;
    }
  }

  function setPhysicianNavigationIsolation() {
    const user = currentUser();
    const roles = user && Array.isArray(user.papeis) ? user.papeis : [];
    const physicianOnly = roles.length === 1 && roles[0] === "medico";
    document.body.classList.toggle("report-physician-only", physicianOnly);
    if (physicianOnly) {
      document.querySelectorAll(".section").forEach((section) => {
        section.classList.toggle("active", section.id === SECTION_ID);
      });
      document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.toggle(
          "active", item.getAttribute("data-section") === SECTION_ID
        );
      });
    }
  }

  function selectedOperational() {
    return state.operational.find(
      (item) => item.document_id === state.selectedOperationalId
    ) || null;
  }

  function selectedAdminAccount() {
    return state.adminAccounts.find(
      (item) => item.user.id === state.selectedAdminUserId
    ) || null;
  }

  function selectedAdminTemplate() {
    return state.adminTemplates.find(
      (item) => item.id === state.selectedAdminTemplateId
    ) || null;
  }

  function renderStatus() {
    return `<div id="reportStatus" class="report-status${
      state.noticeKind ? ` report-status-${esc(state.noticeKind)}` : ""
    }" role="status" aria-live="polite"${state.notice ? "" : " hidden"}>${
      esc(state.notice)
    }</div>`;
  }

  function renderPilotWarning() {
    if (state.reportsMode !== "pilot") return "";
    return `<div id="reportPilotWarning" class="report-pilot-warning" role="alert">${
      esc(PILOT_WARNING)
    }</div>`;
  }

  function renderUnauthenticated() {
    return `
      <div class="report-state" role="status">
        Entre pelo Núcleo administrativo para acessar o fluxo protegido.
      </div>`;
  }

  function renderQueue() {
    const items = state.queue.length
      ? state.queue.map((item) => `
          <button type="button" class="report-queue-item${
            item.document_id === state.selectedDocumentId ? " is-selected" : ""
          }" data-report-open="${esc(item.document_id)}"
            role="option" aria-selected="${
              item.document_id === state.selectedDocumentId ? "true" : "false"
            }">
            <strong>${esc(item.report_code)}</strong>
            <span>${esc(item.exam_code)} · ${fmtDate(item.exam_date, false)}</span>
            <span>${esc(item.origin_type)}${
              item.origin_label ? ` · ${esc(item.origin_label)}` : ""
            }</span>
            <span class="report-status-chip report-${esc(item.status)}">${
              esc(statusLabel(item.status))
            }</span>
            ${item.is_corrective
              ? `<span class="report-queue-flag">corrigido</span>` : ""}
            ${item.locked
              ? `<span class="report-queue-flag is-locked">liberado</span>` : ""}
          </button>`).join("")
      : `<div class="report-empty">Nenhum laudo atribuído neste filtro.</div>`;
    return `
      <section class="report-panel report-queue-panel" aria-labelledby="myReportsTitle">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">Fila clínica restrita</p>
            <h3 id="myReportsTitle">Meus laudos</h3>
          </div>
          <label class="report-compact-field" for="reportStatusFilter">
            Status
            <select id="reportStatusFilter">
              ${options([
                ["", "Todos"],
                ["atribuido", "Pendente"],
                ["em_elaboracao", "Em elaboração"],
                ["liberado", "Liberado"],
                ["assinatura_pendente", "Assinatura pendente"],
                ["assinado", "Assinado"],
              ], state.statusFilter)}
            </select>
          </label>
        </div>
        <div class="report-queue-list" role="listbox"
          aria-label="Laudos atribuídos ao médico autenticado">
          ${items}
        </div>
      </section>`;
  }

  function renderTemplateSelector() {
    const cards = state.templates.map((template) => `
      <label class="report-template-card">
        <span class="report-template-choice">
          <input type="radio" name="template_id" value="${esc(template.id)}"${
            state.selectedTemplateId === template.id ? " checked" : ""
          }>
          <abbr title="${esc(template.texto_tooltip || template.titulo)}">${
            esc(template.codigo)
          }</abbr>
          <span>${esc(template.titulo)}</span>
        </span>
        <details>
          <summary>Texto completo do modelo ${esc(template.codigo)}</summary>
          <div class="report-template-full-text">${esc(template.texto_completo)}</div>
        </details>
      </label>`).join("");
    return `
      <fieldset class="report-template-selector">
        <legend>Modelo clínico aprovado</legend>
        <label class="report-template-card report-template-manual">
          <span class="report-template-choice">
            <input type="radio" name="template_id" value=""${
              state.selectedTemplateId ? "" : " checked"
            }>
            <strong>Texto controlado sem modelo</strong>
          </span>
          <span class="report-help">A conclusão é sempre escrita e revisada pelo médico; não há interpretação automática.</span>
        </label>
        ${cards || `<div class="report-empty">Nenhum template clínico aprovado.</div>`}
      </fieldset>`;
  }

  function versionByKind(kind) {
    const versions = state.detail && Array.isArray(state.detail.versoes)
      ? state.detail.versoes : [];
    return [...versions].reverse().find((version) => version.kind === kind) || null;
  }

  function currentVersion() {
    const detail = state.detail;
    if (!detail || !Array.isArray(detail.versoes)) return null;
    return detail.versoes.find(
      (version) => version.id === detail.current_version_id
    ) || null;
  }

  function renderPdfFrame(kind, title, version) {
    const src = state.pdfUrls[kind];
    if (!version) {
      return `<div class="report-pdf-placeholder">Ainda não há PDF ${esc(title.toLowerCase())}.</div>`;
    }
    if (!src) {
      return `<div class="report-pdf-placeholder" role="status">Carregando ${esc(title.toLowerCase())}…</div>`;
    }
    return `<iframe class="report-pdf-frame" src="${esc(src)}"
      id="reportPdf${kind === "original" ? "Original" : "Generated"}"
      title="${esc(title)} — visualização autenticada"
      referrerpolicy="no-referrer"></iframe>`;
  }

  // ------------------------------------------------------------- M25.2

  function renderConclusionPicker() {
    const catalog = state.catalog;
    if (!catalog) {
      return `<div class="report-empty" role="status">Carregando catálogo de conclusões…</div>`;
    }
    const buttons = catalog.conclusoes.map((option) => `
      <button type="button"
        class="report-conclusion-chip${
          state.conclusionCode === option.codigo ? " is-selected" : ""
        }${option.personalizado ? " is-custom" : ""}"
        data-report-conclusion="${esc(option.codigo)}"
        aria-pressed="${state.conclusionCode === option.codigo ? "true" : "false"}"
        title="${esc(option.texto || "Escreva a conclusão livremente")}">
        ${esc(option.rotulo)}
      </button>`).join("");

    const bdButtons = catalog.complementos_bd.map((option) => `
      <button type="button"
        class="report-bd-chip${
          state.bronchodilatorCode === option.codigo ? " is-selected" : ""
        }"
        data-report-bd="${esc(option.codigo)}"
        aria-pressed="${
          state.bronchodilatorCode === option.codigo ? "true" : "false"
        }"
        title="${esc(option.texto || "Não acrescenta frase de resposta ao broncodilatador")}">
        ${esc(option.rotulo)}
      </button>`).join("");

    return `
      <fieldset class="report-conclusion-picker">
        <legend>Conclusão</legend>
        <p class="report-help">O botão mostra a abreviação; o PDF recebe o texto por extenso. O grau é decisão exclusivamente sua — o sistema não calcula nem sugere.</p>
        <div class="report-chip-grid">${buttons}</div>
        ${state.conclusionCode === "PERSONALIZADO" ? `
          <label for="reportCustomConclusion" class="report-custom-conclusion">
            Conclusão personalizada
            <textarea id="reportCustomConclusion" name="conclusion_custom_text"
              maxlength="2000" rows="3"
              placeholder="Escreva a conclusão completa.">${esc(state.customConclusion)}</textarea>
          </label>` : ""}
      </fieldset>

      <fieldset class="report-conclusion-picker">
        <legend>Pós-broncodilatador</legend>
        ${catalog.exame_com_pos_bd
          ? `<p class="report-help">Exame com fase pós-broncodilatador.</p>`
          : `<p class="report-help">Este exame não possui fase pós-broncodilatador; opções incompatíveis não são oferecidas.</p>`}
        <div class="report-chip-grid">${bdButtons}</div>
      </fieldset>`;
  }

  function renderNativeReportForm(detail) {
    const editable = ["atribuido", "em_elaboracao"].includes(detail.status);
    if (!editable) return "";
    const canPreview = Boolean(state.conclusionCode);
    return `
      <form id="reportNativeForm" class="report-clinical-form report-native-form">
        <h4>Laudo médico da SoproLife</h4>
        <p class="report-help">Documento próprio, gerado pelo Centro de Comando. O PDF técnico da MIR permanece intacto e continua sendo baixado separadamente.</p>
        ${renderConclusionPicker()}
        <label for="reportFinalText">
          Texto final do laudo
          <textarea id="reportFinalText" name="final_text" maxlength="6000"
            rows="5" aria-describedby="reportFinalTextHelp"
            placeholder="Escolha uma conclusão para montar o texto — e edite livremente antes de assinar.">${esc(state.finalText)}</textarea>
          <span id="reportFinalTextHelp" class="report-help">Este é o texto que será assinado. Você pode reescrevê-lo por completo.</span>
        </label>
        <label for="reportObservations">
          Observações complementares (opcional)
          <textarea id="reportObservations" name="observations" maxlength="2000"
            rows="3">${esc(state.observations)}</textarea>
        </label>
        <button class="m15-btn m15-btn-primary" type="submit"${
          state.busy || !canPreview ? " disabled" : ""
        }>Gerar prévia do laudo</button>
      </form>`;
  }

  function renderReleaseAction(detail) {
    if (detail.status !== "em_elaboracao" || !state.previewVersionId) return "";
    if (state.confirmRelease) {
      return `
        <div class="report-release-confirm" role="alertdialog"
          aria-labelledby="reportReleaseConfirmTitle" aria-modal="false">
          <h4 id="reportReleaseConfirmTitle" tabindex="-1">Confirmar assinatura e liberação</h4>
          <p>Você vai <strong>assinar e liberar</strong> este laudo com a sua identificação profissional. O conteúdo será congelado e o documento passa a valer para entrega.</p>
          <ul>
            <li>Confira a prévia exibida acima — é exatamente o PDF final.</li>
            <li>Depois da liberação, correções só entram como <strong>adendo</strong> ou <strong>versão corretiva</strong>; a versão anterior é preservada.</li>
            <li>A liberação é registrada com seu usuário, data e hora.</li>
          </ul>
          <div class="report-release-buttons">
            <button type="button" class="m15-btn m15-btn-danger"
              data-report-release-confirm${state.busy ? " disabled" : ""}>
              Sim, assinar e liberar laudo
            </button>
            <button type="button" class="m15-btn" data-report-release-cancel>
              Cancelar
            </button>
          </div>
        </div>`;
    }
    return `
      <div class="report-release-action">
        <p>Confira a prévia. A assinatura só é aplicada após a sua confirmação consciente.</p>
        <button type="button" class="m15-btn m15-btn-primary report-release-cta"
          data-report-release-open${state.busy ? " disabled" : ""}>
          Assinar e liberar laudo
        </button>
      </div>`;
  }

  function renderAddendumForm(detail) {
    if (detail.status !== "liberado") return "";
    return `
      <form id="reportAddendumForm" class="report-addendum-form">
        <h4>Adendo ao laudo liberado</h4>
        <p class="report-help">O adendo gera uma versão nova preservando integralmente a versão liberada anterior.</p>
        <label for="reportAddendumText">
          Texto do adendo
          <textarea id="reportAddendumText" name="body_text" maxlength="4000"
            rows="3" required>${esc(state.addendumText)}</textarea>
        </label>
        <button class="m15-btn" type="submit"${
          state.busy ? " disabled" : ""
        }>Publicar adendo</button>
      </form>`;
  }

  function renderDocumentsPanel() {
    const docs = state.documents;
    if (!docs) return "";
    const entry = (item, titulo, descricao) => {
      if (!item) {
        return `<li class="report-doc-missing"><strong>${esc(titulo)}</strong><span>Ainda não disponível.</span></li>`;
      }
      return `
        <li>
          <div>
            <strong>${esc(titulo)}</strong>
            <span>${esc(descricao)}</span>
            <span class="report-doc-meta">${esc(kindLabel(item.kind))} · v${
              esc(item.version_number)
            } · ${esc(String(item.sha256).slice(0, 12))}…</span>
          </div>
          <button type="button" class="m15-btn m15-btn-sm"
            data-report-download="${esc(item.version_id)}">Baixar</button>
        </li>`;
    };
    return `
      <aside class="report-documents-panel" aria-labelledby="reportDocsTitle">
        <h4 id="reportDocsTitle">Documentos do exame</h4>
        <ul class="report-documents-list">
          ${entry(docs.tecnico_mir, "Exame técnico (MIR)",
            "PDF original do equipamento, sem qualquer alteração.")}
          ${entry(docs.laudo_soprolife, "Laudo médico SoproLife",
            "Documento próprio com a conclusão médica.")}
        </ul>
        ${docs.validation_code ? `
          <p class="report-validation-code">Código de verificação:
            <code>${esc(docs.validation_code)}</code></p>` : ""}
      </aside>`;
  }

  function renderSignaturePanel(detail) {
    const pending = detail.status === "assinatura_pendente";
    return `
      <aside class="report-signature-panel" aria-labelledby="signatureTitle">
        <h4 id="signatureTitle">Assinatura qualificada</h4>
        <p><strong>${pending ? "assinatura qualificada pendente" :
          "provedor não configurado"}</strong></p>
        <p>Nenhum documento deste fluxo é assinado ou liberado nesta versão.</p>
        <ul>
          <li>ICP-Brasil A1 — indisponível</li>
          <li>VIDaaS — pendente de seleção e integração</li>
          <li>BirdID — pendente de seleção e integração</li>
        </ul>
      </aside>`;
  }

  function renderPhysicianDetail() {
    const detail = state.detail;
    if (!detail) {
      return `
        <section class="report-panel report-detail-empty">
          <h3>Área clínica</h3>
          <p>Selecione um item de “Meus laudos”. A identidade do paciente aparece somente dentro do documento atribuído.</p>
        </section>`;
    }
    const original = versionByKind("original");
    const current = currentVersion();
    const editable = ["atribuido", "em_elaboracao"].includes(detail.status);
    const ready = detail.status === "em_elaboracao";
    const signed = detail.status === "assinado";
    const released = detail.status === "liberado";
    const native = latestNativeVersion();
    const hasAddendum = Boolean(native && native.kind === "laudo_adendo");
    const maxPages = original ? original.page_count : 1;
    return `
      <section class="report-panel report-clinical-panel" aria-labelledby="reportDetailHeading">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">${esc(detail.public_code)} · ${esc(detail.exam.public_code)}</p>
            <h3 id="reportDetailHeading" tabindex="-1">Documento atribuído</h3>
          </div>
          <div class="report-status-badges">
            <span class="report-status-chip report-${esc(detail.status)}">${
              esc(statusLabel(detail.status))
            }</span>
            ${released ? `<span class="report-status-chip report-liberado-flag">Liberado</span>` : ""}
            ${hasAddendum ? `<span class="report-status-chip report-adendo-flag">Com adendo</span>` : ""}
            ${detail.corrects_document_id
              ? `<span class="report-status-chip report-corrigido-flag">Documento corretivo</span>`
              : ""}
            ${detail.locked ? `<span class="report-status-chip report-locked-flag">Conteúdo bloqueado</span>` : ""}
          </div>
        </div>

        <div class="report-clinical-identity">
          <div><span>Paciente</span><strong>${esc(detail.patient.full_name)}</strong></div>
          <div><span>Código</span><strong>${esc(detail.patient.public_code)}</strong></div>
          <div><span>Nascimento</span><strong>${fmtDate(detail.patient.date_of_birth, false)}</strong></div>
          <div><span>Origem</span><strong>${esc(detail.origin_type)}${
            detail.origin_label ? ` · ${esc(detail.origin_label)}` : ""
          }</strong></div>
        </div>

        <div class="report-comparison" aria-label="Exame técnico da MIR e laudo da SoproLife">
          <article>
            <h4>Exame técnico (MIR)</h4>
            <p class="report-help">Documento original, nunca alterado nem assinado por cima.</p>
            ${renderPdfFrame("original", "PDF original", original)}
          </article>
          <article>
            <h4>${current && current.kind !== "original"
              ? kindLabel(current.kind) : "Laudo SoproLife"}</h4>
            <p class="report-help">Laudo médico próprio, gerado pelo Centro de Comando.</p>
            ${renderPdfFrame(
              "generated",
              "PDF gerado para comparação",
              current && current.kind !== "original" ? current : null
            )}
          </article>
        </div>

        ${renderDocumentsPanel()}
        ${renderNativeReportForm(detail)}
        ${renderReleaseAction(detail)}
        ${renderAddendumForm(detail)}

        ${editable ? `
          <details class="report-legacy-compose">
          <summary>Anotação técnica sobre o PDF da MIR (fluxo M24C)</summary>
          <p class="report-help">Fluxo anterior: compõe um bloco de texto sobre o próprio PDF do equipamento. O laudo médico da SoproLife acima é o documento oficial.</p>
          <form id="reportComposeForm" class="report-clinical-form">
            ${renderTemplateSelector()}
            <label for="reportInterpretation">
              Interpretação clínica
              <textarea id="reportInterpretation" name="interpretation_text"
                maxlength="8000" required aria-describedby="interpretationHelp">${
                  esc(state.interpretation)
                }</textarea>
              <span id="interpretationHelp" class="report-help">Texto autorado pelo médico. O sistema não calcula diagnóstico nem conclusão.</span>
            </label>
            <div class="report-placement-grid">
              <label for="reportPageNumber">
                Página de destino
                <input id="reportPageNumber" name="page_number" type="number"
                  min="1" max="${esc(maxPages)}" value="1" required>
              </label>
              <fieldset>
                <legend>Posição do bloco</legend>
                <label><input type="radio" name="placement" value="topo" checked> Topo</label>
                <label><input type="radio" name="placement" value="rodape"> Rodapé</label>
              </fieldset>
            </div>
            <button class="m15-btn" type="submit"${
              state.busy ? " disabled" : ""
            }>Gerar anotação sobre o PDF da MIR</button>
          </form>
          </details>` : ""}

        ${ready ? `
          <details class="report-legacy-compose">
          <summary>Preparar assinatura qualificada (ICP-Brasil, pendente)</summary>
          <div class="report-ready-action">
            <p>Congela os snapshots e deixa o documento aguardando um provedor de assinatura qualificada. Nenhum provedor está configurado nesta versão.</p>
            <button type="button" class="m15-btn" data-report-prepare-signature${
              state.busy ? " disabled" : ""
            }>Marcar conteúdo pronto para assinatura</button>
          </div>
          </details>` : ""}

        ${signed || released ? `
          <form id="reportCorrectionForm" class="report-correction-form">
            <label for="reportCorrectionReason">Motivo técnico da correção
              <select id="reportCorrectionReason" name="reason_code" required>
                <option value="clinical_correction">Correção clínica</option>
                <option value="identification_correction">Correção de identificação</option>
                <option value="technical_document_correction">Correção técnica do documento</option>
              </select>
            </label>
            <button class="m15-btn" type="submit">Abrir documento corretivo</button>
          </form>` : ""}
        ${renderSignaturePanel(detail)}
      </section>`;
  }

  function renderPhysicianWorkspace() {
    return `
      <div class="report-physician-shell">
        ${renderQueue()}
        ${renderPhysicianDetail()}
      </div>`;
  }

  function renderOperationalList() {
    const selected = selectedOperational();
    const rows = state.operational.length
      ? state.operational.map((item) => `
          <button type="button" class="report-operation-row${
            item.document_id === state.selectedOperationalId ? " is-selected" : ""
          }" data-report-operational="${esc(item.document_id)}">
            <strong>${esc(item.report_code)}</strong>
            <span>${esc(item.exam_code)}</span>
            <span>${esc(statusLabel(item.status))}</span>
          </button>`).join("")
      : `<div class="report-empty">Nenhum documento no fluxo.</div>`;
    return `
      <section class="report-panel" aria-labelledby="operationalListTitle">
        <h3 id="operationalListTitle">Acompanhamento operacional</h3>
        <p class="report-help">Somente códigos, origem, atribuição e estado técnico; a interpretação clínica não é exposta.</p>
        <div class="report-operation-list">${rows}</div>
        ${selected && selected.status === "atribuido" ? `
          <form id="reportReassignForm" class="report-reassign-form">
            <h4>Reatribuir antes do primeiro rascunho</h4>
            <input type="hidden" name="document_id" value="${esc(selected.document_id)}">
            <input type="hidden" name="expected_assignment_id" value="${esc(selected.assignment_id)}">
            <label for="reportReassignPhysician">Novo médico responsável
              <select id="reportReassignPhysician" name="physician_profile_id" required>
                <option value="">Selecione</option>
                ${state.physicians
                  .filter((item) => item.id !== selected.physician_profile_id)
                  .map((item) => `<option value="${esc(item.id)}">${
                    esc(item.professional_name)
                  } · CRM/${esc(item.crm_state)} ${esc(item.crm_number)}</option>`).join("")}
              </select>
            </label>
            <label for="reportReassignReason">Motivo técnico fechado
              <select id="reportReassignReason" name="reason_code" required>
                ${options(REASSIGN_REASONS, "")}
              </select>
            </label>
            <button class="m15-btn" type="submit">Confirmar reatribuição</button>
          </form>` : ""}
      </section>`;
  }

  function renderOperationalWorkspace() {
    const physicianOptions = state.physicians.map((profile) =>
      `<option value="${esc(profile.id)}">${esc(profile.professional_name)} · ` +
      `CRM/${esc(profile.crm_state)} ${esc(profile.crm_number)}</option>`
    ).join("");
    return `
      <div class="report-operational-shell">
        <section class="report-panel" aria-labelledby="uploadTitle">
          <p class="eyebrow">Recebimento e atribuição</p>
          <h3 id="uploadTitle">Novo PDF original</h3>
          <form id="reportLocateExamForm" class="report-inline-form">
            <label for="reportExamCode">Código institucional do exame
              <input id="reportExamCode" name="exam_code" autocomplete="off"
                placeholder="ESP-000001" pattern="ESP-[0-9]{1,9}" required>
            </label>
            <button class="m15-btn" type="submit">Localizar exame</button>
          </form>
          ${state.locatedExam ? `
            <div class="report-technical-confirmation" role="status">
              <strong>${esc(state.locatedExam.public_code)}</strong>
              <span>${fmtDate(state.locatedExam.data_exame, false)} · ${esc(state.locatedExam.status)}</span>
            </div>
            <form id="reportUploadForm" class="report-upload-form">
              <input type="hidden" name="exam_code" value="${esc(state.locatedExam.public_code)}">
              <label for="reportPhysician">Médico responsável
                <select id="reportPhysician" name="physician_profile_id" required>
                  <option value="">Selecione um perfil ativo e verificado</option>
                  ${physicianOptions}
                </select>
              </label>
              <label for="reportOriginType">Origem do exame
                <select id="reportOriginType" name="origin_type" required>
                  ${options(ORIGINS, "")}
                </select>
              </label>
              <label for="reportOriginLabel">Rótulo operacional seguro (opcional)
                <input id="reportOriginLabel" name="origin_label" maxlength="120"
                  aria-describedby="originHelp">
                <span id="originHelp" class="report-help">Não informe paciente, contato ou observação clínica.</span>
              </label>
              <label for="reportPartnerUnit">Referência técnica de unidade parceira (opcional)
                <input id="reportPartnerUnit" name="origin_partner_unit_id"
                  maxlength="36" autocomplete="off">
              </label>
              <label for="reportPdfFile">PDF original
                <input id="reportPdfFile" name="file" type="file"
                  accept="application/pdf,.pdf" required>
              </label>
              <button class="m15-btn m15-btn-primary" type="submit"${
                state.busy ? " disabled" : ""
              }>Enviar e atribuir</button>
            </form>` : `
            <p class="report-help">A atribuição só é criada depois da localização exata pelo código ESP.</p>`}
        </section>
        ${renderOperationalList()}
      </div>`;
  }

  function renderProfileAdmin() {
    const selected = selectedAdminAccount();
    const list = state.adminAccounts.map((item) => `
      <option value="${esc(item.user.id)}"${
        selected && selected.user.id === item.user.id ? " selected" : ""
      }>${esc(item.user.nome)} · ${esc(item.user.email)}</option>`).join("");
    const profile = selected && selected.profile;
    return `
      <section class="report-panel" aria-labelledby="physicianAdminTitle">
        <p class="eyebrow">Administração restrita</p>
        <h3 id="physicianAdminTitle">Contas médicas</h3>
        <p class="report-help">Selecione uma conta existente. Este espaço não cria usuários nem recebe senhas.</p>
        <label for="reportAdminUser">Usuário existente
          <select id="reportAdminUser">
            <option value="">Selecione</option>${list}
          </select>
        </label>
        ${selected ? `
          <form id="reportPhysicianAdminForm" class="report-admin-profile-form">
            <input type="hidden" name="user_id" value="${esc(selected.user.id)}">
            <label class="report-check">
              <input type="checkbox" name="grant_physician_role"${
                selected.has_explicit_physician_role ? " checked" : ""
              }> Conceder papel médico explícito
            </label>
            <label for="reportProfessionalName">Nome profissional completo
              <input id="reportProfessionalName" name="professional_name"
                maxlength="220" required value="${esc(profile && profile.professional_name)}">
            </label>
            <div class="report-admin-grid">
              <label for="reportCrmNumber">CRM
                <input id="reportCrmNumber" name="crm_number" maxlength="40"
                  inputmode="numeric" required value="${esc(profile && profile.crm_number)}">
              </label>
              <label for="reportCrmState">UF do CRM
                <select id="reportCrmState" name="crm_state" required>
                  <option value="">UF</option>
                  ${options(UF_VALUES.map((uf) => [uf, uf]), profile && profile.crm_state)}
                </select>
              </label>
              <label for="reportRqe">RQE (opcional)
                <input id="reportRqe" name="rqe" maxlength="30"
                  value="${esc(profile && profile.rqe)}">
              </label>
            </div>
            <label for="reportVerification">Verificação
              <select id="reportVerification" name="verification_status" required>
                ${options([
                  ["pending", "Pendente"],
                  ["verified", "Verificado"],
                  ["rejected", "Recusado"],
                ], profile && profile.verification_status || "pending")}
              </select>
            </label>
            <label class="report-check">
              <input type="checkbox" name="active"${
                profile && profile.active ? " checked" : ""
              }> Perfil ativo
            </label>
            <p class="report-verification-state">Estado atual: <strong>${
              esc(profile ? profile.verification_status : "perfil ainda não criado")
            }</strong>${profile && profile.verified_at
              ? ` · ${fmtDate(profile.verified_at, true)}` : ""}</p>
            <button class="m15-btn" type="submit">Salvar perfil médico</button>
          </form>` : ""}
      </section>`;
  }

  function renderTemplateAdmin() {
    const rows = state.adminTemplates.map((template) => `
      <button type="button" class="report-admin-template${
        template.id === state.selectedAdminTemplateId ? " is-selected" : ""
      }${template.status === "draft" ? " is-provisional" : ""}"
        data-report-admin-template="${esc(template.id)}">
        <span><abbr title="${esc(template.texto_tooltip || template.titulo)}">${
          esc(template.codigo)
        }</abbr> · v${esc(template.versao)}</span>
        <strong>${esc(template.titulo)}</strong>
        <span class="report-template-state">${
          template.clinically_approved ? "APROVADO" :
            "PROVISÓRIO — NÃO UTILIZAR EM PRODUÇÃO"
        }</span>
      </button>`).join("");
    const selected = selectedAdminTemplate();
    return `
      <section class="report-panel" aria-labelledby="templateAdminTitle">
        <p class="eyebrow">Catálogo versionado</p>
        <h3 id="templateAdminTitle">Templates clínicos</h3>
        <div class="report-admin-template-list">${rows}</div>
        ${selected ? `
          <form id="reportTemplateRevisionForm" class="report-template-revision-form">
            <h4>Nova revisão de ${esc(selected.codigo)}</h4>
            <p class="report-warning">A revisão anterior permanece imutável. Os seis textos atuais são placeholders provisórios.</p>
            <input type="hidden" name="template_id" value="${esc(selected.id)}">
            <label for="reportTemplateTitle">Rótulo
              <input id="reportTemplateTitle" name="titulo" maxlength="200"
                required value="${esc(selected.titulo)}">
            </label>
            <label for="reportTemplateTooltip">Tooltip
              <input id="reportTemplateTooltip" name="texto_tooltip" maxlength="240"
                value="${esc(selected.texto_tooltip)}">
            </label>
            <label for="reportTemplateBody">Texto da revisão
              <textarea id="reportTemplateBody" name="texto_completo"
                maxlength="8000" required>${esc(selected.texto_completo)}</textarea>
            </label>
            <label for="reportTemplateStatus">Status
              <select id="reportTemplateStatus" name="status">
                ${options([
                  ["draft", "Rascunho / provisório"],
                  ["approved", "Aprovado"],
                  ["retired", "Retirado"],
                ], selected.status)}
              </select>
            </label>
            <label class="report-check">
              <input type="checkbox" name="clinically_approved"${
                selected.clinically_approved ? " checked" : ""
              }> Aprovação clínica confirmada
            </label>
            <label class="report-check">
              <input type="checkbox" name="ativo"${
                selected.ativo ? " checked" : ""
              }> Revisão ativa
            </label>
            <button class="m15-btn" type="submit">Criar nova revisão</button>
          </form>` : ""}
      </section>`;
  }

  function renderAdminWorkspace() {
    return `
      <div class="report-admin-shell">
        ${renderProfileAdmin()}
        ${renderTemplateAdmin()}
      </div>`;
  }

  function render() {
    const mount = root();
    if (!mount) return;
    setPhysicianNavigationIsolation();
    if (!authenticated()) {
      mount.innerHTML = renderStatus() + renderUnauthenticated();
      return;
    }
    const blocks = [];
    if (explicit("medico")) blocks.push(renderPhysicianWorkspace());
    if (can("operacional")) blocks.push(renderOperationalWorkspace());
    if (can("admin")) blocks.push(renderAdminWorkspace());
    if (!blocks.length) {
      blocks.push(`<div class="report-state" role="alert">Esta conta não possui acesso ao fluxo de laudos.</div>`);
    }
    mount.innerHTML = `
      ${renderStatus()}
      ${renderPilotWarning()}
      <div class="report-session-strip">
        <span>Sessão: ${esc(currentUser() && currentUser().nome || "usuário autenticado")}</span>
        ${explicit("medico") ? `<span>Papel clínico explícito</span>` : ""}
        <button type="button" class="m15-btn" data-report-logout>Sair</button>
      </div>
      ${blocks.join("")}`;
  }

  async function loadAuthenticatedData() {
    const epoch = ++state.loadEpoch;
    if (!authenticated()) {
      render();
      return;
    }
    state.busy = true;
    render();
    try {
      const calls = [];
      const labels = [];
      if (explicit("medico")) {
        const suffix = state.statusFilter
          ? `?status=${encodeURIComponent(state.statusFilter)}` : "";
        calls.push(client().api(`/laudos/meus${suffix}`));
        labels.push("queue");
        calls.push(client().api("/laudos/templates?catalog=clinical"));
        labels.push("templates");
      }
      if (can("operacional")) {
        calls.push(client().api("/laudos"));
        labels.push("operational");
        calls.push(client().api("/laudos/medicos-disponiveis"));
        labels.push("physicians");
      }
      if (can("admin")) {
        calls.push(client().api("/laudos/admin/medicos"));
        labels.push("adminAccounts");
        calls.push(client().api("/laudos/templates?catalog=admin"));
        labels.push("adminTemplates");
      }
      const values = await Promise.all(calls);
      if (epoch !== state.loadEpoch) return;
      labels.forEach((label, index) => { state[label] = values[index]; });
      if (state.selectedDocumentId &&
          !state.queue.some((item) => item.document_id === state.selectedDocumentId)) {
        state.selectedDocumentId = "";
        state.detail = null;
        releasePdfUrls();
      }
      if (state.selectedOperationalId &&
          !state.operational.some((item) => item.document_id === state.selectedOperationalId)) {
        state.selectedOperationalId = "";
      }
      state.busy = false;
      render();
      if (state.selectedDocumentId) await loadDocument(state.selectedDocumentId, false);
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      announce(readableError(error), "erro");
      render();
    } finally {
      if (epoch === state.loadEpoch) {
        state.busy = false;
        render();
      }
    }
  }

  async function loadPdf(version, slot, epoch) {
    if (!version) return;
    try {
      const blob = await client().apiBlob(
        reportContentPath(state.selectedDocumentId, version.id, "inline")
      );
      if (epoch !== state.loadEpoch) return;
      if (state.pdfUrls[slot]) URL.revokeObjectURL(state.pdfUrls[slot]);
      state.pdfUrls[slot] = URL.createObjectURL(blob);
      render();
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      announce(readableError(error), "erro");
      render();
    }
  }

  async function loadDocument(documentId, focusHeading) {
    state.selectedDocumentId = documentId;
    state.detail = null;
    state.catalog = null;
    state.documents = null;
    state.confirmRelease = false;
    state.addendumText = "";
    releasePdfUrls();
    const epoch = ++state.loadEpoch;
    render();
    try {
      const detail = await client().api(`/laudos/${encodeURIComponent(documentId)}`);
      if (epoch !== state.loadEpoch) return;
      state.detail = detail;
      const current = Array.isArray(detail.versoes)
        ? detail.versoes.find((item) => item.id === detail.current_version_id) : null;
      state.interpretation = current && current.interpretation_text_snapshot || "";
      state.selectedTemplateId = current && current.template_id || "";
      // M25.2 — reidrata a escolha de catálogo da última versão nativa,
      // para que reabrir o documento não perca o trabalho em andamento.
      const native = latestNativeVersion();
      state.conclusionCode = native && native.conclusion_code_snapshot || "";
      state.customConclusion = state.conclusionCode === "PERSONALIZADO"
        && native ? native.conclusion_text_snapshot || "" : "";
      state.bronchodilatorCode =
        native && native.bronchodilator_code_snapshot || "";
      state.finalText = native && native.interpretation_text_snapshot || "";
      state.observations = native && native.observations_snapshot || "";
      state.previewVersionId =
        native && native.kind === "laudo_previa" ? native.id : "";
      state.previewTextSha256 =
        native && native.kind === "laudo_previa"
          ? native.interpretation_text_sha256 || "" : "";
      render();
      loadCatalog(documentId, epoch);
      loadDeliveryDocuments(documentId, epoch);
      if (focusHeading) {
        const heading = document.getElementById("reportDetailHeading");
        if (heading) heading.focus();
      }
      const original = Array.isArray(detail.versoes)
        ? detail.versoes.find((item) => item.kind === "original") : null;
      // Cada entrega gera auditoria mínima. Sequenciar evita duas gravações
      // concorrentes na mesma sessão/banco local e mantém os dois Blob URLs
      // estáveis para a comparação.
      await loadPdf(original, "original", epoch);
      await loadPdf(
        current && current.kind !== "original" ? current : null,
        "generated",
        epoch
      );
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      announce(readableError(error), "erro");
      render();
    }
  }

  // ------------------------------------------------------------- M25.2

  async function loadCatalog(documentId, epoch) {
    try {
      const catalog = await client().api(
        `/laudos/${encodeURIComponent(documentId)}/catalogo-conclusoes`
      );
      if (epoch !== state.loadEpoch) return;
      state.catalog = catalog;
      render();
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      state.catalog = null;
      render();
    }
  }

  async function loadDeliveryDocuments(documentId, epoch) {
    try {
      const documents = await client().api(
        `/laudos/${encodeURIComponent(documentId)}/documentos`
      );
      if (epoch !== state.loadEpoch) return;
      state.documents = documents;
      render();
    } catch (error) {
      if (epoch !== state.loadEpoch) return;
      state.documents = null;
      render();
    }
  }

  // Monta a MESMA sugestão determinística que o servidor montaria, apenas
  // para pré-preencher o editor. O texto que vale é sempre o que a médica
  // enviar, e o servidor revalida os códigos de qualquer forma.
  function suggestedConclusionText() {
    const catalog = state.catalog;
    if (!catalog || !state.conclusionCode) return "";
    const conclusion = catalog.conclusoes.find(
      (item) => item.codigo === state.conclusionCode
    );
    if (!conclusion) return "";
    const base = conclusion.personalizado
      ? state.customConclusion.trim() : conclusion.texto;
    const complement = catalog.complementos_bd.find(
      (item) => item.codigo === state.bronchodilatorCode
    );
    const extra = complement && complement.texto ? complement.texto : "";
    if (!base) return extra;
    return extra ? `${base}\n${extra}` : base;
  }

  function applyCatalogText() {
    const suggestion = suggestedConclusionText();
    const current = state.finalText.trim();
    // Só sobrescreve enquanto a médica não tiver escrito a própria redação.
    if (!current || current === (state.suggestedText || "").trim()) {
      state.finalText = suggestion;
    } else if (suggestion && suggestion !== current) {
      announce(
        "Conclusão atualizada. O texto que você editou foi preservado — ajuste-o se necessário.",
        ""
      );
    }
    state.suggestedText = suggestion;
  }

  function readNativeForm() {
    const form = document.getElementById("reportNativeForm");
    if (form) {
      if (form.elements.final_text) {
        state.finalText = form.elements.final_text.value;
      }
      if (form.elements.observations) {
        state.observations = form.elements.observations.value;
      }
      if (form.elements.conclusion_custom_text) {
        state.customConclusion = form.elements.conclusion_custom_text.value;
      }
    }
  }

  async function previewNativeReport() {
    readNativeForm();
    if (!state.conclusionCode) {
      announce("Selecione uma conclusão antes de gerar a prévia.", "erro");
      return;
    }
    const payload = {
      conclusion_code: state.conclusionCode,
      conclusion_custom_text: state.conclusionCode === "PERSONALIZADO"
        ? state.customConclusion : null,
      bronchodilator_code: state.bronchodilatorCode || null,
      // Texto vazio deixa o servidor montar a redação a partir do catálogo.
      final_text: state.finalText.trim() ? state.finalText : null,
      observations: state.observations.trim() ? state.observations : null,
    };
    state.busy = true;
    state.confirmRelease = false;
    announce("Gerando a prévia exata do laudo…", "");
    render();
    try {
      const result = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/laudo/previa`,
        { method: "POST", body: JSON.stringify(payload) }
      );
      state.finalText = result.final_text || "";
      state.previewVersionId = result.preview_version_id || "";
      state.previewTextSha256 = result.final_text_sha256 || "";
      announce("Prévia gerada. Confira o documento antes de assinar.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function releaseReport() {
    if (!state.previewVersionId || !state.previewTextSha256) {
      announce("Gere a prévia do laudo antes de assinar e liberar.", "erro");
      return;
    }
    state.busy = true;
    announce("Assinando e liberando o laudo…", "");
    render();
    try {
      const result = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/assinar-e-liberar`,
        {
          method: "POST",
          body: JSON.stringify({
            confirmacao: RELEASE_CONFIRMATION,
            expected_version_id: state.previewVersionId,
            expected_text_sha256: state.previewTextSha256,
          }),
        }
      );
      state.confirmRelease = false;
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      announce(
        `Laudo liberado. Código de verificação ${result.validation_code}.`,
        "ok"
      );
      await loadAuthenticatedData();
    } catch (error) {
      state.confirmRelease = false;
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function publishAddendum(form) {
    state.addendumText = form.elements.body_text.value;
    state.busy = true;
    announce("Publicando adendo sem alterar a versão anterior…", "");
    render();
    try {
      await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/adendo`,
        {
          method: "POST",
          body: JSON.stringify({
            body_text: state.addendumText,
            confirmacao: ADDENDUM_CONFIRMATION,
          }),
        }
      );
      state.addendumText = "";
      announce("Adendo publicado; a versão liberada anterior foi preservada.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function downloadVersion(versionId) {
    try {
      const blob = await client().apiBlob(
        reportContentPath(state.selectedDocumentId, versionId, "download")
      );
      // URL de objeto temporária, revogada logo após o disparo: nenhum
      // documento fica acessível por endereço persistente.
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
      announce(readableError(error), "erro");
    }
  }

  async function locateExam(code) {
    const normalized = String(code || "").trim().toUpperCase();
    if (!EXAM_CODE_RE.test(normalized)) {
      announce("Informe um código institucional no formato ESP-000001.", "erro");
      return;
    }
    state.busy = true;
    announce("Localizando exame…", "");
    try {
      const response = await client().api(
        `/espirometrias?public_code=${encodeURIComponent(normalized)}`
      );
      const items = Array.isArray(response) ? response : response.itens || [];
      state.locatedExam = items.find((item) => item.public_code === normalized) || null;
      if (!state.locatedExam) throw new Error("Exame não encontrado pelo código informado.");
      announce("Exame localizado. Complete a atribuição técnica.", "ok");
    } catch (error) {
      state.locatedExam = null;
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
      const physician = document.getElementById("reportPhysician");
      if (physician) physician.focus();
    }
  }

  async function uploadOriginal(form) {
    const file = form.elements.file.files[0];
    if (!file) {
      announce("Selecione um PDF original.", "erro");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      announce("O PDF excede o limite de 25 MiB.", "erro");
      return;
    }
    const payload = new FormData();
    [
      "exam_code", "physician_profile_id", "origin_type",
      "origin_label", "origin_partner_unit_id",
    ].forEach((name) => payload.append(name, form.elements[name].value || ""));
    payload.append("file", file);
    state.busy = true;
    announce("Validando, armazenando e atribuindo o PDF…", "");
    render();
    try {
      const result = await client().api("/laudos", {
        method: "POST", body: payload,
      });
      state.locatedExam = null;
      announce(`${result.public_code} recebido e atribuído com segurança.`, "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function compose(form) {
    state.interpretation = form.elements.interpretation_text.value;
    const payload = {
      template_id: form.elements.template_id.value || null,
      interpretation_text: state.interpretation,
      page_number: Number(form.elements.page_number.value),
      placement: form.elements.placement.value,
    };
    state.busy = true;
    announce("Gerando e revalidando a prévia clínica…", "");
    render();
    try {
      await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/compor`,
        { method: "POST", body: JSON.stringify(payload) }
      );
      announce("Prévia criada. Compare os dois PDFs antes de continuar.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function prepareSignature() {
    state.busy = true;
    announce("Congelando evidências e preparando assinatura…", "");
    render();
    try {
      await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/preparar-assinatura`,
        { method: "POST" }
      );
      announce("Conteúdo pronto. Assinatura qualificada permanece pendente.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function reassign(form) {
    const documentId = form.elements.document_id.value;
    const payload = {
      physician_profile_id: form.elements.physician_profile_id.value,
      expected_assignment_id: form.elements.expected_assignment_id.value,
      reason_code: form.elements.reason_code.value,
    };
    state.busy = true;
    announce("Aplicando reatribuição auditada…", "");
    try {
      await client().api(`/laudos/${encodeURIComponent(documentId)}/reatribuir`, {
        method: "POST", body: JSON.stringify(payload),
      });
      announce("Reatribuição concluída antes do primeiro rascunho.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function savePhysician(form) {
    const payload = {
      grant_physician_role: form.elements.grant_physician_role.checked,
      professional_name: form.elements.professional_name.value,
      crm_number: form.elements.crm_number.value,
      crm_state: form.elements.crm_state.value,
      rqe: form.elements.rqe.value || null,
      verification_status: form.elements.verification_status.value,
      active: form.elements.active.checked,
    };
    state.busy = true;
    announce("Validando perfil e papel explícito…", "");
    try {
      await client().api(
        `/laudos/admin/medicos/${encodeURIComponent(form.elements.user_id.value)}`,
        { method: "PATCH", body: JSON.stringify(payload) }
      );
      announce("Perfil médico atualizado.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function saveTemplateRevision(form) {
    const payload = {
      titulo: form.elements.titulo.value,
      texto_tooltip: form.elements.texto_tooltip.value || null,
      texto_completo: form.elements.texto_completo.value,
      status: form.elements.status.value,
      clinically_approved: form.elements.clinically_approved.checked,
      ativo: form.elements.ativo.checked,
    };
    state.busy = true;
    announce("Criando revisão imutável do template…", "");
    try {
      const revision = await client().api(
        `/laudos/templates/${encodeURIComponent(form.elements.template_id.value)}`,
        { method: "PATCH", body: JSON.stringify(payload) }
      );
      state.selectedAdminTemplateId = revision.id;
      announce(`Revisão v${revision.versao} criada sem reescrever a anterior.`, "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  async function openCorrection(form) {
    state.busy = true;
    announce("Abrindo documento corretivo separado…", "");
    try {
      const result = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}/nova-versao-corretiva`,
        {
          method: "POST",
          body: JSON.stringify({ reason_code: form.elements.reason_code.value }),
        }
      );
      state.selectedDocumentId = result.id;
      announce("Documento corretivo aberto; o predecessor assinado não foi alterado.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
      render();
    } finally {
      state.busy = false;
    }
  }

  function handleClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches("[data-report-open]")) {
      loadDocument(button.getAttribute("data-report-open"), true);
      return;
    }
    if (button.matches("[data-report-operational]")) {
      state.selectedOperationalId = button.getAttribute("data-report-operational");
      render();
      const form = document.getElementById("reportReassignForm");
      if (form) form.querySelector("select").focus();
      return;
    }
    if (button.matches("[data-report-admin-template]")) {
      state.selectedAdminTemplateId = button.getAttribute("data-report-admin-template");
      render();
      const heading = document.querySelector("#reportTemplateRevisionForm h4");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus();
      }
      return;
    }
    if (button.matches("[data-report-prepare-signature]")) {
      prepareSignature();
      return;
    }
    // ---------------------------------------------------------- M25.2
    if (button.matches("[data-report-conclusion]")) {
      readNativeForm();
      const code = button.getAttribute("data-report-conclusion");
      state.conclusionCode = state.conclusionCode === code ? "" : code;
      // Trocar a conclusão invalida a prévia já conferida.
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      state.confirmRelease = false;
      applyCatalogText();
      render();
      return;
    }
    if (button.matches("[data-report-bd]")) {
      readNativeForm();
      const code = button.getAttribute("data-report-bd");
      state.bronchodilatorCode =
        state.bronchodilatorCode === code ? "" : code;
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      state.confirmRelease = false;
      applyCatalogText();
      render();
      return;
    }
    if (button.matches("[data-report-release-open]")) {
      readNativeForm();
      state.confirmRelease = true;
      render();
      const heading = document.getElementById("reportReleaseConfirmTitle");
      if (heading) heading.focus();
      return;
    }
    if (button.matches("[data-report-release-cancel]")) {
      state.confirmRelease = false;
      render();
      return;
    }
    if (button.matches("[data-report-release-confirm]")) {
      releaseReport();
      return;
    }
    if (button.matches("[data-report-download]")) {
      downloadVersion(button.getAttribute("data-report-download"));
      return;
    }
    if (button.matches("[data-report-logout]")) {
      releasePdfUrls();
      client().logout();
    }
  }

  function handleChange(event) {
    if (event.target.id === "reportStatusFilter") {
      state.statusFilter = event.target.value;
      loadAuthenticatedData();
      return;
    }
    if (event.target.id === "reportAdminUser") {
      state.selectedAdminUserId = event.target.value;
      render();
      const target = document.getElementById("reportProfessionalName");
      if (target) target.focus();
      return;
    }
    if (event.target.matches('input[name="template_id"]')) {
      state.selectedTemplateId = event.target.value;
      const template = state.templates.find(
        (item) => item.id === state.selectedTemplateId
      );
      if (template) state.interpretation = template.texto_completo;
      else state.interpretation = "";
      render();
      const editor = document.getElementById("reportInterpretation");
      if (editor) {
        editor.focus();
        editor.setSelectionRange(editor.value.length, editor.value.length);
      }
    }
  }

  function handleInput(event) {
    if (event.target.id === "reportInterpretation") {
      state.interpretation = event.target.value;
      return;
    }
    // M25.2 — editar qualquer campo do laudo invalida a prévia conferida:
    // a liberação exige o hash exato do conteúdo que foi visto.
    if (event.target.id === "reportFinalText") {
      state.finalText = event.target.value;
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      return;
    }
    if (event.target.id === "reportObservations") {
      state.observations = event.target.value;
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      return;
    }
    if (event.target.id === "reportCustomConclusion") {
      state.customConclusion = event.target.value;
      state.previewVersionId = "";
      state.previewTextSha256 = "";
      return;
    }
    if (event.target.id === "reportAddendumText") {
      state.addendumText = event.target.value;
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (event.target.id === "reportLocateExamForm") {
      locateExam(event.target.elements.exam_code.value);
    } else if (event.target.id === "reportUploadForm") {
      uploadOriginal(event.target);
    } else if (event.target.id === "reportComposeForm") {
      compose(event.target);
    } else if (event.target.id === "reportNativeForm") {
      previewNativeReport();
    } else if (event.target.id === "reportAddendumForm") {
      publishAddendum(event.target);
    } else if (event.target.id === "reportReassignForm") {
      reassign(event.target);
    } else if (event.target.id === "reportPhysicianAdminForm") {
      savePhysician(event.target);
    } else if (event.target.id === "reportTemplateRevisionForm") {
      saveTemplateRevision(event.target);
    } else if (event.target.id === "reportCorrectionForm") {
      openCorrection(event.target);
    }
  }

  function bindClient() {
    const c = client();
    if (!c) {
      window.setTimeout(bindClient, 200);
      return;
    }
    c.onSessionChange(() => {
      if (!authenticated()) {
        state.loadEpoch += 1;
        state.detail = null;
        releasePdfUrls();
        setPhysicianNavigationIsolation();
        render();
        return;
      }
      loadAuthenticatedData();
    });
    setPhysicianNavigationIsolation();
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
    if (
      config.enabled !== true
      || !reportsFeatureEnabled(config)
      || config.api_base !== "/painel-soprolife/api/m15"
    ) {
      return;
    }
    // M24D — o aviso PILOTO INTERNO só aparece quando o backend está
    // realmente em modo piloto; qualquer outro valor mantém o padrão
    // seguro "disabled" (sem aviso, sem capacidade extra).
    state.reportsMode = config.reports_mode === "pilot" ? "pilot" : "disabled";
    document.querySelectorAll("[data-report-entry]").forEach((entry) => {
      entry.hidden = false;
    });
    mount.addEventListener("click", handleClick);
    mount.addEventListener("change", handleChange);
    mount.addEventListener("input", handleInput);
    mount.addEventListener("submit", handleSubmit);
    const nav = document.querySelector(
      `.nav-item[data-section="${SECTION_ID}"]`
    );
    if (nav) nav.addEventListener("click", loadAuthenticatedData);
    document.addEventListener("click", (event) => {
      const other = event.target.closest
        && event.target.closest(".nav-item[data-section]");
      if (other && other.getAttribute("data-section") !== SECTION_ID) {
        releasePdfUrls();
      }
    });
    window.addEventListener("beforeunload", releasePdfUrls);
    bindClient();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
