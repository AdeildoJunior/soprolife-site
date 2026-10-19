
(function () {
  "use strict";

  // A API pública mínima. É a ÚNICA origem que esta página contata, e a CSP
  // emitida pelo servidor repete a mesma lista — se alguém acrescentar um endereço aqui
  // sem acrescentar lá, o navegador bloqueia. É de propósito.
  var API = "/p/v1";

  var elEntrada = document.getElementById("etapaEntrada");
  var elResultado = document.getElementById("etapaResultado");
  var elMensagem = document.getElementById("etapaMensagem");
  var elAviso = document.getElementById("avisoEntrada");
  var campo = document.getElementById("nascimento");
  var botao = document.getElementById("botaoEntrar");

  // O token vive no FRAGMENTO. O navegador nunca o envia ao servidor: não
  // entra em log de acesso, não vai no cabeçalho Referer e não atravessa
  // proxy. Ele sai daqui uma única vez, no corpo de um POST sobre HTTPS.
  function tokenDoLink() {
    var bruto = String(window.location.hash || "").replace(/^#/, "");
    if (!bruto) return "";
    var partes = bruto.split("&");
    for (var i = 0; i < partes.length; i++) {
      var par = partes[i].split("=");
      if (par[0] === "t" && par[1]) return decodeURIComponent(par[1]);
    }
    return "";
  }

  function mostrar(secao) {
    [elEntrada, elResultado, elMensagem].forEach(function (el) {
      el.classList.toggle("oculto", el !== secao);
    });
  }

  function mensagemFinal(titulo, texto) {
    document.getElementById("mensagemTitulo").textContent = titulo;
    document.getElementById("mensagemTexto").textContent = texto;
    mostrar(elMensagem);
  }

  function avisar(texto) {
    elAviso.textContent = texto;
    elAviso.classList.remove("oculto");
  }

  // Máscara de data. Bloco fecha assim que enche: 09 → "09/",
  // 0904 → "09/04/", 09041978 → "09/04/1978". Quem digita a barra à mão não
  // é atrapalhado — barras repetidas colapsam em uma só.
  function mascarar(texto) {
    var digitos = String(texto).replace(/\D/g, "").slice(0, 8);
    if (digitos.length <= 2) return digitos;
    if (digitos.length <= 4) return digitos.slice(0, 2) + "/" + digitos.slice(2);
    return digitos.slice(0, 2) + "/" + digitos.slice(2, 4) + "/" + digitos.slice(4);
  }

  campo.addEventListener("input", function () {
    var antes = campo.value;
    var depois = mascarar(antes);
    if (antes !== depois) campo.value = depois;
  });

  function paraIso(valor) {
    var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(String(valor).trim());
    if (!m) return "";
    var dia = Number(m[1]), mes = Number(m[2]), ano = Number(m[3]);
    if (mes < 1 || mes > 12 || dia < 1 || dia > 31) return "";
    if (ano < 1900 || ano > 2100) return "";
    var d = new Date(Date.UTC(ano, mes - 1, dia));
    if (d.getUTCDate() !== dia || d.getUTCMonth() !== mes - 1) return "";
    return m[3] + "-" + m[2] + "-" + m[1];
  }

  function dataBr(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    return m ? (m[3] + "/" + m[2] + "/" + m[1]) : "—";
  }

  // Uma falha de REDE e um erro do SERVIDOR não são a mesma coisa, e até a
  // M26.7 esta tela dizia que eram. Quando a API respondia 500, o navegador
  // rejeitava o `fetch` (o 500 saía sem cabeçalho de CORS) e o paciente lia
  // "verifique sua internet" — culpa no lugar errado, e o defeito de
  // servidor escondido atrás de um diagnóstico falso.
  var MSG_REDE =
    "Não foi possível conectar agora. Verifique sua internet e tente novamente.";
  var MSG_SERVIDOR =
    "Não foi possível acessar o resultado agora. Tente novamente em alguns instantes.";

  function erroDaResposta(corpo, status) {
    // 5xx é problema nosso, não do paciente. A mensagem é fixa e daqui: o
    // corpo de um erro de servidor pode ter sido escrito por um proxy no
    // caminho, e nada que venha de lá deve chegar à tela de quem espera um
    // exame.
    if (status >= 500) return MSG_SERVIDOR;
    if (corpo && corpo.erro && corpo.erro.mensagem) return corpo.erro.mensagem;
    if (status === 429) {
      return "Muitas tentativas seguidas. Aguarde alguns minutos e tente novamente.";
    }
    return "Não foi possível abrir este resultado agora. Tente novamente em alguns minutos.";
  }

  function renderizar(dados) {
    if (!dados || !dados.exame || dados.exame.tipo !== "EEG") {
      avisar("Este link não corresponde a um resultado EEG.");
      return;
    }
    document.getElementById("pacienteNome").textContent =
      (dados.paciente && dados.paciente.nome) || "";
    document.getElementById("exameTipo").textContent =
      (dados.exame && dados.exame.tipo) || "EEG";
    document.getElementById("exameData").textContent =
      dataBr(dados.exame && dados.exame.data);

    var mapa = {};
    (dados.documentos || []).forEach(function (d) { mapa[d.chave] = d; });

    // Download por navegação de topo, e nunca por blob: o nome do arquivo
    // vem do Content-Disposition do servidor. Baixar via blob devolveria um
    // nome aleatório — foi o defeito que a M25.18 pagou para descobrir.
    var laudo = mapa["laudo-final-eeg"];
    if (!laudo || !/^\/p\/v1\/eeg\/documentos\/[a-f0-9-]{36}$/.test(laudo.caminho || "")) {
      avisar("Laudo indisponível no momento.");
      return;
    }
    configurarLink("linkLaudo", laudo.caminho, laudo);

    mostrar(elResultado);
  }

  function configurarLink(id, href, doc) {
    var el = document.getElementById(id);
    if (doc && doc.disponivel) {
      el.href = href;
      el.classList.remove("oculto");
    } else {
      el.classList.add("oculto");
    }
  }

  document.getElementById("formEntrada").addEventListener("submit", function (evento) {
    evento.preventDefault();
    elAviso.classList.add("oculto");

    var token = tokenDoLink();
    if (!token) {
      avisar("Este endereço está incompleto. Abra novamente o link que você recebeu.");
      return;
    }
    var nascimento = paraIso(campo.value);
    if (!nascimento) {
      avisar("Informe a data de nascimento no formato DD/MM/AAAA.");
      return;
    }

    botao.disabled = true;
    botao.textContent = "Verificando…";

    fetch(API + "/acesso", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token, nascimento: nascimento })
    }).then(function (resposta) {
      return resposta.json().catch(function () { return null; })
        .then(function (corpo) { return { ok: resposta.ok, status: resposta.status, corpo: corpo }; });
    }).then(function (r) {
      botao.disabled = false;
      botao.textContent = "Acessar resultado";
      if (r.ok) { return r.corpo; }
      if (r.status === 410) {
        mensagemFinal("Acesso expirado", erroDaResposta(r.corpo, r.status));
        return null;
      }
      avisar(erroDaResposta(r.corpo, r.status));
      return null;
    }).catch(function () {
      // Só chega aqui quem NÃO teve resposta: DNS, TLS, cabo, avião. Um
      // status HTTP, qualquer que seja, foi tratado acima.
      botao.disabled = false;
      botao.textContent = "Acessar resultado";
      avisar(MSG_REDE);
      return null;
    }).then(function (dados) {
      // A montagem da tela fica FORA do `catch` de rede de propósito: um
      // defeito ao desenhar o resultado não pode ser anunciado ao paciente
      // como falta de internet.
      if (dados) renderizar(dados);
    });
  });

  // Cada abertura confirma o nascimento para o link atual. Uma sessão de
  // outro resultado, já aberta no mesmo navegador, não preenche esta página.
  if (!tokenDoLink()) mensagemFinal("Link incompleto", "Abra o link completo recebido da EEGLife.");
})();
