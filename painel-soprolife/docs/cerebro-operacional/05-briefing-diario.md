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

## Evolução (M9)

Briefing REAL: comparação com o snapshot do dia anterior ("o que
mudou" de verdade), persistência leve do último briefing (arquivo
gitignored gerado no ciclo), e exibição no topo do painel logo após o
login do dia.
