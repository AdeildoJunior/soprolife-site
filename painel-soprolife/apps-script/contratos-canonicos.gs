/**
 * contratos-canonicos.gs — Contratos versionados EXECUTÁVEIS (M14.3A)
 *
 * Fonte única dos contratos de registro usados pelos writers do Apps Script
 * (command-center-api.gs, converter-lead-em-paciente.gs, pastore-staging.gs).
 * Espelho JSON commitável: core/contracts/registros-schemas.json — o teste
 * scripts/test-contratos.js falha se os dois divergirem.
 *
 * Regras que este arquivo IMPLEMENTA (não apenas documenta):
 *   - fail-closed: cabeçalho incompatível ou campo solicitado sem coluna
 *     correspondente gera ERRO explícito — nunca sucesso silencioso;
 *   - patch seletivo: atualização altera SOMENTE as colunas presentes no
 *     payload validado; colunas desconhecidas, fórmulas e valores não
 *     enviados são preservados; ausência de campo NUNCA apaga célula;
 *   - datas históricas: valor + precisão (dia|mes|ano|desconhecida);
 *     mês/ano nunca vira dia factual; vazio nunca vira hoje; valor
 *     inválido é erro explícito;
 *   - IDs: o SERVIDOR é a autoridade (UUID opaco com prefixo de entidade);
 *     o id gerado no navegador é aceito apenas como idempotency_key legada;
 *     o timestamp embutido em IDs legados é criação técnica, nunca a data
 *     do atendimento; IDs históricos nunca são recalculados.
 *
 * Arquivo dual-runtime: puro (sem SpreadsheetApp), roda no Apps Script e no
 * Node (module.exports no final) — os testes exercitam ESTE código.
 */

var CT_CONTRATOS_VERSAO = "m14.3a";

// ── Contratos de registro (v1 = planilha real hoje; v2 = alvo canônico) ──────
// "obrigatorios" = colunas que PRECISAM existir na aba para a ação gravar
// (fail-closed). "canonicos_opcionais" = campos novos: quando o payload os
// envia com valor e a coluna não existe, a gravação FALHA com mensagem clara.
// Colunas desconhecidas na aba são sempre preservadas (nunca tocadas).

