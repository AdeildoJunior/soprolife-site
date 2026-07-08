# Central de Decisão (Camada 6)

## Contrato (buildDecisionQueue)

```
{ proximaMelhorAcao: acao|null,
  top3: acao[],                    // até 3
  fila: acao[],                    // até 7, ordenada por score desc
  alertasRisco: string[],          // críticos da saúde + segurança
  porArea: { Comercial, CRM, Pacientes, Financeiro,
             Marketing, Operacao, Sistemas }   // 1 frase ou "—" cada }
```

`acao` = shape M4/M5 + `{score, nivel}` do motor de prioridade.

## Fontes da fila

União das ações M4 (operacionais) + M5 (B2B), pontuadas pelo motor
(doc 03), deduplicadas por id, limitadas a 7. Alertas de risco vêm dos
alertas críticos da Saúde Operacional.

## Por área — regra v0

- Comercial: 1ª ação B2B da fila (ou "—");
- CRM: ação de "sem próximo passo"/etapa fora do padrão;
- Pacientes: follow-ups vencidos (contagem → frase);
- Financeiro: pendências de cadastro em custos;
- Marketing: SC/GA4 sem dados → "revisar integração";
- Operacao: 1ª ação operacional (M4) da fila;
- Sistemas: erros de auditoria/check de segurança.

## Evolução (M10)

"Hoje eu tenho que fazer o quê?": fila com marcação de feito
(persistência leve), reordenação ao concluir, e visão por responsável.
