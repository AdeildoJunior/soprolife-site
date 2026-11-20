/* Existing Command Center home, scoped pending work and bounded server search. */
(function () {
  'use strict';
  const api=()=>window.SoproM15, gate=window.SoproBootGate, life=window.LifeCommandCenter;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=name=>window.SoproIcons.svg(name);
  const categoryIcons={hoje:'calendar',sem_laudo:'file',laudo:'activity',fiscal:'file',contato:'inbox',followup:'bell',agenda:'calendar',acerto:'partners',inconsistencia:'alert'};
  const labels={hoje:'Exames de hoje',sem_laudo:'Realizados sem laudo',laudo:'Laudos pendentes',fiscal:'NFS-e pendentes',contato:'Contatos aguardando ação',followup:'Follow-ups vencidos / hoje',agenda:'Próximos 7 dias',acerto:'Acertos de parceiros',inconsistencia:'Informações a completar'};
  const snapshots=new Map(),inflight=new Map();
  let resultItems=[],searchEpoch=0,searchAbort=null,searchTimer=null;
  let pendingPage=1,pendingCategory='',pendingEpoch=0,navigationEpoch=0;
  const brand=()=>life.getContext();
  const date=v=>v?new Date(v.length===10?v+'T12:00:00':v).toLocaleDateString('pt-BR'):'Sem data';
  const money=v=>v==null?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  const count=v=>v==null?'—':String(v);
  const homeRoot=b=>b==='soprolife'?document.getElementById('ccHome'):document.querySelector('[data-eeg-section="visao"]');

  function state(root,kind,message){
    if(!root)return;
    root.dataset.state=kind;root.setAttribute('aria-busy',kind==='loading'?'true':'false');
    root.innerHTML=kind==='loading'?'<div class="cc-loading" role="status"><span>Carregando…</span><div class="cc-skeleton-grid">'+Array(6).fill('<i class="cc-skeleton"></i>').join('')+'</div></div>':
      '<div class="cc-state" role="status"><p>'+esc(message)+'</p>'+(kind==='error'?'<button type="button" data-cc-refresh>Tentar novamente</button>':'')+'</div>';
  }
  function card(label,value,category,extra=''){
    return '<button type="button" class="cc-stat" data-cc-category="'+esc(category)+'"><span class="cc-stat-heading"><i class="cc-stat-icon">'+icon(categoryIcons[category]||'chart')+'</i><span>'+esc(label)+'</span></span><strong>'+esc(count(value))+'</strong>'+extra+'</button>';
  }
  function record(item,index,attr='data-cc-item'){
    return '<button type="button" class="cc-work-row" '+attr+'="'+index+'"><span><strong>'+esc(item.codigo)+'</strong><span>'+esc(item.titulo??item.rotulo)+'</span></span><span>'+esc(item.data?date(item.data):labels[item.categoria]||'Abrir registro')+' <b aria-hidden="true">'+icon('arrow')+'</b></span></button>';
  }
  function render(root,data){
    const cap=data.capacidades,p=data.pendencias,nums=p.categorias,b=data.marca;
    root.dataset.state='ready';root.setAttribute('aria-busy','false');root.classList.add('cc-home');
    const finance=data.financeiro;
    root.innerHTML='<div class="cc-heading"><div><p class="eyebrow">'+(b==='eeglife'?'EEGLife':'SoproLife')+' · operação</p><h2>O que precisa da sua atenção?</h2><p>Hoje, '+esc(date(data.periodo.hoje))+'</p></div></div>'+
      '<div class="cc-actions">'+(cap.operacao?'<button type="button" class="cc-primary" data-cc-new="atendimento">'+icon('plus')+' Novo atendimento</button>':'')+
      (cap.operacao&&b==='soprolife'?'<button type="button" data-cc-new="contato">'+icon('plus')+' Novo contato</button>':'')+
      '<button type="button" data-cc-category="agenda">Ver agenda</button>'+
      (cap.operacao?'<button type="button" data-cc-category="laudo">Ver laudos pendentes</button>':'')+
      (cap.fiscal?'<button type="button" data-cc-category="fiscal">Ver NFS-e pendentes</button>':'')+
      (cap.financeiro?'<button type="button" data-cc-section="financeiro">Ver financeiro</button>':'')+'</div>'+
      '<section class="cc-block" aria-labelledby="ccToday-'+b+'"><h3 id="ccToday-'+b+'">Hoje e próximos passos</h3><div class="cc-stats">'+card('Exames de hoje',data.hoje.exames,'hoje')+
      Object.entries(labels).filter(([k])=>k!=='hoje'&&k!=='inconsistencia'&&(k!=='fiscal'||cap.fiscal)&&(k!=='acerto'||cap.financeiro)&&(k!=='sem_laudo'||b==='soprolife'&&cap.operacao)&&(k!=='laudo'||cap.operacao||cap.clinico)&&(b==='soprolife'||!['contato','followup','acerto'].includes(k))).map(([k,label])=>card(label,p.indisponiveis.includes(k)?null:nums[k]||0,k,k==='fiscal'?'<small>Documentos já preparados</small>':'')).join('')+'</div></section>'+
      '<section class="cc-block"><div class="cc-block-heading"><h3>Para fazer agora</h3><button type="button" data-cc-pending>Ver pendências ('+esc(p.total)+(p.completo?'':'+')+')</button></div>'+
      (p.itens.length?'<div class="cc-work-list">'+p.itens.map((i,n)=>record(i,n)).join('')+'</div>':'<p class="cc-empty">Nenhuma pendência encontrada nas fontes disponíveis.</p>')+
      (p.indisponiveis.length?'<p class="cc-note" role="status">Dados indisponíveis: '+p.indisponiveis.map(k=>esc(labels[k]||k)).join(', ')+'. O total não inclui essas fontes.</p>':'')+'</section>'+
      '<section class="cc-block"><h3>Resumo do mês</h3><p class="cc-note">Desde '+esc(date(data.periodo.inicio))+' · competência do atendimento</p><div class="cc-month">'+
      '<article class="cc-metric"><span>Exames realizados</span><strong>'+esc(count(data.mes.exames))+'</strong></article>'+
      (b==='soprolife'?'<article class="cc-metric cc-cohort"><span>Contatos → Agendados → Realizados</span><div class="cc-cohort-bars">'+[['Contatos',data.mes.contatos],['Agendados',data.mes.agendados],['Realizados',data.mes.realizados]].map(([label,n])=>'<div><span>'+esc(label)+'</span><strong>'+esc(n)+'</strong><i aria-hidden="true" style="--cc-bar:'+Number(n)/Math.max(1,data.mes.contatos,data.mes.agendados,data.mes.realizados)*100+'%"></i></div>').join('')+'</div><small>Contatos iniciados no mês; etapas acumuladas.</small></article>':'')+
      (finance?(b==='soprolife'?[['Receita bruta dos exames','bruto'],['Parcela econômica SoproLife','soprolife'],['Parcela dos parceiros','parceiros'],['Despesas registradas no razão','despesas'],['Resultado gerencial dos exames','resultado']]:[['Receita registrada EEG','bruto'],['Despesas registradas EEG','despesas'],['Resultado gerencial EEG','resultado']]).map(([label,key])=>'<article class="cc-metric" data-cc-financial><span>'+esc(label)+'</span><strong>'+esc(money(finance[key]))+'</strong>'+(finance[key]==null?'<small>Base a confirmar</small>':finance.parcial?.[key]?'<small>Parcial confirmado · '+esc(finance.cobertura_valores?.[key]?.com_dado??finance.cobertura.completos)+'/'+esc(finance.cobertura.total)+' exames</small>':'')+'</article>').join(''):'')+'</div>'+
      (finance?'<p class="cc-note">'+esc(finance.nota)+(finance.cobertura?' Cobertura de custos: '+esc(finance.cobertura.completos)+' de '+esc(finance.cobertura.total)+' exames.':'')+'</p>':'')+'</section>'+
      '<section class="cc-block"><h3>Alertas operacionais</h3>'+
      (nums.inconsistencia?'<button type="button" data-cc-category="inconsistencia">'+esc(nums.inconsistencia)+' pendência(s) de informação operacional</button>':'<p class="cc-empty">Nenhuma inconsistência verificável nas fontes consultadas.</p>')+
      (finance?.cobertura&&finance.cobertura.completos<finance.cobertura.total?'<p class="cc-note">Há exames com base de resultado incompleta. As parcelas conhecidas têm cobertura indicada; nenhum valor ausente foi estimado.</p>':'')+'</section>'+
      '<p class="cc-asof">Consultado em '+esc(new Date(data.observado_em).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}))+' · '+esc(data.escopo_fiscal)+'</p>';
    root.querySelectorAll('[data-cc-item]').forEach(el=>el.onclick=()=>openTarget(p.itens[Number(el.dataset.ccItem)].destino));
    wire(root);
    updatePendingLabel(data);
  }
  function updatePendingLabel(data){
    const button=document.getElementById('ccPendingToggle');
    if(button&&data.marca===brand()){button.disabled=false;button.textContent='Pendências ('+data.pendencias.total+(data.pendencias.completo?'':'+')+')';}
  }
  function loadHome(b,force=false){
    const root=homeRoot(b);if(!root&&b==='soprolife')return Promise.resolve();
    const show=data=>root?render(root,data):updatePendingLabel(data);
    if(inflight.has(b))return inflight.get(b);
    if(!force&&snapshots.has(b)&&Date.now()-Date.parse(snapshots.get(b).observado_em)<30000){show(snapshots.get(b));return Promise.resolve();}
    state(root,'loading');
    const url=root?'/comando/resumo?marca='+b:'/comando/pendencias?marca='+b+'&limite=1';
    const promise=api().api(url,{signal:AbortSignal.timeout(20000)}).then(response=>{
      const data=root?response:{marca:b,pendencias:response,observado_em:new Date().toISOString()};
      if(gate.estado!=='ready')return;
      snapshots.set(b,data);show(data);
    }).catch(error=>{if(gate.estado==='ready'){state(root,error.status===403?'unauthorized':'error',error.status===403?'Este recurso não está disponível para seu perfil.':'Não foi possível carregar os dados.');if(root)wire(root);}}).finally(()=>inflight.delete(b));
    inflight.set(b,promise);return promise;
  }
  function wire(root){
    root.querySelectorAll('[data-cc-category]').forEach(el=>el.onclick=()=>pending(el.dataset.ccCategory));
    root.querySelectorAll('[data-cc-pending]').forEach(el=>el.onclick=()=>pending(''));
    root.querySelectorAll('[data-cc-refresh]').forEach(el=>el.onclick=()=>loadHome(brand(),true));
    root.querySelectorAll('[data-cc-new]').forEach(el=>el.onclick=async()=>{
      try{
        if(brand()==='eeglife'){await gate.carregar('eeg');window.SoproEegOperacao.newAttendance();return;}
        await gate.navegar('central-cadastros',{dispatch:false});window.SoproCentral.open(el.dataset.ccNew);
      }catch{announce('Não foi possível abrir o cadastro.');}
    });
    root.querySelectorAll('[data-cc-section]').forEach(el=>el.onclick=()=>brand()==='eeglife'?life.openEegModule(el.dataset.ccSection):gate.navegar(el.dataset.ccSection));
  }
  function announce(text){const box=document.getElementById('ccAnnouncement');if(box)box.textContent=text;}
  async function pending(category='',page=1){
    if(gate.somenteClinico&&brand()==='soprolife'){await gate.navegar('laudos-espirometria');return;}
    pendingCategory=category;pendingPage=page;
    const host=document.getElementById('ccPendingPanel');host.hidden=false;
    state(host,'loading');host.scrollIntoView({block:'start'});
    const epoch=++pendingEpoch,b=brand();
    try{
      const data=await api().api('/comando/pendencias?marca='+b+'&pagina='+page+(category?'&categoria='+encodeURIComponent(category):''));
      if(epoch!==pendingEpoch||gate.estado!=='ready'||b!==brand())return;
      host.dataset.state=data.itens.length?'ready':'empty';host.setAttribute('aria-busy','false');
      host.innerHTML='<div class="cc-block-heading"><h2>Pendências ('+esc(data.total)+')</h2><button type="button" data-cc-close-pending>Fechar</button></div>'+
        '<label class="cc-filter">Categoria<select id="ccPendingCategory"><option value="">Todas as categorias</option>'+Object.entries(labels).filter(([k])=>data.categorias[k]||k===category).map(([k,label])=>'<option value="'+k+'"'+(k===category?' selected':'')+'>'+esc(label)+' ('+(data.categorias[k]??data.total)+')</option>').join('')+'</select></label>'+
        (data.itens.length?'<div class="cc-work-list">'+data.itens.map((i,n)=>record(i,n,'data-cc-pending-item')).join('')+'</div>':'<p class="cc-empty">Nenhuma pendência nesta categoria.</p>')+
        '<div class="cc-pagination">'+(page>1?'<button type="button" data-cc-prev>Anterior</button>':'')+'<span>Página '+page+'</span>'+(data.tem_proxima?'<button type="button" data-cc-next>Próxima</button>':'')+'</div>';
      host.querySelector('select').onchange=e=>pending(e.target.value);
      host.querySelector('[data-cc-close-pending]').onclick=()=>{host.hidden=true;};
      host.querySelector('[data-cc-prev]')?.addEventListener('click',()=>pending(category,page-1));
      host.querySelector('[data-cc-next]')?.addEventListener('click',()=>pending(category,page+1));
      host.querySelectorAll('[data-cc-pending-item]').forEach(el=>el.onclick=()=>openTarget(data.itens[Number(el.dataset.ccPendingItem)].destino));
    }catch(error){if(epoch===pendingEpoch){
      state(host,error.status===403?'unauthorized':'error','Não foi possível carregar as pendências.');
      host.querySelector('[data-cc-refresh]')?.addEventListener('click',()=>pending(category,page));
      const close=document.createElement('button');close.type='button';close.textContent='Fechar';close.onclick=()=>{host.hidden=true;};host.append(close);
    }}
  }
  function clearSearch(){
    searchEpoch++;searchAbort?.abort();clearTimeout(searchTimer);resultItems=[];
    const input=document.getElementById('ccGlobalSearch'),box=document.getElementById('ccSearchResults');
    if(input){input.value='';input.setAttribute('aria-expanded','false');}
    if(box){box.innerHTML='';box.hidden=true;}
  }
  async function search(){
    const input=document.getElementById('ccGlobalSearch'),box=document.getElementById('ccSearchResults'),q=input.value.trim();
    const epoch=++searchEpoch,b=brand();searchAbort?.abort();
    if(q.length<3){box.hidden=true;return;}
    searchAbort=new AbortController();box.hidden=false;box.dataset.state='loading';box.textContent='Buscando…';input.setAttribute('aria-expanded','true');
    try{
      const data=await api().api('/comando/busca',{method:'POST',body:JSON.stringify({marca:b,termo:q,limite:12}),signal:searchAbort.signal});
      if(epoch!==searchEpoch||b!==brand()||gate.estado!=='ready')return;
      resultItems=[];box.dataset.state=data.total?'ready':'empty';
      box.innerHTML=data.total?data.grupos.map(g=>'<section><h3>'+esc(g.rotulo)+'</h3>'+g.itens.map(item=>{
        const n=resultItems.push(item)-1;
        return '<button type="button" data-cc-search-result="'+n+'"><strong>'+esc(item.codigo)+'</strong><span>'+esc(item.rotulo)+'</span>'+(item.contato_mascarado?'<small>'+esc(item.contato_mascarado)+'</small>':'')+'</button>'+(item.ver_parceria?'<a href="#cc/'+b+'/parceria/'+esc(item.ver_parceria.id)+'" data-cc-partnership="'+n+'">Ver parceria</a>':'');
      }).join('')+'</section>').join(''):'<p>Nenhum registro encontrado.</p>';
      box.querySelectorAll('[data-cc-partnership]').forEach(link=>link.onclick=e=>{e.preventDefault();const target=resultItems[Number(link.dataset.ccPartnership)].ver_parceria;clearSearch();openTarget(target);});
      box.querySelectorAll('button').forEach(button=>button.onclick=()=>{const target=resultItems[Number(button.dataset.ccSearchResult)].destino;clearSearch();openTarget(target);});
    }catch(error){if(epoch===searchEpoch&&error.name!=='AbortError'){box.dataset.state=error.status===403?'unauthorized':'error';box.textContent=error.status===429?'Aguarde um momento antes de pesquisar novamente.':error.status===422?'Informe um nome com 3 letras ou um identificador completo.':'Não foi possível pesquisar.';}}
  }
  function setupHeader(){
    const input=document.getElementById('ccGlobalSearch');if(!input)return;
    if(gate.somenteClinico&&brand()==='soprolife')input.placeholder='Buscar nos meus laudos';
    input.oninput=()=>{clearTimeout(searchTimer);searchAbort?.abort();searchEpoch++;searchTimer=setTimeout(search,350);};
    input.onkeydown=e=>{if(e.key==='Escape')clearSearch();if(e.key==='ArrowDown'){e.preventDefault();document.querySelector('[data-cc-search-result]')?.focus();}if(e.key==='Enter'){e.preventDefault();clearTimeout(searchTimer);search();}};
    document.getElementById('ccSearchResults').onkeydown=e=>{
      const buttons=[...document.querySelectorAll('[data-cc-search-result]')],i=buttons.indexOf(document.activeElement);
      if(e.key==='Escape'){clearSearch();input.focus();}
      if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();buttons[(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}
    };
    const toggle=document.getElementById('ccPendingToggle');
    if(gate.somenteClinico&&brand()==='soprolife'){toggle.disabled=false;toggle.textContent='Meus laudos';}
    toggle.onclick=()=>pending('');
    document.addEventListener('click',e=>{if(!e.target.closest('.cc-search')){document.getElementById('ccSearchResults').hidden=true;input.setAttribute('aria-expanded','false');}});
  }
  async function selectedRecord(target,host,details){
    document.getElementById('ccSelectedRecord')?.remove();
    const article=document.createElement('article');article.id='ccSelectedRecord';article.className='cc-block cc-selected';article.dataset.recordId=target.id;article.tabIndex=-1;
    article.innerHTML='<p class="eyebrow">Registro selecionado</p><h2>'+esc(target.codigo||details.public_code||'Documento')+'</h2><p>'+esc(details.nome||details.nome_completo||details.state||details.status||'')+'</p>';
    if(target.tipo==='fiscal'){
      const prep=details.preparation;
      article.innerHTML+='<dl><dt>Documento</dt><dd>'+esc(details.id)+'</dd><dt>Situação</dt><dd>'+esc(details.state)+'</dd>'+(prep?'<dt>Competência</dt><dd>'+esc(date(prep.competence))+'</dd><dt>Valor fiscal confirmado</dt><dd>'+esc(money(prep.amount_snapshot))+'</dd>':'')+'</dl>';
    }
    host.prepend(article);article.focus();article.scrollIntoView({block:'start'});
  }
  async function openTarget(target,history=true){
    const currentBrand=brand(),epoch=++navigationEpoch;
    const current=()=>gate.estado==='ready'&&brand()===currentBrand&&epoch===navigationEpoch;
    const navigate=async(section)=>{await gate.navegar(section);if(!current())throw Error('Contexto alterado');};
    try{
      const resolved=await api().api('/comando/registro',{method:'POST',body:JSON.stringify({marca:brand(),tipo:target.tipo,id:target.id})});
      if(!current()||resolved.marca!==currentBrand)return;
      target=resolved.destino;
      if(history){const route=['cc',brand(),target.tipo,target.id||''].map(encodeURIComponent).join('/');window.history.pushState(null,'','#'+route);}
      switch(target.tipo){
      case 'parceria':await gate.navegar('parcerias',{dispatch:false});if(current())await window.SoproPartnerships.open(resolved.dados.codigo);break;
      case 'pessoa':await navigate('crm');window.SoproCrm.history(target.id);break;
      case 'contato':await navigate('leads');await window.SoproFunnel.render();await window.SoproFunnel.openContact(target.id,resolved.dados);break;
      case 'exame':case 'agenda':
        if(api().can('operacional')){await navigate('laudos-espirometria');await window.SoproReports.openExam(target.codigo);}
        else if(target.pessoa_id){await navigate('crm');window.SoproCrm.history(target.pessoa_id,target.codigo);}
        else{await navigate('leads');}
        break;
      case 'laudo':await navigate('laudos-espirometria');await window.SoproReports.open(target.id);break;
      case 'fiscal':{
        let host;
        if(currentBrand==='eeglife'){
          await gate.carregar('eeg');if(!current())return;
          life.openEegModule('fiscal');host=document.querySelector('[data-eeg-section="fiscal"]');
        }else{await navigate('fiscal');host=document.getElementById('fiscal');}
        await selectedRecord(target,host,resolved.dados);break;
      }
      case 'acerto':await navigate('financeiro');await window.SoproPartnerShares.open(target.id);break;
      case 'pastore':await navigate('parcerias-pastore');await window.SoproPastoreSettlement.open(target.id);break;
      case 'parceiro':{
        if(brand()==='eeglife'){await gate.carregar('eeg');if(!current())return;life.openEegModule('parceiros');}
        else await navigate('crm');
        const data=resolved.dados;
        await selectedRecord(target,brand()==='eeglife'?document.querySelector('[data-eeg-section="parceiros"]'):document.getElementById('crm'),data);break;
      }
      case 'unidade':{
        if(brand()==='eeglife'){await gate.carregar('eeg');if(!current())return;life.openEegModule('parceiros');}else await navigate('crm');
        const unit=resolved.dados;
        await selectedRecord(target,brand()==='eeglife'?document.querySelector('[data-eeg-section="parceiros"]'):document.getElementById('crm'),unit);break;
      }
      case 'eeg_pessoa':await gate.carregar('eeg');if(!current())return;life.openEegModule('crm');await selectedRecord(target,document.querySelector('[data-eeg-section="crm"]'),resolved.dados);break;
      case 'eeg_exame':await gate.carregar('eeg');if(!current())return;await window.SoproEegOperacao.openExam(target.id,resolved.dados);break;
      default:throw Error('Destino indisponível');
      }
      const panel=document.getElementById('ccPendingPanel');if(panel)panel.hidden=true;
    }catch{if(current())announce('Não foi possível abrir este registro. Confira suas permissões ou tente novamente.');}
  }
  async function route(){
    let parts;try{parts=location.hash.slice(1).split('/').map(decodeURIComponent);}catch{announce('Link de registro inválido.');return;}
    if(parts[0]!=='cc'||parts.length!==4)return;
    if(!life.isAllowed(parts[1])){announce('Contexto não autorizado.');return;}
    await life.setContext(parts[1]);
    const kinds=['parceria','pessoa','contato','exame','agenda','laudo','fiscal','acerto','pastore','parceiro','unidade','eeg_exame','eeg_pessoa'];
    if(!kinds.includes(parts[2])||!/^[-a-f0-9]{36}$/.test(parts[3]))return;
    await openTarget({tipo:parts[2],id:parts[3]},false);
  }
  const menu=document.getElementById('soproMobileMenu'),media=window.matchMedia('(max-width:820px)');
  if(menu){
    menu.classList.toggle('cc-clinical-menu',gate.somenteClinico);
    const adapt=()=>{menu.open=gate.somenteClinico||!media.matches;};adapt();media.addEventListener('change',adapt);
  }
  const refreshButton=document.getElementById('refreshBtn');
  if(refreshButton)refreshButton.onclick=()=>window.location.reload();
  function clinicalPendingLabel(){
    if(!gate.somenteClinico||brand()!=='soprolife')return;
    const n=window.SoproReports?.pendingCount(),button=document.getElementById('ccPendingToggle');
    if(button){button.disabled=false;button.textContent=n==null?'Meus laudos':'Pendências ('+n+')';}
  }
  window.addEventListener('soprolife:report-queue',clinicalPendingLabel);
  setupHeader();clinicalPendingLabel();
  window.SoproCommandCenter={mountEeg:()=>loadHome('eeglife'),refresh:()=>loadHome(brand(),true),openTarget,pending,route};
  for(const b of ['soprolife','eeglife'])life.onContext(b,()=>{
    clearSearch();pendingEpoch++;navigationEpoch++;document.getElementById('ccPendingPanel').hidden=true;
    if(location.hash.startsWith('#cc/')&&location.hash.split('/')[1]!==b)window.history.replaceState(null,'',location.pathname+location.search);
    const toggle=document.getElementById('ccPendingToggle');toggle.disabled=true;toggle.textContent='Pendências';
    if(b==='soprolife'&&gate.somenteClinico){toggle.disabled=false;toggle.textContent='Meus laudos';return;}
    if(snapshots.has(b))updatePendingLabel(snapshots.get(b));
    loadHome(b);
  });
  window.addEventListener('popstate',route);
  document.addEventListener('soprolife:cadastro',()=>{snapshots.clear();if(homeRoot(brand()))loadHome(brand(),true);});
  window.SoproCommandCenter.ready=loadHome(brand());
})();
