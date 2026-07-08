# M9 — Briefing Diário Real

## Objetivo

Transformar o briefing v0 do esqueleto M8 em briefing diário REAL:
resumo executivo do dia, as 6 perguntas, riscos e leitura por área —
computado no navegador a partir dos summaries seguros já carregados.

## Fontes

Camada ACIMA do M8 (não substitui): consome `buildOperationalBrainState`
e `buildDecisionQueue` (que por sua vez leem saúde M3, ações M4, B2B M5,
follow-ups, financeiro, custos, marketing, auditoria, lançamentos).
Módulo: `js/daily-briefing.js` — `buildDailyBriefingReal`,
`buildBriefingSections`, `buildExecutiveSummary`, `buildTodayActionList`,
`buildBriefingRiskFlags`. Shape completo no contrato (source + status +
titulo + resumoExecutivo + 6 seções + riscos + porArea com 7 áreas).

## O que é REAL vs. demo

- **Real**: com qualquer fonte viva, o briefing inteiro é calculado ao
  vivo (`dadosReais: true`); status ok/atenção/crítico é derivado por
  regra previsível (risco→crítico; atraso/ação/saúde-atenção→atenção).
- **Demo**: `data/briefing-diario.json` só aparece quando NENHUMA fonte
  viva existe (`status: "demo"`, rotulado).
- Ainda **não** há snapshot do dia anterior — "o que mudou" é derivado
  do estado atual; comparação real com ontem foi movida para o M10
  (junto da persistência da fila).

## Segurança

Mesma Camada 0 do cérebro: só summaries seguros; sanitizador M4 em todo
texto; shapes fixos (testado); PII/token neutralizados (testado);
nenhum envio de mensagem — o briefing é leitura.

## Como testar

```bash
node painel-soprolife/scripts/test-daily-briefing.js   # 26 casos
bash painel-soprolife/scripts/quality-gate-safe.sh     # inclui M9
python3 -m http.server 8765   # → Painel Geral → Cérebro Operacional
```

## Próximos passos (M10)

"Hoje eu tenho que fazer o quê?": persistência leve da fila
(feito/pendente), snapshot diário para "o que mudou" comparar com ontem
de verdade, e reordenação ao concluir.
