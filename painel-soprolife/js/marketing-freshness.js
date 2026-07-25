// SoproLife — M14.3A.1 Frescor de Marketing & SEO.
// Avalia o snapshot marketing-seo.local.json contra o contrato de frescor
// (core/contracts/freshness-contract.json) e produz estados/rotulos seguros
// para o painel. Espelha scripts/freshness_contract.py.
// Puro: sem fetch, sem DOM, sem rede; relógio injetável (testável em node).

/* eslint-disable no-undef */

const MF_FRESH = "fresh";
const MF_STALE = "stale";
const MF_UNAVAILABLE = "unavailable";
const MF_ERROR = "error";
const MF_AUTH = "authentication_required";
// M21 — credencial durável configurada, faltando apenas a concessão de acesso
// de leitura na propriedade do Google. É pendência de configuração, não login
// vencido: em operação normal com conta de serviço, MF_AUTH não deve aparecer.
const MF_CREDENTIAL = "credential_pending";
// Estado exclusivamente de cliente: uma atualização foi pedida ao servidor e
// ainda não há snapshot novo. Nunca vem do arquivo.
const MF_REFRESHING = "refreshing";
const MF_UNKNOWN = "unknown";

const MF_DEFAULT_STALE_HOURS = 26;

// Ordem de severidade — o pior estado agrega o conjunto.
const MF_SEVERIDADE = [MF_FRESH, MF_STALE, MF_UNKNOWN, MF_UNAVAILABLE, MF_ERROR,
                       MF_CREDENTIAL, MF_AUTH];

// Selo por estado: rótulo exibido + classe CSS. Textos fixos e seguros.
const MF_ROTULOS = {
  [MF_FRESH]:       { label: "Atualizado",               cls: "mf-fresh" },
  [MF_REFRESHING]:  { label: "Atualizando",              cls: "mf-refreshing" },
  [MF_STALE]:       { label: "Dados antigos",            cls: "mf-stale" },
  [MF_AUTH]:        { label: "Reautenticação necessária", cls: "mf-auth" },
  [MF_CREDENTIAL]:  { label: "Credencial/configuração pendente", cls: "mf-credential" },
  [MF_UNAVAILABLE]: { label: "Fonte indisponível",       cls: "mf-unavailable" },
  [MF_ERROR]:       { label: "Falha temporária",         cls: "mf-error" },
  [MF_UNKNOWN]:     { label: "Estado desconhecido",      cls: "mf-unknown" },
};

const MF_FONTES = [
  { key: "searchConsole", id: "search-console", nome: "Search Console" },
  { key: "ga4",           id: "ga4",            nome: "GA4" },
];

// Padrões de erro de autenticação em warnings legados (snapshot v1).
const MF_AUTH_RE = /reauthentication|invalid_grant|insufficient permission|access_token_scope|quota project|application[-_]default/i;

function mfParseIso(valor) {
  if (!valor) return null;
  const ms = Date.parse(String(valor));
  return Number.isFinite(ms) ? ms : null;
}

function mfPiorEstado(estados) {
  let pior = MF_FRESH;
  for (const e of estados) {
    const idx = MF_SEVERIDADE.indexOf(e);
    const atual = MF_SEVERIDADE.indexOf(pior);
    if ((idx === -1 ? MF_SEVERIDADE.indexOf(MF_UNKNOWN) : idx) > atual) pior = e;
  }
  return pior;
}

function mfRotulo(estado) {
  return MF_ROTULOS[estado] || MF_ROTULOS[MF_UNKNOWN];
}

// Sintetiza sourceStatus a partir do formato legado v1 (sem sourceStatus).
function mfSourceStatusLegado(snapshot) {
  const meta = snapshot?.meta || {};
  const sources = meta.sources || {};
  const warnings = Array.isArray(snapshot?.warnings) ? snapshot.warnings : [];
  const out = {};
  for (const f of MF_FONTES) {
    const ok = Boolean(sources[f.key]) && typeof snapshot?.[f.key] === "object";
    let errorCode = null;
    if (!ok) {
      if (meta.configured !== true) {
        errorCode = "NOT_CONFIGURED";
      } else {
        const relevantes = warnings.filter((w) => String(w).toLowerCase().includes(f.nome.toLowerCase()));
        const texto = (relevantes.length ? relevantes : warnings).join(" ");
        errorCode = MF_AUTH_RE.test(texto) ? "AUTH_REQUIRED" : "SYNC_FAILED";
      }
    }
    out[f.key] = {
      sourceId: f.id,
      sourceName: f.nome,
      lastSuccessAt: ok ? meta.generatedAt || null : null,
      lastAttemptAt: meta.generatedAt || null,
      sourceDataThrough: ok ? meta.periodEnd || null : null,
      errorCode,
      errorMessageSafe: errorCode === "AUTH_REQUIRED"
        ? "Reautenticação necessária. Execute a renovação do ADC manualmente."
        : (errorCode ? "Falha de sincronização. Snapshot anterior preservado." : null),
      authenticationRequired: errorCode === "AUTH_REQUIRED",
      publicationRequired: false,
      warnings: [],
    };
  }
  return out;
}

