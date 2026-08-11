/* Autenticação da casca mínima (M25.23).
 *
 * Único JavaScript que roda enquanto NÃO existe sessão. Faz uma coisa só:
 * troca e-mail+senha por um cookie de sessão e recarrega o painel.
 *
 * O que ele deliberadamente NÃO faz:
 *   - não busca dado nenhum (nada de data/*.json, nada de /lancamentos);
 *   - não monta nem toca em DOM administrativo — ele não existe nesta página;
 *   - não guarda token, papel ou identidade em localStorage/sessionStorage.
 *     O cookie é HttpOnly e o papel é sempre relido do servidor depois.
 *
 * Contexto seguro: reaproveita js/m15-security.js. Em origem HTTP remota o
 * formulário é desativado antes de qualquer digitação — credencial não viaja
 * sem TLS, nem para a tailnet.
 */
(function () {
  "use strict";

  var API = "/painel-soprolife/api/m15";
  var DESTINO = "/painel-soprolife/";

  var form = document.getElementById("loginForm");
  var erroEl = document.getElementById("loginErro");
  var btn = document.getElementById("entrarBtn");
  var emailEl = document.getElementById("email");
  var senhaEl = document.getElementById("password");
  var manterEl = document.getElementById("manterConectado");

  function mostrarErro(texto) {
    erroEl.textContent = texto;
    erroEl.hidden = false;
  }

  function limparErro() {
    erroEl.textContent = "";
    erroEl.hidden = true;
  }

  // Fail-closed: se a guarda não carregou, trata como origem insegura.
  var guarda = window.SoproM15Security || null;
  var contexto = guarda && typeof guarda.classify === "function"
    ? guarda.classify(window.location)
    : { secure: false, motivo: "guarda de segurança indisponível" };

  if (!contexto.secure) {
    mostrarErro(
      (guarda && guarda.MENSAGEM_BLOQUEIO) ||
      "Esta página foi aberta por uma origem insegura, então o login está " +
      "desativado para proteger suas credenciais. Abra o endereço HTTPS do painel."
    );
    emailEl.disabled = true;
    senhaEl.disabled = true;
    if (manterEl) manterEl.disabled = true;
    btn.disabled = true;
    return;
  }

  form.addEventListener("submit", function (evento) {
    evento.preventDefault();
    limparErro();

    var email = String(emailEl.value || "").trim();
    var senha = String(senhaEl.value || "");
    if (!email || !senha) {
      mostrarErro("Informe e-mail e senha.");
      return;
    }

    btn.disabled = true;
    btn.textContent = "Entrando…";

    fetch(API + "/auth/token", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      // same-origin: o cookie de sessão emitido pela API precisa ser aceito.
      credentials: "same-origin",
      body: JSON.stringify({
        email: email,
        password: senha,
        manter_conectado: !!(manterEl && manterEl.checked),
      }),
    })
      .then(function (resposta) {
        return resposta.text().then(function (bruto) {
          var corpo = null;
          try { corpo = bruto ? JSON.parse(bruto) : null; } catch (e) { corpo = null; }
          return { status: resposta.status, ok: resposta.ok, corpo: corpo };
        });
      })
      .then(function (r) {
        if (r.ok) {
          // Recarrega pelo servidor: é ele que decide, agora com o cookie na
          // mão, se entrega o Command Center. Nada é montado aqui.
          window.location.replace(DESTINO);
          return;
        }
        restaurarBotao();
        if (r.status === 401) {
          // Mensagem única para e-mail inexistente e senha errada: distinguir
          // os dois enumeraria contas válidas.
          mostrarErro("E-mail ou senha incorretos.");
          senhaEl.value = "";
          senhaEl.focus();
          return;
        }
        if (r.status === 429) {
          mostrarErro("Muitas tentativas. Aguarde alguns minutos e tente novamente.");
          return;
        }
        var detalhe = r.corpo && (r.corpo.detail || (r.corpo.erro && r.corpo.erro.mensagem));
        mostrarErro(
          typeof detalhe === "string" && detalhe
            ? detalhe
            : "Não foi possível entrar agora (HTTP " + r.status + "). Tente novamente."
        );
      })
      .catch(function () {
        restaurarBotao();
        mostrarErro("Sem resposta do servidor. Verifique a conexão e tente de novo.");
      });
  });

  function restaurarBotao() {
    btn.disabled = false;
    btn.textContent = "Entrar";
  }
})();
