# Projeções (Camada 4)

## Formato estável (buildProjectionSkeleton)

Cada projeção retorna SEMPRE:

```
{ id, label, status: "esqueleto"|"parcial"|"ativa",
  valorBase: number|null, projecao30d: number|null,
  premissa: string }        // premissa SEMPRE explícita na tela
```

## As 6 projeções e as fórmulas v0 (simples de propósito)

| id | Fórmula v0 | Fonte base |
|---|---|---|
| `comercial_b2b` | oportunidades ativas × taxa demo de conversão (20%) | b2bStats |
| `exames` | exames do mês corrente extrapolados p/ 30 dias | resumoDashboard/financeiro |
| `receita` | receitaMes extrapolada + parcerias | financeiro |
| `custos` | custo mensal recorrente atual | custos |
| `gargalos` | contagem de "sem próximo passo" + follow-ups vencidos | b2bStats + pacientes |
| `marketing_seo` | flags SC/GA4 → capacidade de medir (sem número v0) | marketing |

Regras: fonte ausente → `valorBase:null` + status "esqueleto" + premissa
"sem dados suficientes"; nunca inventar número (princípio da skill de
finanças: nunca inventar valor); arredondamento em 2 casas.

## Evolução

- M11: comercial B2B com histórico real de conversão por etapa.
- M12: receita/custos com ponto de equilíbrio e cenários (pessimista/
  base/otimista) — pesos revisados com os sócios.
- Nunca apresentar projeção sem premissa visível ao lado.
