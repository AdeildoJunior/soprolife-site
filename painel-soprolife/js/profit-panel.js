/* Lucro analítico sobre a API privada; nenhuma conta monetária é feita no navegador. */
(function () {
  "use strict";
  const root = document.getElementById("profitPanelRoot");
  if (!root) return;
  const charts = [];
  let payload = null;
  let period = "mes";
  let chosenDates = null;
  let initialized = false;
  const state = { local: "", parceiro: "", forma_pagamento: "", status_financeiro: "" };
  const COLORS = ["#087b72", "#5466ce", "#e2a83b", "#c75d53", "#40a89f", "#8394a3", "#bf79a9", "#70a154", "#9e7253", "#6b5b95"];
  const labels = {material:"Material",parceiro:"Parceiros",medico:"Médico",sala:"Sala",estacionamento:"Estacionamento",combustivel:"Combustível",cartao:"Cartão",imposto:"Imposto",tecnico:"Técnico",outros:"Outros"};
  const places = {downtown:"Downtown",nova_america:"Nova América",pastore:"Pastore · Ipanema",maclin:"Maclin",alergolife:"AlergoLife",home:"Domiciliar"};
  const sources = {financial_entry:"Lançamento financeiro",partnership:"Regra vigente da parceria",pastore_mesmo_dia:"Ida diária Pastore (regra histórica)",local_mesma_data:"Mesmo local e data · uma ida",domiciliar_individual:"Atendimento domiciliar · ida própria",manual:"Sessão registrada",receita_liquida_confirmada:"Receita líquida confirmada",partner_transfers:"Repasse registrado", "financial_entries.repasse":"Repasse financeiro",regra_percentual:"Regra percentual vigente",sem_lancamento_individual:"Sem lançamento individual",incluido:"Incluído no fechamento",a_receber:"A receber",recebido:"Recebido"};
  function placeLabel(key){return places[key]||key;}
  function sourceLabel(key){return sources[key]||key||"Pendente";}
  function dateLabel(value){return value ? value.split("-").reverse().join("/") : "—";}
  function ruleLabel(key){const [place,component]=key.split(".");return component?`${placeLabel(place)} · ${{parking:"Estacionamento",fuel:"Combustível",room_hour:"Sala por hora",minimum_hours:"Mínimo faturável por sessão (h)",partner_percent:"Repasse ao parceiro (%)"}[component]||component}`:({card_percent:"Taxa de cartão (%)",tax_percent:"Imposto (%)",technician:"Técnico",material:"Material por exame"}[key]||key);}
  const pendingLabels={receita_soprolife_ausente:"Receita SoproLife ausente",valor_recebido_nao_discriminado:"Valor recebido não discriminado",forma_de_pagamento_nao_classificada:"Forma de pagamento não classificada",base_tributavel_ausente:"Base tributável ausente",repasse_parceiro_nao_conciliado:"Repasse de parceiro não conciliado",repasse_medico_nao_conciliado:"Repasse médico não conciliado",sessao_operacional_ausente:"Sessão operacional ausente",preco_bruto_nao_comprovado:"Preço bruto não comprovado",recebido_superior_a_receita:"Recebido maior que a receita",cartao_superior_a_receita:"Parcela no cartão maior que a receita",regras_de_custo_sem_vigencia:"Regras de custo sem vigência na data",local_nao_classificado:"Local não classificado",taxa_existente_sem_classificacao:"Taxa existente sem classificação",base_receita_parceiro_nao_confirmada:"Base bruta ou líquida do parceiro não confirmada"};
  Object.assign(pendingLabels,{custos_da_sessao_incompletos:"Custos da sessão ainda não comprovados",parceiro_nao_definido:"Parceiro não informado",data_exame_imprecisa:"Data exata do exame não comprovada"});
  pendingLabels.valor_bruto_cartao_nao_comprovado = "Valor bruto processado no cartão não comprovado";
  sources.maclin_liquido_do_bruto = "Maclin: 70% do valor cheio";
  function pendingLabel(code){return pendingLabels[code]||code.replaceAll("_"," ");}
  function pendingMessage(row){return `Lucro pendente — faltam ${row.informacoes_faltantes} informações para fechamento.`;}
  function api(path, options) { return window.SoproM15.api(path, options); }
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[c]); }
  function brl(v) { return v == null ? "—" : Number(v).toLocaleString("pt-BR", {style:"currency",currency:"BRL"}); }
  function iso(d) { return [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-"); }
  function dates() {
    const today = new Date(), from = new Date(today);
    if (period === "hoje") return [iso(today),iso(today)];
    if (period === "semana") from.setDate(today.getDate()-6);
    else if (period === "mes") from.setDate(1);
    return [iso(from),iso(today)];
  }
  function option(value, chosen, label) { return `<option value="${esc(value)}"${value===chosen?" selected":""}>${esc(label || value)}</option>`; }
  function selectFilter(key, title, values) { return `<label>${title}<select name="${key}">${option("",state[key],"Todos")}${values.map(v=>option(v,state[key],key==="local"?placeLabel(v):sourceLabel(v))).join("")}</select></label>`; }
  function chart(id, type, data, options) {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === "undefined" || !data.labels.length) return;
    charts.push(new Chart(canvas, {type,data,options:Object.assign({responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},plugins:{legend:{position:"bottom"},tooltip:{callbacks:{label(item){return `${item.dataset.label}: ${item.raw==null?"Pendente":item.dataset.isPercent?item.raw+"%":brl(item.raw)}`;},afterBody(items){const i=items[0],row=i?.dataset.rows?.[i.dataIndex];return row?[`${row.exames} de ${row.total_exames} exames com cálculo completo`,...(row.incompletos?[pendingMessage(row)]:[])]:"";}}}}}, options || {})}));
  }
  function renderCharts(data) {
    charts.splice(0).forEach(c=>c.destroy());
    const months=data.por_mes||[], places=data.por_local||[];
    const [start,end]=chosenDates||dates();
    const span=(new Date(end)-new Date(start))/86400000;
    const trend=span<=31?(data.por_dia||[]):months;
    const mlabels=months.map(v=>v.chave.split("-").reverse().join("/")), plabels=places.map(v=>placeLabel(v.chave));
    const series=(rows,key,label,color)=>({label,data:rows.map(v=>v[key]==null?null:Number(v[key])),backgroundColor:color,borderColor:color,rows,isPercent:key==="margem",spanGaps:false});
    chart("profitMonthly", "bar", {labels:mlabels,datasets:[series(months,"receita","Receita comprovada",COLORS[0]),series(months,"custos","Custos reconhecidos",COLORS[3]),series(months,"lucro","Lucro por competência",COLORS[1])]}, {scales:{y:{beginAtZero:true}}});
    chart("profitPlaces", "bar", {labels:plabels,datasets:[series(places,"lucro","Lucro por competência",COLORS[0])]}, {indexAxis:"y",scales:{x:{beginAtZero:true}}});
    const comp=Object.entries(data.composicao||{}).filter(([,v])=>Number(v)>0);
    chart("profitComposition", "doughnut", {labels:comp.map(([k])=>labels[k]||k),datasets:[{data:comp.map(([,v])=>Number(v)),backgroundColor:COLORS}]}, {plugins:{tooltip:{callbacks:{label(item){const sum=comp.reduce((a,[,v])=>a+Number(v),0);return `${item.label}: ${brl(item.raw)} · ${sum?(item.raw*100/sum).toFixed(1):"0"}%`;}}}}});
    chart("profitMargins", "bar", {labels:plabels,datasets:[series(places,"margem","Margem %",COLORS[4])]}, {indexAxis:"y",scales:{x:{ticks:{callback:v=>v+"%"}}}});
    chart("profitTrend", "line", {labels:trend.map(v=>dateLabel(v.chave)),datasets:[series(trend,"receita","Receita comprovada",COLORS[0]),series(trend,"custos","Custos reconhecidos",COLORS[3]),series(trend,"lucro","Lucro por competência",COLORS[1])]}, {scales:{y:{beginAtZero:true}}});
  }
  function examDetail(row) {
    const complete=row.status==="completo";
    const item=(name,value)=>`<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`;
    return `<details class="profit-exam"><summary><strong>${esc(row.exame)}</strong><span>${esc(placeLabel(row.local))} · ${esc(dateLabel(row.data))}</span><span>${complete?brl(row.lucro):"Lucro aguardando fechamento"}</span></summary>
      <dl>${item("Receita comprovada",brl(row.receita))}${item("Recebido",brl(row.recebido))}${item("A receber comprovado",brl(row.pendente))}${item("Custos reconhecidos",brl(row.custos_comprovados))}${item("Custo total final",brl(row.custo_total))}${item("Lucro por competência",complete?brl(row.lucro):"Aguardando fechamento")}${item("Margem por competência",row.margem==null?"—":row.margem+"%")}${item("Lucro realizado / caixa",brl(row.lucro_realizado))}${item("Fiscal",row.status_fiscal||"NÃO EMITIDA")}</dl>
      ${complete?"":`<div class="profit-pending"><strong>Faltam:</strong><ul>${row.pendencias.map(p=>`<li>${esc(pendingLabel(p))}</li>`).join("")}${(row.componentes_pendentes||[]).map(k=>`<li>${esc(labels[k]||k)}: ${k==="sala"?"informar horas faturáveis ou custo de sala confirmado":"componente econômico ainda não determinado"}.</li>`).join("")}</ul></div>`}
      <p class="profit-note">Receita: ${esc(sourceLabel(row.fonte_receita))} · Pagamento: ${esc(row.forma_pagamento||"Não classificado")} · ${esc(sourceLabel(row.status_financeiro))} · Recebimento: ${esc(dateLabel(row.data_recebimento))}.</p>
      <p class="profit-note">${esc(row.imposto_descricao||"Imposto gerencial estimado")} · ${row.realizado?"Recebimento integral comprovado.":"Recebimento não comprovado integralmente; não bloqueia competência."}${row.cartao_fonte==="sem_evidencia_cartao_fechamento"?" Taxa de cartão não aplicada: sem evidência de cartão no fechamento.":""}</p>
      <p class="profit-note">Repasse: ${esc(sourceLabel(row.fonte_repasse_parceiro))} · Sessão: ${esc(sourceLabel(row.fonte_sessao))}${row.horas_faturaveis?` · ${esc(row.horas_faturaveis)} h faturável(is) · ${row.fonte_horas==="minimo_operacional_aprovado"?"mínimo operacional aprovado, sem horas adicionais":"sessão registrada"}`:""}.</p>
      ${row.partilha_maclin?`<p class="profit-note">Maclin · ${row.partilha_maclin.modelo==="historico_liquido"?"histórico com receita líquida confirmada":"valor cheio informado"}: bruto ${row.bruto==null?"não comprovado":brl(row.bruto)} · SoproLife (70%) ${brl(row.partilha_maclin.parcela_soprolife)} · Maclin (30%) ${row.partilha_maclin.parcela_maclin==null?"valor não comprovado":brl(row.partilha_maclin.parcela_maclin)}. A parcela Maclin já está separada da receita econômica; não há nova dedução nem confirmação de pagamento por este cálculo.</p>`:""}
      <details class="profit-breakdown"><summary>Componentes conhecidos${complete?"":" · composição parcial"}</summary><dl>${Object.entries(row.custos||{}).filter(([,v])=>v!=null).map(([k,v])=>item(labels[k]||k,brl(v))).join("")}${item("Base gerencial · receita reconhecida",brl(row.base_imposto))}${item("Alíquota vigente",row.aliquota_imposto==null?"—":row.aliquota_imposto+"%")}</dl></details>
      ${window.SoproM15.can("admin")&&(row.pendencias.includes("sessao_operacional_ausente")||row.pendencias.includes("custos_da_sessao_incompletos"))&&!row.session_id?`<button type="button" data-profit-session="${esc(row.id)}">Definir sessão / ida</button>`:""}
      ${window.SoproM15.can("admin")&&row.pendencias.includes("base_receita_parceiro_nao_confirmada")?`<button type="button" data-profit-basis="${esc(row.id)}">Confirmar base da receita</button>`:""}
      ${window.SoproM15.can("admin")&&row.session_id?`<button type="button" data-profit-session-edit="${esc(row.session_id)}">Ajustar sessão / ida</button>`:""}
    </details>`;
  }
  function render(data) {
    payload=data;
    const [defaultStart,defaultEnd]=dates();
    const previousStart=chosenDates?.[0];
    const previousEnd=chosenDates?.[1];
    const k=data.kpis;
    const coverage=(key)=>`${k.cobertura[key]} de ${k.total_exames} exames com informação`;
    const closed=`Base: ${k.exames} de ${k.total_exames} exames completos`;
    const metrics=[
      ["Receita comprovada",brl(k.receita),coverage("receita")],
      ["Custos reconhecidos",brl(k.custos),k.incompletos?"Parciais · custos finais pendentes":closed],
      ["Lucro por competência",brl(k.lucro),closed],
      ["Margem por competência",k.margem==null?"—":k.margem+"%",closed],
      ["Exames com cálculo completo",k.exames,"Resultados fechados"],
      ["Exames com cálculo pendente",k.incompletos,"Fatos conhecidos preservados"],
      ["Recebido comprovado",brl(k.recebido),coverage("recebido")],
      ["A receber comprovado",brl(k.a_receber),coverage("pendente")],
      ["Lucro médio por exame",brl(k.lucro_medio),closed],
      ["Custo total final calculado",brl(k.custo_total_calculado),closed],
      ["Custo médio por exame",brl(k.custo_medio),closed],
      ["Lucro realizado / caixa",brl(k.lucro_realizado),`${k.exames_caixa_comprovado||0} exame(s) com recebimento integral comprovado`],
      ["Lucro com caixa pendente",brl(k.lucro_previsto),`${k.exames_caixa_pendente||0} exame(s) com recebimento a confirmar`],
      ["Quantidade de exames",k.total_exames,"Exames produtivos no filtro"]
    ];
    const pendingPlaces=(data.por_local||[]).filter(v=>v.incompletos).map(v=>`<li title="${esc(pendingMessage(v))}">${esc(placeLabel(v.chave))}: ${v.incompletos} exame(s) pendente(s). ${esc(pendingMessage(v))}</li>`).join("");
    root.innerHTML=`<section class="profit-panel" aria-labelledby="profitTitle"><div class="profit-head"><div><h3 id="profitTitle">Custos e lucro por exame</h3><p>Competência: receita reconhecida menos custos econômicos. Fiscal e recebimento são acompanhados separadamente.</p></div>${window.SoproM15.can("admin")?'<button type="button" data-profit-rules>Configurar custos</button>':""}</div>
      <form class="profit-filters" id="profitFilters"><label>Período<select name="period">${["hoje","semana","mes","personalizado"].map(v=>option(v,period,({hoje:"Hoje",semana:"Últimos 7 dias",mes:"Mês atual",personalizado:"Personalizado"})[v])).join("")}</select></label><label>Início<input name="start" type="date" data-m15-date="full" aria-label="Início" value="${esc(previousStart||defaultStart)}"></label><label>Fim<input name="end" type="date" data-m15-date="full" aria-label="Fim" value="${esc(previousEnd||defaultEnd)}"></label>${selectFilter("local","Local",data.filtros.locais)}${selectFilter("parceiro","Parceiro",data.filtros.parceiros)}${selectFilter("forma_pagamento","Pagamento",data.filtros.formas)}${selectFilter("status_financeiro","Status financeiro",data.filtros.status)}<button type="submit">Aplicar</button></form>
      <div class="profit-kpis">${metrics.map(([name,value,note])=>`<div class="profit-kpi"><small>${esc(name)}</small><strong>${esc(value)}</strong><span>${esc(note)}</span></div>`).join("")}</div>
      <div class="profit-grid">${[["profitMonthly","Receita × custos × lucro por mês"],["profitPlaces","Lucro por local"],["profitComposition","Composição dos custos"],["profitMargins","Margem por local"],["profitTrend","Evolução da receita, custos e lucro"]].map(([id,title])=>`<div class="profit-chart"><h4>${title}</h4><div class="profit-canvas"><canvas id="${id}" role="img" aria-label="${title}"></canvas></div>${id==="profitComposition"?`<p class="profit-note">${data.composicao_parcial?"Composição parcial · somente componentes reconhecidos.":"Componentes reconhecidos."}${Object.values(data.composicao||{}).some(v=>Number(v)>0)?"":" Sem custos positivos comprovados neste filtro."}</p>`:["profitPlaces","profitMargins"].includes(id)&&pendingPlaces?`<ul class="profit-chart-pending">${pendingPlaces}</ul>`:""}</div>`).join("")}</div>
      <div class="profit-pending"><strong>Pendências para cálculo de lucro</strong>${Object.keys(data.pendencias).length?`<ul>${Object.entries(data.pendencias).map(([k,v])=>`<li>${esc(pendingLabel(k))}: ${esc(v)}</li>`).join("")}</ul>`:"<p>Nenhuma pendência neste filtro.</p>"}</div>
      <h4>Memória por exame</h4><div class="profit-list">${data.exames.length?data.exames.map(examDetail).join(""):"<p>Nenhum exame produtivo neste período.</p>"}</div>
      <p class="profit-note">Valores pela competência do exame. Lucro e margem calculados cobrem somente os exames completos; a margem usa a receita desses mesmos exames. Valores desconhecidos aparecem como —. Custos parciais não representam o custo final. O lucro realizado considera somente exames com receita integralmente recebida; custos seguem as regras econômicas, sem afirmar quitação de despesas. NFS-e não emitida e recebimento contratual desconhecido não bloqueiam competência.</p></section>`;
    window.SoproM15DatePicker?.attachAll(root);
    renderCharts(data);
  }
  async function load(start,end) {
    if (!window.SoproM15 || !window.SoproM15.can("gestor")) return;
    if (!start||!end) [start,end]=chosenDates||dates();
    chosenDates=[start,end];
    const query=new URLSearchParams({start,end});
    Object.entries(state).forEach(([k,v])=>{if(v)query.set(k,v);});
    root.innerHTML='<section class="profit-panel">Carregando custos e lucro…</section>';
    try { render(await api("/financeiro/lucro/painel?"+query)); }
    catch(e){root.innerHTML=`<section class="profit-panel profit-error" role="alert">${esc(e.message||e)}</section>`;}
  }
  function modal(content) {
    const dialog=document.createElement("dialog");dialog.className="profit-modal";dialog.innerHTML=content;
    document.body.appendChild(dialog);window.SoproM15DatePicker?.attachAll(dialog);dialog.showModal();dialog.addEventListener("close",()=>dialog.remove());
    dialog.querySelector("[data-close]")?.addEventListener("click",()=>dialog.close());return dialog;
  }
  async function rulesDialog() {
    try {
      const data=await api("/financeiro/lucro/regras");
      const dialog=modal(`<form class="profit-form" data-rule-form><h3>Regras de custo</h3><p>Cadastre uma vigência histórica somente com prova do valor e da data. Informe a referência da evidência na justificativa. Uma nova versão preserva as anteriores; custos atuais não retroagem automaticamente.</p><label>Regra<select name="key">${Object.keys(data.padrao).map(k=>option(k,"",`${ruleLabel(k)} · referência atual ${data.padrao[k]}`)).join("")}</select></label><label>Vigência<input type="date" name="effective_from" data-m15-date="full" aria-label="Vigência" value="${iso(new Date())}" required></label><label>Valor em R$ ou %<input type="number" name="value" step="0.01" min="0" required></label><label>Justificativa e referência da evidência<input name="reason" required minlength="3" maxlength="300"></label><div class="profit-actions"><button type="submit">Salvar nova versão</button><button type="button" data-close>Fechar</button></div><div role="status"></div><h4>Versões registradas</h4>${data.versoes.map(r=>`<p>${esc(ruleLabel(r.chave))} · ${esc(dateLabel(r.vigencia))} · ${esc(r.valor)} · ${esc(r.motivo)}</p>`).join("")||"<p>Nenhuma versão registrada; valores de referência não são aplicados sem vigência.</p>"}</form>`);
      dialog.querySelector("form").onsubmit=async ev=>{ev.preventDefault();const form=ev.currentTarget,body=Object.fromEntries(new FormData(form));try{await api("/financeiro/lucro/regras",{method:"POST",body:JSON.stringify(body)});dialog.close();load(root.querySelector('[name="start"]')?.value,root.querySelector('[name="end"]')?.value);}catch(e){form.querySelector('[role="status"]').textContent=e.message||e;}};
    } catch(e){alert(e.message||e);}
  }
  function sessionDialog(id, existing, allRows) {
    const rows=allRows||payload?.exames||[];
    const row=rows.find(r=>r.id===id);if(!row)return;
    const peers=rows.filter(r=>r.data===row.data&&r.local===row.local);
    const selected=new Set(existing?.exames||[id]);
    const val=(key)=>esc(existing?.[key]??"");
    const dialog=modal(`<form class="profit-form" data-session-form><h3>${existing?"Ajustar":"Nova"} sessão / ida · ${esc(placeLabel(row.local))} · ${esc(dateLabel(row.data))}</h3><fieldset><legend>Exames desta ida</legend>${peers.map(r=>`<label><input type="checkbox" name="exam_ids" value="${esc(r.id)}" ${selected.has(r.id)?"checked":""}>${esc(r.exame)}</label>`).join("")}</fieldset><label>Horas faturáveis da sala<input type="number" step="0.01" min="0.01" name="billable_hours" value="${val("horas_faturaveis")}" ${["downtown","nova_america"].includes(row.local)&&!existing?.sala?"required":""}></label><label>Estacionamento confirmado (opcional)<input type="number" step="0.01" min="0" name="parking_override" value="${val("estacionamento")}"></label><label>Combustível confirmado (opcional)<input type="number" step="0.01" min="0" name="fuel_override" value="${val("combustivel")}"></label><label>Aluguel confirmado (opcional)<input type="number" step="0.01" min="0" name="room_override" value="${val("sala")}"></label><label>Observação<input name="note" maxlength="500" value="${val("observacao")}"></label><div class="profit-actions"><button type="submit">${existing?"Salvar ajuste":"Confirmar sessão"}</button><button type="button" data-close>Cancelar</button></div><div role="status"></div></form>`);
    dialog.querySelector("form").onsubmit=async ev=>{ev.preventDefault();const form=ev.currentTarget,fd=new FormData(form);const body={place:row.local,service_date:row.data,exam_ids:fd.getAll("exam_ids"),status:"confirmado"};for(const key of ["billable_hours","parking_override","fuel_override","room_override","note"]){if(fd.get(key))body[key]=fd.get(key);}try{await api("/financeiro/lucro/sessoes"+(existing?"/"+encodeURIComponent(existing.id):""),{method:existing?"PATCH":"POST",body:JSON.stringify(body)});dialog.close();load(...(chosenDates||dates()));}catch(e){form.querySelector('[role="status"]').textContent=e.message||e;}};
  }
  async function editSession(id){try{const [start,end]=chosenDates||dates();const response=await api("/financeiro/lucro/sessoes?"+new URLSearchParams({start,end}));const session=response.sessoes.find(s=>s.id===id);if(!session)throw new Error("Sessão não encontrada.");const all=await api("/financeiro/lucro/painel?"+new URLSearchParams({start:session.data,end:session.data}));sessionDialog(session.exames[0],session,all.exames);}catch(e){alert(e.message||e);}}
  async function createSession(id){try{const row=payload?.exames.find(r=>r.id===id);if(!row)return;const all=await api("/financeiro/lucro/painel?"+new URLSearchParams({start:row.data,end:row.data}));sessionDialog(id,null,all.exames);}catch(e){alert(e.message||e);}}
  function basisDialog(id){const row=payload?.exames.find(r=>r.id===id);if(!row)return;const dialog=modal(`<form class="profit-form"><h3>Confirmar base · ${esc(row.exame)}</h3><p>Confira o extrato ou contrato. Esta confirmação classifica o lançamento existente e não cria receita nova.</p><label>O valor do lançamento é<select name="basis"><option value="bruto">Preço bruto antes do repasse</option><option value="liquido">Receita líquida após o repasse</option></select></label><label>Preço bruto (opcional se líquido)<input type="number" step="0.01" min="0.01" name="gross_amount"></label><label>Referência da conferência<input name="evidence_note" required minlength="5" maxlength="300"></label><div class="profit-actions"><button type="submit">Confirmar</button><button type="button" data-close>Cancelar</button></div><div role="status"></div></form>`);dialog.querySelector("form").onsubmit=async ev=>{ev.preventDefault();const form=ev.currentTarget,fd=new FormData(form);const body={exam_id:id,basis:fd.get("basis"),evidence_note:fd.get("evidence_note")};if(fd.get("gross_amount"))body.gross_amount=fd.get("gross_amount");try{await api("/financeiro/lucro/bases-receita",{method:"POST",body:JSON.stringify(body)});dialog.close();load(...(chosenDates||dates()));}catch(e){form.querySelector('[role="status"]').textContent=e.message||e;}};}
  root.addEventListener("submit",ev=>{if(ev.target.id!=="profitFilters")return;ev.preventDefault();const fd=new FormData(ev.target);period=fd.get("period");for(const k of Object.keys(state))state[k]=fd.get(k)||"";load(fd.get("start"),fd.get("end"));});
  root.addEventListener("change",ev=>{if(ev.target.name==="period"){period=ev.target.value;if(period!=="personalizado"){const [a,b]=dates();for(const [name,value] of [["start",a],["end",b]]){const input=root.querySelector(`[name="${name}"]`);input.value=value;const display=input.parentElement.querySelector(".m15-date-input");if(display)display.value=dateLabel(value);}}}});
  root.addEventListener("click",ev=>{if(ev.target.closest("[data-profit-rules]"))rulesDialog();const b=ev.target.closest("[data-profit-session]");if(b)createSession(b.dataset.profitSession);const edit=ev.target.closest("[data-profit-session-edit]");if(edit)editSession(edit.dataset.profitSessionEdit);const basis=ev.target.closest("[data-profit-basis]");if(basis)basisDialog(basis.dataset.profitBasis);});
  function boot(){if(initialized||!window.SoproM15||!window.SoproM15.getUser())return;initialized=true;load();}
  document.querySelectorAll('[data-section="financeiro"]').forEach(node=>node.addEventListener("click",()=>setTimeout(boot,80)));
  let tries=0;const timer=setInterval(()=>{boot();if(initialized||++tries>20)clearInterval(timer);},1000);
})();
