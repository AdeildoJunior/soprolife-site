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
  // M25.18 — a faixa "PILOTO INTERNO — DOCUMENTO NÃO ASSINADO — NÃO LIBERAR
  // AO PACIENTE" saiu.
  //
  // Ela descrevia um protótipo; o fluxo virou operação real, com paciente
  // real e assinatura qualificada aplicada FORA do sistema. Manter um alarme
  // vermelho permanente no topo de uma tela usada todo dia não informa mais
  // nada — vira ruído que se aprende a ignorar, inclusive quando um aviso de
  // verdade aparecer no mesmo lugar.
  //
  // O que substitui NÃO é silêncio: o estado do documento passou a dizer
  // exatamente o que falta ("Concluído — aguardando assinatura qualificada"),
  // no lugar onde a informação é usada.
  const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
  // M25.12 — o código institucional de espirometria é SEMPRE emitido por
  // `app/ids.py` como `ESP-` + 6 dígitos zero-preenchidos. Nenhum caminho do
  // sistema produz letras depois do hífen. A expressão continua a mesma; o
  // que mudou é que a recusa agora é explicada na tela em vez de bloquear o
  // envio silenciosamente (ver `renderExamLocator`).
  const EXAM_CODE_RE = /^ESP-\d{1,9}$/i;
  const EXAM_CODE_HINT = "ESP- seguido só de números, por exemplo ESP-000001.";
  // M25.15 — o localizador passou a aceitar também o código do LAUDO. Antes
  // digitar um LAU- era um beco sem saída ("formato não reconhecido"), ainda
  // que seja o código que aparece em quase toda conversa sobre um laudo.
  const REPORT_CODE_RE = /^LAU-\d{1,9}$/i;
  const EXAM_SEARCH_HINT =
    "Nome do paciente, código do exame (ESP-000001) ou do laudo (LAU-000001).";
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
    signatureAsset: null,
    templates: [],
    adminAccounts: [],
    // M25.19 — a lista administrativa de templates e a seleção dela saíram
    // do estado. O catálogo continua íntegro no servidor; o que saiu foi a
    // vitrine dele nesta tela.
    selectedDocumentId: "",
    selectedOperationalId: "",
    selectedAdminUserId: "",
    detail: null,
    locatedExam: null,
    // M25.12 — resultado da última busca por código institucional, FIXO na
    // tela. O toast sumia em segundos e a leitura natural de quem digitava um
    // código inexistente era "o sistema não fez nada".
    // { tipo: "erro"|"aviso"|"ok", titulo, mensagem, detalhe }
    locateFeedback: null,
    // M25.15 — candidatos quando a busca por nome casa com mais de um exame.
    // Vazio significa "sem ambiguidade pendente", nunca "nada encontrado".
    locateMatches: [],
    // Espirometrias recentes para escolher sem digitar código nenhum.
    recentExams: [],
    interpretation: "",
    selectedTemplateId: "",
    statusFilter: "",
    // M25.6 — unidade escolhida antes de ver a fila. `null` significa "ainda
    // não escolheu"; string vazia significa "todas as unidades", que é uma
    // escolha deliberada da médica e não o mesmo que não ter escolhido.
    unitFilter: null,
    // M25.7 — assinatura qualificada. `qualifiedDiagnostics` é o que a API
    // diz sobre a integração (só booleanos); `qualifiedRequest` é a
    // solicitação em andamento, quando existe.
    qualifiedDiagnostics: null,
    qualifiedRequest: null,
    confirmQualified: false,
    qualifiedTimer: null,
    // M25.8 — lote de assinatura externa. `batchSelection` guarda os ids
    // marcados; `batchResults` é o resultado por arquivo do último envio.
    batchSelection: [],
    batchResults: null,
    batchBusy: false,
    // ----------------------------------------------------------- M25.20
    // Central de assinatura externa. A médica lauda um a um; aqui ela
    // resolve em lote o que vem DEPOIS de concluir.
    //
    // `signaturePending` é a lista que o servidor diz estar aguardando
    // assinatura — nunca uma lista montada no navegador. `signatureSelection`
    // são os ids marcados. `signatureReview` é a conferência devolvida pelo
    // upload, que fica na tela até a médica confirmar ou descartar.
    signaturePending: [],
    signatureSelection: [],
    signatureReview: null,
    signatureBusy: false,
    // Fila de entrega da administração e o estado filtrado na tela.
    deliveryQueue: null,
    deliveryFilter: "",
    profileError: "",
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

  // M25.4 — a fila mostrava o código técnico cru ("clinica_parceira ·
  // pastore-ipanema"). O rótulo operacional interno não diz nada à médica e
  // o local completo já aparece ao abrir o documento.
  function originLabel(value) {
    const found = ORIGINS.find((item) => item[0] === value);
    return found ? found[1] : (value || "origem não registrada");
  }

  function statusLabel(value) {
    return {
      atribuido: "Pendente de laudo",
      em_elaboracao: "Em elaboração clínica",
      // M25.8 — revisado pela médica, congelado, AINDA NÃO assinado. Este
      // estado não vai ao paciente.
      assinatura_pendente: "Laudado — aguardando assinatura qualificada",
      assinado: "Assinado com ICP-Brasil",
      // M25.18 — "Liberado" sugeria documento pronto para entrega. Ele não
      // está: falta a assinatura qualificada, que a médica aplica fora do
      // sistema. O rótulo diz o estado real e o que falta, na ordem em que
      // importa para quem lê a fila.
      liberado: "Concluído — aguardando assinatura qualificada",
    }[value] || value || "—";
  }

  // ------------------------------------------------ identidade (M25.15)
  //
  // A partir da M25.15 a referência humana das telas AUTENTICADAS de laudo é
  // o NOME do paciente; os códigos continuam sempre visíveis, mas como
  // metadado de rastreabilidade, não como aquilo que a operação precisa
  // reconhecer. Estes três helpers existem para que todas as listas usem a
  // MESMA hierarquia: nome forte, contexto no meio, códigos discretos.
  //
  // Nada disto é usado em superfície pública: a rota de validação não
  // devolve `patient`, e por isso `patientName` cai num rótulo neutro em vez
  // de inventar um nome.

  function patientName(item) {
    const patient = item && item.patient;
    const nome = patient && patient.full_name;
    return nome ? String(nome) : "Paciente não identificado";
  }

  // Códigos de rastreabilidade em uma linha só. Sem laudo criado, mostra
  // apenas o ESP — nunca um "LAU-—" que pareceria um código existente.
  function codeTrail(item) {
    const codigos = [item.exam_code, item.report_code].filter(Boolean);
    return codigos.length
      ? `<span class="report-code-trail">${
          codigos.map((c) => esc(c)).join(" · ")
        }</span>`
      : "";
  }

  // Linha de contexto que separa homônimos sem expor dado pessoal extra:
  // exame, data e unidade já viajam em todas as filas.
  function contextLine(item, extras) {
    return [
      "Espirometria",
      fmtDate(item.exam_date, false),
      item.location_name || null,
    ].concat(extras || [])
      .filter(Boolean)
      .map((parte) => esc(parte))
      .join(" • ");
  }

  function kindLabel(value) {
    return {
      original: "Exame técnico (MIR)",
      rascunho: "Prévia clínica",
      assinatura_pendente: "Preparado para assinatura",
      assinado: "Versão assinada",
      laudo_previa: "Prévia do laudo",
      laudo_liberado: "Laudo concluído",
      laudo_adendo: "Laudo com adendo",
      // M25.20 — o arquivo que voltou assinado por fora. O rótulo diz
      // "recebido", e não "validado": a cadeia ICP-Brasil não foi conferida
      // por este sistema.
      laudo_assinado_externo_recebido: "Assinado recebido — validação pendente",
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
      // M25.18 — `pdfUrls` passou a guardar object URL (token da CLI) OU um
      // endereço da própria API (sessão por cookie). Revogar só faz sentido
      // para o primeiro; chamar `revokeObjectURL` num caminho comum é
      // silenciosamente inócuo, mas testar deixa claro que são dois casos.
      const valor = state.pdfUrls[key];
      if (valor && valor.startsWith("blob:")) URL.revokeObjectURL(valor);
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

  function renderStatus() {
    return `<div id="reportStatus" class="report-status${
      state.noticeKind ? ` report-status-${esc(state.noticeKind)}` : ""
    }" role="status" aria-live="polite"${state.notice ? "" : " hidden"}>${
      esc(state.notice)
    }</div>`;
  }

  // M25.18 — sem faixa. O estado real do laudo aparece no chip de status de
  // cada documento, que é onde ele muda e onde é consultado.
  function renderPilotWarning() {
    return "";
  }

  function renderUnauthenticated() {
    return `
      <div class="report-state" role="status">
        Entre pelo Núcleo administrativo para acessar o fluxo protegido.
      </div>`;
  }

  // M25.6 — a médica lauda exames de lugares diferentes (Pastore Ipanema,
  // atendimento SoproLife, empresa…). Misturar tudo numa lista só obriga a
  // ler o local de cada linha para saber onde está pisando.
  function queueUnits() {
    const porChave = new Map();
    state.queue.forEach((item) => {
      const chave = item.location_key
        || `origem:${item.origin_type || "outro"}`;
      const atual = porChave.get(chave) || {
        chave,
        // O backend resolve o nome pela MESMA função que imprime o local no
        // laudo. Só caímos no rótulo de origem se a rota não mandou nada.
        nome: item.location_name || originLabel(item.origin_type),
        total: 0,
        pendentes: 0,
      };
      atual.total += 1;
      if (item.status !== "liberado") atual.pendentes += 1;
      porChave.set(chave, atual);
    });
    return Array.from(porChave.values())
      .sort((a, b) => a.nome.localeCompare(b.nome, "pt-BR"));
  }

  function visibleQueue() {
    if (!state.unitFilter) return state.queue;
    return state.queue.filter((item) => (
      (item.location_key || `origem:${item.origin_type || "outro"}`)
        === state.unitFilter
    ));
  }

  function renderUnitChooser(unidades) {
    const cartoes = unidades.map((unidade) => `
      <button type="button" class="report-unit-card"
        data-report-unit="${esc(unidade.chave)}">
        <strong>${esc(unidade.nome)}</strong>
        <span>${unidade.pendentes} ${
          unidade.pendentes === 1 ? "exame aguardando" : "exames aguardando"
        }</span>
        <span class="report-unit-total">${unidade.total} no total</span>
      </button>`).join("");
    return `
      <section class="report-panel report-unit-panel"
        aria-labelledby="reportUnitTitle">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">Passo 1 de 2</p>
            <h3 id="reportUnitTitle">De qual unidade você vai laudar?</h3>
          </div>
        </div>
        <div class="report-unit-grid">
          ${cartoes}
          <button type="button" class="report-unit-card is-all"
            data-report-unit="__todas">
            <strong>Todas as unidades</strong>
            <span>${state.queue.length} ${
              state.queue.length === 1 ? "laudo" : "laudos"
            }</span>
          </button>
        </div>
      </section>`;
  }

  // M25.8 — barra de lote: só aparece quando há laudos revisados esperando
  // assinatura, porque fora disso ela não teria o que fazer.
  function renderBatchBar(lista) {
    const aguardando = lista.filter(
      (item) => item.status === "assinatura_pendente"
    );
    if (!aguardando.length && !state.batchResults) return "";
    const marcados = state.batchSelection.length;
    const resultados = state.batchResults;
    return `
      <div class="report-batch">
        <div class="report-batch-head">
          <strong>${aguardando.length} laudo(s) aguardando assinatura</strong>
          <span class="report-batch-count">${
            marcados ? `${marcados} selecionado(s)` : "nenhum selecionado"
          }</span>
        </div>
        <div class="report-batch-actions">
          <button type="button" class="m15-btn" data-report-batch-all>
            Selecionar todos
          </button>
          <button type="button" class="m15-btn" data-report-batch-none>
            Limpar seleção
          </button>
          <button type="button" class="m15-btn m15-btn-primary"
            data-report-batch-download${state.batchBusy ? " disabled" : ""}>
            ${marcados
              ? `Baixar ${marcados} selecionado(s) para assinatura`
              : "Baixar todos os laudos revisados"}
          </button>
          <label class="report-batch-upload">
            <span>Enviar laudos assinados</span>
            <input type="file" id="reportBatchUpload" multiple
              accept="application/pdf,.pdf,.zip">
          </label>
        </div>
        <p class="report-help">
          Assine os PDFs com o seu certificado no VIDaaS e devolva aqui.
          Pode enviar vários de uma vez, ou um ZIP com todos.
          Não foi confirmado que o VIDaaS assina vários numa sessão só —
          considere assinar arquivo por arquivo.
        </p>
        ${resultados ? renderBatchResults(resultados) : ""}
      </div>`;
  }

  function renderBatchResults(resultados) {
    const linhas = resultados.arquivos.map((item) => `
      <li class="report-batch-result${item.ok ? " is-ok" : " is-error"}">
        <strong>${esc(item.arquivo)}</strong>
        <span>${esc(batchOutcomeLabel(item.resultado))}</span>
        ${item.mensagem ? `<em>${esc(item.mensagem)}</em>` : ""}
      </li>`).join("");
    return `
      <div class="report-batch-results" role="status" aria-live="polite">
        <p><strong>${resultados.resumo.validados}</strong> validado(s) e
          <strong>${resultados.resumo.com_erro}</strong> com erro, de
          ${resultados.resumo.total} arquivo(s).</p>
        <ul>${linhas}</ul>
      </div>`;
  }

  function batchOutcomeLabel(valor) {
    return {
      validado_e_liberado: "Assinatura ICP-Brasil validada",
      arquivo_duplicado: "Já processado",
      laudo_nao_encontrado: "Laudo não encontrado",
      versao_divergente: "Versão divergente",
      assinatura_ausente: "Sem assinatura digital",
      assinatura_invalida: "Assinatura inválida",
      certificado_de_outra_pessoa: "Certificado de outra pessoa",
      documento_alterado: "Documento alterado",
      falha_tecnica_recuperavel: "Falha técnica — tente de novo",
    }[valor] || valor;
  }

  function renderQueue() {
    const unidades = queueUnits();
    // Com uma única unidade, obrigar a escolher seria um clique sem decisão.
    if (state.unitFilter === null && unidades.length > 1) {
      return renderUnitChooser(unidades);
    }
    const lista = visibleQueue();
    const unidadeAtual = unidades.find((u) => u.chave === state.unitFilter);
    const items = lista.length
      ? lista.map((item) => `
          <button type="button" class="report-queue-item${
            item.document_id === state.selectedDocumentId ? " is-selected" : ""
          }" data-report-open="${esc(item.document_id)}"
            role="option" aria-selected="${
              item.document_id === state.selectedDocumentId ? "true" : "false"
            }">
            ${item.status === "assinatura_pendente"
              ? `<span class="report-queue-pick${
                  state.batchSelection.includes(item.document_id)
                    ? " is-picked" : ""
                }" data-report-batch-pick="${esc(item.document_id)}"
                  role="checkbox" aria-checked="${
                    state.batchSelection.includes(item.document_id)
                  }" title="Selecionar para assinatura"></span>` : ""}
            ${/* M25.15 — a fila era LAU-000045 / ESP-000016 / origem. A
                  médica reconhece a paciente, não o código: o nome sobe para
                  o topo e os códigos descem para a última linha, sem sumir. */""}
            <strong class="report-item-name">${esc(patientName(item))}</strong>
            <span>${contextLine(item)}</span>
            <span class="report-status-chip report-${esc(item.status)}">${
              esc(statusLabel(item.status))
            }</span>
            ${codeTrail(item)}
            ${item.is_corrective
              ? `<span class="report-queue-flag">corrigido</span>` : ""}
            ${item.locked && item.status !== "liberado"
              ? `<span class="report-queue-flag is-locked">concluído</span>` : ""}
          </button>`).join("")
      : `<div class="report-empty">Nenhum laudo atribuído neste filtro.</div>`;
    const trocar = unidades.length > 1
      ? `<button type="button" class="report-unit-switch"
          data-report-unit-reset="1">Trocar unidade</button>`
      : "";
    return `
      <section class="report-panel report-queue-panel" aria-labelledby="myReportsTitle">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">${
              unidadeAtual ? esc(unidadeAtual.nome) : "Fila clínica restrita"
            }</p>
            <h3 id="myReportsTitle">Meus laudos</h3>
          </div>
          ${trocar}
          <label class="report-compact-field" for="reportStatusFilter">
            Status
            <select id="reportStatusFilter">
              ${options([
                ["", "Todos"],
                ["atribuido", "Pendentes de laudo"],
                ["em_elaboracao", "Em elaboração"],
                ["assinatura_pendente", "Laudados — aguardando assinatura"],
                ["assinado", "Assinados com ICP-Brasil"],
                ["liberado", "Concluídos"],
              ], state.statusFilter)}
            </select>
          </label>
        </div>
        ${renderBatchBar(lista)}
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
          ${/* M25.18 — a ação não assina nada. A assinatura qualificada
                acontece fora da SoproLife, com o certificado da médica. O
                que este passo faz é congelar o conteúdo e produzir o PDF
                que ela vai levar para assinar. */""}
          <h4 id="reportReleaseConfirmTitle" tabindex="-1">Confirmar conclusão do laudo</h4>
          <p>Você vai <strong>concluir</strong> este laudo com a sua identificação profissional. O conteúdo será congelado e o PDF ficará disponível para assinatura digital qualificada externa.</p>
          <ul>
            <li>Confira a prévia exibida acima — é exatamente o PDF final.</li>
            <li>Depois de concluído, correções só entram como <strong>adendo</strong> ou <strong>versão corretiva</strong>; a versão anterior é preservada.</li>
            <li>A conclusão é registrada com seu usuário, data e hora.</li>
            <li>A assinatura qualificada é aplicada por você, fora do sistema, no arquivo baixado.</li>
          </ul>
          <div class="report-release-buttons">
            <button type="button" class="m15-btn m15-btn-danger"
              data-report-release-confirm${state.busy ? " disabled" : ""}>
              Sim, concluir laudo
            </button>
            <button type="button" class="m15-btn" data-report-release-cancel>
              Cancelar
            </button>
          </div>
        </div>`;
    }
    return `
      <div class="report-release-action">
        <p>Confira a prévia. O conteúdo só é congelado após a sua confirmação consciente.</p>
        <button type="button" class="m15-btn m15-btn-primary report-release-cta"
          data-report-release-open${state.busy ? " disabled" : ""}>
          Concluir laudo
        </button>
        ${renderQualifiedAction(detail)}
      </div>`;
  }

  // M25.7 — assinatura QUALIFICADA ICP-Brasil pelo VIDaaS. Fica ao lado da
  // liberação institucional, nunca no lugar dela: são coisas diferentes e a
  // tela precisa deixar isso óbvio para a médica.
  const QUALIFIED_LABELS = {
    rascunho: "Preparando o documento…",
    aguardando_autenticacao: "Abrindo o VIDaaS…",
    aguardando_autorizacao: "Aguardando sua autorização no app VIDaaS",
    assinatura_recebida: "Autorização recebida — obtendo a assinatura…",
    validando: "Validando a assinatura…",
    assinado_liberado: "Assinado com certificado ICP-Brasil",
    recusado: "Assinatura recusada ou cancelada",
    expirado: "A janela de autorização expirou",
    falha_recuperavel: "Não deu certo desta vez",
    falha_definitiva: "Não foi possível assinar com o VIDaaS",
  };

  const QUALIFIED_WAITING = [
    "rascunho", "aguardando_autenticacao", "aguardando_autorizacao",
    "assinatura_recebida", "validando",
  ];

  function renderQualifiedAction(detail) {
    // A disponibilidade vem do próprio detalhe do laudo: o diagnóstico
    // completo é admin-only e a médica não precisa dele para trabalhar.
    const diag = detail.assinatura_qualificada_disponivel === undefined
      ? state.qualifiedDiagnostics
      : { integracao_pronta: detail.assinatura_qualificada_disponivel };
    const pedido = state.qualifiedRequest;

    if (pedido) {
      const esperando = QUALIFIED_WAITING.includes(pedido.status);
      const rotulo = QUALIFIED_LABELS[pedido.status] || pedido.status;
      return `
        <div class="report-qualified" role="status" aria-live="polite">
          <p class="report-qualified-status${esperando ? " is-waiting" : ""}">
            ${esc(rotulo)}
          </p>
          ${esperando
            ? `<p class="report-help">Abra o aplicativo VIDaaS no seu celular e
                autorize a assinatura. Esta tela se atualiza sozinha.</p>`
            : ""}
          ${pedido.erro && !esperando
            ? `<p class="report-qualified-erro">${esc(pedido.erro)}</p>` : ""}
          <div class="report-release-buttons">
            ${pedido.pode_tentar_novamente
              ? `<button type="button" class="m15-btn m15-btn-primary"
                  data-report-qualified-retry>Tentar novamente</button>` : ""}
            ${esperando
              ? `<button type="button" class="m15-btn"
                  data-report-qualified-cancel>Cancelar assinatura</button>` : ""}
          </div>
        </div>`;
    }

    if (!diag) return "";
    if (!diag.integracao_pronta) {
      return `
        <div class="report-qualified is-unavailable">
          <p class="report-qualified-status">Integração aguardando credencial da Valid</p>
          <p class="report-help">A assinatura ICP-Brasil pelo VIDaaS ainda não
            está disponível neste ambiente. O laudo concluído acima segue para
            assinatura qualificada <strong>externa</strong>: baixe o PDF e
            assine com o seu certificado digital.</p>
        </div>`;
    }
    if (state.confirmQualified) {
      return `
        <div class="report-qualified report-release-confirm" role="alertdialog"
          aria-labelledby="reportQualifiedTitle">
          <h4 id="reportQualifiedTitle" tabindex="-1">Assinar com certificado ICP-Brasil</h4>
          <p>Você será levada ao <strong>VIDaaS</strong> para autorizar a
            assinatura com o seu certificado. A autorização acontece no
            aplicativo, no seu celular — ninguém pode fazer isso por você.</p>
          <ul>
            <li>Somente o <strong>resumo criptográfico</strong> do laudo é
              enviado à autoridade certificadora. O PDF e os dados do paciente
              não saem daqui.</li>
            <li>O laudo só é liberado depois que a assinatura for validada.</li>
          </ul>
          <div class="report-release-buttons">
            <button type="button" class="m15-btn m15-btn-primary"
              data-report-qualified-confirm${state.busy ? " disabled" : ""}>
              Continuar para o VIDaaS
            </button>
            <button type="button" class="m15-btn" data-report-qualified-abort>
              Voltar
            </button>
          </div>
        </div>`;
    }
    return `
      <div class="report-qualified">
        <button type="button" class="m15-btn report-qualified-cta"
          data-report-qualified-open${state.busy ? " disabled" : ""}>
          Assinar com VIDaaS (ICP-Brasil)
        </button>
        <p class="report-help">Assinatura digital qualificada, com o seu
          certificado. Exige autorização no aplicativo VIDaaS.</p>
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
        ${/* M25.15 — de quem são estes PDFs. Baixar e entregar o laudo da
              pessoa errada é o pior desfecho possível deste painel, e até
              aqui a única pista era o código do laudo. */""}
        <p class="report-doc-owner">
          <strong class="report-item-name">${esc(patientName(docs))}</strong>
          ${codeTrail(docs)}
        </p>
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
    // M25.4 — colapsado. O status de um provedor que ainda não existe é
    // informação de projeto, não de rotina clínica: ocupava um bloco âmbar
    // inteiro no fim da tela de emissão, competindo com o CTA de liberação.
    // Continua acessível em um clique, e continua dizendo a verdade.
    return `
      <details class="report-signature-panel" ${pending ? "open" : ""}>
        <summary>Assinatura digital qualificada — ${
          pending ? "pendente" : "provedor não configurado"
        }</summary>
        <p>A conclusão clínica deste fluxo não é assinatura ICP-Brasil. A assinatura qualificada é aplicada por você, fora do sistema, no PDF baixado.</p>
        <ul>
          <li>ICP-Brasil A1 — indisponível</li>
          <li>VIDaaS — pendente de seleção e integração</li>
          <li>BirdID — pendente de seleção e integração</li>
        </ul>
      </details>`;
  }

  // M25.3 — contexto do exame e LOCAL DE REALIZAÇÃO estruturado. A médica
  // precisa ver, antes de escolher a conclusão, o mesmo cabeçalho que o PDF
  // vai imprimir. O local vem da unidade parceira vinculada ao documento ou
  // ao exame (nunca fixo no template) e é dado institucional da clínica —
  // jamais endereço do paciente.
  function renderExamAndLocation(detail) {
    const exam = detail.exam || {};
    const local = detail.location || null;
    const naoInformado = `<em class="report-empty-value">não informado</em>`;
    const bd =
      exam.post_bronchodilator === true
        ? "Com fase pós-broncodilatador"
        : exam.post_bronchodilator === false
          ? "Sem fase pós-broncodilatador"
          : naoInformado;
    const dataHora = exam.exam_time
      ? `${fmtDate(exam.exam_date, false)} às ${esc(exam.exam_time)}`
      : fmtDate(exam.exam_date, false);
    // M25.4 — um contexto só. Antes havia a faixa de identidade (paciente,
    // código, nascimento, origem) MAIS este bloco: o mesmo exame aparecia
    // duas vezes e a "origem" repetia o local em código técnico.
    return `
      <div class="report-exam-context">
        <article>
          <h4>Paciente</h4>
          <div><span>Nome</span><strong>${esc(detail.patient.full_name)}</strong></div>
          <div><span>Nascimento</span><strong>${
            fmtDate(detail.patient.date_of_birth, false)
          }</strong></div>
          <div><span>Registro</span><strong>${esc(detail.patient.public_code)}</strong></div>
        </article>
        <article>
          <h4>Exame</h4>
          <div><span>Código</span><strong>${esc(exam.public_code || "—")}</strong></div>
          <div><span>Data</span><strong>${dataHora}</strong></div>
          <div><span>Broncodil.</span><strong>${bd}</strong></div>
          <div><span>Indicação</span><strong>${
            exam.clinical_indication ? esc(exam.clinical_indication) : `<em class="report-empty-value">não informada</em>`
          }</strong></div>
        </article>
        <article>
          <h4>Local de realização</h4>
          ${local
            ? `<div><span>Unidade</span><strong>${esc(local.nome || "—")}</strong></div>
               <div><span>Endereço</span><strong>${
                 local.endereco ? esc(local.endereco) : naoInformado
               }</strong></div>
               <div><span>Contato</span><strong>${
                 local.contato ? esc(local.contato) : naoInformado
               }</strong></div>`
            : `<p class="report-help">Local não resolvido para este documento.</p>`}
        </article>
      </div>`;
  }

  function renderPhysicianDetail() {
    const detail = state.detail;
    if (!detail) {
      return `
        <section class="report-panel report-detail-empty">
          <h3>Área clínica</h3>
          <p>Selecione um paciente em “Meus laudos”. O exame e o histórico clínico aparecem somente dentro do documento atribuído a você.</p>
        </section>`;
    }
    const original = versionByKind("original");
    const current = currentVersion();
    const editable = ["atribuido", "em_elaboracao"].includes(detail.status);
    const ready = detail.status === "em_elaboracao";
    // M25.14 — `preparar-assinatura` exige que a versão CORRENTE seja um
    // rascunho composto (kind "rascunho", do fluxo de anotação sobre o PDF da
    // MIR). A prévia do laudo nativo não serve. Sem esta checagem a médica
    // recebia "Gere uma prévia antes de preparar a assinatura" mesmo com a
    // prévia gerada — instrução impossível de cumprir.
    const hasComposedDraft = Boolean(current && current.kind === "rascunho");
    const signed = detail.status === "assinado";
    const released = detail.status === "liberado";
    const native = latestNativeVersion();
    const hasAddendum = Boolean(native && native.kind === "laudo_adendo");
    const maxPages = original ? original.page_count : 1;
    return `
      <section class="report-panel report-clinical-panel" aria-labelledby="reportDetailHeading">
        <div class="report-panel-heading">
          <div>
            ${/* M25.15 — o cabeçalho da bancada dizia "Documento atribuído"
                  sob dois códigos. Quem está laudando precisa ver, sem
                  procurar, DE QUEM é o exame aberto: o nome vira o título e
                  os códigos ficam logo abaixo, íntegros. */""}
            <p class="eyebrow">${
              [
                "Espirometria",
                fmtDate(detail.exam && detail.exam.exam_date, false),
                detail.location && detail.location.nome,
              ].filter(Boolean).map((p) => esc(p)).join(" • ")
            }</p>
            <h3 id="reportDetailHeading" tabindex="-1">${
              esc(detail.patient.full_name)
            }</h3>
            <p class="report-code-trail">${
              esc(detail.exam.public_code)
            } · ${esc(detail.public_code)}</p>
          </div>
          <div class="report-status-badges">
            <span class="report-status-chip report-${esc(detail.status)}">${
              esc(statusLabel(detail.status))
            }</span>
            ${hasAddendum ? `<span class="report-status-chip report-adendo-flag">Com adendo</span>` : ""}
            ${detail.corrects_document_id
              ? `<span class="report-status-chip report-corrigido-flag">Documento corretivo</span>`
              : ""}
            ${/* M25.4 — "Liberado" e "Conteúdo bloqueado" repetiam o chip de
                  status: liberado JÁ implica bloqueado. Só mostramos o
                  bloqueio quando ele NÃO é consequência óbvia do status. */""
            }${detail.locked && !released
              ? `<span class="report-status-chip report-locked-flag">Conteúdo bloqueado</span>` : ""}
          </div>
        </div>

        ${renderExamAndLocation(detail)}

        ${/* M25.12 — a bancada clínica.
              O exame técnico da MIR e o trabalho sobre o laudo passaram a
              ficar lado a lado: antes, os dois PDFs ocupavam a largura toda e
              as siglas de conclusão ficavam ABAIXO deles, então escolher a
              conclusão obrigava a rolar para longe do exame — exatamente o
              "ficar mudando de tela" relatado. A coluna da esquerda é
              `sticky`: o traçado da MIR permanece visível enquanto a médica
              escolhe as siglas, edita o texto e gera a prévia. */""}
        <div class="report-clinical-split" aria-label="Exame técnico da MIR e laudo da SoproLife">
          <article class="report-source-pane">
            <h4>Exame técnico (MIR)</h4>
            <p class="report-help">Documento original, nunca alterado nem assinado por cima.</p>
            ${renderPdfFrame("original", "PDF original", original)}
          </article>
          <div class="report-work-pane">
            <article class="report-preview-pane">
              <h4>${current && current.kind !== "original"
                ? kindLabel(current.kind) : "Laudo SoproLife"}</h4>
              <p class="report-help">Laudo médico próprio, gerado pelo Centro de Comando.</p>
              ${renderPdfFrame(
                "generated",
                "PDF gerado para comparação",
                current && current.kind !== "original" ? current : null
              )}
            </article>
            ${renderNativeReportForm(detail)}
            ${renderReleaseAction(detail)}
          </div>
        </div>

        ${renderDocumentsPanel()}
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
            <p class="report-help">Este passo pertence ao caminho da <strong>anotação técnica sobre o PDF da MIR</strong> (fluxo M24C), acima. Ele não usa a prévia do laudo SoproLife — para concluir o laudo próprio, use <strong>“Concluir laudo”</strong>.</p>
            ${hasComposedDraft ? `
              <button type="button" class="m15-btn" data-report-prepare-signature${
                state.busy ? " disabled" : ""
              }>Marcar conteúdo pronto para assinatura</button>` : `
              <button type="button" class="m15-btn" data-report-prepare-signature disabled
                aria-describedby="reportPrepareBlocked">Marcar conteúdo pronto para assinatura</button>
              <p id="reportPrepareBlocked" class="report-help">Indisponível: ainda não há anotação técnica composta sobre o PDF da MIR. Gere-a no bloco acima se for mesmo este o caminho desejado.</p>`}
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

  // ------------------------------- M25.20 — central de assinatura externa
  //
  // Seção própria, acima da bancada: é o primeiro lugar onde a médica olha
  // quando volta de uma sessão de assinatura. Toda a interação é por toque —
  // nenhum caminho depende de arrastar arquivo, clique direito ou desktop.

  function renderSignatureCenter() {
    const lista = state.signaturePending;
    const marcados = state.signatureSelection.length;
    const corpo = lista.length
      ? lista.map(renderSignatureItem).join("")
      : `<p class="report-signature-empty">
           Nenhum laudo aguardando assinatura.
         </p>`;
    return `
      <section class="report-panel report-signature"
        aria-labelledby="signatureCenterTitle">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">Assinatura externa</p>
            <h3 id="signatureCenterTitle">
              Aguardando assinatura qualificada — ${lista.length}
            </h3>
          </div>
        </div>

        ${lista.length ? `
          <div class="report-signature-actions">
            <button type="button" class="m15-btn" data-signature-all>
              ${marcados === lista.length
                ? "Limpar seleção" : "Selecionar todos"}
            </button>
            <button type="button" class="m15-btn m15-btn-primary"
              data-signature-download${
                state.signatureBusy || !marcados ? " disabled" : ""
              }>
              ${marcados
                ? `Baixar ${marcados} para assinatura`
                : "Baixar selecionados para assinatura"}
            </button>
          </div>` : ""}

        <div class="report-signature-list" role="group"
          aria-label="Laudos aguardando assinatura qualificada">
          ${corpo}
        </div>

        ${renderSignatureUpload()}
        ${state.signatureReview ? renderSignatureReview() : ""}
      </section>`;
  }

  function renderSignatureItem(item) {
    const marcado = state.signatureSelection.includes(item.document_id);
    // `<label>` embrulhando o input: a área de toque passa a ser a linha
    // inteira, e não um quadradinho de 13px. É o que torna a seleção
    // possível num iPhone sem zoom.
    return `
      <label class="report-signature-item${marcado ? " is-picked" : ""}">
        <input type="checkbox" data-signature-pick="${esc(item.document_id)}"
          ${marcado ? "checked" : ""}>
        <span class="report-signature-body">
          <strong class="report-signature-name">${
            esc(patientName(item))
          }</strong>
          <span class="report-signature-context">${contextLine(item)}</span>
          ${codeTrail(item)}
        </span>
      </label>`;
  }

  function renderSignatureUpload() {
    return `
      <div class="report-signature-return">
        <h4>Enviar lote assinado</h4>
        <p class="report-help">
          Selecione os PDFs assinados, ou um único ZIP com todos. Nada é
          gravado antes de você conferir a lista.
        </p>
        <label class="report-signature-upload">
          <span class="report-signature-upload-label">
            Selecionar PDFs assinados
          </span>
          <input type="file" id="reportSignatureUpload" multiple
            accept=".pdf,application/pdf,.zip,application/zip"
            data-signature-upload${state.signatureBusy ? " disabled" : ""}>
        </label>
      </div>`;
  }

  function renderSignatureReview() {
    const revisao = state.signatureReview;
    const resumo = revisao.resumo || {};
    const linhas = (revisao.arquivos || []).map((item) => `
      <li class="report-signature-result${
        item.ok ? " is-ok" : " is-problem"
      }">
        <span class="report-signature-mark" aria-hidden="true">${
          item.ok ? "✓" : "⚠"
        }</span>
        <span class="report-signature-result-body">
          <strong>${esc(item.paciente || item.arquivo)}</strong>
          ${item.codigo_laudo
            ? `<span>${esc(item.codigo_laudo)}</span>` : ""}
          <em>${esc(item.mensagem || "")}</em>
        </span>
      </li>`).join("");
    const identificados = resumo.identificados || 0;
    return `
      <div class="report-signature-review" role="status" aria-live="polite">
        <h4>Arquivos recebidos — ${resumo.total || 0}</h4>
        <ul class="report-signature-results">${linhas}</ul>
        <p class="report-signature-summary">
          <strong>${identificados}</strong> identificado(s),
          <strong>${resumo.com_problema || 0}</strong> com problema.
        </p>
        ${identificados ? `
          <button type="button" class="m15-btn m15-btn-primary"
            data-signature-confirm${state.signatureBusy ? " disabled" : ""}>
            Confirmar ${identificados} identificado(s)
          </button>` : ""}
        <button type="button" class="m15-btn" data-signature-discard>
          ${identificados ? "Cancelar" : "Fechar"}
        </button>
        ${resumo.com_problema ? `
          <p class="report-help">
            Os arquivos com problema não foram associados a nenhum laudo e
            não serão gravados. Confira e envie novamente.
          </p>` : ""}
      </div>`;
  }

  function renderPhysicianWorkspace() {
    return `
      <div class="report-physician-shell">
        ${renderSignatureCenter()}
        ${renderQueue()}
        ${renderPhysicianDetail()}
      </div>`;
  }

  // M25.12 — localizador de exame.
  //
  // O campo tinha `pattern="ESP-[0-9]{1,9}"`. Um código com letras (o caso
  // relatado, `ESP-TF0001`) fazia a validação NATIVA do navegador abortar o
  // submit: `locateExam` nunca era chamada, nenhuma requisição saía, nenhuma
  // mensagem da aplicação aparecia e o formulário de anexar o PDF nunca
  // surgia. Falha silenciosa por construção.
  //
  // A regra de formato continua idêntica (o servidor a repete em
  // `_SAFE_EXAM_CODE_RE`); o que muda é que quem recusa agora é a aplicação,
  // que explica o motivo e permanece na tela. E, acima de tudo: não é mais
  // obrigatório adivinhar código nenhum — a lista de exames recentes fica ao
  // lado do campo.
  function renderExamLocator() {
    const feedback = state.locateFeedback;
    const semLaudo = examsWithoutReport();
    return `
      <form id="reportLocateExamForm" class="report-inline-form" novalidate>
        ${/* M25.15 — o campo aceitava SÓ o código exato do exame, então
              anexar um PDF exigia saber um ESP de cabeça. Agora aceita
              também o nome do paciente e o código do laudo: as três formas
              como uma pessoa de fato se refere a um exame. */""}
        <label for="reportExamCode">Paciente ou código do exame
          <input id="reportExamCode" name="exam_code" autocomplete="off"
            inputmode="text" maxlength="120" placeholder="Nome do paciente ou ESP-000001"
            aria-describedby="reportExamCodeHelp">
          <span id="reportExamCodeHelp" class="report-help">${esc(EXAM_SEARCH_HINT)}</span>
        </label>
        <button class="m15-btn" type="submit"${state.busy ? " disabled" : ""}>Localizar exame</button>
      </form>
      ${renderLocatorMatches()}
      ${feedback ? `
        <div class="report-locate-feedback is-${esc(feedback.tipo)}"
          id="reportLocateFeedback" role="status" tabindex="-1">
          <strong>${esc(feedback.titulo)}</strong>
          <span>${esc(feedback.mensagem)}</span>
          ${feedback.detalhe
            ? `<span class="report-help">${esc(feedback.detalhe)}</span>` : ""}
        </div>` : ""}
      ${renderRecentExams(semLaudo)}`;
  }

  // Exames que ainda não têm laudo no fluxo. A lista já vem filtrada do
  // servidor (`somente_sem_laudo`), mas o cruzamento com o acompanhamento
  // operacional continua: entre carregar a lista e enviar um PDF, um laudo
  // pode ter sido criado nesta mesma sessão.
  function examsWithoutReport() {
    const comLaudo = new Set(
      (state.operational || []).map((item) => item.exam_code)
    );
    return (state.recentExams || []).filter(
      (exam) => exam && exam.exam_code && !comLaudo.has(exam.exam_code)
    );
  }

  // Um cartão de exame no padrão nome-primeiro. Serve tanto para os
  // resultados da busca quanto para a lista de recentes, para que os dois
  // caminhos até o mesmo exame tenham exatamente a mesma aparência.
  function renderExamPick(exam) {
    const jaTemLaudo = Boolean(exam.report_code);
    return `
      <button type="button" class="report-exam-pick${
        state.locatedExam && state.locatedExam.exam_code === exam.exam_code
          ? " is-selected" : ""
      }" data-report-exam-pick="${esc(exam.exam_code)}">
        <strong class="report-item-name">${esc(patientName(exam))}</strong>
        <span>${contextLine(exam, [exam.exam_status_display])}</span>
        ${codeTrail(exam)}
        ${jaTemLaudo
          ? `<span class="report-exam-pick-flag">já possui laudo</span>` : ""}
      </button>`;
  }

  // M25.15 — resultados da busca. Com homônimos, o operador vê nome, data,
  // unidade e ESP de cada um: o suficiente para escolher sem decorar código
  // e sem que a tela exponha contato ou nascimento.
  function renderLocatorMatches() {
    const matches = state.locateMatches;
    if (!matches || !matches.length) return "";
    return `
      <div class="report-exam-catalog is-matches">
        <p class="report-help">${
          matches.length === 1
            ? "1 exame encontrado."
            : `${matches.length} exames encontrados — confira a data e a unidade antes de escolher.`
        }</p>
        <div class="report-exam-pick-list">${
          matches.map(renderExamPick).join("")
        }</div>
      </div>`;
  }

  function renderRecentExams(exams) {
    if (!exams.length) {
      return `<p class="report-help">Nenhuma espirometria recente sem laudo. Busque pelo nome do paciente ou pelo código acima.</p>`;
    }
    return `
      <details class="report-exam-catalog" open>
        <summary>Espirometrias recentes sem laudo (${exams.length})</summary>
        <p class="report-help">Clique para localizar sem digitar nada.</p>
        <div class="report-exam-pick-list">${
          exams.slice(0, 12).map(renderExamPick).join("")
        }</div>
      </details>`;
  }

  function renderOperationalList() {
    const selected = selectedOperational();
    const rows = state.operational.length
      ? state.operational.map((item) => `
          <button type="button" class="report-operation-row${
            item.document_id === state.selectedOperationalId ? " is-selected" : ""
          }" data-report-operational="${esc(item.document_id)}">
            <strong class="report-item-name">${esc(patientName(item))}</strong>
            <span>${contextLine(item)}</span>
            <span>${esc(statusLabel(item.status))}</span>
            ${codeTrail(item)}
          </button>`).join("")
      : `<div class="report-empty">Nenhum documento no fluxo.</div>`;
    return `
      <section class="report-panel" aria-labelledby="operationalListTitle">
        <h3 id="operationalListTitle">Acompanhamento operacional</h3>
        <p class="report-help">Paciente, local, atribuição e estado técnico. A interpretação clínica não é exposta aqui.</p>
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
                    esc(physicianLabel(item))
                  }</option>`).join("")}
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

  // M25.15 — quem vai laudar.
  //
  // A lista NUNCA é fixa no código: vem de `/laudos/medicos-disponiveis`,
  // que só devolve perfil ativo, conta ativa, papel médico explícito e
  // `verification_status = verified`. Um médico novo devidamente cadastrado
  // e verificado passa a aparecer aqui sozinho; um suspenso ou pendente
  // desaparece sozinho. Hoje isso resulta em uma única médica elegível.
  //
  // As três situações têm tratamento próprio e explícito:
  //
  //   0 elegíveis — o formulário inteiro é bloqueado (fail closed). Enviar
  //     um PDF sem destinatário criaria um laudo que ninguém pode laudar.
  //   1 elegível  — vem pré-selecionado, MAS o seletor continua visível:
  //     quem envia precisa ver para quem está enviando.
  //   2 ou mais   — nenhum vem marcado. A escolha é consciente; escolher o
  //     primeiro em silêncio mandaria o exame para o médico errado sem que
  //     ninguém percebesse.
  function physicianLabel(profile) {
    return profile.credentials_label
      || `${profile.professional_name} • CRM-${profile.crm_state} ${
           profile.crm_formatted || profile.crm_number}`;
  }

  function renderPhysicianChooser() {
    const eligiveis = state.physicians || [];
    if (!eligiveis.length) {
      return `
        <div class="report-locate-feedback is-erro" role="status">
          <strong>Nenhum médico elegível para receber o laudo</strong>
          <span>O envio está bloqueado: só recebe laudo um perfil médico
            ativo, com conta ativa e verificação de CRM concluída.</span>
          <span class="report-help">Cadastre ou verifique o perfil médico em
            “Contas médicas” antes de anexar o PDF.</span>
        </div>`;
    }
    const unico = eligiveis.length === 1;
    return `
      <label for="reportPhysician">Médico responsável pelo laudo
        <select id="reportPhysician" name="physician_profile_id" required>
          ${unico ? "" : `<option value="">Selecione o médico responsável</option>`}
          ${eligiveis.map((profile) => `
            <option value="${esc(profile.id)}"${
              unico ? " selected" : ""
            }>${esc(physicianLabel(profile))}</option>`).join("")}
        </select>
        <span class="report-help">${
          unico
            ? "Único médico elegível hoje — este laudo será atribuído a ele."
            : "Escolha explícita: o laudo vai para a fila de quem for selecionado aqui."
        }</span>
      </label>`;
  }

  // M25.17 — LOCAL DO EXAME, somente leitura.
  //
  // Três estados, e nenhum deles pede combinação ao operador:
  //   derivado e completo   — mostra unidade e endereço;
  //   derivado sem registro — diz que o atendimento não tem local e oferece
  //                           completar, sem travar o envio;
  //   cadastro contraditório — bloqueia, porque seguir imprimiria o
  //                           endereço de uma clínica onde o exame não foi
  //                           feito, e explica onde consertar.
  function renderExamLocation(exame) {
    const origem = exame && exame.origem_derivada;
    if (!origem) return "";
    if (!origem.ok) {
      return `
        <div class="report-location-readonly is-bloqueado" role="status">
          <span class="report-field-label">Local do exame</span>
          <strong>Cadastro do atendimento incompleto</strong>
          <span>${esc(origem.mensagem || "")}</span>
          <span class="report-help">${esc(origem.como_corrigir || "")}</span>
        </div>`;
    }
    const incompleto = origem.completo === false;
    return `
      <div class="report-location-readonly${incompleto ? " is-incompleto" : ""}" role="status">
        <span class="report-field-label">Local do exame</span>
        <strong>${esc(origem.display_name || "—")}</strong>
        ${origem.address_line
          ? `<span>${esc(origem.address_line)}</span>` : ""}
        <span class="report-help">${
          incompleto
            ? "O atendimento não registra onde o exame foi feito. O laudo sai sem endereço; complete o atendimento para que ele apareça."
            : "Vem do atendimento e é o endereço impresso no laudo."
        }</span>
      </div>`;
  }

  function renderOperationalWorkspace() {
    const semMedico = !(state.physicians || []).length;
    return `
      <div class="report-operational-shell">
        <section class="report-panel" aria-labelledby="uploadTitle">
          <p class="eyebrow">Recebimento e atribuição</p>
          <h3 id="uploadTitle">Novo PDF original</h3>
          ${renderExamLocator()}
          ${state.locatedExam ? `
            ${/* Confirmação do exame localizado: o operador confere a PESSOA
                  antes de anexar o PDF. Anexar o exame de outra pessoa é o
                  erro mais caro deste fluxo, e um código sozinho não deixa
                  ninguém perceber que errou. */""}
            <div class="report-technical-confirmation" role="status">
              <strong class="report-item-name">${esc(patientName(state.locatedExam))}</strong>
              <span>${contextLine(state.locatedExam, [
                state.locatedExam.exam_status_display,
              ])}</span>
              ${codeTrail(state.locatedExam)}
            </div>
            <form id="reportUploadForm" class="report-upload-form">
              <input type="hidden" name="exam_code" value="${esc(state.locatedExam.exam_code)}">
              ${renderPhysicianChooser()}
              ${/* M25.17 — o LOCAL deixou de ser pergunta.
                    Antes havia dois campos livres, "Origem do exame" e
                    "Unidade parceira", e cabia ao operador descobrir quais
                    combinações o servidor aceita. No primeiro uso real isso
                    custou um `unidade_origem_incompativel`: origem "Pastore"
                    com unidade "Pastore Ipanema" é recusada, porque unidade
                    só vale para "clínica parceira".
                    O exame já sabia onde foi feito. Agora ele responde, e a
                    tela apenas mostra — em modo leitura. */""}
              ${renderExamLocation(state.locatedExam)}
              <label for="reportPdfFile">PDF original
                <input id="reportPdfFile" name="file" type="file"
                  accept="application/pdf,.pdf" required>
              </label>
              ${/* Fail closed (M25.15): sem médico elegível o botão não é
                    clicável. O servidor recusaria de qualquer forma
                    (`medico_nao_eligivel`), mas deixar o operador anexar um
                    arquivo, preencher tudo e só então levar erro é gastar o
                    trabalho dele para descobrir algo já sabido na abertura
                    da tela. */""}
              <button class="m15-btn m15-btn-primary" type="submit"${
                state.busy || semMedico ? " disabled" : ""
              }>Enviar e atribuir</button>
            </form>` : `
            <p class="report-help">Localize o paciente acima — por nome ou por
              código — para anexar o PDF. A atribuição só é criada depois que
              um exame exato for escolhido.</p>`}
        </section>
        ${renderDeliveryQueue()}
        ${renderOperationalList()}
      </div>`;
  }

  // ------------------------------ M25.20 — fila de entrega administrativa
  //
  // Os cinco estados do percurso do documento até o paciente. Todos são
  // DERIVADOS do que está gravado — nenhum é um campo que alguém marca à
  // mão. A transição para "assinado recebido" acontece sozinha quando a
  // médica confirma o lote devolvido: ninguém avisa a administração por
  // WhatsApp de que os laudos voltaram.

  function renderDeliveryQueue() {
    const fila = state.deliveryQueue;
    if (!fila) return "";
    const chips = (fila.estados || []).map((estado) => `
      <button type="button" class="report-delivery-chip${
        state.deliveryFilter === estado.chave ? " is-active" : ""
      }" data-delivery-filter="${esc(estado.chave)}">
        ${esc(estado.rotulo)} <span>${estado.total}</span>
      </button>`).join("");
    const visiveis = state.deliveryFilter
      ? (fila.itens || []).filter((i) => i.estado === state.deliveryFilter)
      : (fila.itens || []);
    const linhas = visiveis.length
      ? visiveis.map(renderDeliveryRow).join("")
      : `<p class="report-help">Nenhum laudo neste estado.</p>`;
    return `
      <section class="report-panel report-delivery"
        aria-labelledby="deliveryQueueTitle">
        <div class="report-panel-heading">
          <div>
            <p class="eyebrow">Entrega</p>
            <h3 id="deliveryQueueTitle">Fila de laudos</h3>
          </div>
        </div>
        <div class="report-delivery-chips">
          <button type="button" class="report-delivery-chip${
            state.deliveryFilter ? "" : " is-active"
          }" data-delivery-filter="">Todos</button>
          ${chips}
        </div>
        <div class="report-delivery-list">${linhas}</div>
      </section>`;
  }

  function renderDeliveryRow(item) {
    const assinado = item.assinado;
    return `
      <div class="report-delivery-row report-delivery-${esc(item.estado)}">
        <div class="report-delivery-body">
          <strong class="report-item-name">${esc(patientName(item))}</strong>
          <span>${contextLine(item)}</span>
          ${codeTrail(item)}
          <span class="report-delivery-state">${esc(item.estado_rotulo)}</span>
          ${assinado ? `
            <span class="report-delivery-note">
              ${esc(assinado.pareado_por_rotulo || "")}
              ${/* A negativa fica na tela, e não só na API: uma linha que
                    diga só "assinado" convida a conclusão errada. */""}
              — a SoproLife não verificou a assinatura criptograficamente.
            </span>` : ""}
        </div>
        <div class="report-delivery-actions">
          <a class="m15-btn" download
            href="${esc(apiHref(`/laudos/${item.document_id}/exame-tecnico/conteudo`))}">
            Baixar exame técnico
          </a>
          ${assinado ? `
            <a class="m15-btn" download
              href="${esc(apiHref(`/laudos/${item.document_id}/assinado/conteudo`))}">
              Baixar laudo assinado
            </a>` : ""}
          ${assinado && assinado.status === "recebido_validacao_pendente"
            ? `<button type="button" class="m15-btn m15-btn-primary"
                 data-delivery-validate="${esc(assinado.signed_document_id)}">
                 Registrar validação da assinatura
               </button>` : ""}
          ${assinado && assinado.status === "validado_externamente"
            ? `<button type="button" class="m15-btn"
                 data-delivery-deliver="${esc(assinado.signed_document_id)}">
                 Marcar como entregue
               </button>` : ""}
        </div>
      </div>`;
  }

  // Link de download autenticado pelo COOKIE de sessão — um `<a download>`
  // não manda cabeçalho `Authorization`. É o mesmo caminho que a M25.18
  // abriu para o visualizador de PDF, e por isso só é oferecido quando há
  // sessão de verdade.
  function apiHref(path) {
    const c = client();
    return c && c.hasSession() ? c.apiUrl(path) : "#";
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
            <div class="report-admin-grid">
              <label for="reportCrmDisplay">CRM formatado (impresso no laudo)
                <input id="reportCrmDisplay" name="crm_display" maxlength="30"
                  placeholder="52.62307-5" aria-describedby="crmDisplayHelp"
                  value="${esc(profile && profile.crm_display)}">
                <span id="crmDisplayHelp" class="report-help">Só formatação —
                  precisa ter os mesmos dígitos do CRM.</span>
              </label>
              <label for="reportEspecialidade">Especialidade (impressa no laudo)
                <input id="reportEspecialidade" name="especialidade" maxlength="120"
                  placeholder="Médica Pneumologista"
                  value="${esc(profile && profile.especialidade)}">
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
            <label for="reportVerificationReference">Referência da verificação
              <input id="reportVerificationReference" name="verification_reference"
                maxlength="120" aria-describedby="verificationReferenceHelp"
                placeholder="CREMERJ-BUSCA-PUBLICA-20260808-CRM5262307-5"
                value="${esc(profile && profile.verification_reference)}">
              <span id="verificationReferenceHelp" class="report-help">
                Obrigatória para marcar <strong>Verificado</strong>: o código
                ou identificador da consulta que você fez ao conselho. Sem
                ela o servidor recusa a verificação.
              </span>
            </label>
            <label class="report-check">
              <input type="checkbox" name="active"${
                profile && profile.active ? " checked" : ""
              }> Perfil ativo
            </label>
            ${state.profileError
              ? `<p class="report-profile-error" role="alert">${
                  esc(state.profileError)
                }</p>` : ""}
            <p class="report-verification-state">Estado atual: <strong>${
              esc(profile ? profile.verification_status : "perfil ainda não criado")
            }</strong>${profile && profile.verified_at
              ? ` · ${fmtDate(profile.verified_at, true)}` : ""}</p>
            <button class="m15-btn" type="submit">Salvar perfil médico</button>
          </form>
          ${renderSignatureAssetAdmin(profile)}` : ""}
      </section>`;
  }

  // M25.4 — área administrativa do ativo de assinatura manuscrita.
  //
  // Os endpoints existiam desde a M25.2, mas NÃO havia interface: na prática
  // não havia como cadastrar a assinatura sem chamar a API à mão. Este bloco
  // é o lugar visível onde o ativo autorizado entra.
  //
  // A imagem NUNCA volta pela API (nem bytes, nem caminho): o painel mostra
  // apenas se existe, o hash e as dimensões. Por isso não há preview aqui —
  // a ausência de preview é intencional, não uma lacuna.
  function renderSignatureAssetAdmin(profile) {
    if (!profile || !profile.id) {
      return `<p class="report-help">Salve o perfil médico antes de cadastrar
        a assinatura.</p>`;
    }
    const asset = state.signatureAsset;
    const carregado = asset && asset.physician_profile_id === profile.id;
    const configurada = carregado && asset.configurada;
    return `
      <section class="report-signature-asset" aria-labelledby="signatureAssetTitle">
        <h4 id="signatureAssetTitle">Assinatura manuscrita (imagem)</h4>
        <p class="report-help">
          Elemento visual de identificação. <strong>Não é</strong> assinatura
          digital qualificada ICP-Brasil. É aplicada somente depois de a médica
          clicar em “Concluir laudo” — nunca na prévia.
        </p>
        ${carregado ? `
          <p class="report-signature-asset-state">
            <span class="report-status-chip ${
              configurada ? "report-liberado-flag" : "report-atribuido"
            }">${configurada ? "Cadastrada" : "Não cadastrada"}</span>
            ${configurada ? `<span class="report-help">SHA-256
              ${esc(String(asset.ativo.sha256).slice(0, 16))}… ·
              ${esc(asset.ativo.image_width)}×${esc(asset.ativo.image_height)} px</span>` : ""}
          </p>` : `
          <button class="m15-btn" type="button"
            data-report-signature-status="${esc(profile.id)}">Ver situação da assinatura</button>`}
        <form id="reportSignatureAssetForm" class="report-signature-asset-form">
          <input type="hidden" name="physician_profile_id" value="${esc(profile.id)}">
          <label for="reportSignatureFile">Arquivo PNG com fundo transparente
            <input id="reportSignatureFile" name="arquivo" type="file"
              accept="image/png,.png" required>
            <span class="report-help">Até 2 MiB, proporção entre 0,25:1 e 12:1.
              O arquivo é gravado fora do repositório, em raiz privada 0700.</span>
          </label>
          <label class="report-check">
            <input type="checkbox" name="confirmacao_ok" required>
            Confirmo que este é o ativo de assinatura <strong>autorizado</strong>
            pela profissional identificada acima.
          </label>
          <div class="report-signature-asset-actions">
            <button class="m15-btn m15-btn-primary" type="submit"${
              state.busy ? " disabled" : ""
            }>Cadastrar assinatura</button>
            ${configurada ? `
              <button class="m15-btn" type="button"
                data-report-signature-revoke="${esc(profile.id)}">Revogar atual</button>` : ""}
          </div>
        </form>
      </section>`;
  }

  // M25.19 — o catálogo técnico versionado saiu daqui.
  //
  // Havia um bloco administrativo que listava todo o catálogo de templates do
  // servidor como cards — inclusive os seis placeholders provisórios,
  // carimbados como impróprios para produção — e um formulário de nova
  // revisão. Nada disso é operação: a médica lauda pelas
  // conclusões clínicas definitivas da bancada (M25.2), e o catálogo de
  // templates só alimenta o fluxo legado de anotação sobre o PDF da MIR
  // (M24C).
  //
  // O que ficou de fora é APENAS a vitrine. Os templates continuam no banco,
  // os endpoints de leitura administrativa e de nova revisão continuam
  // existindo com o mesmo RBAC, o versionamento imutável continua valendo e o
  // E2E continua provando que os provisórios seguem em rascunho e não
  // aprovados. Editar o catálogo passou a ser tarefa de API, não de tela
  // operacional.

  function renderAdminWorkspace() {
    // M25.10 — a administração vem RECOLHIDA. Ela competia visualmente com o
    // fluxo operacional e fazia a tela parecer um painel de configuração, e
    // não o lugar onde se recebe exame e se lauda. Continua inteira, a um
    // clique de distância.
    return `
      <details class="report-admin-shell">
        <summary>Administração restrita — contas médicas</summary>
        <p class="report-help">Ajustes de cadastro. Não é necessário para
          receber exames nem para laudar.</p>
        ${renderProfileAdmin()}
      </details>`;
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
    else if (can("admin") || can("operacional")) {
      // Sem esta nota, um administrador conclui que "a tela sumiu". Ela não
      // sumiu: o papel médico é EXPLÍCITO por desenho — uma conta
      // administrativa nunca ganha autoria clínica por herança.
      blocks.push(`
        <section class="report-panel report-role-note">
          <h3>Fila médica não aparece nesta conta</h3>
          <p>Você está autenticado com perfil administrativo/operacional.
            A fila de laudos e a assinatura só aparecem para contas com o
            <strong>papel médico explícito</strong> — uma conta administrativa
            nunca recebe autoria clínica por herança.</p>
          <p class="report-help">Para conferir o fluxo clínico, entre com a
            conta da médica responsável.</p>
        </section>`);
    }
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
        // M25.20 — o que está aguardando assinatura qualificada. Lista
        // própria, e não um filtro da fila clínica: o recorte é do
        // servidor (laudo concluído, desta médica, ainda sem assinado
        // recebido) e o navegador nunca o recalcula.
        calls.push(client().api("/laudos/assinatura-externa/pendentes"));
        labels.push("signaturePending");
      }
      if (can("operacional")) {
        calls.push(client().api("/laudos"));
        labels.push("operational");
        calls.push(client().api("/laudos/medicos-disponiveis"));
        labels.push("physicians");
        // M25.17 — a chamada a `/unidades` saiu junto com o seletor de
        // unidade parceira. O local do exame vem resolvido dentro de
        // `/laudos/exames` (`origem_derivada`), então baixar o catálogo de
        // unidades a cada carga virou trabalho para uma lista que ninguém
        // mais escolhe.
        // M25.12 — espirometrias recentes para escolher sem digitar código.
        // O motivo desta chamada é a falha relatada: o único caminho para
        // anexar um PDF era acertar de cabeça um código exato.
        //
        // M25.15 — passou de `/espirometrias` para `/laudos/exames`, que já
        // devolve o NOME do paciente e a unidade junto do exame. O endpoint
        // genérico não traz identidade, e cruzar exame com paciente no
        // navegador exigiria baixar a base de pessoas inteira para a tela.
        calls.push(client().api("/laudos/exames?somente_sem_laudo=true"));
        labels.push("recentExams");
        // M25.20 — a fila de entrega, com os cinco estados do percurso do
        // documento. Os estados são derivados no servidor; o navegador só
        // filtra o que já recebeu.
        calls.push(client().api("/laudos/assinatura-externa/fila"));
        labels.push("deliveryQueue");
      }
      if (can("admin")) {
        calls.push(client().api("/laudos/admin/medicos"));
        labels.push("adminAccounts");
        // M25.19 — a carga do catálogo administrativo de templates saiu junto
        // com o bloco visual dele. Sem tela que o consuma, baixar o catálogo
        // inteiro a cada carga era trabalho para ninguém ver. O endpoint
        // continua no servidor, restrito a admin.
      }
      const values = await Promise.all(calls);
      if (epoch !== state.loadEpoch) return;
      labels.forEach((label, index) => {
        const value = values[index];
        // `/unidades` e `/espirometrias` são paginados ({itens, total}); os
        // demais devolvem lista.
        state[label] = (value && Array.isArray(value.itens))
          ? value.itens
          : value;
      });
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

  // M25.18 — a SEGUNDA causa do arquivo com nome aleatório.
  //
  // O PDF é exibido num <iframe>, e o visualizador embutido do Chrome tem o
  // próprio botão de download — que é o botão à mão de quem está lendo o
  // laudo. Com `src` apontando para uma object URL (`blob:`), esse download
  // não tem nome nenhum para herdar, e o Chrome inventa um: `UWNAUiEo.pdf`.
  // Nenhum ajuste no botão "Baixar" do painel alcançava esse caminho.
  //
  // Com sessão por cookie o iframe se autentica sozinho e pode apontar
  // direto para a API: aí o visualizador recebe o `Content-Disposition` e o
  // botão dele passa a salvar com o nome certo. Com token da CLI o iframe
  // não tem como autenticar, então o blob continua sendo o único caminho —
  // é modo avançado, e a visualização continua funcionando.
  function pdfViewerSource(version) {
    const c = client();
    if (c && typeof c.hasSession === "function" && c.hasSession()
        && typeof c.apiUrl === "function") {
      return c.apiUrl(
        reportContentPath(state.selectedDocumentId, version.id, "inline")
      );
    }
    return null;
  }

  async function loadPdf(version, slot, epoch) {
    if (!version) return;
    const direto = pdfViewerSource(version);
    if (direto) {
      if (epoch !== state.loadEpoch) return;
      if (state.pdfUrls[slot] && state.pdfUrls[slot].startsWith("blob:")) {
        URL.revokeObjectURL(state.pdfUrls[slot]);
      }
      state.pdfUrls[slot] = direto;
      render();
      return;
    }
    try {
      const blob = await client().apiBlob(
        reportContentPath(state.selectedDocumentId, version.id, "inline")
      );
      if (epoch !== state.loadEpoch) return;
      if (state.pdfUrls[slot] && state.pdfUrls[slot].startsWith("blob:")) {
        URL.revokeObjectURL(state.pdfUrls[slot]);
      }
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
    }
  }

  // ----------------------------------- M25.8 — lote de assinatura externa

  async function downloadSigningBatch() {
    state.batchBusy = true;
    state.batchResults = null;
    announce("Preparando o pacote de assinatura…", "");
    render();
    try {
      const blob = await client().apiBlob("/laudos/lote/baixar", {
        method: "POST",
        body: JSON.stringify({
          document_ids: state.batchSelection,
          incluir_mir: false,
        }),
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "laudos-para-assinar.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Libera a URL depois do clique: revogar antes cancela o download.
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      announce("Pacote baixado. Assine os PDFs e devolva aqui.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.batchBusy = false;
      render();
    }
  }

  async function uploadSignedBatch(arquivos) {
    if (!arquivos || !arquivos.length) return;
    state.batchBusy = true;
    announce(`Validando ${arquivos.length} arquivo(s)…`, "");
    render();
    try {
      const corpo = new FormData();
      for (const arquivo of arquivos) corpo.append("arquivos", arquivo);
      state.batchResults = await client().api("/laudos/lote/enviar", {
        method: "POST",
        body: corpo,
      });
      const { validados, com_erro: comErro } = state.batchResults.resumo;
      announce(
        `${validados} laudo(s) assinado(s) e liberado(s); ${comErro} com erro.`,
        comErro ? "erro" : "ok",
      );
      state.batchSelection = [];
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.batchBusy = false;
      render();
    }
  }

  // ------------------------ M25.20 — central de assinatura externa em lote

  async function downloadForSignature() {
    if (!state.signatureSelection.length) return;
    const total = state.signatureSelection.length;
    state.signatureBusy = true;
    announce(
      total === 1
        ? "Preparando o laudo para assinatura…"
        : `Preparando ${total} laudos para assinatura…`,
      ""
    );
    render();
    try {
      const blob = await client().apiBlob("/laudos/assinatura-externa/baixar", {
        method: "POST",
        body: JSON.stringify({ document_ids: state.signatureSelection }),
      });
      // O nome vem do servidor pelo `Content-Disposition`: é ele que sabe se
      // saiu um PDF com o nome da paciente ou o ZIP do dia.
      saveBlob(blob);
      state.signatureSelection = [];
      announce(
        total === 1
          ? "Laudo baixado. Assine e devolva aqui."
          : "Pacote baixado. Assine os PDFs e devolva aqui.",
        "ok"
      );
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.signatureBusy = false;
      render();
    }
  }

  // Download por âncora, com o nome que o servidor mandou. Sem isso o
  // navegador salva um nome aleatório vindo da object URL — a falha que a
  // M25.18 rastreou até o `Content-Disposition` descartado.
  function saveBlob(blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = blob.nomeSugerido || "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revogar antes do clique cancelaria o download em curso.
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  async function uploadSignedForReview(arquivos) {
    if (!arquivos || !arquivos.length) return;
    state.signatureBusy = true;
    state.signatureReview = null;
    announce(`Conferindo ${arquivos.length} arquivo(s)…`, "");
    render();
    try {
      const corpo = new FormData();
      for (const arquivo of arquivos) corpo.append("arquivos", arquivo);
      state.signatureReview = await client().api(
        "/laudos/assinatura-externa/enviar",
        { method: "POST", body: corpo }
      );
      const resumo = state.signatureReview.resumo || {};
      announce(
        `${resumo.identificados || 0} identificado(s), ${
          resumo.com_problema || 0
        } com problema. Confira antes de confirmar.`,
        resumo.com_problema ? "erro" : "ok"
      );
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.signatureBusy = false;
      render();
    }
  }

  async function confirmSignedBatch() {
    const revisao = state.signatureReview;
    if (!revisao || !revisao.identificados || !revisao.identificados.length) {
      return;
    }
    state.signatureBusy = true;
    announce("Confirmando o lote…", "");
    render();
    try {
      const resposta = await client().api(
        "/laudos/assinatura-externa/confirmar",
        {
          method: "POST",
          body: JSON.stringify({
            batch_id: revisao.batch_id,
            signed_document_ids: revisao.identificados.map(
              (item) => item.signed_document_id
            ),
          }),
        }
      );
      state.signatureReview = null;
      state.signatureSelection = [];
      announce(
        `${resposta.confirmados} laudo(s) assinado(s) recebido(s). A ` +
        "validação da assinatura segue pendente.",
        "ok"
      );
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.signatureBusy = false;
      render();
    }
  }

  // A confirmação é DIGITADA, e não um "ok" de caixa de diálogo. O que está
  // sendo registrado é o testemunho de uma pessoa identificada de que
  // conferiu a assinatura num validador externo — não uma verificação que a
  // SoproLife tenha feito. Um clique distraído não deve produzir isso.
  const VALIDACAO_FRASE = "Confirmo a conferência externa";

  async function registerExternalValidation(signedId) {
    const digitado = window.prompt(
      "Você conferiu esta assinatura no Validar ITI (ou equivalente)?\n\n" +
      "A SoproLife NÃO valida a cadeia ICP-Brasil — o que será registrado é " +
      "a sua conferência externa, com seu usuário e a data/hora.\n\n" +
      `Para confirmar, digite: ${VALIDACAO_FRASE}`
    );
    if (digitado === null) return;
    state.busy = true;
    render();
    try {
      await client().api(
        `/laudos/assinatura-externa/${encodeURIComponent(signedId)}` +
        "/validacao-externa",
        {
          method: "POST",
          body: JSON.stringify({
            metodo: "validar_iti",
            confirmacao: digitado,
          }),
        }
      );
      announce(
        "Conferência externa registrada. O laudo está pronto para entrega.",
        "ok"
      );
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
    }
  }

  async function registerDelivery(signedId) {
    state.busy = true;
    render();
    try {
      await client().api(
        `/laudos/assinatura-externa/${encodeURIComponent(signedId)}/entrega`,
        { method: "POST" }
      );
      announce("Entrega registrada.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
    }
  }

  // ------------------------------------- M25.7 — assinatura qualificada

  function stopQualifiedPolling() {
    if (state.qualifiedTimer) {
      clearTimeout(state.qualifiedTimer);
      state.qualifiedTimer = null;
    }
  }

  async function startQualifiedSignature() {
    if (!state.previewVersionId || !state.previewTextSha256) {
      announce("Gere a prévia do laudo antes de assinar.", "erro");
      return;
    }
    state.busy = true;
    state.confirmQualified = false;
    announce("Preparando o laudo para assinatura com VIDaaS…", "");
    render();
    try {
      const resposta = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}`
        + "/assinatura-qualificada/iniciar",
        {
          method: "POST",
          body: JSON.stringify({
            confirmacao: RELEASE_CONFIRMATION,
            expected_version_id: state.previewVersionId,
            expected_text_sha256: state.previewTextSha256,
          }),
        },
      );
      state.qualifiedRequest = resposta;
      // A médica autoriza no VIDaaS, em outra aba. O painel continua vivo
      // aqui e detecta a conclusão pelo acompanhamento abaixo.
      if (resposta.url_autorizacao) {
        window.open(resposta.url_autorizacao, "_blank", "noopener");
      }
      announce("Autorize a assinatura no aplicativo VIDaaS.", "");
      scheduleQualifiedPoll();
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
    }
  }

  function scheduleQualifiedPoll() {
    stopQualifiedPolling();
    // 4s é rápido o suficiente para parecer imediato e devagar o bastante
    // para não martelar a API enquanto a médica procura o celular.
    state.qualifiedTimer = setTimeout(pollQualifiedSignature, 4000);
  }

  async function pollQualifiedSignature() {
    if (!state.selectedDocumentId) return;
    try {
      const pedido = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}`
        + "/assinatura-qualificada",
      );
      state.qualifiedRequest = pedido;
      if (QUALIFIED_WAITING.includes(pedido.status)) {
        scheduleQualifiedPoll();
      } else {
        stopQualifiedPolling();
        if (pedido.status === "assinado_liberado") {
          announce("Laudo assinado com certificado ICP-Brasil.", "ok");
          await loadDocument(state.selectedDocumentId, false);
        }
      }
      render();
    } catch (error) {
      // Falha de acompanhamento não pode derrubar a tela: a solicitação
      // continua viva no servidor e o próximo ciclo tenta de novo.
      stopQualifiedPolling();
      render();
    }
  }

  async function cancelQualifiedSignature() {
    state.busy = true;
    render();
    try {
      state.qualifiedRequest = await client().api(
        `/laudos/${encodeURIComponent(state.selectedDocumentId)}`
        + "/assinatura-qualificada/cancelar",
        { method: "POST" },
      );
      stopQualifiedPolling();
      announce("Assinatura com VIDaaS cancelada.", "");
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
    }
  }

  async function releaseReport() {
    if (!state.previewVersionId || !state.previewTextSha256) {
      announce("Gere a prévia do laudo antes de concluir.", "erro");
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
      // M25.17 — o nome vem do servidor (Content-Disposition), transportado
      // pelo cliente da API. `download = ""` deixava o navegador inventar um
      // nome a partir da object URL, que é o arquivo aleatório relatado.
      anchor.download = blob.nomeSugerido || "";
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) {
      announce(readableError(error), "erro");
    }
  }

  // M25.12 — cada desfecho da busca tem um estado NOMEADO e visível: formato
  // recusado, não encontrado, já tem laudo, erro de API, encontrado. Antes,
  // "formato recusado" nem chegava aqui (o navegador barrava o submit) e os
  // demais viravam o mesmo toast passageiro.
  function setLocateFeedback(tipo, titulo, mensagem, detalhe) {
    state.locateFeedback = { tipo, titulo, mensagem, detalhe: detalhe || "" };
  }

  // M25.15 — a busca aceita nome do paciente, ESP ou LAU e o servidor decide
  // qual das três é. Quando o termo casa com mais de um exame (homônimos, ou
  // a mesma pessoa com vários exames), NADA é escolhido automaticamente: os
  // candidatos são listados com data e unidade para o operador decidir.
  async function locateExam(termo) {
    const digitado = String(termo || "").trim();
    if (!digitado) {
      state.locatedExam = null;
      state.locateMatches = [];
      setLocateFeedback(
        "erro", "Informe um paciente ou um código",
        "Digite o nome do paciente, o código do exame ou escolha um da lista abaixo.",
        EXAM_SEARCH_HINT
      );
      render();
      return;
    }
    const comoCodigo = digitado.toUpperCase();
    const ehCodigoExame = EXAM_CODE_RE.test(comoCodigo);
    if (!ehCodigoExame && !REPORT_CODE_RE.test(comoCodigo)
        && digitado.replace(/\s+/g, "").length < 3) {
      state.locatedExam = null;
      state.locateMatches = [];
      setLocateFeedback(
        "erro", "Busca curta demais",
        "Informe ao menos 3 letras do nome do paciente, ou um código completo.",
        EXAM_SEARCH_HINT
      );
      render();
      return;
    }
    state.busy = true;
    announce("Localizando exame…", "");
    render();
    try {
      const busca = ehCodigoExame || REPORT_CODE_RE.test(comoCodigo)
        ? comoCodigo : digitado;
      const response = await client().api(
        `/laudos/exames?q=${encodeURIComponent(busca)}`
      );
      const items = Array.isArray(response) ? response : [];
      if (!items.length) {
        state.locatedExam = null;
        state.locateMatches = [];
        setLocateFeedback(
          "erro", `Nada encontrado para “${digitado}”`,
          ehCodigoExame
            ? "Nenhuma espirometria cadastrada com este código institucional."
            : "Nenhum exame de paciente com este nome. Confira a grafia ou busque pelo código.",
          "O exame precisa existir no CRM de Espirometria antes de receber um laudo. Confira a lista abaixo ou cadastre o exame primeiro."
        );
        announce(`Nada encontrado para ${digitado}.`, "erro");
        return;
      }
      if (items.length > 1) {
        // Ambiguidade real: escolher sozinho aqui é como o PDF ia parar no
        // paciente errado. A decisão volta para quem sabe qual é o certo.
        state.locatedExam = null;
        state.locateMatches = items;
        setLocateFeedback(
          "aviso", `${items.length} exames encontrados`,
          "Mais de um exame corresponde à busca. Escolha abaixo conferindo a data e a unidade.",
          "Pacientes com o mesmo nome se distinguem pela data do exame, pela unidade e pelo código ESP."
        );
        announce(`${items.length} exames encontrados. Escolha um.`, "aviso");
        return;
      }
      const found = items[0];
      state.locatedExam = found;
      state.locateMatches = [];
      const jaTemLaudo = Boolean(found.report_code)
        || (state.operational || []).some(
          (item) => item.exam_code === found.exam_code
        );
      if (jaTemLaudo) {
        setLocateFeedback(
          "aviso", `${patientName(found)} — exame já possui laudo`,
          "Este exame já tem um documento no fluxo. Enviar outro PDF cria um segundo laudo para a mesma espirometria.",
          "Confira “Acompanhamento operacional” antes de continuar."
        );
      } else {
        setLocateFeedback(
          "ok", `${patientName(found)} — exame localizado`,
          `${found.exam_code} · ${fmtDate(found.exam_date, false)} · ${
            found.location_name || "local não informado"}.`,
          "Confira se é a pessoa certa, escolha o médico responsável e anexe o PDF do equipamento."
        );
      }
      announce("Exame localizado. Complete a atribuição técnica.", "ok");
    } catch (error) {
      state.locatedExam = null;
      const http = error && error.status ? ` (HTTP ${error.status})` : "";
      setLocateFeedback(
        "erro", `Não foi possível consultar ${normalized}`,
        `${readableError(error)}${http}`,
        error && error.status === 401
          ? "A sessão expirou. Entre novamente e repita a busca."
          : "A busca falhou antes de chegar a uma resposta. Tente de novo; se persistir, o problema é no servidor, não no código digitado."
      );
      announce(readableError(error), "erro");
    } finally {
      state.busy = false;
      render();
      const physician = document.getElementById("reportPhysician");
      if (physician) {
        physician.focus();
      } else {
        const feedback = document.getElementById("reportLocateFeedback");
        if (feedback) feedback.focus();
      }
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
    // M25.17 — só o que ainda é decisão humana. Origem e unidade saem do
    // exame no servidor; mandá-las daqui reintroduziria a chance de o
    // formulário contradizer o atendimento.
    ["exam_code", "physician_profile_id"].forEach(
      (name) => payload.append(name, form.elements[name].value || "")
    );
    payload.append("file", file);
    state.busy = true;
    announce("Validando, armazenando e atribuindo o PDF…", "");
    render();
    try {
      const result = await client().api("/laudos", {
        method: "POST", body: payload,
      });
      const localizado = state.locatedExam;
      const examCode = localizado && localizado.exam_code;
      state.locatedExam = null;
      state.locateMatches = [];
      setLocateFeedback(
        "ok", `${result.public_code} recebido e atribuído`,
        `O PDF técnico de ${
          localizado ? patientName(localizado) : "o exame"
        }${examCode ? ` (${examCode})` : ""} foi armazenado e o laudo entrou na fila da médica atribuída.`,
        "Acompanhe o estado em “Acompanhamento operacional”."
      );
      announce(`${result.public_code} recebido e atribuído com segurança.`, "ok");
      await loadAuthenticatedData();
    } catch (error) {
      const http = error && error.status ? ` (HTTP ${error.status})` : "";
      setLocateFeedback(
        "erro", "O PDF não foi armazenado",
        `${readableError(error)}${http}`,
        "O exame continua localizado. Corrija o apontamento acima e reenvie."
      );
      announce(readableError(error), "erro");
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
    }
  }

  async function savePhysician(form) {
    const payload = {
      grant_physician_role: form.elements.grant_physician_role.checked,
      professional_name: form.elements.professional_name.value,
      crm_number: form.elements.crm_number.value,
      crm_state: form.elements.crm_state.value,
      rqe: form.elements.rqe.value || null,
      // M25.4 — identificação impressa no laudo. Sem estes dois campos o
      // documento saía sem especialidade e com o CRM em dígitos crus.
      crm_display: form.elements.crm_display.value || null,
      especialidade: form.elements.especialidade.value || null,
      verification_status: form.elements.verification_status.value,
      // M25.11 — o campo EXISTIA no contrato da API e nunca era enviado pelo
      // formulário. Sem ele o servidor recusa "Verificado" com 422, e a tela
      // voltava mostrando "Pendente" — parecendo que o salvamento não
      // persistia, quando na verdade ele era rejeitado.
      verification_reference:
        form.elements.verification_reference.value.trim() || null,
      active: form.elements.active.checked,
    };
    state.busy = true;
    announce("Validando perfil e papel explícito…", "");
    try {
      await client().api(
        `/laudos/admin/medicos/${encodeURIComponent(form.elements.user_id.value)}`,
        { method: "PATCH", body: JSON.stringify(payload) }
      );
      state.profileError = "";
      announce("Perfil médico atualizado.", "ok");
      await loadAuthenticatedData();
    } catch (error) {
      // Também fica FIXO na tela: o toast some em segundos e o operador
      // conclui que salvou.
      state.profileError = readableError(error);
      announce(state.profileError, "erro");
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
    }
  }

  // ------------------------------------------- M25.4 ativo de assinatura

  const SIGNATURE_CONFIRMATION = "ATIVO DE ASSINATURA AUTORIZADO";
  const MAX_SIGNATURE_BYTES = 2 * 1024 * 1024;

  async function loadSignatureAsset(profileId) {
    try {
      state.signatureAsset = await client().api(
        `/laudos/admin/medicos/${encodeURIComponent(profileId)}/assinatura`
      );
    } catch (error) {
      state.signatureAsset = null;
      announce(readableError(error), "erro");
    }
    render();
  }

  async function uploadSignatureAsset(form) {
    const file = form.elements.arquivo.files[0];
    if (!file) {
      announce("Selecione o PNG da assinatura autorizada.", "erro");
      return;
    }
    if (file.size > MAX_SIGNATURE_BYTES) {
      announce("A imagem excede o limite de 2 MiB.", "erro");
      return;
    }
    if (!form.elements.confirmacao_ok.checked) {
      announce("Confirme que o arquivo é o ativo autorizado.", "erro");
      return;
    }
    const profileId = form.elements.physician_profile_id.value;
    const payload = new FormData();
    payload.append("arquivo", file);
    // A frase exata é exigida pela API — a caixa marcada acima é o
    // consentimento humano que autoriza enviá-la.
    payload.append("confirmacao", SIGNATURE_CONFIRMATION);
    state.busy = true;
    announce("Validando e armazenando a assinatura em raiz privada…", "");
    render();
    try {
      await client().api(
        `/laudos/admin/medicos/${encodeURIComponent(profileId)}/assinatura`,
        { method: "POST", body: payload }
      );
      announce("Assinatura cadastrada. A anterior, se havia, foi revogada.", "ok");
      await loadSignatureAsset(profileId);
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
    }
  }

  async function revokeSignatureAsset(profileId) {
    if (!window.confirm(
      "Revogar a assinatura atual?\n\n" +
      "Os laudos JÁ liberados continuam válidos e preservam a imagem que " +
      "usaram. Novos laudos passam a sair sem imagem, apenas com a " +
      "identificação profissional."
    )) return;
    state.busy = true;
    announce("Revogando ativo de assinatura…", "");
    render();
    try {
      await client().api(
        `/laudos/admin/medicos/${encodeURIComponent(profileId)}/assinatura`,
        { method: "DELETE" }
      );
      announce("Assinatura revogada sem apagar o histórico.", "ok");
      await loadSignatureAsset(profileId);
    } catch (error) {
      announce(readableError(error), "erro");
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
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
    } finally {
      // M25.14 — repintar SEMPRE depois de zerar `busy`. Antes o render
      // ficava no `catch`, ou seja, a tela era desenhada AINDA com
      // `busy = true`: os botões nasciam desabilitados e nada repintava
      // depois, então a bancada da médica congelava até um F5.
      state.busy = false;
      render();
    }
  }

  function handleClick(event) {
    // A caixa de seleção do lote é um elemento DENTRO do botão que abre o
    // laudo. Sem tratá-la primeiro, marcar um laudo abriria o documento.
    const pick = event.target.closest("[data-report-batch-pick]");
    if (pick) {
      event.preventDefault();
      event.stopPropagation();
      const id = pick.getAttribute("data-report-batch-pick");
      const indice = state.batchSelection.indexOf(id);
      if (indice >= 0) state.batchSelection.splice(indice, 1);
      else state.batchSelection.push(id);
      render();
      return;
    }
    const button = event.target.closest("button");
    if (!button) return;
    // ---------------------------------------------------------- M25.20
    if (button.matches("[data-signature-all]")) {
      const todos = state.signaturePending.map((item) => item.document_id);
      // O mesmo botão alterna: com tudo marcado, ele limpa. Dois botões
      // separados ocupariam a linha inteira num iPhone.
      state.signatureSelection =
        state.signatureSelection.length === todos.length ? [] : todos;
      render();
      return;
    }
    if (button.matches("[data-signature-download]")) {
      downloadForSignature();
      return;
    }
    if (button.matches("[data-signature-confirm]")) {
      confirmSignedBatch();
      return;
    }
    if (button.matches("[data-signature-discard]")) {
      state.signatureReview = null;
      render();
      return;
    }
    if (button.matches("[data-delivery-filter]")) {
      state.deliveryFilter = button.getAttribute("data-delivery-filter");
      render();
      return;
    }
    if (button.matches("[data-delivery-validate]")) {
      registerExternalValidation(
        button.getAttribute("data-delivery-validate")
      );
      return;
    }
    if (button.matches("[data-delivery-deliver]")) {
      registerDelivery(button.getAttribute("data-delivery-deliver"));
      return;
    }
    if (button.matches("[data-report-batch-all]")) {
      state.batchSelection = visibleQueue()
        .filter((item) => item.status === "assinatura_pendente")
        .map((item) => item.document_id);
      render();
      return;
    }
    if (button.matches("[data-report-batch-none]")) {
      state.batchSelection = [];
      render();
      return;
    }
    if (button.matches("[data-report-batch-download]")) {
      downloadSigningBatch();
      return;
    }
    if (button.matches("[data-report-qualified-open]")) {
      state.confirmQualified = true;
      render();
      return;
    }
    if (button.matches("[data-report-qualified-abort]")) {
      state.confirmQualified = false;
      render();
      return;
    }
    if (button.matches("[data-report-qualified-confirm]")
        || button.matches("[data-report-qualified-retry]")) {
      startQualifiedSignature();
      return;
    }
    if (button.matches("[data-report-qualified-cancel]")) {
      cancelQualifiedSignature();
      return;
    }
    if (button.matches("[data-report-unit]")) {
      const escolha = button.getAttribute("data-report-unit");
      // "__todas" é escolha explícita por ver tudo; guardá-la como string
      // vazia evita cair de volta na tela de seleção a cada render.
      state.unitFilter = escolha === "__todas" ? "" : escolha;
      render();
      return;
    }
    if (button.matches("[data-report-unit-reset]")) {
      state.unitFilter = null;
      // Sair da unidade sem fechar o laudo aberto deixaria em tela um
      // documento que não pertence à unidade que a médica vai escolher.
      state.selectedDocumentId = "";
      state.detail = null;
      render();
      return;
    }
    if (button.matches("[data-report-open]")) {
      loadDocument(button.getAttribute("data-report-open"), true);
      return;
    }
    if (button.matches("[data-report-exam-pick]")) {
      const code = button.getAttribute("data-report-exam-pick");
      const input = document.getElementById("reportExamCode");
      if (input) input.value = code;
      locateExam(code);
      return;
    }
    if (button.matches("[data-report-operational]")) {
      state.selectedOperationalId = button.getAttribute("data-report-operational");
      render();
      const form = document.getElementById("reportReassignForm");
      if (form) form.querySelector("select").focus();
      return;
    }
    if (button.matches("[data-report-signature-status]")) {
      loadSignatureAsset(button.getAttribute("data-report-signature-status"));
      return;
    }
    if (button.matches("[data-report-signature-revoke]")) {
      revokeSignatureAsset(button.getAttribute("data-report-signature-revoke"));
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
    // ------------------------------------------------------------ M25.20
    if (event.target.matches("[data-signature-pick]")) {
      const id = event.target.getAttribute("data-signature-pick");
      const indice = state.signatureSelection.indexOf(id);
      if (indice >= 0) state.signatureSelection.splice(indice, 1);
      else state.signatureSelection.push(id);
      render();
      return;
    }
    if (event.target.id === "reportSignatureUpload") {
      const arquivos = Array.from(event.target.files || []);
      // Zerar o input permite reenviar o MESMO arquivo depois de corrigir
      // algo; sem isso o segundo `change` nunca dispara.
      event.target.value = "";
      uploadSignedForReview(arquivos);
      return;
    }
    if (event.target.id === "reportBatchUpload") {
      const arquivos = Array.from(event.target.files || []);
      event.target.value = "";
      uploadSignedBatch(arquivos);
      return;
    }
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
    } else if (event.target.id === "reportCorrectionForm") {
      openCorrection(event.target);
    } else if (event.target.id === "reportSignatureAssetForm") {
      uploadSignatureAsset(event.target);
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
    // O modo continua sendo lido e guardado: é o que distingue "piloto" de
    // "desabilitado" para qualquer capacidade futura, e o padrão seguro
    // continua sendo "disabled". A M25.18 apenas parou de usá-lo para
    // desenhar uma faixa de alarme permanente.
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
