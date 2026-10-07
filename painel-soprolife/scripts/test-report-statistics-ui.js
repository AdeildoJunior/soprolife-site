#!/usr/bin/env node
/* API simulada, nenhum dado real ou tráfego externo. Verifica o botão real,
   modal, filtros, recuperação, sessões e proporção do canvas nos dois engines. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium, firefox} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const out = process.env.STATISTICS_SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'sopro-statistics-'));
fs.mkdirSync(out, {recursive:true});
const series = (values) => Object.entries(values).map(([rotulo, quantidade]) => ({chave:rotulo,rotulo,quantidade}));
const sample = {
  escopo:'institucional', filtros:{inicio:null,fim:null,origem:null},
  totais:{laudos:20,com_conclusao:18,normais:10,sem_conclusao_publicada:2,sem_data_completa:0},
  evolucao:[{mes:'2026-07',quantidade:4},{mes:'2026-08',quantidade:7},{mes:'2026-09',quantidade:9}],
  resultados:series({'Normal':10,'Obstrutivo':4,'Sugestivo de restritivo':2,'Personalizado':2}),
  conclusoes:series({'Normal':10,'DVO Leve':3,'DVO Moderado':1,'DVR sug. Leve':2,'Personalizado':2}),
  broncodilatador:series({'RBD+':3,'RBD−':10,'BD não realizado':4,'Não registrado':1}),
  personalizados:{total:2, ignorado_na_classificacao:['volumes pulmonares / complementar — conduta sugerida, não achado'],
   categorias:[
    {chave:'reducao_cvf_vef1',rotulo:'Redução de CVF e VEF1',quantidade:1,percentual:50.0,
     descricao:'Quantidade de laudos em que essa conclusão foi explicitamente registrada pela médica. O texto cita redução e nomeia os dois parâmetros.',
     criterio:'Termo de redução + CVF + VEF1 no texto da conclusão.'},
    {chave:'reducao_cvf',rotulo:'Redução isolada de CVF',quantidade:1,percentual:50.0,
     descricao:'Quantidade de laudos em que essa conclusão foi explicitamente registrada pela médica. O texto cita redução de CVF e não menciona VEF1.',
     criterio:'Termo de redução + CVF, sem VEF1 no texto da conclusão.'},
    {chave:'reducao_vef1',rotulo:'Redução isolada de VEF1',quantidade:0,percentual:0.0,
     descricao:'Quantidade de laudos em que essa conclusão foi explicitamente registrada pela médica. O texto cita redução de VEF1 e não menciona CVF.',
     criterio:'Termo de redução + VEF1, sem CVF no texto da conclusão.'},
    {chave:'nao_classificavel',rotulo:'Não classificável com segurança',quantidade:0,percentual:0.0,
     descricao:'Laudos cujo texto não corresponde explicitamente a nenhuma categoria acima. Nenhuma classificação é atribuída a eles.',
     criterio:'Nenhuma correspondência explícita, ou presença de negação.'}]},
  faixa_etaria:series({'Até 17 anos':2,'18–39 anos':3,'40–59 anos':6,'60–79 anos':7,'80 anos ou mais':2}),
  sexo:series({'Feminino':12,'Masculino':8}),
  origens:series({'Consultório / coworking':11,'Domiciliar':9}),
  opcoes_origem:[{chave:'residencial',rotulo:'Domiciliar'},{chave:'coworking',rotulo:'Consultório / coworking'}],
};
(async()=>{
for(const [engine, launcher] of Object.entries({chromium,firefox})) {
 const browser=await launcher.launch();
 for(const width of [1440,1024,390,320]) {
  const page=await browser.newPage({viewport:{width,height:1000}});
  const errors=[];page.on('pageerror', e=>errors.push(e.message));
  await page.route('**/*',r=>r.abort());
  await page.setContent('<html lang="pt-BR"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><section id="laudos-espirometria" class="section active"><div id="reportWorkflowRoot"></div></section></main></body></html>');
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  for(const m of html.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) await page.addStyleTag({content:fs.readFileSync(path.join(root,m[1]),'utf8')});
  await page.addStyleTag({content:fs.readFileSync(path.join(root,'css/m15.css'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'js/vendor/chart.umd.min.js'),'utf8')});
  await page.evaluate(sample=>{
   window.sample=sample;window.calls=[];window.sessionListeners=[];window.testRole='admin';window.logged=true;window.reply='ok';
   window.fetch=async()=>new Response(JSON.stringify({enabled:true,reports_enabled:true,reports_mode:'pilot',api_base:'/painel-soprolife/api/m15'}));
   window.SoproM15={
    getUser:()=>({id:'synthetic',nome:'Usuário sintético',papeis:[window.testRole]}),
    hasToken:()=>window.logged,
    can:role=>window.testRole==='admin' || role===window.testRole,
    onSessionChange:cb=>window.sessionListeners.push(cb),
    api:async(url, options)=>{
     window.calls.push({url,method:options?.method || 'GET'});
     if(!url.startsWith('/laudos/estatisticas')) return [];
     if(window.reply==='error') throw Error('Erro sintético');
     if(window.reply==='slow') await new Promise(r=>window.resolveSlow=r);
     const data=structuredClone(window.sample),u=new URL(url,'https://sintetico.test');
     data.filtros={inicio:u.searchParams.get('inicio'),fim:u.searchParams.get('fim'),origem:u.searchParams.get('origem')};
     data.escopo=window.testRole==='medico' ? 'meus_laudos' : 'institucional';
     if(window.reply==='empty') {data.totais={laudos:0,com_conclusao:0,normais:0,sem_conclusao_publicada:0,sem_data_completa:0};data.personalizados={total:0,ignorado_na_classificacao:[],categorias:[]};}
     return data;
    },
   };
  },sample);
  for(const file of ['js/report-statistics.js','js/report-workflow.js']) await page.addScriptTag({content:fs.readFileSync(path.join(root,file),'utf8')});
  const button=page.locator('[data-report-statistics]');await button.waitFor();
  await page.evaluate(()=>{const t=document.createElement('textarea');t.id='unsaved';t.value='Texto clínico sintético em edição';document.querySelector('#reportWorkflowRoot').appendChild(t)});
  await button.click();
  await page.waitForSelector('#rsEvolution');
  await page.waitForTimeout(150);
  assert.equal(await page.locator('.rs-kpis article').count(),4);
  assert.equal(await page.locator('.rs-chart canvas').count(),7);
  const bounds=await page.evaluate(()=>({
   overflow:document.querySelector('.rs-body').scrollWidth>document.querySelector('.rs-body').clientWidth+1,
   charts:[...document.querySelectorAll('.rs-chart canvas')].map(c=>{const r=c.getBoundingClientRect(),p=c.parentElement.getBoundingClientRect(),chart=Chart.getChart(c);return{ratio:Math.abs(r.width/r.height-chart.width/chart.height),spill:r.bottom>p.bottom+1}}),
  }));
  assert(!bounds.overflow,`${engine} ${width}: overflow`);
  assert(bounds.charts.every(c=>c.ratio<.01&&!c.spill),'gráficos proporcionais');
  await page.screenshot({path:path.join(out,`${engine}-${width}-top.png`)});
  await page.locator('.rs-body').evaluate(e=>e.scrollTop=580);
  await page.screenshot({path:path.join(out,`${engine}-${width}-charts.png`)});
  await page.locator('.rs-body').evaluate(e=>e.scrollTop=0);
  await page.locator('#rsStart').fill('2026-08-01');
  await page.locator('#rsEnd').fill('2026-09-30');
  await page.locator('#rsOrigin').selectOption('residencial');
  await page.getByRole('button',{name:'Aplicar filtros'}).click();
  await page.waitForFunction(()=>document.querySelector('#rsMessage').textContent==='20 laudo(s) encontrados.');
  assert((await page.evaluate(()=>window.calls.at(-1).url)).includes('origem=residencial'));
  assert((await page.locator('#rsPeriod').textContent()).includes('01/08/2026'));
  // Escape devolve foco, sem remontar/perder a edição clínica.
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog').count(),0);
  assert.equal(await page.locator('#unsaved').inputValue(),'Texto clínico sintético em edição');
  assert.equal(await button.evaluate(e=>document.activeElement===e),true);
  await button.click();await page.waitForSelector('#rsEvolution');
  await page.evaluate(()=>window.reply='empty');
  await page.getByRole('button',{name:'Aplicar filtros'}).click();
  await page.getByText('Nenhum laudo neste recorte').waitFor();
  assert.equal(await page.locator('canvas').count(),0);
  await page.evaluate(()=>window.reply='error');
  await page.getByRole('button',{name:'Aplicar filtros'}).click();
  await page.getByText('Os gráficos não carregaram').waitFor();
  await page.evaluate(()=>window.reply='ok');
  await page.getByRole('button',{name:'Tentar novamente',exact:true}).click();
  await page.waitForSelector('#rsEvolution');
  if(width===1440) {
   await page.evaluate(()=>document.documentElement.style.zoom='2');
   await page.waitForTimeout(150);
   assert.equal(await page.locator('.rs-body').evaluate(e=>e.scrollWidth>e.clientWidth+1),false);
   await page.screenshot({path:path.join(out,`${engine}-zoom200.png`)});
   await page.evaluate(()=>document.documentElement.style.zoom='');
  }
  await page.evaluate(()=>window.reply='slow');
  await page.getByRole('button',{name:'Aplicar filtros'}).click();
  await page.waitForFunction(()=>typeof window.resolveSlow==='function');
  // Logout invalida a resposta em voo e elimina todo o conteúdo agregado.
  await page.evaluate(()=>{window.logged=false;window.sessionListeners.forEach(cb=>cb());window.resolveSlow()});
  await page.waitForTimeout(50);
  assert.equal(await page.locator('dialog').count(),0);
  if(width===1440) {
   // A sessão médica recebe a visão própria; operação não ganha o botão.
   await page.evaluate(()=>{window.logged=true;window.reply='ok';window.testRole='medico';window.sessionListeners.forEach(cb=>cb())});
   await button.waitFor();await button.click();
   await page.waitForFunction(()=>document.querySelector('#rsScope')?.textContent==='Seus laudos atribuídos');
   await page.evaluate(()=>{window.testRole='operacional';window.sessionListeners.forEach(cb=>cb())});
   await page.waitForFunction(()=>!document.querySelector('dialog')&&!document.querySelector('[data-report-statistics]'));
   const previous=await page.evaluate(()=>window.calls.filter(x=>x.url.startsWith('/laudos/estatisticas')).length);
   await page.evaluate(()=>window.SoproReportStatistics.open());
   assert.equal(await page.locator('dialog').count(),0);
   assert.equal(await page.evaluate(()=>window.calls.filter(x=>x.url.startsWith('/laudos/estatisticas')).length),previous);
  }
  assert.equal(await page.evaluate(()=>window.calls.every(x=>x.method==='GET')),true);
  assert.deepEqual(errors,[]);
  console.log(`OK ${engine} ${width}px: botão real, 7 gráficos, filtros, vazio/erro, Escape, edição e logout`);
  await page.close();
 }
 await browser.close();
}
})().catch(e=>{console.error(e);process.exit(1)});