// "imutaveis" = colunas que NUNCA entram em patch (identidade/metadados de
// criação — recalculá-las em retry corromperia o registro).
// "colunas_tecnicas" = colunas de infraestrutura criadas SOB DEMANDA pelo
// próprio writer (idempotência) — nunca editadas por humanos.
var CT_REGISTROS = {
  crm_pacientes: {
    id: "paciente_id",
    prefixo: "PAC",
    imutaveis: ["paciente_id", "data_cadastro"],
    colunas_tecnicas: [],
    v1: ["paciente_id", "data_cadastro", "primeiro_nome", "telefone",
         "ultimo_servico", "status_relacionamento", "proximo_contato",
         "motivo_proximo_contato", "canal", "responsavel",
         "consentimento_whatsapp", "observacao_privada_minima"],
    v2_extras: ["data_cadastro_precisao", "id_legado"],
    obrigatorios: ["paciente_id", "primeiro_nome"],
    canonicos_opcionais: ["data_cadastro_precisao", "id_legado"],
  },
  crm_espirometria: {
    id: "exame_id",
    prefixo: "ESP",
    imutaveis: ["exame_id", "data_entrada", "idempotency_key", "idempotency_fingerprint"],
    colunas_tecnicas: ["idempotency_key", "idempotency_fingerprint"],
    v1: ["exame_id", "data_entrada", "primeiro_nome", "telefone", "servico",
         "origem", "status_exame", "data_exame", "proximo_contato",
         "motivo_proximo_contato", "canal", "responsavel",
         "consentimento_whatsapp"],
    v2_extras: ["paciente_id", "id_legado", "data_exame_precisao",
                "local_atendimento", "parceiro", "unidade", "modalidade"],
    obrigatorios: ["exame_id", "primeiro_nome", "status_exame"],
    canonicos_opcionais: ["paciente_id", "id_legado", "data_exame_precisao",
                          "local_atendimento", "parceiro", "unidade", "modalidade"],
  },
  crm_consultas: {
    id: "consulta_id",
    prefixo: "CON",
    imutaveis: ["consulta_id", "data_entrada", "idempotency_key", "idempotency_fingerprint"],
    colunas_tecnicas: ["idempotency_key", "idempotency_fingerprint"],
    v1: ["consulta_id", "data_entrada", "primeiro_nome", "telefone", "origem",
         "tipo_consulta", "status", "medica", "data_consulta",
         "proximo_contato", "motivo_proximo_contato", "canal", "responsavel",
         "consentimento_whatsapp"],
    v2_extras: ["paciente_id", "id_legado", "data_consulta_precisao", "modalidade"],
    obrigatorios: ["consulta_id", "primeiro_nome", "status"],
    canonicos_opcionais: ["paciente_id", "id_legado", "data_consulta_precisao", "modalidade"],
  },
  financeiro_lancamentos: {
    id: "id_lancamento",
    prefixo: "FIN",
    // id_atendimento é chave de NEGÓCIO (vínculo com o exame), obrigatória e
    // imutável: a atualização de pagamento por id_atendimento é o fluxo
    // desenhado (patch por presença) — diferente da idempotency_key técnica
    // de espirometria/consulta, que gera replay/409.
    imutaveis: ["id_lancamento", "id_atendimento", "criado_em", "tipo_movimento", "fonte"],
    colunas_tecnicas: [],
    // Cardinalidade (M14.3A): um atendimento pode ter N movimentos; no máximo
    // UMA receita principal ativa; ajustes/complementos/estornos são
    // append-only e referenciam o movimento original (papel_movimento +
    // movimento_referencia, colunas v2). Duplicata é CONFLITO visível para a
    // reconciliação — nunca resolvida silenciosamente por "última vence".
    v1: ["id_lancamento", "id_atendimento", "criado_em", "data_exame",
         "tipo_movimento", "servico", "local_atendimento", "valor_tabela",
         "valor_cobrado", "valor_recebido", "desconto", "status_exame",
         "status_pagamento", "forma_pagamento", "origem_preco",
         "observacao_financeira", "fonte"],
    v2_extras: ["data_recebimento", "data_exame_precisao",
                "estado_reconciliacao", "papel_movimento", "movimento_referencia"],
    obrigatorios: ["id_lancamento", "id_atendimento", "valor_cobrado", "status_pagamento"],
    canonicos_opcionais: ["data_recebimento", "data_exame_precisao",
                          "estado_reconciliacao", "papel_movimento", "movimento_referencia"],
  },
  followup_whatsapp: {
    id: "followup_id",
    prefixo: "FUP",
    imutaveis: ["followup_id", "data_criacao"],
    colunas_tecnicas: [],
    v1: ["followup_id", "data_criacao", "primeiro_nome", "telefone",
         "tipo_mensagem", "data_prevista", "status", "canal", "responsavel",
         "template_usado", "consentimento"],
    v2_extras: ["paciente_id"],
    obrigatorios: ["followup_id", "primeiro_nome", "tipo_mensagem"],
    canonicos_opcionais: ["paciente_id"],
  },
  pastore_atendimentos_staging: {
    id: "",
    prefixo: "",
    imutaveis: [],
    colunas_tecnicas: [],
    v1: ["data_atendimento", "unidade", "dia_semana", "horario_inicio",
         "horario_fim", "origem", "paciente_nome", "paciente_whatsapp",
         "tipo_exame", "broncodilatador", "valor_cobrado", "forma_pagamento",
         "recebido_por", "repasse_pastore", "custo_insumo",
         "custo_deslocamento", "custo_profissional", "outros_custos",
         "receita_bruta", "custo_total", "resultado_liquido", "status",
         "followup_status", "consentimento_contato_futuro",
         "observacao_privada_minima"],
    v2_extras: ["id_atendimento", "estado_reconciliacao"],
    obrigatorios: ["data_atendimento", "paciente_nome", "tipo_exame"],
    canonicos_opcionais: ["id_atendimento", "estado_reconciliacao"],
  },
  // M14.3A (correção final) — registrarInteracaoClinica passa a usar este
  // contrato executável (antes escrevia célula a célula sem fail-closed real:
  // campo suportado sem coluna era ignorado em vez de recusado).
  crm_clinicas: {
    id: "clinica_id",
    prefixo: "CLI",
    imutaveis: ["clinica_id"],
    colunas_tecnicas: [],
    v1: ["clinica_id", "nome_clinica", "bairro", "regiao", "tipo_clinica",
         "etapa", "ultima_interacao", "proxima_acao", "data_proxima_acao",
         "responsavel", "prioridade", "observacao"],
    v2_extras: [],
    obrigatorios: ["clinica_id", "nome_clinica"],
    canonicos_opcionais: [],
  },
  // M14.3A (correção final) — _upsertContatoB2B passa a usar este contrato
  // executável pelo mesmo motivo do crm_clinicas acima.
  crm_contatos_b2b: {
    id: "contato_id",
    prefixo: "CONT",
    imutaveis: ["contato_id"],
    colunas_tecnicas: [],
    v1: ["contato_id", "clinica_id", "nome_contato", "cargo",
         "telefone_whatsapp", "email", "papel", "status_relacionamento",
         "ultima_interacao", "proxima_acao", "data_proxima_acao",
         "responsavel", "observacao"],
    v2_extras: [],
    obrigatorios: ["contato_id", "clinica_id", "nome_contato"],
    canonicos_opcionais: [],
  },
};

