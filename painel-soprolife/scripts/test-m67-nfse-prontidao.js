#!/usr/bin/env node
// SoproLife — M67: prontidão para NFS-e no "Novo atendimento".
//
// Executa as regras de verdade (js/nfse-prontidao.js é módulo puro) e confere
// a paridade com os fontes Python que elas espelham. Offline, sem browser.
//
//   A) paridade: marcadores de placeholder, limite do nome, status
//      "realizado" e modalidade → fluxo são os MESMOS do backend;
//   B) casos compartilhados com o pytest (tests/data_m67_identidade_fiscal.json);
//   C) prontidão por tipo de atendimento e por campo (itens 1–14 da missão);
//   D) a tela: selo NFS-e só nos campos fiscais, nunca nos demais, e
//      separado do asterisco de "obrigatório para salvar";
//   E) nada disso chama rota fiscal / SEFIN nem bloqueia o salvamento.
//
// Uso:  node painel-soprolife/scripts/test-m67-nfse-prontidao.js
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
const P = require(path.join(RAIZ, "js", "nfse-prontidao.js"));

const pyIdent = ler("nucleo-m15", "app", "services", "nfse_national", "recipient_identity.py");
const pyNfse = ler("nucleo-m15", "app", "services", "nfse.py");
const pyAtend = ler("nucleo-m15", "app", "routers", "attendances.py");
const central = ler("js", "central-cadastros.js");
const semComentarios = (src) => src
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");
const centralCodigo = semComentarios(central);
const moduloCodigo = semComentarios(ler("js", "nfse-prontidao.js"));

// ── A) paridade com o backend ───────────────────────────────────────────────
console.log("\nA) Paridade com os fontes Python");

