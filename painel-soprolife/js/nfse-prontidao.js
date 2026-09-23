/* M67 — Prontidão para NFS-e no cadastro de "Novo atendimento".
 *
 * ORIENTAÇÃO, NUNCA DECISÃO. Este módulo diz ao operador, enquanto ele
 * preenche, quais dados a emissão da NFS-e da espirometria vai exigir depois.
 * Ele não emite, não prepara, não chama rota fiscal nenhuma e não bloqueia o
 * salvamento. A autoridade final continua sendo o backend:
 *
 *   services/nfse.py::evaluate(...)                 — elegibilidade do fato
 *   services/nfse.py::production_fact_blockers(...) — CPF/nome/município
 *   o worker de produção / dispatch                 — reconfere tudo antes do envio
 *
 * Por que existe uma cópia das regras aqui: a tela precisa responder a cada
 * tecla, sem mandar CPF ao servidor a cada dígito. Para essa cópia não virar
 * uma "segunda verdade" que diverge em silêncio:
 *
 *   - cada regra aponta a função Python que espelha;
 *   - a lista de municípios NÃO é copiada: vem de
 *     /espirometrias/municipios-atendimento (service_location.py);
 *   - a identidade fiscal de um paciente JÁ cadastrado não é julgada aqui:
 *     vem pronta do servidor em `nfse_identidade_pendencias`, calculada pelo
 *     próprio production_fact_blockers (o CPF completo nunca chega à tela);
 *   - scripts/test-m67-nfse-prontidao.js lê os fontes Python e falha se
 *     marcadores de placeholder, limite do nome, status "realizado" ou o
 *     mapeamento modalidade → fluxo mudarem de um lado só.
 *
 * Política fiscal vigente e configuração tributária NÃO aparecem aqui: não
 * são dados do cadastro, e o operador não tem como corrigi-los nesta tela.
 *
 * Módulo puro (sem DOM): roda no navegador como window.SoproNfseProntidao e
 * no Node via module.exports, para os testes executarem as regras de verdade.
 */
