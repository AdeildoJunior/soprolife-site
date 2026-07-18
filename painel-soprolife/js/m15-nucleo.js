/* Núcleo Operacional M15 (experimental) — módulo isolado e reversível.
 *
 * - Feature flag: data/m15-config.json (enabled) OU localStorage soproM15='on'.
 *   Desligado (padrão), este arquivo não altera NADA no painel.
 * - Backend: proxy de mesma origem → API própria em loopback
 *   (FastAPI + PostgreSQL/SQLite), ver nucleo-m15/README.md.
 * - Token: colado pelo usuário (CLI emitir-token) e mantido SOMENTE EM
 *   MEMÓRIA nesta fase (nunca em localStorage/sessionStorage — reduz o
 *   impacto de XSS). Recarregar a página exige colar o token de novo.
 * - Busca de pessoas via POST (corpo) — nome nunca vai para a URL/logs.
 * - WhatsApp: sempre revisão humana; NUNCA dispara envio automático;
 *   botão só aparece quando a API confirma whatsapp_permitido.
 */
(function () {
  "use strict";

  var CONFIG_URL = "data/m15-config.json";
  var state = {
    apiBase: "/painel-soprolife/api/m15",
    tab: "visao",
    filters: {},
    token: "", // em memória apenas; some ao recarregar (decisão de segurança)
  };

  function getToken() { return state.token; }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var parts = iso.split("-");
    if (parts.length !== 3) return iso;
    return parts[2] + "/" + parts[1] + "/" + parts[0];
  }

  function isSynthetic(item) {
    if (!item) return false;
    if (item.legacy_source === "seed_demo") return true;
    var obs = item.observacao || item.observacao_operacional || "";
    return obs.indexOf("SINTÉTICO") !== -1;
  }

  function sintBadge(item) {
    return isSynthetic(item)
      ? ' <span class="m15-badge-sint" title="Dado sintético de demonstração">sintético</span>'
      : "";
  }

  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({
      "Authorization": "Bearer " + getToken(),
      "Content-Type": "application/json",
    }, options.headers || {});
    return fetch(state.apiBase + path, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var msg = (body && body.erro && body.erro.mensagem) || ("HTTP " + resp.status);
          var err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  // ------------------------------------------------------------ estrutura

  var TABS = [
    ["visao", "Visão geral"],
    ["pessoas", "Pessoas"],
    ["leads", "Leads"],
    ["espirometrias", "Espirometrias"],
    ["consultas", "Consultas"],
    ["parceiros", "Clínicas e Parceiros"],
    ["encaminhamentos", "Pacientes de Parceiros"],
    ["followup", "Follow-up"],
    ["financeiro", "Financeiro"],
    ["migracao", "Migração"],
    ["auditoria", "Auditoria"],
  ];

  function buildSection() {
    var section = document.createElement("section");
    section.id = "m15-nucleo";
    section.className = "section";
    section.innerHTML =
      '<div class="m15-header">' +
      '  <h2>Núcleo Operacional' +
      '    <span class="m15-badge-exp">M15 experimental</span>' +
      '    <span class="m15-badge-sint">ambiente com dados sintéticos</span>' +
      "  </h2>" +
      "</div>" +
      '<div class="m15-panel">' +
      '  <div class="m15-token-box">' +
      '    <span class="m15-muted">Token de acesso (CLI: <code>python -m app.cli emitir-token</code>):</span>' +
      '    <input type="password" id="m15Token" placeholder="cole o token da API aqui" autocomplete="off">' +
      '    <button class="m15-btn m15-btn-sec" id="m15TokenSave">Usar token (sessão)</button>' +
      '    <span class="m15-muted" id="m15ApiStatus">Proxy/API: verificando…</span>' +
      '    <span class="m15-muted">O token fica só em memória — recarregou, cole de novo.</span>' +
      "  </div>" +
      "</div>" +
      '<div class="m15-tabs" id="m15Tabs">' +
      TABS.map(function (t) {
        return '<button class="m15-tab' + (t[0] === state.tab ? " active" : "") +
          '" data-m15-tab="' + t[0] + '">' + t[1] + "</button>";
      }).join("") +
      "</div>" +
      '<div id="m15Body"><div class="m15-empty">Carregando…</div></div>';
    return section;
  }

  function navButton() {
    var btn = document.createElement("button");
    btn.className = "nav-item";
    btn.setAttribute("data-section", "m15-nucleo");
    btn.innerHTML =
      '<svg class="nav-icon" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="9" cy="9" r="7"/><circle cx="9" cy="9" r="2.6"/><path d="M9 2v2.5M9 13.5V16M2 9h2.5M13.5 9H16"/></svg>' +
      "<span>Núcleo M15</span>";
    btn.addEventListener("click", function () {
      document.querySelectorAll(".nav-item").forEach(function (item) { item.classList.remove("active"); });
      document.querySelectorAll(".section").forEach(function (sec) { sec.classList.remove("active"); });
      btn.classList.add("active");
      document.getElementById("m15-nucleo").classList.add("active");
      render();
    });
    return btn;
  }

  // ------------------------------------------------------------ renderização

  function body() { return document.getElementById("m15Body"); }

  function showError(err) {
    var hint = err && err.status === 401
      ? " Cole um token válido acima (CLI: emitir-token) e tente de novo."
      : (err && err.message && err.message.indexOf("fetch") !== -1
        ? " O proxy e a API loopback estão rodando? Veja nucleo-m15/README.md." : "");
    body().innerHTML = '<div class="m15-erro">Erro: ' + esc(err.message || err) + hint + "</div>";
  }

  function cards(defs) {
    return '<div class="m15-cards">' + defs.map(function (c) {
      return '<div class="m15-card ' + (c.cls || "") + '">' +
        '<div class="m15-card-label">' + esc(c.label) + "</div>" +
        '<div class="m15-card-value">' + esc(c.value) + "</div></div>";
    }).join("") + "</div>";
  }

  function table(headers, rows, emptyMsg) {
    if (!rows.length) {
      return '<div class="m15-empty">' + esc(emptyMsg || "Nenhum registro ainda.") + "</div>";
    }
    return '<div class="m15-table-wrap"><table class="m15-table"><thead><tr>' +
      headers.map(function (h) { return "<th>" + esc(h) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr>" + r.map(function (c) { return "<td>" + c + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }

  function pill(value) {
    var key = String(value || "").replace(/\s+/g, "_");
    return '<span class="m15-pill ' + esc(key) + '">' + esc(value || "—") + "</span>";
  }

  var loaders = {};

  loaders.visao = function () {
    return Promise.all([
      api("/pessoas?tamanho=1"), api("/leads?tamanho=1"),
      api("/espirometrias?tamanho=1"), api("/consultas?tamanho=1"),
      api("/parceiros?tamanho=1"), api("/encaminhamentos?tamanho=1"),
      api("/followups/fila"),
    ]).then(function (r) {
      var fila = r[6];
      body().innerHTML =
        cards([
          { label: "Pessoas", value: r[0].total },
          { label: "Leads", value: r[1].total },
          { label: "Espirometrias", value: r[2].total },
          { label: "Consultas", value: r[3].total },
          { label: "Parceiros", value: r[4].total },
          { label: "Encaminhamentos", value: r[5].total },
        ]) +
        '<div class="m15-panel"><h3>Fila de follow-up (hoje: ' + esc(fila.data_referencia) + ")</h3>" +
        cards([
          { label: "Atrasados", value: fila.totais.atrasado, cls: "m15-alerta" },
          { label: "Retomar hoje", value: fila.totais.retomar_hoje, cls: "m15-hoje" },
          { label: "Nesta semana", value: fila.totais.retomar_semana },
          { label: "Aguardando data", value: fila.totais.aguardando_data },
          { label: "Não contatar (ocultos)", value: fila.excluidos_nao_contatar },
        ]) + "</div>" +
        '<div class="m15-aviso">Ambiente experimental M15: coexiste com o painel atual. ' +
        "As telas antigas e o Google Sheets continuam intactos até validação humana.</div>";
    });
  };

  loaders.pessoas = function () {
    var q = state.filters.pessoasQ || "";
    // busca por POST: o nome nunca aparece na query string (logs sem PII)
    var request = q
      ? api("/pessoas/busca", { method: "POST", body: JSON.stringify({ q: q, tamanho: 50 }) })
      : api("/pessoas?tamanho=50");
    return request.then(function (data) {
      body().innerHTML =
        '<div class="m15-filtros">' +
        '  <input id="m15PessoasQ" placeholder="Buscar por nome ou PES-…" value="' + esc(q) + '">' +
        '  <button class="m15-btn m15-btn-sec" id="m15PessoasBuscar">Buscar</button>' +
        "</div>" +
        table(
          ["Código", "Nome", "Status", "Contatos", "Criado em"],
          data.itens.map(function (p) {
            return [
              "<strong>" + esc(p.public_code) + "</strong>",
              esc(p.nome_completo) + sintBadge(p),
              pill(p.nao_contatar ? "não contatar" : p.status),
              (p.contatos || []).map(function (c) { return esc(c.tipo); }).join(", ") || "—",
              esc((p.created_at_local || "").slice(0, 10)),
            ];
          }),
          "Nenhuma pessoa cadastrada. Use o formulário abaixo ou o seed sintético."
        ) +
        '<div class="m15-panel"><h3>Nova pessoa</h3><form class="m15-form" id="m15FormPessoa">' +
        '  <label>Nome completo<input name="nome_completo" required minlength="2"></label>' +
        '  <label>WhatsApp (opcional)<input name="whatsapp" placeholder="(21) 9…"></label>' +
        '  <label>Consentimento WhatsApp<select name="consentimento_whatsapp">' +
        '    <option value="">não informado</option><option value="concedido">concedido</option>' +
        '    <option value="desconhecido">desconhecido</option><option value="revogado">revogado</option>' +
        "  </select></label>" +
        '  <label class="m15-form-full">Observação<input name="observacao"></label>' +
        '  <div class="m15-form-full"><button class="m15-btn" type="submit">Criar pessoa</button></div>' +
        "</form></div>";
      var buscar = document.getElementById("m15PessoasBuscar");
      buscar.addEventListener("click", function () {
        state.filters.pessoasQ = document.getElementById("m15PessoasQ").value;
        render();
      });
      document.getElementById("m15FormPessoa").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        var payload = { nome_completo: f.nome_completo.value, contatos: [] };
        if (f.whatsapp.value) {
          payload.contatos.push({ tipo: "whatsapp", valor: f.whatsapp.value, principal: true });
        }
        if (f.consentimento_whatsapp.value) payload.consentimento_whatsapp = f.consentimento_whatsapp.value;
        if (f.observacao.value) payload.observacao = f.observacao.value;
        api("/pessoas", { method: "POST", body: JSON.stringify(payload) })
          .then(render).catch(showError);
      });
    });
  };

  loaders.leads = function () {
    var etapa = state.filters.leadsEtapa || "";
    return api("/leads?tamanho=50" + (etapa ? "&etapa=" + etapa : "")).then(function (data) {
      body().innerHTML =
        '<div class="m15-filtros"><select id="m15LeadsEtapa">' +
        ["", "novo", "em_contato", "agendado", "convertido", "perdido", "nao_respondeu", "aguardando_retomada"]
          .map(function (e) {
            return '<option value="' + e + '"' + (e === etapa ? " selected" : "") + ">" +
              (e || "todas as etapas") + "</option>";
          }).join("") +
        "</select></div>" +
        table(
          ["Código", "Etapa", "Origem", "Modalidade", "1º contato", "Retomada manual"],
          data.itens.map(function (l) {
            return [
              "<strong>" + esc(l.public_code) + "</strong>" + sintBadge(l),
              pill(l.etapa),
              esc(l.origem || "—"),
              esc(l.modalidade || "—"),
              fmtDate(l.data_primeiro_contato) +
                (l.data_primeiro_contato_dia_assumido
                  ? ' <span class="m15-muted" title="Original: ' + esc(l.data_primeiro_contato_original) + '">(dia assumido)</span>'
                  : ""),
              fmtDate(l.data_retomada_manual),
            ];
          }),
          "Nenhum lead. Leads chegam pela API, importador ou formulário de pessoas + lead."
        );
      document.getElementById("m15LeadsEtapa").addEventListener("change", function (ev) {
        state.filters.leadsEtapa = ev.target.value;
        render();
      });
    });
  };

  function attendanceLoader(kind) {
    var isExam = kind === "espirometrias";
    var statusKey = isExam ? "espStatus" : "conStatus";
    var options = isExam
      ? ["", "Aguardando", "Realizado", "Cancelado", "Remarcado"]
      : ["", "Agendada", "Realizada", "Cancelada", "Remarcada", "Não compareceu"];
    return function () {
      var status = state.filters[statusKey] || "";
      return api("/" + kind + "?tamanho=50" + (status ? "&status=" + encodeURIComponent(status) : ""))
        .then(function (data) {
          body().innerHTML =
            '<div class="m15-filtros"><select id="m15AttStatus">' +
            options.map(function (s) {
              return '<option value="' + s + '"' + (s === status ? " selected" : "") + ">" +
                (s || "todos os status") + "</option>";
            }).join("") + "</select></div>" +
            table(
              ["Código", "Data", "Status", isExam ? "Modalidade" : "Profissional", "Origem"],
              data.itens.map(function (e) {
                var d = isExam ? e.data_exame : e.data_consulta;
                var assumed = isExam ? e.data_exame_dia_assumido : e.data_consulta_dia_assumido;
                var original = isExam ? e.data_exame_original : e.data_consulta_original;
                return [
                  "<strong>" + esc(e.public_code) + "</strong>" + sintBadge(e),
                  fmtDate(d) + (assumed
                    ? ' <span class="m15-muted" title="Original: ' + esc(original) + '">(dia assumido)</span>' : ""),
                  pill(e.status),
                  esc(isExam ? (e.modalidade || "—") : (e.profissional || "—")),
                  esc(e.origem || "—"),
                ];
              }),
              isExam ? "Nenhuma espirometria registrada no núcleo M15."
                     : "Nenhuma consulta registrada no núcleo M15."
            );
          document.getElementById("m15AttStatus").addEventListener("change", function (ev) {
            state.filters[statusKey] = ev.target.value;
            render();
          });
        });
    };
  }

  loaders.espirometrias = attendanceLoader("espirometrias");
  loaders.consultas = attendanceLoader("consultas");

  loaders.parceiros = function () {
    return api("/parceiros?tamanho=50").then(function (data) {
      body().innerHTML =
        table(
          ["Código", "Nome", "Tipo", "Status", "Cidade"],
          data.itens.map(function (p) {
            return [
              "<strong>" + esc(p.public_code) + "</strong>" + sintBadge(p),
              esc(p.nome),
              esc(p.tipo),
              pill(p.status),
              esc(p.cidade || "—"),
            ];
          }),
          "Nenhum parceiro. O cadastro institucional (ex.: Pastore) entra pelo comando privado seed-institucional."
        ) +
        '<div class="m15-panel"><h3>Novo parceiro</h3><form class="m15-form" id="m15FormParceiro">' +
        '  <label>Nome<input name="nome" required minlength="2"></label>' +
        '  <label>Tipo<select name="tipo"><option>clinica</option><option>consultorio</option><option>outro</option></select></label>' +
        '  <label>Status<select name="status"><option>prospecto</option><option>em_negociacao</option><option>ativa</option></select></label>' +
        '  <label>Cidade<input name="cidade"></label>' +
        '  <div class="m15-form-full"><button class="m15-btn" type="submit">Criar parceiro</button></div>' +
        "</form></div>";
      document.getElementById("m15FormParceiro").addEventListener("submit", function (ev) {
        ev.preventDefault();
        var f = ev.target;
        api("/parceiros", {
          method: "POST",
          body: JSON.stringify({
            nome: f.nome.value, tipo: f.tipo.value,
            status: f.status.value, cidade: f.cidade.value || null,
          }),
        }).then(render).catch(showError);
      });
    });
  };

  loaders.encaminhamentos = function () {
    var status = state.filters.encStatus || "";
    return api("/encaminhamentos?tamanho=50" + (status ? "&status=" + encodeURIComponent(status) : ""))
      .then(function (data) {
        var statuses = ["", "Recebido da clínica", "Aguardando contato", "Contato realizado",
          "Agendado", "Realizado", "Laudo enviado", "Cancelado", "Não compareceu",
          "Aguardando pagamento", "Concluído"];
        body().innerHTML =
          '<div class="m15-filtros"><select id="m15EncStatus">' +
          statuses.map(function (s) {
            return '<option value="' + esc(s) + '"' + (s === status ? " selected" : "") + ">" +
              (s || "todos os status") + "</option>";
          }).join("") + "</select></div>" +
          table(
            ["Código", "Encaminhado em", "Serviço", "Status", "Laudo", "Repasse", "Follow-up"],
            data.itens.map(function (r) {
              return [
                "<strong>" + esc(r.public_code) + "</strong>" + sintBadge(r),
                fmtDate(r.data_encaminhamento),
                esc(r.servico_solicitado || "—"),
                pill(r.status),
                r.laudo_enviado ? "enviado " + fmtDate(r.data_envio_laudo) : "pendente",
                esc(r.status_repasse || "—"),
                esc(r.responsavel_followup),
              ];
            }),
            "Nenhum paciente de parceiro. Encaminhamentos ligam Pessoa ↔ Parceiro ↔ Unidade ↔ Atendimento sem duplicar cadastros."
          );
        document.getElementById("m15EncStatus").addEventListener("change", function (ev) {
          state.filters.encStatus = ev.target.value;
          render();
        });
      });
  };

  loaders.followup = function () {
    return api("/followups/fila").then(function (data) {
      var order = ["atrasado", "retomar_hoje", "retomar_semana", "aguardando_data", "concluido"];
      var labels = {
        atrasado: "Atrasado", retomar_hoje: "Retomar hoje",
        retomar_semana: "Retomar nesta semana", aguardando_data: "Aguardando data",
        concluido: "Concluído",
      };
      var html = cards(order.map(function (k) {
        return {
          label: labels[k], value: data.totais[k],
          cls: k === "atrasado" ? "m15-alerta" : (k === "retomar_hoje" ? "m15-hoje" : (k === "concluido" ? "m15-ok" : "")),
        };
      }).concat([{ label: "Não contatar (ocultos)", value: data.excluidos_nao_contatar }]));
      order.forEach(function (key) {
        var itens = data.filas[key];
        html += '<div class="m15-panel"><h3>' + labels[key] + " (" + itens.length + ")</h3>";
        if (!itens.length) {
          html += '<div class="m15-empty">Fila vazia.</div>';
        } else {
          html += table(
            ["Pessoa", "Tipo", "Vencimento", "Tentativas", "Consentimento", "Ações"],
            itens.map(function (f) {
              var actions = "";
              if (f.status === "pendente") {
                // WhatsApp só quando a API confirma (consentimento concedido,
                // follow-up da SoproLife, registro real — fail-closed)
                if (f.whatsapp_permitido) {
                  actions +=
                    '<button class="m15-btn m15-btn-wa" data-m15-wa="' + esc(f.id) + '">WhatsApp</button> ';
                }
                actions +=
                  '<button class="m15-btn m15-btn-sec" data-m15-done="' + esc(f.id) + '">Concluir</button>';
              }
              var etiquetas = "";
              if (f.controlado_por_parceiro) etiquetas += ' <span class="m15-pill">parceiro contata</span>';
              if (f.sintetico) etiquetas += ' <span class="m15-badge-sint">sintético</span>';
              return [
                "<strong>" + esc(f.pessoa.public_code) + "</strong> " + esc(f.pessoa.nome_completo) + etiquetas,
                esc(f.tipo),
                fmtDate(f.due_date),
                String(f.tentativas),
                f.aviso_consentimento
                  ? '<span class="m15-pill atrasado" title="Sem consentimento concedido — contato bloqueado (fail-closed)">' +
                    esc(f.consentimento_whatsapp) + "</span>"
                  : pill(f.consentimento_whatsapp),
                actions || "—",
              ];
            })
          );
        }
        html += "</div>";
      });
      html += '<div id="m15WaBox"></div>';
      body().innerHTML = html;

      body().querySelectorAll("[data-m15-wa]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-m15-wa");
          api("/followups/" + id + "/whatsapp-url").then(function (info) {
            var box = document.getElementById("m15WaBox");
            box.innerHTML =
              '<div class="m15-panel"><h3>WhatsApp — revisão humana obrigatória</h3>' +
              (info.aviso_consentimento
                ? '<div class="m15-aviso">Atenção: consentimento de WhatsApp "' +
                  esc(info.consentimento_whatsapp) + '". Avalie antes de contatar.</div>'
                : "") +
              '<div class="m15-mensagem-preview">' + esc(info.mensagem_sugerida) + "</div>" +
              '<a class="m15-btn m15-btn-wa" href="' + esc(info.url) + '" target="_blank" rel="noopener noreferrer">' +
              "Abrir conversa no WhatsApp</a> " +
              '<button class="m15-btn" id="m15WaConfirm">Enviei — registrar interação</button> ' +
              '<button class="m15-btn m15-btn-sec" id="m15WaCancel">Cancelar</button>' +
              '<p class="m15-muted">Nada é enviado automaticamente. O registro só acontece após a sua confirmação.</p></div>';
            box.scrollIntoView({ behavior: "smooth" });
            document.getElementById("m15WaConfirm").addEventListener("click", function () {
              api("/followups/" + id + "/whatsapp-confirmacao", {
                method: "POST",
                body: JSON.stringify({ resultado: "enviado", resumo: "Mensagem de follow-up enviada manualmente" }),
              }).then(render).catch(showError);
            });
            document.getElementById("m15WaCancel").addEventListener("click", function () {
              box.innerHTML = "";
            });
          }).catch(showError);
        });
      });
      body().querySelectorAll("[data-m15-done]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          api("/followups/" + btn.getAttribute("data-m15-done") + "/concluir", {
            method: "POST", body: JSON.stringify({ resultado: "concluído pela fila" }),
          }).then(render).catch(showError);
        });
      });
    });
  };

  loaders.financeiro = function () {
    return api("/lancamentos?tamanho=50").then(function (data) {
      var total = data.itens.reduce(function (acc, e) {
        // valores chegam como string monetária ("250.00") — sem float no backend
        return e.tipo === "receita" && e.status === "Recebido"
          ? acc + (parseFloat(e.valor) || 0) : acc;
      }, 0);
      body().innerHTML =
        cards([
          { label: "Lançamentos", value: data.total },
          { label: "Receitas recebidas (página)", value: "R$ " + total.toFixed(2), cls: "m15-ok" },
        ]) +
        '<div class="m15-aviso">Financeiro do núcleo é separado dos dados pessoais: ' +
        "sem nome, telefone ou CPF — apenas IDs técnicos (LAN ↔ ESP/CON/ENC).</div>" +
        table(
          ["Código", "Tipo", "Categoria", "Valor", "Competência", "Status", "Vínculos"],
          data.itens.map(function (e) {
            var links = [];
            if (e.spirometry_exam_id) links.push("exame");
            if (e.consultation_id) links.push("consulta");
            if (e.partner_referral_id) links.push("encaminhamento");
            return [
              "<strong>" + esc(e.public_code) + "</strong>",
              esc(e.tipo),
              esc(e.categoria || "—"),
              "R$ " + esc(e.valor || "0.00"),
              fmtDate(e.data_competencia) + (e.data_competencia_dia_assumido ? " (dia assumido)" : ""),
              pill(e.status),
              links.join(", ") || "—",
            ];
          }),
          "Nenhum lançamento no núcleo M15. A fonte financeira legada (Financeiro_Lancamentos) permanece intacta."
        );
    });
  };

  loaders.migracao = function () {
    return Promise.all([
      api("/importacoes?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
      api("/identidade/candidatos?tamanho=25").catch(function () { return { itens: [], total: 0 }; }),
    ]).then(function (r) {
      body().innerHTML =
        '<div class="m15-aviso">Importação real é feita pela CLI com dry-run padrão: ' +
        "<code>python -m app.cli importar --tipo leads --arquivo arquivo.csv</code> " +
        "(só grava com <code>--execute</code>). Planilhas e CSVs antigos permanecem como fonte histórica e rollback.</div>" +
        '<div class="m15-panel"><h3>Lotes de importação (' + r[0].total + ")</h3>" +
        table(
          ["Fonte", "Arquivo", "SHA-256", "Modo", "Total", "Válidas", "Rejeitadas", "Ambíguas"],
          r[0].itens.map(function (b) {
            return [
              esc(b.source_type), esc(b.source_name),
              "<code>" + esc(b.sha256.slice(0, 12)) + "…</code>",
              pill(b.modo), String(b.total_rows), String(b.valid_rows),
              String(b.rejected_rows), String(b.ambiguous_rows),
            ];
          }),
          "Nenhum lote executado ainda."
        ) + "</div>" +
        '<div class="m15-panel"><h3>Candidatos de identidade pendentes (' + r[1].total + ")</h3>" +
        '<p class="m15-muted">Telefone/nome iguais geram CANDIDATOS — nunca fusão automática. Decisão é humana.</p>' +
        table(
          ["Motivo", "Pessoa", "Candidata", "Origem", "Status"],
          r[1].itens.map(function (c) {
            var det = c.detalhes || {};
            return [
              pill(c.motivo),
              esc(det.person_public_code || c.person_id),
              esc(det.candidate_public_code || c.candidate_person_id || "—"),
              esc(c.origem), pill(c.status),
            ];
          }),
          "Nenhuma ambiguidade pendente."
        ) + "</div>";
    });
  };

  loaders.auditoria = function () {
    return api("/auditoria?tamanho=50").then(function (data) {
      body().innerHTML =
        '<p class="m15-muted">Trilha append-only, sem PII nos detalhes. Consulta restrita a gestor/admin.</p>' +
        table(
          ["Quando (local)", "Ação", "Entidade", "Request", "Detalhes"],
          data.itens.map(function (a) {
            return [
              esc((a.ts_local || "").replace("T", " ").slice(0, 19)),
              "<strong>" + esc(a.acao) + "</strong>",
              esc(a.entidade || "—"),
              "<code>" + esc(a.request_id || "—") + "</code>",
              esc(a.detalhes ? JSON.stringify(a.detalhes) : "—"),
            ];
          }),
          "Nenhum evento de auditoria ainda."
        );
    }).catch(function (err) {
      if (err.status === 403) {
        body().innerHTML = '<div class="m15-aviso">Auditoria exige papel gestor ou admin.</div>';
        return;
      }
      throw err;
    });
  };

  function render() {
    var loader = loaders[state.tab] || loaders.visao;
    body().innerHTML = '<div class="m15-empty">Carregando…</div>';
    loader().catch(showError);
  }

  function checkApi() {
    var el = document.getElementById("m15ApiStatus");
    fetch(state.apiBase + "/health").then(function (r) { return r.json(); }).then(function (h) {
      el.textContent = "Proxy/API: " + h.status + " (" + h.ambiente + ", banco " + h.banco + ")";
    }).catch(function () {
      el.textContent = "Proxy/API: indisponível — veja nucleo-m15/README.md";
    });
  }

  // ------------------------------------------------------------ bootstrap

  function activate(config) {
    // Fail-closed: configuração pública nunca pode transformar o frontend em
    // cliente de URL absoluta/outro host. Desenvolvimento troca só o upstream
    // server-side; a rota do navegador permanece a mesma.
    if (config && config.api_base === "/painel-soprolife/api/m15") {
      state.apiBase = config.api_base;
    }

    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "css/m15.css";
    document.head.appendChild(link);

    var nav = document.querySelector(".sidebar .nav");
    if (!nav) return;
    var label = document.createElement("div");
    label.className = "nav-group-label";
    label.textContent = "Experimental";
    nav.appendChild(label);
    nav.appendChild(navButton());

    var main = document.querySelector("main") || document.querySelector(".content");
    var anySection = document.querySelector(".section");
    var container = anySection ? anySection.parentElement : main;
    if (!container) return;
    container.appendChild(buildSection());

    var tokenInput = document.getElementById("m15Token");
    document.getElementById("m15TokenSave").addEventListener("click", function () {
      // token vive só na variável de módulo — nunca em storage persistente
      state.token = tokenInput.value.trim();
      tokenInput.value = "";
      render();
    });
    // migração de segurança: limpa qualquer token persistido por versão antiga
    try { localStorage.removeItem("soproM15Token"); } catch (e) { /* noop */ }
    document.getElementById("m15Tabs").addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-m15-tab]");
      if (!btn) return;
      state.tab = btn.getAttribute("data-m15-tab");
      document.querySelectorAll(".m15-tab").forEach(function (t) { t.classList.remove("active"); });
      btn.classList.add("active");
      render();
    });
    checkApi();
  }

  function boot() {
    fetch(CONFIG_URL).then(function (r) { return r.json(); }).catch(function () { return {}; })
      .then(function (config) {
        var enabled = (config && config.enabled === true) ||
          localStorage.getItem("soproM15") === "on";
        if (!enabled) return; // flag desligada: painel permanece intocado
        activate(config);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
