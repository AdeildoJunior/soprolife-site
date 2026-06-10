/* =========================================================================
   Sopro Life — Analytics central (GA4 + Meta Pixel)
   -------------------------------------------------------------------------
   >>> EDITE APENAS OS DOIS IDs ABAIXO. <<<
   Enquanto estiverem com o valor placeholder, NADA é carregado (modo seguro):
   nenhum script externo, nenhum cookie, nenhum evento. Troque pelos IDs reais
   para ativar.

   Privacidade: este arquivo NÃO envia nome, telefone, bairro, observações,
   pedido médico nem respostas do funil para o GA4 ou Meta. Apenas page_view
   padrão (todas as páginas) e eventos genéricos do funil (sem dados pessoais).
   ========================================================================= */

window.SITE_ANALYTICS = {
  GA4_ID: "G-XXXXXXXXXX",          // ex.: "G-ABC1234567"
  META_PIXEL_ID: "000000000000000" // ex.: "481234567890123"
};

(function () {
  var c = window.SITE_ANALYTICS;
  var hasGA = c.GA4_ID && c.GA4_ID !== "G-XXXXXXXXXX";
  var hasPixel = c.META_PIXEL_ID && c.META_PIXEL_ID !== "000000000000000";

  /* ---- Google Analytics 4 (dispara page_view automaticamente) ---- */
  if (hasGA) {
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(c.GA4_ID);
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", c.GA4_ID);
  }

  /* ---- Meta Pixel (dispara PageView automaticamente) ---- */
  if (hasPixel) {
    !function (f, b, e, v, n, t, s) {
      if (f.fbq) return; n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = "2.0";
      n.queue = []; t = b.createElement(e); t.async = !0;
      t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
    }(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
    window.fbq("init", c.META_PIXEL_ID);
    window.fbq("track", "PageView");
  }
})();

/* track(evento, params)
   Use SOMENTE para eventos genéricos do funil — nunca passe dados pessoais.
   Páginas comuns não precisam chamar isto: o page_view já é automático.
   Se os IDs forem placeholders, é um no-op silencioso. */
window.track = function (event, params) {
  params = params || {};
  try { if (window.gtag) window.gtag("event", event, params); } catch (e) {}
  try {
    if (window.fbq) {
      if (event === "funnel_complete") window.fbq("track", "Lead");
      else if (event === "whatsapp_click") window.fbq("track", "Contact");
      else window.fbq("trackCustom", event, params);
    }
  } catch (e) {}
};
