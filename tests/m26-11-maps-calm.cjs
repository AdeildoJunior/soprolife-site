// Mapas reais em Chromium. Sem envio de mensagens ou interação com API clínica.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.BASE_URL || 'http://127.0.0.1:8111';
const out = 'artifacts/m26-11/screenshots';
const results = { layouts: [], interactions: [], errors: [], tiles: [] };
(async()=>{
 const browser = await chromium.launch();
 const context = await browser.newContext({locale:'pt-BR',reducedMotion:'reduce'});
 await context.route(/.*(?:google-analytics\.com|googletagmanager\.com|api\.soprolife|painel-soprolife).*/,r=>r.abort());
 const page = await context.newPage();
 page.on('pageerror',e=>results.errors.push(e.message));
 page.on('response',r=>{if(r.url().startsWith('https://tile.openstreetmap.org/'))results.tiles.push({url:r.url(),status:r.status()});});
 async function loaded(selector) {
  await page.waitForFunction(s=>{const e=document.querySelector(s);return e && e.querySelectorAll('.leaflet-tile-loaded').length>0 && [...e.querySelectorAll('.leaflet-tile')].every(i=>i.complete && i.naturalWidth===256);},selector);
  await page.waitForTimeout(350);
  assert.equal(await page.locator(selector+' .sl-map-fallback').count(),0);
 }
 async function within(selector) {
  assert(await page.locator(selector).evaluate(node=>{
   const r=node.getBoundingClientRect();
   return [...node.querySelectorAll('.sl-calm-marker')].every(pin=>{const p=pin.getBoundingClientRect();return p.left>=r.left&&p.top>=r.top&&p.right<=r.right&&p.bottom<=r.bottom;});
  }),'fitBounds deve mostrar todos os pins');
 }
 for (const width of [1440,1024,768,430,390]) {
  await page.setViewportSize({width,height:width>=1024?1000:844});
  for (const route of ['/','/espirometria-rio-de-janeiro/','/espirometria-ipanema/']) {
   const ip = route.includes('ipanema');
   const mini = ip?'#sl-ip-map-mini-canvas':'#sl-units-map-mini';
   const large = ip?'#sl-ip-map':'#sl-units-map';
   await page.goto(base+route);
   const refuse=page.getByRole('button',{name:'Recusar',exact:true});if(await refuse.isVisible())await refuse.click();
   await page.locator(mini).scrollIntoViewIfNeeded(); await loaded(mini); await within(mini);
   assert.equal(await page.locator(mini+' .sl-calm-marker').count(),ip?1:3);
   if(!ip) {
    const barra=await page.evaluate(()=>SL_BOOKING.byId('barra'));
    assert.deepEqual(barra.coords,{lat:-23.0029554,lng:-43.3176673});
    assert(barra.address.includes('Shopping Downtown') && barra.address.includes('sala 213'));
   }
   const filter=await page.locator(mini+' .leaflet-tile-pane').evaluate(e=>getComputedStyle(e).filter);
   assert(filter.includes('saturate(0.65)'));
   assert.equal(await page.locator(mini+' .leaflet-marker-pane').evaluate(e=>getComputedStyle(e).filter),'none');
   if(route==='/espirometria-rio-de-janeiro/' && [1440,390].includes(width)) {
    await page.locator('.sl-booking-card').screenshot({path:out+'/mapa-normal-'+(width===1440?'desktop':'390px')+'.png',style:'.sl-header,.sl-whatsapp-bar{visibility:hidden!important}'});
   }
   const open=page.locator(ip?'#sl-ip-open-map':'#sl-open-map-modal'); await open.click(); await loaded(large); await within(large);
   const layout=await page.locator('.sl-map-calm-modal').evaluate(e=>{
    const dialog=e.querySelector('[role="dialog"]').getBoundingClientRect();
    const map=e.querySelector('.sl-map-calm').getBoundingClientRect();
    const side=e.querySelector('.sl-map-modal__info').getBoundingClientRect();
    return {viewport:innerWidth,dialog:dialog.toJSON(),map:map.toJSON(),side:side.toJSON(),overflow:document.documentElement.scrollWidth>innerWidth};
   });
   assert(!layout.overflow); assert(layout.dialog.left>=0 && layout.dialog.right<=width);
   if(width>800) {assert(Math.abs(layout.dialog.width-width*.9)<2);assert(layout.map.width/layout.dialog.width>.65);}
   else {assert(layout.map.bottom<=layout.side.top+1);assert(layout.map.height>=240);}
   assert((await page.locator(large+' .leaflet-control-attribution').textContent()).includes('OpenStreetMap'));
   results.layouts.push({width,route,...layout});
   if(route==='/espirometria-rio-de-janeiro/' && [1440,390].includes(width))await page.screenshot({path:out+'/mapa-ampliado-'+(width===1440?'desktop':'390px')+'.png'});
   if(width>800) {
    const zoomBefore=await page.locator(large+' .leaflet-tile-loaded').first().evaluate(e=>Number(new URL(e.src).pathname.split('/')[1]));
    const bounds=await page.locator(large).boundingBox();
    await page.mouse.move(bounds.x+bounds.width/2,bounds.y+bounds.height/2);
    await page.mouse.wheel(0,-400);
    await page.waitForFunction(({selector,zoom})=>[...document.querySelectorAll(selector+' .leaflet-tile-loaded')].some(e=>Number(new URL(e.src).pathname.split('/')[1])>zoom),{selector:large,zoom:zoomBefore});
    await page.locator('.sl-map-overview').click(); await loaded(large);
    results.interactions.push({width,route,result:'zoom pela roda do mouse: pass'});
   }
   // Lista -> pin -> formulário. Selecionar não abre outro site.
   const id=ip?'pastore-ipanema':'barra';
   await page.locator('.sl-map-unit-select[data-map-location="'+id+'"]').click();
   assert.equal(await page.locator('.sl-map-unit-select[aria-pressed="true"]').count(),1);
   assert.equal(await page.locator(large+' .sl-calm-marker.is-active').count(),1);
   if(!ip) assert.equal(await page.locator('#sl-booking-unit').inputValue(),'Unidade Barra');
   await page.locator(large+' .sl-calm-marker.is-active').click();
   assert(await page.locator(large+' .sl-map-popup').isVisible());
   assert.equal(await page.locator(large+' .sl-map-popup a').count(),2);
   assert.equal(context.pages().length,1);
   const href=await page.locator(large+' .sl-map-popup a').last().getAttribute('href');
   assert(href.includes('api.whatsapp.com/send?phone=5521998901775'));
   if(!ip) {
    assert((await page.locator(large+' .sl-map-popup').textContent()).includes('prédio 20, sala 213'));
    assert(new URL(href).searchParams.get('text').includes('Shopping Downtown'));
    const routeLink=new URL(await page.locator(large+' .sl-map-popup a').first().getAttribute('href'));
    assert(routeLink.searchParams.get('destination').includes('Shopping Downtown'));
    if(width===1440 && route==='/espirometria-rio-de-janeiro/') {
     for(let i=0;i<3;i++) {await page.locator(large+' .leaflet-control-zoom-in').click();await page.waitForTimeout(300);}
     await loaded(large);
     await page.screenshot({path:out+'/mapa-downtown-predio20.png'});
    }
   }
   if(!ip && width===1440) {
    await page.locator('.sl-map-overview').click(); await loaded(large);
    // Outro pin altera a lista, não apenas o mesmo selecionado.
    await page.locator(large+' .sl-calm-marker[title="Unidade Zona Norte"]').click();
    assert.equal(await page.locator('.sl-map-unit-select[data-map-location="zona-norte"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#sl-booking-unit').inputValue(),'Unidade Zona Norte');
    await page.screenshot({path:out+'/mapa-pin-selecionado-desktop.png'});
   }
   await page.keyboard.press('Escape');assert.equal(await page.locator('.sl-map-calm-modal.is-open').count(),0);
   assert(await open.evaluate(e=>document.activeElement===e));
   results.interactions.push({width,route,result:'seleção, popup, WhatsApp inspecionado, Escape e foco: pass'});
  }
 }
 // Redimensionamento com modal aberto: observa tamanho e reenquadra pins.
 await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/');
 await page.locator('#sl-open-map-modal').scrollIntoViewIfNeeded();await page.locator('#sl-open-map-modal').click();await loaded('#sl-units-map');
 await page.setViewportSize({width:390,height:844});await loaded('#sl-units-map');await within('#sl-units-map');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // Mini pin abre popup, sem abrir modal nem WhatsApp.
 await page.keyboard.press('Escape');await page.locator('#sl-units-map-mini').scrollIntoViewIfNeeded();await loaded('#sl-units-map-mini');
 await page.locator('#sl-units-map-mini .sl-calm-marker').first().click();
 assert(await page.locator('#sl-units-map-mini .sl-map-popup').isVisible());
 assert.equal(await page.locator('.sl-map-calm-modal.is-open').count(),0);
 assert.equal(context.pages().length,1);
 results.interactions.push({result:'resize aberto 1440→390 e mini popup: pass'});
 assert.equal(results.errors.length,0);
 assert(results.tiles.length>0 && results.tiles.every(x=>x.status===200));
 fs.writeFileSync('artifacts/m26-11/maps-calm-results.json',JSON.stringify(results,null,2));
 await browser.close();
 console.log('PASS: 15 layouts de mapas, interações bidirecionais, pins, Downtown, tiles reais, attribution, resize e 6 screenshots.');
})().catch(e=>{fs.writeFileSync('artifacts/m26-11/maps-calm-results.json',JSON.stringify(results,null,2));console.error(e);process.exit(1);});
