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
| 4 | Usuário `soprolife` existente é seguro | seção [6] do precheck: sem grupo admin, senha travada, `~/.ssh` revisado manualmente (NÃO haverá criação — usuário já existe) | |
| 5 | Nenhuma credencial exposta nos artefatos I1 | scan do `i1-validate-local-before-vps.sh` limpo + relatório do precheck sanitizado conferido a olho | |
| 6 | Templates systemd revisados pelo GPT | veredito LIBERADO sobre `systemd/*.example` (User/Group/HOME=/home/soprolife/proteções/ReadWritePaths) | |
| 7 | **Estratégia ADC/venv do soprolife RESOLVIDA** | decisão registrada abaixo (escopos exatos + quota project + quem digita o device flow) — **sem isso é NO-GO** | |
| 8 | Estratégia de posse do repo decidida | decisão registrada abaixo (`chown` vs `safe.directory`) | |
| 9 | **Plano dirigido para os arquivos `root:root 600` em `data/`** | listagem do passo (a) da F2 revisada pelo GPT — arquivos exatos a corrigir aprovados — **sem isso é NO-GO** | |
| 10 | **Timer pausado ANTES da migração (compromisso da F4)** | primeiro comando da F4 é `systemctl stop soprolife-update-data.timer` — operador ciente — **sem isso é NO-GO** | |
| 11 | **Rollback do service root pronto** | `.bak` datados planejados + sequência de restauração testada em leitura (rollback da F4) — **sem isso é NO-GO** | |
| 12 | Janela de manutenção definida | data/hora + usuário presente + duração estimada + decisão janela única vs dividida | |

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
