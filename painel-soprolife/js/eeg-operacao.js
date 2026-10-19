/* EEGLife — operação real (Fase 2A).
 *
 * Transforma quatro módulos da casca EEGLife em telas com DADO DE VERDADE,
 * vindo das rotas /eeg do núcleo: Central de Cadastros, Exames e traçados,
 * Laudos de EEG e Agendamentos. Os demais seguem como estrutura vazia.
 *
 * O QUE ESTE ARQUIVO NÃO DECIDE
 * -----------------------------
 * Autorização. Quem pode ver a EEGLife é decidido no servidor
 * (`require_eeg_access`), e toda rota aqui volta 403 para quem não pode. Este
 * arquivo só desenha o que a API devolveu — se ela recusar, a tela diz que
 * recusou, e é isso.
 *
 * ESCOPO
 * ------
 * A Central lista por VÍNCULO (`/eeg/pessoas`), nunca a base inteira de
 * pessoas. Uma pessoa da SoproLife sem exame de EEG não aparece aqui porque a
 * consulta do servidor não a devolve — não porque esta tela a esconde.
 *
 * ARQUIVO TÉCNICO
 * ---------------
 * O traçado não sobe por aqui e não é exibido. O que existe é o registro de
 * que ele EXISTE e onde está. Um EEG digital tem dezenas a centenas de MB;
 * trazê-lo para o navegador seria errado de três formas ao mesmo tempo.
 */