const pyMarkers = (() => {
  const bloco = pyIdent.match(/PLACEHOLDER_MARKERS = \(([\s\S]*?)\)\n/);
  return bloco ? [...bloco[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : null;
})();
caso("PLACEHOLDER_MARKERS idênticos (recipient_identity.py)",
     JSON.stringify(pyMarkers) === JSON.stringify(P.PLACEHOLDER_MARKERS),
     `py=${JSON.stringify(pyMarkers)}`);
const pyMax = (pyIdent.match(/MAX_NAME_LENGTH = (\d+)/) || [])[1];
caso("MAX_NAME_LENGTH idêntico", Number(pyMax) === P.MAX_NAME_LENGTH, `py=${pyMax}`);
const pyPerformed = (pyNfse.match(/PERFORMED = \{([^}]*)\}/) || [])[1] || "";
caso("status realizado = nfse.PERFORMED",
     JSON.stringify([...pyPerformed.matchAll(/'([^']+)'/g)].map((m) => m[1]).sort()) ===
       JSON.stringify([...P.STATUS_REALIZADO].sort()));
const pyFluxo = (pyNfse.match(/\{('residencial'[^}]*)\}\.get\(exam\.modalidade/) || [])[1] || "";
const pyFluxoMap = Object.fromEntries([...pyFluxo.matchAll(/'(\w+)': '(\w+)'/g)].map((m) => [m[1], m[2]]));
caso("modalidade → fluxo = nfse.evaluate",
     JSON.stringify(pyFluxoMap) === JSON.stringify(P.FLUXO_POR_MODALIDADE),
     `py=${JSON.stringify(pyFluxoMap)}`);
caso("evaluate exige status 'Recebido' (a tela exige o mesmo)",
     /entry\.status != 'Recebido'/.test(pyNfse) && P.STATUS_RECEBIDO === "Recebido");
const modalidadesOferecidas = [...pyAtend.matchAll(/"valor": "(\w+)",\n\s+"rotulo"/g)].map((m) => m[1]);
caso("toda modalidade oferecida no fluxo SoproLife tem fluxo fiscal suportado",
     modalidadesOferecidas.length > 0 &&
       modalidadesOferecidas.filter((m) => m !== "clinica_parceira")
         .every((m) => ["HOME", "DIRECT"].includes(P.fluxoFiscal("espirometria_soprolife", m))),
     JSON.stringify(modalidadesOferecidas));

// ── B) casos compartilhados com o pytest ────────────────────────────────────
console.log("\nB) Casos compartilhados com o backend");
const CASOS = JSON.parse(ler("nucleo-m15", "tests", "data_m67_identidade_fiscal.json"));
CASOS.nomes.forEach(([nome, esperado]) => {
  const obtido = P.problemaNome(nome);
  caso(`nome ${JSON.stringify(nome.length > 30 ? nome.slice(0, 30) + "…" : nome)} → ${esperado}`,
       obtido === esperado, `obtido=${obtido}`);
});
CASOS.cpfs.forEach(([cpf, esperado]) => {
  const obtido = P.problemaCpf(cpf);
  caso(`cpf ${JSON.stringify(cpf)} → ${esperado}`, obtido === esperado, `obtido=${obtido}`);
});

// ── C) prontidão ────────────────────────────────────────────────────────────
console.log("\nC) Prontidão por tipo e por campo");
const RIO = "3304557", NITEROI = "3303302";
const CTX = { municipios: [RIO, NITEROI], hoje: "2026-09-23" };
function completo(extra) {
  const base = {
    tipo: "espirometria_soprolife",
    pessoa: { existente: false, nome: "Marina Costa Ribeiro", cpf: "12345678909" },
    exame: { data: "2026-09-01", status: "Realizado", broncodilatador: "false",
             municipio: RIO, modalidade: "residencial" },
    financeiro: { valor: "220.00", status: "Recebido" },
  };
  extra = extra || {};
  return {
    tipo: extra.tipo || base.tipo,
    pessoa: Object.assign({}, base.pessoa, extra.pessoa),
    exame: Object.assign({}, base.exame, extra.exame),
    financeiro: Object.assign({}, base.financeiro, extra.financeiro),
  };
}
const av = (extra) => P.avaliar(completo(extra), CTX);
const item = (r, chave) => r.itens.find((i) => i.chave === chave);
const faltaEm = (r, chave, trecho) => {
  const i = item(r, chave);
  return !r.completo && i && !i.ok && i.faltas.some((f) => f.includes(trecho));
};

let r = av();
caso("1. espirometria própria SoproLife completa → Dados fiscais completos",
     r.modo === "soprolife" && r.completo && r.titulo === "Dados fiscais completos");
caso("1b. mostra os seis itens pedidos",
     JSON.stringify(r.itens.map((i) => i.rotulo)) === JSON.stringify([
       "Nome e CPF", "Data e exame realizado", "Com/sem broncodilatador",
       "Município da prestação", "Modalidade fiscal suportada", "Receita recebida"]));
caso("1c. Espirometria + Consulta também é espirometria própria",
     av({ tipo: "espirometria_consulta_soprolife" }).completo);
caso("1d. incompleto → Faltam dados para NFS-e",
     av({ pessoa: { cpf: "" } }).titulo === "Faltam dados para NFS-e");

caso("2. nome vazio → incompleto", faltaEm(av({ pessoa: { nome: "" } }), "identidade", "Nome completo"));
caso("2b. nome de uma palavra → incompleto", faltaEm(av({ pessoa: { nome: "Marina" } }), "identidade", "sobrenome"));
caso("2c. nome placeholder → incompleto", faltaEm(av({ pessoa: { nome: "Paciente Teste" } }), "identidade", "teste ou exemplo"));
caso("3. CPF ausente → 'CPF necessário para emissão da NFS-e'",
     faltaEm(av({ pessoa: { cpf: "" } }), "identidade", "CPF necessário para emissão da NFS-e"));
caso("3b. CPF com dígito errado → incompleto", faltaEm(av({ pessoa: { cpf: "12345678900" } }), "identidade", "CPF inválido"));
caso("3c. CPF repetido → incompleto", faltaEm(av({ pessoa: { cpf: "11111111111" } }), "identidade", "CPF inválido"));
caso("3d. CPF incompleto → incompleto", faltaEm(av({ pessoa: { cpf: "1234" } }), "identidade", "11 dígitos"));
caso("3e. paciente existente: usa o veredito do servidor",
     faltaEm(av({ pessoa: { existente: true, pendenciasServidor: ["recipient_cpf_missing"] } }),
             "identidade", "CPF necessário") &&
     av({ pessoa: { existente: true, pendenciasServidor: [] } }).completo);
caso("3f. nenhum código técnico chega à tela",
     !JSON.stringify(av({ pessoa: { existente: true, pendenciasServidor: [
       "recipient_cpf_missing", "recipient_name_not_a_full_name"] },
       exame: { municipio: "", modalidade: "" }, financeiro: { valor: "" } }))
       .match(/recipient_|service_location|financial_entry|commercial_flow/));

caso("4. broncodilatador indefinido → incompleto",
     faltaEm(av({ exame: { broncodilatador: "" } }), "broncodilatador",
             "Informe se o exame foi realizado com ou sem broncodilatador"));
caso("4b. com broncodilatador → completo", av({ exame: { broncodilatador: "true" } }).completo);

caso("5. município ausente → incompleto",
     faltaEm(av({ exame: { municipio: "" } }), "municipio", "Informe o município onde o exame foi realizado"));
caso("5b. município fora da lista do servidor → incompleto",
     faltaEm(av({ exame: { municipio: "3550308" } }), "municipio", "não suportado"));
caso("6. Rio de Janeiro → aceito", av({ exame: { municipio: RIO } }).completo);
caso("7. Niterói → aceito", av({ exame: { municipio: NITEROI } }).completo);

caso("8. residencial → HOME, compatível",
     P.fluxoFiscal("espirometria_soprolife", "residencial") === "HOME" && av({ exame: { modalidade: "residencial" } }).completo);
caso("8b. cowork → DIRECT, compatível",
     P.fluxoFiscal("espirometria_soprolife", "cowork") === "DIRECT" && av({ exame: { modalidade: "cowork" } }).completo);
caso("8c. modalidade vazia → incompleto", faltaEm(av({ exame: { modalidade: "" } }), "modalidade", "Escolha a modalidade"));
caso("8d. clínica parceira → fluxo PASTORE, não suportado",
     P.fluxoFiscal("espirometria_soprolife", "clinica_parceira") === "PASTORE" &&
     faltaEm(av({ exame: { modalidade: "clinica_parceira" } }), "modalidade", "não tem fluxo fiscal"));

const past = av({ tipo: "espirometria_pastore" });
caso("9. Pastore: nunca 'completo' / 'pronto'", past.modo === "pastore" && past.completo === false);
caso("9b. Pastore: mostra só a explicação da parceria, sem lista de erros",
     past.titulo === "Parceria Pastore — NFS-e não emitida por exame pela SoproLife" && past.itens.length === 0);
caso("9c. Pastore mesmo com todos os campos preenchidos continua sem prontidão",
     P.avaliar(Object.assign(completo(), { tipo: "espirometria_pastore" }), CTX).modo === "pastore");

caso("10. valor vazio → incompleto", faltaEm(av({ financeiro: { valor: "" } }), "receita", "valor da espirometria"));
caso("10b. valor zero → incompleto", faltaEm(av({ financeiro: { valor: "0" } }), "receita", "valor da espirometria"));
["Pendente", "Parcial", "Cortesia"].forEach((st) => {
  caso(`11. pagamento ${st} → incompleto`,
       faltaEm(av({ financeiro: { status: st } }), "receita",
               "O pagamento precisa estar como Recebido para ficar elegível à NFS-e"));
});

caso("12/13. data de recebimento e forma de pagamento não fazem parte do rascunho fiscal",
     av({ financeiro: { data_recebimento: "", forma_pagamento: "" } }).completo &&
     !/data_recebimento|forma_pagamento|recebimento/i.test(
       (moduloCodigo.match(/function avaliar[\s\S]*?\n  }\n/) || [""])[0].replace(/Recebido|recebida|RECEBIDO/g, "")));
caso("14. WhatsApp/e-mail/nascimento/sexo/origem/observações não entram na avaliação",
     !/fone|email|nasc|sexo|origem|observa|consent|responsavel|followup|local/i.test(
       (moduloCodigo.match(/function avaliar[\s\S]*?\n  }\n/) || [""])[0]));

