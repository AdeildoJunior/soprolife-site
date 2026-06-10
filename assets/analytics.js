/* =========================================================================
   Sopro Life — Analytics central (GA4 + Meta Pixel) com consentimento LGPD
   -------------------------------------------------------------------------
   >>> EDITE APENAS OS DOIS IDs ABAIXO. <<<
   - Enquanto um ID estiver com o placeholder, ele não é usado (modo seguro).
   - O rastreamento SÓ é ativado depois que o visitante clica "Aceitar" no
     banner de cookies. Antes disso nada externo carrega (nem GA, nem Pixel,
     nem cookies de análise).

   Privacidade: este arquivo NÃO envia nome, telefone, bairro, observações,
   pedido médico nem respostas do funil para o GA4 ou Meta. Apenas page_view
   padrão (todas as páginas) e eventos genéricos do funil (sem dados pessoais).
   ========================================================================= */

window.SITE_ANALYTICS = {
  GA4_ID: "G-Y6K78ZF191",            // GA4 real ativo
  META_PIXEL_ID: "000000000000000",  // placeholder — troque para ativar o Pixel
  consentKey: "sl_consent_v1"        // chave do consentimento (localStorage)
};

(function () {
  var c = window.SITE_ANALYTICS;
  var booted = false;

  function hasRealGA() { return c.GA4_ID && c.GA4_ID !== "G-XXXXXXXXXX"; }
  function hasRealPixel() { return c.META_PIXEL_ID && c.META_PIXEL_ID !== "000000000000000"; }
  function anyReal() { return hasRealGA() || hasRealPixel(); }

  function getConsent() { try { return localStorage.getItem(c.consentKey); } catch (e) { return null; } }
  function setConsent(v) { try { localStorage.setItem(c.consentKey, v); } catch (e) {} }

  /* ---- Carrega as ferramentas (só após consentimento) ---- */
  function boot() {
    if (booted) return;
    booted = true;

    if (hasRealGA()) {
      var s = document.createElement("script");
      s.async = true;
      s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(c.GA4_ID);
      document.head.appendChild(s);
      window.dataLayer = window.dataLayer || [];
      window.gtag = function () { window.dataLayer.push(arguments); };
      window.gtag("js", new Date());
      window.gtag("config", c.GA4_ID); // dispara page_view
    }

    if (hasRealPixel()) {
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
  }

  /* ---- Banner de consentimento (injetado, sem mexer no CSS das páginas) ---- */
  function showBanner() {
    if (document.getElementById("sl-consent")) return;

    var css = ""
      + "#sl-consent{position:fixed;left:16px;right:16px;bottom:16px;z-index:99999;"
      + "max-width:560px;margin:0 auto;background:#0b2239;color:#fff;border-radius:16px;"
      + "padding:18px 20px;box-shadow:0 18px 50px rgba(11,34,57,.35);"
      + "font-family:Inter,system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"
      + "font-size:.92rem;line-height:1.5;animation:slcUp .35s ease both}"
      + "@keyframes slcUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}"
      + "#sl-consent p{margin:0 0 12px}"
      + "#sl-consent .sl-actions{display:flex;gap:10px;flex-wrap:wrap}"
      + "#sl-consent button{flex:1 1 auto;min-width:120px;border:none;border-radius:999px;"
      + "padding:11px 16px;font-weight:800;cursor:pointer;font:inherit;font-weight:800}"
      + "#sl-consent .sl-yes{background:#1eb5ab;color:#fff}"
      + "#sl-consent .sl-no{background:rgba(255,255,255,.14);color:#fff}"
      + "#sl-consent .sl-yes:hover{background:#149b92}"
      + "#sl-consent .sl-no:hover{background:rgba(255,255,255,.22)}"
      + "@media(max-width:480px){#sl-consent .sl-actions{flex-direction:column}}";

    var style = document.createElement("style");
    style.id = "sl-consent-style";
    style.textContent = css;
    document.head.appendChild(style);

    var box = document.createElement("div");
    box.id = "sl-consent";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-live", "polite");
    box.setAttribute("aria-label", "Aviso de cookies");
    box.innerHTML =
      "<p>Usamos cookies para entender como você usa o site e melhorar sua experiência. "
      + "Você decide: só ativamos a análise se você aceitar.</p>"
      + '<div class="sl-actions">'
      + '<button type="button" class="sl-yes">Aceitar</button>'
      + '<button type="button" class="sl-no">Recusar</button>'
      + "</div>";
    document.body.appendChild(box);

    box.querySelector(".sl-yes").addEventListener("click", function () {
      setConsent("granted"); removeBanner(); boot();
    });
    box.querySelector(".sl-no").addEventListener("click", function () {
      setConsent("denied"); removeBanner();
    });
  }

  function removeBanner() {
    var b = document.getElementById("sl-consent");
    var s = document.getElementById("sl-consent-style");
    if (b) b.remove();
    if (s) s.remove();
  }

  /* Permite reabrir o banner depois (ex.: link "Gerenciar cookies" no rodapé) */
  window.SITE_ANALYTICS.openConsent = function () {
    try { localStorage.removeItem(c.consentKey); } catch (e) {}
    if (document.body) showBanner();
  };

  /* ---- Decisão na carga ---- */
  if (!anyReal()) return; // nada a rastrear => sem banner

  var consent = getConsent();
  if (consent === "granted") {
    boot();
  } else if (consent === "denied") {
    /* respeitado: não carrega nada */
  } else {
    if (document.body) showBanner();
    else document.addEventListener("DOMContentLoaded", showBanner);
  }
})();

/* track(evento, params)
   Use SOMENTE para eventos genéricos do funil — nunca passe dados pessoais.
   Páginas comuns não precisam chamar isto: o page_view já é automático.
   Antes do consentimento (gtag/fbq inexistentes) é um no-op silencioso. */
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
