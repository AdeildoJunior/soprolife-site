# Modelo operacional de IAs — SoproLife Command Center

> Como GPT, Claude Fable, Claude Code Sonnet e Codex trabalham no projeto
> sem conflito. Versão condensada; as regras executáveis vivem nas skills
> (`.claude/skills/soprolife-*`).

## Papéis (resumo — detalhe na skill soprolife-ai-orchestrator)

| IA | Papel | Nunca faz |
|---|---|---|
| **GPT** | Arquiteto/coordenador: revisa pacotes, decide LIBERADO/CORRIGIR/BLOQUEADO, prioriza | Executar código no repo |
| **Claude Fable** | Estratégia, especificações, planos, análise de risco, prompts para executores | Deploy/publicação sem revisão |
| **Claude Code Sonnet** | Executor padrão: uma etapa fechada por sessão | Commit, push, deploy, decisão de arquitetura |
| **Codex** | Executor alternativo, mesmas regras do Sonnet | Trabalhar em paralelo com Sonnet no mesmo módulo |

## Fluxo padrão de toda etapa

```
etapa (escopo fechado)  →  checks  →  pacote de revisão  →  revisão GPT
       →  commit (usuário)  →  push/deploy (usuário, quando aplicável)
```

1. **Etapa**: executor segue soprolife-etapa-segura — diagnóstico,
   escopo fechado, sem "aproveitar que está mexendo".
2. **Checks**: `bash painel-soprolife/scripts/quality-gate-safe.sh`
   (gate único — sintaxes, suítes M3/M4/M5, JSONs, check-access, guard
   rails de staging; ver docs/m6-quality-gate-safe.md) + teste visual
   se UI mudou. **Deploy só com o gate verde.**
3. **Pacote**: soprolife-review-pack →
   `~/Documents/SoproLife/_REVISOES_GPT/<data>-<etapa>/` — sem secrets,
   sem dados privados, sem IP/usuário de VPS.
4. **Revisão GPT**: contra o checklist do pacote de planejamento
   (bloqueios: secret no diff, check falho, escopo estourado, teste
   visual ausente em mudança de UI).
5. **Commit**: sempre do usuário, com tag de checkpoint quando fechar
   um marco (`checkpoint-<modulo>-<descricao>-v01`).
6. **Push/deploy**: só após commit revisado; VPS segue
   soprolife-vps-safe + soprolife-vps-deploy-safe; units seguem
   soprolife-systemd-safe.

## Quando parar para revisão (executor)

- Fim do escopo da etapa (sempre).
- Check falhou (parar NO ponto da falha, mostrar saída).
- Descoberta estrutural que muda o desenho (ex.: arquivo sem gerador,
  colisão de nomes de campo) — explicar antes de "modificar demais".
- Qualquer coisa que peça commit/push/deploy/publicação.

## Regra de não misturar

- **Uma etapa por sessão; um executor por diff.** Working tree sujo
  pertence à sessão que o criou até commit ou descarte.
- Módulos diferentes (M2, M3, I1...) nunca dividem o mesmo diff.
- Mudança de código e mudança de dado não dividem o mesmo commit sem
  necessidade explícita.

## Pastas e referências padrão

- Pacotes de revisão: `~/Documents/SoproLife/_REVISOES_GPT/`
- Planejamentos: `painel-soprolife/docs/` (ex.:
  `i1-timer-sem-root-planejamento.md`)
- Skills operacionais: `.claude/skills/soprolife-*/SKILL.md`
- Checkpoints: tags git `checkpoint-*` a cada marco aprovado.
