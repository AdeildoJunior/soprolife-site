# Cérebro Operacional SoproLife — Visão Geral

## O que é

A camada que transforma os summaries SEGUROS do painel em leitura
executiva: diagnóstico do estado atual, prioridades do dia, projeções
comerciais/financeiras, alertas, recomendações e briefing diário — e,
no futuro, o desenho de automações (sem envio real).

Não é um sistema novo: é a **cabeça** em cima do corpo que já existe
(M1 auditoria, M2 guarda de PII, M3 saúde, M4/M5 ações, M6 gate).

## Princípio de projeto

**Top-down com contratos estáveis, bottom-up na implementação.**
O M8 entrega o esqueleto completo (contratos, módulo puro, demo, testes,
UI mínima); M9–M15 descem camada por camada trocando placeholder por
lógica real — sem quebrar o formato.

## Insumos (só summaries seguros de `painel-soprolife/data/`)

resumo-dashboard · leads · CRM clínicas · contatos B2B · follow-up
clínicas · follow-up pacientes · financeiro · custos · marketing-seo ·
auditoria · saúde operacional · últimos lançamentos — todos com
`safeToDisplay=true` e `containsPersonalData=false` (regra M2).

## Saídas (v0)

- `buildOperationalBrainState` — estado normalizado e seguro;
- `buildPriorityScore` — matriz de prioridade (6 eixos);
- `buildDailyBriefing` — briefing com 6 perguntas fixas;
- `buildProjectionSkeleton` — 6 projeções com fórmulas simples;
- `buildDecisionQueue` — próxima melhor ação, top 3, alertas, por área.

## Mapa dos documentos

01 camadas · 02 contratos de dados · 03 motor de prioridade ·
04 projeções · 05 briefing diário · 06 central de decisão ·
07 roadmap M9–M15 · 08 riscos e limites.
