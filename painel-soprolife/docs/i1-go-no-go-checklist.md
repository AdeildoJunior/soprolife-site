# I1 — Checklist GO/NO-GO para a janela F1–F5

> Preencher ANTES de agendar a janela. Cada item tem a evidência exigida.
> Qualquer item ✗ = **NO-GO**. Decisão final é do GPT + usuário, nunca do
> executor. Sem IP/usuário/porta reais neste arquivo — placeholders.

## Pré-condições técnicas

| # | Item | Evidência exigida | ✓/✗ |
|---|---|---|---|
| 1 | Repo LOCAL limpo, branch `painel-soprolife-v01` | saída de `i1-validate-local-before-vps.sh` = GO | |
| 2 | Repo da VPS limpo e no commit esperado | seção [7] do relatório do precheck read-only | |
| 3 | Precheck read-only executado, anexado e revisado | arquivo `i1-precheck-vps-readonly-*.txt` em `_REVISOES_GPT` com veredito GO condicional | |
| 4 | Usuário `soprolife`: ausente OU seguro | seção [6] do precheck: se existe → sem grupo admin, senha travada, `~/.ssh` revisado manualmente | |
| 5 | Nenhuma credencial exposta nos artefatos I1 | scan do `i1-validate-local-before-vps.sh` limpo + relatório do precheck sanitizado conferido a olho | |
| 6 | Templates systemd revisados pelo GPT | veredito LIBERADO sobre `systemd/*.example` (User/Group/HOME/proteções/ReadWritePaths) | |
| 7 | Estratégia ADC decidida | decisão registrada abaixo (escopos exatos + quota project + quem digita o device flow) | |
| 8 | Estratégia de posse do repo decidida | decisão registrada abaixo (`chown` vs `safe.directory`) | |
| 9 | Plano de rollback impresso/aberto | `i1-execucao-assistida-f1-f5.md` (rollback por fase) acessível DURANTE a janela | |
| 10 | Janela de manutenção definida | data/hora + usuário presente + duração estimada + decisão janela única vs dividida | |

## Decisões a registrar (preencher na revisão com o GPT)

- **ADC**: escopos = `_____________` · quota project = `<PROJECT_ID>` ·
  operador do device flow = `_____________`
- **Posse do repo**: ( ) `chown soprolife` no repo (deploy passa a rodar
  como soprolife) ( ) manter root + `safe.directory`
- **Janela**: ( ) única F1→F5 ( ) dividida (F1–F3 dia 1, F4–F5 dia 2)
- **Critério de abortar no meio**: 1º FAIL em check de fase → rollback da
  fase e encerrar a janela (não "tentar mais uma coisa").

## Veredito

- [ ] **GO** — assinado por: GPT (revisão) + usuário (aprovação), data: ____
- [ ] **NO-GO** — motivo: ________________________________________________
