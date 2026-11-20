/* Canonical partnership overview. All monetary/settlement facts come from M15. */
(function(){
  'use strict';
  const root=document.getElementById('partnershipCenterRoot');if(!root)return;
  const gate=window.SoproBootGate,api=(url,options)=>window.SoproM15.api(url,options);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=v=>v==null?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  const date=(v,p='dia')=>!v?'—':p==='mes'?new Date(v+'T12:00:00').toLocaleDateString('pt-BR',{month:'2-digit',year:'numeric'})+' (mês)':new Date(v+'T12:00:00').toLocaleDateString('pt-BR');
  const icon=name=>window.SoproIcons.svg(name);
  const labels={exames:'Exames realizados',bruto:'Receita bruta',soprolife:'Parcela SoproLife',parceiro:'Parcela parceiro',a_receber:'A receber',a_pagar:'A pagar',acertos_pendentes:'Acertos pendentes'};
  const receivers={soprolife:'SoproLife',parceiro:'Parceiro',dividido:'Já dividido'};
  const status={pendente:'Pendente',parcial:'Parcial',acertado:'Acertado',sem_repasse:'Sem repasse pendente',aguardando_pagamento:'Aguardando pagamento'};
  const s={inicio:'',fim:'',filter:'todas',code:null,page:1,epoch:0,abort:null};
  function metric(key,value){return '<div class="pc-metric"><dt>'+esc(labels[key])+'</dt><dd>'+(key==='exames'||key==='acertos_pendentes'?esc(value??'—'):esc(money(value)))+'</dd></div>';}
  function metrics(data,finance=true){return '<dl class="pc-metrics">'+Object.entries(labels).filter(([key])=>key==='exames'||finance).map(([k])=>metric(k,data[k])).join('')+'</dl>';}
  function loading(){root.dataset.state='loading';root.setAttribute('aria-busy','true');root.innerHTML='<p role="status">Carregando…</p><div class="pc-skeleton" aria-hidden="true"></div>';}
  function frame(data,content){
    root.dataset.state='ready';root.setAttribute('aria-busy','false');root.className='pc-center';
    root.innerHTML='<div class="section-title"><div><p class="eyebrow">Operação · parceiros</p><h2>Central de Parcerias</h2><p class="section-sub">Produção, parcelas e acertos das parcerias existentes.</p></div></div>'+
      '<form class="pc-filters"><label>Parceria<select name="parceiro">'+[['todas','Todas'],['pastore','Pastore'],['maclin','Maclin'],['alergocare','Alergocare']].map(([v,t])=>'<option value="'+v+'"'+(v===s.filter?' selected':'')+'>'+t+'</option>').join('')+'</select></label><label>Início<input type="date" data-m15-date="full" aria-label="Início do período" name="inicio" value="'+esc(data.inicio)+'" required></label><label>Fim<input type="date" data-m15-date="full" aria-label="Fim do período" name="fim" value="'+esc(data.fim)+'" required></label><button type="submit">Aplicar período</button><button type="button" data-pc-refresh>Atualizar</button></form>'+content+
      '<p class="pc-note">'+esc(data.nota)+' Último atendimento refere-se ao período. Datas imprecisas só entram quando o mês completo está no filtro.</p>'+
      (!data.com_financeiro?'<p class="pc-note">Valores financeiros disponíveis somente para gestão autorizada.</p>':'');
    window.SoproM15DatePicker.attachAll(root.querySelector('form'));
    root.querySelector('form').onsubmit=e=>{e.preventDefault();const form=new FormData(e.target);s.inicio=form.get('inicio');s.fim=form.get('fim');s.filter=form.get('parceiro');s.code=null;s.page=1;load();};
    root.querySelector('[data-pc-refresh]').onclick=()=>load(s.code,s.page);
    root.querySelectorAll('[data-pc-open]').forEach(b=>b.onclick=()=>window.SoproCommandCenter.openTarget({tipo:'parceria',id:b.dataset.pcOpen}));
    root.querySelectorAll('[data-pc-back]').forEach(b=>b.onclick=()=>show());
  }
  function summary(data){
    frame(data,'<section class="panel"><h3>Visão consolidada</h3>'+metrics(data.consolidado,data.com_financeiro)+'</section><div class="pc-cards">'+data.parcerias.map(p=>
      '<article class="panel pc-card" data-partnership-card="'+esc(p.codigo)+'"><div class="pc-partner-mark" aria-hidden="true">'+icon('partners')+'</div><p class="eyebrow">Parceria</p><h3>'+esc(p.nome)+'</h3><p>'+esc(p.unidades.map(u=>u.nome).join(' · ')||'Configuração indisponível')+'</p>'+metrics({exames:p.exames,...p.financeiro},data.com_financeiro)+
      (p.datas_imprecisas?'<p class="pc-note">'+p.datas_imprecisas+' registro(s) com data imprecisa fora dos totais.</p>':'')+'<p class="pc-note">Último atendimento: '+esc(date(p.ultimo_atendimento?.data,p.ultimo_atendimento?.precisao))+'</p>'+(p.financeiro?'<p class="pc-note">Acertos com base registrada: '+p.financeiro.cobertura_acertos+'/'+(p.exames??'—')+' exames.</p>':'')+
      '<button type="button" data-pc-open="'+esc(p.partner_id)+'"'+(!p.partner_id?' disabled':'')+'>Abrir parceria</button></article>').join('')+'</div>');
  }
  function detail(data){
    const p=data.parcerias[0],financial=data.com_financeiro;
    const columns=[['codigo','Exame'],['data','Data'],['status','Situação'],...(financial?[['bruto','Bruto'],['soprolife','SoproLife'],['parceiro',p.nome],['recebido_por','Recebedor'],['status_acerto','Acerto']]:[])];
    frame(data,'<button type="button" data-pc-back>← Todas as parcerias</button><section class="panel" data-partnership-detail="'+esc(p.codigo)+'"><p class="eyebrow">'+esc(p.unidades.map(u=>u.nome).join(' · '))+'</p><h3>'+esc(p.nome)+'</h3>'+metrics({exames:p.exames,...p.financeiro},financial)+
      (p.regra?'<p class="pc-note">Regra vigente: '+esc(p.regra.soprolife)+'% SoproLife / '+esc(p.regra.parceiro)+'% '+esc(p.nome)+'. '+(p.regra.fiscal==='DIRECT'?'NFS-e DIRECT pela SoproLife sobre o bruto, com confirmação humana.':'Enquadramento fiscal conforme configuração vigente.')+' Cada exame conserva sua regra histórica.</p>':'')+
      '<h4>Produção e histórico do período</h4>'+(data.itens.length?'<table class="pc-table"><thead><tr>'+columns.map(([,v])=>'<th scope="col">'+esc(v)+'</th>').join('')+'<th scope="col">Abrir</th></tr></thead><tbody>'+data.itens.map(r=>'<tr>'+columns.map(([k,label])=>'<td data-label="'+esc(label)+'">'+esc(k==='data'?date(r.data,r.precisao):['bruto','soprolife','parceiro'].includes(k)?money(r[k]):k==='recebido_por'?receivers[r[k]]||'—':k==='status_acerto'?status[r[k]]||'Sem partilha registrada':r[k])+'</td>').join('')+'<td data-label="Abrir"><button type="button" data-pc-exam="'+esc(r.id)+'">Ver exame</button>'+(r.share_id?'<button type="button" data-pc-share="'+esc(r.share_id)+'">Acertos e histórico</button>':'')+'</td></tr>').join('')+'</tbody></table>':'<p class="pc-empty">Nenhum exame confirmado neste período.</p>')+
      '<div class="pc-pages">'+(data.pagina>1?'<button type="button" data-pc-prev>Anterior</button>':'')+'<span>Página '+data.pagina+'</span>'+(data.tem_proxima?'<button type="button" data-pc-next>Próxima</button>':'')+'</div></section>');
    root.querySelectorAll('[data-pc-exam]').forEach(b=>b.onclick=()=>window.SoproCommandCenter.openTarget({tipo:'exame',id:b.dataset.pcExam}));
    root.querySelectorAll('[data-pc-share]').forEach(b=>b.onclick=()=>window.SoproCommandCenter.openTarget({tipo:'acerto',id:b.dataset.pcShare}));
    root.querySelector('[data-pc-prev]')?.addEventListener('click',()=>load(s.code,s.page-1));
    root.querySelector('[data-pc-next]')?.addEventListener('click',()=>load(s.code,s.page+1));
  }
  async function load(code=null,page=1){
    s.code=code;s.page=page;s.abort?.abort();s.abort=new AbortController();const epoch=++s.epoch;
    loading();const query=new URLSearchParams();if(s.inicio){query.set('inicio',s.inicio);query.set('fim',s.fim);}if(!code)query.set('parceiro',s.filter);else query.set('pagina',page);
    try{const data=await api('/parcerias/central'+(code?'/'+code:'')+'?'+query,{signal:s.abort.signal});if(epoch!==s.epoch||gate.estado!=='ready')return;
      s.inicio=data.inicio;s.fim=data.fim;code?detail(data):summary(data);
    }catch(error){if(epoch!==s.epoch||error.name==='AbortError')return;root.dataset.state=error.status===403?'unauthorized':'error';root.setAttribute('aria-busy','false');root.innerHTML='<div class="panel" role="status"><p>'+esc(error.status===403?'Esta área não está disponível para seu perfil.':'Não foi possível carregar as parcerias. Confira o período ou tente novamente.')+'</p><button type="button" data-pc-retry>Tentar novamente</button><button type="button" data-pc-reset>Voltar ao mês atual</button></div>';root.querySelector('[data-pc-retry]').onclick=()=>load(code,page);root.querySelector('[data-pc-reset]').onclick=()=>{s.inicio=s.fim='';s.filter='todas';show();};}
  }
  function show(){s.code=null;s.page=1;s.filter='todas';if(location.hash.includes('/parceria/'))history.replaceState(null,'',location.pathname+location.search);return load();}
  window.SoproPartnerships={show,open:async function(code){if(code==='pastore'){await gate.navegar('parcerias-pastore');return;}if(!['maclin','alergocare'].includes(code))throw Error('Parceria indisponível');s.filter=code;return load(code);}};
  document.addEventListener('click',e=>{if(e.target.closest('[data-partnership-back]'))gate.navegar('parcerias');});
})();
