// Public CTA instrumentation in a real browser, with all external network blocked.
const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');const root=path.resolve(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const context=await browser.newContext({locale:'pt-BR',serviceWorkers:'block'});let external=0;
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(u.origin!=='https://soprolife.test'){external++;return route.abort();}let p=decodeURIComponent(u.pathname);if(p.endsWith('/'))p+='index.html';const f=path.join(root,p);if(!f.startsWith(root+path.sep)||!fs.existsSync(f)||!fs.statSync(f).isFile())return route.fulfill({status:404,body:''});return route.fulfill({path:f});});
 const page=await context.newPage();
 const paths=['/','/espirometria-rio-de-janeiro/','/espirometria-barra-da-tijuca/','/espirometria-ipanema/','/espirometria-zona-norte-rio-de-janeiro/','/espirometria-domiciliar-rio-de-janeiro/'];
 for(const route of paths){
  await page.goto('https://soprolife.test'+route,{waitUntil:'domcontentloaded'});
  await page.evaluate(()=>localStorage.setItem('sl_consent_v1','denied'));await page.reload({waitUntil:'load'});
  assert(!await page.evaluate(()=>Boolean(window.gtag)),'GA must not load with denied consent');
  await page.evaluate(()=>localStorage.setItem('sl_consent_v1','granted'));
  await page.goto('https://soprolife.test'+route+'?utm_source=google&utm_medium=organic&utm_campaign=perfil_empresa&email=private@example.test',{waitUntil:'load'});
  await page.evaluate(()=>{window.__events=[];window.gtag=(...args)=>window.__events.push(args);document.addEventListener('click',e=>e.preventDefault(),true);});
  const wa=page.locator('a[href*="wa.me/"],a[href*="api.whatsapp.com/"],a[href*="web.whatsapp.com/"]').first();assert(await wa.count(),'public WhatsApp CTA exists '+route);await wa.evaluate(el=>el.click());
  let events=await page.evaluate(()=>window.__events);
  assert.equal(events.filter(e=>['click_whatsapp','click_whatsapp_ipanema'].includes(e[1])).length,1,'one canonical WhatsApp event '+route);
  const tel=page.locator('a[href^="tel:"]').first();if(await tel.count()){await tel.evaluate(el=>el.click());assert((await page.evaluate(()=>window.__events)).some(e=>e[1]==='click_telefone'));}
  const booking=page.locator('a[href="#agendamento"],a[href="#agendar"]').first();if(await booking.count()){await booking.evaluate(el=>el.click());assert((await page.evaluate(()=>window.__events)).some(e=>e[1]==='click_agendamento'));}
  events=await page.evaluate(()=>window.__events);const json=JSON.stringify(events);assert(!/private@|phone=|text=|tel:|patient_id|person_id/.test(json));
  for(const e of events){assert.equal(e[2].page_path,route);assert.equal(e[2].session_source,'google_business_profile');}
  console.log(route+': consent + CTA canonical/deduplicated + anonymous parameters PASS');
 }
 console.log('External requests blocked in test: '+external);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
