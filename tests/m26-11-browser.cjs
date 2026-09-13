// Executar com Playwright disponível no NODE_PATH e servidor local em BASE_URL.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.BASE_URL || 'http://127.0.0.1:8111';
const out = path.resolve('artifacts/m26-11');
fs.mkdirSync(out + '/screenshots', { recursive: true });
const pages = ['/', '/conheca/', '/servicos/', '/espirometria/', '/espirometria-rio-de-janeiro/', '/espirometria-domiciliar-rio-de-janeiro/', '/espirometria-ipanema/', '/consulta-pneumologista-rio-de-janeiro/', '/telemedicina/', '/links/', '/funil/', '/funil/espirometria/', '/funil/domiciliar/', '/funil/parcerias/'];
const report = { layouts: [], functional: [], errors: [], maps: [] };
(async () => {
 const browser = await chromium.launch({ headless: true });
 const context = await browser.newContext({ locale: 'pt-BR', timezoneId: 'Asia/Tokyo', reducedMotion: 'reduce' });
 // Nenhum envio de formulário, analytics ou acesso à API clínica nos testes.
 await context.route(/.*(?:google-analytics\.com|googletagmanager\.com|api\.soprolife|painel-soprolife).*/, r => r.abort());
 const p = await context.newPage();
 p.on('pageerror', e => report.errors.push({ page: p.url(), message: e.message }));
 await p.clock.install({ time: new Date('2026-09-13T15:00:00Z') });
 for (const width of [1440, 1024, 768, 430, 390]) {
  await p.setViewportSize({ width, height: width > 800 ? 1000 : 844 });
  for (const route of pages) {
   await p.goto(base + route, { waitUntil: 'load' });
   await p.waitForTimeout(150);
   const refuse = p.getByRole('button', { name: 'Recusar', exact: true });
   if (await refuse.isVisible()) await refuse.click();
   const metrics = await p.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: innerWidth,
    offenders: [...document.querySelectorAll('body *')].filter(e => { const r=e.getBoundingClientRect();return r.width>0 && r.right>innerWidth+1 && r.left>=0 && getComputedStyle(e).position!=='fixed' && !e.closest('.leaflet-container,.sl-map-modal,.flatpickr-calendar'); }).slice(0,6).map(e=>e.className) }));
   report.layouts.push({ width, route, ...metrics });
   if (metrics.scroll > width + 1) console.log('OVERFLOW', width, route, metrics);
   const menu = p.locator('.sl-menu-toggle');
   if (await menu.isVisible()) {
    await menu.click(); assert.equal(await menu.getAttribute('aria-expanded'), 'true');
    assert(await p.locator('#sl-public-navigation').isVisible());
    await menu.press('Escape'); assert.equal(await menu.getAttribute('aria-expanded'), 'false');
   }
   if (['/', '/espirometria-rio-de-janeiro/'].includes(route) && [1440,390].includes(width)) {
    await p.evaluate(() => document.activeElement.blur());
    const name = (route === '/' ? 'home' : 'espirometria') + (width===1440 ? '-desktop' : '-mobile');
    await p.screenshot({ path: out + '/screenshots/' + name + '.png' });
    await p.locator('#agendamento').scrollIntoViewIfNeeded();
    await p.waitForSelector('.leaflet-tile-loaded', { timeout: 15000 });
    await p.waitForTimeout(500);
    await p.locator('.sl-slot-btn').first().click();
    await p.locator('.sl-booking-card').screenshot({ path: out + '/screenshots/' + name + '-agenda.png', style: '.sl-header, .sl-whatsapp-bar { visibility: hidden !important; }' });
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
  for (const value of ['Atendimento domiciliar','Unidade Barra','Unidade Zona Norte']) {
   await p.selectOption('#sl-booking-unit', value);
   assert.deepEqual(await p.locator('.sl-slot-btn').allTextContents(), ['08:00','09:00','10:00','11:00','13:00','14:00','15:00','16:00']);
   for (const slot of await p.locator('.sl-slot-btn').all()) assert(await slot.isEnabled());
   await p.locator('.sl-slot-btn').nth(3).click();
   assert.equal(await p.locator('.sl-slot-btn[aria-pressed="true"]').textContent(),'11:00');
   const url = new URL(await p.locator('.sl-booking-confirm').getAttribute('href'));
   assert.equal(url.searchParams.get('phone'),'5521998901775');
   const message=url.searchParams.get('text');
   for(const text of ['Espirometria simples',value,'14/09/2026','11:00']) assert(message.includes(text));
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
  report.functional.push({route, result:'8 horários abertos, seleção, WhatsApp completo, tipo, unidades, domingo e agenda Pastore preservada'});
 }
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
  assert(map.tiles); assert(map.attribution.includes('OpenStreetMap')); assert.equal(map.markers,route.includes('ipanema')?1:3);
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
 // Depoimentos visíveis sem animação e navegação manual funcionando.
 await p.goto(base+'/');
 const carousel=p.locator('.sl-ttrack');
 await carousel.scrollIntoViewIfNeeded();
 assert.equal(await p.locator('.sl-tpage').first().evaluate(e=>getComputedStyle(e).opacity),'1');
 await p.getByRole('button',{name:'Próximo depoimento',exact:true}).click();
 assert(await carousel.evaluate(e=>e.scrollLeft>0));
 report.functional.push({result:'Depoimentos: reduced-motion e navegação manual pass'});
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
