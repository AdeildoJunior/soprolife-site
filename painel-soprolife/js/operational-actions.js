// SoproLife — M4 Central de Ações Operacionais.
// Função PURA que transforma o payload da Saúde Operacional (M3) em ações
// seguras para o painel. Sem DOM, sem fetch, sem dependência — usada pelo
// app.js (script clássico) e pelos testes locais em Node (module.exports).
//
// Segurança: só os campos conhecidos do alerta são lidos (extras ignorados);
// textos são strings truncadas; qualquer texto com cara de PII/segredo
// (CPF, telefone, e-mail, chaves/tokens, URLs de planilha/Apps Script) é
// substituído por mensagem genérica — nunca aparece bruto. O escape de HTML
// é responsabilidade do render (escapeHtml do app.js), como no resto do painel.

const ACOES_NIVEIS_VALIDOS = ["ok", "atencao", "critico", "desconhecido"];

const ACOES_TEXTO_MAX = 220;

const ACOES_GENERICO = "Detalhe omitido por segurança — abrir a Saúde Operacional (seção Automações) para ver a fonte.";

// Ação recomendada por família de alerta (prefixo do id gerado pelo M3).
const ACOES_RECOMENDADAS = [
  ["ALERTA-PIPELINE-",     "Rodar a atualização local ou verificar o timer da VPS."],
  ["ALERTA-CHECK-ACCESS",  "Interromper qualquer deploy e revisar a segurança."],
  ["ALERTA-JSON-",         "Rodar a atualização de dados de novo e conferir a fonte citada."],
  ["ALERTA-PII-",          "Parar e revisar o arquivo citado antes de seguir usando o painel."],
  ["ALERTA-AUDITORIA-",    "Conferir o card Últimas alterações e a aba Log Auditoria."],
  ["ALERTA-FONTES-",       "Rodar a atualização de dados e aguardar o próximo ciclo."],
];

// Padrões que NUNCA podem aparecer brutos na tela (espelho leve do pii_guard).
const ACOES_SUSPEITO = [
  /\d{3}\.?\d{3}\.?\d{3}-?\d{2}/,            // CPF
  /\(?\d{2}\)?\s?\d{4,5}-?\d{4}/,            // telefone BR
  /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, // e-mail
  /AIza[0-9A-Za-z_-]{10,}/,                  // API key Google
  /ya29\./,                                  // token OAuth
  /AKfycb[A-Za-z0-9_-]{10,}/,                // deployment Apps Script
  /[Bb]earer\s+\S+/,                         // bearer token
  /\/spreadsheets\/d\//,                     // URL de planilha
  /script\.google/i,                         // URL Apps Script
  /private_key|client_secret|refresh_token/i,
];

function acoesTextoSeguro(valor, fallback) {
  if (valor === undefined || valor === null) return fallback;
  let texto = String(valor).replace(/\s+/g, " ").trim();
  if (!texto) return fallback;
  if (ACOES_SUSPEITO.some((rx) => rx.test(texto))) return ACOES_GENERICO;
  if (texto.length > ACOES_TEXTO_MAX) texto = texto.slice(0, ACOES_TEXTO_MAX - 1) + "…";
  return texto;
}

function acaoRecomendadaPara(id) {
  const alvo = String(id || "");
  const hit = ACOES_RECOMENDADAS.find(([prefixo]) => alvo.startsWith(prefixo));
  return hit ? hit[1] : "Revisar o alerta na Saúde Operacional.";
}

// payload da Saúde Operacional (real ou demo) -> array de ações seguras.
// Payload nulo/inválido -> []. Campos extras dos alertas são ignorados.
function buildOperationalActions(payload) {
  if (!payload || typeof payload !== "object") return [];
  const alertas = Array.isArray(payload.alertas) ? payload.alertas : [];
  const geradoEm = (payload.source && typeof payload.source.generatedAt === "string")
    ? payload.source.generatedAt : null;

  return alertas
    .filter((a) => a && typeof a === "object")
    .map((a, idx) => {
      const nivelRaw = String(a.nivel || "").toLowerCase();
      return {
        id: acoesTextoSeguro(a.id, `ACAO-${idx + 1}`),
        nivel: ACOES_NIVEIS_VALIDOS.includes(nivelRaw) ? nivelRaw : "desconhecido",
        titulo: acoesTextoSeguro(a.titulo, "Alerta sem título"),
        acao: acaoRecomendadaPara(a.id),
        proximoPasso: acoesTextoSeguro(
          a.proximo_passo,
          "Sem próximo passo registrado — abrir a Saúde Operacional para detalhes."
        ),
        origem: "Saúde Operacional",
        status: "pendente",
        geradoEm: geradoEm,
      };
    });
}

// Node (testes locais) — no navegador, as funções ficam no escopo global.
if (typeof module === "object" && module.exports) {
  module.exports = { buildOperationalActions, acoesTextoSeguro, acaoRecomendadaPara, ACOES_GENERICO };
}