(function () {
  "use strict";

  var MODULOS_VIVOS = {
    cadastros: montarCentral,
    exames: montarExames,
    laudos: montarLaudos,
    leads: montarAgenda,
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
    return promessa.then(function (dados) {
      secao.querySelector("[data-eeg-corpo]").innerHTML = render(dados);
      secao.querySelectorAll("[data-eeg-exame]").forEach(function (b) {
        b.onclick = function () { operar(secao, exames[b.getAttribute("data-eeg-exame")]); };
      });
    }).catch(function (err) {
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

  function montarCentral(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Central de Cadastros",
      "Pessoas com vínculo operacional na EEGLife.",
      '<div class="eeg-toolbar">' +
        '<input type="search" data-eeg-busca placeholder="Buscar paciente da EEGLife…" ' +
        'aria-label="Buscar paciente da EEGLife" />' +
        '<button type="button" data-eeg-novo-paciente>Cadastrar / vincular paciente</button>' +
      "</div>"
    );
    secao.querySelector("[data-eeg-novo-paciente]").onclick = function () { cadastrar(secao); };
    var busca = secao.querySelector("[data-eeg-busca]");
    var pedir = function () {
      var q = busca && busca.value.trim();
      return carregar(secao,
        todos("/eeg/pessoas" + (q ? "?busca=" + encodeURIComponent(q) : "")),
        renderCentral);
    };
    if (busca) {
      var timer = null;
      busca.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(pedir, 300);
      });
    }
    return pedir();
  }

  function renderCentral(dados) {
    var itens = (dados && dados.itens) || [];
    if (!itens.length) {
      return vazio("Nenhuma pessoa vinculada à operação EEGLife ainda.");
    }
    var linhas = itens.map(function (v) {
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
        "</tr>";
    }).join("");
    return tabela(["Código", "Paciente", "CPF", "Exames EEG", "Cadastro"], linhas) +
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
      "Fila operacional dos exames de EEG.", '<button type="button" data-eeg-agendar>Agendar EEG</button>'
    );
    ligarAgenda(secao);
    return carregar(secao, todos("/eeg/exames"), function (dados) {
      var itens = (dados && dados.itens) || [];
      if (!itens.length) return vazio("Nenhum exame de EEG registrado.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Local", "Realizado em",
         "Situação", "Arquivo técnico", "Envio do laudo", "Ações"],
        itens.map(function (e) {
          var tec = e.arquivo_tecnico || {};
          return "<tr><td>" + esc(e.public_code) + "</td>" +
            "<td>" + esc((e.paciente || {}).nome_completo || "—") + "</td>" +
            "<td>" + esc(e.modalidade_nome || e.modalidade_codigo) + "</td>" +
            "<td>" + esc(e.local_nome || "—") + "</td>" +
            "<td>" + esc(dataBr(e.realizado_em)) + "</td>" +
            "<td>" + badgeStatus(e.status) + "</td>" +
            "<td>" + (tec.registrado
              ? '<span class="badge">' + esc(tec.formato || "registrado") + "</span>"
              : '<span class="badge eeg-badge-pendente">não registrado</span>') + "</td>" +
            "<td>" + (e.envio_laudo
              ? esc(e.envio_laudo.destino_nome)
              : "—") + "</td><td>" + botaoExame(e) + "</td></tr>";
        }).join("")
      ) + '<p class="eeg-nota">O traçado não é exibido nem armazenado aqui: ' +
        "o Centro de Comando registra que ele existe e onde está. " +
        "A fonte técnica continua sendo o equipamento ou a plataforma externa.</p>";
    });
  }

  // ── Laudos de EEG ───────────────────────────────────────────────────────

  function montarLaudos(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Laudos de EEG",
      "Fila própria da EEGLife — não reaproveita laudo de espirometria.", ""
    );
    return carregar(secao, todos("/eeg/laudos"), function (dados) {
      var itens = (dados && dados.itens) || [];
      if (!itens.length) return vazio("Nenhum exame realizado aguardando laudo.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Realizado em", "Destino",
         "Enviado em", "Situação", "Laudo", "Ações"],
        itens.map(function (e) {
          var l = e.laudo;
          return "<tr><td>" + esc(e.public_code) + "</td>" +
            "<td>" + esc((e.paciente || {}).nome_completo || "—") + "</td>" +
            "<td>" + esc(e.modalidade_nome || e.modalidade_codigo) + "</td>" +
            "<td>" + esc(dataBr(e.realizado_em)) + "</td>" +
            "<td>" + esc(e.envio_laudo ? e.envio_laudo.destino_nome : "—") + "</td>" +
            "<td>" + esc(dataBr(e.enviado_para_laudo_em)) + "</td>" +
            "<td>" + badgeStatus(e.status) + "</td>" +
            "<td>" + (l
              ? '<span class="badge">' + esc(l.public_code) + " · v" + esc(String(l.versao)) + "</span>"
              : "—") + "</td><td>" + botaoExame(e) + "</td></tr>";
        }).join("")
      );
    });
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
    if (!fabrica || !m15() || typeof m15().api !== "function") return false;
    secao.setAttribute("data-eeg-vivo", "montado");
    secao.setAttribute("data-eeg-modulo", moduloId);
    try {
      fabrica(secao);
    } catch (e) {
      console.error("EEG: falha ao montar módulo " + moduloId, e);
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
    document.body.appendChild(d);
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
    formulario("Cadastrar ou vincular paciente EEG",
      '<p>Verifique a identidade corporativa antes de criar uma pessoa.</p>' +
      campo("nome_completo", "Nome completo", "text", true) +
      campo("cpf", "CPF (opcional)") + campo("data_nascimento", "Nascimento", "date") +
      campo("telefone", "Telefone com DDI (opcional)", "tel") +
      campo("email", "E-mail (opcional)", "email") +
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
          d.querySelectorAll("input").forEach(function (i) { i.oninput = function () { area.innerHTML = ""; }; });
          return false;
        });
      }, "Verificar identidade");
  }

  function ligarAgenda(secao) {
    secao.querySelector("[data-eeg-agendar]").onclick = function () {
      Promise.all([todos("/eeg/pessoas"), m15().api("/eeg/modalidades"), m15().api("/eeg/locais")])
        .then(function (r) {
          if (!r[0].itens.length) { cadastrar(secao); return; }
          formulario("Agendar EEG",
            select("enrollment_id", "Paciente", r[0].itens.map(function (v) { return [v.id, v.pessoa.public_code + " · " + v.pessoa.nome_completo]; })) +
            select("modalidade_codigo", "Modalidade", r[1].itens.filter(function (m) { return m.ativo; }).map(function (m) { return [m.codigo, m.nome]; })) +
            campo("agendado_para", "Data e hora", "datetime-local", true) +
            select("partner_unit_id", "Origem / local", [["", "Atendimento direto EEGLife"]].concat(r[2].itens.map(function (u) { return [u.id, u.nome]; }))) +
            campo("tecnico_responsavel", "Técnico responsável (opcional)"),
            function (dados) {
              dados.origem_tipo = dados.partner_unit_id ? "parceiro" : "direto";
              dados.agendado_para = new Date(dados.agendado_para).toISOString();
              return post("/eeg/exames", dados).then(function () { atualizar(secao); });
            });
        }).catch(function (e) { secao.querySelector("[data-eeg-corpo]").textContent = mensagemErro(e); });
    };
  }

  function operar(secao, e) {
    if (!e) return;
    var d = document.createElement("dialog"); d.className = "eeg-dialog";
    d.innerHTML = '<h3>' + esc(e.public_code) + ' · ' + esc((e.paciente || {}).nome_completo) + '</h3>' +
      '<p>' + esc(e.modalidade_nome) + ' · ' + badgeStatus(e.status) + '</p>' +
      '<p>Local: ' + esc(e.local_nome) + ' · Agenda: ' + esc(dataBr(e.agendado_para)) + '</p>' +
      '<p>Referência técnica: ' + esc((e.arquivo_tecnico || {}).referencia_externa || "Não registrada") + '</p>' +
      '<p>Envio: ' + esc(e.envio_laudo ? e.envio_laudo.destino_nome + " · " + (e.envio_laudo.protocolo_externo || "sem protocolo") + " · " + dataBr(e.envio_laudo.enviado_em) : "Não registrado") + '</p>' +
      '<div data-eeg-acoes class="eeg-toolbar"></div><p data-eeg-erro role="alert"></p><button type="button" data-eeg-fechar>Fechar</button>';
    document.body.appendChild(d); d.addEventListener("close", function () { d.remove(); });
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
    if (["cadastro", "agendado", "confirmado"].includes(e.status)) {
      acao("Agendar / reagendar", function () { transicao("agendado", true); });
      if (e.status === "agendado") acao("Confirmar agendamento", function () { transicao("confirmado"); });
      if (e.status !== "cadastro") acao("Registrar realização", function () { transicao("realizado", true); });
    }
    if (["realizado", "aguardando_envio_laudo", "enviado_para_laudo", "em_laudo", "laudo_recebido", "laudo_disponivel"].includes(e.status)) {
      acao("Registrar referência técnica", function () {
        formulario("Referência técnica do EEG",
          '<p>O arquivo bruto permanece no equipamento ou na plataforma externa.</p>' +
          select("armazenamento_origem", "Onde está o traçado", [["equipamento", "Software do equipamento"], ["plataforma_externa", "Plataforma externa"]]) +
          campo("referencia_externa", "Referência técnica", "text", true) + campo("formato", "Formato (opcional)") + campo("equipamento", "Equipamento (opcional)"),
          function (dados) { return post("/eeg/exames/" + e.id + "/arquivo-tecnico", dados).then(salvo); });
      });
    }
    if (["realizado", "aguardando_envio_laudo", "enviado_para_laudo"].includes(e.status)) {
      acao("Registrar envio ao laudador", function () {
        formulario("Registrar envio realizado",
          '<p>Registre o envio feito fora do painel. Nenhum arquivo é enviado automaticamente.</p>' +
          select("destino_tipo", "Tipo de destino", [["empresa", "Empresa / plataforma de laudos"], ["medico", "Médico laudador direto"]]) +
          campo("destino_nome", "Destino", "text", true) +
          campo("enviado_em", "Data e hora do envio", "datetime-local", true) +
          select("metodo", "Método", [["plataforma_externa", "Plataforma externa"], ["upload_manual_externo", "Upload manual externo"], ["outro", "Outro"]]) +
          campo("protocolo_externo", "Protocolo / referência (opcional)"),
          function (dados) { dados.enviado_em = new Date(dados.enviado_em).toISOString(); return post("/eeg/exames/" + e.id + "/envio-laudo", dados).then(salvo); });
      });
    }
    if (e.status === "enviado_para_laudo") acao("Registrar em laudo", function () { transicao("em_laudo"); });
    if (["enviado_para_laudo", "em_laudo", "laudo_recebido", "laudo_disponivel"].includes(e.status)) {
      acao("Receber PDF do laudo", function () {
        var f = formulario("Receber laudo EEG",
          '<p>Envie somente o laudo PDF final recebido do laudador.</p>' + campo("arquivo", "Laudo PDF", "file", true) + campo("medico_laudador", "Médico laudador (opcional)"),
          function (_dados, _d, form) {
            return m15().api("/eeg/exames/" + e.id + "/laudo", { method: "POST", body: new FormData(form) }).then(salvo);
          });
        f.querySelector('[name="arquivo"]').accept = "application/pdf,.pdf";
      });
    }
    if (e.status === "laudo_recebido") acao("Disponibilizar PDF final", function () { transicao("laudo_disponivel"); });
    if (e.laudo) acao("Baixar laudo PDF", function () {
      return m15().apiBlob("/eeg/laudos/" + e.laudo.id + "/arquivo").then(function (blob) {
        var a = document.createElement("a"), url = URL.createObjectURL(blob);
        a.href = url; a.download = blob.nomeSugerido || (e.laudo.public_code + ".pdf"); a.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      });
    });
    if (e.status === "laudo_disponivel" && e.laudo) {
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
    if (!["cancelado", "laudo_disponivel"].includes(e.status)) acao("Cancelar exame", function () { transicao("cancelado"); });
    d.showModal();
  }

  window.SoproEegOperacao = {
    MODULOS_VIVOS: Object.keys(MODULOS_VIVOS),
    montar: montar,
    cache: cache,
  };
})();