// ── Datas: valor + precisão ──────────────────────────────────────────────────

var CT_PRECISOES = ["dia", "mes", "ano", "desconhecida"];

/**
 * Interpreta uma data histórica SEM inventar dia nem cair para hoje.
 *   ""            → { ok:true, precisao:"desconhecida" }
 *   YYYY-MM-DD    → dia      |  DD/MM/AAAA → dia
 *   YYYY-MM       → mes      |  MM/AAAA    → mes
 *   YYYY          → ano
 *   qualquer outra coisa → { ok:false, erro } — o chamador DEVE recusar.
 * Nunca converte mês/ano em "01/MM/AAAA": o valor original é preservado e a
 * precisão viaja junto. Âncoras dia-01 são exclusividade das ferramentas de
 * ordenação (soprolife_normalizacao.py) e nunca persistidas como fato.
 */
function ctParseDataPrecisao(valor) {
  var s = (valor === undefined || valor === null) ? "" : String(valor).trim();
  if (!s) return { ok: true, valor: "", precisao: "desconhecida" };

  function diaValido(ano, mes, dia) {
    if (ano < 2000 || ano > 2100 || mes < 1 || mes > 12 || dia < 1 || dia > 31) return false;
    var d = new Date(ano, mes - 1, dia);
    return d.getFullYear() === ano && d.getMonth() === mes - 1 && d.getDate() === dia;
  }

  var m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) {
    return diaValido(+m[1], +m[2], +m[3])
      ? { ok: true, valor: s, precisao: "dia" }
      : { ok: false, erro: "data inválida: " + s };
  }
  m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) {
    return diaValido(+m[3], +m[2], +m[1])
      ? { ok: true, valor: s, precisao: "dia" }
      : { ok: false, erro: "data inválida: " + s };
  }
  m = s.match(/^(\d{4})-(\d{2})$/);
  if (m) {
    return (+m[2] >= 1 && +m[2] <= 12 && +m[1] >= 2000 && +m[1] <= 2100)
      ? { ok: true, valor: s, precisao: "mes" }
      : { ok: false, erro: "mês/ano inválido: " + s };
  }
  m = s.match(/^(\d{1,2})\/(\d{4})$/);
  if (m) {
    return (+m[1] >= 1 && +m[1] <= 12 && +m[2] >= 2000 && +m[2] <= 2100)
      ? { ok: true, valor: s, precisao: "mes" }
      : { ok: false, erro: "mês/ano inválido: " + s };
  }
  m = s.match(/^(\d{4})$/);
  if (m) {
    return (+m[1] >= 2000 && +m[1] <= 2100)
      ? { ok: true, valor: s, precisao: "ano" }
      : { ok: false, erro: "ano inválido: " + s };
  }
  return { ok: false, erro: "formato de data não reconhecido: " + s };
}

