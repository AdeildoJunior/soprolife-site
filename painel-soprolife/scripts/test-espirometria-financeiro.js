#!/usr/bin/env node
// SoproLife — Testes do Valor real da espirometria (M11.0/M11.1).
// 100% local, sem dependência externa, fixtures sintéticas.
// Uso: node painel-soprolife/scripts/test-espirometria-financeiro.js
// Exit: 0 = todos passaram | 1 = houve falha.

const fs   = require("fs");
const path = require("path");
const {
  buildEspirometriaFinanceiroPayload, EF_VALOR_TABELA_PADRAO, EF_LOCAL_ATENDIMENTO,
} = require(path.resolve(__dirname, "../js/espirometria-financeiro.js"));

let falhas = 0;
function caso(nome, cond, det = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${det ? " — " + det : ""}`); }
}

// M14.3A (correção final) — criado_em NÃO faz parte do payload do cliente:
// é autoridade exclusiva do servidor no insert (ver _registrarEspirometriaFinanceiro).
const CHAVES = [
  "id_atendimento", "data_exame", "tipo_movimento", "servico",
  "local_atendimento", "valor_tabela", "valor_cobrado", "valor_recebido",
  "desconto", "status_exame", "status_pagamento", "forma_pagamento",
  "origem_preco", "observacao_financeira", "fonte",
];

// Base válida reutilizada pelos casos — Realizado/Recebido, sem PII.
// M11.1.1: Recebido exige forma_pagamento e valor_recebido == valor_cobrado.
const base = () => ({
  data_exame: "2026-07-08",
  status_exame: "Realizado",
  status_pagamento: "Recebido",
  valor_cobrado: "250",
  valor_recebido: "250",
  forma_pagamento: "Pix",
  origem_preco: "Tabela",
  local_atendimento: "Clínica",
});

console.log("M11 — testes do Valor real da espirometria (fixtures sintéticas)");

// 1. M14.3A (2ª rodada): AUSÊNCIA PERMANECE AUSENTE — valor_tabela não
// informado NÃO vira 250; o payload simplesmente omite tabela e desconto.
const r1 = buildEspirometriaFinanceiroPayload(base());
caso("payload válido ok=true", r1.ok === true);
caso("valor_tabela ausente permanece ausente (nunca vira 250)",
     !Object.prototype.hasOwnProperty.call(r1.payload, "valor_tabela"));
caso("desconto não é derivado sem valor_tabela",
     !Object.prototype.hasOwnProperty.call(r1.payload, "desconto"));
const CHAVES_SEM_TABELA = [...CHAVES].filter((c) => c !== "valor_tabela" && c !== "desconto");
caso("shape tem exatamente as chaves do contrato (sem os ausentes)",
     JSON.stringify(Object.keys(r1.payload).sort()) === JSON.stringify(CHAVES_SEM_TABELA.sort()));
caso("tipo_movimento fixo 'receita'", r1.payload.tipo_movimento === "receita");
caso("servico fixo 'Espirometria'", r1.payload.servico === "Espirometria");
caso("fonte fixa 'nova_espirometria'", r1.payload.fonte === "nova_espirometria");

// 2. Sugestão R$ 230 (desconto sobre a tabela padrão) — Recebido exige
// valor_recebido == valor_cobrado, então ambos mudam juntos.
const r2 = buildEspirometriaFinanceiroPayload({ ...base(), valor_tabela: "250", valor_cobrado: "230", valor_recebido: "230" });
caso("sugestão 230 aceita", r2.ok === true && r2.payload.valor_cobrado === 230);
caso("desconto 230 = 20", r2.payload.desconto === 20);

// 3. Sugestão R$ 219 (promoção)
const r3 = buildEspirometriaFinanceiroPayload({ ...base(), valor_tabela: "250", valor_cobrado: "219", valor_recebido: "219" });
caso("sugestão 219 aceita", r3.ok === true && r3.payload.valor_cobrado === 219);
caso("desconto 219 = 31", r3.payload.desconto === 31);

// 4. Valor manual livre (fora das sugestões, negociado)
const r4 = buildEspirometriaFinanceiroPayload({
  ...base(), valor_cobrado: "175.50", valor_recebido: "175.50", origem_preco: "Negociação",
});
caso("valor manual livre aceito", r4.ok === true && r4.payload.valor_cobrado === 175.5);
caso("origem do preço respeita o manual", r4.payload.origem_preco === "Negociação");

// 5. Desconto calculado corretamente com valor_tabela custom
const r5 = buildEspirometriaFinanceiroPayload({
  ...base(), valor_tabela: "300", valor_cobrado: "300", valor_recebido: "300",
});
caso("desconto zero quando cobrado = tabela", r5.payload.desconto === 0);
const r5b = buildEspirometriaFinanceiroPayload({
  ...base(), valor_tabela: "200", valor_cobrado: "230", valor_recebido: "230",
});
caso("desconto nunca fica negativo (cobrado > tabela)", r5b.payload.desconto === 0);

// 6. Cortesia zera valor recebido mesmo se algo foi digitado antes
const r6 = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Cortesia", valor_cobrado: "250", valor_recebido: "250",
});
caso("cortesia zera valor recebido", r6.ok === true && r6.payload.valor_recebido === 0);
caso("cortesia mantém status_pagamento Cortesia", r6.payload.status_pagamento === "Cortesia");

// 7. Pendente não vira recebido (não auto-copia valor_cobrado)
const r7 = buildEspirometriaFinanceiroPayload({ ...base(), status_pagamento: "Pendente", valor_cobrado: "250" });
caso("pendente não vira recebido", r7.ok === true && r7.payload.valor_recebido === 0);
caso("pendente mantém status_pagamento Pendente", r7.payload.status_pagamento === "Pendente");

// 8. Cancelado não vira receita (valor recebido zerado, por status_exame OU status_pagamento)
const r8a = buildEspirometriaFinanceiroPayload({ ...base(), status_exame: "Cancelado", valor_recebido: "250" });
caso("status_exame Cancelado zera valor recebido", r8a.ok === true && r8a.payload.valor_recebido === 0);
const r8b = buildEspirometriaFinanceiroPayload({ ...base(), status_pagamento: "Cancelado", valor_recebido: "250" });
caso("status_pagamento Cancelado zera valor recebido", r8b.ok === true && r8b.payload.valor_recebido === 0);

// 9. Valores inválidos são rejeitados
const rNeg = buildEspirometriaFinanceiroPayload({ ...base(), valor_cobrado: "-10" });
caso("valor_cobrado negativo é rejeitado", rNeg.ok === false && rNeg.erros.length > 0);
const rLixo = buildEspirometriaFinanceiroPayload({ ...base(), valor_cobrado: "abc" });
caso("valor_cobrado não numérico é rejeitado", rLixo.ok === false);
const rTabelaNeg = buildEspirometriaFinanceiroPayload({ ...base(), valor_tabela: "-5" });
caso("valor_tabela negativo é rejeitado", rTabelaNeg.ok === false);
const rFormaInvalida = buildEspirometriaFinanceiroPayload({ ...base(), forma_pagamento: "Boleto" });
caso("forma de pagamento fora da lista é rejeitada", rFormaInvalida.ok === false);
const rOrigemInvalida = buildEspirometriaFinanceiroPayload({ ...base(), origem_preco: "Sorteio" });
caso("origem do preço fora da lista é rejeitada", rOrigemInvalida.ok === false);

// 10. Data obrigatória
const rSemData = buildEspirometriaFinanceiroPayload({ ...base(), data_exame: "" });
caso("data do exame obrigatória", rSemData.ok === false && /[Dd]ata/.test(rSemData.erros.join(" ")));

// 11. Status obrigatório (exame e pagamento)
const rSemStatusExame = buildEspirometriaFinanceiroPayload({ ...base(), status_exame: "" });
caso("status do exame obrigatório", rSemStatusExame.ok === false);
const rSemStatusPagto = buildEspirometriaFinanceiroPayload({ ...base(), status_pagamento: "" });
caso("status do pagamento obrigatório", rSemStatusPagto.ok === false);
const rStatusInvalido = buildEspirometriaFinanceiroPayload({ ...base(), status_exame: "Sumiu" });
caso("status do exame fora da lista é rejeitado", rStatusInvalido.ok === false);

// 12. PII/segredo não vaza (observação financeira sanitizada)
const rSujo = buildEspirometriaFinanceiroPayload({
  ...base(),
  observacao_financeira: "ligar (21) 99999-8888, e-mail x@y.com, CPF 123.456.789-09, token ya29.abc",
});
const bruto = JSON.stringify(rSujo);
caso("telefone não vaza", !bruto.includes("99999-8888"));
caso("e-mail não vaza", !bruto.includes("x@y.com"));
caso("CPF não vaza", !bruto.includes("123.456.789-09"));
caso("token não vaza", !bruto.includes("ya29.abc"));

// 13. Nome/telefone do paciente nunca entram no payload financeiro, mesmo
// que venham juntos no mesmo formData da tela (campo extra é ignorado).
const rComPaciente = buildEspirometriaFinanceiroPayload({
  ...base(),
  primeiro_nome: "Maria",
  responsavel: "João",
  telefone: "(21) 98888-7777",
  campo_desconhecido: "algo que não deveria aparecer",
});
const brutoPaciente = JSON.stringify(rComPaciente);
caso("nome do paciente não entra no payload", !brutoPaciente.includes("Maria"));
caso("responsável não entra no payload", !brutoPaciente.includes("João"));
caso("telefone do paciente não entra no payload", !brutoPaciente.includes("98888-7777"));
caso("campo extra é ignorado", !brutoPaciente.includes("algo que não deveria aparecer"));
caso("shape continua fechado mesmo com campos extras no formData",
     JSON.stringify(Object.keys(rComPaciente.payload).sort()) === JSON.stringify(CHAVES_SEM_TABELA.sort()));

// 14. Nulos/indefinidos não quebram
const rNulo = buildEspirometriaFinanceiroPayload(null);
caso("formData nulo não quebra", !!rNulo && rNulo.ok === false && Array.isArray(rNulo.erros));
const rVazio = buildEspirometriaFinanceiroPayload({});
caso("formData vazio não quebra e reporta erros", rVazio.ok === false && rVazio.erros.length > 0);

// 15. id_atendimento passa adiante (edição futura reaproveita o mesmo lançamento)
const rComId = buildEspirometriaFinanceiroPayload({ ...base(), id_atendimento: "ATD-0001" });
caso("id_atendimento é preservado quando informado", rComId.payload.id_atendimento === "ATD-0001");
const rSemId = buildEspirometriaFinanceiroPayload(base());
caso("id_atendimento é null quando não informado", rSemId.payload.id_atendimento === null);

// M11.2B — id_atendimento no formato gerado pelo painel (data+hora+sufixo)
// não pode ser confundido com telefone pelo sanitizador de texto livre e
// precisa ser preservado igualzinho, pro upsert no Apps Script funcionar.
const rComIdGerado = buildEspirometriaFinanceiroPayload({
  ...base(), id_atendimento: "ESP-20260709-143055-ABC123",
});
caso("id_atendimento no formato ESP-AAAAMMDD-HHMMSS-XXXXXX é preservado",
     rComIdGerado.payload.id_atendimento === "ESP-20260709-143055-ABC123");
const rComIdSuspeito = buildEspirometriaFinanceiroPayload({
  ...base(), id_atendimento: "id com espaço e (21) 99999-8888",
});
caso("id_atendimento fora do formato seguro vira null (não vaza texto livre)",
     rComIdSuspeito.payload.id_atendimento === null);

// 16. Ajuste M11.1 — local_atendimento é campo PRÓPRIO, nunca derivado de
// "servico". servico continua fixo "Espirometria" independente do local.
for (const local of EF_LOCAL_ATENDIMENTO) {
  const r = buildEspirometriaFinanceiroPayload({ ...base(), local_atendimento: local });
  caso(`local_atendimento aceita "${local}"`, r.ok === true && r.payload.local_atendimento === local);
  caso(`servico continua "Espirometria" com local "${local}"`, r.payload.servico === "Espirometria");
}

// servico (texto livre da tela, ex.: "Espirometria domiciliar") não deve
// mais alimentar local_atendimento — mesmo que venha preenchido no formData,
// só o campo local_atendimento decide o valor final.
const rServicoDiferente = buildEspirometriaFinanceiroPayload({
  ...base(), servico: "Espirometria domiciliar", local_atendimento: "Clínica",
});
caso("servico enviado no formData não sobrescreve o payload (fixo 'Espirometria')",
     rServicoDiferente.payload.servico === "Espirometria");
caso("local_atendimento não usa mais o texto de 'servico' como fonte",
     rServicoDiferente.payload.local_atendimento === "Clínica" &&
     rServicoDiferente.payload.local_atendimento !== "Espirometria domiciliar");

// Campo local obrigatório: vazio ou ausente -> erro claro, nenhum payload montado.
const rSemLocal = buildEspirometriaFinanceiroPayload({ ...base(), local_atendimento: "" });
caso("local de atendimento vazio é rejeitado com erro claro",
     rSemLocal.ok === false && /[Ll]ocal de atendimento é obrigatório/.test(rSemLocal.erros.join(" ")));
const { local_atendimento: _omitido, ...baseSemLocal } = base();
const rLocalAusente = buildEspirometriaFinanceiroPayload(baseSemLocal);
caso("local de atendimento ausente do formData é rejeitado", rLocalAusente.ok === false);
const rLocalInvalido = buildEspirometriaFinanceiroPayload({ ...base(), local_atendimento: "Sofá da sala" });
caso("local de atendimento fora da lista é rejeitado", rLocalInvalido.ok === false &&
     /[Ll]ocal de atendimento inválido/.test(rLocalInvalido.erros.join(" ")));

// 17. M11.1.1 — regras cruzadas por status de pagamento. O bug relatado no
// teste visual (Recebido + forma_pagamento vazia validando com sucesso)
// nunca mais deve passar.
const rRecebidoSemForma = buildEspirometriaFinanceiroPayload({ ...base(), forma_pagamento: "" });
caso("Recebido sem forma_pagamento é rejeitado",
     rRecebidoSemForma.ok === false &&
     /[Ff]orma de pagamento é obrigatória.*Recebido/.test(rRecebidoSemForma.erros.join(" ")));

// Recebido com valor_recebido vazio: rejeitado com mensagem de
// obrigatoriedade (nunca 0 silencioso — M14.3A 2ª rodada).
const rRecebidoSemValor = buildEspirometriaFinanceiroPayload({ ...base(), valor_recebido: "" });
caso("Recebido com valor_recebido vazio é rejeitado",
     rRecebidoSemValor.ok === false &&
     /obrigatório.*Recebido/.test(rRecebidoSemValor.erros.join(" ")));
// origem_preco vazia não vira "Tabela"
const rSemOrigem = buildEspirometriaFinanceiroPayload({ ...base(), origem_preco: "" });
caso("origem_preco vazia permanece ausente (nunca vira Tabela)",
     rSemOrigem.ok === true && !Object.prototype.hasOwnProperty.call(rSemOrigem.payload, "origem_preco"));

// Recebido com valor_recebido menor que o cobrado -> orienta usar Parcial.
const rRecebidoParcialDisfarcado = buildEspirometriaFinanceiroPayload({
  ...base(), valor_cobrado: "250", valor_recebido: "150",
});
caso("Recebido com valor_recebido menor que o cobrado é rejeitado",
     rRecebidoParcialDisfarcado.ok === false &&
     /use o status de pagamento "Parcial"/.test(rRecebidoParcialDisfarcado.erros.join(" ")));

// Recebido com valor_recebido maior que o cobrado também é rejeitado
// (a igualdade é obrigatória nos dois sentidos).
const rRecebidoAcima = buildEspirometriaFinanceiroPayload({
  ...base(), valor_cobrado: "250", valor_recebido: "300",
});
caso("Recebido com valor_recebido maior que o cobrado é rejeitado", rRecebidoAcima.ok === false);

// Parcial sem forma_pagamento é rejeitado.
const rParcialSemForma = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Parcial", valor_recebido: "100", forma_pagamento: "",
});
caso("Parcial sem forma_pagamento é rejeitado",
     rParcialSemForma.ok === false &&
     /[Ff]orma de pagamento é obrigatória.*Parcial/.test(rParcialSemForma.erros.join(" ")));

// Parcial com valor_recebido igual ao cobrado é rejeitado (não é parcial,
// é recebido — usar o status "Recebido" nesse caso).
const rParcialIgualCobrado = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Parcial", valor_cobrado: "250", valor_recebido: "250",
});
caso("Parcial com valor_recebido igual ao cobrado é rejeitado", rParcialIgualCobrado.ok === false);

// Parcial válido: recebido > 0 e menor que o cobrado, com forma de pagamento.
const rParcialValido = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Parcial", valor_cobrado: "250", valor_recebido: "100",
});
caso("Parcial válido é aceito", rParcialValido.ok === true && rParcialValido.payload.valor_recebido === 100);

// Pendente sempre gera valor_recebido 0 no payload, mesmo se um valor tiver
// sido preenchido antes (ex.: por chip) e mesmo sem forma_pagamento.
const rPendenteComValorPreenchido = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Pendente", valor_recebido: "250", forma_pagamento: "",
});
caso("Pendente gera valor_recebido 0 no payload mesmo preenchido",
     rPendenteComValorPreenchido.ok === true && rPendenteComValorPreenchido.payload.valor_recebido === 0);

// Cortesia sempre gera valor_recebido 0 no payload, mesmo sem forma_pagamento.
const rCortesiaSemForma = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Cortesia", valor_recebido: "250", forma_pagamento: "",
});
caso("Cortesia gera valor_recebido 0 no payload mesmo sem forma de pagamento",
     rCortesiaSemForma.ok === true && rCortesiaSemForma.payload.valor_recebido === 0);

// Cancelado (por status_exame OU status_pagamento) sempre gera valor_recebido
// 0 no payload, mesmo sem forma_pagamento.
const rCanceladoExameSemForma = buildEspirometriaFinanceiroPayload({
  ...base(), status_exame: "Cancelado", valor_recebido: "250", forma_pagamento: "",
});
caso("Cancelado (status_exame) gera valor_recebido 0 mesmo sem forma de pagamento",
     rCanceladoExameSemForma.ok === true && rCanceladoExameSemForma.payload.valor_recebido === 0);
const rCanceladoPagtoSemForma = buildEspirometriaFinanceiroPayload({
  ...base(), status_pagamento: "Cancelado", valor_recebido: "250", forma_pagamento: "",
});
caso("Cancelado (status_pagamento) gera valor_recebido 0 mesmo sem forma de pagamento",
     rCanceladoPagtoSemForma.ok === true && rCanceladoPagtoSemForma.payload.valor_recebido === 0);

// Forma de pagamento continua aceita para as 4 opções da lista, tanto em
// Recebido (igual ao cobrado) quanto em Parcial (menor que o cobrado).
for (const forma of ["Pix", "Dinheiro", "Cartão", "Outro"]) {
  const rForma = buildEspirometriaFinanceiroPayload({ ...base(), forma_pagamento: forma });
  caso(`forma de pagamento "${forma}" é aceita em Recebido`,
       rForma.ok === true && rForma.payload.forma_pagamento === forma);
}

// M16 — Central de Cadastros consolidou TODA criação (lead, paciente, exame,
// consulta, clínica, contato B2B, financeiro) em js/central-cadastros.js,
// contra a API M15/PostgreSQL. O antigo dual-save da Nova Espirometria
// (bindFormEspiFinanceiro/buildEspirometriaOperacionalPayload/ACTION_MAP/
// camposFinanceiro em renderEntradaDados) e o modal de staging da Pastore
// (openNovoAtendimentoPastoreModal com o helper f()) foram REMOVIDOS de
// app.js por decisão explícita do produto (uma única implementação de cada
// formulário) — não por regressão. As seções abaixo substituem as antigas
// checagens de integração (M11.2A–M14.3A) por checagens do estado atual:
// nenhuma duplicata sobrevive em app.js, e os testes de PII/fetch/XHR do
// módulo financeiro puro (espirometria-financeiro.js, casos 1–17 acima)
// continuam intactos porque o arquivo em si não foi tocado.
console.log();
console.log("M16 — Central de Cadastros consolidou a Nova Espirometria (checagens estáticas)");

const appJsPath = path.resolve(__dirname, "../js/app.js");
const gsPath    = path.resolve(__dirname, "../apps-script/command-center-api.gs");
const centralJsPath = path.resolve(__dirname, "../js/central-cadastros.js");
const appJsSrc  = fs.readFileSync(appJsPath, "utf8");
const gsSrc     = fs.readFileSync(gsPath, "utf8");
const efSrc     = fs.readFileSync(path.resolve(__dirname, "../js/espirometria-financeiro.js"), "utf8");
const centralJsSrc = fs.readFileSync(centralJsPath, "utf8");

caso("app.js não contém mais o dual-save legado da Nova Espirometria (bindFormEspiFinanceiro)",
     !appJsSrc.includes("bindFormEspiFinanceiro") &&
     !appJsSrc.includes("registrarEspirometriaFinanceiro"));
caso("app.js não contém mais o ACTION_MAP legado de Entrada de Dados",
     !/const ACTION_MAP\s*=\s*\{/.test(appJsSrc));
caso("app.js não contém mais o helper f() de opção em branco do modal Pastore (modal removido)",
     !appJsSrc.includes('<option value="">—</option>${opts}</select>'));
caso("openNovoAtendimentoPastoreModal virou deep-link para a Central (sem formulário próprio)",
     /function openNovoAtendimentoPastoreModal\(\)\s*\{[\s\S]{0,300}?SoproCentral\.open\("espirometria"/.test(appJsSrc));

caso("Apps Script mantém _registrarEspirometriaFinanceiro (infra legada/import, não apagada)",
     /function _registrarEspirometriaFinanceiro\s*\(/.test(gsSrc));
caso("Apps Script mantém _createEspirometria (infra legada/import, não apagada)",
     /function _createEspirometria\s*\(/.test(gsSrc));
caso("Financeiro_Lancamentos aparece no Apps Script (fonte histórica preservada)",
     gsSrc.includes("Financeiro_Lancamentos"));

caso("app.js não contém URL do Apps Script (script.google.com)",
     !/script\.google\.com/.test(appJsSrc));
caso("app.js não contém propriedade API_TOKEN hardcoded",
     !/API_TOKEN/.test(appJsSrc));
caso("espirometria-financeiro.js continua sem fetch",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest",
     !/XMLHttpRequest/.test(efSrc));

console.log();
console.log("M16 — Central de Cadastros: equivalentes de segurança na nova aba Espirometria");

caso("central-cadastros.js não contém URL do Apps Script",
     !/script\.google\.com/.test(centralJsSrc));
caso("central-cadastros.js não usa fetch cru fora do cliente do núcleo (sem chamada direta a Sheets)",
     !/fetch\(["'`]https:\/\/script\.google\.com/.test(centralJsSrc));
caso("aba Espirometria da Central usa idempotency_key (anti duplo-clique via API M15)",
     /LOADERS\.espirometria[\s\S]{0,4000}?idempotency_key:\s*m15\(\)\.idemKey\(\)/.test(centralJsSrc));
caso("aba Espirometria da Central inclui campo broncodilatador",
     /LOADERS\.espirometria[\s\S]{0,4000}?broncodilatador/.test(centralJsSrc));
caso("aba Financeiro da Central nunca inclui campo de nome/telefone do paciente no formulário",
     (() => {
       const inicio = centralJsSrc.indexOf("LOADERS.financeiro = function");
       const fim    = centralJsSrc.indexOf("function loadFinRecents(", inicio);
       const trecho = fim > inicio ? centralJsSrc.slice(inicio, fim) : "";
       return trecho.length > 0 &&
         !/name="nome"|name="telefone"|name="whatsapp"|name="cpf"/.test(trecho);
     })());
caso("aba Financeiro da Central vincula por exame/consulta (spirometry_exam_id/consultation_id), não por texto livre",
     /vinculo\.spirometry_exam_id/.test(centralJsSrc) && /vinculo\.consultation_id/.test(centralJsSrc));

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
