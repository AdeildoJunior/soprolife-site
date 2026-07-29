/* ==========================================================================
   SOPRO:SL_BOOKING_SHARED_V1
   Configuração e lógica ÚNICAS do agendamento da SoproLife.

   Carregado por:
     /index.html
     /espirometria-rio-de-janeiro/index.html
     /espirometria-domiciliar-rio-de-janeiro/index.html

   Regras importantes:
   - Domiciliar, Unidade Barra e Unidade Zona Norte compartilham a MESMA
     referência de agenda (SOPROLIFE_SCHEDULE). Não existem listas paralelas.
   - Pastore Ipanema tem agenda própria (terça e sábado, 8h–12h, 30 em 30 min),
     representando a agenda habitual — nunca disponibilidade em tempo real.
   - Nenhum dado pessoal do paciente é enviado ao Analytics.
   ========================================================================== */
(function (window, document) {
  'use strict';

  /* ---------------------------------------------------------------------- *
   * 1. Constantes                                                          *
   * ---------------------------------------------------------------------- */

  var WHATSAPP_PHONE = '5521998901775';

  var PASTORE_BOOKING_URL =
    'https://paciente.centromedicopastore.com.br/exams' +
    '?utm_source=soprolife&utm_medium=referral&utm_campaign=espirometria_ipanema';

  var PASTORE_ROUTE_URL =
    'https://www.google.com/maps/dir/?api=1&destination=' +
    encodeURIComponent('Rua Teixeira de Melo, 54, Ipanema, Rio de Janeiro - RJ');

  var PASTORE_ADDRESS = 'Rua Teixeira de Melo, 54 — Ipanema, Rio de Janeiro/RJ';
  var PASTORE_REFERENCE = 'Em frente ao acesso C do metrô General Osório.';

  /* Mensagem exata pedida pela operação para a unidade parceira. */
  var PASTORE_WHATSAPP_TEXT =
    'Olá! Vim pelo site da SoproLife e gostaria de orientação para agendar ' +
    'uma espirometria na unidade Pastore Ipanema.';

  var PASTORE_STEPS = [
    'Clique em “Outros”.',
    'Selecione “Espirometria”.',
    'Escolha com ou sem broncodilatador, conforme o pedido médico.',
    'Selecione Ipanema.',
    'Escolha a opção interna de espirometria da SoproLife.',
    'Confirme data e horário.'
  ];

  /* Janela de datas aceitas no formulário (a partir de hoje). */
  var DATE_WINDOW_DAYS = 30;

  /* ==========================================================================
     SOPRO:COORD_PASTORE_IPANEMA  —  ÚNICO PONTO DE EDIÇÃO DA COORDENADA
     --------------------------------------------------------------------------
     Rua Teixeira de Melo, 54 — Ipanema, Rio de Janeiro/RJ.
     Coordenada CONFIRMADA no Google Maps pelo operador em 2026-07-28.
     A mesma constante existe em /espirometria-ipanema/index.html
     (procure por SOPRO:COORD_PASTORE_IPANEMA).
     ========================================================================== */
  var COORD_PASTORE_IPANEMA = { lat: -22.983686, lng: -43.198411 };

  /* ---------------------------------------------------------------------- *
   * 2. Agendas                                                             *
   * ---------------------------------------------------------------------- */

  /* Agenda padrão da SoproLife. Preservada exatamente como estava no código
     original do atendimento domiciliar: qualquer dia dentro da janela de 30
     dias e estes oito horários. Barra e Zona Norte apontam para ESTE mesmo
     objeto — alterar aqui altera as três localidades de uma vez. */
  var SOPROLIFE_SCHEDULE = {
    id: 'soprolife-padrao',
    /* null = todos os dias da semana dentro da janela de datas */
    weekdays: null,
    slots: ['08:00', '09:00', '10:00', '11:00', '13:00', '14:00', '15:00', '16:00'],
    daysLabel: '',
    hoursLabel: '',
    note: 'Horários sujeitos a confirmação pelo WhatsApp.'
  };

  /* Agenda da unidade parceira. 0 = domingo … 2 = terça … 6 = sábado. */
  var PASTORE_SCHEDULE = {
    id: 'pastore-ipanema',
    weekdays: [2, 6],
    slots: ['08:00', '08:30', '09:00', '09:30', '10:00', '10:30',
            '11:00', '11:30', '12:00'],
    daysLabel: 'terças-feiras e sábados',
    hoursLabel: 'das 8h às 12h',
    headline: 'Espirometria em Ipanema às terças-feiras e aos sábados, das 8h às 12h.',
    note: 'Disponibilidade sujeita à confirmação no sistema oficial de agendamento da Pastore.'
  };

  /* ---------------------------------------------------------------------- *
   * 3. Localidades (fonte única — ordem do <select> vem daqui)             *
   * ---------------------------------------------------------------------- */

  var LOCATIONS = [
    {
      id: 'domiciliar',
      value: 'Atendimento domiciliar',
      label: 'Atendimento domiciliar',
      shortName: 'Atendimento domiciliar',
      bookingMethod: 'soprolife_whatsapp',
      schedule: SOPROLIFE_SCHEDULE,
      coords: null,
      mapTag: 'Endereço combinado pelo WhatsApp',
      partner: false
    },
    {
      id: 'barra',
      value: 'Unidade Barra',
      label: 'Unidade Barra',
      shortName: 'Unidade Barra',
      bookingMethod: 'soprolife_whatsapp',
      schedule: SOPROLIFE_SCHEDULE,
      coords: { lat: -22.999051350996826, lng: -43.35229531126693 },
      mapTag: 'Espaço parceiro / coworking',
      partner: false
    },
    {
      id: 'zona-norte',
      value: 'Unidade Zona Norte',
      label: 'Unidade Zona Norte',
      shortName: 'Unidade Zona Norte',
      bookingMethod: 'soprolife_whatsapp',
      schedule: SOPROLIFE_SCHEDULE,
      coords: { lat: -22.88608548571967, lng: -43.28447232933152 },
      mapTag: 'Espaço parceiro / coworking',
      partner: false
    },
    {
      id: 'pastore-ipanema',
      value: 'Centro Médico Pastore — Ipanema',
      label: 'Pastore Ipanema — Unidade parceira',
      shortName: 'Centro Médico Pastore — Ipanema',
      bookingMethod: 'pastore_sistema_oficial',
      schedule: PASTORE_SCHEDULE,
      coords: COORD_PASTORE_IPANEMA,
      address: PASTORE_ADDRESS,
      reference: PASTORE_REFERENCE,
      mapTag: 'Unidade parceira · ' + PASTORE_ADDRESS,
      partner: true
    }
  ];

  function byId(id) {
    for (var i = 0; i < LOCATIONS.length; i++) {
      if (LOCATIONS[i].id === id) return LOCATIONS[i];
    }
    return null;
  }

  function byValue(value) {
    for (var i = 0; i < LOCATIONS.length; i++) {
      if (LOCATIONS[i].value === value) return LOCATIONS[i];
    }
    return null;
  }

  /* ---------------------------------------------------------------------- *
   * 4. Datas (America/Sao_Paulo)                                           *
   * ---------------------------------------------------------------------- */

  /* Data de hoje no fuso da operação, em ISO (YYYY-MM-DD). Sem depender do
     relógio/fuso do visitante — nada de data fixa no HTML. */
  function todayIsoSaoPaulo() {
    try {
      return new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/Sao_Paulo',
        year: 'numeric', month: '2-digit', day: '2-digit'
      }).format(new Date());
    } catch (e) {
      var d = new Date();
      return toIso(new Date(d.getFullYear(), d.getMonth(), d.getDate()));
    }
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function toIso(date) {
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  /* ISO -> Date local (meia-noite), sem escorregar de dia por fuso. */
  function parseIso(iso) {
    if (!iso) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso).trim());
    if (!m) return null;
    var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return isNaN(d.getTime()) ? null : d;
  }

  function addDaysIso(iso, days) {
    var d = parseIso(iso);
    if (!d) return iso;
    d.setDate(d.getDate() + days);
    return toIso(d);
  }

  function formatBr(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    return m ? (m[3] + '/' + m[2] + '/' + m[1]) : (iso || '');
  }

  var WEEKDAY_NAMES = ['domingo', 'segunda-feira', 'terça-feira', 'quarta-feira',
                       'quinta-feira', 'sexta-feira', 'sábado'];

  /* "às terças-feiras e sábados, das 8h às 12h" */
  function scheduleLabel(location) {
    var s = location.schedule;
    if (!s.daysLabel) return '';
    return 'às ' + s.daysLabel + (s.hoursLabel ? ', ' + s.hoursLabel : '');
  }

  /* Resultado da validação data × localidade. */
  function validateDate(location, iso, minIso, maxIso) {
    if (!location) return { ok: false, reason: 'no-location', hint: '', message: 'Escolha uma localidade.' };

    if (!iso) {
      return {
        ok: false,
        reason: 'empty',
        hint: '',
        message: location.schedule.weekdays
          ? 'Escolha uma data: em ' + location.shortName + ' a espirometria acontece ' +
            scheduleLabel(location) + '.'
          : 'Escolha uma data para ver os horários.'
      };
    }

    var date = parseIso(iso);
    if (!date) return { ok: false, reason: 'invalid', hint: 'Data inválida.',
                        message: 'Data inválida. Use o calendário para escolher o dia.' };

    var min = parseIso(minIso);
    var max = parseIso(maxIso);

    if (min && date < min) {
      return { ok: false, reason: 'past', hint: 'Data já passada.',
               message: 'Essa data já passou. Escolha a partir de ' + formatBr(minIso) + '.' };
    }
    if (max && date > max) {
      return { ok: false, reason: 'far', hint: 'Fora da janela de agendamento.',
               message: 'Para datas depois de ' + formatBr(maxIso) + ', fale com a SoproLife pelo WhatsApp.' };
    }

    var weekdays = location.schedule.weekdays;
    if (weekdays && weekdays.indexOf(date.getDay()) === -1) {
      return {
        ok: false,
        reason: 'weekday',
        hint: 'Dia sem atendimento nesta localidade.',
        message: 'Não há espirometria em ' + WEEKDAY_NAMES[date.getDay()] + ' na unidade ' +
                 location.shortName + '. O exame acontece ' + scheduleLabel(location) + '.'
      };
    }

    return { ok: true, reason: 'ok', hint: '', message: '' };
  }

  /* Dia permitido para a localidade (usado pelo calendário). */
  function isWeekdayAllowed(location, date) {
    if (!location || !location.schedule.weekdays) return true;
    return location.schedule.weekdays.indexOf(date.getDay()) !== -1;
  }

  /* ---------------------------------------------------------------------- *
   * 5. Analytics (sem dado pessoal)                                        *
   * ---------------------------------------------------------------------- */

  function track(eventName, params) {
    try {
      if (window.track) window.track(eventName, params || {});
    } catch (e) { /* no-op antes do consentimento */ }
  }

  function pagePath() {
    try { return window.location.pathname; } catch (e) { return ''; }
  }

  function locationParams(location) {
    return {
      location_id: location ? location.id : '',
      location_name: location ? location.shortName : '',
      booking_method: location ? location.bookingMethod : '',
      page_path: pagePath()
    };
  }

  /* ---------------------------------------------------------------------- *
   * 6. WhatsApp                                                            *
   * ---------------------------------------------------------------------- */

  function whatsappUrl(text) {
    return 'https://api.whatsapp.com/send?phone=' + WHATSAPP_PHONE +
           '&text=' + encodeURIComponent(text);
  }

  /* Mensagem por localidade. Ipanema tem texto próprio (o agendamento é
     administrado pela Pastore); as demais preservam o fluxo original. */
  function whatsappText(location, service, iso, time) {
    var lines;

    if (location && location.partner) {
      lines = [PASTORE_WHATSAPP_TEXT];
      if (service) lines.push('Tipo de espirometria: ' + service);
      if (iso) lines.push('Data de interesse: ' + formatBr(iso));
      if (time) lines.push('Horário de interesse: ' + time);
      return lines.join('\n');
    }

    lines = ['Olá, gostaria de agendar espirometria na Sopro Life.'];
    if (service) lines.push('Tipo de espirometria: ' + service);
    lines.push('Localidade: ' + (location ? location.value : ''));
    if (iso) lines.push('Data: ' + formatBr(iso));
    if (time) lines.push('Horário: ' + time);
    return lines.join('\n');
  }

  function openWhatsApp(text) {
    window.open(whatsappUrl(text), '_blank', 'noopener');
  }

  /* ---------------------------------------------------------------------- *
   * 7. API pública (consumida por /assets/sl-units-map.js)                 *
   * ---------------------------------------------------------------------- */

  var api = {
    LOCATIONS: LOCATIONS,
    SOPROLIFE_SCHEDULE: SOPROLIFE_SCHEDULE,
    PASTORE_SCHEDULE: PASTORE_SCHEDULE,
    PASTORE_BOOKING_URL: PASTORE_BOOKING_URL,
    PASTORE_ROUTE_URL: PASTORE_ROUTE_URL,
    PASTORE_WHATSAPP_TEXT: PASTORE_WHATSAPP_TEXT,
    WHATSAPP_PHONE: WHATSAPP_PHONE,
    byId: byId,
    byValue: byValue,
    validateDate: validateDate,
    isWeekdayAllowed: isWeekdayAllowed,
    whatsappUrl: whatsappUrl,
    whatsappText: whatsappText,
    track: track,
    locationParams: locationParams,
    todayIso: todayIsoSaoPaulo,
    formatBr: formatBr,
    /* preenchido no init: id da localidade selecionada no formulário */
    current: null
  };
  window.SL_BOOKING = api;

  /* ---------------------------------------------------------------------- *
   * 8. Formulário                                                          *
   * ---------------------------------------------------------------------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* Envolve o controle num wrapper para pendurar a seta/ícone. */
  function wrapControl(control, kind) {
    if (!control) return null;
    control.classList.add('sl-field-control');

    var parent = control.parentNode;
    if (parent && parent.classList && parent.classList.contains('sl-field-wrap')) {
      return parent;
    }
    var wrap = el('span', 'sl-field-wrap sl-field-wrap--' + kind);
    parent.insertBefore(wrap, control);
    wrap.appendChild(control);
    return wrap;
  }

  /* A página oferece esta localidade no seletor? */
  function hasLocation(select, id) {
    var loc = byId(id);
    if (!loc) return false;
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === loc.value) return true;
    }
    return false;
  }

  function fieldOf(control) {
    return control && control.closest ? control.closest('.sl-booking-field') : null;
  }

  function ensureHint(control) {
    var field = fieldOf(control);
    if (!field) return null;
    var hint = field.querySelector('.sl-field-hint');
    if (!hint) {
      hint = el('span', 'sl-field-hint');
      hint.setAttribute('role', 'status');
      field.appendChild(hint);
    }
    return hint;
  }

  /* Normaliza o <select> de localidade a partir de LOCATIONS: rótulo, id e
     ordem vêm da configuração central.

     Cada página decide QUAIS localidades oferece — a página de espirometria
     domiciliar, por exemplo, não oferece a unidade parceira de Ipanema.
     Por isso esta função NUNCA insere uma opção ausente: ela apenas alinha e
     reordena as que a página já declarou no HTML. */
  function syncLocationOptions(select) {
    var slot = 0;
    LOCATIONS.forEach(function (loc) {
      var option = null;
      for (var i = 0; i < select.options.length; i++) {
        if (select.options[i].value === loc.value) { option = select.options[i]; break; }
      }
      if (!option) return; /* localidade não oferecida nesta página */

      option.textContent = loc.label;
      option.setAttribute('data-location-id', loc.id);
      if (select.options[slot] !== option) {
        select.insertBefore(option, select.options[slot] || null);
      }
      slot++;
    });
  }

  function buildIpanemaPanel(getService, getDate) {
    var loc = byId('pastore-ipanema');
    var panel = el('div', 'sl-ipanema-panel');
    panel.id = 'sl-ipanema-panel';
    panel.hidden = true;

    panel.appendChild(el('span', 'sl-ipanema-panel__badge', 'Unidade parceira'));
    panel.appendChild(el('p', 'sl-ipanema-panel__title', PASTORE_SCHEDULE.headline));

    var address = el('address', 'sl-ipanema-panel__address');
    address.appendChild(document.createTextNode(loc.address));
    address.appendChild(document.createElement('br'));
    address.appendChild(document.createTextNode(loc.reference));
    panel.appendChild(address);

    panel.appendChild(el('p', 'sl-ipanema-panel__disclaimer',
      'Disponibilidade sujeita à confirmação no sistema oficial de agendamento da Pastore.'));

    panel.appendChild(el('p', 'sl-ipanema-panel__steps-title',
      'Se for agendar direto na Pastore:'));
    var steps = el('ol', 'sl-ipanema-panel__steps');
    PASTORE_STEPS.forEach(function (step) { steps.appendChild(el('li', null, step)); });
    panel.appendChild(steps);

    var actions = el('div', 'sl-ipanema-panel__actions');

    var wa = el('a', 'sl-ipanema-btn sl-ipanema-btn--wa', 'Falar com a SoproLife');
    wa.href = whatsappUrl(PASTORE_WHATSAPP_TEXT);
    wa.target = '_blank';
    wa.rel = 'noopener';
    wa.addEventListener('click', function () {
      wa.href = whatsappUrl(whatsappText(loc, getService(), getDate(), null));
      track('click_whatsapp_ipanema', locationParams(loc));
    });
    actions.appendChild(wa);

    var pastore = el('a', 'sl-ipanema-btn sl-ipanema-btn--pastore', 'Agendar diretamente na Pastore');
    pastore.href = PASTORE_BOOKING_URL;
    pastore.target = '_blank';
    pastore.rel = 'noopener';
    pastore.addEventListener('click', function () {
      var params = locationParams(loc);
      params.destino = 'paciente.centromedicopastore.com.br';
      track('click_agendar_pastore', params);
    });
    actions.appendChild(pastore);

    panel.appendChild(actions);
    return panel;
  }

  function initBookingForm() {
    var serviceEl = document.getElementById('sl-booking-service');
    var unitEl = document.getElementById('sl-booking-unit');
    var dateEl = document.getElementById('sl-booking-date');
    var slotsWrap = document.getElementById('sl-booking-slots');
    if (!serviceEl || !unitEl || !dateEl || !slotsWrap) return;

    var noteEl = document.querySelector('.sl-booking-note');

    syncLocationOptions(unitEl);

    wrapControl(serviceEl, 'select');
    wrapControl(unitEl, 'select');
    var dateWrap = wrapControl(dateEl, 'date');
    var dateHint = ensureHint(dateEl);
    var dateField = fieldOf(dateEl);

    var minIso = todayIsoSaoPaulo();
    var maxIso = addDaysIso(minIso, DATE_WINDOW_DAYS);
    dateEl.setAttribute('min', minIso);
    dateEl.setAttribute('max', maxIso);
    if (!dateEl.value) dateEl.value = minIso;

    slotsWrap.setAttribute('role', 'group');
    slotsWrap.setAttribute('aria-label', 'Horários disponíveis');

    /* O painel da unidade parceira só é criado se a página oferecer Ipanema. */
    var offersIpanema = hasLocation(unitEl, 'pastore-ipanema');
    var ipanemaPanel = null;
    if (offersIpanema) {
      ipanemaPanel = buildIpanemaPanel(
        function () { return serviceEl.value; },
        function () { return dateEl.value; }
      );
      slotsWrap.parentNode.appendChild(ipanemaPanel);
    }

    var lastTrackedLocation = null;

    function currentLocation() {
      return byValue(unitEl.value) || LOCATIONS[0];
    }

    function setDateValue(iso) {
      if (dateEl._flatpickr) {
        dateEl._flatpickr.setDate(iso || null, false);
      }
      dateEl.value = iso || '';
    }

    /* Ajusta o calendário aos dias permitidos pela localidade. */
    function syncPicker(location) {
      var fp = dateEl._flatpickr;
      if (!fp) return;
      fp.set('minDate', minIso);
      fp.set('maxDate', maxIso);
      fp.set('disable', location.schedule.weekdays
        ? [function (date) { return !isWeekdayAllowed(location, date); }]
        : []);
    }

    function renderEmpty(message) {
      slotsWrap.innerHTML = '';
      var box = el('p', 'sl-booking-slots-empty', message);
      slotsWrap.appendChild(box);
    }

    function renderSlots() {
      var location = currentLocation();
      var iso = dateEl.value;
      var result = validateDate(location, iso, minIso, maxIso);

      if (ipanemaPanel) ipanemaPanel.hidden = !location.partner;

      /* Estado visual do campo de data. */
      if (dateField) {
        dateField.classList.toggle('is-invalid', !result.ok && result.reason !== 'empty');
        dateField.classList.toggle('is-valid', result.ok);
      }
      /* Dica curta no campo; a explicação completa fica na faixa de horários,
         para não repetir o mesmo texto duas vezes na tela. */
      if (dateHint) dateHint.textContent = result.hint || '';

      if (!result.ok) {
        renderEmpty(result.message);
        if (noteEl) {
          noteEl.textContent = location.partner
            ? PASTORE_SCHEDULE.note
            : 'Escolha uma data válida para ver os horários.';
          noteEl.classList.add('is-warning');
        }
        return;
      }

      slotsWrap.innerHTML = '';
      var frag = document.createDocumentFragment();

      location.schedule.slots.forEach(function (time) {
        var btn = el('button', 'sl-slot-btn', time);
        btn.type = 'button';
        btn.setAttribute('data-time', time);
        btn.setAttribute('aria-label', time + ' — ' + location.shortName);
        btn.addEventListener('click', function () {
          var text = whatsappText(location, serviceEl.value, dateEl.value, time);
          if (location.partner) {
            track('click_whatsapp_ipanema', locationParams(location));
          }
          openWhatsApp(text);
        });
        frag.appendChild(btn);
      });

      slotsWrap.appendChild(frag);

      if (noteEl) {
        noteEl.classList.remove('is-warning');
        noteEl.textContent = location.partner
          ? 'Agenda habitual da unidade. ' + PASTORE_SCHEDULE.note
          : SOPROLIFE_SCHEDULE.note;
      }
    }

    function onLocationChange() {
      var location = currentLocation();
      api.current = location.id;
      syncPicker(location);

      /* Data selecionada deixou de ser válida para a nova localidade:
         limpa a seleção antes de reavaliar os horários. */
      var check = validateDate(location, dateEl.value, minIso, maxIso);
      if (!check.ok && check.reason === 'weekday') {
        setDateValue('');
      }

      renderSlots();

      if (lastTrackedLocation !== location.id) {
        lastTrackedLocation = location.id;
        track('booking_location_selected', locationParams(location));
      }

      /* Avisa o mapa (mini + modal) para destacar o ponto correspondente. */
      try {
        document.dispatchEvent(new CustomEvent('sl:booking:location', {
          detail: { id: location.id, value: location.value }
        }));
      } catch (e) { /* navegadores sem CustomEvent construtor */ }
    }

    serviceEl.addEventListener('change', renderSlots);
    dateEl.addEventListener('change', renderSlots);
    unitEl.addEventListener('change', onLocationChange);

    /* Cartões do modal do mapa também trocam a localidade do formulário. */
    document.addEventListener('sl:booking:select-location', function (event) {
      var loc = event && event.detail ? byId(event.detail.id) : null;
      if (!loc || unitEl.value === loc.value) return;
      if (!hasLocation(unitEl, loc.id)) return; /* não oferecida nesta página */
      unitEl.value = loc.value;
      onLocationChange();
    });

    initDatePicker(function () {
      syncPicker(currentLocation());
      renderSlots();
    });

    /* Estado inicial (sem disparar o evento de analytics como "mudança"). */
    lastTrackedLocation = currentLocation().id;
    api.current = lastTrackedLocation;
    renderSlots();

    if (dateWrap) dateWrap.setAttribute('data-sl-ready', '1');
  }

  /* ---------------------------------------------------------------------- *
   * 9. Calendário pt-BR (flatpickr)                                        *
   * ---------------------------------------------------------------------- */

  function initDatePicker(onReadyCallback) {
    var dateEl = document.getElementById('sl-booking-date');
    if (!dateEl) return;

    var attempts = 0;

    function tryInit() {
      if (dateEl._flatpickr) { onReadyCallback(); return; }

      if (!window.flatpickr) {
        /* O CDN pode ainda não ter respondido. Sem flatpickr o campo continua
           utilizável: vira um input type=date nativo. */
        if (++attempts > 40) {
          dateEl.type = 'date';
          dateEl.removeAttribute('inputmode');
          onReadyCallback();
          return;
        }
        window.setTimeout(tryInit, 100);
        return;
      }

      var locale = window.flatpickr.l10ns && window.flatpickr.l10ns.pt
        ? window.flatpickr.l10ns.pt
        : 'default';

      window.flatpickr(dateEl, {
        locale: locale,
        dateFormat: 'Y-m-d',
        altInput: true,
        altFormat: 'd/m/Y',
        altInputClass: 'sl-field-control sl-booking-date-alt',
        defaultDate: dateEl.value || null,
        minDate: dateEl.getAttribute('min') || 'today',
        maxDate: dateEl.getAttribute('max') || null,
        disableMobile: true,
        allowInput: false,
        monthSelectorType: 'dropdown',
        onChange: function () {
          dateEl.dispatchEvent(new Event('change', { bubbles: true }));
        },
        onReady: function (selectedDates, dateStr, instance) {
          if (instance.altInput) {
            instance.altInput.setAttribute('placeholder', 'dd/mm/aaaa');
            instance.altInput.setAttribute('aria-label', 'Data no formato dia, mês e ano');
            instance.altInput.id = 'sl-booking-date-visible';
            var label = document.querySelector('label[for="sl-booking-date"]');
            if (label) label.setAttribute('for', 'sl-booking-date-visible');
          }
        }
      });

      onReadyCallback();
    }

    tryInit();
  }

  /* ---------------------------------------------------------------------- *
   * 10. Boot                                                               *
   * ---------------------------------------------------------------------- */

  function boot() {
    initBookingForm();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})(window, document);
