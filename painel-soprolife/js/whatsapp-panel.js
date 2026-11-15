/* Operational identification only. No message body, sender phone or credentials. */
(function () {
  'use strict';
  const base='/integrations/whatsapp';
  const api=(p,o)=>window.SoproM15.api(p,o);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date=v=>v?new Date(v).toLocaleString('pt-BR',{timeZone:'America/Sao_Paulo'}):'—';
  const reasons={unknown:'Pessoa ainda não identificada',ambiguous_person:'Mais de uma pessoa com este telefone',ambiguous_lead:'Contato comercial precisa de escolha',chronology_review:'Evento anterior recebido com atraso; conferir cronologia'};
  async function render(root,page=1){
    if(!root||!window.SoproM15.can('operacional'))return;
    root.innerHTML='<section class="m15-panel"><h3>WhatsApp / Integração</h3><div data-wa-status role="status">Carregando…</div><h4>WhatsApp pendente de identificação</h4><div data-wa-intakes></div></section>';
    const status=root.querySelector('[data-wa-status]');
    if(window.SoproM15.can('admin')){
      try{
        const s=await api(base+'/status');
        status.innerHTML=`<p><strong>${esc(s.message)}</strong></p><p>Integração: ${s.enabled?'Ativa':'Desativada'} · Configuração: ${s.configuration_ready?'Pronta':'Incompleta'} · Webhook: ${esc(s.webhook)}</p><p>Último evento válido: ${esc(date(s.last_valid_event_at))}</p>${s.last_error?'<p>Último erro técnico: falha de processamento · '+esc(date(s.last_error.at))+'</p>':''}${s.diagnostic?'<p>Diagnóstico: configuração incompleta; processamento bloqueado.</p>':''}`;
      }catch(_){status.textContent='Não foi possível consultar o estado da integração.';}
    }else status.textContent='Estado da integração disponível à administração.';
    const el=root.querySelector('[data-wa-intakes]');
    try{
      const d=await api(base+'/intakes?pagina='+page);
      el.innerHTML=d.itens.length?`<div class="m15-table-wrap"><table class="m15-table"><thead><tr><th scope="col">Primeiro contato</th><th scope="col">Número</th><th scope="col">Identificação</th><th scope="col">Ação</th></tr></thead><tbody>${d.itens.map(i=>`<tr><td>${esc(date(i.first_at))}</td><td>${esc(i.phone_masked)}</td><td>${esc(reasons[i.state]||'Pendente')}</td><td>${i.lead_id?'Conferir no histórico do funil':`<button type="button" data-wa-identify="${esc(i.id)}">Identificar</button>`}</td></tr>`).join('')}</tbody></table></div>`:'<p>Nenhuma entrada pendente.</p>';
      if(page>1||d.tem_proxima){el.insertAdjacentHTML('beforeend',`<button type="button" data-prev ${page===1?'disabled':''}>Anterior</button> Página ${page} <button type="button" data-next ${d.tem_proxima?'':'disabled'}>Próxima</button>`);el.querySelector('[data-prev]').onclick=()=>render(root,page-1);el.querySelector('[data-next]').onclick=()=>render(root,page+1);}
      el.querySelectorAll('[data-wa-identify]').forEach(b=>b.onclick=()=>identify(d.itens.find(i=>i.id===b.dataset.waIdentify),()=>window.SoproFunnel.render()));
    }catch(_){el.textContent='Não foi possível carregar as entradas pendentes.';}
  }
  function identify(intake,done){
    const d=document.createElement('dialog');d.className='m15-panel funnel-dialog whatsapp-dialog';
    d.innerHTML=`<h3>Identificar contato WhatsApp</h3><p>${esc(intake.phone_masked)} · ${esc(date(intake.first_at))}</p><p>Confira a identidade no WhatsApp Business antes de associar. O horário original será preservado.</p><form class="m15-form">
      <label>Ação<select name="mode"><option value="existing">Associar a pessoa existente</option><option value="new">Cadastrar pessoa</option></select></label>
      <div data-existing><label>Buscar pessoa por nome, código ou telefone<input name="query" autocomplete="off"></label><button type="button" data-search>Buscar</button><label>Pessoa existente<select name="person_id"><option value="">Selecione após buscar</option></select></label></div>
      <div data-new hidden><label>Nome completo<input name="name" maxlength="300" autocomplete="off"></label><p>Cadastre somente após confirmar a identidade. Outros dados podem ser completados na Central.</p></div>
      <label>Contato comercial<select name="lead_id"><option value="">Reutilizar contato compatível quando único</option><option value="new">Criar novo contato prospectivo</option></select></label>
      <label class="whatsapp-confirm"><input type="checkbox" name="confirmed" required> Conferi a identidade e o contato que devo associar.</label>
      <button type="submit">Identificar e vincular</button><button type="button" data-close>Voltar</button><p role="status"></p></form>`;
    document.body.appendChild(d);d.showModal();const form=d.querySelector('form');
    const message=d.querySelector('[role=status]');
    form.elements.mode.onchange=()=>{d.querySelector('[data-existing]').hidden=form.elements.mode.value!=='existing';d.querySelector('[data-new]').hidden=form.elements.mode.value!=='new';};
    d.querySelector('[data-close]').onclick=()=>{d.close();d.remove();};d.addEventListener('cancel',()=>d.remove());
    d.querySelector('[data-search]').onclick=async()=>{
      try{const q=form.elements.query.value.trim();if(!q){message.textContent='Digite um termo para buscar.';return;}
        const data=await api('/pessoas/busca',{method:'POST',body:JSON.stringify({q,tamanho:20,status:'ativo'})});
        form.elements.person_id.innerHTML='<option value="">Selecione a pessoa confirmada</option>'+data.itens.filter(p=>!p.arquivado).map(p=>`<option value="${esc(p.id)}">${esc(p.public_code)} · ${esc(p.nome_completo)}</option>`).join('');
        message.textContent=data.itens.length?'Escolha a pessoa após conferir a identidade.':'Nenhuma pessoa encontrada.';
      }catch(_){message.textContent='Falha ao buscar pessoas.';}
    };
    form.elements.query.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();d.querySelector('[data-search]').click();}};
    const key=window.crypto.randomUUID();
    form.onsubmit=async e=>{
      e.preventDefault();const btn=e.submitter;btn.disabled=true;
      const payload={idempotency_key:key,canal_entrada:'whatsapp',servico_interesse:'outro'};
      if(form.elements.mode.value==='existing')payload.person_id=form.elements.person_id.value;
      else payload.pessoa={nome_completo:form.elements.name.value.trim()};
      if(form.elements.lead_id.value==='new')payload.new_contact=true;
      else if(form.elements.lead_id.value)payload.lead_id=form.elements.lead_id.value;
      try{await api(base+'/intakes/'+intake.id+'/identify',{method:'POST',body:JSON.stringify(payload)});d.close();d.remove();done();}
      catch(err){
        // API errors are sanitized; candidate names are never put into a URL/log.
        message.textContent=err.message||'Não foi possível identificar. Confira os dados.';
        const detail=err.detalhe;
        if(detail?.codigo==='whatsapp_ambiguous_lead'){
          form.elements.lead_id.innerHTML='<option value="">Escolha um contato</option>'+detail.contatos.map(l=>`<option value="${esc(l.id)}">${esc(l.public_code)}</option>`).join('')+'<option value="new">Criar novo contato prospectivo</option>';
          message.textContent=detail.mensagem;
        }
        btn.disabled=false;
      }
    };
  }
  window.SoproWhatsApp={render};
})();
