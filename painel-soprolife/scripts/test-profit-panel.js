#!/usr/bin/env node
/* Contrato visual sintético do código real: null não vira barra de zero. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../js/profit-panel.js'),'utf8');
let configs=[];const root={innerHTML:'',addEventListener(){}};
const sandbox={document:{getElementById:id=>id==='profitPanelRoot'?root:{},querySelectorAll:()=>[]},
  window:{SoproM15:{can:()=>true,getUser:()=>null}},setInterval:()=>1,clearInterval(){},URLSearchParams,
  Chart:function(canvas,config){configs.push(config);this.destroy=()=>{};}};
vm.createContext(sandbox);
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.testProfit={render,examDetail};})();'),sandbox);
const row={id:'sintetico',exame:'ESP-SYN1',local:'home',data:'2026-09-15',status:'incompleto',
  receita:'220.00',recebido:'220.00',pendente:'0.00',custos_comprovados:'15.00',custo_total:null,
  lucro:null,margem:null,custos:{medico:'15.00',material:null},pendencias:['regras_de_custo_sem_vigencia'],
  fonte_receita:'financial_entry',status_financeiro:'Recebido'};
const k={receita:'220.00',recebido:'220.00',a_receber:'0.00',custos:'15.00',lucro:null,margem:null,
  lucro_medio:null,custo_total_calculado:null,exames:0,incompletos:1,total_exames:1,
  cobertura:{receita:1,recebido:1,pendente:1},informacoes_faltantes:1};
const data={kpis:k,por_mes:[{chave:'2026-09',...k}],por_dia:[{chave:'2026-09-15',...k}],
  por_local:[{chave:'home',...k}],composicao:{medico:'15.00'},composicao_parcial:true,
  exames:[row],pendencias:{regras_de_custo_sem_vigencia:1},filtros:{locais:['home'],parceiros:[],formas:[],status:['Recebido']}};
sandbox.testProfit.render(data);
assert.match(root.innerHTML,/Receita comprovada/);assert.match(root.innerHTML,/220,00/);
assert.match(root.innerHTML,/Custos já comprovados/);assert.match(root.innerHTML,/Aguardando fechamento/);
assert.match(root.innerHTML,/Composição parcial/);assert.doesNotMatch(root.innerHTML,/financial_entry/);
const monthly=configs.find(c=>c.data.datasets.some(d=>d.label==='Lucro calculado')&&c.type==='bar');
assert.equal(monthly.data.datasets[0].data[0],220);
assert.equal(monthly.data.datasets[2].data[0],null);
const tooltip=monthly.options.plugins.tooltip.callbacks.afterBody([{dataset:monthly.data.datasets[2],dataIndex:0}]);
assert.ok(tooltip.some(t=>t.includes('Lucro pendente — faltam 1 informações')));
assert.match(root.innerHTML,/Domiciliar: 1 exame/);
const unknown=sandbox.testProfit.examDetail({...row,receita:null,recebido:null,pendente:null,custos_comprovados:null,custos:{}});
assert.doesNotMatch(unknown,/0,00/);assert.match(unknown,/—/);
console.log('PASS lucro histórico: receita, detalhes, composição parcial, tooltips e gráficos preservam null.');
