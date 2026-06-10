/* =========================================================================
   Sopro Life — Configuração central dos funis
   -------------------------------------------------------------------------
   Edite SOMENTE este arquivo para ligar as integrações.
   Todos os funis (/funil/, /funil/espirometria/, etc.) usam estas configs.
   ========================================================================= */

window.FUNNEL_CONFIG = {
  /* WhatsApp da equipe (somente números, com DDI 55) */
  whatsapp: "5521999417192",

  /* (Opcional) Salvar cada lead numa planilha do Google Sheets.
     Crie um Google Apps Script publicado como Web App e cole a URL aqui.
     Deixe "" para funcionar só com WhatsApp.
     Tutorial rápido no README.md. */
  leadEndpoint: "",

  /* (Opcional) Google Analytics 4 — ex.: "G-XXXXXXXXXX" */
  ga4Id: "",

  /* (Opcional) Meta Pixel (Facebook/Instagram) — ex.: "123456789012345" */
  metaPixelId: "",

  /* Telefone exibido no rodapé (apenas visual) */
  phoneDisplay: "(21) 99941-7192"
};

/* =========================================================================
   Bootstrap de GA4 e Meta Pixel + função global track()
   Não precisa editar daqui para baixo.
   ========================================================================= */
(function () {
  var c = window.FUNNEL_CONFIG || {};

  /* ---- Google Analytics 4 ---- */
  if (c.ga4Id) {
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(c.ga4Id);
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", c.ga4Id);
  }

  /* ---- Meta Pixel ---- */
  if (c.metaPixelId) {
    !function (f, b, e, v, n, t, s) {
      if (f.fbq) return; n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = "2.0";
      n.queue = []; t = b.createElement(e); t.async = !0;
      t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
    }(window, document, "script", "https://connect.facebook.net/en_US/fbevents.js");
    window.fbq("init", c.metaPixelId);
    window.fbq("track", "PageView");
  }
})();

/* track(evento, parametros)
   - GA4: dispara um evento custom (aparece em Relatórios > Engajamento > Eventos)
   - Meta: mapeia para eventos padrão (Lead, Contact) quando faz sentido */
window.track = function (event, params) {
  params = params || {};
  try { if (window.gtag) window.gtag("event", event, params); } catch (e) {}
  try {
    if (window.fbq) {
      if (event === "funnel_lead") window.fbq("track", "Lead", params);
      else if (event === "funnel_whatsapp_click") window.fbq("track", "Contact", params);
      else if (event === "funnel_start") window.fbq("trackCustom", "FunnelStart", params);
      else window.fbq("trackCustom", event, params);
    }
  } catch (e) {}
};
