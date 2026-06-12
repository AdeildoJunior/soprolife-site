/* =========================================================================
   Sopro Life — Configuração dos funis
   -------------------------------------------------------------------------
   Aqui ficam apenas as configs específicas do funil.
   Os IDs de GA4 / Meta Pixel ficam centralizados em /assets/analytics.js
   (carregado antes deste arquivo em cada página do funil).
   ========================================================================= */

window.FUNNEL_CONFIG = {
  /* WhatsApp da equipe (somente números, com DDI 55) */
  whatsapp: "5521998901775",

  /* (Opcional) Salvar cada lead numa planilha do Google Sheets via Apps Script.
     Este endpoint é o SEU banco de leads — é onde nome/telefone devem ir.
     NÃO é enviado para GA4 nem Meta. Deixe "" para usar só o WhatsApp.
     Tutorial no README.md. */
  leadEndpoint: "",

  /* Telefone exibido no rodapé (apenas visual) */
  phoneDisplay: "(21) 99890-1775"
};
