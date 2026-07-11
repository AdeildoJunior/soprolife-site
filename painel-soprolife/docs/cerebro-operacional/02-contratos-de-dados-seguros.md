# Contratos de Dados Seguros do Cérebro

## Regra de admissão (inegociável)

Um payload só entra no cérebro se: (a) vier de `painel-soprolife/data/`;
(b) tiver `safeToDisplay=true` e `containsPersonalData=false` quando o
wrapper existir; (c) já ter passado pelo pipeline M2 (pii_guard nos
geradores). O cérebro ainda assim re-sanitiza tudo que exibe.

## Entrada: `payloads` de buildOperationalBrainState

Todos opcionais/nuláveis (fonte ausente degrada, não quebra):

| Chave | Origem (estado do app) | Campos usados |
|---|---|---|
| `saudeOperacional` | M3 (real ou demo) | status_geral, indicadores[].status, alertas[] |
| `acoesOperacionais` | M4 `buildOperationalActions()` | nivel, titulo, proximoPasso |
| `acoesB2B` | M5 `buildB2BActions()` | prioridade, titulo, origem, proximoPasso |
| `b2bStats` | M5 `buildB2BStats()` | totalOportunidades, precisamFollowup, convertidas… |
| `resumoDashboard` | resumo-dashboard | contagens numéricas (totalLeads etc.) |
| `followupPacientes` | followup-pacientes-summary | espirometria/consultas: hoje, atrasados… |
| `financeiro` | financeiro-summary (gerado da aba Financeiro_Lancamentos — fonte financeira única, M14.2) | receita_exames, total_entradas_mes_atual, saldo_operacional (null — não derivável da fonte) |
| `custos` | custos-investimentos-summary | total_mensal_atual, pendencias_cadastro |
| `marketing` | marketing-seo.meta | sources.searchConsole/ga4 |
| `auditoria` | auditoria-summary | stats.total_eventos, stats.erros |
| `ultimosLancamentos` | ultimos-lancamentos-summary | stats.hoje, stats.pendencias |

## Saída: `brainState` (shape fixo)

```
{
  geradoEm: ISO string,
  fontes: { <chave>: true|false },          // presença por fonte
  saude:  { statusGeral, alertasCriticos, alertasAtencao },
  comercial: { oportunidades, precisamFollowup, convertidas, semProximoPasso },
  pacientes: { followupsAtrasados, followupsHoje },
  financeiro: { receitaMes, saldo, custoMensal },
  marketing: { searchConsoleOk, ga4Ok },
  sistemas: { auditoriaErros, eventosHoje },
  diagnostico: { bom[], atrasado[], parado[], atencao[], dinheiro[], risco[] }
}
```

Strings de diagnóstico são FRASES GERADAS (templates + números) —
nunca texto vindo de dado; ainda assim sanitizadas.

## Proibições de saída

Nenhuma função do cérebro emite: telefone, CPF, e-mail, nome de pessoa,
token, URL secreta, observação livre de origem externa, JSON bruto.
Nome de clínica institucional é permitido APENAS quando já veio de
summary seguro validado (mesma regra do M5).