/**
 * Valida coerência entre uma data e uma precisão informada pelo cliente.
 * Precisão ausente → deriva do valor. Precisão incompatível → erro.
 */
function ctValidarDataComPrecisao(valor, precisaoInformada) {
  var parsed = ctParseDataPrecisao(valor);
  if (!parsed.ok) return parsed;
  var p = (precisaoInformada === undefined || precisaoInformada === null)
    ? "" : String(precisaoInformada).trim().toLowerCase();
  if (!p) return parsed;
  if (CT_PRECISOES.indexOf(p) < 0) {
    return { ok: false, erro: "precisão desconhecida: " + p };
  }
  if (p !== parsed.precisao) {
    return {
      ok: false,
      erro: "precisão incoerente: valor '" + parsed.valor + "' é '" +
            parsed.precisao + "', payload declarou '" + p + "'",
    };
  }
  return parsed;
}

// ── Cabeçalho: validação fail-closed e detecção de versão ────────────────────

function _ctHeadersLimpos(headersDaAba) {
  return (headersDaAba || []).map(function (h) { return String(h || "").trim(); });
}

/**
 * Valida o cabeçalho real da aba contra o contrato.
 * Retorna { ok, versao: "v1"|"v2"|"incompativel", faltantes: [...] }.
 * Colunas extras/desconhecidas na aba NÃO invalidam (são preservadas);
 * o que invalida é faltar coluna obrigatória da ação.
 */
function ctValidarCabecalho(nomeContrato, headersDaAba) {
  var contrato = CT_REGISTROS[nomeContrato];
  if (!contrato) return { ok: false, versao: "incompativel", faltantes: [], erro: "contrato desconhecido: " + nomeContrato };
  var headers = _ctHeadersLimpos(headersDaAba);
  var tem = {};
  headers.forEach(function (h) { if (h) tem[h] = true; });

  var faltantes = contrato.obrigatorios.filter(function (c) { return !tem[c]; });
  if (faltantes.length > 0) {
    return {
      ok: false, versao: "incompativel", faltantes: faltantes,
      erro: "cabeçalho incompatível com o contrato " + nomeContrato +
            " (" + CT_CONTRATOS_VERSAO + "): colunas obrigatórias ausentes: " +
            faltantes.join(", "),
    };
  }
  var temTodasV2 = contrato.v2_extras.every(function (c) { return tem[c]; });
  return { ok: true, versao: temTodasV2 ? "v2" : "v1", faltantes: [] };
}

/**
 * Planeja uma escrita fail-closed.
 *
 * campos           — { coluna: valor } já validados pela ação;
 * camposSolicitados— nomes de campos que o CLIENTE pediu com valor não vazio
 *                    (subset de campos). Se algum deles não tiver coluna na
 *                    aba, a escrita FALHA — nunca descarte silencioso.
 * Campos não solicitados (derivados/da própria API) sem coluna são apenas
 * omitidos de persistidos — sem erro, sem escrita.
 *
 * Retorna { ok, persistidos, ignorados, celulas:[{coluna, indice, valor}] }
 * ou { ok:false, erro, colunas_ausentes }.
 */
