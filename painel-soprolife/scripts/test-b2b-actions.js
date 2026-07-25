#!/usr/bin/env node
// SoproLife — Testes das Próximas Ações B2B/PCMSO (M5).
// 100% local, sem dependência externa, fixtures sintéticas, data injetável.
// Uso: node painel-soprolife/scripts/test-b2b-actions.js
// Exit: 0 = todos passaram | 1 = houve falha.

const path = require("path");
const { buildB2BActions, buildB2BStats, B2B_MAX_ACOES } =
  require(path.resolve(__dirname, "../js/b2b-actions.js"));

let falhas = 0;
function caso(nome, cond, detalhe = "") {
  if (cond) { console.log(`  PASS: ${nome}`); }
  else { falhas += 1; console.log(`  FAIL: ${nome}${detalhe ? " — " + detalhe : ""}`); }
}

const AGORA = new Date(2026, 6, 7); // 07/07/2026 — determinístico
const clin = (extra) => ({ clinica_id: "CLI-0001", nome_clinica: "Clínica Exemplo",
  etapa: "em_negociacao", tem_proxima_acao: true, ...extra });
const leadB2B = (extra) => ({ lead_id: "LEAD-20260601-001", servico_interesse: "PCMSO / empresa",
  etapa: "em_contato", tem_proxima_acao: true, ...extra });

console.log("M5 — testes de buildB2BActions/buildB2BStats (fixtures sintéticas)");

// 1. Payload vazio/inválido -> seguro
caso("payload vazio -> [] sem quebrar", buildB2BActions({}, AGORA).length === 0);
caso("payload null -> []", buildB2BActions(null, AGORA).length === 0);
caso("arrays inválidos -> []",
     buildB2BActions({ leads: "x", clinicas: 42, contatosB2B: null }, AGORA).length === 0);
caso("stats com payload null -> zeros",
     buildB2BStats(null, AGORA).totalOportunidades === 0);

// 2/3. Em conversa e aguardando retorno -> ações média + stats corretos
const a1 = buildB2BActions({ clinicas: [clin(), clin({ clinica_id: "CLI-0002",
  nome_clinica: "Clínica Norte", etapa: "pausada" })] }, AGORA);
caso("em negociação -> ação média 'manter negociação'",
     a1.some((a) => a.prioridade === "media" && /Manter negociação/.test(a.titulo)));
caso("pausada -> ação média 'reforçar contato'",
     a1.some((a) => a.prioridade === "media" && /Reforçar contato/.test(a.titulo)));
const s1 = buildB2BStats({ clinicas: [clin(), clin({ etapa: "pausada" })] }, AGORA);
caso("stats: emConversa=1 e aguardandoRetorno=1",
     s1.emConversa === 1 && s1.aguardandoRetorno === 1);

// 4. Convertido -> não gera ação de prospecção; conta em convertidas
const s2 = buildB2BStats({ clinicas: [clin({ etapa: "ativa" })],
  leads: [leadB2B({ etapa: "Convertido em clínica/parceiro" })] }, AGORA);
caso("convertidas soma parceiro + lead convertido", s2.convertidas === 2);
caso("convertido não entra em oportunidades (dedup)", s2.totalOportunidades === 0);
const a2 = buildB2BActions({ leads: [leadB2B({ etapa: "convertido" })] }, AGORA);
caso("lead convertido não gera ação", a2.length === 0);

// 5. Desistiu -> perdidas, sem ação
const s3 = buildB2BStats({ leads: [leadB2B({ etapa: "Desistiu" })] }, AGORA);
caso("desistiu conta em perdidas", s3.perdidas === 1 && s3.totalOportunidades === 0);
caso("desistiu não gera ação",
     buildB2BActions({ leads: [leadB2B({ etapa: "perdido" })] }, AGORA).length === 0);

// 6. Sem próximo passo -> ação média + stat
const a3 = buildB2BActions({ clinicas: [clin({ etapa: "prospecto", tem_proxima_acao: false })] }, AGORA);
caso("clínica ativa sem próximo passo -> ação 'definir próximo passo'",
     a3.some((a) => /Definir próximo passo/.test(a.titulo) && a.prioridade === "media"));
caso("stat semProximoPasso conta",
     buildB2BStats({ clinicas: [clin({ etapa: "prospecto", tem_proxima_acao: false })] }, AGORA).semProximoPasso === 1);

// 7. Prioridade ALTA por atraso (data_proxima_acao vencida)
const a4 = buildB2BActions({ leads: [leadB2B({ etapa: "aguardando_retomada",
  data_proxima_acao: "01/07/2026" })] }, AGORA);
caso("lead com ação vencida -> prioridade alta",
     a4.some((a) => a.prioridade === "alta" && /Retomar lead/.test(a.titulo)));
caso("motivo cita dias de atraso", a4.some((a) => /venceu há 6 dia/.test(a.motivo)));

