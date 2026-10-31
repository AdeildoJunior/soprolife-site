/* EEGLife: operação no M15 corporativo, com domínio clínico EEG independente.
 * API valida RBAC e atribuição; UI adapta ações aos papéis da sessão.
 * Nano usa importação manual privada, sem API de fabricante ou interpretação.
 * Custos continuam no resumo por marca do livro-caixa corporativo existente.
 */
(function () {
  "use strict";

  var MODULOS_VIVOS = {
    visao: montarVisao,
    crm: montarCrm,
    tarefas: montarTarefas,
    marketing: montarMarketing,
    fontes: montarFontes,
    lancamentos: montarEventos,
    documentos: montarDocumentos,
    parceiros: montarParceiros,
    nucleo: montarNucleo,
    cadastros: montarCentral,
    exames: montarExames,
    laudos: montarLaudos,
    leads: montarLeads,
    custos: montarCustos,
    financeiro: montarFinanceiro,
    fiscal: montarFiscal,
  };

  var cache = { modalidades: null, locais: null };

  function m15() { return window.SoproM15; }

  function esc(txt) {
    return String(txt == null ? "" : txt)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function dataBr(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("pt-BR") + " " +
      d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }

  function diaBr(value) { if(!value)return "—";var m=String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);return m?m[3]+"/"+m[2]+"/"+m[1]:dataBr(value); }

  var ROTULO_STATUS = {
    cadastro: "Cadastro", agendado: "Agendado", confirmado: "Confirmado",
    realizado: "Realizado", aguardando_envio_laudo: "Aguardando envio",
    enviado_para_laudo: "Enviado para laudo", em_laudo: "Em laudo",
    laudo_recebido: "Laudo recebido", laudo_disponivel: "Disponível ao paciente",
    cancelado: "Cancelado",
  };

  var exames = {};

  function badgeStatus(status) {
    return '<span class="badge eeg-badge" data-eeg-status="' + esc(status) + '">' +
      esc(ROTULO_STATUS[status] || status) + "</span>";
  }

  function vazio(mensagem) {
    return '<div class="empty-state">' + esc(mensagem) + "</div>";
  }

  /* Erro da API no padrão da casa: {"erro": {"codigo", "mensagem"}}. */
  function mensagemErro(erro) {
    if (!erro) return "Falha ao carregar.";
    if (erro.codigo === "http_403") {
      return "Esta sessão não tem acesso à operação EEGLife.";
    }
    return erro.mensagem || erro.message || "Falha ao carregar.";
  }

  function carregar(secao, promessa, render) {
    secao.querySelector("[data-eeg-corpo]").innerHTML =
      '<div class="empty-state">Carregando…</div>';
    var ticket = crypto.randomUUID();
    secao.dataset.eegRequest = ticket;
    return promessa.then(function (dados) {
      if(secao.dataset.eegRequest !== ticket || !secao.isConnected) return;
      secao.querySelector("[data-eeg-corpo]").innerHTML = render(dados);
      prepararTabela(secao);
      secao.querySelectorAll("[data-eeg-preparar]").forEach(function (b) {
        b.onclick = function () { prepararFiscal(secao, b.getAttribute("data-eeg-preparar")); };
      });
      secao.querySelectorAll("[data-eeg-documento]").forEach(function (b) {
        b.onclick = function () { abrirFiscal(secao, documentos[b.getAttribute("data-eeg-documento")]); };
      });
      secao.querySelectorAll("[data-eeg-pagamento]").forEach(function (b) {
        b.onclick = function () {
          formulario("Atualizar pagamento EEG", select("status", "Situação", [["Pendente", "Pendente"], ["Recebido", "Recebido"]]) + campo("data_recebimento", "Data real de recebimento", "date"),
            function (dados) { return post("/eeg/financeiro/" + b.getAttribute("data-eeg-pagamento") + "/pagamento", dados, "PATCH").then(function () { atualizar(secao); }); });
        };
      });
      secao.querySelectorAll('[data-eeg-edit]').forEach(function(b){b.onclick=function(){editarCadastro(secao,centralRows[b.dataset.eegEdit]);};});
      secao.querySelectorAll('[data-eeg-history]').forEach(function(b){b.onclick=function(){historico(secao,b.dataset.eegHistory);};});
      secao.querySelectorAll("[data-eeg-atender]").forEach(function (b) {
        b.onclick = function () { novoAtendimento(secao, b.getAttribute("data-eeg-atender")); };
      });
      secao.querySelectorAll("[data-eeg-exame]").forEach(function (b) {
        b.onclick = function () { operar(secao, exames[b.getAttribute("data-eeg-exame")]); };
      });
    }).catch(function (err) {
      if(secao.dataset.eegRequest !== ticket || !secao.isConnected) return;
      secao.querySelector("[data-eeg-corpo]").innerHTML =
        '<div class="empty-state">' + esc(mensagemErro(err && err.detalhe || err)) + "</div>";
    });
  }

  /* Casca comum das telas vivas: cabeçalho do produto + corpo que troca. */
  function moldura(titulo, resumo, acoes) {
    return '<article class="panel">' +
      '<div class="panel-header"><h3>' + esc(titulo) + "</h3>" +
      "<span>" + esc(resumo) + "</span>" +
      (acoes || "") + "</div>" +
      '<div data-eeg-corpo></div></article>';
  }

  function tabela(colunas, linhasHtml) {
    return '<div class="table-wrap"><table><thead><tr>' +
      colunas.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + linhasHtml + "</tbody></table></div>";
  }

  // ── Central de Cadastros (escopo EEG) ───────────────────────────────────

  var atendimentoPrefill = null;
  function montarCentral(secao) {
    var prefill = atendimentoPrefill; atendimentoPrefill = null;
    if(!document.getElementById('m15css')){
      var css=document.createElement('link');css.id='m15css';css.rel='stylesheet';css.href='css/m15.css?v=2026102801';document.head.appendChild(css);
    }
    secao.innerHTML = cabecalho(secao) + moldura("Novo Atendimento EEG",
      "Paciente e atendimento no mesmo fluxo. A identidade corporativa é compartilhada.", "");
    return carregar(secao, Promise.all([m15().api('/eeg/modalidades'), m15().api('/eeg/locais'),
      m15().api('/eeg/municipios'), todos('/eeg/pessoas')]), function(r) {
      return atendimentoHtml(r) + '<details class="eeg-directory"><summary>Cadastros e vínculos · ações auxiliares</summary>' +
        '<div class="eeg-toolbar"><button type="button" data-eeg-novo-paciente>Cadastrar / vincular paciente</button>' +
        '<label>Buscar vínculo EEGLife<input type="search" data-eeg-busca placeholder="Nome, CPF, telefone ou PES"></label></div>' +
        '<div data-eeg-directory>' + renderCentral(r[3]) + '</div></details>';
    }).then(function() {
      var form=secao.querySelector('[data-eeg-atendimento]'); if(!form)return;
      ligarAtendimento(secao,form,prefill);
      secao.querySelector('[data-eeg-novo-paciente]').onclick=function(){cadastrar(secao);};
      var busca=secao.querySelector('[data-eeg-busca]'), ticket=0;
      busca.oninput=function(){var n=++ticket;buscarPessoas(busca.value,false).then(function(r){
        if(n!==ticket || !busca.isConnected)return;
        secao.querySelector('[data-eeg-directory]').innerHTML=renderCentral(r);prepararTabela(secao);
        secao.querySelectorAll('[data-eeg-edit]').forEach(function(b){b.onclick=function(){editarCadastro(secao,centralRows[b.dataset.eegEdit]);};});
        secao.querySelectorAll('[data-eeg-history]').forEach(function(b){b.onclick=function(){historico(secao,b.dataset.eegHistory);};});
        secao.querySelectorAll('[data-eeg-atender]').forEach(function(b){b.onclick=function(){novoAtendimento(secao,b.dataset.eegAtender);};});
      }).catch(function(e){if(n===ticket)secao.querySelector('[data-eeg-directory]').textContent=mensagemErro(e);});};
    });
  }

  function atendimentoHtml(r) {
    function passo(n,t,html){return '<section class="cad-passo"><h4 class="cad-passo-titulo"><span class="cad-passo-num">'+n+'</span>'+t+'</h4>'+html+'</section>';}
    return '<form class="eeg-atendimento" data-eeg-atendimento>' +
      passo(1,'Paciente','<div class="cad-picker-search"><label>Buscar pessoa existente<input type="search" data-at-search placeholder="Nome, CPF, telefone ou PES" autocomplete="off"></label><button type="button" data-at-find>Buscar</button></div>' +
        '<p class="cad-microcopy">Busque no cadastro compartilhado antes de criar. Nome ou telefone não vinculam ninguém automaticamente.</p>' +
        '<div data-at-results role="status"></div><div data-at-selected hidden></div>' +
        '<fieldset data-at-new><legend>Paciente novo — preencha abaixo, ou selecione uma pessoa na busca</legend><div class="eeg-at-grid">' +
        campo('cpf','CPF (opcional)')+campo('data_nascimento','Nascimento','date')+campo('nome_completo','Nome completo','text',true)+
        campo('telefone','WhatsApp com DDD/DDI (opcional)','tel')+campo('email','E-mail (opcional)','email')+
        select('sexo','Sexo', [['','Não informado'],['feminino','Feminino'],['masculino','Masculino'],['outro','Outro']])+'</div></fieldset>' +
        select('consentimento_whatsapp','Consentimento operacional WhatsApp · somente EEGLife', [['desconhecido','Não informado'],['concedido','Autorizado pelo paciente'],['revogado','Não autorizado / revogado'],['','Manter consentimento EEG existente']])+
        '<div data-at-duplicates></div>') +
      passo(2,'Tipo de atendimento EEG','<p class="cad-microcopy">Classificação para registro e planejamento. Validação técnica no Nano/iCelera pendente; selecionar não confirma disponibilidade.</p>' +
        '<div class="cad-tipos" role="radiogroup" aria-label="Modalidade EEG">'+r[0].itens.map(function(m){return '<label class="cad-tipo'+(!m.ativo?' is-inactive':'')+'"><input type="radio" name="modalidade_codigo" value="'+esc(m.codigo)+'" '+(m.ativo?'required':'disabled')+'><span class="cad-tipo-nome">'+esc(m.nome)+'</span><span class="cad-tipo-desc">'+(m.ativo?(m.ativa_operacionalmente?'Ativa operacionalmente':'Validação técnica pendente · classificação permitida'):'Inativa · indisponível para registro')+'</span></label>';}).join('')+'</div>')+
      passo(3,'Dados do atendimento EEG','<div class="eeg-at-grid">'+
        campo('data_exame','Data do exame','date',true)+campo('hora_exame','Horário','time',true)+
        select('status_inicial','Status do EEG',[['agendado','Agendado'],['confirmado','Confirmado'],['realizado','Realizado']])+
        '<label>Modalidade EEG<input data-at-modalidade readonly value="Selecione no passo 2"></label>'+
        select('municipio_atendimento_ibge','Município onde o exame será/foi realizado', [['','Selecione o município']].concat(r[2].itens.map(function(m){return [m.codigo,m.rotulo];})))+
        select('origem_tipo','Local / modalidade de atendimento',[['direto','Direto EEGLife · local manual'],['parceiro','Unidade parceira cadastrada']])+
        select('partner_unit_id','Unidade / local', [['','Outro / escrever manualmente']].concat(r[1].itens.map(function(u){return [u.id,u.nome];})))+
        campo('local_atendimento','Local de atendimento','text',true)+
        select('origem_canal','Origem',[['direto','Direto'],['site','SITE'],['whatsapp','WhatsApp'],['indicacao','Indicação'],['parceiro','Parceiro'],['outro','Outro']])+
        campo('tecnico_responsavel','Técnico / responsável (opcional)')+
        '</div><p class="cad-microcopy">Município vem do catálogo IBGE. Unidade e endereço não definem o município fiscal.</p>'+
        '<div class="eeg-at-finance"><h5>Financeiro</h5><div class="eeg-at-grid">'+
        select('reteste_sem_cobranca','Reteste técnico / cobrança',[['false','Atendimento com cobrança'],['true','Reteste técnico sem nova cobrança']])+
        campo('valor','Valor do EEG (R$)','number',true)+
        select('pagamento_status','Status do pagamento',[['Pendente','Pendente'],['Recebido','Recebido']])+
        campo('data_recebimento','Data de recebimento','date')+
        select('forma_pagamento','Forma de pagamento',[['','Não informada'],['pix','Pix'],['dinheiro','Dinheiro'],['cartao','Cartão'],['transferencia','Transferência'],['outro','Outro']])+
        '</div></div><label>Observações operacionais<textarea name="observacao" maxlength="500" rows="3" placeholder="Somente informações operacionais; laudo médico em módulo próprio."></textarea></label>')+
      '<section class="eeg-readiness" aria-labelledby="eegAtReadyTitle"><h4 id="eegAtReadyTitle">Prontidão do atendimento EEG</h4><ul data-at-ready></ul><p>Configuração fiscal EEG pendente de aprovação</p><p class="cad-microcopy">A orientação fiscal não impede o cadastro. Este fluxo não emite nota, laudo, PDF clínico ou assinatura.</p></section>'+
      '<p data-at-error role="alert" tabindex="-1"></p><div data-at-success role="status" tabindex="-1"></div>'+
      '<button class="eeg-at-submit" type="submit">SALVAR ATENDIMENTO EEG</button></form>';
  }

  function ligarAtendimento(secao,f,prefill) {
    var selected=null, key='eeg-'+crypto.randomUUID(), searchTicket=0, saving=false;
    var error=f.querySelector('[data-at-error]'), results=f.querySelector('[data-at-results]');
    var newBox=f.querySelector('[data-at-new]'), chosen=f.querySelector('[data-at-selected]'), duplicates=f.querySelector('[data-at-duplicates]');
    function input(n){return f.elements.namedItem(n);}
    function val(n){return input(n).value.trim();}
    function dateDisplay(n){return input(n).parentElement.querySelector('.m15-date-input')||input(n);}
    function setDate(n,value){input(n).value=value;var display=dateDisplay(n);if(display!==input(n))display.value=value?window.SoproM15DatePicker.isoToBr(value):'';}
    function nova(){var p={};['cpf','data_nascimento','nome_completo','telefone','email','sexo'].forEach(function(n){if(val(n))p[n]=val(n);});p.consentimento_whatsapp=val('consentimento_whatsapp')||'desconhecido';p.origem=val('origem_canal');return p;}
    function selecionar(p){searchTicket++;selected=p;newBox.hidden=true;newBox.disabled=true;chosen.hidden=false;duplicates.innerHTML='';results.textContent='';
      chosen.innerHTML='<strong>'+esc(p.public_code)+' · '+esc(p.nome_completo)+'</strong><p>'+esc(p.cpf_mascarado||'CPF não informado')+' · Identidade corporativa preservada</p><button type="button" data-at-change>Trocar paciente / cadastrar novo</button>';
      input('consentimento_whatsapp').value='';chosen.querySelector('button').onclick=function(){selected=null;newBox.hidden=false;newBox.disabled=false;chosen.hidden=true;input('consentimento_whatsapp').value='desconhecido';ready();f.querySelector('[data-at-search]').focus();};ready();
    }
    function ready(){
      var modality=f.querySelector('[name="modalidade_codigo"]:checked'), retest=val('reteste_sem_cobranca')==='true';
      var modalityLabel=modality?modality.closest('label').querySelector('.cad-tipo-nome').textContent:'Selecione no passo 2';
      f.querySelector('[data-at-modalidade]').value=modalityLabel;
      var items=[['Identidade / paciente',!!selected||val('nome_completo').length>=2,selected?'Pessoa existente selecionada':'Paciente novo · identidade conferida ao salvar'],
        ['Data',!!val('data_exame')&&!!val('hora_exame'),val('data_exame')?diaBr(val('data_exame'))+' · '+val('hora_exame'):'Informe data e horário'],['Modalidade',!!modality,modalityLabel],
        ['Município',!!val('municipio_atendimento_ibge'),input('municipio_atendimento_ibge').selectedOptions[0].textContent],['Local',val('local_atendimento').length>=2&&(val('origem_tipo')!=='parceiro'||!!val('partner_unit_id')),val('local_atendimento')||'Informe o local e a unidade quando parceira'],
        ['Financeiro',retest||(Number(val('valor'))>0&&(val('pagamento_status')!=='Recebido'||!!val('data_recebimento'))),retest?'Reteste sem receita':'Valor e recebimento explícitos'],
        ['Arquivo / traçado',false,val('status_inicial')==='realizado'?'Aguardando importação manual após salvar':'Não exigido para agendamento'],
        ['Fiscal',false,'Configuração fiscal EEG pendente de aprovação']];
      f.querySelector('[data-at-ready]').innerHTML=items.map(function(i){return '<li class="'+(i[1]?'is-ready':'is-pending')+'"><strong>'+esc(i[0])+' · '+(i[1]?'Preenchido':'Pendente / próxima etapa')+'</strong><span>'+esc(i[2])+'</span></li>';}).join('');
    }
    f.querySelector('[data-at-find]').onclick=function(){var q=f.querySelector('[data-at-search]').value.trim(),ticket=++searchTicket;
      if(q.length<3){results.textContent='Informe ao menos 3 caracteres para buscar no cadastro compartilhado.';return;}
      results.textContent='Buscando…';buscarPessoas(q,true).then(function(r){if(ticket!==searchTicket||!f.isConnected)return;
        results.textContent=r.itens.length?'Confira e selecione a identidade correta:':'Nenhuma pessoa encontrada. Preencha o paciente novo abaixo.';
        r.itens.forEach(function(v){var b=document.createElement('button');b.type='button';b.textContent=v.pessoa.public_code+' · '+v.pessoa.nome_completo+' · '+(v.pessoa.cpf_mascarado||'CPF não informado');b.onclick=function(){selecionar(v.pessoa);};results.appendChild(b);});
      }).catch(function(e){if(ticket===searchTicket)results.textContent=mensagemErro(e);});
    };
    f.querySelector('[data-at-search]').onkeydown=function(ev){if(ev.key==='Enter'){ev.preventDefault();f.querySelector('[data-at-find]').click();}};
    f.querySelector('[data-at-search]').oninput=function(){searchTicket++;results.textContent='';};
    newBox.addEventListener('input',function(){duplicates.innerHTML='';});
    input('partner_unit_id').onchange=function(){var id=val('partner_unit_id');input('origem_tipo').value=id?'parceiro':'direto';
      if(id){input('local_atendimento').value=input('partner_unit_id').selectedOptions[0].textContent;}ready();};
    input('origem_tipo').onchange=function(){if(val('origem_tipo')==='direto')input('partner_unit_id').value='';ready();};
    input('pagamento_status').onchange=function(){dateDisplay('data_recebimento').required=val('pagamento_status')==='Recebido';if(val('pagamento_status')==='Pendente')setDate('data_recebimento','');ready();};
    input('reteste_sem_cobranca').onchange=function(){var r=val('reteste_sem_cobranca')==='true';if(r){input('valor').value='0';input('pagamento_status').value='Pendente';setDate('data_recebimento','');input('forma_pagamento').value='';}['valor','pagamento_status','data_recebimento','forma_pagamento'].forEach(function(n){input(n).disabled=r;});dateDisplay('data_recebimento').disabled=r;var calendar=input('data_recebimento').parentElement.querySelector('button');if(calendar)calendar.disabled=r;dateDisplay('data_recebimento').required=false;ready();};
    input('valor').step='0.01';input('valor').min='0.01';
    input('municipio_atendimento_ibge').required=true;input('municipio_atendimento_ibge').value='3304557';
    // Existing EEG commercial default is direct; do not silently change it to SITE.
    ['nome_completo','local_atendimento','tecnico_responsavel'].forEach(function(n){input(n).maxLength=n==='nome_completo'?300:200;});
    input('data_nascimento').max=new Date().toLocaleDateString('en-CA');input('data_recebimento').max=new Date().toLocaleDateString('en-CA');
    if(window.SoproM15DatePicker){
      ['data_exame','data_nascimento','data_recebimento'].forEach(function(n){
        var holder=input(n);holder.setAttribute('data-m15-date','full');
        holder.setAttribute('aria-label',holder.parentElement.firstChild.textContent);
        window.SoproM15DatePicker.attach(holder);
      });
    }
    f.addEventListener('input',ready);f.addEventListener('change',ready);f.addEventListener('m15:date-change',ready);ready();
    if(prefill&&prefill.enrollmentId)todos('/eeg/pessoas').then(function(r){var v=r.itens.find(function(v){return v.id===prefill.enrollmentId;});if(v&&f.isConnected)selecionar(v.pessoa);}).catch(function(e){error.textContent=mensagemErro(e);});
    f.onsubmit=function(ev){ev.preventDefault();if(saving)return;saving=true;var submit=f.querySelector('[type="submit"]');submit.disabled=true;error.textContent='';
      var data={};['modalidade_codigo','status_inicial','municipio_atendimento_ibge','origem_tipo','partner_unit_id','local_atendimento','origem_canal','tecnico_responsavel','valor','pagamento_status','data_recebimento','forma_pagamento','observacao','consentimento_whatsapp'].forEach(function(n){if(val(n))data[n]=val(n);});
      var datePicker=window.SoproM15DatePicker;
      var parsed=datePicker?datePicker.parseFlex(val('data_exame')):{ok:/^\d{4}-\d{2}-\d{2}$/.test(val('data_exame')),precisao:'dia',iso:val('data_exame')};
      if(!parsed.ok||parsed.precisao!=='dia'){error.textContent='Informe a data completa do exame em DD/MM/AAAA.';error.focus();submit.disabled=false;saving=false;return;}
      data.agendado_para=new Date(parsed.iso+'T'+val('hora_exame')).toISOString();data.reteste_sem_cobranca=val('reteste_sem_cobranca')==='true';data.idempotency_key=key;
      if(selected)data.person_id=selected.id;else data.nova_pessoa=nova();
      var verify=selected?Promise.resolve({itens:[]}):post('/eeg/pessoas/verificar',data.nova_pessoa);
      verify.then(function(r){
        if(r.itens.length&&!duplicates.querySelector('input:checked')){
          duplicates.innerHTML='<p>Há cadastros semelhantes. Confira a identidade antes de prosseguir:</p>';
          r.itens.forEach(function(p){var b=document.createElement('button');b.type='button';b.textContent='Selecionar '+p.public_code+' · '+p.nome_completo;b.onclick=function(){buscarPessoas(p.public_code,true).then(function(r){if(r.itens[0])selecionar(r.itens[0].pessoa);}).catch(function(e){error.textContent=mensagemErro(e);});};duplicates.appendChild(b);});
          var l=document.createElement('label');l.className='eeg-at-confirm';l.innerHTML='<input type="checkbox"> Conferi os candidatos: é outra pessoa. Confirmar novo cadastro.';duplicates.appendChild(l);throw Error('Confira os cadastros semelhantes. CPF já existente não pode ser duplicado.');
        }
        if(data.nova_pessoa)data.nova_pessoa.confirmar_nova_pessoa=!!r.itens.length;
        return post('/eeg/atendimentos',data);
      }).then(function(exam){return (prefill&&prefill.leadId?post('/eeg/atividades/'+prefill.leadId+'/converter',{enrollment_id:exam.enrollment_id,exam_id:exam.id}):Promise.resolve()).then(function(){
        var success=f.querySelector('[data-at-success]');success.innerHTML='<strong>Atendimento EEG salvo · '+esc(exam.public_code)+'</strong><p>Paciente, vínculo e financeiro registrados. Nenhum laudo ou emissão fiscal foi gerado.</p><button type="button" data-at-another>Novo Atendimento EEG</button>';
        f.querySelectorAll('input,select,textarea,button').forEach(function(i){i.disabled=true;});success.querySelector('button').disabled=false;success.querySelector('button').onclick=function(){montarCentral(secao);};success.focus();
      });}).catch(function(e){error.textContent=mensagemErro(e);error.focus();submit.disabled=false;}).finally(function(){saving=false;});
    };
  }

  var centralRows={};
  function renderCentral(dados) {
    var itens = (dados && dados.itens) || [];
    if (!itens.length) {
      return vazio("Nenhuma pessoa vinculada à operação EEGLife ainda.");
    }
    var linhas = itens.map(function (v) {
      centralRows[v.id]=v;
      var p = v.pessoa || {};
      var pendencias = (p.cadastro_pendencias || []).length;
      return "<tr>" +
        "<td>" + esc(p.public_code || "—") + "</td>" +
        "<td>" + esc(p.nome_completo || "—") + "</td>" +
        "<td>" + esc(p.cpf_mascarado || "não informado") + "</td>" +
        "<td>" + esc(String(v.exames)) + "</td>" +
        "<td>" + (pendencias
          ? '<span class="badge eeg-badge-pendente">' + pendencias + " pendência(s)</span>"
          : '<span class="badge">Completo</span>') + "</td>" +
        '<td><button type="button" data-eeg-atender="' + esc(v.id) + '">Novo Atendimento</button> <button data-eeg-edit="' + esc(v.id) + '">Editar</button> <button data-eeg-history="' + esc(v.id) + '">Histórico</button></td></tr>';
    }).join("");
    return tabela(["Código", "Paciente", "CPF", "Exames EEG", "Cadastro", "Ações"], linhas) +
      '<p class="eeg-nota">Somente pessoas com vínculo EEGLife aparecem aqui. ' +
      "A identidade é a mesma do cadastro corporativo — ninguém é duplicado por marca.</p>";
  }

  // ── Agendamentos ────────────────────────────────────────────────────────

  function montarAgenda(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Agendamentos",
      "Exames de EEG agendados e confirmados.", '<button type="button" data-eeg-agendar>Agendar EEG</button>'
    );
    ligarAgenda(secao);
    return carregar(secao, todos("/eeg/exames"), function (dados) {
      var itens = ((dados && dados.itens) || []).filter(function (e) {
        return e.status === "agendado" || e.status === "confirmado" ||
               e.status === "cadastro";
      });
      if (!itens.length) return vazio("Nenhum exame de EEG agendado.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Quando", "Local", "Situação", "Ações"],
        itens.map(function (e) {
          return "<tr><td>" + esc(e.public_code) + "</td>" +
            "<td>" + esc((e.paciente || {}).nome_completo || "—") + "</td>" +
            "<td>" + esc(e.modalidade_nome || e.modalidade_codigo) + "</td>" +
            "<td>" + esc(dataBr(e.agendado_para)) + "</td>" +
            "<td>" + esc(e.local_nome || "—") + "</td>" +
            "<td>" + badgeStatus(e.status) + "</td><td>" + botaoExame(e) + "</td></tr>";
        }).join("")
      );
    });
  }

  // ── Exames e traçados ───────────────────────────────────────────────────

  function montarExames(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Exames e traçados",
      "Fila operacional dos exames de EEG.", tem("eeg_operador") ? '<button type="button" data-eeg-agendar>Agendar EEG</button>' : ""
    );
    ligarAgenda(secao);
    return carregar(secao, todos("/eeg/exames"), renderExamCards);
  }

  function renderExamCards(dados) {
    var itens=dados.itens||[];
    if(!itens.length) return vazio("Nenhum EEG nesta fila.");
    return '<div class="eeg-cards">'+itens.map(function(e){
      var f=e.fluxo||{},file=e.arquivo_tecnico||{},doctor=f.medico||{};
      return '<article class="panel eeg-exam-card" data-eeg-record="'+esc(e.id)+'"><div class="eeg-record-heading"><span class="eyebrow">'+esc(e.public_code)+'</span>'+badgeStatus(e.status)+'</div><h3>'+esc((e.paciente||{}).nome_completo)+'</h3><p class="eeg-flow-label">'+esc(rotuloFluxo(e))+'</p><dl>'+[
        ['Modalidade',e.modalidade_nome||e.modalidade_codigo],['Data',dataBr(e.realizado_em||e.agendado_para)],['Local',e.local_nome||'Não informado'],['Origem',e.origem_canal||e.origem_tipo],['Responsável técnico',e.tecnico_responsavel||'Não atribuído'],['Arquivo técnico',file.privado?'Arquivo privado · '+(file.formato||''):(file.registrado?'Referência externa registrada':'Aguardando arquivo')],['Laudador',doctor.nome?doctor.nome+' · CRM '+doctor.crm+'/'+doctor.uf:'Não atribuído'],['Financeiro',f.reteste_sem_cobranca?'Correção técnica sem cobrança':e.situacao_financeira||'Sem receita vinculada']
      ].map(function(row){return '<div><dt>'+esc(row[0])+'</dt><dd>'+esc(row[1])+'</dd></div>';}).join('')+'</dl><div class="eeg-toolbar">'+botaoExame(e)+'</div></article>';
    }).join('')+'</div>';
  }

  function montarLaudos(secao) {
    secao.innerHTML = cabecalho(secao) + moldura("Laudos de EEG", "Fila médica própria · redação, assinatura e entrega.", '<button type="button" data-eeg-preparos>Preparos EEG</button>');
    secao.querySelector('[data-eeg-preparos]').onclick=preparos;
    return carregar(secao,todos('/eeg/laudos'),renderExamCards);
  }

  // ── Cabeçalho comum, no padrão do produto ───────────────────────────────

  function cabecalho(secao) {
    var titulo = secao.getAttribute("data-eeg-titulo") || "";
    var eyebrow = secao.getAttribute("data-eeg-eyebrow") || "";
    return '<div class="section-title"><div>' +
      '<p class="eyebrow">' + esc(eyebrow) + "</p>" +
      "<h2>" + esc(titulo) + "</h2></div>" +
      '<span class="safe-label">Operação EEGLife</span></div>';
  }

  /* Ponto de entrada chamado pela casca ao abrir um módulo da EEGLife.
   * Devolve true quando esta tela é viva (e assume o conteúdo). */
  function montar(moduloId, secao) {
    var fabrica = MODULOS_VIVOS[moduloId];
    if (!fabrica) return false;
    // Quase todo módulo vivo depende das rotas /eeg; Custos & Investimentos
    // não (ele lê um resumo local). Exigir `m15().api` dele deixaria a tela
    // vazia sem nenhuma razão — daí a dispensa ser declarada na função, e não
    // uma lista de exceções escrita aqui.
    if (fabrica.semNucleo !== true && (!m15() || typeof m15().api !== "function")) return false;
    secao.setAttribute("data-eeg-vivo", "montado");
    secao.setAttribute("data-eeg-modulo", moduloId);
    try {
      fabrica(secao);
    } catch (e) {
      console.error("EEG: falha ao montar módulo " + moduloId);
      return false;
    }
    return true;
  }


  function todos(path, pagina, itens) {
    pagina = pagina || 1; itens = itens || [];
    return m15().api(path + (path.indexOf("?") >= 0 ? "&" : "?") +
      "tamanho=100&pagina=" + pagina).then(function (d) {
      itens = itens.concat(d.itens || []);
      return itens.length < d.total && (d.itens || []).length
        ? todos(path, pagina + 1, itens) : { itens: itens, total: d.total };
    });
  }

  function post(path, body, method) {
    return m15().api(path, { method: method || "POST", body: JSON.stringify(body) });
  }

  function botaoExame(e) {
    exames[e.id] = e;
    return '<button type="button" data-eeg-exame="' + esc(e.id) + '">Abrir exame</button>';
  }

  function atualizar(secao) {
    var id = secao.getAttribute("data-eeg-modulo");
    (MODULOS_VIVOS[id] || montarExames)(secao);
  }

  function campo(nome, rotulo, tipo, obrigatorio) {
    return '<label>' + esc(rotulo) + '<input name="' + esc(nome) + '" type="' +
      (tipo || "text") + '"' + (obrigatorio ? " required" : "") + ' /></label>';
  }

  function select(nome, rotulo, opcoes) {
    return '<label>' + esc(rotulo) + '<select aria-label="' + esc(rotulo) + '" name="' + nome + '">' + opcoes.map(function (o) {
      return '<option value="' + esc(o[0]) + '">' + esc(o[1]) + '</option>';
    }).join("") + '</select></label>';
  }

  function formulario(titulo, html, salvar, rotulo) {
    var d = document.createElement("dialog");
    d.className = "eeg-dialog";
    d.innerHTML = '<form class="eeg-form"><h3>' + esc(titulo) + '</h3>' + html +
      '<p data-eeg-erro role="alert"></p><div class="eeg-toolbar">' +
      '<button type="button" data-eeg-fechar>Fechar</button><button type="submit">' +
      esc(rotulo || "Salvar") + '</button></div></form>';
    document.getElementById("lifePanelEegLife").appendChild(d);
    d.addEventListener("close", function () { d.remove(); });
    d.querySelector("[data-eeg-fechar]").onclick = function () { d.close(); };
    d.querySelector("form").onsubmit = function (ev) {
      ev.preventDefault();
      var f = ev.target, b = ev.submitter || f.querySelector('[type="submit"]');
      b.disabled = true;
      var dados = {};
      new FormData(f).forEach(function (v, k) { if (v !== "") dados[k] = v; });
      Promise.resolve().then(function () { return salvar(dados, d, f); }).then(function (fechar) {
        if (fechar !== false) d.close();
      }).catch(function (e) { d.querySelector("[data-eeg-erro]").textContent = mensagemErro(e); })
        .finally(function () { b.disabled = false; });
    };
    d.showModal();
    return d;
  }

  function cadastrar(secao) {
    var cadastro = formulario("Cadastrar ou vincular paciente EEG",
      '<p>Verifique a identidade corporativa antes de criar uma pessoa.</p>' +
      '<label>Pesquisar pessoa existente<input type="search" data-eeg-corporativo placeholder="Nome, CPF, telefone ou PES" /></label><button type="button" data-eeg-procurar>Procurar no cadastro compartilhado</button><div data-eeg-encontrados role="status"></div>' +
      campo("nome_completo", "Nome completo", "text", true) +
      campo("cpf", "CPF (opcional)") + campo("data_nascimento", "Nascimento", "date") +
      campo("telefone", "Telefone com DDI (opcional)", "tel") +
      campo("email", "E-mail (opcional)", "email") +
      select("sexo", "Sexo", [["", "Não informado"], ["feminino", "Feminino"], ["masculino", "Masculino"], ["outro", "Outro"]]) +
      select("consentimento_whatsapp", "Contato operacional por WhatsApp · somente EEGLife", [["desconhecido", "Não informado"], ["concedido", "Autorizado pelo paciente"], ["revogado", "Não autorizado / revogado"]]) +
      '<div data-eeg-candidatos></div>',
      function (dados, d) {
        return post("/eeg/pessoas/verificar", dados).then(function (r) {
          var area = d.querySelector("[data-eeg-candidatos]");
          area.innerHTML = '<p>' + (r.itens.length ?
            'Confira as pessoas encontradas e escolha a identidade correta:' :
            'Nenhuma correspondência encontrada. Confira os dados antes de criar.') + '</p>';
          r.itens.forEach(function (p) {
            var b = document.createElement("button"); b.type = "button";
            b.textContent = "Vincular " + p.public_code + " · " + p.nome_completo;
            b.onclick = function () {
              b.disabled = true;
              post("/eeg/pessoas/vincular", { person_id: p.person_id }).then(function () {
                d.close(); atualizar(secao);
              }).catch(function (e) { b.disabled = false; d.querySelector("[data-eeg-erro]").textContent = mensagemErro(e); });
            };
            area.appendChild(b);
          });
          var criar = document.createElement("button"); criar.type = "button";
          criar.textContent = r.itens.length ? "Confirmei: é outra pessoa. Criar novo cadastro" : "Confirmar novo paciente";
          criar.onclick = function () {
            criar.disabled = true;
            // Freeze the reviewed values; a subsequent edit requires another verification.
            post("/eeg/pessoas", Object.assign({}, dados, { confirmar_nova_pessoa: !!r.itens.length }))
              .then(function () { d.close(); atualizar(secao); })
              .catch(function (e) { criar.disabled = false; d.querySelector("[data-eeg-erro]").textContent = mensagemErro(e); });
          };
          area.appendChild(criar);
          d.querySelectorAll("input, select").forEach(function (i) { i.oninput = function () { area.innerHTML = ""; }; });
          return false;
        });
      }, "Verificar identidade");
    cadastro.querySelector("[data-eeg-procurar]").onclick = function () {
      var area = cadastro.querySelector("[data-eeg-encontrados]");
      area.textContent = "Buscando…";
      buscarPessoas(cadastro.querySelector("[data-eeg-corporativo]").value, true).then(function (r) {
        area.textContent = r.itens.length ? "Selecione a pessoa após conferir a identidade." : "Nenhuma pessoa encontrada.";
        r.itens.forEach(function (v) {
          var b = document.createElement("button"); b.type = "button";
          b.textContent = (v.ja_vinculada ? "Abrir vínculo: " : "Vincular: ") + v.pessoa.public_code + " · " + v.pessoa.nome_completo + " · " + (v.pessoa.cpf_mascarado || "CPF não informado");
          b.onclick = function () {
            b.disabled = true;
            post("/eeg/pessoas/vincular", { person_id: v.pessoa.id }).then(function () {
              cadastro.close(); atualizar(secao);
            }).catch(function (e) { b.disabled = false; area.textContent = mensagemErro(e); });
          };
          area.appendChild(b);
        });
      }).catch(function (e) { area.textContent = mensagemErro(e); });
    };
  }

  function buscarPessoas(q, corporativo, pagina, itens) {
    pagina = pagina || 1; itens = itens || [];
    return post("/eeg/pessoas/busca", { q: q || null, corporativo: corporativo,
      pagina: pagina, tamanho: 100 }).then(function (r) {
      itens = itens.concat(r.itens || []);
      return itens.length < r.total && r.itens.length ? buscarPessoas(q, corporativo, pagina + 1, itens) : { itens: itens, total: r.total };
    });
  }

  function ligarAgenda(secao) {
    var b=secao.querySelector("[data-eeg-agendar]");
    if(b)b.onclick = function () { novoAtendimento(secao); };
  }

  function novoAtendimento(secao, enrollmentId, leadId) {
    atendimentoPrefill = {enrollmentId:enrollmentId, leadId:leadId};
    if(secao.dataset.eegModulo==='cadastros')montarCentral(secao);
    else document.querySelector('button[data-eeg-modulo="cadastros"]').click();
  }

  function montarFinanceiro(secao) {
    secao.innerHTML = cabecalho(secao) + moldura("Financeiro EEGLife", "Receitas no razão compartilhado, identificadas pela marca de origem.", "");
    return carregar(secao, todos("/eeg/financeiro"), function (dados) {
      if (!dados.itens.length) return vazio("Nenhuma receita de EEG registrada.");
      return '<div class="eeg-cards">'+dados.itens.map(function(e){return '<article class="panel eeg-exam-card"><p class="eyebrow">'+esc(e.public_code)+' · '+esc(e.exame)+'</p><h3>'+brl(e.valor)+'</h3><p>'+esc(e.status)+' · '+esc(e.forma_pagamento||'Forma não informada')+'</p><dl>'+[['Recebido em',e.data_recebimento?diaBr(e.data_recebimento):'Não recebido'],['Origem',e.origem||'Não informada'],['Responsável',e.responsavel||'Não atribuído'],['Parceiro',e.parceiro||'Direto EEGLife']].map(function(row){return '<div><dt>'+esc(row[0])+'</dt><dd>'+esc(row[1])+'</dd></div>';}).join('')+'</dl><div class="eeg-toolbar"><button data-eeg-pagamento="'+esc(e.id)+'">Pagamento</button><button data-eeg-preparar="'+esc(e.eeg_exam_id)+'">Conferir NFS-e EEG</button></div></article>';}).join('')+'</div>';

    });
  }

  function operar(secao, e) {
    if (!e) return;
    var d = document.createElement("dialog"); d.className = "eeg-dialog";
    d.innerHTML = '<h3>' + esc(e.public_code) + ' · ' + esc((e.paciente || {}).nome_completo) + '</h3>' +
      '<p>' + esc(e.modalidade_nome) + ' · ' + badgeStatus(e.status) + '</p>' +
      '<p>Local: ' + esc(e.local_nome) + ' · Agenda: ' + esc(dataBr(e.agendado_para)) + '</p>' +
      '<p>Fluxo: ' + esc(rotuloFluxo(e)) + '</p>' +
      '<p>Referência técnica: ' + esc((e.arquivo_tecnico || {}).referencia_externa || "Não registrada") + '</p>' +
      '<p>Envio: ' + esc(e.envio_laudo ? e.envio_laudo.destino_nome + " · " + (e.envio_laudo.protocolo_externo || "sem protocolo") + " · " + dataBr(e.envio_laudo.enviado_em) : "Não registrado") + '</p>' +
      '<div data-eeg-acoes class="eeg-toolbar"></div><p data-eeg-erro role="alert"></p><button type="button" data-eeg-fechar>Fechar</button>';
    document.getElementById("lifePanelEegLife").appendChild(d); d.addEventListener("close", function () { d.remove(); });
    d.querySelector("[data-eeg-fechar]").onclick = function () { d.close(); };
    function acao(nome, fn) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = nome;
      b.onclick = function () { b.disabled = true; Promise.resolve().then(fn).catch(function (err) {
        d.querySelector("[data-eeg-erro]").textContent = mensagemErro(err);
      }).finally(function () { b.disabled = false; }); };
      d.querySelector("[data-eeg-acoes]").appendChild(b);
    }
    function salvo() { d.close(); atualizar(secao); }
    function transicao(status, comData) {
      formulario(ROTULO_STATUS[status], comData ? campo("ocorrido_em", "Data e hora", "datetime-local", true) : '<p>Confirmar esta etapa do exame?</p>',
        function (dados) {
          dados.status = status;
          if (dados.ocorrido_em) dados.ocorrido_em = new Date(dados.ocorrido_em).toISOString();
          return post("/eeg/exames/" + e.id + "/status", dados, "PATCH").then(salvo);
        }, "Confirmar");
    }
    eegWorkspaceActions(secao,e,acao,salvo);
    if (tem("eeg_operador", "eeg_tecnico") && ["cadastro", "agendado", "confirmado"].includes(e.status)) {
      if(tem("eeg_operador")) acao("Agendar / reagendar", function () { transicao("agendado", true); });
      if (tem("eeg_operador") && e.status === "agendado") acao("Confirmar agendamento", function () { transicao("confirmado"); });
      if (e.status !== "cadastro") acao("Registrar realização", function () { transicao("realizado", true); });
    }
    if (e.laudo) acao("Baixar laudo PDF", function () {
      return m15().apiBlob("/eeg/laudos/" + e.laudo.id + "/arquivo").then(function (blob) {
        var a = document.createElement("a"), url = URL.createObjectURL(blob);
        a.href = url; a.download = blob.nomeSugerido || (e.laudo.public_code + ".pdf"); a.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      });
    });
    if (tem("eeg_operador") && e.status === "laudo_disponivel" && e.laudo) {
      function link(renovar) {
        return post("/eeg/laudos/" + e.laudo.id + "/acesso-resultado" + (renovar ? "?renovar=true" : ""), {}).then(function (r) {
          var info = d.querySelector("[data-eeg-erro]"); info.textContent = "Acesso do paciente (exige nascimento): ";
          var a = document.createElement("a"); a.href = r.url; a.textContent = "Abrir PDF no portal"; a.target = "_blank"; a.rel = "noopener noreferrer"; info.appendChild(a);
          var input = document.createElement("input"); input.readOnly = true; input.value = r.url; input.setAttribute("aria-label", "Link do resultado"); info.appendChild(input);
        });
      }
      acao("Gerar / exibir acesso do paciente", function () { return link(false); });
      acao("Renovar link do paciente", function () { return link(true); });
      acao("Revogar acesso do paciente", function () { return post("/eeg/laudos/" + e.laudo.id + "/acesso-resultado/revogar", {}).then(function () { d.querySelector("[data-eeg-erro]").textContent = "Acesso revogado."; }); });
    }
    if(tem("eeg_financeiro")) acao("Conferir NFS-e EEG", function () { return prepararFiscal(secao, e.id); });
    if (tem("eeg_operador") && !["cancelado", "laudo_disponivel"].includes(e.status)) acao("Cancelar exame", function () { transicao("cancelado"); });
    d.showModal();
  }

  // Fiscal: interface da marca, mesmo documento, confirmação e worker.
  var documentos = {};
  var FISCAL_STATUS = { blocked: "Bloqueada", pending: "Pronta para conferir", issuing: "Processando",
    issued: "Emitida", simulated: "Simulada · sem validade fiscal", failed: "Rejeitada", uncertain: "Resultado incerto · reconciliar",
    reconciling: "Consultando provedor", cancelled: "Cancelada" };

  function montarFiscal(secao) {
    secao.innerHTML = cabecalho(secao) + moldura("Fiscal EEGLife",
      "Prestador legal: SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA · CNPJ 63.544.026/0001-10.",
      '<button type="button" data-eeg-fiscal-atualizar>Atualizar</button>');
    secao.querySelector("[data-eeg-fiscal-atualizar]").onclick = function () { montarFiscal(secao); };
    return carregar(secao, todos("/eeg/fiscal"), function (dados) {
      return '<p class="eeg-nota">Configuração fiscal EEG pendente de aprovação. A emissão depende de confirmação humana e usa o emissor corporativo SoproLife.</p>' +
        (dados.itens.length ? tabela(["Atendimento", "Modalidade executada", "Valor fiscal", "Situação", "Pendência", "Ações"], dados.itens.map(function (doc) {
          documentos[doc.id] = doc;
          var c = doc.confirmacao || {};
          return "<tr><td>" + esc(c.exam_code) + "</td><td>" + esc(c.service_description) + "</td><td>" + brl(c.amount) + "</td><td>" + esc(doc.pedido ? "Pedido: " + doc.pedido.status : (FISCAL_STATUS[doc.state] || doc.state)) + "</td><td>" + esc(c.block_label || (doc.blocking_reasons || []).join(", ") || "—") +
            '</td><td><button type="button" data-eeg-documento="' + esc(doc.id) + '">Consultar NFS-e EEG / arquivos</button></td></tr>';
        }).join("")) : vazio("Nenhum documento EEG preparado. No atendimento ou financeiro, use Conferir NFS-e EEG."));
    });
  }

  function prepararFiscal(secao, examId) {
    return post("/eeg/fiscal/exames/" + examId + "/preparar", {}).then(function (doc) {
      abrirFiscal(secao, doc);
    }).catch(function (err) {
      formulario("NFS-e indisponível", '<p role="alert">' + esc(mensagemErro(err)) + '</p>', function () {}, "Fechar");
    });
  }

  function abrirFiscal(secao, doc) {
    if (!doc) return;
    var c = doc.confirmacao || {}, key = "eeg-fiscal-" + crypto.randomUUID();
    var d = formulario("Conferir NFS-e EEGLife",
      '<p>Prestador legal: SOPRO LIFE DIAGNÓSTICO E SOLUÇÕES EM SAÚDE LTDA · CNPJ 63.544.026/0001-10</p>' +
      '<dl class="fiscal-readiness-grid"><div><dt>Atendimento</dt><dd>' + esc(c.exam_code) + '</dd></div><div><dt>Tomador</dt><dd>' + esc(c.patient_name) + ' · ' + esc(c.cpf_masked) + '</dd></div><div><dt>Valor</dt><dd>' + brl(c.amount) + '</dd></div><div><dt>Município</dt><dd>' + esc(c.municipio_name || c.municipio_ibge) + '</dd></div><div><dt>Competência</dt><dd>' + esc(c.competence ? c.competence.split("-").reverse().join("/") : "—") + '</dd></div></dl>' +
      '<p><strong>Descrição:</strong> ' + esc(c.service_description) + '</p><p><strong>Situação:</strong> ' + esc(FISCAL_STATUS[doc.state] || doc.state) + '</p>' +
      '<p role="status">' + esc(c.block_label || (doc.blocking_reasons || []).join(", ") || "Confira os dados antes de emitir.") + '</p>' +
      '<div data-eeg-arquivos></div><div data-eeg-emissao></div>', function () {}, "Fechar");
    d.querySelector('[type="submit"]').hidden = true;
    var area = d.querySelector("[data-eeg-emissao]");
    if (doc.pedido && !c.can_confirm) acompanhar(doc.pedido.id, 0);
    if (c.can_confirm) {
      var emitir = document.createElement("button"); emitir.type = "button"; emitir.textContent = "Emitir NFS-e EEG";
      emitir.onclick = function () {
        emitir.remove();
        var aviso = document.createElement("p"); aviso.textContent = "Esta ação enviará uma NFS-e real ao provedor fiscal e poderá gerar obrigação fiscal.";
        area.appendChild(aviso);
        var confirmar = document.createElement("button"); confirmar.type = "button"; confirmar.textContent = c.confirmation_phrase;
        confirmar.onclick = function () {
          confirmar.disabled = true;
          post("/fiscal/producao/documentos/" + doc.id + "/confirmar-emissao", {
            preparation_id: c.preparation_id, amount: c.amount, confirmation: c.confirmation_phrase, idempotency_key: key
          }).then(function (pedido) {
            confirmar.remove(); acompanhar(pedido.id, 0);
          }).catch(function (err) { confirmar.disabled = false; d.querySelector("[data-eeg-erro]").textContent = mensagemErro(err); });
        };
        area.appendChild(confirmar); confirmar.focus();
      };
      area.appendChild(emitir);
    }
    function acompanhar(id, n) {
      if (!d.isConnected) return;
      m15().api("/fiscal/producao/pedidos/" + id).then(function (pedido) {
        if (!d.isConnected) return;
        area.textContent = "Pedido fiscal: " + pedido.status + (pedido.result_code ? " · " + pedido.result_code : "");
        if (["authorized", "running"].includes(pedido.status) && n < 120) setTimeout(function () { acompanhar(id, n + 1); }, 3000);
        else atualizar(secao);
      }).catch(function (err) { d.querySelector("[data-eeg-erro]").textContent = mensagemErro(err); });
    }
    if (["uncertain", "issuing", "reconciling"].includes(doc.state)) {
      var consultar = document.createElement("button"); consultar.type = "button"; consultar.textContent = "Solicitar consulta de status (sem nova emissão)";
      consultar.onclick = function () {
        consultar.disabled = true;
        post("/fiscal/producao/documentos/" + doc.id + "/solicitar-reconciliacao", { idempotency_key: key }).then(function (pedido) {
          consultar.remove(); acompanhar(pedido.id, 0);
        }).catch(function (err) { consultar.disabled = false; d.querySelector("[data-eeg-erro]").textContent = mensagemErro(err); });
      };
      area.appendChild(consultar);
    }
    m15().api("/fiscal/documentos/" + doc.id + "/arquivos").then(function (r) {
      var files = d.querySelector("[data-eeg-arquivos]");
      if (!r.itens.length) files.textContent = "PDF/XML ainda não disponíveis no acervo fiscal.";
      r.itens.forEach(function (a) {
        var b = document.createElement("button"); b.type = "button"; b.textContent = a.kind === "danfse_pdf" ? "Baixar PDF fiscal" : "Baixar XML da NFS-e";
        b.onclick = function () {
          b.disabled = true;
          m15().apiBlob("/fiscal/documentos/" + doc.id + "/arquivos/" + a.id + "/conteudo", { aceitaBlob: [a.kind === "danfse_pdf" ? "application/pdf" : "application/xml"] }).then(function (blob) {
            var url = URL.createObjectURL(blob), link = document.createElement("a");
            link.href = url; link.download = blob.nomeSugerido || ("nfse-" + doc.id + (a.kind === "danfse_pdf" ? ".pdf" : ".xml")); link.click();
            setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
          }).catch(function (err) { d.querySelector("[data-eeg-erro]").textContent = mensagemErro(err); }).finally(function () { b.disabled = false; });
        };
        files.appendChild(b);
      });
    }).catch(function (err) { d.querySelector("[data-eeg-arquivos]").textContent = mensagemErro(err); });
  }

  // ── Custos & Investimentos (escopo EEGLife) ─────────────────────────────
  //
  // POR QUE ESTA TELA NÃO FALA COM O NÚCLEO
  // Custos & Investimentos não está no PostgreSQL: a fonte é o livro-caixa
  // privado dos sócios, e o painel lê o resumo DERIVADO dele por
  // scripts/generate-custos-investimentos.py (M26.21).
  //
  // POR QUE CUSTO DA SOPROLIFE NÃO PODE APARECER AQUI
  // Desde a M26.37 o gerador escreve UM RESUMO POR MARCA, e o que esta tela
  // busca é o resumo da EEGLife — um arquivo que só tem item da EEGLife, não
  // uma visão filtrada de um arquivo com as duas marcas. O custo da SoproLife
  // não é escondido aqui: ele não está no arquivo que esta tela pede. É a
  // mesma ideia do `init()` da casca, onde o dado respiratório não é buscado
  // em vez de ser buscado e ocultado.
  //
  // E o escopo ainda é CONFERIDO antes de desenhar: resumo de outra marca, ou
  // sem marca declarada, é recusado em vez de exibido com rótulo de EEG.
  // Fail-closed, como o resto do painel.

  var RESUMO_CUSTOS_EEG = "./data/custos-investimentos-eeglife.local.json";

  /* Moeda montada à mão de propósito: `Intl.NumberFormat('pt-BR')` insere
   * U+00A0 depois de "R$", e isso já quebrou comparação de texto em teste. */
  function brl(v) {
    if (v === null || v === undefined) return "—";
    return "R$ " + Number(v).toLocaleString("pt-BR",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function lerResumoCustosEeg() {
    return fetch(RESUMO_CUSTOS_EEG, { cache: "no-store" }).then(function (r) {
      if (!r.ok) {
        throw { codigo: "http_" + r.status,
                mensagem: "Resumo de custos da EEGLife não encontrado." };
      }
      return r.json();
    }).then(function (d) {
      if (!d || !d.source || d.source.safeToDisplay !== true) {
        throw { mensagem: "O resumo de custos não está marcado como seguro para exibição." };
      }
      if (d.escopo_marca !== "eeglife") {
        throw { mensagem: "Resumo recusado: ele não é o resumo da marca EEGLife." };
      }
      return d;
    });
  }

  function statCard(label, valor, hint) {
    return '<article class="stat-card"><span>' + esc(label) + "</span>" +
      "<strong>" + esc(valor) + "</strong>" +
      "<small>" + esc(hint) + "</small></article>";
  }

  function renderRateioCustos(d) {
    var socios = d.rateio_socios || [];
    if (!socios.length) return "";
    return '<div class="eeg-stats">' + socios.map(function (s) {
      // Desembolsado e saldo em aberto são coisas diferentes e aparecem
      // separados: o compromisso de um sócio existir não significa que ele
      // já tirou o dinheiro do bolso.
      return statCard(s.nome,
        brl(s.saldo_pendente_atribuido),
        "em aberto · " + brl(s.compromisso_mensal) + "/mês · desembolsado " +
        brl(s.total_desembolsado));
    }).join("") + "</div>";
  }

  function renderPlanosCustos(d) {
    var parcs = d.parcelamentos || [];
    if (!parcs.length) return "";
    return parcs.map(function (p) {
      var planos = p.planos_por_pagador || [];
      var linhas = planos.map(function (pl) {
        return "<tr><td>" + esc(pl.pagador) + "</td><td>" +
          esc(String(pl.parcelas) + "x") + "</td><td>" + brl(pl.valor_parcela) +
          "</td><td>" + brl(pl.total) + "</td><td>" +
          esc(pl.parcelas_pagas + "/" + pl.parcelas) + "</td></tr>";
      }).join("");
      if (!linhas) return "";
      return '<article class="panel"><div class="panel-header"><h3>' +
        esc(p.item) + "</h3><span>" + esc(p.forma_pagamento) +
        " · parcela combinada " + brl(p.valor_parcela) + " · " +
        esc(p.parcela_atual) + " pagas</span></div>" +
        tabela(["Pagador", "Parcelas", "Valor da parcela", "Total do pagador",
                "Progresso"], linhas) + "</article>";
    }).join("");
  }

  function renderCustosEeg(d) {
    var itens = d.itens || [];
    if (!itens.length) {
      return vazio("Nenhum custo ou investimento da EEGLife cadastrado.");
    }
    var linhas = itens.map(function (it) {
      var parc = it.parcelas_total == null
        ? "—" : it.parcelas_pagas + "/" + it.parcelas_total;
      return "<tr><td><strong>" + esc(it.nome) + "</strong>" +
        (it.obs_curta ? "<br><small>" + esc(it.obs_curta) + "</small>" : "") +
        "</td><td>" + esc(it.categoria) + "</td><td>" + esc(it.responsavel) +
        "</td><td>" + brl(it.valor_total) + "</td><td>" +
        (it.valor_mensal == null ? "—" : brl(it.valor_mensal)) +
        "</td><td>" + esc(parc) + "</td><td>" + brl(it.saldo_restante) +
        "</td></tr>";
    }).join("");
    return '<div class="eeg-stats">' +
      statCard("Investido em equipamentos", brl(d.total_investido_equipamentos),
               "ativo da EEGLife, contado uma vez") +
      statCard("Parcelas mensais", brl(d.parcelas_mensais_equipamentos),
               "soma das parcelas em aberto") +
      statCard("Desembolsado confirmado", brl(d.total_desembolsado_confirmado),
               "saída de caixa já confirmada") +
      statCard("Itens cadastrados", String(d.itens_ativos),
               "no escopo da marca EEGLife") +
      "</div>" +
      tabela(["Item", "Categoria", "Responsável", "Total", "Parcela / mês",
              "Parcelas", "Saldo"], linhas) +
      renderRateioCustos(d) +
      renderPlanosCustos(d) +
      (d.pendencias && d.pendencias.length
        ? '<p class="eeg-nota">' + esc(d.pendencias.map(function (pd) {
            return pd.item + ": " + pd.descricao;
          }).join(" · ")) + "</p>"
        : "");
  }

  function montarCustos(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Custos & Investimentos",
      "Custo e investimento da marca EEGLife, com o rateio entre os sócios.",
      ""
    );
    return carregar(secao, lerResumoCustosEeg(), renderCustosEeg);
  }
  /* Não depende das rotas /eeg — ver `montar`. */
  montarCustos.semNucleo = true;

  function papeis() { return ((window.SoproBootGate || {}).identidade || {}).papeis_efetivos || []; }
  function tem() { var r=papeis(); return r.includes("admin") || Array.from(arguments).some(function(p){return r.includes(p);}); }
  function medico() { return papeis().includes("medical_eeg"); }
  var FLUXO = { aguardando_arquivo:"Aguardando traçado", aguardando_conferencia:"Aguardando conferência técnica", pronto_laudo:"Pronto para laudo", atribuido:"Aguardando laudo", em_elaboracao:"Laudo em elaboração", aguardando_assinatura:"Aguardando assinatura", recebido_assinado:"Assinatura recebida", pronto_entrega:"Pronto para entrega", entregue:"Entregue" };
  function rotuloFluxo(e) { if(["cadastro","agendado","confirmado"].includes(e.status))return "Aguardando realização";if(e.status==="cancelado")return "Cancelado";return FLUXO[(e.fluxo||{}).status] || "Aguardando traçado"; }
  function textArea(name,label,required) { return '<label>'+esc(label)+'<textarea name="'+esc(name)+'" rows="5" '+(required?'required':'')+'></textarea></label>'; }
  function baixar(path, types) { return m15().apiBlob(path,{aceitaBlob:types||["application/pdf","application/zip","application/octet-stream"]}).then(function(b){var a=document.createElement("a"),u=URL.createObjectURL(b);a.href=u;a.download=b.nomeSugerido||"arquivo-eeg";a.click();setTimeout(function(){URL.revokeObjectURL(u);},1000);}); }
  function mini(t,v) { return '<div class="stat-card"><span>'+esc(t)+'</span><strong>'+esc(v)+'</strong></div>'; }
  function prepararTabela(secao) {
    secao.querySelectorAll("table").forEach(function(t){var labels=Array.from(t.querySelectorAll("thead th")).map(function(x){return x.textContent;});t.querySelectorAll("tbody tr").forEach(function(row){Array.from(row.children).forEach(function(td,i){td.setAttribute("data-label",labels[i]||"");});});});
  }
  function painel(secao,titulo,resumo,acoes,load,render) { secao.innerHTML=cabecalho(secao)+moldura(titulo,resumo,acoes||""); return carregar(secao,load,render); }
  function montarVisao(secao) {
    return painel(secao,"Operação EEGLife","Fonte PostgreSQL · contagens atuais; receita por competência no mês, em Brasília.",'<button type="button" data-eeg-novo-atendimento>Novo Atendimento EEG</button>',m15().api("/eeg/indicadores"),function(d){
      return '<div class="eeg-stats">'+mini("EEGs agendados",d.agendados)+mini("Realizados",d.realizados)+mini("Aguardando traçado",d.aguardando_tracado)+mini("Aguardando laudo",d.aguardando_laudo)+mini("Aguardando assinatura",d.aguardando_assinatura)+mini("Prontos para entrega",d.prontos_entrega)+mini("Receita na competência",brl(d.receita_competencia))+mini("Tarefas pendentes",d.tarefas_pendentes)+'</div><p>Receita de '+esc(diaBr(d.periodo_inicio))+' a '+esc(diaBr(d.periodo_fim))+'. Não inclui custos ou repasses ainda não configurados.</p>';
    }).then(function(){var b=secao.querySelector('[data-eeg-novo-atendimento]');if(b)b.onclick=function(){novoAtendimento(secao);};});
  }
  function editarCadastro(secao,v) {
    var p=v.pessoa||{}, d=formulario("Editar cadastro compartilhado · "+p.public_code,
      '<p>A identidade é corporativa e a correção cadastral também aparece na SoproLife. Consentimento abaixo é específico EEG.</p>'+campo("nome_completo","Nome completo","text",true)+campo("data_nascimento","Nascimento","date")+campo("telefone","Telefone com DDI","tel")+campo("email","E-mail","email")+select("sexo","Sexo",[["","Não informado"],["feminino","Feminino"],["masculino","Masculino"],["outro","Outro"]])+select("consentimento_whatsapp","WhatsApp operacional EEG",[["desconhecido","Não informado"],["concedido","Autorizado"],["revogado","Revogado"]]),
      function(data){["data_nascimento","sexo","telefone","email"].forEach(function(k){if(!data[k])data[k]=null;});return post("/eeg/pessoas/"+v.id+"/cadastro",data,"PATCH").then(function(){atualizar(secao);});});
    ["nome_completo","data_nascimento","sexo"].forEach(function(k){d.querySelector('[name="'+k+'"]').value=p[k]||"";});
    var contacts=p.contatos||[];["telefone","email"].forEach(function(k){var c=contacts.find(function(x){return x.ativo!==false && (k==="telefone"?["telefone","whatsapp"].includes(x.tipo):x.tipo==="email");});d.querySelector('[name="'+k+'"]').value=c?c.valor:"";});d.querySelector('[name="consentimento_whatsapp"]').value=v.consentimento_whatsapp||"desconhecido";
  }
  function historico(secao,eid) { return m15().api('/eeg/pessoas/'+eid+'/historico').then(function(d){var html=d.exames.map(function(e){return '<article class="panel"><h4>'+esc(e.public_code)+' · '+esc(e.modalidade_nome)+'</h4><p>'+esc(dataBr(e.agendado_para))+' · '+esc(ROTULO_STATUS[e.status])+' · '+esc(rotuloFluxo(e))+'</p>'+botaoExame(e)+'</article>';}).join('')||vazio('Nenhum EEG registrado.');html+='<h4>Trilha operacional</h4>'+d.eventos.map(function(a){return '<p>'+esc(dataBr(a.quando))+' · '+esc(a.acao)+'</p>';}).join('');var modal=formulario('Histórico EEG',html,function(){},'Fechar histórico');modal.querySelectorAll('[data-eeg-exame]').forEach(function(b){b.onclick=function(){operar(secao,exames[b.dataset.eegExame]);};});}); }
  function buscarCrm(q,situacao,pagina,itens) { pagina=pagina||1;itens=itens||[];return post('/eeg/crm/busca',{q:q||null,situacao:situacao||null,pagina:pagina,tamanho:100}).then(function(r){itens=itens.concat(r.itens||[]);return itens.length<r.total&&r.itens.length?buscarCrm(q,situacao,pagina+1,itens):{itens:itens,total:r.total};});}
  function montarCrm(secao) {
    secao.innerHTML=cabecalho(secao)+moldura("Relacionamento EEG","Próximo contato manual. Nenhuma regra respiratória de seis meses é aplicada.",'<input type="search" data-crm-search aria-label="Buscar no CRM EEG" placeholder="Buscar paciente por nome" />'+select('crm_filter','Situação CRM',[["","Todas"],["ativo","Ativo"],["aguardando_contato","Aguardando contato"],["acompanhamento","Acompanhamento"],["encerrado","Encerrado"]]));
    var input=secao.querySelector('[data-crm-search]'),filter=secao.querySelector('[name="crm_filter"]');
    function load(){return carregar(secao,buscarCrm(input.value,filter.value),function(d){if(!d.itens.length)return vazio('Nenhum paciente para este filtro.');return '<div class="eeg-cards">'+d.itens.map(function(v){crmRows[v.id]=v;return '<article class="panel eeg-patient-card"><h4>'+esc(v.paciente)+'</h4><p>'+esc(v.codigo)+' · '+esc(v.contato||'Sem telefone')+'</p><p>Último EEG: '+esc(v.ultimo_eeg?v.ultimo_eeg.codigo+' · '+(ROTULO_STATUS[v.ultimo_eeg.status]||v.ultimo_eeg.status):'Nenhum')+'</p><p>Próximo contato: '+esc(v.proximo_contato?diaBr(v.proximo_contato):'Não definido')+' · '+esc(v.situacao)+'</p><p>Origem: '+esc(v.origem||'Não informada')+'</p><div class="eeg-toolbar">'+(v.whatsapp_url?'<a target="_blank" rel="noopener noreferrer" href="'+esc(v.whatsapp_url)+'">WhatsApp · revisar</a>':'<span>WhatsApp indisponível</span>')+'<button data-crm-history="'+esc(v.id)+'">Histórico</button><button data-eeg-atender="'+esc(v.id)+'">Novo EEG</button><button data-crm-schedule="'+esc(v.id)+'">Agendar / Reagendar</button><button data-crm-contact="'+esc(v.id)+'">Próximo contato</button></div></article>';}).join('')+'</div>';}).then(function(){secao.querySelectorAll('[data-crm-schedule]').forEach(function(b){b.onclick=function(){var v=crmRows[b.dataset.crmSchedule];return todos('/eeg/exames').then(function(r){var e=r.itens.find(function(x){return x.enrollment_id===v.id&&['agendado','confirmado'].includes(x.status);});if(e)operar(secao,e);else novoAtendimento(secao,v.id);});};});secao.querySelectorAll('[data-crm-history]').forEach(function(b){b.onclick=function(){historico(secao,b.dataset.crmHistory);};});secao.querySelectorAll('[data-crm-contact]').forEach(function(b){b.onclick=function(){var v=crmRows[b.dataset.crmContact];var f=formulario('Próximo contato EEG',campo('proximo_contato','Data do próximo contato','date')+select('situacao','Situação',[["ativo","Ativo"],["aguardando_contato","Aguardando contato"],["acompanhamento","Acompanhamento"],["encerrado","Encerrado"]]),function(d){d.proximo_contato=d.proximo_contato||null;return post('/eeg/crm/'+v.id,d,'PATCH').then(load);});f.querySelector('[name="proximo_contato"]').value=v.proximo_contato||'';f.querySelector('[name="situacao"]').value=v.situacao;};});});}
    var timer;input.oninput=function(){clearTimeout(timer);timer=setTimeout(load,300);};filter.onchange=load;return load();
  }
  var crmRows={},activityRows={};
  var leadStates=['lead','interessado','aguardando_contato','solicitado','pre_agendado','confirmado','realizado','faltou','cancelado','reagendado'];
  var taskCategories=['confirmar_agenda','enviar_preparo','cobrar_tracado','atribuir_laudador','acompanhar_assinatura','liberar_resultado','cobranca','retorno_comercial'];
  function human(v){return String(v||'').replace(/_/g,' ');}
  function activitiesForm(secao,tipo,a) {
    return Promise.all([todos('/eeg/pessoas'),m15().api('/eeg/equipe')]).then(function(r){
      var d=formulario((a?'Editar ':'Nova ')+(tipo==='lead'?'oportunidade EEG':'tarefa EEG'),campo('titulo',tipo==='lead'?'Nome do contato / oportunidade':'Título operacional (sem conteúdo clínico)','text',true)+
        (tipo==='lead'?campo('contato','Contato (opcional)')+select('origem','Origem',[['direto','Direto'],['site','Site'],['whatsapp','WhatsApp'],['indicacao','Indicação'],['parceiro','Parceiro'],['outro','Outro']])+select('consentimento','Contato operacional autorizado',[['desconhecido','Não informado'],['concedido','Autorizado'],['revogado','Revogado']]):select('categoria','Tipo de tarefa',taskCategories.map(function(v){return [v,human(v)];})))+
        select('status','Situação',(tipo==='lead'?leadStates:['pendente','em_andamento','concluida','cancelada']).map(function(v){return [v,human(v)];}))+
        select('enrollment_id','Paciente vinculado',[['','Não vinculado']].concat(r[0].itens.map(function(v){return [v.id,v.pessoa.public_code+' · '+v.pessoa.nome_completo];})))+
        select('responsavel_id','Responsável',[['','Não atribuído']].concat(r[1].itens.map(function(u){return [u.id,u.nome+' · '+u.papeis.join(', ')];})))+campo('prazo','Prazo / agendamento solicitado','datetime-local'),
        function(data){data.tipo=tipo;if(tipo==='lead')data.categoria='oportunidade';if(data.prazo)data.prazo=new Date(data.prazo).toISOString();if(a&&a.exam_id)data.exam_id=a.exam_id;return post('/eeg/atividades'+(a?'/'+a.id:''),data,a?'PATCH':'POST').then(function(){atualizar(secao);});});
      if(a)Object.keys(a).forEach(function(k){var el=d.querySelector('[name="'+k+'"]');if(el)el.value=k==='prazo'&&a[k]?localTime(a[k]):a[k]||'';});
    });
  }
  function localTime(iso){var d=new Date(iso);d=new Date(d.getTime()-d.getTimezoneOffset()*60000);return d.toISOString().slice(0,16);}
  function montarAtividades(secao,tipo) {
    return painel(secao,tipo==='lead'?'Leads e Agendamentos':'Tarefas EEG',tipo==='lead'?'Converta a oportunidade em pessoa e atendimento sem duplicar a identidade.':'Rotina operacional com responsáveis explícitos.', '<button data-activity-new>Adicionar '+(tipo==='lead'?'lead':'tarefa')+'</button>'+(tipo==='lead'?'<button data-eeg-agendar>Novo Atendimento EEG</button>':'<button data-eeg-preparos>Preparos por modalidade</button>'),todos('/eeg/atividades?tipo='+tipo),function(d){if(!d.itens.length)return vazio(tipo==='lead'?'Nenhum lead EEG cadastrado.':'Nenhuma tarefa EEG cadastrada.');return tabela(['Registro','Categoria / origem','Prazo','Situação','Ações'],d.itens.map(function(a){activityRows[a.id]=a;return '<tr><td>'+esc(a.titulo)+'</td><td>'+esc(human(tipo==='lead'?a.origem:a.categoria))+'</td><td>'+esc(dataBr(a.prazo))+'</td><td>'+esc(human(a.status))+'</td><td>'+'<button data-activity-edit="'+esc(a.id)+'">Editar</button>'+(tipo==='lead'?'<button data-activity-convert="'+esc(a.id)+'">'+(a.exam_id?'Atendimento':'Converter / agendar')+'</button>':'')+'</td></tr>';}).join(''));}).then(function(){secao.querySelector('[data-activity-new]').onclick=function(){activitiesForm(secao,tipo);};var schedule=secao.querySelector('[data-eeg-agendar]');if(schedule)schedule.onclick=function(){novoAtendimento(secao);};var prep=secao.querySelector('[data-eeg-preparos]');if(prep)prep.onclick=preparos;secao.querySelectorAll('[data-activity-edit]').forEach(function(b){b.onclick=function(){activitiesForm(secao,tipo,activityRows[b.dataset.activityEdit]);};});secao.querySelectorAll('[data-activity-convert]').forEach(function(b){b.onclick=function(){converterLead(secao,activityRows[b.dataset.activityConvert]);};});
      if(tipo==='lead') {var host=document.createElement('div');host.setAttribute('data-eeg-titulo','Agenda de EEG');host.setAttribute('data-eeg-modulo','leads');secao.appendChild(host);montarAgenda(host);}
    });
  }
  function converterLead(secao,a) {if(a.exam_id)return todos('/eeg/exames').then(function(r){var e=r.itens.find(function(e){return e.id===a.exam_id;});if(!e)throw Error('Atendimento não encontrado nesta fila.');operar(secao,e);});return todos('/eeg/pessoas').then(function(r){var d=formulario('Converter lead EEG', '<p>Selecione a identidade após conferência. Use Cadastrar / vincular para uma pessoa ainda ausente.</p><button type="button" data-convert-register>Cadastrar / vincular pessoa</button>'+select('enrollment_id','Paciente',r.itens.map(function(v){return [v.id,v.pessoa.public_code+' · '+v.pessoa.nome_completo];})),function(data){return post('/eeg/atividades/'+a.id+'/converter',data).then(function(){novoAtendimento(secao,data.enrollment_id,a.id);});},'Vincular e criar atendimento');d.querySelector('[data-convert-register]').onclick=function(){d.close();cadastrar(secao);};if(a.enrollment_id)d.querySelector('[name="enrollment_id"]').value=a.enrollment_id;});}
  function montarTarefas(secao){return montarAtividades(secao,'tarefa');}
  function montarLeads(secao){return montarAtividades(secao,'lead');}
  function preparos() {return m15().api('/eeg/preparos').then(function(r){var dialog=formulario('Preparos EEG por modalidade',r.itens.map(function(p){return '<article><h4>'+esc(p.modalidade)+'</h4><p>'+esc(p.texto||'Aguardando texto aprovado. Não envie instrução clínica genérica.')+'</p><p>'+esc(p.referencia_aprovacao||'')+'</p></article>';}).join(''),function(){},'Fechar preparos');if(medico()){var button=document.createElement('button');button.type='button';button.textContent='Registrar preparo aprovado';button.onclick=function(){formulario('Aprovação médica de preparo EEG',select('modalidade_codigo','Modalidade',r.itens.map(function(p){return[p.codigo,p.modalidade];}))+textArea('texto','Texto aprovado (específico da modalidade)',true)+campo('referencia_aprovacao','Referência documental da aprovação','text',true),function(d){return post('/eeg/preparos',d).then(function(){dialog.close();preparos();});});};dialog.querySelector('form').appendChild(button);}});}
  function montarMarketing(secao){return painel(secao,'Marketing & SEO EEGLife','Domínio eeglife.com.br','',Promise.resolve({}),function(){return '<div class="eeg-stats">'+mini('Search Console','Aguardando integração')+mini('GA4','Aguardando integração')+'</div><p>Métricas da SoproLife permanecem no contexto SoproLife. Conecte as propriedades EEGLife antes de apresentar indicadores.</p>';});}
  function montarFontes(secao){return painel(secao,'Fontes de dados','Estado observado e configuração de cada fonte. Configurado não significa teste externo concluído.','',m15().api('/eeg/fontes'),function(d){return tabela(['Fonte','Situação'],d.itens.map(function(f){return '<tr><td>'+esc(f.nome)+'</td><td>'+esc(f.status)+'</td></tr>';}).join(''))+'<p>Verificação: '+esc(dataBr(d.verificado_em))+'</p>';});}
  function montarEventos(secao){return painel(secao,'Últimos lançamentos','Trilha EEGLife sem conteúdo clínico, nomes de pacientes ou contatos.','',todos('/eeg/eventos'),function(d){return d.itens.length?tabela(['Quando','Evento','Entidade','Responsável'],d.itens.map(function(e){return '<tr><td>'+esc(dataBr(e.quando))+'</td><td>'+esc(e.acao)+'</td><td>'+esc(e.entidade)+' · '+esc(e.entidade_id)+'</td><td>'+esc(e.responsavel_id||'Sistema')+'</td></tr>';}).join('')):vazio('Nenhum evento EEG registrado.');});}
  var docCategories=['contratos','equipamento','anvisa','calibracao','treinamento','neurologistas','parceiros','protocolos','fiscal','institucional'];
  function uploadDocumento(secao,previous) {var f=formulario('Documento privado EEGLife',campo('titulo','Título','text',true)+select('categoria','Categoria',docCategories.map(function(c){return[c,human(c)];}))+campo('validade','Validade','date')+campo('arquivo','Arquivo institucional','file',true),function(_data,_d,form){var fd=new FormData(form);if(!fd.get('validade'))fd.delete('validade');if(previous)fd.set('predecessor_id',previous);return m15().api('/eeg/documentos',{method:'POST',body:fd}).then(function(){atualizar(secao);});});f.querySelector('[name="arquivo"]').accept='.pdf,.zip,.edf';}
  function montarDocumentos(secao){return painel(secao,'Documentos EEGLife','Acervo privado com hash e versões preservadas. PDF, EDF ou ZIP até 64 MiB.','<button data-doc-new>Adicionar documento</button>',todos('/eeg/documentos'),function(d){var replaced=d.itens.map(function(v){return v.predecessor_id;});return d.itens.length?tabela(['Documento','Categoria','Validade','Versão','Ações'],d.itens.map(function(v){return '<tr><td>'+esc(v.titulo)+'</td><td>'+esc(human(v.categoria))+'</td><td>'+esc(v.validade?diaBr(v.validade):'Não informada')+'</td><td>'+esc(replaced.includes(v.id)?'Substituído':'Vigente')+'</td><td><button data-doc-download="'+esc(v.id)+'">Baixar</button>'+(!replaced.includes(v.id)?'<button data-doc-replace="'+esc(v.id)+'">Nova versão</button>':'')+'</td></tr>';}).join('')):vazio('Nenhum documento institucional EEGLife registrado.');}).then(function(){secao.querySelector('[data-doc-new]').onclick=function(){uploadDocumento(secao);};secao.querySelectorAll('[data-doc-download]').forEach(function(b){b.onclick=function(){baixar('/eeg/documentos/'+b.dataset.docDownload+'/conteudo').catch(function(e){secao.querySelector('[data-eeg-corpo]').textContent=mensagemErro(e);});};});secao.querySelectorAll('[data-doc-replace]').forEach(function(b){b.onclick=function(){uploadDocumento(secao,b.dataset.docReplace);};});});}
  function montarParceiros(secao){return painel(secao,'Parceiros EEGLife','Instituição e unidade corporativas; profissional laudador tem cadastro próprio.','<button data-partner-new>Adicionar / vincular parceiro</button>',todos('/eeg/parceiros'),function(d){return d.itens.length?tabela(['Parceiro','Categoria','Unidades'],d.itens.map(function(p){return '<tr><td>'+esc(p.nome)+'</td><td>'+esc(human(p.categoria))+'</td><td>'+esc(p.unidades.map(function(u){return u.nome;}).join(' · ')||'Sem unidade')+'</td></tr>';}).join('')):vazio('Nenhum parceiro vinculado à EEGLife.');}).then(function(){secao.querySelector('[data-partner-new]').onclick=function(){return m15().api('/eeg/parceiros/corporativos').then(function(r){formulario('Parceiro corporativo EEG',select('partner_id','Instituição corporativa existente', [['','Cadastrar nova instituição']].concat(r.itens.map(function(p){return[p.id,p.codigo+' · '+p.nome];})))+campo('nome','Nome institucional (para novo)')+select('categoria','Categoria',['clinica','hospital','neurologista','telelaudo','pediatra','psiquiatra','home_care','unidade'].map(function(v){return[v,human(v)];}))+campo('unidade','Unidade (opcional)'),function(d){return post('/eeg/parceiros',d).then(function(){atualizar(secao);});});});};});}
  function montarNucleo(secao) {return painel(secao,'Equipe e laudo EEG','Papéis são concedidos no Núcleo administrativo corporativo. Nenhuma conta ganha autorização clínica por ser admin.','<button data-eeg-professional>Registrar laudador verificado</button><button data-eeg-preparos>Consultar preparos</button>',m15().api('/eeg/profissionais'),function(d){return d.itens.length?tabela(['Médico','CRM','RQE','Serviço'],d.itens.map(function(p){return '<tr><td>'+esc(p.nome)+'</td><td>'+esc(p.crm+'/'+p.uf)+'</td><td>'+esc(p.rqe||'Não informado')+'</td><td>'+esc(p.servico||p.tipo)+'</td></tr>';}).join('')):vazio('Nenhum laudador EEG verificado. Não há médico padrão.');}).then(function(){secao.querySelector('[data-eeg-preparos]').onclick=preparos;secao.querySelector('[data-eeg-professional]').onclick=function(){return m15().api('/eeg/equipe').then(function(team){formulario('Laudador EEG verificado',campo('nome','Nome profissional','text',true)+campo('crm','CRM (dígitos)','text',true)+campo('uf','UF do CRM','text',true)+campo('rqe','RQE (quando aplicável)')+campo('servico','Serviço de telelaudo (opcional)')+select('tipo','Vínculo',[['interno','Neurologista interno'],['parceiro','Neurologista parceiro'],['telelaudo','Serviço de telelaudo']])+select('user_id','Conta médica EEG (obrigatória para interno)',[['','Profissional externo sem conta']].concat(team.itens.filter(function(u){return u.papeis.includes('medical_eeg');}).map(function(u){return[u.id,u.nome];})))+campo('referencia_verificacao','Referência da conferência CRM/RQE','text',true),function(d){return post('/eeg/profissionais',d).then(function(){atualizar(secao);});});});};});}
  function eegWorkspaceActions(secao,e,acao,salvo) {
    var fluxo=e.fluxo||{}, state=fluxo.status;
    if(tem('eeg_tecnico') && !['aguardando_assinatura','recebido_assinado','pronto_entrega','entregue'].includes(state) && !['cadastro','agendado','confirmado','cancelado'].includes(e.status))acao('Importar arquivo do exame',function(){var f=formulario('Importar arquivo · '+e.public_code,'<p>Paciente: '+esc(e.paciente.nome_completo)+'. Confira paciente e código do arquivo exportado pelo Nano/iCelera. Nenhuma interpretação automática será feita. PDF, EDF ou ZIP até 64 MiB.</p>'+campo('confirmacao_codigo','Confirme o código EEG','text',true)+campo('arquivo','Arquivo exportado','file',true),function(_data,_dialog,form){return m15().api('/eeg/exames/'+e.id+'/importar',{method:'POST',body:new FormData(form)}).then(salvo);},'Validar e importar');f.querySelector('[name="arquivo"]').accept='.pdf,.edf,.zip';});
    acao('Arquivos e versões',function(){return m15().api('/eeg/exames/'+e.id+'/arquivos').then(function(d){var f=formulario('Arquivos privados · '+e.public_code,d.itens.map(function(v){return '<article><p>'+esc(v.formato||'Referência externa')+' · '+esc(v.size_bytes||'—')+' bytes · '+esc(dataBr(v.criado_em))+'</p><p class="eeg-hash">SHA-256: '+esc(v.sha256||'Não disponível')+'</p>'+(v.privado?'<button type="button" data-file-download="'+esc(v.id)+'">Baixar arquivo</button>':'')+'</article>';}).join('')||vazio('Nenhum arquivo.'),function(){},'Fechar arquivos');f.querySelectorAll('[data-file-download]').forEach(function(b){b.onclick=function(){baixar('/eeg/arquivos/'+b.dataset.fileDownload+'/conteudo').catch(function(err){f.querySelector('[data-eeg-erro]').textContent=mensagemErro(err);});};});});});
    function step(name,action){acao(name,function(){formulario(name,'<p>'+esc(e.paciente.nome_completo)+' · '+esc(e.public_code)+'</p>'+campo('confirmacao_codigo','Confirme o código EEG','text',true),function(d){d.acao=action;return post('/eeg/exames/'+e.id+'/fluxo',d).then(salvo);},'Confirmar');});}
    if(tem('eeg_tecnico')&&state==='aguardando_conferencia')step('Confirmar conferência técnica','conferir');
    if(tem('eeg_operador')&&['pronto_laudo','atribuido'].includes(state))acao('Atribuir laudador EEG',function(){return m15().api('/eeg/profissionais').then(function(r){if(!r.itens.length)throw Error('Cadastre um laudador verificado no Núcleo administrativo EEG.');formulario('Atribuir EEG',select('professional_id','Neurologista / serviço',r.itens.map(function(p){return [p.id,p.nome+' · CRM '+p.crm+'/'+p.uf+(p.servico?' · '+p.servico:'')];})),function(d){return post('/eeg/exames/'+e.id+'/atribuir',d).then(salvo);});});});
    if(medico()&&['atribuido','em_elaboracao'].includes(state))acao('Editor de laudo EEG',function(){return m15().api('/eeg/exames/'+e.id+'/bancada').then(function(r){var f=formulario('Redação médica EEG · '+e.public_code,'<p>Autoria médica explícita. Preencha somente informações fornecidas e sua avaliação. Nenhuma conclusão automática.</p>'+textArea('indicacao','Indicação clínica (somente se fornecida)')+textArea('tecnica','Técnica',true)+textArea('descricao','Descrição',true)+textArea('conclusao','Conclusão',true)+textArea('observacoes','Observações'),function(d){return post('/eeg/exames/'+e.id+'/redacao',d).then(salvo);},'Salvar versão');Object.keys(r.conteudo||{}).forEach(function(k){var el=f.querySelector('[name="'+k+'"]');if(el)el.value=r.conteudo[k]||'';});});});
    if(medico()&&state==='em_elaboracao')step('Concluir laudo e gerar PDF final','concluir');
    if(fluxo.final_document_id)acao('Baixar PDF final para assinatura',function(){return baixar('/eeg/laudos/'+fluxo.final_document_id+'/arquivo');});
    if(tem('eeg_operador')&&['atribuido','em_elaboracao','aguardando_assinatura'].includes(state))acao('Receber PDF assinado',function(){var f=formulario('Recebimento operacional de assinatura · '+e.public_code,'<p>Confira paciente, código, médico e assinatura no PDF. Este registro não certifica validação criptográfica ICP-Brasil.</p>'+campo('confirmacao_codigo','Confirme o código EEG','text',true)+campo('arquivo','PDF assinado recebido','file',true),function(_d,_modal,form){var fd=new FormData(form);fd.set('assinatura_recebida','true');return m15().api('/eeg/exames/'+e.id+'/laudo',{method:'POST',body:fd}).then(salvo);},'Registrar recebido assinado');f.querySelector('[name="arquivo"]').accept='.pdf,application/pdf';});
    if(tem('eeg_operador')&&state==='recebido_assinado')step('Liberar resultado para entrega','publicar');
    if(tem('eeg_operador')&&state==='pronto_entrega')step('Registrar entrega ao paciente','entregar');
  }

  window.SoproEegOperacao = {
    MODULOS_VIVOS: Object.keys(MODULOS_VIVOS),
    montar: montar,
    cache: cache,
  };
})();