function ctPlanejarEscrita(nomeContrato, headersDaAba, campos, camposSolicitados) {
  var validacao = ctValidarCabecalho(nomeContrato, headersDaAba);
  if (!validacao.ok) return { ok: false, erro: validacao.erro, colunas_ausentes: validacao.faltantes };

  var headers = _ctHeadersLimpos(headersDaAba);
  var indice = {};
  headers.forEach(function (h, i) { if (h && indice[h] === undefined) indice[h] = i; });

  var solicitados = camposSolicitados || [];
  var semColuna = solicitados.filter(function (c) { return indice[c] === undefined; });
  if (semColuna.length > 0) {
    return {
      ok: false,
      erro: "campo(s) solicitado(s) sem coluna na aba (contrato " + nomeContrato +
            ", " + CT_CONTRATOS_VERSAO + "): " + semColuna.join(", ") +
            ". Crie as colunas (M14.3B) ou remova os campos do payload — " +
            "nada foi gravado.",
      colunas_ausentes: semColuna,
    };
  }

  var persistidos = [];
  var ignorados = [];
  var celulas = [];
  Object.keys(campos).forEach(function (coluna) {
    if (indice[coluna] === undefined) {
      ignorados.push(coluna); // derivado sem coluna: omitido com transparência
      return;
    }
    celulas.push({ coluna: coluna, indice: indice[coluna], valor: campos[coluna] });
    persistidos.push(coluna);
  });

  return {
    ok: true,
    versao_cabecalho: validacao.versao,
    contrato_versao: CT_CONTRATOS_VERSAO,
    persistidos: persistidos.sort(),
    ignorados: ignorados.sort(),
    celulas: celulas,
  };
}

/**
 * Planeja um UPSERT com patch seletivo.
 *
 * linhaExistente = null → { modo:"insert", linha:[...] } com TODAS as colunas
 * da aba (novas em branco — linha nova não tem o que preservar).
 * linhaExistente = array de valores atuais → { modo:"patch", celulas:[...] }
 * contendo SOMENTE as colunas presentes em campos — qualquer outra coluna
 * (desconhecida, fórmula, metadado, valor antigo) fica intocada. Ausência de
 * um campo no payload NUNCA vira limpeza; limpar exigirá ação explícita
 * futura, fora deste contrato.
 */
function ctPlanejarUpsert(nomeContrato, headersDaAba, campos, camposSolicitados, linhaExistente) {
  var plano = ctPlanejarEscrita(nomeContrato, headersDaAba, campos, camposSolicitados);
  if (!plano.ok) return plano;

  if (!linhaExistente) {
    var headers = _ctHeadersLimpos(headersDaAba);
    var linha = headers.map(function () { return ""; });
    plano.celulas.forEach(function (c) { linha[c.indice] = String(c.valor); });
    return {
      ok: true, modo: "insert", linha: linha,
      persistidos: plano.persistidos, ignorados: plano.ignorados,
      contrato_versao: plano.contrato_versao, versao_cabecalho: plano.versao_cabecalho,
    };
  }

  var celulasPatch = plano.celulas.filter(function (c) {
    // patch só escreve o que mudou — reescrever valor idêntico é inofensivo,
    // mas escrever a menos reduz o risco de disparar validações/formatos.
    var atual = c.indice < linhaExistente.length ? String(linhaExistente[c.indice]) : "";
    return atual !== String(c.valor);
  });
  return {
    ok: true, modo: "patch", celulas: celulasPatch,
    persistidos: plano.persistidos, ignorados: plano.ignorados,
    contrato_versao: plano.contrato_versao, versao_cabecalho: plano.versao_cabecalho,
  };
}

// ── IDs: autoridade do servidor + idempotência ───────────────────────────────

// Chave de idempotência legada (o antigo id gerado no navegador): formato
// fechado, nunca texto livre. O timestamp embutido em chaves ESP-AAAAMMDD-*
// é o instante TÉCNICO de criação do formulário — nunca a data do exame.
var CT_IDEMPOTENCY_RX = /^[A-Za-z0-9-]{1,64}$/;

function ctIdempotencyKeySegura(raw) {
  var v = (raw === undefined || raw === null) ? "" : String(raw).trim();
  return CT_IDEMPOTENCY_RX.test(v) ? v : "";
}

/**
 * Novo ID canônico: prefixo de entidade + UUID opaco. O UUID vem do chamador
 * (Utilities.getUuid() no Apps Script; crypto/fixture nos testes) — colisão
 * é estatisticamente impossível e NÃO depende de lastRow, número de linha,
 * contador nem relógio/aleatório do navegador.
 */
