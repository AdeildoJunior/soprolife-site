#!/usr/bin/env node
// SoproLife — M68: identificação assistida (CPF → Nascimento → Nome).
//
// Executa o controlador de verdade (js/identificacao-assistida.js é módulo
// puro) com relógio e API falsos. Offline, sem browser, só CPF sintético.
//
//   A) validações locais (CPF, nascimento) sem nenhuma chamada;
//   B) ordem das portas: busca local ANTES da consulta oficial;
//   C) anti-custo: debounce, mesmo par nunca repete, cache da sessão;
//   D) invalidação: CPF/nascimento trocados, paciente existente escolhido;
//   E) resultados oficiais e mensagens da missão;
//   F) a tela: ordem dos campos, selos, script carregado, nada direto ao SERPRO.
//
// Uso:  node painel-soprolife/scripts/test-m68-identificacao-assistida.js
// Exit: 0 = todos passaram | 1 = houve falha.

"use strict";

const fs = require("fs");
const path = require("path");

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

const RAIZ = path.resolve(__dirname, "..");
const ler = (...p) => fs.readFileSync(path.join(RAIZ, ...p), "utf8");
const IA = require(path.join(RAIZ, "js", "identificacao-assistida.js"));

function cpf(base9) {
  const dv = (d, peso) => {
    let s = 0;
    for (let i = 0; i < d.length; i++) s += Number(d[i]) * (peso - i);
    const r = (s * 10) % 11;
    return r === 10 ? 0 : r;
  };
  const d1 = dv(base9, 10);
  return base9 + d1 + dv(base9 + d1, 11);
}
const CPF_NOVO = cpf("284017395");
const CPF_OUTRO = cpf("590318462");
const CPF_EXISTE = cpf("713204859");
const CPF_INVALIDO = CPF_NOVO.slice(0, 10) + String((Number(CPF_NOVO[10]) + 1) % 10);
const NASC = "1970-11-14";
const HOJE = "2026-09-24";
const PESSOA = { id: "p1", public_code: "PES-000123", nome_completo: "Paciente 001", data_nascimento: "1980-02-03" };

// ── ambiente falso ─────────────────────────────────────────────────────────
function ambiente(respostaOficial) {
  const timers = [];
  const chamadas = [];
  const abortados = [];
  const pendentes = [];
  let manual = false;
  const env = {
    chamadas, abortados, estados: [],
    set manual(v) { manual = v; },
    api(pathApi, o) {
      const corpo = JSON.parse(o.body);
      chamadas.push({ path: pathApi, corpo, method: o.method });
      if (o.signal) o.signal.addEventListener("abort", () => abortados.push(pathApi));
      if (pathApi === "/pessoas/busca-cpf") {
        const achou = corpo.cpf === CPF_EXISTE;
        return Promise.resolve({ encontrada: achou, pessoa: achou ? PESSOA : null });
      }
      if (manual) {
        return new Promise((resolve, reject) => pendentes.push({ resolve, reject }));
      }
      const r = typeof respostaOficial === "function" ? respostaOficial(corpo) : respostaOficial;
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r);
    },
    pendentes,
    async correr() {
      while (timers.length) timers.shift().fn();
      for (let i = 0; i < 5; i++) await Promise.resolve();
      await new Promise((r) => setImmediate(r));
    },
    timers,
  };
  env.ctl = IA.criar({
    api: env.api,
    aoEstado: (e) => env.estados.push(e),
    agendar: (fn) => { const t = { fn }; timers.push(t); return t; },
    cancelar: (t) => { const i = timers.indexOf(t); if (i !== -1) timers.splice(i, 1); },
    hoje: () => HOJE,
  });
  env.ultimo = () => env.estados[env.estados.length - 1] || {};
  env.oficiais = () => chamadas.filter((c) => c.path === "/pessoas/identificacao-assistida");
  env.locais = () => chamadas.filter((c) => c.path === "/pessoas/busca-cpf");
  return env;
}

const CONFERE = {
  resultado: "confere", mensagem: IA.MENSAGENS.confere, nome_oficial: "MARINA COSTA RIBEIRO",
  nome_social: null, situacao: { codigo: "0", descricao: "Regular" }, comprovante: "v1.9.a.b",
};

