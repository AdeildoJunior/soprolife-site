# Camadas do Cérebro Operacional

```
7 · Automação futura      (desenho apenas — nenhum envio real)
6 · Central de Decisão    (próxima melhor ação, top 3, por área)
5 · Briefing Diário       (6 perguntas fixas)
4 · Projeções             (comercial, exames, receita, custos, gargalos, SEO)
3 · Motor de Prioridade   (matriz de 6 eixos → alta/média/baixa)
2 · Diagnóstico           (bom / atrasado / parado / atenção / dinheiro / risco)
1 · Estado Atual          (summaries seguros normalizados)
0 · Segurança/Governança  (o que PODE entrar — regra M2, pii_guard, sanitizador)
```

## Camada 0 — Segurança e Governança

- Entram SOMENTE summaries com `safeToDisplay=true` +
  `containsPersonalData=false` (mesma dupla checagem do app).
- Nunca: data-private, .config, ADC, tokens, telefone, CPF, e-mail,
  nome de paciente, dado clínico individual, observação privada.
- Todo texto que o cérebro produz passa pelo sanitizador do M4
  (`acoesTextoSeguro`) — PII suspeita vira mensagem genérica.
- Shape fixo em todas as saídas: campo inesperado não existe.

## Camada 1 — Estado Atual

`buildOperationalBrainState(payloads)` normaliza os 12 summaries em um
estado único: contadores por área + flags de presença/frescor. Fonte
ausente vira `null` explícito, nunca crash. Contrato: doc 02.

## Camada 2 — Diagnóstico Operacional

Classifica o estado em 6 baldes: **bom** (verde), **atrasado**
(follow-ups vencidos), **parado** (sem próximo passo), **atenção**
(saúde ≠ ok), **gera dinheiro** (exames, parcerias, propostas),
**gera risco** (check de segurança, dado velho, erros de auditoria).
v0: regras simples derivadas dos contadores da Camada 1.

## Camada 3 — Motor de Prioridade

Doc 03. Seis eixos 0–3 → score 0–100 → alta/média/baixa.

## Camada 4 — Projeções

Doc 04. Fórmulas simples e premissas EXPLÍCITAS; v0 usa base demo.

## Camada 5 — Briefing Diário

Doc 05. Estrutura fixa de 6 perguntas; v0 preenche com regras.

## Camada 6 — Central de Decisão

Doc 06. Fila limitada (7), próxima melhor ação, top 3, 7 áreas.

## Camada 7 — Automação futura (SÓ desenho)

Fluxos planejados (nenhum implementado, nenhum envio real): lembrete
WhatsApp manual (gera TEXTO para copiar, humano envia); follow-up de
clínica; retomada de paciente; atualização assistida de planilha (via
Command Center auditado); resumo diário; alerta de dado velho; alerta
de oportunidade quente. Pré-requisito de qualquer um: M1 auditoria +
aprovação humana por ação.
