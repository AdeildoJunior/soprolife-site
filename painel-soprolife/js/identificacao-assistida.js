/* M68 — identificação assistida no "Novo atendimento": CPF → Nascimento → Nome.
 *
 * Controlador SEM DOM (a Central só desenha o estado que ele emite), para que
 * o comportamento que custa dinheiro — quando uma consulta ao SERPRO sai —
 * seja testável no Node sem navegador.
 *
 * Portas, na ordem, e cada uma só abre se a anterior deixou passar:
 *   1. CPF com 11 dígitos e verificadores corretos (senão: "CPF inválido.");
 *   2. CPF e nascimento estáveis por DEBOUNCE_MS (nada sai durante a digitação);
 *   3. busca EXATA no banco local (POST /pessoas/busca-cpf) — achou, para aqui;
 *   4. nascimento válido (senão: orientação para informá-lo);
 *   5. resultado do mesmo par já obtido nesta sessão → reaproveitado;
 *   6. só então POST /pessoas/identificacao-assistida (o servidor decide se o
 *      SERPRO está ligado; o navegador nunca fala com o SERPRO).
 *
 * Trocar CPF ou nascimento invalida o resultado anterior e aborta o que
 * estiver em voo; escolher um paciente existente também.
 */
(function (global) {
  "use strict";

  var DEBOUNCE_MS = 700;

  var MSG = {
    cpf_invalido: "CPF inválido.",
    aguardando_nascimento: "CPF válido — informe a data de nascimento para consultar o cadastro oficial.",
    consultando: "Consultando cadastro oficial…",
    verificando: "Verificando se o CPF já está cadastrado…",
    erro_local: "Não foi possível verificar agora se o CPF já está cadastrado. Você pode continuar o cadastro manualmente.",
    indisponivel: "Não foi possível consultar o cadastro oficial agora. Você pode preencher o nome manualmente e continuar.",
    nao_configurada: "Consulta oficial indisponível — preencha o nome manualmente.",
    confere: "Nome confirmado na Receita Federal via SERPRO.",
    nao_confere: "CPF e data de nascimento não conferem no cadastro oficial.",
    nome_alterado: "Nome alterado após a confirmação oficial.",
  };

  function cpfDigitos(valor) {
    return String(valor == null ? "" : valor).replace(/\D/g, "").slice(0, 11);
  }

  // Mesma regra de services/cpf.py::cpf_valido (verificadores + repetidos).
  function cpfValido(valor) {
    var d = cpfDigitos(valor);
    if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false;
    function dv(n) {
      var soma = 0;
      for (var i = 0; i < n; i++) soma += Number(d[i]) * (n + 1 - i);
      var resto = (soma * 10) % 11;
      return resto === 10 ? 0 : resto;
    }
    return dv(9) === Number(d[9]) && dv(10) === Number(d[10]);
  }

  // ISO completo, data real, 1900..hoje. Datas parciais não consultam nada.
  function nascimentoValido(iso, hojeIso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || "").trim());
    if (!m) return false;
    var a = Number(m[1]), mes = Number(m[2]), dia = Number(m[3]);
    var dt = new Date(Date.UTC(a, mes - 1, dia));
    if (dt.getUTCFullYear() !== a || dt.getUTCMonth() !== mes - 1 || dt.getUTCDate() !== dia) return false;
    if (a < 1900) return false;
    return !hojeIso || iso <= hojeIso;
  }

  function normalizarNome(nome) {
    return String(nome || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ").trim().toUpperCase();
  }

  function hojeLocal() {
    var d = new Date();
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  /* opts:
   *   api(path, {method, body, signal}) → Promise  (obrigatório)
   *   aoEstado(estado)                             (desenho)
   *   agendar(fn, ms) / cancelar(id)               (padrão: setTimeout)
   *   hoje() → "AAAA-MM-DD"
   */
  function criar(opts) {
    var agendar = opts.agendar || function (fn, ms) { return setTimeout(fn, ms); };
    var cancelar = opts.cancelar || function (id) { clearTimeout(id); };
    var hoje = opts.hoje || hojeLocal;
    var s = {
      cpf: "", nasc: "", timer: null, seq: 0, abortar: null,
      locais: {},      // cpf → pessoa mínima | null (resultado da busca local)
      oficiais: {},    // cpf|nasc → resposta do servidor (só nesta página)
      confirmado: null, // {cpf, nasc, nome, comprovante}
      estado: { fase: "vazio" },
    };

    function emitir(estado) {
      s.estado = estado;
      if (opts.aoEstado) opts.aoEstado(estado);
    }

    function invalidar() {
      s.seq++;
      if (s.timer !== null) { cancelar(s.timer); s.timer = null; }
      if (s.abortar) { try { s.abortar(); } catch (e) { /* já encerrada */ } s.abortar = null; }
      s.confirmado = null;
    }

    function chamar(path, corpo) {
      var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      s.abortar = ctl ? function () { ctl.abort(); } : null;
      var o = { method: "POST", body: JSON.stringify(corpo) };
      if (ctl) o.signal = ctl.signal;
      return opts.api(path, o);
    }

    function aplicarOficial(cpf, nasc, r) {
      if (r.resultado === "cpf_ja_cadastrado" && r.pessoa) {
        s.locais[cpf] = r.pessoa;
        emitir({ fase: "existente", pessoa: r.pessoa });
        return;
      }
      if (r.resultado === "confere" && r.nome_oficial) {
        s.confirmado = { cpf: cpf, nasc: nasc, nome: r.nome_oficial, comprovante: r.comprovante || null };
        emitir({
          fase: "confirmado", mensagem: MSG.confere, nomeOficial: r.nome_oficial,
          nomeSocial: r.nome_social || null, situacao: r.situacao || null,
        });
        return;
      }
      var fase = (MSG[r.resultado] || r.mensagem) ? r.resultado : "indisponivel";
      emitir({ fase: fase, mensagem: r.mensagem || MSG[fase] || MSG.indisponivel });
    }

    function executar() {
      s.timer = null;
      var seq = s.seq, cpf = s.cpf, nasc = s.nasc;
      var vigente = function () { return seq === s.seq; };

      var local = Object.prototype.hasOwnProperty.call(s.locais, cpf)
        ? Promise.resolve(s.locais[cpf])
        : (emitir({ fase: "verificando", mensagem: MSG.verificando }),
          chamar("/pessoas/busca-cpf", { cpf: cpf }).then(function (r) {
            var p = r && r.encontrada ? r.pessoa : null;
            s.locais[cpf] = p;
            return p;
          }));

      return local.then(function (pessoa) {
        if (!vigente()) return;
        if (pessoa) { emitir({ fase: "existente", pessoa: pessoa }); return; }
        if (!nascimentoValido(nasc, hoje())) {
          emitir({ fase: "aguardando_nascimento", mensagem: MSG.aguardando_nascimento });
          return;
        }
        var chave = cpf + "|" + nasc;
        if (s.oficiais[chave]) { aplicarOficial(cpf, nasc, s.oficiais[chave]); return; }
        emitir({ fase: "consultando", mensagem: MSG.consultando });
        return chamar("/pessoas/identificacao-assistida", { cpf: cpf, data_nascimento: nasc })
          .then(function (r) {
            if (!vigente()) return;
            s.oficiais[chave] = r;
            aplicarOficial(cpf, nasc, r);
          }, function (err) {
            if (!vigente() || (err && err.name === "AbortError")) return;
            // Falha nossa ou limite: o cadastro manual segue livre. Não entra
            // no cache — mas também não repete sozinho: só uma mudança de
            // CPF/nascimento dispara outra tentativa.
            emitir({ fase: "indisponivel", mensagem: MSG.indisponivel });
          });
      }, function (err) {
        if (!vigente() || (err && err.name === "AbortError")) return;
        if (err && (err.code === "cpf_invalido" || err.code === "cpf_formato_invalido")) {
          emitir({ fase: "cpf_invalido", mensagem: MSG.cpf_invalido });
          return;
        }
        emitir({ fase: "erro_local", mensagem: MSG.erro_local });
      });
    }

    /* Chamado a cada mudança do formulário; só age quando o PAR mudou. */
    function atualizar(cpfBruto, nascIso) {
      var cpf = cpfDigitos(cpfBruto);
      var nasc = String(nascIso || "").trim();
      if (cpf === s.cpf && nasc === s.nasc) return false;
      var soNascMudou = cpf === s.cpf;
      s.cpf = cpf; s.nasc = nasc;
      invalidar();
      if (!cpf) { emitir({ fase: "vazio" }); return true; }
      if (cpf.length < 11) { emitir({ fase: "digitando" }); return true; }
      if (!cpfValido(cpf)) { emitir({ fase: "cpf_invalido", mensagem: MSG.cpf_invalido }); return true; }
      // CPF já sabidamente cadastrado: mudar só o nascimento não reabre nada.
      if (soNascMudou && s.locais[cpf]) { emitir({ fase: "existente", pessoa: s.locais[cpf] }); return true; }
      // Par novo: o resultado anterior some JÁ (não depois do debounce), para
      // a tela nunca mostrar a confirmação de um par que não está mais nela.
      emitir({ fase: "pendente" });
      s.timer = agendar(executar, DEBOUNCE_MS);
      return true;
    }

    return {
      atualizar: atualizar,
      /* "Usar este paciente": nada pendente sobrevive. */
      cancelar: function () { invalidar(); emitir({ fase: "cancelado" }); },
      reset: function () { invalidar(); s.cpf = ""; s.nasc = ""; emitir({ fase: "vazio" }); },
      estado: function () { return s.estado; },
      /* Comprovante só se ainda vale para o par que está na tela. */
      comprovantePara: function (cpfBruto, nascIso) {
        var c = s.confirmado;
        return c && c.cpf === cpfDigitos(cpfBruto) && c.nasc === String(nascIso || "").trim()
          ? c.comprovante : null;
      },
      nomeOficial: function () { return s.confirmado ? s.confirmado.nome : null; },
      nomeAlterado: function (nome) {
        return !!s.confirmado && normalizarNome(nome) !== normalizarNome(s.confirmado.nome);
      },
    };
  }

  var api = {
    DEBOUNCE_MS: DEBOUNCE_MS,
    MENSAGENS: MSG,
    cpfValido: cpfValido,
    nascimentoValido: nascimentoValido,
    normalizarNome: normalizarNome,
    criar: criar,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.SoproIdentificacaoAssistida = api;
})(typeof window !== "undefined" ? window : this);
