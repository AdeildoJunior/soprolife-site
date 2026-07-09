// SoproLife — M11 Valor real da espirometria dentro da Nova Espirometria.
// Função PURA que monta e VALIDA o payload financeiro do exame a partir do
// formData da tela "Nova Espirometria". Sem DOM, sem fetch, sem rede, sem
// persistência — nesta etapa só monta e valida; a escrita real (Google
// Sheets/Apps Script) fica para uma etapa futura.
//
// Segurança: só lê os campos financeiros explícitos desta lista — nunca
// nome, telefone, responsável ou qualquer outro campo de identificação do
// paciente que possa estar no mesmo formData. Todo texto livre (observação
// financeira) passa pelo sanitizador do M4 (acoesTextoSeguro) antes de
// entrar no payload — mesma defesa contra CPF/telefone/e-mail/token usada
// no resto do painel.

/* eslint-disable no-undef */
const _efSan = (typeof acoesTextoSeguro === "function")
  ? acoesTextoSeguro
  : require("./operational-actions.js").acoesTextoSeguro;

const EF_VALOR_TABELA_PADRAO = 250;

const EF_STATUS_EXAME = ["Aguardando", "Realizado", "Cancelado", "Remarcado"];
const EF_STATUS_PAGAMENTO = ["Recebido", "Pendente", "Parcial", "Cortesia", "Cancelado"];
const EF_FORMA_PAGAMENTO = ["Pix", "Dinheiro", "Cartão", "Outro"];
const EF_ORIGEM_PRECO = ["Tabela", "Promoção", "Parceria", "Negociação", "PCMSO", "Cortesia"];
const EF_LOCAL_ATENDIMENTO = ["Domiciliar", "Clínica", "Empresa / PCMSO", "Parceiro", "Outro"];

function _efObj(v) { return (v && typeof v === "object" && !Array.isArray(v)) ? v : {}; }

// id_atendimento é um ID técnico gerado pelo próprio painel (M11.2B, ex.:
// ESP-20260709-143055-ABC123) — nunca texto digitado por humano — por isso
// usa checagem de formato fechada em vez do sanitizador de texto livre
// (acoesTextoSeguro). O sanitizador existe para bloquear PII digitada à mão
// e dá falso positivo nesse formato: o trecho de data+hora bate com a regex
// de telefone BR (ex. "0260709-1430" dentro de "ESP-20260709-143055-..."),
// o que substituiria o ID pelo texto genérico e quebraria o upsert.
const EF_ID_ATENDIMENTO_RX = /^[A-Za-z0-9-]{1,64}$/;
function _efIdAtendimento(raw) {
  const v = (raw === undefined || raw === null) ? "" : String(raw).trim();
  return EF_ID_ATENDIMENTO_RX.test(v) ? v : null;
}

// Valor monetário: vazio -> fallback (ou erro se obrigatório); qualquer
// coisa que não vire número finito >= 0 -> erro (nunca aceita valor negativo
// ou lixo tipo "abc").
function _efNum(raw, { obrigatorio = false, fallback = null } = {}) {
  if (raw === undefined || raw === null || String(raw).trim() === "") {
    return obrigatorio ? { erro: true } : { valor: fallback };
  }
  const n = Number(String(raw).replace(",", "."));
  if (!Number.isFinite(n) || n < 0) return { erro: true };
  return { valor: Math.round(n * 100) / 100 };
}

// Enum fechado (status/forma/origem): vazio -> fallback (ou erro se
// obrigatório); fora da lista -> erro.
function _efEnum(raw, opcoes, { obrigatorio = false, fallback = "" } = {}) {
  const v = (raw === undefined || raw === null) ? "" : String(raw).trim();
  if (!v) return obrigatorio ? { erro: true } : { valor: fallback };
  return opcoes.includes(v) ? { valor: v } : { erro: true };
}

