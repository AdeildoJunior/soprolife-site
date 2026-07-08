# Briefing Diário (Camada 5)

## As 6 perguntas fixas (contrato de buildDailyBriefing)

```
{ aconteceu: string[],      // "O que aconteceu?"
  mudou: string[],          // "O que mudou?"
  atrasado: string[],       // "O que está atrasado?"
  fazerHoje: string[],      // "O que eu devo fazer hoje?"
  podeEsperar: string[],    // "O que posso ignorar por enquanto?"
  maiorRetorno: string }    // "Qual ação tem maior retorno?"
```

Arrays sempre presentes (vazios quando não há nada); máximo 4 itens por
pergunta; frases curtas de template + números — nunca texto bruto de
dado; tudo sanitizado.

## Regras v0 (implementadas)

- `aconteceu`: eventos de hoje (últimos lançamentos) + escritas
  auditadas do dia.
- `mudou`: status geral da saúde + convertidas B2B.
- `atrasado`: follow-ups vencidos (clínicas e pacientes).
- `fazerHoje`: as 3 primeiras ações da fila de decisão.
- `podeEsperar`: ações de prioridade baixa (contagem).
- `maiorRetorno`: a 1ª ação da fila (maior score).

## M9 — ENTREGUE (briefing real)

`js/daily-briefing.js` implementa o contrato completo (source + status
ok/atenção/crítico/demo + titulo + resumoExecutivo + 6 seções + riscos +
porArea com 7 áreas), computado ao vivo dos summaries seguros, com
resumo executivo em destaque na UI do Cérebro. Ver
`docs/m9-briefing-diario-real.md`.

## Evolução (M10)

Comparação REAL com o dia anterior ("o que mudou" via snapshot
persistido) + persistência leve da fila (feito/pendente) — movidas para
o M10 junto da fila de decisão do dia.
