# M8 — Esqueleto do Cérebro Operacional SoproLife

## Objetivo

Versão 0, top-down: arquitetura em 8 camadas, contratos estáveis, módulo
JS puro, demo seguro, testes e UI mínima — para as etapas M9–M15
descerem camada por camada trocando placeholder por lógica real SEM
mudar formatos.

## O que foi criado

| Item | Arquivo(s) |
|---|---|
| Arquitetura macro (camadas 0–7, contratos, motor, projeções, briefing, decisão, roadmap, riscos) | `docs/cerebro-operacional/00…08` (9 docs) |
| Módulo puro | `js/operational-brain.js` — `buildOperationalBrainState`, `buildPriorityScore`, `buildDailyBriefing`, `buildProjectionSkeleton`, `buildDecisionQueue` |
| Demo commitável | `data/cerebro-operacional.json` (flags de segurança + exemplos genéricos + M9–M15) |
| Testes | `scripts/test-operational-brain.js` — 32 casos |
| UI mínima | Painel "Cérebro Operacional" no topo do Painel Geral (status, top 3, briefing, projeções com premissa em tooltip, próximos módulos) |
| Quality gate | +3 checks (node --check do brain, suíte M8, json do demo) |

## O que já é REAL vs. o que é demo

- **Real**: o painel computa AO VIVO estado/diagnóstico/fila/briefing/
  projeções a partir dos summaries seguros já carregados (saúde, B2B,
  follow-ups, financeiro, custos, marketing, auditoria, lançamentos);
  chip mostra "leitura ao vivo dos summaries seguros".
- **Demo**: fórmulas de projeção usam premissas placeholder (ex.: 20%
  de conversão) — SEMPRE com a premissa visível; exemplos do JSON demo
  só aparecem quando não há nenhuma fonte viva; "próximos módulos" vem
  do demo por definição.

## O que já está testado (32 casos)

Payload nulo/vazio/summaries ausentes sem crash; shapes estáveis de
estado (6 baldes), briefing (6 chaves), projeções (6 itens) e fila;
matriz de prioridade (faixas + eixos inválidos truncados); ordenação
alta>média>baixa; dedup; limite 7; PII/segredo neutralizados (telefone,
e-mail, CPF, token); campos extras ignorados (shape fixo); demo com
flags corretas e sem padrões de PII.

## Riscos

Ver `docs/cerebro-operacional/08-riscos-e-limites.md` — em especial:
falsa autoridade de projeção demo (mitigada por premissa visível) e a
regra dura da Camada 7 (gerar texto ≠ enviar; envio é humano; registro
auditado).

## Próximos passos sugeridos

M9 briefing real (snapshot diário) → M10 fila do dia com feito/pendente
→ M11 projeções B2B reais → M12 financeiro/ponto de equilíbrio → M13
motor de campanhas (texto manual) → M14 assistente de CRM (via Command
Center auditado) → M15 painel executivo semanal. Detalhe:
`docs/cerebro-operacional/07-roadmap-de-implementacao.md`.
