# Motor de Prioridade

## Matriz (6 eixos, cada um 0–3)

| Eixo | Peso | 0 | 3 |
|---|---|---|---|
| `impactoFinanceiro` | 30 | nenhum | receita direta (exame/parceria/proposta) |
| `urgencia` | 25 | pode esperar | vence hoje / já venceu |
| `riscoOperacional` | 20 | nenhum | segurança/dado velho/pipeline parado |
| `crescimentoB2B` | 10 | não relacionado | destrava parceria/PCMSO |
| `pacientes` | 10 | não relacionado | follow-up de paciente vencido |
| `facilidade` | 5 | difícil/longo | resolve em minutos |

`score = Σ(eixo/3 × peso)` → 0–100.

## Faixas

- **alta**: score ≥ 60
- **media**: 30 ≤ score < 60
- **baixa**: score < 30

## v0 (implementado em buildPriorityScore)

Recebe `{impactoFinanceiro, urgencia, riscoOperacional, crescimentoB2B,
pacientes, facilidade}` (ausente = 0; fora de 0–3 é truncado) e devolve
`{score, nivel}`. Determinístico e puro — testável sem mock.

Mapeamento v0 dos itens vindos de M4/M5 (que só têm nivel):
alta → {urgencia:3, riscoOperacional:2}; media → {urgencia:2};
baixa → {urgencia:1} + eixos de contexto (origem B2B soma
crescimentoB2B:2; origem follow-up pacientes soma pacientes:3).

## Evolução (M10+)

Cada gerador de ação passa a emitir os 6 eixos direto (em vez do
mapeamento por nível); pesos viram constantes revisáveis com os sócios;
empate desempata por facilidade.
