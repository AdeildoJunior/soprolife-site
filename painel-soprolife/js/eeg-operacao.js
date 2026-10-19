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
      "</div>"
    );
    var busca = secao.querySelector("[data-eeg-busca]");
    var pedir = function () {
      var q = busca && busca.value.trim();
      return carregar(secao,
        m15().api("/eeg/pessoas" + (q ? "?busca=" + encodeURIComponent(q) : "")),
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
      "Exames de EEG agendados e confirmados.", ""
    );
    return carregar(secao, m15().api("/eeg/exames"), function (dados) {
      var itens = ((dados && dados.itens) || []).filter(function (e) {
        return e.status === "agendado" || e.status === "confirmado" ||
               e.status === "cadastro";
      });
      if (!itens.length) return vazio("Nenhum exame de EEG agendado.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Quando", "Local", "Situação"],
        itens.map(function (e) {
          return "<tr><td>" + esc(e.public_code) + "</td>" +
            "<td>" + esc((e.paciente || {}).nome_completo || "—") + "</td>" +
            "<td>" + esc(e.modalidade_nome || e.modalidade_codigo) + "</td>" +
            "<td>" + esc(dataBr(e.agendado_para)) + "</td>" +
            "<td>" + esc(e.local_nome || "—") + "</td>" +
            "<td>" + badgeStatus(e.status) + "</td></tr>";
        }).join("")
      );
    });
  }

  // ── Exames e traçados ───────────────────────────────────────────────────

  function montarExames(secao) {
    secao.innerHTML = cabecalho(secao) + moldura(
      "Exames e traçados",
      "Fila operacional dos exames de EEG.", ""
    );
    return carregar(secao, m15().api("/eeg/exames"), function (dados) {
      var itens = (dados && dados.itens) || [];
      if (!itens.length) return vazio("Nenhum exame de EEG registrado.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Local", "Realizado em",
         "Situação", "Arquivo técnico", "Envio do laudo"],
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
              : "—") + "</td></tr>";
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
    return carregar(secao, m15().api("/eeg/laudos"), function (dados) {
      var itens = (dados && dados.itens) || [];
      if (!itens.length) return vazio("Nenhum exame realizado aguardando laudo.");
      return tabela(
        ["Exame", "Paciente", "Modalidade", "Realizado em", "Destino",
         "Enviado em", "Situação", "Laudo"],
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
              : "—") + "</td></tr>";
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
    if (secao.getAttribute("data-eeg-vivo") === "montado") {
      return true;
    }
    secao.setAttribute("data-eeg-vivo", "montado");
    try {
      fabrica(secao);
    } catch (e) {
      console.error("EEG: falha ao montar módulo " + moduloId, e);
      return false;
    }
    return true;
  }

  window.SoproEegOperacao = {
    MODULOS_VIVOS: Object.keys(MODULOS_VIVOS),
    montar: montar,
    cache: cache,
  };
})();