caso("data mês/ano → incompleto (evaluate exige dia)",
     faltaEm(av({ exame: { data: "2026-09" } }), "servico", "data exata"));
caso("data futura → incompleto", faltaEm(av({ exame: { data: "2026-09-24" } }), "servico", "futuro"));
caso("data de hoje → aceita", av({ exame: { data: "2026-09-23" } }).completo);
caso("data DD/MM/AAAA digitada → aceita", av({ exame: { data: "01/09/2026" } }).completo);
caso("exame Aguardando → incompleto", faltaEm(av({ exame: { status: "Aguardando" } }), "servico", "Realizado"));
caso("exame Laudo Liberado → aceito", av({ exame: { status: "Laudo Liberado" } }).completo);

const cons = av({ tipo: "consulta_soprolife" });
caso("consulta pura → não aplicável (nenhum requisito de espirometria)",
     cons.modo === "nao_aplicavel" && cons.itens.length === 0);
caso("sem tipo → não aplicável", P.avaliar({}, CTX).modo === "nao_aplicavel");

// ── D) a tela ───────────────────────────────────────────────────────────────
console.log("\nD) Selo NFS-e na tela");

// Rótulo do campo → recebe (ou não) `nfse` nas opções do fld().
function opcoesDoCampo(rotulo) {
  const i = centralCodigo.indexOf(`fld("${rotulo}"`);
  if (i === -1) return null;
  // Opções do fld: do rótulo até o fim da chamada — basta o trecho até o
  // próximo `${fld(` ou fim de linha de template.
  const resto = centralCodigo.slice(i + 5);
  const fim = resto.search(/\$\{fld\(|\n\s*`/);
  return resto.slice(0, fim === -1 ? 600 : fim);
}
const fiscais = ["Nome completo", "CPF", "Data do exame", "Status", "Broncodilatador",
  "Município onde o exame foi realizado", "Modalidade", "Valor da espirometria (R$)",
  "Status do pagamento"];
fiscais.forEach((rot) => {
  const o = opcoesDoCampo(rot);
  caso(`selo NFS-e em "${rot}"`, o && /nfse(\s*[,}]|: )/.test(o), o ? o.slice(0, 90) : "campo não encontrado");
});
const naoFiscais = ["WhatsApp", "Nascimento", "E-mail (opcional)", "Sexo", "Consentimento WhatsApp",
  "Origem", "Local do atendimento", "Data de recebimento", "Forma de pagamento",
  "Técnico / responsável", "Próximo acompanhamento", "Observações do exame"];
naoFiscais.forEach((rot) => {
  const o = opcoesDoCampo(rot);
  caso(`SEM selo NFS-e em "${rot}"`, o !== null && !/nfse/.test(o), o ? o.slice(0, 90) : "campo não encontrado");
});
caso("selo tem texto visível, rótulo acessível e tooltip com a frase pedida",
     />NFS-e<\/button>/.test(central) && /aria-label="Obrigatório para NFS-e"/.test(central) &&
     /role="tooltip"/.test(central) &&
     /"Obrigatório para emissão da NFS-e da SoproLife\."/.test(central));
caso("selo liga aria-describedby no campo só no modo SoproLife",
     /descricaoAria\(alvo, l\.getAttribute\("data-nfse-desc"\), r\.modo === "soprolife"\)/.test(centralCodigo));
caso("asterisco de 'obrigatório para salvar' continua separado (cad-req intacto)",
     /<b class="cad-req" title="Campo obrigatório">\*<\/b>/.test(central) &&
     !/cad-req[^"]*nfse|nfse[^"]*cad-req/.test(central));
caso("selo fica escondido fora do modo SoproLife (CSS)",
     /\.cad-nfse-selo-wrap \{[^}]*display: none/.test(ler("css", "central.css")) &&
     /form\[data-nfse-modo="soprolife"\] \.cad-nfse-selo-wrap \{ display: inline-flex; \}/.test(ler("css", "central.css")));
caso("variante Pastore do bloco não carrega selo (nfse = !ehPastore)",
     /const nfse = !ehPastore;/.test(centralCodigo));
caso("Lead não herda o selo (só o Novo atendimento pede { nfse: true })",
     /personPickerHtml\("cadAtP", \{ nfse: true \}\)/.test(centralCodigo) &&
     /personPickerHtml\("cadLeadP"\)/.test(centralCodigo));
caso("cartão do tipo Pastore diz que não gera NFS-e por exame",
     /espirometria_pastore: \["nao-emite", "Sem NFS-e por exame/.test(centralCodigo));
caso("consulta pura não recebe linha fiscal no cartão",
     !/^\s+consulta_soprolife: \[/m.test(centralCodigo));
caso("index.html carrega o módulo antes da Central",
     (() => { const h = ler("index.html");
       const a = h.indexOf("js/nfse-prontidao.js"), b = h.indexOf("js/central-cadastros.js");
       return a !== -1 && b !== -1 && a < b; })());

// ── E) sem emissão, sem SEFIN, sem bloqueio de salvamento ───────────────────
console.log("\nE) Nenhuma emissão, nenhuma rota fiscal, salvamento livre");
caso("o módulo não faz rede (sem fetch/XMLHttpRequest/api())",
     !/fetch\(|XMLHttpRequest|\bapi\(|sefin|\/fiscal/i.test(moduloCodigo));
const blocoProntidao = (centralCodigo.match(/function prontidaoNfseHtml[\s\S]*?LOADERS\.atendimento/) || [""])[0] +
  (centralCodigo.match(/function atualizarProntidaoNfse[\s\S]*?function wireModalidade/) || [""])[0];
caso("a UX de prontidão não chama api() nem rota fiscal",
     blocoProntidao.length > 200 && !/\bapi\(|fetch\(|\/fiscal|\/nfse|emiss[aã]o\/|sefin/i.test(blocoProntidao));
caso("a Central inteira não referencia rota fiscal/NFS-e de emissão",
     !/api\(["'`]\/(fiscal|nfse)/.test(centralCodigo));
caso("15. o submit não consulta a prontidão (salvar incompleto continua permitido)",
     !/\bavaliar\b|SoproNfseProntidao|nfseRegras|ProntidaoNfse|\bcompleto\b/.test(
       (centralCodigo.match(/wireSubmit\(form, \(\) => \{\n\s+const tipo = tipoAtual\(\);[\s\S]*?\n      \}, \(res\)/) || ["avaliar"])[0]));

console.log(`\n${falhas === 0 ? "OK" : "FALHOU"} — ${falhas} falha(s)`);
process.exit(falhas === 0 ? 0 : 1);
