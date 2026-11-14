/* Funil prospectivo no Centro de Comando existente. API autenticada, sem snapshots. */
(function () {
  "use strict";
  const api=(path,opts)=>window.SoproM15.api(path,opts);
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct=v=>v==null?'—':v.toLocaleString('pt-BR')+'%';
  const date=v=>v?new Date(v.length===10?v+'T12:00:00':v).toLocaleString('pt-BR',v.length===10?{dateStyle:'short'}:{dateStyle:'short',timeStyle:'short',timeZone:'America/Sao_Paulo'}):'—';
  const labels={NOVO_CONTATO:'Novo contato',PRIMEIRA_RESPOSTA:'Respondido',AGENDADO:'Agendado',REMARCADO:'Remarcado',CANCELADO:'Cancelado',NAO_COMPARECEU:'Não compareceu',EXAME_REALIZADO:'Exame realizado',AGUARDANDO_RETORNO:'Aguardando retorno',PERDIDO:'Encerrado'};
  const state={config:null,page:1,filters:null};
  const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const options=(obj,all=true)=> (all?'<option value="">Todos</option>':'')+Object.entries(obj).map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('');
  const table=(headers,rows)=>`<div class="m15-table-wrap"><table class="m15-table"><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">Sem contatos registrados nesta coorte.</td></tr>`}</tbody></table></div>`;
  function dialog(html){const d=document.createElement('dialog');d.className='m15-panel funnel-dialog';d.innerHTML=html;document.body.appendChild(d);d.showModal();return d;}
  function error(el,e){el.textContent='Não foi possível concluir: '+(e.message||'tente novamente.');}
  async function render(){
    const root=document.getElementById('funnelRoot');if(!root)return;
    if(!window.SoproM15?.can('leitura')){root.innerHTML='<div class="m15-panel">Entre no Núcleo para acessar o Funil Comercial.</div>';return;}
    root.innerHTML='<p>Carregando funil…</p>';
    try{
      const cfg=state.config||await api('/funil/config');state.config=cfg;
      if(!state.filters){const end=today();state.filters={inicio:end.slice(0,8)+'01',fim:end};}
      root.innerHTML=`<div class="m15-panel"><h2>Funil Comercial</h2><p>Contato → resposta → agendamento → exame</p>${window.SoproM15.can('operacional')?'<button id="funnelNew" type="button">+ Novo contato</button>':''}
        <form id="funnelFilters" class="m15-form">
        <label>De<input type="date" name="inicio" aria-label="Início do período" data-m15-date="full" required></label><label>Até<input type="date" name="fim" aria-label="Fim do período" data-m15-date="full" required></label>
        <label>Unidade<select name="unidade">${options(cfg.unidades)}</select></label><label>Origem<select name="origem">${options(cfg.origens)}</select></label>
        <label>Canal<select name="canal">${options(cfg.canais)}</select></label><label>Serviço<select name="servico">${options(cfg.servicos)}</select></label><button type="submit">Aplicar filtros</button></form>
        <div id="funnelError" role="status"></div><div id="funnelMetrics"></div></div>
        <div class="m15-panel"><h3>Contatos recentes · operação</h3><p>Lista operacional de todos os períodos; os filtros acima afetam os indicadores.</p><div id="funnelContacts"></div></div>`;
      const form=root.querySelector('form');Object.entries(state.filters).forEach(([k,v])=>{if(form.elements[k])form.elements[k].value=v;});
      window.SoproM15DatePicker?.attachAll(form);
      form.onsubmit=e=>{e.preventDefault();state.filters=Object.fromEntries(new FormData(form));render();};
      if(root.querySelector('#funnelNew'))root.querySelector('#funnelNew').onclick=()=>window.SoproCentral.open('contato');
      const data=await api('/funil/metricas?'+new URLSearchParams(Object.fromEntries(Object.entries(state.filters).filter(([,v])=>v))));
      const cards=[['contatos','Novos contatos'],['respondidos','Respondidos'],['agendados','Agendados'],['realizados','Realizados'],['cancelados','Cancelados'],['remarcacoes','Remarcações'],['nao_compareceu','Não compareceu'],['perdidos','Perdidos/encerrados'],['aguardando_retorno','Aguardando retorno']];
      root.querySelector('#funnelMetrics').innerHTML=`<p class="m15-muted">Funil pré-atendimento sem cobertura completa antes de ${esc(date(data.implantado_em))}.</p><p>${esc(data.coorte)}</p>
        <div class="funnel-cards">${cards.map(([k,v])=>`<div><span>${v}</span><strong>${data[k]}</strong></div>`).join('')}</div>
        <div class="funnel-cards">${[['contato_resposta','Contato → resposta'],['contato_agendamento','Contato → agendamento'],['agendamento_exame','Agendamento → exame'],['contato_exame','Contato → exame']].map(([k,v])=>`<div><span>${v}</span><strong>${pct(data.conversoes[k])}</strong></div>`).join('')}</div>
        <h3>Tempos e cobertura</h3><p>${esc(data.tempos_escopo)}</p>
        ${table(['Etapa','Mediana (minutos)','Disponibilidade'],Object.entries(data.tempos).map(([k,v])=>[esc(({contato_resposta:'Contato → resposta',contato_agendamento:'Contato → agendamento',agendamento_exame:'Agendamento → exame'})[k]),v.mediana_minutos==null?'—':v.mediana_minutos.toLocaleString('pt-BR'),`${v.disponiveis}/${v.total} contatos`]))}
        <h3>Cobertura do funil</h3><p>${esc(data.cobertura_escopo)}</p>${table(['Indicador','Com dado / total','Cobertura'],Object.entries(data.cobertura).map(([k,v])=>[esc(({exames_rastreados:'Exames realizados com contato rastreado',origem_registrada:'Contatos com origem registrada',agendamentos_vinculados:'Reservas atuais vinculadas'})[k]),`${v.com_dado}/${v.total}`,pct(v.percentual)]))}
        <h3>Por origem</h3>${table(['Origem','Contatos','Agendados','Realizados','Contato → exame'],data.por_origem.filter(r=>r.contatos).map(r=>[esc(r.rotulo),r.contatos,r.agendados,r.realizados,pct(r.conversoes.contato_exame)]))}
        <h3>Por unidade</h3>${table(['Unidade','Contatos','Agendados','Realizados','Cancelados','Contato → exame'],data.por_unidade.filter(r=>r.contatos).map(r=>[esc(r.rotulo),r.contatos,r.agendados,r.realizados,r.cancelados,pct(r.conversoes.contato_exame)]))}
        <h3>Coortes semanais</h3><p>Semana PARCIAL inclui a semana atual ou cortada pelos filtros. Coortes recentes tiveram menos tempo para converter.</p>
        ${table(['Semana','Contatos','Agendados','Realizados','Contato → agendamento','Agendamento → exame','Contato → exame'],data.semanas.map(r=>[`${esc(date(r.inicio))} – ${esc(date(r.fim))}${r.parcial?' · <strong>PARCIAL</strong>':''}`,r.contatos,r.agendados,r.realizados,pct(r.conversoes.contato_agendamento),pct(r.conversoes.agendamento_exame),pct(r.conversoes.contato_exame)]))}
        <details><summary>Origem e unidade por semana</summary>${data.semanas.map(r=>`<h4>${esc(date(r.inicio))}${r.parcial?' · PARCIAL':''}</h4>${table(['Origem','Contatos'],Object.entries(r.origens).filter(([,v])=>v).map(([k,v])=>[esc(cfg.origens[k]),v]))}${table(['Unidade','Contatos'],Object.entries(r.unidades).filter(([,v])=>v).map(([k,v])=>[esc(cfg.unidades[k]),v]))}`).join('')}</details>`;
      await loadContacts(root);
    }catch(e){root.innerHTML='<p role="alert">Falha ao carregar Funil Comercial. Reabra a seção para tentar novamente.</p>';}
  }
  async function loadContacts(root){
    const res=await api('/funil/contatos?pagina='+state.page);
    const write=window.SoproM15.can('operacional');
    const el=root.querySelector('#funnelContacts');
    el.innerHTML=table(['Contato / paciente','Origem / canal / unidade','Status','Próximo passo'],res.itens.map(l=>[`${esc(l.public_code)} · ${esc(l.nome_completo)}<br>${esc(date(l.primeiro_contato_em))}`,`${esc(state.config.origens[l.origem_aquisicao])}<br>${esc(state.config.canais[l.canal_entrada])}<br>${esc(state.config.unidades[l.unidade_pretendida])}`,esc(labels[l.status_funil]),`<button data-history="${esc(l.id)}">Histórico</button>${write?`<button data-action="${esc(l.id)}">Atualizar etapa</button><button data-edit="${esc(l.id)}">Corrigir origem/canal</button><button data-link="${esc(l.id)}">Vincular exame existente</button>${l.exam_ids.length?`<button data-performed="${esc(l.exam_ids[0])}">Registrar exame realizado</button>`:''}`:''}`]))+
      `<button id="funnelPrev" ${state.page===1?'disabled':''}>Anterior</button><span> Página ${state.page} </span><button id="funnelNext" ${res.tem_proxima?'':'disabled'}>Próxima</button>`;
    el.querySelector('#funnelPrev').onclick=()=>{state.page--;loadContacts(root);};el.querySelector('#funnelNext').onclick=()=>{state.page++;loadContacts(root);};
    el.querySelectorAll('[data-history]').forEach(b=>b.onclick=async()=>{const d=dialog('<h3>Histórico do contato</h3><div></div><button>Fechar</button>');d.querySelector('button').onclick=()=>{d.close();d.remove();};try{const h=await api('/funil/contatos/'+b.dataset.history+'/eventos');d.querySelector('div').innerHTML=table(['Evento','Quando','Data anterior','Data prevista','Motivo'],h.itens.map(e=>[esc(labels[e.evento]||({EXAME_VINCULADO:'Exame vinculado',DIMENSOES_CORRIGIDAS:'Classificação corrigida'})[e.evento]||e.evento),esc(date(e.em)),esc(date(e.data_anterior)),esc(date(e.data_prevista)),esc(state.config.motivos[e.motivo]||'Não informado')]));}catch(e){error(d.querySelector('div'),e);}});
    el.querySelectorAll('[data-performed]').forEach(b=>b.onclick=async()=>{if(!window.confirm('Confirmar que este exame foi realizado?'))return;try{await api('/espirometrias/'+b.dataset.performed,{method:'PATCH',body:JSON.stringify({status:'Realizado'})});render();}catch(e){error(root.querySelector('#funnelError'),e);}});
    el.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>editDimensions(res.itens.find(l=>l.id===b.dataset.edit)));
    el.querySelectorAll('[data-link]').forEach(b=>b.onclick=()=>linkExam(res.itens.find(l=>l.id===b.dataset.link)));
    el.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b.dataset.action));
  }
  function linkExam(lead){
    const d=dialog(`<h3>Vincular exame a ${esc(lead.public_code)}</h3><p>Use apenas um contato real anterior ao exame, da mesma pessoa. O vínculo não inventa resposta ou agendamento.</p><form class="m15-form"><label>Código do exame<input name="exam_code" placeholder="ESP-000001" required></label><button>Vincular exame</button><button type="button" id="close">Voltar</button><p role="status"></p></form>`);
    d.querySelector('#close').onclick=()=>{d.close();d.remove();};
    d.querySelector('input').focus();
    d.querySelector('form').onsubmit=async e=>{
      e.preventDefault();const btn=e.submitter;btn.disabled=true;
      try{
        const res=await api('/espirometrias?public_code='+encodeURIComponent(e.target.elements.exam_code.value.trim()));
        const exam=res.itens.find(exam=>exam.person_id===lead.person_id);
        if(!exam)throw new Error('Exame não encontrado para a pessoa deste contato. Confira o código.');
        await api('/funil/contatos/'+lead.id+'/vincular-exame',{method:'POST',body:JSON.stringify({exam_id:exam.id})});
        d.close();d.remove();render();
      }catch(err){error(d.querySelector('[role=status]'),err);btn.disabled=false;}
    };
  }
  function editDimensions(l){
    const cfg=state.config;const d=dialog(`<h3>Corrigir classificação do contato</h3><form class="m15-form">${[['origem_aquisicao','Origem',cfg.origens],['canal_entrada','Canal',cfg.canais],['unidade_pretendida','Unidade',cfg.unidades],['servico_interesse','Serviço',cfg.servicos]].map(([k,label,opts])=>`<label>${label}<select name="${k}">${options(opts,false)}</select></label>`).join('')}<label>Unidade parceira (opcional)<select name="partner_unit_id"><option value="">Não se aplica</option>${cfg.unidades_parceiras.map(u=>`<option value="${esc(u.id)}">${esc(u.nome)}</option>`).join('')}</select></label><button>Salvar correção auditada</button><button type="button" id="close">Voltar</button><p role="status"></p></form>`);
    const form=d.querySelector('form');Object.keys(l).forEach(k=>{if(form.elements[k])form.elements[k].value=l[k];});d.querySelector('#close').onclick=()=>{d.close();d.remove();};form.onsubmit=async e=>{e.preventDefault();try{await api('/funil/contatos/'+l.id,{method:'PATCH',body:JSON.stringify((()=>{const p=Object.fromEntries(new FormData(form));const u=cfg.unidades_parceiras.find(u=>u.id===p.partner_unit_id);p.partner_unit_id=u?u.id:null;p.partner_id=u?u.partner_id:null;return p;})())});d.close();d.remove();render();}catch(err){error(d.querySelector('p'),err);}};
  }
  function action(id){
    const d=dialog(`<h3>Registrar etapa</h3><form class="m15-form"><label>Ação<select name="evento">${Object.entries(labels).filter(([k])=>!['NOVO_CONTATO','EXAME_REALIZADO'].includes(k)).map(([k,v])=>`<option value="${k}">${k==='PRIMEIRA_RESPOSTA'?'Registrar primeira resposta':esc(v)}</option>`).join('')}</select></label><label>Data prevista (ao agendar/remarcar)<input type="date" name="data_prevista" aria-label="Data prevista do exame" data-m15-date="full"></label><label>Motivo (opcional)<select name="motivo"><option value="">Não informado</option>${options(state.config.motivos,false)}</select></label><p>Agendar cria ou atualiza o exame na agenda existente. A primeira resposta é preservada.</p><button>Registrar etapa</button><button type="button" id="close">Voltar</button><p role="status"></p></form>`);
    window.SoproM15DatePicker?.attachAll(d);
    d.querySelector('#close').onclick=()=>{d.close();d.remove();};d.querySelector('form').onsubmit=async e=>{e.preventDefault();const btn=e.submitter;btn.disabled=true;const payload=Object.fromEntries([...new FormData(e.target)].filter(([,v])=>v));try{await api('/funil/contatos/'+id+'/eventos',{method:'POST',body:JSON.stringify(payload)});d.close();d.remove();render();}catch(err){error(d.querySelector('[role=status]'),err);btn.disabled=false;}};
  }
  function boot(){document.querySelectorAll('.nav-item[data-section="leads"]').forEach(b=>b.addEventListener('click',render));window.SoproM15?.onSessionChange(()=>{state.config=null;if(document.getElementById('leads')?.classList.contains('active'))render();});}
  window.SoproFunnel={render};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
