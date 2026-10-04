#!/usr/bin/env node
// Regressão visual: Chart.js real, dados sintéticos e nenhuma conexão externa.
// PLAYWRIGHT_MODULE: instalação existente; VISUAL_SHOTS: evidências fora do Git.
const assert = require('node:assert/strict');
const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, firefox } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const out = process.env.VISUAL_SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'sopro-visual-'));
fs.mkdirSync(out, { recursive: true });
const phase = 'verified';
(async () => {
for (const [engine, launcher] of Object.entries({chromium,firefox})) {
 const browser = await launcher.launch({headless:true});
 for (const width of [1440,1280,1024,768,390,320]) {
  const page = await browser.newPage({viewport:{width,height:1000}});
  const errors = []; page.on('pageerror', e=>errors.push(e.message));
  await page.route('**/*', route=>route.abort());
  let html=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'');
  html=html.replace('./assets/soprolife-logo.png', 'data:image/png;base64,'+fs.readFileSync(path.join(root,'assets/soprolife-logo.png')).toString('base64'));
  await page.setContent(html);
  const original=fs.readFileSync(path.join(root,'index.html'),'utf8');
  for(const m of original.matchAll(/<link[^>]*rel="stylesheet"[^>]*href="\.\/([^"?]+)[^"]*"/g)) await page.addStyleTag({content:fs.readFileSync(path.join(root,m[1]),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'js/vendor/chart.umd.min.js'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'js/app.js'),'utf8')});
  await page.evaluate(()=>{
   state.resumo={cards:[],funilClinicas:{labels:['Abordadas','Responderam','Reunião','Proposta','Piloto'],values:[27,6,3,2,1]}};
   state.leads=Array.from({length:28},(_,i)=>({etapa:['novo','em_contato','aguardando_retomada','agendado','convertido','nao_respondeu','perdido'][i%7],origem:['Google','Indicação de parceiros','Contato direto','Instagram'][i%4],servico:'Espirometria',data_contato:`${String(1+i%28).padStart(2,'0')}/${String(1+i%(new Date().getMonth()+1)).padStart(2,'0')}/${new Date().getFullYear()}`}));
   state.crm=Array.from({length:12},(_,i)=>({etapa:['Abordada','Em negociação','Reunião agendada','Parceiro ativo'][i%4]}));
   state.marketing={canais:{labels:['Google','Instagram','Indicação','WhatsApp direto','Clínicas'],values:[42,24,16,10,8]}};
   state.marketingSeo={searchConsole:{byDate:Array.from({length:14},(_,i)=>({date:`2026-09-${String(i+1).padStart(2,'0')}`,impressions:20+i*7}))}};
   state.followupSummary={espirometria:{total:12,atrasados:2,hoje:1,proximos7dias:4,futuro:5},consultas:{total:5,hoje:2,futuro:3}};
   setPremiumChartDefaults(); Chart.defaults.animation=false;
   renderCharts(); renderLeadsCharts(); renderMktTrendChart();
   document.querySelector('#crmView').innerHTML='<div id="crmReportChartsGrid"></div>';
   renderCrmReportCharts(state.leads);
   state.parceriaPastore={producao_por_data:{labels:['01/09','02/09','03/09'],exames:[2,4,3]},financeiro_por_periodo:{labels:['Setembro'],receita:[100],custos:[40],resultado:[60]}};
   renderParceriaPastore();
   state.custosInvestimentos={source:{safeToDisplay:true},itens:[],por_responsavel:[],por_categoria:[],alertas:[],rateio_socios:[{nome:'Adeildo (sintético)',total_desembolsado:100,saldo_pendente_atribuido:20},{nome:'Faustino (sintético)',total_desembolsado:80,saldo_pendente_atribuido:40}]};
   renderCustosInvestimentos();
   document.querySelector('#ci-resumo').hidden=true;
   document.querySelector('#ci-socios').hidden=false;
   document.querySelector('#heroSourceLabel').textContent='Validação visual';
  });
  const originalData=await page.evaluate(()=>JSON.stringify(Object.values(state.charts).map(c=>c.data)));
  const mensal=await page.evaluate(()=>({labels:state.charts.monthly.data.labels,n:(new Date().getFullYear()-2026)*12+new Date().getMonth()-4+1}));
  assert.equal(mensal.labels[0],'Mai','evolução mensal começa em Mai/2026');
  assert.equal(mensal.labels.length,mensal.n,'evolução mensal vai até o mês corrente, sem meses futuros');
  assert(!['Jan','Fev','Mar','Abr'].some(m=>mensal.labels.includes(m)),'Jan–Abr/2026 fora da evolução mensal');
  const results={engine,width,sections:[],errors};
  for(const section of ['overview','leads','marketing','crm','parcerias-pastore','custos-investimentos']) {
   await page.evaluate(section=>{document.querySelectorAll('.section').forEach(e=>e.classList.toggle('active',e.id===section));resizeCharts()},section);
   await page.waitForTimeout(350);
   results.sections.push(await page.evaluate(section=>({section,overflow:document.documentElement.scrollWidth>innerWidth+1,charts:[...document.querySelectorAll(`#${section} canvas`)].filter(e=>e.getClientRects().length).map(c=>{const r=c.getBoundingClientRect(),p=c.parentElement.getBoundingClientRect(),ch=Chart.getChart(c);return{id:c.id,w:r.width,h:r.height,parentH:p.height,spill:r.bottom>p.bottom+1,ratioError:ch?Math.abs(r.width/r.height-ch.width/ch.height):null}})}),section));
   await page.screenshot({path:path.join(out,`${phase}-${engine}-${width}-${section}.png`),fullPage:true});
  }
  for(const s of results.sections) {
   assert.equal(s.overflow,false,`${engine} ${width} ${s.section}: overflow`);
   assert(s.charts.length>0,`${s.section}: gráficos presentes`);
   for(const c of s.charts) {
    assert(c.w>0 && c.h>0,`${c.id}: tamanho positivo`);
    assert.equal(c.spill,false,`${c.id}: desenho fora da caixa`);
    assert(c.ratioError<0.01,`${c.id}: proporção do bitmap deformada`);
   }
  }
  assert.equal(await page.evaluate(()=>JSON.stringify(Object.values(state.charts).map(c=>c.data))),originalData,'trocar seção não altera dados');
  await page.evaluate(()=>{
   document.querySelectorAll('.section').forEach(e=>e.classList.toggle('active',e.id==='overview'));
   document.querySelector('.nav-hub-card').focus();
   resizeCharts();
  });
  assert.equal(await page.locator('.nav-hub-card').first().evaluate(e=>getComputedStyle(e).outlineStyle),'solid','foco visível');
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement.classList.contains('nav-hub-card')),true,'ordem dos atalhos pelo teclado');
  if(width===1440) {
   await page.evaluate(()=>{document.documentElement.style.zoom='2';resizeCharts()});
   await page.waitForTimeout(350);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'zoom 200% sem overflow');
   await page.screenshot({path:path.join(out,`${phase}-${engine}-zoom200.png`),fullPage:true});
   await page.evaluate(()=>{document.documentElement.style.zoom='';resizeCharts()});
  }
  // Estado vazio e alternância canvas/lista preservam hidden e mensagens.
  await page.evaluate(()=>{
   document.querySelectorAll('.section').forEach(e=>e.classList.toggle('active',e.id==='parcerias-pastore'));
   state.parceriaPastore={};renderParceriaPastore();
  });
  assert.equal(await page.locator('#parceriaProducaoChart').isVisible(),false);
  assert.equal(await page.locator('#parceriaProducaoEmpty').isVisible(),true);
  await page.evaluate(()=>{
   document.querySelectorAll('.section').forEach(e=>e.classList.toggle('active',e.id==='marketing'));
   document.querySelector('#channelsChart').hidden=true;
   document.querySelector('#trafficSourceList').hidden=false;
   document.querySelector('#trafficSourceList').textContent='Canal sintético — sem dados';
  });
  assert.equal(await page.locator('#channelsChart').locator('..').isVisible(),false,'canvas oculto não deixa caixa vazia');
  assert.equal(await page.locator('#trafficSourceList').isVisible(),true);
  assert.deepEqual(errors,[],'nenhum erro JavaScript');
  fs.writeFileSync(path.join(out,`${phase}-${engine}-${width}.json`),JSON.stringify(results,null,2));
  console.log(`OK ${engine} ${width}px: ${results.sections.reduce((n,s)=>n+s.charts.length,0)} gráficos, navegação, vazio e proporções`);
  await page.close();
 }
 await browser.close();
}
})().catch(e=>{console.error(e);process.exit(1)});
