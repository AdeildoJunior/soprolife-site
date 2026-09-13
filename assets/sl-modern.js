/* Navegação pública progressiva: links disponíveis mesmo sem JavaScript. */
(function () {
  'use strict';
  var header = document.querySelector('.sl-header');
  var nav = header && header.querySelector('.sl-main-nav, .sl-nav');
  if (nav) {
    header.classList.add('sl-has-menu');
    var instagram = header.querySelector('a[href*="instagram.com"]');
    if (instagram) {
      var social = document.createElement('a');
      social.className = 'sl-mobile-social'; social.href = instagram.href;
      social.target = '_blank'; social.rel = 'noopener'; social.textContent = 'Instagram';
      nav.appendChild(social);
    }
    nav.id = nav.id || 'sl-public-navigation';
    var toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'sl-menu-toggle';
    toggle.textContent = 'Menu';
    toggle.setAttribute('aria-controls', nav.id);
    toggle.setAttribute('aria-expanded', 'false');
    nav.before(toggle);
    function setOpen(open) {
      header.classList.toggle('sl-menu-open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.textContent = open ? 'Fechar' : 'Menu';
    }
    toggle.addEventListener('click', function () { setOpen(!header.classList.contains('sl-menu-open')); });
    nav.addEventListener('click', function (e) { if (e.target.closest('a:not([aria-haspopup])')) setOpen(false); });
    header.addEventListener('keydown', function (e) { if (e.key === 'Escape') { setOpen(false); toggle.focus(); } });
    nav.querySelectorAll('a').forEach(function (a) {
      if (a.pathname === location.pathname && !a.hash) a.setAttribute('aria-current', 'page');
    });
  }
  var main = document.querySelector('main, .sl-page, .sl-container:not(header .sl-container), .page');
  if (main && header) {
    if (main.tagName !== 'MAIN') main.setAttribute('role', 'main');
    main.id = main.id || 'sl-main-content';
    main.tabIndex = -1;
    var skip = document.createElement('a');
    skip.className = 'sl-skip-link'; skip.href = '#' + main.id; skip.textContent = 'Pular para o conteúdo';
    document.body.prepend(skip);
  }
  var testimonials = document.querySelector('#depoimentos');
  var track = testimonials && testimonials.querySelector('.sl-ttrack');
  if (track) {
    track.tabIndex = 0;
    track.setAttribute('role', 'region');
    track.setAttribute('aria-label', 'Depoimentos de pacientes. Use as setas para navegar.');
    function move(direction) {
      var step = track.clientWidth + 20;
      var next = track.scrollLeft + direction * step;
      if (next > track.scrollWidth - track.clientWidth + 1) next = 0;
      if (next < -1) next = track.scrollWidth - track.clientWidth;
      track.scrollTo({left: next, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    }
    testimonials.querySelectorAll('.sl-tarrow').forEach(function (button) {
      button.addEventListener('click', function () { move(button.classList.contains('sl-tarrow--left') ? -1 : 1); });
    });
  }
  // Mantém o foco no mapa ampliado enquanto o diálogo estiver aberto.
  document.addEventListener('keydown', function (e) {
    var dialog = document.querySelector('.sl-map-modal.is-open [role="dialog"]');
    if (!dialog || e.key !== 'Tab') return;
    var items = Array.from(dialog.querySelectorAll('a[href],button,[tabindex="0"]')).filter(function (n) { return n.getClientRects().length && !n.disabled; });
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
})();