// 8. Prioridade por etapa: proposta enviada -> alta
// M23 — o enum canônico não tem "Proposta enviada" como fase própria: o
// alias histórico resolve para em_negociacao e o nudge é o de negociação.
const a5 = buildB2BActions({ clinicas: [clin({ etapa: "Proposta enviada" })] }, AGORA);
caso("alias histórico 'Proposta enviada' -> negociação, sem denúncia de etapa",
     a5.some((a) => /Manter negociação/.test(a.titulo)) &&
     !a5.some((a) => /Revisar etapa no CRM/.test(a.titulo)));
caso("follow-up atrasados agregado -> alta",
     buildB2BActions({ followupStats: { atrasados: 3 } }, AGORA)
       .some((a) => a.prioridade === "alta" && /atrasados/.test(a.titulo)));

// 9. Texto suspeito não vaza (telefone/e-mail/CPF/token no nome/etapa)
const suspeitos = buildB2BActions({ clinicas: [
  clin({ etapa: "em_negociacao", nome_clinica: "ligar (21) 99999-8888" }),
  clin({ clinica_id: "CLI-9", etapa: "em_negociacao", nome_clinica: "contato x@y.com" }),
  clin({ clinica_id: "CLI-10", etapa: "pausada", nome_clinica: "doc 123.456.789-09" }),
  clin({ clinica_id: "CLI-11", etapa: "em_negociacao", nome_clinica: "chave ya29.abc" }),
] }, AGORA);
const bruto = JSON.stringify(suspeitos);
caso("telefone não vaza", !bruto.includes("99999-8888"));
caso("e-mail não vaza", !bruto.includes("x@y.com"));
caso("CPF não vaza", !bruto.includes("123.456.789-09"));
caso("token não vaza", !bruto.includes("ya29.abc"));
caso("suspeitos viram mensagem genérica", /omitido por segurança/.test(bruto));

// 10. Campos extras ignorados -> shape fixo
const comExtra = buildB2BActions({ clinicas: [clin({ etapa: "em_negociacao",
  telefone: "(21) 98888-0000", observacao: "privada", html: "<img onerror=x>" })] }, AGORA)[0];
const SHAPE = ["id", "prioridade", "origem", "titulo", "motivo", "proximoPasso"];
caso("shape fixo da ação",
     JSON.stringify(Object.keys(comExtra).sort()) === JSON.stringify([...SHAPE].sort()),
     Object.keys(comExtra).join(","));
caso("campo extra não vaza em valor", !JSON.stringify(comExtra).includes("98888-0000"));

// 11. Limite de ações e ordenação alta > média > baixa
const muitas = buildB2BActions({ clinicas: Array.from({ length: 20 }, (_, i) =>
  clin({ clinica_id: `CLI-${i}`, nome_clinica: `Clínica ${i}`,
         etapa: i % 2 ? "pausada" : "em_negociacao" })) }, AGORA);
caso(`lista limitada a ${B2B_MAX_ACOES}`, muitas.length === B2B_MAX_ACOES);
caso("ordenada alta antes de média",
     muitas.findIndex((a) => a.prioridade === "media") === -1 ||
     muitas.findIndex((a) => a.prioridade === "media") >
     muitas.findIndex((a) => a.prioridade === "alta"));

// 12. Etapa fora do vocabulário oficial -> ação de qualidade de dado
const a6 = buildB2BActions({ clinicas: [clin({ etapa: "Etapa Inventada" })] }, AGORA);
caso("etapa fora do padrão -> ação 'revisar etapa'",
     a6.some((a) => a.prioridade === "media" && /Revisar etapa no CRM/.test(a.titulo)));
caso("etapa fora do padrão citada no motivo", a6.some((a) => /Etapa Inventada/.test(a.motivo)));
caso("etapa fora do padrão não conta como oportunidade",
     buildB2BStats({ clinicas: [clin({ etapa: "Etapa Inventada" })] }, AGORA).totalOportunidades === 0);

// 13. M23 — alias histórico continua LEGÍVEL, sem virar denúncia de qualidade
const a7 = buildB2BActions({ clinicas: [clin({ etapa: "Parceira",
  tem_proxima_acao: false })] }, AGORA);
caso("alias 'Parceira' resolve para parceiro ativo",
     a7.some((a) => /Alinhar rotina com o parceiro/.test(a.titulo)) &&
     !a7.some((a) => /Revisar etapa no CRM/.test(a.titulo)));
caso("alias histórico conta como convertida nos stats",
     buildB2BStats({ clinicas: [clin({ etapa: "Parceira" })] }, AGORA).convertidas === 1);

console.log();
if (falhas) { console.log(`RESULTADO: ${falhas} caso(s) FALHARAM.`); process.exit(1); }
console.log("RESULTADO: todos os casos passaram.");
