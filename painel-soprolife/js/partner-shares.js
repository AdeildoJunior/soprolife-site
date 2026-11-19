/* Partilhas econômicas e movimentos de acerto no razão canônico. */
(function () {
  "use strict";
  const root = document.getElementById("partnerSharesRoot");
  if (!root) return;
  const m15 = () => window.SoproM15;
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const brl = v => Number(v).toLocaleString("pt-BR", {style:"currency",currency:"BRL"});
  let rows = [];
  const status = {pendente:"Acerto pendente",parcial:"Acerto parcial",acertado:"Acertado",sem_repasse:"Sem repasse pendente",aguardando_pagamento:"Aguardando pagamento do paciente"};
  const receiver = {soprolife:"SoproLife recebeu o bruto",parceiro:"Clínica recebeu o bruto",dividido:"Pagamento já dividido"};
  function form(row) {
    if (!m15().can("gestor") || row.status_pagamento_paciente !== "Recebido") return "";
    const pending = Number(row.a_pagar) > 0 || Number(row.a_receber) > 0;
    const incoming = Number(row.a_receber) > 0;
    const regularized = row.movimentos.filter(m => m.natureza === "regularizacao").reduce((v,m) => v+Number(m.valor),0);
    if (!pending && (row.recebido_por === "parceiro" || regularized >= Number(row.parcela_soprolife))) return "";
    return `<details class="pastore-settlement-actions"><summary>Registrar ${pending?"acerto":"entrada da parcela própria em conta"}</summary>
      <form class="pastore-settlement-form" data-share="${esc(row.id)}" data-key="${esc(m15().idemKey())}">
        <label>Tipo de movimento<select name="natureza">${pending?'<option value="acerto">Baixar obrigação entre as partes</option>':''}${row.recebido_por!=="parceiro"?'<option value="regularizacao">Regularização da parcela própria</option>':''}</select></label>
        <input type="hidden" name="direcao" value="${pending&&!incoming?'saida':'entrada'}">
        <label>Valor (R$)<input name="valor" inputmode="decimal" required value="${pending?(incoming?row.a_receber:row.a_pagar):Number(row.parcela_soprolife)-regularized}"></label>
        <label>Data<input name="data" type="date" required></label><label>Hora<input name="hora" type="time" step="1" required></label>
        <label>Conta SoproLife<input name="conta" maxlength="60" required placeholder="Ex.: C6 PJ"></label>
        <label>Forma<select name="forma"><option>Pix</option><option>Dinheiro</option><option>Cartão</option><option>Outro</option></select></label>
        <label>Responsável/origem do movimento<input name="origem" maxlength="120" placeholder="Ex.: Faustino"></label>
        <button type="submit">Registrar movimento sem nova receita</button><p role="status"></p>
      </form></details>`;
  }
  let loading=null;
  function load(){if(loading)return loading;loading=loadNow().finally(()=>loading=null);return loading;}
  async function loadNow() {
    if (!m15()?.can("leitura")) { root.innerHTML=""; return; }
    try {
      rows=(await m15().api("/financeiro/partilhas")).itens;
      root.innerHTML=`<article class="panel pastore-settlement-panel"><h3>Partilhas e acertos de clínicas</h3>
        <p>Bruto do exame, parcelas econômicas e entradas em conta são valores distintos. Acerto não gera receita.</p>
        ${rows.length?rows.map(row=>`<article class="pastore-settlement-row" data-share-record="${esc(row.id)}" tabindex="-1"><h4>${esc(row.exame)} · ${esc(row.unidade)}</h4>
          <p>${esc(receiver[row.recebido_por])} · ${esc(row.forma_pagamento||"Forma não informada")} · ${esc(status[row.status_acerto])}</p>
          <dl>${[["Bruto do exame",row.bruto],["Parcela clínica",row.parcela_parceiro],["Parcela SoproLife",row.parcela_soprolife],["A pagar à clínica",row.a_pagar],["A receber da clínica",row.a_receber],["Entradas em conta registradas",row.entradas_em_conta],["Saídas de conta registradas",row.saidas_de_conta]].map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${brl(v)}</dd></div>`).join("")}</dl>
          ${row.movimentos.map(m=>`<p>${esc(m.natureza==="acerto"?"Acerto":"Regularização")} · ${brl(m.valor)} · ${esc(m.direcao)} · ${esc(m.conta)}${m.origem_bancaria?" · "+esc(m.origem_bancaria):""} · ${esc(new Date(m.ocorrido_em).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo"}))}</p>`).join("")}${form(row)}</article>`).join(""):"<p>Nenhuma partilha nova registrada. Histórico preservado.</p>"}</article>`;
      root.querySelectorAll("form").forEach(f=>{
        f.elements.natureza.addEventListener("change",()=>{
          const row=rows.find(r=>r.id===f.dataset.share),regular=f.elements.natureza.value==="regularizacao";
          f.elements.direcao.value=regular||Number(row.a_receber)>0?"entrada":"saida";
          const already=row.movimentos.filter(m=>m.natureza==="regularizacao").reduce((n,m)=>n+Number(m.valor),0);
          f.elements.valor.value=regular ? (Number(row.parcela_soprolife)-already).toFixed(2) :
            (Number(row.a_receber)>0?row.a_receber:row.a_pagar);
        });
      });
    } catch(e) { root.textContent="Não foi possível carregar partilhas: "+e.message; }
  }
  root.addEventListener("submit",async event=>{
    const f=event.target;if(!f.matches("[data-share]"))return;event.preventDefault();
    const button=f.querySelector("button"),message=f.querySelector("[role=status]");button.disabled=true;
    try {
      const raw=f.elements.valor.value.trim().replace(/\.(?=\d{3}(?:\D|$))/g,"").replace(",",".");
      if(!/^\d+(\.\d{1,2})?$/.test(raw)||Number(raw)<=0)throw Error("Informe um valor válido.");
      await m15().api(`/financeiro/partilhas/${f.dataset.share}/movimentos`,{method:"POST",body:JSON.stringify({
        valor:raw,natureza:f.elements.natureza.value,direcao:f.elements.direcao.value,conta:f.elements.conta.value,
        origem_bancaria:f.elements.origem.value||null,forma_pagamento:f.elements.forma.value,
        ocorrido_em:`${f.elements.data.value}T${f.elements.hora.value}-03:00`,idempotency_key:f.dataset.key})});
      await load();
    } catch(e) {message.textContent=e.message;button.disabled=false;}
  });
  window.SoproPartnerShares={open:async function(id){await load();const row=root.querySelector('[data-share-record="'+CSS.escape(id)+'"]');if(!row)throw Error('Acerto indisponível');row.focus();row.scrollIntoView({block:'start'});}};
  document.addEventListener("soprolife:cadastro",load);
  if(m15())m15().onSessionChange(load);
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",load);else load();
})();