function mfAvaliarFonte(bloco, staleAfterHours, nowMs) {
  const err = bloco?.errorCode || null;
  const successMs = mfParseIso(bloco?.lastSuccessAt);
  const age = successMs === null ? null : Math.max(0, Math.floor((nowMs - successMs) / 1000));

  let estado;
  if (bloco?.credentialPending || err === "CREDENTIAL_PENDING") {
    estado = MF_CREDENTIAL;
  } else if (bloco?.authenticationRequired || err === "AUTH_REQUIRED" || err === "PERMISSION_DENIED") {
    estado = MF_AUTH;
  } else if (["NOT_CONFIGURED", "DEPENDENCY_MISSING", "SOURCE_NOT_FOUND", "NETWORK_BLOCKED"].includes(err)) {
    estado = MF_UNAVAILABLE;
  } else if (successMs === null) {
    estado = err ? MF_ERROR : MF_UNKNOWN;
  } else if (err) {
    estado = MF_ERROR;
  } else {
    const limite = Number(staleAfterHours) > 0 ? Number(staleAfterHours) * 3600 : null;
    estado = (limite !== null && age > limite) ? MF_STALE : MF_FRESH;
  }
  return { status: estado, ageSeconds: age };
}

/**
 * Avaliação completa do snapshot para o painel.
 * @param {object|null} snapshot  conteúdo de marketing-seo.local.json
 * @param {number} [nowMs]        relógio injetável (testes)
 */
function mfAvaliar(snapshot, nowMs) {
  const agora = Number.isFinite(nowMs) ? nowMs : Date.now();
  if (!snapshot || typeof snapshot !== "object" || !snapshot.meta) {
    return { configured: false, overall: MF_UNKNOWN, fontes: {}, period: null,
             generatedAt: null, staleAfterHours: MF_DEFAULT_STALE_HOURS };
  }
  const meta = snapshot.meta;
  const staleAfterHours = Number(meta.staleAfterHours) > 0
    ? Number(meta.staleAfterHours) : MF_DEFAULT_STALE_HOURS;
  const status = (meta.sourceStatus && typeof meta.sourceStatus === "object" &&
                  Object.keys(meta.sourceStatus).length)
    ? meta.sourceStatus
    : mfSourceStatusLegado(snapshot);

  const fontes = {};
  const estados = [];
  for (const f of MF_FONTES) {
    const bloco = status[f.key];
    if (!bloco) continue;
    const aval = mfAvaliarFonte(bloco, staleAfterHours, agora);
    fontes[f.key] = {
      ...bloco,
      ...aval,
      rotulo: mfRotulo(aval.status),
      temDados: typeof snapshot[f.key] === "object" && snapshot[f.key] !== null,
    };
    // Fonte deliberadamente não configurada não rebaixa o agregado
    // quando existe outra fonte configurada.
    if (bloco.errorCode !== "NOT_CONFIGURED") estados.push(aval.status);
  }

  const overall = estados.length
    ? mfPiorEstado(estados)
    : (Object.keys(fontes).length ? MF_UNAVAILABLE : MF_UNKNOWN);

  return {
    configured: meta.configured === true,
    overall,
    rotulo: mfRotulo(overall),
    fontes,
    generatedAt: meta.generatedAt || null,
    lastAttemptAt: meta.lastAttemptAt || meta.generatedAt || null,
    // M21 — tipo de credencial usado na última tentativa (service_account /
    // personal_adc / none). Diagnóstico: nunca identidade, nunca chave.
    credentialKind: meta.credentialKind || null,
    period: (meta.periodStart && meta.periodEnd)
      ? { start: meta.periodStart, end: meta.periodEnd, lookbackDays: meta.lookbackDays || null }
      : null,
    staleAfterHours,
  };
}

// Cadência do serviço agendado (soprolife-update-data.timer): 10 minutos.
const MF_INTERVALO_MINUTOS = 10;

/**
 * Próxima atualização agendada, estimada a partir da última TENTATIVA.
 * A cadência real é do timer; isto é só previsão honesta para a UI.
 * @returns {{iso: string, atrasada: boolean}|null}
 */
function mfProximaAtualizacao(lastAttemptAt, nowMs, intervaloMinutos) {
  const base = mfParseIso(lastAttemptAt);
  if (base === null) return null;
  const passo = (Number(intervaloMinutos) > 0 ? Number(intervaloMinutos)
    : MF_INTERVALO_MINUTOS) * 60000;
  const agora = Number.isFinite(nowMs) ? nowMs : Date.now();
  return { iso: new Date(base + passo).toISOString(), atrasada: base + passo < agora };
}

// Formata ISO UTC como data/hora local curta e segura (sem depender de lib).
function mfFormatarDataHora(iso) {
  const ms = mfParseIso(iso);
  if (ms === null) return "—";
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    MF_FRESH, MF_STALE, MF_UNAVAILABLE, MF_ERROR, MF_AUTH, MF_UNKNOWN,
    MF_CREDENTIAL, MF_REFRESHING, MF_INTERVALO_MINUTOS,
    MF_DEFAULT_STALE_HOURS, MF_FONTES, MF_ROTULOS,
    mfAvaliar, mfAvaliarFonte, mfSourceStatusLegado, mfPiorEstado,
    mfRotulo, mfParseIso, mfFormatarDataHora, mfProximaAtualizacao,
  };
}