(async () => {
  console.log("\nA) Validações locais");
  caso("CPF sintético válido", IA.cpfValido(CPF_NOVO));
  caso("CPF com verificador errado é inválido", !IA.cpfValido(CPF_INVALIDO));
  caso("sequência repetida é inválida", !IA.cpfValido("11111111111"));
  caso("nascimento ISO válido", IA.nascimentoValido(NASC, HOJE));
  caso("nascimento futuro recusado", !IA.nascimentoValido("2030-01-01", HOJE));
  caso("nascimento impossível recusado", !IA.nascimentoValido("1990-02-30", HOJE));
  caso("nascimento parcial recusado", !IA.nascimentoValido("1990-02", HOJE));

  {
    const e = ambiente(CONFERE);
    e.ctl.atualizar(CPF_INVALIDO, NASC);
    await e.correr();
    caso("3. CPF inválido → mensagem, nenhuma busca local nem oficial",
      e.ultimo().fase === "cpf_invalido" && e.ultimo().mensagem === "CPF inválido." && e.chamadas.length === 0);
    e.ctl.atualizar(CPF_NOVO.slice(0, 7), "");
    await e.correr();
    caso("CPF incompleto → nada sai", e.chamadas.length === 0 && e.ultimo().fase === "digitando");
  }

  console.log("\nB) Busca local antes da consulta oficial");
  {
    const e = ambiente(CONFERE);
    e.ctl.atualizar(CPF_EXISTE, NASC);
    await e.correr();
    caso("4. CPF existente → 'existente' com a pessoa mínima", e.ultimo().fase === "existente" &&
      e.ultimo().pessoa.public_code === "PES-000123");
    caso("4. CPF existente → SERPRO nunca chamado", e.oficiais().length === 0 && e.locais().length === 1);
    caso("busca local é POST com CPF no corpo (nunca na URL)",
      e.locais()[0].method === "POST" && e.locais()[0].corpo.cpf === CPF_EXISTE && !/\d{11}/.test(e.locais()[0].path));
    e.ctl.atualizar(CPF_EXISTE, "1970-11-15");
    await e.correr();
    caso("mudar o nascimento de um CPF já cadastrado não reabre nada",
      e.ultimo().fase === "existente" && e.chamadas.length === 1);
  }
  {
    const e = ambiente(CONFERE);
    e.ctl.atualizar(CPF_NOVO, "");
    await e.correr();
    caso("5. CPF novo sem nascimento → orientação, SERPRO não chamado",
      e.ultimo().fase === "aguardando_nascimento" &&
      e.ultimo().mensagem === "CPF válido — informe a data de nascimento para consultar o cadastro oficial." &&
      e.oficiais().length === 0);
    e.ctl.atualizar(CPF_NOVO, "1990-02");
    await e.correr();
    caso("nascimento parcial → ainda sem consulta", e.oficiais().length === 0);
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("CPF + nascimento → uma consulta oficial, com 'Consultando cadastro oficial…' antes",
      e.oficiais().length === 1 &&
      e.estados.some((s) => s.fase === "consultando" && s.mensagem === "Consultando cadastro oficial…"));
    caso("busca local do mesmo CPF reaproveitada (1 só)", e.locais().length === 1);
    caso("payload interno mínimo {cpf, data_nascimento ISO}",
      JSON.stringify(e.oficiais()[0].corpo) === JSON.stringify({ cpf: CPF_NOVO, data_nascimento: NASC }));
    caso("7. sucesso → estado 'confirmado' com nome oficial",
      e.ultimo().fase === "confirmado" && e.ultimo().nomeOficial === "MARINA COSTA RIBEIRO" &&
      e.ultimo().mensagem === "Nome confirmado na Receita Federal via SERPRO.");
    caso("comprovante disponível só para o par da tela",
      e.ctl.comprovantePara(CPF_NOVO, NASC) === "v1.9.a.b" && e.ctl.comprovantePara(CPF_OUTRO, NASC) === null);
    caso("nome editado é detectado (sem bloquear)",
      e.ctl.nomeAlterado("Marina C. Ribeiro") && !e.ctl.nomeAlterado("marina costa ribeiro"));
  }

  console.log("\nC) Anti-custo");
  {
    const e = ambiente(CONFERE);
    for (const parcial of ["2", "28", "284", CPF_NOVO.slice(0, 10), CPF_NOVO]) e.ctl.atualizar(parcial, NASC);
    await e.correr();
    caso("17. digitação rápida → uma consulta só", e.oficiais().length === 1);
    for (let i = 0; i < 5; i++) e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("17. mesmo par repetido → nenhuma consulta nova", e.oficiais().length === 1);
    e.ctl.atualizar(CPF_NOVO, "1970-11-15");
    await e.correr();
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("voltar a um par já consultado → cache da sessão (sem nova chamada)",
      e.oficiais().length === 2 && e.ultimo().fase === "confirmado");
    caso("debounce configurado (≥ 500 ms)", IA.DEBOUNCE_MS >= 500);
  }

  console.log("\nD) Invalidação");
  {
    const e = ambiente(CONFERE);
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    e.ctl.atualizar(CPF_OUTRO, NASC);
    caso("18. trocar CPF invalida o resultado anterior",
      e.ctl.comprovantePara(CPF_NOVO, NASC) === null && e.ctl.nomeOficial() === null);
    caso("18. a tela sai da confirmação na hora (não só depois do debounce)",
      e.ultimo().fase === "pendente");
    await e.correr();
    e.ctl.atualizar(CPF_OUTRO, "1971-01-01");
    caso("19. trocar nascimento invalida o resultado anterior",
      e.ctl.comprovantePara(CPF_OUTRO, NASC) === null && e.ctl.nomeOficial() === null);
  }
  {
    const e = ambiente(CONFERE);
    e.manual = true;
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("consulta em voo", e.oficiais().length === 1 && e.pendentes.length === 1);
    e.ctl.cancelar();
    caso("20. escolher paciente existente aborta a consulta pendente",
      e.abortados.includes("/pessoas/identificacao-assistida"));
    e.pendentes[0].resolve(CONFERE);
    await e.correr();
    caso("20. resposta atrasada é ignorada", e.ultimo().fase === "cancelado" && e.ctl.nomeOficial() === null);
  }
  {
    const e = ambiente(CONFERE);
    e.manual = true;
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    e.ctl.atualizar(CPF_NOVO, "1970-11-15");
    e.pendentes[0].resolve(CONFERE);
    await e.correr();
    caso("resposta de um par antigo nunca preenche o par novo",
      e.ctl.comprovantePara(CPF_NOVO, "1970-11-15") === null &&
      !e.estados.slice(-2).some((s) => s.fase === "confirmado"));
  }
  {
    const e = ambiente(CONFERE);
    e.ctl.atualizar(CPF_NOVO, "");
    e.ctl.cancelar();
    await e.correr();
    caso("cancelar antes do debounce → nada sai", e.chamadas.length === 0);
  }

  console.log("\nE) Resultados oficiais");
  const casos = [
    ["6. integração desligada", { resultado: "nao_configurada", mensagem: "Consulta oficial indisponível — preencha o nome manualmente." },
      "nao_configurada", "Consulta oficial indisponível — preencha o nome manualmente."],
    ["9. não correspondência", { resultado: "nao_confere", mensagem: "CPF e data de nascimento não conferem no cadastro oficial." },
      "nao_confere", "CPF e data de nascimento não conferem no cadastro oficial."],
    ["11. indisponível", { resultado: "indisponivel", mensagem: IA.MENSAGENS.indisponivel },
      "indisponivel", "Não foi possível consultar o cadastro oficial agora. Você pode preencher o nome manualmente e continuar."],
    ["erro de rede/429 → indisponível", Object.assign(new Error("HTTP 429"), { code: "limite_consultas" }),
      "indisponivel", IA.MENSAGENS.indisponivel],
  ];
  for (const [nome, resp, fase, msg] of casos) {
    const e = ambiente(resp);
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso(`${nome} → '${fase}' com a mensagem da missão`,
      e.ultimo().fase === fase && e.ultimo().mensagem === msg && e.ctl.comprovantePara(CPF_NOVO, NASC) === null,
      JSON.stringify(e.ultimo()));
  }
  {
    const e = ambiente(Object.assign({}, CONFERE, { nome_social: "MARINA SOCIAL" }));
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("8. nome social vem separado do nome civil",
      e.ultimo().nomeOficial === "MARINA COSTA RIBEIRO" && e.ultimo().nomeSocial === "MARINA SOCIAL");
  }
  {
    const e = ambiente({ resultado: "cpf_ja_cadastrado", pessoa: PESSOA });
    e.ctl.atualizar(CPF_NOVO, NASC);
    await e.correr();
    caso("corrida: servidor diz 'já cadastrado' → aviso de paciente existente", e.ultimo().fase === "existente");
  }

  console.log("\nF) A tela");
  const central = ler("js", "central-cadastros.js");
  const modulo = ler("js", "identificacao-assistida.js");
  const index = ler("index.html");
  const semComentarios = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const fn = (semComentarios(central).match(/function camposPessoaNova[\s\S]*?\n  \}\n/) || [""])[0];
  const blocoIdent = (fn.match(/if \(!ident\)[\s\S]*$/) || [""])[0];
  const pos = (s) => blocoIdent.indexOf(s);
  caso("1. ordem no Novo atendimento: CPF → Nascimento → Nome → WhatsApp → E-mail → Sexo → Consentimento",
    (() => {
      const retornoIdent = blocoIdent.slice(blocoIdent.indexOf("return `"));
      const ordem = ["campo.cpf", "campo.nasc", "campo.nome", "campo.fone", "campo.email", "campo.sexo", "campo.consent"]
        .map((c) => retornoIdent.indexOf(c));
      return ordem.every((v, i) => v !== -1 && (i === 0 || v > ordem[i - 1]));
    })());
  caso("CPF e Nascimento no mesmo grupo, com a nota da consulta oficial",
    /cad-ident-chave[\s\S]*?cad-ident-grid">\$\{campo\.cpf\}\$\{campo\.nasc\}/.test(fn) &&
    /Usados para consulta oficial de identificação/.test(fn));
  caso("Lead mantém a ordem anterior (Nome primeiro)",
    /if \(!ident\) \{\s*return campo\.nome \+ campo\.fone \+ campo\.nasc/.test(fn));
  caso("Novo atendimento liga a identificação", /personPickerHtml\("cadAtP", \{ nfse: true, identificacao: true \}\)/.test(central));
  const opcoes = (rotulo) => { const i = fn.indexOf(`fld("${rotulo}"`); return i === -1 ? "" : fn.slice(i, fn.indexOf("`,", i)); };
  caso("Nascimento sem selo NFS-e", opcoes("Nascimento") !== "" && !/nfse/.test(opcoes("Nascimento")));
  caso("CPF e Nome continuam com selo NFS-e", /nfse: !!opts\.nfse/.test(opcoes("CPF")) && /nfse: !!opts\.nfse/.test(opcoes("Nome completo")));
  caso("status acessível (role=status, aria-live)", /IdentStatus" role="status" aria-live="polite"/.test(fn));
  caso("index carrega o módulo antes da Central, com cache-buster novo",
    /identificacao-assistida\.js\?v=2026092401[\s\S]*central-cadastros\.js\?v=2026092401/.test(index));
  caso("central.css com cache-buster novo", /link\.href = href \+ "\?v=2026092401"/.test(central));
  caso("navegador nunca fala com o SERPRO (sem host/URL/credencial no front)",
    !/serpro\.gov\.br|apiserpro|consumer|Bearer/i.test(modulo) &&
    !/serpro\.gov\.br|apiserpro|consumer_?secret/i.test(central));
  caso("nenhuma rota com CPF/nascimento na URL",
    !/busca-cpf\?|identificacao-assistida\?|\/pessoas\/busca-cpf\/\$\{|encodeURIComponent\(cpf/.test(modulo + central));
  caso("o módulo não salva nada (só as duas rotas de leitura)",
    (modulo.match(/"\/[a-z\-\/]+"/g) || []).every((r) => r === '"/pessoas/busca-cpf"' || r === '"/pessoas/identificacao-assistida"'));
  caso("25. salvar não depende da consulta oficial (submit não consulta o controlador)",
    !/ctl\.|atualizar\(|estado\(\)/.test(
      (semComentarios(central).match(/wireSubmit\(form, \(\) => \{\n\s+const tipo = tipoAtual\(\);[\s\S]*?\n      \}, \(res\)/) || ["ctl."])[0]));
  caso("nome digitado pelo operador nunca é sobrescrito (só vazio ou o próprio autofill)",
    /const preencher = !atual \|\| atual === autofill;/.test(central));
  caso("autofill dispara 'input' (a prontidão NFS-e reage)",
    /el\.dispatchEvent\(new Event\("input", \{ bubbles: true \}\)\)/.test(central));
  caso("M67 preservada: o módulo de prontidão não foi alterado para aceitar nascimento",
    !/nascimento/i.test(semComentarios(ler("js", "nfse-prontidao.js")).replace(/lerData/g, "")));

  console.log(`\n${falhas === 0 ? "OK" : "FALHOU"} — ${falhas} falha(s)`);
  process.exit(falhas === 0 ? 0 : 1);
})().catch((err) => { console.error(err); process.exit(1); });
