/* Calendário SoproLife (M15.4A) — date picker próprio, leve e reutilizável.
 *
 * - Zero dependência externa: sem CDN, sem fonte remota, sem framework.
 * - Exibição em formato brasileiro (DD/MM/AAAA); o valor enviado à API
 *   permanece no formato do backend (AAAA-MM-DD, AAAA-MM ou AAAA).
 * - Precisão parcial é PRESERVADA: digitar "07/2026" envia "2026-07" e
 *   digitar "2026" envia "2026" — o calendário nunca inventa dia exato.
 *   Selecionar um dia no calendário é sempre uma escolha explícita.
 * - Segurança: nunca usa innerHTML (DOM montado nó a nó, textContent puro).
 * - Acessibilidade: role="dialog", rótulos ARIA, navegação por teclado
 *   (setas, PageUp/PageDown, Home/End, Enter, Escape), foco devolvido ao
 *   campo ao fechar, clique fora fecha.
 * - Testável em Node: DOM restrito a createElement/appendChild/insertBefore/
 *   classList/atributos/addEventListener — sem querySelector no módulo.
 */
(function (global) {
  "use strict";

  var MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  var DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

  var _doc = (typeof document !== "undefined") ? document : null;
  var _todayFn = function () {
    var n = new Date();
    return { y: n.getFullYear(), m: n.getMonth() + 1, d: n.getDate() };
  };
  var _openPicker = null; // no máximo um calendário aberto por vez

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function isLeap(y) { return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0; }

  function daysInMonth(y, m) {
    return [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];
  }

  function validYMD(y, m, d) {
    return y >= 1900 && y <= 2200 && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m);
  }

  // Interpreta o texto digitado (formatos brasileiros OU ISO do backend).
  // Retorna { ok, iso, br, precisao } — precisao: "dia" | "mes" | "ano".
  function parseFlex(raw) {
    var v = String(raw == null ? "" : raw).trim();
    var m;
    if (v === "") return { ok: false, vazio: true };
    if ((m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) {
      var d1 = +m[1], m1 = +m[2], y1 = +m[3];
      if (!validYMD(y1, m1, d1)) return { ok: false };
      return { ok: true, precisao: "dia", iso: y1 + "-" + pad2(m1) + "-" + pad2(d1),
        br: pad2(d1) + "/" + pad2(m1) + "/" + y1 };
    }
    if ((m = v.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
      var y2 = +m[1], m2 = +m[2], d2 = +m[3];
      if (!validYMD(y2, m2, d2)) return { ok: false };
      return { ok: true, precisao: "dia", iso: y2 + "-" + pad2(m2) + "-" + pad2(d2),
        br: pad2(d2) + "/" + pad2(m2) + "/" + y2 };
    }
    if ((m = v.match(/^(\d{1,2})\/(\d{4})$/))) {
      var m3 = +m[1], y3 = +m[2];
      if (m3 < 1 || m3 > 12 || y3 < 1900 || y3 > 2200) return { ok: false };
      return { ok: true, precisao: "mes", iso: y3 + "-" + pad2(m3), br: pad2(m3) + "/" + y3 };
    }
    if ((m = v.match(/^(\d{4})-(\d{1,2})$/))) {
      var y4 = +m[1], m4 = +m[2];
      if (m4 < 1 || m4 > 12 || y4 < 1900 || y4 > 2200) return { ok: false };
      return { ok: true, precisao: "mes", iso: y4 + "-" + pad2(m4), br: pad2(m4) + "/" + y4 };
    }
    if ((m = v.match(/^(\d{4})$/))) {
      var y5 = +m[1];
      if (y5 < 1900 || y5 > 2200) return { ok: false };
      return { ok: true, precisao: "ano", iso: String(y5), br: String(y5) };
    }
    return { ok: false };
  }

  /* M25.26 — barra automática ao digitar, sem destruir data parcial.
   *
   * O operador digita "12082026" e espera ver "12/08/2026". O problema é que
   * mascarar cegamente a cada 2 dígitos QUEBRA os campos de data parcial:
   * "2026" (ano, contrato legítimo do domínio) viraria "20/26", e o campo
   * ficaria preso — ao corrigir, a máscara reescreveria de novo.
   *
   * Três regras resolvem sem mentir:
   *
   * 1. Apagando (backspace/delete) NADA é reescrito. Sem isto a barra
   *    reaparece no mesmo instante em que é apagada e o campo trava.
   * 2. Barra digitada pelo humano manda. Quem escreve "08/2026" está dizendo
   *    "mês e ano"; a máscara não tem o direito de discordar.
   * 3. Só reformata a partir de dígitos puros, e em campo PARCIAL apenas com
   *    5 ou mais dígitos — abaixo disso "2026" ainda é um ano válido e
   *    reescrevê-lo inventaria uma precisão que o operador não pediu. Em
   *    campo de data COMPLETA não existe leitura parcial legítima, então a
   *    barra pode entrar já no 3º dígito.
   *
   * Função pura de propósito: é o que permite testar as bordas (colar,
   * apagar, ano solto) sem navegador.
   */
  /* M26.4 — em campo de data COMPLETA a SEGUNDA barra também é da máscara.
   *
   * Até aqui, assim que a primeira barra entrava, `texto` já continha "/" e a
   * regra 2 devolvia tudo intacto: "12122012" parava em "12/122012" e o
   * operador tinha de digitar a segunda barra à mão. Em campo COMPLETO
   * (DD/MM/AAAA) não existe leitura parcial legítima, então o texto que a
   * PRÓPRIA máscara pode ter escrito — segmentos de exatamente 2 dígitos
   * antes de cada barra — pode ser remascarado sem ambiguidade. "1/2/2012",
   * digitado à mão, não tem essa forma e continua intocado (regra 2). Barras
   * repetidas colapsam em uma só, para o caso de o operador digitar "/" logo
   * depois da barra que a máscara acabou de inserir.
   *
   * Campo PARCIAL não muda em nada: ali "2026" ainda é um ano legítimo.
   */
  function mascararData(raw, mode, apagando) {
    var texto = String(raw == null ? "" : raw);
    if (apagando) return texto;
    var completo = (mode !== "partial");
    var semDuplicadas = completo ? texto.replace(/\/{2,}/g, "/") : texto;
    var remascarar = completo && (/^\d{2}\/\d*$/.test(semDuplicadas) ||
      /^\d{2}\/\d{2}\/\d*$/.test(semDuplicadas));
    if (!remascarar && texto.indexOf("/") !== -1) return texto;
    var base = remascarar ? semDuplicadas.replace(/\//g, "") : texto;
    var soDigitos = base.replace(/\D/g, "");
    // Texto com letra ou símbolo passa intacto — "dezembro/2026" é uma
    // entrada válida do domínio e não pode ser reescrita como número. A
    // comparação é feita ANTES de limitar o tamanho: truncar primeiro faria
    // toda digitação longa parecer "texto com símbolo" e escapar da máscara.
    if (soDigitos.length !== base.length) return texto;
    var digitos = soDigitos.slice(0, 8);
    var minimo = completo ? 2 : 5;
    if (digitos.length < minimo) return digitos;
    if (completo) {
      // A barra fecha o bloco assim que ele está cheio: 12 → "12/",
      // 1212 → "12/12/". Digitando só números sai DD/MM/AAAA.
      if (digitos.length === 2) return digitos + "/";
      if (digitos.length === 3) return digitos.slice(0, 2) + "/" + digitos.slice(2);
      if (digitos.length === 4) return digitos.slice(0, 2) + "/" + digitos.slice(2) + "/";
      return digitos.slice(0, 2) + "/" + digitos.slice(2, 4) + "/" + digitos.slice(4);
    }
    if (digitos.length <= 4) return digitos.slice(0, 2) + "/" + digitos.slice(2);
    return digitos.slice(0, 2) + "/" + digitos.slice(2, 4) + "/" + digitos.slice(4);
  }

  // Backend (ISO/parcial) → exibição brasileira. Valor irreconhecível volta cru.
  function isoToBr(iso) {
    var p = parseFlex(iso);
    return p.ok ? p.br : String(iso == null ? "" : iso);
  }

  // Exibição brasileira → backend (ISO/parcial). Irreconhecível volta cru.
  function brToIso(br) {
    var p = parseFlex(br);
    return p.ok ? p.iso : String(br == null ? "" : br);
  }

  // getDay(): 0=domingo … 6=sábado → índice com segunda-feira primeiro (0=seg).
  function mondayIndex(y, m, d) {
    return (new Date(y, m - 1, d).getDay() + 6) % 7;
  }

  function el(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function attach(input, opts) {
    opts = opts || {};
    var doc = opts.document || _doc;
    if (!doc || !input || !input.parentNode) return null;
    if (input.getAttribute("data-m15-date-attached")) return null;
    input.setAttribute("data-m15-date-attached", "1");

    var mode = input.getAttribute("data-m15-date") || opts.mode ||
      (input.type === "date" ? "full" : "partial");

    var wrapper = el(doc, "span", "m15-date");
    var display = el(doc, "input", "m15-date-input");
    display.type = "text";
    display.autocomplete = "off";
    display.setAttribute("inputmode", "numeric");
    display.setAttribute("placeholder", "DD/MM/AAAA");
    display.setAttribute("aria-label",
      (input.getAttribute("aria-label") || "Data") + " (formato DD/MM/AAAA)");
    if (input.getAttribute("required") != null || input.required) {
      display.setAttribute("required", "");
      input.removeAttribute("required");
      try { input.required = false; } catch (e) { /* fake DOM */ }
    }
    display.value = isoToBr(input.value || "");

    var btn = el(doc, "button", "m15-date-btn", "📅");
    btn.type = "button";
    btn.setAttribute("aria-label", "Abrir calendário");
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", "false");
    btn.tabIndex = 0;

    // o input original vira o portador escondido do valor de backend —
    // nome, id e formato do payload permanecem exatamente os mesmos.
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(display);
    wrapper.appendChild(btn);
    wrapper.appendChild(input);
    input.type = "hidden";

    var picker = {
      mode: mode,
      holder: input,
      display: display,
      btn: btn,
      wrapper: wrapper,
      doc: doc,
      pop: null,
      view: null,          // {y, m} exibido
      focused: null,       // dia com foco de teclado
      dayButtons: {},      // dia → botão do mês corrente
      _docMouse: null,
      _docKey: null,
    };

    function syncFromDisplay(normalizar) {
      var raw = display.value;
      var p = parseFlex(raw);
      if (p.vazio) {
        input.value = "";
        wrapper.className = "m15-date";
        return;
      }
      if (p.ok) {
        // precisão preservada: dia → AAAA-MM-DD, mês → AAAA-MM, ano → AAAA
        input.value = p.iso;
        if (normalizar) display.value = p.br;
        wrapper.className = "m15-date";
      } else {
        // texto irreconhecível segue cru — o servidor valida (fail-closed),
        // nada é convertido nem descartado silenciosamente.
        input.value = raw;
        wrapper.className = "m15-date m15-date-invalid";
      }
    }

    function selectedYMD() {
      var p = parseFlex(input.value);
      if (p.ok && p.precisao === "dia") {
        var parts = p.iso.split("-");
        return { y: +parts[0], m: +parts[1], d: +parts[2] };
      }
      return null;
    }

    function initialView() {
      var p = parseFlex(input.value);
      var hoje = _todayFn();
      if (p.ok) {
        var parts = p.iso.split("-");
        return { y: +parts[0], m: parts.length > 1 ? +parts[1] : 1 };
      }
      return { y: hoje.y, m: hoje.m };
    }

    function renderGrid() {
      var grid = picker.grid;
      while (grid.firstChild) grid.removeChild(grid.firstChild);
      picker.dayButtons = {};
      var y = picker.view.y, m = picker.view.m;
      picker.titleEl.textContent = MESES[m - 1] + " " + y;

      var i;
      for (i = 0; i < 7; i++) {
        grid.appendChild(el(doc, "span", "m15-cal-dow", DIAS_SEMANA[i]));
      }
      var lead = mondayIndex(y, m, 1);
      var prevY = m === 1 ? y - 1 : y, prevM = m === 1 ? 12 : m - 1;
      var prevTotal = daysInMonth(prevY, prevM);
      for (i = 0; i < lead; i++) {
        var out = el(doc, "button", "m15-cal-day m15-cal-out",
          String(prevTotal - lead + 1 + i));
        out.type = "button";
        out.disabled = true;
        out.tabIndex = -1;
        out.setAttribute("aria-hidden", "true");
        grid.appendChild(out);
      }
      var hoje = _todayFn();
      var sel = selectedYMD();
      var total = daysInMonth(y, m);
      for (var d = 1; d <= total; d++) {
        var cls = "m15-cal-day";
        var isToday = hoje.y === y && hoje.m === m && hoje.d === d;
        var isSel = !!(sel && sel.y === y && sel.m === m && sel.d === d);
        if (isToday) cls += " m15-cal-today";
        if (isSel) cls += " m15-cal-selected";
        var b = el(doc, "button", cls, String(d));
        b.type = "button";
        b.setAttribute("data-day", String(d));
        b.setAttribute("aria-label", d + " de " + MESES[m - 1] + " de " + y);
        if (isToday) b.setAttribute("aria-current", "date");
        b.setAttribute("aria-selected", isSel ? "true" : "false");
        b.tabIndex = (picker.focused === d) ? 0 : -1;
        (function (dia) {
          b.addEventListener("click", function () { selectDay(dia); });
        })(d);
        picker.dayButtons[d] = b;
        grid.appendChild(b);
      }
    }

    function focusDay(d) {
      var total = daysInMonth(picker.view.y, picker.view.m);
      if (d < 1) d = 1;
      if (d > total) d = total;
      var prev = picker.dayButtons[picker.focused];
      if (prev) prev.tabIndex = -1;
      picker.focused = d;
      var b = picker.dayButtons[d];
      if (b) {
        b.tabIndex = 0;
        if (b.focus) b.focus();
      }
    }

    function moveFocus(deltaDays) {
      var y = picker.view.y, m = picker.view.m, d = picker.focused + deltaDays;
      while (d < 1) {
        m -= 1;
        if (m < 1) { m = 12; y -= 1; }
        d += daysInMonth(y, m);
      }
      while (d > daysInMonth(y, m)) {
        d -= daysInMonth(y, m);
        m += 1;
        if (m > 12) { m = 1; y += 1; }
      }
      if (y !== picker.view.y || m !== picker.view.m) {
        picker.view = { y: y, m: m };
        picker.focused = d;
        renderGrid();
        focusDay(d);
      } else {
        focusDay(d);
      }
    }

    function shiftMonth(delta, manterFoco) {
      var m = picker.view.m + delta, y = picker.view.y;
      while (m < 1) { m += 12; y -= 1; }
      while (m > 12) { m -= 12; y += 1; }
      picker.view = { y: y, m: m };
      var d = Math.min(picker.focused || 1, daysInMonth(y, m));
      picker.focused = d;
      renderGrid();
      if (manterFoco) focusDay(d);
    }

    function selectDay(d) {
      var y = picker.view.y, m = picker.view.m;
      input.value = y + "-" + pad2(m) + "-" + pad2(d);
      display.value = pad2(d) + "/" + pad2(m) + "/" + y;
      wrapper.className = "m15-date";
      close(true);
    }

    function onGridKey(ev) {
      var k = ev.key;
      var stop = true;
      if (k === "ArrowLeft") moveFocus(-1);
      else if (k === "ArrowRight") moveFocus(1);
      else if (k === "ArrowUp") moveFocus(-7);
      else if (k === "ArrowDown") moveFocus(7);
      else if (k === "PageUp") shiftMonth(-1, true);
      else if (k === "PageDown") shiftMonth(1, true);
      else if (k === "Home") moveFocus(-mondayIndex(picker.view.y, picker.view.m, picker.focused));
      else if (k === "End") moveFocus(6 - mondayIndex(picker.view.y, picker.view.m, picker.focused));
      else if (k === "Enter" || k === " ") selectDay(picker.focused);
      else stop = false;
      if (stop && ev.preventDefault) ev.preventDefault();
    }

    function isInside(node) {
      while (node) {
        if (node === wrapper) return true;
        node = node.parentNode;
      }
      return false;
    }

    function open() {
      if (picker.pop) return;
      if (_openPicker && _openPicker !== picker) _openPicker.close(false);
      _openPicker = picker;

      picker.view = initialView();
      var sel = selectedYMD();
      var hoje = _todayFn();
      picker.focused = (sel && sel.y === picker.view.y && sel.m === picker.view.m)
        ? sel.d
        : ((hoje.y === picker.view.y && hoje.m === picker.view.m) ? hoje.d : 1);

      var pop = el(doc, "div", "m15-cal");
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-modal", "false");
      pop.setAttribute("aria-label", "Calendário");
      picker.pop = pop;

      var head = el(doc, "div", "m15-cal-head");
      var prev = el(doc, "button", "m15-cal-nav", "‹");
      prev.type = "button";
      prev.setAttribute("aria-label", "Mês anterior");
      prev.addEventListener("click", function () { shiftMonth(-1, false); });
      var title = el(doc, "div", "m15-cal-title");
      title.setAttribute("aria-live", "polite");
      var next = el(doc, "button", "m15-cal-nav", "›");
      next.type = "button";
      next.setAttribute("aria-label", "Próximo mês");
      next.addEventListener("click", function () { shiftMonth(1, false); });
      head.appendChild(prev);
      head.appendChild(title);
      head.appendChild(next);
      picker.titleEl = title;

      var grid = el(doc, "div", "m15-cal-grid");
      grid.setAttribute("role", "grid");
      grid.setAttribute("aria-label", "Dias do mês");
      grid.addEventListener("keydown", onGridKey);
      picker.grid = grid;

      var foot = el(doc, "div", "m15-cal-foot");
      var hojeBtn = el(doc, "button", "m15-btn m15-btn-sec", "Hoje");
      hojeBtn.type = "button";
      hojeBtn.addEventListener("click", function () {
        var h = _todayFn();
        picker.view = { y: h.y, m: h.m };
        selectDay(h.d);
      });
      var limparBtn = el(doc, "button", "m15-btn m15-btn-sec", "Limpar");
      limparBtn.type = "button";
      limparBtn.addEventListener("click", function () {
        input.value = "";
        display.value = "";
        wrapper.className = "m15-date";
        close(true);
      });
      foot.appendChild(hojeBtn);
      foot.appendChild(limparBtn);

      pop.appendChild(head);
      pop.appendChild(grid);
      pop.appendChild(foot);
      if (mode === "partial") {
        pop.appendChild(el(doc, "div", "m15-cal-hint",
          "Data parcial? Digite MM/AAAA ou AAAA direto no campo — a precisão é preservada."));
      }
      wrapper.appendChild(pop);
      btn.setAttribute("aria-expanded", "true");

      renderGrid();
      focusDay(picker.focused);

      picker._docMouse = function (ev) {
        if (!isInside(ev.target)) close(false);
      };
      picker._docKey = function (ev) {
        if (ev.key === "Escape") {
          close(true);
          if (ev.preventDefault) ev.preventDefault();
        }
      };
      doc.addEventListener("mousedown", picker._docMouse);
      doc.addEventListener("keydown", picker._docKey);
    }

    function close(voltarFoco) {
      if (!picker.pop) return;
      wrapper.removeChild(picker.pop);
      picker.pop = null;
      picker.grid = null;
      picker.titleEl = null;
      picker.dayButtons = {};
      btn.setAttribute("aria-expanded", "false");
      doc.removeEventListener("mousedown", picker._docMouse);
      doc.removeEventListener("keydown", picker._docKey);
      picker._docMouse = null;
      picker._docKey = null;
      if (_openPicker === picker) _openPicker = null;
      if (voltarFoco && display.focus) display.focus();
    }

    picker.open = open;
    picker.close = close;
    picker.isOpen = function () { return !!picker.pop; };
    picker.syncFromDisplay = syncFromDisplay;

    display.addEventListener("input", function (ev) {
      // A máscara roda ANTES da leitura do valor: o que o operador vê e o que
      // o campo oculto envia saem do mesmo texto, nunca de duas versões.
      var apagando = !!(ev && ev.inputType &&
        String(ev.inputType).indexOf("delete") === 0);
      var mascarado = mascararData(display.value, picker.mode, apagando);
      if (mascarado !== display.value) {
        var noFim = display.selectionStart === display.value.length;
        display.value = mascarado;
        // Cursor no fim only quando já estava no fim — quem edita no meio do
        // texto não é teleportado para o final a cada tecla.
        if (noFim && display.setSelectionRange) {
          try {
            display.setSelectionRange(mascarado.length, mascarado.length);
          } catch (e) { /* input sem suporte a seleção */ }
        }
      }
      syncFromDisplay(false);
    });
    display.addEventListener("change", function () { syncFromDisplay(true); });
    display.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowDown" && ev.altKey) {
        open();
        if (ev.preventDefault) ev.preventDefault();
      }
    });
    btn.addEventListener("click", function () {
      if (picker.pop) close(true); else open();
    });

    return picker;
  }

  // Percorre a subárvore procurando inputs data-m15-date ainda não anexados.
  // Caminhada manual (sem querySelectorAll) para funcionar no DOM falso dos testes.
  function attachAll(root, opts) {
    if (!root) return [];
    var out = [];
    (function walk(node) {
      if (!node) return;
      var isInput = node.tagName === "INPUT" ||
        (node.nodeName && String(node.nodeName).toUpperCase() === "INPUT");
      if (isInput && node.getAttribute && node.getAttribute("data-m15-date") &&
          !node.getAttribute("data-m15-date-attached")) {
        var p = attach(node, opts);
        if (p) out.push(p);
        return;
      }
      var kids = node.children || [];
      for (var i = 0; i < kids.length; i++) walk(kids[i]);
    })(root);
    return out;
  }

  var api = {
    parseFlex: parseFlex,
    mascararData: mascararData,
    isoToBr: isoToBr,
    brToIso: brToIso,
    daysInMonth: daysInMonth,
    mondayIndex: mondayIndex,
    attach: attach,
    attachAll: attachAll,
    _setToday: function (fn) { _todayFn = fn; },       // só para testes
    _setDocument: function (doc) { _doc = doc; },      // só para testes
    _openPicker: function () { return _openPicker; },  // só para testes
  };

  global.SoproM15DatePicker = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : this);