function ctNovoIdServidor(prefixo, uuid) {
  var p = String(prefixo || "").trim().toUpperCase();
  var u = String(uuid || "").trim().toLowerCase();
  if (!/^[A-Z]{3,4}$/.test(p)) throw new Error("prefixo de entidade inválido: " + prefixo);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(u)) {
    throw new Error("uuid inválido para ID de servidor");
  }
  return p + "-" + u;
}

// ── Idempotência por AÇÃO (M14.3A, 2ª rodada) ───────────────────────────────
// A chave de idempotência é SEPARADA do ID canônico: o ID é sempre UUID do
// servidor; a chave (quando enviada) só serve para replay/conflito e é
// armazenada em coluna técnica própria. Formato fechado POR AÇÃO: precisa do
// prefixo da entidade + formato legado do navegador OU uuid — "x", "PAC-0001"
// para espirometria e qualquer prefixo cruzado são RECUSADOS.

function ctChaveIdempotenciaAcao(raw, prefixo) {
  var v = (raw === undefined || raw === null) ? "" : String(raw).trim();
  if (!v) return { ok: true, chave: "" };
  var p = String(prefixo || "").toUpperCase();
  var rx = new RegExp(
    "^" + p + "-(\\d{8}-\\d{6}-[A-Z0-9]{4,8}" +
    "|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$");
  if (!rx.test(v)) {
    return {
      ok: false,
      erro: "idempotency_key inválida para esta ação (esperado prefixo " + p +
            "- com formato do navegador ou uuid): valor recusado.",
    };
  }
  return { ok: true, chave: v };
}

/**
 * Serialização canônica e determinística de um payload de negócio: chaves
 * ordenadas; cada valor carrega um marcador de TIPO ("u"=ausente/nulo,
 * "n"=número, "b"=booleano, "s"=string) e o comprimento do texto antes do
 * próprio texto — dois payloads só produzem o mesmo texto se tiverem as
 * mesmas chaves, os mesmos tipos e os mesmos valores. Isso evita que "123"
 * (string) colida com 123 (número), que "" (vazio explícito) colida com
 * ausência, ou que a concatenação de valores vizinhos colida por falta de
 * delimitador (o comprimento prefixado torna a codificação injetiva).
 */
function ctSerializacaoCanonica(campos) {
  var chaves = Object.keys(campos || {}).sort();
  return chaves.map(function (k) {
    var v = campos[k];
    var tipo, texto;
    if (v === undefined || v === null) { tipo = "u"; texto = ""; }
    else if (typeof v === "number") { tipo = "n"; texto = String(v); }
    else if (typeof v === "boolean") { tipo = "b"; texto = String(v); }
    else { tipo = "s"; texto = String(v).trim(); }
    return k + ":" + tipo + ":" + texto.length + ":" + texto;
  }).join("|");
}

/**
 * SHA-256 hexadecimal completo (64 chars) de um texto — funciona tanto no
 * Apps Script (Utilities.computeDigest) quanto no Node dos testes
 * (require("crypto")). Nenhum runtime aceito cai para um hash mais fraco.
 */
function _ctSha256Hex(texto) {
  if (typeof Utilities !== "undefined" && Utilities.computeDigest) {
    var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, texto, Utilities.Charset.UTF_8);
    return bytes.map(function (b) {
      var v = (b < 0 ? b + 256 : b).toString(16);
      return v.length === 1 ? "0" + v : v;
    }).join("");
  }
  if (typeof require === "function") {
    var crypto = require("crypto");
    return crypto.createHash("sha256").update(texto, "utf8").digest("hex");
  }
  throw new Error("Nenhum provedor de SHA-256 disponível neste runtime.");
}

/**
 * Fingerprint DETERMINÍSTICO do payload de negócio: SHA-256 (256 bits, hex
 * completo) sobre a serialização canônica acima. Usado para distinguir
 * replay legítimo (mesma chave + mesmo payload → mesmo resultado) de reuso
 * indevido de chave (mesma chave + payload diferente → conflito 409). Campos
 * imutáveis/técnicos ficam de fora. Substitui o FNV-1a de 32 bits (M14.3A,
 * correção final): a auditoria demonstrou colisão sintética real do hash de
 * 32 bits entre dois payloads distintos sob a mesma chave.
 */
