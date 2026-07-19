---
name: soprolife-etapa-segura
description: Protocolo de execução em etapa única no SoproLife — diagnóstico inicial, escopo fechado, checks obrigatórios, mostrar o diff e parar. Usar em TODA tarefa técnica executada por IA neste repositório.
---

# soprolife-etapa-segura

## Quando usar
Sempre que uma IA (Sonnet/Codex/Fable) for executar qualquer mudança no
repositório. É a regra-mãe que os prompts longos repetiam — agora basta
citar esta skill.

## Regra de ouro
**Uma etapa por sessão. Diff mostrado. Parada obrigatória. Commit só do usuário.**

## Fluxo obrigatório
1. Diagnóstico inicial (sempre, antes de editar):
   - `git branch --show-current` (esperado: `painel-soprolife-v01`)
   - `git status --short` (working tree limpo ou só o escopo da etapa)
   - `git log --oneline --decorate -5`
2. Ler os arquivos relevantes antes de editar. Nunca editar de memória.
3. Executar SOMENTE o escopo declarado da etapa. "Aproveitar que está
   mexendo" é violação de escopo.
4. Rodar os checks da área tocada:
   - `node --check painel-soprolife/js/app.js` (se JS mudou)
   - `python3 -m py_compile` nos `.py` alterados
   - `python3 painel-soprolife/scripts/pii_guard.py --self-test` (se guardas mudaram)
   - `bash -n` nos `.sh` alterados
   - `bash painel-soprolife/scripts/check-access.sh` → exit 0
   - teste visual real se frontend mudou (skill soprolife-ux-premium)
5. Mostrar: `git status --short`, `git diff --check`, `git diff --stat`,
   diff completo, riscos e decisões tomadas.
6. **Parar.** Aguardar revisão/aprovação.

## Proibições
- Não commitar, não dar push, não deployar, não publicar Apps Script.
- Não avançar para a próxima etapa sem ordem explícita.
- Não misturar mudança de código com mudança de dado no mesmo diff.
- Não esconder check falho — falhou, parou, reportou.
- Não tocar em VPS nem em produção nesta modalidade.

## Em caso de falha
Parar no ponto da falha, mostrar a saída completa do check que falhou e
aguardar — nunca contornar silenciosamente.
