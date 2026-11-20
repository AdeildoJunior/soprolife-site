/* Local outline icon vocabulary. Decorative only; labels remain authoritative. */
(function(global){
  'use strict';
  const paths={
    activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    chart:'<path d="M4 4v16h16M8 15v-4m5 4V7m5 8v-6"/>',
    trend:'<path d="m4 16 6-6 4 4 6-9M15 5h5v5"/>',
    folder:'<path d="M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v11H3Z"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h1m6 0h1"/>',
    alert:'<path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/>',
    message:'<path d="M20 15a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3Z"/><path d="M7 8h9M7 12h6"/>',
    partners:'<path d="m3 8 5-4 4 2 4-2 5 4-3 9-5 4-5-2-5-5m5-10 5 4-3 3-3-2m3 2 6 6m-9-4 6 6"/>',
    trophy:'<path d="M7 3h10v7a5 5 0 0 1-10 0ZM7 5H3v3a4 4 0 0 0 4 4m10-7h4v3a4 4 0 0 1-4 4m-5 3v6m-4 0h8"/>',
    check:'<path d="m5 12 4 4L19 6"/>',x:'<path d="m6 6 12 12M6 18 18 6"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    refresh:'<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    building:'<path d="M5 21V3h14v18M9 7h1m4 0h1m-6 4h1m4 0h1m-6 4h1m4 0h1m-5 6v-3h2v3"/>',
    lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
    bell:'<path d="M5 17h14l-2-4V9A5 5 0 0 0 7 9v4Zm5 3h4"/>',
    pin:'<path d="m9 3 9 9-4 1-3 5-2-6-5-2Zm0 9-6 9"/>',
    radio:'<circle cx="12" cy="12" r="2"/><path d="M7 7a7 7 0 0 0 0 10m10-10a7 7 0 0 1 0 10M4 4a11 11 0 0 0 0 16M20 4a11 11 0 0 1 0 16"/>',
    file:'<path d="M5 3h9l5 5v13H5Zm9 0v5h5M9 12h6m-6 4h6"/>',
    target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    clipboard:'<path d="M8 5H5v16h14V5h-3"/><rect x="8" y="3" width="8" height="4" rx="1"/><path d="M8 12h8m-8 4h5"/>',
    user:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    inbox:'<path d="M3 14 6 4h12l3 10v6H3Zm0 0h5l2 3h4l2-3h5"/>',
    wallet:'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8h18m0 5h-5v4h5m-3-2h.01"/>',
    arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
    plus:'<path d="M12 4v16M4 12h16"/>',
    search:'<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>'
  };
  function svg(name){return '<svg class="sl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+(Object.hasOwn(paths,name)?paths[name]:paths.info)+'</svg>';}
  global.SoproIcons={svg};
})(window);
