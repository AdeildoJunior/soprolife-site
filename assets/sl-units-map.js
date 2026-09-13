/* ==========================================================================
   SOPRO:SL_UNITS_MAP_V1
   Mini-mapa + modal ampliado das unidades (Leaflet + OpenStreetMap).

   Consome as localidades de /assets/sl-booking.js — coordenadas e nomes têm
   uma fonte única. Reage ao evento "sl:booking:location" para destacar o
   ponto da localidade escolhida no formulário.

   Preserva o comportamento original: lazy-load do Leaflet, tiles OpenStreetMap,
   zoom, teclado (Enter/Espaço para abrir), Esc e backdrop para fechar.
   ========================================================================== */
(function (window, document) {
  'use strict';

  var openBtn = document.getElementById('sl-open-map-modal');
  var closeBtn = document.getElementById('sl-close-map-modal');
  var modal = document.getElementById('sl-map-modal');
  var miniNode = document.getElementById('sl-units-map-mini');
  var mapNode = document.getElementById('sl-units-map');
  if (!openBtn || !closeBtn || !modal || !mapNode || !miniNode) return;

  // O botão Ampliar mapa é o controle de teclado; a atribuição é um link próprio.
  miniNode.removeAttribute('role');
  miniNode.removeAttribute('tabindex');
  miniNode.setAttribute('aria-label', 'Mapa das unidades SoproLife');
  var CFG = window.SL_BOOKING;
  if (!CFG) return;

  /* Só entram no mapa as localidades com endereço físico. O atendimento
     domiciliar não tem ponto fixo — nada de endereço inventado. */
  var units = CFG.LOCATIONS.filter(function (loc) { return !!loc.coords; });

  var leafletReady = false;
  var miniReady = false;
  var largeReady = false;
  var miniMap = null;
  var largeMap = null;
  var markers = { mini: {}, large: {} };
  var activeId = CFG.current;
  var lastFocused = null;

  function loadLeafletAssets() {
    if (window.L && window.L.map) { leafletReady = true; return Promise.resolve(); }
    if (window.__slLeafletLoadingPromise) return window.__slLeafletLoadingPromise;

    window.__slLeafletLoadingPromise = new Promise(function (resolve, reject) {
      var css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      css.crossOrigin = '';
      document.head.appendChild(css);

      var script = document.createElement('script');
      script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      script.crossOrigin = '';
      script.onload = function () { leafletReady = true; resolve(); };
      script.onerror = reject;
      document.body.appendChild(script);
    });

    return window.__slLeafletLoadingPromise;
  }

  function showMapFallback() {
    if (miniNode.querySelector('.sl-map-fallback')) return;
    var message = document.createElement('a');
    message.className = 'sl-map-fallback';
    message.href = 'https://www.openstreetmap.org/#map=11/-22.95/-43.27';
    message.target = '_blank'; message.rel = 'noopener';
    message.textContent = 'Não foi possível carregar o mapa. Ver no OpenStreetMap →';
    miniNode.appendChild(message);
  }

  function createTileLayer(target) {
    return window.L.tileLayer(
      'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }
    ).on('tileerror', showMapFallback).addTo(target);
  }

  function markerIcon(isActive) {
    return window.L.divIcon({
      className: 'sl-map-pin-wrap' + (isActive ? ' is-active' : ''),
      html: '<span class="sl-map-pin" aria-hidden="true"></span>',
      iconSize: [20, 20],
      iconAnchor: [10, 20],
      popupAnchor: [0, -18]
    });
  }

  function fitUnits(target, padding, store) {
    var bounds = [];
    units.forEach(function (unit) {
      var marker = window.L.marker([unit.coords.lat, unit.coords.lng], {
        icon: markerIcon(unit.id === activeId),
        title: unit.shortName,
        alt: unit.shortName
      }).addTo(target);
      marker.bindPopup(unit.mapTag
        ? ('<strong>' + unit.shortName + '</strong><br>' + unit.mapTag)
        : unit.shortName);
      store[unit.id] = marker;
      bounds.push([unit.coords.lat, unit.coords.lng]);
    });
    if (bounds.length) target.fitBounds(bounds, { padding: padding });
  }

  /* Destaca (e centraliza) o ponto da localidade escolhida. Para o
     atendimento domiciliar, volta ao enquadramento geral. */
  function highlight(locationId) {
    activeId = locationId || null;

    [['mini', miniMap], ['large', largeMap]].forEach(function (pair) {
      var key = pair[0];
      var map = pair[1];
      if (!map) return;

      var store = markers[key];
      Object.keys(store).forEach(function (id) {
        store[id].setIcon(markerIcon(id === activeId));
      });

      var target = activeId ? store[activeId] : null;
      if (target) {
        map.setView(target.getLatLng(), key === 'large' ? 15 : 14, { animate: !window.matchMedia('(prefers-reduced-motion: reduce)').matches });
        if (key === 'large') target.openPopup();
      } else {
        var bounds = units.map(function (u) { return [u.coords.lat, u.coords.lng]; });
        if (bounds.length) map.fitBounds(bounds, key === 'large'
          ? { padding: [36, 36] }
          : { paddingTopLeft: [24, 24], paddingBottomRight: [24, 100] });
      }
    });

    document.querySelectorAll('[data-map-unit],[data-map-location]').forEach(function (card) {
      var id = card.getAttribute('data-map-location');
      card.classList.toggle('is-selected', !!id && id === locationId);
    });
  }

  function initMiniMapIfNeeded() {
    if (miniReady || !leafletReady || !window.L) return;
    miniMap = window.L.map(miniNode, {
      zoomControl: false,
      attributionControl: true,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      tap: false,
      touchZoom: false
    });
    createTileLayer(miniMap);
    fitUnits(miniMap, [16, 16], markers.mini);
    miniReady = true;
    if (window.ResizeObserver) new ResizeObserver(function () {
      miniMap.invalidateSize({ pan: false });
      highlight(activeId);
    }).observe(miniMap.getContainer());
    highlight(activeId);
  }

  function initLargeMapIfNeeded() {
    if (largeReady || !leafletReady || !window.L) return;
    largeMap = window.L.map(mapNode, {
      scrollWheelZoom: true,
      zoomControl: true,
      attributionControl: true
    });
    createTileLayer(largeMap);
    fitUnits(largeMap, [36, 36], markers.large);
    largeReady = true;
    if (window.ResizeObserver) new ResizeObserver(function () {
      largeMap.invalidateSize({ pan: false });
    }).observe(largeMap.getContainer());
    highlight(activeId);
  }

  function openModal() {
    lastFocused = document.activeElement;
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    loadLeafletAssets().then(function () {
      initMiniMapIfNeeded();
      initLargeMapIfNeeded();
      window.setTimeout(function () { if (largeMap) largeMap.invalidateSize(); }, 90);
    }).catch(showMapFallback);
    window.setTimeout(function () { closeBtn.focus(); }, 40);
  }

  function closeModal() {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  openBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    openModal();
  });
  miniNode.addEventListener('click', function (e) { if (!e.target.closest('a,button')) openModal(); });
  miniNode.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openModal();
    }
  });
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) {
    if (e.target && e.target.getAttribute('data-map-close') === 'backdrop') closeModal();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal.classList.contains('is-open')) closeModal();
  });

  /* ---------------------------------------------------------------------- *
   * Analytics do cartão da unidade parceira                                *
   * Substitui o antigo bloco inline MAP_PASTORE_IPANEMA_TRACK_V1 e passa a  *
   * valer também para a página /espirometria-rio-de-janeiro/.              *
   * window.track() é no-op enquanto o visitante não aceita os cookies.      *
   * ---------------------------------------------------------------------- */
  (function bindPartnerTracking() {
    var ipanema = CFG.byId('pastore-ipanema');
    var bindings = [
      ['[data-sl-pastore-conhecer]', 'click_mapa_pastore_ipanema', { acao: 'conhecer_unidade' }],
      ['[data-sl-pastore-rota]', 'click_rota_pastore_ipanema', { destino: 'google_maps' }],
      ['[data-sl-pastore-wa]', 'click_whatsapp_ipanema', {}],
      ['[data-sl-pastore-agendar]', 'click_agendar_pastore', { destino: 'paciente.centromedicopastore.com.br' }]
    ];

    bindings.forEach(function (entry) {
      document.querySelectorAll(entry[0]).forEach(function (node) {
        node.addEventListener('click', function () {
          var params = CFG.locationParams(ipanema);
          params.origem = 'modal_mapa';
          Object.keys(entry[2]).forEach(function (k) { params[k] = entry[2][k]; });
          CFG.track(entry[1], params);
        });
      });
    });
  })();

  /* Formulário -> mapa */
  document.addEventListener('sl:booking:location', function (event) {
    var id = event && event.detail ? event.detail.id : null;
    var loc = id ? CFG.byId(id) : null;
    highlight(loc && loc.coords ? id : null);
  });

  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          loadLeafletAssets().then(initMiniMapIfNeeded).catch(showMapFallback);
          io.disconnect();
        }
      });
    }, { rootMargin: '120px' });
    io.observe(miniNode);
  } else {
    loadLeafletAssets().then(initMiniMapIfNeeded).catch(showMapFallback);
  }

})(window, document);
