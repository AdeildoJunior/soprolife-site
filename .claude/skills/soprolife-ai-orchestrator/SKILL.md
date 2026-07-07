---
name: soprolife-ai-orchestrator
description: Divisão de papéis entre as IAs do projeto (GPT, Claude Fable, Claude Code Sonnet, Codex) — quem pensa, quem organiza, quem executa, e a regra de nunca ter duas IAs no mesmo diff. Usar ao decidir qual IA recebe uma tarefa.
---

# soprolife-ai-orchestrator

Use esta skill para decidir qual IA/modelo usar e como elas se coordenam.

## Papéis

**GPT (arquiteto/coordenador):**
- revisão de diffs e pacotes de revisão (soprolife-review-pack);
- decide LIBERADO/CORRIGIR/BLOQUEADO antes de commit/deploy;
- prioriza, integra as IAs e transforma estratégia em tarefas fechadas.

**Claude Fable:**
- estratégia, arquitetura de produto, roadmap, análise de risco;
- planos de 7/30/90 dias; especificações e prompts para executores;
- revisão crítica de decisões.
- Não usar Fable para publicar, deployar ou mexer em produção sem
  revisão. Fable pensa grande, mas não é executor operacional solto.

**Claude Code Sonnet (executor padrão):**
- implementar código em escopo fechado (soprolife-etapa-segura);
- JS/CSS/HTML, Python, Apps Script, shell; rodar checks; mostrar diffs.

**Codex (executor alternativo):**
- mesmas regras do Sonnet: escopo fechado, uma etapa, diff e parada;
- usar quando o usuário indicar; nunca em paralelo com Sonnet no mesmo
  arquivo/módulo.

## Regras de coordenação

1. **Nunca duas IAs no mesmo diff.** Um working tree sujo pertence a UMA
   sessão de UM executor até ser commitado ou descartado. Trocar de
   executor exige: commit (usuário) ou reset explícito antes.
2. Executor não decide arquitetura no meio da etapa — achou decisão
   grande, para e devolve ao GPT/usuário.
3. Toda tarefa técnica termina com: git status, diff stat, diff completo,
   checks, riscos e **parada para aprovação** — antes de qualquer
   commit/push/deploy, que são sempre atos do usuário.

## Ordem ideal

Fable pensa. GPT organiza e revisa. Sonnet/Codex executam. Usuário aprova
e commita. (Detalhes do fluxo: painel-soprolife/docs/ai-operating-model.md)
