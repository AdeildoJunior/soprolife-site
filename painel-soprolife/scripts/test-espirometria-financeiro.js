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

const CHAVES = [
  "id_atendimento", "criado_em", "data_exame", "tipo_movimento", "servico",
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

// 1. Valor padrão R$ 250 (valor_tabela não informado -> cai no padrão)
const r1 = buildEspirometriaFinanceiroPayload(base());
caso("payload válido ok=true", r1.ok === true);
caso("valor_tabela padrão é 250 quando não informado", r1.payload.valor_tabela === EF_VALOR_TABELA_PADRAO);
caso("shape tem exatamente as chaves do contrato",
     JSON.stringify(Object.keys(r1.payload).sort()) === JSON.stringify([...CHAVES].sort()));
caso("tipo_movimento fixo 'receita'", r1.payload.tipo_movimento === "receita");
caso("servico fixo 'Espirometria'", r1.payload.servico === "Espirometria");
caso("fonte fixa 'nova_espirometria'", r1.payload.fonte === "nova_espirometria");

// 2. Sugestão R$ 230 (desconto sobre a tabela padrão) — Recebido exige
// valor_recebido == valor_cobrado, então ambos mudam juntos.
const r2 = buildEspirometriaFinanceiroPayload({ ...base(), valor_cobrado: "230", valor_recebido: "230" });
caso("sugestão 230 aceita", r2.ok === true && r2.payload.valor_cobrado === 230);
caso("desconto 230 = 20", r2.payload.desconto === 20);

// 3. Sugestão R$ 219 (promoção)
const r3 = buildEspirometriaFinanceiroPayload({ ...base(), valor_cobrado: "219", valor_recebido: "219" });
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
caso("shape continua fixo mesmo com campos extras no formData",
     JSON.stringify(Object.keys(rComPaciente.payload).sort()) === JSON.stringify([...CHAVES].sort()));

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

// Recebido com valor_recebido vazio: a função pura rejeita (mais segura do
// que silenciosamente aceitar 0 como "recebido").
const rRecebidoSemValor = buildEspirometriaFinanceiroPayload({ ...base(), valor_recebido: "" });
caso("Recebido com valor_recebido vazio é rejeitado",
     rRecebidoSemValor.ok === false &&
     /maior que zero.*Recebido/.test(rRecebidoSemValor.erros.join(" ")));

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

// 18. M11.2A — Command Center financeiro. Checagens estáticas de texto
// (sem require do .gs — Apps Script não é um módulo Node válido, e sem rede/
// Google Sheets nesta etapa): confirmam que a ação existe nos dois lados,
// que a aba nova não carrega campo de paciente e que nenhum segredo/URL do
// Apps Script vaza para o front-end.
console.log();
console.log("M11.2A — Command Center financeiro (checagens estáticas)");

const appJsPath = path.resolve(__dirname, "../js/app.js");
const gsPath    = path.resolve(__dirname, "../apps-script/command-center-api.gs");
const appJsSrc  = fs.readFileSync(appJsPath, "utf8");
const gsSrc     = fs.readFileSync(gsPath, "utf8");
const efSrc     = fs.readFileSync(path.resolve(__dirname, "../js/espirometria-financeiro.js"), "utf8");

caso("registrarEspirometriaFinanceiro existe no front-end (app.js)",
     appJsSrc.includes("registrarEspirometriaFinanceiro"));
caso("registrarEspirometriaFinanceiro existe no Apps Script (função)",
     /function _registrarEspirometriaFinanceiro\s*\(/.test(gsSrc));
caso("registrarEspirometriaFinanceiro está registrada no switch de ações do Apps Script",
     /case "registrarEspirometriaFinanceiro":/.test(gsSrc));
caso("Financeiro_Lancamentos aparece no Apps Script",
     gsSrc.includes("Financeiro_Lancamentos"));

const cabecalhoMatch = gsSrc.match(/_FINANCEIRO_LANCAMENTOS_CABECALHO\s*=\s*\[([\s\S]*?)\];/);
caso("cabeçalho de Financeiro_Lancamentos encontrado no Apps Script", !!cabecalhoMatch);
if (cabecalhoMatch) {
  const CAMPOS_PROIBIDOS = [
    "nome", "primeiro_nome", "paciente", "responsavel", "telefone", "whatsapp",
    "cpf", "email", "endereco", "token", "secret",
  ];
  const cabecalhoHeaders = (cabecalhoMatch[1].match(/"([^"]+)"/g) || []).map((s) => s.slice(1, -1));
  const vazamento = CAMPOS_PROIBIDOS.filter((c) => cabecalhoHeaders.includes(c));
  caso("cabeçalho de Financeiro_Lancamentos não contém campos proibidos", vazamento.length === 0,
       vazamento.join(", "));
}

caso("app.js não contém URL do Apps Script (script.google.com)",
     !/script\.google\.com/.test(appJsSrc));
caso("app.js não contém propriedade API_TOKEN hardcoded",
     !/API_TOKEN/.test(appJsSrc));

caso("espirometria-financeiro.js continua sem fetch",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest",
     !/XMLHttpRequest/.test(efSrc));

// M11.2B — ajustes finos pós-teste real: origem_preco automática por chip,
// id_atendimento gerado no front (hidden input estável por formulário) e
// upsert seguro no Apps Script. Checagens estáticas de texto (sem DOM).
console.log();
console.log("M11.2B — ajustes finos pós-teste real (checagens estáticas)");

caso("app.js gera id_atendimento para a Nova Espirometria",
     /function gerarIdAtendimentoEspi\s*\(/.test(appJsSrc) &&
     appJsSrc.includes('id="id_atendimento" name="id_atendimento"'));
caso("app.js mapeia R$250 para origem_preco Tabela",
     /250:\s*"Tabela"/.test(appJsSrc));
caso("Apps Script continua com Financeiro_Lancamentos (upsert por id_atendimento)",
     gsSrc.includes("Financeiro_Lancamentos") && gsSrc.includes('"id_atendimento"'));

// M11.2C — Nova Espirometria enxuta e pré-preenchida. Checagens estáticas de
// texto sobre app.js (sem DOM): responsável virou select fechado, chips vão
// de R$250 a R$200 de 10 em 10, R$220 mapeia para Promoção, e os defaults
// (status_pagamento Recebido / forma_pagamento Pix) favorecem o caminho mais
// comum do operador.
console.log();
console.log("M11.2C — Nova Espirometria enxuta e pré-preenchida (checagens estáticas)");

caso('Responsável contém "Adeildo"', /EF_RESPONSAVEL_OPTS\s*=\s*\[[^\]]*"Adeildo"/.test(appJsSrc));
caso('Responsável contém "Luiz Faustino"', /EF_RESPONSAVEL_OPTS\s*=\s*\[[^\]]*"Luiz Faustino"/.test(appJsSrc));

for (const valor of [250, 240, 230, 220, 210, 200]) {
  caso(`chip de preço contém valor ${valor}`, new RegExp(`valor:\\s*${valor}\\b`).test(appJsSrc));
}
caso('chip "Outro valor" continua presente', appJsSrc.includes("Outro valor"));

caso("app.js mapeia R$220 para origem_preco Promoção",
     /220:\s*"Promoção"/.test(appJsSrc));

caso('status_pagamento da Nova Espirometria vem pré-preenchido com "Recebido"',
     /id:\s*"status_pagamento".*value:\s*"Recebido"/.test(appJsSrc));
caso('forma_pagamento da Nova Espirometria vem pré-preenchida com "Pix"',
     /id:\s*"forma_pagamento".*value:\s*"Pix"/.test(appJsSrc));

caso("id_atendimento continua existindo no payload financeiro", CHAVES.includes("id_atendimento"));

// M11.2C.1 — "Dados opcionais / CRM": Origem detalhada e Motivo do próximo
// contato viram select de opções fechadas em vez de texto livre. Checagens
// estáticas simples de texto sobre app.js (sem DOM) — não mexe no payload
// financeiro nem em espirometria-financeiro.js.
console.log();
console.log("M11.2C.1 — Opções prontas em Dados opcionais / CRM (checagens estáticas)");

caso('app.js contém "Google" (Origem detalhada)', appJsSrc.includes("Google"));
caso('app.js contém "Instagram" (Origem detalhada)', appJsSrc.includes("Instagram"));
caso('app.js contém "WhatsApp" (Origem detalhada)', appJsSrc.includes("WhatsApp"));
caso('app.js contém "Indicação médica" (Origem detalhada)', appJsSrc.includes("Indicação médica"));
caso('app.js contém "Empresa / PCMSO" (Origem detalhada)', appJsSrc.includes("Empresa / PCMSO"));
caso('app.js contém "Confirmar agendamento" (Motivo do próximo contato)', appJsSrc.includes("Confirmar agendamento"));
caso('app.js contém "Enviar preparo" (Motivo do próximo contato)', appJsSrc.includes("Enviar preparo"));
caso('app.js contém "Solicitar pedido médico" (Motivo do próximo contato)', appJsSrc.includes("Solicitar pedido médico"));

caso("espirometria-financeiro.js continua sem fetch (M11.2C.1 não mexeu no arquivo)",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest (M11.2C.1 não mexeu no arquivo)",
     !/XMLHttpRequest/.test(efSrc));

// M12.2A — Nova Espirometria também grava registro operacional em CRM
// Espirometria. bindFormEspiFinanceiro() agora faz dual-save: chama
// createEspirometria (ACTION_MAP.espi) com o payload operacional e, na
// sequência, registrarEspirometriaFinanceiro com resultado.payload (nunca
// o formData bruto). Checagens estáticas de texto sobre app.js e sobre o
// Apps Script — não faz chamada real ao Google Sheets.
console.log();
console.log("M12.2A — Espirometria também grava registro operacional no CRM (checagens estáticas)");

const bindFormEspiSrc = (() => {
  const inicio = appJsSrc.indexOf("function bindFormEspiFinanceiro(");
  const fim    = appJsSrc.indexOf("function bindForm(panel, tabName)", inicio);
  return fim > inicio ? appJsSrc.slice(inicio, fim) : "";
})();

caso("app.js chama createEspirometria (via ACTION_MAP.espi) dentro do fluxo da Nova Espirometria",
     /submitToCommandCenter\(ACTION_MAP\.espi,\s*payloadOperacional\)/.test(bindFormEspiSrc));
caso("app.js continua chamando registrarEspirometriaFinanceiro dentro do mesmo fluxo",
     /submitToCommandCenter\("registrarEspirometriaFinanceiro",\s*resultado\.payload\)/.test(bindFormEspiSrc));
caso("registrarEspirometriaFinanceiro recebe resultado.payload — não o formData bruto",
     bindFormEspiSrc.includes('submitToCommandCenter("registrarEspirometriaFinanceiro", resultado.payload)') &&
     !/submitToCommandCenter\("registrarEspirometriaFinanceiro",\s*formData\)/.test(bindFormEspiSrc));
caso("payload operacional não inclui campos financeiros (valor_cobrado/valor_recebido/status_pagamento)",
     (() => {
       const inicio = appJsSrc.indexOf("function buildEspirometriaOperacionalPayload(");
       const fim    = appJsSrc.indexOf("function bindFormEspiFinanceiro(", inicio);
       const trecho = fim > inicio ? appJsSrc.slice(inicio, fim) : "";
       return trecho.length > 0 &&
         !trecho.includes("valor_cobrado") && !trecho.includes("valor_recebido") &&
         !trecho.includes("status_pagamento");
     })());

caso("createEspirometria existe no Apps Script (função)",
     /function _createEspirometria\s*\(/.test(gsSrc));
caso("createEspirometria está registrada no switch de ações do Apps Script",
     /case "createEspirometria":/.test(gsSrc));
caso("buildEspirometriaFinanceiroPayload continua sem fetch (M12.2A não mexeu no arquivo financeiro)",
     !/\bfetch\s*\(/.test(efSrc));
caso("buildEspirometriaFinanceiroPayload continua sem XMLHttpRequest (M12.2A não mexeu no arquivo financeiro)",
     !/XMLHttpRequest/.test(efSrc));
caso("app.js continua sem URL do Apps Script (script.google.com) após o M12.2A",
     !/script\.google\.com/.test(appJsSrc));

// M12.2B — Upsert/idempotência no CRM Espirometria. id_atendimento (mesmo
// hidden input técnico já usado pelo financeiro) passa a viajar também no
// payload operacional, e _createEspirometria usa esse valor como exame_id
// quando o formato é seguro, procurando a linha existente antes de decidir
// entre criar ou atualizar — evita duplicar linha em reenvio (duplo clique
// ou reenvio manual após falha parcial). Checagens estáticas de texto sobre
// app.js e command-center-api.gs — sem chamada real ao Google Sheets.
console.log();
console.log("M12.2B — Upsert/idempotência no CRM Espirometria (checagens estáticas)");

const buildOperacionalSrc = (() => {
  const inicio = appJsSrc.indexOf("function buildEspirometriaOperacionalPayload(");
  const fim    = appJsSrc.indexOf("function bindFormEspiFinanceiro(", inicio);
  return fim > inicio ? appJsSrc.slice(inicio, fim) : "";
})();
caso("payload operacional (buildEspirometriaOperacionalPayload) inclui id_atendimento",
     /id_atendimento:\s*formData\.id_atendimento/.test(buildOperacionalSrc));

const createEspiGsSrc = (() => {
  const inicio = gsSrc.indexOf("function _createEspirometria(");
  const fim    = gsSrc.indexOf("function _createConsulta(", inicio);
  return fim > inicio ? gsSrc.slice(inicio, fim) : "";
})();
caso("_createEspirometria lê data.id_atendimento", createEspiGsSrc.includes("data.id_atendimento"));
caso("_createEspirometria valida o formato do id_atendimento antes de usar (regex fechada)",
     /_ESPI_ID_ATENDIMENTO_RX\s*=\s*\/\^\[A-Za-z0-9-\]\{1,64\}\$\//.test(gsSrc) &&
     createEspiGsSrc.includes("_espiIdAtendimentoSeguro(data.id_atendimento)"));
caso("_createEspirometria usa o id_atendimento seguro como exame_id",
     /var id = existente \? idAtendimentoSeguro : \(idAtendimentoSeguro \|\| _nextId\(sheet, "ESP"\)\)/.test(createEspiGsSrc));
caso("_createEspirometria busca linha existente por exame_id antes de decidir criar/atualizar (upsert)",
     /_findRowByIdColumn\(sheet,\s*"exame_id",\s*idAtendimentoSeguro\)/.test(createEspiGsSrc));
caso("_createEspirometria atualiza a linha existente em vez de duplicar quando encontrada",
     /if\s*\(existente\)\s*\{[\s\S]*?_writeCellVerified/.test(createEspiGsSrc));
caso("_createEspirometria continua criando linha nova quando não há id_atendimento/linha existente",
     createEspiGsSrc.includes("_appendRowSemValidacao(sheet, _buildRow(sheet, camposLinha))"));
caso("_createEspirometria não cria coluna nova — reaproveita exame_id já existente no cabeçalho",
     !createEspiGsSrc.includes("_ensureExtraColumns") && !createEspiGsSrc.includes("_ensureSheetHeader"));

const registrarFinanceiroGsSrc = (() => {
  const inicio = gsSrc.indexOf("function _registrarEspirometriaFinanceiro(");
  const fim    = gsSrc.indexOf("\nfunction _updateLeadStage(", inicio);
  return fim > inicio ? gsSrc.slice(inicio, fim) : "";
})();
caso("registrarEspirometriaFinanceiro continua inalterado pelo M12.2B (sem marcas do upsert de exame_id)",
     registrarFinanceiroGsSrc.length > 0 &&
     !registrarFinanceiroGsSrc.includes("_espiIdAtendimentoSeguro") &&
     !registrarFinanceiroGsSrc.includes("exame_id"));

caso("app.js continua enviando resultado.payload (não FormData bruto) para registrarEspirometriaFinanceiro após o M12.2B",
     bindFormEspiSrc.includes('submitToCommandCenter("registrarEspirometriaFinanceiro", resultado.payload)'));
caso("app.js continua sem script.google.com hardcoded após o M12.2B",
     !/script\.google\.com/.test(appJsSrc));
caso("espirometria-financeiro.js continua sem fetch após o M12.2B",
     !/\bfetch\s*\(/.test(efSrc));
caso("espirometria-financeiro.js continua sem XMLHttpRequest após o M12.2B",
     !/XMLHttpRequest/.test(efSrc));

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
