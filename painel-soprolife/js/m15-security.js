/* Guarda de contexto seguro do Núcleo M15 (M15.5A) — módulo reutilizável.
 *
 * Decide, apenas a partir da origem da página (protocol + hostname), se
 * credenciais reais podem ser digitadas e enviadas:
 *   - "https"    → qualquer origem servida por HTTPS (ex.: endereço privado
 *                  publicado via Tailscale Serve). Nenhum hostname de tailnet
 *                  é fixado aqui: HTTPS válido é o critério.
 *   - "localdev" → desenvolvimento local em loopback (localhost, 127.0.0.0/8,
 *                  ::1), mesmo em HTTP puro — o tráfego nunca sai da máquina.
 *   - "blocked"  → QUALQUER outra origem insegura: HTTP em hostname remoto,
 *                  HTTP em IP de Tailscale (100.x é privado, mas NÃO é
 *                  criptografia de página — credencial digitada ali viaja
 *                  sem TLS até o navegador), file:, origem desconhecida.
 *                  Fail-closed: na dúvida, bloqueia.
 *
 * Zero rede, zero storage, zero DOM — puro e testável em Node.
 */
(function (global) {
  "use strict";

  function normalizeHost(hostname) {
    var h = String(hostname == null ? "" : hostname).trim().toLowerCase();
    // IPv6 pode chegar entre colchetes ([::1]) dependendo do navegador.
    if (h.charAt(0) === "[" && h.charAt(h.length - 1) === "]") {
      h = h.slice(1, -1);
    }
    return h;
  }

  // Loopback estrito: localhost, 127.0.0.0/8 e ::1. Nada de rede privada
  // (192.168.x, 10.x, 100.x) — privado não é sinônimo de seguro.
  function isLoopbackHost(hostname) {
    var h = normalizeHost(hostname);
    if (h === "localhost" || h === "::1") return true;
    return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h);
  }

  // Recebe um objeto tipo window.location ({ protocol, hostname }) e devolve
  // { mode: "https"|"localdev"|"blocked", secure: boolean, motivo: string }.
  function classify(loc) {
    if (!loc || typeof loc !== "object") {
      return { mode: "blocked", secure: false, motivo: "origem desconhecida (fail-closed)" };
    }
    var proto = String(loc.protocol || "").toLowerCase();
    if (proto === "https:") {
      return { mode: "https", secure: true, motivo: "conexão HTTPS" };
    }
    if (proto === "http:" && isLoopbackHost(loc.hostname)) {
      return { mode: "localdev", secure: true, motivo: "desenvolvimento local em loopback" };
    }
    return {
      mode: "blocked",
      secure: false,
      motivo: "origem HTTP insegura (" + (normalizeHost(loc.hostname) || "desconhecida") + ")",
    };
  }

  function canAuthenticate(loc) {
    return classify(loc).secure === true;
  }

  // Mensagem única de bloqueio (PT) — sem hostname de tailnet fixado.
  var MENSAGEM_BLOQUEIO =
    "Esta página foi aberta por HTTP em um endereço remoto, então o login do " +
    "Núcleo M15 fica desativado para proteger suas credenciais. Abra o " +
    "endereço HTTPS privado do painel (publicado via Tailscale Serve) para " +
    "entrar com segurança. As áreas antigas do painel continuam disponíveis.";

  var api = {
    classify: classify,
    canAuthenticate: canAuthenticate,
    isLoopbackHost: isLoopbackHost,
    MENSAGEM_BLOQUEIO: MENSAGEM_BLOQUEIO,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (global) global.SoproM15Security = api;
})(typeof window !== "undefined" ? window : null);