// formData da tela "Nova Espirometria" -> { ok, payload, erros }.
// ok=false -> payload=null e erros lista o que corrigir (nada é montado
// pela metade). ok=true -> payload tem o shape fixo do contrato M11.
function buildEspirometriaFinanceiroPayload(formData) {
  const f = _efObj(formData);
  const erros = [];

  const dataExame = String(f.data_exame || "").trim();
  if (!dataExame) erros.push("Data do exame é obrigatória.");

  const statusExame = _efEnum(f.status_exame, EF_STATUS_EXAME, { obrigatorio: true });
  if (statusExame.erro) {
    erros.push(f.status_exame ? "Status do exame inválido." : "Status do exame é obrigatório.");
  }

  const statusPagamento = _efEnum(f.status_pagamento, EF_STATUS_PAGAMENTO, { obrigatorio: true });
  if (statusPagamento.erro) {
    erros.push(f.status_pagamento ? "Status do pagamento inválido." : "Status do pagamento é obrigatório.");
  }

  const valorTabela = _efNum(f.valor_tabela, { fallback: EF_VALOR_TABELA_PADRAO });
  if (valorTabela.erro) erros.push("Valor tabela inválido.");

  const valorCobrado = _efNum(f.valor_cobrado, { obrigatorio: true });
  if (valorCobrado.erro) {
    erros.push(f.valor_cobrado ? "Valor cobrado inválido." : "Valor cobrado é obrigatório.");
  }

  const valorRecebido = _efNum(f.valor_recebido, { fallback: 0 });
  if (valorRecebido.erro) erros.push("Valor recebido inválido.");

  const formaPagamento = _efEnum(f.forma_pagamento, EF_FORMA_PAGAMENTO);
  if (formaPagamento.erro) erros.push("Forma de pagamento inválida.");

  const origemPreco = _efEnum(f.origem_preco, EF_ORIGEM_PRECO, { fallback: "Tabela" });
  if (origemPreco.erro) erros.push("Origem do preço inválida.");

  const localAtendimento = _efEnum(f.local_atendimento, EF_LOCAL_ATENDIMENTO, { obrigatorio: true });
  if (localAtendimento.erro) {
    erros.push(f.local_atendimento ? "Local de atendimento inválido." : "Local de atendimento é obrigatório.");
  }

  // Regras cruzadas por status de pagamento (M11.1.1) — só rodam se todos os
  // campos base já são válidos individualmente, pra não empilhar um erro de
  // regra de negócio em cima de um erro de campo (ex.: comparar recebido x
  // cobrado quando cobrado já é inválido não ajudaria o operador). Exame
  // Cancelado pula essas regras: o exame não aconteceu, então exigir forma
  // de pagamento/igualdade de valores por causa de um status_pagamento
  // esquecido em "Recebido" não faria sentido — o valor recebido é zerado
  // de qualquer forma logo abaixo.
  if (erros.length === 0 && statusExame.valor !== "Cancelado") {
    const sp = statusPagamento.valor;
    if (sp === "Recebido") {
      if (!formaPagamento.valor) {
        erros.push("Forma de pagamento é obrigatória quando o status do pagamento é Recebido.");
      }
      if (valorRecebido.valor <= 0) {
        erros.push("Valor recebido deve ser maior que zero quando o status do pagamento é Recebido.");
      } else if (valorRecebido.valor < valorCobrado.valor) {
        erros.push('Valor recebido menor que o valor cobrado — use o status de pagamento "Parcial".');
      } else if (valorRecebido.valor > valorCobrado.valor) {
        erros.push("Valor recebido deve ser igual ao valor cobrado quando o status do pagamento é Recebido.");
      }
    } else if (sp === "Parcial") {
      if (!formaPagamento.valor) {
        erros.push("Forma de pagamento é obrigatória quando o status do pagamento é Parcial.");
      }
      if (valorRecebido.valor <= 0) {
        erros.push("Valor recebido deve ser maior que zero quando o status do pagamento é Parcial.");
      } else if (valorRecebido.valor >= valorCobrado.valor) {
        erros.push("Valor recebido deve ser menor que o valor cobrado quando o status do pagamento é Parcial.");
      }
    }
    // Pendente/Cortesia/Cancelado: forma de pagamento pode ficar vazia — o
    // valor recebido é zerado abaixo independentemente do que foi digitado.
  }

  if (erros.length > 0) return { ok: false, payload: null, erros };

  // Regras de negócio (M11 + M11.1.1): cortesia, cancelamento e pendência
  // nunca contam como receita recebida — zeram o valor recebido nesta
  // camada, mesmo que algo tenha sido digitado/preenchido por chip antes de
  // mudar o status (ex.: clicar num valor sugerido e só depois trocar o
  // status para Pendente).
  const cancelado = statusExame.valor === "Cancelado" || statusPagamento.valor === "Cancelado";
  const cortesia = statusPagamento.valor === "Cortesia";
  const pendente = statusPagamento.valor === "Pendente";
  const valorRecebidoFinal = (cortesia || cancelado || pendente) ? 0 : valorRecebido.valor;

  // Desconto: mesma fórmula seguindo em todos os status (nunca negativo).
  // Em Cortesia isso já resulta em desconto = valor_tabela quando o exame é
  // cobrado como 0, ou a diferença tabela-cobrado nos demais casos — a
  // lógica atual já é a mais segura (nunca negativa) e não precisa de um
  // caso especial a mais.
  const desconto = Math.max(0, Math.round((valorTabela.valor - valorCobrado.valor) * 100) / 100);

  const payload = {
    id_atendimento: _efIdAtendimento(f.id_atendimento),
    criado_em: new Date().toISOString(),
    data_exame: dataExame,
    tipo_movimento: "receita",
    servico: "Espirometria",
    local_atendimento: localAtendimento.valor,
    valor_tabela: valorTabela.valor,
    valor_cobrado: valorCobrado.valor,
    valor_recebido: valorRecebidoFinal,
    desconto,
    status_exame: statusExame.valor,
    status_pagamento: statusPagamento.valor,
    forma_pagamento: formaPagamento.valor,
    origem_preco: origemPreco.valor,
    observacao_financeira: _efSan(f.observacao_financeira, ""),
    fonte: "nova_espirometria",
  };

  return { ok: true, payload, erros: [] };
}

// Node (testes locais) — no navegador, as funções ficam no escopo global.
if (typeof module === "object" && module.exports) {
  module.exports = {
    buildEspirometriaFinanceiroPayload,
    EF_VALOR_TABELA_PADRAO, EF_STATUS_EXAME, EF_STATUS_PAGAMENTO,
    EF_FORMA_PAGAMENTO, EF_ORIGEM_PRECO, EF_LOCAL_ATENDIMENTO,
  };
}