(function (global) {
  "use strict";

  // recipient_identity.py::PLACEHOLDER_MARKERS — mesma ordem, mesmo texto.
  var PLACEHOLDER_MARKERS = [
    "test", "teste", "mock", "exemplo", "example", "sample", "dummy",
    "placeholder", "ficticio", "fake", "lorem", "ipsum",
    "fulano", "sicrano", "beltrano", "sintetico", "sintetica", "synthetic",
    "paciente 001", "nome do paciente", "xxx", "aaa", "zzz",
  ];
  // recipient_identity.py::MAX_NAME_LENGTH (TSNomeRazaoSocial)
  var MAX_NAME_LENGTH = 300;
  // nfse.py::PERFORMED
  var STATUS_REALIZADO = ["Realizado", "Laudo Liberado"];
  // nfse.py::evaluate — `{'residencial': 'HOME', 'cowork': 'DIRECT'}`
  var FLUXO_POR_MODALIDADE = { residencial: "HOME", cowork: "DIRECT" };
  var FLUXOS_SUPORTADOS = ["HOME", "DIRECT"];
  // nfse.py::evaluate — a receita precisa estar recebida
  var STATUS_RECEBIDO = "Recebido";

  // Tipos do formulário (central-cadastros.js::TIPOS_ATENDIMENTO).
  var TIPOS_ESPIROMETRIA_SOPROLIFE = ["espirometria_soprolife", "espirometria_consulta_soprolife"];
  var TIPO_PASTORE = "espirometria_pastore";

  // Códigos do servidor (nfse.py::RECIPIENT_FISCAL_REASONS) → frase humana.
  // Nenhum código técnico chega à tela de cadastro.
  var MSG = {
    recipient_name_missing: "Nome completo do paciente necessário para a NFS-e",
    recipient_name_not_a_full_name: "Informe nome e sobrenome — a NFS-e exige o nome completo",
    recipient_name_looks_like_placeholder: "O nome parece de teste ou exemplo — a NFS-e exige o nome real do paciente",
    recipient_name_too_long: "Nome longo demais para a NFS-e (máximo de " + MAX_NAME_LENGTH + " caracteres)",
    recipient_cpf_missing: "CPF necessário para emissão da NFS-e",
    recipient_cpf_malformed: "CPF incompleto — a NFS-e exige os 11 dígitos",
    recipient_cpf_repeated_digits: "CPF inválido (dígitos repetidos) — confira o número",
    recipient_cpf_check_digits_invalid: "CPF inválido — confira os dígitos",
    data_ausente: "Informe a data do exame",
    data_imprecisa: "Informe a data exata do exame (dia, mês e ano) — mês ou ano sozinhos não bastam para a NFS-e",
    data_invalida: "Data do exame não reconhecida — use DD/MM/AAAA",
    data_futura: "A data do exame não pode estar no futuro",
    exame_nao_realizado: "O exame precisa estar como Realizado (ou Laudo liberado) para gerar NFS-e",
    bd_indefinido: "Informe se o exame foi realizado com ou sem broncodilatador",
    municipio_ausente: "Informe o município onde o exame foi realizado",
    municipio_nao_suportado: "Município ainda não suportado para a NFS-e",
    modalidade_ausente: "Escolha a modalidade — domiciliar ou cowork/espaço SoproLife",
    modalidade_nao_suportada: "Esta modalidade não tem fluxo fiscal suportado para a NFS-e",
    valor_ausente: "Informe o valor da espirometria (maior que zero) — é ele que vai na NFS-e",
    pagamento_nao_recebido: "O pagamento precisa estar como Recebido para ficar elegível à NFS-e",
  };

  var PASTORE_EXPLICACAO = "Parceria Pastore — NFS-e não emitida por exame pela SoproLife";

  function fold(texto) {
    return String(texto).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function palavras(texto) {
    return texto.split(/[^0-9a-z]+/).filter(Boolean);
  }

  /* recipient_identity.py::assert_production_name — devolve o código do
   * primeiro problema, ou null quando o nome serve para a NFS-e. */
  function problemaNome(nome) {
    if (typeof nome !== "string") return "recipient_name_missing";
    var limpo = nome.trim();
    if (!limpo) return "recipient_name_missing";
    if (limpo.length > MAX_NAME_LENGTH) return "recipient_name_too_long";
    var folded = fold(limpo);
    var ws = palavras(folded);
    if (ws.filter(function (w) { return w.length >= 2; }).length < 2) {
      return "recipient_name_not_a_full_name";
    }
    for (var i = 0; i < PLACEHOLDER_MARKERS.length; i++) {
      var marker = PLACEHOLDER_MARKERS[i];
      var mw = palavras(marker);
      if (mw.length === 1) {
        if (ws.indexOf(mw[0]) !== -1) return "recipient_name_looks_like_placeholder";
      } else if (folded.indexOf(marker) !== -1) {
        return "recipient_name_looks_like_placeholder";
      }
    }
    return null;
  }

  // recipient_identity.py::cpf_check_digits_valid (módulo 11)
  function cpfDigitosVerificadoresOk(cpf) {
    var d = cpf.split("").map(Number);
    for (var len = 9; len <= 10; len++) {
      var total = 0;
      for (var i = 0; i < len; i++) total += d[i] * ((len + 1) - i);
      var resto = (total * 10) % 11;
      if (resto === 10) resto = 0;
      if (resto !== d[len]) return false;
    }
    return true;
  }

  /* recipient_identity.py::assert_production_cpf, com a leitura de
   * production_fact_blockers: vazio é "ausente", não "malformado". Recebe o
   * valor já tratado como a tela envia (só dígitos). */
  function problemaCpf(cpf) {
    var s = cpf == null ? "" : String(cpf);
    if (!s) return "recipient_cpf_missing";
    if (!/^\d{11}$/.test(s)) return "recipient_cpf_malformed";
    if (/^(\d)\1{10}$/.test(s)) return "recipient_cpf_repeated_digits";
    if (!cpfDigitosVerificadoresOk(s)) return "recipient_cpf_check_digits_invalid";
    return null;
  }

  // nfse.py::evaluate — fluxo derivado SÓ de tipo/parceiro/modalidade estruturados.
  function fluxoFiscal(tipo, modalidade) {
    if (tipo === TIPO_PASTORE || modalidade === "clinica_parceira") return "PASTORE";
    return FLUXO_POR_MODALIDADE[modalidade] || "UNSUPPORTED";
  }

  /* Precisão da data como o backend grava (dates.py::parse_incomplete_date).
   * O calendário entrega AAAA-MM-DD / AAAA-MM / AAAA; digitação crua segue
   * como veio. evaluate() exige precisão "dia". */
  function lerData(valor) {
    var v = String(valor == null ? "" : valor).trim();
    if (!v) return { estado: "ausente" };
    var m = v.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/) ||
      (function () {
        var b = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
        return b ? [b[0], b[3], b[2], b[1]] : null;
      })();
    if (m) {
      var y = +m[1], mo = +m[2], d = +m[3];
      var dt = new Date(Date.UTC(y, mo - 1, d));
      if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) {
        return { estado: "invalida" };
      }
      var pad = function (n) { return (n < 10 ? "0" : "") + n; };
      return { estado: "dia", iso: y + "-" + pad(mo) + "-" + pad(d) };
    }
    if (/^\d{4}(-\d{1,2})?$/.test(v) || /^\d{1,2}\/\d{4}$/.test(v)) return { estado: "parcial" };
    return { estado: "invalida" };
  }

  // Hoje no fuso de evaluate() (America/Sao_Paulo), em AAAA-MM-DD.
  function hojeSaoPaulo(agora) {
    try {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit",
      }).format(agora || new Date());
    } catch (e) {
      var n = agora || new Date();
      return n.toISOString().slice(0, 10);
    }
  }

  function valorPositivo(valor) {
    var n = Number(String(valor == null ? "" : valor).replace(",", "."));
    return isFinite(n) && n > 0;
  }

  /* Avalia o rascunho do formulário.
   *
   * rascunho = {
   *   tipo,                                   // valor do radio "tipo"
   *   pessoa: { existente: bool,
   *             nome, cpf,                    // paciente NOVO (cpf só dígitos)
   *             pendenciasServidor: [códigos] // paciente EXISTENTE (do servidor)
   *           },
   *   exame: { data, status, broncodilatador ("", "true", "false"),
   *            municipio, modalidade },
   *   financeiro: { valor, status },          // valor já normalizado ("220.00")
   * }
   * contexto = { municipios: [códigos suportados], hoje: "AAAA-MM-DD" }
   *
   * Devolve { modo: "soprolife" | "pastore" | "nao_aplicavel", completo,
   *           titulo, itens: [{ chave, rotulo, ok, faltas: [frases] }] }
   */
  function avaliar(rascunho, contexto) {
    rascunho = rascunho || {};
    contexto = contexto || {};
    var tipo = rascunho.tipo || "";
    if (tipo === TIPO_PASTORE) {
      return { modo: "pastore", completo: false, titulo: PASTORE_EXPLICACAO, itens: [] };
    }
    if (TIPOS_ESPIROMETRIA_SOPROLIFE.indexOf(tipo) === -1) {
      return { modo: "nao_aplicavel", completo: false, titulo: "", itens: [] };
    }
    var pessoa = rascunho.pessoa || {};
    var exame = rascunho.exame || {};
    var fin = rascunho.financeiro || {};
    var itens = [];

    function item(chave, rotulo, faltas) {
      itens.push({ chave: chave, rotulo: rotulo, ok: faltas.length === 0, faltas: faltas });
    }

    // A. Identidade fiscal do tomador
    var idFaltas = [];
    if (pessoa.existente) {
      (pessoa.pendenciasServidor || []).forEach(function (c) {
        idFaltas.push(MSG[c] || "Dados fiscais do paciente incompletos");
      });
    } else {
      var pn = problemaNome(pessoa.nome);
      var pc = problemaCpf(pessoa.cpf);
      if (pn) idFaltas.push(MSG[pn]);
      if (pc) idFaltas.push(MSG[pc]);
    }
    item("identidade", "Nome e CPF", idFaltas);

    // B. Serviço: data exata, não futura, exame realizado
    var sFaltas = [];
    var data = lerData(exame.data);
    if (data.estado === "ausente") sFaltas.push(MSG.data_ausente);
    else if (data.estado === "parcial") sFaltas.push(MSG.data_imprecisa);
    else if (data.estado === "invalida") sFaltas.push(MSG.data_invalida);
    else if (data.iso > (contexto.hoje || hojeSaoPaulo())) sFaltas.push(MSG.data_futura);
    if (STATUS_REALIZADO.indexOf(exame.status) === -1) sFaltas.push(MSG.exame_nao_realizado);
    item("servico", "Data e exame realizado", sFaltas);

    // C. Variante — service_description.py exige booleano real
    var bd = exame.broncodilatador;
    item("broncodilatador", "Com/sem broncodilatador",
      bd === "true" || bd === "false" || bd === true || bd === false ? [] : [MSG.bd_indefinido]);

    // D. Local da prestação — service_location.py
    var mFaltas = [];
    if (!exame.municipio) mFaltas.push(MSG.municipio_ausente);
    else if ((contexto.municipios || []).indexOf(exame.municipio) === -1) {
      mFaltas.push(MSG.municipio_nao_suportado);
    }
    item("municipio", "Município da prestação", mFaltas);

    // E. Fluxo / modalidade
    var fFaltas = [];
    var fluxo = fluxoFiscal(tipo, exame.modalidade);
    if (FLUXOS_SUPORTADOS.indexOf(fluxo) === -1) {
      fFaltas.push(exame.modalidade ? MSG.modalidade_nao_suportada : MSG.modalidade_ausente);
    }
    item("modalidade", "Modalidade fiscal suportada", fFaltas);

    // F. Financeiro — receita própria, BRL, > 0, Recebido. Data de
    // recebimento e forma de pagamento NÃO entram: evaluate() não as exige.
    var rFaltas = [];
    if (!valorPositivo(fin.valor)) rFaltas.push(MSG.valor_ausente);
    if (fin.status !== STATUS_RECEBIDO) rFaltas.push(MSG.pagamento_nao_recebido);
    item("receita", "Receita recebida", rFaltas);

    var completo = itens.every(function (i) { return i.ok; });
    return {
      modo: "soprolife",
      completo: completo,
      titulo: completo ? "Dados fiscais completos" : "Faltam dados para NFS-e",
      itens: itens,
    };
  }

  var api = {
    PLACEHOLDER_MARKERS: PLACEHOLDER_MARKERS,
    MAX_NAME_LENGTH: MAX_NAME_LENGTH,
    STATUS_REALIZADO: STATUS_REALIZADO,
    FLUXO_POR_MODALIDADE: FLUXO_POR_MODALIDADE,
    STATUS_RECEBIDO: STATUS_RECEBIDO,
    TIPOS_ESPIROMETRIA_SOPROLIFE: TIPOS_ESPIROMETRIA_SOPROLIFE,
    TIPO_PASTORE: TIPO_PASTORE,
    PASTORE_EXPLICACAO: PASTORE_EXPLICACAO,
    MENSAGENS: MSG,
    problemaNome: problemaNome,
    problemaCpf: problemaCpf,
    fluxoFiscal: fluxoFiscal,
    lerData: lerData,
    hojeSaoPaulo: hojeSaoPaulo,
    avaliar: avaliar,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.SoproNfseProntidao = api;
})(typeof window !== "undefined" ? window : this);
