// Executar com Playwright disponível no NODE_PATH e servidor local em BASE_URL.
const { chromium, firefox } = require('playwright');
const engine = process.env.ENGINE || 'chromium';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:8112';
const out = path.resolve(process.env.ARTIFACTS || '/tmp/soprolife-editorial-' + engine);
fs.mkdirSync(out + '/screenshots', { recursive: true });
const pages = ['/', '/conheca/', '/servicos/', '/espirometria/', '/espirometria-rio-de-janeiro/', '/espirometria-domiciliar-rio-de-janeiro/', '/espirometria-ipanema/', '/consulta-pneumologista-rio-de-janeiro/', '/telemedicina/', '/espirometria-barra-da-tijuca/', '/espirometria-zona-norte-rio-de-janeiro/'];
const report = { layouts: [], functional: [], errors: [], maps: [] };
(async () => {
 const browser = await (engine === 'firefox' ? firefox : chromium).launch({ headless: true });
 const context = await browser.newContext({ locale: 'pt-BR', timezoneId: 'Asia/Tokyo', reducedMotion: 'reduce' });
 // Nenhum envio de formulário, analytics ou acesso à API clínica nos testes.
 await context.route(/.*(?:google-analytics\.com|googletagmanager\.com|api\.soprolife|painel-soprolife).*/, r => r.abort());
 const p = await context.newPage();
 p.on('pageerror', e => report.errors.push({ page: p.url(), message: e.message }));
 await p.clock.install({ time: new Date('2026-09-13T15:00:00Z') });
 for (const width of [1440, 1024, 768, 390, 320]) {
  await p.setViewportSize({ width, height: width > 800 ? 1000 : 844 });
  for (const route of pages) {
   await p.goto(base + route, { waitUntil: 'load' });
   await p.waitForTimeout(150);
   const refuse = p.getByRole('button', { name: 'Recusar', exact: true });
   if (await refuse.isVisible()) {
    const consent = await p.locator('#sl-consent').boundingBox();
    const bar = await p.locator('.ed-contact-bar').boundingBox();
    assert(consent.y + consent.height <= bar.y, 'cookies sobre a barra fixa');
    await refuse.click();
   }
   assert.equal(await p.locator('.ed-contact-bar').count(),1);
   const barMetrics = await p.locator('.ed-contact-bar').evaluate(e => {
    const r=e.getBoundingClientRect(), a=e.querySelector('a'), text=e.querySelector('p');
    return {position:getComputedStyle(e).position,bottom:r.bottom,width:r.width,height:r.height,
      sideBySide:text.getBoundingClientRect().right<=a.getBoundingClientRect().left,
      phone:new URL(a.href).searchParams.get('phone')};
   });
   assert.equal(barMetrics.position,'fixed');
   assert.equal(barMetrics.bottom,width > 800 ? 1000 : 844);
   assert.equal(barMetrics.width,width);
   assert(barMetrics.height<=88);
   assert(barMetrics.sideBySide);
   assert.equal(barMetrics.phone,'5521998901775');
   const metrics = await p.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth,
    offenders: [...document.querySelectorAll('body *')].filter(e => { const r=e.getBoundingClientRect();return r.width>0 && r.right>innerWidth+1 && r.left>=0 && getComputedStyle(e).position!=='fixed' && !e.closest('.leaflet-container,.sl-map-modal,.flatpickr-calendar'); }).slice(0,6).map(e=>e.className) }));
   report.layouts.push({ width, route, ...metrics });
   if (metrics.scroll > width + 1) console.log('OVERFLOW', width, route, metrics);
   const menu = p.locator('.sl-menu-toggle');
   if (width <= 1100) assert(await menu.isVisible(), route + ': menu móvel ausente');
   if (await menu.isVisible()) {
    await menu.click(); assert.equal(await menu.getAttribute('aria-expanded'), 'true');
    assert(await p.locator('#sl-public-navigation').isVisible());
    await menu.press('Escape'); assert.equal(await menu.getAttribute('aria-expanded'), 'false');
   }
   if (['/', '/espirometria-rio-de-janeiro/'].includes(route) && [1440,390].includes(width)) {
    await p.evaluate(() => document.activeElement.blur());
    const name = (route === '/' ? 'home' : 'espirometria') + (width===1440 ? '-desktop' : '-mobile');
    await p.screenshot({ path: out + '/screenshots/' + name + '.png' });
    // O mapa da home agora é independente da agenda e carrega ao entrar na tela.
    await p.locator('#sl-units-map-mini').scrollIntoViewIfNeeded();
    await p.waitForSelector('.leaflet-tile-loaded', { timeout: 15000 });
    await p.waitForTimeout(500);
    await p.locator('#agendamento').scrollIntoViewIfNeeded();
    await p.locator('.sl-slot-btn').first().click();
    await p.locator('.sl-booking-card').screenshot({ path: out + '/screenshots/' + name + '-agenda.png', style: '.sl-header, .sl-whatsapp-bar, .ed-contact-bar { visibility: hidden !important; }' });
    await p.evaluate(() => window.scrollTo(0, 0));
    await p.screenshot({ path: out + '/screenshots/' + name + '-completa.png', fullPage: true });
   }
  }
 }
 console.log('Layouts examinados:', report.layouts.length);
 await p.setViewportSize({width:1440,height:1000});
 for (const route of ['/', '/espirometria-rio-de-janeiro/', '/espirometria-domiciliar-rio-de-janeiro/']) {
  await p.goto(base+route);
  await p.waitForSelector('.sl-slot-btn');
  assert.equal(await p.locator('#sl-booking-date').inputValue(), '2026-09-14');
  for (const value of ['Atendimento domiciliar','Unidade Barra','Unidade Zona Norte — Shopping Nova América']) {
   await p.selectOption('#sl-booking-unit', value);
   assert.deepEqual(await p.locator('.sl-slot-btn').allTextContents(), ['08:00','09:00','10:00','11:00','13:00','14:00','15:00','16:00']);
   for (const slot of await p.locator('.sl-slot-btn').all()) assert(await slot.isEnabled());
   await p.locator('.sl-slot-btn').nth(3).click();
   assert.equal(await p.locator('.sl-slot-btn[aria-pressed="true"]').textContent(),'11:00');
   assert((await p.locator('.sl-booking-summary').textContent()).includes(value));
   const url = new URL(await p.locator('.sl-booking-confirm').getAttribute('href'));
   assert.equal(url.searchParams.get('phone'),'5521998901775');
   const message=url.searchParams.get('text');
   for(const text of ['Espirometria simples',value,'14/09/2026','11:00']) assert(message.includes(text));
   if(value.includes('Shopping Nova América')) {
    assert(message.includes('Av. Pastor Martin Luther King Jr., 126'));
    assert(message.includes('Sala e ponto de encontro confirmados no agendamento.'));
   }
   // Troca de exame limpa o horário anterior e atualiza o resumo/WhatsApp.
   await p.selectOption('#sl-booking-service','Espirometria com broncodilatador');
   assert(await p.locator('.sl-booking-confirm').isHidden());
   await p.locator('.sl-slot-btn').last().click();
   assert((await p.locator('.sl-booking-confirm').getAttribute('href')).includes('broncodilatador'));
   await p.selectOption('#sl-booking-service','Espirometria simples');
  }
  // Domingo digitado/programático: explica e avança para segunda.
  await p.evaluate(()=>{const d=document.querySelector('#sl-booking-date');d.value='2026-09-20';d.dispatchEvent(new Event('change'));});
  assert.equal(await p.locator('#sl-booking-date').inputValue(),'2026-09-21');
  assert((await p.locator('.sl-field-hint').textContent()).includes('Esse dia não tem atendimento'));
  if(route !== '/espirometria-domiciliar-rio-de-janeiro/') {
   await p.selectOption('#sl-booking-unit','Centro Médico Pastore — Ipanema');
   assert.equal(await p.locator('#sl-booking-date').inputValue(),'2026-09-15');
   assert.equal(await p.locator('.sl-slot-btn').count(),9);
   assert(await p.locator('.sl-ipanema-panel').isVisible());
  }
  // Norte Shopping: só segundas, 13h–17h, e sempre pelo WhatsApp da SoproLife.
  // Vindo de Ipanema (terça 15/09) volta para a 1ª segunda; na domiciliar a data anterior já é segunda (21/09).
  await p.selectOption('#sl-booking-unit','Unidade Norte Shopping');
  const nsDate = route === '/espirometria-domiciliar-rio-de-janeiro/' ? '2026-09-21' : '2026-09-14';
  assert.equal(await p.locator('#sl-booking-date').inputValue(),nsDate);
  assert.deepEqual(await p.locator('.sl-slot-btn').allTextContents(), ['13:00','13:30','14:00','14:30','15:00','15:30','16:00','16:30','17:00']);
  assert(await p.locator('.sl-ipanema-panel').isHidden());
  await p.locator('.sl-slot-btn').last().click();
  const ns = new URL(await p.locator('.sl-booking-confirm').getAttribute('href'));
  assert.equal(ns.hostname,'api.whatsapp.com');
  assert.equal(ns.searchParams.get('phone'),'5521998901775');
  for (const text of ['Unidade Norte Shopping','Av. Dom Hélder Câmara, 5200',nsDate.split('-').reverse().join('/'),'17:00']) assert(ns.searchParams.get('text').includes(text));
  await p.evaluate(()=>{const d=document.querySelector('#sl-booking-date');d.value='2026-09-15';d.dispatchEvent(new Event('change'));});
  assert.equal(await p.locator('#sl-booking-date').inputValue(),'2026-09-21');
  report.functional.push({route, result:'8 horários abertos, seleção, WhatsApp completo, tipo, unidades, domingo, agenda Pastore preservada e Norte Shopping às segundas'});
 }
 // Landing Zona Norte: começa no Nova América; o Norte Shopping troca o mapa e segue pela SoproLife.
 await p.goto(base+'/espirometria-zona-norte-rio-de-janeiro/');
 await p.waitForSelector('.sl-slot-btn'); await p.waitForTimeout(400);
 assert.equal(await p.locator('#sl-booking-unit').inputValue(),'Unidade Zona Norte — Shopping Nova América');
 assert.equal(await p.locator('.sl-slot-btn').count(),8);
 await p.selectOption('#sl-booking-unit','Unidade Norte Shopping');
 assert.equal(await p.locator('.sl-slot-btn').count(),9);
 assert((await p.locator('#sl-zn-map-frame').getAttribute('src')).includes('Dom%20H%C3%A9lder'));
 assert((await p.locator('#sl-zn-map-name').textContent()).includes('Sopro Life — Norte Shopping'));
 await p.locator('.sl-slot-btn').first().click();
 assert.equal(new URL(await p.locator('.sl-booking-confirm').getAttribute('href')).searchParams.get('phone'),'5521998901775');
 await p.selectOption('#sl-booking-unit','Unidade Zona Norte — Shopping Nova América');
 assert((await p.locator('#sl-zn-map-frame').getAttribute('src')).includes('Nova%20Am%C3%A9rica'));
 report.functional.push({route:'/espirometria-zona-norte-rio-de-janeiro/', result:'Nova América padrão, Norte Shopping com 9 horários, mapa acompanha a unidade, WhatsApp SoproLife'});
 // Calendário civil sob relógios reais simulados; o contexto do visitante está em Tóquio.
 for (const [instant, expected] of [
  ['2026-09-13T01:30:00Z','2026-09-14'], // ainda sábado em São Paulo
  ['2026-09-30T15:00:00Z','2026-10-01'],
  ['2026-12-31T15:00:00Z','2027-01-01'],
  ['2026-09-18T15:00:00Z','2026-09-19'],
  ['2026-09-19T15:00:00Z','2026-09-21']
 ]) {
  await p.clock.setFixedTime(new Date(instant)); await p.goto(base+'/espirometria-rio-de-janeiro/');
  assert.equal(await p.locator('#sl-booking-date').inputValue(),expected);
  report.functional.push({instant,expected, result:'pass'});
 }
 const invalid = await p.evaluate(()=>SL_BOOKING.validateDate(SL_BOOKING.byId('barra'),'2026-02-31','2026-01-01','2027-01-01'));
 assert.equal(invalid.reason,'invalid');
 await p.clock.setFixedTime(new Date('2026-09-13T15:00:00Z'));
 for (const route of ['/', '/espirometria-rio-de-janeiro/', '/espirometria-ipanema/']) {
  await p.goto(base+route);
  const trigger = p.locator('#sl-open-map-modal, #sl-ip-open-map');
  // Ipanema possui ids próprios: o texto é a alternativa sem depender do id.
  const open = await trigger.count() ? trigger.first() : p.getByRole('button',{name:/Ampliar mapa/}).first();
  await open.scrollIntoViewIfNeeded(); await open.click();
  await p.waitForSelector('.sl-map-modal.is-open .leaflet-tile-loaded');
  const map = await p.locator('.sl-map-modal.is-open').evaluate(e=>({
   attribution:e.querySelector('.leaflet-control-attribution')?.textContent,
   markers:e.querySelectorAll('.leaflet-marker-icon').length,
   tiles:[...e.querySelectorAll('.leaflet-tile')].every(x=>x.src.startsWith('https://tile.openstreetmap.org/'))
  }));
  assert(map.tiles); assert(map.attribution.includes('OpenStreetMap')); assert.equal(map.markers,route.includes('ipanema')?1:4);
  await p.keyboard.press('Escape'); assert.equal(await p.locator('.sl-map-modal.is-open').count(),0);
  report.maps.push({route,...map});
 }
 // Mapas e diálogo também em celular, com retorno de foco e sem overflow.
 await p.setViewportSize({width:390,height:844});
 for (const route of ['/', '/espirometria-rio-de-janeiro/', '/espirometria-ipanema/']) {
  await p.goto(base+route);
  const open=p.locator('#sl-open-map-modal, #sl-ip-open-map');
  await open.scrollIntoViewIfNeeded(); await open.click();
  await p.waitForSelector('.sl-map-modal.is-open .leaflet-tile-loaded');
  assert(await p.locator('.sl-map-modal.is-open [role="dialog"]').isVisible());
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await p.keyboard.press('Escape');
  assert(await open.evaluate(e=>document.activeElement===e));
  report.maps.push({route,width:390,result:'mapa, modal, Escape e retorno de foco: pass'});
 }
 // A nova identidade não publica depoimentos sem procedência confirmada.
 await p.goto(base+'/');
 assert.equal(await p.locator('#depoimentos,.sl-testimonials').count(),0);
 await p.locator('.ed-faq summary').first().click();
 assert(await p.locator('.ed-faq details').first().evaluate(e=>e.open));
 // Reflow adicional com ampliação CSS de 200% (não simula zoom da interface do navegador).
 await p.setViewportSize({width:1440,height:1000});
 await p.evaluate(()=>document.documentElement.style.zoom='2');
 assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow em ampliação 200%');
 await p.screenshot({path:out+'/screenshots/home-200percent.png'});
 report.functional.push({result:'FAQ, ausência de depoimentos e ampliação CSS 200%: pass'});
 // Sem CDN: calendário nativo e agenda continuam utilizáveis.
 const offline = await browser.newContext({locale:'pt-BR'});
 await offline.route(/https:\/\/.*/,r=>r.abort());
 const fallback = await offline.newPage();await fallback.goto(base+'/espirometria-rio-de-janeiro/');await fallback.waitForTimeout(4500);
 assert.equal(await fallback.locator('#sl-booking-date').getAttribute('type'),'date');
 assert.equal(await fallback.locator('.sl-slot-btn').count(),8);
 report.functional.push({result:'CDNs bloqueados: calendário nativo e 8 horários funcionam'});
 await offline.close();
 fs.writeFileSync(out+'/test-results.json',JSON.stringify(report,null,2));
 await browser.close();
 assert.equal(report.layouts.filter(x=>x.scroll>x.width+1).length,0,'overflow horizontal');
 assert.equal(report.errors.length,0,'erros JavaScript');
 console.log('PASS — layouts, agenda, datas, WhatsApp e mapas; screenshots em '+out+'/screenshots');
})().catch(e=>{fs.writeFileSync(out+'/test-results.json',JSON.stringify(report,null,2));console.error(e);process.exit(1)});
