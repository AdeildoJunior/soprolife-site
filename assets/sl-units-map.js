/* M26.11 — Mapas públicos SoproLife: base clara, pins e lista sincronizados.
   OSM sem chave; CARTO exige autenticação desde agosto/2026 (ver relatório).
   Mesma implementação na home, Espirometria RJ e Ipanema.
   Nomes/coordenadas/agenda vêm exclusivamente de SL_BOOKING. */
(function (window, document) {
  'use strict';
  var CFG = window.SL_BOOKING;
  var ipanemaOnly = !!document.getElementById('sl-ip-map');
  var openBtn = document.getElementById(ipanemaOnly ? 'sl-ip-open-map' : 'sl-open-map-modal');
  var closeBtn = document.getElementById(ipanemaOnly ? 'sl-ip-close-map' : 'sl-close-map-modal');
  var modal = document.getElementById(ipanemaOnly ? 'sl-ip-map-modal' : 'sl-map-modal');
  var miniNode = document.getElementById(ipanemaOnly ? 'sl-ip-map-mini-canvas' : 'sl-units-map-mini');
  var mapNode = document.getElementById(ipanemaOnly ? 'sl-ip-map' : 'sl-units-map');
  if (!CFG || !openBtn || !closeBtn || !modal || !miniNode || !mapNode) return;

  var units = CFG.LOCATIONS.filter(function (unit) {
    return unit.coords && (!ipanemaOnly || unit.id === 'pastore-ipanema');
  });
  var maps = {}, markers = { mini: {}, large: {} }, rows = {};
  var activeId = ipanemaOnly ? 'pastore-ipanema' : null;
  var lastFocused, previousOverflow, partnerDetails, status;
  var info = modal.querySelector('.sl-map-modal__info');
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  document.body.appendChild(modal); // Escapa dos containers/stacking contexts legados.
  modal.classList.add('sl-map-calm-modal');
  miniNode.classList.add('sl-map-calm');
  mapNode.classList.add('sl-map-calm');
  miniNode.removeAttribute('role');
  miniNode.removeAttribute('tabindex');
  miniNode.removeAttribute('aria-hidden');
  miniNode.setAttribute('aria-label', 'Localização das unidades SoproLife');

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function routeUrl(unit) {
    return unit.partner ? CFG.PASTORE_ROUTE_URL :
      'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(unit.address || (unit.coords.lat + ',' + unit.coords.lng));
  }
  function waUrl(unit) {
    var service = document.getElementById('sl-booking-service');
    var date = document.getElementById('sl-booking-date');
    var slot = document.querySelector('.sl-slot-btn[aria-pressed="true"]');
    return CFG.whatsappUrl(CFG.whatsappText(unit, service ? service.value : 'Espirometria',
      date ? date.value : '', slot ? slot.getAttribute('data-time') : ''));
  }
  function action(text, href) {
    var a = el('a', '', text); a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a;
  }
  function unitSummary(unit) {
    return unit.address || 'Espaço parceiro · endereço confirmado no agendamento';
  }
  function popup(unit) {
    var card = el('div', 'sl-map-popup');
    card.appendChild(el('span', 'sl-map-popup__eyebrow', unit.partner ? 'Unidade parceira' : 'SoproLife · Espirometria'));
    card.appendChild(el('strong', 'sl-map-popup__title', unit.shortName));
    card.appendChild(el('p', '', unitSummary(unit)));
    var links = el('div', 'sl-map-popup__links');
    links.appendChild(action('Como chegar ↗', routeUrl(unit)));
    var wa = action('WhatsApp ↗', waUrl(unit));
    wa.addEventListener('click', function () { wa.href = waUrl(unit); if (unit.partner) CFG.track('click_whatsapp_ipanema', CFG.locationParams(unit)); });
    links.appendChild(wa); card.appendChild(links);
    return card;
  }
  function markerIcon(selected, index) {
    // Número visível liga o pin à lista; a seleção também tem um anel branco.
    return window.L.divIcon({
      className: 'sl-calm-marker' + (selected ? ' is-active' : ''),
      html: '<span class="sl-calm-marker__pin"><span>' + (index + 1) + '</span></span>',
      iconSize: [44, 48], iconAnchor: [22, 44], popupAnchor: [0, -38]
    });
  }
  function syncSidebar() {
    Object.keys(rows).forEach(function (id) {
      var selected = id === activeId;
      rows[id].setAttribute('aria-pressed', String(selected));
      rows[id].closest('.sl-map-unit-row').classList.toggle('is-selected', selected);
    });
    if (partnerDetails) {
      partnerDetails.hidden = activeId !== 'pastore-ipanema';
      if (partnerDetails.hidden) partnerDetails.open = false;
    }
    if (status) status.textContent = activeId ? CFG.byId(activeId).shortName + ' selecionada.' : 'Todas as unidades no mapa.';
  }
  function frame(key, animate) {
    var map = maps[key]; if (!map) return;
    var selected = activeId && markers[key][activeId];
    if (selected) {
      // Zoom de bairro; o popup só aparece por ação explícita no pin.
      map.setView(selected.getLatLng(), ipanemaOnly ? 15 : 14, { animate: !!animate && !reduced.matches });
    } else {
      map.fitBounds(units.map(function (u) { return [u.coords.lat, u.coords.lng]; }), {
        paddingTopLeft: [44, 50], paddingBottomRight: [44, key === 'mini' ? 104 : 50],
        maxZoom: 13, animate: false
      });
    }
  }
  function highlight(id, options) {
    options = options || {};
    activeId = units.some(function (u) { return u.id === id; }) ? id : null;
    ['mini', 'large'].forEach(function (key) {
      if (!maps[key]) return;
      units.forEach(function (unit, i) {
        var marker = markers[key][unit.id];
        marker.setIcon(markerIcon(unit.id === activeId, i));
        marker.setZIndexOffset(unit.id === activeId ? 500 : 0);
        marker.getElement().setAttribute('aria-label', unit.shortName + (unit.id === activeId ? ' — selecionada' : ' — ver unidade'));
      });
      maps[key].closePopup();
      frame(key, options.animate);
    });
    syncSidebar();
    if (options.booking && activeId) {
      // A seleção é compartilhada com o formulário, sem disparar WhatsApp.
      document.dispatchEvent(new CustomEvent('sl:booking:select-location', { detail: { id: activeId } }));
    }
  }
  function buildSidebar() {
    // Preserva os links/explicações da parceira, apresentados sob demanda.
    var oldPartner = info.querySelector('.sl-map-partner-card');
    var oldContent = ipanemaOnly ? Array.from(info.childNodes) : oldPartner ? Array.from(oldPartner.childNodes) : [];
    var oldLinks = oldPartner ? Array.from(oldPartner.querySelectorAll('a')) : [];
    info.replaceChildren();
    info.appendChild(el('span', 'sl-map-sidebar-kicker', 'RIO DE JANEIRO'));
    info.appendChild(el('h4', 'sl-map-sidebar-title', ipanemaOnly ? 'Unidade Ipanema' : 'Encontre sua unidade'));
    info.appendChild(el('p', 'sl-map-sidebar-intro', 'Escolha uma unidade para ver no mapa.'));
    var list = el('div', 'sl-map-unit-list');
    units.forEach(function (unit, index) {
      var row = el('div', 'sl-map-unit-row');
      var button = el('button', 'sl-map-unit-select');
      button.type = 'button'; button.dataset.mapLocation = unit.id;
      button.setAttribute('aria-pressed', 'false');
      button.appendChild(el('span', 'sl-map-unit-number', String(index + 1)));
      var copy = el('span', 'sl-map-unit-copy');
      copy.appendChild(el('strong', '', unit.shortName));
      copy.appendChild(el('span', '', unit.mapSummary || (unit.partner ? 'Rua Teixeira de Melo, 54 · Ipanema' : 'Espaço parceiro · Zona Norte')));
      button.appendChild(copy);
      button.addEventListener('click', function () { highlight(unit.id, { booking: true, animate: true }); });
      rows[unit.id] = button;
      row.appendChild(button);
      var actions = el('div', 'sl-map-unit-actions');
      actions.appendChild(action('Como chegar ↗', routeUrl(unit)));
      var wa = action('WhatsApp ↗', waUrl(unit));
      wa.addEventListener('click', function () { wa.href = waUrl(unit); if (unit.partner) CFG.track('click_whatsapp_ipanema', CFG.locationParams(unit)); });
      actions.appendChild(wa); row.appendChild(actions); list.appendChild(row);
    });
    info.appendChild(list);
    if (oldContent.length) {
      partnerDetails = el('details', 'sl-map-partner-details');
      partnerDetails.appendChild(el('summary', '', 'Sobre o atendimento em Ipanema'));
      var content = el('div', 'sl-map-partner-details__content');
      // Texto institucional original; links mantidos com seus destinos exatos.
      oldContent.forEach(function (node) { content.appendChild(node); });
      partnerDetails.appendChild(content); info.appendChild(partnerDetails);
      oldLinks.forEach(function (link) { link.className = ''; });
    }
    if (!ipanemaOnly) {
      var home = el('p', 'sl-map-home-note', 'Prefere fazer em casa? ');
      var selectHome = el('button', '', 'Atendimento domiciliar'); selectHome.type = 'button';
      selectHome.addEventListener('click', function () {
        highlight(null);
        document.dispatchEvent(new CustomEvent('sl:booking:select-location', { detail: { id: 'domiciliar' } }));
      });
      home.appendChild(selectHome); info.appendChild(home);
      info.appendChild(el('p', 'sl-map-address-note', 'O endereço da unidade Zona Norte é confirmado no agendamento.'));
    }
    status = el('p', 'sl-map-selection-status'); status.setAttribute('role', 'status');
    info.appendChild(status);
    var overview = el('button', 'sl-map-overview', 'Ver todas as unidades'); overview.type = 'button';
    overview.addEventListener('click', function () { highlight(ipanemaOnly ? 'pastore-ipanema' : null); });
    modal.querySelector('.sl-map-modal__header').insertBefore(overview, closeBtn);
    syncSidebar();
  }
  function loadLeaflet() {
    if (window.L && window.L.map) return Promise.resolve();
    if (window.__slLeafletLoadingPromise) return window.__slLeafletLoadingPromise;
    window.__slLeafletLoadingPromise = new Promise(function (resolve, reject) {
      var css = el('link'); css.rel = 'stylesheet'; css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(css);
      var js = el('script'); js.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
      js.onload = resolve; js.onerror = reject; document.body.appendChild(js);
    }).catch(function (error) { window.__slLeafletLoadingPromise = null; throw error; });
    return window.__slLeafletLoadingPromise;
  }
  function showFallback(node) {
    if (node.querySelector('.sl-map-fallback')) return;
    var message = action('Mapa indisponível. Ver no OpenStreetMap ↗', 'https://www.openstreetmap.org/#map=11/-22.95/-43.27');
    message.className = 'sl-map-fallback'; node.appendChild(message);
  }
  function initMap(key) {
    if (maps[key]) return;
    var node = key === 'mini' ? miniNode : mapNode;
    var map = window.L.map(node, {
      zoomControl: key === 'large', attributionControl: true,
      scrollWheelZoom: key === 'large', dragging: key === 'large', touchZoom: key === 'large',
      doubleClickZoom: key === 'large', boxZoom: key === 'large', keyboard: true
    });
    maps[key] = map;
    // O tratamento cromático vive SOMENTE no tile pane (sl-maps-calm.css).
    window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).on('tileerror', function () { showFallback(node); }).addTo(map);
    units.forEach(function (unit, index) {
      var marker = window.L.marker([unit.coords.lat, unit.coords.lng], {
        icon: markerIcon(unit.id === activeId, index), title: unit.shortName, alt: unit.shortName,
        riseOnHover: true, keyboard: true
      }).addTo(map);
      marker.bindPopup(function () { return popup(unit); }, { maxWidth: 250, minWidth: 180, autoPanPadding: [20, 24] });
      marker.on('click', function () {
        highlight(unit.id, { booking: true });
        // Reabre após sincronizar o formulário e mantém a seleção visível.
        marker.openPopup();
        if (rows[unit.id]) rows[unit.id].scrollIntoView({ block: 'nearest', inline: 'nearest' });
      });
      markers[key][unit.id] = marker;
    });
    frame(key, false);
    units.forEach(function (unit) {
      markers[key][unit.id].getElement().setAttribute('aria-label', unit.shortName + (unit.id === activeId ? ' — selecionada' : ' — ver unidade'));
    });
    if (window.ResizeObserver) new ResizeObserver(function () {
      if (!node.getClientRects().length) return;
      map.invalidateSize({ pan: false }); frame(key, false);
    }).observe(node);
  }
  function openModal() {
    lastFocused = document.activeElement;
    previousOverflow = document.body.style.overflow;
    modal.classList.add('is-open'); modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    closeBtn.focus();
    loadLeaflet().then(function () {
      initMap('large');
      requestAnimationFrame(function () { maps.large.invalidateSize({ pan: false }); frame('large', false); });
    }).catch(function () { showFallback(mapNode); });
  }
  function closeModal() {
    modal.classList.remove('is-open'); modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = previousOverflow || '';
    if (lastFocused) lastFocused.focus();
  }
  buildSidebar();
  // Mantém a medição dos destinos da parceira, sem dados pessoais.
  modal.querySelectorAll('[data-sl-pastore-agendar],[data-sl-pastore-rota],[data-sl-pastore-conhecer]').forEach(function (a) {
    a.addEventListener('click', function () {
      var name = a.hasAttribute('data-sl-pastore-agendar') ? 'click_agendar_pastore'
        : a.hasAttribute('data-sl-pastore-rota') ? 'click_rota_pastore_ipanema' : 'click_mapa_pastore_ipanema';
      CFG.track(name, CFG.locationParams(CFG.byId('pastore-ipanema')));
    });
  });
  openBtn.addEventListener('click', function (event) { event.stopPropagation(); openModal(); });
  // Pins abrem o próprio popup; só o fundo funciona como atalho de ampliação.
  miniNode.addEventListener('click', function (event) {
    if (!event.target.closest('a,button,.leaflet-marker-icon,.leaflet-popup')) openModal();
  });
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', function (event) {
    if (event.target.getAttribute('data-map-close') === 'backdrop') closeModal();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal.classList.contains('is-open')) closeModal();
  });
  document.addEventListener('sl:booking:location', function (event) { highlight(event.detail.id); });
  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      if (!entries.some(function (entry) { return entry.isIntersecting; })) return;
      loadLeaflet().then(function () { initMap('mini'); }).catch(function () { showFallback(miniNode); });
      observer.disconnect();
    }, { rootMargin: '120px' }); observer.observe(miniNode);
  } else {
    loadLeaflet().then(function () { initMap('mini'); }).catch(function () { showFallback(miniNode); });
  }
})(window, document);