function ctFingerprintPayload(campos) {
  return _ctSha256Hex(ctSerializacaoCanonica(campos));
}

/**
 * Fingerprint equivalente calculado do ESTADO de uma linha existente (para
 * linhas gravadas antes da coluna idempotency_fingerprint existir): usa as
 * MESMAS colunas do payload comparado.
 */
function ctFingerprintDaLinha(headersDaAba, linha, colunas) {
  var headers = _ctHeadersLimpos(headersDaAba);
  var indice = {};
  headers.forEach(function (h, i) { if (h && indice[h] === undefined) indice[h] = i; });
  var estado = {};
  (colunas || []).forEach(function (c) {
    var i = indice[c];
    estado[c] = (i === undefined || i >= linha.length) ? "" : linha[i];
  });
  return ctFingerprintPayload(estado);
}

/**
 * Colunas SUPORTADAS pelo contrato que estão REALMENTE presentes no payload
 * (own property — presença explícita, mesmo vazia, é diferente de ausência).
 * É a base do fail-closed TOTAL (qualquer campo enviado sem coluna → erro) e
 * do patch por presença (ausência nunca limpa célula). Campos imutáveis e
 * técnicos nunca entram (não são graváveis via payload).
 */
function ctCamposPresentes(nomeContrato, data) {
  var contrato = CT_REGISTROS[nomeContrato];
  if (!contrato) throw new Error("contrato desconhecido: " + nomeContrato);
  var bloqueadas = {};
  (contrato.imutaveis || []).forEach(function (c) { bloqueadas[c] = true; });
  (contrato.colunas_tecnicas || []).forEach(function (c) { bloqueadas[c] = true; });
  return contrato.v1.concat(contrato.v2_extras).filter(function (c) {
    return !bloqueadas[c] && Object.prototype.hasOwnProperty.call(data || {}, c);
  });
}

// IDs legados preservados (nunca recalculados): padrões reconhecidos apenas
// para CLASSIFICAÇÃO/rastreabilidade — mesma tabela de ids-canonicos.json.
var CT_ID_LEGADO_PADROES = [
  /^[A-Z]{3,4}-\d{4}$/,              // sequencial _nextId (PAC-0001, CON-0001, FIN-0001)
  /^[A-Z]{3,4}-\d{8}-\d{3}$/,        // data+seq (PAC-20260615-001)
  /^ESM-.+$/,                        // padrão antigo de espirometria
  /^[A-Z]{3,4}-\d{8}-\d{6}-[A-Z0-9]{4,8}$/, // gerado no navegador (agora idempotency key)
];

function ctIdEhLegadoConhecido(id) {
  var v = String(id || "").trim();
  if (!v) return false;
  return CT_ID_LEGADO_PADROES.some(function (rx) { return rx.test(v); });
}

// ── Node (testes) ────────────────────────────────────────────────────────────
if (typeof module === "object" && module.exports) {
  module.exports = {
    CT_CONTRATOS_VERSAO: CT_CONTRATOS_VERSAO,
    CT_REGISTROS: CT_REGISTROS,
    CT_PRECISOES: CT_PRECISOES,
    ctParseDataPrecisao: ctParseDataPrecisao,
    ctValidarDataComPrecisao: ctValidarDataComPrecisao,
    ctValidarCabecalho: ctValidarCabecalho,
    ctPlanejarEscrita: ctPlanejarEscrita,
    ctPlanejarUpsert: ctPlanejarUpsert,
    ctIdempotencyKeySegura: ctIdempotencyKeySegura,
    ctNovoIdServidor: ctNovoIdServidor,
    ctIdEhLegadoConhecido: ctIdEhLegadoConhecido,
    ctChaveIdempotenciaAcao: ctChaveIdempotenciaAcao,
    ctSerializacaoCanonica: ctSerializacaoCanonica,
    ctFingerprintPayload: ctFingerprintPayload,
    ctFingerprintDaLinha: ctFingerprintDaLinha,
    ctCamposPresentes: ctCamposPresentes,
  };
}
